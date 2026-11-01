import pytest

from us_equity_strategies.catalog import get_runtime_enabled_profiles
from us_equity_strategies.platform_registry_support import (
    resolve_platform_strategy_definition,
)
from us_equity_strategies.runtime_allowlist import get_runtime_selectable_profiles


def test_legacy_runtime_entrypoint_reads_explicit_allowlist():
    assert get_runtime_enabled_profiles() == get_runtime_selectable_profiles()


def test_allowlist_does_not_include_research_profiles():
    assert "tecl_xlk_trend_income" not in get_runtime_selectable_profiles()
    assert "us_equity_combo_leveraged" not in get_runtime_selectable_profiles()


def test_platform_selection_restores_global_etf_and_keeps_existing_cores():
    enabled = get_runtime_enabled_profiles()
    cores = {
        "global_etf_rotation", "tqqq_growth_income", "soxl_soxx_trend_income",
        "russell_top50_leader_rotation", "ibit_smart_dca", "nasdaq_sp500_smart_dca",
    }
    assert enabled == cores
    kwargs = {
        "platform_id": "ibkr", "expected_platform_id": "ibkr",
        "enabled_profiles": enabled,
        "platform_supported_domains": {"ibkr": frozenset({"us_equity"})},
        "require_explicit": True,
    }
    for profile in cores:
        assert resolve_platform_strategy_definition(profile, **kwargs).profile == profile
    for alias in ("global_macro_etf_rotation", "global_etf_confidence_vol_gate"):
        assert resolve_platform_strategy_definition(alias, **kwargs).profile == "global_etf_rotation"
    for profile in ("tecl_xlk_trend_income", "us_equity_combo_leveraged", "unknown_profile"):
        with pytest.raises(ValueError, match="Unsupported STRATEGY_PROFILE"):
            resolve_platform_strategy_definition(profile, **kwargs)


@pytest.mark.parametrize("platform", ["ibkr", "longbridge", "schwab", "firstrade", "qmt"])
def test_global_etf_is_selectable_for_new_compatible_platform_targets(platform):
    kwargs = {
        "platform_id": platform,
        "expected_platform_id": platform,
        "enabled_profiles": get_runtime_enabled_profiles(),
        "platform_supported_domains": {platform: frozenset({"us_equity"})},
        "require_explicit": True,
    }
    assert resolve_platform_strategy_definition("global_etf_rotation", **kwargs).profile == "global_etf_rotation"
    kwargs["enabled_profiles"] = frozenset({"russell_top50_leader_rotation"})
    with pytest.raises(ValueError, match="Unsupported STRATEGY_PROFILE"):
        resolve_platform_strategy_definition("global_etf_rotation", **kwargs)
