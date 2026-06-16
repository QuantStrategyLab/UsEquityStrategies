from __future__ import annotations

from quant_platform_kit.common.strategies import (
    StrategyCatalog,
    StrategyComponentDefinition,
    StrategyDefinition,
    StrategyEntrypointDefinition,
    StrategyMetadata,
    US_EQUITY_DOMAIN,
    build_strategy_catalog,
    build_strategy_index_rows,
    get_catalog_compatible_platforms,
    get_catalog_strategy_definition,
    get_catalog_strategy_metadata,
    load_strategy_entrypoint,
    normalize_profile_name as qpk_normalize_profile_name,
)

from .ai_extensions import build_default_ai_extension_config
from .income_layer_defaults import income_layer_default_config

GLOBAL_ETF_ROTATION_PROFILE = "global_etf_rotation"
# Legacy alias retained for lookups and docs; runtime registry is canonical rotation.
GLOBAL_ETF_CONFIDENCE_VOL_GATE_PROFILE = "global_etf_confidence_vol_gate"
TQQQ_GROWTH_INCOME_PROFILE = "tqqq_growth_income"
SOXL_SOXX_TREND_INCOME_PROFILE = "soxl_soxx_trend_income"
RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE = "russell_1000_multi_factor_defensive"
MEGA_CAP_LEADER_ROTATION_TOP50_BALANCED_PROFILE = "mega_cap_leader_rotation_top50_balanced"
NASDAQ_SP500_SMART_DCA_PROFILE = "nasdaq_sp500_smart_dca"
FULL_SHARED_PLATFORM_MATRIX = frozenset(
    {"ibkr", "schwab", "longbridge", "firstrade", "paper_signal"}
)


STRATEGY_PLATFORM_COMPATIBILITY: dict[str, frozenset[str]] = {
    GLOBAL_ETF_ROTATION_PROFILE: FULL_SHARED_PLATFORM_MATRIX,
    TQQQ_GROWTH_INCOME_PROFILE: FULL_SHARED_PLATFORM_MATRIX,
    SOXL_SOXX_TREND_INCOME_PROFILE: FULL_SHARED_PLATFORM_MATRIX,
    RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE: FULL_SHARED_PLATFORM_MATRIX,
    MEGA_CAP_LEADER_ROTATION_TOP50_BALANCED_PROFILE: FULL_SHARED_PLATFORM_MATRIX,
    NASDAQ_SP500_SMART_DCA_PROFILE: FULL_SHARED_PLATFORM_MATRIX,
}

STRATEGY_REQUIRED_INPUTS: dict[str, frozenset[str]] = {
    GLOBAL_ETF_ROTATION_PROFILE: frozenset({"market_history"}),
    TQQQ_GROWTH_INCOME_PROFILE: frozenset({"benchmark_history", "portfolio_snapshot"}),
    SOXL_SOXX_TREND_INCOME_PROFILE: frozenset({"derived_indicators", "portfolio_snapshot"}),
    RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE: frozenset({"feature_snapshot"}),
    MEGA_CAP_LEADER_ROTATION_TOP50_BALANCED_PROFILE: frozenset({"feature_snapshot"}),
    NASDAQ_SP500_SMART_DCA_PROFILE: frozenset({"market_history", "portfolio_snapshot"}),
}

