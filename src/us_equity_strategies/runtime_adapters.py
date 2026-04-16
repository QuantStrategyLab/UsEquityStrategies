from __future__ import annotations

from dataclasses import replace

from quant_platform_kit.strategy_contracts import (
    StrategyRuntimeAdapter,
    validate_strategy_runtime_adapter,
)

from us_equity_strategies.catalog import (
    DYNAMIC_MEGA_LEVERAGED_PULLBACK_PROFILE,
    MEGA_CAP_LEADER_ROTATION_AGGRESSIVE_PROFILE,
    MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE,
    QQQ_TECH_ENHANCEMENT_PROFILE,
    get_strategy_definition,
    get_strategy_definitions,
    resolve_canonical_profile,
)
from us_equity_strategies.strategies import (
    dynamic_mega_leveraged_pullback as dynamic_mega_leveraged_pullback_strategy,
    mega_cap_leader_rotation_dynamic_top20 as mega_cap_leader_rotation_dynamic_top20_strategy,
    qqq_tech_enhancement as qqq_tech_enhancement_strategy,
    russell_1000_multi_factor_defensive as legacy_russell,
)


IBKR_PLATFORM = "ibkr"
SCHWAB_PLATFORM = "schwab"
LONGBRIDGE_PLATFORM = "longbridge"

SUPPORTED_RUNTIME_PLATFORMS = frozenset(
    {IBKR_PLATFORM, SCHWAB_PLATFORM, LONGBRIDGE_PLATFORM}
)

PLATFORM_NATIVE_TARGET_MODES: dict[str, str] = {
    IBKR_PLATFORM: "weight",
    SCHWAB_PLATFORM: "value",
    LONGBRIDGE_PLATFORM: "value",
}


BASE_RUNTIME_ADAPTERS: dict[str, StrategyRuntimeAdapter] = {
    "global_etf_rotation": StrategyRuntimeAdapter(status_icon="🐤"),
    "tqqq_growth_income": StrategyRuntimeAdapter(status_icon="🐤"),
    "soxl_soxx_trend_income": StrategyRuntimeAdapter(status_icon="🐤"),
    "russell_1000_multi_factor_defensive": StrategyRuntimeAdapter(
        status_icon=legacy_russell.STATUS_ICON,
        required_feature_columns=legacy_russell.REQUIRED_FEATURE_COLUMNS,
        managed_symbols_extractor=legacy_russell.extract_managed_symbols,
    ),
    QQQ_TECH_ENHANCEMENT_PROFILE: StrategyRuntimeAdapter(
        status_icon=qqq_tech_enhancement_strategy.STATUS_ICON,
        required_feature_columns=qqq_tech_enhancement_strategy.REQUIRED_FEATURE_COLUMNS,
        snapshot_date_columns=qqq_tech_enhancement_strategy.SNAPSHOT_DATE_COLUMNS,
        max_snapshot_month_lag=qqq_tech_enhancement_strategy.MAX_SNAPSHOT_MONTH_LAG,
        require_snapshot_manifest=qqq_tech_enhancement_strategy.REQUIRE_SNAPSHOT_MANIFEST,
        snapshot_contract_version=qqq_tech_enhancement_strategy.SNAPSHOT_CONTRACT_VERSION,
        runtime_parameter_loader=qqq_tech_enhancement_strategy.load_runtime_parameters,
        managed_symbols_extractor=qqq_tech_enhancement_strategy.extract_managed_symbols,
    ),
    MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE: StrategyRuntimeAdapter(
        status_icon=mega_cap_leader_rotation_dynamic_top20_strategy.STATUS_ICON,
        required_feature_columns=mega_cap_leader_rotation_dynamic_top20_strategy.REQUIRED_FEATURE_COLUMNS,
        snapshot_date_columns=mega_cap_leader_rotation_dynamic_top20_strategy.SNAPSHOT_DATE_COLUMNS,
        max_snapshot_month_lag=mega_cap_leader_rotation_dynamic_top20_strategy.MAX_SNAPSHOT_MONTH_LAG,
        require_snapshot_manifest=mega_cap_leader_rotation_dynamic_top20_strategy.REQUIRE_SNAPSHOT_MANIFEST,
        snapshot_contract_version=mega_cap_leader_rotation_dynamic_top20_strategy.SNAPSHOT_CONTRACT_VERSION,
        managed_symbols_extractor=mega_cap_leader_rotation_dynamic_top20_strategy.extract_managed_symbols,
    ),
    MEGA_CAP_LEADER_ROTATION_AGGRESSIVE_PROFILE: StrategyRuntimeAdapter(
        status_icon=mega_cap_leader_rotation_dynamic_top20_strategy.STATUS_ICON,
        required_feature_columns=mega_cap_leader_rotation_dynamic_top20_strategy.REQUIRED_FEATURE_COLUMNS,
        snapshot_date_columns=mega_cap_leader_rotation_dynamic_top20_strategy.SNAPSHOT_DATE_COLUMNS,
        max_snapshot_month_lag=mega_cap_leader_rotation_dynamic_top20_strategy.MAX_SNAPSHOT_MONTH_LAG,
        require_snapshot_manifest=mega_cap_leader_rotation_dynamic_top20_strategy.REQUIRE_SNAPSHOT_MANIFEST,
        snapshot_contract_version="mega_cap_leader_rotation_aggressive.feature_snapshot.v1",
        managed_symbols_extractor=mega_cap_leader_rotation_dynamic_top20_strategy.extract_managed_symbols,
    ),
    DYNAMIC_MEGA_LEVERAGED_PULLBACK_PROFILE: StrategyRuntimeAdapter(
        status_icon=dynamic_mega_leveraged_pullback_strategy.STATUS_ICON,
        required_feature_columns=dynamic_mega_leveraged_pullback_strategy.REQUIRED_FEATURE_COLUMNS,
        snapshot_date_columns=dynamic_mega_leveraged_pullback_strategy.SNAPSHOT_DATE_COLUMNS,
        max_snapshot_month_lag=dynamic_mega_leveraged_pullback_strategy.MAX_SNAPSHOT_MONTH_LAG,
        require_snapshot_manifest=dynamic_mega_leveraged_pullback_strategy.REQUIRE_SNAPSHOT_MANIFEST,
        snapshot_contract_version=dynamic_mega_leveraged_pullback_strategy.SNAPSHOT_CONTRACT_VERSION,
        managed_symbols_extractor=dynamic_mega_leveraged_pullback_strategy.extract_managed_symbols,
    ),
}


