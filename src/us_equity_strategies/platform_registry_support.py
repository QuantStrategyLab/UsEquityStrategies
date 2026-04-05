from __future__ import annotations

from quant_platform_kit.common.strategies import StrategyDefinition

from .catalog import (
    get_strategy_definition,
    get_strategy_metadata,
    resolve_canonical_profile,
)


def get_enabled_profiles_for_platform(
    platform_id: str,
    *,
    expected_platform_id: str,
    enabled_profiles: frozenset[str],
) -> frozenset[str]:
    if platform_id != expected_platform_id:
        return frozenset()
    return enabled_profiles


def build_platform_profile_matrix(
    *,
    platform_id: str,
    enabled_profiles: frozenset[str],
    default_profile: str,
    rollback_profile: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for profile in sorted(enabled_profiles):
        definition = get_strategy_definition(profile)
        metadata = get_strategy_metadata(profile)
        rows.append(
            {
                "platform": platform_id,
                "canonical_profile": definition.profile,
                "display_name": metadata.display_name,
                "aliases": metadata.aliases,
                "enabled": True,
                "is_default": definition.profile == default_profile,
                "is_rollback": definition.profile == rollback_profile,
                "domain": definition.domain,
            }
        )
    return rows


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
    if platform_id != expected_platform_id:
        raise ValueError(f"Unsupported platform_id={platform_id!r}")

    normalized = str(raw_value or "").strip()
    if require_explicit and not normalized:
        raise EnvironmentError("STRATEGY_PROFILE is required")

    candidate = normalized or str(default_profile or "").strip()
    if not candidate:
        raise EnvironmentError("STRATEGY_PROFILE is required")

    canonical_profile = resolve_canonical_profile(candidate)
    supported = ", ".join(sorted(enabled_profiles))

    if canonical_profile not in enabled_profiles:
        raise ValueError(
            f"Unsupported STRATEGY_PROFILE={raw_value!r}; supported values: {supported}"
        )

    definition = get_strategy_definition(canonical_profile)
    if definition.domain not in platform_supported_domains.get(platform_id, frozenset()):
        raise ValueError(
            f"Unsupported strategy domain {definition.domain!r} for platform {platform_id!r}"
        )

    return definition
