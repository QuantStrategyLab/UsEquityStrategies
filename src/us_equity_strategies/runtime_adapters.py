from __future__ import annotations

from quant_platform_kit.strategy_contracts import (
    StrategyRuntimeAdapter,
    validate_strategy_runtime_adapter,
)

from us_equity_strategies.catalog import get_strategy_definition, resolve_canonical_profile
from us_equity_strategies.strategies import (
    russell_1000_multi_factor_defensive as legacy_russell,
    qqq_tech_enhancement as qqq_tech_enhancement_strategy,
)


IBKR_PLATFORM = "ibkr"
SCHWAB_PLATFORM = "schwab"
LONGBRIDGE_PLATFORM = "longbridge"


IBKR_RUNTIME_ADAPTERS: dict[str, StrategyRuntimeAdapter] = {
    "global_etf_rotation": StrategyRuntimeAdapter(
        status_icon="🐤",
        available_inputs=frozenset({"market_history"}),
        available_capabilities=frozenset({"broker_client"}),
    ),
    "russell_1000_multi_factor_defensive": StrategyRuntimeAdapter(
        status_icon=legacy_russell.STATUS_ICON,
        available_inputs=frozenset({"feature_snapshot"}),
        required_feature_columns=legacy_russell.REQUIRED_FEATURE_COLUMNS,
        managed_symbols_extractor=legacy_russell.extract_managed_symbols,
    ),
    "qqq_tech_enhancement": StrategyRuntimeAdapter(
        status_icon=qqq_tech_enhancement_strategy.STATUS_ICON,
        available_inputs=frozenset({"feature_snapshot"}),
        required_feature_columns=qqq_tech_enhancement_strategy.REQUIRED_FEATURE_COLUMNS,
        snapshot_date_columns=qqq_tech_enhancement_strategy.SNAPSHOT_DATE_COLUMNS,
        max_snapshot_month_lag=qqq_tech_enhancement_strategy.MAX_SNAPSHOT_MONTH_LAG,
        require_snapshot_manifest=qqq_tech_enhancement_strategy.REQUIRE_SNAPSHOT_MANIFEST,
        snapshot_contract_version=qqq_tech_enhancement_strategy.SNAPSHOT_CONTRACT_VERSION,
        runtime_parameter_loader=qqq_tech_enhancement_strategy.load_runtime_parameters,
        managed_symbols_extractor=qqq_tech_enhancement_strategy.extract_managed_symbols,
    ),
    "tqqq_growth_income": StrategyRuntimeAdapter(
        status_icon="🐤",
        available_inputs=frozenset({"benchmark_history", "portfolio_snapshot"}),
        portfolio_input_name="portfolio_snapshot",
    ),
    "soxl_soxx_trend_income": StrategyRuntimeAdapter(
        status_icon="🐤",
        available_inputs=frozenset({"derived_indicators", "portfolio_snapshot"}),
        portfolio_input_name="portfolio_snapshot",
    ),
}

