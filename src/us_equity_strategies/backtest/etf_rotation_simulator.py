"""Weight-based ETF rotation backtest for US orchestrator integration."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

import pandas as pd

StrategySignalFn = Callable[[Any], tuple[Mapping[str, float], Mapping[str, object]]]


@dataclass(frozen=True)
class UsRotationBacktestConfig:
    rebalance_frequency: str = "monthly"
    min_history_days: int = 260
    cost_bps: float = 10.0
    include_current_holdings: bool = False


@dataclass
class UsRotationBacktestResult:
    daily_returns: pd.Series
    metrics: dict[str, float | int] = field(default_factory=dict)


def build_close_matrix(
    market_history: pd.DataFrame,
    *,
    universe_symbols: Sequence[str] | None = None,
) -> pd.DataFrame:
    frame = market_history.copy()
    frame["date"] = pd.to_datetime(frame["date"], utc=False).dt.tz_localize(None).dt.normalize()
    frame["symbol"] = frame["symbol"].astype(str).str.strip().str.upper()
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    pivot = frame.pivot_table(index="date", columns="symbol", values="close", aggfunc="last", dropna=False).sort_index()
    if universe_symbols:
        columns = [str(symbol).upper() for symbol in universe_symbols if str(symbol).upper() in pivot.columns]
        pivot = pivot[columns]
    # Preserve missing quotes for the held-asset and execution-price checks.
    return pivot


def _rebalance_dates(index: pd.DatetimeIndex, *, frequency: str) -> pd.DatetimeIndex:
    if frequency == "monthly":
        return index.to_series().resample("ME").last().dropna().index
    if frequency == "weekly":
        return index.to_series().resample("W-FRI").last().dropna().index
    raise ValueError("rebalance_frequency must be 'monthly' or 'weekly'")


def _history_slice(market_history: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    frame = market_history.copy()
    frame["date"] = pd.to_datetime(frame["date"], utc=False).dt.tz_localize(None).dt.normalize()
    return frame.loc[frame["date"] <= as_of]


def _rebalance_holdings(
    shares: pd.Series,
    cash: float,
    prices: pd.Series,
    targets: pd.Series,
    *,
    cost_rate: float,
) -> tuple[pd.Series, float, float]:
    """Fill pre-fee targets, selling first and budgeting buy fees from cash.

    Fractional buys are scaled together if cash is insufficient; post-fee
    weights need not exactly equal targets. Inputs come from the validated
    long-only target events, not broker orders or a shared account allocator.
    """
    needed = (shares > 0.0) | (targets > 0.0)
    if any(not math.isfinite(price) or price <= 0.0 for price in prices[needed]):
        raise ValueError("held or targeted assets require positive finite fill prices")
    safe_prices = prices.where(needed, 1.0)
    values = shares * safe_prices
    delta = targets * (cash + float(values.sum())) - values
    sells = -delta.clip(upper=0.0)
    buys = delta.clip(lower=0.0)
    sale_notional = float(sells.sum())
    available_cash = cash + sale_notional * (1.0 - cost_rate)
    desired_buys = float(buys.sum())
    if desired_buys > 0.0:
        buys *= min(1.0, available_cash / (desired_buys * (1.0 + cost_rate)))
    purchase_notional = float(buys.sum())
    fees = (sale_notional + purchase_notional) * cost_rate
    # Only floating-point roundoff can be negative after the fee-inclusive cap.
    cash = max(0.0, available_cash - purchase_notional * (1.0 + cost_rate))
    return (values - sells + buys) / safe_prices, cash, fees


def compute_backtest_metrics(daily_returns: pd.Series) -> dict[str, float | int]:
    returns = daily_returns.dropna()
    if returns.empty:
        return {
            "days": 0,
            "annual_return": 0.0,
            "max_drawdown": 0.0,
            "annual_volatility": 0.0,
            "total_return": 0.0,
            "sharpe_ratio": 0.0,
        }
    equity = (1.0 + returns).cumprod()
    years = len(returns) / 252.0
    annual_return = float(equity.iloc[-1] ** (1 / years) - 1) if years > 0 else 0.0
    drawdown = equity / equity.cummax().clip(lower=1.0) - 1.0
    annual_volatility = float(returns.std(ddof=0) * math.sqrt(252))
    sharpe = float(returns.mean()) * 252.0 / annual_volatility if annual_volatility > 0 else 0.0
    return {
        "days": int(len(returns)),
        "annual_return": annual_return,
        "max_drawdown": float(drawdown.min()),
        "annual_volatility": annual_volatility,
        "total_return": float(equity.iloc[-1] - 1.0),
        "sharpe_ratio": float(sharpe),
    }


def run_etf_rotation_backtest(
    market_history: pd.DataFrame,
    strategy_signal_fn: StrategySignalFn,
    *,
    config: UsRotationBacktestConfig | None = None,
    universe_symbols: Sequence[str] | None = None,
    strategy_kwargs: Mapping[str, Any] | None = None,
) -> UsRotationBacktestResult:
    settings = config or UsRotationBacktestConfig()
    kwargs = dict(strategy_kwargs or {})
    close = build_close_matrix(market_history, universe_symbols=universe_symbols)
    if len(close) < int(settings.min_history_days):
        raise ValueError(
            f"market_history requires at least {int(settings.min_history_days)} overlapping trading days"
        )

    cost_rate = float(settings.cost_bps) / 10_000.0
    if not math.isfinite(cost_rate) or not 0.0 <= cost_rate < 1.0:
        raise ValueError("cost_bps must be finite and in [0, 10000)")
    shares = pd.Series(0.0, index=close.columns)
    cash = equity = 1.0
    net = pd.Series(0.0, index=close.index)
    event_dates = set()
    for label in _rebalance_dates(pd.DatetimeIndex(close.index), frequency=settings.rebalance_frequency):
        event_position = close.index.searchsorted(label, side="right") - 1
        if 0 <= event_position < len(close) - 1:
            event_dates.add(pd.Timestamp(close.index[event_position]))
    pending_target: pd.Series | None = None
    for position in range(1, len(close)):
        signal_date = pd.Timestamp(close.index[position - 1])
        if signal_date in event_dates:
            history = _history_slice(market_history, signal_date)
            if len(history["date"].drop_duplicates()) < int(settings.min_history_days):
                weights: Mapping[str, float] = {}
                metadata: Mapping[str, object] = {}
            else:
                signal_kwargs = dict(kwargs)
                if settings.include_current_holdings:
                    signal_kwargs["current_holdings"] = set(shares.index[shares > 0.0])
                weights, metadata = strategy_signal_fn(history, **signal_kwargs)
            if metadata.get("mode") == "hold":
                pending_target = None
            else:
                selected = {symbol: float(weights.get(symbol, 0.0)) for symbol in close.columns}
                if (any(not math.isfinite(weight) or weight < 0.0 for weight in selected.values())
                    or math.fsum(selected.values()) > 1.0):
                    raise ValueError("target weights must be finite, non-negative and sum to at most one")
                pending_target = pd.Series(selected, index=close.columns)
        if pending_target is not None:
            # Preserve the close-only lag-one convention: the signal at d close
            # sets holdings for d -> d+1, with its fee charged in that interval.
            # This is not proof that a close-derived signal can fill live at d.
            shares, cash, _fees = _rebalance_holdings(
                shares, cash, close.iloc[position - 1], pending_target, cost_rate=cost_rate,
            )
            pending_target = None
        held = shares > 0.0
        prices = close.iloc[position][held]
        if any(not math.isfinite(price) or price <= 0.0 for price in prices):
            raise ValueError("held assets require positive finite mark prices")
        marked_equity = cash + float((shares[held] * prices).sum())
        net.iloc[position] = marked_equity / equity - 1.0
        equity = marked_equity
    metrics = compute_backtest_metrics(net)
    return UsRotationBacktestResult(daily_returns=net, metrics=metrics)


__all__ = [
    "UsRotationBacktestConfig",
    "UsRotationBacktestResult",
    "build_close_matrix",
    "compute_backtest_metrics",
    "run_etf_rotation_backtest",
]