STRATEGY_DEFAULT_CONFIG: dict[str, dict[str, object]] = {
    GLOBAL_ETF_ROTATION_PROFILE: {
        "ranking_pool": (
            "EWY", "EWT", "INDA", "FXI", "EWJ", "VGK", "VOO", "XLK", "SMH", "GLD",
            "SLV", "USO", "DBA", "XLE", "XLF", "ITA", "XLP", "XLU", "XLV", "IHI", "VNQ", "KRE",
        ),
        "canary_assets": ("SPY", "EFA", "EEM", "AGG"),
        "safe_haven": "BIL",
        "top_n": 2,
        "hold_bonus": 0.02,
        "canary_bad_threshold": 4,
        "rebalance_months": (3, 6, 9, 12),
        "sma_period": 250,
        "confidence_weighting_enabled": True,
        "confidence_metric": "z_gap",
        "confidence_threshold": 1.0,
        "confidence_top1_weight": 0.75,
        "confidence_volatility_gate_enabled": True,
        "confidence_volatility_window": 126,
        "confidence_volatility_max_ratio": 1.3,
        **income_layer_default_config(GLOBAL_ETF_ROTATION_PROFILE),
        "market_regime_control_enabled": True,
        "market_regime_control_apply_risk_reduced": True,
        "market_regime_control_apply_risk_off": True,
        "market_regime_control_risk_reduced_scalar": 0.50,
        "market_regime_control_risk_off_scalar": 0.0,
    },
    TQQQ_GROWTH_INCOME_PROFILE: {
        "benchmark_symbol": "QQQ",
        "managed_symbols": ("TQQQ", "QQQM", "BOXX", "SCHD", "DGRO", "SGOV", "SPYI", "QQQI"),
        "income_threshold_usd": 250000.0,
        "qqqi_income_ratio": 0.10,
        "cash_reserve_ratio": 0.02,
        "rebalance_threshold_ratio": 0.01,
        "execution_cash_reserve_ratio": 0.0,
        **income_layer_default_config(TQQQ_GROWTH_INCOME_PROFILE),
        "option_growth_overlay_enabled": True,
        "option_growth_overlay_recipe": "tqqq_leaps_growth_v1",
        "option_growth_overlay_start_usd": 250000.0,
        "option_growth_overlay_nav_budget_ratio": 0.03,
        "attack_allocation_mode": "fixed_qqq_tqqq_pullback",
        "dual_drive_qqq_weight": 0.45,
        "dual_drive_tqqq_weight": 0.45,
        "dual_drive_unlevered_symbol": "QQQM",
        "dual_drive_cash_reserve_ratio": 0.02,
        "dual_drive_allow_pullback": True,
        "dual_drive_require_ma20_slope": True,
        "dual_drive_pullback_rebound_window": 20,
        "dual_drive_pullback_rebound_threshold_mode": "volatility_scaled",
        "dual_drive_pullback_rebound_threshold": 0.0,
        "dual_drive_pullback_rebound_volatility_multiplier": 2.0,
        "dual_drive_volatility_delever_enabled": True,
        "dual_drive_volatility_delever_window": 5,
        "dual_drive_volatility_delever_threshold": 0.28,
        "dual_drive_volatility_delever_exit_threshold": 0.28,
        "dual_drive_volatility_delever_threshold_mode": "rolling_percentile",
        "dual_drive_volatility_delever_dynamic_lookback": 252,
        "dual_drive_volatility_delever_dynamic_percentile": 0.90,
        "dual_drive_volatility_delever_dynamic_min_periods": 126,
        "dual_drive_volatility_delever_dynamic_floor": 0.24,
        "dual_drive_volatility_delever_dynamic_cap": 0.36,
        "dual_drive_volatility_delever_taco_veto_enabled": True,
        "dual_drive_volatility_delever_retention_mode": "environment",
        "dual_drive_volatility_delever_retention_ratio": 0.0,
        "dual_drive_volatility_delever_retention_policy": "tqqq_step_softzero_0.25_0.50",
        "dual_drive_volatility_delever_retention_context_required": True,
        "dual_drive_volatility_delever_max_retention_ratio": 0.50,
        "dual_drive_macro_risk_governor_enabled": True,
        "dual_drive_crisis_defense_enabled": True,
        "market_regime_control_enabled": True,
        "ai_extensions": build_default_ai_extension_config(),
    },
    SOXL_SOXX_TREND_INCOME_PROFILE: {
        "managed_symbols": ("SOXL", "SOXX", "BOXX", "SCHD", "DGRO", "SGOV", "SPYI", "QQQI"),
        "trend_ma_window": 140,
        "cash_reserve_ratio": 0.03,
        "min_trade_ratio": 0.01,
        "min_trade_floor": 100.0,
        "rebalance_threshold_ratio": 0.01,
        **income_layer_default_config(SOXL_SOXX_TREND_INCOME_PROFILE),
        "option_income_overlay_enabled": True,
        "option_income_overlay_recipe": "soxx_put_credit_spread_income_v1",
        "option_income_overlay_start_usd": 1000000.0,
        "option_income_overlay_nav_risk_ratio": 0.01,
        "trend_entry_buffer": 0.08,
        "trend_mid_buffer": 0.06,
        "trend_exit_buffer": 0.02,
        "attack_allocation_mode": "soxx_gate_tiered_blend",
        "blend_gate_trend_source": "SOXX",
        "blend_gate_soxl_weight": 0.70,
        "blend_gate_mid_soxl_weight": 0.65,
        "blend_gate_active_soxx_weight": 0.20,
        "blend_gate_defensive_soxx_weight": 0.15,
        "blend_gate_rsi_cap_enabled": True,
        "blend_gate_rsi_threshold": 70.0,
        "blend_gate_dynamic_rsi_threshold_enabled": True,
        "blend_gate_bollinger_cap_enabled": True,
        "blend_gate_overlay_stack_triggers": True,
        "blend_gate_volatility_delever_enabled": True,
        "blend_gate_volatility_delever_symbol": "SOXX",
        "blend_gate_volatility_delever_window": 10,
        "blend_gate_volatility_delever_threshold": 0.55,
        "blend_gate_volatility_delever_threshold_mode": "rolling_percentile",
        "blend_gate_volatility_delever_dynamic_lookback": 252,
        "blend_gate_volatility_delever_dynamic_percentile": 0.95,
        "blend_gate_volatility_delever_dynamic_min_periods": 126,
        "blend_gate_volatility_delever_dynamic_floor": 0.50,
        "blend_gate_volatility_delever_dynamic_cap": 0.75,
        "blend_gate_volatility_delever_retention_ratio": 0.0,
        "blend_gate_volatility_delever_retention_mode": "environment",
        "blend_gate_volatility_delever_retention_policy": "soxl_step_rebound_0.25_0.50",
        "blend_gate_volatility_delever_retention_context_required": True,
        "blend_gate_volatility_delever_max_retention_ratio": 0.50,
        "blend_gate_volatility_delever_redirect_symbol": "SOXX",
        "market_regime_control_enabled": True,
        "market_regime_control_apply_risk_reduced": False,
        "market_regime_control_apply_risk_off": True,
    },
    RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE: {
        "benchmark_symbol": "SPY",
        "safe_haven": "BOXX",
        "holdings_count": 24,
        "single_name_cap": 0.06,
        "sector_cap": 0.20,
        "hold_bonus": 0.15,
        "soft_defense_exposure": 0.50,
        "hard_defense_exposure": 0.10,
        "soft_breadth_threshold": 0.55,
        "hard_breadth_threshold": 0.35,
        **income_layer_default_config(RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE),
        "market_regime_control_enabled": True,
        "market_regime_control_apply_risk_reduced": True,
        "market_regime_control_apply_risk_off": True,
        "market_regime_control_risk_reduced_scalar": 0.50,
        "market_regime_control_risk_off_scalar": 0.0,
    },
    MEGA_CAP_LEADER_ROTATION_TOP50_BALANCED_PROFILE: {
        "benchmark_symbol": "QQQ",
        "broad_benchmark_symbol": "SPY",
        "safe_haven": "BOXX",
        "dynamic_universe_size": 50,
        "blend_sleeves": (
            {"name": "top2_cap50", "weight": 0.50, "holdings_count": 2, "single_name_cap": 0.50},
            {"name": "top4_cap25", "weight": 0.50, "holdings_count": 4, "single_name_cap": 0.25},
        ),
        "holdings_count": 4,
        "single_name_cap": 0.25,
        "min_position_value_usd": 3000.0,
        "hold_buffer": 2,
        "hold_bonus": 0.10,
        "risk_on_exposure": 1.0,
        "soft_defense_exposure": 1.0,
        "hard_defense_exposure": 1.0,
        "soft_breadth_threshold": 0.0,
        "hard_breadth_threshold": 0.0,
        "min_adv20_usd": 20000000.0,
        "runtime_execution_window_trading_days": 3,
        "execution_cash_reserve_ratio": 0.0,
        **income_layer_default_config(MEGA_CAP_LEADER_ROTATION_TOP50_BALANCED_PROFILE),
        "option_growth_overlay_enabled": True,
        "option_growth_overlay_recipe": "qqq_leaps_growth_v1",
        "option_growth_overlay_start_usd": 1000000.0,
        "option_growth_overlay_nav_budget_ratio": 0.03,
        "market_regime_control_enabled": True,
        "market_regime_control_apply_risk_reduced": True,
        "market_regime_control_apply_risk_off": True,
        "market_regime_control_risk_reduced_scalar": 0.50,
        "market_regime_control_risk_off_scalar": 0.0,
    },
    NASDAQ_SP500_SMART_DCA_PROFILE: {
        "signal_symbols": ("QQQ", "SPY"),
        "trade_allocations": {
            "QQQM": 0.50,
            "SPLG": 0.50,
        },
        "managed_symbols": ("QQQM", "SPLG"),
        "base_investment_usd": 1000.0,
        "max_investment_usd": 2000.0,
        "cash_reserve_usd": 50.0,
        "min_investment_usd": 200.0,
        "cadence": "monthly",
        "monthly_day": 25,
        "monthly_window_calendar_days": 5,
        "weekly_day": 4,
        "mild_drawdown_threshold": 0.08,
        "deep_drawdown_threshold": 0.15,
        "severe_drawdown_threshold": 0.25,
        "mild_discount_gap": 0.05,
        "deep_discount_gap": 0.10,
        "expensive_gap": 0.12,
        "very_expensive_gap": 0.20,
        "shallow_drawdown_threshold": 0.03,
        "overbought_rsi": 70.0,
        "base_multiplier": 1.0,
        "mild_pullback_multiplier": 1.25,
        "deep_pullback_multiplier": 1.50,
        "severe_pullback_multiplier": 2.0,
        "expensive_multiplier": 0.50,
        "very_expensive_multiplier": 0.0,
        "execution_cash_reserve_ratio": 0.0,
        "execution_rebalance_threshold_ratio": 0.0,
    },
}

