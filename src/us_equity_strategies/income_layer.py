from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import numpy as np

INCOME_LAYER_RATIO_MODE_LOG_TOTAL_DRAWDOWN_BUDGET = "log_total_drawdown_budget"
INCOME_LAYER_RATIO_MODES = {
    INCOME_LAYER_RATIO_MODE_LOG_TOTAL_DRAWDOWN_BUDGET,
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
    income_layer_activation_band_ratio=0.0,
    income_layer_ratio_mode=INCOME_LAYER_RATIO_MODE_LOG_TOTAL_DRAWDOWN_BUDGET,
    income_layer_core_stress_drawdown_ratio=0.45,
    income_layer_income_stress_drawdown_ratio=0.08,
    income_layer_base_drawdown_budget_ratio=0.45,
    income_layer_min_drawdown_budget_ratio=0.25,
    income_layer_drawdown_budget_decay_per_double=0.05,
):
    ratio, _diagnostics = resolve_income_layer_ratio(
        total_equity_usd,
        income_layer_start_usd=income_layer_start_usd,
        income_layer_max_ratio=income_layer_max_ratio,
        income_layer_enabled=income_layer_enabled,
        income_layer_activation_band_ratio=income_layer_activation_band_ratio,
        income_layer_ratio_mode=income_layer_ratio_mode,
        income_layer_core_stress_drawdown_ratio=income_layer_core_stress_drawdown_ratio,
        income_layer_income_stress_drawdown_ratio=income_layer_income_stress_drawdown_ratio,
        income_layer_base_drawdown_budget_ratio=income_layer_base_drawdown_budget_ratio,
        income_layer_min_drawdown_budget_ratio=income_layer_min_drawdown_budget_ratio,
        income_layer_drawdown_budget_decay_per_double=income_layer_drawdown_budget_decay_per_double,
    )
    return ratio


def resolve_income_layer_ratio(
    total_equity_usd,
    *,
    income_layer_start_usd,
    income_layer_max_ratio,
    income_layer_enabled=True,
    income_layer_activation_band_ratio=0.0,
    income_layer_ratio_mode=INCOME_LAYER_RATIO_MODE_LOG_TOTAL_DRAWDOWN_BUDGET,
    income_layer_core_stress_drawdown_ratio=0.45,
    income_layer_income_stress_drawdown_ratio=0.08,
    income_layer_base_drawdown_budget_ratio=0.45,
    income_layer_min_drawdown_budget_ratio=0.25,
    income_layer_drawdown_budget_decay_per_double=0.05,
):
    enabled = as_bool(income_layer_enabled, default=True)
    mode = str(
        income_layer_ratio_mode or INCOME_LAYER_RATIO_MODE_LOG_TOTAL_DRAWDOWN_BUDGET
    ).strip().lower()
    if mode not in INCOME_LAYER_RATIO_MODES:
        modes = ", ".join(sorted(INCOME_LAYER_RATIO_MODES))
        raise ValueError(f"Unsupported income layer ratio mode: {mode!r}; expected one of {modes}")

    total = max(0.0, float(total_equity_usd or 0.0))
    start = max(0.0, float(income_layer_start_usd or 0.0))
    max_ratio = as_clamped_ratio(income_layer_max_ratio, default=0.0, upper=1.0)
    activation_band_ratio = as_clamped_ratio(
        income_layer_activation_band_ratio,
        default=0.0,
        upper=10.0,
    )
    activation_band_usd = start * activation_band_ratio if start > 0.0 else 0.0
    activation_end_usd = start + activation_band_usd
    if not enabled or total <= start or max_ratio <= 0.0:
        return 0.0, {
            "income_layer_enabled": enabled,
            "income_layer_activation_band_ratio": activation_band_ratio,
            "income_layer_activation_multiplier": 0.0,
            "income_layer_activation_end_usd": activation_end_usd,
            "income_layer_ratio_mode": mode,
            "income_layer_required_ratio": 0.0,
            "income_layer_ratio_before_activation": 0.0,
            "income_layer_account_drawdown_budget_ratio": 0.0,
            "income_layer_account_stress_drawdown_ratio": 0.0,
            "income_layer_drawdown_budget_gap_ratio": 0.0,
            "income_layer_drawdown_budget_met": True,
            "income_layer_core_stress_drawdown_ratio": 0.0,
            "income_layer_income_stress_drawdown_ratio": 0.0,
        }

    if start <= 0.0:
        doubles_since_start = 0.0
    else:
        doubles_since_start = max(0.0, float(np.log2(total / start)))

    activation_multiplier = 1.0
    if activation_band_usd > 0.0:
        activation_multiplier = max(0.0, min(1.0, (total - start) / activation_band_usd))

    core_stress_drawdown_ratio = as_clamped_ratio(
        income_layer_core_stress_drawdown_ratio,
        default=0.45,
        upper=1.0,
    )
    income_stress_drawdown_ratio = as_clamped_ratio(
        income_layer_income_stress_drawdown_ratio,
        default=0.08,
        upper=1.0,
    )
    base_drawdown_budget_ratio = as_clamped_ratio(
        income_layer_base_drawdown_budget_ratio,
        default=0.45,
        upper=1.0,
    )
    min_drawdown_budget_ratio = as_clamped_ratio(
        income_layer_min_drawdown_budget_ratio,
        default=min(base_drawdown_budget_ratio, 0.25),
        upper=1.0,
    )
    min_drawdown_budget_ratio = min(min_drawdown_budget_ratio, base_drawdown_budget_ratio)
    budget_decay = max(0.0, float(income_layer_drawdown_budget_decay_per_double or 0.0))
    account_drawdown_budget_ratio = max(
        min_drawdown_budget_ratio,
        base_drawdown_budget_ratio - budget_decay * doubles_since_start,
    )
    if core_stress_drawdown_ratio <= income_stress_drawdown_ratio:
        required_ratio = 0.0 if account_drawdown_budget_ratio >= core_stress_drawdown_ratio else max_ratio
    else:
        required_ratio = (
            (core_stress_drawdown_ratio - account_drawdown_budget_ratio)
            / (core_stress_drawdown_ratio - income_stress_drawdown_ratio)
        )
        required_ratio = max(0.0, required_ratio)
    ratio_before_activation = min(max_ratio, required_ratio)
    ratio = ratio_before_activation * activation_multiplier
    account_stress_drawdown_ratio = (
        ratio * income_stress_drawdown_ratio
        + (1.0 - ratio) * core_stress_drawdown_ratio
    )
    drawdown_budget_gap_ratio = max(0.0, account_stress_drawdown_ratio - account_drawdown_budget_ratio)

    return ratio, {
        "income_layer_enabled": enabled,
        "income_layer_activation_band_ratio": activation_band_ratio,
        "income_layer_activation_multiplier": activation_multiplier,
        "income_layer_activation_end_usd": activation_end_usd,
        "income_layer_ratio_mode": mode,
        "income_layer_required_ratio": required_ratio,
        "income_layer_ratio_before_activation": ratio_before_activation,
        "income_layer_account_drawdown_budget_ratio": account_drawdown_budget_ratio,
        "income_layer_account_stress_drawdown_ratio": account_stress_drawdown_ratio,
        "income_layer_drawdown_budget_gap_ratio": drawdown_budget_gap_ratio,
        "income_layer_drawdown_budget_met": drawdown_budget_gap_ratio <= 1e-12,
        "income_layer_core_stress_drawdown_ratio": core_stress_drawdown_ratio,
        "income_layer_income_stress_drawdown_ratio": income_stress_drawdown_ratio,
    }


