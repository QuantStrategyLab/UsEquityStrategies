"""Tests for UsEtfRotationBacktestRunner + BacktestOrchestrator integration."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from us_equity_strategies.backtest.orchestrator_runner import (
    SUPPORTED_PROFILES,
    UsEquityComboBacktestRunner,
    UsEtfRotationBacktestRunner,
    build_backtest_runner,
)
from us_equity_strategies.strategies.global_etf_rotation import DEFAULT_MIN_HISTORY_DAYS, PROFILE_NAME
from us_equity_strategies.strategies.us_equity_combo import PROFILE_NAME as US_EQUITY_COMBO_PROFILE


class UsEtfRotationBacktestRunnerTests(unittest.TestCase):
    def test_supported_profile_includes_global_etf(self) -> None:
        self.assertIn(PROFILE_NAME, SUPPORTED_PROFILES)

    def test_supported_profile_includes_equity_combo(self) -> None:
        self.assertIn(US_EQUITY_COMBO_PROFILE, SUPPORTED_PROFILES)

    def test_build_backtest_runner_dispatches_combo(self) -> None:
        runner = build_backtest_runner(US_EQUITY_COMBO_PROFILE, synthetic_days=500)
        self.assertIsInstance(runner, UsEquityComboBacktestRunner)

    def test_run_returns_backtest_result(self) -> None:
        runner = UsEtfRotationBacktestRunner(synthetic_days=500)
        result = runner.run(
            PROFILE_NAME,
            {"min_history_days": DEFAULT_MIN_HISTORY_DAYS},
            start_date=date(2023, 6, 1),
            end_date=date(2024, 6, 1),
        )
        self.assertEqual(result.strategy_profile, PROFILE_NAME)
        self.assertEqual(result.domain, "us_equity")
        self.assertIsNotNone(result.sharpe_ratio)
        self.assertGreater(result.observation_count, 0)

    def test_unsupported_profile_raises(self) -> None:
        runner = UsEtfRotationBacktestRunner(synthetic_days=100)
        with self.assertRaises(ValueError):
            runner.run("unknown_profile", {})


class UsEquityComboBacktestRunnerTests(unittest.TestCase):
    def test_run_returns_backtest_result(self) -> None:
        runner = UsEquityComboBacktestRunner(synthetic_days=500)
        result = runner.run(
            US_EQUITY_COMBO_PROFILE,
            {"min_history_days": DEFAULT_MIN_HISTORY_DAYS, "combo_mode": "dynamic"},
            start_date=date(2023, 6, 1),
            end_date=date(2024, 6, 1),
        )
        self.assertEqual(result.strategy_profile, US_EQUITY_COMBO_PROFILE)
        self.assertEqual(result.domain, "us_equity")
        self.assertGreater(result.observation_count, 0)

    def test_walk_forward_combo_profile(self) -> None:
        from quant_platform_kit.strategy_lifecycle.backtest_orchestrator import BacktestOrchestrator
        from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore

        with tempfile.TemporaryDirectory() as tmp:
            store = PerformanceStore(local_root=Path(tmp))
            orchestrator = BacktestOrchestrator(store=store)
            orchestrator.register_runner(
                "us_equity",
                UsEquityComboBacktestRunner(synthetic_days=800),
            )
            windows = (
                (date(2023, 6, 1), date(2023, 12, 31)),
                (date(2024, 1, 1), date(2024, 6, 30)),
            )
            results = orchestrator.walk_forward(
                US_EQUITY_COMBO_PROFILE,
                domain="us_equity",
                params={"min_history_days": DEFAULT_MIN_HISTORY_DAYS, "combo_mode": "dynamic"},
                windows=windows,
            )
            self.assertEqual(len(results), 2)
            self.assertTrue(all(item.strategy_profile == US_EQUITY_COMBO_PROFILE for item in results))


class WalkForwardPilotTests(unittest.TestCase):
    def test_walk_forward_produces_one_result_per_window(self) -> None:
        from quant_platform_kit.strategy_lifecycle.backtest_orchestrator import BacktestOrchestrator
        from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore

        with tempfile.TemporaryDirectory() as tmp:
            store = PerformanceStore(local_root=Path(tmp))
            orchestrator = BacktestOrchestrator(store=store)
            orchestrator.register_runner("us_equity", UsEtfRotationBacktestRunner(synthetic_days=800))
            windows = (
                (date(2023, 6, 1), date(2023, 12, 31)),
                (date(2024, 1, 1), date(2024, 6, 30)),
            )
            results = orchestrator.walk_forward(
                PROFILE_NAME,
                domain="us_equity",
                params={"min_history_days": DEFAULT_MIN_HISTORY_DAYS},
                windows=windows,
            )
            self.assertEqual(len(results), 2)
            self.assertTrue(all(item.strategy_profile == PROFILE_NAME for item in results))


if __name__ == "__main__":
    unittest.main()
