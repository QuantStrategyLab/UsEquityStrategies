from __future__ import annotations

import pandas as pd
import pytest
from quant_platform_kit.strategy_lifecycle.backtest_orchestrator import (
    BacktestOrchestrator,
)

import us_equity_strategies.backtest.spy_boxx_monthly_trend_learning as learning


def _history(*, sessions: int = 523) -> pd.DataFrame:
    dates = pd.bdate_range("2023-08-01", periods=sessions)
    rows: list[dict[str, object]] = []
    for index, day in enumerate(dates):
        spy = 100.0 + index * 0.2
        if index >= 400:
            spy -= (index - 399) * 0.8
        rows.extend(
            (
                {
                    "date": day,
                    "symbol": "SPY",
                    "open": spy,
                    "high": spy * 1.01,
                    "low": spy * 0.99,
                    "close": spy,
                },
                {
                    "date": day,
                    "symbol": "BOXX",
                    "open": 100.0 + index * 0.03,
                    "high": (100.0 + index * 0.03) * 1.001,
                    "low": (100.0 + index * 0.03) * 0.999,
                    "close": 100.0 + index * 0.03,
                },
            )
        )
    return pd.DataFrame(rows)


def _flat_history(*, sessions: int = 523) -> pd.DataFrame:
    history = _history(sessions=sessions)
    for column in ("open", "high", "low", "close"):
        history[column] = 100.0
    return history


def _set_bar(
    history: pd.DataFrame,
    *,
    session: int,
    symbol: str = "SPY",
    open_price: float,
    low_price: float,
    close_price: float,
) -> None:
    row = (history["symbol"] == symbol) & (
        history["date"] == history["date"].unique()[session]
    )
    history.loc[row, ["open", "high", "low", "close"]] = (
        open_price,
        max(open_price, close_price),
        low_price,
        close_price,
    )


def _fixed_spy_decision(*, target_weight: float = 0.20):
    def _decision(close, *, as_of, account_equity, drawdown_scalar):
        return learning.StrategyDecision(
            positions=(
                learning.PositionTarget(symbol="SPY", target_weight=target_weight),
            )
        )

    return _decision


def test_monthly_decision_uses_lagged_200_session_signal_and_risk_gate() -> None:
    history = _history()
    approved = learning.build_monthly_decision(history, as_of=history["date"].iloc[401])
    assert approved.positions[0].symbol == "SPY"
    assert approved.positions[0].target_weight == pytest.approx(0.20)
    assert "risk_gate:passed" in approved.risk_flags

    falling = history.copy()
    spy_rows = falling["symbol"] == "SPY"
    falling_spy_close = pd.Series(range(523, 0, -1), index=falling.index[spy_rows])
    falling.loc[spy_rows, "open"] = falling_spy_close
    falling.loc[spy_rows, "high"] = falling_spy_close * 1.01
    falling.loc[spy_rows, "low"] = falling_spy_close * 0.99
    falling.loc[spy_rows, "close"] = falling_spy_close
    defensive = learning.build_monthly_decision(
        falling, as_of=falling["date"].iloc[401]
    )
    assert defensive.positions[0].symbol == "BOXX"
    assert defensive.positions[0].target_weight == pytest.approx(0.20)
    assert defensive.diagnostics["signal_lag_sessions"] == 1


