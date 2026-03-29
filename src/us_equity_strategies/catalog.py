from __future__ import annotations

from quant_platform_kit.common.strategies import StrategyDefinition


STRATEGY_DEFINITIONS: dict[str, StrategyDefinition] = {}


def get_strategy_definitions() -> dict[str, StrategyDefinition]:
    return dict(STRATEGY_DEFINITIONS)


def get_strategy_definition(profile: str) -> StrategyDefinition:
    normalized = str(profile or "").strip().lower()
    if normalized not in STRATEGY_DEFINITIONS:
        supported = ", ".join(sorted(STRATEGY_DEFINITIONS)) or "<none>"
        raise ValueError(
            f"Unknown us_equity strategy profile={profile!r}; supported values: {supported}"
        )
    return STRATEGY_DEFINITIONS[normalized]
