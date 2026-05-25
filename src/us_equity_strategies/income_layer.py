from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import numpy as np

INCOME_LAYER_RATIO_MODE_LINEAR_CAP = "linear_cap"
INCOME_LAYER_RATIO_MODE_LOG_CAP = "log_cap"
INCOME_LAYER_RATIO_MODE_LOG_LOSS_BUDGET = "log_loss_budget"
INCOME_LAYER_RATIO_MODES = {
    INCOME_LAYER_RATIO_MODE_LINEAR_CAP,
    INCOME_LAYER_RATIO_MODE_LOG_CAP,
    INCOME_LAYER_RATIO_MODE_LOG_LOSS_BUDGET,
}


@dataclass(frozen=True)
class IncomeLayerPlan:
    allocations: dict[str, float]
    symbols: tuple[str, ...]
    ratio: float
    current_value: float
    desired_value: float
    locked_value: float
    add_value: float
    target_values: dict[str, float]
    diagnostics: dict[str, object]


def as_float_or_none(value):
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(result):
        return None
    return result


def as_clamped_ratio(value, *, default=0.0, upper=1.0):
    result = as_float_or_none(value)
    if result is None:
        result = float(default)
    return max(0.0, min(float(upper), result))


def as_bool(value, *, default=True) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _allocation_items(values) -> Iterable[tuple[object, object]]:
    if not values:
        return ()
    if isinstance(values, Mapping):
        return values.items()
    return values


def normalize_income_layer_allocations(
    allocations,
    *,
    fallback_allocations=(),
    excluded_symbols: Iterable[str] = (),
) -> dict[str, float]:
    excluded = {str(symbol).strip().upper() for symbol in excluded_symbols}
    raw_items = []
    for item in _allocation_items(allocations):
        try:
            symbol, weight = item
        except (TypeError, ValueError):
            continue
        symbol_text = str(symbol or "").strip().upper()
        weight_value = as_float_or_none(weight)
        if not symbol_text or symbol_text in excluded or weight_value is None or weight_value <= 0.0:
            continue
        raw_items.append((symbol_text, float(weight_value)))

    if not raw_items:
        for item in _allocation_items(fallback_allocations):
            try:
                symbol, weight = item
            except (TypeError, ValueError):
                continue
            symbol_text = str(symbol or "").strip().upper()
            weight_value = as_float_or_none(weight)
            if not symbol_text or symbol_text in excluded or weight_value is None or weight_value <= 0.0:
                continue
            raw_items.append((symbol_text, float(weight_value)))

    merged: dict[str, float] = {}
    for symbol, weight in raw_items:
        merged[symbol] = merged.get(symbol, 0.0) + float(weight)

    total = sum(merged.values())
    if total <= 0.0:
        return {}
    return {symbol: weight / total for symbol, weight in merged.items()}


def get_income_layer_ratio(
    total_equity_usd,
    *,
    income_layer_start_usd,
    income_layer_max_ratio,
    income_layer_enabled=True,
    income_layer_ratio_mode=INCOME_LAYER_RATIO_MODE_LINEAR_CAP,
    income_layer_log_growth_factor=0.70,
    income_layer_stress_drawdown_ratio=0.30,
    income_layer_base_loss_budget_ratio=0.08,
    income_layer_min_loss_budget_ratio=0.06,
    income_layer_loss_budget_decay_per_double=0.01,
):
    ratio, _diagnostics = resolve_income_layer_ratio(
        total_equity_usd,
        income_layer_start_usd=income_layer_start_usd,
        income_layer_max_ratio=income_layer_max_ratio,
        income_layer_enabled=income_layer_enabled,
        income_layer_ratio_mode=income_layer_ratio_mode,
        income_layer_log_growth_factor=income_layer_log_growth_factor,
        income_layer_stress_drawdown_ratio=income_layer_stress_drawdown_ratio,
        income_layer_base_loss_budget_ratio=income_layer_base_loss_budget_ratio,
        income_layer_min_loss_budget_ratio=income_layer_min_loss_budget_ratio,
        income_layer_loss_budget_decay_per_double=income_layer_loss_budget_decay_per_double,
    )
    return ratio


