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


def _market_regime_authorization(*, approved: bool = True) -> dict[str, object]:
    evidence_status = "automation_approved" if approved else "notification_only"
    return {
        "execution_controls": {
            "position_control_allowed": approved,
            "consumption_evidence_status": evidence_status,
        },
        "consumption_policy": {
            "position_control_allowed": approved,
            "evidence_status": evidence_status,
        },
    }


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

        self.assertEqual(plan["sell_order_symbols"], ("TQQQ", "QQQM", "SPYI", "QQQI", "BOXX"))
        self.assertEqual(plan["buy_order_symbols"], ("SPYI", "QQQI", "TQQQ", "QQQM"))
        self.assertEqual(plan["portfolio_rows"], (("TQQQ", "QQQM", "BOXX"), ("SPYI", "QQQI")))
        self.assertEqual(plan["account_hash"], "acct-1")
        self.assertEqual(plan["allocation_mode"], "fixed_qqq_tqqq_pullback")
        self.assertAlmostEqual(plan["target_values"]["TQQQ"], 150000.0 * 0.45)
        self.assertAlmostEqual(plan["target_values"]["QQQM"], 150000.0 * 0.45)
        self.assertAlmostEqual(plan["reserved"], 150000.0 * 0.10)
        self.assertEqual(plan["real_buying_power"], 20000.0)
        self.assertEqual(plan["investable_buying_power"], 5000.0)
        self.assertEqual(plan["target_values"]["BOXX"], 0.0)
        self.assertEqual(plan["exit_line"], plan["ma200"])
        self.assertIn("QQQM: $", plan["dashboard"])
        self.assertIn("buying_power: $20,000.00", plan["dashboard"])
        self.assertIn("Reserved Cash: $15,000.00", plan["dashboard"])
        self.assertIn("Investable Cash: $5,000.00", plan["dashboard"])
        self.assertIn("MA200 Exit:", plan["dashboard"])
        self.assertIn("MA20Δ:", plan["dashboard"])
        self.assertEqual(plan["notification_context"]["signal"]["state"], "entry")
        self.assertIn(
            plan["notification_context"]["signal"]["reason_code"],
            {"tqqq_signal_reason_entry_trend", "tqqq_signal_reason_entry_pullback"},
        )
        self.assertIn("reason:", plan["notification_context"]["signal"]["reason"])
        self.assertIn(" | reason:", plan["sig_display"])
        self.assertEqual(plan["notification_context"]["benchmark"]["symbol"], "QQQ")
        self.assertEqual(
            plan["notification_context"]["portfolio"]["holdings_order"],
            ("TQQQ", "QQQM", "BOXX", "SPYI", "QQQI"),
        )
        self.assertEqual(plan["notification_context"]["portfolio"]["raw_buying_power"], 20000.0)
        self.assertEqual(plan["notification_context"]["portfolio"]["reserved_cash"], 15000.0)
        self.assertEqual(plan["notification_context"]["portfolio"]["investable_cash"], 5000.0)

    def test_tqqq_growth_income_normalizes_close_column_case(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.tqqq_growth_income import (
            build_rebalance_plan as build_tqqq_plan,
        )

        qqq_history = [
            {"Close": 100.0 + index * 0.5, "High": 101.0 + index * 0.5, "Low": 99.0 + index * 0.5}
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

        self.assertAlmostEqual(plan["qqq_p"], 229.5)
        self.assertEqual(plan["notification_context"]["benchmark"]["symbol"], "QQQ")

    def test_tqqq_growth_income_accepts_portfolio_without_account_hash(self):
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
            metadata={"account_ids": ["U1234567"]},
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

        self.assertIsNone(plan["account_hash"])
        self.assertEqual(plan["sell_order_symbols"], ("TQQQ", "QQQM", "SPYI", "QQQI", "BOXX"))
        self.assertEqual(plan["notification_context"]["portfolio"]["raw_buying_power"], 20000.0)

    def test_tqqq_growth_income_can_trade_qqqm_while_using_qqq_signal(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.tqqq_growth_income import (
            build_rebalance_plan as build_tqqq_plan,
        )

        qqq_history = [
            {"close": 100.0 + index * 0.5, "high": 101.0 + index * 0.5, "low": 99.0 + index * 0.5}
            for index in range(260)
        ]
        snapshot = SimpleNamespace(
            positions=[
                SimpleNamespace(symbol="TQQQ", market_value=45000.0, quantity=100),
                SimpleNamespace(symbol="QQQM", market_value=10000.0, quantity=40),
                SimpleNamespace(symbol="BOXX", market_value=8000.0, quantity=80),
                SimpleNamespace(symbol="QQQ", market_value=99999.0, quantity=99),
            ],
            total_equity=100000.0,
            buying_power=2000.0,
            metadata={"account_hash": "acct-1"},
        )

        plan = build_tqqq_plan(
            qqq_history,
            snapshot,
            signal_text_fn=lambda icon: icon,
            translator=_translator,
            income_threshold_usd=1_000_000_000.0,
            qqqi_income_ratio=0.5,
            cash_reserve_ratio=0.02,
            rebalance_threshold_ratio=0.01,
            dual_drive_qqq_weight=0.45,
            dual_drive_tqqq_weight=0.45,
            dual_drive_unlevered_symbol="qqqm",
            dual_drive_cash_reserve_ratio=0.02,
        )

        self.assertEqual(plan["strategy_symbols"], ["TQQQ", "QQQM", "BOXX", "SPYI", "QQQI"])
        self.assertEqual(plan["sell_order_symbols"], ("TQQQ", "QQQM", "SPYI", "QQQI", "BOXX"))
        self.assertEqual(plan["buy_order_symbols"], ("SPYI", "QQQI", "TQQQ", "QQQM"))
        self.assertEqual(plan["portfolio_rows"], (("TQQQ", "QQQM", "BOXX"), ("SPYI", "QQQI")))
        self.assertAlmostEqual(plan["target_values"]["QQQM"], 100000.0 * 0.45)
        self.assertNotIn("QQQ", plan["target_values"])
        self.assertIn("QQQM: $", plan["dashboard"])
        self.assertIn("QQQ: 229.50 | MA200 Exit:", plan["dashboard"])

    def test_tqqq_growth_income_supports_diversified_income_layer(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.tqqq_growth_income import (
            build_rebalance_plan as build_tqqq_plan,
        )

        qqq_history = [
            {"close": 100.0 + index * 0.5, "high": 101.0 + index * 0.5, "low": 99.0 + index * 0.5}
            for index in range(260)
        ]
        snapshot = SimpleNamespace(
            positions=[SimpleNamespace(symbol="BOXX", market_value=225000.0, quantity=1000)],
            total_equity=225000.0,
            buying_power=30000.0,
            metadata={"account_hash": "acct-1"},
        )
        income_symbols = ("SCHD", "DGRO", "SGOV", "SPYI", "QQQI")

        plan = build_tqqq_plan(
            qqq_history,
            snapshot,
            signal_text_fn=lambda icon: icon,
            translator=_translator,
            income_threshold_usd=150000.0,
            qqqi_income_ratio=0.10,
            cash_reserve_ratio=0.02,
            rebalance_threshold_ratio=0.01,
            income_layer_start_usd=150000.0,
            income_layer_max_ratio=0.20,
            income_layer_qqqi_weight=0.10,
            income_layer_spyi_weight=0.20,
            income_layer_allocations={
                "SCHD": 0.40,
                "DGRO": 0.20,
                "SGOV": 0.10,
                "SPYI": 0.20,
                "QQQI": 0.10,
            },
            dual_drive_qqq_weight=0.45,
            dual_drive_tqqq_weight=0.45,
            dual_drive_cash_reserve_ratio=0.02,
        )

        self.assertEqual(plan["income_layer_symbols"], income_symbols)
        self.assertEqual(plan["strategy_symbols"], ["TQQQ", "QQQM", "BOXX", *income_symbols])
        self.assertEqual(plan["buy_order_symbols"], (*income_symbols, "TQQQ", "QQQM"))
        self.assertEqual(plan["portfolio_rows"], (("TQQQ", "QQQM", "BOXX"), income_symbols))
        self.assertAlmostEqual(plan["income_layer_ratio"], 0.0790489865839401)
        self.assertAlmostEqual(plan["income_layer_value"], 17786.021981386522)
        self.assertAlmostEqual(plan["target_values"]["TQQQ"], 93246.29010837656)
        self.assertAlmostEqual(plan["target_values"]["QQQM"], 93246.29010837656)
        self.assertAlmostEqual(plan["target_values"]["BOXX"], 16577.118241489167)
        self.assertAlmostEqual(plan["target_values"]["SCHD"], 7114.408792554609)
        self.assertAlmostEqual(plan["target_values"]["DGRO"], 3557.2043962773044)
        self.assertAlmostEqual(plan["target_values"]["SGOV"], 1778.6021981386523)
        self.assertAlmostEqual(plan["target_values"]["SPYI"], 3557.2043962773044)
        self.assertAlmostEqual(plan["target_values"]["QQQI"], 1778.6021981386523)

    def test_tqqq_growth_income_total_drawdown_budget_sets_income_layer(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.tqqq_growth_income import (
            get_income_layer_ratio,
        )

        ratio = get_income_layer_ratio(
            600000.0,
            income_layer_start_usd=150000.0,
            income_layer_max_ratio=0.55,
            income_layer_ratio_mode="log_total_drawdown_budget",
            income_layer_core_stress_drawdown_ratio=0.45,
            income_layer_income_stress_drawdown_ratio=0.08,
            income_layer_base_drawdown_budget_ratio=0.45,
            income_layer_min_drawdown_budget_ratio=0.25,
            income_layer_drawdown_budget_decay_per_double=0.05,
        )

        self.assertAlmostEqual(ratio, 0.27027027027027023)

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
        self.assertEqual(flat_plan["target_values"]["QQQM"], 0.0)
        self.assertAlmostEqual(flat_plan["target_values"]["BOXX"], 100000.0 * 0.98)

        active_snapshot = SimpleNamespace(
            positions=[
                SimpleNamespace(symbol="TQQQ", market_value=45000.0, quantity=100),
                SimpleNamespace(symbol="QQQM", market_value=45000.0, quantity=100),
                SimpleNamespace(symbol="BOXX", market_value=8000.0, quantity=80),
            ],
            total_equity=100000.0,
            buying_power=2000.0,
            metadata={"account_hash": "acct-1"},
        )
        active_plan = build_tqqq_plan(qqq_history, active_snapshot, **common_kwargs)
        self.assertAlmostEqual(active_plan["target_values"]["TQQQ"], 100000.0 * 0.45)
        self.assertAlmostEqual(active_plan["target_values"]["QQQM"], 100000.0 * 0.45)
        self.assertAlmostEqual(active_plan["target_values"]["BOXX"], 100000.0 * 0.08)
        self.assertAlmostEqual(active_plan["reserved"], 100000.0 * 0.02)

    def test_tqqq_growth_income_pullback_uses_volatility_scaled_rebound_quality(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.tqqq_growth_income import (
            build_rebalance_plan as build_tqqq_plan,
        )

        def build_history(recent):
            return [{"close": 120.0, "high": 121.0, "low": 119.0} for _ in range(220)] + [
                {"close": close, "high": close + 1.0, "low": close - 1.0}
                for close in recent
            ]

        snapshot = SimpleNamespace(
            positions=[SimpleNamespace(symbol="BOXX", market_value=100000.0, quantity=1000)],
            total_equity=100000.0,
            buying_power=2000.0,
            metadata={"account_hash": "acct-1"},
        )
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

        weak_rebound_plan = build_tqqq_plan(
            build_history(
                [
                    106,
                    108,
                    105,
                    107,
                    104,
                    106,
                    103,
                    105,
                    102,
                    104,
                    101,
                    103,
                    100,
                    102,
                    99,
                    101,
                    100,
                    101,
                    100.5,
                    101.2,
                    102.0,
                ]
            ),
            snapshot,
            **common_kwargs,
        )
        self.assertEqual(weak_rebound_plan["target_values"]["TQQQ"], 0.0)
        self.assertEqual(weak_rebound_plan["target_values"]["QQQM"], 0.0)
        self.assertEqual(weak_rebound_plan["pullback_rebound_threshold_mode"], "volatility_scaled")
        self.assertGreater(
            weak_rebound_plan["pullback_rebound_threshold"],
            weak_rebound_plan["pullback_rebound"],
        )

        strong_rebound_plan = build_tqqq_plan(
            build_history([100.0 + index * 0.45 for index in range(21)]),
            snapshot,
            **common_kwargs,
        )
        self.assertAlmostEqual(strong_rebound_plan["target_values"]["TQQQ"], 100000.0 * 0.45)
        self.assertAlmostEqual(strong_rebound_plan["target_values"]["QQQM"], 100000.0 * 0.45)
        self.assertLess(
            strong_rebound_plan["pullback_rebound_threshold"],
            strong_rebound_plan["pullback_rebound"],
        )

    def test_tqqq_growth_income_volatility_delever_redirects_tqqq_to_unlevered_sleeve(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.tqqq_growth_income import (
            build_rebalance_plan as build_tqqq_plan,
        )

        qqq_history = [{"close": 100.0, "high": 101.0, "low": 99.0} for _ in range(230)] + [
            {"close": close, "high": close + 1.0, "low": close - 1.0}
            for close in (104.0, 99.0, 106.0, 100.0, 108.0, 101.0, 110.0, 103.0, 112.0, 106.0, 115.0)
        ]
        snapshot = SimpleNamespace(
            positions=[SimpleNamespace(symbol="BOXX", market_value=100000.0, quantity=1000)],
            total_equity=100000.0,
            buying_power=2000.0,
            metadata={"account_hash": "acct-1"},
        )

        plan = build_tqqq_plan(
            qqq_history,
            snapshot,
            signal_text_fn=lambda icon: icon,
            translator=_translator,
            income_threshold_usd=1_000_000_000.0,
            qqqi_income_ratio=0.5,
            cash_reserve_ratio=0.02,
            rebalance_threshold_ratio=0.01,
            dual_drive_qqq_weight=0.45,
            dual_drive_tqqq_weight=0.45,
            dual_drive_cash_reserve_ratio=0.02,
            dual_drive_volatility_delever_enabled=True,
            dual_drive_volatility_delever_window=5,
            dual_drive_volatility_delever_threshold=0.10,
        )

        self.assertTrue(plan["dual_drive_volatility_delever_triggered"])
        self.assertTrue(plan["dual_drive_volatility_delever_applied"])
        self.assertFalse(plan["dual_drive_volatility_delever_vetoed"])
        self.assertGreater(plan["dual_drive_volatility_delever_metric"], 0.10)
        self.assertTrue(plan["dual_drive_volatility_delever_entry_triggered"])
        self.assertFalse(plan["dual_drive_volatility_delever_hysteresis_triggered"])
        self.assertEqual(plan["dual_drive_volatility_delever_trigger_reason"], "entry_threshold")
        self.assertEqual(plan["dual_drive_volatility_delever_redirect_symbol"], "QQQM")
        self.assertEqual(plan["dual_drive_volatility_delever_retained_ratio"], 0.0)
        self.assertEqual(plan["dual_drive_volatility_delever_redirected_ratio"], 1.0)
        self.assertEqual(
            plan["notification_context"]["risk_controls"]["dual_drive_volatility_delever"]["allocation_detail"],
            "TQQQ sleeve retained 0.0%, redirected to QQQM 100.0%",
        )
        self.assertEqual(plan["target_values"]["TQQQ"], 0.0)
        self.assertAlmostEqual(plan["target_values"]["QQQM"], 100000.0 * 0.90)
        self.assertAlmostEqual(plan["target_values"]["BOXX"], 100000.0 * 0.08)
        self.assertIn("Vol Delever: applied", plan["dashboard"])

    def test_tqqq_growth_income_volatility_delever_hysteresis_holds_unlevered_sleeve(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.tqqq_growth_income import (
            build_rebalance_plan as build_tqqq_plan,
        )

        qqq_history = [{"close": 100.0, "high": 101.0, "low": 99.0} for _ in range(230)] + [
            {"close": close, "high": close + 1.0, "low": close - 1.0}
            for close in (104.0, 99.0, 106.0, 100.0, 108.0, 101.0, 110.0, 103.0, 112.0, 106.0, 115.0)
        ]
        snapshot = SimpleNamespace(
            positions=[SimpleNamespace(symbol="QQQM", market_value=90000.0, quantity=100)],
            total_equity=100000.0,
            buying_power=2000.0,
            metadata={"account_hash": "acct-1"},
        )

        plan = build_tqqq_plan(
            qqq_history,
            snapshot,
            signal_text_fn=lambda icon: icon,
            translator=_translator,
            income_threshold_usd=1_000_000_000.0,
            qqqi_income_ratio=0.5,
            cash_reserve_ratio=0.02,
            rebalance_threshold_ratio=0.01,
            dual_drive_qqq_weight=0.45,
            dual_drive_tqqq_weight=0.45,
            dual_drive_cash_reserve_ratio=0.02,
            dual_drive_volatility_delever_enabled=True,
            dual_drive_volatility_delever_window=5,
            dual_drive_volatility_delever_threshold_mode="fixed",
            dual_drive_volatility_delever_threshold=10.00,
            dual_drive_volatility_delever_exit_threshold=0.10,
        )

        self.assertTrue(plan["dual_drive_volatility_delever_triggered"])
        self.assertTrue(plan["dual_drive_volatility_delever_applied"])
        self.assertFalse(plan["dual_drive_volatility_delever_entry_triggered"])
        self.assertTrue(plan["dual_drive_volatility_delever_hysteresis_triggered"])
        self.assertEqual(plan["dual_drive_volatility_delever_trigger_reason"], "hysteresis_hold")
        self.assertGreaterEqual(
            plan["dual_drive_volatility_delever_metric"],
            plan["dual_drive_volatility_delever_exit_threshold"],
        )
        self.assertLess(
            plan["dual_drive_volatility_delever_metric"],
            plan["dual_drive_volatility_delever_threshold"],
        )
        self.assertEqual(plan["target_values"]["TQQQ"], 0.0)
        self.assertEqual(plan["dual_drive_volatility_delever_retained_ratio"], 0.0)
        self.assertEqual(plan["dual_drive_volatility_delever_redirected_ratio"], 1.0)
        self.assertAlmostEqual(plan["target_values"]["QQQM"], 100000.0 * 0.90)

    def test_tqqq_growth_income_volatility_delever_uses_dynamic_percentile_threshold(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.tqqq_growth_income import (
            build_rebalance_plan as build_tqqq_plan,
        )

        calm_history = [
            {"close": 100.0 + index * 0.03, "high": 101.0 + index * 0.03, "low": 99.0 + index * 0.03}
            for index in range(245)
        ]
        volatile_tail = [
            {"close": close, "high": close + 1.0, "low": close - 1.0}
            for close in (108.0, 102.0, 111.0, 104.0, 114.0, 106.0, 118.0, 109.0, 121.0, 112.0, 126.0)
        ]
        snapshot = SimpleNamespace(
            positions=[SimpleNamespace(symbol="BOXX", market_value=100000.0, quantity=1000)],
            total_equity=100000.0,
            buying_power=2000.0,
            metadata={"account_hash": "acct-1"},
        )

        plan = build_tqqq_plan(
            [*calm_history, *volatile_tail],
            snapshot,
            signal_text_fn=lambda icon: icon,
            translator=_translator,
            income_threshold_usd=1_000_000_000.0,
            qqqi_income_ratio=0.5,
            cash_reserve_ratio=0.02,
            rebalance_threshold_ratio=0.01,
            dual_drive_qqq_weight=0.45,
            dual_drive_tqqq_weight=0.45,
            dual_drive_cash_reserve_ratio=0.02,
            dual_drive_volatility_delever_enabled=True,
            dual_drive_volatility_delever_window=5,
            dual_drive_volatility_delever_threshold=0.99,
            dual_drive_volatility_delever_threshold_mode="rolling_percentile",
            dual_drive_volatility_delever_dynamic_lookback=60,
            dual_drive_volatility_delever_dynamic_percentile=0.80,
            dual_drive_volatility_delever_dynamic_min_periods=30,
            dual_drive_volatility_delever_dynamic_cap=0.50,
        )

        self.assertEqual(plan["dual_drive_volatility_delever_threshold_mode"], "rolling_percentile")
        self.assertIsNotNone(plan["dual_drive_volatility_delever_dynamic_threshold"])
        self.assertEqual(plan["dual_drive_volatility_delever_dynamic_sample_count"], 60)
        self.assertLess(plan["dual_drive_volatility_delever_threshold"], 0.99)
        self.assertLessEqual(plan["dual_drive_volatility_delever_threshold"], 0.50)
        self.assertGreaterEqual(
            plan["dual_drive_volatility_delever_metric"],
            plan["dual_drive_volatility_delever_threshold"],
        )
        self.assertTrue(plan["dual_drive_volatility_delever_applied"])
        self.assertEqual(plan["target_values"]["TQQQ"], 0.0)
        self.assertAlmostEqual(plan["target_values"]["QQQM"], 100000.0 * 0.90)
        self.assertIn("mode p80/60d", plan["dashboard"])

    def test_tqqq_growth_income_taco_context_vetoes_volatility_delever(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.tqqq_growth_income import (
            build_rebalance_plan as build_tqqq_plan,
        )

        qqq_history = [{"close": 100.0, "high": 101.0, "low": 99.0} for _ in range(230)] + [
            {"close": close, "high": close + 1.0, "low": close - 1.0}
            for close in (104.0, 99.0, 106.0, 100.0, 108.0, 101.0, 110.0, 103.0, 112.0, 106.0, 115.0)
        ]
        snapshot = SimpleNamespace(
            positions=[SimpleNamespace(symbol="BOXX", market_value=100000.0, quantity=1000)],
            total_equity=100000.0,
            buying_power=2000.0,
            metadata={
                "taco_rebound_shadow": {
                    "plugin": "taco_rebound_shadow",
                    "canonical_route": "taco_rebound",
                    "rebound_context_active": True,
                },
            },
        )

        plan = build_tqqq_plan(
            qqq_history,
            snapshot,
            signal_text_fn=lambda icon: icon,
            translator=_translator,
            income_threshold_usd=1_000_000_000.0,
            qqqi_income_ratio=0.5,
            cash_reserve_ratio=0.02,
            rebalance_threshold_ratio=0.01,
            dual_drive_qqq_weight=0.45,
            dual_drive_tqqq_weight=0.45,
            dual_drive_cash_reserve_ratio=0.02,
            dual_drive_volatility_delever_enabled=True,
            dual_drive_volatility_delever_window=5,
            dual_drive_volatility_delever_threshold=0.10,
        )

        self.assertTrue(plan["dual_drive_volatility_delever_triggered"])
        self.assertFalse(plan["dual_drive_volatility_delever_applied"])
        self.assertTrue(plan["dual_drive_volatility_delever_vetoed"])
        self.assertEqual(plan["dual_drive_volatility_delever_veto_reason"], "taco_rebound_context")
        self.assertTrue(plan["dual_drive_volatility_delever_taco_rebound_context_active"])
        self.assertFalse(plan["dual_drive_volatility_delever_true_crisis_active"])
        self.assertAlmostEqual(plan["target_values"]["TQQQ"], 100000.0 * 0.45)
        self.assertAlmostEqual(plan["target_values"]["QQQM"], 100000.0 * 0.45)
        self.assertIn("Vol Delever: vetoed", plan["dashboard"])

    def test_tqqq_growth_income_true_crisis_overrides_taco_volatility_delever_veto(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.tqqq_growth_income import (
            build_rebalance_plan as build_tqqq_plan,
        )

        qqq_history = [{"close": 100.0, "high": 101.0, "low": 99.0} for _ in range(230)] + [
            {"close": close, "high": close + 1.0, "low": close - 1.0}
            for close in (104.0, 99.0, 106.0, 100.0, 108.0, 101.0, 110.0, 103.0, 112.0, 106.0, 115.0)
        ]
        snapshot = SimpleNamespace(
            positions=[SimpleNamespace(symbol="BOXX", market_value=100000.0, quantity=1000)],
            total_equity=100000.0,
            buying_power=2000.0,
            metadata={
                "taco_rebound_shadow": {
                    "plugin": "taco_rebound_shadow",
                    "canonical_route": "taco_rebound",
                    "rebound_context_active": True,
                },
                "crisis_response_shadow": {
                    "plugin": "crisis_response_shadow",
                    "canonical_route": "true_crisis",
                },
            },
        )

        plan = build_tqqq_plan(
            qqq_history,
            snapshot,
            signal_text_fn=lambda icon: icon,
            translator=_translator,
            income_threshold_usd=1_000_000_000.0,
            qqqi_income_ratio=0.5,
            cash_reserve_ratio=0.02,
            rebalance_threshold_ratio=0.01,
            dual_drive_qqq_weight=0.45,
            dual_drive_tqqq_weight=0.45,
            dual_drive_cash_reserve_ratio=0.02,
            dual_drive_volatility_delever_enabled=True,
            dual_drive_volatility_delever_window=5,
            dual_drive_volatility_delever_threshold=0.10,
        )

        self.assertFalse(plan["dual_drive_volatility_delever_triggered"])
        self.assertFalse(plan["dual_drive_volatility_delever_applied"])
        self.assertFalse(plan["dual_drive_volatility_delever_vetoed"])
        self.assertTrue(plan["dual_drive_volatility_delever_taco_rebound_context_active"])
        self.assertTrue(plan["dual_drive_volatility_delever_true_crisis_active"])
        self.assertTrue(plan["dual_drive_crisis_defense_triggered"])
        self.assertTrue(plan["dual_drive_crisis_defense_applied"])
        self.assertEqual(plan["target_values"]["TQQQ"], 0.0)
        self.assertEqual(plan["target_values"]["QQQM"], 0.0)
        self.assertAlmostEqual(plan["target_values"]["BOXX"], 100000.0 * 0.98)
        self.assertIn("Crisis Defense: applied", plan["dashboard"])

    def test_tqqq_growth_income_macro_risk_governor_delever_redirects_tqqq_to_qqqm(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.tqqq_growth_income import (
            build_rebalance_plan as build_tqqq_plan,
        )

        qqq_history = [
            {"close": 100.0 + index * 0.5, "high": 101.0 + index * 0.5, "low": 99.0 + index * 0.5}
            for index in range(260)
        ]
        snapshot = SimpleNamespace(
            positions=[
                SimpleNamespace(symbol="TQQQ", market_value=45000.0, quantity=1000),
                SimpleNamespace(symbol="QQQM", market_value=45000.0, quantity=100),
                SimpleNamespace(symbol="BOXX", market_value=8000.0, quantity=80),
            ],
            total_equity=100000.0,
            buying_power=2000.0,
            metadata={
                "macro_risk_governor": {
                    "plugin": "macro_risk_governor",
                    "canonical_route": "delever",
                    "suggested_action": "delever",
                    "leverage_scalar": 0.0,
                    "risk_asset_scalar": 1.0,
                    "actionable_score": 5.0,
                    "reason_codes": ["vix_crisis_level", "credit_pair_stress"],
                }
            },
        )

        plan = build_tqqq_plan(
            qqq_history,
            snapshot,
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

        self.assertTrue(plan["dual_drive_macro_risk_governor_found"])
        self.assertTrue(plan["dual_drive_macro_risk_governor_active"])
        self.assertTrue(plan["dual_drive_macro_risk_governor_applied"])
        self.assertEqual(plan["dual_drive_macro_risk_governor_route"], "delever")
        self.assertEqual(plan["target_values"]["TQQQ"], 0.0)
        self.assertAlmostEqual(plan["target_values"]["QQQM"], 100000.0 * 0.90)
        self.assertAlmostEqual(plan["target_values"]["BOXX"], 100000.0 * 0.08)
        self.assertAlmostEqual(plan["dual_drive_macro_risk_governor_redirected_to_unlevered"], 100000.0 * 0.45)
        self.assertAlmostEqual(plan["dual_drive_macro_risk_governor_removed_value"], 0.0)
        self.assertIn("Macro Risk Governor: applied", plan["dashboard"])

    def test_tqqq_growth_income_macro_risk_governor_crisis_moves_risk_to_boxx(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.tqqq_growth_income import (
            build_rebalance_plan as build_tqqq_plan,
        )

        qqq_history = [
            {"close": 100.0 + index * 0.5, "high": 101.0 + index * 0.5, "low": 99.0 + index * 0.5}
            for index in range(260)
        ]
        snapshot = SimpleNamespace(
            positions=[
                SimpleNamespace(symbol="TQQQ", market_value=45000.0, quantity=1000),
                SimpleNamespace(symbol="QQQM", market_value=45000.0, quantity=100),
                SimpleNamespace(symbol="BOXX", market_value=8000.0, quantity=80),
            ],
            total_equity=100000.0,
            buying_power=2000.0,
            metadata={
                "macro_risk_governor": {
                    "plugin": "macro_risk_governor",
                    "canonical_route": "crisis",
                    "suggested_action": "defend",
                    "leverage_scalar": 0.0,
                    "risk_asset_scalar": 0.0,
                    "actionable_score": 7.0,
                }
            },
        )

        plan = build_tqqq_plan(
            qqq_history,
            snapshot,
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

        self.assertTrue(plan["dual_drive_macro_risk_governor_active"])
        self.assertTrue(plan["dual_drive_macro_risk_governor_applied"])
        self.assertEqual(plan["dual_drive_macro_risk_governor_route"], "crisis")
        self.assertEqual(plan["target_values"]["TQQQ"], 0.0)
        self.assertEqual(plan["target_values"]["QQQM"], 0.0)
        self.assertAlmostEqual(plan["target_values"]["BOXX"], 100000.0 * 0.98)
        self.assertAlmostEqual(plan["dual_drive_macro_risk_governor_removed_value"], 100000.0 * 0.90)
        self.assertIn("Macro Risk Governor: applied", plan["dashboard"])

    def test_tqqq_growth_income_market_regime_control_delever_latest_policy_moves_risk_to_boxx(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.tqqq_growth_income import (
            build_rebalance_plan as build_tqqq_plan,
        )

        qqq_history = [
            {"close": 100.0 + index * 0.5, "high": 101.0 + index * 0.5, "low": 99.0 + index * 0.5}
            for index in range(260)
        ]
        snapshot = SimpleNamespace(
            positions=[
                SimpleNamespace(symbol="TQQQ", market_value=45000.0, quantity=1000),
                SimpleNamespace(symbol="QQQM", market_value=45000.0, quantity=100),
                SimpleNamespace(symbol="BOXX", market_value=8000.0, quantity=80),
            ],
            total_equity=100000.0,
            buying_power=2000.0,
            metadata={
                "market_regime_control": {
                    "plugin": "market_regime_control",
                    "schema_version": "market_regime_control.v1",
                    "canonical_route": "risk_reduced",
                    "suggested_action": "delever",
                    **_market_regime_authorization(),
                    "position_control": {
                        "final_route": "risk_reduced",
                        "suggested_action": "delever",
                        "route_source": "macro",
                        "leverage_scalar": 0.0,
                        "risk_asset_scalar": 0.0,
                        "risk_budget_scalar": 1.0,
                        "taco_allowed": False,
                        "local_delever_veto_allowed": False,
                        "reason_codes": ["macro:vix_crisis_level"],
                    },
                    "component_signals": {
                        "macro": {
                            "actionable_score": 5.0,
                            "total_score": 5.0,
                        }
                    },
                }
            },
        )

        plan = build_tqqq_plan(
            qqq_history,
            snapshot,
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

        self.assertTrue(plan["market_regime_control_found"])
        self.assertEqual(plan["market_regime_control_schema_version"], "market_regime_control.v1")
        self.assertTrue(plan["market_regime_control_active"])
        self.assertEqual(plan["market_regime_control_route"], "risk_reduced")
        self.assertTrue(plan["dual_drive_macro_risk_governor_applied"])
        self.assertEqual(plan["target_values"]["TQQQ"], 0.0)
        self.assertAlmostEqual(plan["target_values"]["QQQM"], 0.0)
        self.assertAlmostEqual(plan["target_values"]["BOXX"], 100000.0 * 0.98)
        self.assertIn("Market Regime Control: applied", plan["dashboard"])

    def test_tqqq_growth_income_ignores_unapproved_market_regime_position_control(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.tqqq_growth_income import (
            build_rebalance_plan as build_tqqq_plan,
        )

        qqq_history = [
            {"close": 100.0 + index * 0.5, "high": 101.0 + index * 0.5, "low": 99.0 + index * 0.5}
            for index in range(260)
        ]
        snapshot = SimpleNamespace(
            positions=[
                SimpleNamespace(symbol="TQQQ", market_value=45000.0, quantity=1000),
                SimpleNamespace(symbol="QQQM", market_value=45000.0, quantity=100),
                SimpleNamespace(symbol="BOXX", market_value=8000.0, quantity=80),
            ],
            total_equity=100000.0,
            buying_power=2000.0,
            metadata={
                "market_regime_control": {
                    "plugin": "market_regime_control",
                    "schema_version": "market_regime_control.v1",
                    "canonical_route": "risk_reduced",
                    "suggested_action": "delever",
                    **_market_regime_authorization(approved=False),
                    "position_control": {
                        "final_route": "risk_reduced",
                        "suggested_action": "delever",
                        "route_source": "macro",
                        "leverage_scalar": 0.0,
                        "risk_asset_scalar": 0.0,
                        "risk_budget_scalar": 1.0,
                    },
                }
            },
        )

        plan = build_tqqq_plan(
            qqq_history,
            snapshot,
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

        self.assertTrue(plan["market_regime_control_found"])
        self.assertFalse(plan["market_regime_control_active"])
        self.assertFalse(plan["market_regime_control_position_control_authorized"])
        self.assertEqual(plan["market_regime_control_consumption_evidence_status"], "notification_only")
        self.assertFalse(plan["dual_drive_macro_risk_governor_active"])
        self.assertFalse(plan["dual_drive_macro_risk_governor_applied"])
        self.assertGreater(plan["target_values"]["TQQQ"], 0.0)
        self.assertGreater(plan["target_values"]["QQQM"], 0.0)

    def test_tqqq_growth_income_can_disable_market_regime_control_position_effect(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.tqqq_growth_income import (
            build_rebalance_plan as build_tqqq_plan,
        )

        qqq_history = [
            {"close": 100.0 + index * 0.5, "high": 101.0 + index * 0.5, "low": 99.0 + index * 0.5}
            for index in range(260)
        ]
        snapshot = SimpleNamespace(
            positions=[
                SimpleNamespace(symbol="TQQQ", market_value=45000.0, quantity=1000),
                SimpleNamespace(symbol="QQQM", market_value=45000.0, quantity=100),
                SimpleNamespace(symbol="BOXX", market_value=8000.0, quantity=80),
            ],
            total_equity=100000.0,
            buying_power=2000.0,
            metadata={
                "market_regime_control": {
                    "plugin": "market_regime_control",
                    "schema_version": "market_regime_control.v1",
                    "canonical_route": "risk_reduced",
                    "suggested_action": "delever",
                    **_market_regime_authorization(),
                    "position_control": {
                        "final_route": "risk_reduced",
                        "suggested_action": "delever",
                        "route_source": "macro",
                        "leverage_scalar": 0.0,
                        "risk_asset_scalar": 0.0,
                    },
                }
            },
        )

        plan = build_tqqq_plan(
            qqq_history,
            snapshot,
            signal_text_fn=lambda icon: icon,
            translator=_translator,
            income_threshold_usd=1_000_000_000.0,
            qqqi_income_ratio=0.5,
            cash_reserve_ratio=0.02,
            rebalance_threshold_ratio=0.01,
            dual_drive_qqq_weight=0.45,
            dual_drive_tqqq_weight=0.45,
            dual_drive_cash_reserve_ratio=0.02,
            market_regime_control_enabled=False,
        )

        self.assertFalse(plan["market_regime_control_enabled"])
        self.assertFalse(plan["market_regime_control_found"])
        self.assertFalse(plan["dual_drive_macro_risk_governor_found"])
        self.assertFalse(plan["dual_drive_macro_risk_governor_applied"])
        self.assertAlmostEqual(plan["target_values"]["TQQQ"], 100000.0 * 0.45)
        self.assertAlmostEqual(plan["target_values"]["QQQM"], 100000.0 * 0.45)
        self.assertAlmostEqual(plan["target_values"]["BOXX"], 100000.0 * 0.08)
        self.assertFalse(plan["notification_context"]["risk_controls"]["market_regime_control"]["enabled"])

    def test_tqqq_growth_income_market_regime_control_taco_allows_local_volatility_veto(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.tqqq_growth_income import (
            build_rebalance_plan as build_tqqq_plan,
        )

        qqq_history = [{"close": 100.0, "high": 101.0, "low": 99.0} for _ in range(230)] + [
            {"close": close, "high": close + 1.0, "low": close - 1.0}
            for close in (104.0, 99.0, 106.0, 100.0, 108.0, 101.0, 110.0, 103.0, 112.0, 106.0, 115.0)
        ]
        snapshot = SimpleNamespace(
            positions=[SimpleNamespace(symbol="BOXX", market_value=100000.0, quantity=1000)],
            total_equity=100000.0,
            buying_power=2000.0,
            metadata={
                "market_regime_control": {
                    "plugin": "market_regime_control",
                    "canonical_route": "opportunity_watch",
                    "suggested_action": "notify_manual_review",
                    **_market_regime_authorization(),
                    "position_control": {
                        "final_route": "opportunity_watch",
                        "suggested_action": "notify_manual_review",
                        "route_source": "taco",
                        "leverage_scalar": 1.0,
                        "risk_asset_scalar": 1.0,
                        "risk_budget_scalar": 1.0,
                        "taco_allowed": True,
                        "local_delever_veto_allowed": True,
                        "reason_codes": ["taco:taco_rebound"],
                    },
                }
            },
        )

        plan = build_tqqq_plan(
            qqq_history,
            snapshot,
            signal_text_fn=lambda icon: icon,
            translator=_translator,
            income_threshold_usd=1_000_000_000.0,
            qqqi_income_ratio=0.5,
            cash_reserve_ratio=0.02,
            rebalance_threshold_ratio=0.01,
            dual_drive_qqq_weight=0.45,
            dual_drive_tqqq_weight=0.45,
            dual_drive_cash_reserve_ratio=0.02,
            dual_drive_volatility_delever_enabled=True,
            dual_drive_volatility_delever_window=5,
            dual_drive_volatility_delever_threshold=0.10,
        )

        self.assertTrue(plan["market_regime_control_found"])
        self.assertFalse(plan["market_regime_control_active"])
        self.assertTrue(plan["market_regime_control_taco_allowed"])
        self.assertTrue(plan["dual_drive_volatility_delever_triggered"])
        self.assertFalse(plan["dual_drive_volatility_delever_applied"])
        self.assertTrue(plan["dual_drive_volatility_delever_vetoed"])
        self.assertEqual(plan["dual_drive_volatility_delever_veto_reason"], "taco_rebound_context")

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
            market_regime_control_apply_risk_reduced=True,
        )

        self.assertEqual(plan["limit_order_symbols"], ("SOXL", "SOXX", "QQQI", "SPYI"))
        self.assertEqual(plan["allocation_mode"], SOXX_GATE_TIERED_BLEND_MODE)
        self.assertEqual(plan["blend_tier"], "full")
        self.assertEqual(plan["active_risk_asset"], "SOXX+SOXL")
        self.assertEqual(plan["market_status"], "market_status_blend_gate_risk_on(asset=SOXX+SOXL)")
        self.assertAlmostEqual(plan["targets"]["SOXL"], 70000.0)
        self.assertAlmostEqual(plan["targets"]["SOXX"], 20000.0)
        self.assertAlmostEqual(plan["targets"]["BOXX"], 10000.0)
        self.assertEqual(
            plan["notification_context"]["status"]["code"],
            "market_status_blend_gate_risk_on",
        )
        self.assertEqual(
            plan["notification_context"]["signal"]["code"],
            "signal_blend_gate_risk_on",
        )
        self.assertAlmostEqual(plan["reserved_cash"], 3000.0)
        self.assertAlmostEqual(plan["investable_cash"], 2000.0)
        self.assertEqual(plan["notification_context"]["portfolio"]["raw_buying_power"], 5000.0)
        self.assertAlmostEqual(plan["notification_context"]["portfolio"]["reserved_cash"], 3000.0)
        self.assertAlmostEqual(plan["notification_context"]["portfolio"]["investable_cash"], 2000.0)

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
            market_regime_control_apply_risk_reduced=True,
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

    def test_soxl_soxx_trend_income_supports_diversified_income_basket(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.soxl_soxx_trend_income import (
            SOXX_GATE_TIERED_BLEND_MODE,
            build_rebalance_plan as build_soxl_soxx_plan,
        )

        income_symbols = ("SCHD", "DGRO", "SGOV", "SPYI", "QQQI")
        account_state = {
            "available_cash": 5000.0,
            "market_values": {
                "SOXL": 0.0,
                "SOXX": 0.0,
                "BOXX": 300000.0,
                **{symbol: 0.0 for symbol in income_symbols},
            },
            "quantities": {"SOXL": 0, "SOXX": 0, "BOXX": 3000, **{symbol: 0 for symbol in income_symbols}},
            "sellable_quantities": {"SOXL": 0, "SOXX": 0, "BOXX": 3000, **{symbol: 0 for symbol in income_symbols}},
            "total_strategy_equity": 300000.0,
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
            income_layer_max_ratio=0.25,
            income_layer_qqqi_weight=0.05,
            income_layer_spyi_weight=0.15,
            income_layer_allocations={
                "SCHD": 0.40,
                "DGRO": 0.20,
                "SGOV": 0.20,
                "SPYI": 0.15,
                "QQQI": 0.05,
            },
            attack_allocation_mode=SOXX_GATE_TIERED_BLEND_MODE,
            blend_gate_trend_source="SOXX",
            trend_entry_buffer=0.08,
            trend_mid_buffer=0.06,
            trend_exit_buffer=0.02,
            blend_gate_soxl_weight=0.70,
            blend_gate_mid_soxl_weight=0.65,
            blend_gate_active_soxx_weight=0.20,
            blend_gate_defensive_soxx_weight=0.15,
            market_regime_control_apply_risk_reduced=True,
        )

        self.assertEqual(plan["income_layer_symbols"], income_symbols)
        self.assertEqual(plan["limit_order_symbols"], ("SOXL", "SOXX", *income_symbols))
        self.assertAlmostEqual(plan["targets"]["SOXL"], 183076.9230769231)
        self.assertAlmostEqual(plan["targets"]["SOXX"], 52307.69230769232)
        self.assertAlmostEqual(plan["targets"]["BOXX"], 26153.84615384616)
        self.assertAlmostEqual(plan["targets"]["SCHD"], 15384.61538461538)
        self.assertAlmostEqual(plan["targets"]["DGRO"], 7692.30769230769)
        self.assertAlmostEqual(plan["targets"]["SGOV"], 7692.30769230769)
        self.assertAlmostEqual(plan["targets"]["SPYI"], 5769.230769230767)
        self.assertAlmostEqual(plan["targets"]["QQQI"], 1923.0769230769226)

    def test_soxl_soxx_trend_income_overlay_cap_can_downgrade_live_tier(self):
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
            blend_gate_rsi_cap_enabled=True,
            blend_gate_rsi_threshold=70.0,
            blend_gate_bollinger_cap_enabled=True,
            blend_gate_overlay_stack_triggers=True,
        )

        mid_plan = build_soxl_soxx_plan(
            {
                "soxl": {"price": 50.0, "ma_trend": 45.0},
                "soxx": {"price": 109.0, "ma_trend": 100.0, "rsi14": 75.0, "bb_upper": 120.0},
            },
            account_state,
            **common_kwargs,
        )
        self.assertEqual(mid_plan["base_blend_tier"], "full")
        self.assertEqual(mid_plan["blend_tier"], "mid")
        self.assertEqual(mid_plan["overlay_trigger_count"], 1)
        self.assertEqual(mid_plan["overlay_trigger_codes"], ("blend_gate_reason_rsi_cap",))
        self.assertAlmostEqual(mid_plan["targets"]["SOXL"], 65000.0)
        self.assertAlmostEqual(mid_plan["targets"]["SOXX"], 20000.0)
        self.assertEqual(
            mid_plan["notification_context"]["status"]["code"],
            "market_status_blend_gate_overlay_capped",
        )

        dynamic_hold_plan = build_soxl_soxx_plan(
            {
                "soxl": {"price": 50.0, "ma_trend": 45.0},
                "soxx": {
                    "price": 109.0,
                    "ma_trend": 100.0,
                    "rsi14": 75.0,
                    "rsi14_dynamic_threshold": 78.0,
                    "bb_upper": 120.0,
                },
            },
            account_state,
            **{**common_kwargs, "blend_gate_dynamic_rsi_threshold_enabled": True},
        )
        self.assertEqual(dynamic_hold_plan["blend_tier"], "full")
        self.assertEqual(dynamic_hold_plan["overlay_trigger_count"], 0)
        self.assertEqual(dynamic_hold_plan["trend_rsi14_effective_threshold"], 78.0)

        dynamic_mid_plan = build_soxl_soxx_plan(
            {
                "soxl": {"price": 50.0, "ma_trend": 45.0},
                "soxx": {
                    "price": 109.0,
                    "ma_trend": 100.0,
                    "rsi14": 79.0,
                    "rsi14_dynamic_threshold": 78.0,
                    "bb_upper": 120.0,
                },
            },
            account_state,
            **{**common_kwargs, "blend_gate_dynamic_rsi_threshold_enabled": True},
        )
        self.assertEqual(dynamic_mid_plan["blend_tier"], "mid")
        self.assertEqual(dynamic_mid_plan["overlay_trigger_codes"], ("blend_gate_reason_rsi_cap",))

        defensive_plan = build_soxl_soxx_plan(
            {
                "soxl": {"price": 50.0, "ma_trend": 45.0},
                "soxx": {"price": 109.0, "ma_trend": 100.0, "rsi14": 75.0, "bb_upper": 108.0},
            },
            account_state,
            **common_kwargs,
        )
        self.assertEqual(defensive_plan["base_blend_tier"], "full")
        self.assertEqual(defensive_plan["blend_tier"], "defensive")
        self.assertEqual(defensive_plan["overlay_trigger_count"], 2)
        self.assertEqual(
            defensive_plan["overlay_trigger_codes"],
            ("blend_gate_reason_rsi_cap", "blend_gate_reason_bollinger_cap"),
        )
        self.assertAlmostEqual(defensive_plan["targets"]["SOXL"], 0.0)
        self.assertAlmostEqual(defensive_plan["targets"]["SOXX"], 15000.0)
        self.assertAlmostEqual(defensive_plan["targets"]["BOXX"], 85000.0)

    def test_soxl_soxx_trend_income_volatility_delever_redirects_soxl_to_soxx(self):
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
                "soxx": {
                    "price": 109.0,
                    "ma_trend": 100.0,
                    "realized_volatility_10": 0.55,
                },
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
            blend_gate_volatility_delever_enabled=True,
            blend_gate_volatility_delever_symbol="SOXX",
            blend_gate_volatility_delever_window=10,
            blend_gate_volatility_delever_threshold=0.50,
            blend_gate_volatility_delever_retention_ratio=0.0,
            blend_gate_volatility_delever_redirect_symbol="SOXX",
        )

        self.assertEqual(plan["base_blend_tier"], "full")
        self.assertEqual(plan["blend_tier"], "full")
        self.assertTrue(plan["blend_gate_volatility_delever_triggered"])
        self.assertEqual(plan["blend_gate_volatility_delever_window"], 10)
        self.assertEqual(plan["active_risk_asset"], "SOXX")
        self.assertEqual(plan["overlay_trigger_codes"], ("blend_gate_reason_volatility_delever",))
        self.assertAlmostEqual(plan["targets"]["SOXL"], 0.0)
        self.assertAlmostEqual(plan["targets"]["SOXX"], 90000.0)
        self.assertAlmostEqual(plan["targets"]["BOXX"], 10000.0)
        self.assertAlmostEqual(plan["blend_gate_volatility_delever_removed_ratio"], 0.70)
        self.assertEqual(
            plan["notification_context"]["signal"]["code"],
            "signal_blend_gate_overlay_capped",
        )

    def test_soxl_soxx_trend_income_uses_dynamic_volatility_delever_threshold(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.soxl_soxx_trend_income import (
            SOXX_GATE_TIERED_BLEND_MODE,
            VOLATILITY_DELEVER_THRESHOLD_MODE_ROLLING_PERCENTILE,
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
                "soxx": {
                    "price": 109.0,
                    "ma_trend": 100.0,
                    "realized_volatility_10": 0.61,
                    "realized_volatility_10_dynamic_threshold": 0.60,
                    "realized_volatility_10_dynamic_sample_count": 252.0,
                },
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
            blend_gate_volatility_delever_enabled=True,
            blend_gate_volatility_delever_symbol="SOXX",
            blend_gate_volatility_delever_window=10,
            blend_gate_volatility_delever_threshold=0.55,
            blend_gate_volatility_delever_threshold_mode=VOLATILITY_DELEVER_THRESHOLD_MODE_ROLLING_PERCENTILE,
            blend_gate_volatility_delever_dynamic_min_periods=126,
            blend_gate_volatility_delever_retention_ratio=0.0,
            blend_gate_volatility_delever_redirect_symbol="SOXX",
        )

        self.assertTrue(plan["blend_gate_volatility_delever_triggered"])
        self.assertEqual(
            plan["blend_gate_volatility_delever_threshold_mode"],
            VOLATILITY_DELEVER_THRESHOLD_MODE_ROLLING_PERCENTILE,
        )
        self.assertAlmostEqual(plan["blend_gate_volatility_delever_threshold"], 0.60)
        self.assertAlmostEqual(plan["blend_gate_volatility_delever_dynamic_threshold"], 0.60)
        self.assertAlmostEqual(plan["blend_gate_volatility_delever_dynamic_sample_count"], 252.0)
        self.assertEqual(plan["overlay_trigger_codes"], ("blend_gate_reason_volatility_delever",))
        self.assertIn(
            "blend_gate_reason_volatility_delever_dynamic",
            plan["overlay_trigger_reasons"][0],
        )
        self.assertIn(
            "threshold_detail=blend_gate_volatility_threshold_detail_dynamic",
            plan["overlay_trigger_reasons"][0],
        )

    def test_soxl_soxx_trend_income_market_regime_control_delever_moves_risk_to_boxx(self):
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
            "metadata": {
                "market_regime_control": {
                    "plugin": "market_regime_control",
                    "schema_version": "market_regime_control.v1",
                    "canonical_route": "risk_reduced",
                    "suggested_action": "delever",
                    **_market_regime_authorization(),
                    "position_control": {
                        "final_route": "risk_reduced",
                        "suggested_action": "delever",
                        "route_source": "macro",
                        "leverage_scalar": 0.0,
                        "risk_asset_scalar": 0.0,
                        "risk_budget_scalar": 1.0,
                        "reason_codes": ["macro:vix_crisis_level"],
                    },
                }
            },
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
            market_regime_control_enabled=True,
            market_regime_control_apply_risk_reduced=True,
        )

        self.assertTrue(plan["market_regime_control_found"])
        self.assertEqual(plan["market_regime_control_schema_version"], "market_regime_control.v1")
        self.assertEqual(plan["market_regime_control_route"], "risk_reduced")
        self.assertTrue(plan["market_regime_control_applied"])
        self.assertEqual(plan["active_risk_asset"], "BOXX")
        self.assertEqual(plan["overlay_trigger_codes"], ("blend_gate_reason_market_regime_control_risk_reduced",))
        self.assertAlmostEqual(plan["targets"]["SOXL"], 0.0)
        self.assertAlmostEqual(plan["targets"]["SOXX"], 0.0)
        self.assertAlmostEqual(plan["targets"]["BOXX"], 100000.0)
        self.assertAlmostEqual(plan["market_regime_control_removed_ratio"], 0.90)
        self.assertAlmostEqual(plan["market_regime_control_redirected_to_unlevered_ratio"], 0.70)
        self.assertEqual(
            plan["notification_context"]["risk_controls"]["market_regime_control"]["route"],
            "risk_reduced",
        )

    def test_soxl_soxx_trend_income_ignores_unapproved_market_regime_position_control(self):
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
            "metadata": {
                "market_regime_control": {
                    "plugin": "market_regime_control",
                    "schema_version": "market_regime_control.v1",
                    "canonical_route": "risk_reduced",
                    "suggested_action": "delever",
                    **_market_regime_authorization(approved=False),
                    "position_control": {
                        "final_route": "risk_reduced",
                        "suggested_action": "delever",
                        "route_source": "macro",
                        "leverage_scalar": 0.0,
                        "risk_asset_scalar": 0.0,
                        "risk_budget_scalar": 1.0,
                    },
                }
            },
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
            market_regime_control_enabled=True,
            market_regime_control_apply_risk_reduced=True,
        )

        self.assertTrue(plan["market_regime_control_found"])
        self.assertFalse(plan["market_regime_control_active"])
        self.assertFalse(plan["market_regime_control_applied"])
        self.assertFalse(plan["market_regime_control_position_control_authorized"])
        self.assertEqual(plan["market_regime_control_consumption_evidence_status"], "notification_only")
        self.assertEqual(plan["active_risk_asset"], "SOXX+SOXL")
        self.assertAlmostEqual(plan["targets"]["SOXL"], 100000.0 * 0.70)
        self.assertAlmostEqual(plan["targets"]["SOXX"], 100000.0 * 0.20)
        self.assertAlmostEqual(plan["targets"]["BOXX"], 100000.0 * 0.10)

    def test_soxl_soxx_trend_income_default_does_not_consume_market_regime_control(self):
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
            "metadata": {
                "market_regime_control": {
                    "plugin": "market_regime_control",
                    "schema_version": "market_regime_control.v1",
                    "canonical_route": "risk_reduced",
                    "suggested_action": "delever",
                    **_market_regime_authorization(),
                    "position_control": {
                        "final_route": "risk_reduced",
                        "suggested_action": "delever",
                        "route_source": "macro",
                        "leverage_scalar": 0.0,
                        "risk_asset_scalar": 0.0,
                    },
                }
            },
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

        self.assertFalse(plan["market_regime_control_enabled"])
        self.assertFalse(plan["market_regime_control_found"])
        self.assertEqual(plan["market_regime_control_route"], "")
        self.assertFalse(plan["market_regime_control_route_allowed"])
        self.assertFalse(plan["market_regime_control_applied"])
        self.assertEqual(plan["active_risk_asset"], "SOXX+SOXL")
        self.assertAlmostEqual(plan["targets"]["SOXL"], 100000.0 * 0.70)
        self.assertAlmostEqual(plan["targets"]["SOXX"], 100000.0 * 0.20)
        self.assertAlmostEqual(plan["targets"]["BOXX"], 100000.0 * 0.10)
        self.assertFalse(plan["notification_context"]["risk_controls"]["market_regime_control"]["enabled"])

    def test_soxl_soxx_trend_income_can_disable_market_regime_control_position_effect(self):
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
            "metadata": {
                "market_regime_control": {
                    "plugin": "market_regime_control",
                    "schema_version": "market_regime_control.v1",
                    "canonical_route": "risk_reduced",
                    "suggested_action": "delever",
                    **_market_regime_authorization(),
                    "position_control": {
                        "final_route": "risk_reduced",
                        "suggested_action": "delever",
                        "route_source": "macro",
                        "leverage_scalar": 0.0,
                        "risk_asset_scalar": 0.0,
                    },
                }
            },
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
            market_regime_control_enabled=False,
        )

        self.assertFalse(plan["market_regime_control_enabled"])
        self.assertFalse(plan["market_regime_control_found"])
        self.assertFalse(plan["market_regime_control_applied"])
        self.assertEqual(plan["active_risk_asset"], "SOXX+SOXL")
        self.assertAlmostEqual(plan["targets"]["SOXL"], 100000.0 * 0.70)
        self.assertAlmostEqual(plan["targets"]["SOXX"], 100000.0 * 0.20)
        self.assertAlmostEqual(plan["targets"]["BOXX"], 100000.0 * 0.10)
        self.assertFalse(plan["notification_context"]["risk_controls"]["market_regime_control"]["enabled"])

    def test_soxl_soxx_trend_income_legacy_crisis_adapter_maps_to_market_regime_risk_off(self):
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
            "metadata": {
                "crisis_response_shadow": {
                    "plugin": "crisis_response_shadow",
                    "schema_version": "crisis_response_shadow.v1",
                    "canonical_route": "true_crisis",
                }
            },
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
            market_regime_control_enabled=True,
        )

        self.assertTrue(plan["market_regime_control_found"])
        self.assertEqual(plan["market_regime_control_source"], "crisis_response_shadow")
        self.assertEqual(plan["market_regime_control_route"], "risk_off")
        self.assertTrue(plan["market_regime_control_applied"])
        self.assertEqual(plan["active_risk_asset"], "BOXX")
        self.assertAlmostEqual(plan["targets"]["SOXL"], 0.0)
        self.assertAlmostEqual(plan["targets"]["SOXX"], 0.0)
        self.assertAlmostEqual(plan["targets"]["BOXX"], 100000.0)

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
