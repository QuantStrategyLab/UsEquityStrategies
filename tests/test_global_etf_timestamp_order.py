"""Synthetic point-in-time ordering regressions for the actual strategy function."""

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

_SOURCE = (
    Path(__file__).resolve().parents[1]
    / "src/us_equity_strategies/strategies/global_etf_rotation.py"
)
_SPEC = importlib.util.spec_from_file_location("global_etf_timestamp_test", _SOURCE)
strategy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(strategy)


def _weights(rows, monkeypatch):
    monkeypatch.setattr(strategy, "_snapshot_rebalance_day", lambda *_a, **_kw: True)
    weights, *_ = strategy.compute_signals_from_feature_snapshot(
        pd.DataFrame(rows),
        (),
        as_of_date="2026-03-31T20:00:00Z",
        ranking_pool=["AAA", "BBB"],
        canary_assets=[],
        top_n=1,
        translator=lambda key, **_kwargs: key,
    )
    return weights


def _rows(column, later):
    common = {
        "as_of": "2026-03-30T00:00:00+00:00",
        "available_at": "2026-03-30T00:00:00+00:00",
        "received_at": "2026-03-30T00:00:00+00:00",
        "revised_at": "2026-03-30T00:00:00+00:00",
        "role": "ranking_pool_etf",
        "close": 100,
        "sma_pass": True,
        "eligible": True,
        "vol_126": 0.2,
    }
    older = "2026-03-30T13:00:00+00:00"
    return [
        {**common, "symbol": "AAA", "momentum_13612w": 0.1, column: older},
        {**common, "symbol": "AAA", "momentum_13612w": 1.0, column: later},
        {**common, "symbol": "BBB", "momentum_13612w": 0.5, column: older},
    ]


@pytest.mark.parametrize("column", ["as_of", "revised_at", "available_at", "received_at"])
def test_snapshot_order_compares_utc_instants(column, monkeypatch):
    # 09:00 -05:00 is 14:00 UTC, later than 13:00 +00:00.
    assert _weights(_rows(column, "2026-03-30T09:00:00-05:00"), monkeypatch) == {
        "AAA": 1.0
    }


@pytest.mark.parametrize("column", ["as_of", "revised_at", "available_at", "received_at"])
def test_snapshot_rejects_future_visibility_even_with_offset(column, monkeypatch):
    # 16:00 -05:00 is 21:00 UTC, beyond the 20:00 cutoff.
    assert _weights(_rows(column, "2026-03-31T16:00:00-05:00"), monkeypatch) == {
        "BBB": 1.0
    }
