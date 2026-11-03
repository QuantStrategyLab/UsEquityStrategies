from __future__ import annotations

from us_equity_strategies.income_budget import IncomeBudgetError, allocate_income_budget

import unittest
from dataclasses import replace

from us_equity_strategies.income_layer import (
    build_income_layer_plan,
    get_income_layer_ratio,
    normalize_income_layer_allocations,
    resolve_income_layer_ratio,
)
from us_equity_strategies.income_layer_defaults import INCOME_LAYER_DEFAULT_CONFIGS


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

    def test_total_drawdown_budget_reverses_into_income_layer_ratio(self) -> None:
        ratio = get_income_layer_ratio(
            600000.0,
            income_layer_start_usd=150000.0,
            income_layer_max_ratio=0.65,
            income_layer_ratio_mode="log_total_drawdown_budget",
            income_layer_core_stress_drawdown_ratio=0.40,
            income_layer_income_stress_drawdown_ratio=0.08,
            income_layer_base_drawdown_budget_ratio=0.30,
            income_layer_min_drawdown_budget_ratio=0.15,
            income_layer_drawdown_budget_decay_per_double=0.05,
        )

        self.assertAlmostEqual(ratio, 0.625)

    def test_total_drawdown_budget_reports_stress_diagnostics(self) -> None:
        ratio, diagnostics = resolve_income_layer_ratio(
            600000.0,
            income_layer_start_usd=150000.0,
            income_layer_max_ratio=0.65,
            income_layer_ratio_mode="log_total_drawdown_budget",
            income_layer_core_stress_drawdown_ratio=0.40,
            income_layer_income_stress_drawdown_ratio=0.08,
            income_layer_base_drawdown_budget_ratio=0.30,
            income_layer_min_drawdown_budget_ratio=0.15,
            income_layer_drawdown_budget_decay_per_double=0.05,
        )

        self.assertAlmostEqual(ratio, 0.625)
        self.assertEqual(diagnostics["income_layer_ratio_mode"], "log_total_drawdown_budget")
        self.assertAlmostEqual(diagnostics["income_layer_account_drawdown_budget_ratio"], 0.20)
        self.assertAlmostEqual(diagnostics["income_layer_account_stress_drawdown_ratio"], 0.20)
        self.assertTrue(diagnostics["income_layer_drawdown_budget_met"])

    def test_retired_income_layer_modes_are_rejected(self) -> None:
        for mode in ("linear_cap", "log_cap", "log_loss_budget"):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(ValueError, "Unsupported income layer ratio mode"):
                    get_income_layer_ratio(
                        600000.0,
                        income_layer_start_usd=150000.0,
                        income_layer_max_ratio=0.65,
                        income_layer_ratio_mode=mode,
                    )

    def test_activation_band_smooths_ratio_after_start_threshold(self) -> None:
        normal_ratio = get_income_layer_ratio(
            165000.0,
            income_layer_start_usd=150000.0,
            income_layer_max_ratio=0.65,
            income_layer_ratio_mode="log_total_drawdown_budget",
            income_layer_core_stress_drawdown_ratio=0.40,
            income_layer_income_stress_drawdown_ratio=0.08,
            income_layer_base_drawdown_budget_ratio=0.30,
            income_layer_min_drawdown_budget_ratio=0.15,
            income_layer_drawdown_budget_decay_per_double=0.05,
        )
        softened_ratio, diagnostics = resolve_income_layer_ratio(
            165000.0,
            income_layer_start_usd=150000.0,
            income_layer_max_ratio=0.65,
            income_layer_activation_band_ratio=0.20,
            income_layer_ratio_mode="log_total_drawdown_budget",
            income_layer_core_stress_drawdown_ratio=0.40,
            income_layer_income_stress_drawdown_ratio=0.08,
            income_layer_base_drawdown_budget_ratio=0.30,
            income_layer_min_drawdown_budget_ratio=0.15,
            income_layer_drawdown_budget_decay_per_double=0.05,
        )

        self.assertAlmostEqual(softened_ratio, normal_ratio * 0.5)
        self.assertAlmostEqual(diagnostics["income_layer_activation_band_ratio"], 0.20)
        self.assertAlmostEqual(diagnostics["income_layer_activation_multiplier"], 0.5)
        self.assertEqual(diagnostics["income_layer_activation_end_usd"], 180000.0)

    def test_default_budget_curves_are_monotonic_and_start_smoothly(self) -> None:
        param_keys = (
            "income_layer_start_usd",
            "income_layer_max_ratio",
            "income_layer_activation_band_ratio",
            "income_layer_ratio_mode",
            "income_layer_core_stress_drawdown_ratio",
            "income_layer_income_stress_drawdown_ratio",
            "income_layer_base_drawdown_budget_ratio",
            "income_layer_min_drawdown_budget_ratio",
            "income_layer_drawdown_budget_decay_per_double",
        )
        for profile, config in INCOME_LAYER_DEFAULT_CONFIGS.items():
            with self.subTest(profile=profile):
                params = {key: config[key] for key in param_keys}
                start = float(params["income_layer_start_usd"])
                band = float(params["income_layer_activation_band_ratio"])
                navs = (
                    start,
                    start * 1.001,
                    start * (1.0 + band / 2.0),
                    start * (1.0 + band),
                    start * 2.0,
                    start * 4.0,
                    start * 8.0,
                    start * 16.0,
                )
                ratios = [
                    resolve_income_layer_ratio(nav, income_layer_enabled=True, **params)[0]
                    for nav in navs
                ]

                self.assertEqual(ratios[0], 0.0)
                self.assertLess(ratios[1], 0.0001)
                self.assertLessEqual(max(ratios), float(params["income_layer_max_ratio"]))
                self.assertEqual(ratios, sorted(ratios))


