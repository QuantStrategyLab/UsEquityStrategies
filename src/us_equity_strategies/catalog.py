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

from .strategies.dynamic_mega_leveraged_pullback import DEFAULT_CONFIG as DYNAMIC_MEGA_LEVERAGED_PULLBACK_DEFAULT_CONFIG

GLOBAL_ETF_ROTATION_PROFILE = "global_etf_rotation"
TQQQ_GROWTH_INCOME_PROFILE = "tqqq_growth_income"
SOXL_SOXX_TREND_INCOME_PROFILE = "soxl_soxx_trend_income"
RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE = "russell_1000_multi_factor_defensive"
TECH_COMMUNICATION_PULLBACK_ENHANCEMENT_PROFILE = "tech_communication_pullback_enhancement"
MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE = "mega_cap_leader_rotation_dynamic_top20"
MEGA_CAP_LEADER_ROTATION_AGGRESSIVE_PROFILE = "mega_cap_leader_rotation_aggressive"
_DYNAMIC_MEGA_LEVERAGED_PULLBACK_PROFILE = "dynamic_mega_leveraged_pullback"
QQQ_TECH_ENHANCEMENT_LEGACY_PROFILE = "qqq_tech_enhancement"
QQQ_TECH_ENHANCEMENT_PROFILE = TECH_COMMUNICATION_PULLBACK_ENHANCEMENT_PROFILE
DYNAMIC_MEGA_LEVERAGED_PULLBACK_PROFILE = _DYNAMIC_MEGA_LEVERAGED_PULLBACK_PROFILE


STRATEGY_PLATFORM_COMPATIBILITY: dict[str, frozenset[str]] = {
    GLOBAL_ETF_ROTATION_PROFILE: frozenset({"ibkr", "schwab", "longbridge"}),
    TQQQ_GROWTH_INCOME_PROFILE: frozenset({"ibkr", "schwab", "longbridge"}),
    SOXL_SOXX_TREND_INCOME_PROFILE: frozenset({"ibkr", "longbridge", "schwab"}),
    RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE: frozenset({"ibkr", "schwab", "longbridge"}),
    QQQ_TECH_ENHANCEMENT_PROFILE: frozenset({"ibkr", "schwab", "longbridge"}),
    MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE: frozenset({"ibkr", "schwab", "longbridge"}),
    MEGA_CAP_LEADER_ROTATION_AGGRESSIVE_PROFILE: frozenset({"ibkr", "schwab", "longbridge"}),
    DYNAMIC_MEGA_LEVERAGED_PULLBACK_PROFILE: frozenset({"ibkr", "schwab", "longbridge"}),
}

STRATEGY_REQUIRED_INPUTS: dict[str, frozenset[str]] = {
    GLOBAL_ETF_ROTATION_PROFILE: frozenset({"market_history"}),
    TQQQ_GROWTH_INCOME_PROFILE: frozenset({"benchmark_history", "portfolio_snapshot"}),
    SOXL_SOXX_TREND_INCOME_PROFILE: frozenset({"derived_indicators", "portfolio_snapshot"}),
    RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE: frozenset({"feature_snapshot"}),
    QQQ_TECH_ENHANCEMENT_PROFILE: frozenset({"feature_snapshot"}),
    MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE: frozenset({"feature_snapshot"}),
    MEGA_CAP_LEADER_ROTATION_AGGRESSIVE_PROFILE: frozenset({"feature_snapshot"}),
    DYNAMIC_MEGA_LEVERAGED_PULLBACK_PROFILE: frozenset(
        {"feature_snapshot", "market_history", "benchmark_history", "portfolio_snapshot"}
    ),
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
        "managed_symbols": ("TQQQ", "QQQ", "BOXX", "SPYI", "QQQI"),
        "income_threshold_usd": 1_000_000_000.0,
        "qqqi_income_ratio": 0.5,
        "cash_reserve_ratio": 0.02,
        "rebalance_threshold_ratio": 0.01,
        "execution_cash_reserve_ratio": 0.0,
        "attack_allocation_mode": "fixed_qqq_tqqq_pullback",
        "dual_drive_qqq_weight": 0.45,
        "dual_drive_tqqq_weight": 0.45,
        "dual_drive_cash_reserve_ratio": 0.02,
        "dual_drive_allow_pullback": True,
        "dual_drive_require_ma20_slope": True,
    },
    SOXL_SOXX_TREND_INCOME_PROFILE: {
        "managed_symbols": ("SOXL", "SOXX", "BOXX", "QQQI", "SPYI"),
        "trend_ma_window": 140,
        "cash_reserve_ratio": 0.03,
        "min_trade_ratio": 0.01,
        "min_trade_floor": 100.0,
        "rebalance_threshold_ratio": 0.01,
        "income_layer_start_usd": 150000.0,
        "income_layer_max_ratio": 0.15,
        "income_layer_qqqi_weight": 0.70,
        "income_layer_spyi_weight": 0.30,
        "trend_entry_buffer": 0.08,
        "trend_mid_buffer": 0.06,
        "trend_exit_buffer": 0.02,
        "attack_allocation_mode": "soxx_gate_tiered_blend",
        "blend_gate_trend_source": "SOXX",
        "blend_gate_soxl_weight": 0.70,
        "blend_gate_mid_soxl_weight": 0.65,
        "blend_gate_active_soxx_weight": 0.20,
        "blend_gate_defensive_soxx_weight": 0.15,
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
    MEGA_CAP_LEADER_ROTATION_AGGRESSIVE_PROFILE: {
        "benchmark_symbol": "QQQ",
        "broad_benchmark_symbol": "SPY",
        "safe_haven": "BOXX",
        "dynamic_universe_size": 50,
        "holdings_count": 3,
        "single_name_cap": 0.35,
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
    },
    DYNAMIC_MEGA_LEVERAGED_PULLBACK_PROFILE: {
        **DYNAMIC_MEGA_LEVERAGED_PULLBACK_DEFAULT_CONFIG,
    },
}

