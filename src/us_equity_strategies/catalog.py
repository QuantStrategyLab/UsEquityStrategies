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

GLOBAL_ETF_ROTATION_PROFILE = "global_etf_rotation"
TQQQ_GROWTH_INCOME_PROFILE = "tqqq_growth_income"
SOXL_SOXX_TREND_INCOME_PROFILE = "soxl_soxx_trend_income"
RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE = "russell_1000_multi_factor_defensive"
TECH_COMMUNICATION_PULLBACK_ENHANCEMENT_PROFILE = "tech_communication_pullback_enhancement"
MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE = "mega_cap_leader_rotation_dynamic_top20"
QQQ_TECH_ENHANCEMENT_LEGACY_PROFILE = "qqq_tech_enhancement"
QQQ_TECH_ENHANCEMENT_PROFILE = TECH_COMMUNICATION_PULLBACK_ENHANCEMENT_PROFILE


STRATEGY_PLATFORM_COMPATIBILITY: dict[str, frozenset[str]] = {
    GLOBAL_ETF_ROTATION_PROFILE: frozenset({"ibkr", "schwab", "longbridge"}),
    TQQQ_GROWTH_INCOME_PROFILE: frozenset({"ibkr", "schwab", "longbridge"}),
    SOXL_SOXX_TREND_INCOME_PROFILE: frozenset({"ibkr", "longbridge", "schwab"}),
    RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE: frozenset({"ibkr", "schwab", "longbridge"}),
    QQQ_TECH_ENHANCEMENT_PROFILE: frozenset({"ibkr", "schwab", "longbridge"}),
    MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE: frozenset({"ibkr", "schwab", "longbridge"}),
}

STRATEGY_REQUIRED_INPUTS: dict[str, frozenset[str]] = {
    GLOBAL_ETF_ROTATION_PROFILE: frozenset({"market_history"}),
    TQQQ_GROWTH_INCOME_PROFILE: frozenset({"benchmark_history", "portfolio_snapshot"}),
    SOXL_SOXX_TREND_INCOME_PROFILE: frozenset({"derived_indicators", "portfolio_snapshot"}),
    RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE: frozenset({"feature_snapshot"}),
    QQQ_TECH_ENHANCEMENT_PROFILE: frozenset({"feature_snapshot"}),
    MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE: frozenset({"feature_snapshot"}),
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
        "sma_period": 200,
    },
    TQQQ_GROWTH_INCOME_PROFILE: {
        "benchmark_symbol": "QQQ",
        "managed_symbols": ("TQQQ", "BOXX", "SPYI", "QQQI"),
        "income_threshold_usd": 100000.0,
        "qqqi_income_ratio": 0.5,
        "cash_reserve_ratio": 0.05,
        "rebalance_threshold_ratio": 0.01,
        "alloc_tier1_breakpoints": (0, 15000, 30000, 70000),
        "alloc_tier1_values": (1.0, 0.95, 0.85, 0.70),
        "alloc_tier2_breakpoints": (70000, 140000),
        "alloc_tier2_values": (0.70, 0.50),
        "risk_leverage_factor": 3.0,
        "risk_agg_cap": 0.50,
        "risk_numerator": 0.30,
        "atr_exit_scale": 2.0,
        "atr_entry_scale": 2.5,
        "exit_line_floor": 0.92,
        "exit_line_cap": 0.98,
        "entry_line_floor": 1.02,
        "entry_line_cap": 1.08,
    },
    SOXL_SOXX_TREND_INCOME_PROFILE: {
        "managed_symbols": ("SOXL", "SOXX", "BOXX", "QQQI", "SPYI"),
        "trend_ma_window": 150,
        "cash_reserve_ratio": 0.03,
        "min_trade_ratio": 0.01,
        "min_trade_floor": 100.0,
        "rebalance_threshold_ratio": 0.01,
        "small_account_deploy_ratio": 0.60,
        "mid_account_deploy_ratio": 0.57,
        "large_account_deploy_ratio": 0.50,
        "trade_layer_decay_coeff": 0.04,
        "income_layer_start_usd": 150000.0,
        "income_layer_max_ratio": 0.15,
        "income_layer_qqqi_weight": 0.70,
        "income_layer_spyi_weight": 0.30,
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
    },
    QQQ_TECH_ENHANCEMENT_PROFILE: {
        "benchmark_symbol": "QQQ",
        "safe_haven": "BOXX",
        "holdings_count": 8,
        "single_name_cap": 0.10,
        "sector_cap": 0.40,
        "min_position_value_usd": 3000.0,
        "max_dynamic_single_name_cap": 0.40,
        "max_dynamic_sector_cap": 0.60,
        "hold_bonus": 0.10,
        "risk_on_exposure": 0.80,
        "soft_defense_exposure": 0.60,
        "hard_defense_exposure": 0.00,
        "soft_breadth_threshold": 0.55,
        "hard_breadth_threshold": 0.35,
        "min_adv20_usd": 50000000.0,
        "sector_whitelist": ("Information Technology", "Communication"),
        "normalization": "universe_cross_sectional",
        "score_template": "balanced_pullback",
        "runtime_execution_window_trading_days": 3,
        "execution_cash_reserve_ratio": 0.0,
        "residual_proxy": "simple_excess_return_vs_QQQ",
    },
    MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE: {
        "benchmark_symbol": "QQQ",
        "broad_benchmark_symbol": "SPY",
        "safe_haven": "BOXX",
        "dynamic_universe_size": 20,
        "holdings_count": 4,
        "single_name_cap": 0.25,
        "min_position_value_usd": 3000.0,
        "hold_buffer": 2,
        "hold_bonus": 0.10,
        "risk_on_exposure": 1.0,
        "soft_defense_exposure": 0.50,
        "hard_defense_exposure": 0.50,
        "soft_breadth_threshold": 0.0,
        "hard_breadth_threshold": 0.0,
        "min_adv20_usd": 20000000.0,
        "runtime_execution_window_trading_days": 3,
        "execution_cash_reserve_ratio": 0.0,
    },
}

