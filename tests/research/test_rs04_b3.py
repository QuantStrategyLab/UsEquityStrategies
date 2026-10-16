"""Invented prices only. No providers, archives, brokers, or real-return fixtures."""
from dataclasses import replace
from datetime import date, timedelta
import math

import pytest

from us_equity_strategies.research.rs04_b3 import (
    CONFIG, Bar, Features, State, advance, build_features, combined_targets,
    ewma_forecast, record_fill,
)


def feature(**kw):
    values = dict(session=date(2020, 1, 1), available=date(2020, 1, 1),
                  price=100., ma=99., slope=.001, adx=28., rv_percentile=.5,
                  rsi=50., atr=2., trade_price=100.)
    return Features(**(values | kw))


def step(state, f=None, i=1, targets=None, held=None, hard=False):
    return advance(state, f or feature(), decision=date(2020, 1, 2), index=i,
                   profile="SOXL", baseline=targets or {"SOXL": .679, "SOXX": .194, "BOXX": .097},
                   held=held or {}, hard_risk=hard)


def test_hysteresis_consecutive_confirmation_and_hard_exit():
    state = State()
    for i in range(2):
        out = step(state, i=i)
        state = out.state
        assert state.regime == "TRANSITION"
    state = step(state, i=2).state
    assert state.regime == "TREND"
    # Minimum hold does not postpone a hard-risk exit.
    out = step(state, i=3, hard=True)
    assert out.state.regime == "RISK_OFF"
    assert out.targets["SOXL"] == out.targets["SOXX"] == 0


def test_range_confirmation_noise_and_risk_off_priority():
    state = State()
    quiet = feature(adx=17., slope=0., rsi=5.)
    for i in range(5):
        out = step(state, quiet, i=i)
        state = out.state
    assert state.regime == "RANGE"
    assert 0 < out.targets["SOXL"] <= .10
    assert out.targets["SOXX"] == 0
    assert out.targets["SOXL"] * (2 * quiet.atr / quiet.trade_price) <= .0025
    # One noisy observation cannot change state.
    state = step(state, feature(adx=24., slope=0.), i=5).state
    assert state.regime == "RANGE"
    out = step(state, feature(price=95., slope=-.01, rsi=1.), i=6)
    assert out.state.regime == "RISK_OFF" and out.targets["SOXL"] == 0


def test_trail_monotone_partial_fill_and_cooldown():
    state = record_fill(State(regime="TREND", age=10), old_quantity=0, quantity=100,
                        price=100, fee_per_share=.1, atr=2, index=0, sleeve="TREND")
    first = step(state, feature(trade_price=110, atr=1), i=1, held={"SOXL": .5})
    assert first.state.stop >= 107
    partial = record_fill(first.state, old_quantity=100, quantity=75, price=110,
                          fee_per_share=0, atr=10, index=1, sleeve="TREND")
    assert partial.entry == state.entry and partial.initial_r == state.initial_r
    second = step(partial, feature(trade_price=109, atr=10), i=2, held={"SOXL": .5})
    assert second.state.stop >= first.state.stop
    out = step(second.state, feature(trade_price=106, atr=2), i=3, held={"SOXL": .5})
    assert out.targets["SOXL"] == 0 and "PROFIT_TRAIL" in out.reasons
    flat = record_fill(out.state, old_quantity=75, quantity=0, price=90,
                       fee_per_share=0, atr=2, index=3, sleeve="TREND")
    for i in range(4, 12):
        out = step(flat, i=i)
        flat = out.state
        assert out.targets["SOXL"] == 0  # persistent old signal cannot re-enter


@pytest.mark.parametrize("profile,cap,single", [("SOXL", .97, .679), ("TQQQ", .98, .98)])
def test_caps_once_kelly_and_combined_exposure(profile, cap, single):
    result = combined_targets({profile: 1., "BOXX": .5}, profile=profile,
                              kelly_full=8., forecast_vol=.10, drawdown=.15)
    assert sum(result.targets.values()) <= cap + 1e-12
    assert result.targets[profile] <= single
    assert result.sizing_factor == .5  # c=.25 consumed once; ladder consumed once
    assert result.cash >= 1-cap-1e-12
    # No repeated multiplication of the same B3/B1 cap.
    result = combined_targets({profile: .1}, profile=profile,
                              kelly_full=4., forecast_vol=.1, drawdown=0)
    assert result.targets[profile] == pytest.approx(.1)


@pytest.mark.parametrize("dd,expected", [(.1, 1), (.1000001, .5), (.2, .5), (.200001, 0)])
def test_dd_exact_boundaries(dd, expected):
    out = combined_targets({"TQQQ": .4}, profile="TQQQ", kelly_full=4,
                           forecast_vol=.2, drawdown=dd)
    assert out.sizing_factor == expected


