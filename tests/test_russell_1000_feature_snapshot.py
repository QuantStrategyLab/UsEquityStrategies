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


def _price_rows():
    rows = []
    for day in range(320):
        close_aaa = 100.0 + (day * 0.40)
        close_bbb = 8.0 + (day * 0.002)
        close_spy = 400.0 + (day * 0.30)
        rows.append({"symbol": "AAA", "as_of": f"2024-01-{(day % 28) + 1:02d}", "close": close_aaa, "volume": 300000})
        rows.append({"symbol": "BBB", "as_of": f"2024-01-{(day % 28) + 1:02d}", "close": close_bbb, "volume": 200000})
        rows.append({"symbol": "SPY", "as_of": f"2024-01-{(day % 28) + 1:02d}", "close": close_spy, "volume": 1000000})
    return rows


def _price_frame():
    import pandas as pd

    start = pd.Timestamp("2024-01-01")
    rows = []
    for day in range(320):
        as_of = start + pd.Timedelta(days=day)
        close_aaa = 100.0 + (day * 0.40)
        close_bbb = 8.0 + (day * 0.002)
        close_spy = 400.0 + (day * 0.30)
        rows.append({"symbol": "AAA", "as_of": as_of, "close": close_aaa, "volume": 300000})
        rows.append({"symbol": "BBB", "as_of": as_of, "close": close_bbb, "volume": 200000})
        rows.append({"symbol": "SPY", "as_of": as_of, "close": close_spy, "volume": 1000000})
    return pd.DataFrame(rows)


class Russell1000FeatureSnapshotTest(unittest.TestCase):
    def test_build_feature_snapshot_marks_low_price_stock_ineligible(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.snapshots.russell_1000_multi_factor_defensive import (
            build_feature_snapshot,
        )

        snapshot = build_feature_snapshot(
            _price_frame(),
            [
                {"symbol": "AAA", "sector": "tech"},
                {"symbol": "BBB", "sector": "health"},
            ],
            benchmark_symbol="SPY",
            min_price_usd=10.0,
            min_adv20_usd=1_000_000.0,
        )

        rows = {row["symbol"]: row for row in snapshot.to_dict(orient="records")}
        self.assertTrue(rows["AAA"]["eligible"])
        self.assertFalse(rows["BBB"]["eligible"])
        self.assertFalse(rows["SPY"]["eligible"])
        self.assertGreater(rows["AAA"]["mom_12_1"], 0.0)
        self.assertIn("adv20_usd", rows["AAA"])

    def test_cli_writes_csv_snapshot(self):
        _skip_if_missing_numeric_stack()
        from scripts.generate_russell_1000_feature_snapshot import main

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            prices_path = tmp_path / "prices.csv"
            universe_path = tmp_path / "universe.csv"
            output_path = tmp_path / "snapshot.csv"

            _price_frame().to_csv(prices_path, index=False)
            universe_path.write_text("symbol,sector\nAAA,tech\nBBB,health\n", encoding="utf-8")

            exit_code = main(
                [
                    "--prices",
                    str(prices_path),
                    "--universe",
                    str(universe_path),
                    "--output",
                    str(output_path),
                    "--benchmark-symbol",
                    "SPY",
                    "--min-adv20-usd",
                    "1000000",
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.exists())


if __name__ == "__main__":
    unittest.main()
