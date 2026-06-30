from __future__ import annotations

from dataclasses import replace

from quant_platform_kit.strategy_contracts import (
    StrategyArtifactContract,
    StrategyRuntimeAdapter,
    StrategyRuntimePolicy,
    resolve_strategy_artifact_contract,
    validate_strategy_runtime_adapter,
)

from us_equity_strategies.catalog import (
    IBIT_SMART_DCA_PROFILE,
    RUSSELL_TOP50_LEADER_ROTATION_PROFILE,
    NASDAQ_SP500_SMART_DCA_PROFILE,
    US_EQUITY_COMBO_PROFILE,
    US_EQUITY_COMBO_LEVERAGED_PROFILE,
    get_strategy_definition,
    get_strategy_definitions,
    resolve_canonical_profile,
)
from us_equity_strategies.strategies import (
    global_etf_rotation as global_etf_rotation_strategy,
    ibit_smart_dca as ibit_smart_dca_strategy,
    mega_cap_leader_rotation as mega_cap_leader_rotation_strategy,
    nasdaq_sp500_smart_dca as nasdaq_sp500_smart_dca_strategy,
)


IBKR_PLATFORM = "ibkr"
SCHWAB_PLATFORM = "schwab"
LONGBRIDGE_PLATFORM = "longbridge"
FIRSTRADE_PLATFORM = "firstrade"
PAPER_SIGNAL_PLATFORM = "paper_signal"

SUPPORTED_RUNTIME_PLATFORMS = frozenset(
    {
        IBKR_PLATFORM,
        SCHWAB_PLATFORM,
        LONGBRIDGE_PLATFORM,
        FIRSTRADE_PLATFORM,
        PAPER_SIGNAL_PLATFORM,
    }
)

PLATFORM_NATIVE_TARGET_MODES: dict[str, str] = {
    IBKR_PLATFORM: "weight",
    SCHWAB_PLATFORM: "value",
    LONGBRIDGE_PLATFORM: "value",
    FIRSTRADE_PLATFORM: "value",
}


