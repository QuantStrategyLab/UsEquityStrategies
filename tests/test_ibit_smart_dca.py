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


def _portfolio(
    *,
    total_equity: float = 10000.0,
    buying_power: float = 5000.0,
    ibit_value: float = 100.0,
    boxx_value: float = 0.0,
    metadata: dict | None = None,
):
    positions = [Position(symbol="IBIT", quantity=2, market_value=ibit_value)]
    if boxx_value:
        positions.append(Position(symbol="BOXX", quantity=1, market_value=boxx_value))
    return PortfolioSnapshot(
        as_of=pd.Timestamp("2026-05-26").to_pydatetime(),
        total_equity=total_equity,
        buying_power=buying_power,
        positions=tuple(positions),
        metadata=metadata or {"account_hash": "demo"},
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
        cycle_indicator_enabled=False,
    )

    assert plan["actionable"] is False
    assert plan["skip_reason"] == "insufficient_cash"
    assert plan["regime"] == "severe_pullback"
    assert plan["requested_investment_usd"] == 2000.0
    assert plan["planned_investment_usd"] == 0.0
    assert plan["cash_capped"] is True
    assert plan["cash_shortfall_usd"] == 450.0
    assert plan["target_values"] == {}


def test_ibit_smart_dca_sells_boxx_cash_substitute_to_fund_dca() -> None:
    plan = build_rebalance_plan(
        _unavailable_history,
        _portfolio(buying_power=200.0, ibit_value=1000.0, boxx_value=900.0),
        as_of="2026-05-26",
        base_investment_usd=1000.0,
    )

    assert plan["actionable"] is True
    assert plan["planned_investment_usd"] == 1000.0
    assert plan["cash_capped"] is False
    assert plan["cash_shortfall_usd"] == 800.0
    assert plan["cash_substitute_symbol"] == "BOXX"
    assert plan["cash_substitute_value_usd"] == 900.0
    assert plan["cash_substitute_used_usd"] == 800.0
    assert plan["cash_substitute_funding_shortfall_usd"] == 0.0
    assert plan["target_values"] == {"BOXX": 100.0, "IBIT": 2000.0}
    assert plan["managed_symbols"] == ("IBIT", "BOXX")
    assert plan["ibit_zscore_exit"]["enabled"] is False
    assert plan["ibit_zscore_exit"]["found"] is False
    assert plan["ibit_zscore_exit"]["applied"] is False


def test_ibit_smart_dca_skips_when_cash_and_boxx_are_both_insufficient() -> None:
    plan = build_rebalance_plan(
        _unavailable_history,
        _portfolio(buying_power=200.0, ibit_value=1000.0, boxx_value=300.0),
        as_of="2026-05-26",
        base_investment_usd=1000.0,
    )

    assert plan["actionable"] is False
    assert plan["skip_reason"] == "insufficient_cash"
    assert plan["planned_investment_usd"] == 0.0
    assert plan["cash_capped"] is True
    assert plan["cash_shortfall_usd"] == 800.0
    assert plan["cash_substitute_value_usd"] == 300.0
    assert plan["cash_substitute_used_usd"] == 0.0
    assert plan["cash_substitute_funding_shortfall_usd"] == 500.0
    assert plan["target_values"] == {}


def test_ibit_smart_dca_can_disable_boxx_cash_substitute_for_dca() -> None:
    plan = build_rebalance_plan(
        _unavailable_history,
        _portfolio(buying_power=200.0, ibit_value=1000.0, boxx_value=900.0),
        as_of="2026-05-26",
        base_investment_usd=1000.0,
        cash_substitute_for_dca_enabled=False,
    )

    assert plan["actionable"] is False
    assert plan["skip_reason"] == "insufficient_cash"
    assert plan["cash_substitute_for_dca_enabled"] is False
    assert plan["cash_substitute_symbol"] == ""
    assert plan["cash_substitute_value_usd"] == 0.0
    assert plan["cash_substitute_used_usd"] == 0.0
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
        cycle_indicator_enabled=False,
        expensive_gap=0.10,
        very_expensive_gap=0.15,
        very_expensive_multiplier=0.0,
    )

    assert plan["actionable"] is False
    assert plan["skip_reason"] == "valuation_too_expensive"


