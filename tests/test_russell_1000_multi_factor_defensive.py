from __future__ import annotations

import unittest


def _skip_if_missing_numeric_stack() -> None:
    try:
        import numpy  # noqa: F401
        import pandas  # noqa: F401
    except ModuleNotFoundError as exc:
        raise unittest.SkipTest(f"numeric stack unavailable: {exc}") from exc


def _normal_snapshot():
    return [
        {
            "symbol": "SPY",
            "sector": "benchmark",
            "mom_6_1": 0.20,
            "mom_12_1": 0.24,
            "sma200_gap": 0.03,
            "vol_63": 0.15,
            "maxdd_126": 0.08,
            "eligible": False,
        },
        {
            "symbol": "AAA",
            "sector": "tech",
            "mom_6_1": 0.40,
            "mom_12_1": 0.42,
            "sma200_gap": 0.10,
            "vol_63": 0.18,
            "maxdd_126": 0.10,
        },
        {
            "symbol": "BBB",
            "sector": "tech",
            "mom_6_1": 0.30,
            "mom_12_1": 0.31,
            "sma200_gap": 0.06,
            "vol_63": 0.22,
            "maxdd_126": 0.14,
        },
        {
            "symbol": "DDD",
            "sector": "health",
            "mom_6_1": 0.38,
            "mom_12_1": 0.40,
            "sma200_gap": 0.11,
            "vol_63": 0.17,
            "maxdd_126": 0.09,
        },
        {
            "symbol": "EEE",
            "sector": "health",
            "mom_6_1": 0.20,
            "mom_12_1": 0.22,
            "sma200_gap": 0.02,
            "vol_63": 0.26,
            "maxdd_126": 0.18,
        },
        {
            "symbol": "GGG",
            "sector": "industrial",
            "mom_6_1": 0.35,
            "mom_12_1": 0.37,
            "sma200_gap": 0.08,
            "vol_63": 0.19,
            "maxdd_126": 0.10,
        },
        {
            "symbol": "HHH",
            "sector": "industrial",
            "mom_6_1": 0.12,
            "mom_12_1": 0.15,
            "sma200_gap": 0.01,
            "vol_63": 0.28,
            "maxdd_126": 0.20,
        },
    ]


def _hard_defense_snapshot():
    return [
        {
            "symbol": "SPY",
            "sector": "benchmark",
            "mom_6_1": -0.10,
            "mom_12_1": -0.05,
            "sma200_gap": -0.02,
            "vol_63": 0.18,
            "maxdd_126": 0.14,
            "eligible": False,
        },
        {
            "symbol": "AAA",
            "sector": "tech",
            "mom_6_1": 0.25,
            "mom_12_1": 0.20,
            "sma200_gap": 0.04,
            "vol_63": 0.22,
            "maxdd_126": 0.11,
        },
        {
            "symbol": "BBB",
            "sector": "tech",
            "mom_6_1": 0.10,
            "mom_12_1": 0.08,
            "sma200_gap": -0.03,
            "vol_63": 0.30,
            "maxdd_126": 0.19,
        },
        {
            "symbol": "CCC",
            "sector": "health",
            "mom_6_1": 0.12,
            "mom_12_1": 0.10,
            "sma200_gap": -0.04,
            "vol_63": 0.27,
            "maxdd_126": 0.18,
        },
        {
            "symbol": "DDD",
            "sector": "industrial",
            "mom_6_1": 0.11,
            "mom_12_1": 0.09,
            "sma200_gap": -0.01,
            "vol_63": 0.29,
            "maxdd_126": 0.17,
        },
    ]


class Russell1000MultiFactorDefensiveTest(unittest.TestCase):
    def test_build_target_weights_prefers_current_holding_inside_sector_slots(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.russell_1000_multi_factor_defensive import (
            build_target_weights,
        )

        weights, signal, metadata = build_target_weights(
            _normal_snapshot(),
            current_holdings={"BBB"},
            holdings_count=3,
            single_name_cap=0.40,
            sector_cap=0.50,
            hold_bonus=5.00,
        )

        self.assertEqual(metadata["regime"], "risk_on")
        self.assertEqual(metadata["selected_symbols"], ("BBB", "DDD", "GGG"))
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        self.assertNotIn("BOXX", weights)
        self.assertIn("selected=3", signal)

    def test_compute_signals_includes_managed_symbols_metadata(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.russell_1000_multi_factor_defensive import (
            compute_signals,
        )

        weights, _signal, _is_emergency, status_desc, metadata = compute_signals(
            _normal_snapshot(),
            current_holdings={"BBB"},
            holdings_count=3,
            single_name_cap=0.40,
            sector_cap=0.50,
            hold_bonus=5.00,
        )

        self.assertIn("breadth=", status_desc)
        self.assertEqual(metadata["status_icon"], "📏")
        self.assertIn("BOXX", metadata["managed_symbols"])
        self.assertIn("BBB", weights)

    def test_build_target_weights_rotates_to_safe_haven_under_hard_defense(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.strategies.russell_1000_multi_factor_defensive import (
            build_target_weights,
        )

        weights, _signal, metadata = build_target_weights(
            _hard_defense_snapshot(),
            current_holdings={},
            holdings_count=2,
            single_name_cap=0.20,
            sector_cap=1.0,
            hard_defense_exposure=0.10,
        )

        self.assertEqual(metadata["regime"], "hard_defense")
        self.assertAlmostEqual(metadata["breadth_ratio"], 0.25)
        self.assertAlmostEqual(weights["BOXX"], 0.90)
        self.assertAlmostEqual(sum(weights.values()), 1.0)


if __name__ == "__main__":
    unittest.main()
