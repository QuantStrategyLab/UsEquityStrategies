from __future__ import annotations

import pandas as pd
import pytest

from quant_platform_kit.common.models import PortfolioSnapshot, Position
from quant_platform_kit.strategy_contracts import StrategyContext
from us_equity_strategies import get_strategy_definition, get_strategy_entrypoint
from us_equity_strategies.manifests import ibit_smart_dca_manifest
from us_equity_strategies.strategies.ibit_smart_dca import build_rebalance_plan


def _series(values) -> pd.Series:
    index = pd.date_range("2025-01-01", periods=len(values), freq="B")
    return pd.Series(values, index=index, dtype=float)


BTC_SIGNAL_SYMBOL = "BTC-USD"


def _normal_history() -> pd.Series:
    return _series([30.0 + i * 0.01 for i in range(260)])


def _severe_pullback_history() -> pd.Series:
    return _series([30.0 + i * 0.08 for i in range(220)] + [47.0 - i * 0.6 for i in range(40)])


def _expensive_history() -> pd.Series:
    return _series([20.0 + i * 0.12 for i in range(260)])


def _portfolio(*, total_equity: float = 10000.0, buying_power: float = 5000.0, ibit_value: float = 100.0):
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


def _unavailable_history(_client, _symbol):
    raise AssertionError("ordinary DCA should not fetch market history")


def test_ibit_smart_dca_defaults_to_ordinary_dca_like_main_dca_profile() -> None:
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
    assert plan["target_values"]["IBIT"] == 1100.0
    assert plan["indicator_rows"] == ()


def test_ibit_smart_dca_can_run_fixed_amount_ordinary_dca() -> None:
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
    assert plan["target_values"]["IBIT"] == 1100.0


def test_ibit_smart_dca_skips_pullback_buy_when_cash_is_below_requested_amount() -> None:
    history = {BTC_SIGNAL_SYMBOL: _severe_pullback_history()}

    plan = build_rebalance_plan(
        lambda _client, symbol: history[symbol],
        _portfolio(buying_power=1550.0, ibit_value=300.0),
        as_of="2026-05-26",
        investment_amount_mode="fixed",
        max_investment_usd=2000.0,
        smart_multiplier_enabled=True,
    )

    assert plan["actionable"] is False
    assert plan["skip_reason"] == "insufficient_cash"
    assert plan["regime"] == "severe_pullback"
    assert plan["requested_investment_usd"] == 2000.0
    assert plan["planned_investment_usd"] == 0.0
    assert plan["cash_capped"] is True
    assert plan["cash_shortfall_usd"] == 450.0
    assert plan["target_values"] == {}


def test_ibit_smart_dca_waits_when_cash_is_below_minimum() -> None:
    history = {BTC_SIGNAL_SYMBOL: _normal_history()}

    plan = build_rebalance_plan(
        lambda _client, symbol: history[symbol],
        _portfolio(buying_power=4.0, ibit_value=500.0),
        as_of="2026-05-26",
    )

    assert plan["actionable"] is False
    assert plan["skip_reason"] == "insufficient_cash"
    assert plan["planned_investment_usd"] == 0.0
    assert plan["cash_capped"] is True
    assert plan["target_values"] == {}


def test_ibit_smart_dca_uses_fractional_friendly_default_minimum() -> None:
    plan = build_rebalance_plan(
        _unavailable_history,
        _portfolio(buying_power=180.0, ibit_value=500.0),
        as_of="2026-05-26",
        base_investment_usd=180.0,
    )

    assert plan["actionable"] is True
    assert plan["min_investment_usd"] == 5.0
    assert plan["planned_investment_usd"] == 180.0
    assert plan["target_values"]["IBIT"] == 680.0


def test_ibit_smart_dca_rejects_available_cash_amount_modes() -> None:
    with pytest.raises(ValueError, match="investment_amount_mode must be 'fixed'"):
        build_rebalance_plan(
            _unavailable_history,
            _portfolio(buying_power=1550.0, ibit_value=300.0),
            as_of="2026-05-26",
            investment_amount_mode="available_cash_ratio",
        )


def test_ibit_smart_dca_skips_when_too_expensive_and_overbought() -> None:
    history = {BTC_SIGNAL_SYMBOL: _expensive_history()}

    plan = build_rebalance_plan(
        lambda _client, symbol: history[symbol],
        _portfolio(ibit_value=0.0),
        as_of="2026-05-26",
        investment_amount_mode="fixed",
        smart_multiplier_enabled=True,
        expensive_gap=0.10,
        very_expensive_gap=0.15,
    )

    assert plan["actionable"] is False
    assert plan["skip_reason"] == "valuation_too_expensive"


