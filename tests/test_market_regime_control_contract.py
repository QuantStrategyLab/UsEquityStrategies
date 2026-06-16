from __future__ import annotations

from us_equity_strategies.market_regime_control_contract import (
    resolve_market_regime_position_control_authorization,
)


def test_market_regime_execution_controls_authorize_position_control() -> None:
    authorization = resolve_market_regime_position_control_authorization(
        {
            "execution_controls": {
                "position_control_allowed": True,
                "consumption_evidence_status": "automation_approved",
            }
        }
    )

    assert authorization["position_control_allowed"] is True
    assert authorization["position_control_authorized"] is True
    assert authorization["consumption_evidence_status"] == "automation_approved"


def test_market_regime_consumption_policy_remains_compatible_fallback() -> None:
    authorization = resolve_market_regime_position_control_authorization(
        {
            "consumption_policy": {
                "position_control_allowed": "true",
                "evidence_status": "automation_approved",
            }
        }
    )

    assert authorization["position_control_allowed"] is True
    assert authorization["position_control_authorized"] is True
    assert authorization["consumption_evidence_status"] == "automation_approved"


def test_market_regime_notification_only_does_not_authorize_position_control() -> None:
    missing_authorization = resolve_market_regime_position_control_authorization({})
    notification_only_authorization = resolve_market_regime_position_control_authorization(
        {
            "execution_controls": {
                "position_control_allowed": True,
                "consumption_evidence_status": "notification_only",
            }
        }
    )

    assert missing_authorization["position_control_allowed"] is False
    assert missing_authorization["position_control_authorized"] is False
    assert missing_authorization["consumption_evidence_status"] == ""
    assert notification_only_authorization["position_control_allowed"] is True
    assert notification_only_authorization["position_control_authorized"] is False
    assert notification_only_authorization["consumption_evidence_status"] == "notification_only"


def test_market_regime_execution_controls_take_precedence_over_legacy_policy() -> None:
    authorization = resolve_market_regime_position_control_authorization(
        {
            "execution_controls": {
                "position_control_allowed": False,
                "consumption_evidence_status": "notification_only",
            },
            "consumption_policy": {
                "position_control_allowed": True,
                "evidence_status": "automation_approved",
            },
        }
    )

    assert authorization["position_control_allowed"] is False
    assert authorization["position_control_authorized"] is False
    assert authorization["consumption_evidence_status"] == "notification_only"


def test_market_regime_partial_execution_controls_do_not_fall_back_to_legacy_policy() -> None:
    missing_evidence = resolve_market_regime_position_control_authorization(
        {
            "execution_controls": {
                "position_control_allowed": True,
            },
            "consumption_policy": {
                "position_control_allowed": True,
                "evidence_status": "automation_approved",
            },
        }
    )
    missing_allowed = resolve_market_regime_position_control_authorization(
        {
            "execution_controls": {
                "consumption_evidence_status": "automation_approved",
            },
            "consumption_policy": {
                "position_control_allowed": True,
                "evidence_status": "automation_approved",
            },
        }
    )

    assert missing_evidence["position_control_allowed"] is True
    assert missing_evidence["position_control_authorized"] is False
    assert missing_evidence["consumption_evidence_status"] == ""
    assert missing_allowed["position_control_allowed"] is False
    assert missing_allowed["position_control_authorized"] is False
    assert missing_allowed["consumption_evidence_status"] == "automation_approved"
