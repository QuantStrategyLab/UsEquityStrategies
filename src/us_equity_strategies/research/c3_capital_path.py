"""C3 research capital-path helpers: drift, optional rebalance fees, risk scaling.

Member sleeve returns are treated as already net of member-level costs when the
shared ``cost_model_digest`` says so.  This module only charges **incremental**
combo-layer turnover when an explicit fee rate, rebalance schedule, and
fee-bearing member set are supplied.  Fee-bearing membership is whatever the
caller lists; cash or ETF names are not an exemption.  The charge is
``fee_rate * sum(|target - pre_trade_weight|)`` over that set: a pre-trade
notional approximation, not a self-financing trade solution.  It does not
invent holdings from net returns alone when those inputs are missing, and it
never authorizes execution.
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


def _fee_bearing_member_ids(
    value: object,
    *,
    member_ids: set[str],
    fee_rate: float | None,
) -> tuple[str, ...] | None:
    """Require an explicit member subset when a positive fee rate is charged."""

    if fee_rate is None:
        if value is not None:
            raise ValueError("FEE_BEARING_MEMBER_IDS_REQUIRE_EXPLICIT_FEE")
        return None
    if value is None:
        if fee_rate > 0.0:
            raise ValueError("FEE_BEARING_MEMBER_IDS_REQUIRED")
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) == 0:
        raise ValueError("FEE_BEARING_MEMBER_IDS_INVALID")
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or item not in member_ids or item in seen:
            raise ValueError("FEE_BEARING_MEMBER_IDS_INVALID")
        seen.add(item)
    return tuple(sorted(seen))


def simulate_fixed_budget_capital_path(
    *,
    member_ids: Sequence[str],
    member_returns: Mapping[str, Sequence[float]],
    target_weights: Mapping[str, float],
    rebalance_fee_bps: float | None,
    rebalance_indices: Sequence[int] | None,
    member_costs_already_embedded: bool = True,
    fee_bearing_member_ids: Sequence[str] | None = None,
) -> dict[str, object]:
    """Evolve unit NAV under fixed targets with optional fee-on-rebalance.

    Decision inputs are only the declared target, fee rate, fee-bearing member
    set, and rebalance index set (plus contemporaneous sleeve returns).  Future
    returns are never used to choose weights or fees.  A positive fee rate
    charges ``fee_rate`` times the sum of absolute pre-trade weight changes of
    ``fee_bearing_member_ids`` only.  That is not a self-financing solution:
    trade size is not reduced to fund the fee.
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

    fee_bearing_ids = _fee_bearing_member_ids(
        fee_bearing_member_ids,
        member_ids=ids,
        fee_rate=fee_rate,
    )

    if not member_costs_already_embedded:
        raise ValueError("MEMBER_GROSS_RETURNS_REQUIRED_TO_RECHARGE_MEMBER_COSTS")

    holdings = dict(target)
    nav = 1.0
    daily_returns: list[float] = []
    fee_fractions: list[float] = []
    one_way_turnovers: list[float] = []
    pre_trade_fee_notionals: list[float] = []
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
        fee_notional = 0.0
        if index in rebalance_set:
            turnover = one_way_turnover(drifted, target)
            if fee_bearing_ids is not None:
                fee_notional = math.fsum(
                    abs(target[member_id] - drifted[member_id])
                    for member_id in fee_bearing_ids
                )
            # Pre-trade |Δweight| of the explicit set. Not a self-financing solve.
            assert fee_rate is not None
            fee_fraction = fee_notional * fee_rate
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
        pre_trade_fee_notionals.append(fee_notional)
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
    if fee_rate is None:
        fee_basis = "NOT_APPLIED"
    elif fee_rate == 0.0:
        fee_basis = "ZERO_FEE_RATE_NO_COST"
    else:
        fee_basis = "PRE_TRADE_NOTIONAL_TURNOVER_APPROXIMATION"
    return {
        "daily_returns": tuple(daily_returns),
        "terminal_nav": nav,
        "weight_path": tuple(weight_path),
        "fee_fractions": tuple(fee_fractions),
        "one_way_turnovers": tuple(one_way_turnovers),
        "pre_trade_fee_notionals": tuple(pre_trade_fee_notionals),
        "fee_bearing_member_ids": fee_bearing_ids,
        "rebalance_fee_basis": fee_basis,
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


def _research_finite(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("CAPITAL_RISK_RATIO_PARAMETERS_INVALID")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError("CAPITAL_RISK_RATIO_PARAMETERS_INVALID")
    return numeric


def smooth_bounded_capital_risk_ratio(
    *,
    capital: float,
    a0: float,
    lower: float,
    upper: float,
    curvature: float,
) -> dict[str, object]:
    """Map continuous capital to a smooth risk ratio inside explicit bounds.

    Closed form, with every substantive parameter required:

        lower + (upper - lower) / (1 + (capital / a0) ** curvature)

    For ``curvature > 0`` the ratio is monotone non-increasing in capital,
    equals ``upper`` at zero capital, and approaches ``lower`` as capital
    grows.  ``upper <= 1`` so the function does not introduce leverage.
    There is no default and no historical fit.  The result is a function
    property only, not an optimal position.
    """

    capital_value = _research_finite(capital)
    a0_value = _research_finite(a0)
    lower_value = _research_finite(lower)
    upper_value = _research_finite(upper)
    curvature_value = _research_finite(curvature)
    if (
        capital_value < 0.0
        or a0_value <= 0.0
        or curvature_value <= 0.0
        or lower_value < 0.0
        or upper_value > 1.0
        or lower_value > upper_value
    ):
        raise ValueError("CAPITAL_RISK_RATIO_PARAMETERS_INVALID")
    if capital_value == 0.0:
        upper_fraction = 1.0
    else:
        log_scale = curvature_value * (math.log(capital_value) - math.log(a0_value))
        if log_scale >= 0.0:
            inverse_scale = math.exp(-log_scale)
            upper_fraction = inverse_scale / (1.0 + inverse_scale)
        else:
            scale = math.exp(log_scale)
            upper_fraction = 1.0 / (1.0 + scale)
    risk_ratio = lower_value + (upper_value - lower_value) * upper_fraction
    return {
        "capital": capital_value,
        "a0": a0_value,
        "lower": lower_value,
        "upper": upper_value,
        "curvature": curvature_value,
        "risk_ratio": risk_ratio,
        "risk_capital": capital_value * risk_ratio,
        "formula": "LOWER_PLUS_SPAN_OVER_ONE_PLUS_CAPITAL_OVER_A0_TO_CURVATURE",
        "research_only": True,
        "execution_authorized": False,
        "leverage_applied": False,
        "optimal_position": False,
        "interpretation": "FUNCTION_PROPERTY_ONLY_NOT_OPTIMAL_POSITION",
    }


def diagnose_capital_path_inputs(
    *,
    apply_risk_scaling: bool,
    rebalance_fee_bps: float | None,
    rebalance_indices: Sequence[int] | None,
    cash_member_id: str | None,
    has_risk_diagnosis: bool,
    fee_bearing_member_ids: Sequence[str] | None = None,
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
        positive_fee = (
            isinstance(rebalance_fee_bps, (int, float))
            and not isinstance(rebalance_fee_bps, bool)
            and float(rebalance_fee_bps) > 0.0
        )
        if positive_fee and fee_bearing_member_ids is None:
            gaps.append("NEED_EXPLICIT_FEE_BEARING_MEMBER_IDS")
    elif apply_risk_scaling is False:
        gaps.append("CAPITAL_PATH_OPTIONAL_INPUTS_NOT_REQUESTED")
    return tuple(gaps)


__all__ = [
    "diagnose_capital_path_inputs",
    "one_way_turnover",
    "scale_member_budgets_to_cash",
    "simulate_fixed_budget_capital_path",
    "smooth_bounded_capital_risk_ratio",
]