def resolve_income_layer_ratio(
    total_equity_usd,
    *,
    income_layer_start_usd,
    income_layer_max_ratio,
    income_layer_enabled=True,
    income_layer_ratio_mode=INCOME_LAYER_RATIO_MODE_LINEAR_CAP,
    income_layer_log_growth_factor=0.70,
    income_layer_stress_drawdown_ratio=0.30,
    income_layer_base_loss_budget_ratio=0.08,
    income_layer_min_loss_budget_ratio=0.06,
    income_layer_loss_budget_decay_per_double=0.01,
):
    enabled = as_bool(income_layer_enabled, default=True)
    mode = str(income_layer_ratio_mode or INCOME_LAYER_RATIO_MODE_LINEAR_CAP).strip().lower()
    if mode not in INCOME_LAYER_RATIO_MODES:
        modes = ", ".join(sorted(INCOME_LAYER_RATIO_MODES))
        raise ValueError(f"Unsupported income layer ratio mode: {mode!r}; expected one of {modes}")

    total = max(0.0, float(total_equity_usd or 0.0))
    start = max(0.0, float(income_layer_start_usd or 0.0))
    max_ratio = as_clamped_ratio(income_layer_max_ratio, default=0.0, upper=1.0)
    if not enabled or total <= start or max_ratio <= 0.0:
        return 0.0, {
            "income_layer_enabled": enabled,
            "income_layer_ratio_mode": mode,
            "income_layer_log_ratio": 0.0,
            "income_layer_loss_budget_ratio": 0.0,
            "income_layer_loss_budget_cap_ratio": 0.0,
            "income_layer_stress_drawdown_ratio": 0.0,
        }

    if start <= 0.0:
        linear_ratio = max_ratio
        doubles_since_start = 0.0
    elif total <= (start * 2):
        linear_ratio = float(
            np.interp(
                total,
                [start, start * 2],
                [0.0, max_ratio],
            )
        )
        doubles_since_start = max(0.0, float(np.log2(total / start)))
    else:
        linear_ratio = max_ratio
        doubles_since_start = max(0.0, float(np.log2(total / start)))

    if mode == INCOME_LAYER_RATIO_MODE_LINEAR_CAP:
        return linear_ratio, {
            "income_layer_enabled": enabled,
            "income_layer_ratio_mode": mode,
            "income_layer_log_ratio": linear_ratio,
            "income_layer_loss_budget_ratio": np.nan,
            "income_layer_loss_budget_cap_ratio": max_ratio,
            "income_layer_stress_drawdown_ratio": np.nan,
        }

    growth_factor = max(0.0, float(income_layer_log_growth_factor or 0.0))
    log_ratio = max_ratio * (1.0 - float(np.exp(-growth_factor * doubles_since_start)))
    if mode == INCOME_LAYER_RATIO_MODE_LOG_CAP:
        return min(max_ratio, log_ratio), {
            "income_layer_enabled": enabled,
            "income_layer_ratio_mode": mode,
            "income_layer_log_ratio": log_ratio,
            "income_layer_loss_budget_ratio": np.nan,
            "income_layer_loss_budget_cap_ratio": max_ratio,
            "income_layer_stress_drawdown_ratio": np.nan,
        }

    stress_drawdown_ratio = as_clamped_ratio(
        income_layer_stress_drawdown_ratio,
        default=0.30,
        upper=1.0,
    )
    base_loss_budget_ratio = as_clamped_ratio(
        income_layer_base_loss_budget_ratio,
        default=0.08,
        upper=1.0,
    )
    min_loss_budget_ratio = as_clamped_ratio(
        income_layer_min_loss_budget_ratio,
        default=min(base_loss_budget_ratio, 0.06),
        upper=1.0,
    )
    min_loss_budget_ratio = min(min_loss_budget_ratio, base_loss_budget_ratio)
    loss_budget_decay = max(0.0, float(income_layer_loss_budget_decay_per_double or 0.0))
    loss_budget_ratio = max(
        min_loss_budget_ratio,
        base_loss_budget_ratio - loss_budget_decay * doubles_since_start,
    )
    loss_budget_cap_ratio = max_ratio
    if stress_drawdown_ratio > 0.0:
        loss_budget_cap_ratio = min(max_ratio, loss_budget_ratio / stress_drawdown_ratio)

    return min(max_ratio, log_ratio, loss_budget_cap_ratio), {
        "income_layer_enabled": enabled,
        "income_layer_ratio_mode": mode,
        "income_layer_log_ratio": log_ratio,
        "income_layer_loss_budget_ratio": loss_budget_ratio,
        "income_layer_loss_budget_cap_ratio": loss_budget_cap_ratio,
        "income_layer_stress_drawdown_ratio": stress_drawdown_ratio,
    }


