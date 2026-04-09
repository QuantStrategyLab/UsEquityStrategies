from __future__ import annotations

from quant_platform_kit.strategy_contracts import CallableStrategyEntrypoint, StrategyDecision, StrategyContext

from us_equity_strategies.manifests import (
    global_etf_rotation_manifest,
    qqq_tech_enhancement_manifest,
    russell_1000_multi_factor_defensive_manifest,
    soxl_soxx_trend_income_manifest,
    tqqq_growth_income_manifest,
)
from us_equity_strategies.strategies import (
    global_etf_rotation as legacy_global_etf_rotation,
    hybrid_growth_income as legacy_hybrid_growth_income,
    russell_1000_multi_factor_defensive as legacy_russell,
    semiconductor_rotation_income as legacy_semiconductor,
    tech_pullback_cash_buffer as legacy_tech_pullback,
)

from ._common import (
    default_signal_text_fn,
    default_translator,
    get_current_holdings,
    merge_runtime_config,
    require_market_data,
    require_portfolio,
    target_values_to_positions,
    weights_to_positions,
)


"""Unified strategy entrypoints built as adapters over legacy implementations."""


def evaluate_global_etf_rotation(ctx: StrategyContext) -> StrategyDecision:
    config = merge_runtime_config(global_etf_rotation_manifest.default_config, ctx)
    config["ranking_pool"] = list(config.get("ranking_pool", ()))
    config["canary_assets"] = list(config.get("canary_assets", ()))
    market_history = require_market_data(ctx, "market_history")
    weights, signal_desc, is_emergency, canary_str = legacy_global_etf_rotation.compute_signals(
        ctx.capabilities.get("broker_client"),
        get_current_holdings(ctx),
        get_historical_close=market_history,
        translator=config.pop("translator", default_translator),
        pacing_sec=float(config.pop("pacing_sec", 0.0)),
        **config,
    )
    diagnostics = {
        "signal_description": signal_desc,
        "canary_status": canary_str,
        "actionable": weights is not None,
    }
    risk_flags = ("emergency",) if is_emergency else ()
    return StrategyDecision(
        positions=weights_to_positions(weights, safe_haven=str(config.get("safe_haven", "BIL"))),
        risk_flags=risk_flags,
        diagnostics=diagnostics,
    )


GLOBAL_ETF_ROTATION_LEGACY_DOC = "Legacy compute_signals adapter retained for platform compatibility."
legacy_global_etf_rotation.compute_signals.__doc__ = (
    (legacy_global_etf_rotation.compute_signals.__doc__ or "").strip() + "\n\nLegacy adapter: prefer us_equity_strategies entrypoints for new integrations."
).strip()


def evaluate_hybrid_growth_income(ctx: StrategyContext) -> StrategyDecision:
    config = merge_runtime_config(tqqq_growth_income_manifest.default_config, ctx)
    config.pop("managed_symbols", None)
    config.pop("benchmark_symbol", None)
    plan = legacy_hybrid_growth_income.build_rebalance_plan(
        require_market_data(ctx, "benchmark_history"),
        require_portfolio(ctx),
        signal_text_fn=config.pop("signal_text_fn", default_signal_text_fn),
        translator=config.pop("translator", default_translator),
        **config,
    )
    diagnostics = {
        "signal_display": plan["sig_display"],
        "dashboard": plan["dashboard"],
        "threshold": plan["threshold"],
        "reserved": plan["reserved"],
        "qqq_price": plan["qqq_p"],
        "ma200": plan["ma200"],
        "exit_line": plan["exit_line"],
        "real_buying_power": plan["real_buying_power"],
        "total_equity": plan["total_equity"],
        "execution_annotations": {
            "trade_threshold_value": plan["threshold"],
            "reserved_cash": plan["reserved"],
            "signal_display": plan["sig_display"],
            "dashboard_text": plan["dashboard"],
            "benchmark_symbol": "QQQ",
            "benchmark_price": plan["qqq_p"],
            "long_trend_value": plan["ma200"],
            "exit_line": plan["exit_line"],
        },
    }
    return StrategyDecision(
        positions=target_values_to_positions(plan["target_values"]),
        diagnostics=diagnostics,
    )


