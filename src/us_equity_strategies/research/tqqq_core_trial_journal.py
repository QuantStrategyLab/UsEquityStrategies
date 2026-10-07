"""Opt-in local, synthetic-only journal for the existing frozen TQQQ SMA study.

This module adds no strategy or selection algorithm. Its per-slot success means
a verified simulation/result/ledger triplet, not aggregate study qualification.
"""
from __future__ import annotations

from dataclasses import replace
from collections.abc import Mapping
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path

import pandas as pd

from quant_platform_kit.strategy_lifecycle import contracts as qpk_contracts
from quant_platform_kit.strategy_lifecycle import performance_metrics as qpk_metrics
from quant_platform_kit.strategy_lifecycle import performance_store as qpk_store
from quant_platform_kit.strategy_lifecycle.contracts import (
    BacktestResult, ResearchDailyLedger, ResearchLedgerDay, ResearchPositionMark,
    ResearchTrialRecord, ResearchTrialStatus,
)
from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore

from . import tqqq_core_optimization as core
from . import tqqq_offline_input_contract as inputs
from . import tqqq_trial_journal as journal
from . import tqqq_typed_baseline_result as baseline

PROFILE = "tqqq_core_optimization_sma_journal_v1"


def compute_synthetic_research_metrics(
    ledger: ResearchDailyLedger, *, annual_risk_free_rate: float,
    annual_minimum_acceptable_return: float,
    start_session: date | None = None, end_session: date | None = None,
) -> dict:
    """Pure opt-in analysis of a closed synthetic XNYS account, not v1 scoring.

    Rates are annual simple decimal fractions, divided by 252 per session.
    Window bounds identify included return sessions exactly; the preceding NAV
    is the initial drawdown mark, not an additional return observation. QPK
    supplies compounding, initial-mark drawdown and target-downside Sortino.
    Its population volatility is converted to sample volatility for Sharpe;
    Sortino instead uses full-sample RMS shortfall and the MAR numerator.
    Nothing is persisted, simulated, selected or granted execution authority.
    """
    return _compute_synthetic_account_metrics(
        ledger, calendar_id="XNYS", contract_version="synthetic_account_metrics_v1",
        annual_risk_free_rate=annual_risk_free_rate,
        annual_minimum_acceptable_return=annual_minimum_acceptable_return,
        start_session=start_session, end_session=end_session)


def compute_declared_session_research_metrics(
    ledger: ResearchDailyLedger, *, annual_risk_free_rate: float,
    annual_minimum_acceptable_return: float,
    start_session: date | None = None, end_session: date | None = None,
) -> dict:
    """Report a synthetic declared-session account on an assumed 252-step year.

    This independent contract accepts neither XNYS nor real-data ledgers.
    The caller's declared steps provide no market-calendar or PIT attestation.
    """
    report = _compute_synthetic_account_metrics(
        ledger, calendar_id="synthetic_declared_sessions",
        contract_version="synthetic_declared_session_account_metrics_v1",
        annual_risk_free_rate=annual_risk_free_rate,
        annual_minimum_acceptable_return=annual_minimum_acceptable_return,
        start_session=start_session, end_session=end_session)
    report["evaluation_contract"].update({
        "annualization_basis": "DECLARED_SYNTHETIC_252_STEPS_PER_YEAR",
        "session_scope": "CALLER_DECLARED_SEQUENCE_NOT_VERIFIED_MARKET_SESSIONS_OR_PIT",
    })
    return report


def _compute_synthetic_account_metrics(
    ledger: ResearchDailyLedger, *, calendar_id: str, contract_version: str,
    annual_risk_free_rate: float, annual_minimum_acceptable_return: float,
    start_session: date | None, end_session: date | None,
) -> dict:
    """Preserve the strict input boundary of both frozen synthetic contracts."""
    if not isinstance(ledger, ResearchDailyLedger):
        raise ValueError("RESEARCH_LEDGER_REQUIRED")
    if ledger.synthetic is not True:
        raise ValueError("REAL_RESEARCH_DATA_UNQUALIFIED")
    return _compute_account_metrics(
        ledger, calendar_id=calendar_id, contract_version=contract_version,
        annual_risk_free_rate=annual_risk_free_rate,
        annual_minimum_acceptable_return=annual_minimum_acceptable_return,
        start_session=start_session, end_session=end_session)


