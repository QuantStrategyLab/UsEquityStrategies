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
    def test_hybrid_growth_income_exposes_execution_metadata(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.hybrid_growth_income import (
            build_rebalance_plan as build_hybrid_plan,
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

        plan = build_hybrid_plan(
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

    def test_semiconductor_rotation_income_exposes_execution_metadata(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.semiconductor_rotation_income import (
            build_rebalance_plan as build_semiconductor_plan,
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

        plan = build_semiconductor_plan(
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
        self.assertEqual(
            plan["portfolio_rows"],
            (("SOXL", "SOXX"), ("QQQI", "SPYI"), ("BOXX",)),
        )


if __name__ == "__main__":
    unittest.main()
