"""Research-only aggregation of component evidence into a combo record.

This is deliberately a thin boundary around the existing portfolio risk
budget assessor.  It joins *references* to component evidence; it does not
recompute or promote component evidence and it never authorizes execution.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence

from .portfolio_risk_budget import (
    PortfolioAssetRiskSpec,
    PortfolioRiskBudgetPolicy,
    assess_portfolio_risk_budget,
)

SCHEMA_VERSION = "qsl.combo-evidence-aggregation-research.v1"
_DIGEST = re.compile(r"[0-9a-f]{64}")
# Optional C2 comparability fields. When any component carries one of these,
# every component must present the same non-empty values or aggregation PARKS.
# Absent fields keep legacy single-artifact callers unchanged.
_COMPARABILITY_FIELDS = (
    "as_of",
    "quote_currency",
    "capital_basis_digest",
    "cost_model_digest",
    "risk_policy_digest",
    "data_scope_digest",
)


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parked(
    reason: str,
    *,
    combo_candidate_id: str = "",
    component_refs: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "status": "PARKED",
        "combo_candidate_id": combo_candidate_id,
        "component_refs": list(component_refs or ()),
        "risk_assessment": {
            "status": "PARKED",
            "execution_authorized": False,
            "reason_codes": (reason,),
        },
        "execution_authorized": False,
        "promotion_authorized": False,
        "reason_codes": (reason,),
    }
    payload["evidence_digest"] = _digest(payload)
    return payload


def _comparability_slice(
    component: Mapping[str, object],
) -> dict[str, str] | None | str:
    """Return normalized fields, ``None`` if undeclared, or a PARK reason."""
    present_fields = tuple(field for field in _COMPARABILITY_FIELDS if field in component)
    if not present_fields:
        return None
    normalized: dict[str, str] = {}
    for field in present_fields:
        value = component[field]
        if not isinstance(value, str) or not value.strip():
            return "COMPONENT_COMPARABILITY_INCOMPLETE"
        if field.endswith("_digest") and _DIGEST.fullmatch(value) is None:
            return "COMPONENT_COMPARABILITY_INCOMPLETE"
        normalized[field] = value
    return normalized


def _check_component_comparability(
    components: Sequence[Mapping[str, object]],
) -> str | None:
    """Return a PARK reason when declared comparability fields conflict."""
    slices: list[dict[str, str] | None] = []
    for component in components:
        parsed = _comparability_slice(component)
        if isinstance(parsed, str):
            return parsed
        slices.append(parsed)
    declared = [item for item in slices if item is not None]
    if not declared:
        return None
    if any(item is None for item in slices):
        return "COMPONENT_COMPARABILITY_INCOMPLETE"
    field_sets = {frozenset(item) for item in declared}
    if len(field_sets) != 1:
        return "COMPONENT_COMPARABILITY_INCOMPLETE"
    baseline = declared[0]
    if any(item != baseline for item in declared[1:]):
        return "COMPONENT_COMPARABILITY_MISMATCH"
    return None


def aggregate_combo_evidence(
    *,
    combo_candidate_id: str,
    combo_revision: str,
    components: Sequence[Mapping[str, object]],
    target_weights: Mapping[str, float],
    asset_risk_specs: Mapping[str, PortfolioAssetRiskSpec],
    policy: PortfolioRiskBudgetPolicy,
    current_weights: Mapping[str, float] | None = None,
) -> dict[str, object]:
    """Join verified component references and assess a combo target.

    Component records must carry immutable ``candidate_id``, ``evidence_digest``
    and ``input_digest`` values.  A component is usable only when it explicitly
    reports ``evidence_valid`` and an eligible research status.  When any
    component declares C2 comparability fields (``as_of``, ``quote_currency``,
    capital/cost/risk/data digests), every component must carry the same values
    or the result is ``PARKED``.  A successful result remains research-only: no
    promotion or broker gate is bypassed.
    """
    if not isinstance(combo_candidate_id, str) or not combo_candidate_id.strip():
        return _parked("COMBO_CANDIDATE_ID_INVALID")
    if not isinstance(combo_revision, str) or not combo_revision.strip():
        return _parked("COMBO_REVISION_INVALID", combo_candidate_id=combo_candidate_id)
    if not isinstance(components, Sequence) or isinstance(components, (str, bytes)) or not components:
        return _parked("COMPONENTS_REQUIRED", combo_candidate_id=combo_candidate_id)

    refs: list[dict[str, object]] = []
    seen: set[str] = set()
    for component in components:
        if not isinstance(component, Mapping):
            return _parked("COMPONENT_REFERENCE_INVALID", combo_candidate_id=combo_candidate_id)
        candidate_id = component.get("candidate_id")
        evidence_digest = component.get("evidence_digest")
        input_digest = component.get("input_digest")
        if (
            not isinstance(candidate_id, str)
            or not candidate_id.strip()
            or candidate_id in seen
            or not isinstance(evidence_digest, str)
            or _DIGEST.fullmatch(evidence_digest) is None
            or not isinstance(input_digest, str)
            or _DIGEST.fullmatch(input_digest) is None
        ):
            return _parked("COMPONENT_IDENTITY_INVALID", combo_candidate_id=combo_candidate_id)
        seen.add(candidate_id)
        eligible = component.get("evidence_valid") is True and component.get(
            "research_eligibility_status"
        ) in {"ELIGIBLE", "PASS", "READY_REPORT_ONLY"}
        refs.append(
            {
                "candidate_id": candidate_id,
                "evidence_digest": evidence_digest,
                "input_digest": input_digest,
                "eligible": eligible,
            }
        )

    if not all(ref["eligible"] is True for ref in refs):
        return _parked(
            "COMPONENT_EVIDENCE_NOT_ELIGIBLE",
            combo_candidate_id=combo_candidate_id,
            component_refs=refs,
        )

    comparability_reason = _check_component_comparability(components)
    if comparability_reason is not None:
        return _parked(
            comparability_reason,
            combo_candidate_id=combo_candidate_id,
            component_refs=refs,
        )

    risk = assess_portfolio_risk_budget(
        target_weights=target_weights,
        asset_risk_specs=asset_risk_specs,
        policy=policy,
        current_weights=current_weights,
    )
    status = "READY_RESEARCH_ONLY" if risk["status"] in {"APPROVE", "REDUCE"} else "PARKED"
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "combo_candidate_id": combo_candidate_id,
        "combo_revision": combo_revision,
        "component_refs": refs,
        "risk_assessment": risk,
        "execution_authorized": False,
        "promotion_authorized": False,
        "reason_codes": () if status != "PARKED" else risk["reason_codes"],
    }
    payload["evidence_digest"] = _digest(payload)
    return payload


__all__ = ["SCHEMA_VERSION", "aggregate_combo_evidence"]
