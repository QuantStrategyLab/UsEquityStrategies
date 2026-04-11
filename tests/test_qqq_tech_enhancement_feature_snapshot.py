from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import json

import pandas as pd


def _price_frame() -> pd.DataFrame:
    start = pd.Timestamp("2025-01-01")
    rows = []
    for day in range(420):
        as_of = start + pd.Timedelta(days=day)
        rows.append({"symbol": "AAPL", "as_of": as_of, "close": 150.0 + day * 0.20, "volume": 2_000_000})
        rows.append({"symbol": "MSFT", "as_of": as_of, "close": 300.0 + day * 0.15, "volume": 1_500_000})
        rows.append({"symbol": "META", "as_of": as_of, "close": 200.0 + day * 0.18, "volume": 1_200_000})
        rows.append({"symbol": "JNJ", "as_of": as_of, "close": 160.0 + day * 0.02, "volume": 800_000})
        rows.append({"symbol": "QQQ", "as_of": as_of, "close": 400.0 + day * 0.25, "volume": 3_000_000})
        rows.append({"symbol": "SPY", "as_of": as_of, "close": 500.0 + day * 0.20, "volume": 4_000_000})
        rows.append({"symbol": "BOXX", "as_of": as_of, "close": 101.0 + day * 0.005, "volume": 250_000})
    return pd.DataFrame(rows)


class QQQTechEnhancementFeatureSnapshotTest(unittest.TestCase):
    def test_build_feature_snapshot_filters_to_tech_sectors(self):
        from us_equity_strategies.snapshots.qqq_tech_enhancement import build_feature_snapshot

        snapshot = build_feature_snapshot(
            _price_frame(),
            [
                {"symbol": "AAPL", "sector": "Information Technology"},
                {"symbol": "MSFT", "sector": "Information Technology"},
                {"symbol": "META", "sector": "Communication"},
                {"symbol": "JNJ", "sector": "Health Care"},
            ],
            as_of_date="2026-02-24",
        )

        symbols = set(snapshot["symbol"])
        self.assertIn("AAPL", symbols)
        self.assertIn("META", symbols)
        self.assertNotIn("JNJ", symbols)
        self.assertIn("QQQ", symbols)
        self.assertIn("BOXX", symbols)
        base_flags = dict(zip(snapshot["symbol"], snapshot["base_eligible"]))
        self.assertTrue(base_flags["AAPL"])
        self.assertFalse(base_flags["QQQ"])
        self.assertFalse(base_flags["BOXX"])

    def test_cli_writes_snapshot(self):
        from scripts.generate_qqq_tech_enhancement_feature_snapshot import main

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            prices_path = tmp_path / "prices.csv"
            universe_path = tmp_path / "universe.csv"
            output_path = tmp_path / "snapshot.csv"
            config_path = tmp_path / "tech_communication_pullback_enhancement.json"

            _price_frame().to_csv(prices_path, index=False)
            universe_path.write_text(
                "symbol,sector\nAAPL,Information Technology\nMSFT,Information Technology\nMETA,Communication\n",
                encoding="utf-8",
            )
            config_path.write_text(
                json.dumps(
                    {
                        "name": "tech_communication_pullback_enhancement",
                    }
                ),
                encoding="utf-8",
            )

            exit_code = main(
                [
                    "--prices",
                    str(prices_path),
                    "--universe",
                    str(universe_path),
                    "--output",
                    str(output_path),
                    "--config-path",
                    str(config_path),
                    "--as-of",
                    "2026-02-24",
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.exists())
            self.assertTrue(Path(f"{output_path}.manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
