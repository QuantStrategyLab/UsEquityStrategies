from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch


def _input(**overrides):
    if "prior_volatility_latch" in overrides or "prior_alpha_active" in overrides:
        from us_equity_strategies.strategies.tqqq_dual_drive_live_v2 import DualDriveCoreInput
    else:
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
    def test_decide_preserves_each_explicit_zero_weight(self):
        from us_equity_strategies.strategies.tqqq_dual_drive_core import decide_tqqq_dual_drive

        for tqqq_weight, unlevered_weight, tqqq_value, unlevered_value in (
            (0.0, 0, 0.0, 0.0),
            (0, 0.45, 0.0, 45_000.0),
            (0.45, 0.0, 45_000.0, 0.0),
        ):
            with self.subTest(tqqq_weight=tqqq_weight, unlevered_weight=unlevered_weight):
                decision = decide_tqqq_dual_drive(
                    _input(tqqq_weight=tqqq_weight, unlevered_weight=unlevered_weight)
                )

                self.assertEqual(decision.target_tqqq_value, tqqq_value)
                self.assertEqual(decision.target_unlevered_value, unlevered_value)
                self.assertEqual(decision.target_boxx_value, 98_000.0 - tqqq_value - unlevered_value)
                self.assertEqual(decision.reserved, 2_000.0)

    def test_decide_none_weights_keep_original_defaults(self):
        from us_equity_strategies.strategies.tqqq_dual_drive_core import decide_tqqq_dual_drive

        for tqqq_weight, unlevered_weight, tqqq_value, unlevered_value in (
            (None, None, 45_000.0, 45_000.0),
            (None, 0.0, 45_000.0, 0.0),
            (0.0, None, 0.0, 45_000.0),
        ):
            with self.subTest(tqqq_weight=tqqq_weight, unlevered_weight=unlevered_weight):
                decision = decide_tqqq_dual_drive(
                    _input(tqqq_weight=tqqq_weight, unlevered_weight=unlevered_weight)
                )

                self.assertEqual(decision.target_tqqq_value, tqqq_value)
                self.assertEqual(decision.target_unlevered_value, unlevered_value)

    def test_planner_preserves_zero_weights_and_missing_defaults(self):
        from us_equity_strategies.strategies.tqqq_growth_income import build_rebalance_plan

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
        for weights, tqqq_value, unlevered_value in (
            ({"dual_drive_tqqq_weight": 0.0, "dual_drive_qqq_weight": 0}, 0.0, 0.0),
            ({"dual_drive_tqqq_weight": 0}, 0.0, 45_000.0),
            ({"dual_drive_qqq_weight": 0.0}, 45_000.0, 0.0),
            ({"dual_drive_tqqq_weight": None, "dual_drive_qqq_weight": None}, 45_000.0, 45_000.0),
            ({}, 45_000.0, 45_000.0),
        ):
            with self.subTest(weights=weights):
                plan = build_rebalance_plan(
                    history,
                    snapshot,
                    signal_text_fn=lambda icon: icon,
                    translator=lambda key, **kwargs: key,
                    income_threshold_usd=1_000_000_000.0,
                    qqqi_income_ratio=0.5,
                    cash_reserve_ratio=0.02,
                    rebalance_threshold_ratio=0.01,
                    **weights,
                )

                self.assertEqual(plan["target_values"]["TQQQ"], tqqq_value)
                self.assertEqual(plan["target_values"]["QQQM"], unlevered_value)
                self.assertEqual(
                    plan["target_values"]["BOXX"], 98_000.0 - tqqq_value - unlevered_value
                )
                self.assertEqual(plan["reserved"], 2_000.0)

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


def test_explicit_volatility_latch_survives_partial_retention_and_absent_holdings():
    from us_equity_strategies.strategies.tqqq_dual_drive_live_v2 import decide_tqqq_dual_drive

    prior = False
    for metric, expected in ((0.30, True), (0.25, True), (0.19, False)):
        decision = decide_tqqq_dual_drive(_input(
            volatility_enabled=True, volatility_metric=metric,
            volatility_entry_threshold=0.28, volatility_exit_threshold=0.20,
            retention_mode="fixed", retention_ratio=0.5,
            current_tqqq_quantity=20, current_unlevered_quantity=30,
            prior_volatility_latch=prior,
        ))
        assert decision.volatility_latch_active is expected
        assert decision.volatility_applied is expected
        prior = decision.volatility_latch_active
    decision = decide_tqqq_dual_drive(_input(
        volatility_enabled=True, volatility_metric=0.25, volatility_entry_threshold=0.28,
        volatility_exit_threshold=0.20, prior_volatility_latch=True,
        current_tqqq_quantity=0, current_unlevered_quantity=0,
    ))
    assert decision.volatility_latch_active is True


def test_partial_retention_with_distinct_exit_requires_state_for_restoration():
    from unittest.mock import patch
    from us_equity_strategies.strategies.tqqq_growth_income_live_v2 import build_rebalance_plan
    history = [{"close": 100.0 + i * 0.5} for i in range(260)]
    snapshot = SimpleNamespace(positions=[SimpleNamespace(symbol="TQQQ", market_value=22000.0, quantity=20),
        SimpleNamespace(symbol="QQQM", market_value=50000.0, quantity=30)],
        total_equity=100000.0, buying_power=0.0, metadata={})
    with patch("us_equity_strategies.strategies.tqqq_growth_income_live_v2._resolve_volatility_delever_thresholds") as resolve:
        resolve.return_value = {"mode": "fixed", "entry_threshold": .28, "exit_threshold": .20,
            "dynamic_threshold": None, "dynamic_sample_count": 0, "dynamic_lookback": 252,
            "dynamic_percentile": .9, "dynamic_min_periods": 126, "dynamic_floor": .24,
            "dynamic_cap": .36, "metric": .25}
        plan = build_rebalance_plan(history, snapshot, signal_text_fn=str, translator=lambda key, **kwargs: key,
            cash_reserve_ratio=.02, rebalance_threshold_ratio=.01, income_layer_enabled=False,
            dual_drive_volatility_delever_retention_mode="fixed", dual_drive_volatility_delever_retention_ratio=.5)
        assert plan["daily_state_continuity_required"] is True
        assert plan["target_values"]["TQQQ"] <= 22000.0
        backed = build_rebalance_plan(history, snapshot, signal_text_fn=str, translator=lambda key, **kwargs: key,
            cash_reserve_ratio=.02, rebalance_threshold_ratio=.01, income_layer_enabled=False,
            dual_drive_volatility_delever_retention_mode="fixed", dual_drive_volatility_delever_retention_ratio=.5,
            daily_state_prior={"alpha_trend_active": True, "volatility_latch_active": True})
        assert backed["daily_volatility_latch_active"] is True
        assert backed["target_values"]["TQQQ"] == 22500.0
