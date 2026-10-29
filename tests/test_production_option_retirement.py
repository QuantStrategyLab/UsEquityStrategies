"""Real entrypoints reject retired options before attempting market decisions."""
from types import SimpleNamespace

import pytest
from quant_platform_kit.common.strategy_contracts import StrategyContext

from us_equity_strategies import entrypoints
from us_equity_strategies.option_overlay_retirement import OptionOverlayRetiredError

ENTRYPOINT_NAMES = (
    "tqqq_growth_income_entrypoint",
    "soxl_soxx_trend_income_entrypoint",
    "russell_top50_leader_rotation_entrypoint",
    "ibit_smart_dca_entrypoint",
    "nasdaq_sp500_smart_dca_entrypoint",
    "us_equity_combo_entrypoint",
    "us_equity_combo_core_entrypoint",
    "us_equity_combo_leveraged_entrypoint",
)


@pytest.mark.parametrize("name", ENTRYPOINT_NAMES)
def test_production_manifest_has_no_option_defaults(name):
    manifest = getattr(entrypoints, name).manifest
    assert not any(key.startswith("option_") for key in manifest.default_config)


@pytest.mark.parametrize("name", ENTRYPOINT_NAMES)
@pytest.mark.parametrize("runtime_config", [
    {"option_overlay_enabled": True},
    {"option_growth_overlay_enabled": True},
    {"option_income_overlay_recipe": "soxx_put_credit_spread_income_v1"},
    {"option_overlay_enabled": False, "option_growth_overlay_recipe": "tqqq_leaps_growth_v1"},
    {"option_growth_overlay_nav_budget_ratio": 0.03},
])
def test_legacy_option_configuration_rejects_before_market_inputs(name, runtime_config):
    ctx = StrategyContext(as_of="2026-10-29", runtime_config=runtime_config)
    with pytest.raises(OptionOverlayRetiredError, match="retired"):
        getattr(entrypoints, name).evaluate(ctx)


@pytest.mark.parametrize("name", ENTRYPOINT_NAMES)
@pytest.mark.parametrize("source", ["state", "market_data", "portfolio_metadata"])
@pytest.mark.parametrize("field", ["option_positions", "option_order_intents"])
def test_known_option_obligations_reject_before_market_inputs(name, source, field):
    evidence = {field: [{"underlier": "SPY", "quantity": 1}]}
    kwargs = {source: evidence} if source != "portfolio_metadata" else {
        "portfolio": SimpleNamespace(metadata=evidence)
    }
    ctx = StrategyContext(as_of="2026-10-29", **kwargs)
    with pytest.raises(OptionOverlayRetiredError, match="retired"):
        getattr(entrypoints, name).evaluate(ctx)


@pytest.mark.parametrize("name", [
    "evaluate_us_equity_combo", "evaluate_us_equity_combo_core", "evaluate_us_equity_combo_leveraged",
])
def test_combo_retirement_emits_no_option_budget(name):
    from tests.test_mega_cap_leader_rotation import _mega_snapshot
    from us_equity_strategies import combo_entrypoints

    ctx = StrategyContext(
        as_of="2026-04-01",
        market_data={"russell_snapshot": _mega_snapshot(), "market_data": {}},
        portfolio=SimpleNamespace(total_equity=1_000_000.0, positions=(), metadata={}),
    )
    decision = getattr(combo_entrypoints, name)(ctx)
    assert decision.budgets == ()
    assert decision.diagnostics["option_overlay_diagnostics"]["option_overlay_status"] == "retired"
    assert not decision.diagnostics["option_overlay_diagnostics"]["option_overlay_active"]


@pytest.mark.parametrize("field", ["option_positions", "option_order_intents"])
def test_known_option_obligations_in_base_diagnostics_are_rejected(field):
    from us_equity_strategies.entrypoints._common import (
        build_option_overlay_diagnostics,
    )

    with pytest.raises(OptionOverlayRetiredError, match="retired"):
        build_option_overlay_diagnostics(
            {}, StrategyContext(as_of="2026-10-29"),
            base_diagnostics={field: [{"underlier": "SPY", "quantity": 1}]},
        )