def _build_runtime_adapter_for_platform(
    profile: str,
    *,
    platform_id: str,
) -> StrategyRuntimeAdapter:
    canonical_profile = resolve_canonical_profile(profile)
    normalized_platform = str(platform_id).strip().lower()
    if normalized_platform not in SUPPORTED_RUNTIME_PLATFORMS:
        raise ValueError(f"Unsupported platform runtime adapter lookup for {platform_id!r}")

    definition = get_strategy_definition(canonical_profile)
    if normalized_platform not in definition.supported_platforms:
        raise ValueError(
            f"Strategy profile {canonical_profile!r} does not declare support for platform {platform_id!r}"
        )

    try:
        base_adapter = BASE_RUNTIME_ADAPTERS[canonical_profile]
    except KeyError as exc:
        raise ValueError(f"Strategy profile {canonical_profile!r} has no runtime adapter spec") from exc

    available_inputs = set(base_adapter.available_inputs or definition.required_inputs)
    available_inputs.update(definition.required_inputs)

    native_target_mode = PLATFORM_NATIVE_TARGET_MODES[normalized_platform]
    if definition.target_mode != native_target_mode:
        available_inputs.add("portfolio_snapshot")

    portfolio_input_name = base_adapter.portfolio_input_name
    if "portfolio_snapshot" in available_inputs:
        portfolio_input_name = portfolio_input_name or "portfolio_snapshot"

    available_capabilities = set(base_adapter.available_capabilities)
    if normalized_platform == IBKR_PLATFORM:
        available_capabilities.add("broker_client")

    return validate_strategy_runtime_adapter(
        replace(
            base_adapter,
            available_inputs=frozenset(available_inputs),
            available_capabilities=frozenset(available_capabilities),
            portfolio_input_name=portfolio_input_name,
        )
    )


def _build_platform_runtime_adapter_map(platform_id: str) -> dict[str, StrategyRuntimeAdapter]:
    normalized_platform = str(platform_id).strip().lower()
    adapters: dict[str, StrategyRuntimeAdapter] = {}
    for profile, definition in get_strategy_definitions().items():
        if normalized_platform not in definition.supported_platforms:
            continue
        adapters[profile] = _build_runtime_adapter_for_platform(
            profile,
            platform_id=normalized_platform,
        )
    return adapters


PLATFORM_RUNTIME_ADAPTERS: dict[str, dict[str, StrategyRuntimeAdapter]] = {
    platform_id: _build_platform_runtime_adapter_map(platform_id)
    for platform_id in sorted(SUPPORTED_RUNTIME_PLATFORMS)
}
IBKR_RUNTIME_ADAPTERS: dict[str, StrategyRuntimeAdapter] = PLATFORM_RUNTIME_ADAPTERS[IBKR_PLATFORM]


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
    if normalized == frozenset({"feature_snapshot", "market_history", "benchmark_history", "portfolio_snapshot"}):
        return "feature_snapshot+market_history+benchmark_history+portfolio_snapshot"
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
        "requires_snapshot_manifest_path": bool(
            requires_snapshot_artifacts and adapter.require_snapshot_manifest
        ),
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
    "BASE_RUNTIME_ADAPTERS",
    "IBKR_PLATFORM",
    "SCHWAB_PLATFORM",
    "LONGBRIDGE_PLATFORM",
    "IBKR_RUNTIME_ADAPTERS",
    "PLATFORM_RUNTIME_ADAPTERS",
    "PLATFORM_NATIVE_TARGET_MODES",
    "derive_runtime_input_mode",
    "describe_platform_runtime_requirements",
    "get_platform_runtime_adapter",
]