def test_runner_returns_aggregate_only_and_uses_discarding_walk_forward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"walk_forward": 0, "risk_gate": 0}
    original_walk_forward = BacktestOrchestrator.walk_forward
    original_risk_gate = learning.apply_risk_gate

    def _walk_forward(*args, **kwargs):
        calls["walk_forward"] += 1
        windows = kwargs["windows"]
        dates = _history()["date"].unique()
        assert windows == (
            (dates[251].date(), dates[376].date()),
            (dates[397].date(), dates[522].date()),
        )
        return original_walk_forward(*args, **kwargs)

    def _risk_gate(decision, **kwargs):
        calls["risk_gate"] += 1
        assert set(kwargs) == {
            "ctx",
            "risk_mandate_id",
            "product_leverage_factors",
            "available_account_exposure",
        }
        assert kwargs["risk_mandate_id"] == "bootstrap_small_account_v2"
        assert kwargs["product_leverage_factors"] in ({"SPY": 1}, {"BOXX": 1})
        assert kwargs["available_account_exposure"] == 0.50
        result = original_risk_gate(decision, **kwargs)
        assert kwargs["ctx"].portfolio is not None
        return result

    monkeypatch.setattr(BacktestOrchestrator, "walk_forward", _walk_forward)
    monkeypatch.setattr(learning, "apply_risk_gate", _risk_gate)
    result = learning.run_spy_boxx_monthly_trend_learning(_history())

    assert calls["walk_forward"] == 1
    assert calls["risk_gate"] > 0
    assert set(result) == {
        "folds",
        "sessions",
        "sharpe",
        "cagr",
        "mdd",
        "turnover",
        "costs",
        "risk_rejects",
        "effective_exposure_cap",
        "max_effective_exposure",
        "risk_mandate_id",
        "stop_events",
        "stop_gap_open_events",
        "stop_slippage_costs",
        "drawdown_breaker",
        "embargo_sessions",
        "decision_label",
        "learning_only",
        "promotion_eligible",
        "live_ready",
        "size_zero_required",
        "no_order",
    }
    assert result["folds"] == 2
    assert result["sessions"] == 252
    assert result["risk_rejects"] == 0
    assert result["costs"] == pytest.approx(
        result["turnover"] * learning.COST_PER_SIDE_BPS / 10_000.0
    )
    assert result["learning_only"] is True
    assert result["promotion_eligible"] is False
    assert result["live_ready"] is False
    assert result["size_zero_required"] is True
    assert result["no_order"] is True
    assert result["risk_mandate_id"] == "bootstrap_small_account_v2"
    assert result["effective_exposure_cap"] == 0.50
    assert result["embargo_sessions"] == 20


def test_runner_rejects_non_frozen_session_count() -> None:
    with pytest.raises(ValueError, match="523"):
        learning.run_spy_boxx_monthly_trend_learning(_history(sessions=522))


