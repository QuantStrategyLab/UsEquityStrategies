from us_equity_strategies.catalog import get_runtime_enabled_profiles
from us_equity_strategies.runtime_allowlist import get_runtime_selectable_profiles


def test_legacy_runtime_entrypoint_reads_explicit_allowlist():
    assert get_runtime_enabled_profiles() == get_runtime_selectable_profiles()


def test_allowlist_does_not_include_research_profiles():
    assert "tecl_xlk_trend_income" not in get_runtime_selectable_profiles()
    assert "us_equity_combo_leveraged" not in get_runtime_selectable_profiles()
