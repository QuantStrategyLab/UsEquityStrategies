"""Observed three-ETF return correlation from approved raw bars and actions."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
from pathlib import Path

from boxx_outer_cash_compare import _load
from phase4_qqqm_tqqq_boxx_compare import _events

MANIFEST_SHA = "cb14a511083c824a748d137a271c93cfe0e8adf38f648905b26e37decf4c6182"
SUMMARY_SHA = "32e812ee0f38461f74f10975d32003db496b81e9232be7bd5f6be6b8459c9793"
SYMBOLS = ("QQQM", "TQQQ", "BOXX")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _holding_return(prior: float, close: float, dividend: float) -> float:
    if (not math.isfinite(prior) or prior <= 0 or not math.isfinite(close)
            or close <= 0 or not math.isfinite(dividend) or dividend < 0):
        raise ValueError("PHASE4_CORRELATION_PRICE_OR_EVENT_INVALID")
    return (close + dividend) / prior - 1


def _returns(rows: list[dict], events: dict, ledger_dates: tuple[str, ...]) -> dict[str, tuple[float, ...]]:
    relevant = [row for row in rows if "2023-03-27" <= row["date"] <= "2024-12-31"]
    dates = tuple(row["date"] for row in relevant)
    if (len(relevant) != 445 or dates[0] != "2023-03-27" or dates[1:] != ledger_dates
            or len(set(dates)) != len(dates)):
        raise ValueError("PHASE4_CORRELATION_WINDOW_OR_DATES_CHANGED")
    result = {}
    for symbol in SYMBOLS:
        if any("2023-03-28" <= day <= "2024-12-31" for day in events[symbol]["splits"]):
            raise ValueError("PHASE4_CORRELATION_SPLIT_ADJUSTMENT_REQUIRED")
        series = []
        for previous, current in zip(relevant, relevant[1:]):
            day = current["date"]
            prior = float(previous[symbol.lower() + "_close"])
            close = float(current[symbol.lower() + "_close"])
            dividends = events[symbol]["dividends"].get(day, [])
            amount = math.fsum(float(event["rate"]) for event in dividends)
            series.append(_holding_return(prior, close, amount))
        result[symbol] = tuple(series)
    return result


def _matrix(returns: dict[str, tuple[float, ...]]) -> dict[str, dict[str, float]]:
    if set(returns) != set(SYMBOLS) or any(len(series) != 444 for series in returns.values()):
        raise ValueError("PHASE4_CORRELATION_SERIES_INVALID")
    return {left: {right: (1.0 if left == right else statistics.correlation(returns[left], returns[right]))
                   for right in SYMBOLS} for left in SYMBOLS}


def analyze(root: Path) -> dict:
    manifest_path = root / "manifest.json"
    summary_path = root / "phase4_fixed_pair_v3" / "phase4_comparison_summary.v3.json"
    if _sha(manifest_path) != MANIFEST_SHA or _sha(summary_path) != SUMMARY_SHA:
        raise ValueError("PHASE4_CORRELATION_SOURCE_CHANGED")
    manifest = json.loads(manifest_path.read_text())
    summary = json.loads(summary_path.read_text())
    if (manifest["price_adjustment"] != "raw" or manifest["research_only"] is not True
            or summary["development"] is not True or summary["research_only"] is not True):
        raise ValueError("PHASE4_CORRELATION_SOURCE_IDENTITY_INVALID")
    _, _, _, rows, actions = _load(root)
    events = _events(actions)
    reference = root / "phase4_fixed_pair_v3" / "private_daily_enhanced_10bps.json"
    if _sha(reference) != summary["private_ledger_sha256"]["enhanced_10bps"]:
        raise ValueError("PHASE4_CORRELATION_REFERENCE_LEDGER_CHANGED")
    ledger_dates = tuple(row["date"] for row in json.loads(reference.read_text()))
    returns = _returns(rows, events, ledger_dates)
    matrix = _matrix(returns)
    negative = {symbol: sum(value < 0 for value in returns[symbol]) for symbol in SYMBOLS}
    common_negative = sum(all(returns[symbol][index] < 0 for symbol in SYMBOLS)
                          for index in range(444))
    return {"schema": "qsl.research.phase4_asset_correlation.v1",
            "development": True, "research_only": True,
            "source_manifest_sha256": MANIFEST_SHA,
            "source_phase4_summary_sha256": SUMMARY_SHA,
            "sessions": 444, "first_date": ledger_dates[0], "last_date": ledger_dates[-1],
            "method": "For each same-date ETF, one-session retrospective economic holding return is (raw close on t + per-share dividend with ex_date t)/raw close on t-1 - 1. No split occurred in this fixed window. Pearson correlation uses 444 common sessions. These returns are not decision-time inputs.",
            "limits": "ETF return correlation, not correlation of full strategy members or a causal diversification estimate. Dividend ex-date is used retrospectively for economic measurement, not asserted known before a historical decision; provider process time is not announcement time. The seen window excludes other crises, fees and whole-share portfolio execution from individual ETF returns.",
            "dividend_event_count": {symbol: sum(len(events[symbol]["dividends"].get(day, []))
                                                  for day in ledger_dates) for symbol in SYMBOLS},
            "negative_daily_return_count": negative,
            "all_three_negative_days": common_negative,
            "correlation_matrix": matrix}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.parent != args.private_root / "phase4_fixed_pair_v3":
        raise ValueError("PHASE4_CORRELATION_OUTPUT_OUTSIDE_PRIVATE_ROOT")
    result = analyze(args.private_root)
    data = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
    print(json.dumps({"sessions": result["sessions"], "summary_sha256": hashlib.sha256(data).hexdigest()}, sort_keys=True))
