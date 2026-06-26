from __future__ import annotations

import pandas as pd
import pytest

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


def _zh_translator(key: str, **kwargs) -> str:
    translations = {
        "no_trades": "本轮没有交易",
    }
    template = translations.get(key, key)
    return template.format(**kwargs) if kwargs else template


def _unavailable_history(_client, _symbol):
    raise AssertionError("ordinary DCA should not fetch market history")


def test_smart_dca_defaults_to_ordinary_dca_with_default_split() -> None:
    plan = build_rebalance_plan(
        _unavailable_history,
        _portfolio(),
        as_of="2026-05-26",
    )

    assert plan["actionable"] is True
    assert plan["regime"] == "ordinary_dca"
    assert plan["multiplier"] == 1.0
    assert plan["smart_multiplier_enabled"] is False
    assert plan["investment_amount_mode"] == "fixed"
    assert plan["base_investment_budget_usd"] == 1000.0
    assert plan["requested_investment_usd"] == 1000.0
    assert plan["planned_investment_usd"] == 1000.0
    assert plan["cash_capped"] is False
    assert plan["target_values"]["QQQM"] == 1500.0
    assert plan["target_values"]["SPLG"] == 1700.0
    assert plan["indicator_rows"] == ()


def test_smart_dca_can_run_fixed_amount_ordinary_dca() -> None:
    plan = build_rebalance_plan(
        _unavailable_history,
        _portfolio(),
        as_of="2026-05-26",
        investment_amount_mode="fixed",
    )

    assert plan["actionable"] is True
    assert plan["regime"] == "ordinary_dca"
    assert plan["base_investment_budget_usd"] == 1000.0
    assert plan["planned_investment_usd"] == 1000.0
    assert plan["target_values"]["QQQM"] == 1500.0
    assert plan["target_values"]["SPLG"] == 1700.0


def test_smart_dca_skips_when_too_expensive_and_overbought() -> None:
    history = {"QQQ": _expensive_history(), "SPY": _expensive_history()}

    plan = build_rebalance_plan(
        lambda _client, symbol: history[symbol],
        _portfolio(),
        as_of="2026-05-26",
        investment_amount_mode="fixed",
        smart_multiplier_enabled=True,
        expensive_gap=0.10,
        very_expensive_gap=0.15,
        very_expensive_multiplier=0.0,
    )

    assert plan["actionable"] is False
    assert plan["skip_reason"] == "valuation_too_expensive"
    assert plan["target_values"] == {}


def test_smart_dca_uses_external_technical_indicator_snapshot() -> None:
    def unavailable_history(_client, _symbol):
        raise AssertionError("external indicators should avoid market_history")

    plan = build_rebalance_plan(
        unavailable_history,
        _portfolio(),
        as_of="2026-05-26",
        smart_multiplier_enabled=True,
        technical_indicator_snapshot={
            "QQQ": {
                "close": 90.0,
                "sma50": 95.0,
                "sma200": 100.0,
                "high252": 120.0,
                "drawdown_252d": 0.20,
                "sma200_gap": -0.10,
                "rsi14": 42.0,
            },
            "SPY": {
                "close": 180.0,
                "sma50": 190.0,
                "sma200": 200.0,
                "high252": 240.0,
                "drawdown_252d": 0.20,
                "sma200_gap": -0.10,
                "rsi14": 44.0,
            },
        },
    )

    assert plan["actionable"] is True
    assert plan["regime"] == "deep_pullback"
    assert plan["multiplier"] == 1.25
    assert plan["avg_sma200_gap"] == pytest.approx(-0.10)
    assert plan["avg_drawdown_252d"] == pytest.approx(0.20)
    assert plan["signal_symbols"] == ("QQQ", "SPY")


def test_smart_dca_waits_when_cash_is_below_minimum() -> None:
    history = {"QQQ": _normal_history(), "SPY": _normal_history()}

    plan = build_rebalance_plan(
        lambda _client, symbol: history[symbol],
        _portfolio(buying_power=4.0),
        as_of="2026-05-26",
    )

    assert plan["actionable"] is False
    assert plan["skip_reason"] == "insufficient_cash"
    assert plan["planned_investment_usd"] == 0.0
    assert plan["cash_capped"] is True


def test_smart_dca_uses_fractional_friendly_default_minimum() -> None:
    plan = build_rebalance_plan(
        _unavailable_history,
        _portfolio(buying_power=180.0),
        as_of="2026-05-26",
        base_investment_usd=180.0,
    )

    assert plan["actionable"] is True
    assert plan["min_investment_usd"] == 5.0
    assert plan["planned_investment_usd"] == 180.0
    assert plan["target_values"]["QQQM"] == 1090.0
    assert plan["target_values"]["SPLG"] == 1290.0


def test_smart_dca_rejects_available_cash_amount_modes() -> None:
    with pytest.raises(ValueError, match="investment_amount_mode must be 'fixed'"):
        build_rebalance_plan(
            _unavailable_history,
            _portfolio(buying_power=1550.0),
            as_of="2026-05-26",
            investment_amount_mode="available_cash_ratio",
        )


def test_smart_dca_invests_partial_cash_when_pullback_multiplier_exceeds_balance() -> None:
    history = {"QQQ": _severe_pullback_history(), "SPY": _severe_pullback_history()}

    plan = build_rebalance_plan(
        lambda _client, symbol: history[symbol],
        _portfolio(buying_power=1450.0),
        as_of="2026-05-26",
        investment_amount_mode="fixed",
        max_investment_usd=2000.0,
        smart_multiplier_enabled=True,
    )

    assert plan["actionable"] is True
    assert plan["skip_reason"] is None
    assert plan["requested_investment_usd"] == 1500.0
    assert plan["planned_investment_usd"] == 1450.0
    assert plan["cash_capped"] is True
    assert plan["cash_shortfall_usd"] == 50.0
    assert plan["target_values"]["QQQM"] == 1725.0
    assert plan["target_values"]["SPLG"] == 1925.0


