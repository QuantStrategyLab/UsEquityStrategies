"""Synthetic counterexamples for the RS-04 B1/B2 preparation contract.

Fixture seed and absolute tolerance are engineering bindings only.
They do not freeze research parameters, fees, caps, PIT, holdout, or a journal.
"""
import importlib
import inspect
import math
import os
import random
from datetime import date, timedelta

import pytest

from us_equity_strategies.research import b1_b2_rs04_preparation as prep
from us_equity_strategies.research import kelly_core_return_generator as gen
from us_equity_strategies.research import kelly_private_runner as runner
from us_equity_strategies.research.fractional_kelly_walk_forward import _scaled_path


def _has_execution_models():
    try:
        importlib.import_module("quant_platform_kit.research_stats.execution_models")
        return True
    except ImportError:
        return False


def _has_unified_report():
    try:
        importlib.import_module("quant_platform_kit.research_stats.unified_report")
        return True
    except ImportError:
        return False


requires_execution_models = pytest.mark.skipif(
    not _has_execution_models() and os.environ.get("UES_REQUIRE_QPK_EXECUTION_MODELS") != "1",
    reason="installed QPK lacks opt-in A2 execution models; pin remains unchanged",
)
requires_unified_report = pytest.mark.skipif(
    not _has_unified_report() and os.environ.get("UES_REQUIRE_UNIFIED_NET_REPORT") != "1",
    reason="installed QPK lacks the unified net report; pin remains unchanged",
)


def _days(n, start=date(2026, 1, 5)):
    out, day = [], start
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def test_proposal_marks_gaps_and_refuses_a_research_freeze():
    proposal = prep.RECOMMENDED_PROPOSAL
    assert proposal["state"] == "RECOMMENDED_NOT_FROZEN"
    assert proposal["research_frozen"] is False
    assert proposal["live_ready"] is False
    assert proposal["no_order"] is True
    assert proposal["budget_effect"] == "NONE"
    assert proposal["cartesian_product"] is False
    assert proposal["har_rv"] == "PARKED"
    for key in ("fee_bps", "existing_cap", "d_max", "target_vol", "pit", "holdout", "journal",
                "ewma_initial_variance", "ewma_warmup_sessions"):
        assert proposal[key] in {"NOT_ATTESTED", "UNSPECIFIED"}
    assert proposal["ewma_lambda"]["status"] == "RECOMMENDED_NOT_FROZEN"
    assert proposal["ewma_lambda"]["scope_attestation"] == "NOT_ATTESTED"
    assert prep.SYNTHETIC_COUNTEREXAMPLE["seed"] == 20261014
    assert prep.SYNTHETIC_COUNTEREXAMPLE["absolute_tolerance"] == 1e-12
    assert prep.SYNTHETIC_COUNTEREXAMPLE["research_frozen"] is False
    assert prep.SYNTHETIC_COUNTEREXAMPLE["fee_schedule"] == "NOT_A_FEE_SCHEDULE"
    assert prep.SYNTHETIC_COUNTEREXAMPLE["risk_cap"] == "NOT_A_RISK_CAP"
    missing = {gap["missing_capability"] for gap in prep.QPK_INTERFACE_GAPS}
    assert "security_level_netting_of_inner_weights_and_outer_fraction" in missing
    assert "inner_versus_outer_cash_role" in missing
    assert all(gap["invent_qpk_api"] is False for gap in prep.QPK_INTERFACE_GAPS)


def test_private_runner_still_omits_close_fill_measurement():
    source = inspect.getsource(runner._evaluate_private)
    assert "_evaluate_private(" in inspect.getsource(runner.run_private)
    assert '"turnover": path.turnover' in source
    assert "strategy_turnover=list(path.turnover)" in source
    assert "close_fill_turnover" not in source
    assert "measure_close_fill_costs" not in source
    assert prep.RUNNER_DIFFERENTIAL["live_parity"] is False
    assert prep.RUNNER_DIFFERENTIAL["runner_adoption"] == "NOT_ATTESTED"
    assert "close_fill_turnover" in prep.RUNNER_DIFFERENTIAL["private_runner_omits"]


