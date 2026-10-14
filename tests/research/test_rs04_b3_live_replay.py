"""Invented inputs only; strategy parity is distinct from recorded-live parity."""
from dataclasses import replace
from datetime import date

import pytest

from us_equity_strategies.research.rs04_b3 import State, advance
from us_equity_strategies.research.rs04_b3_live import (
    LiveRuleAdapter, binding_report, compare_recorded_decisions, default_config,
)
from us_equity_strategies.research.rs04_b3_evaluation import (
    historical_plan, evaluate_paths,
)
from us_equity_strategies.research.rs04_b3_runner import replay, synthetic_sessions


def test_defaults_keep_live_layers_and_binding_does_not_claim_effective_config():
    for profile in ("SOXL", "TQQQ"):
        config = default_config(profile)
        assert config["income_layer_enabled"] and config["market_regime_control_enabled"]
        assert binding_report(profile)["effective_config_verified"] is False
    assert default_config("SOXL")["trend_ma_window"] == 140
    assert default_config("SOXL")["blend_gate_soxl_weight"] == .70
    assert default_config("TQQQ")["dual_drive_crisis_defense_enabled"]


@pytest.mark.parametrize("signal_price", [120., 107., 97.])
def test_soxl_adapter_calls_original_rule_with_own_equity_and_income_layer(signal_price):
    from us_equity_strategies.strategies.soxl_soxx_trend_income import build_rebalance_plan
    from us_equity_strategies.research.kelly_core_return_generator import _filtered_kwargs
    rows = synthetic_sessions(2)
    indicators = {s.lower(): {"price": 120., "ma_trend": 100., "ma20": 110.,
                             "ma20_slope": 1., "rsi14": 50., "bollinger_upper": 150.,
                             "realized_volatility": .1} for s in ("SOXL", "SOXX")}
    indicators["soxx"]["price"] = signal_price
    row = replace(rows[0], live_indicators=indicators, context={})
    adapter = LiveRuleAdapter("SOXL", default_config("SOXL"))
    for nav in (100_000., 500_000.):
        book = {"cash": nav, "quantity": {}, "nav": nav, "receivables": 0.}
        account = {"available_cash": nav, "market_values": {}, "quantities": {},
                   "sellable_quantities": {}, "total_strategy_equity": nav,
                   "cash_sweep_symbol": "BOXX", "metadata": {}}
        kwargs = _filtered_kwargs(build_rebalance_plan, adapter.config,
                                  ("indicators", "account_state", "translator"))
        expected = build_rebalance_plan(indicators, account, translator=lambda k, **kw: k, **kwargs)
        actual = adapter((row,), book)
        assert actual == pytest.approx({s: v/nav for s, v in expected["targets"].items()})
        assert bool(sum(actual.get(s, 0.) for s in ("SCHD", "DGRO", "SGOV", "SPYI", "QQQI"))) == (nav > 150_000)


def test_tqqq_adapter_ma200_and_recorded_decision_comparison():
    rows = [replace(r, signal_closes={"QQQ": 100+i*.1}, context={})
            for i, r in enumerate(synthetic_sessions(300))]
    adapter = LiveRuleAdapter("TQQQ", default_config("TQQQ"))
    book = {"cash": 100_000., "quantity": {}, "nav": 100_000., "receivables": 0.}
    targets = adapter(rows, book)
    assert targets["TQQQ"] > 0
    record = {"session": rows[-1].session, "history": rows, "book": book, "targets": targets}
    assert compare_recorded_decisions(adapter, [record])["status"] == "MATCH"
    bad = dict(record, targets={"TQQQ": .123})
    assert compare_recorded_decisions(adapter, [bad])["status"] == "MISMATCH"
    assert compare_recorded_decisions(adapter, [])["status"] == "UNAVAILABLE"