def test_runner_rejects_missing_adjusted_ohlc_or_invalid_prices() -> None:
    missing_open = _history().drop(columns="open")
    with pytest.raises(ValueError, match="open"):
        learning.run_spy_boxx_monthly_trend_learning(missing_open)

    invalid_low = _history()
    invalid_low.loc[0, "low"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        learning.run_spy_boxx_monthly_trend_learning(invalid_low)


def test_runner_executes_gap_stop_without_same_session_reentry() -> None:
    history = _history()
    gap_index = 263
    gap_price = 80.0
    spy = history["symbol"] == "SPY"
    history.loc[
        spy & (history["date"] == history["date"].unique()[gap_index]),
        ["open", "high", "low", "close"],
    ] = (
        gap_price,
        gap_price + 1.0,
        gap_price - 1.0,
        gap_price,
    )

    result = learning.run_spy_boxx_monthly_trend_learning(history)

    assert result["stop_events"] >= 1
    assert result["stop_gap_open_events"] >= 1
    assert result["stop_slippage_costs"] > 0.0


def test_each_oos_fold_starts_without_formation_portfolio_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = _history()
    oos_start = history["date"].unique()[learning.FORMATION_SESSIONS]
    decision_dates: list[pd.Timestamp] = []

    def _no_position_decision(close, *, as_of, **kwargs):
        decision_dates.append(pd.Timestamp(as_of))
        return learning.StrategyDecision(positions=())

    monkeypatch.setattr(learning, "_monthly_decision_from_close", _no_position_decision)
    runner = learning.SpyBoxxMonthlyTrendLearningRunner(history)
    runner.run(
        learning.PROFILE_NAME,
        {"learning_only": True, "no_order": True},
        start_date=oos_start.date(),
        end_date=history["date"].unique()[
            learning.FORMATION_SESSIONS + learning.OOS_FOLD_SESSIONS - 1
        ].date(),
    )

    expected_initial_signal = history["date"].unique()[239]
    assert decision_dates[0] == expected_initial_signal
    assert all(
        decision_date == expected_initial_signal or decision_date >= oos_start
        for decision_date in decision_dates
    )


def test_fold_initial_exposure_uses_pre_boundary_close_without_oos_entry_trade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = _flat_history()
    _set_bar(
        history,
        session=251,
        open_price=110.0,
        low_price=109.0,
        close_price=110.0,
    )
    monkeypatch.setattr(
        learning, "_monthly_decision_from_close", _fixed_spy_decision()
    )
    runner = learning.SpyBoxxMonthlyTrendLearningRunner(history)

    returns, turnover, costs, _ = runner._simulate(start_index=251, end_index=251)

    assert returns.iloc[0] == pytest.approx(0.20 * (110.0 / 100.0 - 1.0))
    assert turnover.iloc[0] == 0.0
    assert costs.iloc[0] == 0.0


def test_position_units_and_equity_are_conserved_between_monthly_decisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = _flat_history()
    _set_bar(
        history,
        session=252,
        open_price=110.0,
        low_price=109.0,
        close_price=110.0,
    )
    _set_bar(
        history,
        session=253,
        open_price=121.0,
        low_price=120.0,
        close_price=121.0,
    )
    monkeypatch.setattr(
        learning, "_monthly_decision_from_close", _fixed_spy_decision()
    )
    runner = learning.SpyBoxxMonthlyTrendLearningRunner(history)

    returns, turnover, costs, _ = runner._simulate(start_index=251, end_index=253)

    expected_equity = 0.80 + 0.20 * (121.0 / 100.0)
    assert float((1.0 + returns).prod()) == pytest.approx(expected_equity)
    assert returns.iloc[2] == pytest.approx(expected_equity / 1.02 - 1.0)
    assert turnover.sum() == 0.0
    assert costs.sum() == 0.0


def test_current_bar_loss_updates_drawdown_scalar_before_monthly_sizing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = _flat_history()
    _set_bar(
        history,
        session=252,
        open_price=94.0,
        low_price=94.0,
        close_price=94.0,
    )
    _set_bar(
        history,
        session=284,
        open_price=96.0,
        low_price=96.0,
        close_price=96.0,
    )
    decision_scalars: dict[pd.Timestamp, float] = {}

    def _decision(close, *, as_of, account_equity, drawdown_scalar):
        decision_scalars[pd.Timestamp(as_of)] = drawdown_scalar
        return learning.StrategyDecision(
            positions=(learning.PositionTarget(symbol="SPY", target_weight=0.50),)
        )

    monkeypatch.setattr(learning, "_monthly_decision_from_close", _decision)
    runner = learning.SpyBoxxMonthlyTrendLearningRunner(history)

    runner._simulate(start_index=251, end_index=284)

    september_rebalance = pd.Timestamp(history["date"].unique()[284])
    assert decision_scalars[september_rebalance] == 0.5


def test_gap_stop_slippage_matches_actual_portfolio_return_impact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = _flat_history()
    _set_bar(
        history,
        session=252,
        open_price=50.0,
        low_price=49.0,
        close_price=50.0,
    )
    monkeypatch.setattr(
        learning, "_monthly_decision_from_close", _fixed_spy_decision()
    )
    runner = learning.SpyBoxxMonthlyTrendLearningRunner(history)

    runner._simulate(start_index=251, end_index=252)

    unslipped_fill = 50.0
    slipped_fill = unslipped_fill * (1.0 - learning.STOP_SLIPPAGE_BPS / 10_000.0)
    units = 0.20 / 100.0
    assert runner._stop_slippage_costs == pytest.approx(
        units * (unslipped_fill - slipped_fill)
    )


def test_historical_decision_is_stable_when_future_rows_are_appended() -> None:
    full_history = _flat_history(sessions=800)
    historical_as_of = full_history["date"].unique()[400]
    causal_history = full_history[
        full_history["date"] <= historical_as_of
    ].copy()

    causal_decision = learning.build_monthly_decision(
        causal_history, as_of=historical_as_of
    )
    appended_decision = learning.build_monthly_decision(
        full_history, as_of=historical_as_of
    )

    assert appended_decision == causal_decision


def test_adding_to_a_position_refreshes_the_stop_basis() -> None:
    assert learning._blended_entry_price(100.0, 0.10, 200.0, 0.10) == pytest.approx(
        150.0
    )


def test_adjusted_ohlc_keeps_exchange_local_session_date() -> None:
    history = _history()
    original_first_date = history["date"].iloc[0].date()
    history["date"] = history["date"].dt.tz_localize("America/New_York") + pd.Timedelta(
        hours=20
    )

    bars = learning._frozen_ohlc_matrices(history)

    assert bars["close"].index[0].date() == original_first_date


@pytest.mark.parametrize(
    ("drawdown", "expected"),
    ((0.05, 1.0), (0.050001, 0.5), (0.10, 0.5), (0.100001, 0.0)),
)
def test_completed_equity_drawdown_scalar_parks_only_above_ten_percent(
    drawdown: float, expected: float
) -> None:
    assert learning._drawdown_scalar(drawdown) == expected
