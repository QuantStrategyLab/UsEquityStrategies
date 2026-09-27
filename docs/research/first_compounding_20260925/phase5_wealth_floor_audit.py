"""Read-only high-water loss-space audit of frozen Phase 5 paths."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from phase5_locked_inventory_audit import (COST_BPS, PATHS, PRINCIPALS,
                                           SCALE_SUMMARY_SHA, _ledger, _sha)


def _max_observed_gap(ledger: list[dict], principal: int) -> dict:
    """Return ex-post minimum L for N_t >= H_t - L on every recorded signal."""
    if len(ledger) != 444 or ledger[0]["date"] != "2023-03-28" or ledger[-1]["date"] != "2024-12-31":
        raise ValueError("PHASE5_FLOOR_WINDOW_CHANGED")
    return _observed_gap(ledger, principal)


def _observed_gap(ledger: list[dict], principal: int) -> dict:
    high_water = float(principal)
    maximum_gap = 0.0
    worst_signal = None
    previous_signal = None
    for row in ledger:
        signal = row["signal_date"]
        if previous_signal is not None and signal <= previous_signal:
            raise ValueError("PHASE5_FLOOR_SIGNAL_ORDER_INVALID")
        previous_signal = signal
        equity = float(row["decision_equity_usd"])
        if equity <= 0:
            raise ValueError("PHASE5_FLOOR_EQUITY_INVALID")
        high_water = max(high_water, equity)
        gap = high_water - equity
        if gap > maximum_gap:
            maximum_gap = gap
            worst_signal = signal
    return {"minimum_nonbreached_constant_loss_limit_usd": round(maximum_gap, 6),
            "first_signal_at_maximum_gap": worst_signal,
            "ratio_to_initial_principal": round(maximum_gap / principal, 9)}


def analyze(root: Path) -> dict:
    source = root / "phase5_capital_scale_v1" / "phase5_capital_scale_summary.v1.json"
    if _sha(source) != SCALE_SUMMARY_SHA:
        raise ValueError("PHASE5_FLOOR_SCALE_SOURCE_CHANGED")
    scale = json.loads(source.read_text())
    if scale["development"] is not True or scale["research_only"] is not True:
        raise ValueError("PHASE5_FLOOR_RESEARCH_IDENTITY_CHANGED")
    results = {}
    for principal in PRINCIPALS:
        for path_name in PATHS:
            name = f"capital_{principal}_{path_name}_{COST_BPS}bps"
            results[name] = _max_observed_gap(_ledger(root, principal, path_name, scale), principal)
    return {"schema": "qsl.research.phase5_wealth_floor_audit.v1",
            "research_only": True, "development": True,
            "source_phase5_summary_sha256": SCALE_SUMMARY_SHA,
            "method": "At each recorded signal, H_t=max(A_init, observed decision equities through t), gap=H_t-N_t. The maximum gap is the ex-post minimum constant L that would avoid an already-breached H_t-L floor on the original unchanged path.",
            "cost_bps_per_side": COST_BPS,
            "results": results,
            "interpretation_limit": "A hindsight threshold is not a precommitted loss preference, a stress limit, a proposed allocation, or a counterfactual replay. A tighter policy could have changed earlier fills and states; no adoption follows."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.parent != args.private_root / "phase5_capital_scale_v1":
        raise ValueError("PHASE5_FLOOR_OUTPUT_OUTSIDE_PRIVATE_ROOT")
    result = analyze(args.private_root)
    data = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
    print(json.dumps({"schema": result["schema"], "paths": len(result["results"]),
                      "summary_sha256": hashlib.sha256(data).hexdigest()}, sort_keys=True))
