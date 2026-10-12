"""A2 opt-in research adapter, with synthetic prices only."""
import random
import os
import importlib
import unittest
from unittest.mock import patch
from datetime import date, timedelta

from us_equity_strategies.research.execution_model_adapter import compare_execution_timings, replay_targets
from us_equity_strategies.research.kelly_core_return_generator import _replay


def fixture(opens=(100, 120, 90, 100), closes=(100, 130, 110, 100)):
    days = tuple(date(2026, 1, 1) + timedelta(days=i) for i in range(len(opens)))
    return days, opens, closes


def _models_available():
    try:
        importlib.import_module("quant_platform_kit.research_stats.execution_models")
        return True
    except ImportError:
        return False


@unittest.skipUnless(_models_available() or os.environ.get("UES_REQUIRE_QPK_EXECUTION_MODELS") == "1",
                     "installed QPK lacks opt-in A2 execution models; pin remains unchanged")
class ExecutionAdapterTests(unittest.TestCase):
    def test_modes_repeat_and_differ_on_gaps(self):
        args = fixture()
        targets = (1.0, 1.0, 0.0, 0.0)
        actual = compare_execution_timings(*args, targets)
        self.assertEqual(actual, compare_execution_timings(*args, targets))
        self.assertEqual(tuple(p.timing for p in actual), ("same_close", "next_close", "next_open"))
        self.assertAlmostEqual(actual[0].gross_returns[1], .3)
        self.assertEqual(actual[1].gross_returns[1], 0)
        self.assertAlmostEqual(actual[2].gross_returns[1], 130 / 120 - 1)
        self.assertTrue(all(p.scope == "DEVELOPMENT_ONLY" for p in actual))

    def test_next_close_gross_and_shifted_turnover_match_old_engine(self):
        rng = random.Random(7)
        closes = tuple(100 * (1 + rng.uniform(-.2, .2)) for _ in range(40))
        days, opens, closes = fixture(closes, closes)
        targets = tuple(rng.choice((0., .25, .5, 1.)) for _ in days)
        returns = (0.,) + tuple(b/a - 1 for a, b in zip(closes, closes[1:]))
        old = _replay("X", days, {"A": returns}, ("A",), lambda i, _: {"A": targets[i]}, None)
        new = replay_targets(days, opens, closes, targets, timing="next_close")
        self.assertEqual(new.gross_returns, old.gross_returns)
        self.assertEqual(new.legacy_turnover, old.turnover)

    def test_exit_cost_is_charged_even_at_last_close(self):
        args = fixture((100, 100, 100), (100, 100, 100))
        path = replay_targets(*args, (1., 0., 0.), timing="next_close", commission_bps=100)
        self.assertEqual(path.fill_turnover, (0., 1., 1.))
        self.assertEqual(path.legacy_turnover, (0., 0., 1.))
        self.assertAlmostEqual(path.net_returns[1], -.01)
        self.assertAlmostEqual(path.net_returns[2], -.01)
        self.assertEqual(len(path.fills), 2)
        self.assertEqual(path.unfilled_signal_indices, (2,))

    def test_cost_gaps_and_position_drift(self):
        args = fixture((100, 120), (100, 132))
        path = replay_targets(*args, (1., None), timing="next_open", commission_bps=100)
        self.assertAlmostEqual(path.gross_returns[1], .1)
        self.assertAlmostEqual(path.net_returns[1], .089)
        # Old holdings earn the gap before the new open target; cash earns zero.
        path = replay_targets(*args, (.5, 0.), timing="same_close")
        self.assertAlmostEqual(path.gross_returns[1], .16)
        self.assertAlmostEqual(path.fills[-1].turnover, .5 * 1.32 / 1.16)

    def test_future_perturbation_invariance(self):
        a = replay_targets(*fixture(), (1., .5, 0., 1.), timing="next_open", slippage_bps=5)
        b = replay_targets(*fixture((100, 120, 90, 999), (100, 130, 110, 888)),
                           (1., .5, 0., 0.), timing="next_open", slippage_bps=5)
        self.assertEqual(a.gross_returns[:3], b.gross_returns[:3])
        self.assertEqual(a.net_returns[:3], b.net_returns[:3])
        self.assertEqual(a.fills[:2], b.fills[:2])

    def test_invalid_prices_sessions_targets_are_rejected(self):
        for opens in ((100, 0), (100, float("nan")), (100, True)):
            with self.assertRaises(ValueError):
                replay_targets(*fixture(opens, (100, 100)), (1., 0.), timing="next_open")
        for target in (1.01, -.1, True, float("nan")):
            with self.assertRaises(ValueError):
                replay_targets(*fixture(), (target,) * 4, timing="same_close")
        with self.assertRaises(ValueError):
            replay_targets((date(2026, 1, 1),) * 2, (100, 100), (100, 100), (1., 0.), timing="same_close")
        with self.assertRaisesRegex(ValueError, "CORPORATE_ACTION"):
            replay_targets(*fixture(), (1.,) * 4, timing="next_open", price_basis="raw_unadjusted")


class OptionalDependencyTests(unittest.TestCase):
    def test_missing_models_fail_closed_without_using_legacy_fallback(self):
        original = __import__

        def guarded(name, *args, **kwargs):
            if name == "quant_platform_kit.research_stats.execution_models":
                raise ImportError("synthetic absent dependency")
            return original(name, *args, **kwargs)

        with patch("builtins.__import__", guarded):
            with self.assertRaisesRegex(ValueError, "QPK_EXECUTION_MODELS_REQUIRED"):
                replay_targets(*fixture(), (1.,) * 4, timing="next_open")


if __name__ == "__main__":
    unittest.main()