STRATEGY_ENTRYPOINT_ATTRIBUTES: dict[str, str] = {
    GLOBAL_ETF_ROTATION_PROFILE: "global_etf_rotation_entrypoint",
    TQQQ_GROWTH_INCOME_PROFILE: "tqqq_growth_income_entrypoint",
    SOXL_SOXX_TREND_INCOME_PROFILE: "soxl_soxx_trend_income_entrypoint",
    RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE: "russell_1000_multi_factor_defensive_entrypoint",
    MEGA_CAP_LEADER_ROTATION_TOP50_BALANCED_PROFILE: "mega_cap_leader_rotation_top50_balanced_entrypoint",
    NASDAQ_SP500_SMART_DCA_PROFILE: "nasdaq_sp500_smart_dca_entrypoint",
}

STRATEGY_TARGET_MODES: dict[str, str] = {
    GLOBAL_ETF_ROTATION_PROFILE: "weight",
    TQQQ_GROWTH_INCOME_PROFILE: "value",
    SOXL_SOXX_TREND_INCOME_PROFILE: "value",
    RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE: "weight",
    MEGA_CAP_LEADER_ROTATION_TOP50_BALANCED_PROFILE: "weight",
    NASDAQ_SP500_SMART_DCA_PROFILE: "value",
}

