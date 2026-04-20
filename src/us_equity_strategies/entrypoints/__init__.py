from __future__ import annotations

from collections.abc import Mapping

from quant_platform_kit.strategy_contracts import CallableStrategyEntrypoint, StrategyDecision, StrategyContext

from us_equity_strategies.account_sizing import (
    append_account_size_warning,
    build_account_size_diagnostics_from_context,
)
from us_equity_strategies.manifests import (
    dynamic_mega_leveraged_pullback_manifest,
    global_etf_rotation_manifest,
    mega_cap_leader_rotation_aggressive_manifest,
    mega_cap_leader_rotation_dynamic_top20_manifest,
    mega_cap_leader_rotation_top50_balanced_manifest,
    qqq_tech_enhancement_manifest,
    russell_1000_multi_factor_defensive_manifest,
    soxl_soxx_trend_income_manifest,
    tqqq_growth_income_manifest,
)
from us_equity_strategies.strategies import (
    dynamic_mega_leveraged_pullback as dynamic_mega_leveraged_pullback_strategy,
    global_etf_rotation as legacy_global_etf_rotation,
    mega_cap_leader_rotation_dynamic_top20 as mega_cap_leader_rotation_dynamic_top20_strategy,
    tqqq_growth_income as tqqq_growth_income_strategy,
    russell_1000_multi_factor_defensive as legacy_russell,
    soxl_soxx_trend_income as soxl_soxx_trend_income_strategy,
    qqq_tech_enhancement as qqq_tech_enhancement_strategy,
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
from ._portfolio_dashboard import build_portfolio_dashboard


"""Unified strategy entrypoints built as adapters over legacy implementations."""


def _account_size_diagnostics(profile: str, ctx: StrategyContext) -> dict[str, object]:
    return build_account_size_diagnostics_from_context(profile, ctx)


def _symbols_from_sources(*sources) -> tuple[str, ...]:
    symbols: list[str] = []
    seen: set[str] = set()
    for source in sources:
        if isinstance(source, Mapping):
            values = source.keys()
        elif isinstance(source, (str, bytes)):
            values = (source,)
        else:
            values = source
        for symbol in values or ():
            normalized = str(symbol or "").strip().upper().removesuffix(".US")
            if not normalized or normalized in seen:
                continue
            symbols.append(normalized)
            seen.add(normalized)
    return tuple(symbols)


def _config_managed_symbols(config: Mapping[str, object]) -> tuple[str, ...]:
    return _symbols_from_sources(config.get("managed_symbols") or ())


def _build_dashboard_text(
    ctx: StrategyContext,
    *,
    strategy_symbols=(),
    translator,
    signal_text=None,
    benchmark_text=None,
) -> str:
    if ctx.portfolio is None:
        return ""
    return build_portfolio_dashboard(
        ctx.portfolio,
        strategy_symbols=strategy_symbols,
        translator=translator,
        signal_text=signal_text,
        benchmark_text=benchmark_text,
    )


def _attach_dashboard_text(diagnostics: dict[str, object], dashboard_text: str) -> dict[str, object]:
    text = str(dashboard_text or "").strip()
    if not text:
        return diagnostics
    raw_annotations = diagnostics.get("execution_annotations")
    annotations = dict(raw_annotations) if isinstance(raw_annotations, Mapping) else {}
    annotations["dashboard_text"] = text
    diagnostics["dashboard"] = text
    diagnostics["execution_annotations"] = annotations
    return diagnostics


def evaluate_global_etf_rotation(ctx: StrategyContext) -> StrategyDecision:
    config = merge_runtime_config(global_etf_rotation_manifest.default_config, ctx)
    config["ranking_pool"] = list(config.get("ranking_pool", ()))
    config["canary_assets"] = list(config.get("canary_assets", ()))
    market_history = require_market_data(ctx, "market_history")
    translator = config.pop("translator", default_translator)
    weights, signal_desc, is_emergency, canary_str = legacy_global_etf_rotation.compute_signals(
        ctx.capabilities.get("broker_client"),
        get_current_holdings(ctx),
        get_historical_close=market_history,
        translator=translator,
        pacing_sec=float(config.pop("pacing_sec", 0.0)),
        **config,
    )
    diagnostics = {
        "signal_description": signal_desc,
        "canary_status": canary_str,
        "actionable": weights is not None,
    }
    diagnostics.update(_account_size_diagnostics(global_etf_rotation_manifest.profile, ctx))
    diagnostics["signal_description"] = append_account_size_warning(
        str(diagnostics["signal_description"]),
        diagnostics,
        translator=translator,
    )
    _attach_dashboard_text(
        diagnostics,
        _build_dashboard_text(
            ctx,
            strategy_symbols=_symbols_from_sources(
                weights or {},
                get_current_holdings(ctx),
                config.get("safe_haven"),
            ),
            translator=translator,
            signal_text=diagnostics["signal_description"],
        ),
    )
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


def evaluate_tqqq_growth_income(ctx: StrategyContext) -> StrategyDecision:
    config = merge_runtime_config(tqqq_growth_income_manifest.default_config, ctx)
    managed_symbols = _config_managed_symbols(config)
    config.pop("managed_symbols", None)
    config.pop("benchmark_symbol", None)
    config.pop("execution_cash_reserve_ratio", None)
    translator = config.pop("translator", default_translator)
    plan = tqqq_growth_income_strategy.build_rebalance_plan(
        require_market_data(ctx, "benchmark_history"),
        require_portfolio(ctx),
        signal_text_fn=config.pop("signal_text_fn", default_signal_text_fn),
        translator=translator,
        **config,
    )
    account_size_diagnostics = _account_size_diagnostics(tqqq_growth_income_manifest.profile, ctx)
    signal_display = append_account_size_warning(
        str(plan["sig_display"]),
        account_size_diagnostics,
        translator=translator,
    )
    benchmark_text = str(plan["dashboard"]).splitlines()[-1]
    dashboard_text = _build_dashboard_text(
        ctx,
        strategy_symbols=managed_symbols,
        translator=translator,
        signal_text=signal_display,
        benchmark_text=benchmark_text,
    )
    diagnostics = {
        "signal_display": signal_display,
        "dashboard": dashboard_text or plan["dashboard"],
        "threshold": plan["threshold"],
        "reserved": plan["reserved"],
        "qqq_price": plan["qqq_p"],
        "ma200": plan["ma200"],
        "exit_line": plan["exit_line"],
        "pullback_rebound": plan.get("pullback_rebound"),
        "pullback_rebound_window": plan.get("pullback_rebound_window"),
        "pullback_rebound_threshold": plan.get("pullback_rebound_threshold"),
        "pullback_rebound_threshold_mode": plan.get("pullback_rebound_threshold_mode"),
        "pullback_rebound_volatility": plan.get("pullback_rebound_volatility"),
        "pullback_rebound_volatility_multiplier": plan.get("pullback_rebound_volatility_multiplier"),
        "real_buying_power": plan["real_buying_power"],
        "total_equity": plan["total_equity"],
        **account_size_diagnostics,
        "execution_annotations": {
            "trade_threshold_value": plan["threshold"],
            "reserved_cash": plan["reserved"],
            "signal_display": signal_display,
            "dashboard_text": dashboard_text or plan["dashboard"],
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


tqqq_growth_income_strategy.build_rebalance_plan.__doc__ = (
    ((tqqq_growth_income_strategy.build_rebalance_plan.__doc__ or "").strip() +
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


def evaluate_soxl_soxx_trend_income(ctx: StrategyContext) -> StrategyDecision:
    config = merge_runtime_config(soxl_soxx_trend_income_manifest.default_config, ctx)
    strategy_symbols = tuple(str(symbol) for symbol in config.pop("managed_symbols", ()))
    config.pop("signal_text_fn", None)
    portfolio = require_portfolio(ctx)
    translator = config.pop("translator", default_translator)
    plan = soxl_soxx_trend_income_strategy.build_rebalance_plan(
        require_market_data(ctx, "derived_indicators"),
        _build_semiconductor_account_state_from_portfolio(
            portfolio,
            strategy_symbols=strategy_symbols,
        ),
        translator=translator,
        **config,
    )
    account_size_diagnostics = _account_size_diagnostics(soxl_soxx_trend_income_manifest.profile, ctx)
    signal_message = append_account_size_warning(
        str(plan["signal_message"]),
        account_size_diagnostics,
        translator=translator,
    )
    dashboard_text = _build_dashboard_text(
        ctx,
        strategy_symbols=strategy_symbols,
        translator=translator,
        signal_text=signal_message,
    )
    diagnostics = {
        "market_status": plan["market_status"],
        "signal_message": signal_message,
        "dashboard": dashboard_text,
        "deploy_ratio_text": plan["deploy_ratio_text"],
        "income_ratio_text": plan["income_ratio_text"],
        "income_locked_ratio_text": plan["income_locked_ratio_text"],
        "active_risk_asset": plan["active_risk_asset"],
        "investable_cash": plan["investable_cash"],
        "threshold_value": plan["threshold_value"],
        "current_min_trade": plan["current_min_trade"],
        "total_strategy_equity": plan["total_strategy_equity"],
        "allocation_mode": plan.get("allocation_mode"),
        "trend_entry_buffer": plan.get("trend_entry_buffer"),
        "trend_mid_buffer": plan.get("trend_mid_buffer"),
        "trend_exit_buffer": plan.get("trend_exit_buffer"),
        "blend_tier": plan.get("blend_tier"),
        "soxl_entry_line": plan.get("soxl_entry_line"),
        "soxl_exit_line": plan.get("soxl_exit_line"),
        "trend_entry_line": plan.get("trend_entry_line"),
        "trend_mid_line": plan.get("trend_mid_line"),
        "trend_exit_line": plan.get("trend_exit_line"),
        "trend_symbol": plan.get("trend_symbol"),
        "trend_price": plan.get("trend_price"),
        "trend_ma": plan.get("trend_ma"),
        "trend_ma20": plan.get("trend_ma20"),
        "trend_ma20_slope": plan.get("trend_ma20_slope"),
        **account_size_diagnostics,
        "execution_annotations": {
            "trade_threshold_value": plan["threshold_value"],
            "signal_display": signal_message,
            "status_display": plan["market_status"],
            "dashboard_text": dashboard_text,
            "deploy_ratio_text": plan["deploy_ratio_text"],
            "income_ratio_text": plan["income_ratio_text"],
            "income_locked_ratio_text": plan["income_locked_ratio_text"],
            "active_risk_asset": plan["active_risk_asset"],
            "investable_cash": plan["investable_cash"],
            "current_min_trade": plan["current_min_trade"],
            "allocation_mode": plan.get("allocation_mode"),
            "trend_entry_buffer": plan.get("trend_entry_buffer"),
            "trend_mid_buffer": plan.get("trend_mid_buffer"),
            "trend_exit_buffer": plan.get("trend_exit_buffer"),
            "blend_tier": plan.get("blend_tier"),
            "soxl_entry_line": plan.get("soxl_entry_line"),
            "soxl_exit_line": plan.get("soxl_exit_line"),
            "trend_entry_line": plan.get("trend_entry_line"),
            "trend_mid_line": plan.get("trend_mid_line"),
            "trend_exit_line": plan.get("trend_exit_line"),
            "trend_symbol": plan.get("trend_symbol"),
            "trend_price": plan.get("trend_price"),
            "trend_ma": plan.get("trend_ma"),
            "trend_ma20": plan.get("trend_ma20"),
            "trend_ma20_slope": plan.get("trend_ma20_slope"),
        },
    }
    return StrategyDecision(
        positions=target_values_to_positions(plan["targets"]),
        diagnostics=diagnostics,
    )


soxl_soxx_trend_income_strategy.build_rebalance_plan.__doc__ = (
    ((soxl_soxx_trend_income_strategy.build_rebalance_plan.__doc__ or "").strip() +
     "\n\nLegacy adapter: prefer us_equity_strategies entrypoints for new integrations.")
    .strip()
)


def evaluate_russell_1000_multi_factor_defensive(ctx: StrategyContext) -> StrategyDecision:
    config = merge_runtime_config(russell_1000_multi_factor_defensive_manifest.default_config, ctx)
    translator = config.get("translator", default_translator)
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
    diagnostics.update(_account_size_diagnostics(russell_1000_multi_factor_defensive_manifest.profile, ctx))
    diagnostics["signal_description"] = append_account_size_warning(
        str(diagnostics["signal_description"]),
        diagnostics,
        translator=translator,
    )
    _attach_dashboard_text(
        diagnostics,
        _build_dashboard_text(
            ctx,
            strategy_symbols=_symbols_from_sources(
                metadata.get("managed_symbols"),
                weights or {},
                get_current_holdings(ctx),
                config.get("safe_haven"),
            ),
            translator=translator,
            signal_text=diagnostics["signal_description"],
        ),
    )
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


def evaluate_qqq_tech_enhancement(ctx: StrategyContext) -> StrategyDecision:
    config = merge_runtime_config(qqq_tech_enhancement_manifest.default_config, ctx)
    translator = config.get("translator", default_translator)
    config.pop("execution_cash_reserve_ratio", None)
    if ctx.portfolio is not None and "portfolio_total_equity" not in config:
        total_equity = getattr(ctx.portfolio, "total_equity", None)
        if total_equity is not None:
            config["portfolio_total_equity"] = float(total_equity)
    weights, signal_desc, is_emergency, status_desc, metadata = qqq_tech_enhancement_strategy.compute_signals(
        require_market_data(ctx, "feature_snapshot"),
        get_current_holdings(ctx),
        **config,
    )
    diagnostics = {
        **metadata,
        "signal_description": signal_desc,
        "status_description": status_desc,
        "signal_source": qqq_tech_enhancement_strategy.SIGNAL_SOURCE,
        "actionable": weights is not None,
    }
    diagnostics.update(_account_size_diagnostics(qqq_tech_enhancement_manifest.profile, ctx))
    diagnostics["signal_description"] = append_account_size_warning(
        str(diagnostics["signal_description"]),
        diagnostics,
        translator=translator,
    )
    _attach_dashboard_text(
        diagnostics,
        _build_dashboard_text(
            ctx,
            strategy_symbols=_symbols_from_sources(
                metadata.get("managed_symbols"),
                weights or {},
                get_current_holdings(ctx),
                config.get("safe_haven"),
            ),
            translator=translator,
            signal_text=diagnostics["signal_description"],
        ),
    )
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


qqq_tech_enhancement_strategy.compute_signals.__doc__ = (
    ((qqq_tech_enhancement_strategy.compute_signals.__doc__ or "").strip() +
     "\n\nLegacy adapter: prefer us_equity_strategies entrypoints for new integrations.")
    .strip()
)


def _evaluate_mega_cap_leader_rotation_snapshot_profile(
    ctx: StrategyContext,
    *,
    manifest,
) -> StrategyDecision:
    config = merge_runtime_config(manifest.default_config, ctx)
    translator = config.get("translator", default_translator)
    config.pop("execution_cash_reserve_ratio", None)
    if ctx.as_of is not None and "run_as_of" not in config:
        config["run_as_of"] = ctx.as_of
    if ctx.portfolio is not None and "portfolio_total_equity" not in config:
        total_equity = getattr(ctx.portfolio, "total_equity", None)
        if total_equity is not None:
            config["portfolio_total_equity"] = float(total_equity)
    weights, signal_desc, is_emergency, status_desc, metadata = mega_cap_leader_rotation_dynamic_top20_strategy.compute_signals(
        require_market_data(ctx, "feature_snapshot"),
        get_current_holdings(ctx),
        **config,
    )
    diagnostics = {
        **metadata,
        "signal_description": signal_desc,
        "status_description": status_desc,
        "signal_source": mega_cap_leader_rotation_dynamic_top20_strategy.SIGNAL_SOURCE,
        "actionable": weights is not None,
    }
    diagnostics.update(_account_size_diagnostics(manifest.profile, ctx))
    diagnostics["signal_description"] = append_account_size_warning(
        str(diagnostics["signal_description"]),
        diagnostics,
        translator=translator,
    )
    _attach_dashboard_text(
        diagnostics,
        _build_dashboard_text(
            ctx,
            strategy_symbols=_symbols_from_sources(
                metadata.get("managed_symbols"),
                weights or {},
                get_current_holdings(ctx),
                config.get("safe_haven"),
            ),
            translator=translator,
            signal_text=diagnostics["signal_description"],
        ),
    )
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


def evaluate_mega_cap_leader_rotation_dynamic_top20(ctx: StrategyContext) -> StrategyDecision:
    return _evaluate_mega_cap_leader_rotation_snapshot_profile(
        ctx,
        manifest=mega_cap_leader_rotation_dynamic_top20_manifest,
    )


def evaluate_mega_cap_leader_rotation_aggressive(ctx: StrategyContext) -> StrategyDecision:
    return _evaluate_mega_cap_leader_rotation_snapshot_profile(
        ctx,
        manifest=mega_cap_leader_rotation_aggressive_manifest,
    )


def evaluate_mega_cap_leader_rotation_top50_balanced(ctx: StrategyContext) -> StrategyDecision:
    return _evaluate_mega_cap_leader_rotation_snapshot_profile(
        ctx,
        manifest=mega_cap_leader_rotation_top50_balanced_manifest,
    )


mega_cap_leader_rotation_dynamic_top20_strategy.compute_signals.__doc__ = (
    ((mega_cap_leader_rotation_dynamic_top20_strategy.compute_signals.__doc__ or "").strip() +
     "\n\nLegacy adapter: prefer us_equity_strategies entrypoints for new integrations.")
    .strip()
)


def evaluate_dynamic_mega_leveraged_pullback(ctx: StrategyContext) -> StrategyDecision:
    config = merge_runtime_config(dynamic_mega_leveraged_pullback_manifest.default_config, ctx)
    translator = config.get("translator", default_translator)
    config.pop("execution_cash_reserve_ratio", None)
    config.pop("runtime_execution_window_trading_days", None)
    portfolio = require_portfolio(ctx)
    if "portfolio_total_equity" not in config:
        config["portfolio_total_equity"] = float(portfolio.total_equity)
    weights, signal_desc, is_emergency, status_desc, metadata = dynamic_mega_leveraged_pullback_strategy.compute_signals(
        require_market_data(ctx, "feature_snapshot"),
        get_current_holdings(ctx),
        market_history=require_market_data(ctx, "market_history"),
        benchmark_history=require_market_data(ctx, "benchmark_history"),
        portfolio=portfolio,
        ib=ctx.capabilities.get("broker_client"),
        **config,
    )
    diagnostics = {
        **metadata,
        "signal_description": signal_desc,
        "status_description": status_desc,
        "signal_source": dynamic_mega_leveraged_pullback_strategy.SIGNAL_SOURCE,
        "actionable": weights is not None,
    }
    diagnostics.update(_account_size_diagnostics(dynamic_mega_leveraged_pullback_manifest.profile, ctx))
    diagnostics["signal_description"] = append_account_size_warning(
        str(diagnostics["signal_description"]),
        diagnostics,
        translator=translator,
    )
    _attach_dashboard_text(
        diagnostics,
        _build_dashboard_text(
            ctx,
            strategy_symbols=_symbols_from_sources(
                metadata.get("managed_symbols"),
                weights or {},
                get_current_holdings(ctx),
                config.get("safe_haven"),
            ),
            translator=translator,
            signal_text=diagnostics["signal_description"],
        ),
    )
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


dynamic_mega_leveraged_pullback_strategy.compute_signals.__doc__ = (
    ((dynamic_mega_leveraged_pullback_strategy.compute_signals.__doc__ or "").strip() +
     "\n\nLegacy adapter: prefer us_equity_strategies entrypoints for new integrations.")
    .strip()
)


global_etf_rotation_entrypoint = CallableStrategyEntrypoint(
    manifest=global_etf_rotation_manifest,
    _evaluate=evaluate_global_etf_rotation,
)
tqqq_growth_income_entrypoint = CallableStrategyEntrypoint(
    manifest=tqqq_growth_income_manifest,
    _evaluate=evaluate_tqqq_growth_income,
)
soxl_soxx_trend_income_entrypoint = CallableStrategyEntrypoint(
    manifest=soxl_soxx_trend_income_manifest,
    _evaluate=evaluate_soxl_soxx_trend_income,
)
russell_1000_multi_factor_defensive_entrypoint = CallableStrategyEntrypoint(
    manifest=russell_1000_multi_factor_defensive_manifest,
    _evaluate=evaluate_russell_1000_multi_factor_defensive,
)
qqq_tech_enhancement_entrypoint = CallableStrategyEntrypoint(
    manifest=qqq_tech_enhancement_manifest,
    _evaluate=evaluate_qqq_tech_enhancement,
)
mega_cap_leader_rotation_dynamic_top20_entrypoint = CallableStrategyEntrypoint(
    manifest=mega_cap_leader_rotation_dynamic_top20_manifest,
    _evaluate=evaluate_mega_cap_leader_rotation_dynamic_top20,
)
mega_cap_leader_rotation_aggressive_entrypoint = CallableStrategyEntrypoint(
    manifest=mega_cap_leader_rotation_aggressive_manifest,
    _evaluate=evaluate_mega_cap_leader_rotation_aggressive,
)
mega_cap_leader_rotation_top50_balanced_entrypoint = CallableStrategyEntrypoint(
    manifest=mega_cap_leader_rotation_top50_balanced_manifest,
    _evaluate=evaluate_mega_cap_leader_rotation_top50_balanced,
)
dynamic_mega_leveraged_pullback_entrypoint = CallableStrategyEntrypoint(
    manifest=dynamic_mega_leveraged_pullback_manifest,
    _evaluate=evaluate_dynamic_mega_leveraged_pullback,
)


__all__ = [
    "global_etf_rotation_entrypoint",
    "tqqq_growth_income_entrypoint",
    "soxl_soxx_trend_income_entrypoint",
    "qqq_tech_enhancement_entrypoint",
    "russell_1000_multi_factor_defensive_entrypoint",
    "mega_cap_leader_rotation_dynamic_top20_entrypoint",
    "mega_cap_leader_rotation_aggressive_entrypoint",
    "mega_cap_leader_rotation_top50_balanced_entrypoint",
    "dynamic_mega_leveraged_pullback_entrypoint",
    "evaluate_global_etf_rotation",
    "evaluate_tqqq_growth_income",
    "evaluate_soxl_soxx_trend_income",
    "evaluate_russell_1000_multi_factor_defensive",
    "evaluate_qqq_tech_enhancement",
    "evaluate_mega_cap_leader_rotation_dynamic_top20",
    "evaluate_mega_cap_leader_rotation_aggressive",
    "evaluate_mega_cap_leader_rotation_top50_balanced",
    "evaluate_dynamic_mega_leveraged_pullback",
]
