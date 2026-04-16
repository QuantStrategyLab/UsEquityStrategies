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
        self.assertEqual(plan["exit_line"], plan["ma200"])
        self.assertIn("MA200 Exit:", plan["dashboard"])
        self.assertIn("MA20Δ:", plan["dashboard"])
        self.assertNotIn(" | Exit:", plan["dashboard"])

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

    def test_soxl_soxx_trend_income_trend_switch_uses_hysteresis_band(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.soxl_soxx_trend_income import (
            build_rebalance_plan as build_soxl_soxx_plan,
        )

        def build_plan(price, *, soxl_quantity=0, soxl_value=0.0):
            return build_soxl_soxx_plan(
                {
                    "soxl": {
                        "price": price,
                        "ma_trend": 100.0,
                    }
                },
                {
                    "available_cash": 0.0,
                    "market_values": {
                        "SOXL": soxl_value,
                        "SOXX": 0.0,
                        "BOXX": 10000.0 - soxl_value,
                        "QQQI": 0.0,
                        "SPYI": 0.0,
                    },
                    "quantities": {
                        "SOXL": soxl_quantity,
                        "SOXX": 0,
                        "BOXX": 100,
                        "QQQI": 0,
                        "SPYI": 0,
                    },
                    "sellable_quantities": {
                        "SOXL": soxl_quantity,
                        "SOXX": 0,
                        "BOXX": 100,
                        "QQQI": 0,
                        "SPYI": 0,
                    },
                    "total_strategy_equity": 10000.0,
                },
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
                trend_entry_buffer=0.03,
                trend_exit_buffer=0.03,
            )

        wait_plan = build_plan(102.0)
        self.assertEqual(wait_plan["active_risk_asset"], "SOXX")
        self.assertEqual(wait_plan["targets"]["SOXL"], 0.0)
        self.assertAlmostEqual(wait_plan["targets"]["SOXX"], 6000.0)
        self.assertAlmostEqual(wait_plan["soxl_entry_line"], 103.0)
        self.assertAlmostEqual(wait_plan["soxl_exit_line"], 97.0)

        hold_plan = build_plan(98.0, soxl_quantity=100, soxl_value=6000.0)
        self.assertEqual(hold_plan["active_risk_asset"], "SOXL")
        self.assertAlmostEqual(hold_plan["targets"]["SOXL"], 6000.0)

        exit_plan = build_plan(96.5, soxl_quantity=100, soxl_value=6000.0)
        self.assertEqual(exit_plan["active_risk_asset"], "SOXX")
        self.assertAlmostEqual(exit_plan["targets"]["SOXX"], 6000.0)

    def test_soxl_soxx_trend_income_can_use_fixed_dual_drive_mode(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.soxl_soxx_trend_income import (
            FIXED_DUAL_DRIVE_MODE,
            build_rebalance_plan as build_soxl_soxx_plan,
        )

        indicators = {
            "soxl": {
                "price": 50.0,
                "ma_trend": 45.0,
            },
            "soxx": {
                "price": 110.0,
                "ma_trend": 100.0,
                "ma20": 105.0,
                "ma20_slope": 0.5,
            },
        }
        account_state = {
            "available_cash": 5000.0,
            "market_values": {
                "SOXL": 0.0,
                "SOXX": 0.0,
                "BOXX": 100000.0,
                "QQQI": 0.0,
                "SPYI": 0.0,
            },
            "quantities": {
                "SOXL": 0,
                "SOXX": 0,
                "BOXX": 1000,
                "QQQI": 0,
                "SPYI": 0,
            },
            "sellable_quantities": {
                "SOXL": 0,
                "SOXX": 0,
                "BOXX": 1000,
                "QQQI": 0,
                "SPYI": 0,
            },
            "total_strategy_equity": 100000.0,
        }

        plan = build_soxl_soxx_plan(
            indicators,
            account_state,
            trend_ma_window=200,
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
            attack_allocation_mode=FIXED_DUAL_DRIVE_MODE,
            dual_drive_soxx_weight=0.45,
            dual_drive_soxl_weight=0.45,
            dual_drive_allow_pullback=True,
            dual_drive_require_ma20_slope=True,
            dual_drive_trend_source="SOXX",
        )

        self.assertEqual(plan["active_risk_asset"], "SOXX+SOXL")
        self.assertAlmostEqual(plan["targets"]["SOXX"], 45000.0)
        self.assertAlmostEqual(plan["targets"]["SOXL"], 45000.0)
        self.assertAlmostEqual(plan["targets"]["BOXX"], 10000.0)
        self.assertEqual(plan["trend_symbol"], "SOXX")
        self.assertEqual(plan["allocation_mode"], FIXED_DUAL_DRIVE_MODE)

    def test_soxl_soxx_trend_income_can_use_soxx_gate_blend_mode(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.soxl_soxx_trend_income import (
            SOXX_GATE_BLEND_MODE,
            build_rebalance_plan as build_soxl_soxx_plan,
        )

        account_state = {
            "available_cash": 5000.0,
            "market_values": {
                "SOXL": 0.0,
                "SOXX": 0.0,
                "BOXX": 100000.0,
                "QQQI": 0.0,
                "SPYI": 0.0,
            },
            "quantities": {
                "SOXL": 0,
                "SOXX": 0,
                "BOXX": 1000,
                "QQQI": 0,
                "SPYI": 0,
            },
            "sellable_quantities": {
                "SOXL": 0,
                "SOXX": 0,
                "BOXX": 1000,
                "QQQI": 0,
                "SPYI": 0,
            },
            "total_strategy_equity": 100000.0,
        }
        common_kwargs = dict(
            trend_ma_window=140,
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
            attack_allocation_mode=SOXX_GATE_BLEND_MODE,
            blend_gate_trend_source="SOXX",
            trend_entry_buffer=0.10,
            trend_exit_buffer=0.02,
            blend_gate_soxl_weight=0.80,
            blend_gate_active_soxx_weight=0.19,
            blend_gate_defensive_soxx_weight=0.17,
        )

        active_plan = build_soxl_soxx_plan(
            {
                "soxl": {"price": 50.0, "ma_trend": 45.0},
                "soxx": {"price": 111.0, "ma_trend": 100.0},
            },
            account_state,
            **common_kwargs,
        )

        self.assertEqual(active_plan["active_risk_asset"], "SOXX+SOXL")
        self.assertAlmostEqual(active_plan["targets"]["SOXL"], 80000.0)
        self.assertAlmostEqual(active_plan["targets"]["SOXX"], 19000.0)
        self.assertAlmostEqual(active_plan["targets"]["BOXX"], 1000.0)
        self.assertEqual(active_plan["allocation_mode"], SOXX_GATE_BLEND_MODE)
        self.assertEqual(active_plan["trend_symbol"], "SOXX")
        self.assertAlmostEqual(active_plan["trend_entry_line"], 110.0)
        self.assertAlmostEqual(active_plan["trend_exit_line"], 98.0)

        defensive_plan = build_soxl_soxx_plan(
            {
                "soxl": {"price": 50.0, "ma_trend": 45.0},
                "soxx": {"price": 109.0, "ma_trend": 100.0},
            },
            account_state,
            **common_kwargs,
        )

        self.assertEqual(defensive_plan["active_risk_asset"], "SOXX")
        self.assertEqual(defensive_plan["targets"]["SOXL"], 0.0)
        self.assertAlmostEqual(defensive_plan["targets"]["SOXX"], 17000.0)
        self.assertAlmostEqual(defensive_plan["targets"]["BOXX"], 83000.0)

    def test_soxl_soxx_trend_income_can_use_tiered_soxx_gate_blend_mode(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.soxl_soxx_trend_income import (
            SOXX_GATE_TIERED_BLEND_MODE,
            build_rebalance_plan as build_soxl_soxx_plan,
        )

        account_state = {
            "available_cash": 5000.0,
            "market_values": {
                "SOXL": 0.0,
                "SOXX": 0.0,
                "BOXX": 100000.0,
                "QQQI": 0.0,
                "SPYI": 0.0,
            },
            "quantities": {
                "SOXL": 0,
                "SOXX": 0,
                "BOXX": 1000,
                "QQQI": 0,
                "SPYI": 0,
            },
            "sellable_quantities": {
                "SOXL": 0,
                "SOXX": 0,
                "BOXX": 1000,
                "QQQI": 0,
                "SPYI": 0,
            },
            "total_strategy_equity": 100000.0,
        }
        common_kwargs = dict(
            trend_ma_window=140,
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
            attack_allocation_mode=SOXX_GATE_TIERED_BLEND_MODE,
            blend_gate_trend_source="SOXX",
            trend_entry_buffer=0.08,
            trend_mid_buffer=0.06,
            trend_exit_buffer=0.02,
            blend_gate_soxl_weight=0.70,
            blend_gate_mid_soxl_weight=0.65,
            blend_gate_active_soxx_weight=0.20,
            blend_gate_defensive_soxx_weight=0.15,
        )

        full_plan = build_soxl_soxx_plan(
            {
                "soxl": {"price": 50.0, "ma_trend": 45.0},
                "soxx": {"price": 109.0, "ma_trend": 100.0},
            },
            account_state,
            **common_kwargs,
        )

        self.assertEqual(full_plan["blend_tier"], "full")
        self.assertAlmostEqual(full_plan["targets"]["SOXL"], 70000.0)
        self.assertAlmostEqual(full_plan["targets"]["SOXX"], 20000.0)
        self.assertAlmostEqual(full_plan["targets"]["BOXX"], 10000.0)
        self.assertAlmostEqual(full_plan["trend_entry_line"], 108.0)
        self.assertAlmostEqual(full_plan["trend_mid_line"], 106.0)
        self.assertAlmostEqual(full_plan["trend_exit_line"], 98.0)

        mid_plan = build_soxl_soxx_plan(
            {
                "soxl": {"price": 50.0, "ma_trend": 45.0},
                "soxx": {"price": 107.0, "ma_trend": 100.0},
            },
            account_state,
            **common_kwargs,
        )

        self.assertEqual(mid_plan["blend_tier"], "mid")
        self.assertAlmostEqual(mid_plan["targets"]["SOXL"], 65000.0)
        self.assertAlmostEqual(mid_plan["targets"]["SOXX"], 20000.0)
        self.assertAlmostEqual(mid_plan["targets"]["BOXX"], 15000.0)

        defensive_plan = build_soxl_soxx_plan(
            {
                "soxl": {"price": 50.0, "ma_trend": 45.0},
                "soxx": {"price": 105.0, "ma_trend": 100.0},
            },
            account_state,
            **common_kwargs,
        )

        self.assertEqual(defensive_plan["blend_tier"], "defensive")
        self.assertEqual(defensive_plan["targets"]["SOXL"], 0.0)
        self.assertAlmostEqual(defensive_plan["targets"]["SOXX"], 15000.0)
        self.assertAlmostEqual(defensive_plan["targets"]["BOXX"], 85000.0)

        held_account_state = {
            **account_state,
            "market_values": {**account_state["market_values"], "SOXL": 65000.0, "BOXX": 35000.0},
            "quantities": {**account_state["quantities"], "SOXL": 100},
            "sellable_quantities": {**account_state["sellable_quantities"], "SOXL": 100},
        }
        held_mid_plan = build_soxl_soxx_plan(
            {
                "soxl": {"price": 50.0, "ma_trend": 45.0},
                "soxx": {"price": 99.0, "ma_trend": 100.0},
            },
            held_account_state,
            **common_kwargs,
        )

        self.assertEqual(held_mid_plan["blend_tier"], "mid")
        self.assertAlmostEqual(held_mid_plan["targets"]["SOXL"], 65000.0)

    def test_soxl_soxx_trend_income_fixed_dual_drive_parks_when_gate_is_off(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.soxl_soxx_trend_income import (
            FIXED_DUAL_DRIVE_MODE,
            build_rebalance_plan as build_soxl_soxx_plan,
        )

        indicators = {
            "soxl": {"price": 40.0, "ma_trend": 45.0},
            "soxx": {
                "price": 90.0,
                "ma_trend": 100.0,
                "ma20": 95.0,
                "ma20_slope": 0.5,
            },
        }
        account_state = {
            "available_cash": 5000.0,
            "market_values": {"SOXL": 0.0, "SOXX": 0.0, "BOXX": 100000.0, "QQQI": 0.0, "SPYI": 0.0},
            "quantities": {"SOXL": 0, "SOXX": 0, "BOXX": 1000, "QQQI": 0, "SPYI": 0},
            "sellable_quantities": {"SOXL": 0, "SOXX": 0, "BOXX": 1000, "QQQI": 0, "SPYI": 0},
            "total_strategy_equity": 100000.0,
        }

        plan = build_soxl_soxx_plan(
            indicators,
            account_state,
            trend_ma_window=200,
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
            attack_allocation_mode=FIXED_DUAL_DRIVE_MODE,
            dual_drive_soxx_weight=0.45,
            dual_drive_soxl_weight=0.45,
            dual_drive_allow_pullback=True,
            dual_drive_require_ma20_slope=True,
            dual_drive_trend_source="SOXX",
        )

        self.assertEqual(plan["active_risk_asset"], "BOXX")
        self.assertEqual(plan["targets"]["SOXX"], 0.0)
        self.assertEqual(plan["targets"]["SOXL"], 0.0)
        self.assertAlmostEqual(plan["targets"]["BOXX"], 100000.0)


if __name__ == "__main__":
    unittest.main()
