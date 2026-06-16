"""Shared market-regime-control artifact contract helpers."""

from __future__ import annotations

from collections.abc import Mapping

MARKET_REGIME_AUTOMATION_APPROVED = "automation_approved"


def _as_bool(value: object, *, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", ""}:
        return False
    return bool(default)


def resolve_market_regime_position_control_authorization(payload: Mapping[str, object]) -> dict[str, object]:
    raw_execution_controls = payload.get("execution_controls")
    execution_controls_present = isinstance(raw_execution_controls, Mapping)
    if execution_controls_present:
        execution_controls = raw_execution_controls
    else:
        execution_controls = {}
    consumption_policy = payload.get("consumption_policy")
    if not isinstance(consumption_policy, Mapping):
        consumption_policy = {}
    if execution_controls_present:
        position_control_allowed = _as_bool(execution_controls.get("position_control_allowed"), default=False)
        evidence_status = str(execution_controls.get("consumption_evidence_status") or "").strip().lower()
    else:
        position_control_allowed = _as_bool(consumption_policy.get("position_control_allowed"), default=False)
        evidence_status = str(consumption_policy.get("evidence_status") or "").strip().lower()
    return {
        "position_control_allowed": position_control_allowed,
        "position_control_authorized": bool(
            position_control_allowed and evidence_status == MARKET_REGIME_AUTOMATION_APPROVED
        ),
        "consumption_evidence_status": evidence_status,
    }


__all__ = [
    "MARKET_REGIME_AUTOMATION_APPROVED",
    "resolve_market_regime_position_control_authorization",
]
