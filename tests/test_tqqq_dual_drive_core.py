from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch


def _input(**overrides):
    from us_equity_strategies.strategies.tqqq_dual_drive_core import DualDriveCoreInput

    values = {
        "qqq_price": 101.0,
        "ma200": 100.0,
        "latest_ma20": 100.0,
        "ma20_slope": 1.0,
        "pullback_rebound": 0.05,
        "pullback_rebound_threshold": 0.02,
        "current_tqqq_quantity": 0.0,
        "current_unlevered_quantity": 0.0,
        "require_ma20_slope": True,
        "allow_pullback": True,
        "strategy_equity": 100_000.0,
        "initial_reserved": 2_000.0,
        "cash_reserve_floor": 0.0,
        "risk_on_cash_reserve_ratio": 0.02,
        "tqqq_weight": 0.45,
        "unlevered_weight": 0.45,
        "macro_active": False,
        "macro_route": None,
        "macro_leverage_scalar": 1.0,
        "macro_risk_asset_scalar": 1.0,
        "crisis_defense_enabled": True,
        "true_crisis_active": False,
        "volatility_enabled": False,
        "volatility_metric": None,
        "volatility_entry_threshold": 0.28,
        "volatility_exit_threshold": 0.24,
        "taco_veto_enabled": True,
        "taco_rebound_context_active": False,
        "retention_mode": None,
        "retention_ratio": 0.0,
    }
    values.update(overrides)
    return DualDriveCoreInput(**values)


class TqqqDualDriveCoreTests(unittest.TestCase):
    def test_decide_applies_macro_then_crisis_then_volatility_retention(self):
        from us_equity_strategies.strategies.tqqq_dual_drive_core import decide_tqqq_dual_drive

        decision = decide_tqqq_dual_drive(
            _input(
                macro_active=True,
                macro_route="delever",
                macro_leverage_scalar=0.5,
                volatility_enabled=True,
                volatility_metric=0.30,
                retention_ratio=0.25,
            )
        )

        self.assertEqual(decision.state, "macro_delever")
        self.assertEqual(decision.target_tqqq_value, 5_625.0)
        self.assertEqual(decision.target_unlevered_value, 84_375.0)
        self.assertEqual(decision.target_boxx_value, 8_000.0)
        self.assertTrue(decision.volatility_entry_triggered)
        self.assertEqual(decision.volatility_trigger_reason, "entry_threshold")

    def test_decide_crisis_defense_prevents_volatility_redirect(self):
        from us_equity_strategies.strategies.tqqq_dual_drive_core import decide_tqqq_dual_drive

        decision = decide_tqqq_dual_drive(
            _input(
                true_crisis_active=True,
                volatility_enabled=True,
                volatility_metric=0.30,
            )
        )

        self.assertEqual(decision.state, "crisis_defense")
        self.assertEqual(decision.target_tqqq_value, 0.0)
        self.assertEqual(decision.target_unlevered_value, 0.0)
        self.assertFalse(decision.volatility_triggered)

    def test_live_plan_delegates_to_the_real_core_once(self):
        from us_equity_strategies.strategies import tqqq_growth_income

        history = [
            {"close": 100.0 + index * 0.5, "high": 101.0 + index * 0.5, "low": 99.0 + index * 0.5}
            for index in range(260)
        ]
        snapshot = SimpleNamespace(
            positions=[SimpleNamespace(symbol="BOXX", market_value=100_000.0, quantity=1_000)],
            total_equity=100_000.0,
            buying_power=2_000.0,
            metadata={"account_hash": "test"},
        )

        with patch.object(
            tqqq_growth_income,
            "decide_tqqq_dual_drive",
            wraps=tqqq_growth_income.decide_tqqq_dual_drive,
        ) as decide:
            plan = tqqq_growth_income.build_rebalance_plan(
                history,
                snapshot,
                signal_text_fn=lambda icon: icon,
                translator=lambda key, **kwargs: key,
                income_threshold_usd=1_000_000_000.0,
                qqqi_income_ratio=0.5,
                cash_reserve_ratio=0.02,
                rebalance_threshold_ratio=0.01,
            )

        decide.assert_called_once()
        self.assertEqual(plan["notification_context"]["signal"]["state"], "entry")
