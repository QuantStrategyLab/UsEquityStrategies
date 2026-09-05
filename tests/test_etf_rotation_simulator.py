from __future__ import annotations

import math
from functools import reduce

import pandas as pd
import pytest

from us_equity_strategies.backtest import etf_rotation_simulator as simulator


def _history(dates, **prices):
    return pd.DataFrame(
        {"date": day, "symbol": symbol, "close": price}
        for symbol, values in prices.items()
        for day, price in zip(pd.to_datetime(dates), values, strict=True)
    )


def _run(history, signal=None, *, cost_bps=0.0, frequency="monthly"):
    return simulator.run_etf_rotation_backtest(
        history,
        signal or (lambda history: ({"A": 0.5, "B": 0.5}, {})),
        config=simulator.UsRotationBacktestConfig(
            min_history_days=1, cost_bps=cost_bps, rebalance_frequency=frequency,
        ),
    )


def test_non_rebalance_holdings_drift_without_free_rebalancing():
    history = _history(
        ["2024-01-31", "2024-02-01", "2024-02-02"],
        A=[100.0, 200.0, 100.0], B=[100.0, 100.0, 100.0],
    )
    result = _run(history)
    assert result.daily_returns.tolist() == pytest.approx([0.0, 0.5, -1.0 / 3.0])
    assert (1.0 + result.daily_returns).prod() == pytest.approx(1.0)


def test_same_target_next_explicit_rebalance_pays_for_drift():
    history = _history(
        ["2024-01-31", "2024-02-01", "2024-02-29", "2024-03-01"],
        A=[100.0, 200.0, 200.0, 200.0], B=[100.0] * 4,
    )
    result = _run(history, cost_bps=100.0)
    # First entry buys 1 / 1.01; unchanged prices on Feb 29 incur no trade.
    assert result.daily_returns.iloc[1] == pytest.approx(1.5 / 1.01 - 1.0)
    assert result.daily_returns.iloc[2] == pytest.approx(0.0)
    # At the next event: sell 0.25 / 1.01, then buy sale * .99 / 1.01.
    sale = 0.25 / 1.01
    purchase = sale * 0.99 / 1.01
    fee = (sale + purchase) * 0.01
    assert result.daily_returns.iloc[3] == pytest.approx(-fee / (1.5 / 1.01))


def test_cash_exit_reentry_and_unchanged_days():
    history = _history(
        ["2024-01-31", "2024-02-01", "2024-02-02", "2024-02-29",
         "2024-03-01", "2024-03-04", "2024-03-28", "2024-04-01"],
        A=[100.0] * 8,
    )

    def signal(frame):
        return ({"A": 1.0} if frame["date"].max().month != 2 else {}, {})

    result = _run(history, signal, cost_bps=100.0)
    assert result.daily_returns.tolist() == pytest.approx(
        [0.0, 1.0 / 1.01 - 1.0, 0.0, 0.0, -0.01, 0.0, 0.0, 1.0 / 1.01 - 1.0]
    )
    assert (1.0 + result.daily_returns).prod() == pytest.approx(0.99 / 1.01**2)


def test_target_events_remain_distinct_and_lagged_without_future_history():
    history = _history(
        ["2024-01-31", "2024-02-01", "2024-02-29", "2024-03-01"], A=[100.0] * 4,
    )
    observed = []

    def signal(frame):
        observed.append(frame["date"].max())
        return ({"A": 1.0} if frame["date"].max().month == 1 else {}, {})

    targets = simulator._target_weights(
        history, simulator.build_close_matrix(history), signal_fn=signal,
        config=simulator.UsRotationBacktestConfig(min_history_days=1), strategy_kwargs={},
    )
    assert observed == list(pd.to_datetime(["2024-01-31", "2024-02-29", "2024-03-01"]))
    assert targets.iloc[0].isna().all()
    assert targets.iloc[1]["A"] == 1.0
    assert targets.iloc[2].isna().all()
    assert targets.iloc[3]["A"] == 0.0


def test_weekly_signal_does_not_capture_signal_day_return():
    history = _history(
        ["2024-01-04", "2024-01-05", "2024-01-08"], A=[100.0, 200.0, 400.0],
    )
    result = _run(history, lambda frame: ({"A": 1.0}, {}), frequency="weekly")
    assert result.daily_returns.tolist() == pytest.approx([0.0, 0.0, 1.0])


@pytest.mark.parametrize("weight", [0.0, 0.5, 1.0])
def test_first_purchase_cash_and_fee_conservation(weight):
    shares, cash, fee = simulator._rebalance_holdings(
        pd.Series({"A": 0.0}), 1.0, pd.Series({"A": 100.0}),
        pd.Series({"A": weight}), cost_rate=0.01,
    )
    notional = min(weight, 1.0 / 1.01)
    assert shares["A"] == pytest.approx(notional / 100.0)
    assert fee == pytest.approx(notional * 0.01)
    assert cash == pytest.approx(1.0 - notional * 1.01)
    assert cash >= 0.0
    assert cash + shares["A"] * 100.0 + fee == pytest.approx(1.0)