STRATEGY_BUNDLED_CONFIG_RELPATHS: dict[str, str] = {}


# `supported_platforms` 仍保留为兼容镜像，避免一次性改动所有平台 runtime。
# 平台真正的启用状态由各自 runtime 仓库维护；UES 这里只表达策略层兼容性。
def _build_strategy_definition(
    profile: str,
    *,
    component_name: str,
    module_path: str,
) -> StrategyDefinition:
    return StrategyDefinition(
        profile=profile,
        domain=US_EQUITY_DOMAIN,
        supported_platforms=STRATEGY_PLATFORM_COMPATIBILITY[profile],
        components=(
            StrategyComponentDefinition(
                name=component_name,
                module_path=module_path,
            ),
        ),
        entrypoint=StrategyEntrypointDefinition(
            module_path="us_equity_strategies.entrypoints",
            attribute_name=STRATEGY_ENTRYPOINT_ATTRIBUTES[profile],
        ),
        required_inputs=STRATEGY_REQUIRED_INPUTS[profile],
        default_config=STRATEGY_DEFAULT_CONFIG[profile],
        target_mode=STRATEGY_TARGET_MODES[profile],
        bundled_config_relpath=STRATEGY_BUNDLED_CONFIG_RELPATHS.get(profile),
    )


STRATEGY_DEFINITIONS: dict[str, StrategyDefinition] = {
    GLOBAL_ETF_ROTATION_PROFILE: _build_strategy_definition(
        GLOBAL_ETF_ROTATION_PROFILE,
        component_name="signal_logic",
        module_path="us_equity_strategies.strategies.global_etf_rotation",
    ),
    TQQQ_GROWTH_INCOME_PROFILE: _build_strategy_definition(
        TQQQ_GROWTH_INCOME_PROFILE,
        component_name="allocation",
        module_path="us_equity_strategies.strategies.tqqq_growth_income",
    ),
    SOXL_SOXX_TREND_INCOME_PROFILE: _build_strategy_definition(
        SOXL_SOXX_TREND_INCOME_PROFILE,
        component_name="allocation",
        module_path="us_equity_strategies.strategies.soxl_soxx_trend_income",
    ),
    RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE: _build_strategy_definition(
        RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE,
        component_name="signal_logic",
        module_path="us_equity_strategies.strategies.russell_1000_multi_factor_defensive",
    ),
    MEGA_CAP_LEADER_ROTATION_TOP50_BALANCED_PROFILE: _build_strategy_definition(
        MEGA_CAP_LEADER_ROTATION_TOP50_BALANCED_PROFILE,
        component_name="signal_logic",
        module_path="us_equity_strategies.strategies.mega_cap_leader_rotation",
    ),
    NASDAQ_SP500_SMART_DCA_PROFILE: _build_strategy_definition(
        NASDAQ_SP500_SMART_DCA_PROFILE,
        component_name="allocation",
        module_path="us_equity_strategies.strategies.nasdaq_sp500_smart_dca",
    ),
}