def test_ewma_seed_warmup_and_missing():
    rs = [.01, -.02, .03] * 21
    assert ewma_forecast(rs[:-1]) is None
    seed = sum((r-sum(rs)/63)**2 for r in rs)/62
    assert ewma_forecast(rs) == pytest.approx(math.sqrt(seed*252))
    updated = CONFIG.ewma_lambda*seed + (1-CONFIG.ewma_lambda)*.04**2
    assert ewma_forecast(rs+[.04]) == pytest.approx(math.sqrt(updated*252))
    assert ewma_forecast(rs+[float("nan")]) is None


def test_ohlc_features_causal_and_daily_close_decision():
    bars = [Bar(date(2020, 1, 1)+timedelta(days=i),
                100+math.sin(i/4)+i/20, 98+math.sin(i/4)+i/20,
                99+math.sin(i/4)+i/20) for i in range(360)]
    original = build_features(bars, bars)
    changed = list(bars)
    changed[320:] = [replace(b, high=b.high*3, low=b.low*3, close=b.close*3) for b in changed[320:]]
    perturbed = build_features(changed, changed)
    assert original[:320] == perturbed[:320]
    for features in (original, perturbed):
        state = State()
        decisions = []
        for i in range(280, 321):
            out = advance(state, features[i-1], decision=bars[i].session, index=i,
                          profile="TQQQ", baseline={"TQQQ": .45, "QQQM": .45}, held={})
            decisions.append(out.targets)
            state = out.state
        if features is original:
            first = decisions
        else:
            assert decisions == first  # bar t cannot change decision filled at t close
    with pytest.raises(ValueError, match="FEATURE_NOT_AVAILABLE"):
        advance(State(), original[300], decision=bars[300].session, index=300,
                profile="SOXL", baseline={}, held={})


def test_missing_data_invalid_weights_and_late_features():
    assert step(State(), feature(atr=float("nan"))).targets["SOXL"] == 0
    with pytest.raises(ValueError, match="WEIGHTS_INVALID"):
        combined_targets({"SOXL": -.1}, profile="SOXL", kelly_full=4, forecast_vol=.2, drawdown=0)
    with pytest.raises(ValueError, match="FEATURE_NOT_AVAILABLE"):
        step(State(), feature(available=date(2020, 1, 2)))


def test_range_volatility_exit_is_immediate():
    out = step(State(regime="RANGE", age=0, sleeve="MR", entry=100, initial_r=4),
               feature(adx=15, slope=0, rv_percentile=.95), i=5, held={"SOXL": .05})
    assert out.targets["SOXL"] == 0
    assert out.state.regime != "RANGE"


def test_duplicate_session_rejected_and_checkpoint_replay():
    first = step(State(), i=1)
    with pytest.raises(ValueError, match="DUPLICATE"):
        step(first.state, i=1)
    assert step(first.state, i=2) == step(replace(first.state), i=2)


def test_ledger_terminal_fill_cost_and_post_cost_cap():
    from us_equity_strategies.research.rs04_b3_runner import Ledger
    ledger = Ledger(100_000., {}, {})
    cost = ledger.rebalance({"SOXL": .679, "SOXX": .194, "BOXX": .097},
                            {"SOXL": 100., "SOXX": 100., "BOXX": 100.}, profile="SOXL", cost_multiplier=1.)
    nav = ledger.nav({"SOXL": 100., "SOXX": 100., "BOXX": 100.})
    assert cost["commission"] > 0 and cost["slippage"] > 0
    assert nav == pytest.approx(100000-sum(cost.values()))
    assert ledger.cash/nav >= .03
    assert ledger.quantity["SOXL"]*100/nav <= .679
    final = ledger.rebalance({}, {"SOXL": 100., "SOXX": 100., "BOXX": 100.},
                             profile="SOXL", cost_multiplier=2.)
    assert final["commission"] > 0 and final["slippage"] > 0
    assert ledger.cash == pytest.approx(nav-sum(final.values()))
    assert all(q == 0 for q in ledger.quantity.values())


def test_dividend_receivable_not_spendable_and_split_wealth_conservation():
    from us_equity_strategies.research.rs04_b3_runner import Ledger
    ledger = Ledger(1000., {"SOXL": 10}, {})
    before = ledger.nav({"SOXL": 100.})
    ledger.split("SOXL", 2.)
    ledger.accrue("SOXL", 1., date(2020, 1, 3))
    assert ledger.cash == 1000
    assert ledger.nav({"SOXL": 49.}) == before
    ledger.pay(date(2020, 1, 3))
    assert ledger.cash == 1020 and ledger.receivables == {}