def compute_historical_research_metrics(
    ledger: ResearchDailyLedger, *, input_binding: Mapping,
    annual_risk_free_rate: float, annual_minimum_acceptable_return: float,
    start_session: date | None = None, end_session: date | None = None,
) -> dict:
    """Report historical modeled sleeves; binding is not PIT/OOS qualification.

    The active Batch A CLI verifies original snapshot bytes and re-materializes
    the pack before supplying this binding. This arithmetic-only API grants no
    data license, source authority, promotion or execution permissions.
    """
    if not isinstance(ledger, ResearchDailyLedger) or ledger.synthetic is not False:
        raise ValueError("HISTORICAL_RESEARCH_LEDGER_REQUIRED")
    digest = input_binding.get("pack_digest") if isinstance(input_binding, Mapping) else None
    if (not isinstance(digest, str) or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or input_binding.get("schema") != "qsl.batch-a-historical-input-binding.v1"
            or ledger.input_id != "sha256:" + digest):
        raise ValueError("HISTORICAL_INPUT_BINDING_INVALID")
    report = _compute_account_metrics(
        ledger, calendar_id="XNYS", contract_version="historical_research_account_metrics_v1",
        annual_risk_free_rate=annual_risk_free_rate,
        annual_minimum_acceptable_return=annual_minimum_acceptable_return,
        start_session=start_session, end_session=end_session)
    report.update({"data_qualified": False, "oos_qualified": False,
                   "evidence_scope": "HISTORICAL_SOURCE_SEEN_DEVELOPMENT_MODELED_SLEEVES",
                   "input_binding": json.loads(json.dumps(input_binding, allow_nan=False))})
    report["evaluation_contract"].update({
        "session_scope": "XNYS_DECLARED_BY_SNAPSHOT_NOT_INDEPENDENT_CALENDAR_OR_PIT_ATTESTATION",
        "historical_available_at_verified": False,
        "cash_return_policy": "ASSUMED_ZERO_USD_CASH",
        "initial_allocation_cost": "EXCLUDED_ALREADY_INVESTED_INITIAL_NAV",
        "trial_matrix_and_dependence_assumptions": "NOT_ESTABLISHED_SEEN_DEVELOPMENT_ONLY",
    })
    return report


