from __future__ import annotations

from quant_platform_kit.strategy_contracts import StrategyContext

from us_equity_strategies.entrypoints._common import apply_market_regime_control_to_weights


def _market_regime_payload(route: str, scalar: float) -> dict[str, object]:
    return {
        "plugin": "market_regime_control",
        "schema_version": "market_regime_control.v1",
        "canonical_route": route,
        "suggested_action": "delever" if route == "risk_reduced" else "defend",
        "position_control": {
            "final_route": route,
            "suggested_action": "delever" if route == "risk_reduced" else "defend",
            "route_source": "macro",
            "risk_budget_scalar": scalar,
            "risk_asset_scalar": scalar,
            "reason_codes": ("macro:vix_crisis_level",),
        },
        "localized_messages": {
            "schema_version": "strategy_plugin_messages.v1",
            "notification": {
                "en-US": "Notification required: market regime risk reduced.",
                "zh-CN": "需要通知：市场状态风险降低。",
            },
            "log": {
                "en-US": "route=risk_reduced action=delever",
                "zh-CN": "路线=risk_reduced 动作=delever",
            },
        },
        "log_record": {
            "schema_version": "strategy_plugin_log.v1",
            "event": "strategy_plugin_signal",
            "localized_messages": {"zh-CN": "路线=risk_reduced 动作=delever"},
        },
        "notification": {
            "localized_message_schema_version": "strategy_plugin_messages.v1",
            "localized_messages": {"zh-CN": "需要通知：市场状态风险降低。"},
        },
    }


def test_market_regime_control_weight_consumer_scales_risk_weight_to_safe_haven() -> None:
    weights, diagnostics = apply_market_regime_control_to_weights(
        {"AAPL": 0.80, "BOXX": 0.20},
        market_regime_control_config={
            "market_regime_control_enabled": True,
            "market_regime_control_risk_reduced_scalar": 0.50,
        },
        ctx=StrategyContext(
            as_of="2026-05-28",
            artifacts={"market_regime_control": _market_regime_payload("risk_reduced", 0.0)},
        ),
        safe_haven="BOXX",
    )

    assert weights is not None
    assert weights["AAPL"] == 0.40
    assert round(weights["BOXX"], 10) == 0.60
    assert diagnostics["market_regime_control_applied"] is True
    assert diagnostics["market_regime_control_removed_weight"] == 0.40
    assert diagnostics["market_regime_control_risk_symbols"] == ("AAPL",)
    market_regime_notice = diagnostics["market_regime_control_notification_context"]["risk_controls"][
        "market_regime_control"
    ]
    assert market_regime_notice["localized_messages"]["schema_version"] == "strategy_plugin_messages.v1"
    assert market_regime_notice["log_record"]["schema_version"] == "strategy_plugin_log.v1"
    assert market_regime_notice["notification"]["localized_message_schema_version"] == "strategy_plugin_messages.v1"


def test_market_regime_control_weight_consumer_can_leave_risk_reduced_notification_only() -> None:
    weights, diagnostics = apply_market_regime_control_to_weights(
        {"AAPL": 0.80, "BOXX": 0.20},
        market_regime_control_config={
            "market_regime_control_enabled": True,
            "market_regime_control_apply_risk_reduced": False,
        },
        ctx=StrategyContext(
            as_of="2026-05-28",
            artifacts={"market_regime_control": _market_regime_payload("risk_reduced", 0.0)},
        ),
        safe_haven="BOXX",
    )

    assert weights == {"AAPL": 0.80, "BOXX": 0.20}
    assert diagnostics["market_regime_control_active"] is True
    assert diagnostics["market_regime_control_route_allowed"] is False
    assert diagnostics["market_regime_control_applied"] is False
