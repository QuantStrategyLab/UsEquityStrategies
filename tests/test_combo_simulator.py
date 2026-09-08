from __future__ import annotations

import unittest

import pandas as pd

from us_equity_strategies.backtest.combo_simulator import (
    DEFAULT_DCA_WEIGHT,
    DEFAULT_GLOBAL_WEIGHT,
    DEFAULT_RUSSELL_WEIGHT,
    UsComboBacktestConfig,
    _dynamic_exposure_multiplier,
    run_combo_backtest,
)
from us_equity_strategies.strategies.global_etf_rotation import (
    DEFAULT_MIN_HISTORY_DAYS,
    build_target_weights,
    extract_managed_symbols_universe,
)


def _fixture_history(*, days: int = 320) -> pd.DataFrame:
    rows = []
    symbols = list(dict.fromkeys([*extract_managed_symbols_universe(), "QQQ", "AAPL", "MSFT", "NVDA"]))
    for day in pd.bdate_range("2022-01-03", periods=days):
        for idx, symbol in enumerate(symbols):
            rows.append({"date": day, "symbol": symbol, "close": 20.0 + idx + day.day / 100.0})
    return pd.DataFrame(rows)


class ComboSimulatorTests(unittest.TestCase):
    def test_active_proxy_rejects_missing_prices_instead_of_zero_returns(self) -> None:
        dates = pd.to_datetime(["2024-01-31", "2024-02-01", "2024-02-02"])
        for sleeve in ("dca", "russell"):
            for mega_cap in (False, True):
                with self.subTest(sleeve=sleeve, mega_cap=mega_cap):
                    symbols = ["QQQ", "SPY"] + (["AAPL", "MSFT", "NVDA"] if mega_cap else [])
                    missing_symbol = "AAPL" if sleeve == "russell" and mega_cap else "QQQ"
                    history = pd.DataFrame(
                        {"date": day, "symbol": symbol, "close": float("nan")
                         if symbol == missing_symbol and day == dates[1] else 100.0}
                        for day in dates for symbol in symbols
                    )
                    with self.assertRaisesRegex(
                        ValueError,
                        r"proxy prices|held assets require positive finite",
                    ):
                        run_combo_backtest(
                            history, lambda frame: ({}, {}),
                            combo_config=UsComboBacktestConfig(
                                global_weight=0.0, russell_weight=float(sleeve == "russell"),
                                dca_weight=float(sleeve == "dca"), min_history_days=1, combo_mode="static",
                            ),
                        )

    def test_inactive_proxy_missing_prices_do_not_block_cash(self) -> None:
        history = pd.DataFrame(
            {"date": day, "symbol": symbol, "close": float("nan") if symbol == "QQQ" else 100.0}
            for day in pd.to_datetime(["2024-01-31", "2024-02-01", "2024-02-02"])
            for symbol in ("QQQ", "SPY")
        )
        for mode in ("static", "dynamic"):
            with self.subTest(mode=mode):
                result = run_combo_backtest(
                    history, lambda frame: ({}, {}),
                    combo_config=UsComboBacktestConfig(
                        global_weight=0.0, russell_weight=float(mode == "dynamic"), dca_weight=0.0,
                        min_history_days=1, combo_mode=mode, spy_sma_period=1, dynamic_reduction_pct=1.0,
                    ),
                )
                self.assertEqual(result.daily_returns.tolist(), [0.0] * 3)

    def test_default_weights_match_research_script(self) -> None:
        self.assertEqual(DEFAULT_GLOBAL_WEIGHT, 0.50)
        self.assertEqual(DEFAULT_RUSSELL_WEIGHT, 0.30)
        self.assertEqual(DEFAULT_DCA_WEIGHT, 0.20)

    def test_dynamic_multiplier_risk_on(self) -> None:
        index = pd.bdate_range("2020-01-01", periods=260)
        close = pd.DataFrame({"SPY": range(100, 360)}, index=index)
        mult = _dynamic_exposure_multiplier(
            close,
            pd.Timestamp(index[-1]),
            spy_sma_period=200,
            reduction_pct=0.30,
        )
        self.assertEqual(mult, 1.0)

    def test_run_combo_backtest_returns_metrics(self) -> None:
        history = _fixture_history()
        result = run_combo_backtest(
            history,
            build_target_weights,
            combo_config=UsComboBacktestConfig(combo_mode="static"),
            strategy_kwargs={"min_history_days": DEFAULT_MIN_HISTORY_DAYS},
        )
        self.assertGreater(int(result.metrics["days"]), 0)
        self.assertIn("sharpe_ratio", result.metrics)

    def test_no_rebalance_month_preserves_funded_round_trip(self) -> None:
        # Within one calendar month the old free daily reweight produced 1.125.
        dates = pd.to_datetime(["2024-01-08", "2024-01-09", "2024-01-10"])
        history = pd.DataFrame(
            {"date": day, "symbol": symbol, "close": price}
            for day, qqq in zip(dates, [100.0, 200.0, 100.0])
            for symbol, price in (("QQQ", qqq), ("SPY", 100.0))
        )
        terminals = []
        for cost in (0.0, 100.0):
            result = run_combo_backtest(
                history,
                lambda frame: ({}, {}),
                combo_config=UsComboBacktestConfig(
                    global_weight=0.5,
                    russell_weight=0.0,
                    dca_weight=0.5,
                    combo_mode="static",
                    min_history_days=1,
                    cost_bps=cost,
                    rebalance_frequency="monthly",
                ),
            )
            terminals.append(float((1.0 + result.daily_returns).prod()))
        self.assertAlmostEqual(terminals[0], 1.0)
        self.assertAlmostEqual(terminals[1], 1.0)

    def test_holdings_drift_without_rebalance_after_entry(self) -> None:
        dates = pd.to_datetime(
            ["2023-12-29", "2024-01-02", "2024-01-03", "2024-01-04"]
        )
        history = pd.DataFrame(
            {"date": day, "symbol": symbol, "close": price}
            for day, qqq in zip(dates, [100.0, 100.0, 200.0, 100.0])
            for symbol, price in (("QQQ", qqq), ("SPY", 100.0))
        )
        result = run_combo_backtest(
            history,
            lambda frame: ({}, {}),
            combo_config=UsComboBacktestConfig(
                global_weight=0.5,
                russell_weight=0.0,
                dca_weight=0.5,
                combo_mode="static",
                min_history_days=1,
                cost_bps=0.0,
                rebalance_frequency="monthly",
            ),
        )
        # Dec month-end signals; Jan 2 fills 50% QQQ; round-trip without another trade.
        self.assertAlmostEqual(float((1.0 + result.daily_returns).prod()), 1.0)

    def test_rebalance_cost_is_monotonic_and_consumed(self) -> None:
        dates = pd.bdate_range("2023-12-01", periods=45)
        history = pd.DataFrame(
            {
                "date": day,
                "symbol": symbol,
                "close": 100.0 + (i % 7),
            }
            for i, day in enumerate(dates)
            for symbol in ("QQQ", "SPY", "AAPL", "MSFT", "NVDA")
        )
        zero = run_combo_backtest(
            history,
            lambda frame: ({}, {}),
            combo_config=UsComboBacktestConfig(
                global_weight=0.0,
                russell_weight=0.5,
                dca_weight=0.5,
                combo_mode="static",
                min_history_days=1,
                cost_bps=0.0,
                rebalance_frequency="monthly",
            ),
        )
        costly = run_combo_backtest(
            history,
            lambda frame: ({}, {}),
            combo_config=UsComboBacktestConfig(
                global_weight=0.0,
                russell_weight=0.5,
                dca_weight=0.5,
                combo_mode="static",
                min_history_days=1,
                cost_bps=100.0,
                rebalance_frequency="monthly",
            ),
        )
        zero_terminal = float((1.0 + zero.daily_returns).prod())
        costly_terminal = float((1.0 + costly.daily_returns).prod())
        self.assertGreater(zero_terminal, costly_terminal)


if __name__ == "__main__":
    unittest.main()