def test_smart_dca_skips_pullback_buy_when_cash_is_below_requested_amount() -> None:
    history = {"QQQ": _severe_pullback_history(), "SPY": _severe_pullback_history()}

    plan = build_rebalance_plan(
        lambda _client, symbol: history[symbol],
        _portfolio(buying_power=4.0),
        as_of="2026-05-26",
        investment_amount_mode="fixed",
        max_investment_usd=2000.0,
        smart_multiplier_enabled=True,
    )

    assert plan["actionable"] is False
    assert plan["skip_reason"] == "insufficient_cash"
    assert plan["requested_investment_usd"] == 1500.0
    assert plan["planned_investment_usd"] == 0.0
    assert plan["cash_capped"] is True
    assert plan["cash_shortfall_usd"] == 1496.0
    assert plan["target_values"] == {}


def test_smart_dca_signal_uses_chinese_fallback_when_translator_is_zh() -> None:
    plan = build_rebalance_plan(
        _unavailable_history,
        _portfolio(buying_power=4.0),
        as_of="2026-05-26",
        translator=_zh_translator,
    )

    assert "普通定投" in plan["signal_description"]
    assert "跳过：可投资现金不足" in plan["signal_description"]
    assert "每月第 25 日起" in plan["status_description"]
    assert "avg drawdown" not in plan["status_description"]
    assert "Smart DCA" not in plan["signal_description"]


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
                "translator": _zh_translator,
                "pacing_sec": 0.5,
                "signal_effective_after_trading_days": 0,
                "investment_amount_mode": "fixed",
            },
        )
    )

    targets = {position.symbol: position.target_value for position in decision.positions}
    assert decision.risk_flags == ()
    assert targets == {"QQQM": 1500.0, "SPLG": 1700.0}
    assert decision.diagnostics["signal_source"] == "derived_indicators/market_history+portfolio_snapshot"
    assert decision.diagnostics["investment_amount_mode"] == "fixed"
    assert decision.diagnostics["smart_multiplier_enabled"] is False
    assert "普通定投" in decision.diagnostics["signal_description"]
    assert decision.diagnostics["signal_date"] == "2026-05-26"
    assert decision.diagnostics["effective_date"] == "2026-05-26"

    expensive_decision = entrypoint.evaluate(
        StrategyContext(
            as_of="2026-05-26",
            market_data={"market_history": lambda _client, symbol: _expensive_history()},
            portfolio=_portfolio(),
            runtime_config={
                "translator": lambda key, **_kwargs: key,
                "investment_amount_mode": "fixed",
                "smart_multiplier_enabled": True,
                "expensive_gap": 0.10,
                "very_expensive_gap": 0.15,
                "very_expensive_multiplier": 0.0,
            },
        )
    )
    assert expensive_decision.positions == ()
    assert expensive_decision.risk_flags == ("no_execute",)


def test_smart_dca_entrypoint_accepts_unified_derived_indicators() -> None:
    entrypoint = get_strategy_entrypoint("nasdaq_sp500_smart_dca")
    decision = entrypoint.evaluate(
        StrategyContext(
            as_of="2026-05-26",
            market_data={
                "derived_indicators": {
                    "QQQ": {
                        "close": 90.0,
                        "sma50": 95.0,
                        "sma200": 100.0,
                        "high252": 120.0,
                        "drawdown_252d": 0.20,
                        "sma200_gap": -0.10,
                        "rsi14": 42.0,
                    },
                    "SPY": {
                        "close": 180.0,
                        "sma50": 190.0,
                        "sma200": 200.0,
                        "high252": 240.0,
                        "drawdown_252d": 0.20,
                        "sma200_gap": -0.10,
                        "rsi14": 44.0,
                    },
                }
            },
            portfolio=_portfolio(),
            runtime_config={
                "investment_amount_mode": "fixed",
                "smart_multiplier_enabled": True,
            },
        )
    )

    targets = {position.symbol: position.target_value for position in decision.positions}
    assert targets == {"QQQM": 1625.0, "SPLG": 1825.0}
    assert decision.diagnostics["regime"] == "deep_pullback"
    assert decision.diagnostics["avg_sma200_gap"] == pytest.approx(-0.10)


def test_smart_dca_entrypoint_applies_platform_reserved_cash_floor() -> None:
    entrypoint = get_strategy_entrypoint("nasdaq_sp500_smart_dca")
    decision = entrypoint.evaluate(
        StrategyContext(
            as_of="2026-05-26",
            market_data={"market_history": lambda _client, symbol: _normal_history()},
            portfolio=_portfolio(),
            runtime_config={
                "translator": _zh_translator,
                "investment_amount_mode": "fixed",
                "reserved_cash_floor_usd": 4500.0,
                "reserved_cash_ratio": 0.03,
            },
        )
    )

    targets = {position.symbol: position.target_value for position in decision.positions}
    assert decision.risk_flags == ()
    assert decision.diagnostics["reserved_cash"] == 4500.0
    assert decision.diagnostics["investable_cash"] == 500.0
    assert decision.diagnostics["requested_investment_usd"] == 1000.0
    assert decision.diagnostics["planned_investment_usd"] == 500.0
    assert decision.diagnostics["skip_reason"] is None
    assert decision.diagnostics["cash_capped"] is True
    assert targets == {"QQQM": 1250.0, "SPLG": 1450.0}
