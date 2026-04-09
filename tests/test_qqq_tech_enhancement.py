from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd


def _feature_snapshot() -> pd.DataFrame:
    as_of = pd.Timestamp("2026-03-31")
    rows = [
        {
            "as_of": as_of,
            "symbol": "QQQ",
            "sector": "benchmark",
            "close": 500.0,
            "volume": 1_000_000,
            "adv20_usd": 1_000_000_000.0,
            "history_days": 400,
            "mom_6_1": 0.20,
            "mom_12_1": 0.30,
            "sma20_gap": 0.03,
            "sma50_gap": 0.05,
            "sma200_gap": 0.08,
            "ma50_over_ma200": 0.04,
            "vol_63": 0.22,
            "maxdd_126": -0.12,
            "breakout_252": -0.01,
            "dist_63_high": -0.03,
            "dist_126_high": -0.05,
            "rebound_20": 0.04,
            "base_eligible": False,
        },
        {
            "as_of": as_of,
            "symbol": "BOXX",
            "sector": "defense",
            "close": 101.0,
            "volume": 200_000,
            "adv20_usd": 20_000_000.0,
            "history_days": 400,
            "mom_6_1": 0.02,
            "mom_12_1": 0.04,
            "sma20_gap": 0.00,
            "sma50_gap": 0.00,
            "sma200_gap": 0.01,
            "ma50_over_ma200": 0.00,
            "vol_63": 0.03,
            "maxdd_126": -0.01,
            "breakout_252": 0.00,
            "dist_63_high": -0.01,
            "dist_126_high": -0.01,
            "rebound_20": 0.00,
            "base_eligible": False,
        },
    ]
    tech_rows = [
        ("AAPL", "Information Technology", 0.18, 0.31, 0.02, 0.04, 0.07, 0.03, 0.19, -0.10, -0.02, -0.04, -0.07, 0.05),
        ("MSFT", "Information Technology", 0.16, 0.29, 0.02, 0.04, 0.06, 0.03, 0.18, -0.11, -0.03, -0.05, -0.08, 0.04),
        ("NVDA", "Information Technology", 0.28, 0.55, 0.05, 0.08, 0.16, 0.09, 0.32, -0.01, -0.02, -0.04, -0.06, 0.10),
        ("META", "Communication", 0.20, 0.38, 0.03, 0.05, 0.10, 0.05, 0.25, -0.06, -0.04, -0.08, -0.10, 0.06),
        ("GOOGL", "Communication", 0.17, 0.27, 0.02, 0.03, 0.07, 0.03, 0.20, -0.08, -0.05, -0.09, -0.11, 0.05),
        ("NFLX", "Communication", 0.19, 0.34, 0.03, 0.05, 0.09, 0.04, 0.22, -0.05, -0.03, -0.07, -0.09, 0.05),
        ("TTWO", "Communication", 0.14, 0.21, 0.01, 0.02, 0.05, 0.02, 0.14, -0.06, -0.04, -0.08, -0.11, 0.03),
        ("CRM", "Information Technology", 0.14, 0.24, 0.01, 0.02, 0.05, 0.02, 0.16, -0.07, -0.05, -0.08, -0.10, 0.03),
        ("ADBE", "Information Technology", 0.13, 0.22, 0.01, 0.02, 0.04, 0.01, 0.15, -0.09, -0.05, -0.07, -0.09, 0.02),
        ("NOW", "Information Technology", 0.15, 0.26, 0.02, 0.03, 0.05, 0.02, 0.18, -0.04, -0.03, -0.06, -0.09, 0.05),
    ]
    for symbol, sector, mom6, mom12, sma20, sma50, sma200, ma50_200, breakout, d63, d126, mdd, vol, rebound in tech_rows:
        rows.append(
            {
                "as_of": as_of,
                "symbol": symbol,
                "sector": sector,
                "close": 100.0,
                "volume": 1_000_000,
                "adv20_usd": 120_000_000.0,
                "history_days": 400,
                "mom_6_1": mom6,
                "mom_12_1": mom12,
                "sma20_gap": sma20,
                "sma50_gap": sma50,
                "sma200_gap": sma200,
                "ma50_over_ma200": ma50_200,
                "vol_63": vol,
                "maxdd_126": d126,
                "breakout_252": breakout,
                "dist_63_high": d63,
                "dist_126_high": d126,
                "rebound_20": rebound,
                "base_eligible": True,
            }
        )
    return pd.DataFrame(rows)


class QQQTechEnhancementStrategyTest(unittest.TestCase):
    def test_build_target_weights_is_geometry_honest(self):
        from us_equity_strategies.strategies.qqq_tech_enhancement import build_target_weights

        weights, signal, metadata = build_target_weights(
            _feature_snapshot(),
            current_holdings={"AAPL"},
        )

        self.assertIn("target_stock=80.0%", signal)
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=8)
        self.assertAlmostEqual(metadata["target_stock_weight"], 0.8, places=8)
        self.assertAlmostEqual(metadata["realized_stock_weight"], 0.8, places=8)
        self.assertEqual(len(metadata["selected_symbols"]), 8)
        self.assertAlmostEqual(weights["BOXX"], 0.2, places=8)

    def test_compute_signals_noops_outside_execution_window(self):
        from us_equity_strategies.strategies.qqq_tech_enhancement import compute_signals

        weights, _signal, _emergency, status_desc, metadata = compute_signals(
            _feature_snapshot(),
            current_holdings=set(),
            run_as_of="2026-04-10",
        )

        self.assertIsNone(weights)
        self.assertIn("outside_monthly_execution_window", metadata["no_op_reason"])
        self.assertIn("no-op", status_desc)

    def test_load_runtime_parameters_reads_canonical_config(self):
        from us_equity_strategies.strategies.qqq_tech_enhancement import load_runtime_parameters

        with TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "qqq_tech_enhancement.json"
            config_path.write_text(
                json.dumps(
                    {
                        "name": "qqq_tech_enhancement",
                        "family": "tech_heavy_pullback",
                        "branch_role": "cash-buffered parallel branch",
                        "benchmark_symbol": "QQQ",
                        "holdings_count": 8,
                        "single_name_cap": 0.10,
                        "sector_cap": 0.40,
                        "hold_bonus": 0.10,
                        "min_adv20_usd": 50_000_000.0,
                        "normalization": "universe_cross_sectional",
                        "score_template": "balanced_pullback",
                        "sector_whitelist": ["Information Technology", "Communication"],
                        "residual_proxy": "simple_excess_return_vs_QQQ",
                        "breadth_thresholds": {"soft": 0.55, "hard": 0.35},
                        "exposures": {"risk_on": 0.8, "soft_defense": 0.6, "hard_defense": 0.0},
                        "execution_cash_reserve_ratio": 0.0,
                    }
                ),
                encoding="utf-8",
            )

            params = load_runtime_parameters(config_path=config_path)

        self.assertEqual(params["runtime_config_source"], "external_config")
        self.assertEqual(params["runtime_config_name"], "qqq_tech_enhancement")
        self.assertEqual(params["sector_whitelist"], ("Information Technology", "Communication"))
        self.assertEqual(params["execution_cash_reserve_ratio"], 0.0)


if __name__ == "__main__":
    unittest.main()