STRATEGY_ENTRYPOINT_ATTRIBUTES: dict[str, str] = {
    GLOBAL_ETF_ROTATION_PROFILE: "global_etf_rotation_entrypoint",
    TQQQ_GROWTH_INCOME_PROFILE: "tqqq_growth_income_entrypoint",
    SOXL_SOXX_TREND_INCOME_PROFILE: "soxl_soxx_trend_income_entrypoint",
    RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE: "russell_1000_multi_factor_defensive_entrypoint",
    QQQ_TECH_ENHANCEMENT_PROFILE: "qqq_tech_enhancement_entrypoint",
    MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE: "mega_cap_leader_rotation_dynamic_top20_entrypoint",
}

STRATEGY_TARGET_MODES: dict[str, str] = {
    GLOBAL_ETF_ROTATION_PROFILE: "weight",
    TQQQ_GROWTH_INCOME_PROFILE: "value",
    SOXL_SOXX_TREND_INCOME_PROFILE: "value",
    RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE: "weight",
    QQQ_TECH_ENHANCEMENT_PROFILE: "weight",
    MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE: "weight",
}

STRATEGY_BUNDLED_CONFIG_RELPATHS: dict[str, str] = {
    QQQ_TECH_ENHANCEMENT_PROFILE: "research/configs/growth_pullback_tech_communication_pullback_enhancement.json",
}


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
    QQQ_TECH_ENHANCEMENT_PROFILE: _build_strategy_definition(
        QQQ_TECH_ENHANCEMENT_PROFILE,
        component_name="signal_logic",
        module_path="us_equity_strategies.strategies.qqq_tech_enhancement",
    ),
    MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE: _build_strategy_definition(
        MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE,
        component_name="signal_logic",
        module_path="us_equity_strategies.strategies.mega_cap_leader_rotation_dynamic_top20",
    ),
}


STRATEGY_METADATA: dict[str, StrategyMetadata] = {
    GLOBAL_ETF_ROTATION_PROFILE: StrategyMetadata(
        canonical_profile=GLOBAL_ETF_ROTATION_PROFILE,
        display_name="Global ETF Rotation",
        description="Quarterly top-2 global ETF rotation with daily canary defense and BIL safe haven.",
        aliases=("global_macro_etf_rotation",),
        cadence="quarterly + daily canary",
        asset_scope="global_etf_rotation",
        benchmark="VOO",
        role="defensive_rotation",
        status="runtime_enabled",
    ),
    TQQQ_GROWTH_INCOME_PROFILE: StrategyMetadata(
        canonical_profile=TQQQ_GROWTH_INCOME_PROFILE,
        display_name="TQQQ Growth Income",
        description="QQQ-led TQQQ attack sleeve with SPYI / QQQI income and BOXX defense.",
        aliases=(),
        cadence="daily",
        asset_scope="us_equity_etf_plus_income",
        benchmark="QQQ",
        role="offensive_income",
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
    QQQ_TECH_ENHANCEMENT_PROFILE: StrategyMetadata(
        canonical_profile=QQQ_TECH_ENHANCEMENT_PROFILE,
        display_name="Tech/Communication Pullback Enhancement",
        description="Tech-heavy monthly stock selection with controlled pullback entry and explicit BOXX cash buffer.",
        aliases=(QQQ_TECH_ENHANCEMENT_LEGACY_PROFILE,),
        cadence="monthly",
        asset_scope="us_tech_communication_stocks",
        benchmark="QQQ",
        role="parallel_cash_buffer_branch",
        status="runtime_enabled",
    ),
    MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE: StrategyMetadata(
        canonical_profile=MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE,
        display_name="Mega Cap Leader Rotation Dynamic Top20",
        description="Monthly dynamic top-20 mega-cap leader rotation with QQQ trend defense and BOXX parking.",
        aliases=(),
        cadence="monthly",
        asset_scope="us_mega_cap_stocks",
        benchmark="QQQ",
        role="concentrated_leader_rotation",
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


def get_strategy_metadata(profile: str) -> StrategyMetadata:
    return get_catalog_strategy_metadata(STRATEGY_CATALOG, profile)


def get_profile_aliases() -> dict[str, str]:
    return dict(PROFILE_ALIASES)