def test_adapter_never_fills_missing_history_with_zero_or_silently_clips_cap():
    adapter = LiveRuleAdapter("TQQQ", default_config("TQQQ"))
    with pytest.raises(ValueError, match="LIVE_SIGNAL_HISTORY_MISSING"):
        adapter([replace(r, context={}) for r in synthetic_sessions(300)],
                {"cash": 100000., "quantity": {}, "nav": 100000.})
    with pytest.raises(ValueError, match="BASELINE_CAP_BREACH"):
        replay(synthetic_sessions(3), lambda h, b: {"SOXL": .7},
               profile="SOXL", mode="baseline", kelly_full=0.)


def test_de_risk_only_blocks_mr_without_changing_default_b3():
    from us_equity_strategies.research.rs04_b3 import Features
    f = Features(date(2020, 1, 1), date(2020, 1, 1), 100., 100., 0., 15., .3, 5., 2., 100.)
    state = State(regime="RANGE")
    kw = dict(decision=date(2020, 1, 2), index=1, profile="SOXL", baseline={"SOXL": .6}, held={})
    assert advance(state, f, **kw).targets["SOXL"] > 0
    out = advance(state, f, allow_mr=False, **kw)
    assert out.targets["SOXL"] == 0 and out.sleeve != "MR"
    result = replay(synthetic_sessions(400), lambda h, b: {"SOXL": .6},
                    profile="SOXL", mode="de_risk_only", kelly_full=4.)
    assert result.metrics["closed_mr_episodes"] == 0


def test_pseudo_holdout_is_archive_trailing_24_months_and_excluded_from_wfa():
    rows = synthetic_sessions(1800)
    plan = historical_plan([r.session for r in rows], feature_warmup=280)
    assert plan["pseudo_holdout"]["weaker_evidence"]
    assert plan["pseudo_holdout"]["previously_seen_development"]
    assert plan["pseudo_holdout"]["end"] == rows[-1].session.isoformat()
    boundary = plan["pseudo_holdout"]["start_index"]
    assert all(f["test_end"] <= boundary for f in plan["walk_forward"]["folds"])
    assert plan["forward_holdout"]["start"] == "2026-11-01"


def test_evaluation_keeps_continuous_ledger_and_never_promotes_weak_evidence():
    rows = synthetic_sessions(500)
    def rule(h, b):
        return {"TQQQ": .45, "QQQM": .45, "BOXX": .08}
    paths = {m: replay(rows, rule, profile="TQQQ", mode=m, kelly_full=4.)
             for m in ("baseline", "sizing_control", "b3", "de_risk_only")}
    report = evaluate_paths(paths, start=100, end=400)
    assert set(report["paths"]) == set(paths)
    assert report["paths"]["b3"]["observations"] == 300
    assert "giveback" in report["paths"]["b3"]
    assert report["paths"]["b3"]["deflated_sharpe"] is None
    assert report["paths"]["b3"]["regime_stats"]
    assert report["recommend_shadow"] is False


def test_windows_and_stress_use_same_continuous_book_and_fixed_dates():
    from us_equity_strategies.research.rs04_b3_evaluation import evaluate_windows
    rows = synthetic_sessions(1000)
    def rule(h, b):
        return {"TQQQ": .45}
    paths = {m: replay(rows, rule, profile="TQQQ", mode=m, kelly_full=4., cost_multiplier=2.)
             for m in ("baseline", "b3", "de_risk_only")}
    windows = evaluate_windows(paths, feature_warmup=280, cost_multiplier=2.)
    ph = windows["pseudo_holdout"]
    assert ph["status"] == "COVERED" and ph["results"]["continuous_ledger"]
    assert ph["results"]["paths"]["baseline"]["observations"] == ph["end_index"]-ph["start_index"]
    assert ph["results"]["numeric_acceptance"]["b3"]["giveback_required_reduction"] == .1
    assert windows["walk_forward"]["status"] == "INSUFFICIENT_COVERAGE"
    assert windows["forward_holdout"]["opened"] is False
