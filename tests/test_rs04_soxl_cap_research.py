"""Synthetic acceptance only; these tests never read cloud or market data."""
import importlib.util
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "rs04_cap", Path(__file__).resolve().parents[1] / "scripts/rs04_soxl_cap_research.py")
research = importlib.util.module_from_spec(spec)
spec.loader.exec_module(research)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class CapResearchTests(unittest.TestCase):
    def test_actual_replay_preserves_next_close_and_last_fill(self):
        from us_equity_strategies.research.kelly_core_return_generator import _replay
        days = [date(2026, 1, 1) + timedelta(days=i) for i in range(4)]
        returns = {"SOXL": [0, .1, .1, .1], "SOXX": [0] * 4, "BOXX": [0] * 4}
        def decide(i, drifted):
            return {"SOXL": .679 if i < 2 else 0, "SOXX": .1, "BOXX": .1}
        path = research.capped_replay(_replay, .45)(
            "SOXL", days, returns, ("SOXL", "SOXX", "BOXX"), decide, None)
        self.assertEqual(path.gross_returns[:2], (0, 0))
        self.assertAlmostEqual(path.gross_returns[2], .045)
        self.assertAlmostEqual(path.close_fill_turnover[1], .65)
        self.assertGreater(path.close_fill_turnover[-1], .45)
        self.assertEqual(path.turnover[-1], path.close_fill_turnover[-2])

    def test_final_cap_clips_once_without_rescaling_existing_sizing(self):
        target = {"SOXL": 0.2, "SOXX": 0.1, "BOXX": 0.1}
        self.assertEqual(research.capped_target(target, 0.45), target)
        target["SOXL"] = 0.679
        clipped = research.capped_target(target, 0.45)
        self.assertEqual(clipped, {"SOXL": 0.45, "SOXX": 0.1, "BOXX": 0.1})
        self.assertEqual(target["SOXL"], 0.679)

    def test_preserves_other_runtime_limits(self):
        with self.assertRaisesRegex(research.ResearchStop, "OTHER_RUNTIME_CAP_EXCEEDED"):
            research.capped_target({"SOXL": .7, "SOXX": .3}, .45)
        with self.assertRaisesRegex(research.ResearchStop, "TARGET_INVALID"):
            research.capped_target({"SOXL": float("nan")}, .45)

    def test_initial_nav_drawdown_and_recovery_are_not_lost(self):
        days = [date(2026, 1, 1) + timedelta(days=i) for i in range(20)]
        result = research.metrics(days, [-.5, 1.0] + [0.0] * 18)
        self.assertEqual(result["max_drawdown"], .5)
        self.assertEqual(result["worst_20_session_drawdown"], .5)
        self.assertEqual(result["max_drawdown_peak_to_recovery_sessions"], 2)
        self.assertEqual(result["giveback_from_peak_nav"], 0)
        self.assertEqual(result["cagr"], 0)

    def test_unrecovered_drawdown_is_censored_and_profit_giveback_defined(self):
        days = [date(2026, 1, 1) + timedelta(days=i) for i in range(20)]
        result = research.metrics(days, [1.0, -.25] + [0.0] * 18)
        self.assertEqual(result["max_drawdown"], .25)
        self.assertEqual(result["giveback_from_peak_nav"], .25)
        self.assertEqual(result["profit_giveback_fraction"], .5)
        self.assertIsNone(result["max_drawdown_peak_to_recovery_sessions"])
        self.assertEqual(result["max_drawdown_unrecovered_sessions"], 19)

    def test_20_session_drawdown_is_peak_to_trough_not_endpoint_return(self):
        days = [date(2026, 1, 1) + timedelta(days=i) for i in range(40)]
        result = research.metrics(days, [0.0] * 20 + [1.0, -.5] + [0.0] * 18)
        self.assertEqual(result["worst_20_session_drawdown"], .5)

    def test_missing_cloud_input_parks_before_imports_or_network(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_RUN_ATTEMPT": "1"}, clear=True):
            output = StringIO()
            with patch.object(research, "research", side_effect=AssertionError("no network")), redirect_stdout(output):
                self.assertEqual(research.main(["--preflight"]), 2)
            result = json.loads(output.getvalue())
            self.assertEqual(result["reason_code"], "RS04_CLOUD_ARCHIVE_LOCATION_REQUIRED")
            self.assertIsNone(result["metrics"])

    def test_local_data_and_repeat_attempts_are_rejected(self):
        prefix = "gs://synthetic-research-evidence/research/v2/input/rs04"
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(research.ResearchStop, "REAL_DATA_CLOUD_ONLY"):
                research.validate_cloud(prefix)
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_RUN_ATTEMPT": "2"}, clear=True):
            with self.assertRaisesRegex(research.ResearchStop, "REPEAT_RUN_REJECTED"):
                research.validate_cloud(prefix)


if __name__ == "__main__":
    unittest.main()