STRATEGY_METADATA: dict[str, StrategyMetadata] = {
    GLOBAL_ETF_ROTATION_PROFILE: StrategyMetadata(
        canonical_profile=GLOBAL_ETF_ROTATION_PROFILE,
        display_name="Global ETF Rotation",
        description="Quarterly top-2 global ETF rotation with daily canary defense, SMA250 confidence gating, and BIL safe haven.",
        aliases=("global_macro_etf_rotation", GLOBAL_ETF_CONFIDENCE_VOL_GATE_PROFILE),
        cadence="quarterly + daily canary",
        asset_scope="global_etf_rotation",
        benchmark="VOO",
        role="defensive_rotation",
        status="runtime_enabled",
    ),
    TQQQ_GROWTH_INCOME_PROFILE: StrategyMetadata(
        canonical_profile=TQQQ_GROWTH_INCOME_PROFILE,
        display_name="TQQQ Growth Income",
        description="QQQ-signal TQQQ/QQQM dual-drive growth profile with BOXX/cash defense and additive income sleeve.",
        aliases=(),
        cadence="daily",
        asset_scope="us_equity_qqq_tqqq_dual_drive",
        benchmark="QQQ",
        role="offensive_dual_drive",
        status="runtime_enabled",
    ),
    SOXL_SOXX_TREND_INCOME_PROFILE: StrategyMetadata(
        canonical_profile=SOXL_SOXX_TREND_INCOME_PROFILE,
        display_name="SOXL/SOXX Semiconductor Trend Income",
        description="SOXL / SOXX semiconductor trend switch with BOXX parking and additive income sleeve.",
        aliases=(),
        cadence="daily",
        asset_scope="semiconductor_etf_plus_income",
        benchmark="SOXX",
        role="sector_offensive_income",
        status="runtime_enabled",
    ),
    RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE: StrategyMetadata(
        canonical_profile=RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE,
        display_name="Russell 1000 Multi-Factor",
        description="Monthly price-only Russell 1000 stock selection with SPY+breadth defense and BOXX parking.",
        aliases=("r1000_multifactor_defensive",),
        cadence="monthly",
        asset_scope="us_large_cap_stocks",
        benchmark="SPY",
        role="defensive_stock_baseline",
        status="runtime_enabled",
    ),
    MEGA_CAP_LEADER_ROTATION_TOP50_BALANCED_PROFILE: StrategyMetadata(
        canonical_profile=MEGA_CAP_LEADER_ROTATION_TOP50_BALANCED_PROFILE,
        display_name="Mega Cap Leader Rotation Top50 Balanced",
        description="Balanced monthly Top50 mega-cap leader rotation using a fixed 50% Top2 / 50% Top4 sleeve blend and no trend de-risking by default.",
        aliases=(),
        cadence="monthly",
        asset_scope="us_mega_cap_top50_balanced_stocks",
        benchmark="QQQ",
        role="balanced_leader_rotation",
        status="runtime_enabled",
    ),
    NASDAQ_SP500_SMART_DCA_PROFILE: StrategyMetadata(
        canonical_profile=NASDAQ_SP500_SMART_DCA_PROFILE,
        display_name="Nasdaq/S&P 500 Smart DCA",
        description="Buy-only Nasdaq 100 and S&P 500 smart DCA profile with trend, pullback, and overvaluation gates.",
        aliases=(),
        cadence="monthly",
        asset_scope="nasdaq_100_sp500_etf_dca",
        benchmark="QQQ/SPY",
        role="buy_only_smart_dca",
        status="runtime_enabled",
    ),
}