def test_ibit_smart_dca_uses_external_ahr999_indicator_snapshot() -> None:
    plan = build_rebalance_plan(
        _unavailable_history,
        _portfolio(buying_power=2500.0, ibit_value=300.0),
        as_of="2026-05-26",
        smart_multiplier_enabled=True,
        crypto_indicator_snapshot={
            BTC_SIGNAL_SYMBOL: {
                "ahr999": 0.70,
                "mayer_multiple": 0.85,
            }
        },
    )

    assert plan["actionable"] is True
    assert plan["regime"] == "ahr999_accumulation"
    assert plan["multiplier"] == 2.25
    assert plan["requested_investment_usd"] == 2250.0
    assert plan["planned_investment_usd"] == 2250.0
    assert plan["target_values"]["IBIT"] == 2550.0
    assert plan["ahr999"] == 0.70
    assert plan["mayer_multiple"] == 0.85
    assert plan["cycle_indicator_source"] == "derived_indicators"


def test_ibit_smart_dca_skips_when_external_ahr999_is_expensive() -> None:
    plan = build_rebalance_plan(
        _unavailable_history,
        _portfolio(buying_power=5000.0, ibit_value=300.0),
        as_of="2026-05-26",
        smart_multiplier_enabled=True,
        crypto_indicator_snapshot={BTC_SIGNAL_SYMBOL: {"ahr999": 1.35}},
    )

    assert plan["actionable"] is False
    assert plan["skip_reason"] == "valuation_too_expensive"
    assert plan["regime"] == "ahr999_expensive"
    assert plan["requested_investment_usd"] == 0.0
    assert plan["target_values"] == {}


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
    assert catalog_config["ibit_zscore_exit_enabled"] is False
    assert manifest_config["ibit_zscore_exit_enabled"] is False
    assert catalog_config["ibit_zscore_exit_mode"] == "paper"
    assert manifest_config["ibit_zscore_exit_mode"] == "paper"
    assert catalog_config["ibit_zscore_exit_parking_symbol"] == "BOXX"
    assert manifest_config["ibit_zscore_exit_parking_symbol"] == "BOXX"
    assert catalog_config["cash_substitute_for_dca_enabled"] is True
    assert manifest_config["cash_substitute_for_dca_enabled"] is True
    assert catalog_config["cash_substitute_symbol"] == "BOXX"
    assert manifest_config["cash_substitute_symbol"] == "BOXX"


def test_ibit_zscore_exit_paper_mode_does_not_change_targets() -> None:
    plan = build_rebalance_plan(
        _unavailable_history,
        _portfolio(ibit_value=1000.0),
        as_of="2026-05-26",
        ibit_zscore_exit_enabled=True,
        ibit_zscore_exit_mode="paper",
        ibit_zscore_exit_context={
            "plugin": "ibit_zscore_exit",
            "schema_version": "ibit_zscore_exit.v1",
            "canonical_route": "risk_reduced",
            "position_control": {
                "target_allocations": {"IBIT": 0.50, "BOXX": 0.50},
                "reason_codes": ["mvrv_zscore_above_dynamic_soft_exit"],
            },
            "metrics": {"mvrv_zscore": 7.2},
        },
    )

    assert plan["actionable"] is True
    assert plan["target_values"] == {"IBIT": 2000.0}
    assert plan["ibit_zscore_exit"]["found"] is True
    assert plan["ibit_zscore_exit"]["enabled"] is True
    assert plan["ibit_zscore_exit"]["applied"] is False
    assert plan["ibit_zscore_exit"]["mode"] == "paper"


