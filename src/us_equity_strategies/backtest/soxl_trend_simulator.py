"""Core-only daily SOXL/SOXX/BOXX replay for lifecycle baselines.

This is a lifecycle proxy for Drift Check baselines. It freezes income-layer and
market-regime apply off, and calls the shared ``build_rebalance_plan`` daily.
It is not a live-fidelity claim for the full production sleeve.
"""

from __future__ import annotations

import inspect
import math
from dataclasses import dataclass, field
from typing import Any, Mapping

import numpy as np
import pandas as pd

from us_equity_strategies.backtest.etf_rotation_simulator import (
    UsRotationBacktestResult,
    _rebalance_holdings,
    build_close_matrix,
    compute_backtest_metrics,
)
from us_equity_strategies.manifests import soxl_soxx_trend_income_manifest
from us_equity_strategies.strategies.soxl_soxx_trend_income import (
    CORE_ASSETS,
    build_rebalance_plan,
)

PROFILE_NAME = "soxl_soxx_trend_income"
DEFAULT_MIN_HISTORY_DAYS = 260
DEFAULT_COST_BPS = 5.0
DEFAULT_RSI_WINDOW = 14
DEFAULT_BOLLINGER_WINDOW = 20
DEFAULT_BOLLINGER_STD = 2.0
CALENDAR_BENCHMARK_SYMBOL = "SPY"

# Lifecycle proxy: core sleeve only; no income overlay / regime apply.
_LIFECYCLE_CORE_ONLY_OVERRIDES: dict[str, object] = {
    "income_layer_enabled": False,
    "income_layer_start_usd": 1e18,
    "income_layer_max_ratio": 0.0,
    "market_regime_control_enabled": False,
    "market_regime_control_apply_risk_reduced": False,
    "market_regime_control_apply_risk_off": False,
    "blend_gate_volatility_delever_retention_mode": "none",
    "blend_gate_volatility_delever_retention_ratio": 0.0,
    "blend_gate_volatility_delever_retention_context_required": False,
}


@dataclass(frozen=True)
class SoxlCoreOnlyBacktestConfig:
    min_history_days: int = DEFAULT_MIN_HISTORY_DAYS
    cost_bps: float = DEFAULT_COST_BPS


@dataclass
class SoxlCoreOnlyBacktestResult(UsRotationBacktestResult):
    """Alias result type for SOXL lifecycle runner consumers."""

    metrics: dict[str, float | int] = field(default_factory=dict)


def _build_rsi(close: pd.Series, window: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / int(window), min_periods=int(window), adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / int(window), min_periods=int(window), adjust=False).mean()
    relative_strength = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + relative_strength))
    rsi = rsi.where(avg_loss.ne(0.0), 100.0)
    rsi = rsi.where(avg_gain.ne(0.0), 0.0)
    return rsi


def build_indicator_history(
    close_matrix: pd.DataFrame,
    *,
    trend_ma_window: int,
    volatility_window: int,
    volatility_lookback: int,
    volatility_percentile: float,
    volatility_min_periods: int,
    volatility_floor: float,
    volatility_cap: float,
    rsi_window: int = DEFAULT_RSI_WINDOW,
    bollinger_window: int = DEFAULT_BOLLINGER_WINDOW,
    bollinger_std: float = DEFAULT_BOLLINGER_STD,
) -> dict[str, pd.DataFrame]:
    """Port of the UESP indicator builder for SOXL/SOXX core signals."""
    indicators: dict[str, pd.DataFrame] = {}
    for symbol in ("SOXL", "SOXX"):
        if symbol not in close_matrix.columns:
            continue
        close = pd.to_numeric(close_matrix[symbol], errors="coerce")
        history = pd.DataFrame(
            {
                "price": close,
                "ma_trend": close.rolling(int(trend_ma_window)).mean(),
            },
            index=close.index,
        )
        if symbol == "SOXL":
            history["ma10"] = close.rolling(10).mean()
            history["ma30"] = close.rolling(30).mean()
        if symbol == "SOXX":
            ma20 = close.rolling(20).mean()
            daily_returns = close.pct_change(fill_method=None)
            realized_volatility_10 = daily_returns.rolling(10).std() * np.sqrt(252)
            realized_volatility_20 = daily_returns.rolling(20).std() * np.sqrt(252)
            realized_by_window = {10: realized_volatility_10, 20: realized_volatility_20}
            volatility_metric = realized_by_window.get(int(volatility_window))
            if volatility_metric is None:
                volatility_metric = daily_returns.rolling(int(volatility_window)).std() * np.sqrt(252)
            min_periods = min(int(volatility_lookback), int(volatility_min_periods))
            dynamic_threshold = (
                volatility_metric.rolling(int(volatility_lookback), min_periods=min_periods)
                .quantile(float(volatility_percentile))
                .clip(lower=float(volatility_floor), upper=float(volatility_cap))
            )
            sample_count = volatility_metric.rolling(int(volatility_lookback), min_periods=1).count()
            history["ma20"] = ma20
            history["ma20_slope"] = ma20.diff()
            history["realized_volatility"] = realized_volatility_20
            history["realized_volatility_10"] = realized_volatility_10
            history["realized_volatility_20"] = realized_volatility_20
            key = f"realized_volatility_{int(volatility_window)}"
            history[f"{key}_dynamic_threshold"] = dynamic_threshold
            history[f"{key}_dynamic_sample_count"] = sample_count
            history[f"{key}_dynamic_lookback"] = int(volatility_lookback)
            history[f"{key}_dynamic_percentile"] = float(volatility_percentile)
            history[f"{key}_dynamic_min_periods"] = int(volatility_min_periods)
            history[f"{key}_dynamic_floor"] = float(volatility_floor)
            history[f"{key}_dynamic_cap"] = float(volatility_cap)
            history["realized_volatility_dynamic_threshold"] = dynamic_threshold
            history["realized_volatility_dynamic_sample_count"] = sample_count
            rsi = _build_rsi(close, int(rsi_window))
            history["rsi14_raw"] = rsi
            history["rsi14"] = rsi
            bollinger_mid = close.rolling(int(bollinger_window)).mean()
            bollinger_stddev = close.rolling(int(bollinger_window)).std(ddof=0)
            history["bb_mid"] = bollinger_mid
            history["bb_upper"] = bollinger_mid + (float(bollinger_std) * bollinger_stddev)
            history["bb_lower"] = bollinger_mid - (float(bollinger_std) * bollinger_stddev)
        indicators[symbol.lower()] = history
    return indicators


