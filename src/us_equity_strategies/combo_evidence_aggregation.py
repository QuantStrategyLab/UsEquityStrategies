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


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parked(reason: str, *, combo_candidate_id: str = "") -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "status": "PARKED",
        "combo_candidate_id": combo_candidate_id,
        "component_refs": [],
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
    reports ``evidence_valid`` and an eligible research status.  A successful
    result remains research-only: no promotion or broker gate is bypassed.
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
        parked = _parked("COMPONENT_EVIDENCE_NOT_ELIGIBLE", combo_candidate_id=combo_candidate_id)
        parked["component_refs"] = refs
        parked.pop("evidence_digest")
        parked["evidence_digest"] = _digest(parked)
        return parked

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