PLATFORM_RUNTIME_ADAPTERS: dict[str, dict[str, StrategyRuntimeAdapter]] = {
    IBKR_PLATFORM: IBKR_RUNTIME_ADAPTERS,
    SCHWAB_PLATFORM: {
        "global_etf_rotation": StrategyRuntimeAdapter(
            status_icon="🐤",
            available_inputs=frozenset({"market_history", "portfolio_snapshot"}),
            portfolio_input_name="portfolio_snapshot",
        ),
        "russell_1000_multi_factor_defensive": StrategyRuntimeAdapter(
            status_icon=legacy_russell.STATUS_ICON,
            available_inputs=frozenset({"feature_snapshot", "portfolio_snapshot"}),
            required_feature_columns=legacy_russell.REQUIRED_FEATURE_COLUMNS,
            managed_symbols_extractor=legacy_russell.extract_managed_symbols,
            portfolio_input_name="portfolio_snapshot",
        ),
        "tqqq_growth_income": StrategyRuntimeAdapter(
            status_icon="🐤",
            available_inputs=frozenset({"benchmark_history", "portfolio_snapshot"}),
            portfolio_input_name="portfolio_snapshot",
        ),
        "soxl_soxx_trend_income": StrategyRuntimeAdapter(
            status_icon="🐤",
            available_inputs=frozenset({"derived_indicators", "portfolio_snapshot"}),
            portfolio_input_name="portfolio_snapshot",
        ),
        "qqq_tech_enhancement": StrategyRuntimeAdapter(
            status_icon=qqq_tech_enhancement_strategy.STATUS_ICON,
            available_inputs=frozenset({"feature_snapshot", "portfolio_snapshot"}),
            required_feature_columns=qqq_tech_enhancement_strategy.REQUIRED_FEATURE_COLUMNS,
            snapshot_date_columns=qqq_tech_enhancement_strategy.SNAPSHOT_DATE_COLUMNS,
            max_snapshot_month_lag=qqq_tech_enhancement_strategy.MAX_SNAPSHOT_MONTH_LAG,
            require_snapshot_manifest=qqq_tech_enhancement_strategy.REQUIRE_SNAPSHOT_MANIFEST,
            snapshot_contract_version=qqq_tech_enhancement_strategy.SNAPSHOT_CONTRACT_VERSION,
            runtime_parameter_loader=qqq_tech_enhancement_strategy.load_runtime_parameters,
            managed_symbols_extractor=qqq_tech_enhancement_strategy.extract_managed_symbols,
            portfolio_input_name="portfolio_snapshot",
        ),
    },
    LONGBRIDGE_PLATFORM: {
        "global_etf_rotation": StrategyRuntimeAdapter(
            status_icon="🐤",
            available_inputs=frozenset({"market_history", "portfolio_snapshot"}),
            portfolio_input_name="portfolio_snapshot",
        ),
        "russell_1000_multi_factor_defensive": StrategyRuntimeAdapter(
            status_icon=legacy_russell.STATUS_ICON,
            available_inputs=frozenset({"feature_snapshot", "portfolio_snapshot"}),
            required_feature_columns=legacy_russell.REQUIRED_FEATURE_COLUMNS,
            managed_symbols_extractor=legacy_russell.extract_managed_symbols,
            portfolio_input_name="portfolio_snapshot",
        ),
        "tqqq_growth_income": StrategyRuntimeAdapter(
            status_icon="🐤",
            available_inputs=frozenset({"benchmark_history", "portfolio_snapshot"}),
            portfolio_input_name="portfolio_snapshot",
        ),
        "soxl_soxx_trend_income": StrategyRuntimeAdapter(
            status_icon="🐤",
            available_inputs=frozenset({"derived_indicators", "portfolio_snapshot"}),
            portfolio_input_name="portfolio_snapshot",
        ),
        "qqq_tech_enhancement": StrategyRuntimeAdapter(
            status_icon=qqq_tech_enhancement_strategy.STATUS_ICON,
            available_inputs=frozenset({"feature_snapshot", "portfolio_snapshot"}),
            required_feature_columns=qqq_tech_enhancement_strategy.REQUIRED_FEATURE_COLUMNS,
            snapshot_date_columns=qqq_tech_enhancement_strategy.SNAPSHOT_DATE_COLUMNS,
            max_snapshot_month_lag=qqq_tech_enhancement_strategy.MAX_SNAPSHOT_MONTH_LAG,
            require_snapshot_manifest=qqq_tech_enhancement_strategy.REQUIRE_SNAPSHOT_MANIFEST,
            snapshot_contract_version=qqq_tech_enhancement_strategy.SNAPSHOT_CONTRACT_VERSION,
            runtime_parameter_loader=qqq_tech_enhancement_strategy.load_runtime_parameters,
            managed_symbols_extractor=qqq_tech_enhancement_strategy.extract_managed_symbols,
            portfolio_input_name="portfolio_snapshot",
        ),
    },
}


def derive_runtime_input_mode(required_inputs: frozenset[str] | set[str] | tuple[str, ...]) -> str:
    normalized = frozenset(str(value).strip() for value in required_inputs)
    if normalized == frozenset({"market_history"}):
        return "market_history"
    if normalized == frozenset({"benchmark_history", "portfolio_snapshot"}):
        return "benchmark_history+portfolio_snapshot"
    if normalized == frozenset({"derived_indicators", "portfolio_snapshot"}):
        return "derived_indicators+portfolio_snapshot"
    if normalized == frozenset({"feature_snapshot"}):
        return "feature_snapshot"
    return "+".join(sorted(normalized)) or "none"


def describe_platform_runtime_requirements(profile: str | None, *, platform_id: str) -> dict[str, object]:
    canonical_profile = resolve_canonical_profile(profile)
    definition = get_strategy_definition(canonical_profile)
    adapter = get_platform_runtime_adapter(canonical_profile, platform_id=platform_id)
    requires_snapshot_artifacts = "feature_snapshot" in frozenset(definition.required_inputs)
    requires_strategy_config_path = bool(
        requires_snapshot_artifacts and callable(adapter.runtime_parameter_loader)
    )
    return {
        "input_mode": derive_runtime_input_mode(definition.required_inputs),
        "requires_snapshot_artifacts": requires_snapshot_artifacts,
        "requires_strategy_config_path": requires_strategy_config_path,
        "profile_group": "snapshot_backed" if requires_snapshot_artifacts else "direct_runtime_inputs",
    }


def get_platform_runtime_adapter(profile: str | None, *, platform_id: str) -> StrategyRuntimeAdapter:
    canonical_profile = resolve_canonical_profile(profile)
    adapters = PLATFORM_RUNTIME_ADAPTERS.get(str(platform_id).strip().lower())
    if adapters is None:
        raise ValueError(f"Unsupported platform runtime adapter lookup for {platform_id!r}")
    try:
        adapter = adapters[canonical_profile]
    except KeyError as exc:
        raise ValueError(
            f"Strategy profile {canonical_profile!r} has no runtime adapter for platform {platform_id!r}"
        ) from exc
    return validate_strategy_runtime_adapter(adapter)


__all__ = [
    "IBKR_PLATFORM",
    "SCHWAB_PLATFORM",
    "LONGBRIDGE_PLATFORM",
    "IBKR_RUNTIME_ADAPTERS",
    "PLATFORM_RUNTIME_ADAPTERS",
    "derive_runtime_input_mode",
    "describe_platform_runtime_requirements",
    "get_platform_runtime_adapter",
]