def _strategy_kwargs(overrides: Mapping[str, object] | None = None) -> dict[str, object]:
    config = dict(soxl_soxx_trend_income_manifest.default_config)
    config.update(_LIFECYCLE_CORE_ONLY_OVERRIDES)
    if overrides:
        config.update({key: value for key, value in overrides.items() if value is not None})
    supported = set(inspect.signature(build_rebalance_plan).parameters)
    supported.discard("indicators")
    supported.discard("account_state")
    supported.discard("translator")
    return {key: config[key] for key in supported if key in config}


def _indicator_snapshot_at(
    indicator_history: Mapping[str, pd.DataFrame],
    as_of: pd.Timestamp,
) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for symbol, frame in indicator_history.items():
        if as_of not in frame.index:
            continue
        row = frame.loc[as_of]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[-1]
        payload = {key: float(value) for key, value in row.items() if pd.notna(value)}
        result[symbol] = payload
    return result


def _account_state_from_weights(
    *,
    weights: Mapping[str, float],
    equity: float,
    close_prices: Mapping[str, float],
) -> dict[str, object]:
    market_values = {symbol: float(equity) * float(weights.get(symbol, 0.0)) for symbol in CORE_ASSETS}
    quantities: dict[str, float] = {}
    for symbol in CORE_ASSETS:
        price = float(close_prices.get(symbol, 0.0) or 0.0)
        quantities[symbol] = (market_values[symbol] / price) if price > 0 else 0.0
    return {
        "available_cash": float(equity),
        "market_values": market_values,
        "quantities": quantities,
        "sellable_quantities": dict(quantities),
        "total_strategy_equity": float(equity),
        "cash_sweep_symbol": "BOXX",
        "metadata": {},
    }


def _target_weight_frame(
    close: pd.DataFrame,
    indicator_history: Mapping[str, pd.DataFrame],
    *,
    min_history_days: int,
    strategy_kwargs: Mapping[str, object],
) -> pd.DataFrame:
    rows: list[dict[str, float | pd.Timestamp]] = []
    weights = {symbol: 0.0 for symbol in CORE_ASSETS}
    weights["BOXX"] = 1.0
    equity = 1.0
    unique_dates = pd.DatetimeIndex(close.index)
    for position, as_of in enumerate(unique_dates):
        if position + 1 < int(min_history_days):
            continue
        close_row = close.loc[as_of]
        if any(not math.isfinite(float(close_row.get(symbol, np.nan))) for symbol in ("SOXL", "SOXX", "BOXX")):
            continue
        indicators = _indicator_snapshot_at(indicator_history, as_of)
        if "soxl" not in indicators or "soxx" not in indicators:
            continue
        if not math.isfinite(float(indicators["soxx"].get("ma_trend", np.nan))):
            continue
        account_state = _account_state_from_weights(
            weights=weights,
            equity=equity,
            close_prices={symbol: float(close_row[symbol]) for symbol in CORE_ASSETS},
        )
        try:
            plan = build_rebalance_plan(
                indicators,
                account_state,
                translator=lambda key, **_kwargs: key,
                **dict(strategy_kwargs),
            )
        except Exception:
            continue
        targets = dict(plan.get("targets") or {})
        total = float(sum(float(targets.get(symbol, 0.0) or 0.0) for symbol in CORE_ASSETS))
        if total <= 0.0:
            selected = {symbol: 0.0 for symbol in CORE_ASSETS}
            selected["BOXX"] = 1.0
        else:
            selected = {
                symbol: max(0.0, float(targets.get(symbol, 0.0) or 0.0) / float(account_state["total_strategy_equity"]))
                for symbol in CORE_ASSETS
            }
            weight_sum = math.fsum(selected.values())
            if weight_sum > 1.0 + 1e-9:
                selected = {symbol: value / weight_sum for symbol, value in selected.items()}
        if any(not math.isfinite(weight) or weight < 0.0 for weight in selected.values()):
            raise ValueError("SOXL target weights must be finite and non-negative")
        rows.append({"date": as_of, **selected})
        weights = selected
    if not rows:
        raise ValueError("SOXL core-only replay produced no target weight events")
    targets_frame = pd.DataFrame(rows).set_index("date")
    # Lag-one close convention: signal at d close applies to d -> d+1.
    return targets_frame.reindex(close.index).shift(1)


