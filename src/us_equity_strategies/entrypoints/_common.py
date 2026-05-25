from __future__ import annotations

from collections.abc import Mapping

from quant_platform_kit.strategy_contracts import PositionTarget, StrategyContext
from us_equity_strategies.income_layer import (
    build_income_layer_plan,
    normalize_income_layer_allocations,
)


SAFE_HAVENS = {"BIL", "BOXX"}
INCOME_SYMBOLS = {"SCHD", "DGRO", "VIG", "VYM", "HDV", "SGOV", "SPYI", "QQQI"}
INCOME_LAYER_CONFIG_KEYS = {
    "income_layer_enabled",
    "income_layer_start_usd",
    "income_layer_max_ratio",
    "income_layer_ratio_mode",
    "income_layer_log_growth_factor",
    "income_layer_stress_drawdown_ratio",
    "income_layer_base_loss_budget_ratio",
    "income_layer_min_loss_budget_ratio",
    "income_layer_loss_budget_decay_per_double",
    "income_layer_qqqi_weight",
    "income_layer_spyi_weight",
    "income_layer_allocations",
}


def merge_runtime_config(manifest_default_config: Mapping[str, object], ctx: StrategyContext) -> dict[str, object]:
    config = dict(manifest_default_config)
    config.update(dict(ctx.runtime_config))
    return config


def pop_income_layer_config(config: dict[str, object]) -> dict[str, object]:
    return {key: config.pop(key) for key in INCOME_LAYER_CONFIG_KEYS if key in config}


def _portfolio_market_values(ctx: StrategyContext, symbols: tuple[str, ...]) -> dict[str, float]:
    market_values = {symbol: 0.0 for symbol in symbols}
    portfolio = ctx.portfolio
    if portfolio is None:
        return market_values
    for position in getattr(portfolio, "positions", ()) or ():
        symbol = str(getattr(position, "symbol", "") or "").strip().upper()
        if symbol in market_values:
            market_values[symbol] += float(getattr(position, "market_value", 0.0) or 0.0)
    return market_values


def apply_income_layer_to_weights(
    weights,
    *,
    income_layer_config: Mapping[str, object],
    ctx: StrategyContext,
    excluded_symbols=(),
) -> tuple[dict[str, float] | None, dict[str, object]]:
    if not weights:
        return weights, {}
    if ctx.portfolio is None:
        return dict(weights), {"income_layer_applied": False, "income_layer_skip_reason": "missing_portfolio"}

    total_equity = float(getattr(ctx.portfolio, "total_equity", 0.0) or 0.0)
    if total_equity <= 0.0:
        return dict(weights), {"income_layer_applied": False, "income_layer_skip_reason": "missing_total_equity"}

    fallback_allocations = (
        ("SPYI", income_layer_config.get("income_layer_spyi_weight", 0.0)),
        ("QQQI", income_layer_config.get("income_layer_qqqi_weight", 0.0)),
    )
    normalized_exclusions = {
        str(symbol or "").strip().upper()
        for symbol in (*dict(weights).keys(), *tuple(excluded_symbols or ()))
    }
    allocations = normalize_income_layer_allocations(
        income_layer_config.get("income_layer_allocations"),
        fallback_allocations=fallback_allocations,
        excluded_symbols=normalized_exclusions,
    )
    if not allocations:
        return dict(weights), {"income_layer_applied": False, "income_layer_skip_reason": "empty_allocations"}

    market_values = _portfolio_market_values(ctx, tuple(allocations))
    plan = build_income_layer_plan(
        total_equity_usd=total_equity,
        market_values=market_values,
        allocations=allocations,
        income_layer_enabled=income_layer_config.get("income_layer_enabled", True),
        income_layer_start_usd=income_layer_config.get("income_layer_start_usd", 0.0),
        income_layer_max_ratio=income_layer_config.get("income_layer_max_ratio", 0.0),
        income_layer_ratio_mode=income_layer_config.get("income_layer_ratio_mode", "linear_cap"),
        income_layer_log_growth_factor=income_layer_config.get("income_layer_log_growth_factor", 0.70),
        income_layer_stress_drawdown_ratio=income_layer_config.get("income_layer_stress_drawdown_ratio", 0.30),
        income_layer_base_loss_budget_ratio=income_layer_config.get("income_layer_base_loss_budget_ratio", 0.08),
        income_layer_min_loss_budget_ratio=income_layer_config.get("income_layer_min_loss_budget_ratio", 0.06),
        income_layer_loss_budget_decay_per_double=income_layer_config.get(
            "income_layer_loss_budget_decay_per_double",
            0.01,
        ),
    )
    locked_ratio = min(1.0, plan.locked_value / total_equity)
    core_scale = max(0.0, 1.0 - locked_ratio)
    target_weights = {
        str(symbol).strip().upper(): float(weight) * core_scale
        for symbol, weight in dict(weights).items()
        if str(symbol).strip().upper() not in plan.symbols
    }
    for symbol, target_value in plan.target_values.items():
        target_weight = float(target_value) / total_equity
        if abs(target_weight) > 1e-12:
            target_weights[symbol] = target_weight

    diagnostics = {
        "income_layer_applied": True,
        "income_layer_allocations": plan.allocations,
        "income_layer_symbols": plan.symbols,
        "income_layer_ratio": plan.ratio,
        "income_layer_value": plan.locked_value,
        "income_layer_core_scale": core_scale,
        **plan.diagnostics,
    }
    return target_weights, diagnostics


def get_current_holdings(ctx: StrategyContext):
    if "current_holdings" in ctx.state:
        return ctx.state["current_holdings"]
    if ctx.portfolio is not None and hasattr(ctx.portfolio, "positions"):
        return tuple(getattr(position, "symbol", position) for position in ctx.portfolio.positions)
    return ()


def require_market_data(ctx: StrategyContext, key: str):
    if key not in ctx.market_data:
        raise ValueError(f"StrategyContext.market_data missing required key: {key}")
    return ctx.market_data[key]


def require_portfolio(ctx: StrategyContext):
    if ctx.portfolio is None:
        raise ValueError("StrategyContext.portfolio is required for this entrypoint")
    return ctx.portfolio


def default_translator(key: str, **kwargs) -> str:
    if not kwargs:
        return key
    pairs = ", ".join(f"{name}={value}" for name, value in sorted(kwargs.items()))
    return f"{key}({pairs})"


def default_signal_text_fn(icon: str) -> str:
    return str(icon)


def weights_to_positions(weights, *, safe_haven: str | None = None) -> tuple[PositionTarget, ...]:
    if not weights:
        return ()
    positions: list[PositionTarget] = []
    for symbol, weight in sorted(weights.items()):
        role = None
        if safe_haven and symbol == safe_haven:
            role = "safe_haven"
        elif symbol in INCOME_SYMBOLS:
            role = "income"
        positions.append(PositionTarget(symbol=symbol, target_weight=float(weight), role=role))
    return tuple(positions)


def target_values_to_positions(target_values: Mapping[str, float]) -> tuple[PositionTarget, ...]:
    positions: list[PositionTarget] = []
    for symbol, value in sorted(target_values.items()):
        role = None
        if symbol in SAFE_HAVENS:
            role = "safe_haven"
        elif symbol in INCOME_SYMBOLS:
            role = "income"
        positions.append(PositionTarget(symbol=symbol, target_value=float(value), role=role))
    return tuple(positions)
