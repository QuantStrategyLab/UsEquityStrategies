from __future__ import annotations

import unittest

import pandas as pd

from us_equity_strategies.backtest.orchestrator_research import (
    run_combo_profile_backtest,
    run_etf_rotation_profile_backtest,
)
from us_equity_strategies.strategies.global_etf_rotation import (
    DEFAULT_MIN_HISTORY_DAYS,
    extract_managed_symbols_universe,
    PROFILE_NAME,
)
from us_equity_strategies.strategies.us_equity_combo import PROFILE_NAME as US_EQUITY_COMBO_PROFILE


def _fixture_history(*, days: int = 400) -> pd.DataFrame:
    rows = []
    symbols = list(dict.fromkeys([*extract_managed_symbols_universe(), "QQQ", "AAPL", "MSFT"]))
    for day in pd.bdate_range("2022-01-03", periods=days):
        for symbol in symbols:
            rows.append({"date": day, "symbol": symbol, "close": 10.0 + hash(symbol) % 5})
    return pd.DataFrame(rows)


class OrchestratorResearchTests(unittest.TestCase):
    def test_run_etf_rotation_profile_backtest_with_fixture_history(self) -> None:
        history = _fixture_history()
        payload = run_etf_rotation_profile_backtest(
            PROFILE_NAME,
            market_history=history,
            params={"min_history_days": DEFAULT_MIN_HISTORY_DAYS},
        )
        self.assertEqual(payload["profile"], PROFILE_NAME)
        self.assertEqual(payload["source"], "UsEtfRotationBacktestRunner")
        self.assertGreater(payload["metrics"]["days"], 0)

    def test_run_combo_profile_backtest_with_fixture_history(self) -> None:
        history = _fixture_history()
        payload = run_combo_profile_backtest(
            US_EQUITY_COMBO_PROFILE,
            market_history=history,
            params={"min_history_days": DEFAULT_MIN_HISTORY_DAYS, "combo_mode": "static"},
        )
        self.assertEqual(payload["profile"], US_EQUITY_COMBO_PROFILE)
        self.assertEqual(payload["source"], "UsEquityComboBacktestRunner")
        self.assertGreater(payload["metrics"]["days"], 0)


if __name__ == "__main__":
    unittest.main()