def run_soxl_core_only_backtest(
    market_history: pd.DataFrame,
    *,
    min_history_days: int = DEFAULT_MIN_HISTORY_DAYS,
    cost_bps: float = DEFAULT_COST_BPS,
    strategy_overrides: Mapping[str, object] | None = None,
) -> SoxlCoreOnlyBacktestResult:
    settings = SoxlCoreOnlyBacktestConfig(
        min_history_days=int(min_history_days),
        cost_bps=float(cost_bps),
    )
    kwargs = _strategy_kwargs(strategy_overrides)
    close = build_close_matrix(market_history, universe_symbols=CORE_ASSETS)
    missing = [symbol for symbol in CORE_ASSETS if symbol not in close.columns]
    if missing:
        raise ValueError(f"market history is missing required symbols: {', '.join(missing)}")
    if len(close) < int(settings.min_history_days):
        raise ValueError(
            f"market_history requires at least {int(settings.min_history_days)} overlapping trading days"
        )
    cost_rate = float(settings.cost_bps) / 10_000.0
    if not math.isfinite(cost_rate) or not 0.0 <= cost_rate < 1.0:
        raise ValueError("cost_bps must be finite and in [0, 10000)")

    indicator_history = build_indicator_history(
        close,
        trend_ma_window=int(kwargs["trend_ma_window"]),
        volatility_window=int(kwargs["blend_gate_volatility_delever_window"]),
        volatility_lookback=int(kwargs["blend_gate_volatility_delever_dynamic_lookback"]),
        volatility_percentile=float(kwargs["blend_gate_volatility_delever_dynamic_percentile"]),
        volatility_min_periods=int(kwargs["blend_gate_volatility_delever_dynamic_min_periods"]),
        volatility_floor=float(kwargs["blend_gate_volatility_delever_dynamic_floor"]),
        volatility_cap=float(kwargs["blend_gate_volatility_delever_dynamic_cap"]),
    )
    targets = _target_weight_frame(
        close,
        indicator_history,
        min_history_days=settings.min_history_days,
        strategy_kwargs=kwargs,
    )
    shares = pd.Series(0.0, index=close.columns)
    cash = equity = 1.0
    net = pd.Series(0.0, index=close.index, dtype=float)
    for position in range(1, len(close)):
        target = targets.iloc[position]
        if target.notna().any():
            shares, cash, _fees = _rebalance_holdings(
                shares,
                cash,
                close.iloc[position - 1],
                target.reindex(close.columns).fillna(0.0),
                cost_rate=cost_rate,
            )
        held = shares > 0.0
        prices = close.iloc[position][held]
        if any(not math.isfinite(price) or price <= 0.0 for price in prices):
            raise ValueError("held assets require positive finite mark prices")
        marked_equity = cash + float((shares[held] * prices).sum())
        net.iloc[position] = marked_equity / equity - 1.0
        equity = marked_equity
    metrics = compute_backtest_metrics(net)
    return SoxlCoreOnlyBacktestResult(daily_returns=net, metrics=metrics)


def required_market_symbols() -> tuple[str, ...]:
    """Symbols required in lifecycle market history for the SOXL proxy."""
    return tuple(dict.fromkeys([*CORE_ASSETS, CALENDAR_BENCHMARK_SYMBOL]))


__all__ = [
    "CALENDAR_BENCHMARK_SYMBOL",
    "CORE_ASSETS",
    "DEFAULT_MIN_HISTORY_DAYS",
    "PROFILE_NAME",
    "SoxlCoreOnlyBacktestConfig",
    "SoxlCoreOnlyBacktestResult",
    "build_indicator_history",
    "required_market_symbols",
    "run_soxl_core_only_backtest",
]
