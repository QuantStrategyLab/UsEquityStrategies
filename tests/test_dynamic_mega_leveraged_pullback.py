from __future__ import annotations

import pandas as pd

from quant_platform_kit.common.models import PortfolioSnapshot
from quant_platform_kit.strategy_contracts import StrategyContext
from us_equity_strategies import get_strategy_entrypoint
from us_equity_strategies.strategies.dynamic_mega_leveraged_pullback import (
    build_target_weights,
    compute_signals,
)


def _snapshot() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"as_of": "2026-03-31", "symbol": "NVDL", "underlying_symbol": "NVDA", "sector": "Information Technology", "candidate_rank": 1, "product_leverage": 2.0, "product_available": True},
            {"as_of": "2026-03-31", "symbol": "MSFU", "underlying_symbol": "MSFT", "sector": "Information Technology", "candidate_rank": 2, "product_leverage": 2.0, "product_available": True},
            {"as_of": "2026-03-31", "symbol": "AAPU", "underlying_symbol": "AAPL", "sector": "Information Technology", "candidate_rank": 3, "product_leverage": 2.0, "product_available": True},
            {"as_of": "2026-03-31", "symbol": "AMZU", "underlying_symbol": "AMZN", "sector": "Consumer Discretionary", "candidate_rank": 4, "product_leverage": 2.0, "product_available": True},
        ]
    )


def _benchmark_history(*, above_entry: bool = True, below_exit: bool = False) -> list[dict[str, float | str]]:
    dates = pd.bdate_range("2025-01-02", periods=280)
    rows = []
    for idx, as_of in enumerate(dates):
        close = 100.0 + idx * 0.25
        if not above_entry:
            close = 100.0 + idx * 0.035
        if below_exit and idx > 260:
            close *= 0.82
        rows.append(
            {
                "as_of": as_of.date().isoformat(),
                "close": close,
                "high": close * 1.005,
                "low": close * 0.995,
            }
        )
    return rows


def _underlying_close(symbol: str):
    slope = {"NVDA": 0.45, "MSFT": 0.30, "AAPL": 0.22, "AMZN": 0.26}.get(symbol, 0.20)
    pullback = {"NVDA": 0.08, "MSFT": 0.05, "AAPL": 0.12, "AMZN": 0.03}.get(symbol, 0.06)
    dates = pd.bdate_range("2025-01-02", periods=280)
    values = []
    for idx, _as_of in enumerate(dates):
        close = 90.0 + idx * slope
        if idx > 265:
            close *= 1.0 - pullback
        values.append(close)
    return pd.Series(values, index=dates)


def _portfolio() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        as_of=pd.Timestamp("2026-04-01").to_pydatetime(),
        total_equity=20_000.0,
        buying_power=5_000.0,
        positions=(),
        metadata={},
    )


def test_build_target_weights_selects_top3_2x_products_when_qqq_clears_atr_entry() -> None:
    weights, ranked, metadata = build_target_weights(
        _snapshot(),
        lambda _ib, symbol, **_kwargs: _underlying_close(symbol),
        _benchmark_history(above_entry=True),
        current_holdings=set(),
        portfolio_total_equity=20_000.0,
        ib=None,
    )

    assert metadata["regime"] == "risk_on_entry"
    assert 1 <= metadata["selected_count"] <= 3
    assert set(weights) - {"BOXX"} <= {"NVDL", "MSFU", "AAPU", "AMZU"}
    assert "NVDA" not in set(weights)
    assert not ranked.empty


def test_entry_wait_stays_in_boxx_until_qqq_clears_atr_entry_line() -> None:
    weights, _ranked, metadata = build_target_weights(
        _snapshot(),
        lambda _ib, symbol, **_kwargs: _underlying_close(symbol),
        _benchmark_history(above_entry=False),
        current_holdings=set(),
        portfolio_total_equity=20_000.0,
        ib=None,
    )

    assert metadata["regime"] == "entry_wait"
    assert weights == {"BOXX": 1.0}


def test_existing_risk_position_exits_when_qqq_breaks_exit_line() -> None:
    weights, _signal, is_emergency, _status, metadata = compute_signals(
        _snapshot(),
        current_holdings={"NVDL"},
        market_history=lambda _ib, symbol, **_kwargs: _underlying_close(symbol),
        benchmark_history=_benchmark_history(below_exit=True),
        portfolio=_portfolio(),
    )

    assert is_emergency is True
    assert metadata["regime"] == "hard_defense"
    assert weights == {"BOXX": 1.0}


def test_entrypoint_uses_feature_snapshot_market_history_benchmark_history_and_portfolio() -> None:
    entrypoint = get_strategy_entrypoint("dynamic_mega_leveraged_pullback")
    decision = entrypoint.evaluate(
        StrategyContext(
            as_of="2026-04-01",
            market_data={
                "feature_snapshot": _snapshot(),
                "market_history": lambda _ib, symbol, **_kwargs: _underlying_close(symbol),
                "benchmark_history": _benchmark_history(above_entry=True),
                "portfolio_snapshot": _portfolio(),
            },
            portfolio=_portfolio(),
            state={"current_holdings": set()},
            runtime_config={},
        )
    )

    assert decision.positions
    assert decision.diagnostics["signal_source"] == "feature_snapshot+daily_market_history"
    assert decision.diagnostics["effective_holdings_count"] <= 3
