from __future__ import annotations

import unittest
from types import SimpleNamespace


def _translator(key: str, **kwargs) -> str:
    if key == "separator":
        return "━━━━━━━━━━━━━━━━━━"
    if kwargs:
        pairs = ", ".join(f"{name}={value}" for name, value in sorted(kwargs.items()))
        return f"{key}({pairs})"
    return key


def _skip_if_missing_numeric_stack() -> None:
    try:
        import numpy  # noqa: F401
        import pandas  # noqa: F401
    except ModuleNotFoundError as exc:
        raise unittest.SkipTest(f"numeric stack unavailable: {exc}") from exc


class StrategyPlanMetadataTest(unittest.TestCase):
    def test_tqqq_growth_income_exposes_execution_metadata(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.tqqq_growth_income import (
            build_rebalance_plan as build_tqqq_plan,
        )

        qqq_history = [
            {
                "close": 100.0 + index * 0.5,
                "high": 101.0 + index * 0.5,
                "low": 99.0 + index * 0.5,
            }
            for index in range(220)
        ]
        snapshot = SimpleNamespace(
            positions=[
                SimpleNamespace(symbol="TQQQ", market_value=5000.0, quantity=10),
                SimpleNamespace(symbol="BOXX", market_value=1000.0, quantity=8),
            ],
            total_equity=150000.0,
            buying_power=20000.0,
            metadata={"account_hash": "acct-1"},
        )

        plan = build_tqqq_plan(
            qqq_history,
            snapshot,
            signal_text_fn=lambda icon: icon,
            translator=_translator,
            income_threshold_usd=100000.0,
            qqqi_income_ratio=0.5,
            cash_reserve_ratio=0.03,
            rebalance_threshold_ratio=0.01,
            alloc_tier1_breakpoints=(0, 50000),
            alloc_tier1_values=(0.1, 0.2),
            alloc_tier2_breakpoints=(50000, 100000),
            alloc_tier2_values=(0.2, 0.3),
            risk_leverage_factor=3.0,
            risk_agg_cap=0.6,
            risk_numerator=0.03,
            atr_exit_scale=1.0,
            atr_entry_scale=1.0,
            exit_line_floor=0.85,
            exit_line_cap=0.99,
            entry_line_floor=1.0,
            entry_line_cap=1.15,
        )

        self.assertEqual(plan["sell_order_symbols"], ("TQQQ", "SPYI", "QQQI", "BOXX"))
        self.assertEqual(plan["buy_order_symbols"], ("SPYI", "QQQI", "TQQQ"))
        self.assertEqual(plan["cash_sweep_symbol"], "BOXX")
        self.assertEqual(plan["portfolio_rows"], (("TQQQ", "BOXX"), ("QQQI", "SPYI")))
        self.assertNotIn("QQQ", plan["strategy_symbols"])

    def test_tqqq_growth_income_can_route_idle_sleeve_to_qqq_when_enabled(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.tqqq_growth_income import (
            build_rebalance_plan as build_tqqq_plan,
        )

        qqq_history = [
            {
                "close": 100.0 + index * 0.5,
                "high": 101.0 + index * 0.5,
                "low": 99.0 + index * 0.5,
            }
            for index in range(220)
        ]
        snapshot = SimpleNamespace(
            positions=[
                SimpleNamespace(symbol="TQQQ", market_value=5000.0, quantity=10),
                SimpleNamespace(symbol="BOXX", market_value=1000.0, quantity=8),
                SimpleNamespace(symbol="QQQ", market_value=0.0, quantity=0),
            ],
            total_equity=150000.0,
            buying_power=20000.0,
            metadata={"account_hash": "acct-1"},
        )

        plan = build_tqqq_plan(
            qqq_history,
            snapshot,
            signal_text_fn=lambda icon: icon,
            translator=_translator,
            income_threshold_usd=100000.0,
            qqqi_income_ratio=0.5,
            cash_reserve_ratio=0.03,
            rebalance_threshold_ratio=0.01,
            alloc_tier1_breakpoints=(0, 50000),
            alloc_tier1_values=(0.1, 0.2),
            alloc_tier2_breakpoints=(50000, 100000),
            alloc_tier2_values=(0.2, 0.3),
            risk_leverage_factor=3.0,
            risk_agg_cap=0.6,
            risk_numerator=0.03,
            atr_exit_scale=1.0,
            atr_entry_scale=1.0,
            exit_line_floor=0.85,
            exit_line_cap=0.99,
            entry_line_floor=1.0,
            entry_line_cap=1.15,
            dual_drive_idle_symbol="QQQ",
            dual_drive_idle_fraction=0.5,
            dual_drive_idle_trigger="tqqq_active",
        )

        self.assertIn("QQQ", plan["strategy_symbols"])
        self.assertEqual(plan["sell_order_symbols"], ("TQQQ", "QQQ", "SPYI", "QQQI", "BOXX"))
        self.assertEqual(plan["buy_order_symbols"], ("SPYI", "QQQI", "TQQQ", "QQQ"))
        self.assertEqual(plan["portfolio_rows"], (("TQQQ", "QQQ", "BOXX"), ("QQQI", "SPYI")))
        self.assertGreater(plan["target_values"]["QQQ"], 0.0)
        self.assertGreater(plan["target_values"]["BOXX"], 0.0)

    def test_tqqq_growth_income_can_trim_attack_size_below_ma20(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.tqqq_growth_income import (
            build_rebalance_plan as build_tqqq_plan,
        )

        qqq_history = [
            {
                "close": 100.0 + index * 0.5,
                "high": 101.0 + index * 0.5,
                "low": 99.0 + index * 0.5,
            }
            for index in range(260)
        ]
        qqq_history[-1] = {"close": 218.0, "high": 220.0, "low": 216.0}
        snapshot = SimpleNamespace(
            positions=[
                SimpleNamespace(symbol="TQQQ", market_value=5000.0, quantity=10),
                SimpleNamespace(symbol="BOXX", market_value=1000.0, quantity=8),
            ],
            total_equity=150000.0,
            buying_power=20000.0,
            metadata={"account_hash": "acct-1"},
        )
        common_params = dict(
            qqq_history=qqq_history,
            snapshot=snapshot,
            signal_text_fn=lambda icon: icon,
            translator=_translator,
            income_threshold_usd=100000.0,
            qqqi_income_ratio=0.5,
            cash_reserve_ratio=0.03,
            rebalance_threshold_ratio=0.01,
            alloc_tier1_breakpoints=(0, 50000),
            alloc_tier1_values=(0.1, 0.2),
            alloc_tier2_breakpoints=(50000, 100000),
            alloc_tier2_values=(0.2, 0.3),
            risk_leverage_factor=3.0,
            risk_agg_cap=0.6,
            risk_numerator=0.03,
            atr_exit_scale=1.0,
            atr_entry_scale=1.0,
            exit_line_floor=0.85,
            exit_line_cap=0.99,
            entry_line_floor=1.0,
            entry_line_cap=1.15,
        )

        baseline_plan = build_tqqq_plan(**common_params)
        scaled_plan = build_tqqq_plan(
            **common_params,
            attack_scale_mode="ma20_gap_trim_only",
            attack_scale_min=0.55,
            attack_scale_gap_limit=0.08,
        )

        self.assertEqual(baseline_plan["attack_scale"], 1.0)
        self.assertLess(scaled_plan["attack_scale"], 1.0)
        self.assertLess(scaled_plan["target_values"]["TQQQ"], baseline_plan["target_values"]["TQQQ"])
        self.assertGreater(scaled_plan["target_values"]["BOXX"], baseline_plan["target_values"]["BOXX"])

    def test_tqqq_growth_income_can_use_fixed_dual_drive_no_income_mode(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.tqqq_growth_income import (
            build_rebalance_plan as build_tqqq_plan,
        )

        qqq_history = [
            {
                "close": 100.0 + index * 0.5,
                "high": 101.0 + index * 0.5,
                "low": 99.0 + index * 0.5,
            }
            for index in range(260)
        ]
        snapshot = SimpleNamespace(
            positions=[SimpleNamespace(symbol="BOXX", market_value=150000.0, quantity=1000)],
            total_equity=150000.0,
            buying_power=20000.0,
            metadata={"account_hash": "acct-1"},
        )

        plan = build_tqqq_plan(
            qqq_history,
            snapshot,
            signal_text_fn=lambda icon: icon,
            translator=_translator,
            income_threshold_usd=1_000_000_000.0,
            qqqi_income_ratio=0.5,
            cash_reserve_ratio=0.03,
            rebalance_threshold_ratio=0.01,
            alloc_tier1_breakpoints=(0, 50000),
            alloc_tier1_values=(0.1, 0.2),
            alloc_tier2_breakpoints=(50000, 100000),
            alloc_tier2_values=(0.2, 0.3),
            risk_leverage_factor=3.0,
            risk_agg_cap=0.6,
            risk_numerator=0.03,
            atr_exit_scale=1.0,
            atr_entry_scale=1.0,
            exit_line_floor=0.85,
            exit_line_cap=0.99,
            entry_line_floor=1.0,
            entry_line_cap=1.15,
            attack_allocation_mode="fixed_qqq_tqqq_pullback",
            dual_drive_qqq_weight=0.45,
            dual_drive_tqqq_weight=0.45,
            dual_drive_cash_reserve_ratio=0.10,
        )

        self.assertIn("QQQ", plan["strategy_symbols"])
        self.assertAlmostEqual(plan["target_values"]["TQQQ"], 150000.0 * 0.45)
        self.assertAlmostEqual(plan["target_values"]["QQQ"], 150000.0 * 0.45)
        self.assertAlmostEqual(plan["reserved"], 150000.0 * 0.10)
        self.assertEqual(plan["target_values"]["BOXX"], 0.0)

    def test_tqqq_growth_income_fixed_dual_drive_uses_stateful_ma200_exit(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.tqqq_growth_income import (
            build_rebalance_plan as build_tqqq_plan,
        )

        qqq_history = [
            {"close": 100.0, "high": 101.0, "low": 99.0}
            for _ in range(220)
        ] + [
            {"close": 170.0 - index, "high": 171.0 - index, "low": 169.0 - index}
            for index in range(40)
        ]
        common_kwargs = dict(
            signal_text_fn=lambda icon: icon,
            translator=_translator,
            income_threshold_usd=1_000_000_000.0,
            qqqi_income_ratio=0.5,
            cash_reserve_ratio=0.02,
            rebalance_threshold_ratio=0.01,
            alloc_tier1_breakpoints=(0, 50000),
            alloc_tier1_values=(0.1, 0.2),
            alloc_tier2_breakpoints=(50000, 100000),
            alloc_tier2_values=(0.2, 0.3),
            risk_leverage_factor=3.0,
            risk_agg_cap=0.6,
            risk_numerator=0.03,
            atr_exit_scale=1.0,
            atr_entry_scale=1.0,
            exit_line_floor=0.85,
            exit_line_cap=0.99,
            entry_line_floor=1.0,
            entry_line_cap=1.15,
            attack_allocation_mode="fixed_qqq_tqqq_pullback",
            dual_drive_qqq_weight=0.45,
            dual_drive_tqqq_weight=0.45,
            dual_drive_cash_reserve_ratio=0.02,
        )

        flat_snapshot = SimpleNamespace(
            positions=[SimpleNamespace(symbol="BOXX", market_value=100000.0, quantity=1000)],
            total_equity=100000.0,
            buying_power=2000.0,
            metadata={"account_hash": "acct-1"},
        )
        flat_plan = build_tqqq_plan(qqq_history, flat_snapshot, **common_kwargs)
        self.assertEqual(flat_plan["target_values"]["TQQQ"], 0.0)
        self.assertEqual(flat_plan["target_values"]["QQQ"], 0.0)
        self.assertAlmostEqual(flat_plan["target_values"]["BOXX"], 100000.0 * 0.98)

        active_snapshot = SimpleNamespace(
            positions=[
                SimpleNamespace(symbol="TQQQ", market_value=45000.0, quantity=100),
                SimpleNamespace(symbol="QQQ", market_value=45000.0, quantity=100),
                SimpleNamespace(symbol="BOXX", market_value=8000.0, quantity=80),
            ],
            total_equity=100000.0,
            buying_power=2000.0,
            metadata={"account_hash": "acct-1"},
        )
        active_plan = build_tqqq_plan(qqq_history, active_snapshot, **common_kwargs)
        self.assertAlmostEqual(active_plan["target_values"]["TQQQ"], 100000.0 * 0.45)
        self.assertAlmostEqual(active_plan["target_values"]["QQQ"], 100000.0 * 0.45)
        self.assertAlmostEqual(active_plan["target_values"]["BOXX"], 100000.0 * 0.08)
        self.assertAlmostEqual(active_plan["reserved"], 100000.0 * 0.02)

    def test_soxl_soxx_trend_income_exposes_execution_metadata(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.soxl_soxx_trend_income import (
            build_rebalance_plan as build_soxl_soxx_plan,
        )

        indicators = {
            "soxl": {
                "price": 50.0,
                "ma_trend": 45.0,
            }
        }
        account_state = {
            "available_cash": 5000.0,
            "market_values": {
                "SOXL": 10000.0,
                "SOXX": 0.0,
                "BOXX": 8000.0,
                "QQQI": 2000.0,
                "SPYI": 1000.0,
            },
            "quantities": {
                "SOXL": 100,
                "SOXX": 0,
                "BOXX": 80,
                "QQQI": 20,
                "SPYI": 10,
            },
            "sellable_quantities": {
                "SOXL": 100,
                "SOXX": 0,
                "BOXX": 80,
                "QQQI": 20,
                "SPYI": 10,
            },
            "total_strategy_equity": 26000.0,
        }

        plan = build_soxl_soxx_plan(
            indicators,
            account_state,
            trend_ma_window=150,
            translator=_translator,
            cash_reserve_ratio=0.03,
            min_trade_ratio=0.01,
            min_trade_floor=100.0,
            rebalance_threshold_ratio=0.01,
            small_account_deploy_ratio=0.60,
            mid_account_deploy_ratio=0.57,
            large_account_deploy_ratio=0.50,
            trade_layer_decay_coeff=0.04,
            income_layer_start_usd=150000.0,
            income_layer_max_ratio=0.15,
            income_layer_qqqi_weight=0.70,
            income_layer_spyi_weight=0.30,
        )

        self.assertEqual(plan["limit_order_symbols"], ("SOXL", "SOXX", "QQQI", "SPYI"))
        self.assertEqual(plan["market_status"], "market_status_risk_on(asset=SOXL)")
        self.assertEqual(plan["signal_message"], "signal_risk_on(ratio=59.4%, window=150)")
        self.assertEqual(
            plan["portfolio_rows"],
            (("SOXL", "SOXX"), ("QQQI", "SPYI"), ("BOXX",)),
        )


if __name__ == "__main__":
    unittest.main()
