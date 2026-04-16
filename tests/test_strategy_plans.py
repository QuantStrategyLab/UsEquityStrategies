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
    def test_tqqq_growth_income_exposes_live_dual_drive_metadata(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.tqqq_growth_income import (
            build_rebalance_plan as build_tqqq_plan,
        )

        qqq_history = [
            {"close": 100.0 + index * 0.5, "high": 101.0 + index * 0.5, "low": 99.0 + index * 0.5}
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
            dual_drive_qqq_weight=0.45,
            dual_drive_tqqq_weight=0.45,
            dual_drive_cash_reserve_ratio=0.10,
        )

        self.assertEqual(plan["sell_order_symbols"], ("TQQQ", "QQQ", "SPYI", "QQQI", "BOXX"))
        self.assertEqual(plan["buy_order_symbols"], ("SPYI", "QQQI", "TQQQ", "QQQ"))
        self.assertEqual(plan["portfolio_rows"], (("TQQQ", "QQQ", "BOXX"), ("QQQI", "SPYI")))
        self.assertEqual(plan["allocation_mode"], "fixed_qqq_tqqq_pullback")
        self.assertAlmostEqual(plan["target_values"]["TQQQ"], 150000.0 * 0.45)
        self.assertAlmostEqual(plan["target_values"]["QQQ"], 150000.0 * 0.45)
        self.assertAlmostEqual(plan["reserved"], 150000.0 * 0.10)
        self.assertEqual(plan["target_values"]["BOXX"], 0.0)
        self.assertEqual(plan["exit_line"], plan["ma200"])
        self.assertIn("MA200 Exit:", plan["dashboard"])
        self.assertIn("MA20Δ:", plan["dashboard"])

    def test_tqqq_growth_income_live_dual_drive_uses_stateful_ma200_exit(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.tqqq_growth_income import (
            build_rebalance_plan as build_tqqq_plan,
        )

        qqq_history = [{"close": 100.0, "high": 101.0, "low": 99.0} for _ in range(220)] + [
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

    def test_soxl_soxx_trend_income_exposes_live_tiered_metadata(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.soxl_soxx_trend_income import (
            SOXX_GATE_TIERED_BLEND_MODE,
            build_rebalance_plan as build_soxl_soxx_plan,
        )

        account_state = {
            "available_cash": 5000.0,
            "market_values": {"SOXL": 0.0, "SOXX": 0.0, "BOXX": 100000.0, "QQQI": 0.0, "SPYI": 0.0},
            "quantities": {"SOXL": 0, "SOXX": 0, "BOXX": 1000, "QQQI": 0, "SPYI": 0},
            "sellable_quantities": {"SOXL": 0, "SOXX": 0, "BOXX": 1000, "QQQI": 0, "SPYI": 0},
            "total_strategy_equity": 100000.0,
        }

        plan = build_soxl_soxx_plan(
            {
                "soxl": {"price": 50.0, "ma_trend": 45.0},
                "soxx": {"price": 109.0, "ma_trend": 100.0},
            },
            account_state,
            trend_ma_window=140,
            translator=_translator,
            cash_reserve_ratio=0.03,
            min_trade_ratio=0.01,
            min_trade_floor=100.0,
            rebalance_threshold_ratio=0.01,
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

        self.assertEqual(plan["limit_order_symbols"], ("SOXL", "SOXX", "QQQI", "SPYI"))
        self.assertEqual(plan["allocation_mode"], SOXX_GATE_TIERED_BLEND_MODE)
        self.assertEqual(plan["blend_tier"], "full")
        self.assertEqual(plan["active_risk_asset"], "SOXX+SOXL")
        self.assertEqual(plan["market_status"], "market_status_blend_gate_risk_on(asset=SOXX+SOXL)")
        self.assertAlmostEqual(plan["targets"]["SOXL"], 70000.0)
        self.assertAlmostEqual(plan["targets"]["SOXX"], 20000.0)
        self.assertAlmostEqual(plan["targets"]["BOXX"], 10000.0)

    def test_soxl_soxx_trend_income_tiered_blend_uses_mid_and_defensive_bands(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.soxl_soxx_trend_income import (
            SOXX_GATE_TIERED_BLEND_MODE,
            build_rebalance_plan as build_soxl_soxx_plan,
        )

        account_state = {
            "available_cash": 5000.0,
            "market_values": {"SOXL": 0.0, "SOXX": 0.0, "BOXX": 100000.0, "QQQI": 0.0, "SPYI": 0.0},
            "quantities": {"SOXL": 0, "SOXX": 0, "BOXX": 1000, "QQQI": 0, "SPYI": 0},
            "sellable_quantities": {"SOXL": 0, "SOXX": 0, "BOXX": 1000, "QQQI": 0, "SPYI": 0},
            "total_strategy_equity": 100000.0,
        }
        common_kwargs = dict(
            trend_ma_window=140,
            translator=_translator,
            cash_reserve_ratio=0.03,
            min_trade_ratio=0.01,
            min_trade_floor=100.0,
            rebalance_threshold_ratio=0.01,
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

    def test_live_strategies_reject_retired_allocation_modes(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.soxl_soxx_trend_income import (
            build_rebalance_plan as build_soxl_soxx_plan,
        )
        from us_equity_strategies.strategies.tqqq_growth_income import (
            build_rebalance_plan as build_tqqq_plan,
        )

        with self.assertRaisesRegex(ValueError, "fixed_qqq_tqqq_pullback"):
            build_tqqq_plan(
                [{"close": 100.0, "high": 101.0, "low": 99.0} for _ in range(220)],
                SimpleNamespace(positions=(), total_equity=100000.0, buying_power=1000.0, metadata={"account_hash": "acct"}),
                signal_text_fn=lambda icon: icon,
                translator=_translator,
                income_threshold_usd=1_000_000_000.0,
                qqqi_income_ratio=0.5,
                cash_reserve_ratio=0.02,
                rebalance_threshold_ratio=0.01,
                attack_allocation_mode="atr_staged",
            )

        with self.assertRaisesRegex(ValueError, "soxx_gate_tiered_blend"):
            build_soxl_soxx_plan(
                {
                    "soxl": {"price": 50.0, "ma_trend": 45.0},
                    "soxx": {"price": 100.0, "ma_trend": 100.0},
                },
                {
                    "available_cash": 0.0,
                    "market_values": {"SOXL": 0.0, "SOXX": 0.0, "BOXX": 100000.0, "QQQI": 0.0, "SPYI": 0.0},
                    "quantities": {"SOXL": 0, "SOXX": 0, "BOXX": 1000, "QQQI": 0, "SPYI": 0},
                    "sellable_quantities": {"SOXL": 0, "SOXX": 0, "BOXX": 1000, "QQQI": 0, "SPYI": 0},
                    "total_strategy_equity": 100000.0,
                },
                trend_ma_window=140,
                translator=_translator,
                cash_reserve_ratio=0.03,
                min_trade_ratio=0.01,
                min_trade_floor=100.0,
                rebalance_threshold_ratio=0.01,
                income_layer_start_usd=150000.0,
                income_layer_max_ratio=0.15,
                income_layer_qqqi_weight=0.70,
                income_layer_spyi_weight=0.30,
                attack_allocation_mode="fixed_soxx_soxl_pullback",
            )


if __name__ == "__main__":
    unittest.main()
