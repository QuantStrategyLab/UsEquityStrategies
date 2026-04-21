from __future__ import annotations

import time
from datetime import datetime
from importlib import import_module
from typing import Callable

import numpy as np
import pandas as pd
import pytz

RANKING_POOL = [
    "EWY",
    "EWT",
    "INDA",
    "FXI",
    "EWJ",
    "VGK",
    "VOO",
    "XLK",
    "SMH",
    "GLD",
    "SLV",
    "USO",
    "DBA",
    "XLE",
    "XLF",
    "ITA",
    "XLP",
    "XLU",
    "XLV",
    "IHI",
    "VNQ",
    "KRE",
]
CANARY_ASSETS = ["SPY", "EFA", "EEM", "AGG"]
SAFE_HAVEN = "BIL"
TOP_N = 2
SMA_PERIOD = 200
HOLD_BONUS = 0.02
CANARY_BAD_THRESHOLD = 4
REBALANCE_MONTHS = {3, 6, 9, 12}


def _load_nyse_calendar():
    try:
        module = import_module("pandas_market_calendars")
    except Exception:
        return None
    try:
        return module.get_calendar("NYSE")
    except Exception:
        return None


def _is_rebalance_day(now_ny: datetime, *, rebalance_months) -> bool:
    if now_ny.month not in rebalance_months:
        return False

    month_start = pd.Timestamp(now_ny.date()).replace(day=1)
    month_end = month_start + pd.offsets.MonthEnd(0)
    calendar = _load_nyse_calendar()
    if calendar is None:
        last_trading_day = pd.bdate_range(start=month_start, end=month_end)[-1].date()
        return now_ny.date() == last_trading_day

    schedule = calendar.schedule(start_date=month_start, end_date=month_end)
    if schedule.empty:
        return False
    last_trading_day = pd.Timestamp(schedule.index[-1]).date()
    return now_ny.date() == last_trading_day


def compute_13612w_momentum(closes: pd.Series, as_of_date=None) -> float:
    """Compute Keller's 13612W momentum score from monthly close prices."""
    if len(closes) < 253:
        return float("nan")

    me = closes.resample("ME").last().dropna()
    if as_of_date is not None:
        me = me[me.index <= as_of_date]

    if len(me) < 13:
        return float("nan")

    cur = me.iloc[-1]
    lookbacks = {1: 12, 3: 4, 6: 2, 12: 1}
    score = 0.0
    for months, weight in lookbacks.items():
        if len(me) < months + 1:
            return float("nan")
        prior = me.iloc[-(months + 1)]
        if prior == 0 or pd.isna(prior):
            return float("nan")
        score += weight * (cur / prior - 1)
    return score / 19


def check_sma(closes: pd.Series, period: int = SMA_PERIOD) -> bool:
    """Return True if last close is above the configured SMA period."""
    if len(closes) < period:
        return False
    return bool(closes.iloc[-1] > closes.iloc[-period:].mean())


def compute_signals(
    ib,
    current_holdings,
    *,
    get_historical_close: Callable,
    as_of_date=None,
    ranking_pool=RANKING_POOL,
    canary_assets=CANARY_ASSETS,
    safe_haven: str = SAFE_HAVEN,
    top_n: int = TOP_N,
    hold_bonus: float = HOLD_BONUS,
    canary_bad_threshold: int = CANARY_BAD_THRESHOLD,
    rebalance_months=REBALANCE_MONTHS,
    translator: Callable,
    pacing_sec: float,
    sma_period: int = SMA_PERIOD,
):
    """
    Compute target weights.
    Returns (weights_dict, signal_description, is_emergency, canary_str).
    """
    all_tickers = list(set(ranking_pool + canary_assets + [safe_haven]))
    price_data = {}
    for ticker in all_tickers:
        try:
            closes = get_historical_close(ib, ticker)
            if len(closes) > 0:
                price_data[ticker] = closes
        except Exception as exc:
            print(f"Warning: failed to fetch {ticker}: {exc}", flush=True)
        time.sleep(pacing_sec)

    n_bad = 0
    canary_details = []
    for ticker in canary_assets:
        if ticker not in price_data:
            n_bad += 1
            canary_details.append(f"{ticker}:❌(no data)")
            continue
        mom = compute_13612w_momentum(price_data[ticker])
        if np.isnan(mom) or mom < 0:
            n_bad += 1
            canary_details.append(
                f"{ticker}:❌({mom:.3f})" if not np.isnan(mom) else f"{ticker}:❌(nan)"
            )
        else:
            canary_details.append(f"{ticker}:✅({mom:.3f})")

    canary_str = ", ".join(canary_details)
    if n_bad >= canary_bad_threshold:
        signal_desc = translator("emergency", n_bad=n_bad, safe=safe_haven)
        return {safe_haven: 1.0}, signal_desc, True, canary_str

    tz_ny = pytz.timezone("America/New_York")
    if as_of_date is None:
        now_ny = datetime.now(tz_ny)
    else:
        timestamp = pd.Timestamp(as_of_date)
        if timestamp.tzinfo is None:
            timestamp = tz_ny.localize(timestamp.to_pydatetime())
        else:
            timestamp = timestamp.tz_convert(tz_ny)
        now_ny = timestamp.to_pydatetime()
    is_rebal_day = _is_rebalance_day(now_ny, rebalance_months=rebalance_months)

    if not is_rebal_day:
        signal_desc = translator("daily_check")
        return None, signal_desc, False, canary_str

    scores = {}
    for ticker in ranking_pool:
        if ticker not in price_data:
            continue
        mom = compute_13612w_momentum(price_data[ticker])
        if np.isnan(mom):
            continue
        if not check_sma(price_data[ticker], sma_period):
            continue
        bonus = hold_bonus if ticker in current_holdings else 0.0
        scores[ticker] = mom + bonus

    sorted_tickers = sorted(scores.items(), key=lambda item: -item[1])
    top = sorted_tickers[:top_n]
    if len(top) == 0:
        signal_desc = translator("emergency", n_bad="SMA", safe=safe_haven)
        return {safe_haven: 1.0}, signal_desc, False, canary_str

    per_weight = 1.0 / top_n
    weights = {ticker: per_weight for ticker, _score in top}
    if len(top) < top_n:
        weights[safe_haven] = weights.get(safe_haven, 0) + per_weight * (top_n - len(top))

    top_str = ", ".join(f"{ticker}({score:.3f})" for ticker, score in top)
    signal_desc = translator("quarterly", n=top_n) + f"\n  Top: {top_str}"
    return weights, signal_desc, False, canary_str
