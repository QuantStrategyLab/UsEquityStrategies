from __future__ import annotations

from quant_platform_kit.common.strategies import (
    StrategyComponentDefinition,
    StrategyDefinition,
    US_EQUITY_DOMAIN,
)

GLOBAL_ETF_ROTATION_PROFILE = "global_etf_rotation"
HYBRID_GROWTH_INCOME_PROFILE = "hybrid_growth_income"
SEMICONDUCTOR_ROTATION_INCOME_PROFILE = "semiconductor_rotation_income"

STRATEGY_DEFINITIONS: dict[str, StrategyDefinition] = {
    GLOBAL_ETF_ROTATION_PROFILE: StrategyDefinition(
        profile=GLOBAL_ETF_ROTATION_PROFILE,
        domain=US_EQUITY_DOMAIN,
        supported_platforms=frozenset({"ibkr"}),
        components=(
            StrategyComponentDefinition(
                name="signal_logic",
                module_path="us_equity_strategies.strategies.global_etf_rotation",
            ),
        ),
    ),
    HYBRID_GROWTH_INCOME_PROFILE: StrategyDefinition(
        profile=HYBRID_GROWTH_INCOME_PROFILE,
        domain=US_EQUITY_DOMAIN,
        supported_platforms=frozenset({"schwab"}),
        components=(
            StrategyComponentDefinition(
                name="allocation",
                module_path="us_equity_strategies.strategies.hybrid_growth_income",
            ),
        ),
    ),
    SEMICONDUCTOR_ROTATION_INCOME_PROFILE: StrategyDefinition(
        profile=SEMICONDUCTOR_ROTATION_INCOME_PROFILE,
        domain=US_EQUITY_DOMAIN,
        supported_platforms=frozenset({"longbridge"}),
        components=(
            StrategyComponentDefinition(
                name="allocation",
                module_path="us_equity_strategies.strategies.semiconductor_rotation_income",
            ),
        ),
    ),
}


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
