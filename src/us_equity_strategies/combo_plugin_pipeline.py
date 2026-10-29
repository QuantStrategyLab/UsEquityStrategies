"""Plugin pipeline for the US equity combo entrypoints.

Applies one cash-preserving income budget. The retired option seam only rejects
legacy configuration; it cannot create a production budget.

Each stage takes a weights dict and returns an augmented result.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping

from us_equity_strategies.option_overlay_retirement import validate_retired_option_inputs

from us_equity_strategies.income_budget import allocate_income_budget
from us_equity_strategies.income_layer import (
    build_income_layer_plan,
    normalize_income_layer_allocations,
)

# ---------------------------------------------------------------------------
# Income layer
# ---------------------------------------------------------------------------

INCOME_LAYER_DEFAULT_ALLOCATIONS: dict[str, float] = {
    "SCHD": 0.25,
    "DGRO": 0.25,
    "SGOV": 0.20,
    "SPYI": 0.15,
    "QQQI": 0.15,
}


def apply_income_layer(
    weights: MutableMapping[str, float],
    total_equity: float,
    config: Mapping[str, object],
    *,
    market_values: Mapping[str, float] | None = None,
) -> dict[str, object]:
    """Apply the income layer to *weights* in-place.

    Parameters
    ----------
    weights :
        Current target-weight mapping (mutated in-place).
    total_equity :
        Total portfolio equity in USD.
    config :
        Runtime config dictionary.  Relevant keys:

        * ``income_layer_enabled`` (bool, default ``True``)
        * ``income_layer_start_usd`` (float, default ``500000.0``)
        * ``income_layer_max_ratio`` (float, default ``0.25``)
        * ``income_layer_allocations`` (dict or list of pairs, optional)

    Returns
    -------
    dict[str, object]
        Diagnostics containing ``income_layer_ratio``,
        ``income_layer_plan`` and the resolved allocations.
    """
    enabled = bool(config.get("income_layer_enabled", True))
    start_usd = float(config.get("income_layer_start_usd", 500000.0))
    max_ratio = float(config.get("income_layer_max_ratio", 0.25))

    raw_allocations = config.get("income_layer_allocations")
    if not raw_allocations:
        raw_allocations = INCOME_LAYER_DEFAULT_ALLOCATIONS

    allocations = normalize_income_layer_allocations(
        raw_allocations,
        fallback_allocations=INCOME_LAYER_DEFAULT_ALLOCATIONS,
    )

    observed_values = dict(market_values or {})

    plan = build_income_layer_plan(
        total_equity_usd=total_equity,
        market_values=observed_values,
        allocations=allocations,
        income_layer_enabled=enabled,
        income_layer_start_usd=start_usd,
        income_layer_max_ratio=max_ratio,
    )

    if not allocations or plan.locked_value <= 0.0:
        return {
            "income_layer_enabled": enabled,
            "income_layer_active": False,
            "income_layer_ratio": 0.0,
            "income_layer_start_usd": start_usd,
            "income_layer_max_ratio": max_ratio,
        }

    targets = allocate_income_budget(weights, plan, total_equity=total_equity)
    weights.clear()
    weights.update(targets)

    return {
        "income_layer_enabled": enabled,
        "income_layer_active": True,
        "income_layer_ratio": plan.ratio,
        "income_layer_start_usd": start_usd,
        "income_layer_max_ratio": max_ratio,
        "income_layer_plan": {
            "ratio": plan.ratio,
            "current_value": plan.current_value,
            "desired_value": plan.desired_value,
            "locked_value": plan.locked_value,
            "add_value": plan.add_value,
            "target_values": dict(plan.target_values),
            "allocations": dict(plan.allocations),
        },
        "income_layer_allocations": dict(allocations),
    }


# ---------------------------------------------------------------------------
# Option overlay
# ---------------------------------------------------------------------------


def apply_option_overlay(
    weights: MutableMapping[str, float],
    config: Mapping[str, object],
    *,
    total_equity: float = 0.0,
) -> dict[str, object]:
    """Compatibility seam: retired options never reserve a production budget."""
    validate_retired_option_inputs(config)
    return {"option_overlay_enabled": False, "option_overlay_active": False,
            "option_overlay_status": "retired"}


__all__ = ["INCOME_LAYER_DEFAULT_ALLOCATIONS", "apply_income_layer", "apply_option_overlay"]
