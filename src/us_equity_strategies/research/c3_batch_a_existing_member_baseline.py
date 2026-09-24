"""Batch A research: existing SOXL/TQQQ + cash fixed-budget baseline gate.

This consumer does **not** invent daily returns.  It only:

1. accepts an explicit ``qsl.c3-batch-a-frozen-member-pack.v2`` pack;
2. when a complete frozen pack is supplied, reuses
   ``compare_fixed_member_budget_baselines`` for declared fixed budgets;
3. optionally forwards explicit ``capital_path_options`` into that C3 consumer
   (never invents combo fee bps or rebalance schedules); and
4. always keeps research/shadow/no-order boundaries.

Without a frozen, comparable SOXL/TQQQ/cash member pack the result is PARKED.
Legacy R3 private-root readiness and v1 packs are rejected as Batch A evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from us_equity_strategies.portfolio_risk_budget import (
    PortfolioAssetRiskSpec,
    PortfolioRiskBudgetPolicy,
)
from us_equity_strategies.research.batch_a_member_pack import (
    CASH_RETURN_POLICY,
    REQUIRED_MEMBER_IDS,
    SCHEMA_VERSION as MEMBER_PACK_SCHEMA,
    BatchAMemberPackError,
    frozen_cost_model_digest,
    validate_batch_a_member_pack,
)
from us_equity_strategies.research.c3_fixed_budget_baseline_comparison import (
    compare_fixed_member_budget_baselines,
)

SCHEMA_VERSION = "qsl.c3-batch-a-existing-member-baseline-research.v1"
EVIDENCE_SCOPE = "BATCH_A_EXISTING_SOXL_TQQQ_CASH_FIXED_BUDGET_ONLY"
ALLOWED_CASH_POLICIES = frozenset({CASH_RETURN_POLICY})
MEMBER_COST_SCENARIO = "TYPED_BASELINE_ZERO"
_BOUNDARY_MARKERS = {
    "integer_share_sizing": "DIAGNOSTIC_ONLY_NOT_COMPUTED",
    "cash_reserve_enforcement": "DIAGNOSTIC_ONLY_NOT_COMPUTED",
    "leverage_expansion": "NOT_AUTHORIZED_NOT_COMPUTED",
    "live_liquidity_gates": "NOT_COMPUTED",
    "optimization": "DISABLED_FIXED_BUDGETS_ONLY",
    "legacy_combo_derived_returns": "REJECTED_NOT_BATCH_A_EVIDENCE",
    "legacy_r3_private_root": "EXITED_ACTIVE_BATCH_A_ENTRY",
    "price_snapshot_v1": "REJECTED_NOT_BATCH_A_EVIDENCE",
    "member_pack_v1": "REJECTED_NOT_BATCH_A_EVIDENCE",
    "return_invention": "FORBIDDEN",
    "cost_accounting": "EMBEDDED_IN_MEMBER_RETURNS_VIA_COST_MODEL_DIGEST",
    "cash_symbol": "USD_CASH_NOT_BOXX",
    "risk_scaling_applied_to_returns": "NOT_APPLIED_DEFAULT_RAW_C3",
    "rebalance_fee_reconstruction": (
        "NOT_COMPUTED_MISSING_COMBO_FEE_BPS_AND_REBALANCE_SCHEDULE"
    ),
    "capital_path_inputs": (
        "NEED_EXPLICIT_COMBO_REBALANCE_FEE_BPS_AND_SCHEDULE_TO_APPLY"
    ),
    "capital_path_requested": False,
}


def _digest_payload(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _set_evidence_digest(payload: dict[str, object]) -> None:
    """Bind the final payload, excluding any previously computed digest."""
    payload.pop("evidence_digest", None)
    payload["evidence_digest"] = _digest_payload(payload)


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
    member_refs: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        **_authority_fields(),
        "evidence_scope": EVIDENCE_SCOPE,
        "status": "PARKED",
        "reason_codes": tuple(reasons),
        "evidence_gaps": list(evidence_gaps or ()),
        "member_refs": list(member_refs or ()),
        "standalone_members": [],
        "c3_comparison": None,
        "declared_baselines": [],
        "boundaries": dict(_BOUNDARY_MARKERS),
        "batch_a_accepted": False,
    }
    _set_evidence_digest(payload)
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
                "USD_CASH": 0.20,
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
                "USD_CASH": 0.45,
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
                "USD_CASH": 0.20,
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
                "USD_CASH": 0.20,
                "SOXL": 0.20,
                "TQQQ": 0.60,
            },
            "declared_one_way_turnover": 0.08,
        },
    )


def _default_risk_specs() -> dict[str, PortfolioAssetRiskSpec]:
    return {
        "USD_CASH": PortfolioAssetRiskSpec("USD_CASH", 1.0, "CASH", is_cash=True),
        "SOXL": PortfolioAssetRiskSpec("SOXL", 3.0, "SEMICONDUCTOR"),
        "TQQQ": PortfolioAssetRiskSpec("TQQQ", 3.0, "NASDAQ100"),
    }


def _default_risk_policy() -> PortfolioRiskBudgetPolicy:
    return PortfolioRiskBudgetPolicy(
        cash_symbol="USD_CASH",
        max_effective_risk_exposure=1.5,
        max_symbol_weights={"SOXL": 0.60, "TQQQ": 0.60},
        max_underlying_effective_exposure={"SEMICONDUCTOR": 1.8, "NASDAQ100": 1.8},
    )


def _parse_capital_path_options(
    value: Mapping[str, object] | None,
) -> tuple[dict[str, object] | None, tuple[str, ...]]:
    """Parse explicit capital-path options; never invent fee/schedule defaults.

    Returning non-empty gaps means the caller requested a capital path but
    omitted required fee bps and/or rebalance indices (or risk-scaling cash id).
    """

    if value is None:
        return None, ()
    if not isinstance(value, Mapping):
        raise ValueError("CAPITAL_PATH_OPTIONS_INVALID")
    unknown_keys = set(value) - {
        "apply_risk_scaling",
        "cash_member_id",
        "rebalance_fee_bps",
        "rebalance_indices",
        "fee_bearing_member_ids",
        "member_costs_already_embedded",
    }
    if unknown_keys:
        raise ValueError("CAPITAL_PATH_OPTIONS_UNKNOWN_KEYS")

    apply_risk_scaling = value.get("apply_risk_scaling", False)
    if not isinstance(apply_risk_scaling, bool):
        raise ValueError("CAPITAL_PATH_OPTIONS_INVALID")

    cash_member_id = value.get("cash_member_id")
    if cash_member_id is not None:
        if not isinstance(cash_member_id, str) or not cash_member_id.strip():
            raise ValueError("CASH_MEMBER_ID_INVALID")
        cash_member_id = cash_member_id.strip()
        if cash_member_id != "cash_sleeve":
            raise ValueError("CASH_MEMBER_ID_INVALID")

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

    fee_bearing_member_ids: tuple[str, ...] | None = None
    if "fee_bearing_member_ids" in value:
        raw_fee_bearing_ids = value["fee_bearing_member_ids"]
        if (
            raw_fee_bearing_ids is None
            or isinstance(raw_fee_bearing_ids, (str, bytes))
            or not isinstance(raw_fee_bearing_ids, Sequence)
            or len(raw_fee_bearing_ids) == 0
        ):
            raise ValueError("FEE_BEARING_MEMBER_IDS_INVALID")
        valid_member_ids = {"cash_sleeve", "soxl_core", "tqqq_core"}
        parsed_fee_bearing_ids: list[str] = []
        for item in raw_fee_bearing_ids:
            if (
                not isinstance(item, str)
                or item not in valid_member_ids
                or item in parsed_fee_bearing_ids
            ):
                raise ValueError("FEE_BEARING_MEMBER_IDS_INVALID")
            parsed_fee_bearing_ids.append(item)
        fee_bearing_member_ids = tuple(sorted(parsed_fee_bearing_ids))

    member_costs_already_embedded = value.get("member_costs_already_embedded", True)
    if member_costs_already_embedded is not True:
        raise ValueError("MEMBER_GROSS_RETURNS_REQUIRED_TO_RECHARGE_MEMBER_COSTS")

    gaps: list[str] = []
    if rebalance_fee_bps is None:
        gaps.append("NEED_EXPLICIT_COMBO_REBALANCE_FEE_BPS")
    if rebalance_indices is None:
        gaps.append("NEED_EXPLICIT_REBALANCE_SCHEDULE_INDICES")
    if apply_risk_scaling and cash_member_id is None:
        gaps.append("NEED_CASH_MEMBER_ID_FOR_RISK_SCALING_RESIDUAL")

    options = {
        "apply_risk_scaling": apply_risk_scaling,
        "cash_member_id": cash_member_id,
        "rebalance_fee_bps": rebalance_fee_bps,
        "rebalance_indices": rebalance_indices,
        "fee_bearing_member_ids": fee_bearing_member_ids,
        "member_costs_already_embedded": True,
    }
    return options, tuple(gaps)


def _boundaries_for_comparison(
    *,
    pack_cash_policy: object,
    comparison: Mapping[str, object],
    path_options: Mapping[str, object] | None,
) -> dict[str, object]:
    boundaries = {
        **_BOUNDARY_MARKERS,
        "cash_return_policy": pack_cash_policy,
        "concentration_underlying_diagnosis": comparison["boundaries"].get(
            "concentration_underlying_diagnosis"
        ),
        "capital_path_requested": path_options is not None,
        "member_transaction_cost_scenario": MEMBER_COST_SCENARIO,
        "member_transaction_costs": "TYPED_BASELINE_ZERO_ASSUMED_NO_MEMBER_FEES",
    }
    if path_options is None:
        return boundaries

    comparison_boundaries = comparison["boundaries"]
    assert isinstance(comparison_boundaries, Mapping)
    boundaries["risk_scaling_applied_to_returns"] = comparison_boundaries.get(
        "risk_scaling_applied_to_returns",
        "NOT_APPLIED",
    )
    boundaries["rebalance_fee_reconstruction"] = comparison_boundaries.get(
        "rebalance_fee_reconstruction",
        "NOT_COMPUTED",
    )
    boundaries["capital_path_inputs"] = {
        "apply_risk_scaling": path_options["apply_risk_scaling"],
        "cash_member_id": path_options["cash_member_id"],
        "rebalance_fee_bps": path_options["rebalance_fee_bps"],
        "rebalance_indices": path_options["rebalance_indices"],
        "fee_bearing_member_ids": path_options["fee_bearing_member_ids"],
        "member_costs_already_embedded": True,
        "member_cost_scenario": MEMBER_COST_SCENARIO,
    }
    if "metrics_basis" in comparison_boundaries:
        boundaries["metrics_basis"] = comparison_boundaries["metrics_basis"]
    return boundaries


def evaluate_batch_a_existing_member_baselines(
    *,
    frozen_member_pack: Mapping[str, object] | None = None,
    capital_path_options: Mapping[str, object] | None = None,
    private_root: Any = None,
    _r3_readiness_reader: Any = None,
) -> dict[str, object]:
    """Gate Batch A fixed-budget research without inventing returns.

    ``frozen_member_pack`` must already carry aligned SOXL/TQQQ/cash daily
    strategy returns and full C2 comparability fields under the v2 schema.
    ``capital_path_options`` is optional and fail-closed: requesting a capital
    path without explicit ``rebalance_fee_bps`` and ``rebalance_indices`` parks
    with those gaps (no silent zero-fee / empty-schedule defaults).
    ``private_root`` / R3 readiness hooks are ignored: R3 has exited the
    active Batch A entry.
    """
    del private_root, _r3_readiness_reader

    gaps = [
        "BATCH_A_V2_FROZEN_MEMBER_PACK_REQUIRED",
        "LEGACY_R3_PRIVATE_ROOT_EXITED_ACTIVE_ENTRY",
        "LEGACY_COMBO_DERIVED_RETURNS_REJECTED",
        "PRICE_SNAPSHOT_V1_REJECTED",
        "MEMBER_PACK_V1_REJECTED",
    ]

    if frozen_member_pack is None:
        return _parked(
            "BATCH_A_FROZEN_COMPARABLE_INPUTS_UNAVAILABLE",
            "BATCH_A_C3_MEMBER_PACK_NOT_PROVIDED",
            "BATCH_A_REFUSE_TO_INVENT_RETURNS",
            "BATCH_A_LEGACY_R3_ENTRY_EXITED",
            evidence_gaps=gaps
            + [
                "NEED_EXPLICIT_ASSUMED_ZERO_USD_CASH_AND_ALIGNED_STRATEGY_SERIES",
            ],
        )

    try:
        path_options, path_gaps = _parse_capital_path_options(capital_path_options)
        pack = validate_batch_a_member_pack(frozen_member_pack)
        members = pack["members"]
        expected_cost_digest = frozen_cost_model_digest()
        if any(member["cost_model_digest"] != expected_cost_digest for member in members):
            return _parked(
                "BATCH_A_MEMBER_COST_MODEL_DIGEST_MISMATCH",
                evidence_gaps=["TYPED_BASELINE_ZERO_COST_MODEL_NOT_VERIFIED"],
                member_refs=[
                    {
                        "member_id": member["member_id"],
                        "evidence_digest": member["evidence_digest"],
                        "input_digest": member["input_digest"],
                    }
                    for member in members
                ],
            )
        if path_options is not None and path_gaps:
            parked = _parked(
                "BATCH_A_CAPITAL_PATH_INPUTS_INCOMPLETE",
                *path_gaps,
                evidence_gaps=list(path_gaps),
                member_refs=[
                    {
                        "member_id": member["member_id"],
                        "evidence_digest": member["evidence_digest"],
                        "input_digest": member["input_digest"],
                    }
                    for member in pack["members"]
                ],
            )
            boundaries = dict(parked["boundaries"])  # type: ignore[arg-type]
            boundaries["member_transaction_cost_scenario"] = MEMBER_COST_SCENARIO
            boundaries["member_transaction_costs"] = (
                "TYPED_BASELINE_ZERO_ASSUMED_NO_MEMBER_FEES"
            )
            boundaries["capital_path_requested"] = True
            boundaries["capital_path_inputs"] = {
                "apply_risk_scaling": path_options["apply_risk_scaling"],
                "cash_member_id": path_options["cash_member_id"],
                "rebalance_fee_bps": path_options["rebalance_fee_bps"],
                "rebalance_indices": path_options["rebalance_indices"],
                "fee_bearing_member_ids": path_options["fee_bearing_member_ids"],
                "member_costs_already_embedded": True,
                "member_cost_scenario": MEMBER_COST_SCENARIO,
                "input_gaps": list(path_gaps),
            }
            parked["boundaries"] = boundaries
            parked["capital_path_requested"] = True
            parked["capital_path_options"] = path_options
            parked["member_cost_scenario"] = MEMBER_COST_SCENARIO
            _set_evidence_digest(parked)
            return parked

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
        comparison_path_options = dict(path_options) if path_options is not None else None
        if (
            comparison_path_options is not None
            and comparison_path_options["fee_bearing_member_ids"] is None
        ):
            comparison_path_options.pop("fee_bearing_member_ids")
        comparison = compare_fixed_member_budget_baselines(
            members=members,
            baselines=baselines,
            asset_risk_specs=_default_risk_specs(),
            risk_policy=_default_risk_policy(),
            capital_path_options=comparison_path_options,
        )
        if comparison["status"] != "READY_RESEARCH_ONLY":
            return _parked(
                "BATCH_A_C3_COMPARISON_PARKED",
                *tuple(comparison.get("reason_codes") or ()),
                evidence_gaps=["C3_CONSUMER_REJECTED_FROZEN_PACK"],
                member_refs=list(comparison.get("member_refs") or ()),
            )
        payload: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            **_authority_fields(),
            "evidence_scope": EVIDENCE_SCOPE,
            "status": "READY_RESEARCH_ONLY",
            "reason_codes": (),
            "evidence_gaps": [],
            "cash_return_policy": pack["cash_return_policy"],
            "member_pack_schema": MEMBER_PACK_SCHEMA,
            "member_cost_scenario": MEMBER_COST_SCENARIO,
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
            "boundaries": _boundaries_for_comparison(
                pack_cash_policy=pack["cash_return_policy"],
                comparison=comparison,
                path_options=path_options,
            ),
            "batch_a_accepted": True,
            "capital_path_requested": path_options is not None,
            "capital_path_options": path_options,
            "policy_digest": comparison["policy_digest"],
            "input_digest": _digest_payload(
                {
                    "member_refs": comparison["member_refs"],
                    "cash_return_policy": pack["cash_return_policy"],
                    "pack_digest": pack["pack_digest"],
                    "member_cost_scenario": MEMBER_COST_SCENARIO,
                    "capital_path_options": path_options,
                }
            ),
        }
        _set_evidence_digest(payload)
        return payload
    except BatchAMemberPackError as exc:
        return _parked(
            "BATCH_A_FROZEN_PACK_INVALID",
            exc.code,
            evidence_gaps=["FROZEN_MEMBER_PACK_FAILED_VALIDATION"],
        )
    except (ValueError, TypeError, ArithmeticError) as exc:
        reason = str(exc) if str(exc) else "BATCH_A_PACK_VALIDATION_FAILED"
        return _parked(
            "BATCH_A_FROZEN_PACK_INVALID",
            reason,
            evidence_gaps=["FROZEN_MEMBER_PACK_FAILED_VALIDATION"],
        )


__all__ = [
    "ALLOWED_CASH_POLICIES",
    "EVIDENCE_SCOPE",
    "MEMBER_COST_SCENARIO",
    "REQUIRED_MEMBER_IDS",
    "SCHEMA_VERSION",
    "evaluate_batch_a_existing_member_baselines",
]