def _compute_account_metrics(
    ledger: ResearchDailyLedger, *, calendar_id: str, contract_version: str,
    annual_risk_free_rate: float, annual_minimum_acceptable_return: float,
    start_session: date | None, end_session: date | None,
) -> dict:
    """One arithmetic body shared by the explicit report-only input contracts."""
    if ledger.calendar_id != calendar_id or ledger.periods_per_year != 252:
        raise ValueError("RESEARCH_BASIS_INVALID")
    rates = (annual_risk_free_rate, annual_minimum_acceptable_return)
    if any(type(rate) not in (int, float) or not math.isfinite(rate) for rate in rates):
        raise ValueError("RESEARCH_RATE_INVALID")
    if any(day.external_cashflow != 0.0 for day in ledger.days):
        raise ValueError("RESEARCH_EXTERNAL_FLOWS_UNSUPPORTED")
    sessions = [day.session_date for day in ledger.days]
    start = sessions[0] if start_session is None else start_session
    end = sessions[-1] if end_session is None else end_session
    if (type(start) is not date or type(end) is not date
            or start not in sessions or end not in sessions or start > end):
        raise ValueError("RESEARCH_WINDOW_INVALID")
    first, last = sessions.index(start), sessions.index(end)
    selected = ledger.days[first:last + 1]
    returns = tuple(day.daily_return for day in selected)
    count = len(returns)
    shared = qpk_metrics.compute_window_metrics(
        pd.Series(returns, index=pd.DatetimeIndex(day.session_date for day in selected)),
        risk_free_rate=annual_minimum_acceptable_return,
        periods_per_year=252, calendar_id=calendar_id)
    sharpe, sortino, volatility = None, None, None
    if count < 2:
        sharpe_status = sortino_status = "INSUFFICIENT_OBSERVATIONS"
    elif len(set(returns)) == 1:
        volatility = 0.0
        sharpe_status = sortino_status = "CONSTANT_RETURN_SAMPLE"
    else:
        # The pinned helper uses ddof=0. n/(n-1) gives the ddof=1 variance;
        # annualized Sharpe uses the corresponding sample standard deviation.
        volatility = shared.volatility * math.sqrt(count / (count - 1))
        daily_std = volatility / math.sqrt(252)
        sharpe = (math.fsum(returns) / count - annual_risk_free_rate / 252) / daily_std * math.sqrt(252)
        sharpe_status = "COMPUTED" if math.isfinite(sharpe) else "NONFINITE_RESULT"
        if sharpe_status != "COMPUTED":
            sharpe = None
        sortino = shared.sortino_ratio if math.isfinite(shared.sortino_ratio) else None
        sortino_status = ("COMPUTED" if sortino is not None else
                          "ZERO_DOWNSIDE_DEVIATION" if all(
                              r >= annual_minimum_acceptable_return / 252 for r in returns)
                          else "NONFINITE_RESULT")
    return {
        "research_only": True, "synthetic": ledger.synthetic, "promotion_eligible": False,
        "live_ready": False, "size_zero_required": True, "no_order": True,
        "trial_id": ledger.trial_id, "run_id": ledger.run_id,
        "strategy_profile": ledger.strategy_profile, "input_id": ledger.input_id,
        "initial_session": (ledger.initial_session_date if first == 0 else
                            ledger.days[first - 1].session_date).isoformat(),
        "start_session": start.isoformat(), "end_session": end.isoformat(),
        "observation_count": count,
        "metrics": {"cumulative_return": shared.total_return, "cagr": shared.cagr,
                    "max_drawdown": shared.max_drawdown,
                    "annualized_volatility": volatility, "sharpe": sharpe,
                    "sortino": sortino, "dsr": None, "pbo": None},
        "metric_status": {"sharpe": sharpe_status, "sortino": sortino_status,
                          "dsr": "NOT_COMPUTED", "pbo": "NOT_COMPUTED"},
        "effective_independent_observations": None,
        "effective_sample_size_status": "NOT_ESTIMATED",
        "evaluation_contract": {
            "version": contract_version, "role": "REPORT_ONLY",
            "unit": "STRATEGY_ACCOUNT_SESSION_SIMPLE_NET_RETURN",
            "return_unit": "SIGNED_DECIMAL_FRACTION",
            "calendar_id": calendar_id, "periods_per_year": 252,
            "real_calendar_verified": False, "zero_return_sessions": "INCLUDED",
            "initial_nav_is_return_observation": False, "initial_nav_in_drawdown": True,
            "rf": {"annual_simple_decimal": annual_risk_free_rate,
                   "per_session": annual_risk_free_rate / 252},
            "mar": {"annual_simple_decimal": annual_minimum_acceptable_return,
                    "per_session": annual_minimum_acceptable_return / 252},
            "annual_rate_conversion": "DIVIDE_BY_252_NOT_COMPOUND_RATE_CONVERSION",
            "sharpe": "MEAN_RETURN_MINUS_RF_OVER_SAMPLE_STD_TIMES_SQRT_252",
            "volatility_ddof": 1, "qpk_population_volatility_adapter": "MULTIPLY_SQRT_N_OVER_N_MINUS_1",
            "sortino": "MEAN_RETURN_MINUS_MAR_OVER_FULL_SAMPLE_RMS_SHORTFALL_TIMES_SQRT_252",
            "cagr": "COMPOUNDED_NET_RETURN_POWER_252_OVER_OBSERVATION_COUNT",
            "cost": {"source": ledger.cost_source, "inputs": dict(ledger.cost_inputs),
                     "net_nav_costs_deducted_again": False,
                     "cash_and_positions": "WHOLE_ACCOUNT_NAV",
                     "cash_interest": "AS_RECORDED_IN_NAV_NO_ADDITIONAL_ACCRUAL",
                     "external_flows": "NONE_CLOSED_RESEARCH_ACCOUNT"},
            "undefined_policy": "NONE_WITH_REASON_NO_ZERO_FILL",
            "constant_return_ratio_policy": "BOTH_RATIOS_NONE_EVEN_IF_MAR_SHORTFALL_NONZERO",
            "uncomputed_metrics": ["dsr", "pbo"],
            "trial_matrix_and_dependence_assumptions": "NOT_ESTABLISHED",
        },
    }


