"""Research combo backtest for US global ETF rotation + Russell proxy + DCA."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal, Mapping

import pandas as pd

from us_equity_strategies.backtest.etf_rotation_simulator import (
    StrategySignalFn,
    UsRotationBacktestConfig,
    UsRotationBacktestResult,
    build_close_matrix,
    run_etf_rotation_backtest,
)
from us_equity_strategies.strategies.global_etf_rotation import extract_managed_symbols_universe

ComboMode = Literal["static", "dynamic"]

DEFAULT_GLOBAL_WEIGHT: float = 0.50
DEFAULT_RUSSELL_WEIGHT: float = 0.30
DEFAULT_DCA_WEIGHT: float = 0.20
DEFAULT_DYNAMIC_REDUCTION_PCT: float = 0.30

SPY_SYMBOL = "SPY"
RUSSELL_PROXY_SYMBOL = "QQQ"
DCA_SYMBOL = "QQQ"
MEGA_CAP_PROXY_SYMBOLS: tuple[str, ...] = (
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "META",
    "TSLA",
    "AVGO",
)


@dataclass(frozen=True)
class UsComboBacktestConfig:
    global_weight: float = DEFAULT_GLOBAL_WEIGHT
    russell_weight: float = DEFAULT_RUSSELL_WEIGHT
    dca_weight: float = DEFAULT_DCA_WEIGHT
    combo_mode: ComboMode = "dynamic"
    min_history_days: int = 260
    cost_bps: float = 10.0
    rebalance_frequency: str = "monthly"
    dynamic_reduction_pct: float = DEFAULT_DYNAMIC_REDUCTION_PCT
    spy_sma_period: int = 200


def _dynamic_exposure_multiplier(
    close: pd.DataFrame,
    as_of: pd.Timestamp,
    *,
    spy_sma_period: int,
    reduction_pct: float,
) -> float:
    if SPY_SYMBOL not in close.columns:
        return 1.0
    spy = close[SPY_SYMBOL].loc[:as_of].dropna()
    if len(spy) < spy_sma_period:
        return 1.0
    sma = float(spy.iloc[-spy_sma_period:].mean())
    if float(spy.iloc[-1]) > sma:
        return 1.0
    return max(0.0, 1.0 - float(reduction_pct))


def _close_from_history(history: pd.DataFrame) -> pd.DataFrame:
    return build_close_matrix(history)


def _russell_sleeve_weights(close: pd.DataFrame) -> dict[str, float]:
    mega_cap = [symbol for symbol in MEGA_CAP_PROXY_SYMBOLS if symbol in close.columns]
    if len(mega_cap) >= 3:
        weight = 1.0 / float(len(mega_cap))
        return {symbol: weight for symbol in mega_cap}
    if RUSSELL_PROXY_SYMBOL in close.columns:
        return {RUSSELL_PROXY_SYMBOL: 1.0}
    return {}


def _dca_sleeve_weights(close: pd.DataFrame) -> dict[str, float]:
    if DCA_SYMBOL in close.columns:
        return {DCA_SYMBOL: 1.0}
    return {}


def _require_active_proxy_prices(
    close: pd.DataFrame,
    as_of: pd.Timestamp,
    *,
    symbols: Mapping[str, float],
) -> None:
    needed = [symbol for symbol, weight in symbols.items() if float(weight) != 0.0]
    if not needed:
        return
    if as_of not in close.index:
        raise ValueError("active sleeves require positive finite proxy prices")
    prices = close.loc[as_of, needed]
    if any(not math.isfinite(float(price)) or float(price) <= 0.0 for price in prices):
        raise ValueError("active sleeves require positive finite proxy prices")


def _merge_weight(target: dict[str, float], symbol: str, weight: float) -> None:
    if weight == 0.0:
        return
    target[symbol] = float(target.get(symbol, 0.0)) + float(weight)


def _combo_signal_fn(
    signal_fn: StrategySignalFn,
    *,
    combo_config: UsComboBacktestConfig,
    strategy_kwargs: Mapping[str, Any],
) -> StrategySignalFn:
    def _signal(history: pd.DataFrame, **kwargs: Any) -> tuple[dict[str, float], dict[str, object]]:
        close = _close_from_history(history)
        if close.empty:
            return {}, {}
        as_of = pd.Timestamp(close.index[-1])
        if combo_config.combo_mode == "dynamic":
            mult = _dynamic_exposure_multiplier(
                close,
                as_of,
                spy_sma_period=int(combo_config.spy_sma_period),
                reduction_pct=float(combo_config.dynamic_reduction_pct),
            )
        else:
            mult = 1.0

        merged: dict[str, float] = {}
        call_kwargs = dict(strategy_kwargs)
        call_kwargs.update(kwargs)
        global_weights, _metadata = signal_fn(history, **call_kwargs)
        w_global = float(combo_config.global_weight) * mult
        for symbol, weight in dict(global_weights or {}).items():
            _merge_weight(merged, str(symbol).upper(), float(weight) * w_global)

        russell = _russell_sleeve_weights(close)
        w_russell = float(combo_config.russell_weight) * mult
        scaled_russell = {symbol: weight * w_russell for symbol, weight in russell.items()}
        _require_active_proxy_prices(close, as_of, symbols=scaled_russell)
        for symbol, weight in scaled_russell.items():
            _merge_weight(merged, symbol, weight)

        dca = _dca_sleeve_weights(close)
        w_dca = float(combo_config.dca_weight)
        scaled_dca = {symbol: weight * w_dca for symbol, weight in dca.items()}
        _require_active_proxy_prices(close, as_of, symbols=scaled_dca)
        for symbol, weight in scaled_dca.items():
            _merge_weight(merged, symbol, weight)

        return merged, {"combo_exposure_multiplier": mult}

    return _signal


def run_combo_backtest(
    market_history: pd.DataFrame,
    strategy_signal_fn: StrategySignalFn,
    *,
    combo_config: UsComboBacktestConfig | None = None,
    rotation_config: UsRotationBacktestConfig | None = None,
    universe_symbols: Any = None,
    strategy_kwargs: Mapping[str, Any] | None = None,
) -> UsRotationBacktestResult:
    combo = combo_config or UsComboBacktestConfig()
    rotation = rotation_config or UsRotationBacktestConfig(
        min_history_days=combo.min_history_days,
        cost_bps=combo.cost_bps,
        rebalance_frequency=combo.rebalance_frequency,
    )
    # Keep caller rotation settings but always consume combo cost/rebalance inputs.
    rotation = UsRotationBacktestConfig(
        rebalance_frequency=str(combo.rebalance_frequency),
        min_history_days=int(rotation.min_history_days),
        cost_bps=float(combo.cost_bps),
    )
    symbols = tuple(
        dict.fromkeys(
            [
                *(universe_symbols or extract_managed_symbols_universe()),
                RUSSELL_PROXY_SYMBOL,
                *MEGA_CAP_PROXY_SYMBOLS,
            ]
        )
    )
    close = build_close_matrix(market_history, universe_symbols=symbols)
    if len(close) < int(combo.min_history_days):
        raise ValueError(
            f"market_history requires at least {int(combo.min_history_days)} overlapping trading days"
        )
    return run_etf_rotation_backtest(
        market_history,
        _combo_signal_fn(
            strategy_signal_fn,
            combo_config=combo,
            strategy_kwargs=dict(strategy_kwargs or {}),
        ),
        config=rotation,
        universe_symbols=symbols,
        strategy_kwargs={},
    )


__all__ = [
    "DCA_SYMBOL",
    "DEFAULT_DCA_WEIGHT",
    "DEFAULT_GLOBAL_WEIGHT",
    "DEFAULT_RUSSELL_WEIGHT",
    "RUSSELL_PROXY_SYMBOL",
    "SPY_SYMBOL",
    "UsComboBacktestConfig",
    "run_combo_backtest",
]
