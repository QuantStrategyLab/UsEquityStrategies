from types import SimpleNamespace
from unittest.mock import patch

import pytest
from quant_platform_kit.common.strategy_contracts import StrategyContext

from us_equity_strategies.combo_entrypoints import evaluate_us_equity_combo_leveraged
from us_equity_strategies.combo_plugin_pipeline import apply_income_layer
from us_equity_strategies.entrypoints._common import apply_income_layer_to_weights
from us_equity_strategies.income_budget import IncomeBudgetError


def config(**overrides):
    return {"income_layer_enabled": True, "income_layer_start_usd": 500_000.0,
            "income_layer_max_ratio": 0.25, "income_layer_allocations": {"SGOV": 1.0},
            "income_layer_core_stress_drawdown_ratio": 0.45,
            "income_layer_income_stress_drawdown_ratio": 0.08,
            "income_layer_base_drawdown_budget_ratio": 0.45,
            "income_layer_min_drawdown_budget_ratio": 0.25,
            "income_layer_drawdown_budget_decay_per_double": 0.05, **overrides}


def test_combo_budget_preserves_cash_and_is_not_applied_twice():
    weights = {"SPY": 0.6}
    diagnostics = apply_income_layer(weights, 1_000_000.0, config())
    ratio = diagnostics["income_layer_ratio"]
    assert weights == pytest.approx({"SPY": 0.6 - ratio, "SGOV": ratio})
    assert sum(weights.values()) == pytest.approx(0.6)
    once = dict(weights)
    apply_income_layer(weights, 1_000_000.0, config())
    assert weights == pytest.approx(once)


def test_disabled_existing_income_is_retained_without_extra_allocation():
    weights = {"SPY": 0.3, "SGOV": 0.3}
    apply_income_layer(weights, 1_000_000.0, config(income_layer_enabled=False),
                       market_values={"SGOV": 300_000.0})
    assert weights == {"SPY": 0.3, "SGOV": 0.3}


def test_common_and_combo_share_absolute_budget_contract():
    ctx = StrategyContext(as_of="2026-04-06", portfolio=SimpleNamespace(
        total_equity=1_000_000.0, positions=()))
    result, _ = apply_income_layer_to_weights({"SPY": 0.6}, income_layer_config=config(), ctx=ctx)
    combo = {"SPY": 0.6}
    apply_income_layer(combo, 1_000_000.0, config())
    assert result == pytest.approx(combo)
    assert sum(result.values()) == pytest.approx(0.6)


def test_public_combo_reads_current_income_holdings_and_never_issues_option_budget():
    ctx = StrategyContext(as_of="2026-04-06", portfolio=SimpleNamespace(
        total_equity=1_000_000.0, positions=(SimpleNamespace(symbol="SGOV", market_value=300_000.0),)),
        runtime_config=config(income_layer_enabled=False))
    with patch("us_equity_strategies.combo_entrypoints.us_equity_combo_leveraged.compute_signals",
               return_value=({"SPY": 0.6}, "synthetic", False, "ok", {})):
        decision = evaluate_us_equity_combo_leveraged(ctx)
    assert {p.symbol: p.target_weight for p in decision.positions} == pytest.approx({"SPY": 0.3, "SGOV": 0.3})
    assert decision.budgets == ()


def test_infeasible_income_budget_aborts_without_returning_a_zero_target():
    ctx = StrategyContext(as_of="2026-04-06", portfolio=SimpleNamespace(
        total_equity=1_000_000.0, positions=(SimpleNamespace(symbol="SGOV", market_value=700_000.0),)),
        runtime_config=config(income_layer_enabled=False))
    with (
        patch("us_equity_strategies.combo_entrypoints.us_equity_combo_leveraged.compute_signals",
              return_value=({"SPY": 0.6}, "synthetic", False, "ok", {})),
        pytest.raises(IncomeBudgetError, match="INSUFFICIENT"),
    ):
        evaluate_us_equity_combo_leveraged(ctx)
