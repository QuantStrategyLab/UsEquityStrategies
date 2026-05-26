from __future__ import annotations

import pandas as pd

from quant_platform_kit.common.models import PortfolioSnapshot, Position
from quant_platform_kit.strategy_contracts import StrategyContext
from us_equity_strategies import get_strategy_definition, get_strategy_entrypoint
from us_equity_strategies.manifests import nasdaq_sp500_smart_dca_manifest
from us_equity_strategies.strategies.nasdaq_sp500_smart_dca import build_rebalance_plan


def _series(values) -> pd.Series:
    index = pd.date_range("2025-01-01", periods=len(values), freq="B")
    return pd.Series(values, index=index, dtype=float)


def _normal_history() -> pd.Series:
    return _series([100.0 + i * 0.02 for i in range(260)])


def _severe_pullback_history() -> pd.Series:
    return _series(
        [100.0 + i * 0.10 for i in range(220)] + [122.0 - i * 1.0 for i in range(40)]
    )


def _expensive_history() -> pd.Series:
    return _series([100.0 + i * 0.20 for i in range(260)])


def _portfolio(*, buying_power: float = 5000.0) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        as_of=pd.Timestamp("2026-05-26").to_pydatetime(),
        total_equity=10000.0,
        buying_power=buying_power,
        positions=(
            Position(symbol="QQQM", quantity=10, market_value=1000.0),
            Position(symbol="SPLG", quantity=20, market_value=1200.0),
        ),
        metadata={"account_hash": "demo"},
    )


def test_smart_dca_buys_only_current_window_with_default_split() -> None:
    history = {"QQQ": _normal_history(), "SPY": _normal_history()}

    plan = build_rebalance_plan(
        lambda _client, symbol: history[symbol],
        _portfolio(),
        as_of="2026-05-26",
    )

    assert plan["actionable"] is True
    assert plan["regime"] == "normal"
    assert plan["multiplier"] == 1.0
    assert plan["planned_investment_usd"] == 1000.0
    assert plan["target_values"]["QQQM"] == 1500.0
    assert plan["target_values"]["SPLG"] == 1700.0


def test_smart_dca_skips_when_too_expensive_and_overbought() -> None:
    history = {"QQQ": _expensive_history(), "SPY": _expensive_history()}

    plan = build_rebalance_plan(
        lambda _client, symbol: history[symbol],
        _portfolio(),
        as_of="2026-05-26",
        expensive_gap=0.10,
        very_expensive_gap=0.15,
    )

    assert plan["actionable"] is False
    assert plan["skip_reason"] == "valuation_too_expensive"
    assert plan["target_values"] == {}


def test_smart_dca_waits_when_cash_is_below_minimum() -> None:
    history = {"QQQ": _normal_history(), "SPY": _normal_history()}

    plan = build_rebalance_plan(
        lambda _client, symbol: history[symbol],
        _portfolio(buying_power=180.0),
        as_of="2026-05-26",
    )

    assert plan["actionable"] is False
    assert plan["skip_reason"] == "insufficient_cash"


def test_smart_dca_caps_pullback_buy_to_investable_cash() -> None:
    history = {"QQQ": _severe_pullback_history(), "SPY": _severe_pullback_history()}

    plan = build_rebalance_plan(
        lambda _client, symbol: history[symbol],
        _portfolio(buying_power=1550.0),
        as_of="2026-05-26",
    )

    assert plan["actionable"] is True
    assert plan["regime"] == "severe_pullback"
    assert plan["requested_investment_usd"] == 2000.0
    assert plan["planned_investment_usd"] == 1500.0
    assert plan["cash_capped"] is True
    assert plan["cash_shortfall_usd"] == 500.0
    assert plan["target_values"]["QQQM"] == 1750.0
    assert plan["target_values"]["SPLG"] == 1950.0
    assert "cash capped from requested $2,000.00" in plan["signal_description"]


def test_smart_dca_disables_platform_rebalance_threshold_by_default() -> None:
    catalog_config = get_strategy_definition("nasdaq_sp500_smart_dca").default_config
    manifest_config = nasdaq_sp500_smart_dca_manifest.default_config

    assert catalog_config["execution_rebalance_threshold_ratio"] == 0.0
    assert manifest_config["execution_rebalance_threshold_ratio"] == 0.0


def test_smart_dca_entrypoint_returns_value_targets_and_no_execute_flag() -> None:
    entrypoint = get_strategy_entrypoint("nasdaq_sp500_smart_dca")
    history = {"QQQ": _normal_history(), "SPY": _normal_history()}

    decision = entrypoint.evaluate(
        StrategyContext(
            as_of="2026-05-26",
            market_data={"market_history": lambda _client, symbol: history[symbol]},
            portfolio=_portfolio(),
            runtime_config={
                "translator": lambda key, **_kwargs: key,
                "pacing_sec": 0.5,
                "signal_effective_after_trading_days": 0,
            },
        )
    )

    targets = {position.symbol: position.target_value for position in decision.positions}
    assert decision.risk_flags == ()
    assert targets == {"QQQM": 1500.0, "SPLG": 1700.0}
    assert decision.diagnostics["signal_source"] == "market_history+portfolio_snapshot"
    assert decision.diagnostics["signal_date"] == "2026-05-26"
    assert decision.diagnostics["effective_date"] == "2026-05-26"

    expensive_decision = entrypoint.evaluate(
        StrategyContext(
            as_of="2026-05-26",
            market_data={"market_history": lambda _client, symbol: _expensive_history()},
            portfolio=_portfolio(),
            runtime_config={
                "translator": lambda key, **_kwargs: key,
                "expensive_gap": 0.10,
                "very_expensive_gap": 0.15,
            },
        )
    )
    assert expensive_decision.positions == ()
    assert expensive_decision.risk_flags == ("no_execute",)
