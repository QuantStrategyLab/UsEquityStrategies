"""Offline Schwab proposal check against frozen first-open research decisions."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace

from boxx_outer_cash_compare import _load
from phase5_locked_inventory_audit import (COST_BPS, PATHS, PRINCIPALS,
                                           SCALE_SUMMARY_SHA, SYMBOLS, _ledger, _sha)

FIRST_TRADE = "2023-03-28"


def _proposal_summary(proposals: tuple, actual_shares: dict, targets: dict) -> dict:
    mapped = {item.symbol: item.details for item in proposals}
    if set(mapped) != {symbol for symbol in SYMBOLS if targets[symbol] > 0.01}:
        raise ValueError("PHASE5_SCHWAB_FIRST_OPEN_PROPOSAL_SET_CHANGED")
    if any(details["side"] != "buy" for details in mapped.values()):
        raise ValueError("PHASE5_SCHWAB_FIRST_OPEN_NON_BUY_PROPOSAL")
    return {
        "positive_proposal_symbols": sorted(mapped),
        "fractional_quantity_symbols": sorted(symbol for symbol, details in mapped.items()
                                               if not math.isclose(details["quantity"],
                                                                   round(details["quantity"]), abs_tol=1e-8)),
        "research_zero_share_but_positive_proposal_symbols": sorted(
            symbol for symbol in mapped if actual_shares[symbol] == 0 and mapped[symbol]["quantity"] > 0),
        "proposal_equals_research_whole_share_symbols": sorted(
            symbol for symbol in mapped if math.isclose(
                mapped[symbol]["quantity"], actual_shares[symbol], abs_tol=1e-8)),
        "no_target_no_proposal_symbols": sorted(symbol for symbol in SYMBOLS
                                                 if targets[symbol] <= 0.01 and symbol not in mapped),
    }


def _mapper(schwab_root: Path):
    source = schwab_root / "application/paper_execution_command_consumer.py"
    spec = importlib.util.spec_from_file_location("schwab_phase5_pure_proposal_mapper", source)
    if spec is None or spec.loader is None:
        raise ValueError("PHASE5_SCHWAB_PURE_MAPPER_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, _sha(source)


def analyze(root: Path, schwab_root: Path) -> dict:
    study_path = root / "phase5_capital_scale_v1" / "phase5_capital_scale_summary.v1.json"
    if _sha(study_path) != SCALE_SUMMARY_SHA:
        raise ValueError("PHASE5_SCHWAB_SCALE_SOURCE_CHANGED")
    study = json.loads(study_path.read_text())
    if study["research_only"] is not True or study["development"] is not True:
        raise ValueError("PHASE5_SCHWAB_RESEARCH_IDENTITY_CHANGED")
    _, _, _, rows, _ = _load(root)
    first = next(row for row in rows if row["date"] == FIRST_TRADE)
    prices = {symbol: float(first[symbol.lower() + "_open"]) for symbol in SYMBOLS}
    if any(not math.isfinite(price) or price <= 0 for price in prices.values()):
        raise ValueError("PHASE5_SCHWAB_OPEN_PRICE_INVALID")
    module, source_sha = _mapper(schwab_root)
    results = {}
    for principal in PRINCIPALS:
        for path_name in PATHS:
            key = f"capital_{principal}_{path_name}_{COST_BPS}bps"
            day = _ledger(root, principal, path_name, study)[0]
            if day["date"] != FIRST_TRADE or day["signal_date"] != "2023-03-27":
                raise ValueError("PHASE5_SCHWAB_FIRST_SIGNAL_CHANGED")
            targets = {symbol: float(day["target_usd"][symbol]) for symbol in SYMBOLS}
            actual = {symbol: float(day["trade_shares"][symbol]) for symbol in SYMBOLS}
            if any(value < 0 for value in targets.values()) or any(value < 0 or value != int(value) for value in actual.values()):
                raise ValueError("PHASE5_SCHWAB_RESEARCH_TARGET_OR_FILL_INVALID")
            portfolio = SimpleNamespace(positions=(), cash_balance=float(principal), total_equity=float(principal))
            quotes = SimpleNamespace(get_quote=lambda symbol: SimpleNamespace(last_price=prices[symbol]))
            intent = {"schema_version": module.SCHWAB_PAPER_EXECUTION_INTENT_SCHEMA_VERSION,
                      "target_mode": "value", "strategy_symbols": list(SYMBOLS), "targets": targets}
            mapped = module._build_reconciled_order_proposals(
                SimpleNamespace(intent=intent), portfolio=portfolio,
                market_data_port=quotes, managed_symbols=SYMBOLS)
            if mapped.integrity_findings:
                raise ValueError("PHASE5_SCHWAB_PURE_MAPPER_REJECTED")
            results[key] = _proposal_summary(mapped.proposals, actual, targets)
            results[key]["research_first_open_tqqq_activated"] = actual["TQQQ"] > 0
    return {"schema": "qsl.research.phase5_schwab_first_open_contract.v1",
            "development": True, "research_only": True,
            "source_phase5_summary_sha256": SCALE_SUMMARY_SHA,
            "schwab_pure_mapper_source_sha256": source_sha,
            "first_signal": "2023-03-27", "first_trade": FIRST_TRADE,
            "method": "Synthetic cash-only portfolio from frozen A_init; frozen research target values and approved first-trade raw open quotes are passed to Schwab's local pure paper proposal mapper. Compare proposal quantities with separately replayed whole-share fills. No command consumer, account, execution service or order port is called.",
            "limits": "A pure paper value-proposal mapper is not the Schwab execution cycle. It does not verify whole-share execution, BOXX cash sweep, fees, settlement, lot ownership, broker risk admission or real-account compatibility. Different quantities identify a contract gap, not an authorized order or a request to change the frozen candidate.",
            "paths": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--schwab-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.parent != args.private_root / "phase5_capital_scale_v1":
        raise ValueError("PHASE5_SCHWAB_OUTPUT_OUTSIDE_PRIVATE_ROOT")
    result = analyze(args.private_root, args.schwab_root)
    data = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
    print(json.dumps({"paths": len(result["paths"]),
                      "summary_sha256": hashlib.sha256(data).hexdigest()}, sort_keys=True))
