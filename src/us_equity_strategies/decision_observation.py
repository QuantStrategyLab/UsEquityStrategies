"""Optional, read-only lineage for actual strategy and risk-gate decisions."""
from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone

from quant_platform_kit.risk.gate import apply_risk_gate as _qpk_apply_risk_gate
from us_equity_strategies.market_regime_control_contract import iter_payloads_with_authority


def consumer_payload_sha256(value: object) -> str:
    """Hash the exact JSON-safe mapping handed to the strategy consumer."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()


def _actual_consumed_payload(ctx, pre, reference, profile):
    diagnostics = pre.diagnostics
    if reference == "derived_indicators" and profile == "soxl_soxx_trend_income":
        value = ctx.market_data.get("derived_indicators")
        return (value, "unknown") if isinstance(value, Mapping) else (None, "unknown")
    if reference != "market_regime_control" or diagnostics.get("market_regime_control_enabled") is not True:
        return None, "unknown"
    if diagnostics.get("market_regime_control_found") is not True:
        return None, "unknown"
    source = diagnostics.get("market_regime_control_source")
    if source is not None and source != "market_regime_control":
        return None, "unknown"
    metadata = getattr(ctx.portfolio, "metadata", {}) or {}
    for payload, _ in iter_payloads_with_authority(metadata):
        if str(payload.get("plugin") or payload.get("profile") or "").strip().lower() == "market_regime_control":
            authorized = diagnostics.get("market_regime_control_position_control_authorized")
            authority = "authorized" if authorized is True else "denied" if authorized is False else "unknown"
            return payload, authority
    return None, "unknown"


def attach_optional_decision_receipt(ctx, pre, post, *, profile):
    """Attach a receipt only when explicit runtime evidence and clock are available.

    Receipt failure is observable but never changes the decision's targets or
    grants execution authority. The default path does not import the new QPK API.
    """
    options = ctx.capabilities.get("decision_consumption_receipt")
    if options is None:
        return post
    status = "unavailable"
    receipt = None
    try:
        if not isinstance(options, Mapping):
            raise ValueError("invalid receipt capability")
        decision_at = options["decision_at"]
        gate_clock = ctx.capabilities.get("logical_evaluation_time")
        if not isinstance(decision_at, datetime) or not isinstance(gate_clock, datetime):
            raise ValueError("explicit decision and gate clocks required")
        if decision_at.tzinfo is None or gate_clock.tzinfo is None:
            raise ValueError("timezone-aware clocks required")
        if decision_at.astimezone(timezone.utc) != gate_clock.astimezone(timezone.utc):
            raise ValueError("decision and gate clocks differ")
        if "logical_evaluation_time" not in inspect.signature(_qpk_apply_risk_gate).parameters:
            raise ValueError("gate clock unavailable")
        observed_gate_time = post.diagnostics.get("risk_gate_evaluated_at")
        if not isinstance(observed_gate_time, str):
            raise ValueError("gate did not confirm its evaluation clock")
        gate_recorded = datetime.fromisoformat(observed_gate_time.replace("Z", "+00:00"))
        if gate_recorded.tzinfo is None or gate_recorded.astimezone(timezone.utc) != decision_at.astimezone(timezone.utc):
            raise ValueError("gate evaluation clock mismatch")
        supplied = options["observations"]
        if not isinstance(supplied, (tuple, list)):
            raise ValueError("observations required")
        observations = []
        for item in supplied:
            if not isinstance(item, Mapping):
                raise ValueError("invalid observation")
            reference = item["reference"]
            state = item["status"]
            actual, authority = _actual_consumed_payload(ctx, pre, item.get("consumer_key"), profile)
            matched = (state == "observed" and actual is not None
                       and item.get("consumer_payload_sha256") == consumer_payload_sha256(actual)
                       and isinstance(item.get("artifact_sha256"), str)
                       and re.fullmatch(r"[0-9a-f]{64}", item["artifact_sha256"]) is not None
                       and item.get("digest_status") in {"verified", "unverified"})
            if matched:
                observations.append({"reference": reference, "status": "observed",
                    "artifact_sha256": item.get("artifact_sha256"), "digest_status": item.get("digest_status"),
                    "availability_at": item.get("availability_at"), "observed_at": item.get("observed_at"),
                    "consumer_payload_sha256": item.get("consumer_payload_sha256"),
                    "consumer": item.get("consumer_key"), "authority": authority})
            else:
                observations.append({"reference": reference,
                    "status": state if state in {"missing", "unavailable"} and actual is None else "unavailable",
                    "artifact_sha256": None, "digest_status": "unknown", "availability_at": None,
                    "observed_at": None, "consumer_payload_sha256": None,
                    "consumer": item.get("consumer_key"),
                    "authority": "unknown"})
        from quant_platform_kit.common.decision_consumption_receipts import build_decision_consumption_receipt
        receipt = build_decision_consumption_receipt(run_id=options["run_id"], strategy_profile=profile,
            strategy_revision=options.get("strategy_revision"), config_sha256=options.get("config_sha256"),
            decision_at=decision_at, signal_session=str(ctx.as_of)[:10], pre_risk_decision=pre,
            post_risk_decision=post, observations=observations)
        status = "recorded"
    except Exception:
        pass
    diagnostics = {key: value for key, value in post.diagnostics.items() if key != "decision_consumption_receipt"}
    diagnostics["decision_consumption_receipt_status"] = status
    if receipt is not None:
        diagnostics["decision_consumption_receipt"] = receipt
    return replace(post, diagnostics=diagnostics)