def test_inner_residual_cash_earns_zero_and_fee_roles_are_not_summed():
    days = _days(4)
    returns = (0.0, 0.0, 0.2, 0.0)

    def decide(i, _weights):
        return {"A": 0.5}

    path = gen._replay("X", days, {"A": returns}, ("A",), decide, None)
    assert path.gross_returns[2] == pytest.approx(0.10)
    report = prep.fee_role_report(path.close_fill_turnover, path.turnover, (0.0, 0.5, 0.0, 0.0))
    assert report["sum_permitted"] is False
    assert report["security_level_netting"] == "QPK_INTERFACE_ABSENT"
    assert report["live_parity"] is False
    with pytest.raises(prep.PreparationStop, match="FEE_ROLES_MUST_NOT_BE_SUMMED"):
        prep.fee_role_report(path.close_fill_turnover, path.turnover, path.turnover, sum_roles=True)


def test_future_return_and_late_action_do_not_change_the_prefix():
    days = _days(5)
    base = (0.0, 0.01, -0.02, 0.03, 0.04)
    perturbed = base[:4] + (0.5,)

    def decide(i, _weights):
        return None if i < 1 else {"A": 1.0}

    first = gen._replay("X", days, {"A": base}, ("A",), decide, None)
    second = gen._replay("X", days, {"A": perturbed}, ("A",), decide, None)
    assert first.gross_returns[:4] == second.gross_returns[:4]
    assert first.weights[:4] == second.weights[:4]
    assert first.close_fill_turnover[:4] == second.close_fill_turnover[:4]
    assert first.gross_returns[4] != second.gross_returns[4]

    bars = [gen.RawBar(day, 100.0, 100.0) for day in days]
    early = gen.total_return_series(bars[:4], gen.CorporateActions(), days[:4])
    late_bars = bars[:4] + [gen.RawBar(days[4], 50.0, 50.0)]
    late = gen.total_return_series(late_bars, gen.CorporateActions(splits=((days[4], 2.0),)), days)
    assert early == late[:4]
    assert late[4] == pytest.approx(0.0)

    rng = random.Random(prep.SYNTHETIC_COUNTEREXAMPLE["seed"])
    observed = [rng.gauss(0.0, 0.01) for _ in range(6)]
    before = prep.advance_vol_forecast(observed[:4], predictor="EWMA", decay=0.94,
                                       initial_variance=0.0004, warmup_sessions=2)
    after = prep.advance_vol_forecast(observed[:4] + [9.0], predictor="EWMA", decay=0.94,
                                      initial_variance=0.0004, warmup_sessions=2)
    assert before["variance_path"] == after["variance_path"][:4]
    assert after["variance_path"][4] != before["variance_path"][-1]
    clocks = prep.session_clocks(signal_index=1, timing="next_close")
    assert clocks["observation_index"] == clocks["available_index"] == clocks["signal_index"] == 1
    assert clocks["fill_index"] == 2
    assert {clocks["observation_phase"], clocks["available_phase"], clocks["fill_phase"]} == {
        "close_print", "after_completed_close", "next_close"}
    assert clocks["same_close_is_hypothetical"] is False
    assert clocks["live_parity"] is False


def test_ewma_hand_recursion_missing_and_har_do_not_switch_or_explode():
    tol = prep.SYNTHETIC_COUNTEREXAMPLE["absolute_tolerance"]
    assert prep.ewma_variance_update(0.04, 0.1, 0.94) == pytest.approx(0.0382, abs=tol)
    stepped = prep.advance_vol_forecast((0.1,), predictor="EWMA", decay=0.94,
                                        initial_variance=0.04, warmup_sessions=1)
    assert stepped["variance"] == pytest.approx(0.0382, abs=tol)
    assert stepped["unit"] == "simple_return_squared_per_observation"
    assert stepped["annualized"] is False
    warmup = prep.advance_vol_forecast((0.1, -0.2), predictor="EWMA", decay=0.94,
                                       initial_variance=0.04, warmup_sessions=3)
    assert warmup["status"] == "PARKED"
    assert warmup["reason_code"] == "WARMUP_INCOMPLETE"
    assert warmup["forecast_emitted"] is False
    assert warmup["exposure_scale"] == 0.0
    for bad in (None, math.nan, math.inf, True):
        parked = prep.advance_vol_forecast((0.1, bad), predictor="EWMA", decay=0.94,
                                           initial_variance=0.04, warmup_sessions=1)
        assert parked["status"] == "PARKED"
        assert parked["exposure_scale"] == 0.0
        assert parked["predictor_used"] == "EWMA"
        assert parked["predictor_switched"] is False
        assert parked["infinite_exposure"] is False
        assert math.isfinite(parked["exposure_scale"])
    zero = prep.advance_vol_forecast((0.1,), predictor="EWMA", decay=0.94,
                                     initial_variance=0.0, warmup_sessions=1)
    assert zero["reason_code"] == "INITIAL_VARIANCE_NOT_POSITIVE"
    assert zero["exposure_scale"] == 0.0
    har = prep.advance_vol_forecast((0.1, -0.2), predictor="HAR", decay=0.94,
                                    initial_variance=0.04, warmup_sessions=1)
    assert har["status"] == "PARKED"
    assert har["reason_code"] == "HAR_PARKED_DAILY_SQUARE_IS_NOT_RV"
    assert har["predictor_used"] == "HAR"
    assert har["predictor_switched"] is False
    assert har["exposure_scale"] == 0.0


