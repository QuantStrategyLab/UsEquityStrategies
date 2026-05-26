from __future__ import annotations

import unittest

from us_equity_strategies.income_layer import (
    build_income_layer_plan,
    get_income_layer_ratio,
    normalize_income_layer_allocations,
    resolve_income_layer_ratio,
)


class IncomeLayerTest(unittest.TestCase):
    def test_normalizes_allocations_and_excludes_core_symbols(self) -> None:
        allocations = normalize_income_layer_allocations(
            {
                "SCHD": 2.0,
                "DGRO": 1.0,
                "TQQQ": 1.0,
                "bad": 0.0,
                "BOXX": 5.0,
            },
            fallback_allocations=(("SPYI", 0.6), ("QQQI", 0.4)),
            excluded_symbols=("TQQQ", "BOXX"),
        )

        self.assertEqual(tuple(allocations), ("SCHD", "DGRO"))
        self.assertAlmostEqual(allocations["SCHD"], 2.0 / 3.0)
        self.assertAlmostEqual(allocations["DGRO"], 1.0 / 3.0)

    def test_disabled_layer_locks_existing_income_value_without_new_target(self) -> None:
        plan = build_income_layer_plan(
            total_equity_usd=300000.0,
            market_values={"SCHD": 12000.0, "DGRO": 8000.0},
            allocations={"SCHD": 0.6, "DGRO": 0.4},
            income_layer_enabled=False,
            income_layer_start_usd=150000.0,
            income_layer_max_ratio=0.50,
        )

        self.assertEqual(plan.ratio, 0.0)
        self.assertEqual(plan.current_value, 20000.0)
        self.assertEqual(plan.locked_value, 20000.0)
        self.assertEqual(plan.add_value, 0.0)
        self.assertEqual(plan.target_values, {"SCHD": 12000.0, "DGRO": 8000.0})
        self.assertIs(plan.diagnostics["income_layer_enabled"], False)

    def test_log_loss_budget_caps_income_layer_ratio(self) -> None:
        ratio = get_income_layer_ratio(
            600000.0,
            income_layer_start_usd=150000.0,
            income_layer_max_ratio=0.50,
            income_layer_ratio_mode="log_loss_budget",
            income_layer_log_growth_factor=0.70,
            income_layer_stress_drawdown_ratio=0.30,
            income_layer_base_loss_budget_ratio=0.08,
            income_layer_min_loss_budget_ratio=0.06,
            income_layer_loss_budget_decay_per_double=0.01,
        )

        self.assertAlmostEqual(ratio, 0.20)

    def test_log_cap_grows_without_loss_budget_cap(self) -> None:
        ratio, diagnostics = resolve_income_layer_ratio(
            600000.0,
            income_layer_start_usd=150000.0,
            income_layer_max_ratio=0.50,
            income_layer_ratio_mode="log_cap",
            income_layer_log_growth_factor=0.70,
            income_layer_stress_drawdown_ratio=0.30,
            income_layer_base_loss_budget_ratio=0.08,
            income_layer_min_loss_budget_ratio=0.06,
            income_layer_loss_budget_decay_per_double=0.01,
        )

        self.assertGreater(ratio, 0.35)
        self.assertLess(ratio, 0.50)
        self.assertEqual(diagnostics["income_layer_ratio_mode"], "log_cap")
        self.assertEqual(diagnostics["income_layer_loss_budget_cap_ratio"], 0.50)

    def test_activation_band_smooths_ratio_after_start_threshold(self) -> None:
        normal_ratio = get_income_layer_ratio(
            165000.0,
            income_layer_start_usd=150000.0,
            income_layer_max_ratio=0.50,
            income_layer_ratio_mode="log_cap",
            income_layer_log_growth_factor=0.70,
        )
        softened_ratio, diagnostics = resolve_income_layer_ratio(
            165000.0,
            income_layer_start_usd=150000.0,
            income_layer_max_ratio=0.50,
            income_layer_activation_band_ratio=0.20,
            income_layer_ratio_mode="log_cap",
            income_layer_log_growth_factor=0.70,
        )

        self.assertAlmostEqual(softened_ratio, normal_ratio * 0.5)
        self.assertAlmostEqual(diagnostics["income_layer_activation_band_ratio"], 0.20)
        self.assertAlmostEqual(diagnostics["income_layer_activation_multiplier"], 0.5)
        self.assertEqual(diagnostics["income_layer_activation_end_usd"], 180000.0)


if __name__ == "__main__":
    unittest.main()
