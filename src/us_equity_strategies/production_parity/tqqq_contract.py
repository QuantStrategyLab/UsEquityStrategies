from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import Any

from quant_platform_kit.risk.contracts import (
    CandidateRiskIdentity,
    RiskGateAssessment,
)
from quant_platform_kit.risk.gate import (
    assess_with_evidence as _qpk_assess_with_evidence,
)
from quant_platform_kit.strategy_contracts import PositionTarget, StrategyDecision


_CONTRACT_VERSION = "qsl.tqqq_production_parity.v1"
_ALLOWED_ASSETS = ("TQQQ", "BOXX")
_EXCLUDED_ASSET_WEIGHTS = {
    "QQQM": 0.0,
    "SCHD": 0.0,
    "DGRO": 0.0,
    "SGOV": 0.0,
    "SPYI": 0.0,
    "QQQI": 0.0,
}
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class TqqqProductionParityEvidence:
    """Immutable identities and continuity flags for the ETF-only candidate."""

    contract_version: str
    config_sha256: str
    input_manifest_sha256: str
    candidate_identity_sha256: str
    prior_state_sha256: str
    signal_state_sha256: str
    risk_active_state_sha256: str
    volatility_hysteresis_state_sha256: str
    retention_state_sha256: str
    market_regime_control_sha256: str
    signal_session: date
    execution_session: date
    signal_effective_after_trading_days: int
    warmup_sessions: int
    state_continuity: str
    cash_reset: bool
    income_layer_enabled: bool
    option_overlay_enabled: bool
    option_growth_overlay_enabled: bool
    option_income_overlay_enabled: bool
    option_order_intents: tuple[object, ...]


@dataclass(frozen=True)
class TqqqResearchContractResult:
    outcome: str
    reason_codes: tuple[str, ...]
    research_decision: StrategyDecision
    executable_decision: StrategyDecision
    assessment: RiskGateAssessment
    authority_scope: str = "RESEARCH_ONLY"
    no_order: bool = True
    size_zero_required: bool = True
    promotion_eligible: bool = False
    live_ready: bool = False
    allowed_nonzero_assets: tuple[str, ...] = _ALLOWED_ASSETS
    excluded_asset_weights: Mapping[str, float] = field(
        default_factory=lambda: MappingProxyType(_EXCLUDED_ASSET_WEIGHTS)
    )
    option_order_intents: tuple[object, ...] = ()


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_RE.fullmatch(value) is not None


def _evidence_errors(
    evidence: TqqqProductionParityEvidence | None,
    candidate_identity: CandidateRiskIdentity | None,
) -> set[str]:
    if type(evidence) is not TqqqProductionParityEvidence:
        return {"missing_production_parity_evidence"}

    errors: set[str] = set()
    if evidence.contract_version != _CONTRACT_VERSION:
        errors.add("invalid_contract_version")
    for value in (
        evidence.config_sha256,
        evidence.input_manifest_sha256,
        evidence.candidate_identity_sha256,
        evidence.prior_state_sha256,
        evidence.signal_state_sha256,
        evidence.risk_active_state_sha256,
        evidence.volatility_hysteresis_state_sha256,
        evidence.retention_state_sha256,
    ):
        if not _is_sha256(value):
            errors.add("invalid_state_identity")
    if not _is_sha256(evidence.market_regime_control_sha256):
        errors.add("market_regime_control_required")

    if type(candidate_identity) is not CandidateRiskIdentity:
        errors.add("candidate_identity_required")
    else:
        if evidence.config_sha256 != candidate_identity.config_sha256:
            errors.add("config_identity_mismatch")
        if evidence.input_manifest_sha256 != candidate_identity.input_manifest_sha256:
            errors.add("input_manifest_identity_mismatch")
        if evidence.candidate_identity_sha256 != candidate_identity.candidate_sha256:
            errors.add("candidate_identity_mismatch")

    if (
        type(evidence.signal_session) is not date
        or type(evidence.execution_session) is not date
        or evidence.execution_session <= evidence.signal_session
        or type(evidence.signal_effective_after_trading_days) is not int
        or evidence.signal_effective_after_trading_days != 1
    ):
        errors.add("invalid_signal_timing")
    if type(evidence.warmup_sessions) is not int or evidence.warmup_sessions < 252:
        errors.add("insufficient_warmup")
    if evidence.state_continuity != "continuous":
        errors.add("continuous_state_required")
    if evidence.cash_reset is not False:
        errors.add("cash_reset_forbidden")
    if evidence.income_layer_enabled is not False:
        errors.add("income_layer_not_allowed")
    if (
        evidence.option_overlay_enabled is not False
        or evidence.option_growth_overlay_enabled is not False
        or evidence.option_income_overlay_enabled is not False
        or type(evidence.option_order_intents) is not tuple
        or evidence.option_order_intents
    ):
        errors.add("option_overlay_not_allowed")
    return errors


