"""Single history-only research candidate; never used by the runtime strategy."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from us_equity_strategies.strategies.global_etf_rotation import build_target_weights

WINDOW = 126
TARGET_VOLATILITY = 0.15


def build_research_target_weights(
    market_history: Any, **kwargs: Any,
) -> tuple[dict[str, float], dict[str, object]]:
    """Scale risky return contributions on existing rebalance events only.

    The caller supplies history available at the signal time. An optional
    as_of_date also excludes future rows before the baseline signal is built.
    This does not bound total portfolio volatility, including BIL, or drawdown.
    """
    frame = pd.DataFrame(market_history).copy()
    if not frame.empty:
        frame["date"] = pd.to_datetime(frame["date"]).dt.tz_localize(None).dt.normalize()
        if frame["date"].isna().any():
            raise ValueError("research history requires valid dates")
        if kwargs.get("as_of_date") is not None:
            cutoff = pd.Timestamp(kwargs["as_of_date"]).tz_localize(None).normalize()
            frame = frame.loc[frame["date"] <= cutoff]
    weights, metadata = build_target_weights(frame, **kwargs)
    if metadata.get("mode") in {"empty_history", "insufficient_history"}:
        raise ValueError("research candidate requires complete history")
    if metadata.get("mode") != "rebalance":
        return weights, metadata
    safe_haven = kwargs.get("safe_haven", "BIL")
    risky = {symbol: weight for symbol, weight in weights.items() if symbol != safe_haven and weight > 0}
    if not risky:
        return weights, metadata
    if any(not math.isfinite(w) or w < 0 for w in weights.values()) or math.fsum(weights.values()) > 1.0:
        raise ValueError("research candidate requires unlevered nonnegative weights")
    dates = pd.DatetimeIndex(frame["date"].unique()).sort_values()[-(WINDOW + 1):]
    if len(dates) < WINDOW + 1:
        raise ValueError("research volatility requires 127 aligned closes")
    recent = frame.loc[frame["date"].isin(dates) & frame["symbol"].isin(risky)]
    if recent.duplicated(["date", "symbol"]).any():
        raise ValueError("research volatility rejects duplicate prices")
    closes = recent.pivot(index="date", columns="symbol", values="close").reindex(index=dates, columns=list(risky))
    closes = closes.apply(pd.to_numeric, errors="raise")
    if not np.isfinite(closes.to_numpy()).all() or (closes <= 0).any().any():
        raise ValueError("research volatility requires complete positive finite prices")
    returns = closes.pct_change(fill_method=None).iloc[1:]
    contributions = returns.mul(pd.Series(risky)).sum(axis=1)
    sigma = float(contributions.std(ddof=1) * math.sqrt(252))
    if not math.isfinite(sigma):
        raise ValueError("research volatility must be finite")
    scale = min(1.0, TARGET_VOLATILITY / sigma) if sigma > 0 else 1.0
    scaled = dict(weights)
    for symbol, weight in risky.items():
        scaled[symbol] = weight * scale
    if scale < 1:
        scaled[safe_haven] = weights.get(safe_haven, 0.0) + math.fsum(
            weight - scaled[symbol] for symbol, weight in risky.items()
        )
    return scaled, {**metadata, "absolute_volatility_scale": scale, "risky_contribution_volatility": sigma}
