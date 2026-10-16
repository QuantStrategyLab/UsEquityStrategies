"""Invented fixtures only; no market data, optional artifacts or broker path."""
from datetime import date, timedelta

import pytest

from us_equity_strategies.research.rs04_b3_live import default_config
from us_equity_strategies.research.rs04_b3_runner import Session
from us_equity_strategies.research.soxl_v7_derisk import (
    paired_synthetic_replay,
    variant_config,
)


@pytest.mark.parametrize("cost_multiplier", [1., 2.])
def test_same_native_config_without_optional_plugins_preserves_paired_books(cost_multiplier):
    # This comparison isolates the ledger: both rules receive the same native
    # derisk configuration. It does not assert equality to the legacy config.
    config = variant_config(default_config("SOXL"))
    assert variant_config(config) == config
    indicators = {s: {
        "price": 120., "ma_trend": 100., "ma20": 110., "ma20_slope": 1.,
        "rsi14": 50., "bb_upper": 150., "realized_volatility_10": .1,
    } for s in ("soxl", "soxx")}
    symbols = ("SOXL", "SOXX", "BOXX", "SCHD", "DGRO", "SGOV", "SPYI", "QQQI")
    rows = [Session(
        session=date(2001, 1, 1) + timedelta(days=i),
        prices={s: 100. for s in symbols}, features=None,
        live_indicators=indicators, context={},
    ) for i in range(6)]
    points = paired_synthetic_replay(rows, config, cost_multiplier=cost_multiplier)
    for point in points:
        # Includes NAV, cash, holdings weights, net return and each fee leg.
        # Keep this a failing regression until strict_rebalance preserves the
        # identical-input no-op case without weakening post-fill invariants.
        assert point["baseline"] == point["candidate"]
