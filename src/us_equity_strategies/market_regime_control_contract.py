"""Shared market-regime-control artifact contract helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

MARKET_REGIME_AUTOMATION_APPROVED = "automation_approved"


def _legacy_position_control_allowed(value: object) -> bool:
    return value is True or (type(value) is str and value == "true")


def _metadata_authority_permitted(value: Mapping) -> bool:
    plugin = str(value.get("plugin") or value.get("profile") or "").strip().lower()
    if plugin in {"macro_risk_governor", "crisis_response_shadow", "taco_rebound_shadow"}:
        modes = (value.get("mode"), value.get("configured_mode"), value.get("effective_mode"))
        if any(str(mode or "").strip().lower() in {"shadow", "disabled"} for mode in modes):
            return False
    if ("strategy_runtime_metadata_non_authoritative" in value
            and value["strategy_runtime_metadata_non_authoritative"] is not False):
        return False
    if "position_control_allowed" in value and value["position_control_allowed"] is not True:
        return False
    for key in ("execution_controls", "consumption_policy"):
        controls = value.get(key)
        if key in value and not isinstance(controls, Mapping):
            return False
        if isinstance(controls, Mapping) and "position_control_allowed" in controls:
            allowed = controls["position_control_allowed"]
            if not (_legacy_position_control_allowed(allowed) if key == "consumption_policy" else allowed is True):
                return False
    runtime = value.get("runtime_consumption")
    if "runtime_consumption" in value and not isinstance(runtime, Mapping):
        return False
    if isinstance(runtime, Mapping) and runtime.get("runtime_metadata_non_authoritative") is not False:
        return False
    return True


def iter_payloads_with_authority(value, *, _depth: int = 0, _permitted: bool = True):
    """Yield metadata and inherited authority without laundering a denied ancestor."""
    if _depth > 4:
        return
    if isinstance(value, Mapping):
        permitted = _permitted and _metadata_authority_permitted(value)
        yield value, permitted
        for item in value.values():
            yield from iter_payloads_with_authority(item, _depth=_depth + 1, _permitted=permitted)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            yield from iter_payloads_with_authority(item, _depth=_depth + 1, _permitted=_permitted)


def iter_authoritative_legacy_payloads(value):
    """Traverse legacy metadata without crossing an explicit authority denial."""
    for payload, permitted in iter_payloads_with_authority(value):
        if permitted:
            yield payload


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
        position_control_allowed = execution_controls.get("position_control_allowed") is True
        evidence_status = str(execution_controls.get("consumption_evidence_status") or "").strip().lower()
    else:
        position_control_allowed = _legacy_position_control_allowed(consumption_policy.get("position_control_allowed"))
        evidence_status = str(consumption_policy.get("evidence_status") or "").strip().lower()
    if not _metadata_authority_permitted(payload):
        position_control_allowed = False
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
