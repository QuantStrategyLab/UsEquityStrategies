from __future__ import annotations

import pandas as pd
import pytest

from us_equity_strategies.backtests.ibit_smart_dca import compare_smart_vs_fixed_dca
from us_equity_strategies.backtests import ibit_smart_dca as helper


def _series(values) -> pd.Series:
    index = pd.date_range("2025-01-01", periods=len(values), freq="B")
    return pd.Series(values, index=index, dtype=float)


def test_compare_smart_vs_fixed_dca_returns_parallel_results() -> None:
    prices = _series([100.0 + i * 0.05 for i in range(320)])

    result = compare_smart_vs_fixed_dca(
        signal_prices=prices,
        trade_prices=prices,
        monthly_contribution_usd=1000.0,
        align_start_after_smart_warmup=False,
    )

    assert set(result) == {"smart", "fixed"}
    assert result["smart"].name == "smart"
    assert result["fixed"].name == "fixed"
    assert result["smart"].terminal_value > 0.0
    assert result["fixed"].terminal_value > 0.0
    assert result["smart"].contributions == result["fixed"].contributions


def test_fixed_dca_path_does_not_wait_for_smart_indicator_history() -> None:
    prices = _series([100.0 + i * 0.05 for i in range(120)])

    result = compare_smart_vs_fixed_dca(
        signal_prices=prices,
        trade_prices=prices,
        monthly_contribution_usd=1000.0,
    )

    assert result["smart"].trades == ()
    assert result["fixed"].trades
    assert result["fixed"].invested > 0.0


def test_compare_can_start_both_paths_from_explicit_date_after_warmup() -> None:
    prices = _series([100.0 + i * 0.05 for i in range(520)])

    result = compare_smart_vs_fixed_dca(
        signal_prices=prices,
        trade_prices=prices,
        monthly_contribution_usd=1000.0,
        start_date="2026-06-01",
        plan_overrides={"cycle_indicator_enabled": False},
    )

    assert result["smart"].trades
    assert result["fixed"].trades
    assert result["smart"].trades[0]["date"] == result["fixed"].trades[0]["date"]
    assert result["smart"].trades[0]["date"] >= "2026-06-01"


def test_helper_uses_only_prior_signal_sessions_after_warmup(monkeypatch) -> None:
    prior = pd.bdate_range(end="2025-01-03", periods=252)
    execution = pd.Timestamp("2025-01-06")
    signals = pd.Series(100.0, index=prior.append(pd.DatetimeIndex([execution])))
    observed = []

    def plan(loader, portfolio, **kwargs):
        history = loader(None, None)
        observed.append((kwargs["as_of"], history.index[-1], len(history)))
        return {"actionable": False, "skip_reason": "synthetic_skip"}

    monkeypatch.setattr(helper, "build_rebalance_plan", plan)
    result = compare_smart_vs_fixed_dca(signal_prices=signals, trade_prices=signals)

    assert observed == [(execution, prior[-1], 252), (execution, prior[-1], 252)]
    assert result["smart"].contributions == result["fixed"].contributions == 1000.0


def test_fixed_drawdown_is_contribution_neutral_and_later_cash_cannot_clear_it() -> None:
    prices = pd.Series([100.0, 50.0, 50.0], index=pd.to_datetime([
        "2025-01-25", "2025-02-25", "2025-03-25",
    ]))
    fixed = compare_smart_vs_fixed_dca(
        signal_prices=prices, trade_prices=prices, align_start_after_smart_warmup=False,
    )["fixed"]

    assert fixed.terminal_value == 2500.0
    assert fixed.max_drawdown == pytest.approx(0.25)
    assert fixed.cash + fixed.shares * 50.0 == fixed.terminal_value


def test_missing_same_session_signal_does_not_drop_a_trade_price_or_contribution() -> None:
    dates = pd.to_datetime(["2025-01-25", "2025-02-25"])
    trades = pd.Series([100.0, 50.0], index=dates)
    fixed = compare_smart_vs_fixed_dca(
        signal_prices=trades.iloc[:1], trade_prices=trades,
        align_start_after_smart_warmup=False,
    )["fixed"]

    assert fixed.contributions == 2000.0
    assert fixed.terminal_value == 1500.0
    assert fixed.max_drawdown == pytest.approx(0.25)


def test_fixed_budget_tracks_contributions_unless_explicitly_overridden() -> None:
    prices = pd.Series([100.0], index=pd.to_datetime(["2025-01-25"]))
    fixed = compare_smart_vs_fixed_dca(
        signal_prices=prices, trade_prices=prices, monthly_contribution_usd=2500.0,
    )["fixed"]
    capped = compare_smart_vs_fixed_dca(
        signal_prices=prices, trade_prices=prices, monthly_contribution_usd=2500.0,
        plan_overrides={"base_investment_usd": 200.0},
    )["fixed"]

    assert fixed.invested == 2500.0
    assert fixed.cash == 0.0
    assert capped.invested == 200.0
    assert capped.cash == 2300.0