STRATEGY_ENTRYPOINT_ATTRIBUTES: dict[str, str] = {
    GLOBAL_ETF_ROTATION_PROFILE: "global_etf_rotation_entrypoint",
    TQQQ_GROWTH_INCOME_PROFILE: "tqqq_growth_income_entrypoint",
    SOXL_SOXX_TREND_INCOME_PROFILE: "soxl_soxx_trend_income_entrypoint",
    RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE: "russell_1000_multi_factor_defensive_entrypoint",
    QQQ_TECH_ENHANCEMENT_PROFILE: "qqq_tech_enhancement_entrypoint",
    MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE: "mega_cap_leader_rotation_dynamic_top20_entrypoint",
    MEGA_CAP_LEADER_ROTATION_AGGRESSIVE_PROFILE: "mega_cap_leader_rotation_aggressive_entrypoint",
    DYNAMIC_MEGA_LEVERAGED_PULLBACK_PROFILE: "dynamic_mega_leveraged_pullback_entrypoint",
}

STRATEGY_TARGET_MODES: dict[str, str] = {
    GLOBAL_ETF_ROTATION_PROFILE: "weight",
    TQQQ_GROWTH_INCOME_PROFILE: "value",
    SOXL_SOXX_TREND_INCOME_PROFILE: "value",
    RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE: "weight",
    QQQ_TECH_ENHANCEMENT_PROFILE: "weight",
    MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE: "weight",
    MEGA_CAP_LEADER_ROTATION_AGGRESSIVE_PROFILE: "weight",
    DYNAMIC_MEGA_LEVERAGED_PULLBACK_PROFILE: "weight",
}

STRATEGY_BUNDLED_CONFIG_RELPATHS: dict[str, str] = {
    QQQ_TECH_ENHANCEMENT_PROFILE: (
        "package://us_equity_strategies/configs/tech_communication_pullback_enhancement.json"
    ),
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
    MEGA_CAP_LEADER_ROTATION_AGGRESSIVE_PROFILE: _build_strategy_definition(
        MEGA_CAP_LEADER_ROTATION_AGGRESSIVE_PROFILE,
        component_name="signal_logic",
        module_path="us_equity_strategies.strategies.mega_cap_leader_rotation_dynamic_top20",
    ),
    DYNAMIC_MEGA_LEVERAGED_PULLBACK_PROFILE: _build_strategy_definition(
        DYNAMIC_MEGA_LEVERAGED_PULLBACK_PROFILE,
        component_name="signal_logic",
        module_path="us_equity_strategies.strategies.dynamic_mega_leveraged_pullback",
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
        description="QQQ/TQQQ dual-drive growth profile with BOXX/cash defense; income sleeve remains opt-in.",
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
    MEGA_CAP_LEADER_ROTATION_AGGRESSIVE_PROFILE: StrategyMetadata(
        canonical_profile=MEGA_CAP_LEADER_ROTATION_AGGRESSIVE_PROFILE,
        display_name="Mega Cap Leader Rotation Aggressive",
        description="Aggressive mega-cap leader rotation using a larger/curated snapshot, top-3 concentration, and no trend de-risking by default.",
        aliases=(),
        cadence="monthly",
        asset_scope="us_mega_cap_aggressive_stocks",
        benchmark="QQQ",
        role="aggressive_leader_rotation",
        status="runtime_enabled",
    ),
    DYNAMIC_MEGA_LEVERAGED_PULLBACK_PROFILE: StrategyMetadata(
        canonical_profile=DYNAMIC_MEGA_LEVERAGED_PULLBACK_PROFILE,
        display_name="Dynamic Mega Leveraged Pullback",
        description="Monthly dynamic mega-cap candidate snapshot with daily QQQ ATR/SMA gate and top-3 2x long pullback execution.",
        aliases=(),
        cadence="monthly snapshot + daily runtime",
        asset_scope="us_mega_cap_single_stock_leveraged_products",
        benchmark="QQQ",
        role="offensive_leveraged_pullback",
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
