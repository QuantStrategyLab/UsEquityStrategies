from __future__ import annotations

from typing import Any

from us_equity_strategies.entrypoints._common import validate_production_option_context

from quant_platform_kit.common.strategy_contracts import (
    CallableStrategyEntrypoint,
    PositionTarget,
    StrategyContext,
    StrategyDecision,
)

from us_equity_strategies.combo_manifests import (
    us_equity_combo_core_manifest,
    us_equity_combo_leveraged_manifest,
    us_equity_combo_manifest,
)
from us_equity_strategies.combo_plugin_pipeline import (
    apply_income_layer,
    apply_option_overlay,
)
from us_equity_strategies.strategies import us_equity_combo
from us_equity_strategies.strategies import us_equity_combo_core
from us_equity_strategies.strategies import us_equity_combo_leveraged


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def merge_runtime_config(
    default_config: dict[str, object],
    ctx: StrategyContext,
) -> dict[str, object]:
    config = {**dict(default_config or {}), **dict(ctx.runtime_config or {})}
    validate_production_option_context(config, ctx)
    return config


def _get_current_holdings(ctx: StrategyContext) -> set[str]:
    if "current_holdings" in ctx.state:
        raw = ctx.state["current_holdings"]
        return set(raw.keys() if isinstance(raw, dict) else raw)
    if ctx.portfolio is None:
        return set()
    return {
        str(getattr(position, "symbol", "") or "").strip().upper()
        for position in getattr(ctx.portfolio, "positions", ())
        if float(getattr(position, "quantity", 0.0) or 0.0) != 0.0
    }


def _weights_to_positions(
    weights: dict[str, float] | None,
) -> tuple[PositionTarget, ...]:
    if not weights:
        return ()
    return tuple(
        PositionTarget(symbol=str(symbol), target_weight=float(weight), role="target")
        for symbol, weight in sorted(weights.items())
        if abs(float(weight)) > 1e-12
    )


def _require_market_data(ctx: StrategyContext, key: str) -> Any:
    if key not in ctx.market_data:
        raise ValueError(f"StrategyContext.market_data[{key!r}] is required")
    return ctx.market_data[key]


def _total_equity(ctx: StrategyContext) -> float:
    portfolio = ctx.portfolio
    if portfolio is None:
        return 0.0
    return float(getattr(portfolio, "total_equity", 0.0) or 0.0)


def _income_market_values(ctx: StrategyContext) -> dict[str, float]:
    values: dict[str, float] = {}
    if ctx.portfolio is not None:
        for position in getattr(ctx.portfolio, "positions", ()):
            symbol = str(getattr(position, "symbol", "")).strip().upper()
            values[symbol] = values.get(symbol, 0.0) + float(getattr(position, "market_value", 0.0))
    return values


# ---------------------------------------------------------------------------
# US Equity Combo
# ---------------------------------------------------------------------------


def evaluate_us_equity_combo(ctx: StrategyContext) -> StrategyDecision:
    config = merge_runtime_config(us_equity_combo_manifest.default_config, ctx)

    # --- Stage 1: compute raw sub-strategy weights ----------------------------
    weights, signal_desc, is_emergency, status_desc, metadata = (
        us_equity_combo.compute_signals(
            russell_snapshot=_require_market_data(ctx, "russell_snapshot"),
            current_holdings=_get_current_holdings(ctx),
            config=dict(config),
        )
    )

    total_equity = _total_equity(ctx)

    # --- Stage 2: income layer -----------------------------------------------
    income_diagnostics = apply_income_layer(weights, total_equity, config, market_values=_income_market_values(ctx))

    # --- Stage 3: option overlay ---------------------------------------------
    option_diagnostics = apply_option_overlay(
        weights,
        config,
        total_equity=total_equity,
    )

    diagnostics = {
        **metadata,
        "signal_description": signal_desc,
        "status_description": status_desc,
        "signal_source": us_equity_combo.SIGNAL_SOURCE,
        "actionable": True,
        "income_layer_diagnostics": income_diagnostics,
        "option_overlay_diagnostics": option_diagnostics,
    }
    risk_flags: tuple[str, ...] = ()
    if is_emergency:
        risk_flags += ("emergency_defense",)
    return StrategyDecision(
        positions=_weights_to_positions(weights),
        budgets=(),
        risk_flags=risk_flags,
        diagnostics=diagnostics,
    )


