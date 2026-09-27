"""Read-only locked-inventory applicability check on frozen Phase 5 ledgers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

SCALE_SUMMARY_SHA = "f9345361e8b5aed249b4d2039d67ac00b2bc8eeb2c590a9a0f4c587f41edbfef"
PRINCIPALS = (1000, 1250, 10000, 100000)
PATHS = ("enhanced", "matched_defense_baseline")
SYMBOLS = ("TQQQ", "QQQM", "BOXX")
COST_BPS = 10


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _first_conflict(ledger: list[dict], symbol: str) -> dict:
    if len(ledger) != 444 or ledger[0]["date"] != "2023-03-28" or ledger[-1]["date"] != "2024-12-31":
        raise ValueError("PHASE5_LOCKED_WINDOW_CHANGED")
    locked = ledger[0]["shares"][symbol]
    if locked < 0:
        raise ValueError("PHASE5_LOCKED_STARTING_SHARES_INVALID")
    conflict = next((row for row in ledger[1:]
                     if row["shares"][symbol] + 1e-9 < locked), None)
    return {"locked_first_close_shares": locked,
            "first_original_path_conflict": None if conflict is None else conflict["date"],
            "original_share_shortfall_at_first_conflict":
                None if conflict is None else locked - conflict["shares"][symbol]}


def _ledger(root: Path, principal: int, path_name: str, scale: dict) -> list[dict]:
    name = f"{path_name}_{COST_BPS}bps"
    if principal == 10000:
        path = root / "phase4_fixed_pair_v3" / "phase4_comparison_summary.v3.json"
        if _sha(path) != scale["source_phase4_summary_sha256"]:
            raise ValueError("PHASE5_LOCKED_PHASE4_SUMMARY_CHANGED")
        source = json.loads(path.read_text())
        digest = source["private_ledger_sha256"][name]
        ledger_path = path.parent / f"private_daily_{name}.json"
    else:
        key = f"capital_{principal}_{name}"
        digest = scale["private_new_ledger_sha256"][key]
        ledger_path = root / "phase5_capital_scale_v1" / f"private_daily_{key}.json"
    if _sha(ledger_path) != digest:
        raise ValueError("PHASE5_LOCKED_LEDGER_CHANGED")
    return json.loads(ledger_path.read_text())


def analyze(root: Path) -> dict:
    source = root / "phase5_capital_scale_v1" / "phase5_capital_scale_summary.v1.json"
    if _sha(source) != SCALE_SUMMARY_SHA:
        raise ValueError("PHASE5_LOCKED_SCALE_SOURCE_CHANGED")
    scale = json.loads(source.read_text())
    if scale["development"] is not True or scale["research_only"] is not True:
        raise ValueError("PHASE5_LOCKED_RESEARCH_IDENTITY_CHANGED")
    results = {}
    for principal in PRINCIPALS:
        for path_name in PATHS:
            ledger = _ledger(root, principal, path_name, scale)
            results[f"capital_{principal}_{path_name}_{COST_BPS}bps"] = {
                symbol: _first_conflict(ledger, symbol) for symbol in SYMBOLS}
    return {"schema": "qsl.research.phase5_locked_inventory_audit.v1",
            "research_only": True, "development": True,
            "source_phase5_summary_sha256": SCALE_SUMMARY_SHA,
            "hypothesis": "For each symbol separately, all shares held at the first execution close become unavailable for sale from the next session through the fixed window. No other shares are locked. This is a necessary share-floor audit on the original paths, not a new replay or an initial-account state.",
            "cost_bps_per_side": COST_BPS,
            "results": results,
            "interpretation_limit": "A conflict proves the original path cannot represent that lock hypothesis from that date. No conflict proves only that its share-count floor was respected; it does not establish lot-level transferability, cost basis, funding feasibility of an alternate path, or a real locked starting account."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.parent != args.private_root / "phase5_capital_scale_v1":
        raise ValueError("PHASE5_LOCKED_OUTPUT_OUTSIDE_PRIVATE_ROOT")
    result = analyze(args.private_root)
    data = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
    print(json.dumps({"schema": result["schema"], "paths": len(result["results"]),
                      "summary_sha256": hashlib.sha256(data).hexdigest()}, sort_keys=True))
