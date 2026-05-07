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
CONFIDENCE_METRIC_Z_GAP = "z_gap"


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


def _annualized_volatility(closes: pd.Series, window: int) -> float:
    if len(closes) <= window:
        return float("nan")
    returns = closes.pct_change(fill_method=None).dropna()
    if len(returns) < window:
        return float("nan")
    volatility = returns.iloc[-window:].std()
    if pd.isna(volatility):
        return float("nan")
    return float(volatility * np.sqrt(252))


def _score_confidence(sorted_tickers: list[tuple[str, float]], *, metric: str) -> float:
    if len(sorted_tickers) < 2:
        return float("nan")
    gap = float(sorted_tickers[0][1] - sorted_tickers[1][1])
    if metric == CONFIDENCE_METRIC_Z_GAP:
        dispersion = float(np.nanstd([score for _ticker, score in sorted_tickers]))
        if dispersion <= 0.0 or np.isnan(dispersion):
            return float("nan")
        return gap / dispersion
    return gap


def _passes_relative_volatility_gate(
    price_data: dict[str, pd.Series],
    top1: str,
    top2: str,
    *,
    window: int,
    max_ratio: float,
) -> tuple[bool, float, float]:
    top1_vol = _annualized_volatility(price_data.get(top1, pd.Series(dtype=float)), window)
    top2_vol = _annualized_volatility(price_data.get(top2, pd.Series(dtype=float)), window)
    if np.isnan(top1_vol) or np.isnan(top2_vol) or top2_vol <= 0.0:
        return False, top1_vol, top2_vol
    return top1_vol <= top2_vol * max_ratio, top1_vol, top2_vol


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
    confidence_weighting_enabled: bool = False,
    confidence_metric: str = CONFIDENCE_METRIC_Z_GAP,
    confidence_threshold: float = 1.0,
    confidence_top1_weight: float = 0.75,
    confidence_volatility_gate_enabled: bool = False,
    confidence_volatility_window: int = 126,
    confidence_volatility_max_ratio: float = 1.3,
):
    """
    Compute target weights.
    Returns (weights_dict, signal_description, is_emergency, canary_str).
    """
    ranking_pool = list(ranking_pool)
    canary_assets = list(canary_assets)
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
            now_ny = tz_ny.localize(timestamp.to_pydatetime())
        else:
            now_ny = timestamp.tz_convert(tz_ny).to_pydatetime()
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
    confidence_note = ""
    if confidence_weighting_enabled and top_n == 2 and len(top) == 2:
        confidence = _score_confidence(sorted_tickers, metric=str(confidence_metric))
        top1, top2 = top[0][0], top[1][0]
        use_confidence_weight = not np.isnan(confidence) and confidence >= float(confidence_threshold)
        top1_vol = top2_vol = float("nan")
        if use_confidence_weight and confidence_volatility_gate_enabled:
            use_confidence_weight, top1_vol, top2_vol = _passes_relative_volatility_gate(
                price_data,
                top1,
                top2,
                window=int(confidence_volatility_window),
                max_ratio=float(confidence_volatility_max_ratio),
            )
        if use_confidence_weight:
            top1_weight = min(1.0, max(per_weight, float(confidence_top1_weight)))
            weights = {top1: top1_weight, top2: 1.0 - top1_weight}
            confidence_note = (
                f"\n  Confidence: {confidence:.3f} >= {float(confidence_threshold):.3f}; "
                f"{top1} weight {top1_weight:.1%}"
            )
        else:
            confidence_note = (
                f"\n  Confidence: {confidence:.3f}"
                if not np.isnan(confidence)
                else "\n  Confidence: n/a"
            )
        if confidence_volatility_gate_enabled and not np.isnan(top1_vol) and not np.isnan(top2_vol):
            confidence_note += (
                f"; vol {top1}/{top2}: {top1_vol:.1%}/{top2_vol:.1%}, "
                f"max ratio {float(confidence_volatility_max_ratio):.2f}"
            )
    if len(top) < top_n:
        weights[safe_haven] = weights.get(safe_haven, 0) + per_weight * (top_n - len(top))

    top_str = ", ".join(f"{ticker}({score:.3f})" for ticker, score in top)
    signal_desc = translator("quarterly", n=top_n) + f"\n  Top: {top_str}{confidence_note}"
    return weights, signal_desc, False, canary_str
