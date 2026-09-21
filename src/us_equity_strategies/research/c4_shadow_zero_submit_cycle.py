"""C4 research: shadow zero-submit cycle over already-materialized inputs.

This consumer only joins an account snapshot, an open-orders snapshot, and a
final RiskEngine assessment that were materialized elsewhere.  It does not
touch broker APIs, credentials, workflows, or the network.  Research target
weights may be diagnosed via the existing portfolio risk budget helper, but
they never become proposed orders or execution intents.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence

from us_equity_strategies.portfolio_risk_budget import (
    PortfolioAssetRiskSpec,
    PortfolioRiskBudgetPolicy,
    assess_portfolio_risk_budget,
)

SCHEMA_VERSION = "qsl.c4-shadow-zero-submit-cycle-research.v1"
EVIDENCE_SCOPE = "SHADOW_ZERO_SUBMIT_CYCLE_ONLY"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_KNOWN_ORDER_OUTCOMES = frozenset(
    {
        "NONE",
        "OPEN",
        "ACKNOWLEDGED",
        "PARTIALLY_FILLED",
        "FILLED",
        "CANCELED",
        "REJECTED",
        "EXPIRED",
    }
)
_BOUNDARY_MARKERS = {
    "integer_share_sizing": "DIAGNOSTIC_ONLY_NOT_COMPUTED",
    "cash_reserve_enforcement": "DIAGNOSTIC_ONLY_NOT_COMPUTED",
    "leverage_expansion": "NOT_AUTHORIZED_NOT_COMPUTED",
    "live_liquidity_gates": "NOT_COMPUTED",
    "fill_ledger_reconstruction": "NOT_COMPUTED_REQUIRES_PLATFORM_FACTS",
    "broker_access": "DISABLED_MATERIALIZED_SNAPSHOTS_ONLY",
    "optimization": "DISABLED_NO_ALLOCATOR",
}


def _digest_payload(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parked(
    reason: str,
    *,
    account_ref: Mapping[str, object] | None = None,
    orders_ref: Mapping[str, object] | None = None,
    risk_ref: Mapping[str, object] | None = None,
    member_refs: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "research_only": True,
        "shadow_only": True,
        "execution_authorized": False,
        "promotion_authorized": False,
        "no_order": True,
        "proposed_orders": [],
        "submission_attempted": False,
        "execution_permitted": False,
        "evidence_scope": EVIDENCE_SCOPE,
        "status": "PARKED",
        "reason_codes": (reason,),
        "account_ref": dict(account_ref or {}),
        "orders_ref": dict(orders_ref or {}),
        "risk_ref": dict(risk_ref or {}),
        "member_refs": list(member_refs or ()),
        "risk_contribution": {},
        "boundaries": dict(_BOUNDARY_MARKERS),
    }
    payload["evidence_digest"] = _digest_payload(payload)
    return payload


def _identity(value: object, label: str) -> str:
    if not isinstance(value, str) or not _IDENTITY.fullmatch(value):
        raise ValueError(f"{label}_INVALID")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ValueError(f"{label}_INVALID")
    return value


def _as_of(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}_INVALID")
    return value


def _freshness(value: object, label: str) -> str:
    if value != "FRESH":
        raise ValueError(f"{label}_STALE_OR_MISSING")
    return "FRESH"


def _snapshot_ref(
    snapshot: object,
    *,
    kind: str,
    digest_key: str,
) -> dict[str, object]:
    if not isinstance(snapshot, Mapping):
        raise ValueError(f"{kind}_SNAPSHOT_REQUIRED")
    digest = _digest(snapshot.get(digest_key), f"{kind}_DIGEST")
    return {
        "account_id": _identity(snapshot.get("account_id"), f"{kind}_ACCOUNT_ID"),
        "strategy_id": _identity(snapshot.get("strategy_id"), f"{kind}_STRATEGY_ID"),
        "as_of": _as_of(snapshot.get("as_of"), f"{kind}_AS_OF"),
        "snapshot_version": _identity(
            snapshot.get("snapshot_version"), f"{kind}_SNAPSHOT_VERSION"
        ),
        "freshness": _freshness(snapshot.get("freshness"), f"{kind}_FRESHNESS"),
        digest_key: digest,
    }


def _parse_orders(snapshot: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    orders = snapshot.get("orders")
    if not isinstance(orders, Sequence) or isinstance(orders, (str, bytes)):
        raise ValueError("OPEN_ORDERS_LIST_REQUIRED")
    parsed: list[dict[str, object]] = []
    for order in orders:
        if not isinstance(order, Mapping):
            raise ValueError("OPEN_ORDER_INVALID")
        order_id = _identity(order.get("order_id"), "OPEN_ORDER_ID")
        outcome = order.get("outcome")
        if not isinstance(outcome, str) or outcome not in _KNOWN_ORDER_OUTCOMES:
            raise ValueError("OPEN_ORDER_OUTCOME_UNKNOWN")
        parsed.append({"order_id": order_id, "outcome": outcome})
    return tuple(parsed)


def _parse_risk_engine(result: object) -> dict[str, object]:
    if not isinstance(result, Mapping):
        raise ValueError("RISK_ENGINE_RESULT_REQUIRED")
    status = result.get("status")
    if status != "APPROVE":
        raise ValueError("RISK_ENGINE_NOT_APPROVE")
    if result.get("execution_authorized") is not False:
        raise ValueError("RISK_ENGINE_EXECUTION_AUTHORIZED_SEMANTICS_UNSATISFIED")
    account_digest = _digest(result.get("account_digest"), "RISK_ENGINE_ACCOUNT_DIGEST")
    policy_digest = _digest(result.get("risk_policy_digest"), "RISK_ENGINE_POLICY_DIGEST")
    assessment_digest = _digest(
        result.get("assessment_digest"), "RISK_ENGINE_ASSESSMENT_DIGEST"
    )
    return {
        "account_id": _identity(result.get("account_id"), "RISK_ENGINE_ACCOUNT_ID"),
        "strategy_id": _identity(result.get("strategy_id"), "RISK_ENGINE_STRATEGY_ID"),
        "as_of": _as_of(result.get("as_of"), "RISK_ENGINE_AS_OF"),
        "snapshot_version": _identity(
            result.get("snapshot_version"), "RISK_ENGINE_SNAPSHOT_VERSION"
        ),
        "freshness": _freshness(result.get("freshness"), "RISK_ENGINE_FRESHNESS"),
        "status": "APPROVE",
        "execution_authorized": False,
        "account_digest": account_digest,
        "risk_policy_digest": policy_digest,
        "assessment_digest": assessment_digest,
    }


def _parse_member_refs(
    value: Sequence[Mapping[str, object]] | None,
) -> list[dict[str, object]]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("MEMBER_REFS_INVALID")
    refs: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("MEMBER_REF_INVALID")
        member_id = _identity(item.get("member_id"), "MEMBER_ID")
        if member_id in seen:
            raise ValueError("MEMBER_REF_DUPLICATE")
        seen.add(member_id)
        refs.append(
            {
                "member_id": member_id,
                "evidence_digest": _digest(item.get("evidence_digest"), "MEMBER_EVIDENCE"),
                "input_digest": _digest(item.get("input_digest"), "MEMBER_INPUT"),
            }
        )
    return refs


def _risk_contribution(
    *,
    research_target_weights: Mapping[str, float] | None,
    asset_risk_specs: Mapping[str, PortfolioAssetRiskSpec] | None,
    risk_policy: PortfolioRiskBudgetPolicy | None,
) -> dict[str, object]:
    provided = (
        research_target_weights is not None,
        asset_risk_specs is not None,
        risk_policy is not None,
    )
    if not any(provided):
        return {
            "status": "NOT_PROVIDED",
            "execution_authorized": False,
            "metrics": {},
            "recommended_target_weights": {},
            "reason_codes": (),
        }
    if not all(provided):
        raise ValueError("RISK_CONTRIBUTION_INPUTS_INCOMPLETE")
    assert research_target_weights is not None
    assert asset_risk_specs is not None
    assert risk_policy is not None
    assessment = assess_portfolio_risk_budget(
        target_weights=research_target_weights,
        asset_risk_specs=asset_risk_specs,
        policy=risk_policy,
    )
    if assessment["status"] == "PARKED":
        raise ValueError("RISK_CONTRIBUTION_PARKED")
    return {
        "status": assessment["status"],
        "execution_authorized": False,
        "risk_scalar": assessment["risk_scalar"],
        "reason_codes": tuple(assessment["reason_codes"]),
        "metrics": dict(assessment["metrics"]),
        # Keep research recommendation as evidence only; never promote to orders.
        "recommended_target_weights": dict(assessment["recommended_target_weights"]),
    }


def consume_c4_shadow_zero_submit_cycle(
    *,
    account_snapshot: Mapping[str, object],
    open_orders_snapshot: Mapping[str, object],
    risk_engine_result: Mapping[str, object],
    member_refs: Sequence[Mapping[str, object]] | None = None,
    research_target_weights: Mapping[str, float] | None = None,
    asset_risk_specs: Mapping[str, PortfolioAssetRiskSpec] | None = None,
    risk_policy: PortfolioRiskBudgetPolicy | None = None,
) -> dict[str, object]:
    """Join materialized account/orders/RiskEngine facts into shadow evidence.

    Successful outputs stay research/shadow-only and encode a verifiable zero
    submit: empty ``proposed_orders``, ``submission_attempted=false``, and
    ``execution_permitted=false``.  Missing, stale, identity/as-of/digest
    mismatches, unknown order outcomes, non-APPROVE RiskEngine results, or
    RiskEngine ``execution_authorized=true`` all fail closed as ``PARKED``.
    """
    account_ref: dict[str, object] = {}
    orders_ref: dict[str, object] = {}
    risk_ref: dict[str, object] = {}
    parsed_members: list[dict[str, object]] = []
    try:
        account_ref = _snapshot_ref(
            account_snapshot, kind="ACCOUNT", digest_key="account_digest"
        )
        orders_ref = _snapshot_ref(
            open_orders_snapshot, kind="ORDERS", digest_key="orders_digest"
        )
        risk_ref = _parse_risk_engine(risk_engine_result)
        order_rows = _parse_orders(open_orders_snapshot)
        orders_ref["order_count"] = len(order_rows)
        parsed_members = _parse_member_refs(member_refs)

        if account_ref["account_id"] != orders_ref["account_id"]:
            raise ValueError("ACCOUNT_ORDERS_ACCOUNT_ID_MISMATCH")
        if account_ref["account_id"] != risk_ref["account_id"]:
            raise ValueError("ACCOUNT_RISK_ACCOUNT_ID_MISMATCH")
        if account_ref["strategy_id"] != orders_ref["strategy_id"]:
            raise ValueError("ACCOUNT_ORDERS_STRATEGY_ID_MISMATCH")
        if account_ref["strategy_id"] != risk_ref["strategy_id"]:
            raise ValueError("ACCOUNT_RISK_STRATEGY_ID_MISMATCH")
        if account_ref["as_of"] != orders_ref["as_of"]:
            raise ValueError("ACCOUNT_ORDERS_AS_OF_MISMATCH")
        if account_ref["as_of"] != risk_ref["as_of"]:
            raise ValueError("ACCOUNT_RISK_AS_OF_MISMATCH")
        if account_ref["account_digest"] != risk_ref["account_digest"]:
            raise ValueError("ACCOUNT_DIGEST_MISMATCH")
        bound = _digest(
            open_orders_snapshot.get("account_digest"), "ORDERS_ACCOUNT_DIGEST"
        )
        if bound != account_ref["account_digest"]:
            raise ValueError("ORDERS_ACCOUNT_DIGEST_MISMATCH")

        contribution = _risk_contribution(
            research_target_weights=research_target_weights,
            asset_risk_specs=asset_risk_specs,
            risk_policy=risk_policy,
        )
        input_digest = _digest_payload(
            {
                "account_ref": account_ref,
                "orders_ref": orders_ref,
                "risk_ref": risk_ref,
                "member_refs": parsed_members,
            }
        )
        payload: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "research_only": True,
            "shadow_only": True,
            "execution_authorized": False,
            "promotion_authorized": False,
            "no_order": True,
            "proposed_orders": [],
            "submission_attempted": False,
            "execution_permitted": False,
            "evidence_scope": EVIDENCE_SCOPE,
            "status": "READY_SHADOW_ZERO_SUBMIT",
            "reason_codes": (),
            "account_ref": account_ref,
            "orders_ref": orders_ref,
            "risk_ref": risk_ref,
            "member_refs": parsed_members,
            "policy_digest": risk_ref["risk_policy_digest"],
            "input_digest": input_digest,
            "risk_contribution": contribution,
            "boundaries": {
                **_BOUNDARY_MARKERS,
                "risk_contribution": (
                    "PORTFOLIO_RISK_BUDGET_SNAPSHOT_ONLY"
                    if contribution["status"] != "NOT_PROVIDED"
                    else "NOT_PROVIDED"
                ),
            },
        }
        payload["evidence_digest"] = _digest_payload(payload)
        return payload
    except ValueError as exc:
        return _parked(
            str(exc),
            account_ref=account_ref,
            orders_ref=orders_ref,
            risk_ref=risk_ref,
            member_refs=parsed_members,
        )
    except (TypeError, ArithmeticError, OverflowError):
        return _parked(
            "C4_SHADOW_CYCLE_FAILED_CLOSED",
            account_ref=account_ref,
            orders_ref=orders_ref,
            risk_ref=risk_ref,
            member_refs=parsed_members,
        )


__all__ = [
    "EVIDENCE_SCOPE",
    "SCHEMA_VERSION",
    "consume_c4_shadow_zero_submit_cycle",
]