if __name__ == "__main__":
    unittest.main()

class IncomeBudgetTests(unittest.TestCase):
    def test_preserves_cash_and_merges_overlap(self):

        plan = build_income_layer_plan(
            total_equity_usd=100.0, market_values={}, allocations={"SGOV": 1.0},
            income_layer_start_usd=50.0, income_layer_max_ratio=0.5,
            income_layer_core_stress_drawdown_ratio=0.4,
            income_layer_income_stress_drawdown_ratio=0.1,
            income_layer_base_drawdown_budget_ratio=0.37,
            income_layer_min_drawdown_budget_ratio=0.37,
            income_layer_drawdown_budget_decay_per_double=0.0,
        )
        self.assertAlmostEqual(plan.locked_value, 10.0)
        result = allocate_income_budget({"SPY": 0.3, "SGOV": 0.3}, plan, total_equity=100.0)
        self.assertAlmostEqual(result["SPY"], 0.3)
        self.assertAlmostEqual(result["SGOV"], 0.3)
        self.assertAlmostEqual(sum(result.values()), 0.6)

    def test_disabled_preserves_existing_holdings_and_cash(self):

        plan = build_income_layer_plan(
            total_equity_usd=100.0, market_values={"SGOV": 10.0}, allocations={"SGOV": 1.0},
            income_layer_enabled=False, income_layer_start_usd=50.0, income_layer_max_ratio=0.5,
        )
        result = allocate_income_budget({"SPY": 0.6}, plan, total_equity=100.0)
        self.assertAlmostEqual(result["SPY"], 0.5)
        self.assertAlmostEqual(result["SGOV"], 0.1)
        self.assertAlmostEqual(sum(result.values()), 0.6)

    def test_insufficient_core_budget_fails_without_target(self):

        plan = build_income_layer_plan(
            total_equity_usd=100.0, market_values={"SGOV": 70.0}, allocations={"SGOV": 1.0},
            income_layer_enabled=False, income_layer_start_usd=50.0, income_layer_max_ratio=0.5,
        )
        with self.assertRaises(IncomeBudgetError):
            allocate_income_budget({"SPY": 0.6}, plan, total_equity=100.0)

    def test_invalid_inputs_do_not_produce_zero_targets(self):

        plan = build_income_layer_plan(
            total_equity_usd=100.0, market_values={}, allocations={"SGOV": 1.0},
            income_layer_enabled=False, income_layer_start_usd=50.0, income_layer_max_ratio=0.5,
        )
        for weights in ({"SPY": float("nan")}, {"SPY": -0.1}, {"SPY": True}, {"SPY": 1.1}):
            with self.subTest(weights=weights), self.assertRaises(IncomeBudgetError):
                allocate_income_budget(weights, plan, total_equity=100.0)
        with self.assertRaises(IncomeBudgetError):
            allocate_income_budget({"SPY": 0.6}, replace(plan, target_values=None), total_equity=100.0)
        with self.assertRaises(IncomeBudgetError):
            allocate_income_budget({"SPY": 0.6}, plan, total_equity=0.0)

    def test_empty_income_leaves_core_and_cash_unchanged(self):

        plan = build_income_layer_plan(
            total_equity_usd=100.0, market_values={}, allocations={},
            income_layer_enabled=False, income_layer_start_usd=50.0, income_layer_max_ratio=0.5,
        )
        self.assertEqual(allocate_income_budget({"SPY": 0.6}, plan, total_equity=100.0), {"SPY": 0.6})

    def test_disabled_overlap_never_double_counts_existing_income(self):

        plan = build_income_layer_plan(
            total_equity_usd=100.0, market_values={"SGOV": 30.0}, allocations={"SGOV": 1.0},
            income_layer_enabled=False, income_layer_start_usd=50.0, income_layer_max_ratio=0.5,
        )
        result = allocate_income_budget({"SPY": 0.3, "SGOV": 0.3}, plan, total_equity=100.0)
        self.assertAlmostEqual(result["SPY"], 0.3)
        self.assertAlmostEqual(result["SGOV"], 0.3)

    def test_income_excess_funds_another_income_minimum(self):

        plan = build_income_layer_plan(
            total_equity_usd=100.0, market_values={"SGOV": 30.0}, allocations={"SGOV": 1.0},
            income_layer_enabled=False, income_layer_start_usd=50.0, income_layer_max_ratio=0.5,
        )
        plan = replace(plan, target_values={"SGOV": 10.0, "SCHD": 20.0})
        result = allocate_income_budget({"SPY": 0.05, "SGOV": 0.55}, plan, total_equity=100.0)
        self.assertAlmostEqual(result["SPY"], 0.0)
        self.assertAlmostEqual(result["SGOV"], 0.4)
        self.assertAlmostEqual(result["SCHD"], 0.2)
        self.assertAlmostEqual(sum(result.values()), 0.6)