def report_journaled_tqqq_core_metrics(
    *, store: PerformanceStore, trial_id: str, annual_risk_free_rate: float,
    annual_minimum_acceptable_return: float,
    start_session: date | None = None, end_session: date | None = None,
) -> dict:
    """Read one verified v1 success into a separate synthetic metric report.

    This is not an aggregate study or trial filter. Failed/incomplete trials
    remain in the original journal and are refused here, never reconstructed.
    The pinned store verifies the result/ledger/terminal triplet on readback.
    No simulation, selection, persistence or frozen-result mutation occurs.
    """
    if (not isinstance(store, PerformanceStore) or store.cloud_bucket
            or not isinstance(store.local_root, Path) or store.local_root.is_symlink()
            or not store.local_root.is_dir()):
        raise ValueError("LOCAL_RESEARCH_STORE_REQUIRED")
    record = store.load_research_trial("us_equity", PROFILE, trial_id)
    if record is None:
        raise ValueError("RESEARCH_TRIAL_READBACK_INVALID")
    if record.status is not ResearchTrialStatus.SUCCEEDED:
        raise ValueError("RESEARCH_TRIAL_NOT_SUCCEEDED:" + record.status.value)
    ledger = store.load_research_ledger(
        record.domain, record.strategy_profile, record.trial_id,
        record.run_id, record.param_version)
    report = compute_synthetic_research_metrics(
        ledger, annual_risk_free_rate=annual_risk_free_rate,
        annual_minimum_acceptable_return=annual_minimum_acceptable_return,
        start_session=start_session, end_session=end_session)
    report.update({
        "report_profile": "tqqq_core_trial_metric_report_v1",
        "source_trial_status": record.status.value,
        "source_candidate": json.loads(json.dumps(record.actual_params)),
        "scope": "ONE_VERIFIED_SYNTHETIC_TRIAL_NOT_AGGREGATE_OR_SELECTION",
    })
    return report


