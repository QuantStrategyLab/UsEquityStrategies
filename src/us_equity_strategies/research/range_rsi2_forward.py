"""Research-only contribution accounting and prospective evidence checks."""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable
from .range_rsi2_study import FROZEN_PREREGISTRATION_SHA256


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _number(value, *, upper=1.0):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= upper:
        raise ValueError("invalid weight")
    return float(value)


@dataclass(frozen=True)
class ContributionResult:
    trend: Mapping[str, float]
    mean_reversion: Mapping[str, float]
    risk_final: Mapping[str, float]
    cash_weight: float
    risk_scale: float
    research_only: bool = field(default=True, init=False)
    promotion_eligible: bool = field(default=False, init=False)
    risk_gate_evaluated: bool = field(default=False, init=False)
    execution_authorized: bool = field(default=False, init=False)


def aggregate_research_contributions(trend: Mapping[str, float], *, leveraged_symbol: str,
                                     mr_active: bool, risk_cap: float) -> ContributionResult:
    """Apply a research-only nominal cap; this is not a QPK risk assessment."""
    if type(mr_active) is not bool or leveraged_symbol not in {"TQQQ", "SOXL"}:
        raise ValueError("invalid research candidate")
    cap = _number(risk_cap)
    base = {str(symbol): _number(value) for symbol, value in trend.items()}
    if sum(base.values()) > 1 + 1e-9:
        raise ValueError("trend weights exceed capital")
    cash_available = max(0.0, 1.0 - sum(base.values()))
    safe_funding = min(base.get("BOXX", 0.0), max(0.0, .10 - cash_available)) if mr_active else 0.0
    overlay = min(.10, cash_available + safe_funding) if mr_active else 0.0
    combined = dict(base)
    if safe_funding:
        combined["BOXX"] -= safe_funding
    combined[leveraged_symbol] = combined.get(leveraged_symbol, 0.0) + overlay
    risk = sum(value for symbol, value in combined.items() if symbol != "BOXX")
    scale = min(1.0, cap / risk) if risk > 0 else 1.0
    final = {symbol: value if symbol == "BOXX" else value * scale for symbol, value in combined.items()}
    cash = max(0.0, 1.0 - sum(final.values()))
    mr_contribution = {leveraged_symbol: overlay}
    if safe_funding:
        mr_contribution["BOXX"] = -safe_funding
    return ContributionResult(base, mr_contribution, final, cash, scale)


def evaluate_production_trend_fixture(ctx, *, profile: str, mr_active: bool, risk_cap: float) -> ContributionResult:
    """Combine the actual production builder output with the isolated research sleeve."""
    from us_equity_strategies.entrypoints import _build_tqqq_growth_income_decision, _build_soxl_soxx_trend_income_decision
    builders = {"tqqq_growth_income": (_build_tqqq_growth_income_decision, "TQQQ"),
                "soxl_soxx_trend_income": (_build_soxl_soxx_trend_income_decision, "SOXL")}
    if profile not in builders:
        raise ValueError("unsupported strategy profile")
    builder, leveraged = builders[profile]
    equity = float(ctx.portfolio.total_equity)
    if not math.isfinite(equity) or equity <= 0:
        raise ValueError("invalid equity")
    decision = builder(ctx)
    trend = {}
    for item in decision.positions:
        if item.target_weight is not None or item.target_value is None or item.symbol in trend:
            raise ValueError("invalid production trend target")
        trend[item.symbol] = float(item.target_value) / equity
    return aggregate_research_contributions(trend, leveraged_symbol=leveraged, mr_active=mr_active, risk_cap=risk_cap)


