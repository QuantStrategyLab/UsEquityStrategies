from __future__ import annotations

import pandas as pd

from us_equity_strategies.backtests.ibit_smart_dca import compare_smart_vs_fixed_dca


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
    )

    assert result["smart"].trades
    assert result["fixed"].trades
    assert result["smart"].trades[0]["date"] == result["fixed"].trades[0]["date"]
    assert result["smart"].trades[0]["date"] >= "2026-06-01"
