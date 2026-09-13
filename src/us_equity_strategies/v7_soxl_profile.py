"""Frozen, research-only identity for the SOXL/SOXX/BOXX V7 candidate."""

from __future__ import annotations

from collections.abc import Mapping

SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE = (
    "soxl_soxx_core_only_p2_v7_longterm_compounding_cash_reserve"
)
V7_CONFIG_SHA256 = "843ab4e93e81985c2b3becc61a2f0b971508ccf25afa59acf402e75f574514d1"
V7_UES_REVISION = "07b164d95f2ab4d4c54fd993f6f2040bd207d664"
V7_QPK_REVISION = "f30e7b1910df8da22fdcedc347ab847df5adcd76"
V7_REQUIRED_INPUTS = frozenset({"derived_indicators", "portfolio_snapshot"})
V7_SIGNAL_EFFECTIVE_AFTER_TRADING_DAYS = 1

# This is deliberately a plain mapping so JSON-loaded candidate config can be
# compared after the wrapper normalizes JSON arrays to tuples.  It is copied at
# each boundary; callers cannot mutate the frozen module value.
V7_FROZEN_RUNTIME_CONFIG: dict[str, object] = {
    "attack_allocation_mode": "soxx_gate_tiered_blend",
    "blend_gate_active_soxx_weight": 0.25,
    "blend_gate_bollinger_cap_enabled": True,
    "blend_gate_defensive_soxx_weight": 0.15,
    "blend_gate_dynamic_rsi_threshold_enabled": True,
    "blend_gate_mid_soxl_weight": 0.25,
    "blend_gate_overlay_stack_triggers": True,
    "blend_gate_rsi_cap_enabled": True,
    "blend_gate_rsi_threshold": 70.0,
    "blend_gate_soxl_weight": 0.35,
    "blend_gate_trend_source": "SOXX",
    "blend_gate_volatility_delever_dynamic_cap": 0.75,
    "blend_gate_volatility_delever_dynamic_floor": 0.50,
    "blend_gate_volatility_delever_dynamic_lookback": 252,
    "blend_gate_volatility_delever_dynamic_min_periods": 126,
    "blend_gate_volatility_delever_dynamic_percentile": 0.95,
    "blend_gate_volatility_delever_enabled": True,
    "blend_gate_volatility_delever_max_retention_ratio": 0.50,
    "blend_gate_volatility_delever_redirect_symbol": "BOXX",
    "blend_gate_volatility_delever_retention_context_required": False,
    "blend_gate_volatility_delever_retention_mode": "none",
    "blend_gate_volatility_delever_retention_ratio": 0.0,
    "blend_gate_volatility_delever_retention_policy": "soxl_step_rebound_0.25_0.50",
    "blend_gate_volatility_delever_symbol": "SOXX",
    "blend_gate_volatility_delever_threshold": 0.55,
    "blend_gate_volatility_delever_threshold_mode": "rolling_percentile",
    "blend_gate_volatility_delever_window": 10,
    "cash_reserve_floor_usd": 0.0,
    "cash_reserve_ratio": 0.03,
    "income_layer_enabled": False,
    "managed_symbols": ("SOXL", "SOXX", "BOXX"),
    "market_regime_control_apply_risk_off": False,
    "market_regime_control_apply_risk_reduced": False,
    "market_regime_control_enabled": False,
    "min_trade_floor": 100.0,
    "min_trade_ratio": 0.01,
    "option_growth_overlay_enabled": False,
    "option_income_overlay_enabled": False,
    "option_overlay_enabled": False,
    "rebalance_threshold_ratio": 0.01,
    "trend_entry_buffer": 0.08,
    "trend_exit_buffer": 0.02,
    "trend_ma_window": 140,
    "trend_mid_buffer": 0.06,
}

def copy_v7_runtime_config() -> dict[str, object]:
    return dict(V7_FROZEN_RUNTIME_CONFIG)


def canonicalize_v7_config(value: Mapping[str, object]) -> dict[str, object]:
    """Normalize JSON arrays for exact frozen-config comparison."""

    def normalize(item: object) -> object:
        if isinstance(item, Mapping):
            return {str(key): normalize(raw) for key, raw in item.items()}
        if isinstance(item, (list, tuple)):
            return tuple(normalize(raw) for raw in item)
        return item

    return normalize(value)  # type: ignore[return-value]


__all__ = [
    "SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE",
    "V7_CONFIG_SHA256",
    "V7_UES_REVISION",
    "V7_QPK_REVISION",
    "V7_REQUIRED_INPUTS",
    "V7_SIGNAL_EFFECTIVE_AFTER_TRADING_DAYS",
    "V7_FROZEN_RUNTIME_CONFIG",
    "canonicalize_v7_config",
    "copy_v7_runtime_config",
]