def test_drawdown_boundaries_cash_flow_and_checkpoint_peak():
    tol = prep.SYNTHETIC_COUNTEREXAMPLE["absolute_tolerance"]
    d_max = 0.25
    assert prep.continuous_drawdown_factor(1.0, 1.0, d_max) == pytest.approx(1.0, abs=tol)
    assert prep.continuous_drawdown_factor(0.75, 1.0, d_max) == pytest.approx(0.0, abs=tol)
    assert prep.normalized_cushion_factor(1.0, 1.0, d_max) == pytest.approx(1.0, abs=tol)
    assert prep.normalized_cushion_factor(0.75, 1.0, d_max) == pytest.approx(0.0, abs=tol)
    previous = (1.0, 1.0)
    for step in range(1, 11):
        wealth = 1.0 - d_max * step / 10.0
        current = (prep.continuous_drawdown_factor(wealth, 1.0, d_max),
                   prep.normalized_cushion_factor(wealth, 1.0, d_max))
        assert current[0] <= previous[0] + tol
        assert current[1] <= previous[1] + tol
        previous = current
    breached = prep.continuous_drawdown_factor(0.5, 1.0, d_max, report_floor_breach=True)
    assert breached["factor"] == 0.0
    assert breached["hard_drawdown_guarantee"] is False
    assert breached["breached_floor"] is True
    full_until, half_until = 0.10, 0.20
    assert prep.original_ladder_factor(full_until, full_until=full_until, half_until=half_until) == 1.0
    assert prep.original_ladder_factor(math.nextafter(full_until, 1.0), full_until=full_until,
                                       half_until=half_until) == 0.5
    assert prep.original_ladder_factor(half_until, full_until=full_until, half_until=half_until) == 0.5
    assert prep.original_ladder_factor(math.nextafter(half_until, 1.0), full_until=full_until,
                                       half_until=half_until) == 0.0

    state = prep.apply_investment_return(prep.start_drawdown(100.0), 0.25)
    state = prep.apply_investment_return(state, -0.20)
    assert state.peak_index == pytest.approx(1.25, abs=tol)
    drawdown_before_cash = 1.0 - state.investment_index / state.peak_index
    state = prep.apply_external_cash(state, 50.0)
    assert state.dollar_wealth() > state.initial_nav
    assert 1.0 - state.investment_index / state.peak_index == pytest.approx(drawdown_before_cash, abs=tol)
    token = prep.checkpoint_drawdown(state)
    repeated = prep.apply_investment_return(prep.restore_drawdown(token), -0.10)
    assert repeated.peak_index == pytest.approx(state.peak_index, abs=tol)
    assert repeated.investment_index < state.investment_index


