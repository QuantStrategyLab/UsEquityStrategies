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


@pytest.mark.parametrize(
    ("drawdown", "expected"),
    ((0.05, 1.0), (0.050001, 0.5), (0.10, 0.5), (0.100001, 0.0)),
)
def test_completed_equity_drawdown_scalar_parks_only_above_ten_percent(
    drawdown: float, expected: float
) -> None:
    assert learning._drawdown_scalar(drawdown) == expected