def test_ibit_zscore_exit_defaults_to_research_only_when_context_is_present() -> None:
    plan = build_rebalance_plan(
        _unavailable_history,
        _portfolio(buying_power=1000.0, ibit_value=1000.0),
        as_of="2026-05-26",
        ibit_zscore_exit_context={
            "plugin": "ibit_zscore_exit",
            "schema_version": "ibit_zscore_exit.v1",
            "mode": "shadow",
            "canonical_route": "risk_off",
            "execution_controls": {
                "position_control_allowed": True,
                "consumption_evidence_status": "automation_approved",
            },
            "position_control": {
                "final_route": "risk_off",
                "target_allocations": {"IBIT": 0.25, "BOXX": 0.75},
            },
        },
    )

    assert plan["target_values"] == {"IBIT": 2000.0}
    assert plan["ibit_zscore_exit"]["mode"] == "paper"
    assert plan["ibit_zscore_exit"]["enabled"] is False
    assert plan["ibit_zscore_exit"]["found"] is True
    assert plan["ibit_zscore_exit"]["applied"] is False


def test_ibit_zscore_exit_can_be_disabled_after_default_enablement() -> None:
    plan = build_rebalance_plan(
        _unavailable_history,
        _portfolio(buying_power=1000.0, ibit_value=1000.0),
        as_of="2026-05-26",
        ibit_zscore_exit_enabled=False,
        ibit_zscore_exit_context={
            "plugin": "ibit_zscore_exit",
            "schema_version": "ibit_zscore_exit.v1",
            "canonical_route": "risk_off",
            "position_control": {
                "final_route": "risk_off",
                "target_allocations": {"IBIT": 0.25, "BOXX": 0.75},
            },
        },
    )

    assert plan["target_values"] == {"IBIT": 2000.0}
    assert plan["ibit_zscore_exit"]["found"] is True
    assert plan["ibit_zscore_exit"]["enabled"] is False
    assert plan["ibit_zscore_exit"]["applied"] is False


def test_ibit_zscore_exit_live_rebalances_to_parking_inside_monthly_window() -> None:
    plan = build_rebalance_plan(
        _unavailable_history,
        _portfolio(buying_power=1000.0, ibit_value=1000.0),
        as_of="2026-05-26",
        ibit_zscore_exit_enabled=True,
        ibit_zscore_exit_mode="live",
        ibit_zscore_exit_context={
            "plugin": "ibit_zscore_exit",
            "schema_version": "ibit_zscore_exit.v1",
            "mode": "live",
            "position_control": {
                "final_route": "risk_reduced",
                "target_allocations": {"IBIT": 0.50, "BOXX": 0.50},
                "reason_codes": ["mvrv_zscore_above_dynamic_soft_exit"],
            },
            "metrics": {"mvrv_zscore": 7.2},
        },
    )

    assert plan["actionable"] is True
    assert plan["skip_reason"] is None
    assert plan["planned_investment_usd"] == 1000.0
    assert plan["target_values"] == {"IBIT": 1000.0, "BOXX": 1000.0}
    assert plan["managed_symbols"] == ("IBIT", "BOXX")
    assert plan["ibit_zscore_exit"]["applied"] is True
    assert plan["ibit_zscore_exit"]["target_ibit_exposure"] == 0.50
    assert plan["ibit_zscore_exit"]["parking_symbol"] == "BOXX"


def test_ibit_zscore_exit_live_can_rebalance_outside_monthly_window() -> None:
    plan = build_rebalance_plan(
        _unavailable_history,
        _portfolio(buying_power=1000.0, ibit_value=1000.0),
        as_of="2026-05-30",
        ibit_zscore_exit_enabled=True,
        ibit_zscore_exit_mode="live",
        ibit_zscore_exit_context={
            "plugin": "ibit_zscore_exit",
            "schema_version": "ibit_zscore_exit.v1",
            "mode": "live",
            "position_control": {
                "final_route": "risk_off",
                "target_allocations": {"IBIT": 0.25, "BOXX": 0.75},
                "reason_codes": ["mvrv_zscore_above_dynamic_hard_exit"],
            },
        },
    )

    assert plan["actionable"] is True
    assert plan["skip_reason"] is None
    assert plan["in_execution_window"] is False
    assert plan["planned_investment_usd"] == 0.0
    assert plan["target_values"] == {"IBIT": 250.0, "BOXX": 750.0}
    assert plan["ibit_zscore_exit"]["applied"] is True
    assert plan["ibit_zscore_exit"]["route"] == "risk_off"


