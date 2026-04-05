from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


def _skip_if_missing_numeric_stack() -> None:
    try:
        import numpy  # noqa: F401
        import pandas  # noqa: F401
    except ModuleNotFoundError as exc:
        raise unittest.SkipTest(f"numeric stack unavailable: {exc}") from exc


def _build_price_history():
    import pandas as pd

    start = pd.Timestamp("2024-01-01")
    rows = []
    for day in range(330):
        as_of = start + pd.Timedelta(days=day)
        rows.extend(
            [
                {"symbol": "AAA", "as_of": as_of, "close": 100.0 + (day * 0.50), "volume": 400000},
                {"symbol": "BBB", "as_of": as_of, "close": 100.0 + (day * 0.15), "volume": 350000},
                {"symbol": "CCC", "as_of": as_of, "close": 100.0 - (day * 0.05), "volume": 300000},
                {"symbol": "SPY", "as_of": as_of, "close": 400.0 + (day * 0.25), "volume": 1000000},
                {"symbol": "BOXX", "as_of": as_of, "close": 100.0 + (day * 0.01), "volume": 500000},
            ]
        )
    return rows


def _build_universe():
    return [
        {"symbol": "AAA", "sector": "tech"},
        {"symbol": "BBB", "sector": "health"},
        {"symbol": "CCC", "sector": "industrial"},
    ]


class Russell1000BacktestTest(unittest.TestCase):
    def test_run_backtest_produces_positive_equity_curve(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.backtests.russell_1000_multi_factor_defensive import (
            run_backtest,
        )

        result = run_backtest(
            _build_price_history(),
            _build_universe(),
            holdings_count=2,
            single_name_cap=0.60,
            sector_cap=0.60,
            turnover_cost_bps=0.0,
        )

        summary = result["summary"]
        weights_history = result["weights_history"]
        self.assertGreater(summary["Final Equity"], 1.0)
        self.assertGreater(summary["Total Return"], 0.0)
        self.assertIn("AAA", weights_history.columns)
        self.assertIn("BOXX", weights_history.columns)
        self.assertGreater(weights_history["AAA"].max(), 0.0)

    def test_backtest_cli_writes_outputs(self):
        _skip_if_missing_numeric_stack()
        from scripts.backtest_russell_1000_multi_factor_defensive import main

        import pandas as pd

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            prices_path = tmp_path / "prices.csv"
            universe_path = tmp_path / "universe.csv"
            output_dir = tmp_path / "backtest"

            pd.DataFrame(_build_price_history()).to_csv(prices_path, index=False)
            pd.DataFrame(_build_universe()).to_csv(universe_path, index=False)

            exit_code = main(
                [
                    "--prices",
                    str(prices_path),
                    "--universe",
                    str(universe_path),
                    "--output-dir",
                    str(output_dir),
                    "--holdings-count",
                    "2",
                    "--single-name-cap",
                    "0.60",
                    "--sector-cap",
                    "0.60",
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue((output_dir / "summary.csv").exists())
            self.assertTrue((output_dir / "portfolio_returns.csv").exists())
            self.assertTrue((output_dir / "weights_history.csv").exists())


if __name__ == "__main__":
    unittest.main()
