from __future__ import annotations

from collections.abc import Mapping

from quant_platform_kit.strategy_contracts import (
    CallableStrategyEntrypoint,
    StrategyContext,
    StrategyDecision,
    build_execution_timing_metadata,
)

from us_equity_strategies.account_sizing import (
    append_account_size_warning,
    build_account_size_diagnostics_from_context,
)
from us_equity_strategies.ai_extensions import (
    AI_EXTENSION_SIGNAL_STATE_KEY,
    build_ai_extension_diagnostics,
)
from us_equity_strategies.combo_manifests import (
    us_equity_combo_core_manifest,
    us_equity_combo_leveraged_manifest,
    us_equity_combo_manifest,
)
from us_equity_strategies.manifests import (
    global_etf_rotation_manifest,
    ibit_smart_dca_manifest,
    russell_top50_leader_rotation_manifest,
    nasdaq_sp500_smart_dca_manifest,
    soxl_soxx_trend_income_manifest,
    tecl_xlk_trend_income_manifest,
    tqqq_growth_income_manifest,
)
from us_equity_strategies.option_overlay import build_option_overlay_diagnostics
from us_equity_strategies.strategies import (
    global_etf_rotation as legacy_global_etf_rotation,
    ibit_smart_dca as ibit_smart_dca_strategy,
    mega_cap_leader_rotation as mega_cap_leader_rotation_strategy,
    nasdaq_sp500_smart_dca as nasdaq_sp500_smart_dca_strategy,
    tqqq_growth_income as tqqq_growth_income_strategy,
    soxl_soxx_trend_income as soxl_soxx_trend_income_strategy,
    tecl_xlk_trend_income as tecl_xlk_trend_income_strategy,
    us_equity_combo as us_equity_combo_strategy,
    us_equity_combo_core as us_equity_combo_core_strategy,
    us_equity_combo_leveraged as us_equity_combo_leveraged_strategy,
)

from ._common import (
    apply_risk_gate,
    apply_reserved_cash_policy_to_ratio_config,
    apply_reserved_cash_policy_to_usd_config,
    apply_income_layer_to_weights,
    apply_market_regime_control_to_weights,
    default_signal_text_fn,
    default_translator,
    get_current_holdings,
    merge_runtime_config,
    pop_reserved_cash_policy_config,
    pop_execution_only_config,
    pop_income_layer_config,
    pop_market_regime_control_config,
    pop_option_overlay_config,
    require_market_data,
    require_portfolio,
    record_strategy_decision,
    target_values_to_positions,
    weights_to_positions,
)
from ._portfolio_dashboard import build_portfolio_dashboard
from us_equity_strategies.cash_only_equity import (
    compute_strategy_total_equity,
    resolve_raw_cash_from_snapshot,
)


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
    portfolio_context=None,
) -> str:
    if ctx.portfolio is None:
        return ""
    return build_portfolio_dashboard(
        ctx.portfolio,
        strategy_symbols=strategy_symbols,
        translator=translator,
        signal_text=signal_text,
        benchmark_text=benchmark_text,
        portfolio_context=portfolio_context,
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


def _attach_notification_context(
    diagnostics: dict[str, object],
    notification_context: Mapping[str, object] | None,
) -> dict[str, object]:
    if not isinstance(notification_context, Mapping) or not notification_context:
        return diagnostics
    raw_annotations = diagnostics.get("execution_annotations")
    annotations = dict(raw_annotations) if isinstance(raw_annotations, Mapping) else {}
    annotations["notification_context"] = dict(notification_context)
    diagnostics["notification_context"] = dict(notification_context)
    diagnostics["execution_annotations"] = annotations
    return diagnostics


def _merge_notification_contexts(
    base: Mapping[str, object] | None,
    overlay: Mapping[str, object] | None,
) -> Mapping[str, object] | None:
    if not isinstance(overlay, Mapping) or not overlay:
        return base
    if not isinstance(base, Mapping) or not base:
        return dict(overlay)
    merged = dict(base)
    overlay_risk_controls = overlay.get("risk_controls")
    if isinstance(overlay_risk_controls, Mapping):
        risk_controls = dict(merged.get("risk_controls") or {})
        risk_controls.update(overlay_risk_controls)
        merged["risk_controls"] = risk_controls
    for key, value in overlay.items():
        if key != "risk_controls":
            merged.setdefault(key, value)
    return merged


def _attach_execution_timing(
    diagnostics: dict[str, object],
    ctx: StrategyContext,
) -> dict[str, object]:
    signal_delay = ctx.runtime_config.get("signal_effective_after_trading_days")
    timing = build_execution_timing_metadata(
        signal_date=ctx.as_of,
        signal_effective_after_trading_days=(
            int(signal_delay) if signal_delay is not None else None
        ),
    )
    raw_annotations = diagnostics.get("execution_annotations")
    annotations = dict(raw_annotations) if isinstance(raw_annotations, Mapping) else {}
    annotations.update(timing)
    diagnostics.update(timing)
    diagnostics["execution_annotations"] = annotations
    return diagnostics


def _render_translation_context(
    notification_context: Mapping[str, object] | None,
    *,
    translator,
    fallback: str,
) -> str:
    if not isinstance(notification_context, Mapping):
        return fallback
    key = str(notification_context.get("code") or "").strip()
    if not key:
        return fallback
    params = dict(notification_context.get("params") or {})
    rendered = translator(key, **params)
    return fallback if rendered == key else str(rendered)


def _render_notification_displays(
    signal_desc: object,
    status_desc: object,
    metadata: Mapping[str, object] | None,
    *,
    translator,
) -> tuple[str, str, Mapping[str, object] | None]:
    notification_context = None
    if isinstance(metadata, Mapping):
        raw_notification_context = metadata.get("notification_context")
        if isinstance(raw_notification_context, Mapping):
            notification_context = raw_notification_context
    rendered_signal = _render_translation_context(
        notification_context.get("signal") if isinstance(notification_context, Mapping) else None,
        translator=translator,
        fallback=str(signal_desc or ""),
    )
    rendered_status = _render_translation_context(
        notification_context.get("status") if isinstance(notification_context, Mapping) else None,
        translator=translator,
        fallback=str(status_desc or ""),
    )
    return rendered_signal, rendered_status, notification_context


def _build_tqqq_benchmark_text(notification_context: Mapping[str, object] | None) -> str:
    if not isinstance(notification_context, Mapping):
        return ""
    symbol = str(notification_context.get("symbol") or "").strip().upper() or "QQQ"
    price = notification_context.get("price")
    exit_line = notification_context.get("exit_line")
    ma20_slope_text = str(notification_context.get("ma20_slope_text") or "").strip()
    if ma20_slope_text:
        slope_text = ma20_slope_text
    else:
        ma20_slope = notification_context.get("ma20_slope")
        slope_text = "n/a" if ma20_slope is None else f"{float(ma20_slope):+.2f}"
    if price is None or exit_line is None:
        return ""
    return (
        f"{symbol}: {float(price):.2f} | MA200 Exit: {float(exit_line):.2f} | "
        f"MA20Δ: {slope_text}"
    )


def _evaluate_global_etf_rotation_with_manifest(ctx: StrategyContext, *, manifest) -> StrategyDecision:
    config = merge_runtime_config(manifest.default_config, ctx)
    income_layer_config = pop_income_layer_config(config)
    option_overlay_config = pop_option_overlay_config(config)
    market_regime_control_config = pop_market_regime_control_config(config)
    config["ranking_pool"] = list(config.get("ranking_pool", ()))
    config["canary_assets"] = list(config.get("canary_assets", ()))
    config.pop("signal_effective_after_trading_days", None)
    pop_execution_only_config(config)
    feature_snapshot = require_market_data(ctx, "feature_snapshot")
    translator = config.pop("translator", default_translator)
    config.pop("signal_text_fn", None)
    config.pop("pacing_sec", None)
    weights, signal_desc, is_emergency, canary_str = legacy_global_etf_rotation.compute_signals_from_feature_snapshot(
        feature_snapshot,
        get_current_holdings(ctx),
        as_of_date=ctx.as_of,
        translator=translator,
        **config,
    )
    weights, income_layer_diagnostics = apply_income_layer_to_weights(
        weights,
        income_layer_config=income_layer_config,
        ctx=ctx,
        excluded_symbols=(config.get("safe_haven"),),
    )
    weights, market_regime_control_diagnostics = apply_market_regime_control_to_weights(
        weights,
        market_regime_control_config=market_regime_control_config,
        ctx=ctx,
        safe_haven=str(config.get("safe_haven", "BIL")),
        excluded_symbols=(config.get("safe_haven"),),
    )
    notification_context = _merge_notification_contexts(
        None,
        market_regime_control_diagnostics.get("market_regime_control_notification_context"),
    )
    diagnostics = {
        "signal_source": legacy_global_etf_rotation.SIGNAL_SOURCE,
        "snapshot_contract_version": legacy_global_etf_rotation.SNAPSHOT_CONTRACT_VERSION,
        "signal_description": signal_desc,
        "canary_status": canary_str,
        "actionable": weights is not None,
        **income_layer_diagnostics,
        **market_regime_control_diagnostics,
        **build_option_overlay_diagnostics(option_overlay_config, ctx),
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
                weights or {},
                get_current_holdings(ctx),
                config.get("safe_haven"),
            ),
            translator=translator,
            signal_text=diagnostics["signal_description"],
        ),
    )
    _attach_notification_context(diagnostics, notification_context)
    _attach_execution_timing(diagnostics, ctx)
    risk_flags = ("emergency",) if is_emergency else ()
    decision = StrategyDecision(
        positions=weights_to_positions(weights, safe_haven=str(config.get("safe_haven", "BIL"))),
        risk_flags=risk_flags,
        diagnostics=diagnostics,
    )
    decision = apply_risk_gate(decision)
    record_strategy_decision(
        ctx,
        decision,
        profile_id=manifest.profile,
        domain=manifest.domain,
    )
    return decision


