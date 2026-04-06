from __future__ import annotations

from collections.abc import Mapping

from quant_platform_kit.strategy_contracts import PositionTarget, StrategyContext


SAFE_HAVENS = {"BIL", "BOXX"}
INCOME_SYMBOLS = {"SPYI", "QQQI"}


def merge_runtime_config(manifest_default_config: Mapping[str, object], ctx: StrategyContext) -> dict[str, object]:
    config = dict(manifest_default_config)
    config.update(dict(ctx.runtime_config))
    return config


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
        role = "safe_haven" if safe_haven and symbol == safe_haven else None
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