def test_ibit_zscore_exit_live_accepts_shadow_artifact_when_position_control_is_approved() -> None:
    plan = build_rebalance_plan(
        _unavailable_history,
        _portfolio(buying_power=1000.0, ibit_value=1000.0),
        as_of="2026-05-30",
        ibit_zscore_exit_enabled=True,
        ibit_zscore_exit_mode="live",
        ibit_zscore_exit_context={
            "plugin": "ibit_zscore_exit",
            "schema_version": "ibit_zscore_exit.v1",
            "mode": "shadow",
            "canonical_route": "risk_off",
            "execution_controls": {
                "position_control_allowed": True,
                "consumption_evidence_status": "automation_approved",
            },
            "position_control": {
                "final_route": "risk_off",
                "target_allocations": {"IBIT": 0.25, "BOXX": 0.75},
            },
        },
    )

    assert plan["actionable"] is True
    assert plan["target_values"] == {"IBIT": 250.0, "BOXX": 750.0}
    assert plan["ibit_zscore_exit"]["payload_mode"] == "shadow"
    assert plan["ibit_zscore_exit"]["position_control_authorized"] is True
    assert plan["ibit_zscore_exit"]["applied"] is True


def test_ibit_zscore_exit_live_ignores_artifact_without_automation_approval() -> None:
    plan = build_rebalance_plan(
        _unavailable_history,
        _portfolio(ibit_value=1000.0),
        as_of="2026-05-26",
        ibit_zscore_exit_enabled=True,
        ibit_zscore_exit_mode="live",
        ibit_zscore_exit_context={
            "plugin": "ibit_zscore_exit",
            "schema_version": "ibit_zscore_exit.v1",
            "mode": "shadow",
            "canonical_route": "risk_off",
            "execution_controls": {
                "position_control_allowed": True,
                "consumption_evidence_status": "notification_only",
            },
            "position_control": {
                "final_route": "risk_off",
                "target_allocations": {"IBIT": 0.25, "BOXX": 0.75},
            },
        },
    )

    assert plan["target_values"] == {"IBIT": 2000.0}
    assert plan["ibit_zscore_exit"]["position_control_authorized"] is False
    assert plan["ibit_zscore_exit"]["applied"] is False


def test_ibit_zscore_exit_ignores_unrelated_strategy_plugin_position_control() -> None:
    plan = build_rebalance_plan(
        _unavailable_history,
        _portfolio(
            ibit_value=1000.0,
            metadata={
                "account_hash": "demo",
                "strategy_plugins": [
                    {
                        "plugin": "market_regime_control",
                        "mode": "live",
                        "position_control": {
                            "final_route": "risk_off",
                            "target_allocations": {"IBIT": 0.0, "BOXX": 1.0},
                        },
                    }
                ],
            },
        ),
        as_of="2026-05-26",
        ibit_zscore_exit_enabled=True,
        ibit_zscore_exit_mode="live",
    )

    assert plan["target_values"] == {"IBIT": 2000.0}
    assert plan["ibit_zscore_exit"]["found"] is False
    assert plan["ibit_zscore_exit"]["applied"] is False


def test_ibit_smart_dca_entrypoint_returns_value_targets_and_no_execute_flag() -> None:
    entrypoint = get_strategy_entrypoint("ibit_smart_dca")

    decision = entrypoint.evaluate(
        StrategyContext(
            as_of="2026-05-26",
            market_data={"derived_indicators": {}},
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
    assert decision.diagnostics["signal_source"] == "derived_indicators/market_history+portfolio_snapshot"
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
            market_data={"derived_indicators": {}},
            portfolio=_portfolio(buying_power=4.0, ibit_value=500.0),
            runtime_config={"translator": lambda key, **_kwargs: key},
        )
    )
    assert target_full_decision.positions == ()
    assert target_full_decision.risk_flags == ("no_execute",)


