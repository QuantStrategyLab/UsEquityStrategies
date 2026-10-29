"""Synthetic regression for standalone income and cash accounting."""

import pandas as pd
from quant_platform_kit.common.models import PortfolioSnapshot, Position

from us_equity_strategies.manifests import (
    soxl_soxx_trend_income_manifest,
    tqqq_growth_income_manifest,
)
from us_equity_strategies.strategies import soxl_soxx_trend_income, tqqq_growth_income


def _config(manifest, excluded):
    return {
        key: value
        for key, value in manifest.default_config.items()
        if key not in excluded and not key.startswith("option_")
    }


def test_tqqq_standalone_income_lock_and_cash():
    history = [{"close": 300.0 + day * 0.4, "high": 301.0 + day * 0.4,
                "low": 299.0 + day * 0.4} for day in range(260)]
    snapshot = PortfolioSnapshot(
        as_of=pd.Timestamp("2026-04-06").to_pydatetime(),
        total_equity=120000.0,
        buying_power=20000.0,
        positions=(Position(symbol="TQQQ", quantity=10, market_value=8000.0),
                   Position(symbol="BOXX", quantity=20, market_value=4000.0),
                   Position(symbol="SPYI", quantity=30, market_value=1500.0),
                   Position(symbol="QQQI", quantity=30, market_value=1700.0)),
        metadata={"account_hash": "demo"},
    )
    plan = tqqq_growth_income.build_rebalance_plan(
        history, snapshot, signal_text_fn=str, translator=lambda key, **kwargs: key,
        **_config(tqqq_growth_income_manifest,
                  {"benchmark_symbol", "managed_symbols", "execution_cash_reserve_ratio", "ai_extensions"}),
    )
    targets = plan["target_values"]
    assert targets["SPYI"] == 1500.0
    assert targets["QQQI"] == 1700.0
    assert targets["QQQM"] == (120000.0 - 3200.0) * 0.45
    assert targets["TQQQ"] == (120000.0 - 3200.0) * 0.45
    assert plan["reserved"] >= 0.0
    assert plan["investable_buying_power"] <= snapshot.buying_power


def test_soxl_standalone_income_lock_and_cash():
    indicators = {
        "soxl": {"price": 80.0, "ma_trend": 75.0},
        "soxx": {"price": 80.0, "ma_trend": 75.0, "realized_volatility_10": 0.20,
                 "realized_volatility_10_dynamic_threshold": 0.50,
                 "realized_volatility_10_dynamic_sample_count": 252.0},
    }
    state = {"available_cash": 43000.0,
             "market_values": {"SOXL": 0.0, "SOXX": 0.0, "BOXX": 5000.0, "QQQI": 1000.0, "SPYI": 1000.0},
             "quantities": {"SOXL": 0, "SOXX": 0, "BOXX": 50, "QQQI": 10, "SPYI": 10},
             "sellable_quantities": {"SOXL": 0, "SOXX": 0, "BOXX": 50, "QQQI": 10, "SPYI": 10},
             "total_strategy_equity": 50000.0}
    plan = soxl_soxx_trend_income.build_rebalance_plan(
        indicators, state, translator=lambda key, **kwargs: key,
        **_config(soxl_soxx_trend_income_manifest, {"managed_symbols"}),
    )
    assert plan["targets"]["QQQI"] == 1000.0
    assert plan["targets"]["SPYI"] == 1000.0
    assert plan["income_layer_value"] == 2000.0
    assert plan["deployable_core_equity"] == 50000.0 - 2000.0 - plan["reserved_cash"]
    assert plan["investable_cash"] <= state["available_cash"]