def test_ibit_smart_dca_signal_uses_chinese_fallback_when_translator_is_zh() -> None:
    plan = build_rebalance_plan(
        _unavailable_history,
        _portfolio(buying_power=4.0, ibit_value=500.0),
        as_of="2026-05-26",
        translator=_zh_translator,
    )

    assert "IBIT 普通定投" in plan["signal_description"]
    assert "跳过：可投资现金不足" in plan["signal_description"]
    assert "每月第 25 日起" in plan["status_description"]


def test_ibit_smart_dca_monthly_window_can_cover_retry_days() -> None:
    retry_day_plan = build_rebalance_plan(
        _unavailable_history,
        _portfolio(),
        as_of="2026-05-29",
    )
    outside_window_plan = build_rebalance_plan(
        _unavailable_history,
        _portfolio(),
        as_of="2026-05-30",
    )

    assert retry_day_plan["actionable"] is True
    assert retry_day_plan["execution_window"] == "monthly_day=25 window_calendar_days=5"
    assert outside_window_plan["actionable"] is False
    assert outside_window_plan["skip_reason"] == "outside_execution_window"


def test_ibit_smart_dca_weekly_window_can_cover_retry_days() -> None:
    retry_day_plan = build_rebalance_plan(
        _unavailable_history,
        _portfolio(),
        as_of="2026-06-01",
        cadence="weekly",
        weekly_day=4,
        weekly_window_calendar_days=4,
    )
    outside_window_plan = build_rebalance_plan(
        _unavailable_history,
        _portfolio(),
        as_of="2026-06-02",
        cadence="weekly",
        weekly_day=4,
        weekly_window_calendar_days=4,
    )

    assert retry_day_plan["actionable"] is True
    assert retry_day_plan["execution_window"] == "weekly_day=4 window_calendar_days=4"
    assert outside_window_plan["actionable"] is False
    assert outside_window_plan["skip_reason"] == "outside_execution_window"


def test_ibit_smart_dca_quarterly_window_can_cover_retry_days() -> None:
    retry_day_plan = build_rebalance_plan(
        _unavailable_history,
        _portfolio(),
        as_of="2026-04-29",
        cadence="quarterly",
    )
    outside_window_plan = build_rebalance_plan(
        _unavailable_history,
        _portfolio(),
        as_of="2026-05-01",
        cadence="quarterly",
    )

    assert retry_day_plan["actionable"] is True
    assert (
        retry_day_plan["execution_window"]
        == "quarterly_months=1,4,7,10 quarterly_day=25 window_calendar_days=5"
    )
    assert outside_window_plan["actionable"] is False
    assert outside_window_plan["skip_reason"] == "outside_execution_window"


def test_ibit_smart_dca_disables_platform_rebalance_threshold_by_default() -> None:
    catalog_config = get_strategy_definition("ibit_smart_dca").default_config
    manifest_config = ibit_smart_dca_manifest.default_config

    assert catalog_config["execution_rebalance_threshold_ratio"] == 0.0
    assert manifest_config["execution_rebalance_threshold_ratio"] == 0.0


def test_ibit_smart_dca_entrypoint_returns_value_targets_and_no_execute_flag() -> None:
    entrypoint = get_strategy_entrypoint("ibit_smart_dca")
    history = {BTC_SIGNAL_SYMBOL: _normal_history()}

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
    assert targets == {"IBIT": 1100.0}
    assert decision.diagnostics["signal_source"] == "market_history+portfolio_snapshot"
    assert decision.diagnostics["signal_symbols"] == (BTC_SIGNAL_SYMBOL,)
    assert decision.diagnostics["planned_investment_usd"] == 1000.0
    assert decision.diagnostics["investment_amount_mode"] == "fixed"
    assert decision.diagnostics["smart_multiplier_enabled"] is False
    assert "IBIT 普通定投" in decision.diagnostics["signal_description"]
    assert decision.diagnostics["signal_date"] == "2026-05-26"
    assert decision.diagnostics["effective_date"] == "2026-05-26"

    target_full_decision = entrypoint.evaluate(
        StrategyContext(
            as_of="2026-05-26",
            market_data={"market_history": lambda _client, symbol: history[symbol]},
            portfolio=_portfolio(buying_power=4.0, ibit_value=500.0),
            runtime_config={"translator": lambda key, **_kwargs: key},
        )
    )
    assert target_full_decision.positions == ()
    assert target_full_decision.risk_flags == ("no_execute",)