def build_income_layer_plan(
    *,
    total_equity_usd,
    market_values: Mapping[str, float],
    allocations: Mapping[str, float],
    income_layer_start_usd,
    income_layer_max_ratio,
    income_layer_enabled=True,
    income_layer_ratio_mode=INCOME_LAYER_RATIO_MODE_LINEAR_CAP,
    income_layer_log_growth_factor=0.70,
    income_layer_stress_drawdown_ratio=0.30,
    income_layer_base_loss_budget_ratio=0.08,
    income_layer_min_loss_budget_ratio=0.06,
    income_layer_loss_budget_decay_per_double=0.01,
) -> IncomeLayerPlan:
    income_symbols = tuple(allocations)
    ratio, diagnostics = resolve_income_layer_ratio(
        total_equity_usd,
        income_layer_start_usd=income_layer_start_usd,
        income_layer_max_ratio=income_layer_max_ratio,
        income_layer_enabled=income_layer_enabled,
        income_layer_ratio_mode=income_layer_ratio_mode,
        income_layer_log_growth_factor=income_layer_log_growth_factor,
        income_layer_stress_drawdown_ratio=income_layer_stress_drawdown_ratio,
        income_layer_base_loss_budget_ratio=income_layer_base_loss_budget_ratio,
        income_layer_min_loss_budget_ratio=income_layer_min_loss_budget_ratio,
        income_layer_loss_budget_decay_per_double=income_layer_loss_budget_decay_per_double,
    )
    current_value = sum(float(market_values.get(symbol, 0.0)) for symbol in income_symbols)
    desired_value = float(total_equity_usd or 0.0) * ratio
    locked_value = max(current_value, desired_value)
    add_value = max(0.0, locked_value - current_value)
    targets = {
        symbol: float(market_values.get(symbol, 0.0)) + (add_value * weight)
        for symbol, weight in allocations.items()
    }
    return IncomeLayerPlan(
        allocations=dict(allocations),
        symbols=income_symbols,
        ratio=ratio,
        current_value=current_value,
        desired_value=desired_value,
        locked_value=locked_value,
        add_value=add_value,
        target_values=targets,
        diagnostics=diagnostics,
    )


__all__ = [
    "INCOME_LAYER_RATIO_MODE_LINEAR_CAP",
    "INCOME_LAYER_RATIO_MODE_LOG_CAP",
    "INCOME_LAYER_RATIO_MODE_LOG_LOSS_BUDGET",
    "INCOME_LAYER_RATIO_MODES",
    "IncomeLayerPlan",
    "as_bool",
    "as_clamped_ratio",
    "as_float_or_none",
    "build_income_layer_plan",
    "get_income_layer_ratio",
    "normalize_income_layer_allocations",
    "resolve_income_layer_ratio",
]