def evaluate_global_etf_rotation(ctx: StrategyContext) -> StrategyDecision:
    return _evaluate_global_etf_rotation_with_manifest(ctx, manifest=global_etf_rotation_manifest)


GLOBAL_ETF_ROTATION_LEGACY_DOC = "Legacy compute_signals adapter retained for platform compatibility."
legacy_global_etf_rotation.compute_signals.__doc__ = (
    (legacy_global_etf_rotation.compute_signals.__doc__ or "").strip() + "\n\nLegacy adapter: prefer us_equity_strategies entrypoints for new integrations."
).strip()


def evaluate_tqqq_growth_income(ctx: StrategyContext) -> StrategyDecision:
    config = merge_runtime_config(tqqq_growth_income_manifest.default_config, ctx)
    option_overlay_config = pop_option_overlay_config(config)
    managed_symbols = _config_managed_symbols(config)
    config.pop("managed_symbols", None)
    config.pop("benchmark_symbol", None)
    config.pop("signal_effective_after_trading_days", None)
    ai_extension_config = config.pop("ai_extensions", None)
    reserved_cash_policy = pop_reserved_cash_policy_config(config)
    pop_execution_only_config(config)
    apply_reserved_cash_policy_to_ratio_config(config, reserved_cash_policy)
    translator = config.pop("translator", default_translator)
    signal_text_fn = config.pop("signal_text_fn", default_signal_text_fn)
    plan = tqqq_growth_income_strategy.build_rebalance_plan(
        require_market_data(ctx, "benchmark_history"),
        require_portfolio(ctx),
        signal_text_fn=signal_text_fn,
        translator=translator,
        **config,
    )
    account_size_diagnostics = _account_size_diagnostics(tqqq_growth_income_manifest.profile, ctx)
    notification_context = dict(plan.get("notification_context") or {})
    signal_context = notification_context.get("signal")
    signal_state = str(signal_context.get("state") or "").strip() if isinstance(signal_context, Mapping) else ""
    raw_signal_display = str(plan.get("sig_display") or "").strip()
    if not raw_signal_display:
        raw_signal_display = (
            str(signal_text_fn(signal_state))
            if signal_state
            else str(plan["sig_display"])
        )
    signal_display = append_account_size_warning(
        raw_signal_display,
        account_size_diagnostics,
        translator=translator,
    )
    benchmark_text = _build_tqqq_benchmark_text(notification_context.get("benchmark")) or str(plan["dashboard"]).splitlines()[-1]
    dashboard_text = _build_dashboard_text(
        ctx,
        strategy_symbols=managed_symbols,
        translator=translator,
        signal_text=signal_display,
        benchmark_text=benchmark_text,
        portfolio_context=notification_context.get("portfolio"),
    )
    option_overlay_diagnostics = build_option_overlay_diagnostics(
        option_overlay_config,
        ctx,
        base_diagnostics={"notification_context": notification_context},
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
        "dual_drive_volatility_delever_enabled": plan.get("dual_drive_volatility_delever_enabled"),
        "dual_drive_volatility_delever_window": plan.get("dual_drive_volatility_delever_window"),
        "dual_drive_volatility_delever_threshold_mode": plan.get("dual_drive_volatility_delever_threshold_mode"),
        "dual_drive_volatility_delever_threshold": plan.get("dual_drive_volatility_delever_threshold"),
        "dual_drive_volatility_delever_exit_threshold": plan.get("dual_drive_volatility_delever_exit_threshold"),
        "dual_drive_volatility_delever_dynamic_threshold": plan.get(
            "dual_drive_volatility_delever_dynamic_threshold"
        ),
        "dual_drive_volatility_delever_dynamic_sample_count": plan.get(
            "dual_drive_volatility_delever_dynamic_sample_count"
        ),
        "dual_drive_volatility_delever_dynamic_lookback": plan.get(
            "dual_drive_volatility_delever_dynamic_lookback"
        ),
        "dual_drive_volatility_delever_dynamic_percentile": plan.get(
            "dual_drive_volatility_delever_dynamic_percentile"
        ),
        "dual_drive_volatility_delever_dynamic_min_periods": plan.get(
            "dual_drive_volatility_delever_dynamic_min_periods"
        ),
        "dual_drive_volatility_delever_dynamic_floor": plan.get("dual_drive_volatility_delever_dynamic_floor"),
        "dual_drive_volatility_delever_dynamic_cap": plan.get("dual_drive_volatility_delever_dynamic_cap"),
        "dual_drive_volatility_delever_metric": plan.get("dual_drive_volatility_delever_metric"),
        "dual_drive_volatility_delever_triggered": plan.get("dual_drive_volatility_delever_triggered"),
        "dual_drive_volatility_delever_entry_triggered": plan.get(
            "dual_drive_volatility_delever_entry_triggered"
        ),
        "dual_drive_volatility_delever_hysteresis_triggered": plan.get(
            "dual_drive_volatility_delever_hysteresis_triggered"
        ),
        "dual_drive_volatility_delever_trigger_reason": plan.get("dual_drive_volatility_delever_trigger_reason"),
        "dual_drive_volatility_delever_applied": plan.get("dual_drive_volatility_delever_applied"),
        "dual_drive_volatility_delever_vetoed": plan.get("dual_drive_volatility_delever_vetoed"),
        "dual_drive_volatility_delever_veto_reason": plan.get("dual_drive_volatility_delever_veto_reason"),
        "dual_drive_volatility_delever_taco_veto_enabled": plan.get(
            "dual_drive_volatility_delever_taco_veto_enabled"
        ),
        "dual_drive_volatility_delever_taco_rebound_context_active": plan.get(
            "dual_drive_volatility_delever_taco_rebound_context_active"
        ),
        "dual_drive_volatility_delever_true_crisis_active": plan.get(
            "dual_drive_volatility_delever_true_crisis_active"
        ),
        "dual_drive_volatility_delever_retention_mode": plan.get(
            "dual_drive_volatility_delever_retention_mode"
        ),
        "dual_drive_volatility_delever_retention_policy": plan.get(
            "dual_drive_volatility_delever_retention_policy"
        ),
        "dual_drive_volatility_delever_retention_ratio": plan.get(
            "dual_drive_volatility_delever_retention_ratio"
        ),
        "dual_drive_volatility_delever_retention_source": plan.get(
            "dual_drive_volatility_delever_retention_source"
        ),
        "dual_drive_volatility_delever_retention_context_found": plan.get(
            "dual_drive_volatility_delever_retention_context_found"
        ),
        "dual_drive_volatility_delever_retention_reason_codes": plan.get(
            "dual_drive_volatility_delever_retention_reason_codes"
        ),
        "dual_drive_volatility_delever_redirect_symbol": plan.get(
            "dual_drive_volatility_delever_redirect_symbol"
        ),
        "dual_drive_volatility_delever_removed_value": plan.get("dual_drive_volatility_delever_removed_value"),
        "market_regime_control_enabled": plan.get("market_regime_control_enabled"),
        "market_regime_control_found": plan.get("market_regime_control_found"),
        "market_regime_control_schema_version": plan.get("market_regime_control_schema_version"),
        "market_regime_control_route": plan.get("market_regime_control_route"),
        "market_regime_control_route_source": plan.get("market_regime_control_route_source"),
        "market_regime_control_active": plan.get("market_regime_control_active"),
        "market_regime_control_risk_budget_scalar": plan.get("market_regime_control_risk_budget_scalar"),
        "market_regime_control_leverage_scalar": plan.get("market_regime_control_leverage_scalar"),
        "market_regime_control_risk_asset_scalar": plan.get("market_regime_control_risk_asset_scalar"),
        "market_regime_control_crisis_defense_required": plan.get(
            "market_regime_control_crisis_defense_required"
        ),
        "market_regime_control_reason_codes": plan.get("market_regime_control_reason_codes"),
        "dual_drive_crisis_defense_enabled": plan.get("dual_drive_crisis_defense_enabled"),
        "dual_drive_crisis_defense_triggered": plan.get("dual_drive_crisis_defense_triggered"),
        "dual_drive_crisis_defense_applied": plan.get("dual_drive_crisis_defense_applied"),
        "dual_drive_crisis_defense_destination": plan.get("dual_drive_crisis_defense_destination"),
        "dual_drive_crisis_defense_removed_value": plan.get("dual_drive_crisis_defense_removed_value"),
        "real_buying_power": plan["real_buying_power"],
        "total_equity": plan["total_equity"],
        **account_size_diagnostics,
        **option_overlay_diagnostics,
        "ai_extensions": build_ai_extension_diagnostics(
            ai_extension_config,
            signals=(
                ctx.state.get(AI_EXTENSION_SIGNAL_STATE_KEY)
                or ctx.artifacts.get(AI_EXTENSION_SIGNAL_STATE_KEY)
                or ctx.market_data.get(AI_EXTENSION_SIGNAL_STATE_KEY)
            ),
        ),
        "execution_annotations": {
            "trade_threshold_value": plan["threshold"],
            "raw_buying_power": plan["real_buying_power"],
            "reserved_cash": plan["reserved"],
            "investable_cash": plan["investable_buying_power"],
            "signal_display": signal_display,
            "dashboard_text": dashboard_text or plan["dashboard"],
            "benchmark_symbol": "QQQ",
            "benchmark_price": plan["qqq_p"],
            "long_trend_value": plan["ma200"],
            "exit_line": plan["exit_line"],
            "dual_drive_volatility_delever_enabled": plan.get("dual_drive_volatility_delever_enabled"),
            "dual_drive_volatility_delever_window": plan.get("dual_drive_volatility_delever_window"),
            "dual_drive_volatility_delever_threshold_mode": plan.get(
                "dual_drive_volatility_delever_threshold_mode"
            ),
            "dual_drive_volatility_delever_threshold": plan.get("dual_drive_volatility_delever_threshold"),
            "dual_drive_volatility_delever_exit_threshold": plan.get(
                "dual_drive_volatility_delever_exit_threshold"
            ),
            "dual_drive_volatility_delever_dynamic_threshold": plan.get(
                "dual_drive_volatility_delever_dynamic_threshold"
            ),
            "dual_drive_volatility_delever_dynamic_sample_count": plan.get(
                "dual_drive_volatility_delever_dynamic_sample_count"
            ),
            "dual_drive_volatility_delever_dynamic_lookback": plan.get(
                "dual_drive_volatility_delever_dynamic_lookback"
            ),
            "dual_drive_volatility_delever_dynamic_percentile": plan.get(
                "dual_drive_volatility_delever_dynamic_percentile"
            ),
            "dual_drive_volatility_delever_dynamic_min_periods": plan.get(
                "dual_drive_volatility_delever_dynamic_min_periods"
            ),
            "dual_drive_volatility_delever_dynamic_floor": plan.get("dual_drive_volatility_delever_dynamic_floor"),
            "dual_drive_volatility_delever_dynamic_cap": plan.get("dual_drive_volatility_delever_dynamic_cap"),
            "dual_drive_volatility_delever_metric": plan.get("dual_drive_volatility_delever_metric"),
            "dual_drive_volatility_delever_triggered": plan.get("dual_drive_volatility_delever_triggered"),
            "dual_drive_volatility_delever_entry_triggered": plan.get(
                "dual_drive_volatility_delever_entry_triggered"
            ),
            "dual_drive_volatility_delever_hysteresis_triggered": plan.get(
                "dual_drive_volatility_delever_hysteresis_triggered"
            ),
            "dual_drive_volatility_delever_trigger_reason": plan.get(
                "dual_drive_volatility_delever_trigger_reason"
            ),
            "dual_drive_volatility_delever_applied": plan.get("dual_drive_volatility_delever_applied"),
            "dual_drive_volatility_delever_vetoed": plan.get("dual_drive_volatility_delever_vetoed"),
            "dual_drive_volatility_delever_veto_reason": plan.get(
                "dual_drive_volatility_delever_veto_reason"
            ),
            "dual_drive_volatility_delever_taco_veto_enabled": plan.get(
                "dual_drive_volatility_delever_taco_veto_enabled"
            ),
            "dual_drive_volatility_delever_taco_rebound_context_active": plan.get(
                "dual_drive_volatility_delever_taco_rebound_context_active"
            ),
            "dual_drive_volatility_delever_true_crisis_active": plan.get(
                "dual_drive_volatility_delever_true_crisis_active"
            ),
            "dual_drive_volatility_delever_retention_mode": plan.get(
                "dual_drive_volatility_delever_retention_mode"
            ),
            "dual_drive_volatility_delever_retention_policy": plan.get(
                "dual_drive_volatility_delever_retention_policy"
            ),
            "dual_drive_volatility_delever_retention_ratio": plan.get(
                "dual_drive_volatility_delever_retention_ratio"
            ),
            "dual_drive_volatility_delever_retention_source": plan.get(
                "dual_drive_volatility_delever_retention_source"
            ),
            "dual_drive_volatility_delever_retention_context_found": plan.get(
                "dual_drive_volatility_delever_retention_context_found"
            ),
            "dual_drive_volatility_delever_retention_reason_codes": plan.get(
                "dual_drive_volatility_delever_retention_reason_codes"
            ),
            "dual_drive_volatility_delever_redirect_symbol": plan.get(
                "dual_drive_volatility_delever_redirect_symbol"
            ),
            "dual_drive_volatility_delever_removed_value": plan.get(
                "dual_drive_volatility_delever_removed_value"
            ),
            "market_regime_control_enabled": plan.get("market_regime_control_enabled"),
            "market_regime_control_found": plan.get("market_regime_control_found"),
            "market_regime_control_route": plan.get("market_regime_control_route"),
            "market_regime_control_active": plan.get("market_regime_control_active"),
            "dual_drive_crisis_defense_enabled": plan.get("dual_drive_crisis_defense_enabled"),
            "dual_drive_crisis_defense_triggered": plan.get("dual_drive_crisis_defense_triggered"),
            "dual_drive_crisis_defense_applied": plan.get("dual_drive_crisis_defense_applied"),
            "dual_drive_crisis_defense_destination": plan.get("dual_drive_crisis_defense_destination"),
        },
    }
    _attach_notification_context(diagnostics, notification_context)
    _attach_execution_timing(diagnostics, ctx)
    decision = StrategyDecision(
        positions=target_values_to_positions(plan["target_values"]),
        diagnostics=diagnostics,
    )
    decision = apply_risk_gate(decision, max_single_weight=0.20)
    record_strategy_decision(
        ctx,
        decision,
        profile_id=tqqq_growth_income_manifest.profile,
        domain=tqqq_growth_income_manifest.domain,
    )
    return decision


