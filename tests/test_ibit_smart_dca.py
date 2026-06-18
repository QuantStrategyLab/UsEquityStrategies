from __future__ import annotations

import math

import pandas as pd

from quant_platform_kit.common.models import PortfolioSnapshot, Position
from quant_platform_kit.strategy_contracts import StrategyContext
from us_equity_strategies import get_strategy_definition, get_strategy_entrypoint
from us_equity_strategies.manifests import ibit_smart_dca_manifest
from us_equity_strategies.strategies.ibit_smart_dca import build_rebalance_plan


def _series(values) -> pd.Series:
    index = pd.date_range("2025-01-01", periods=len(values), freq="B")
    return pd.Series(values, index=index, dtype=float)


def _normal_history() -> pd.Series:
    return _series([30.0 + i * 0.01 for i in range(260)])


def _severe_pullback_history() -> pd.Series:
    return _series([30.0 + i * 0.08 for i in range(220)] + [47.0 - i * 0.6 for i in range(40)])


def _expensive_history() -> pd.Series:
    return _series([20.0 + i * 0.12 for i in range(260)])


def _portfolio(*, total_equity: float = 10000.0, buying_power: float = 1000.0, ibit_value: float = 100.0):
    return PortfolioSnapshot(
        as_of=pd.Timestamp("2026-05-26").to_pydatetime(),
        total_equity=total_equity,
        buying_power=buying_power,
        positions=(Position(symbol="IBIT", quantity=2, market_value=ibit_value),),
        metadata={"account_hash": "demo"},
    )


def _zh_translator(key: str, **kwargs) -> str:
    translations = {
        "no_trades": "本轮没有交易",
    }
    template = translations.get(key, key)
    return template.format(**kwargs) if kwargs else template


def test_ibit_smart_dca_buys_under_dynamic_target_sleeve() -> None:
    history = {"IBIT": _normal_history()}

    plan = build_rebalance_plan(
        lambda _client, symbol: history[symbol],
        _portfolio(),
        as_of="2026-05-26",
    )

    expected_ratio = 0.03 + 0.02 * math.log1p(10000.0 / 10000.0)
    assert plan["actionable"] is True
    assert plan["regime"] == "normal"
    assert plan["multiplier"] == 1.0
    assert plan["planned_investment_usd"] == 250.0
    assert plan["target_values"]["IBIT"] == 350.0
    assert plan["target_allocation_ratio"] == expected_ratio
    assert plan["target_allocation_value_usd"] == expected_ratio * 10000.0


def test_ibit_smart_dca_caps_pullback_buy_to_remaining_target_capacity() -> None:
    history = {"IBIT": _severe_pullback_history()}

    plan = build_rebalance_plan(
        lambda _client, symbol: history[symbol],
        _portfolio(ibit_value=300.0),
        as_of="2026-05-26",
    )

    assert plan["actionable"] is True
    assert plan["regime"] == "severe_pullback"
    assert plan["requested_investment_usd"] == 625.0
    assert plan["planned_investment_usd"] == plan["remaining_capacity_usd"]
    assert plan["target_capped"] is True
    assert plan["target_values"]["IBIT"] == plan["target_allocation_value_usd"]
    assert "target capped by remaining sleeve capacity" in plan["signal_description"]


def test_ibit_smart_dca_skips_when_target_sleeve_is_full() -> None:
    history = {"IBIT": _normal_history()}

    plan = build_rebalance_plan(
        lambda _client, symbol: history[symbol],
        _portfolio(ibit_value=500.0),
        as_of="2026-05-26",
    )

    assert plan["actionable"] is False
    assert plan["skip_reason"] == "target_allocation_reached"
    assert plan["target_values"] == {}


def test_ibit_smart_dca_skips_when_too_expensive_and_overbought() -> None:
    history = {"IBIT": _expensive_history()}

    plan = build_rebalance_plan(
        lambda _client, symbol: history[symbol],
        _portfolio(ibit_value=0.0),
        as_of="2026-05-26",
        expensive_gap=0.10,
        very_expensive_gap=0.15,
    )

    assert plan["actionable"] is False
    assert plan["skip_reason"] == "valuation_too_expensive"


def test_ibit_smart_dca_signal_uses_chinese_fallback_when_translator_is_zh() -> None:
    history = {"IBIT": _normal_history()}

    plan = build_rebalance_plan(
        lambda _client, symbol: history[symbol],
        _portfolio(ibit_value=500.0),
        as_of="2026-05-26",
        translator=_zh_translator,
    )

    assert "IBIT 智能定投" in plan["signal_description"]
    assert "跳过：目标仓位已满" in plan["signal_description"]
    assert "每月第 25 日起" in plan["status_description"]


def test_ibit_smart_dca_disables_platform_rebalance_threshold_by_default() -> None:
    catalog_config = get_strategy_definition("ibit_smart_dca").default_config
    manifest_config = ibit_smart_dca_manifest.default_config

    assert catalog_config["execution_rebalance_threshold_ratio"] == 0.0
    assert manifest_config["execution_rebalance_threshold_ratio"] == 0.0


def test_ibit_smart_dca_entrypoint_returns_value_targets_and_no_execute_flag() -> None:
    entrypoint = get_strategy_entrypoint("ibit_smart_dca")
    history = {"IBIT": _normal_history()}

    decision = entrypoint.evaluate(
        StrategyContext(
            as_of="2026-05-26",
            market_data={"market_history": lambda _client, symbol: history[symbol]},
            portfolio=_portfolio(),
            runtime_config={
                "translator": _zh_translator,
                "pacing_sec": 0.5,
                "signal_effective_after_trading_days": 0,
            },
        )
    )

    targets = {position.symbol: position.target_value for position in decision.positions}
    assert decision.risk_flags == ()
    assert targets == {"IBIT": 350.0}
    assert decision.diagnostics["signal_source"] == "market_history+portfolio_snapshot"
    assert decision.diagnostics["target_allocation_ratio"] > 0.0
    assert "IBIT 智能定投" in decision.diagnostics["signal_description"]
    assert decision.diagnostics["signal_date"] == "2026-05-26"
    assert decision.diagnostics["effective_date"] == "2026-05-26"

    target_full_decision = entrypoint.evaluate(
        StrategyContext(
            as_of="2026-05-26",
            market_data={"market_history": lambda _client, symbol: history[symbol]},
            portfolio=_portfolio(ibit_value=500.0),
            runtime_config={"translator": lambda key, **_kwargs: key},
        )
    )
    assert target_full_decision.positions == ()
    assert target_full_decision.risk_flags == ("no_execute",)