def _time(value):
    if not isinstance(value, str):
        raise ValueError("timestamp required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("aware timestamp required")
    return result.astimezone(timezone.utc)


def validate_forward_observation(registration: Mapping[str, Any], observation: Mapping[str, Any], *,
                                 now: datetime, verifier: Callable[[str, Mapping[str, Any]], bool] | None = None) -> dict[str, Any]:
    """Validate frozen identities/timing; no observation grants promotion authority.

    The trusted verifier authenticates registration, source bytes and their
    publication evidence, observation timestamps, and the supplied clock.
    Producer-created timestamps alone never satisfy this check.
    """
    reasons = []
    try:
        if not isinstance(registration, Mapping) or not isinstance(observation, Mapping):
            raise ValueError("missing registration or observation")
        if now.tzinfo is None:
            raise ValueError("clock must be aware")
        required = {"registration_id", "registered_at", "candidate_id", "candidate_sha256", "rules_sha256",
                    "source_sha256", "config_sha256", "forward_start", "forward_end", "max_age_seconds", "evidence",
                    "policy_sha256", "dependency_digests", "evidence_modes", "strategy_profile"}
        if set(registration) != required:
            raise ValueError("invalid registration fields")
        for key in ("candidate_sha256", "rules_sha256", "source_sha256", "config_sha256", "policy_sha256"):
            if type(registration[key]) is not str or len(registration[key]) != 64 or any(c not in "0123456789abcdef" for c in registration[key]):
                raise ValueError("invalid frozen digest")
        if registration["rules_sha256"] != FROZEN_PREREGISTRATION_SHA256:
            reasons.append("FROZEN_RULES_MISMATCH")
        if registration["candidate_id"] not in {"trend_mr_5", "trend_mr_10", "trend_mr_15"}:
            reasons.append("CANDIDATE_NOT_PREREGISTERED")
        if type(registration["registration_id"]) is not str or not registration["registration_id"]:
            raise ValueError("registration identity required")
        max_age = registration["max_age_seconds"]
        if type(max_age) not in (int, float) or not math.isfinite(max_age) or max_age < 0:
            raise ValueError("invalid freshness limit")
        registered = _time(registration["registered_at"])
        observed = _time(observation["observed_at"])
        available = _time(observation["available_at"])
        decision = _time(observation["decision_at"])
        if not registered < available <= decision <= observed <= now.astimezone(timezone.utc):
            reasons.append("TIMING_INVALID")
        if (decision - available).total_seconds() > max_age:
            reasons.append("INPUT_STALE")
        session = date.fromisoformat(observation["signal_session"])
        start = date.fromisoformat(registration["forward_start"])
        end = date.fromisoformat(registration["forward_end"])
        if start <= registered.date():
            reasons.append("FORWARD_WINDOW_NOT_PROSPECTIVE")
        if start > end or not start <= session <= end or session > decision.date():
            reasons.append("SESSION_OUTSIDE_FROZEN_WINDOW")
        if any(observation.get(key) != registration[key] for key in ("registration_id", "candidate_id", "candidate_sha256", "rules_sha256", "source_sha256", "config_sha256")):
            reasons.append("FROZEN_IDENTITY_MISMATCH")
        if (observation.get("policy_sha256") != registration["policy_sha256"]
                or observation.get("dependency_digests") != registration["dependency_digests"]
                or observation.get("evidence_modes") != registration["evidence_modes"]):
            reasons.append("FORWARD_DEPENDENCY_MISMATCH")
        if (observation.get("data_status") != "ready" or observation.get("risk_status") != "pass"
                or observation.get("shadow_status") != "healthy" or observation.get("paper_status") != "healthy"):
            reasons.append("EVIDENCE_UNAVAILABLE_OR_UNHEALTHY")
        input_sha = observation.get("input_sha256")
        if type(input_sha) is not str or len(input_sha) != 64 or any(c not in "0123456789abcdef" for c in input_sha):
            reasons.append("INPUT_IDENTITY_MISSING")
        if (verifier is None or verifier("registration", registration) is not True
                or verifier("observation", observation) is not True
                or verifier("clock", {"now": now.astimezone(timezone.utc).isoformat()}) is not True):
            reasons.append("INDEPENDENT_EVIDENCE_UNVERIFIED")
    except Exception:
        reasons.append("INVALID_OR_MISSING_EVIDENCE")
    return {"status": "BLOCKED" if reasons else "OBSERVED_RESEARCH_ONLY", "reason_codes": tuple(sorted(set(reasons))),
            "promotion_status": "NOT_QUALIFIED", "promotion_eligible": False, "execution_authorized": False}


def build_research_forward_receipt(registration, observation, *, policy, observation_index, previous_receipt,
                                   now, verifier=None, previous_binding=None):
    """Link independently verified prospective evidence to the frozen QPK policy chain."""
    checked = validate_forward_observation(registration, observation, now=now, verifier=verifier)
    if checked["status"] != "OBSERVED_RESEARCH_ONLY":
        return {**checked, "receipt": None}
    try:
        from quant_platform_kit.strategy_lifecycle.forward_observation_receipt import (
            build_forward_observation_receipt, forward_observation_policy_sha256,
        )
        previous_digest = previous_receipt.get("receipt_sha256") if isinstance(previous_receipt, Mapping) else None
        prior_binding_digest = None
        if previous_receipt is not None:
            if not isinstance(previous_binding, Mapping):
                raise ValueError("previous observation binding required")
            binding_fields = {"schema_version", "qpk_receipt_sha256", "registration_sha256", "observation_sha256",
                "input_sha256", "registered_at", "available_at", "decision_at", "observed_at",
                "previous_binding_sha256", "execution_authorized", "promotion_eligible", "binding_sha256"}
            if (set(previous_binding) != binding_fields
                    or previous_binding.get("schema_version") != "qsl.range_rsi2_forward_binding.v1"
                    or previous_binding.get("registration_sha256") != digest(registration)
                    or previous_binding.get("execution_authorized") is not False
                    or previous_binding.get("promotion_eligible") is not False):
                raise ValueError("previous observation binding identity mismatch")
            previous_material = {key: value for key, value in previous_binding.items() if key != "binding_sha256"}
            prior_binding_digest = previous_binding.get("binding_sha256")
            if (digest(previous_material) != prior_binding_digest
                    or previous_binding.get("qpk_receipt_sha256") != previous_digest
                    or verifier is None or verifier("previous_binding", previous_binding) is not True):
                raise ValueError("previous observation binding mismatch")
        elif previous_binding is not None:
            raise ValueError("unexpected previous binding")
        if (policy.candidate_id != registration["candidate_id"]
                or policy.strategy_profile != registration["strategy_profile"]
                or policy.domain != "us_equity" or policy.observation_start_session != registration["forward_start"]
                or forward_observation_policy_sha256(policy) != registration["policy_sha256"]
                or observation.get("observation_index") != observation_index
                or observation.get("previous_receipt_sha256") != previous_digest
                or observation.get("previous_binding_sha256") != prior_binding_digest):
            raise ValueError("forward chain binding mismatch")
        receipt = build_forward_observation_receipt(policy=policy, observation_session=observation["signal_session"],
            observation_index=observation_index, dependency_digests=registration["dependency_digests"],
            evidence_modes=registration["evidence_modes"], previous_receipt=previous_receipt)
        binding = {"schema_version": "qsl.range_rsi2_forward_binding.v1", "qpk_receipt_sha256": receipt["receipt_sha256"],
                   "registration_sha256": digest(registration), "observation_sha256": digest(observation),
                   "input_sha256": observation["input_sha256"], "registered_at": registration["registered_at"],
                   "available_at": observation["available_at"], "decision_at": observation["decision_at"],
                   "observed_at": observation["observed_at"], "previous_binding_sha256": prior_binding_digest,
                   "execution_authorized": False, "promotion_eligible": False}
        binding["binding_sha256"] = digest(binding)
        return {**checked, "receipt": receipt, "observation_binding": binding}
    except Exception:
        return {"status": "BLOCKED", "reason_codes": ("FORWARD_CHAIN_INVALID",), "receipt": None,
                "promotion_status": "NOT_QUALIFIED", "promotion_eligible": False, "execution_authorized": False}
