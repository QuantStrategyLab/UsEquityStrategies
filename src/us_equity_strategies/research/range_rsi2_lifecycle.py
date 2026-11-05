"""Link frozen retrospective RSI2 trials to validated QPK research ledgers."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from io import StringIO
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from quant_platform_kit.strategy_lifecycle.contracts import (
    BacktestResult, ResearchDailyLedger, ResearchLedgerDay, ResearchPositionMark, ResearchTrialStatus,
)
from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore
from .range_rsi2_study import FROZEN_PREREGISTRATION_SHA256, load_pair, simulate, metrics


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_trial_registry(registry, config, source_digest):
    """Verify the complete frozen grid and each content-derived trial identity."""
    expected = set()
    for symbol in config["input_files"]:
        for cost in config["costs"]:
            for lag in config["execution"]["lag_sessions"]:
                for strategy in config["strategies"]:
                    expected.add((symbol, strategy, cost["id"], lag))
    if (not isinstance(registry, dict) or registry.get("schema_version") != "qsl.range_rsi2_trial_registry.v1"
            or registry.get("preregistration_sha256") != FROZEN_PREREGISTRATION_SHA256
            or registry.get("classification") != "retrospective_development_only"
            or registry.get("promotion_eligible") is not False):
        raise ValueError("invalid trial registry metadata")
    trials = registry.get("trials")
    if not isinstance(trials, list) or len(expected) != 64 or len(trials) != 64:
        raise ValueError("invalid trial grid")
    actual = set()
    for item in trials:
        fields = {"symbol", "strategy", "cost", "lag", "input_sha256", "source", "preregistration_sha256", "trial_id"}
        if not isinstance(item, dict) or set(item) != fields:
            raise ValueError("invalid trial identity fields")
        key = (item["symbol"], item["strategy"], item["cost"], item["lag"])
        if key in actual or key not in expected:
            raise ValueError("duplicate or unexpected trial")
        actual.add(key)
        if (item["input_sha256"] != config["input_files"][item["symbol"]]["sha256"]
                or item["source"] != "sha256-" + source_digest
                or item["preregistration_sha256"] != FROZEN_PREREGISTRATION_SHA256
                or item["trial_id"] != _digest({k: v for k, v in item.items() if k != "trial_id"})):
            raise ValueError("trial identity mismatch")
    if actual != expected:
        raise ValueError("incomplete trial grid")


def validate_stored_trial(trial, item, config):
    cost = next(c for c in config["costs"] if c["id"] == item["cost"])
    expected_costs = {"commission_bps": float(cost["commission_bps"]),
                      "slippage_bps": float(cost["slippage_bps"]), "market_impact_bps": 0.0}
    if (trial.candidate_config_id != item["strategy"] or trial.param_set_id != item["strategy"]
            or trial.source_revision != item["source"] or trial.input_id != item["input_sha256"]
            or trial.cost_source != item["cost"] or dict(trial.cost_inputs) != expected_costs
            or trial.synthetic is not False or trial.window_start != date.fromisoformat(config["evaluation_start"])
            or trial.window_end != date.fromisoformat(config["evaluation_end"])
            or trial.calendar_id != "paired_adjusted_price_sessions" or trial.periods_per_year != 252.0):
        raise ValueError("STORED_TRIAL_IDENTITY_MISMATCH")


def _trade_cashflow(previous_cash, current_cash, fee):
    value = current_cash - previous_cash + fee
    for candidate in (value, math.nextafter(value, math.inf), math.nextafter(value, -math.inf)):
        if abs(previous_cash + candidate - fee - current_cash) <= 1e-9:
            return candidate
    raise ValueError("ledger cash rounding exceeds QPK tolerance")


def build_qpk_ledger(started, frame, pair, run_id):
    """Normalize marks; QPK excludes the first marked day from returns."""
    if frame.empty or len(frame) < 2:
        raise ValueError("insufficient daily observations")
    indices = {day.strftime("%Y-%m-%d"): i for i, day in enumerate(pair.dates)}
    def mark(row):
        index = indices[row["date"]]
        positions = []
        for symbol, quantity, close in ((pair.symbol, float(row["levered_quantity"]), pair.closes[index, 0]),
                                        (pair.signal_symbol, float(row["underlying_quantity"]), pair.closes[index, 1])):
            if quantity:
                positions.append(ResearchPositionMark(symbol=symbol, quantity=quantity, valuation=quantity * float(close)))
        cash = float(row["cash"])
        nav = cash + sum(item.valuation for item in positions)
        if not math.isclose(nav, float(row["nav"]), rel_tol=1e-12, abs_tol=1e-7):
            raise ValueError("ledger mark mismatch")
        return cash, tuple(positions), nav
    rows = list(frame.to_dict("records"))
    initial_cash, initial_positions, initial_nav = mark(rows[0])
    previous_cash, previous_nav = initial_cash, initial_nav
    days = []
    for row in rows[1:]:
        cash, positions, nav = mark(row)
        fee = float(row["commission"])
        days.append(ResearchLedgerDay(session_date=date.fromisoformat(row["date"]), cash=cash,
            positions=positions, trade_net_cashflow=_trade_cashflow(previous_cash, cash, fee), fees=fee,
            nav=nav, daily_return=nav / previous_nav - 1.0))
        previous_cash, previous_nav = cash, nav
    return ResearchDailyLedger(trial_id=started.trial_id, domain=started.domain,
        strategy_profile=started.strategy_profile, run_id=run_id, param_version=1, input_id=started.input_id,
        calendar_id=started.calendar_id, periods_per_year=started.periods_per_year,
        cost_source=started.cost_source, cost_inputs=started.cost_inputs,
        initial_session_date=date.fromisoformat(rows[0]["date"]), initial_nav=initial_nav,
        initial_cash=initial_cash, initial_positions=initial_positions, days=tuple(days), synthetic=False)


def validate_frozen_path(path, pair, *, strategy, cost, lag, expected_metrics):
    """Recompute the entire preregistered path before accepting stored bytes."""
    calculated = simulate(pair, strategy, cost["commission_bps"], cost["slippage_bps"], lag)
    stream = StringIO()
    calculated.to_csv(stream, float_format="%.17g")
    data = Path(path).read_bytes()
    if data != stream.getvalue().encode("utf-8"):
        raise ValueError("frozen daily path mismatch")
    if metrics(calculated) != {key: value for key, value in expected_metrics.items()
                               if key not in {"symbol", "strategy", "cost", "lag"}}:
        raise ValueError("frozen metrics mismatch")
    return pd.read_csv(path, float_precision="round_trip"), hashlib.sha256(data).hexdigest()


def record_linkage_failure(store, trial, *, terminal_write_attempted):
    """Report uncertainty when a terminal may already have been persisted."""
    if trial.status is not ResearchTrialStatus.STARTED:
        return "INTEGRITY_CHECK_FAILED"
    if terminal_write_attempted:
        return "PERSISTENCE_UNCERTAIN"
    try:
        failed = replace(trial, status=ResearchTrialStatus.FAILED, reason_code="ledger_linkage_failed")
        store.save_research_trial(failed)
        if store.load_research_trial(trial.domain, trial.strategy_profile, trial.trial_id) != failed:
            return "PERSISTENCE_UNCERTAIN"
        return "FAILED"
    except Exception:
        return "PERSISTENCE_UNCERTAIN"


def complete_lifecycle(output_dir: Path, preregistration: Path, input_paths: dict[str, Path]):
    """Write terminal results only after QPK validates each complete marked ledger."""
    output_dir = Path(output_dir)
    prereg = preregistration.read_bytes()
    if hashlib.sha256(prereg).hexdigest() != FROZEN_PREREGISTRATION_SHA256:
        raise ValueError("PREREGISTRATION_CHANGED")
    config = json.loads(prereg)
    results = json.loads((output_dir / "results.json").read_text())
    registry_bytes = (output_dir / "trial_registry.json").read_bytes()
    registry = json.loads(registry_bytes)
    if results["trial_registry_sha256"] != hashlib.sha256(registry_bytes).hexdigest() or len(registry["trials"]) != 64:
        raise ValueError("TRIAL_REGISTRY_MISMATCH")
    source_digest = hashlib.sha256(Path(__file__).with_name("range_rsi2_study.py").read_bytes()).hexdigest()
    if results["script_sha256"] != source_digest or results["run_count"] != 64:
        raise ValueError("STUDY_SOURCE_OR_RESULT_MISMATCH")
    validate_trial_registry(registry, config, source_digest)
    pairs = {symbol: load_pair(symbol, config, input_paths) for symbol in config["input_files"]}
    metric_rows = {(r["symbol"], r["strategy"], r["cost"], r["lag"]): r for r in results["results"]}
    if len(metric_rows) != 64:
        raise ValueError("STUDY_RESULTS_INCOMPLETE")
    store = PerformanceStore(local_root=output_dir / "performance_store")
    outcomes = []
    for item in registry["trials"]:
        profile = "range_rsi2_" + item["symbol"].lower()
        started = store.load_research_trial("us_equity", profile, item["trial_id"])
        if started is None:
            raise ValueError("MISSING_STARTED_TRIAL")
        validate_stored_trial(started, item, config)
        terminal_write_attempted = False
        try:
            key = (item["symbol"], item["strategy"], item["cost"], item["lag"])
            study_metrics = metric_rows[key]
            params = {"symbol": item["symbol"], "strategy": item["strategy"], "cost": item["cost"],
                      "lag": item["lag"], "preregistration_sha256": FROZEN_PREREGISTRATION_SHA256,
                      "frozen_study_metrics_sha256": _digest(study_metrics),
                      "qpk_return_convention": "exclude_first_marked_session"}
            filename = f"{item['symbol']}_{item['strategy']}_{item['cost']}_lag{item['lag']}.csv"
            cost = next(c for c in config["costs"] if c["id"] == item["cost"])
            frame, path_sha256 = validate_frozen_path(output_dir / "daily_paths" / filename,
                pairs[item["symbol"]], strategy=item["strategy"], cost=cost, lag=item["lag"],
                expected_metrics=study_metrics)
            if started.status is not ResearchTrialStatus.STARTED:
                if started.status is not ResearchTrialStatus.SUCCEEDED or dict(started.actual_params or {}) != params:
                    raise ValueError("existing terminal identity mismatch")
                existing_ledger = store.load_research_ledger("us_equity", profile, item["trial_id"],
                                                             started.run_id, started.param_version)
                if existing_ledger != build_qpk_ledger(started, frame, pairs[item["symbol"]], started.run_id):
                    raise ValueError("existing QPK ledger differs from frozen path")
                outcomes.append({"trial_id": item["trial_id"], "status": "SUCCEEDED",
                                 "frozen_daily_path_sha256": path_sha256, "recomputed_path_verified": True,
                                 "frozen_study_metrics_sha256": params["frozen_study_metrics_sha256"],
                                 "qpk_total_return_excluding_first_mark": existing_ledger.total_return})
                continue
            run_id = "qsl-range-rsi2-" + item["trial_id"]
            ledger = build_qpk_ledger(started, frame, pairs[item["symbol"]], run_id)
            result = BacktestResult(strategy_profile=profile, domain="us_equity", param_set_id=started.param_set_id,
                params=params, param_version=1, start_date=started.window_start, end_date=started.window_end,
                observation_count=ledger.observation_count, total_return=ledger.total_return,
                run_id=run_id, source_script="range_rsi2_study", computed_at=datetime.now(timezone.utc).isoformat(),
                source_revision=started.source_revision, cost_model=started.cost_source, cost_inputs=started.cost_inputs,
                periods_per_year=252.0, calendar_id=started.calendar_id)
            terminal = replace(started, status=ResearchTrialStatus.SUCCEEDED, actual_params=params,
                               run_id=run_id, param_version=1)
            store.save_backtest_result(result)
            store.save_research_ledger(ledger)
            terminal_write_attempted = True
            store.save_research_trial(terminal)
            if store.load_research_trial("us_equity", profile, item["trial_id"]) != terminal:
                raise ValueError("terminal readback mismatch")
            outcomes.append({"trial_id": item["trial_id"], "status": "SUCCEEDED",
                             "frozen_study_metrics_sha256": params["frozen_study_metrics_sha256"],
                             "frozen_daily_path_sha256": path_sha256, "recomputed_path_verified": True,
                             "qpk_total_return_excluding_first_mark": ledger.total_return})
        except Exception as exc:
            failure_status = record_linkage_failure(store, started, terminal_write_attempted=terminal_write_attempted)
            outcomes.append({"trial_id": item["trial_id"], "status": failure_status, "error": type(exc).__name__,
                             "detail": str(exc)[:200]})
    report = {"outcomes": outcomes, "count": len(outcomes), "promotion_eligible": False,
              "linker_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "qpk_return_convention": "exclude_first_marked_session",
              "study_return_convention": "include_first_session_vs_flat_initial_cash",
              "cashflow_provenance": "derived from recorded cash balances and fees; not broker fill evidence"}
    (output_dir / "qpk_lifecycle_results.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Link the frozen study to QPK research ledgers.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--soxl-input", type=Path, required=True)
    parser.add_argument("--tqqq-input", type=Path, required=True)
    args = parser.parse_args(argv)
    result = complete_lifecycle(args.output_dir, args.preregistration,
                                {"SOXL": args.soxl_input, "TQQQ": args.tqqq_input})
    print(json.dumps({"count": result["count"], "failed": sum(x["status"] != "SUCCEEDED" for x in result["outcomes"])}))


if __name__ == "__main__":
    main()
