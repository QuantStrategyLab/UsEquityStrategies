from __future__ import annotations

from quant_platform_kit.common.strategies import (
    StrategyCatalog,
    StrategyComponentDefinition,
    StrategyDefinition,
    StrategyMetadata,
    US_EQUITY_DOMAIN,
    build_strategy_catalog,
    build_strategy_index_rows,
    get_catalog_compatible_platforms,
    get_catalog_strategy_definition,
    get_catalog_strategy_metadata,
    normalize_profile_name as qpk_normalize_profile_name,
    resolve_catalog_profile,
)

GLOBAL_ETF_ROTATION_PROFILE = "global_etf_rotation"
HYBRID_GROWTH_INCOME_PROFILE = "hybrid_growth_income"
SEMICONDUCTOR_ROTATION_INCOME_PROFILE = "semiconductor_rotation_income"
RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE = "russell_1000_multi_factor_defensive"
TECH_PULLBACK_CASH_BUFFER_PROFILE = "tech_pullback_cash_buffer"


STRATEGY_PLATFORM_COMPATIBILITY: dict[str, frozenset[str]] = {
    GLOBAL_ETF_ROTATION_PROFILE: frozenset({"ibkr"}),
    HYBRID_GROWTH_INCOME_PROFILE: frozenset({"schwab"}),
    SEMICONDUCTOR_ROTATION_INCOME_PROFILE: frozenset({"longbridge"}),
    RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE: frozenset({"ibkr"}),
    TECH_PULLBACK_CASH_BUFFER_PROFILE: frozenset({"ibkr"}),
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
    )


STRATEGY_DEFINITIONS: dict[str, StrategyDefinition] = {
    GLOBAL_ETF_ROTATION_PROFILE: _build_strategy_definition(
        GLOBAL_ETF_ROTATION_PROFILE,
        component_name="signal_logic",
        module_path="us_equity_strategies.strategies.global_etf_rotation",
    ),
    HYBRID_GROWTH_INCOME_PROFILE: _build_strategy_definition(
        HYBRID_GROWTH_INCOME_PROFILE,
        component_name="allocation",
        module_path="us_equity_strategies.strategies.hybrid_growth_income",
    ),
    SEMICONDUCTOR_ROTATION_INCOME_PROFILE: _build_strategy_definition(
        SEMICONDUCTOR_ROTATION_INCOME_PROFILE,
        component_name="allocation",
        module_path="us_equity_strategies.strategies.semiconductor_rotation_income",
    ),
    RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE: _build_strategy_definition(
        RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE,
        component_name="signal_logic",
        module_path="us_equity_strategies.strategies.russell_1000_multi_factor_defensive",
    ),
    TECH_PULLBACK_CASH_BUFFER_PROFILE: _build_strategy_definition(
        TECH_PULLBACK_CASH_BUFFER_PROFILE,
        component_name="signal_logic",
        module_path="us_equity_strategies.strategies.tech_pullback_cash_buffer",
    ),
}


STRATEGY_METADATA: dict[str, StrategyMetadata] = {
    GLOBAL_ETF_ROTATION_PROFILE: StrategyMetadata(
        canonical_profile=GLOBAL_ETF_ROTATION_PROFILE,
        display_name="Global ETF Rotation Defense",
        description="Quarterly top-2 global ETF rotation with daily canary defense and BIL safe haven.",
        aliases=("global_macro_etf_rotation",),
        cadence="quarterly + daily canary",
        asset_scope="global_etf_rotation",
        benchmark="VOO",
        role="defensive_rotation",
        status="runtime_enabled",
    ),
    HYBRID_GROWTH_INCOME_PROFILE: StrategyMetadata(
        canonical_profile=HYBRID_GROWTH_INCOME_PROFILE,
        display_name="QQQ/TQQQ Growth Income",
        description="QQQ-led TQQQ attack sleeve with SPYI / QQQI income and BOXX defense.",
        aliases=("qqq_tqqq_growth_income",),
        cadence="daily",
        asset_scope="us_equity_etf_plus_income",
        benchmark="QQQ",
        role="offensive_income",
        status="runtime_enabled",
    ),
    SEMICONDUCTOR_ROTATION_INCOME_PROFILE: StrategyMetadata(
        canonical_profile=SEMICONDUCTOR_ROTATION_INCOME_PROFILE,
        display_name="Semiconductor Trend Income",
        description="SOXL / SOXX semiconductor trend switch with BOXX parking and additive income sleeve.",
        aliases=("semiconductor_trend_income",),
        cadence="daily",
        asset_scope="semiconductor_etf_plus_income",
        benchmark="SOXX",
        role="sector_offensive_income",
        status="runtime_enabled",
    ),
    RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE: StrategyMetadata(
        canonical_profile=RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE,
        display_name="Russell 1000 Multi-Factor Defensive",
        description="Monthly price-only Russell 1000 stock selection with SPY+breadth defense and BOXX parking.",
        aliases=("r1000_multifactor_defensive",),
        cadence="monthly",
        asset_scope="us_large_cap_stocks",
        benchmark="SPY",
        role="defensive_stock_baseline",
        status="runtime_enabled",
    ),
    TECH_PULLBACK_CASH_BUFFER_PROFILE: StrategyMetadata(
        canonical_profile=TECH_PULLBACK_CASH_BUFFER_PROFILE,
        display_name="Tech Pullback Cash Buffer",
        description="Tech-heavy monthly stock selection with controlled pullback entry and explicit BOXX cash buffer.",
        aliases=(),
        cadence="monthly",
        asset_scope="us_tech_communication_stocks",
        benchmark="QQQ",
        role="parallel_cash_buffer_branch",
        status="paper_dry_run",
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
    return resolve_catalog_profile(profile, strategy_catalog=STRATEGY_CATALOG)


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


def get_strategy_index_rows() -> list[dict[str, object]]:
    return build_strategy_index_rows(STRATEGY_CATALOG)


def get_strategy_metadata_map() -> dict[str, StrategyMetadata]:
    return dict(STRATEGY_METADATA)


def get_strategy_metadata(profile: str) -> StrategyMetadata:
    return get_catalog_strategy_metadata(STRATEGY_CATALOG, profile)


def get_profile_aliases() -> dict[str, str]:
    return dict(PROFILE_ALIASES)