def build_income_layer_plan(
    *,
    total_equity_usd,
    market_values: Mapping[str, float],
    allocations: Mapping[str, float],
    income_layer_start_usd,
    income_layer_max_ratio,
    income_layer_enabled=True,
    income_layer_activation_band_ratio=0.0,
    income_layer_ratio_mode=INCOME_LAYER_RATIO_MODE_LOG_TOTAL_DRAWDOWN_BUDGET,
    income_layer_core_stress_drawdown_ratio=0.45,
    income_layer_income_stress_drawdown_ratio=0.08,
    income_layer_base_drawdown_budget_ratio=0.45,
    income_layer_min_drawdown_budget_ratio=0.25,
    income_layer_drawdown_budget_decay_per_double=0.05,
) -> IncomeLayerPlan:
    income_symbols = tuple(allocations)
    ratio, diagnostics = resolve_income_layer_ratio(
        total_equity_usd,
        income_layer_start_usd=income_layer_start_usd,
        income_layer_max_ratio=income_layer_max_ratio,
        income_layer_enabled=income_layer_enabled,
        income_layer_activation_band_ratio=income_layer_activation_band_ratio,
        income_layer_ratio_mode=income_layer_ratio_mode,
        income_layer_core_stress_drawdown_ratio=income_layer_core_stress_drawdown_ratio,
        income_layer_income_stress_drawdown_ratio=income_layer_income_stress_drawdown_ratio,
        income_layer_base_drawdown_budget_ratio=income_layer_base_drawdown_budget_ratio,
        income_layer_min_drawdown_budget_ratio=income_layer_min_drawdown_budget_ratio,
        income_layer_drawdown_budget_decay_per_double=income_layer_drawdown_budget_decay_per_double,
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
    "INCOME_LAYER_RATIO_MODE_LOG_TOTAL_DRAWDOWN_BUDGET",
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