def test_runner_causal_cash_conservation_and_combined_path():
    from us_equity_strategies.research.rs04_b3_runner import synthetic_sessions, replay
    rows = synthetic_sessions(420)
    def baseline(history, book):
        return {"TQQQ": .45, "QQQM": .45, "BOXX": .08}
    a = replay(rows, baseline, profile="TQQQ", mode="b3", kelly_full=4.)
    changed = list(rows)
    changed[380:] = [replace(r, prices={k: v*3 for k, v in r.prices.items()}) for r in changed[380:]]
    b = replay(changed, baseline, profile="TQQQ", mode="b3", kelly_full=4.)
    assert a.points[:380] == b.points[:380]
    assert a.points[380]["targets"] == b.points[380]["targets"]
    for p in a.points:
        assert p["cash"] >= 0
        assert p["nominal"] <= .98+1e-10
        assert p["nominal"] == pytest.approx(sum(p["weights"].values()))
        assert p["nav"] == pytest.approx(p["cash"] + p["market_value"] + p["receivables"])
    assert a.metrics["synthetic"] is True
    assert a.metrics["max_drawdown"] >= 0
    assert a.metrics["promotion_eligible"] is False


def test_baseline_is_not_resized_by_b1_b2():
    from us_equity_strategies.research.rs04_b3_runner import synthetic_sessions, replay
    out = replay(synthetic_sessions(5), lambda history, book: {"TQQQ": .45},
                 profile="TQQQ", mode="baseline", kelly_full=0.)
    assert out.points[-1]["targets"]["TQQQ"] == .45
    assert out.points[-1]["nominal"] > .4


def test_no_baseline_cap_silent_rewrite():
    from us_equity_strategies.research.rs04_b3_runner import synthetic_sessions, replay
    with pytest.raises(ValueError, match="BASELINE_CAP_BREACH"):
        replay(synthetic_sessions(5), lambda history, book: {"TQQQ": 1.},
               profile="TQQQ", mode="baseline", kelly_full=4.)


def test_metrics_include_initial_nav_and_paired_same_blocks():
    from us_equity_strategies.research.rs04_b3_runner import path_metrics, paired_giveback
    m = path_metrics([-.1, 1/9], turnover=0.)
    assert m["max_drawdown"] == pytest.approx(.1)
    assert m["cagr"] == pytest.approx(0)
    assert m["sortino"] is not None
    base = [.001]*40+[-.001]*23
    candidate = [.0009]*40+[-.0001]*23
    result = paired_giveback(base, candidate)
    assert result["eligible_blocks"] == 1
    assert result["paired_reduction_normalized_by_baseline_peak"] > 0


def test_mr_never_averages_down_and_gap_risk_is_recapped_at_fill():
    from us_equity_strategies.research.rs04_b3_runner import Ledger
    ledger = Ledger(90000., {"SOXL": 100}, {})
    ledger.rebalance({"SOXL": .10}, {"SOXL": 50.}, profile="SOXL", cost_multiplier=1.,
                     max_quantities={"SOXL": 100})
    assert ledger.quantity["SOXL"] <= 100
    # t-1 expected risk budget must also hold at a lower next-close fill price.
    ledger = Ledger(100000., {}, {})
    ledger.rebalance({"SOXL": .1}, {"SOXL": 20.}, profile="SOXL", cost_multiplier=1.,
                     loss_per_share={"SOXL": 4.})
    assert ledger.quantity["SOXL"]*4 <= .0025*ledger.nav({"SOXL": 20.})


def test_b1_absolute_budget_does_not_apply_existing_cash_cap_twice():
    # Frozen B1: min(scope_cap, c*f*, vol_target/forecast); cap is an
    # absolute nominal budget, not an extra multiplier on a 97% target.
    out = combined_targets({"SOXL": .679, "SOXX": .194, "BOXX": .097},
                           profile="SOXL", kelly_full=2., forecast_vol=.2, drawdown=0.)
    assert sum(out.targets.values()) == pytest.approx(.5)
    assert sum(out.targets.values()) != pytest.approx(.5*.97)


def test_combined_path_exposes_upstream_b1_b2_and_actual_fills_separately():
    from us_equity_strategies.research.rs04_b3_runner import synthetic_sessions, replay
    out = replay(synthetic_sessions(350), lambda history, book: {"TQQQ": .45},
                 profile="TQQQ", mode="sizing_control", kelly_full=2.)
    point = out.points[-1]
    assert point["upstream_targets"] == {"TQQQ": .45}
    assert point["b1_nominal_budget"] <= .5
    assert point["b2_drawdown_factor"] in (0., .5, 1.)
    assert sum(point["targets"].values()) == pytest.approx(.45*point["sizing_factor"])
    assert point["nominal"] <= sum(point["targets"].values())+1e-8


def test_mr_simultaneous_exit_reasons_are_preserved():
    out = step(State(regime="RANGE", sleeve="MR", entry=100., initial_r=4., entry_index=0),
               feature(adx=15., slope=0., rsi=70., trade_price=95.), i=6, held={"SOXL": .05})
    assert out.targets["SOXL"] == 0
    assert "MR_STOP" in out.reasons and "MR_EXIT" in out.reasons
