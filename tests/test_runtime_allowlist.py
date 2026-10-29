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


def test_platform_selection_retires_global_etf_and_keeps_five_cores():
    enabled = get_runtime_enabled_profiles()
    cores = {
        "tqqq_growth_income", "soxl_soxx_trend_income",
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
    for profile in ("global_etf_rotation", "global_macro_etf_rotation", "global_etf_confidence_vol_gate"):
        with pytest.raises(ValueError, match="Unsupported STRATEGY_PROFILE"):
            resolve_platform_strategy_definition(profile, **kwargs)
