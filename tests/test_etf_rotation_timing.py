"""Synthetic-only daily-bar timing; no provider or broker access."""
import dataclasses

import pandas as pd
import pytest

from us_equity_strategies.backtest.etf_rotation_simulator import (
    UsRotationBacktestConfig,
    require_comparable_rotation_results,
    run_etf_rotation_backtest,
)


def _history():
    return pd.DataFrame({"date": pd.to_datetime(["2026-01-30", "2026-02-02", "2026-02-03"]),
                         "symbol": "A", "open": [100., 120., 140.], "close": [100., 132., 150.]})


def _run(timing, *, history=None, cost=0.):
    return run_etf_rotation_backtest(
        _history() if history is None else history, lambda h: ({"A": 1.}, {}),
        config=UsRotationBacktestConfig(min_history_days=1, cost_bps=cost, execution_timing=timing))


def test_weekend_hand_math_distinguishes_all_three_timings():
    legacy, close, opened = (_run(t) for t in ("same_close_research_only", "next_close", "next_open"))
    assert legacy.daily_returns.tolist() == pytest.approx([0., .32, 150 / 132 - 1])
    assert close.daily_returns.tolist() == pytest.approx([0., 0., 150 / 132 - 1])
    assert opened.daily_returns.tolist() == pytest.approx([0., .1, 150 / 132 - 1])
    assert close.timing_execution == "next_close"


def test_next_close_terminal_fill_charges_fee_without_future_earning_session():
    result = _run("next_close", history=_history().iloc[:2], cost=100.)
    assert result.daily_returns.tolist() == pytest.approx([0., 1 / 1.01 - 1])


def test_next_open_gap_not_earned_before_entry_and_fee_is_budgeted():
    result = _run("next_open", cost=100.)
    assert result.daily_returns.iloc[1] == pytest.approx(1.1 / 1.01 - 1)


def test_future_prices_do_not_change_signal_history_or_return_prefix():
    history = _history()
    changed = history.copy()
    changed.loc[2, ["open", "close"]] = [1000., 999.]
    for timing in ("same_close_research_only", "next_close", "next_open"):
        assert _run(timing, history=history).daily_returns.iloc[:2].equals(
            _run(timing, history=changed).daily_returns.iloc[:2])


@pytest.mark.parametrize("price", [float("nan"), 0., -1., float("inf")])
def test_missing_or_invalid_open_never_falls_back_to_close(price):
    history = _history()
    history.loc[1, "open"] = price
    with pytest.raises(ValueError, match="fill prices"):
        _run("next_open", history=history)
    with pytest.raises(ValueError, match="explicit open"):
        _run("next_open", history=history.drop(columns="open"))


def test_comparison_rejects_mixed_timing_windows_costs_and_same_close():
    a, b = _run("next_close"), _run("next_open")
    require_comparable_rotation_results([a, a])
    for bad in (b, _run("same_close_research_only"), _run("next_close", cost=5.),
                dataclasses.replace(a, daily_returns=a.daily_returns.iloc[1:])):
        with pytest.raises(ValueError):
            require_comparable_rotation_results([a, bad])
    with pytest.raises(ValueError):
        require_comparable_rotation_results([_run("same_close_research_only")], execution="same_close_research_only")


def test_legacy_default_is_explicitly_a_research_only_control():
    result = run_etf_rotation_backtest(_history(), lambda h: ({"A": 1.}, {}),
                                      config=UsRotationBacktestConfig(min_history_days=1, cost_bps=0.))
    assert result.timing_execution == "same_close_research_only"
    with pytest.raises(ValueError):
        require_comparable_rotation_results([result])


def test_next_session_absent_never_fills_at_signal_close():
    result = _run("next_close", history=_history().iloc[:1], cost=100.)
    assert result.daily_returns.tolist() == [0.]


def test_a2_canonical_labels_and_comparison_guard():
    from datetime import date
    from us_equity_strategies.research.execution_model_adapter import (
        replay_targets, require_comparable_execution_paths,
    )
    from us_equity_strategies.research.kelly_core_return_generator import CorePath
    days = (date(2026, 1, 30), date(2026, 2, 2))
    try:
        a = replay_targets(days, (100., 120.), (100., 132.), (1., None), timing="next_close")
    except ValueError as exc:
        if str(exc) == "QPK_EXECUTION_MODELS_REQUIRED":
            pytest.skip("required A2 overlay is tested separately")
        raise
    assert CorePath("synthetic", "synthetic", (), (), (), ()).timing_execution == "next_close"
    require_comparable_execution_paths([a, a])
    for timing in ("same_close", "next_open"):
        b = replay_targets(days, (100., 120.), (100., 132.), (1., None), timing=timing)
        if timing == "same_close":
            assert b.timing_execution == "same_close_research_only"
        with pytest.raises(ValueError, match="TIMING_MISMATCH"):
            require_comparable_execution_paths([a, b])
    with pytest.raises(ValueError, match="INPUT_MISMATCH"):
        require_comparable_execution_paths([a, dataclasses.replace(a, slippage_bps=5.)])