def test_original_cap_priority_clips_fractional_and_fixed_baseline():
    capped = prep.compose_exposure(kind="fractional_kelly", c=0.5, f_star=3.0, existing_cap=0.4)
    assert capped["exposure"] == pytest.approx(0.4)
    assert capped["fractional_applications"] == 1
    assert capped["live_ready"] is False and capped["no_order"] is True
    assert capped["budget_effect"] == "NONE"
    with pytest.raises(prep.PreparationStop, match="FRACTIONAL_ALREADY_APPLIED"):
        prep.compose_exposure(kind="fractional_kelly", c=0.5, f_star=0.2, existing_cap=0.4,
                              fractional_applications=1)
    with pytest.raises(prep.PreparationStop, match="FRACTIONAL_ABOVE_CEILING"):
        prep.compose_exposure(kind="fractional_kelly", c=1.0, f_star=0.2, existing_cap=0.4)
    baseline = prep.compose_exposure(kind="fixed_50", existing_cap=0.4)
    assert baseline["exposure"] == pytest.approx(0.4)
    assert baseline["baseline_exempt"] is False
    veto = prep.compose_exposure(kind="fixed_50", existing_cap=0.4, breaker_veto=True,
                                 cvar_constraint_available=False)
    assert veto["exposure"] == 0.0
    assert veto["reason_code"] == "BREAKER_OR_VETO_PRIORITY"
    assert veto["cvar_released_cap"] is False
    missing_cvar = prep.compose_exposure(kind="fixed_50", existing_cap=0.4,
                                         cvar_constraint_available=False)
    assert missing_cvar["exposure"] == pytest.approx(0.4)
    assert missing_cvar["cvar_released_cap"] is False
    parked = prep.compose_exposure(kind="vol_target_and_kelly", c=0.25, f_star=0.5,
                                   target_vol=0.2, forecast_vol=0.0, existing_cap=0.4)
    assert parked["status"] == "PARKED"
    assert parked["exposure"] == 0.0
    assert parked["infinite_exposure"] is False
    unknown_cap = prep.compose_exposure(kind="fixed_50", existing_cap=None)
    assert unknown_cap["status"] == "UNCOMPUTABLE"
    assert unknown_cap["exposure"] is None
    assert unknown_cap["reason_code"] == "CAP_NOT_ATTESTED"


def test_measurement_nulls_keep_legacy_annualization_separate():
    measured = prep.period_measurement(sessions=_days(3), net_returns=(0.0, 0.1, -0.1),
                                       gross_returns=None, trials_complete=False, initial_nav=1.0,
                                       legacy_annualized=9.9)
    assert measured["observations"] == 3
    assert measured["zero_observations"] == 1
    assert measured["initial_nav_is_observation"] is False
    assert measured["cost_log_drag"] is None
    assert measured["cost_log_drag_reason"] == "MISSING_GROSS"
    assert measured["trial_sharpe_variance"] is None
    assert measured["trial_reason"] == "TRIALS_INCOMPLETE"
    assert measured["raw_net_volatility_ddof"] == 1
    assert measured["legacy_annualized_field"] == 9.9
    assert "legacy_annualized_field" != "raw_net_sharpe_ddof1"
    assert measured["raw_net_sharpe_ddof1"] == pytest.approx(0.0)
    assert measured["period_outcome_is_trade_score"] is False
    assert measured["trade_score"] is None
    zero_variance = prep.period_measurement(sessions=_days(2), net_returns=(0.1, 0.1),
                                            gross_returns=(0.1, 0.1), trials_complete=True,
                                            initial_nav=1.0, legacy_annualized=None)
    assert zero_variance["raw_net_sharpe_ddof1"] is None
    assert zero_variance["sharpe_reason"] == "ZERO_VARIANCE"
    assert zero_variance["cost_log_drag"] == pytest.approx(0.0)


def test_trial_attempts_register_before_calculation_and_keep_failures():
    ledger = prep.AttemptLedger()
    with pytest.raises(prep.PreparationStop, match="PREREGISTRATION_REQUIRED"):
        ledger.register("row-1")
    ledger.preregister()
    with pytest.raises(prep.PreparationStop, match="ATTEMPT_NOT_REGISTERED"):
        ledger.finish("row-1", "COMPUTED")
    ledger.register("row-1")
    ledger.register("row-2")
    ledger.register("row-3")
    ledger.finish("row-1", "REJECTED")
    ledger.finish("row-2", "INTERRUPTED")
    ledger.finish("row-3", "PARKED")
    cumulative = ledger.cumulative()
    assert cumulative == (("row-1", "REJECTED"), ("row-2", "INTERRUPTED"), ("row-3", "PARKED"))
    with pytest.raises(prep.PreparationStop, match="SUCCESS_ONLY_SELECTION_FORBIDDEN"):
        ledger.cumulative(successes_only=True)
    with pytest.raises(prep.PreparationStop, match="FAILED_ROW_DELETE_FORBIDDEN"):
        ledger.delete("row-1")
    with pytest.raises(prep.PreparationStop, match="FAILED_ROW_OVERWRITE_FORBIDDEN"):
        ledger.overwrite("row-2", "COMPUTED")
    assert ledger.cumulative()[0] == ("row-1", "REJECTED")