tqqq_growth_income_strategy.build_rebalance_plan.__doc__ = (
    ((tqqq_growth_income_strategy.build_rebalance_plan.__doc__ or "").strip() +
     "\n\nLegacy adapter: prefer us_equity_strategies entrypoints for new integrations.")
    .strip()
)


def _build_tiered_blend_account_state_from_portfolio(portfolio, *, strategy_symbols: tuple[str, ...]) -> dict[str, object]:
    market_values = {symbol: 0.0 for symbol in strategy_symbols}
    quantities = {symbol: 0.0 for symbol in strategy_symbols}
    sellable_quantities = {symbol: 0.0 for symbol in strategy_symbols}
    metadata = getattr(portfolio, "metadata", {}) or {}
    raw_sellable_quantities = metadata.get("sellable_quantities") if isinstance(metadata, Mapping) else {}
    for position in getattr(portfolio, "positions", ()):
        if position.symbol not in market_values:
            continue
        market_values[position.symbol] = float(position.market_value)
        quantity = float(position.quantity)
        quantities[position.symbol] = quantity
        sellable_quantities[position.symbol] = float(
            raw_sellable_quantities.get(position.symbol, quantity)
            if isinstance(raw_sellable_quantities, Mapping)
            else quantity
        )
    raw_cash = resolve_raw_cash_from_snapshot(portfolio)
    return {
        "available_cash": raw_cash,
        "market_values": market_values,
        "quantities": quantities,
        "sellable_quantities": sellable_quantities,
        "total_strategy_equity": compute_strategy_total_equity(market_values, raw_cash),
        "metadata": dict(metadata) if isinstance(metadata, Mapping) else {},
    }