legacy_hybrid_growth_income.build_rebalance_plan.__doc__ = (
    ((legacy_hybrid_growth_income.build_rebalance_plan.__doc__ or "").strip() +
     "\n\nLegacy adapter: prefer us_equity_strategies entrypoints for new integrations.")
    .strip()
)


def _build_semiconductor_account_state_from_portfolio(portfolio, *, strategy_symbols: tuple[str, ...]) -> dict[str, object]:
    market_values = {symbol: 0.0 for symbol in strategy_symbols}
    quantities = {symbol: 0 for symbol in strategy_symbols}
    sellable_quantities = {symbol: 0 for symbol in strategy_symbols}
    for position in getattr(portfolio, "positions", ()):
        if position.symbol not in market_values:
            continue
        market_values[position.symbol] = float(position.market_value)
        quantity = int(position.quantity)
        quantities[position.symbol] = quantity
        sellable_quantities[position.symbol] = quantity
    available_cash = float(
        getattr(portfolio, "buying_power", None)
        or getattr(portfolio, "cash_balance", None)
        or 0.0
    )
    return {
        "available_cash": available_cash,
        "market_values": market_values,
        "quantities": quantities,
        "sellable_quantities": sellable_quantities,
        "total_strategy_equity": float(portfolio.total_equity),
    }


def evaluate_semiconductor_rotation_income(ctx: StrategyContext) -> StrategyDecision:
    config = merge_runtime_config(soxl_soxx_trend_income_manifest.default_config, ctx)
    strategy_symbols = tuple(str(symbol) for symbol in config.pop("managed_symbols", ()))
    config.pop("signal_text_fn", None)
    portfolio = require_portfolio(ctx)
    plan = legacy_semiconductor.build_rebalance_plan(
        require_market_data(ctx, "derived_indicators"),
        _build_semiconductor_account_state_from_portfolio(
            portfolio,
            strategy_symbols=strategy_symbols,
        ),
        translator=config.pop("translator", default_translator),
        **config,
    )
    diagnostics = {
        "market_status": plan["market_status"],
        "signal_message": plan["signal_message"],
        "deploy_ratio_text": plan["deploy_ratio_text"],
        "income_ratio_text": plan["income_ratio_text"],
        "income_locked_ratio_text": plan["income_locked_ratio_text"],
        "active_risk_asset": plan["active_risk_asset"],
        "investable_cash": plan["investable_cash"],
        "threshold_value": plan["threshold_value"],
        "current_min_trade": plan["current_min_trade"],
        "total_strategy_equity": plan["total_strategy_equity"],
        "execution_annotations": {
            "trade_threshold_value": plan["threshold_value"],
            "signal_display": plan["signal_message"],
            "status_display": plan["market_status"],
            "deploy_ratio_text": plan["deploy_ratio_text"],
            "income_ratio_text": plan["income_ratio_text"],
            "income_locked_ratio_text": plan["income_locked_ratio_text"],
            "active_risk_asset": plan["active_risk_asset"],
            "investable_cash": plan["investable_cash"],
            "current_min_trade": plan["current_min_trade"],
        },
    }
    return StrategyDecision(
        positions=target_values_to_positions(plan["targets"]),
        diagnostics=diagnostics,
    )


legacy_semiconductor.build_rebalance_plan.__doc__ = (
    ((legacy_semiconductor.build_rebalance_plan.__doc__ or "").strip() +
     "\n\nLegacy adapter: prefer us_equity_strategies entrypoints for new integrations.")
    .strip()
)


