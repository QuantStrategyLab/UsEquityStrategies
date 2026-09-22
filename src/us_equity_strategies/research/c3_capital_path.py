"""C3 research capital-path helpers: drift, optional rebalance fees, risk scaling.

Member sleeve returns are treated as already net of member-level costs when the
shared ``cost_model_digest`` says so.  This module only charges **incremental**
combo-layer turnover when an explicit fee rate and rebalance schedule are
supplied.  It does not invent holdings from net returns alone when those
inputs are missing, and it never authorizes execution.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

_EPSILON = 1e-12


def one_way_turnover(
    current: Mapping[str, float], target: Mapping[str, float]
) -> float:
    """Half L1 weight distance (standard one-way turnover)."""

    keys = set(current) | set(target)
    return 0.5 * math.fsum(
        abs(target.get(key, 0.0) - current.get(key, 0.0)) for key in keys
    )


def scale_member_budgets_to_cash(
    *,
    budgets: Mapping[str, float],
    risk_scalar: float,
    cash_member_id: str,
) -> dict[str, float]:
    """Proportionally shrink non-cash sleeves; residual goes to ``cash_member_id``."""

    if cash_member_id not in budgets:
        raise ValueError("CASH_MEMBER_ID_MISSING")
    if (
        not math.isfinite(risk_scalar)
        or risk_scalar < 0.0
        or risk_scalar > 1.0 + _EPSILON
    ):
        raise ValueError("RISK_SCALAR_INVALID")
    scalar = min(1.0, float(risk_scalar))
    scaled: dict[str, float] = {}
    residual = 0.0
    for member_id, weight in budgets.items():
        if member_id == cash_member_id:
            continue
        reduced = weight * scalar
        scaled[member_id] = reduced
        residual += weight - reduced
    scaled[cash_member_id] = budgets[cash_member_id] + residual
    total = math.fsum(scaled.values())
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=_EPSILON):
        raise ValueError("SCALED_BUDGETS_NOT_FULLY_FUNDED")
    return dict(sorted(scaled.items()))


def simulate_fixed_budget_capital_path(
    *,
    member_ids: Sequence[str],
    member_returns: Mapping[str, Sequence[float]],
    target_weights: Mapping[str, float],
    rebalance_fee_bps: float | None,
    rebalance_indices: Sequence[int] | None,
    member_costs_already_embedded: bool = True,
) -> dict[str, object]:
    """Evolve unit NAV under fixed targets with optional fee-on-rebalance.

    Decision inputs are only the declared target, fee rate, and rebalance index
    set (plus contemporaneous sleeve returns).  Future returns are never used
    to choose weights or fees.
    """
    ids = set(member_ids)
    if not member_ids or ids != set(target_weights) or ids != set(member_returns):
        raise ValueError("CAPITAL_PATH_MEMBER_SET_MISMATCH")
    count = len(next(iter(member_returns.values())))
    if count < 1:
        raise ValueError("CAPITAL_PATH_RETURNS_EMPTY")
    for member_id in member_ids:
        series = member_returns[member_id]
        if len(series) != count:
            raise ValueError("CAPITAL_PATH_RETURN_LENGTH_MISMATCH")
        for value in series:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError("CAPITAL_PATH_RETURN_INVALID")
            numeric = float(value)
            if not math.isfinite(numeric) or numeric <= -1.0:
                raise ValueError("CAPITAL_PATH_RETURN_INVALID")
    target = {member_id: float(target_weights[member_id]) for member_id in member_ids}
    if any(weight < 0.0 for weight in target.values()):
        raise ValueError("CAPITAL_PATH_TARGET_WEIGHT_INVALID")
    if not math.isclose(math.fsum(target.values()), 1.0, rel_tol=0.0, abs_tol=_EPSILON):
        raise ValueError("CAPITAL_PATH_TARGET_NOT_FULLY_FUNDED")

    fee_rate: float | None
    if rebalance_fee_bps is None:
        fee_rate = None
    else:
        if isinstance(rebalance_fee_bps, bool) or not isinstance(
            rebalance_fee_bps, (int, float)
        ):
            raise ValueError("REBALANCE_FEE_BPS_INVALID")
        fee_rate = float(rebalance_fee_bps) / 10_000.0
        if not math.isfinite(fee_rate) or fee_rate < 0.0:
            raise ValueError("REBALANCE_FEE_BPS_INVALID")

    rebalance_set: set[int]
    if rebalance_indices is None:
        rebalance_set = set()
    else:
        rebalance_set = set()
        for index in rebalance_indices:
            if isinstance(index, bool) or not isinstance(index, int):
                raise TypeError("REBALANCE_INDEX_INVALID")
            if index < 0 or index >= count:
                raise ValueError("REBALANCE_INDEX_INVALID")
            rebalance_set.add(index)
        if fee_rate is None and rebalance_set:
            raise ValueError("REBALANCE_FEE_BPS_REQUIRED_FOR_REBALANCE")
        if fee_rate is not None and not rebalance_set and fee_rate > 0.0:
            raise ValueError("REBALANCE_SCHEDULE_REQUIRED_FOR_POSITIVE_FEE")

    if not member_costs_already_embedded:
        raise ValueError("MEMBER_GROSS_RETURNS_REQUIRED_TO_RECHARGE_MEMBER_COSTS")

    holdings = dict(target)
    nav = 1.0
    daily_returns: list[float] = []
    fee_fractions: list[float] = []
    one_way_turnovers: list[float] = []
    weight_path: list[dict[str, float]] = []
    total_fee_drag = 0.0

    for index in range(count):
        prev_nav = nav
        grown = {
            member_id: holdings[member_id]
            * (1.0 + float(member_returns[member_id][index]))
            for member_id in member_ids
        }
        nav_pre = math.fsum(grown.values())
        if not math.isfinite(nav_pre) or nav_pre <= 0.0:
            raise ValueError("CAPITAL_PATH_NAV_INVALID")
        drifted = {member_id: grown[member_id] / nav_pre for member_id in member_ids}

        fee_fraction = 0.0
        turnover = 0.0
        if index in rebalance_set:
            turnover = one_way_turnover(drifted, target)
            # Two-sided notional traded ≈ 2 * one-way; fee_bps is per traded unit.
            assert fee_rate is not None
            fee_fraction = (2.0 * turnover) * fee_rate
            if fee_fraction >= 1.0:
                raise ValueError("CAPITAL_PATH_FEE_CONSUMES_NAV")
            nav = nav_pre * (1.0 - fee_fraction)
            holdings = {member_id: target[member_id] * nav for member_id in member_ids}
            end_weights = dict(target)
        else:
            nav = nav_pre
            holdings = grown
            end_weights = drifted

        daily_return = nav / prev_nav - 1.0
        if not math.isfinite(daily_return) or daily_return <= -1.0:
            raise ValueError("CAPITAL_PATH_RETURN_INVALID")
        daily_returns.append(daily_return)
        fee_fractions.append(fee_fraction)
        one_way_turnovers.append(turnover)
        weight_path.append(dict(sorted(end_weights.items())))
        total_fee_drag += fee_fraction

    fees_computed = fee_rate is not None
    if fee_rate is None:
        fee_reconstruction = "NOT_COMPUTED_MISSING_FEE_BPS_OR_SCHEDULE"
    elif rebalance_set:
        fee_reconstruction = "COMPUTED_EXPLICIT_BPS_AND_SCHEDULE"
    else:
        fee_reconstruction = "COMPUTED_EXPLICIT_ZERO_OR_NO_REBALANCE_EVENTS"
    fees_applied = bool(
        fees_computed and rebalance_set and (fee_rate or 0.0) > 0.0
    )
    return {
        "daily_returns": tuple(daily_returns),
        "terminal_nav": nav,
        "weight_path": tuple(weight_path),
        "fee_fractions": tuple(fee_fractions),
        "one_way_turnovers": tuple(one_way_turnovers),
        "total_fee_fraction_sum": total_fee_drag,
        "final_weights": dict(sorted(weight_path[-1].items())),
        "target_weights": dict(sorted(target.items())),
        "rebalance_fee_bps": None if fee_rate is None else fee_rate * 10_000.0,
        "rebalance_indices": tuple(sorted(rebalance_set)),
        "member_costs_already_embedded": True,
        "member_costs_recharged": False,
        "risk_scaling_applied": False,
        "rebalance_fees_applied": fees_applied,
        "rebalance_fee_reconstruction": fee_reconstruction,
    }


def diagnose_capital_path_inputs(
    *,
    apply_risk_scaling: bool,
    rebalance_fee_bps: float | None,
    rebalance_indices: Sequence[int] | None,
    cash_member_id: str | None,
    has_risk_diagnosis: bool,
) -> tuple[str, ...]:
    """Return missing-input gap codes without inventing a fee formula."""

    gaps: list[str] = []
    if apply_risk_scaling:
        if not has_risk_diagnosis:
            gaps.append("NEED_RISK_POLICY_AND_TARGET_WEIGHTS_FOR_SCALING")
        if cash_member_id is None:
            gaps.append("NEED_CASH_MEMBER_ID_FOR_RISK_SCALING_RESIDUAL")
    wants_fees = rebalance_fee_bps is not None or rebalance_indices is not None
    if wants_fees:
        if rebalance_fee_bps is None:
            gaps.append("NEED_EXPLICIT_COMBO_REBALANCE_FEE_BPS")
        if rebalance_indices is None:
            gaps.append("NEED_EXPLICIT_REBALANCE_SCHEDULE_INDICES")
    elif apply_risk_scaling is False:
        gaps.append("CAPITAL_PATH_OPTIONAL_INPUTS_NOT_REQUESTED")
    return tuple(gaps)


__all__ = [
    "diagnose_capital_path_inputs",
    "one_way_turnover",
    "scale_member_budgets_to_cash",
    "simulate_fixed_budget_capital_path",
]
