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
from us_equity_strategies.research.c3_capital_path import (
    diagnose_capital_path_inputs,
    scale_member_budgets_to_cash,
    simulate_fixed_budget_capital_path,
)

SCHEMA_VERSION = "qsl.c3-fixed-budget-baseline-comparison-research.v1"
EVIDENCE_SCOPE = "FIXED_MEMBER_BUDGET_BASELINE_COMPARISON_ONLY"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTITY = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
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
    "metrics_basis": "RAW_FIXED_MEMBER_BUDGETS_UNSCALED",
    "risk_scaling_applied_to_returns": "NOT_APPLIED",
    "rebalance_fee_reconstruction": "NOT_COMPUTED",
    "optimization": "DISABLED_FIXED_BUDGETS_ONLY",
}
_METRICS_ACCOUNTING = {
    "weight_source": "declared_member_budget_weights",
    "risk_scaling_applied": False,
    "rebalance_fees_applied": False,
    "realized_vs_recommended": (
        "METRICS_ARE_RAW_FIXED_BUDGET_NOT_RISK_SCALED_RECOMMENDATION"
    ),
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
        if field == "as_of":
            try:
                parsed = date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("MEMBER_COMPARABILITY_INCOMPLETE") from exc
            if parsed.isoformat() != value:
                raise ValueError("MEMBER_COMPARABILITY_INCOMPLETE")
        elif field == "quote_currency":
            if _CURRENCY.fullmatch(value) is None:
                raise ValueError("MEMBER_COMPARABILITY_INCOMPLETE")
        elif field.endswith("_digest") and _DIGEST.fullmatch(value) is None:
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


def _parse_capital_path_options(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("CAPITAL_PATH_OPTIONS_INVALID")
    apply_risk_scaling = value.get("apply_risk_scaling", False)
    if not isinstance(apply_risk_scaling, bool):
        raise ValueError("CAPITAL_PATH_OPTIONS_INVALID")
    cash_member_id = value.get("cash_member_id")
    if cash_member_id is not None:
        cash_member_id = _identity(cash_member_id, "CASH_MEMBER_ID")
    rebalance_fee_bps = value.get("rebalance_fee_bps")
    if rebalance_fee_bps is not None:
        if isinstance(rebalance_fee_bps, bool) or not isinstance(
            rebalance_fee_bps, (int, float)
        ):
            raise ValueError("REBALANCE_FEE_BPS_INVALID")
        rebalance_fee_bps = float(rebalance_fee_bps)
        if not math.isfinite(rebalance_fee_bps) or rebalance_fee_bps < 0.0:
            raise ValueError("REBALANCE_FEE_BPS_INVALID")
    rebalance_indices = value.get("rebalance_indices")
    if rebalance_indices is not None:
        if not isinstance(rebalance_indices, Sequence) or isinstance(
            rebalance_indices, (str, bytes)
        ):
            raise ValueError("REBALANCE_INDEX_INVALID")
        parsed_indices: list[int] = []
        for item in rebalance_indices:
            if isinstance(item, bool) or not isinstance(item, int):
                raise ValueError("REBALANCE_INDEX_INVALID")
            parsed_indices.append(item)
        rebalance_indices = tuple(parsed_indices)
    member_costs_already_embedded = value.get("member_costs_already_embedded", True)
    if member_costs_already_embedded is not True:
        raise ValueError("MEMBER_GROSS_RETURNS_REQUIRED_TO_RECHARGE_MEMBER_COSTS")
    fee_bearing_member_ids: tuple[str, ...] | None
    if "fee_bearing_member_ids" not in value:
        fee_bearing_member_ids = None
    else:
        raw_ids = value["fee_bearing_member_ids"]
        if (
            raw_ids is None
            or isinstance(raw_ids, (str, bytes))
            or not isinstance(raw_ids, Sequence)
            or len(raw_ids) == 0
        ):
            raise ValueError("FEE_BEARING_MEMBER_IDS_INVALID")
        parsed_ids: list[str] = []
        seen_ids: set[str] = set()
        for item in raw_ids:
            if not isinstance(item, str) or item in seen_ids:
                raise ValueError("FEE_BEARING_MEMBER_IDS_INVALID")
            try:
                parsed_id = _identity(item, "FEE_BEARING_MEMBER_ID")
            except ValueError as exc:
                raise ValueError("FEE_BEARING_MEMBER_IDS_INVALID") from exc
            seen_ids.add(parsed_id)
            parsed_ids.append(parsed_id)
        fee_bearing_member_ids = tuple(sorted(parsed_ids))
    return {
        "apply_risk_scaling": apply_risk_scaling,
        "cash_member_id": cash_member_id,
        "rebalance_fee_bps": rebalance_fee_bps,
        "rebalance_indices": rebalance_indices,
        "fee_bearing_member_ids": fee_bearing_member_ids,
        "member_costs_already_embedded": True,
    }


def _capital_path_for_baseline(
    *,
    members: Sequence[Mapping[str, object]],
    budgets: Mapping[str, float],
    dates: Sequence[str],
    options: Mapping[str, object],
    concentration: Mapping[str, object] | None,
) -> dict[str, object]:
    apply_risk_scaling = bool(options["apply_risk_scaling"])
    cash_member_id = options["cash_member_id"]
    rebalance_fee_bps = options["rebalance_fee_bps"]
    rebalance_indices = options["rebalance_indices"]
    fee_bearing_member_ids = options["fee_bearing_member_ids"]
    gaps = diagnose_capital_path_inputs(
        apply_risk_scaling=apply_risk_scaling,
        rebalance_fee_bps=(
            rebalance_fee_bps if isinstance(rebalance_fee_bps, float) else None
        ),
        rebalance_indices=(
            rebalance_indices if isinstance(rebalance_indices, tuple) else None
        ),
        cash_member_id=cash_member_id if isinstance(cash_member_id, str) else None,
        has_risk_diagnosis=concentration is not None,
        fee_bearing_member_ids=(
            fee_bearing_member_ids if isinstance(fee_bearing_member_ids, tuple) else None
        ),
    )
    # Drift-only (no fee schedule) is allowed when scaling or path is requested
    # without positive fee inputs; drop the "not requested" diagnostic noise.
    gaps = tuple(
        gap
        for gap in gaps
        if gap != "CAPITAL_PATH_OPTIONAL_INPUTS_NOT_REQUESTED"
    )
    if apply_risk_scaling and (
        "NEED_RISK_POLICY_AND_TARGET_WEIGHTS_FOR_SCALING" in gaps
        or "NEED_CASH_MEMBER_ID_FOR_RISK_SCALING_RESIDUAL" in gaps
    ):
        raise ValueError("CAPITAL_PATH_RISK_SCALING_INPUTS_INCOMPLETE")
    if (
        isinstance(rebalance_fee_bps, float)
        and rebalance_fee_bps > 0.0
        and rebalance_indices is None
    ):
        raise ValueError("REBALANCE_SCHEDULE_REQUIRED_FOR_POSITIVE_FEE")
    if rebalance_indices is not None and rebalance_fee_bps is None:
        raise ValueError("REBALANCE_FEE_BPS_REQUIRED_FOR_REBALANCE")
    if (
        isinstance(rebalance_fee_bps, float)
        and rebalance_fee_bps > 0.0
        and not isinstance(fee_bearing_member_ids, tuple)
    ):
        raise ValueError("FEE_BEARING_MEMBER_IDS_REQUIRED")

    path_budgets = dict(budgets)
    risk_scalar = 1.0
    scaled_from: dict[str, float] | None = None
    if apply_risk_scaling:
        assert concentration is not None
        assert isinstance(cash_member_id, str)
        risk_scalar = float(concentration["risk_scalar"])
        scaled_from = dict(budgets)
        path_budgets = scale_member_budgets_to_cash(
            budgets=budgets,
            risk_scalar=risk_scalar,
            cash_member_id=cash_member_id,
        )

    member_ids = tuple(str(member["member_id"]) for member in members)
    member_returns = {
        str(member["member_id"]): tuple(float(item) for item in member["returns"])  # type: ignore[arg-type]
        for member in members
    }
    path = simulate_fixed_budget_capital_path(
        member_ids=member_ids,
        member_returns=member_returns,
        target_weights=path_budgets,
        rebalance_fee_bps=(
            rebalance_fee_bps if isinstance(rebalance_fee_bps, float) else None
        ),
        rebalance_indices=(
            rebalance_indices if isinstance(rebalance_indices, tuple) else None
        ),
        fee_bearing_member_ids=(
            fee_bearing_member_ids if isinstance(fee_bearing_member_ids, tuple) else None
        ),
        member_costs_already_embedded=True,
    )
    path_returns = tuple(float(item) for item in path["daily_returns"])  # type: ignore[arg-type]
    cash_weight = (
        float(path_budgets[cash_member_id]) if isinstance(cash_member_id, str) else None
    )
    non_cash_exposure = math.fsum(
        weight
        for member_id, weight in path_budgets.items()
        if member_id != cash_member_id
    )
    return {
        "metrics": _metrics(dates, path_returns),
        "daily_returns": path_returns,
        "target_weights_used": path_budgets,
        "risk_scalar": risk_scalar,
        "scaled_from_member_budget_weights": scaled_from,
        "cash_weight_after_scaling": cash_weight,
        "non_cash_nominal_exposure_after_scaling": non_cash_exposure,
        "final_weights": path["final_weights"],
        "weight_path": path["weight_path"],
        "fee_fractions": path["fee_fractions"],
        "one_way_turnovers": path["one_way_turnovers"],
        "pre_trade_fee_notionals": path["pre_trade_fee_notionals"],
        "fee_bearing_member_ids": path["fee_bearing_member_ids"],
        "rebalance_fee_basis": path["rebalance_fee_basis"],
        "total_fee_fraction_sum": path["total_fee_fraction_sum"],
        "input_gaps": gaps,
        "metrics_accounting": {
            "weight_source": (
                "risk_scaled_member_budget_weights"
                if apply_risk_scaling
                else "declared_member_budget_weights"
            ),
            "risk_scaling_applied": apply_risk_scaling,
            "rebalance_fees_applied": bool(path["rebalance_fees_applied"]),
            "member_costs_recharged": False,
            "member_costs_treatment": "ALREADY_EMBEDDED_VIA_COST_MODEL_DIGEST",
            "realized_vs_recommended": (
                "METRICS_USE_RISK_SCALED_TARGET_WHEN_REQUESTED"
                if apply_risk_scaling
                else "METRICS_USE_DECLARED_FIXED_BUDGET_WITH_OPTIONAL_PATH_FEES"
            ),
        },
        "rebalance_fee_reconstruction": path["rebalance_fee_reconstruction"],
    }


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
    capital_path_options: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Compare ≥2 declared fixed member budgets on aligned member returns.

    Successful outputs remain research/shadow-only: ``execution_authorized``,
    ``promotion_authorized`` stay false and ``no_order`` stays true.  Default
    metrics represent the raw declared fixed-budget combination only: risk
    scaling and rebalance-fee reconstruction are not applied unless
    ``capital_path_options`` supplies the explicit inputs required to compute
    them.  A positive rebalance fee also requires ``fee_bearing_member_ids``;
    that charge is the pre-trade notional approximation from the capital-path
    simulator.  Member returns are not re-costed; cost meaning is bound by the shared
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

        path_options = _parse_capital_path_options(capital_path_options)
        comparability = dict(parsed_members[0]["comparability"])
        dates = parsed_members[0]["dates"]
        baseline_results: list[dict[str, object]] = []
        any_scaling = False
        fee_reconstruction = "NOT_COMPUTED"
        for baseline in parsed_baselines:
            combined = _combine_returns(
                members=parsed_members, budgets=baseline["member_budget_weights"]
            )
            concentration = _concentration(
                baseline=baseline,
                asset_risk_specs=asset_risk_specs,
                risk_policy=risk_policy,
            )
            result: dict[str, object] = {
                "baseline_id": baseline["baseline_id"],
                "budget_digest": baseline["budget_digest"],
                "member_budget_weights": baseline["member_budget_weights"],
                # Default metrics stay on declared fixed budgets only.
                "metrics": _metrics(dates, combined),
                "metrics_accounting": dict(_METRICS_ACCOUNTING),
                "declared_one_way_turnover": baseline["declared_one_way_turnover"],
                "concentration": concentration,
                "capital_path": None,
            }
            if path_options is not None:
                capital_path = _capital_path_for_baseline(
                    members=parsed_members,
                    budgets=baseline["member_budget_weights"],
                    dates=dates,
                    options=path_options,
                    concentration=concentration,
                )
                result["capital_path"] = capital_path
                accounting = capital_path["metrics_accounting"]
                assert isinstance(accounting, Mapping)
                any_scaling = any_scaling or bool(accounting["risk_scaling_applied"])
                fee_reconstruction = str(capital_path["rebalance_fee_reconstruction"])
            baseline_results.append(result)

        policy_digest = comparability["risk_policy_digest"]
        boundaries = dict(_BOUNDARY_MARKERS)
        if path_options is None:
            pass
        else:
            boundaries["metrics_basis"] = (
                "RAW_FIXED_PLUS_OPTIONAL_CAPITAL_PATH"
            )
            boundaries["risk_scaling_applied_to_returns"] = (
                "APPLIED_IN_CAPITAL_PATH" if any_scaling else "NOT_APPLIED"
            )
            boundaries["rebalance_fee_reconstruction"] = fee_reconstruction
            boundaries["member_costs_recharged"] = "FALSE_ALREADY_EMBEDDED"
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
                    "capital_path_options": path_options,
                }
            ),
            "baselines": baseline_results,
            "boundaries": boundaries,
            "capital_path_requested": path_options is not None,
        }
        if all(item["concentration"] is None for item in baseline_results):
            payload["boundaries"] = {
                **boundaries,
                "concentration_underlying_diagnosis": "NOT_PROVIDED",
            }
        else:
            payload["boundaries"] = {
                **boundaries,
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
