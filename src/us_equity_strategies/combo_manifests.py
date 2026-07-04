from __future__ import annotations

from quant_platform_kit.strategy_contracts import StrategyManifest

from us_equity_strategies.strategies import us_equity_combo
from us_equity_strategies.strategies import us_equity_combo_core
from us_equity_strategies.strategies import us_equity_combo_leveraged

US_EQUITY_COMBO_PROFILE = us_equity_combo.PROFILE_NAME
US_EQUITY_COMBO_CORE_PROFILE = us_equity_combo_core.PROFILE_NAME
US_EQUITY_COMBO_LEVERAGED_PROFILE = us_equity_combo_leveraged.PROFILE_NAME


def _manifest(
    *,
    profile: str,
    domain: str,
    display_name: str,
    description: str,
    aliases: tuple[str, ...] = (),
    required_inputs: frozenset[str] = frozenset(),
    default_config: dict[str, object] | None = None,
) -> StrategyManifest:
    return StrategyManifest(
        profile=profile,
        domain=domain,
        display_name=display_name,
        description=description,
        aliases=aliases,
        required_inputs=required_inputs,
        default_config=default_config or {},
    )


_INCOME_LAYER_DEFAULT_CONFIG: dict[str, object] = {
    "income_layer_enabled": True,
    "income_layer_start_usd": 500000.0,
    "income_layer_max_ratio": 0.25,
    "income_layer_allocations": {
        "SCHD": 0.25,
        "DGRO": 0.25,
        "SGOV": 0.20,
        "SPYI": 0.15,
        "QQQI": 0.15,
    },
}

_OPTION_OVERLAY_DEFAULT_CONFIG: dict[str, object] = {
    "option_overlay_enabled": True,
    "option_growth_overlay_enabled": True,
    "option_growth_overlay_recipe": "spy_leaps_growth_v1",
    "option_growth_overlay_start_usd": 500000.0,
    "option_growth_overlay_nav_budget_ratio": 0.015,
}

_US_CORE_COMBO_DEFAULT_CONFIG: dict[str, object] = {
    "dynamic": True,
    "russell_weight": 0.40,
    "dca_weight": 0.40,
    "safe_weight": 0.20,
    "soft_russell_weight": 0.35,
    "soft_dca_weight": 0.35,
    "soft_safe_weight": 0.30,
    "hard_russell_weight": 0.20,
    "hard_dca_weight": 0.05,
    "hard_safe_weight": 0.75,
    "dca_allocations": {
        "QQQM": 0.50,
        "SPLG": 0.50,
    },
    "safe_haven": "BOXX",
    "execution_cash_reserve_ratio": 0.02,
    "rebalance_frequency": "monthly",
    **_INCOME_LAYER_DEFAULT_CONFIG,
    **_OPTION_OVERLAY_DEFAULT_CONFIG,
}

us_equity_combo_manifest = _manifest(
    profile=US_EQUITY_COMBO_PROFILE,
    domain="quant_combo",
    display_name="US Equity Combo",
    description=(
        "Live US core combo: Russell Top50 leaders (40%) + Nasdaq/S&P ETF "
        "sleeve (40%) + BOXX cash defense (20%), with dynamic defense weights."
    ),
    aliases=(),
    required_inputs=frozenset({"russell_snapshot", "current_holdings"}),
    default_config={**_US_CORE_COMBO_DEFAULT_CONFIG},
)

us_equity_combo_core_manifest = _manifest(
    profile=US_EQUITY_COMBO_CORE_PROFILE,
    domain="quant_combo",
    display_name="US Core Combo Shadow",
    description=(
        "Shadow candidate: Russell Top50 leaders (40%) + Nasdaq/S&P ETF "
        "sleeve (40%) + BOXX/SGOV-style cash defense (20%), with hard-defense "
        "risk-off weights."
    ),
    aliases=(),
    required_inputs=frozenset({"russell_snapshot", "current_holdings"}),
    default_config={
        **_US_CORE_COMBO_DEFAULT_CONFIG,
        "shadow_candidate": True,
    },
)

us_equity_combo_leveraged_manifest = _manifest(
    profile=US_EQUITY_COMBO_LEVERAGED_PROFILE,
    domain="quant_combo",
    display_name="US Equity Combo Leveraged",
    description=(
        "Leveraged US combo: TQQQ (40%) + SOXL (20%) + BOXX (40%) with "
        "SPY MA200 dynamic risk-off cut."
    ),
    aliases=(),
    required_inputs=frozenset({"market_data"}),
    default_config={
        "dynamic": True,
        "execution_cash_reserve_ratio": 0.02,
        "rebalance_frequency": "daily",
        **_INCOME_LAYER_DEFAULT_CONFIG,
        **_OPTION_OVERLAY_DEFAULT_CONFIG,
    },
)

MANIFESTS = {
    us_equity_combo_manifest.profile: us_equity_combo_manifest,
    us_equity_combo_core_manifest.profile: us_equity_combo_core_manifest,
    us_equity_combo_leveraged_manifest.profile: us_equity_combo_leveraged_manifest,
}

MANIFEST_ALIASES = {
    str(alias).strip().lower(): manifest.profile
    for manifest in MANIFESTS.values()
    for alias in manifest.aliases
}


def get_strategy_manifest(profile: str) -> StrategyManifest:
    normalized = str(profile or "").strip().lower().replace("-", "_")
    return MANIFESTS[MANIFEST_ALIASES.get(normalized, normalized)]


__all__ = [
    "US_EQUITY_COMBO_PROFILE",
    "US_EQUITY_COMBO_CORE_PROFILE",
    "US_EQUITY_COMBO_LEVERAGED_PROFILE",
    "MANIFESTS",
    "get_strategy_manifest",
    "us_equity_combo_manifest",
    "us_equity_combo_core_manifest",
    "us_equity_combo_leveraged_manifest",
]
