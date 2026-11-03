import pytest

from us_equity_strategies.option_overlay import option_overlay_default_config
from us_equity_strategies.option_overlay_retirement import (
    OptionOverlayRetiredError,
    validate_retired_option_inputs,
)


@pytest.mark.parametrize("profile", [
    "tqqq_growth_income", "soxl_soxx_trend_income", "russell_top50_leader_rotation",
    "nasdaq_sp500_smart_dca", "ibit_smart_dca",
])
def test_no_core_profile_advertises_retired_option_defaults(profile):
    assert option_overlay_default_config(profile) == {}


@pytest.mark.parametrize("config", [
    {"option_overlay_enabled": True},
    {"option_growth_overlay_enabled": True},
    {"option_income_overlay_enabled": "true"},
    {"option_growth_overlay_recipe": "tqqq_leaps_growth_v1"},
    {"option_overlay_enabled": False, "option_income_overlay_recipe": "soxx_put_credit_spread_income_v1"},
    {"option_growth_overlay_nav_budget_ratio": 0.03},
    {"option_overlay_enabled": "unexpected"},
    {"option_unknown_field": 1},
])
def test_legacy_option_config_needs_explicit_migration(config):
    with pytest.raises(OptionOverlayRetiredError, match="retired"):
        validate_retired_option_inputs(config)


def test_absent_or_explicitly_disabled_options_do_not_block_other_configuration():
    validate_retired_option_inputs({"unrelated": 1})
    validate_retired_option_inputs({"option_overlay_enabled": False, "option_growth_overlay_enabled": False})


@pytest.mark.parametrize("kwargs", [
    {"option_order_intents": {"intents": [{"asset_class": "option"}]}},
    {"option_order_intents": [{"underlier": "SPY"}]},
    {"option_positions": [{"symbol": "SPY", "asset_class": "option", "quantity": 1}]},
    {"option_positions": "unknown"},
])
def test_option_intents_or_holdings_block_until_reconciled(kwargs):
    with pytest.raises(OptionOverlayRetiredError, match="retired"):
        validate_retired_option_inputs({}, **kwargs)