def evaluate_soxl_soxx_trend_income(ctx: StrategyContext) -> StrategyDecision:
    config = merge_runtime_config(soxl_soxx_trend_income_manifest.default_config, ctx)
    option_overlay_config = pop_option_overlay_config(config)
    strategy_symbols = tuple(str(symbol) for symbol in config.pop("managed_symbols", ()))
    config.pop("signal_text_fn", None)
    config.pop("signal_effective_after_trading_days", None)
    reserved_cash_policy = pop_reserved_cash_policy_config(config)
    pop_execution_only_config(config)
    apply_reserved_cash_policy_to_ratio_config(config, reserved_cash_policy)
    portfolio = require_portfolio(ctx)
    translator = config.pop("translator", default_translator)
    plan = soxl_soxx_trend_income_strategy.build_rebalance_plan(
        require_market_data(ctx, "derived_indicators"),
        _build_tiered_blend_account_state_from_portfolio(
            portfolio,
            strategy_symbols=strategy_symbols,
        ),
        translator=translator,
        **config,
    )
    account_size_diagnostics = _account_size_diagnostics(soxl_soxx_trend_income_manifest.profile, ctx)
    notification_context = dict(plan.get("notification_context") or {})
    rendered_market_status = _render_translation_context(
        notification_context.get("status"),
        translator=translator,
        fallback=str(plan["market_status"]),
    )
    raw_signal_message = _render_translation_context(
        notification_context.get("signal"),
        translator=translator,
        fallback=str(plan["signal_message"]),
    )
    signal_message = append_account_size_warning(
        raw_signal_message,
        account_size_diagnostics,
        translator=translator,
    )
    dashboard_text = _build_dashboard_text(
        ctx,
        strategy_symbols=strategy_symbols,
        translator=translator,
        signal_text=signal_message,
        portfolio_context=notification_context.get("portfolio"),
    )
    option_overlay_diagnostics = build_option_overlay_diagnostics(
        option_overlay_config,
        ctx,
        base_diagnostics={
            "blend_tier": plan.get("blend_tier"),
            "active_risk_asset": plan["active_risk_asset"],
            "notification_context": notification_context,
        },
    )
    diagnostics = {
        "market_status": rendered_market_status,
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
        **option_overlay_diagnostics,
        "allocation_mode": plan.get("allocation_mode"),
        "trend_entry_buffer": plan.get("trend_entry_buffer"),
        "trend_mid_buffer": plan.get("trend_mid_buffer"),
        "trend_exit_buffer": plan.get("trend_exit_buffer"),
        "blend_tier": plan.get("blend_tier"),
        "base_blend_tier": plan.get("base_blend_tier"),
        "overlay_trigger_count": plan.get("overlay_trigger_count"),
        "overlay_trigger_reasons": plan.get("overlay_trigger_reasons"),
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
        "trend_rsi14": plan.get("trend_rsi14"),
        "trend_rsi14_dynamic_threshold": plan.get("trend_rsi14_dynamic_threshold"),
        "trend_rsi14_effective_threshold": plan.get("trend_rsi14_effective_threshold"),
        "trend_bb_upper": plan.get("trend_bb_upper"),
        "blend_gate_volatility_delever_enabled": plan.get("blend_gate_volatility_delever_enabled"),
        "blend_gate_volatility_delever_symbol": plan.get("blend_gate_volatility_delever_symbol"),
        "blend_gate_volatility_delever_window": plan.get("blend_gate_volatility_delever_window"),
        "blend_gate_volatility_delever_threshold": plan.get("blend_gate_volatility_delever_threshold"),
        "blend_gate_volatility_delever_threshold_mode": plan.get("blend_gate_volatility_delever_threshold_mode"),
        "blend_gate_volatility_delever_dynamic_threshold": plan.get(
            "blend_gate_volatility_delever_dynamic_threshold"
        ),
        "blend_gate_volatility_delever_dynamic_sample_count": plan.get(
            "blend_gate_volatility_delever_dynamic_sample_count"
        ),
        "blend_gate_volatility_delever_dynamic_lookback": plan.get(
            "blend_gate_volatility_delever_dynamic_lookback"
        ),
        "blend_gate_volatility_delever_dynamic_percentile": plan.get(
            "blend_gate_volatility_delever_dynamic_percentile"
        ),
        "blend_gate_volatility_delever_dynamic_min_periods": plan.get(
            "blend_gate_volatility_delever_dynamic_min_periods"
        ),
        "blend_gate_volatility_delever_dynamic_floor": plan.get("blend_gate_volatility_delever_dynamic_floor"),
        "blend_gate_volatility_delever_dynamic_cap": plan.get("blend_gate_volatility_delever_dynamic_cap"),
        "blend_gate_volatility_delever_metric": plan.get("blend_gate_volatility_delever_metric"),
        "blend_gate_volatility_delever_triggered": plan.get("blend_gate_volatility_delever_triggered"),
        "blend_gate_volatility_delever_retention_ratio": plan.get("blend_gate_volatility_delever_retention_ratio"),
        "blend_gate_volatility_delever_retention_mode": plan.get(
            "blend_gate_volatility_delever_retention_mode"
        ),
        "blend_gate_volatility_delever_retention_policy": plan.get(
            "blend_gate_volatility_delever_retention_policy"
        ),
        "blend_gate_volatility_delever_effective_retention_ratio": plan.get(
            "blend_gate_volatility_delever_effective_retention_ratio"
        ),
        "blend_gate_volatility_delever_retention_source": plan.get(
            "blend_gate_volatility_delever_retention_source"
        ),
        "blend_gate_volatility_delever_retention_context_found": plan.get(
            "blend_gate_volatility_delever_retention_context_found"
        ),
        "blend_gate_volatility_delever_retention_reason_codes": plan.get(
            "blend_gate_volatility_delever_retention_reason_codes"
        ),
        "blend_gate_volatility_delever_redirect_symbol": plan.get("blend_gate_volatility_delever_redirect_symbol"),
        "blend_gate_volatility_delever_removed_ratio": plan.get("blend_gate_volatility_delever_removed_ratio"),
        "market_regime_control_enabled": plan.get("market_regime_control_enabled"),
        "market_regime_control_found": plan.get("market_regime_control_found"),
        "market_regime_control_source": plan.get("market_regime_control_source"),
        "market_regime_control_schema_version": plan.get("market_regime_control_schema_version"),
        "market_regime_control_route": plan.get("market_regime_control_route"),
        "market_regime_control_route_source": plan.get("market_regime_control_route_source"),
        "market_regime_control_active": plan.get("market_regime_control_active"),
        "market_regime_control_route_allowed": plan.get("market_regime_control_route_allowed"),
        "market_regime_control_applied": plan.get("market_regime_control_applied"),
        "market_regime_control_risk_budget_scalar": plan.get("market_regime_control_risk_budget_scalar"),
        "market_regime_control_leverage_scalar": plan.get("market_regime_control_leverage_scalar"),
        "market_regime_control_risk_asset_scalar": plan.get("market_regime_control_risk_asset_scalar"),
        "market_regime_control_crisis_defense_required": plan.get("market_regime_control_crisis_defense_required"),
        "market_regime_control_reason_codes": plan.get("market_regime_control_reason_codes"),
        "market_regime_control_removed_ratio": plan.get("market_regime_control_removed_ratio"),
        "market_regime_control_redirected_to_unlevered_ratio": plan.get(
            "market_regime_control_redirected_to_unlevered_ratio"
        ),
        **account_size_diagnostics,
        "execution_annotations": {
            "trade_threshold_value": plan["threshold_value"],
            "signal_display": signal_message,
            "status_display": rendered_market_status,
            "dashboard_text": dashboard_text,
            "raw_buying_power": plan["available_cash"],
            "reserved_cash": plan["reserved_cash"],
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
            "base_blend_tier": plan.get("base_blend_tier"),
            "overlay_trigger_count": plan.get("overlay_trigger_count"),
            "overlay_trigger_reasons": plan.get("overlay_trigger_reasons"),
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
            "trend_rsi14": plan.get("trend_rsi14"),
            "trend_rsi14_dynamic_threshold": plan.get("trend_rsi14_dynamic_threshold"),
            "trend_rsi14_effective_threshold": plan.get("trend_rsi14_effective_threshold"),
            "trend_bb_upper": plan.get("trend_bb_upper"),
            "blend_gate_volatility_delever_enabled": plan.get("blend_gate_volatility_delever_enabled"),
            "blend_gate_volatility_delever_symbol": plan.get("blend_gate_volatility_delever_symbol"),
            "blend_gate_volatility_delever_window": plan.get("blend_gate_volatility_delever_window"),
            "blend_gate_volatility_delever_threshold": plan.get("blend_gate_volatility_delever_threshold"),
            "blend_gate_volatility_delever_threshold_mode": plan.get("blend_gate_volatility_delever_threshold_mode"),
            "blend_gate_volatility_delever_dynamic_threshold": plan.get(
                "blend_gate_volatility_delever_dynamic_threshold"
            ),
            "blend_gate_volatility_delever_dynamic_sample_count": plan.get(
                "blend_gate_volatility_delever_dynamic_sample_count"
            ),
            "blend_gate_volatility_delever_dynamic_lookback": plan.get(
                "blend_gate_volatility_delever_dynamic_lookback"
            ),
            "blend_gate_volatility_delever_dynamic_percentile": plan.get(
                "blend_gate_volatility_delever_dynamic_percentile"
            ),
            "blend_gate_volatility_delever_dynamic_min_periods": plan.get(
                "blend_gate_volatility_delever_dynamic_min_periods"
            ),
            "blend_gate_volatility_delever_dynamic_floor": plan.get("blend_gate_volatility_delever_dynamic_floor"),
            "blend_gate_volatility_delever_dynamic_cap": plan.get("blend_gate_volatility_delever_dynamic_cap"),
            "blend_gate_volatility_delever_metric": plan.get("blend_gate_volatility_delever_metric"),
            "blend_gate_volatility_delever_triggered": plan.get("blend_gate_volatility_delever_triggered"),
            "blend_gate_volatility_delever_retention_ratio": plan.get("blend_gate_volatility_delever_retention_ratio"),
            "blend_gate_volatility_delever_retention_mode": plan.get(
                "blend_gate_volatility_delever_retention_mode"
            ),
            "blend_gate_volatility_delever_retention_policy": plan.get(
                "blend_gate_volatility_delever_retention_policy"
            ),
            "blend_gate_volatility_delever_effective_retention_ratio": plan.get(
                "blend_gate_volatility_delever_effective_retention_ratio"
            ),
            "blend_gate_volatility_delever_retention_source": plan.get(
                "blend_gate_volatility_delever_retention_source"
            ),
            "blend_gate_volatility_delever_retention_context_found": plan.get(
                "blend_gate_volatility_delever_retention_context_found"
            ),
            "blend_gate_volatility_delever_retention_reason_codes": plan.get(
                "blend_gate_volatility_delever_retention_reason_codes"
            ),
            "blend_gate_volatility_delever_redirect_symbol": plan.get("blend_gate_volatility_delever_redirect_symbol"),
            "blend_gate_volatility_delever_removed_ratio": plan.get("blend_gate_volatility_delever_removed_ratio"),
            "market_regime_control_enabled": plan.get("market_regime_control_enabled"),
        },
    }
    _attach_notification_context(diagnostics, notification_context)
    _attach_execution_timing(diagnostics, ctx)
    decision = StrategyDecision(
        positions=target_values_to_positions(plan["targets"]),
        diagnostics=diagnostics,
    )
    decision = apply_risk_gate(decision, max_single_weight=0.20)
    record_strategy_decision(
        ctx,
        decision,
        profile_id=soxl_soxx_trend_income_manifest.profile,
        domain=soxl_soxx_trend_income_manifest.domain,
    )
    return decision