def evaluate_russell_1000_multi_factor_defensive(ctx: StrategyContext) -> StrategyDecision:
    config = merge_runtime_config(russell_1000_multi_factor_defensive_manifest.default_config, ctx)
    weights, signal_desc, is_emergency, status_desc, metadata = legacy_russell.compute_signals(
        require_market_data(ctx, "feature_snapshot"),
        get_current_holdings(ctx),
        **config,
    )
    diagnostics = {
        **metadata,
        "signal_description": signal_desc,
        "status_description": status_desc,
        "signal_source": legacy_russell.SIGNAL_SOURCE,
    }
    risk_flags = ("hard_defense",) if is_emergency else ()
    return StrategyDecision(
        positions=weights_to_positions(weights, safe_haven=str(config.get("safe_haven", "BOXX"))),
        risk_flags=risk_flags,
        diagnostics=diagnostics,
    )


legacy_russell.compute_signals.__doc__ = (
    ((legacy_russell.compute_signals.__doc__ or "").strip() +
     "\n\nLegacy adapter: prefer us_equity_strategies entrypoints for new integrations.")
    .strip()
)


def evaluate_tech_pullback_cash_buffer(ctx: StrategyContext) -> StrategyDecision:
    config = merge_runtime_config(qqq_tech_enhancement_manifest.default_config, ctx)
    config.pop("execution_cash_reserve_ratio", None)
    weights, signal_desc, is_emergency, status_desc, metadata = legacy_tech_pullback.compute_signals(
        require_market_data(ctx, "feature_snapshot"),
        get_current_holdings(ctx),
        **config,
    )
    diagnostics = {
        **metadata,
        "signal_description": signal_desc,
        "status_description": status_desc,
        "signal_source": legacy_tech_pullback.SIGNAL_SOURCE,
        "actionable": weights is not None,
    }
    risk_flags: tuple[str, ...] = ()
    if is_emergency:
        risk_flags += ("hard_defense",)
    if weights is None:
        risk_flags += ("no_execute",)
    return StrategyDecision(
        positions=weights_to_positions(weights, safe_haven=str(config.get("safe_haven", "BOXX"))),
        risk_flags=risk_flags,
        diagnostics=diagnostics,
    )


legacy_tech_pullback.compute_signals.__doc__ = (
    ((legacy_tech_pullback.compute_signals.__doc__ or "").strip() +
     "\n\nLegacy adapter: prefer us_equity_strategies entrypoints for new integrations.")
    .strip()
)


global_etf_rotation_entrypoint = CallableStrategyEntrypoint(
    manifest=global_etf_rotation_manifest,
    _evaluate=evaluate_global_etf_rotation,
)
tqqq_growth_income_entrypoint = CallableStrategyEntrypoint(
    manifest=tqqq_growth_income_manifest,
    _evaluate=evaluate_hybrid_growth_income,
)
soxl_soxx_trend_income_entrypoint = CallableStrategyEntrypoint(
    manifest=soxl_soxx_trend_income_manifest,
    _evaluate=evaluate_semiconductor_rotation_income,
)
russell_1000_multi_factor_defensive_entrypoint = CallableStrategyEntrypoint(
    manifest=russell_1000_multi_factor_defensive_manifest,
    _evaluate=evaluate_russell_1000_multi_factor_defensive,
)
qqq_tech_enhancement_entrypoint = CallableStrategyEntrypoint(
    manifest=qqq_tech_enhancement_manifest,
    _evaluate=evaluate_tech_pullback_cash_buffer,
)


__all__ = [
    "global_etf_rotation_entrypoint",
    "tqqq_growth_income_entrypoint",
    "soxl_soxx_trend_income_entrypoint",
    "qqq_tech_enhancement_entrypoint",
    "russell_1000_multi_factor_defensive_entrypoint",
    "evaluate_global_etf_rotation",
    "evaluate_hybrid_growth_income",
    "evaluate_semiconductor_rotation_income",
    "evaluate_russell_1000_multi_factor_defensive",
    "evaluate_tech_pullback_cash_buffer",
]