BASE_RUNTIME_ADAPTERS: dict[str, StrategyRuntimeAdapter] = {
    "global_etf_rotation": StrategyRuntimeAdapter(
        status_icon="🐤",
        required_feature_columns=global_etf_rotation_strategy.REQUIRED_FEATURE_COLUMNS,
        snapshot_date_columns=global_etf_rotation_strategy.SNAPSHOT_DATE_COLUMNS,
        max_snapshot_month_lag=global_etf_rotation_strategy.MAX_SNAPSHOT_MONTH_LAG,
        require_snapshot_manifest=global_etf_rotation_strategy.REQUIRE_SNAPSHOT_MANIFEST,
        snapshot_contract_version=global_etf_rotation_strategy.SNAPSHOT_CONTRACT_VERSION,
        managed_symbols_extractor=global_etf_rotation_strategy.extract_managed_symbols,
        artifact_contract=StrategyArtifactContract(
            requires_snapshot_artifacts=True,
            requires_snapshot_manifest_path=global_etf_rotation_strategy.REQUIRE_SNAPSHOT_MANIFEST,
            snapshot_contract_version=global_etf_rotation_strategy.SNAPSHOT_CONTRACT_VERSION,
        ),
        runtime_policy=StrategyRuntimePolicy(signal_effective_after_trading_days=1),
    ),
    "tqqq_growth_income": StrategyRuntimeAdapter(
        status_icon="🐤",
        runtime_policy=StrategyRuntimePolicy(signal_effective_after_trading_days=1),
    ),
    "soxl_soxx_trend_income": StrategyRuntimeAdapter(
        status_icon="🐤",
        runtime_policy=StrategyRuntimePolicy(signal_effective_after_trading_days=1),
    ),
    RUSSELL_TOP50_LEADER_ROTATION_PROFILE: StrategyRuntimeAdapter(
        status_icon=mega_cap_leader_rotation_strategy.STATUS_ICON,
        required_feature_columns=mega_cap_leader_rotation_strategy.REQUIRED_FEATURE_COLUMNS,
        snapshot_date_columns=mega_cap_leader_rotation_strategy.SNAPSHOT_DATE_COLUMNS,
        max_snapshot_month_lag=mega_cap_leader_rotation_strategy.MAX_SNAPSHOT_MONTH_LAG,
        require_snapshot_manifest=mega_cap_leader_rotation_strategy.REQUIRE_SNAPSHOT_MANIFEST,
        snapshot_contract_version="russell_top50_leader_rotation.feature_snapshot.v1",
        managed_symbols_extractor=mega_cap_leader_rotation_strategy.extract_managed_symbols,
        artifact_contract=StrategyArtifactContract(
            requires_snapshot_artifacts=True,
            requires_snapshot_manifest_path=mega_cap_leader_rotation_strategy.REQUIRE_SNAPSHOT_MANIFEST,
            snapshot_contract_version="russell_top50_leader_rotation.feature_snapshot.v1",
        ),
    ),
    NASDAQ_SP500_SMART_DCA_PROFILE: StrategyRuntimeAdapter(
        status_icon=nasdaq_sp500_smart_dca_strategy.STATUS_ICON,
        portfolio_input_name="portfolio_snapshot",
        available_capabilities=frozenset({"fractional_share_execution"}),
        runtime_policy=StrategyRuntimePolicy(signal_effective_after_trading_days=0),
    ),
    IBIT_SMART_DCA_PROFILE: StrategyRuntimeAdapter(
        status_icon=ibit_smart_dca_strategy.STATUS_ICON,
        available_inputs=frozenset({"derived_indicators", "market_history", "portfolio_snapshot"}),
        portfolio_input_name="portfolio_snapshot",
        available_capabilities=frozenset({"fractional_share_execution"}),
        runtime_policy=StrategyRuntimePolicy(signal_effective_after_trading_days=0),
    ),
    US_EQUITY_COMBO_PROFILE: StrategyRuntimeAdapter(
        status_icon="\U0001f1fa\U0001f1f8",
        available_inputs=frozenset({"russell_snapshot", "current_holdings"}),
        runtime_policy=StrategyRuntimePolicy(signal_effective_after_trading_days=0),
    ),
    US_EQUITY_COMBO_LEVERAGED_PROFILE: StrategyRuntimeAdapter(
        status_icon="\U0001f1fa\U0001f1f8",
        available_inputs=frozenset({"market_data"}),
        runtime_policy=StrategyRuntimePolicy(signal_effective_after_trading_days=0),
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

    native_target_mode = PLATFORM_NATIVE_TARGET_MODES.get(normalized_platform)
    if native_target_mode is not None and definition.target_mode != native_target_mode:
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
        if profile not in BASE_RUNTIME_ADAPTERS:
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
    if normalized == frozenset({"market_history", "portfolio_snapshot"}):
        return "market_history+portfolio_snapshot"
    if normalized == frozenset({"feature_snapshot"}):
        return "feature_snapshot"
    if normalized == frozenset({"feature_snapshot", "market_history", "benchmark_history", "portfolio_snapshot"}):
        return "feature_snapshot+market_history+benchmark_history+portfolio_snapshot"
    return "+".join(sorted(normalized)) or "none"


def describe_platform_runtime_requirements(profile: str | None, *, platform_id: str) -> dict[str, object]:
    canonical_profile = resolve_canonical_profile(profile)
    definition = get_strategy_definition(canonical_profile)
    adapter = get_platform_runtime_adapter(canonical_profile, platform_id=platform_id)
    artifact_contract = resolve_strategy_artifact_contract(
        adapter,
        required_inputs=definition.required_inputs,
    )
    requires_snapshot_artifacts = artifact_contract.requires_snapshot_artifacts
    return {
        "input_mode": derive_runtime_input_mode(definition.required_inputs),
        "requires_snapshot_artifacts": requires_snapshot_artifacts,
        "requires_snapshot_manifest_path": artifact_contract.requires_snapshot_manifest_path,
        "requires_strategy_config_path": artifact_contract.requires_strategy_config_path,
        "snapshot_contract_version": artifact_contract.snapshot_contract_version,
        "config_source_policy": artifact_contract.config_source_policy,
        "reconciliation_output_policy": adapter.runtime_policy.reconciliation_output_policy,
        "runtime_execution_window_trading_days": (
            adapter.runtime_policy.runtime_execution_window_trading_days
        ),
        "signal_effective_after_trading_days": (
            adapter.runtime_policy.signal_effective_after_trading_days
        ),
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
    "FIRSTRADE_PLATFORM",
    "PAPER_SIGNAL_PLATFORM",
    "IBKR_RUNTIME_ADAPTERS",
    "PLATFORM_RUNTIME_ADAPTERS",
    "PLATFORM_NATIVE_TARGET_MODES",
    "derive_runtime_input_mode",
    "describe_platform_runtime_requirements",
    "get_platform_runtime_adapter",
]
