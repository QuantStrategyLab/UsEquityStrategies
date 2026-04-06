from __future__ import annotations

from quant_platform_kit.strategy_contracts import (
    StrategyRuntimeAdapter,
    validate_strategy_runtime_adapter,
)

from us_equity_strategies.catalog import resolve_canonical_profile
from us_equity_strategies.strategies import (
    russell_1000_multi_factor_defensive as legacy_russell,
    tech_pullback_cash_buffer as legacy_tech_pullback,
)


IBKR_PLATFORM = "ibkr"


IBKR_RUNTIME_ADAPTERS: dict[str, StrategyRuntimeAdapter] = {
    "global_etf_rotation": StrategyRuntimeAdapter(
        status_icon="🐤",
    ),
    "russell_1000_multi_factor_defensive": StrategyRuntimeAdapter(
        status_icon=legacy_russell.STATUS_ICON,
        required_feature_columns=legacy_russell.REQUIRED_FEATURE_COLUMNS,
        managed_symbols_extractor=legacy_russell.extract_managed_symbols,
    ),
    "tech_pullback_cash_buffer": StrategyRuntimeAdapter(
        status_icon=legacy_tech_pullback.STATUS_ICON,
        required_feature_columns=legacy_tech_pullback.REQUIRED_FEATURE_COLUMNS,
        snapshot_date_columns=legacy_tech_pullback.SNAPSHOT_DATE_COLUMNS,
        max_snapshot_month_lag=legacy_tech_pullback.MAX_SNAPSHOT_MONTH_LAG,
        require_snapshot_manifest=legacy_tech_pullback.REQUIRE_SNAPSHOT_MANIFEST,
        snapshot_contract_version=legacy_tech_pullback.SNAPSHOT_CONTRACT_VERSION,
        runtime_parameter_loader=legacy_tech_pullback.load_runtime_parameters,
        managed_symbols_extractor=legacy_tech_pullback.extract_managed_symbols,
    ),
}

PLATFORM_RUNTIME_ADAPTERS: dict[str, dict[str, StrategyRuntimeAdapter]] = {
    IBKR_PLATFORM: IBKR_RUNTIME_ADAPTERS,
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
    "IBKR_RUNTIME_ADAPTERS",
    "PLATFORM_RUNTIME_ADAPTERS",
    "get_platform_runtime_adapter",
]
