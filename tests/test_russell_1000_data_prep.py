from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


def _skip_if_missing_numeric_stack() -> None:
    try:
        import pandas  # noqa: F401
    except ModuleNotFoundError as exc:
        raise unittest.SkipTest(f"pandas unavailable: {exc}") from exc


class Russell1000DataPrepTest(unittest.TestCase):
    def test_build_interval_universe_history_from_snapshot_dates(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.data_prep.russell_1000_history import (
            build_interval_universe_history,
        )

        history = build_interval_universe_history(
            [
                (
                    "2024-06-28",
                    [
                        {"symbol": "AAA", "sector": "tech"},
                        {"symbol": "BBB", "sector": "health"},
                    ],
                ),
                (
                    "2025-06-27",
                    [
                        {"symbol": "AAA", "sector": "tech"},
                        {"symbol": "CCC", "sector": "industrial"},
                    ],
                ),
            ]
        )

        rows = history.to_dict(orient="records")
        self.assertEqual(rows[0]["symbol"], "AAA")
        self.assertEqual(str(rows[0]["start_date"].date()), "2024-06-28")
        self.assertEqual(str(rows[0]["end_date"].date()), "2025-06-26")
        self.assertEqual(rows[-1]["symbol"], "CCC")

    def test_build_universe_history_cli_reads_directory(self):
        _skip_if_missing_numeric_stack()
        from scripts.build_russell_1000_universe_history import main

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            (tmp_path / "r1000_2024-06-28.csv").write_text(
                "symbol,sector\nAAA,tech\nBBB,health\n",
                encoding="utf-8",
            )
            (tmp_path / "r1000_2025-06-27.csv").write_text(
                "symbol,sector\nAAA,tech\nCCC,industrial\n",
                encoding="utf-8",
            )
            output_path = tmp_path / "history.csv"

            exit_code = main(
                [
                    "--input-dir",
                    str(tmp_path),
                    "--output",
                    str(output_path),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.exists())

    def test_build_universe_history_cli_supports_backfill_start_date(self):
        _skip_if_missing_numeric_stack()
        import pandas as pd

        from scripts.build_russell_1000_universe_history import main

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            (tmp_path / "r1000_2020-04-16.csv").write_text(
                "symbol,sector\nAAA,tech\nBBB,health\n",
                encoding="utf-8",
            )
            output_path = tmp_path / "history.csv"

            exit_code = main(
                [
                    "--input-dir",
                    str(tmp_path),
                    "--output",
                    str(output_path),
                    "--backfill-start-date",
                    "2018-01-01",
                ]
            )

            history = pd.read_csv(output_path)
            self.assertEqual(exit_code, 0)
            self.assertEqual(history["start_date"].iloc[0], "2018-01-01")

    def test_parse_ishares_holdings_snapshot_normalizes_rows(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.data_prep.russell_1000_history import (
            parse_ishares_holdings_snapshot,
        )

        as_of_date, snapshot = parse_ishares_holdings_snapshot(
            """\ufeffiShares Russell 1000 ETF
Fund Holdings as of,"Apr 16, 2020"
Inception Date,"May 15, 2000"
Ticker,Name,Asset Class,Weight (%),Price,Shares,Market Value,Notional Value,Sector,SEDOL,ISIN,Exchange
"MSFT","MICROSOFT CORP","Equity","5.26","177.04","5,830,318.00","1,032,199,498.72","1,032,199,498.72","Information Technology","2588173","US5949181045","NASDAQ"
"BRKB","BERKSHIRE HATHAWAY INC CLASS B","Equity","1.44","187.96","1,507,863.00","283,417,929.48","283,417,929.48","Financials","2073390","US0846707026","NYSE"
"","","","","","","","","","","",""
"""
        )

        self.assertEqual(str(as_of_date.date()), "2020-04-16")
        self.assertEqual(snapshot["symbol"].tolist(), ["BRKB", "MSFT"])
        self.assertIn("name", snapshot.columns)

    def test_parse_ishares_holdings_json_snapshot_normalizes_rows(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.data_prep.russell_1000_history import (
            parse_ishares_holdings_json_snapshot,
        )

        as_of_date, snapshot = parse_ishares_holdings_json_snapshot(
            """
            {"aaData":[
                ["MSFT","MICROSOFT CORP","Information Technology","Equity",null,null,null,null,"594918104","US5949181045","2588173",null,"United States","NASDAQ","USD","1.00","USD"],
                ["BRKB","BERKSHIRE HATHAWAY INC CLASS B","Financials","Equity",null,null,null,null,"084670702","US0846707026","2073390",null,"United States","NYSE","USD","1.00","USD"],
                ["-","IGNORE","Information Technology","Equity"],
                ["CASH","USD CASH","Cash and/or Derivatives","Cash"]
            ]}
            """,
            as_of_date="2025-03-31",
        )

        self.assertEqual(str(as_of_date.date()), "2025-03-31")
        self.assertEqual(snapshot["symbol"].tolist(), ["BRKB", "MSFT"])
        self.assertIn("name", snapshot.columns)
        self.assertIn("isin", snapshot.columns)
        self.assertEqual(snapshot.loc[snapshot["symbol"] == "MSFT", "cusip"].iloc[0], "594918104")

    def test_resolve_ishares_holdings_snapshot_steps_back_when_requested_date_is_empty(self):
        _skip_if_missing_numeric_stack()
        import pandas as pd

        from us_equity_strategies.data_prep.russell_1000_history import (
            resolve_ishares_holdings_snapshot,
        )

        empty_snapshot = pd.DataFrame(columns=["symbol", "sector", "name"])
        available_snapshot = pd.DataFrame(
            [
                {"symbol": "MSFT", "sector": "Information Technology", "name": "MICROSOFT CORP"},
            ]
        )
        calls = []

        def fake_download(as_of_date, *, holdings_url_template):
            as_of = pd.Timestamp(as_of_date).normalize()
            calls.append(str(as_of.date()))
            if str(as_of.date()) in {"2025-03-31", "2025-03-30", "2025-03-29"}:
                return as_of, empty_snapshot
            if str(as_of.date()) == "2025-03-28":
                return as_of, available_snapshot
            raise AssertionError(f"unexpected as_of_date: {as_of}")

        resolved = resolve_ishares_holdings_snapshot(
            "2025-03-31",
            max_lookback_days=4,
            download_fn=fake_download,
        )

        self.assertEqual(str(resolved["requested_date"].date()), "2025-03-31")
        self.assertEqual(str(resolved["as_of_date"].date()), "2025-03-28")
        self.assertEqual(resolved["lookback_days"], 3)
        self.assertEqual(calls, ["2025-03-31", "2025-03-30", "2025-03-29", "2025-03-28"])

    def test_build_symbol_alias_candidates_prefers_latest_ticker_for_same_identifier(self):
        _skip_if_missing_numeric_stack()
        from us_equity_strategies.data_prep.russell_1000_history import (
            build_symbol_alias_candidates,
        )

        alias_map = build_symbol_alias_candidates(
            [
                (
                    "2024-01-31",
                    [
                        {
                            "symbol": "ABC",
                            "sector": "health",
                            "name": "Example Corp",
                            "isin": "US0000000001",
                            "cusip": "000000001",
                        }
                    ],
                ),
                (
                    "2024-06-28",
                    [
                        {
                            "symbol": "COR",
                            "sector": "health",
                            "name": "Example Corp",
                            "isin": "US0000000001",
                            "cusip": "000000001",
                        }
                    ],
                ),
            ]
        )

        self.assertEqual(alias_map["ABC"], ["COR", "ABC"])
        self.assertEqual(alias_map["COR"], ["COR", "ABC"])

    def test_download_price_history_normalizes_multi_symbol_download(self):
        _skip_if_missing_numeric_stack()
        import pandas as pd

        from us_equity_strategies.data_prep.yfinance_prices import download_price_history

        index = pd.to_datetime(["2024-01-02", "2024-01-03"])
        raw = pd.concat(
            {
                "Close": pd.DataFrame({"AAA": [100.0, 101.0], "BBB": [50.0, 51.0]}, index=index),
                "Volume": pd.DataFrame({"AAA": [1000, 1100], "BBB": [2000, 2100]}, index=index),
            },
            axis=1,
        )

        prices = download_price_history(
            ["AAA", "BBB"],
            start="2024-01-01",
            download_fn=lambda *args, **kwargs: raw,
            chunk_size=100,
        )

        self.assertEqual(len(prices), 4)
        self.assertEqual(prices.iloc[0]["symbol"], "AAA")
        self.assertEqual(prices.iloc[-1]["symbol"], "BBB")

    def test_download_price_history_maps_special_yfinance_aliases_back_to_original_symbol(self):
        _skip_if_missing_numeric_stack()
        import pandas as pd

        from us_equity_strategies.data_prep.yfinance_prices import download_price_history

        index = pd.to_datetime(["2024-01-02", "2024-01-03"])
        raw = pd.concat(
            {
                "Close": pd.DataFrame({"BRK-B": [300.0, 301.0]}, index=index),
                "Volume": pd.DataFrame({"BRK-B": [1000, 1100]}, index=index),
            },
            axis=1,
        )

        prices = download_price_history(
            ["BRKB"],
            start="2024-01-01",
            download_fn=lambda *args, **kwargs: raw,
            chunk_size=100,
        )

        self.assertEqual(prices["symbol"].tolist(), ["BRKB", "BRKB"])

    def test_download_price_history_uses_supplied_symbol_alias_candidates(self):
        _skip_if_missing_numeric_stack()
        import pandas as pd

        from us_equity_strategies.data_prep.yfinance_prices import download_price_history

        index = pd.to_datetime(["2024-01-02", "2024-01-03"])
        raw_by_symbol = {
            "COR": pd.concat(
                {
                    "Close": pd.DataFrame({"COR": [120.0, 121.0]}, index=index),
                    "Volume": pd.DataFrame({"COR": [1000, 1100]}, index=index),
                },
                axis=1,
            ),
        }

        def fake_download(symbols, *args, **kwargs):
            key = symbols[0] if isinstance(symbols, list) and len(symbols) == 1 else "__batch__"
            return raw_by_symbol.get(key, pd.DataFrame())

        prices = download_price_history(
            ["ABC"],
            start="2024-01-01",
            download_fn=fake_download,
            chunk_size=100,
            symbol_aliases={"ABC": ["COR", "ABC"]},
        )

        self.assertEqual(prices["symbol"].tolist(), ["ABC", "ABC"])
        self.assertEqual(prices["close"].tolist(), [120.0, 121.0])

    def test_fetch_price_history_cli_uses_universe_symbols(self):
        _skip_if_missing_numeric_stack()
        import pandas as pd

        from scripts.fetch_russell_1000_price_history import main

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            universe_path = tmp_path / "universe_history.csv"
            output_path = tmp_path / "prices.csv"
            pd.DataFrame(
                [
                    {"symbol": "AAA", "sector": "tech", "start_date": "2024-06-28", "end_date": ""},
                    {"symbol": "BBB", "sector": "health", "start_date": "2024-06-28", "end_date": ""},
                ]
            ).to_csv(universe_path, index=False)

            import scripts.fetch_russell_1000_price_history as module

            observed = {}

            def fake_download(symbols, *, start, end=None, chunk_size=100, symbol_aliases=None):
                observed["symbols"] = symbols
                observed["symbol_aliases"] = symbol_aliases
                return pd.DataFrame(
                    [
                        {"symbol": "AAA", "as_of": "2024-01-02", "close": 100.0, "volume": 1000},
                    ]
                )

            original = module.download_price_history
            module.download_price_history = fake_download
            try:
                exit_code = main(
                    [
                        "--universe-history",
                        str(universe_path),
                        "--output",
                        str(output_path),
                        "--start",
                        "2024-01-01",
                    ]
                )
            finally:
                module.download_price_history = original

            self.assertEqual(exit_code, 0)
            self.assertIn("SPY", observed["symbols"])
            self.assertIn("BOXX", observed["symbols"])
            self.assertEqual(observed["symbol_aliases"], {})
            self.assertTrue(output_path.exists())


if __name__ == "__main__":
    unittest.main()
