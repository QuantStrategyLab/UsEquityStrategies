"""Invented inputs only. No test establishes live configuration or market P&L."""
from copy import deepcopy
from dataclasses import replace
import random

import pytest

from us_equity_strategies.research.rs04_b3_live import LiveRuleAdapter, default_config
from us_equity_strategies.research.rs04_b3_runner import Ledger, synthetic_sessions
from us_equity_strategies.research.soxl_v7_derisk import (
    assert_exposure, clamp_targets, variant_config, VariantAdapter, strict_rebalance,
    paired_synthetic_replay,
)
from us_equity_strategies.v7_soxl_profile import copy_v7_runtime_config


def fixture(route="no_action", **changes):
    row = synthetic_sessions(2)[0]
    indicators = {s: {"price": 120., "ma_trend": 100., "ma20": 110.,
        "ma20_slope": 1., "rsi14": 50., "bollinger_upper": 150.,
        "realized_volatility_10": .1} for s in ("soxl", "soxx")}
    indicators["soxx"].update(changes)
    off = route == "risk_off"
    context = {"market_regime_control": {
        "plugin": "market_regime_control", "schema_version": "market_regime_control.v1",
        "as_of": row.session.isoformat(), "candidate_id": "synthetic-derisk-counterexample",
        "execution_controls": {"position_control_allowed": True,
            "consumption_evidence_status": "automation_approved"},
        "position_control": {"final_route": route, "route_source": "synthetic_fixture",
            "suggested_action": "defend" if off else "watch_only",
            "risk_budget_scalar": 0. if off else 1., "leverage_scalar": 0. if off else 1.,
            "risk_asset_scalar": 0. if off else 1., "crisis_defense_required": off,
            "reason_codes": ["synthetic_fixture"]}}}
    return replace(row, live_indicators=indicators, context=context)


def account(nav=100000.):
    return {"cash": nav, "nav": nav, "quantity": {}, "receivables": 0.}


def test_only_approved_changes_preserve_soxx_income_regime_and_options():
    baseline = default_config("SOXL")
    original = deepcopy(baseline)
    variant = variant_config(baseline)
    assert baseline == original
    assert variant["blend_gate_active_soxx_weight"] == baseline["blend_gate_active_soxx_weight"] == .20
    for key in baseline:
        if key.startswith(("income_", "market_regime_", "option_")):
            assert variant[key] == baseline[key]
    assert variant["blend_gate_soxl_weight"] == .35
    assert variant["blend_gate_mid_soxl_weight"] == .25
    assert variant["blend_gate_volatility_delever_retention_mode"] == "none"
    assert variant["blend_gate_volatility_delever_redirect_symbol"] == "BOXX"
    with pytest.raises(ValueError, match="BASELINE_PROTECTION_REQUIRED"):
        variant_config(dict(baseline, income_layer_enabled=False))


def test_original_code_counterexample_reproduced_and_variant_eliminates_it():
    row, book = fixture("risk_off"), account()
    baseline = LiveRuleAdapter("SOXL", default_config("SOXL"))((row,), book)
    old_config = dict(default_config("SOXL"), **copy_v7_runtime_config())
    old_v7 = LiveRuleAdapter("SOXL", old_config)((row,), book)
    new_v7 = VariantAdapter(default_config("SOXL"))((row,), book, baseline)
    assert baseline["SOXL"] == baseline["SOXX"] == 0.
    assert 3*old_v7["SOXL"]+old_v7["SOXX"] == pytest.approx(1.261)
    assert new_v7["SOXL"] == new_v7["SOXX"] == 0.
    assert_exposure(new_v7, baseline)


@pytest.mark.parametrize("changes", [{}, {"rsi14": 90.}, {"bollinger_upper": 110.},
    {"rsi14": 90., "bollinger_upper": 110.}, {"realized_volatility_10": .9}])
@pytest.mark.parametrize("nav", [100000., 500000.])
def test_all_triggers_and_income_threshold_remain_bounded(changes, nav):
    row, book = fixture(**changes), account(nav)
    baseline = LiveRuleAdapter("SOXL", default_config("SOXL"))((row,), book)
    actual = VariantAdapter(default_config("SOXL"))((row,), book, baseline)
    assert_exposure(actual, baseline)
    assert sum(actual.values()) <= .97+1e-12
    if changes.get("realized_volatility_10", 0) > .55:
        assert actual["SOXL"] == 0.
    if nav == 500000.:
        assert sum(actual.get(s, 0) for s in ("SCHD", "DGRO", "SPYI", "QQQI")) > 0