def evaluate_tecl_xlk_trend_income(ctx: StrategyContext) -> StrategyDecision:
    config = merge_runtime_config(tecl_xlk_trend_income_manifest.default_config, ctx)
    option_overlay_config = pop_option_overlay_config(config)
    strategy_symbols = tuple(str(symbol) for symbol in config.pop("managed_symbols", ()))
    config.pop("signal_text_fn", None)
    config.pop("signal_effective_after_trading_days", None)
    reserved_cash_policy = pop_reserved_cash_policy_config(config)
    pop_execution_only_config(config)
    apply_reserved_cash_policy_to_ratio_config(config, reserved_cash_policy)
    portfolio = require_portfolio(ctx)
    translator = config.pop("translator", default_translator)
    plan = tecl_xlk_trend_income_strategy.build_rebalance_plan(
        require_market_data(ctx, "derived_indicators"),
        _build_tiered_blend_account_state_from_portfolio(
            portfolio,
            strategy_symbols=strategy_symbols,
        ),
        translator=translator,
        **config,
    )
    account_size_diagnostics = _account_size_diagnostics(tecl_xlk_trend_income_manifest.profile, ctx)
    notification_context = dict(plan.get("notification_context") or {})
    rendered_market_status = _render_translation_context(
        notification_context.get("status"),
        translator=translator,
        fallback=str(plan["market_status"]),
    )
    raw_signal_message = _render_translation_context(
        notification_context.get("signal"),
        translator=translator,
        fallback=str(plan["signal_message"]),
    )
    signal_message = append_account_size_warning(
        raw_signal_message,
        account_size_diagnostics,
        translator=translator,
    )
    dashboard_text = _build_dashboard_text(
        ctx,
        strategy_symbols=strategy_symbols,
        translator=translator,
        signal_text=signal_message,
        portfolio_context=notification_context.get("portfolio"),
    )
    option_overlay_diagnostics = build_option_overlay_diagnostics(
        option_overlay_config,
        ctx,
        base_diagnostics={
            "blend_tier": plan.get("blend_tier"),
            "active_risk_asset": plan["active_risk_asset"],
            "notification_context": notification_context,
        },
    )
    diagnostics = {
        "market_status": rendered_market_status,
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
        **option_overlay_diagnostics,
        "allocation_mode": plan.get("allocation_mode"),
        "trend_entry_buffer": plan.get("trend_entry_buffer"),
        "trend_mid_buffer": plan.get("trend_mid_buffer"),
        "trend_exit_buffer": plan.get("trend_exit_buffer"),
        "blend_tier": plan.get("blend_tier"),
        "base_blend_tier": plan.get("base_blend_tier"),
        "overlay_trigger_count": plan.get("overlay_trigger_count"),
        "overlay_trigger_reasons": plan.get("overlay_trigger_reasons"),
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
        "trend_rsi14": plan.get("trend_rsi14"),
        "trend_rsi14_dynamic_threshold": plan.get("trend_rsi14_dynamic_threshold"),
        "trend_rsi14_effective_threshold": plan.get("trend_rsi14_effective_threshold"),
        "trend_bb_upper": plan.get("trend_bb_upper"),
        "blend_gate_volatility_delever_enabled": plan.get("blend_gate_volatility_delever_enabled"),
        "blend_gate_volatility_delever_symbol": plan.get("blend_gate_volatility_delever_symbol"),
        "blend_gate_volatility_delever_window": plan.get("blend_gate_volatility_delever_window"),
        "blend_gate_volatility_delever_threshold": plan.get("blend_gate_volatility_delever_threshold"),
        "blend_gate_volatility_delever_threshold_mode": plan.get("blend_gate_volatility_delever_threshold_mode"),
        "blend_gate_volatility_delever_dynamic_threshold": plan.get(
            "blend_gate_volatility_delever_dynamic_threshold"
        ),
        "blend_gate_volatility_delever_dynamic_sample_count": plan.get(
            "blend_gate_volatility_delever_dynamic_sample_count"
        ),
        "blend_gate_volatility_delever_dynamic_lookback": plan.get(
            "blend_gate_volatility_delever_dynamic_lookback"
        ),
        "blend_gate_volatility_delever_dynamic_percentile": plan.get(
            "blend_gate_volatility_delever_dynamic_percentile"
        ),
        "blend_gate_volatility_delever_dynamic_min_periods": plan.get(
            "blend_gate_volatility_delever_dynamic_min_periods"
        ),
        "blend_gate_volatility_delever_dynamic_floor": plan.get("blend_gate_volatility_delever_dynamic_floor"),
        "blend_gate_volatility_delever_dynamic_cap": plan.get("blend_gate_volatility_delever_dynamic_cap"),
        "blend_gate_volatility_delever_metric": plan.get("blend_gate_volatility_delever_metric"),
        "blend_gate_volatility_delever_triggered": plan.get("blend_gate_volatility_delever_triggered"),
        "blend_gate_volatility_delever_retention_ratio": plan.get("blend_gate_volatility_delever_retention_ratio"),
        "blend_gate_volatility_delever_retention_mode": plan.get(
            "blend_gate_volatility_delever_retention_mode"
        ),
        "blend_gate_volatility_delever_retention_policy": plan.get(
            "blend_gate_volatility_delever_retention_policy"
        ),
        "blend_gate_volatility_delever_effective_retention_ratio": plan.get(
            "blend_gate_volatility_delever_effective_retention_ratio"
        ),
        "blend_gate_volatility_delever_retention_source": plan.get(
            "blend_gate_volatility_delever_retention_source"
        ),
        "blend_gate_volatility_delever_retention_context_found": plan.get(
            "blend_gate_volatility_delever_retention_context_found"
        ),
        "blend_gate_volatility_delever_retention_reason_codes": plan.get(
            "blend_gate_volatility_delever_retention_reason_codes"
        ),
        "blend_gate_volatility_delever_redirect_symbol": plan.get("blend_gate_volatility_delever_redirect_symbol"),
        "blend_gate_volatility_delever_removed_ratio": plan.get("blend_gate_volatility_delever_removed_ratio"),
        "market_regime_control_enabled": plan.get("market_regime_control_enabled"),
        "market_regime_control_found": plan.get("market_regime_control_found"),
        "market_regime_control_source": plan.get("market_regime_control_source"),
        "market_regime_control_schema_version": plan.get("market_regime_control_schema_version"),
        "market_regime_control_route": plan.get("market_regime_control_route"),
        "market_regime_control_route_source": plan.get("market_regime_control_route_source"),
        "market_regime_control_active": plan.get("market_regime_control_active"),
        "market_regime_control_route_allowed": plan.get("market_regime_control_route_allowed"),
        "market_regime_control_applied": plan.get("market_regime_control_applied"),
        "market_regime_control_risk_budget_scalar": plan.get("market_regime_control_risk_budget_scalar"),
        "market_regime_control_leverage_scalar": plan.get("market_regime_control_leverage_scalar"),
        "market_regime_control_risk_asset_scalar": plan.get("market_regime_control_risk_asset_scalar"),
        "market_regime_control_crisis_defense_required": plan.get("market_regime_control_crisis_defense_required"),
        "market_regime_control_reason_codes": plan.get("market_regime_control_reason_codes"),
        "market_regime_control_removed_ratio": plan.get("market_regime_control_removed_ratio"),
        "market_regime_control_redirected_to_unlevered_ratio": plan.get(
            "market_regime_control_redirected_to_unlevered_ratio"
        ),
        **account_size_diagnostics,
        "execution_annotations": {
            "trade_threshold_value": plan["threshold_value"],
            "signal_display": signal_message,
            "status_display": rendered_market_status,
            "dashboard_text": dashboard_text,
            "raw_buying_power": plan["available_cash"],
            "reserved_cash": plan["reserved_cash"],
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
            "base_blend_tier": plan.get("base_blend_tier"),
            "overlay_trigger_count": plan.get("overlay_trigger_count"),
            "overlay_trigger_reasons": plan.get("overlay_trigger_reasons"),
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
            "trend_rsi14": plan.get("trend_rsi14"),
            "trend_rsi14_dynamic_threshold": plan.get("trend_rsi14_dynamic_threshold"),
            "trend_rsi14_effective_threshold": plan.get("trend_rsi14_effective_threshold"),
            "trend_bb_upper": plan.get("trend_bb_upper"),
            "blend_gate_volatility_delever_enabled": plan.get("blend_gate_volatility_delever_enabled"),
            "blend_gate_volatility_delever_symbol": plan.get("blend_gate_volatility_delever_symbol"),
            "blend_gate_volatility_delever_window": plan.get("blend_gate_volatility_delever_window"),
            "blend_gate_volatility_delever_threshold": plan.get("blend_gate_volatility_delever_threshold"),
            "blend_gate_volatility_delever_threshold_mode": plan.get("blend_gate_volatility_delever_threshold_mode"),
            "blend_gate_volatility_delever_dynamic_threshold": plan.get(
                "blend_gate_volatility_delever_dynamic_threshold"
            ),
            "blend_gate_volatility_delever_dynamic_sample_count": plan.get(
                "blend_gate_volatility_delever_dynamic_sample_count"
            ),
            "blend_gate_volatility_delever_dynamic_lookback": plan.get(
                "blend_gate_volatility_delever_dynamic_lookback"
            ),
            "blend_gate_volatility_delever_dynamic_percentile": plan.get(
                "blend_gate_volatility_delever_dynamic_percentile"
            ),
            "blend_gate_volatility_delever_dynamic_min_periods": plan.get(
                "blend_gate_volatility_delever_dynamic_min_periods"
            ),
            "blend_gate_volatility_delever_dynamic_floor": plan.get("blend_gate_volatility_delever_dynamic_floor"),
            "blend_gate_volatility_delever_dynamic_cap": plan.get("blend_gate_volatility_delever_dynamic_cap"),
            "blend_gate_volatility_delever_metric": plan.get("blend_gate_volatility_delever_metric"),
            "blend_gate_volatility_delever_triggered": plan.get("blend_gate_volatility_delever_triggered"),
            "blend_gate_volatility_delever_retention_ratio": plan.get("blend_gate_volatility_delever_retention_ratio"),
            "blend_gate_volatility_delever_retention_mode": plan.get(
                "blend_gate_volatility_delever_retention_mode"
            ),
            "blend_gate_volatility_delever_retention_policy": plan.get(
                "blend_gate_volatility_delever_retention_policy"
            ),
            "blend_gate_volatility_delever_effective_retention_ratio": plan.get(
                "blend_gate_volatility_delever_effective_retention_ratio"
            ),
            "blend_gate_volatility_delever_retention_source": plan.get(
                "blend_gate_volatility_delever_retention_source"
            ),
            "blend_gate_volatility_delever_retention_context_found": plan.get(
                "blend_gate_volatility_delever_retention_context_found"
            ),
            "blend_gate_volatility_delever_retention_reason_codes": plan.get(
                "blend_gate_volatility_delever_retention_reason_codes"
            ),
            "blend_gate_volatility_delever_redirect_symbol": plan.get("blend_gate_volatility_delever_redirect_symbol"),
            "blend_gate_volatility_delever_removed_ratio": plan.get("blend_gate_volatility_delever_removed_ratio"),
            "market_regime_control_enabled": plan.get("market_regime_control_enabled"),
        },
    }
    _attach_notification_context(diagnostics, notification_context)
    _attach_execution_timing(diagnostics, ctx)
    decision = StrategyDecision(
        positions=target_values_to_positions(plan["targets"]),
        diagnostics=diagnostics,
    )
    decision = apply_risk_gate(decision, max_single_weight=0.20)
    record_strategy_decision(
        ctx,
        decision,
        profile_id=tecl_xlk_trend_income_manifest.profile,
        domain=tecl_xlk_trend_income_manifest.domain,
    )
    return decision