us_equity_combo_entrypoint = CallableStrategyEntrypoint(
    manifest=us_equity_combo_manifest,
    _evaluate=evaluate_us_equity_combo,
)


def evaluate_us_equity_combo_core(ctx: StrategyContext) -> StrategyDecision:
    config = merge_runtime_config(us_equity_combo_core_manifest.default_config, ctx)

    weights, signal_desc, is_emergency, status_desc, metadata = (
        us_equity_combo_core.compute_signals(
            russell_snapshot=_require_market_data(ctx, "russell_snapshot"),
            current_holdings=_get_current_holdings(ctx),
            config=dict(config),
        )
    )

    total_equity = _total_equity(ctx)
    income_diagnostics = apply_income_layer(weights, total_equity, config, market_values=_income_market_values(ctx))
    option_diagnostics = apply_option_overlay(
        weights,
        config,
        total_equity=total_equity,
    )
    diagnostics = {
        **metadata,
        "signal_description": signal_desc,
        "status_description": status_desc,
        "signal_source": us_equity_combo_core.SIGNAL_SOURCE,
        "actionable": True,
        "income_layer_diagnostics": income_diagnostics,
        "option_overlay_diagnostics": option_diagnostics,
    }
    risk_flags: tuple[str, ...] = ()
    if is_emergency:
        risk_flags += ("hard_defense",)
    return StrategyDecision(
        positions=_weights_to_positions(weights),
        budgets=(),
        risk_flags=risk_flags,
        diagnostics=diagnostics,
    )


us_equity_combo_core_entrypoint = CallableStrategyEntrypoint(
    manifest=us_equity_combo_core_manifest,
    _evaluate=evaluate_us_equity_combo_core,
)


def evaluate_us_equity_combo_leveraged(ctx: StrategyContext) -> StrategyDecision:
    config = merge_runtime_config(us_equity_combo_leveraged_manifest.default_config, ctx)

    # --- Stage 1: compute raw sub-strategy weights ----------------------------
    market_data = {"spy_above_ma200": True}
    raw_md = ctx.market_data.get("market_data")
    if isinstance(raw_md, dict):
        market_data.update(raw_md)
    weights, signal_desc, has_cash, status_desc, metadata = (
        us_equity_combo_leveraged.compute_signals(
            market_data=market_data,
            config=dict(config),
        )
    )

    total_equity = _total_equity(ctx)

    # --- Stage 2: income layer -----------------------------------------------
    income_diagnostics = apply_income_layer(weights, total_equity, config, market_values=_income_market_values(ctx))

    # --- Stage 3: option overlay ---------------------------------------------
    option_diagnostics = apply_option_overlay(
        weights,
        config,
        total_equity=total_equity,
    )

    diagnostics = {
        **metadata,
        "signal_description": signal_desc,
        "status_description": status_desc,
        "signal_source": us_equity_combo_leveraged.SIGNAL_SOURCE,
        "actionable": True,
        "income_layer_diagnostics": income_diagnostics,
        "option_overlay_diagnostics": option_diagnostics,
    }
    risk_flags: tuple[str, ...] = ()
    if has_cash:
        risk_flags += ("cash_parked",)
    regime_state = str(metadata.get("regime_state") or "")
    if regime_state == "hard_defense":
        risk_flags += ("ma200_risk_off",)
    elif regime_state == "soft_defense":
        risk_flags += ("soft_defense",)
    return StrategyDecision(
        positions=_weights_to_positions(weights),
        budgets=(),
        risk_flags=risk_flags,
        diagnostics=diagnostics,
    )


us_equity_combo_leveraged_entrypoint = CallableStrategyEntrypoint(
    manifest=us_equity_combo_leveraged_manifest,
    _evaluate=evaluate_us_equity_combo_leveraged,
)


__all__ = [
    "evaluate_us_equity_combo",
    "evaluate_us_equity_combo_core",
    "evaluate_us_equity_combo_leveraged",
    "us_equity_combo_core_entrypoint",
    "us_equity_combo_entrypoint",
    "us_equity_combo_leveraged_entrypoint",
]
