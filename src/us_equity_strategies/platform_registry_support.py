from __future__ import annotations

from quant_platform_kit.common.strategies import (
    PlatformStrategyPolicy,
    StrategyDefinition,
    build_platform_profile_matrix as qpk_build_platform_profile_matrix,
    get_enabled_profiles_for_platform as qpk_get_enabled_profiles_for_platform,
    resolve_platform_strategy_definition as qpk_resolve_platform_strategy_definition,
)

from .catalog import STRATEGY_CATALOG


def get_enabled_profiles_for_platform(
    platform_id: str,
    *,
    expected_platform_id: str,
    enabled_profiles: frozenset[str],
) -> frozenset[str]:
    return qpk_get_enabled_profiles_for_platform(
        platform_id,
        policy=PlatformStrategyPolicy(
            platform_id=expected_platform_id,
            supported_domains=frozenset(),
            enabled_profiles=enabled_profiles,
            default_profile="",
            rollback_profile="",
        ),
    )


def build_platform_profile_matrix(
    *,
    platform_id: str,
    enabled_profiles: frozenset[str],
    default_profile: str,
    rollback_profile: str,
) -> list[dict[str, object]]:
    return qpk_build_platform_profile_matrix(
        STRATEGY_CATALOG,
        policy=PlatformStrategyPolicy(
            platform_id=platform_id,
            supported_domains=frozenset(),
            enabled_profiles=enabled_profiles,
            default_profile=default_profile,
            rollback_profile=rollback_profile,
        ),
    )


def resolve_platform_strategy_definition(
    raw_value: str | None,
    *,
    platform_id: str,
    expected_platform_id: str,
    enabled_profiles: frozenset[str],
    platform_supported_domains: dict[str, frozenset[str]],
    default_profile: str | None = None,
    require_explicit: bool = False,
) -> StrategyDefinition:
    return qpk_resolve_platform_strategy_definition(
        raw_value,
        platform_id=platform_id,
        strategy_catalog=STRATEGY_CATALOG,
        policy=PlatformStrategyPolicy(
            platform_id=expected_platform_id,
            supported_domains=platform_supported_domains.get(expected_platform_id, frozenset()),
            enabled_profiles=enabled_profiles,
            default_profile=default_profile or "",
            rollback_profile=default_profile or "",
            require_explicit_profile=require_explicit,
        ),
    )
