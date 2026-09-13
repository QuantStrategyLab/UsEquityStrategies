from __future__ import annotations

import pandas as pd
import pytest

from quant_platform_kit.common.models import PortfolioSnapshot
from quant_platform_kit.common.strategy_contracts import StrategyContext
from us_equity_strategies import get_runtime_enabled_profiles, get_strategy_entrypoint
from us_equity_strategies.v7_soxl_profile import (
    SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE,
    V7_FROZEN_RUNTIME_CONFIG,
)


def _context(
    *,
    runtime_config=None,
    high_volatility: bool = False,
    trend_price: float = 80.0,
) -> StrategyContext:
    volatility = 0.60 if high_volatility else 0.20
    snapshot = PortfolioSnapshot(
        as_of=pd.Timestamp("2026-08-26").to_pydatetime(),
        total_equity=100_000.0,
        buying_power=100_000.0,
        positions=(),
        metadata={"market_currency_cash": 100_000.0},
    )
    return StrategyContext(
        as_of="2026-08-26",
        market_data={
            "derived_indicators": {
                "soxl": {"price": 80.0, "ma_trend": 70.0},
                "soxx": {
                    "price": trend_price,
                    "ma_trend": 70.0,
                    "realized_volatility_10": volatility,
                    "realized_volatility_10_dynamic_threshold": 0.50,
                    "realized_volatility_10_dynamic_sample_count": 252.0,
                },
            }
        },
        portfolio=snapshot,
        runtime_config=dict(V7_FROZEN_RUNTIME_CONFIG if runtime_config is None else runtime_config),
    )


def _target_values(decision):
    return {position.symbol: position.target_value for position in decision.positions}


def test_v7_profile_is_findable_but_not_runtime_enabled() -> None:
    entrypoint = get_strategy_entrypoint(SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE)

    assert entrypoint.manifest.profile == SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE
    assert entrypoint.manifest.default_config == V7_FROZEN_RUNTIME_CONFIG
    assert SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE not in get_runtime_enabled_profiles()


def test_v7_entrypoint_applies_frozen_full_weights_after_cash_reserve() -> None:
    decision = get_strategy_entrypoint(SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE).evaluate(_context())

    assert _target_values(decision) == {
        "BOXX": 38_800.0,
        "SOXL": 33_950.0,
        "SOXX": 24_250.0,
    }
    assert decision.diagnostics["frozen_research_source"]["ues_revision"].startswith("07b164")
    assert decision.diagnostics["signal_effective_after_trading_days"] == 1
    assert decision.diagnostics["execution_annotations"]["signal_effective_after_trading_days"] == 1


def test_v7_entrypoint_redirects_soxl_to_boxx_in_high_volatility() -> None:
    decision = get_strategy_entrypoint(SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE).evaluate(
        _context(high_volatility=True)
    )

    assert _target_values(decision) == {
        "BOXX": 72_750.0,
        "SOXL": 0.0,
        "SOXX": 24_250.0,
    }


def test_v7_entrypoint_applies_frozen_mid_weights_after_cash_reserve() -> None:
    decision = get_strategy_entrypoint(SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE).evaluate(
        _context(trend_price=75.0)
    )

    assert _target_values(decision) == {
        "BOXX": 48_500.0,
        "SOXL": 24_250.0,
        "SOXX": 24_250.0,
    }


def test_v7_entrypoint_rejects_material_runtime_overrides() -> None:
    overridden = dict(V7_FROZEN_RUNTIME_CONFIG)
    overridden["blend_gate_soxl_weight"] = 0.70

    with pytest.raises(ValueError, match="frozen V7 runtime config"):
        get_strategy_entrypoint(SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE).evaluate(
            _context(runtime_config=overridden)
        )

    bad_timing = dict(V7_FROZEN_RUNTIME_CONFIG)
    bad_timing["signal_effective_after_trading_days"] = 0
    with pytest.raises(ValueError, match="signal timing"):
        get_strategy_entrypoint(SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE).evaluate(
            _context(runtime_config=bad_timing)
        )
