"""Reproduce predeclared proxy and 2026 diagnostics from bound local files."""
from __future__ import annotations

import argparse
import csv
from datetime import date
import hashlib
import json
from pathlib import Path

from .paired_bayesian_update import evaluate_pair
from .prelisting_proxy_research import (
    SyntheticAssumptions, floating_rate_overlap_diagnostic, floating_rate_three_times_path,
    pre2026_overlap_diagnostic, proxy_signal_diagnostics, synthetic_three_times_path,
)
from .provider_history import read_provider_history


def build_report(input_dir: Path, manifest: Path, rates_path: Path, rates_sha256: str,
                 paths_dir: Path, frozen_paths_manifest: Path):
    manifest_bytes = manifest.read_bytes()
    source_manifest = json.loads(manifest_bytes)
    sources = {}
    for record in source_manifest["records"]:
        source = input_dir / Path(record["path"]).name
        data = read_provider_history(source, expected_sha256=record["sha256"], expected_symbol=record["symbol"])
        sources[record["symbol"]] = data
    raw_rates = rates_path.read_bytes()
    if hashlib.sha256(raw_rates).hexdigest() != rates_sha256:
        raise ValueError("RATE_BYTES_MISMATCH")
    rates = [(date.fromisoformat(row["observation_date"]), float(row["DFF"]) / 100)
             for row in csv.DictReader(raw_rates.decode().splitlines()) if row["DFF"] not in ("", ".")]
    bars = {symbol: [(row["session"], row["adjusted_close"]) for row in data["rows"]]
            for symbol, data in sources.items()}
    proxy_results = {}
    for symbol, asset, listing in (("QQQ", "TQQQ", date(2010, 2, 9)),
                                   ("SOXX", "SOXL", date(2010, 3, 11))):
        signals = proxy_signal_diagnostics(bars[symbol], first_tradable_session=listing)
        before = [row for row in signals["rows"] if row["tradability"] == "PRELISTING_PROXY_ONLY"]
        historical_bars = [bar for bar in bars[symbol] if bar[0] < listing]
        constant_scenarios = []
        for assumptions in (SyntheticAssumptions(.01, .04, 0.), SyntheticAssumptions(.01, .08, .02)):
            path = synthetic_three_times_path(historical_bars, assumptions)
            constant_scenarios.append({"assumptions": path["assumptions"],
                                       "synthetic_terminal_nav": path["rows"][-1]["synthetic_nav"],
                                       "pre2026_overlap": pre2026_overlap_diagnostic(bars[symbol], bars[asset], assumptions)})
        scenarios = []
        for spread in (0., .005, .01):
            for tracking in (0., .005):
                hypothetical = floating_rate_three_times_path(historical_bars, rates, spread=spread, tracking_drag=tracking)
                overlap = floating_rate_overlap_diagnostic(bars[symbol], bars[asset], rates,
                                                          spread=spread, tracking_drag=tracking)
                scenarios.append({"spread": spread, "tracking_drag": tracking,
                                  "synthetic_terminal_nav": hypothetical["rows"][-1]["synthetic_nav"],
                                  "pre2026_overlap": overlap})
        proxy_results[symbol] = {"listed_asset": asset, "source_bars_sha256": signals["source_bars_sha256"],
                                 "prelisting_sessions": len(before),
                                 "range_warm_sessions": sum(row["range"] is not None for row in before),
                                 "range_true_sessions": sum(row["range"] is True for row in before),
                                 "prior_constant_scenarios": constant_scenarios, "scenarios": scenarios}
    index_signal = proxy_signal_diagnostics(bars["^SOX"], first_tradable_session=date(2010, 3, 11))
    before = [row for row in index_signal["rows"] if row["tradability"] == "PRELISTING_PROXY_ONLY"]
    frozen = json.loads(frozen_paths_manifest.read_bytes())
    expected = {row["path"]: row["sha256"] for row in frozen["files"]}
    paired = []
    for asset in ("TQQQ", "SOXL"):
        for cost in ("C2_5", "C5_10"):
            for lag in (1, 2):
                b = f"daily_paths/{asset}_trend_{cost}_lag{lag}.csv"
                c = f"daily_paths/{asset}_trend_mr_10_{cost}_lag{lag}.csv"
                paired.append({"asset": asset, "cost": cost, "lag": lag,
                               "result": evaluate_pair(paths_dir / b, paths_dir / c, through=date(2026, 10, 30),
                                                       expected_baseline_sha256=expected[b],
                                                       expected_candidate_sha256=expected[c])})
    return {"scope": "retrospective_research_only", "executable": False, "promotion_eligible": False,
            "source_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "frozen_path_manifest_sha256": hashlib.sha256(frozen_paths_manifest.read_bytes()).hexdigest(),
            "rate_sha256": rates_sha256, "rate_availability": "ASSUMED_NOT_POINT_IN_TIME",
            "provider_inputs": {symbol: {"sha256": value["provider_sha256"], "event_counts": value["event_counts"],
                                          "first_quote": value["rows"][0]["session"].isoformat(),
                                          "last_quote": value["rows"][-1]["session"].isoformat(),
                                          "sessions": len(value["rows"])} for symbol, value in sources.items()},
            "proxy_signals_and_scenarios": proxy_results,
            "sox_price_index_only": {"prelisting_sessions": len(before),
                                     "range_warm_sessions": sum(row["range"] is not None for row in before),
                                     "range_true_sessions": sum(row["range"] is True for row in before)},
            "paired_2026": paired}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("input-dir", "manifest", "rates-path", "paths-dir", "frozen-paths-manifest"):
        parser.add_argument("--" + flag, type=Path, required=True)
    parser.add_argument("--rates-sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(build_report(args.input_dir, args.manifest, args.rates_path, args.rates_sha256,
                                  args.paths_dir, args.frozen_paths_manifest), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
