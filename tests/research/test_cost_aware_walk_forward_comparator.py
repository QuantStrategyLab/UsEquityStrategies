"""R5 comparator: fixed vs cost-aware fractional Kelly. Synthetic inputs only."""
import importlib
import json
import math
import os
import random
import socket
from datetime import date, timedelta

import pytest

from us_equity_strategies.research import cost_aware_walk_forward_comparator as cmp
from us_equity_strategies.research import fractional_kelly_walk_forward as rs04
from us_equity_strategies.research.fractional_kelly_walk_forward_plan import WalkForwardPlan


def _qpk_stats():
    try:
        module = importlib.import_module("quant_platform_kit.research_stats")
    except ImportError:
        return None
    return module if hasattr(module, "cost_stress_recompute") else None


REQUIRE_QPK_STATS = os.environ.get("UES_REQUIRE_QPK_RESEARCH_STATS") == "1"
requires_qpk_stats = pytest.mark.skipif(
    _qpk_stats() is None and not REQUIRE_QPK_STATS,
    reason="installed QPK lacks research_stats (runs in research-stats-integration CI job)")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("SYNTHETIC_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


def _dates(n):
    d, out = date(2001, 1, 1), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _synthetic(n=400, seed=7, drift=0.001, vol=0.05):
    rng = random.Random(seed)
    days = _dates(n)
    values = tuple(rng.gauss(drift, vol) for _ in days)
    return days, values, tuple((d, r) for d, r in zip(days, values))


PLAN = WalkForwardPlan(train_sessions=120, test_sessions=40, purge_sessions=2)


def _build(*, cost_bps=5.0, holding_sessions=1, min_trade_weight=0.0,
           position_cap=1.0, fractions=(0.25, 0.5), internal=None):
    days, values, _ = _synthetic()
    return cmp._build_paths(
        dates=days, strategy=values, cash=(0.0,) * len(values), internal=internal,
        folds=PLAN.folds(len(values)), fractions=fractions, position_cap=position_cap,
        cost_bps_per_side=cost_bps, holding_sessions=holding_sessions,
        min_trade_weight=min_trade_weight)


def _run(**overrides):
    _, _, rows = _synthetic()
    kwargs = {"strategy_returns": rows, "plan": PLAN, "cost_bps_per_side": 5.0,
              "assume_zero_cash": True}
    kwargs.update(overrides)
    return cmp.evaluate_cost_aware_walk_forward(**kwargs)


def test_fixed_paths_reproduce_the_rs04_scaled_path():
    days, values, _ = _synthetic()
    built = _build()
    folds = PLAN.folds(len(values))
    window = slice(folds[0][2], folds[-1][3])
    for c in (0.25, 0.5):
        schedule = []
        for a, b, f_start, f_end in folds:
            fit = rs04.fit_fractional_kelly(values[a:b], (0.0,) * (b - a), c)
            schedule.extend([fit["exposure"]] * (f_end - f_start))
        expected = rs04._scaled_path(schedule, values[window], (0.0,) * len(schedule), None)
        assert built["paths"][f"kelly_c{format(c, 'g')}"] == expected


def test_fold_fits_are_training_only_and_shared_by_both_variants():
    days, values, _ = _synthetic()
    built = _build()
    folds = PLAN.folds(len(values))
    assert len(built["fold_records"]) == len(folds)
    for record, (a, b, f_start, f_end) in zip(built["fold_records"], folds):
        assert record["train_start"] == days[a].isoformat()
        assert record["train_end"] == days[b - 1].isoformat()
        assert record["test_start"] == days[f_start].isoformat()
        assert record["test_end"] == days[f_end - 1].isoformat()
        for c in (0.25, 0.5):
            fit = rs04.fit_fractional_kelly(values[a:b], (0.0,) * (b - a), c)
            assert record["fits"][format(c, "g")] == fit
    assert all(left[3] == right[2] for left, right in zip(folds, folds[1:]))
    assert built["test_end"] - built["test_start"] == PLAN.test_sessions * len(folds)


def test_zero_cost_cost_aware_equals_fixed_fractional():
    built = _build(cost_bps=0.0)
    for c in (0.25, 0.5):
        fixed = built["paths"][f"kelly_c{format(c, 'g')}"]
        aware = built["paths"][f"kelly_cost_aware_c{format(c, 'g')}"]
        assert aware[0] == pytest.approx(fixed[0])
        assert aware[1] == pytest.approx(fixed[1])
        assert built["summaries"][f"kelly_cost_aware_c{format(c, 'g')}"][
            "decision_status_counts"].get("PARKED", 0) == 0


def test_cost_aware_band_reduces_rebalance_turnover():
    built = _build(cost_bps=5.0)
    for c in (0.25, 0.5):
        fixed = built["summaries"][f"kelly_c{format(c, 'g')}"]
        aware = built["summaries"][f"kelly_cost_aware_c{format(c, 'g')}"]
        assert aware["rebalance_turnover_total"] < fixed["rebalance_turnover_total"]
        assert aware["rebalance_sessions"] < fixed["rebalance_sessions"]
        assert aware["turnover_total"] < fixed["turnover_total"]


def test_targets_respect_zero_and_position_cap():
    built = _build(position_cap=0.2, cost_bps=5.0)
    for name, summary in built["summaries"].items():
        assert summary["min_target_weight"] >= 0.0
        assert summary["max_target_weight"] <= 0.2
        assert summary["max_target_weight"] > 0.0


def test_parked_fold_is_cash_in_both_variants():
    days = _dates(400)
    values = tuple(0.001 for _ in days)  # one-sided training returns
    built = cmp._build_paths(
        dates=days, strategy=values, cash=(0.0,) * len(values), internal=None,
        folds=PLAN.folds(len(values)), fractions=(0.5,), position_cap=1.0,
        cost_bps_per_side=5.0, holding_sessions=1, min_trade_weight=0.0)
    assert all(record["fits"]["0.5"]["status"] == "PARKED" for record in built["fold_records"])
    for name in built["summaries"]:
        assert built["summaries"][name]["max_target_weight"] == 0.0
        assert built["summaries"][name]["rebalance_turnover_total"] == 0.0


def test_strategy_internal_turnover_is_accounted_but_does_not_move_the_band():
    internal = (0.02,) * 400
    without = _build(internal=None)
    with_internal = _build(internal=internal)
    for c in (0.25, 0.5):
        name = f"kelly_cost_aware_c{format(c, 'g')}"
        assert (without["summaries"][name]["rebalance_turnover_total"]
                == pytest.approx(with_internal["summaries"][name]["rebalance_turnover_total"]))
        assert (with_internal["summaries"][name]["turnover_total"]
                > without["summaries"][name]["turnover_total"])
        assert without["paths"][name][0] == pytest.approx(with_internal["paths"][name][0])


def test_holding_sessions_changes_band_width():
    short = _build(cost_bps=5.0, holding_sessions=1)
    long = _build(cost_bps=5.0, holding_sessions=5)
    name = "kelly_cost_aware_c0.5"
    assert (long["summaries"][name]["rebalance_turnover_total"]
            > short["summaries"][name]["rebalance_turnover_total"])


def test_min_trade_weight_suppresses_small_rebalances():
    none = _build(cost_bps=5.0, min_trade_weight=0.0)
    floored = _build(cost_bps=5.0, min_trade_weight=0.1)
    name = "kelly_cost_aware_c0.5"
    assert (floored["summaries"][name]["rebalance_turnover_total"]
            < none["summaries"][name]["rebalance_turnover_total"])
    assert (floored["summaries"][name]["rebalance_sessions"]
            < none["summaries"][name]["rebalance_sessions"])


def test_comparator_refuses_real_data_and_reports():
    with pytest.raises(ValueError, match="REAL_DATA_REQUIRES_PREREGISTRATION_EXTENSION"):
        _run(synthetic=False)
    with pytest.raises(ValueError, match="REPORT_CONTEXT_NOT_SUPPORTED_IN_COMPARATOR"):
        _run(report_context={"report_id": "x"})
    with pytest.raises(ValueError, match="CASH_RETURNS_REQUIRED_OR_DECLARE_ZERO"):
        _run(assume_zero_cash=False)


def test_invalid_inputs_fail_closed():
    for bad in ((1.0,), (0.25, 0.25), (0.0,), (-0.1,)):
        with pytest.raises(ValueError):
            _run(fractions=bad)
    for bad in ((0.0,), (1.0, 1.0), (-1.0,)):
        with pytest.raises(ValueError):
            _run(multipliers=bad)
    for bad in (-0.1, 1.1, math.nan):
        with pytest.raises(ValueError):
            _run(position_cap=bad)
    for bad in (-0.1, 1.1, math.nan):
        with pytest.raises(ValueError):
            _run(min_trade_weight=bad)
    for bad in (0, -1, 1.5, True):
        with pytest.raises(ValueError):
            _run(holding_sessions=bad)
    with pytest.raises(ValueError):
        _run(bootstrap_seed=True)
    with pytest.raises(ValueError):
        _run(strategy_turnover=(0.1,))


def test_missing_qpk_stats_is_uncomputable_without_numbers(monkeypatch):
    monkeypatch.setattr(cmp, "_stats_module", lambda: None)
    result = _run()
    assert result["status"] == "UNCOMPUTABLE"
    assert result["reason_code"] == "QPK_RESEARCH_STATS_UNAVAILABLE"
    for key in ("comparators", "paired_deltas_net1x", "folds", "test_window"):
        assert key not in result
    assert result["live_ready"] is False
    assert result["no_order"] is True
    assert result["budget_effect"] == "NONE"


def test_envelope_is_json_safe_and_has_no_authority(monkeypatch):
    monkeypatch.setattr(cmp, "_stats_module", lambda: None)
    payload = _run()
    encoded = json.dumps(payload, allow_nan=False, sort_keys=True)
    assert '"no_order": true' in encoded
    assert '"budget_effect": "NONE"' in encoded
    assert '"live_ready": false' in encoded
    assert payload["kelly_ready_role"] == "COMPARATOR_ONLY_NEVER_RAISES_BUDGET"
    assert payload["comparator_version"] == cmp.COMPARATOR_VERSION
    assert payload["rs04_evaluator_version"] == rs04.EVALUATOR_VERSION


@requires_qpk_stats
def test_public_output_compares_same_window_with_paired_deltas():
    result = _run(cost_bps_per_side=5.0, bootstrap_seed=7)
    assert result["status"] == "COMPUTED"
    assert result["test_window"]["sessions"] == 240
    assert set(result["comparators"]) == {
        "kelly_c0.25", "kelly_c0.5", "kelly_cost_aware_c0.25", "kelly_cost_aware_c0.5"}
    for record in result["comparators"].values():
        assert set(record["tiers"]) == {"1", "2", "3"}
        assert record["path_summary"]["sessions"] == 240
        assert record["tiers"]["1"]["status"] == "COMPUTED"
    for c in ("0.25", "0.5"):
        delta = result["paired_deltas_net1x"][f"kelly_cost_aware_c{c}__vs__kelly_c{c}"]
        assert delta["status"] == "COMPUTED"
        assert delta["same_sessions"] is True
        assert delta["log_growth_per_session"] > 0.0
        assert delta["max_drawdown"] > 0.0
        assert delta["turnover_total"] < 0.0
        assert delta["total_cost_log_drag"] < 0.0
        assert delta["paired_stationary_bootstrap"]["status"] == "COMPUTED"
        assert delta["paired_stationary_bootstrap"]["observations"] == 240
    json.dumps(result, allow_nan=False, sort_keys=True)
    again = _run(cost_bps_per_side=5.0, bootstrap_seed=7)
    assert (again["paired_deltas_net1x"]
            == result["paired_deltas_net1x"])


@requires_qpk_stats
def test_fixed_variants_match_rs04_public_output():
    _, _, rows = _synthetic()
    _, _, bench = _synthetic(seed=8)
    rs04_result = rs04.evaluate_fractional_kelly_walk_forward(
        strategy_returns=rows, benchmark_returns=bench, plan=PLAN,
        cost_bps_per_side=5.0, synthetic=True, assume_zero_cash=True)
    result = _run(cost_bps_per_side=5.0)
    assert rs04_result["test_window"] == result["test_window"]
    assert rs04_result["folds"] == result["folds"]
    for c in ("0.25", "0.5"):
        fixed = result["comparators"][f"kelly_c{c}"]
        reference = rs04_result["comparators"][f"kelly_c{c}"]
        for tier in ("1", "2", "3"):
            for key in ("log_growth_per_session", "cagr", "max_drawdown",
                        "cvar_95_daily", "turnover_total"):
                assert fixed["tiers"][tier][key] == reference["tiers"][tier][key]


@requires_qpk_stats
def test_zero_cost_public_metrics_are_identical():
    result = _run(cost_bps_per_side=0.0)
    for c in ("0.25", "0.5"):
        fixed = result["comparators"][f"kelly_c{c}"]["tiers"]["1"]
        aware = result["comparators"][f"kelly_cost_aware_c{c}"]["tiers"]["1"]
        for key in ("log_growth_per_session", "cagr", "max_drawdown", "cvar_95_daily"):
            assert aware[key] == pytest.approx(fixed[key])


@requires_qpk_stats
def test_tier_multipliers_do_not_change_decisions():
    base = _run(cost_bps_per_side=5.0, multipliers=(1.0, 2.0, 3.0))
    steeper = _run(cost_bps_per_side=5.0, multipliers=(1.0, 5.0))
    for name in base["comparators"]:
        assert (steeper["comparators"][name]["path_summary"]
                == base["comparators"][name]["path_summary"])
    for c in ("0.25", "0.5"):
        aware = f"kelly_cost_aware_c{c}"
        one_x = base["comparators"][aware]["tiers"]["1"]
        five_x = steeper["comparators"][aware]["tiers"]["5"]
        assert five_x["total_cost_log_drag"] >= one_x["total_cost_log_drag"]
        assert five_x["log_growth_per_session"] <= one_x["log_growth_per_session"]