def _decision_errors(decision: StrategyDecision) -> set[str]:
    if type(decision) is not StrategyDecision:
        return {"invalid_strategy_decision"}
    errors: set[str] = set()
    if type(decision.positions) is not tuple:
        errors.add("invalid_strategy_decision")
        positions: tuple[object, ...] = ()
    else:
        positions = decision.positions
    if type(decision.budgets) is not tuple or decision.budgets:
        errors.add("budget_intent_not_allowed")

    nonzero_assets: list[str] = []
    for position in positions:
        if type(position) is not PositionTarget:
            errors.add("invalid_strategy_decision")
            continue
        if position.symbol not in _ALLOWED_ASSETS:
            errors.add("excluded_asset")
        weight = position.target_weight
        if (
            type(weight) not in {int, float}
            or not math.isfinite(float(weight))
            or float(weight) < 0.0
            or position.target_value is not None
        ):
            errors.add("invalid_position_target")
            continue
        if float(weight) > 0.0:
            nonzero_assets.append(position.symbol)
    if len(nonzero_assets) > 1 or len(set(nonzero_assets)) > 1:
        errors.add("nonzero_asset_count")
    return errors


def _rejected_decision(reason_codes: tuple[str, ...]) -> StrategyDecision:
    return StrategyDecision(
        positions=(),
        budgets=(),
        risk_flags=("rejected:tqqq_production_parity_contract",),
        diagnostics={
            "tqqq_contract_outcome": "REJECT",
            "reason_codes": reason_codes,
        },
    )


def evaluate_tqqq_research_contract(
    decision: StrategyDecision,
    portfolio_snapshot: Any,
    *,
    mandate_provenance: Mapping[str, Any] | None,
    candidate_identity: CandidateRiskIdentity | None,
    risk_control_state: Mapping[str, Any] | None,
    production_parity_evidence: TqqqProductionParityEvidence | None,
    market_data: Mapping[str, Any] | None = None,
) -> TqqqResearchContractResult:
    """Evaluate the research candidate once and never create execution authority."""

    errors = _evidence_errors(production_parity_evidence, candidate_identity)
    errors.update(_decision_errors(decision))
    safe_decision = (
        decision if type(decision) is StrategyDecision else StrategyDecision()
    )
    assessment_result = _qpk_assess_with_evidence(
        safe_decision,
        portfolio_snapshot,
        scope="MEMBER",
        mandate_provenance=mandate_provenance,
        market_data=market_data if isinstance(market_data, Mapping) else {},
        candidate_identity=candidate_identity,
        risk_control_state=risk_control_state if not errors else None,
    )

    accepted = not errors and assessment_result.assessment.outcome == "APPROVE"
    reason_codes = (
        tuple(sorted(errors))
        if errors
        else tuple(assessment_result.assessment.reason_codes)
    )
    research_decision = (
        assessment_result.decision if accepted else _rejected_decision(reason_codes)
    )
    executable_decision = StrategyDecision(
        positions=(),
        budgets=(),
        risk_flags=("execution_disabled:research_only",),
        diagnostics={
            "authority_scope": "RESEARCH_ONLY",
            "execution_authorized": False,
        },
    )
    return TqqqResearchContractResult(
        outcome="APPROVE" if accepted else "REJECT",
        reason_codes=reason_codes,
        research_decision=research_decision,
        executable_decision=executable_decision,
        assessment=assessment_result.assessment,
    )
