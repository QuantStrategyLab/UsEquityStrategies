"""Research combo backtest for US global ETF rotation + Russell proxy + DCA."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

import pandas as pd

from us_equity_strategies.backtest.etf_rotation_simulator import (
    StrategySignalFn,
    UsRotationBacktestConfig,
    UsRotationBacktestResult,
    build_close_matrix,
    compute_backtest_metrics,
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


def _russell_proxy_returns(close: pd.DataFrame) -> pd.Series:
    mega_cap = [symbol for symbol in MEGA_CAP_PROXY_SYMBOLS if symbol in close.columns]
    if len(mega_cap) >= 3:
        return close[mega_cap].pct_change().fillna(0.0).mean(axis=1)
    if RUSSELL_PROXY_SYMBOL in close.columns:
        return close[RUSSELL_PROXY_SYMBOL].pct_change().fillna(0.0)
    return pd.Series(0.0, index=close.index)


def _dca_returns(close: pd.DataFrame) -> pd.Series:
    if DCA_SYMBOL in close.columns:
        return close[DCA_SYMBOL].pct_change().fillna(0.0)
    return pd.Series(0.0, index=close.index)


def _combo_strategy_returns(
    market_history: pd.DataFrame,
    close: pd.DataFrame,
    *,
    signal_fn: StrategySignalFn,
    rotation_config: UsRotationBacktestConfig,
    combo_config: UsComboBacktestConfig,
    strategy_kwargs: Mapping[str, Any],
    universe_symbols: Any = None,
) -> pd.Series:
    global_result = run_etf_rotation_backtest(
        market_history,
        signal_fn,
        config=rotation_config,
        universe_symbols=universe_symbols,
        strategy_kwargs=strategy_kwargs,
    )
    global_returns = global_result.daily_returns
    russell_returns = _russell_proxy_returns(close)
    dca_returns = _dca_returns(close)

    common_idx = (
        global_returns.index.intersection(russell_returns.index).intersection(dca_returns.index)
    )
    if len(common_idx) < 2:
        return pd.Series(dtype=float)

    w_global = float(combo_config.global_weight)
    w_russell = float(combo_config.russell_weight)
    w_dca = float(combo_config.dca_weight)

    combo_returns = pd.Series(0.0, index=common_idx)
    for date in common_idx[1:]:
        prior_dates = common_idx[common_idx < date]
        as_of = pd.Timestamp(prior_dates[-1]) if len(prior_dates) > 0 else pd.Timestamp(date)
        if combo_config.combo_mode == "dynamic":
            mult = _dynamic_exposure_multiplier(
                close,
                as_of,
                spy_sma_period=int(combo_config.spy_sma_period),
                reduction_pct=float(combo_config.dynamic_reduction_pct),
            )
        else:
            mult = 1.0

        combo_returns.at[date] = (
            w_global * mult * float(global_returns.loc[date])
            + w_russell * mult * float(russell_returns.loc[date])
            + w_dca * float(dca_returns.loc[date])
        )

    return combo_returns


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
    net = _combo_strategy_returns(
        market_history,
        close,
        signal_fn=strategy_signal_fn,
        rotation_config=rotation,
        combo_config=combo,
        strategy_kwargs=dict(strategy_kwargs or {}),
        universe_symbols=symbols,
    )
    return UsRotationBacktestResult(daily_returns=net, metrics=compute_backtest_metrics(net))


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
