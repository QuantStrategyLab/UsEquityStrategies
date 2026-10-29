"""One funded target budget, separate from frozen income-plan research bytes."""
from __future__ import annotations

import math
from collections.abc import Mapping

from .income_layer import IncomeLayerPlan


class IncomeBudgetError(ValueError):
    """The requested sleeve cannot be funded without consuming reserved cash."""


def allocate_income_budget(
    weights: Mapping[str, float],
    plan: IncomeLayerPlan,
    *,
    total_equity: float,
) -> dict[str, float]:
    """Fund an income plan from existing targets while preserving implicit cash.

    The caller owns this budget exactly once. An infeasible or malformed input
    must make the calling decision non-actionable; it is not a zero target.
    """
    def finite_nonnegative(value: object) -> float:
        if isinstance(value, bool):
            raise IncomeBudgetError("INCOME_BUDGET_INVALID")
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise IncomeBudgetError("INCOME_BUDGET_INVALID") from exc
        if not math.isfinite(number) or number < 0.0:
            raise IncomeBudgetError("INCOME_BUDGET_INVALID")
        return number

    if (not isinstance(weights, Mapping) or not isinstance(plan, IncomeLayerPlan)
            or not isinstance(plan.target_values, Mapping)):
        raise IncomeBudgetError("INCOME_BUDGET_INVALID")
    equity = finite_nonnegative(total_equity)
    if equity == 0.0:
        raise IncomeBudgetError("INCOME_BUDGET_INVALID")

    core: dict[str, float] = {}
    for symbol, value in weights.items():
        if not isinstance(symbol, str) or not symbol.strip():
            raise IncomeBudgetError("INCOME_BUDGET_INVALID")
        key = symbol.strip().upper()
        core[key] = core.get(key, 0.0) + finite_nonnegative(value)
    income: dict[str, float] = {}
    for symbol, value in plan.target_values.items():
        if not isinstance(symbol, str) or not symbol.strip():
            raise IncomeBudgetError("INCOME_BUDGET_INVALID")
        key = symbol.strip().upper()
        income[key] = income.get(key, 0.0) + finite_nonnegative(value) / equity
    locked = finite_nonnegative(plan.locked_value) / equity
    core_sum = math.fsum(core.values())
    if (core_sum > 1.0 + 1e-12
            or not math.isclose(math.fsum(income.values()), locked, rel_tol=1e-10, abs_tol=1e-12)):
        raise IncomeBudgetError("INCOME_BUDGET_INVALID")
    result = dict(core)
    deficits = {
        symbol: max(0.0, minimum - result.get(symbol, 0.0))
        for symbol, minimum in income.items()
    }
    needed = math.fsum(deficits.values())
    # Protect income minima first; only excess can fund a different minimum.
    donor_groups = (
        {symbol: weight for symbol, weight in core.items() if symbol not in income},
        {symbol: max(0.0, weight - income[symbol])
         for symbol, weight in core.items() if symbol in income},
    )
    if needed > math.fsum(value for group in donor_groups for value in group.values()) + 1e-12:
        raise IncomeBudgetError("INCOME_BUDGET_INSUFFICIENT")
    remaining = needed
    for donors in donor_groups:
        available = math.fsum(donors.values())
        used = min(remaining, available)
        if used > 0.0 and available > 0.0:
            for symbol, amount in donors.items():
                result[symbol] -= used * amount / available
        remaining = max(0.0, remaining - used)
    for symbol, deficit in deficits.items():
        if deficit > 0.0:
            result[symbol] = result.get(symbol, 0.0) + deficit

    return result


__all__ = ["IncomeBudgetError", "allocate_income_budget"]