def _evaluation_contract() -> dict:
    """Describe this caller's existing calculations, not new scoring or qualification.

    The executing implementation hashes bind this description to the evaluator.
    Synthetic characterization tests check its numeric and decision semantics.
    Configured bootstrap counts are not attestations that evaluation completed.
    """
    return {
        "version": "tqqq_sma_evaluation_contract_v1",
        "scope": "THIS_SYNTHETIC_JOURNALED_CALLER_ONLY",
        "source_functions": ["core._window_metrics", "core._five_metric_winner",
                             "core._eligibility", "core._terminal_loss_probability",
                             "_build_ledger"],
        "sample": {
            "unit": "STRATEGY_ACCOUNT_SESSION_SIMPLE_NET_RETURN",
            "return_unit": "SIGNED_DECIMAL_FRACTION", "currency": "USD",
            "zero_return_sessions": "INCLUDED", "initial_nav_is_return_observation": False,
            "effective_independent_observations": None,
            "effective_sample_size_status": "NOT_ESTIMATED",
            "minimum_window_observations": 2,
            "insufficient_window_behavior": "NO_VALID_WINDOW_RESULT",
        },
        "timing": {"signal": "PREVIOUS_SESSION_QQQ_CLOSE_INCLUSIVE_SMA",
                   "execution": "NEXT_TQQQ_OPEN", "valuation": "SOURCE_SESSION_CLOSE",
                   "calendar_id": "XNYS", "real_calendar_verified": False},
        "cost": {
            "parameter_unit": "BASIS_POINTS_DIVIDED_BY_10000",
            "amount_unit": "USD", "net_nav_costs_deducted_again": False,
            "total_cost": "COMMISSION_PLUS_SLIPPAGE_IMPACT_VS_OPEN",
            "ledger_fees": "COMMISSION_ONLY_SLIPPAGE_ALREADY_IN_TRADE_CASHFLOW",
            "cash_interest": "NOT_ACCRUED_IN_FROZEN_MODEL",
            "external_flows": "NONE_IN_CLOSED_RESEARCH_MODEL",
            "corporate_actions": "NOT_ACTION_COMPLETE",
            "financing_market_impact_fx": "NOT_SEPARATELY_MODELED",
        },
        "rf": {"status": "NOT_USED", "reason": "NO_EXCESS_RETURN_METRIC_COMPUTED"},
        "mar": {"status": "NOT_USED", "reason": "NO_DOWNSIDE_RATIO_COMPUTED"},
        "volatility": {"ddof": 1, "periods_per_year": 252,
                       "annualization": "SQRT_PERIODS_PER_YEAR"},
        "journal_cagr": {"basis": "NET_NAV_RATIO_POWER_252_OVER_RETURN_OBSERVATIONS",
                         "role": "REPORT"},
        "drawdown": {"initial_nav_included": True, "unit": "NONPOSITIVE_DECIMAL_RETURN",
                     "recovery_duration": "NOT_COMPUTED"},
        "expected_shortfall_95": {
            "method": "MEAN_OF_LOWEST_CEIL_N_TIMES_0_05_SESSION_RETURNS",
            "nominal_tail_fraction": 0.05, "sign": "SIGNED_RETURN_LOWER_IS_WORSE",
            "tail_count_source": "WINDOW_EXPECTED_SHORTFALL_95_EVIDENCE",
        },
        "trade_count": "POSITION_STATE_TRANSITIONS_NOT_PAIRED_ROUND_TRIPS",
        "uncomputed_metrics": ["sharpe", "sortino", "dsr", "pbo"],
        "undefined_policy": "NO_ZERO_FILL_FOR_ABSENT_UNDEFINED_OR_INSUFFICIENT_METRICS",
        "metric_roles": {
            "cumulative_return": ["REPORT", "PARETO_COMPARISON", "ELIGIBILITY_VETO"],
            "max_drawdown": ["REPORT", "PARETO_COMPARISON"],
            "annualized_volatility": ["REPORT", "PARETO_COMPARISON"],
            "expected_shortfall_95": ["REPORT", "PARETO_COMPARISON"],
            "stress_cumulative_return": ["REPORT", "STRESS_COMPARISON", "ELIGIBILITY_VETO"],
            "mc_terminal_loss_probability_c2_5": ["REPORT", "ELIGIBILITY_VETO"],
            "total_cost": ["REPORT"], "trade_count": ["REPORT"],
        },
        "comparison": {
            "baseline_window_days": core.BASELINE_WINDOW_DAYS,
            "selection": "UNIQUE_STRICT_FOUR_METRIC_PARETO_THEN_STRESS_NOT_WORSE",
            "tie_or_no_winner": "RETAIN_BASELINE",
            "windows": "FOLD_VALIDATION_AND_FINAL_HOLDOUT_RECOMMENDATION_COMPARISON",
            "new_absolute_risk_thresholds": False,
        },
        "eligibility": {"positive_folds_minimum": 2, "fold_count": 3,
                        "final_c2_5_return_strictly_above": 0.0,
                        "final_stress_return_strictly_above": 0.0,
                        "terminal_loss_probability_strictly_below": 0.5},
        "uncertainty": {
            "configured_method": "CIRCULAR_MOVING_BLOCK_BOOTSTRAP",
            "configured_path_count": core.MC_TRIALS,
            "configured_path_length": core.MC_PATH_LENGTH,
            "configured_block_length": core.MC_BLOCK_LENGTH,
            "path_count_is_independent_observation_count": False,
            "autocorrelation_adjusted_sharpe": "NOT_COMPUTED",
            "execution_evidence": "EXISTING_AGGREGATE_RESULT_WHEN_AVAILABLE",
        },
        "freeze_scope": "CONFIGURATION_BEFORE_THIS_ATTEMPTS_SIMULATIONS",
        "untouched_holdout_established": False,
        "forward_observations_established": False,
        "promotion_or_execution_authority": False,
    }


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _implementation() -> dict[str, str]:
    # Hash the actual executing helper as well as both callers and pinned contracts.
    hashes = {"sma_journal": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    hashes.update({name: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                   for name, module in (
                       ("sma_evaluation", core), ("trial_journal", journal),
                       ("offline_input", inputs), ("typed_baseline", baseline),
                       ("qpk_contracts", qpk_contracts), ("qpk_performance_store", qpk_store))})
    return hashes


def _same(actual: float, expected: float) -> None:
    if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("SMA_LEDGER_ACCOUNTING_MISMATCH")


def _build_ledger(source: inputs.OfflineInput, started: ResearchTrialRecord,
                  points: tuple[core.DailyPoint, ...], computed_at: str
                  ) -> tuple[BacktestResult, ResearchDailyLedger]:
    """Adapt fractional shares, commission USD and adverse-fill cashflow without resimulation."""
    params = started.actual_params
    window = params["window_days"]
    qqq, tqqq = core._typed_rows(source, core.EXPECTED_INPUT_DIGEST)
    if (type(points) is not tuple or len(points) != len(tqqq) - window
            or any(type(point) is not core.DailyPoint for point in points)):
        raise ValueError("SMA_LEDGER_WINDOW_INVALID")
    initial = params["initial_equity_usd"]
    previous_nav, previous_cash, previous_quantity = initial, initial, 0.0
    commission_rate = params["commission_bps"] / 10_000.0
    slippage_rate = params["slippage_bps"] / 10_000.0
    days = []
    peak, max_drawdown = initial, 0.0
    for row, point in zip(tqqq[window:], points, strict=True):
        values = (point.start_equity, point.end_equity, point.cash, point.quantity,
                  point.daily_return, point.commission_paid, point.slippage_impact_vs_open,
                  point.gross_traded_notional_at_open)
        if (any(type(value) not in (int, float) or not math.isfinite(value) for value in values)
                or point.date != row.as_of or point.start_equity != previous_nav
                or point.cash < 0 or point.quantity < 0 or point.commission_paid < 0
                or point.slippage_impact_vs_open < 0 or type(point.transition) is not bool):
            raise ValueError("SMA_LEDGER_POINT_INVALID")
        trade = point.quantity - previous_quantity
        if point.transition != ((point.quantity > 0.0) != (previous_quantity > 0.0)):
            raise ValueError("SMA_LEDGER_ACCOUNTING_MISMATCH")
        if not point.transition and trade != 0.0:
            raise ValueError("SMA_LEDGER_ACCOUNTING_MISMATCH")
        fill = row.open * (1.0 + slippage_rate if trade > 0 else 1.0 - slippage_rate)
        trade_cash = -trade * fill
        _same(point.gross_traded_notional_at_open, abs(trade) * row.open)
        _same(point.commission_paid, abs(trade) * fill * commission_rate)
        _same(point.slippage_impact_vs_open, abs(trade) * abs(fill - row.open))
        _same(point.cash, previous_cash + trade_cash - point.commission_paid)
        _same(point.end_equity, point.cash + point.quantity * row.close)
        if point.daily_return != point.end_equity / previous_nav - 1.0:
            raise ValueError("SMA_LEDGER_ACCOUNTING_MISMATCH")
        positions = (() if point.quantity == 0.0 else
                     (ResearchPositionMark("TQQQ", point.quantity, point.quantity * row.close),))
        days.append(ResearchLedgerDay(
            session_date=date.fromisoformat(point.date), cash=point.cash, positions=positions,
            trade_net_cashflow=trade_cash, fees=point.commission_paid,
            nav=point.end_equity, daily_return=point.daily_return))
        peak = max(peak, point.end_equity)
        max_drawdown = min(max_drawdown, point.end_equity / peak - 1.0)
        previous_nav, previous_cash, previous_quantity = point.end_equity, point.cash, point.quantity
    ledger = ResearchDailyLedger(
        trial_id=started.trial_id, domain=started.domain, strategy_profile=started.strategy_profile,
        run_id=started.trial_id, param_version=1, input_id=started.input_id,
        calendar_id=started.calendar_id, periods_per_year=started.periods_per_year,
        cost_source=started.cost_source, cost_inputs=dict(started.cost_inputs),
        initial_session_date=date.fromisoformat(qqq[window - 1].as_of),
        initial_nav=initial, initial_cash=initial, initial_positions=(),
        days=tuple(days), synthetic=True)
    result_params = dict(params)
    result_params["research_identity"] = dict(started.research_identity)
    result = BacktestResult(
        strategy_profile=started.strategy_profile, domain=started.domain,
        param_set_id=started.param_set_id, params=result_params, param_version=1,
        max_drawdown=max_drawdown,
        cagr=(ledger.days[-1].nav / initial) ** (252.0 / len(days)) - 1.0,
        total_return=ledger.total_return, start_date=ledger.window_start,
        end_date=ledger.window_end, observation_count=ledger.observation_count,
        run_id=ledger.run_id, source_script=__name__, computed_at=computed_at,
        source_revision=started.source_revision, cost_model=ledger.cost_source,
        cost_inputs=dict(ledger.cost_inputs), calendar_id=ledger.calendar_id,
        periods_per_year=ledger.periods_per_year)
    return result, ledger


def run_journaled_tqqq_core_optimization(
    source: inputs.OfflineInput, *, store: PerformanceStore,
    trial_namespace: str, synthetic: bool,
) -> dict:
    """Journal new synthetic attempts only; never resume or backfill an old attempt.

    The explicit synthetic classification never establishes input qualification.
    A production durable store and real-run authorization remain outside this API.
    This is single-caller replay protection, not a concurrent-worker atomic claim.
    """
    if synthetic is not True:
        raise ValueError("REAL_RESEARCH_DATA_UNQUALIFIED")
    if (not isinstance(store, PerformanceStore) or store.cloud_bucket
            or not isinstance(store.local_root, Path) or store.local_root.is_symlink()
            or (store.local_root.exists() and not store.local_root.is_dir())):
        raise ValueError("LOCAL_RESEARCH_STORE_REQUIRED")
    if (type(trial_namespace) is not str or not trial_namespace or len(trial_namespace) > 300
            or any(char.isspace() or ord(char) < 32 for char in trial_namespace)):
        raise ValueError("TRIAL_NAMESPACE_INVALID")
    qqq, _ = core._typed_rows(source, core.EXPECTED_INPUT_DIGEST)
    core._verify_immutable_input(source)
    implementation = _implementation()
    source_revision = "sha256:" + _digest(implementation)
    artifact_digest = hashlib.sha256(source.canonical_bytes).hexdigest()
    identity = {
        "verified_canonical_artifact_sha256": artifact_digest,
        "accepted_input_digest": source.input_digest,
        "input_digest_semantics": "MATCHES_EXPECTED_LITERAL_ONLY",
        "implementation_sha256": implementation,
        "corporate_action_scope": "unchanged_frozen_sma_model_not_action_complete",
        "external_cashflow_scope": "closed_research_no_external_flows",
    }
    config = {
        "evaluation_contract": _evaluation_contract(),
        "candidate_windows": list(core.CANDIDATE_WINDOWS),
        "scenarios": [[x.scenario_id, x.commission_bps, x.slippage_bps] for x in core.SCENARIOS],
        "initial_equity_usd": core.INITIAL_EQUITY, "plugin_control": dict(core.PLUGIN_CONTROL),
        "window_specs": [list(x) for x in core.WINDOW_SPECS],
        "baseline_window_days": core.BASELINE_WINDOW_DAYS,
        "mc_seed_hex": core.MC_SEED_HEX, "mc_trials": core.MC_TRIALS,
        "mc_path_length": core.MC_PATH_LENGTH, "mc_block_length": core.MC_BLOCK_LENGTH,
    }
    started_records = {}
    for window in core.CANDIDATE_WINDOWS:
        for scenario in core.SCENARIOS:
            key = f"sma{window}:{scenario.scenario_id}"
            params = {"window_days": window, "scenario_id": scenario.scenario_id,
                      "commission_bps": scenario.commission_bps, "slippage_bps": scenario.slippage_bps,
                      "initial_equity_usd": core.INITIAL_EQUITY, "study_config": config}
            started_records[(window, scenario.scenario_id)] = ResearchTrialRecord(
                trial_id=f"{trial_namespace}:{key}", domain="us_equity", strategy_profile=PROFILE,
                status=ResearchTrialStatus.STARTED, candidate_config_id="sha256:" + _digest(config),
                actual_params=json.loads(json.dumps(params)), param_set_id=key,
                source_revision=source_revision, input_id="sha256:" + artifact_digest,
                window_start=date.fromisoformat(qqq[window - 1].as_of),
                window_end=date.fromisoformat(qqq[-1].as_of), calendar_id="XNYS",
                periods_per_year=252.0, cost_source="synthetic_frozen_sma_commission_slippage_bps",
                cost_inputs={"commission_bps": float(scenario.commission_bps),
                             "slippage_bps": float(scenario.slippage_bps)},
                reason_code="", synthetic=True, run_id=None, param_version=None,
                research_identity=identity)
    previous = []
    for started in started_records.values():
        record = store.load_research_trial(started.domain, started.strategy_profile, started.trial_id)
        # QPK's typed read returns None for both missing and corrupt evidence.
        # Use the same pinned store's raw existence read, never mislabel corruption
        # as an unattempted slot or silently reconstruct an archived result.
        if record is None and any(store._research_text(store._research_key(
                started.domain, started.strategy_profile, started.trial_id, name)) is not None
                for name in ("started", "terminal", "ledger")):
            raise ValueError("RESEARCH_TRIAL_READBACK_INVALID")
        if record is not None and replace(record, status=ResearchTrialStatus.STARTED, reason_code="",
                                         run_id=None, param_version=None) != started:
            raise ValueError("research_trial_conflict")
        previous.append(record)
    report = {
        "evaluation_contract": json.loads(json.dumps(config["evaluation_contract"])),
        "research_only": True, "synthetic": True, "data_qualified": False,
        "execution_authorized": False, "promotion_authorized": False, "no_order": True,
        "trial_namespace": trial_namespace, "implementation_sha256": implementation,
        "journal": {"trial_ids": [x.trial_id for x in started_records.values()], "statuses": {}},
        "optimization": None,
    }
    if any(record is not None for record in previous):
        statuses = [record.status.value if record else "not_started" for record in previous]
        report["journal"]["statuses"] = dict(zip(report["journal"]["trial_ids"], statuses, strict=True))
        failures = [value for value in statuses if value in {"failed", "rejected", "aborted"}]
        report["status"] = (failures[0] if failures else
                            "stored" if all(value == "succeeded" for value in statuses) else "incomplete")
        return report
    computed_at = datetime.now(timezone.utc).isoformat()
    evaluation_errors = []

    def simulate(src, window, scenario):
        started = started_records[(window, scenario.scenario_id)]
        frozen = json.dumps(started.actual_params, sort_keys=True, separators=(",", ":"), allow_nan=False)

        def calculate(params):
            result = core.simulate_candidate(src, params["window_days"], core.CostScenario(
                params["scenario_id"], params["commission_bps"], params["slippage_bps"]))
            if core.INITIAL_EQUITY != params["initial_equity_usd"]:
                raise ValueError("SIMULATOR_PARAMS_CHANGED")
            return result

        def adapt(readback, params, points):
            return _build_ledger(src, readback, points, computed_at)

        try:
            terminal, points = journal._journal_trial(started, store, frozen, calculate, adapt)
            if points is None:
                raise ValueError("RESEARCH_TRIAL_CHANGED_DURING_EVALUATION")
            report["journal"]["statuses"][terminal.trial_id] = terminal.status.value
            return points
        except Exception as exc:
            evaluation_errors.append(exc)
            raise

    result = core._run_tqqq_core_optimization(source, plugin_control=core.PLUGIN_CONTROL,
        expected_input_digest=core.EXPECTED_INPUT_DIGEST, simulate=simulate)
    if evaluation_errors:
        # The legacy evaluator sanitizes ordinary errors; storage callers must see the original.
        raise evaluation_errors[0]
    report["optimization"] = result
    report["status"] = "succeeded" if result["evidence_valid"] else "rejected"
    return report