soxl_soxx_trend_income_strategy.build_rebalance_plan.__doc__ = (
    ((soxl_soxx_trend_income_strategy.build_rebalance_plan.__doc__ or "").strip() +
     "\n\nLegacy adapter: prefer us_equity_strategies entrypoints for new integrations.")
    .strip()
)

tecl_xlk_trend_income_strategy.build_rebalance_plan.__doc__ = (
    ((tecl_xlk_trend_income_strategy.build_rebalance_plan.__doc__ or "").strip() +
     "\n\nLegacy adapter: prefer us_equity_strategies entrypoints for new integrations.")
    .strip()
)


def _evaluate_mega_cap_leader_rotation_snapshot_profile(
    ctx: StrategyContext,
    *,
    manifest,
) -> StrategyDecision:
    config = merge_runtime_config(manifest.default_config, ctx)
    income_layer_config = pop_income_layer_config(config)
    option_overlay_config = pop_option_overlay_config(config)
    market_regime_control_config = pop_market_regime_control_config(config)
    translator = config.get("translator", default_translator)
    config.pop("signal_effective_after_trading_days", None)
    pop_execution_only_config(config)
    if ctx.as_of is not None and "run_as_of" not in config:
        config["run_as_of"] = ctx.as_of
    if ctx.portfolio is not None and "portfolio_total_equity" not in config:
        total_equity = getattr(ctx.portfolio, "total_equity", None)
        if total_equity is not None:
            config["portfolio_total_equity"] = float(total_equity)
    weights, signal_desc, is_emergency, status_desc, metadata = mega_cap_leader_rotation_strategy.compute_signals(
        require_market_data(ctx, "feature_snapshot"),
        get_current_holdings(ctx),
        **config,
    )
    weights, income_layer_diagnostics = apply_income_layer_to_weights(
        weights,
        income_layer_config=income_layer_config,
        ctx=ctx,
        excluded_symbols=(
            config.get("safe_haven"),
            config.get("benchmark_symbol"),
            config.get("broad_benchmark_symbol"),
        ),
    )
    weights, market_regime_control_diagnostics = apply_market_regime_control_to_weights(
        weights,
        market_regime_control_config=market_regime_control_config,
        ctx=ctx,
        safe_haven=str(config.get("safe_haven", "BOXX")),
        excluded_symbols=(
            config.get("safe_haven"),
            config.get("benchmark_symbol"),
            config.get("broad_benchmark_symbol"),
        ),
    )
    rendered_signal_desc, rendered_status_desc, notification_context = _render_notification_displays(
        signal_desc,
        status_desc,
        metadata,
        translator=translator,
    )
    notification_context = _merge_notification_contexts(
        notification_context,
        market_regime_control_diagnostics.get("market_regime_control_notification_context"),
    )
    option_overlay_diagnostics = build_option_overlay_diagnostics(
        option_overlay_config,
        ctx,
        base_diagnostics=metadata,
    )
    diagnostics = {
        **metadata,
        **income_layer_diagnostics,
        **market_regime_control_diagnostics,
        **option_overlay_diagnostics,
        "signal_description": rendered_signal_desc,
        "status_description": rendered_status_desc,
        "signal_source": mega_cap_leader_rotation_strategy.SIGNAL_SOURCE,
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
    _attach_notification_context(diagnostics, notification_context)
    risk_flags: tuple[str, ...] = ()
    if is_emergency:
        risk_flags += ("hard_defense",)
    if weights is None:
        risk_flags += ("no_execute",)
    decision = StrategyDecision(
        positions=weights_to_positions(weights, safe_haven=str(config.get("safe_haven", "BOXX"))),
        risk_flags=risk_flags,
        diagnostics=diagnostics,
    )
    decision = apply_risk_gate(decision)
    record_strategy_decision(
        ctx,
        decision,
        profile_id=manifest.profile,
        domain=manifest.domain,
    )
    return decision


def evaluate_russell_top50_leader_rotation(ctx: StrategyContext) -> StrategyDecision:
    return _evaluate_mega_cap_leader_rotation_snapshot_profile(
        ctx,
        manifest=russell_top50_leader_rotation_manifest,
    )


mega_cap_leader_rotation_strategy.compute_signals.__doc__ = (
    ((mega_cap_leader_rotation_strategy.compute_signals.__doc__ or "").strip() +
     "\n\nLegacy adapter: prefer us_equity_strategies entrypoints for new integrations.")
    .strip()
)


def evaluate_nasdaq_sp500_smart_dca(ctx: StrategyContext) -> StrategyDecision:
    config = merge_runtime_config(nasdaq_sp500_smart_dca_manifest.default_config, ctx)
    option_overlay_config = pop_option_overlay_config(config)
    translator = config.pop("translator", default_translator)
    config.pop("signal_effective_after_trading_days", None)
    config.pop("pacing_sec", None)
    reserved_cash_policy = pop_reserved_cash_policy_config(config)
    pop_execution_only_config(config)
    market_history = ctx.market_data.get("market_history")
    if market_history is None:
        def _empty_market_history(_client, _symbol):
            return ()

        market_history = _empty_market_history
    technical_indicator_snapshot = (
        ctx.market_data.get("technical_indicator_snapshot")
        or ctx.market_data.get("derived_indicators")
    )
    portfolio = require_portfolio(ctx)
    apply_reserved_cash_policy_to_usd_config(
        config,
        reserved_cash_policy,
        total_equity=float(getattr(portfolio, "total_equity", 0.0) or 0.0),
    )
    plan = nasdaq_sp500_smart_dca_strategy.build_rebalance_plan(
        market_history,
        portfolio,
        as_of=ctx.as_of,
        technical_indicator_snapshot=technical_indicator_snapshot,
        broker_client=ctx.capabilities.get("broker_client"),
        translator=translator,
        **config,
    )
    diagnostics = {
        "signal_description": plan["signal_description"],
        "status_description": plan["status_description"],
        "signal_source": nasdaq_sp500_smart_dca_strategy.SIGNAL_SOURCE,
        "actionable": plan["actionable"],
        "skip_reason": plan["skip_reason"],
        "regime": plan["regime"],
        "multiplier": plan["multiplier"],
        "regime_multiplier": plan["regime_multiplier"],
        "smart_multiplier_enabled": plan["smart_multiplier_enabled"],
        "investment_amount_mode": plan["investment_amount_mode"],
        "base_investment_usd": plan["base_investment_usd"],
        "base_investment_budget_usd": plan["base_investment_budget_usd"],
        "requested_investment_usd": plan["requested_investment_usd"],
        "planned_investment_usd": plan["planned_investment_usd"],
        "available_cash": plan["available_cash"],
        "reserved_cash": plan["reserved_cash"],
        "investable_cash": plan["investable_cash"],
        "min_investment_usd": plan["min_investment_usd"],
        "execution_window": plan["execution_window"],
        "in_execution_window": plan["in_execution_window"],
        "avg_drawdown_252d": plan["avg_drawdown_252d"],
        "avg_sma200_gap": plan["avg_sma200_gap"],
        "avg_rsi14": plan["avg_rsi14"],
        "indicator_rows": plan["indicator_rows"],
        "managed_symbols": plan["managed_symbols"],
        "signal_symbols": plan["signal_symbols"],
        "trade_allocations": plan["trade_allocations"],
        "target_values": plan["target_values"],
        "execution_annotations": {
            "trade_threshold_value": plan["min_investment_usd"],
            "raw_buying_power": plan["available_cash"],
            "reserved_cash": plan["reserved_cash"],
            "investable_cash": plan["investable_cash"],
            "planned_investment_usd": plan["planned_investment_usd"],
            "multiplier": plan["multiplier"],
            "regime_multiplier": plan["regime_multiplier"],
            "smart_multiplier_enabled": plan["smart_multiplier_enabled"],
            "investment_amount_mode": plan["investment_amount_mode"],
            "base_investment_budget_usd": plan["base_investment_budget_usd"],
            "regime": plan["regime"],
            "signal_display": plan["signal_description"],
            "status_display": plan["status_description"],
        },
        **build_option_overlay_diagnostics(option_overlay_config, ctx),
    }
    diagnostics.update(_account_size_diagnostics(nasdaq_sp500_smart_dca_manifest.profile, ctx))
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
                plan["managed_symbols"],
                plan["target_values"],
                get_current_holdings(ctx),
            ),
            translator=translator,
            signal_text=diagnostics["signal_description"],
        ),
    )
    _attach_execution_timing(diagnostics, ctx)
    risk_flags = ("no_execute",) if not plan["actionable"] else ()
    decision = StrategyDecision(
        positions=target_values_to_positions(plan["target_values"]),
        risk_flags=risk_flags,
        diagnostics=diagnostics,
    )
    decision = apply_risk_gate(decision)
    record_strategy_decision(
        ctx,
        decision,
        profile_id=nasdaq_sp500_smart_dca_manifest.profile,
        domain=nasdaq_sp500_smart_dca_manifest.domain,
    )
    return decision


