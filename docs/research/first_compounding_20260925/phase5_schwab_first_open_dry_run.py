"""Guarded Schwab dry-run against the frozen Phase 5 first-open fills."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import sys
from pathlib import Path
from unittest import mock

from boxx_outer_cash_compare import _load
from phase5_locked_inventory_audit import (COST_BPS, PATHS, PRINCIPALS,
                                           SCALE_SUMMARY_SHA, SYMBOLS, _ledger, _sha)

FIRST_TRADE = "2023-03-28"


def _schwab_modules(schwab_root: Path):
    sys.path.insert(0, str(schwab_root))
    try:
        from application import account_new_risk_gate_support, execution_service
        from notifications.telegram import build_translator
        from quant_platform_kit.common.models import QuoteSnapshot
        from quant_platform_kit.common.port_adapters import CallableExecutionPort, CallableMarketDataPort
    finally:
        sys.path.pop(0)
    return (account_new_risk_gate_support, execution_service, build_translator,
            QuoteSnapshot, CallableExecutionPort, CallableMarketDataPort)


def _dry_run(principal: int, targets: dict[str, float], cash_target: float,
             prices: dict[str, float], modules: tuple) -> dict:
    (risk_support, execution_service, build_translator, QuoteSnapshot,
     CallableExecutionPort, CallableMarketDataPort) = modules

    def forbidden(*_args, **_kwargs):
        raise RuntimeError("PHASE5_SCHWAB_EXTERNAL_CALLBACK_REACHED")

    portfolio = {"market_values": {symbol: 0.0 for symbol in SYMBOLS},
                 "quantities": {symbol: 0 for symbol in SYMBOLS},
                 "liquid_cash": float(principal), "cash_sweep_symbol": "BOXX",
                 "total_equity": float(principal), "total_strategy_equity": float(principal)}
    execution = {"trade_threshold_value": 0.0, "reserved_cash": cash_target}
    allocation = {"target_mode": "value", "strategy_symbols": tuple(SYMBOLS),
                  "targets": targets, "risk_symbols": ("TQQQ", "QQQM"),
                  "income_symbols": (), "safe_haven_symbols": ("BOXX",)}
    plan = {"account_hash": "synthetic-no-account", "portfolio": portfolio,
            "execution": execution, "allocation": allocation}
    with (mock.patch.object(execution_service, "attach_daily_loss_fact_to_portfolio",
                            side_effect=lambda current, **_kwargs: current),
          mock.patch.object(risk_support, "resolve_production_drift_status_from_store",
                            return_value=None) as drift_read,
          mock.patch.object(execution_service, "maybe_publish_attention_for_admission",
                            return_value={"sent": 0, "skipped": 1, "failed": 0}),
          contextlib.redirect_stdout(io.StringIO())):
        try:
            cycle = execution_service.execute_rebalance_cycle(
                client=object(), plan=plan, portfolio=portfolio, execution=execution,
                allocation=allocation, fetch_managed_snapshot=forbidden,
                market_data_port=CallableMarketDataPort(quote_loader=lambda symbol: QuoteSnapshot(
                    symbol=symbol, as_of=FIRST_TRADE, last_price=prices[symbol],
                    ask_price=prices[symbol])),
                load_plan=forbidden, execution_port=CallableExecutionPort(forbidden),
                translator=build_translator("en"), limit_buy_premium=1.005,
                sell_settle_delay_sec=0, dry_run_only=True, publish_order_issue=forbidden)
        finally:
            execution_service.set_cycle_snapshot(None)
    if not drift_read.called:
        raise ValueError("PHASE5_SCHWAB_DRIFT_STORE_NOT_ISOLATED")
    status = cycle.execution.get("execution_status")
    if status != "dry_run" or any(order.get("status") != "dry_run" for order in cycle.submitted_orders):
        raise ValueError("PHASE5_SCHWAB_NOT_DRY_RUN")
    if any(order["side"] != "buy" or order["symbol"] not in SYMBOLS or
           int(order["quantity"]) != order["quantity"] for order in cycle.submitted_orders):
        raise ValueError("PHASE5_SCHWAB_UNEXPECTED_ORDER")
    quantities = {symbol: 0 for symbol in SYMBOLS}
    for order in cycle.submitted_orders:
        quantities[order["symbol"]] += int(order["quantity"])
    return {"order_count": len(cycle.submitted_orders),
            "order_symbols": [order["symbol"] for order in cycle.submitted_orders],
            "whole_share_buy_quantities": quantities,
            "boxx_target_cleared_by_platform": (targets["BOXX"] > 0.0 and
                                                cycle.allocation["targets"]["BOXX"] == 0.0),
            "boxx_cash_sweep_order_exceeds_research_target":
                quantities["BOXX"] * prices["BOXX"] > targets["BOXX"] + 0.01,
            "buy_blocked_reason": cycle.execution.get("buys_blocked_reason"),
            "account_new_risk_reason_codes": cycle.execution.get("account_new_risk_reason_codes")}


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
    if any(not 0 < price < float("inf") for price in prices.values()):
        raise ValueError("PHASE5_SCHWAB_OPEN_PRICE_INVALID")
    modules = _schwab_modules(schwab_root)
    results = {}
    for principal in PRINCIPALS:
        for path_name in PATHS:
            day = _ledger(root, principal, path_name, study)[0]
            if day["date"] != FIRST_TRADE or day["signal_date"] != "2023-03-27":
                raise ValueError("PHASE5_SCHWAB_FIRST_SIGNAL_CHANGED")
            targets = {symbol: float(day["target_usd"][symbol]) for symbol in SYMBOLS}
            raw_actual = {symbol: float(day["trade_shares"][symbol]) for symbol in SYMBOLS}
            if any(value < 0 or value != int(value) for value in raw_actual.values()):
                raise ValueError("PHASE5_SCHWAB_RESEARCH_FILL_INVALID")
            actual = {symbol: int(value) for symbol, value in raw_actual.items()}
            cash_target = float(day["cash_target_usd"])
            if not 0 <= cash_target <= principal:
                raise ValueError("PHASE5_SCHWAB_CASH_TARGET_INVALID")
            result = _dry_run(principal, targets, cash_target, prices, modules)
            result["research_equal_by_symbol"] = {symbol: result["whole_share_buy_quantities"][symbol] == actual[symbol]
                                                   for symbol in SYMBOLS}
            result["all_research_share_deltas_equal"] = all(result["research_equal_by_symbol"].values())
            results[f"capital_{principal}_{path_name}_{COST_BPS}bps"] = result
    return {"schema": "qsl.research.phase5_schwab_first_open_dry_run.v3",
            "development": True, "research_only": True,
            "source_phase5_summary_sha256": SCALE_SUMMARY_SHA,
            "schwab_execution_service_sha256": _sha(schwab_root / "application/execution_service.py"),
            "first_signal": "2023-03-27", "first_trade": FIRST_TRADE,
            "method": "Existing Schwab execution cycle with synthetic cash-only portfolio, guarded artificial ports, reserved_cash mapped from the frozen first-day outer cash target only, and dry_run_only=True; compare first-open integer proposals with frozen research fills. Existing default $1000 safe-haven substitution and 1.005 buy-limit premium apply. No broker submission or account read.",
            "limits": "The platform mapping does not yet preserve the TQQQ member's internal cash reserve and the artificial snapshot omits real account admission facts. A dry-run order is not a fill; no bps research cost, lot ownership, post-open NAV, sale settlement, dividends, full-window recursion or live eligibility is validated. A mismatch diagnoses this mapping, not inherent platform impossibility.",
            "paths": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--schwab-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.parent != args.private_root / "phase5_capital_scale_v1":
        raise ValueError("PHASE5_SCHWAB_OUTPUT_OUTSIDE_PRIVATE_ROOT")
    data = (json.dumps(analyze(args.private_root, args.schwab_root), indent=2, sort_keys=True) + "\n").encode()
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
    print(json.dumps({"paths": 8, "summary_sha256": hashlib.sha256(data).hexdigest()}, sort_keys=True))