def test_random_targets_enforce_component_gross_effective_and_dollar_bounds():
    rng = random.Random(20261015)
    for _ in range(1000):
        b = {s: rng.random()*.12 for s in ("SOXL", "SOXX", "SCHD", "DGRO", "SPYI", "QQQI")}
        v = {s: rng.random()*.12 for s in b}
        v["BOXX"] = .1
        bn, vn = rng.uniform(1000, 1e6), rng.uniform(1000, 1e6)
        out = clamp_targets(v, b, baseline_nav=bn, candidate_nav=vn)
        assert_exposure(out, b, baseline_nav=bn, candidate_nav=vn)
        assert sum(out.values()) <= .97+1e-12


def test_unknown_nonfinite_short_and_missing_baseline_never_pass():
    for v in ({"UNKNOWN": .1}, {"SOXL": float("nan")}, {"SOXL": -.1}):
        with pytest.raises(ValueError):
            clamp_targets(v, {"SOXL": .1})
    with pytest.raises(ValueError):
        assert_exposure({"SOXL": .1}, {"SOXL": 0})
    with pytest.raises(ValueError):
        VariantAdapter(default_config("SOXL"))((fixture(),), account(), None)
    assert sum(clamp_targets({"BOXX": .97}, {}).values()) == 0
    with pytest.raises(ValueError, match="EXPOSURE_INVARIANT_AGGREGATE"):
        assert_exposure({"BOXX": .97}, {"BOXX": .5})


@pytest.mark.parametrize("cost", [1., 2.])
def test_post_cost_whole_share_exposure_and_cash_reserve(cost):
    b, v = Ledger(100003., {}, {}), Ledger(35001., {}, {})
    prices = {"SOXL": 71.17, "SOXX": 203.13, "BOXX": 103.18}
    b.rebalance({"SOXL": .51, "SOXX": .19, "BOXX": .27}, prices,
                profile="SOXL", cost_multiplier=cost)
    for multiplier in (1., 1.3, .6, 2.):
        prices["SOXL"] *= multiplier
        strict_rebalance(v, {"SOXL": .4, "SOXX": .25, "BOXX": .32}, b, prices,
                         cost_multiplier=cost)
        bn, vn = b.nav(prices), v.nav(prices)
        bw = {s: q*prices[s]/bn for s, q in b.quantity.items()}
        vw = {s: q*prices[s]/vn for s, q in v.quantity.items()}
        assert_exposure(vw, bw, baseline_nav=bn, candidate_nav=vn)
        assert v.cash >= .03*vn-1e-8
    b.rebalance({"BOXX": .97}, prices, profile="SOXL", cost_multiplier=cost)
    strict_rebalance(v, {"SOXL": .4, "SOXX": .2, "BOXX": .37}, b, prices,
                     cost_multiplier=cost)
    assert v.quantity.get("SOXL", 0) == v.quantity.get("SOXX", 0) == 0


def test_strict_fill_rejects_hidden_partial_fill_or_unmodelled_exposure():
    class BrokenLedger(Ledger):
        def rebalance(self, *args, **kwargs):
            self.quantity = {"SOXL": 2}
            return {"commission": 0., "slippage": 0.}
    with pytest.raises(ValueError, match="EXPOSURE_INVARIANT"):
        strict_rebalance(BrokenLedger(1000., {}, {}), {"SOXL": .2}, Ledger(1000., {}, {}),
                         {"SOXL": 100., "BOXX": 100.}, cost_multiplier=1.)


@pytest.mark.parametrize("cost", [1., 2.])
def test_every_bar_on_paired_own_wealth_ledger_and_future_causality(cost):
    rows = []
    for i, row in enumerate(synthetic_sessions(20)):
        signal = fixture("risk_off" if 6 <= i < 10 else "no_action",
                         rsi14=90. if i % 4 == 0 else 50.)
        context = deepcopy(signal.context)
        context["market_regime_control"]["as_of"] = row.session.isoformat()
        prices = dict(row.prices, **{s: 100. for s in ("SCHD", "DGRO", "SGOV", "SPYI", "QQQI")})
        rows.append(replace(row, prices=prices, context=context, live_indicators=signal.live_indicators))
    points = paired_synthetic_replay(rows, default_config("SOXL"), cost_multiplier=cost)
    assert len(points) == len(rows)
    for point in points:
        c, b = point["candidate"], point["baseline"]
        assert_exposure(c["weights"], b["weights"], baseline_nav=b["nav"], candidate_nav=c["nav"])
        assert c["cash"] >= .03*c["nav"]-1e-8
    assert points[7]["candidate"]["weights"].get("SOXL", 0) == 0
    changed = rows[:12]+[replace(r, prices={s: p*2 for s, p in r.prices.items()}) for r in rows[12:]]
    assert paired_synthetic_replay(changed, default_config("SOXL"), cost_multiplier=cost)[:12] == points[:12]
    with pytest.raises(ValueError, match="SYNTHETIC_PAIRED_INPUT_INVALID"):
        paired_synthetic_replay(rows, default_config("SOXL"), synthetic=False)