def evaluate_ibit_smart_dca(ctx: StrategyContext) -> StrategyDecision:
    config = merge_runtime_config(ibit_smart_dca_manifest.default_config, ctx)
    option_overlay_config = pop_option_overlay_config(config)
    translator = config.pop("translator", default_translator)
    config.pop("signal_effective_after_trading_days", None)
    config.pop("pacing_sec", None)
    reserved_cash_policy = pop_reserved_cash_policy_config(config)
    pop_execution_only_config(config)
    market_history = ctx.market_data.get("market_history")
    if market_history is None:
        def _empty_market_history(_client, _symbol):
            return ()

        market_history = _empty_market_history
    crypto_indicator_snapshot = (
        ctx.market_data.get("crypto_indicator_snapshot")
        or ctx.market_data.get("derived_indicators")
    )
    portfolio = require_portfolio(ctx)
    apply_reserved_cash_policy_to_usd_config(
        config,
        reserved_cash_policy,
        total_equity=float(getattr(portfolio, "total_equity", 0.0) or 0.0),
    )
    plan = ibit_smart_dca_strategy.build_rebalance_plan(
        market_history,
        portfolio,
        as_of=ctx.as_of,
        crypto_indicator_snapshot=crypto_indicator_snapshot,
        broker_client=ctx.capabilities.get("broker_client"),
        translator=translator,
        **config,
    )
    diagnostics = {
        "signal_description": plan["signal_description"],
        "status_description": plan["status_description"],
        "signal_source": ibit_smart_dca_strategy.SIGNAL_SOURCE,
        "actionable": plan["actionable"],
        "skip_reason": plan["skip_reason"],
        "regime": plan["regime"],
        "multiplier": plan["multiplier"],
        "regime_multiplier": plan["regime_multiplier"],
        "smart_multiplier_enabled": plan["smart_multiplier_enabled"],
        "investment_amount_mode": plan["investment_amount_mode"],
        "base_investment_usd": plan["base_investment_usd"],
        "base_investment_budget_usd": plan["base_investment_budget_usd"],
        "requested_investment_usd": plan["requested_investment_usd"],
        "planned_investment_usd": plan["planned_investment_usd"],
        "dca_actionable": plan["dca_actionable"],
        "dca_skip_reason": plan["dca_skip_reason"],
        "available_cash": plan["available_cash"],
        "reserved_cash": plan["reserved_cash"],
        "investable_cash": plan["investable_cash"],
        "min_investment_usd": plan["min_investment_usd"],
        "cash_capped": plan["cash_capped"],
        "cash_shortfall_usd": plan["cash_shortfall_usd"],
        "cash_substitute_for_dca_enabled": plan["cash_substitute_for_dca_enabled"],
        "cash_substitute_symbol": plan["cash_substitute_symbol"],
        "cash_substitute_value_usd": plan["cash_substitute_value_usd"],
        "cash_substitute_used_usd": plan["cash_substitute_used_usd"],
        "cash_substitute_funding_shortfall_usd": plan["cash_substitute_funding_shortfall_usd"],
        "execution_window": plan["execution_window"],
        "in_execution_window": plan["in_execution_window"],
        "avg_drawdown_252d": plan["avg_drawdown_252d"],
        "avg_sma200_gap": plan["avg_sma200_gap"],
        "avg_rsi14": plan["avg_rsi14"],
        "ahr999": plan["ahr999"],
        "ahr999_sma": plan["ahr999_sma"],
        "mayer_multiple": plan["mayer_multiple"],
        "cycle_indicator_source": plan["cycle_indicator_source"],
        "indicator_rows": plan["indicator_rows"],
        "managed_symbols": plan["managed_symbols"],
        "signal_symbols": plan["signal_symbols"],
        "trade_allocations": plan["trade_allocations"],
        "target_values": plan["target_values"],
        "ibit_zscore_exit": plan["ibit_zscore_exit"],
        "execution_annotations": {
            "trade_threshold_value": plan["min_investment_usd"],
            "raw_buying_power": plan["available_cash"],
            "reserved_cash": plan["reserved_cash"],
            "investable_cash": plan["investable_cash"],
            "cash_substitute_for_dca_enabled": plan["cash_substitute_for_dca_enabled"],
            "cash_substitute_symbol": plan["cash_substitute_symbol"],
            "cash_substitute_value_usd": plan["cash_substitute_value_usd"],
            "cash_substitute_used_usd": plan["cash_substitute_used_usd"],
            "cash_substitute_funding_shortfall_usd": plan["cash_substitute_funding_shortfall_usd"],
            "planned_investment_usd": plan["planned_investment_usd"],
            "dca_actionable": plan["dca_actionable"],
            "dca_skip_reason": plan["dca_skip_reason"],
            "multiplier": plan["multiplier"],
            "regime_multiplier": plan["regime_multiplier"],
            "smart_multiplier_enabled": plan["smart_multiplier_enabled"],
            "investment_amount_mode": plan["investment_amount_mode"],
            "base_investment_budget_usd": plan["base_investment_budget_usd"],
            "regime": plan["regime"],
            "ahr999": plan["ahr999"],
            "mayer_multiple": plan["mayer_multiple"],
            "cycle_indicator_source": plan["cycle_indicator_source"],
            "ibit_zscore_exit": plan["ibit_zscore_exit"],
            "signal_display": plan["signal_description"],
            "status_display": plan["status_description"],
        },
        **build_option_overlay_diagnostics(option_overlay_config, ctx),
    }
    diagnostics.update(_account_size_diagnostics(ibit_smart_dca_manifest.profile, ctx))
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
                plan["managed_symbols"],
                plan["target_values"],
                get_current_holdings(ctx),
            ),
            translator=translator,
            signal_text=diagnostics["signal_description"],
        ),
    )
    _attach_execution_timing(diagnostics, ctx)
    risk_flags = ("no_execute",) if not plan["actionable"] else ()
    decision = StrategyDecision(
        positions=target_values_to_positions(plan["target_values"]),
        risk_flags=risk_flags,
        diagnostics=diagnostics,
    )
    decision = apply_risk_gate(decision, max_single_weight=0.20)
    record_strategy_decision(
        ctx,
        decision,
        profile_id=ibit_smart_dca_manifest.profile,
        domain=ibit_smart_dca_manifest.domain,
    )
    return decision