def test_ibit_smart_dca_entrypoint_emits_boxx_sell_target_for_cash_substitute_dca() -> None:
    entrypoint = get_strategy_entrypoint("ibit_smart_dca")

    decision = entrypoint.evaluate(
        StrategyContext(
            as_of="2026-05-26",
            market_data={"derived_indicators": {}},
            portfolio=_portfolio(buying_power=200.0, ibit_value=1000.0, boxx_value=900.0),
            runtime_config={"investment_amount_mode": "fixed"},
        )
    )

    targets = {position.symbol: position.target_value for position in decision.positions}
    roles = {position.symbol: position.role for position in decision.positions}
    assert decision.risk_flags == ()
    assert targets == {"BOXX": 100.0, "IBIT": 2000.0}
    assert roles["BOXX"] == "safe_haven"
    assert decision.diagnostics["cash_substitute_used_usd"] == 800.0
    assert decision.diagnostics["execution_annotations"]["cash_substitute_used_usd"] == 800.0


def test_ibit_smart_dca_entrypoint_uses_derived_indicators_for_ahr999() -> None:
    entrypoint = get_strategy_entrypoint("ibit_smart_dca")

    decision = entrypoint.evaluate(
        StrategyContext(
            as_of="2026-05-26",
            market_data={
                "derived_indicators": {
                    BTC_SIGNAL_SYMBOL: {
                        "ahr999": 0.40,
                        "mayer_multiple": 0.80,
                    }
                }
            },
            portfolio=_portfolio(buying_power=5000.0, ibit_value=500.0),
            runtime_config={
                "smart_multiplier_enabled": True,
            },
        )
    )

    targets = {position.symbol: position.target_value for position in decision.positions}
    assert decision.risk_flags == ()
    assert targets == {"IBIT": 3500.0}
    assert decision.diagnostics["regime"] == "ahr999_bottom"
    assert decision.diagnostics["multiplier"] == 3.0
    assert decision.diagnostics["ahr999"] == 0.40
    assert decision.diagnostics["cycle_indicator_source"] == "derived_indicators"


def test_ibit_smart_dca_entrypoint_consumes_zscore_exit_plugin_metadata() -> None:
    entrypoint = get_strategy_entrypoint("ibit_smart_dca")

    decision = entrypoint.evaluate(
        StrategyContext(
            as_of="2026-05-30",
            market_data={"derived_indicators": {}},
            portfolio=_portfolio(
                buying_power=1000.0,
                ibit_value=1000.0,
                metadata={
                    "account_hash": "demo",
                    "strategy_plugins": [
                        {
                            "plugin": "ibit_zscore_exit",
                            "schema_version": "ibit_zscore_exit.v1",
                            "mode": "live",
                            "position_control": {
                                "final_route": "risk_off",
                                "target_allocations": {"IBIT": 0.25, "BOXX": 0.75},
                            },
                        }
                    ],
                },
            ),
            runtime_config={
                "ibit_zscore_exit_enabled": True,
                "ibit_zscore_exit_mode": "live",
            },
        )
    )

    targets = {position.symbol: position.target_value for position in decision.positions}
    assert decision.risk_flags == ()
    assert targets == {"BOXX": 750.0, "IBIT": 250.0}
    assert decision.diagnostics["planned_investment_usd"] == 0.0
    assert decision.diagnostics["ibit_zscore_exit"]["applied"] is True
    assert decision.diagnostics["ibit_zscore_exit"]["source"] == "portfolio.metadata.strategy_plugins"


def test_ibit_smart_dca_entrypoint_applies_platform_reserved_cash_floor() -> None:
    entrypoint = get_strategy_entrypoint("ibit_smart_dca")
    history = {BTC_SIGNAL_SYMBOL: _normal_history()}

    decision = entrypoint.evaluate(
        StrategyContext(
            as_of="2026-05-26",
            market_data={"market_history": lambda _client, symbol: history[symbol]},
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
    assert decision.risk_flags == ("no_execute",)
    assert decision.diagnostics["reserved_cash"] == 4500.0
    assert decision.diagnostics["investable_cash"] == 500.0
    assert decision.diagnostics["requested_investment_usd"] == 1000.0
    assert decision.diagnostics["planned_investment_usd"] == 0.0
    assert decision.diagnostics["skip_reason"] == "insufficient_cash"
    assert targets == {}
