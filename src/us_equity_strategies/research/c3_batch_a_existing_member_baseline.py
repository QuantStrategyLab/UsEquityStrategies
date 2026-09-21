"""Batch A research: existing SOXL/TQQQ + cash fixed-budget baseline gate.

This consumer does **not** invent daily returns.  It only:

1. reports whether locked private R3 inputs / a frozen C3 member pack exist;
2. when a complete frozen pack is supplied, reuses
   ``compare_fixed_member_budget_baselines`` for declared fixed budgets; and
3. always keeps research/shadow/no-order boundaries.

Without a frozen, comparable SOXL/TQQQ/cash member pack the result is PARKED.
Legacy combo derived-return replay is explicitly rejected as Batch A evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from us_equity_strategies.portfolio_risk_budget import (
    PortfolioAssetRiskSpec,
    PortfolioRiskBudgetPolicy,
)
from us_equity_strategies.research.c3_fixed_budget_baseline_comparison import (
    compare_fixed_member_budget_baselines,
)
from us_equity_strategies.research.r3_joint_evidence import (
    PRIVATE_ROOT,
    assess_private_r3_readiness,
)

SCHEMA_VERSION = "qsl.c3-batch-a-existing-member-baseline-research.v1"
EVIDENCE_SCOPE = "BATCH_A_EXISTING_SOXL_TQQQ_CASH_FIXED_BUDGET_ONLY"
REQUIRED_MEMBER_IDS = ("cash_sleeve", "soxl_core", "tqqq_core")
ALLOWED_CASH_POLICIES = frozenset(
    {
        "ASSUMED_ZERO_CASH_SLEEVE",
        "FROZEN_BOXX_RETURNS",
    }
)
_BOUNDARY_MARKERS = {
    "integer_share_sizing": "DIAGNOSTIC_ONLY_NOT_COMPUTED",
    "cash_reserve_enforcement": "DIAGNOSTIC_ONLY_NOT_COMPUTED",
    "leverage_expansion": "NOT_AUTHORIZED_NOT_COMPUTED",
    "live_liquidity_gates": "NOT_COMPUTED",
    "optimization": "DISABLED_FIXED_BUDGETS_ONLY",
    "legacy_combo_derived_returns": "REJECTED_NOT_BATCH_A_EVIDENCE",
    "return_invention": "FORBIDDEN",
    "cost_accounting": "EMBEDDED_IN_MEMBER_RETURNS_VIA_COST_MODEL_DIGEST",
}


def _digest_payload(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _authority_fields() -> dict[str, object]:
    return {
        "research_only": True,
        "shadow_only": True,
        "execution_authorized": False,
        "promotion_authorized": False,
        "no_order": True,
    }


def _parked(
    *reasons: str,
    evidence_gaps: Sequence[str] | None = None,
    r3_readiness: Mapping[str, object] | None = None,
    member_refs: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        **_authority_fields(),
        "evidence_scope": EVIDENCE_SCOPE,
        "status": "PARKED",
        "reason_codes": tuple(reasons),
        "evidence_gaps": list(evidence_gaps or ()),
        "r3_readiness": dict(r3_readiness or {}),
        "member_refs": list(member_refs or ()),
        "standalone_members": [],
        "c3_comparison": None,
        "declared_baselines": [],
        "boundaries": dict(_BOUNDARY_MARKERS),
        "batch_a_accepted": False,
    }
    payload["evidence_digest"] = _digest_payload(payload)
    return payload


def _finite_return(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("MEMBER_RETURN_INVALID")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= -1.0:
        raise ValueError("MEMBER_RETURN_INVALID")
    return numeric


def _metrics(dates: Sequence[str], returns: Sequence[float]) -> dict[str, object]:
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for item in returns:
        equity *= 1.0 + item
        if not math.isfinite(equity) or equity <= 0.0:
            raise ValueError("STANDALONE_EQUITY_INVALID")
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1.0)
    start = date.fromisoformat(dates[0])
    end = date.fromisoformat(dates[-1])
    years = (end - start).days / 365.2425
    if years <= 0.0:
        raise ValueError("METRICS_WINDOW_INVALID")
    mean = math.fsum(returns) / len(returns)
    variance = math.fsum((item - mean) ** 2 for item in returns) / len(returns)
    return {
        "start_date": dates[0],
        "end_date": dates[-1],
        "session_count": len(returns),
        "cumulative_return": equity - 1.0,
        "terminal_nav": equity,
        "cagr": equity ** (1.0 / years) - 1.0,
        "max_drawdown": max_drawdown,
        "annualized_volatility": math.sqrt(variance) * math.sqrt(252.0),
        "tail_loss_proxy_min_daily_return": min(returns),
    }


def _declared_baselines() -> tuple[dict[str, object], ...]:
    """Fixed member budgets for Batch A (all weights > 0; cash always present)."""

    return (
        {
            "baseline_id": "balanced_risk_with_cash_40_40_20",
            "member_budget_weights": {
                "cash_sleeve": 0.20,
                "soxl_core": 0.40,
                "tqqq_core": 0.40,
            },
            "representative_target_weights": {
                "BOXX": 0.20,
                "SOXL": 0.40,
                "TQQQ": 0.40,
            },
            "declared_one_way_turnover": 0.06,
        },
        {
            "baseline_id": "shadow_like_352045_tqqq_soxl_cash",
            "member_budget_weights": {
                "cash_sleeve": 0.45,
                "soxl_core": 0.20,
                "tqqq_core": 0.35,
            },
            "representative_target_weights": {
                "BOXX": 0.45,
                "SOXL": 0.20,
                "TQQQ": 0.35,
            },
            "declared_one_way_turnover": 0.05,
        },
        {
            "baseline_id": "soxl_tilt_with_cash_60_20_20",
            "member_budget_weights": {
                "cash_sleeve": 0.20,
                "soxl_core": 0.60,
                "tqqq_core": 0.20,
            },
            "representative_target_weights": {
                "BOXX": 0.20,
                "SOXL": 0.60,
                "TQQQ": 0.20,
            },
            "declared_one_way_turnover": 0.08,
        },
        {
            "baseline_id": "tqqq_tilt_with_cash_20_60_20",
            "member_budget_weights": {
                "cash_sleeve": 0.20,
                "soxl_core": 0.20,
                "tqqq_core": 0.60,
            },
            "representative_target_weights": {
                "BOXX": 0.20,
                "SOXL": 0.20,
                "TQQQ": 0.60,
            },
            "declared_one_way_turnover": 0.08,
        },
    )


def _default_risk_specs() -> dict[str, PortfolioAssetRiskSpec]:
    return {
        "BOXX": PortfolioAssetRiskSpec("BOXX", 1.0, "CASH", is_cash=True),
        "SOXL": PortfolioAssetRiskSpec("SOXL", 3.0, "SEMICONDUCTOR"),
        "TQQQ": PortfolioAssetRiskSpec("TQQQ", 3.0, "NASDAQ100"),
    }


def _default_risk_policy() -> PortfolioRiskBudgetPolicy:
    return PortfolioRiskBudgetPolicy(
        cash_symbol="BOXX",
        max_effective_risk_exposure=1.5,
        max_symbol_weights={"SOXL": 0.60, "TQQQ": 0.60},
        max_underlying_effective_exposure={"SEMICONDUCTOR": 1.8, "NASDAQ100": 1.8},
    )


def _validate_frozen_member_pack(raw: Mapping[str, object]) -> dict[str, Any]:
    if raw.get("schema_version") != "qsl.c3-batch-a-frozen-member-pack.v1":
        raise ValueError("FROZEN_MEMBER_PACK_SCHEMA_INVALID")
    if raw.get("research_only") is not True:
        raise ValueError("FROZEN_MEMBER_PACK_NOT_RESEARCH_ONLY")
    if raw.get("execution_authorized") is not False:
        raise ValueError("FROZEN_MEMBER_PACK_EXECUTION_AUTHORIZED")
    cash_policy = raw.get("cash_return_policy")
    if cash_policy not in ALLOWED_CASH_POLICIES:
        raise ValueError("CASH_RETURN_POLICY_INVALID")
    members_raw = raw.get("members")
    if not isinstance(members_raw, Sequence) or isinstance(members_raw, (str, bytes)):
        raise ValueError("FROZEN_MEMBERS_INVALID")
    by_id: dict[str, Mapping[str, object]] = {}
    for item in members_raw:
        if not isinstance(item, Mapping):
            raise ValueError("FROZEN_MEMBERS_INVALID")
        member_id = item.get("member_id")
        if not isinstance(member_id, str):
            raise ValueError("FROZEN_MEMBERS_INVALID")
        by_id[member_id] = item
    if tuple(sorted(by_id)) != tuple(REQUIRED_MEMBER_IDS):
        raise ValueError("FROZEN_MEMBERS_INCOMPLETE")
    cash = by_id["cash_sleeve"]
    dates = cash.get("dates")
    if not isinstance(dates, Sequence) or isinstance(dates, (str, bytes)) or len(dates) < 2:
        raise ValueError("CASH_DATES_INVALID")
    returns = cash.get("returns")
    if not isinstance(returns, Sequence) or isinstance(returns, (str, bytes)):
        raise ValueError("CASH_RETURNS_INVALID")
    if len(returns) != len(dates):
        raise ValueError("CASH_RETURNS_INVALID")
    parsed_returns = tuple(_finite_return(item) for item in returns)
    if cash_policy == "ASSUMED_ZERO_CASH_SLEEVE" and any(
        abs(item) > 1e-15 for item in parsed_returns
    ):
        raise ValueError("ASSUMED_ZERO_CASH_RETURNS_NONZERO")
    return {
        "cash_return_policy": cash_policy,
        "members": tuple(by_id[member_id] for member_id in REQUIRED_MEMBER_IDS),
        "pack_digest": raw.get("pack_digest"),
        "source_note": raw.get("source_note"),
    }


def evaluate_batch_a_existing_member_baselines(
    *,
    private_root: str | Path | None = None,
    frozen_member_pack: Mapping[str, object] | None = None,
    _r3_readiness_reader: Any = None,
) -> dict[str, object]:
    """Gate Batch A fixed-budget research without inventing returns.

    ``frozen_member_pack`` must already carry aligned SOXL/TQQQ/cash daily
    returns and full C2 comparability fields.  This function never synthesizes
    those series from public market data or legacy combo artifacts.
    """
    root = Path(private_root) if private_root is not None else PRIVATE_ROOT
    readiness_fn = _r3_readiness_reader or assess_private_r3_readiness
    readiness = readiness_fn(private_root=root)
    readiness_payload = (
        readiness.to_dict() if hasattr(readiness, "to_dict") else dict(readiness)
    )

    gaps = [
        "IN_REPO_ALIGNED_SOXL_TQQQ_DAILY_RETURNS_MISSING",
        "IN_REPO_C3_COMPARABILITY_MEMBER_PACK_MISSING",
        "CASH_OR_BOXX_ALIGNED_RETURN_SERIES_MISSING_UNLESS_DECLARED_IN_PACK",
        "R3_PERSISTED_BUNDLE_OMITS_FULL_DAILY_RETURN_SERIES",
        "LEGACY_COMBO_DERIVED_RETURNS_REJECTED",
    ]

    # A caller-supplied frozen pack is the Batch A evidence object.  Private R3
    # readiness only explains why a pack cannot be produced on the default path.
    if frozen_member_pack is None:
        reasons = [
            "BATCH_A_FROZEN_COMPARABLE_INPUTS_UNAVAILABLE",
            "BATCH_A_C3_MEMBER_PACK_NOT_PROVIDED",
            "BATCH_A_REFUSE_TO_INVENT_RETURNS",
        ]
        extra_gaps = [
            "NEED_EXPLICIT_CASH_RETURN_POLICY_AND_ALIGNED_SERIES",
        ]
        if readiness_payload.get("ready"):
            extra_gaps.insert(
                0, "PRIVATE_R3_READY_BUT_NO_MATERIALIZED_C3_MEMBER_PACK"
            )
        else:
            reasons.insert(1, "BATCH_A_PRIVATE_R3_NOT_READY")
            reasons.extend(tuple(readiness_payload.get("findings") or ()))
        return _parked(
            *reasons,
            evidence_gaps=gaps + extra_gaps,
            r3_readiness=readiness_payload,
        )

    try:
        pack = _validate_frozen_member_pack(frozen_member_pack)
        members = pack["members"]
        standalone = []
        for member in members:
            dates = tuple(str(item) for item in member["dates"])  # type: ignore[index]
            returns = tuple(_finite_return(item) for item in member["returns"])  # type: ignore[index]
            standalone.append(
                {
                    "member_id": member["member_id"],
                    "evidence_digest": member["evidence_digest"],
                    "input_digest": member["input_digest"],
                    "metrics": _metrics(dates, returns),
                    "role": (
                        "CASH_BASELINE_SLEEVE"
                        if member["member_id"] == "cash_sleeve"
                        else "SINGLE_STRATEGY_MEMBER"
                    ),
                }
            )
        baselines = _declared_baselines()
        comparison = compare_fixed_member_budget_baselines(
            members=members,
            baselines=baselines,
            asset_risk_specs=_default_risk_specs(),
            risk_policy=_default_risk_policy(),
        )
        if comparison["status"] != "READY_RESEARCH_ONLY":
            return _parked(
                "BATCH_A_C3_COMPARISON_PARKED",
                *tuple(comparison.get("reason_codes") or ()),
                evidence_gaps=["C3_CONSUMER_REJECTED_FROZEN_PACK"],
                r3_readiness=readiness_payload,
                member_refs=list(comparison.get("member_refs") or ()),
            )
        payload: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            **_authority_fields(),
            "evidence_scope": EVIDENCE_SCOPE,
            "status": "READY_RESEARCH_ONLY",
            "reason_codes": (),
            "evidence_gaps": [],
            "r3_readiness": readiness_payload,
            "cash_return_policy": pack["cash_return_policy"],
            "member_refs": list(comparison["member_refs"]),
            "standalone_members": standalone,
            "c3_comparison": comparison,
            "declared_baselines": [
                {
                    "baseline_id": item["baseline_id"],
                    "member_budget_weights": item["member_budget_weights"],
                }
                for item in baselines
            ],
            "boundaries": {
                **_BOUNDARY_MARKERS,
                "cash_return_policy": pack["cash_return_policy"],
                "concentration_underlying_diagnosis": comparison["boundaries"].get(
                    "concentration_underlying_diagnosis"
                ),
            },
            "batch_a_accepted": True,
            "policy_digest": comparison["policy_digest"],
            "input_digest": _digest_payload(
                {
                    "member_refs": comparison["member_refs"],
                    "cash_return_policy": pack["cash_return_policy"],
                    "pack_digest": pack["pack_digest"],
                }
            ),
        }
        payload["evidence_digest"] = _digest_payload(payload)
        return payload
    except (ValueError, TypeError, ArithmeticError) as exc:
        reason = str(exc) if str(exc) else "BATCH_A_PACK_VALIDATION_FAILED"
        return _parked(
            "BATCH_A_FROZEN_PACK_INVALID",
            reason,
            evidence_gaps=["FROZEN_MEMBER_PACK_FAILED_VALIDATION"],
            r3_readiness=readiness_payload,
        )


__all__ = [
    "ALLOWED_CASH_POLICIES",
    "EVIDENCE_SCOPE",
    "REQUIRED_MEMBER_IDS",
    "SCHEMA_VERSION",
    "evaluate_batch_a_existing_member_baselines",
]
