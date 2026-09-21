"""C3 research: compare declared fixed member-budget baselines offline.

This module recombines already-frozen, aligned member daily returns under
explicit member budget weights.  It does not optimize weights, estimate
Kelly fractions, equate prediction scores to capital, read accounts, or
authorize paper/shadow/live execution.

Concentration / look-through diagnosis optionally reuses
``assess_portfolio_risk_budget``.  Integer-share sizing, cash reserves,
leverage expansion, and live liquidity gates are diagnostic boundaries only.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import date

from us_equity_strategies.portfolio_risk_budget import (
    PortfolioAssetRiskSpec,
    PortfolioRiskBudgetPolicy,
    assess_portfolio_risk_budget,
)

SCHEMA_VERSION = "qsl.c3-fixed-budget-baseline-comparison-research.v1"
EVIDENCE_SCOPE = "FIXED_MEMBER_BUDGET_BASELINE_COMPARISON_ONLY"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTITY = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_EPSILON = 1e-12
_COMPARABILITY_FIELDS = (
    "as_of",
    "quote_currency",
    "capital_basis_digest",
    "cost_model_digest",
    "risk_policy_digest",
    "data_scope_digest",
)
_BOUNDARY_MARKERS = {
    "integer_share_sizing": "DIAGNOSTIC_ONLY_NOT_COMPUTED",
    "cash_reserve_enforcement": "DIAGNOSTIC_ONLY_NOT_COMPUTED",
    "leverage_expansion": "NOT_AUTHORIZED_NOT_COMPUTED",
    "live_liquidity_gates": "NOT_COMPUTED",
    "cost_accounting": "EMBEDDED_IN_MEMBER_RETURNS_VIA_COST_MODEL_DIGEST",
    "optimization": "DISABLED_FIXED_BUDGETS_ONLY",
}


def _digest_payload(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parked(
    reason: str,
    *,
    member_refs: Sequence[Mapping[str, object]] | None = None,
    baseline_refs: Sequence[Mapping[str, object]] | None = None,
    comparability: Mapping[str, str] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "research_only": True,
        "shadow_only": True,
        "execution_authorized": False,
        "promotion_authorized": False,
        "no_order": True,
        "evidence_scope": EVIDENCE_SCOPE,
        "status": "PARKED",
        "reason_codes": (reason,),
        "member_refs": list(member_refs or ()),
        "baseline_refs": list(baseline_refs or ()),
        "comparability": dict(comparability or {}),
        "baselines": [],
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


def _finite_return(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("MEMBER_RETURN_INVALID")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= -1.0:
        raise ValueError("MEMBER_RETURN_INVALID")
    return numeric


def _weight(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}_INVALID")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= _EPSILON or numeric > 1.0:
        raise ValueError(f"{label}_INVALID")
    return numeric


def _parse_dates(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) < 2:
        raise ValueError("MEMBER_DATES_INVALID")
    dates: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("MEMBER_DATES_INVALID")
        try:
            normalized = date.fromisoformat(item).isoformat()
        except ValueError as exc:
            raise ValueError("MEMBER_DATES_INVALID") from exc
        if normalized != item:
            raise ValueError("MEMBER_DATES_INVALID")
        dates.append(normalized)
    if tuple(dates) != tuple(sorted(dates)) or len(set(dates)) != len(dates):
        raise ValueError("MEMBER_DATES_INVALID")
    return tuple(dates)


def _parse_returns(value: object, *, count: int) -> tuple[float, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != count:
        raise ValueError("MEMBER_RETURNS_INVALID")
    return tuple(_finite_return(item) for item in value)


def _comparability(member: Mapping[str, object]) -> dict[str, str]:
    present = tuple(field for field in _COMPARABILITY_FIELDS if field in member)
    if len(present) != len(_COMPARABILITY_FIELDS):
        raise ValueError("MEMBER_COMPARABILITY_INCOMPLETE")
    normalized: dict[str, str] = {}
    for field in _COMPARABILITY_FIELDS:
        value = member[field]
        if not isinstance(value, str) or not value.strip():
            raise ValueError("MEMBER_COMPARABILITY_INCOMPLETE")
        if field.endswith("_digest") and _DIGEST.fullmatch(value) is None:
            raise ValueError("MEMBER_COMPARABILITY_INCOMPLETE")
        normalized[field] = value
    return normalized


def _parse_member(raw: object) -> dict[str, object]:
    if not isinstance(raw, Mapping):
        raise ValueError("MEMBER_REFERENCE_INVALID")
    member_id = _identity(raw.get("member_id"), "MEMBER_ID")
    evidence_digest = _digest(raw.get("evidence_digest"), "MEMBER_EVIDENCE_DIGEST")
    input_digest = _digest(raw.get("input_digest"), "MEMBER_INPUT_DIGEST")
    dates = _parse_dates(raw.get("dates"))
    returns = _parse_returns(raw.get("returns"), count=len(dates))
    comparability = _comparability(raw)
    return {
        "member_id": member_id,
        "evidence_digest": evidence_digest,
        "input_digest": input_digest,
        "dates": dates,
        "returns": returns,
        "comparability": comparability,
    }


def _parse_members(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) < 2:
        raise ValueError("MEMBERS_REQUIRED")
    members = tuple(_parse_member(item) for item in value)
    ids = tuple(member["member_id"] for member in members)
    if ids != tuple(sorted(set(ids))):
        raise ValueError("MEMBER_IDS_NOT_UNIQUE_SORTED")
    baseline_dates = members[0]["dates"]
    baseline_comp = members[0]["comparability"]
    for member in members[1:]:
        if member["dates"] != baseline_dates:
            raise ValueError("MEMBER_DATE_ALIGNMENT_MISMATCH")
        if member["comparability"] != baseline_comp:
            raise ValueError("MEMBER_COMPARABILITY_MISMATCH")
    return members


def _parse_budgets(
    value: object, *, member_ids: tuple[str, ...]
) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != set(member_ids):
        raise ValueError("BASELINE_BUDGETS_MISMATCH")
    budgets = {
        member_id: _weight(value[member_id], "BASELINE_BUDGET") for member_id in member_ids
    }
    if not math.isclose(math.fsum(budgets.values()), 1.0, rel_tol=0.0, abs_tol=_EPSILON):
        raise ValueError("BASELINE_BUDGETS_NOT_FULLY_FUNDED")
    return dict(sorted(budgets.items()))


def _optional_turnover(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("BASELINE_TURNOVER_INVALID")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0.0 or numeric > 1.0:
        raise ValueError("BASELINE_TURNOVER_INVALID")
    return numeric


def _parse_baseline(
    raw: object, *, member_ids: tuple[str, ...]
) -> dict[str, object]:
    if not isinstance(raw, Mapping):
        raise ValueError("BASELINE_REFERENCE_INVALID")
    baseline_id = _identity(raw.get("baseline_id"), "BASELINE_ID")
    budgets = _parse_budgets(raw.get("member_budget_weights"), member_ids=member_ids)
    target_weights = raw.get("representative_target_weights")
    if target_weights is not None:
        if not isinstance(target_weights, Mapping) or not target_weights:
            raise ValueError("BASELINE_TARGET_WEIGHTS_INVALID")
        normalized_targets: dict[str, float] = {}
        for symbol, weight in target_weights.items():
            if not isinstance(symbol, str) or not symbol or symbol != symbol.strip() or symbol.upper() != symbol:
                raise ValueError("BASELINE_TARGET_WEIGHTS_INVALID")
            if isinstance(weight, bool) or not isinstance(weight, (int, float)):
                raise ValueError("BASELINE_TARGET_WEIGHTS_INVALID")
            numeric = float(weight)
            if not math.isfinite(numeric) or numeric < 0.0:
                raise ValueError("BASELINE_TARGET_WEIGHTS_INVALID")
            normalized_targets[symbol] = numeric
        target_weights = dict(sorted(normalized_targets.items()))
    return {
        "baseline_id": baseline_id,
        "member_budget_weights": budgets,
        "representative_target_weights": target_weights,
        "declared_one_way_turnover": _optional_turnover(raw.get("declared_one_way_turnover")),
        "budget_digest": _digest_payload(
            {
                "baseline_id": baseline_id,
                "member_budget_weights": budgets,
            }
        ),
    }


def _parse_baselines(
    value: object, *, member_ids: tuple[str, ...]
) -> tuple[dict[str, object], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) < 2:
        raise ValueError("BASELINES_REQUIRED")
    baselines = tuple(_parse_baseline(item, member_ids=member_ids) for item in value)
    ids = tuple(item["baseline_id"] for item in baselines)
    if ids != tuple(sorted(set(ids))):
        raise ValueError("BASELINE_IDS_NOT_UNIQUE_SORTED")
    turnover_declared = [item["declared_one_way_turnover"] is not None for item in baselines]
    if any(turnover_declared) and not all(turnover_declared):
        raise ValueError("BASELINE_TURNOVER_INCOMPLETE")
    targets_declared = [item["representative_target_weights"] is not None for item in baselines]
    if any(targets_declared) and not all(targets_declared):
        raise ValueError("BASELINE_TARGET_WEIGHTS_INCOMPLETE")
    return baselines


def _metrics(dates: Sequence[str], returns: Sequence[float]) -> dict[str, object]:
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for item in returns:
        equity *= 1.0 + item
        if not math.isfinite(equity) or equity <= 0.0:
            raise ValueError("COMBINED_EQUITY_INVALID")
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1.0)
    start = date.fromisoformat(dates[0])
    end = date.fromisoformat(dates[-1])
    years = (end - start).days / 365.2425
    if years <= 0.0:
        raise ValueError("METRICS_WINDOW_INVALID")
    mean = math.fsum(returns) / len(returns)
    variance = math.fsum((item - mean) ** 2 for item in returns) / len(returns)
    annualized_volatility = math.sqrt(variance) * math.sqrt(252.0)
    return {
        "start_date": dates[0],
        "end_date": dates[-1],
        "session_count": len(returns),
        "cumulative_return": equity - 1.0,
        "terminal_nav": equity,
        "cagr": equity ** (1.0 / years) - 1.0,
        "max_drawdown": max_drawdown,
        "annualized_volatility": annualized_volatility,
        "tail_loss_proxy_min_daily_return": min(returns),
    }


def _combine_returns(
    *,
    members: Sequence[Mapping[str, object]],
    budgets: Mapping[str, float],
) -> tuple[float, ...]:
    count = len(members[0]["returns"])
    combined: list[float] = []
    for index in range(count):
        value = math.fsum(
            budgets[str(member["member_id"])] * float(member["returns"][index])
            for member in members
        )
        if not math.isfinite(value) or value <= -1.0:
            raise ValueError("COMBINED_RETURN_INVALID")
        combined.append(value)
    return tuple(combined)


def _concentration(
    *,
    baseline: Mapping[str, object],
    asset_risk_specs: Mapping[str, PortfolioAssetRiskSpec] | None,
    risk_policy: PortfolioRiskBudgetPolicy | None,
) -> dict[str, object] | None:
    target = baseline["representative_target_weights"]
    if target is None:
        return None
    if asset_risk_specs is None or risk_policy is None:
        raise ValueError("RISK_DIAGNOSIS_INPUTS_INCOMPLETE")
    assessment = assess_portfolio_risk_budget(
        target_weights=target,
        asset_risk_specs=asset_risk_specs,
        policy=risk_policy,
    )
    if assessment["status"] == "PARKED":
        raise ValueError("CONCENTRATION_DIAGNOSIS_PARKED")
    return {
        "status": assessment["status"],
        "execution_authorized": False,
        "risk_scalar": assessment["risk_scalar"],
        "reason_codes": tuple(assessment["reason_codes"]),
        "metrics": dict(assessment["metrics"]),
        "recommended_target_weights": dict(assessment["recommended_target_weights"]),
    }


def compare_fixed_member_budget_baselines(
    *,
    members: Sequence[Mapping[str, object]],
    baselines: Sequence[Mapping[str, object]],
    asset_risk_specs: Mapping[str, PortfolioAssetRiskSpec] | None = None,
    risk_policy: PortfolioRiskBudgetPolicy | None = None,
) -> dict[str, object]:
    """Compare ≥2 declared fixed member budgets on aligned member returns.

    Successful outputs remain research/shadow-only: ``execution_authorized``,
    ``promotion_authorized`` stay false and ``no_order`` stays true.  Member
    returns are not re-costed; cost meaning is bound by the shared
    ``cost_model_digest``.  Different windows or synthetic splices are rejected
    via date/comparability fail-closed checks.
    """
    member_refs: list[dict[str, object]] = []
    baseline_refs: list[dict[str, object]] = []
    try:
        parsed_members = _parse_members(members)
        member_ids = tuple(str(member["member_id"]) for member in parsed_members)
        member_refs = [
            {
                "member_id": member["member_id"],
                "evidence_digest": member["evidence_digest"],
                "input_digest": member["input_digest"],
            }
            for member in parsed_members
        ]
        parsed_baselines = _parse_baselines(baselines, member_ids=member_ids)
        baseline_refs = [
            {
                "baseline_id": item["baseline_id"],
                "budget_digest": item["budget_digest"],
                "member_budget_weights": item["member_budget_weights"],
            }
            for item in parsed_baselines
        ]
        if (asset_risk_specs is None) ^ (risk_policy is None):
            raise ValueError("RISK_DIAGNOSIS_INPUTS_INCOMPLETE")
        needs_risk = any(
            item["representative_target_weights"] is not None for item in parsed_baselines
        )
        if needs_risk and (asset_risk_specs is None or risk_policy is None):
            raise ValueError("RISK_DIAGNOSIS_INPUTS_INCOMPLETE")
        if not needs_risk and asset_risk_specs is not None:
            raise ValueError("RISK_DIAGNOSIS_INPUTS_INCOMPLETE")

        comparability = dict(parsed_members[0]["comparability"])
        dates = parsed_members[0]["dates"]
        baseline_results: list[dict[str, object]] = []
        for baseline in parsed_baselines:
            combined = _combine_returns(
                members=parsed_members, budgets=baseline["member_budget_weights"]
            )
            result: dict[str, object] = {
                "baseline_id": baseline["baseline_id"],
                "budget_digest": baseline["budget_digest"],
                "member_budget_weights": baseline["member_budget_weights"],
                "metrics": _metrics(dates, combined),
                "declared_one_way_turnover": baseline["declared_one_way_turnover"],
                "concentration": _concentration(
                    baseline=baseline,
                    asset_risk_specs=asset_risk_specs,
                    risk_policy=risk_policy,
                ),
            }
            baseline_results.append(result)

        policy_digest = comparability["risk_policy_digest"]
        payload: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "research_only": True,
            "shadow_only": True,
            "execution_authorized": False,
            "promotion_authorized": False,
            "no_order": True,
            "evidence_scope": EVIDENCE_SCOPE,
            "status": "READY_RESEARCH_ONLY",
            "reason_codes": (),
            "member_refs": member_refs,
            "baseline_refs": baseline_refs,
            "comparability": comparability,
            "policy_digest": policy_digest,
            "input_digest": _digest_payload(
                {
                    "member_refs": member_refs,
                    "baseline_refs": baseline_refs,
                    "comparability": comparability,
                }
            ),
            "baselines": baseline_results,
            "boundaries": dict(_BOUNDARY_MARKERS),
        }
        if all(item["concentration"] is None for item in baseline_results):
            payload["boundaries"] = {
                **_BOUNDARY_MARKERS,
                "concentration_underlying_diagnosis": "NOT_PROVIDED",
            }
        else:
            payload["boundaries"] = {
                **_BOUNDARY_MARKERS,
                "concentration_underlying_diagnosis": "PORTFOLIO_RISK_BUDGET_SNAPSHOT_ONLY",
            }
        payload["evidence_digest"] = _digest_payload(payload)
        return payload
    except ValueError as exc:
        return _parked(
            str(exc),
            member_refs=member_refs,
            baseline_refs=baseline_refs,
        )
    except (TypeError, ArithmeticError):
        return _parked(
            "C3_COMPARISON_FAILED_CLOSED",
            member_refs=member_refs,
            baseline_refs=baseline_refs,
        )


__all__ = [
    "EVIDENCE_SCOPE",
    "SCHEMA_VERSION",
    "compare_fixed_member_budget_baselines",
]