def test_sell_before_buy_funds_both_sides_fees():
    shares, cash, fee = simulator._rebalance_holdings(
        pd.Series({"A": 0.01, "B": 0.0}), 0.0, pd.Series({"A": 100.0, "B": 50.0}),
        pd.Series({"A": 0.0, "B": 1.0}), cost_rate=0.01,
    )
    purchase = 0.99 / 1.01
    assert shares["A"] == 0.0
    assert shares["B"] == pytest.approx(purchase / 50.0)
    assert fee == pytest.approx(0.01 + purchase * 0.01)
    assert cash >= 0.0
    assert cash + shares["B"] * 50.0 + fee == pytest.approx(1.0)


def test_unused_missing_price_does_not_block_cash_or_selected_asset():
    history = _history(
        ["2024-01-31", "2024-02-01", "2024-02-02"],
        A=[100.0, 100.0, 100.0], B=[math.nan, math.nan, 100.0],
    )
    for weights in ({}, {"A": 0.5}):
        result = _run(history, lambda frame: (weights, {}))
        assert result.daily_returns.tolist() == pytest.approx([0.0, 0.0, 0.0])


@pytest.mark.parametrize("price", [0.0, -1.0, math.inf, math.nan])
def test_required_fill_price_is_rejected(price):
    history = _history(
        ["2024-01-31", "2024-02-01"], A=[price, 100.0], B=[100.0, 100.0],
    )
    with pytest.raises(ValueError):
        _run(history, lambda frame: ({"A": 1.0}, {}))


def test_invalid_held_mark_price_is_rejected_without_rebalance():
    history = _history(
        ["2024-01-31", "2024-02-01", "2024-02-02"], A=[100.0, 100.0, 0.0],
    )
    with pytest.raises(ValueError):
        _run(history, lambda frame: ({"A": 1.0}, {}))


@pytest.mark.parametrize("legacy_sum", [False, True])
def test_nine_asset_equal_weights_do_not_fail_from_sum_roundoff(legacy_sum, monkeypatch):
    if legacy_sum:
        # CI uses Python 3.11's sequential float sum; 3.12+ compensates it.
        monkeypatch.setattr(simulator, "sum", lambda values: reduce(lambda a, b: a + b, values, 0.0), raising=False)
    weights = {f"A{index}": 1.0 / 9.0 for index in range(9)}
    history = _history(
        ["2024-01-31", "2024-02-01"], **{symbol: [100.0, 100.0] for symbol in weights},
    )
    result = _run(history, lambda frame: (weights, {}))
    assert result.daily_returns.tolist() == pytest.approx([0.0, 0.0])


@pytest.mark.parametrize("weights", [{"A": -0.1}, {"A": 1.1}, {"A": math.nextafter(1.0, math.inf)}, {"A": math.nan}, {"A": math.inf}])
def test_invalid_target_is_rejected(weights):
    history = _history(["2024-01-31", "2024-02-01"], A=[100.0, 100.0])
    with pytest.raises(ValueError):
        _run(history, lambda frame: (weights, {}))


@pytest.mark.parametrize("cost_bps", [-1.0, 10_000.0, math.inf, math.nan])
def test_invalid_cost_is_rejected(cost_bps):
    history = _history(["2024-01-31", "2024-02-01"], A=[100.0, 100.0])
    with pytest.raises(ValueError):
        _run(history, lambda frame: ({"A": 1.0}, {}), cost_bps=cost_bps)


@pytest.mark.parametrize("returns, expected", [([-0.1], -0.1), ([-0.1, -0.1], -0.19), ([0.1, -0.1], -0.1)])
def test_drawdown_includes_initial_equity_without_adding_a_sample(returns, expected):
    metrics = simulator.compute_backtest_metrics(pd.Series(returns))
    assert metrics["max_drawdown"] == pytest.approx(expected)
    assert metrics["days"] == len(returns)


def test_sharpe_uses_arithmetic_return_and_population_std():
    metrics = simulator.compute_backtest_metrics(pd.Series([0.02, -0.01]))
    assert metrics["sharpe_ratio"] == pytest.approx(math.sqrt(252) / 3.0)
    assert metrics["annual_return"] == pytest.approx((1.02 * 0.99)**126 - 1.0)


@pytest.mark.parametrize("returns", [[], [0.0], [0.01, 0.01]])
def test_empty_or_zero_volatility_preserves_finite_zero_sharpe(returns):
    metrics = simulator.compute_backtest_metrics(pd.Series(returns, dtype=float))
    assert metrics["sharpe_ratio"] == 0.0
    assert metrics["days"] == len(returns)