PROFILE_ALIASES: dict[str, str] = {
    alias: metadata.canonical_profile
    for metadata in STRATEGY_METADATA.values()
    for alias in metadata.aliases
}

STRATEGY_CATALOG: StrategyCatalog = build_strategy_catalog(
    strategy_definitions=STRATEGY_DEFINITIONS,
    metadata=STRATEGY_METADATA,
    compatible_platforms=STRATEGY_PLATFORM_COMPATIBILITY,
    profile_aliases=PROFILE_ALIASES,
)


def normalize_profile_name(profile: str | None) -> str:
    return qpk_normalize_profile_name(profile)


def resolve_canonical_profile(profile: str | None) -> str:
    normalized = normalize_profile_name(profile)
    if not normalized:
        return normalized
    definition = get_catalog_strategy_definition(STRATEGY_CATALOG, normalized)
    return definition.profile


def get_strategy_definitions() -> dict[str, StrategyDefinition]:
    return dict(STRATEGY_DEFINITIONS)


def get_strategy_catalog() -> StrategyCatalog:
    return STRATEGY_CATALOG


def get_strategy_platform_compatibility_map() -> dict[str, frozenset[str]]:
    return dict(STRATEGY_PLATFORM_COMPATIBILITY)


def get_compatible_platforms(profile: str) -> frozenset[str]:
    return get_catalog_compatible_platforms(STRATEGY_CATALOG, profile)


def get_strategy_definition(profile: str) -> StrategyDefinition:
    return get_catalog_strategy_definition(STRATEGY_CATALOG, profile)


def get_strategy_entrypoint(profile: str):
    definition = get_strategy_definition(profile)
    metadata = get_strategy_metadata(profile)
    return load_strategy_entrypoint(definition, metadata=metadata)


def get_strategy_index_rows() -> list[dict[str, object]]:
    return build_strategy_index_rows(STRATEGY_CATALOG)


def get_strategy_metadata_map() -> dict[str, StrategyMetadata]:
    return dict(STRATEGY_METADATA)


def get_runtime_enabled_profiles() -> frozenset[str]:
    return frozenset(
        profile
        for profile, metadata in STRATEGY_METADATA.items()
        if str(metadata.status or "").strip().lower() == "runtime_enabled"
    )


def get_strategy_metadata(profile: str) -> StrategyMetadata:
    return get_catalog_strategy_metadata(STRATEGY_CATALOG, profile)


def get_profile_aliases() -> dict[str, str]:
    return dict(PROFILE_ALIASES)