nasdaq_sp500_smart_dca_strategy.build_rebalance_plan.__doc__ = (
    ((nasdaq_sp500_smart_dca_strategy.build_rebalance_plan.__doc__ or "").strip() +
     "\n\nPrefer us_equity_strategies entrypoints for platform integrations.")
    .strip()
)
ibit_smart_dca_strategy.build_rebalance_plan.__doc__ = (
    ((ibit_smart_dca_strategy.build_rebalance_plan.__doc__ or "").strip() +
     "\n\nPrefer us_equity_strategies entrypoints for platform integrations.")
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
tecl_xlk_trend_income_entrypoint = CallableStrategyEntrypoint(
    manifest=tecl_xlk_trend_income_manifest,
    _evaluate=evaluate_tecl_xlk_trend_income,
)
russell_top50_leader_rotation_entrypoint = CallableStrategyEntrypoint(
    manifest=russell_top50_leader_rotation_manifest,
    _evaluate=evaluate_russell_top50_leader_rotation,
)
nasdaq_sp500_smart_dca_entrypoint = CallableStrategyEntrypoint(
    manifest=nasdaq_sp500_smart_dca_manifest,
    _evaluate=evaluate_nasdaq_sp500_smart_dca,
)
ibit_smart_dca_entrypoint = CallableStrategyEntrypoint(
    manifest=ibit_smart_dca_manifest,
    _evaluate=evaluate_ibit_smart_dca,
)


# ---------------------------------------------------------------------------
# US Equity Combo entrypoints — 50/50 Russell Top50 + IBIT DCA
# ---------------------------------------------------------------------------


def evaluate_us_equity_combo(ctx: StrategyContext) -> StrategyDecision:
    from us_equity_strategies.combo_entrypoints import evaluate_us_equity_combo as _eval
    decision = apply_risk_gate(_eval(ctx))
    record_strategy_decision(
        ctx,
        decision,
        profile_id=us_equity_combo_manifest.profile,
        domain=us_equity_combo_manifest.domain,
    )
    return decision


us_equity_combo_entrypoint = CallableStrategyEntrypoint(
    manifest=us_equity_combo_manifest,
    _evaluate=evaluate_us_equity_combo,
)


def evaluate_us_equity_combo_core(ctx: StrategyContext) -> StrategyDecision:
    from us_equity_strategies.combo_entrypoints import evaluate_us_equity_combo_core as _eval
    decision = apply_risk_gate(_eval(ctx))
    record_strategy_decision(
        ctx,
        decision,
        profile_id=us_equity_combo_core_manifest.profile,
        domain=us_equity_combo_core_manifest.domain,
    )
    return decision


us_equity_combo_core_entrypoint = CallableStrategyEntrypoint(
    manifest=us_equity_combo_core_manifest,
    _evaluate=evaluate_us_equity_combo_core,
)


def evaluate_us_equity_combo_leveraged(ctx: StrategyContext) -> StrategyDecision:
    from us_equity_strategies.combo_entrypoints import evaluate_us_equity_combo_leveraged as _eval
    decision = apply_risk_gate(_eval(ctx))
    record_strategy_decision(
        ctx,
        decision,
        profile_id=us_equity_combo_leveraged_manifest.profile,
        domain=us_equity_combo_leveraged_manifest.domain,
    )
    return decision


us_equity_combo_leveraged_entrypoint = CallableStrategyEntrypoint(
    manifest=us_equity_combo_leveraged_manifest,
    _evaluate=evaluate_us_equity_combo_leveraged,
)


__all__ = [
    "global_etf_rotation_entrypoint",
    "tqqq_growth_income_entrypoint",
    "soxl_soxx_trend_income_entrypoint",
    "tecl_xlk_trend_income_entrypoint",
    "russell_top50_leader_rotation_entrypoint",
    "nasdaq_sp500_smart_dca_entrypoint",
    "ibit_smart_dca_entrypoint",
    "us_equity_combo_entrypoint",
    "us_equity_combo_core_entrypoint",
    "us_equity_combo_leveraged_entrypoint",
    "evaluate_global_etf_rotation",
    "evaluate_tqqq_growth_income",
    "evaluate_soxl_soxx_trend_income",
    "evaluate_tecl_xlk_trend_income",
    "evaluate_russell_top50_leader_rotation",
    "evaluate_nasdaq_sp500_smart_dca",
    "evaluate_ibit_smart_dca",
    "evaluate_us_equity_combo",
    "evaluate_us_equity_combo_core",
    "evaluate_us_equity_combo_leveraged",
]
