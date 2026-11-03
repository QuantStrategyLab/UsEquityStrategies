"""One income budget preserves implicit cash and counts overlapping targets once."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from quant_platform_kit.common.strategy_contracts import StrategyContext

from us_equity_strategies.combo_entrypoints import evaluate_us_equity_combo_leveraged
from us_equity_strategies.combo_plugin_pipeline import apply_income_layer
from us_equity_strategies.income_budget import IncomeBudgetError


def _income_config(**allocations: float) -> dict[str, object]:
    return {
        "income_layer_enabled": True,
        "income_layer_start_usd": 500_000.0,
        "income_layer_max_ratio": 0.25,
        "income_layer_allocations": dict(allocations),
    }


class ComboIncomeOverlapTests(unittest.TestCase):
    def test_overlap_merges_core_and_income_weights(self) -> None:
        weights = {"SGOV": 0.5, "SPY": 0.5}
        diagnostics = apply_income_layer(
            weights,
            1_000_000.0,
            _income_config(SGOV=1.0),
        )
        ratio = float(diagnostics["income_layer_ratio"])
        self.assertGreater(ratio, 0.0)
        self.assertAlmostEqual(weights["SPY"], 0.5)
        self.assertAlmostEqual(weights["SGOV"], 0.5)
        self.assertAlmostEqual(sum(weights.values()), 1.0)

    def test_no_overlap_keeps_scaled_core_plus_income(self) -> None:
        weights = {"SPY": 0.6, "QQQ": 0.4}
        diagnostics = apply_income_layer(
            weights,
            1_000_000.0,
            _income_config(SGOV=1.0),
        )
        ratio = float(diagnostics["income_layer_ratio"])
        self.assertAlmostEqual(weights["SPY"], 0.6 * (1.0 - ratio))
        self.assertAlmostEqual(weights["QQQ"], 0.4 * (1.0 - ratio))
        self.assertAlmostEqual(weights["SGOV"], ratio)
        self.assertAlmostEqual(sum(weights.values()), 1.0)

    def test_all_cash_core_does_not_silently_spend_cash(self) -> None:
        weights: dict[str, float] = {}
        with self.assertRaisesRegex(IncomeBudgetError, "INSUFFICIENT"):
            apply_income_layer(weights, 1_000_000.0, _income_config(SGOV=0.5, SCHD=0.5))
        self.assertEqual(weights, {})

    def test_empty_core_with_disabled_income_unchanged(self) -> None:
        weights = {"SPY": 1.0}
        diagnostics = apply_income_layer(
            weights,
            1_000_000.0,
            {
                "income_layer_enabled": False,
                "income_layer_start_usd": 500_000.0,
                "income_layer_max_ratio": 0.25,
                "income_layer_allocations": {"SGOV": 1.0},
            },
        )
        self.assertFalse(diagnostics["income_layer_active"])
        self.assertEqual(weights, {"SPY": 1.0})

    def test_public_leveraged_entrypoint_merges_overlap_targets(self) -> None:
        ctx = StrategyContext(
            as_of="2026-04-06",
            portfolio=SimpleNamespace(total_equity=1_000_000.0, positions=()),
            market_data={"market_data": {"spy_above_ma200": True}},
            runtime_config=_income_config(SGOV=1.0),
        )
        with patch(
            "us_equity_strategies.combo_entrypoints.us_equity_combo_leveraged.compute_signals",
            return_value=(
                {"SGOV": 0.5, "SPY": 0.5},
                "synthetic",
                False,
                "ok",
                {},
            ),
        ):
            decision = evaluate_us_equity_combo_leveraged(ctx)

        by_symbol = {p.symbol: float(p.target_weight) for p in decision.positions}
        ratio = float(decision.diagnostics["income_layer_diagnostics"]["income_layer_ratio"])
        self.assertGreater(ratio, 0.0)
        self.assertAlmostEqual(by_symbol["SPY"], 0.5)
        self.assertAlmostEqual(by_symbol["SGOV"], 0.5)
        self.assertAlmostEqual(sum(by_symbol.values()), 1.0)


if __name__ == "__main__":
    unittest.main()
