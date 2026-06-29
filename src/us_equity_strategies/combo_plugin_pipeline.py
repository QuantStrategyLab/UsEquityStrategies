"""Plugin pipeline for the US equity combo entrypoints.

Provides two plugin stages:
  1. apply_income_layer  — reserve X% of portfolio for income assets.
  2. apply_option_overlay — reserve budget for LEAPS / spread option intents.

Each stage takes a weights dict and returns an augmented result.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping

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

    if not enabled or total_equity < start_usd or max_ratio <= 0.0:
        return {
            "income_layer_enabled": enabled,
            "income_layer_active": False,
            "income_layer_ratio": 0.0,
            "income_layer_start_usd": start_usd,
            "income_layer_max_ratio": max_ratio,
        }

    raw_allocations = config.get("income_layer_allocations")
    if not raw_allocations:
        raw_allocations = INCOME_LAYER_DEFAULT_ALLOCATIONS

    allocations = normalize_income_layer_allocations(
        raw_allocations,
        fallback_allocations=INCOME_LAYER_DEFAULT_ALLOCATIONS,
    )

    # Current market values for income symbols (all zero at plan time —
    # we are building target weights from scratch).
    market_values: dict[str, float] = {sym: 0.0 for sym in allocations}

    plan = build_income_layer_plan(
        total_equity_usd=total_equity,
        market_values=market_values,
        allocations=allocations,
        income_layer_enabled=enabled,
        income_layer_start_usd=start_usd,
        income_layer_max_ratio=max_ratio,
    )

    if not allocations or plan.ratio <= 0.0:
        return {
            "income_layer_enabled": enabled,
            "income_layer_active": False,
            "income_layer_ratio": 0.0,
            "income_layer_start_usd": start_usd,
            "income_layer_max_ratio": max_ratio,
        }

    # Reduce existing weights proportionally to make room for income assets
    total_income_weight = plan.ratio
    existing_total = sum(max(0.0, float(w)) for w in weights.values())
    if existing_total > 0.0:
        scale = (1.0 - total_income_weight) / existing_total
        for sym in list(weights):
            weights[sym] = max(0.0, float(weights[sym]) * scale)

    # Add income allocation weights
    for sym, target_value in plan.target_values.items():
        income_weight = target_value / total_equity if total_equity > 0.0 else 0.0
        weights[sym] = max(0.0, income_weight)

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
    """Apply the option overlay stage, returning diagnostics and budget data.

    Parameters
    ----------
    weights :
        Current target-weight mapping (not modified by this stage — only
        diagnostics / budget intent metadata is produced).
    config :
        Runtime config dictionary.  Relevant keys:

        * ``option_overlay_enabled`` (bool, default ``True``)
        * ``option_growth_overlay_enabled`` (bool, default ``True``)
        * ``option_growth_overlay_recipe`` (str, default
          ``"spy_leaps_growth_v1"``)
        * ``option_growth_overlay_start_usd`` (float, default
          ``500000.0``)
        * ``option_growth_overlay_nav_budget_ratio`` (float, default
          ``0.015``)

    total_equity :
        Total portfolio equity in USD (used for budget calculation).
        If zero, it is inferred from ``config`` or left as zero.

    Returns
    -------
    dict[str, object]
        Diagnostics including ``option_overlay_active`` and, if active,
        an ``option_budget`` entry consumable by ``BudgetIntent``.
    """
    enabled = bool(config.get("option_overlay_enabled", True))
    growth_enabled = bool(config.get("option_growth_overlay_enabled", True))
    recipe = str(config.get("option_growth_overlay_recipe", "spy_leaps_growth_v1"))
    start_usd = float(config.get("option_growth_overlay_start_usd", 500000.0))
    nav_budget_ratio = float(config.get("option_growth_overlay_nav_budget_ratio", 0.015))

    if not enabled or not growth_enabled or not recipe:
        return {
            "option_overlay_enabled": enabled,
            "option_overlay_active": False,
        }

    equity = max(0.0, total_equity)
    if equity < start_usd:
        return {
            "option_overlay_enabled": enabled,
            "option_overlay_active": False,
            "option_overlay_below_start": True,
            "option_overlay_equity": equity,
            "option_overlay_start_usd": start_usd,
        }

    budget = equity * nav_budget_ratio

    return {
        "option_overlay_enabled": enabled,
        "option_overlay_active": True,
        "option_growth_overlay_enabled": growth_enabled,
        "option_growth_overlay_recipe": recipe,
        "option_growth_overlay_start_usd": start_usd,
        "option_growth_overlay_nav_budget_ratio": nav_budget_ratio,
        "option_overlay_equity": equity,
        "option_overlay_budget_usd": budget,
        "option_budget": {
            "name": "leaps_growth_budget",
            "symbol": None,
            "amount": budget,
            "unit": "quote_ccy",
            "purpose": f"growth_leaps_{recipe}",
        },
    }


__all__ = [
    "INCOME_LAYER_DEFAULT_ALLOCATIONS",
    "apply_income_layer",
    "apply_option_overlay",
]
