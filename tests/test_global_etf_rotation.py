from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from us_equity_strategies.strategies import global_etf_rotation


def _history(symbol: str, *, volatility: float) -> pd.Series:
    dates = pd.bdate_range(end="2026-03-31", periods=320)
    returns = np.full(len(dates), 0.003)
    returns[::2] += volatility
    returns[1::2] -= volatility
    values = 100.0 * np.cumprod(1.0 + returns)
    return pd.Series(values, index=dates, name=symbol)


class GlobalEtfRotationConfidenceTests(unittest.TestCase):
    def _run_confidence_case(self, *, top1_volatility: float) -> dict[str, float]:
        histories = {
            "AAA": _history("AAA", volatility=top1_volatility),
            "BBB": _history("BBB", volatility=0.004),
            "CCC": _history("CCC", volatility=0.003),
            "SPY": _history("SPY", volatility=0.002),
            "EFA": _history("EFA", volatility=0.002),
            "EEM": _history("EEM", volatility=0.002),
            "AGG": _history("AGG", volatility=0.001),
            "BIL": _history("BIL", volatility=0.001),
        }
        scores = {
            "AAA": 0.20,
            "BBB": 0.10,
            "CCC": 0.00,
            "SPY": 0.01,
            "EFA": 0.01,
            "EEM": 0.01,
            "AGG": 0.01,
        }

        def score(series):
            return scores.get(series.name, 0.0)

        with patch.object(global_etf_rotation, "compute_13612w_momentum", side_effect=score):
            weights, _signal, is_emergency, _canary = global_etf_rotation.compute_signals(
                None,
                current_holdings=(),
                get_historical_close=lambda _ib, ticker: histories[ticker],
                as_of_date="2026-03-31",
                ranking_pool=("AAA", "BBB", "CCC"),
                canary_assets=("SPY", "EFA", "EEM", "AGG"),
                safe_haven="BIL",
                translator=lambda key, **kwargs: key,
                pacing_sec=0.0,
                sma_period=200,
                confidence_weighting_enabled=True,
                confidence_threshold=1.0,
                confidence_top1_weight=0.75,
                confidence_volatility_gate_enabled=True,
                confidence_volatility_window=126,
                confidence_volatility_max_ratio=1.3,
            )

        self.assertFalse(is_emergency)
        return weights

    def test_confidence_weighting_concentrates_when_relative_volatility_passes(self) -> None:
        weights = self._run_confidence_case(top1_volatility=0.003)

        self.assertEqual(weights, {"AAA": 0.75, "BBB": 0.25})

    def test_confidence_weighting_stays_equal_weight_when_top1_volatility_is_too_high(self) -> None:
        weights = self._run_confidence_case(top1_volatility=0.025)

        self.assertEqual(weights, {"AAA": 0.5, "BBB": 0.5})

    def test_default_global_rotation_keeps_equal_weighting(self) -> None:
        histories = {
            "AAA": _history("AAA", volatility=0.003),
            "BBB": _history("BBB", volatility=0.004),
            "SPY": _history("SPY", volatility=0.002),
            "EFA": _history("EFA", volatility=0.002),
            "EEM": _history("EEM", volatility=0.002),
            "AGG": _history("AGG", volatility=0.001),
            "BIL": _history("BIL", volatility=0.001),
        }

        with patch.object(
            global_etf_rotation,
            "compute_13612w_momentum",
            side_effect=lambda series: 0.20 if series.name == "AAA" else 0.10,
        ):
            weights, _signal, _is_emergency, _canary = global_etf_rotation.compute_signals(
                None,
                current_holdings=(),
                get_historical_close=lambda _ib, ticker: histories[ticker],
                as_of_date="2026-03-31",
                ranking_pool=("AAA", "BBB"),
                canary_assets=("SPY", "EFA", "EEM", "AGG"),
                safe_haven="BIL",
                translator=lambda key, **kwargs: key,
                pacing_sec=0.0,
                sma_period=200,
            )

        self.assertEqual(weights, {"AAA": 0.5, "BBB": 0.5})

    def test_feature_snapshot_runtime_uses_snapshot_universe_and_csv_booleans(self) -> None:
        feature_snapshot = pd.DataFrame(
            [
                {
                    "as_of": "2026-03-31",
                    "symbol": "AAA",
                    "role": "ranking_pool_etf",
                    "close": 100.0,
                    "momentum_13612w": 0.30,
                    "score": 0.30,
                    "sma_pass": "true",
                    "eligible": "true",
                    "vol_126": 0.20,
                },
                {
                    "as_of": "2026-03-31",
                    "symbol": "BBB",
                    "role": "ranking_pool_etf",
                    "close": 100.0,
                    "momentum_13612w": 0.20,
                    "score": 0.20,
                    "sma_pass": "false",
                    "eligible": "true",
                    "vol_126": 0.10,
                },
                {
                    "as_of": "2026-03-31",
                    "symbol": "SPY",
                    "role": "canary_asset",
                    "close": 500.0,
                    "momentum_13612w": 0.05,
                    "score": 0.05,
                    "sma_pass": "true",
                    "eligible": "false",
                    "vol_126": 0.15,
                },
                {
                    "as_of": "2026-03-31",
                    "symbol": "BIL",
                    "role": "safe_haven",
                    "close": 91.0,
                    "momentum_13612w": 0.01,
                    "score": 0.01,
                    "sma_pass": "true",
                    "eligible": "false",
                    "vol_126": 0.01,
                },
            ]
        )

        weights, _signal, is_emergency, canary = global_etf_rotation.compute_signals_from_feature_snapshot(
            feature_snapshot,
            current_holdings=(),
            as_of_date="2026-03-31",
            ranking_pool=("IGNORED",),
            canary_assets=("IGNORED",),
            safe_haven="BIL",
            top_n=2,
            translator=lambda key, **kwargs: key,
        )

        self.assertFalse(is_emergency)
        self.assertEqual(weights, {"AAA": 0.5, "BIL": 0.5})
        self.assertEqual(canary, "SPY:✅(0.050)")


if __name__ == "__main__":
    unittest.main()