def test_outer_traded_notional_stays_a_different_role_from_inner_fills():
    fractions = (0.5, 0.5)
    strategy = (0.0, 0.10)
    cash = (0.0, 0.01)
    inner_legacy = (0.0, 0.4)
    _gross, outer = _scaled_path(fractions, strategy, cash, inner_legacy)
    assert outer[0] == pytest.approx(0.5)
    assert outer[1] == pytest.approx(0.5 * 0.4)
    report = prep.fee_role_report((0.0, 0.4), inner_legacy, outer)
    assert report["roles_equal"] is False
    assert report["sum_permitted"] is False


@requires_execution_models
def test_three_timings_terminal_fill_and_no_weight_feedback():
    from quant_platform_kit.research_stats.execution_models import CostModel

    from us_equity_strategies.research.execution_model_adapter import (
        compare_execution_timings,
        replay_targets,
    )

    days = tuple(_days(3))
    opens = (100.0, 100.0, 110.0)
    closes = (100.0, 100.0, 121.0)
    targets = (1.0, 0.0, 0.0)
    paths = compare_execution_timings(days, opens, closes, targets, commission_bps=100.0,
                                      price_basis="synthetic")
    assert tuple(path.timing for path in paths) == ("same_close", "next_close", "next_open")
    assert len({path.net_returns for path in paths}) == 3
    assert all(path.scope == "DEVELOPMENT_ONLY" for path in paths)
    assert all(path.cost_accounting == "at_fill_pre_fill_nav_no_weight_feedback" for path in paths)
    next_close = paths[1]
    assert next_close.fill_turnover == (0.0, 1.0, 1.0)
    assert next_close.legacy_turnover == (0.0, 0.0, 1.0)
    assert next_close.unfilled_signal_indices == (2,)
    assert next_close.net_returns[1] == pytest.approx(-0.01)
    assert next_close.net_returns[2] == pytest.approx(0.1979)
    asset_return = closes[2] / closes[1] - 1.0
    core = gen._replay("X", days, {"A": (0.0, 0.0, asset_return)}, ("A",),
                       lambda i, _weights: {"A": targets[i]}, None)
    before = core.gross_returns
    net = core.measure_close_fill_costs(CostModel(commission_bps=100.0))
    assert core.gross_returns == before
    matched = replay_targets(days, closes, closes, targets, timing="next_close", commission_bps=100.0)
    assert net == pytest.approx(matched.net_returns)
    assert prep.fee_role_report(core.close_fill_turnover, core.turnover, next_close.legacy_turnover)[
        "sum_permitted"] is False


@requires_unified_report
def test_qpk_measurement_nulls_match_the_local_stop_rule():
    from quant_platform_kit.research_stats.unified_report import build_unified_net_report

    report = build_unified_net_report((0.0, 0.1, -0.1), gross_returns=None, trials=None,
                                      include_outcome_diagnostics=True)
    assert report.volatility_ddof == 1
    assert report.observations == 3
    assert report.live_ready is False and report.no_order is True
    drag = report.metric("cost_log_drag_per_period")
    dsr = report.metric("deflated_sharpe_ratio")
    assert drag.status == "UNCOMPUTABLE" and drag.value is None
    assert drag.reason_code == "missing_gross_returns"
    assert dsr.status == "UNCOMPUTABLE" and dsr.value is None
    assert dsr.reason_code == "missing_trial_evidence"
    outcome = report.metric("net_win_rate_ex_zero_per_period")
    assert outcome.details["aggregation_level"] == "return_observations_not_trades"
    assert "cagr" not in report.to_dict()
    local = prep.period_measurement(sessions=_days(3), net_returns=(0.0, 0.1, -0.1),
                                    gross_returns=None, trials_complete=False, initial_nav=1.0,
                                    legacy_annualized=None)
    assert local["cost_log_drag"] is None and local["trial_sharpe_variance"] is None
