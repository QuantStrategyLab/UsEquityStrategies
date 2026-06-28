"""US Equity Combo Backtest: Static vs Dynamic allocation.

Combines three legs:
1. Global ETF Rotation (50%) — momentum rotation across global ETF pool
2. Russell Top50 Leader Rotation (30%) — snapshot multi-factor leader selection
3. Nasdaq 100 DCA (20%) — dollar-cost averaging into QQQ

Uses yfinance to download daily closes, then simulates monthly rebalancing
with the same signal logic as the production strategy modules.

Usage:
    python3 scripts/research_us_equity_combo_backtest.py
    python3 scripts/research_us_equity_combo_backtest.py --mode static
    python3 scripts/research_us_equity_combo_backtest.py --mode dynamic
    python3 scripts/research_us_equity_combo_backtest.py --mode both --json-output results.json
    python3 scripts/research_us_equity_combo_backtest.py --weights 50,25,25
"""

from __future__ import annotations

import argparse
import json
import math
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RECOMMENDED_WEIGHTS: tuple[float, float, float] = (0.50, 0.30, 0.20)
LEG_NAMES: tuple[str, str, str] = (
    "Global ETF Rotation",
    "Russell Top50 Leader Rotation",
    "Nasdaq 100 DCA",
)

# Global ETF Rotation constants (mirrors global_etf_rotation.py)
GLOBAL_ETF_RANKING_POOL: tuple[str, ...] = (
    "EWY", "EWT", "INDA", "FXI", "EWJ", "VGK",
    "VOO", "XLK", "SMH", "GLD", "SLV", "USO",
    "DBA", "XLE", "XLF", "ITA", "XLP", "XLU",
    "XLV", "IHI", "VNQ", "KRE",
)
GLOBAL_CANARY_ASSETS: tuple[str, ...] = ("SPY", "EFA", "EEM", "AGG")
GLOBAL_SAFE_HAVEN = "BIL"
GLOBAL_TOP_N = 2
GLOBAL_HOLD_BONUS = 0.02
GLOBAL_CANARY_BAD_THRESHOLD = 4
GLOBAL_REBALANCE_MONTHS: set[int] = {3, 6, 9, 12}

# Russell Top50 Leader Rotation constants (mirrors mega_cap_leader_rotation.py)
RUSSELL_BENCHMARK = "QQQ"
RUSSELL_BROAD_BENCHMARK = "SPY"
RUSSELL_SAFE_HAVEN = "BOXX"
RUSSELL_DYNAMIC_UNIVERSE_SIZE = 50
RUSSELL_HOLDINGS_COUNT = 4
RUSSELL_SINGLE_NAME_CAP = 0.25
RUSSELL_HOLD_BUFFER = 2
RUSSELL_HOLD_BONUS = 0.10

# Mega-cap stock pool for Russell Top50 simulation
MEGA_CAP_POOL: tuple[str, ...] = (
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
    "AVGO", "NFLX", "AMD", "COST", "JPM", "BRK-B", "LLY",
    "ORCL", "CRM", "ADBE", "CSCO", "QCOM", "TXN",
    "IBM", "INTC", "AMAT", "MU", "NOW", "UBER",
    "PLTR", "PANW", "MRVL", "PYPL", "BKNG", "GOOG",
)

# DCA constants
DCA_TARGET_SYMBOL = "QQQ"

# Benchmark
SPY_SYMBOL = "SPY"

# Warmup period (months of data needed for momentum computations)
DEFAULT_WARMUP_MONTHS = 24

# Period definitions for sub-period analysis
PERIODS: tuple[dict[str, Any], ...] = (
    {"label": "Full Period", "start": None, "end": None},
    {"label": "2022 Bear Market", "start": "2022-01-01", "end": "2022-12-31"},
    {"label": "2023-2026 Recovery", "start": "2023-01-01", "end": None},
)

PERIOD_METRICS_COLUMNS: tuple[str, ...] = (
    "Period", "Total Return", "CAGR", "Volatility", "Max Drawdown",
    "Sharpe", "Calmar", "Best Month", "Worst Month", "Win Rate",
)


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _normalize_symbol(value: str) -> str:
    return str(value or "").strip().upper()


def _compute_warmup_start(start_date: str, warmup_months: int = DEFAULT_WARMUP_MONTHS) -> str:
    """Compute an earlier start date that includes warmup history."""
    start = pd.Timestamp(start_date)
    warmup = start - pd.DateOffset(months=warmup_months)
    return warmup.strftime("%Y-%m-%d")


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def download_yfinance_prices(
    symbols: Sequence[str],
    start: str = "2015-01-01",
    end: str | None = None,
) -> pd.DataFrame:
    """Download daily close prices from Yahoo Finance using yfinance."""
    import yfinance as yf  # noqa: F811 — imported inside function for optional dep

    tickers = list(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
    end_str = end or datetime.now().strftime("%Y-%m-%d")
    data = yf.download(tickers, start=start, end=end_str, auto_adjust=True, progress=False)
    if data.empty:
        raise RuntimeError(f"No data returned from yfinance for symbols={tickers}")
    # yfinance returns a MultiIndex DataFrame; extract 'Close'
    if isinstance(data.columns, pd.MultiIndex):
        close_data = data["Close"].copy()
    else:
        close_data = data.copy()
    close_data = close_data.ffill().dropna(how="all")
    return close_data


def _close_series(price_matrix: pd.DataFrame, symbol: str) -> pd.Series:
    """Extract a single symbol's close series from the price matrix."""
    if symbol not in price_matrix.columns:
        return pd.Series(dtype=float)
    return price_matrix[symbol].dropna()


def _compute_sma(closes: pd.Series, period: int = 200) -> pd.Series:
    return closes.rolling(period).mean()


def _max_drawdown(equity_curve: pd.Series) -> float:
    running_max = equity_curve.cummax()
    dd = equity_curve / running_max - 1.0
    return float(dd.min())


def _compute_period_metrics(
    returns: pd.Series,
    label: str = "Period",
) -> dict[str, Any]:
    """Compute a full set of period metrics from daily returns."""
    returns = returns.dropna()
    if returns.empty:
        return {col: None for col in PERIOD_METRICS_COLUMNS}

    equity = (1.0 + returns).cumprod()
    total_return = float(equity.iloc[-1] - 1.0)
    days = (returns.index[-1] - returns.index[0]).days
    years = max(days / 365.25, 1.0 / 365.25)
    cagr = float(equity.iloc[-1] ** (1.0 / years) - 1.0)
    volatility = float(returns.std() * np.sqrt(252))
    max_dd = _max_drawdown(equity)
    sharpe = float(returns.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else float("nan")
    calmar = float(cagr / abs(max_dd)) if max_dd < 0 else float("nan")
    best_month = float(returns.resample("ME").sum().max()) if len(returns) > 20 else float("nan")
    worst_month = float(returns.resample("ME").sum().min()) if len(returns) > 20 else float("nan")
    win_rate = float((returns > 0).sum() / len(returns)) if len(returns) > 0 else float("nan")

    return {
        "Period": label,
        "Total Return": total_return,
        "CAGR": cagr,
        "Volatility": volatility,
        "Max Drawdown": max_dd,
        "Sharpe": sharpe,
        "Calmar": calmar,
        "Best Month": best_month,
        "Worst Month": worst_month,
        "Win Rate": win_rate,
    }


# ---------------------------------------------------------------------------
# Leg 1: Global ETF Rotation
# ---------------------------------------------------------------------------


def _compute_13612w_momentum(closes: pd.Series, as_of_date=None) -> float:
    """Compute Keller's 13612W momentum from monthly close prices."""
    if len(closes) < 253:
        return float("nan")
    me = closes.resample("ME").last().dropna()
    if as_of_date is not None:
        me = me[me.index <= pd.Timestamp(as_of_date)]
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


def _check_sma(closes: pd.Series, period: int = 200) -> bool:
    if len(closes) < period:
        return False
    return bool(closes.iloc[-1] > closes.iloc[-period:].mean())


def _global_etf_rotation_signal(
    price_matrix: pd.DataFrame,
    date: pd.Timestamp,
    current_holdings: set[str],
) -> dict[str, float]:
    """Compute Global ETF Rotation target weights for a given rebalance date.

    Returns dict of {symbol: weight} with 100% allocation.
    """
    # 1. Check canary assets
    n_bad = 0
    for ticker in GLOBAL_CANARY_ASSETS:
        closes = _close_series(price_matrix, ticker)
        if len(closes) == 0:
            n_bad += 1
            continue
        close_at_date = closes.loc[closes.index <= date]
        if len(close_at_date) < 253:
            n_bad += 1
            continue
        mom = _compute_13612w_momentum(close_at_date, as_of_date=date)
        if np.isnan(mom) or mom < 0:
            n_bad += 1

    if n_bad >= GLOBAL_CANARY_BAD_THRESHOLD:
        return {GLOBAL_SAFE_HAVEN: 1.0}

    # 2. Score ranking pool
    scores: dict[str, float] = {}
    for ticker in GLOBAL_ETF_RANKING_POOL:
        closes = _close_series(price_matrix, ticker)
        close_at_date = closes.loc[closes.index <= date]
        if len(close_at_date) < 253:
            continue
        mom = _compute_13612w_momentum(close_at_date, as_of_date=date)
        if np.isnan(mom):
            continue
        if not _check_sma(close_at_date, 200):
            continue
        bonus = GLOBAL_HOLD_BONUS if ticker in current_holdings else 0.0
        scores[ticker] = mom + bonus

    if not scores:
        return {GLOBAL_SAFE_HAVEN: 1.0}

    sorted_scores = sorted(scores.items(), key=lambda x: -x[1])
    top = sorted_scores[:GLOBAL_TOP_N]
    per_weight = 1.0 / GLOBAL_TOP_N
    weights: dict[str, float] = {t: per_weight for t, _ in top}

    # Fill safe haven for missing top spots
    if len(top) < GLOBAL_TOP_N:
        weights[GLOBAL_SAFE_HAVEN] = per_weight * (GLOBAL_TOP_N - len(top))

    return weights


def run_global_etf_rotation_leg(
    price_matrix: pd.DataFrame,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp | None = None,
) -> pd.Series:
    """Run Global ETF Rotation backtest leg and return daily returns.

    Uses the full price_matrix for warmup (momentum history) but only
    records returns from start_date onward.
    """
    all_dates = price_matrix.index.sort_values()
    if len(all_dates) < 2:
        return pd.Series(dtype=float)

    returns_all = pd.Series(0.0, index=all_dates, name="global_etf_return")
    current_weights: dict[str, float] = {GLOBAL_SAFE_HAVEN: 1.0}
    current_holdings: set[str] = set()

    rebalance_set = {
        date
        for date in all_dates
        if date.month in GLOBAL_REBALANCE_MONTHS
        and _is_last_trading_day_of_month(price_matrix, date)
    }

    for i in range(len(all_dates) - 1):
        date = all_dates[i]
        next_date = all_dates[i + 1]

        if date in rebalance_set:
            current_weights = _global_etf_rotation_signal(price_matrix, date, current_holdings)
            current_holdings = {
                sym for sym, w in current_weights.items()
                if w > 0 and sym != GLOBAL_SAFE_HAVEN
            }

        # Compute daily return
        next_close = price_matrix.loc[next_date] if next_date in price_matrix.index else None
        prev_close = price_matrix.loc[date] if date in price_matrix.index else None
        daily_return = 0.0
        for symbol, weight in current_weights.items():
            if symbol == GLOBAL_SAFE_HAVEN:
                daily_return += weight * 0.0  # Cash/BIL ~0%
            else:
                if next_close is not None and prev_close is not None and symbol in price_matrix.columns:
                    nc = float(next_close.get(symbol, float("nan")))
                    pc = float(prev_close.get(symbol, float("nan")))
                    if not np.isnan(nc) and not np.isnan(pc) and pc > 0:
                        daily_return += weight * (nc / pc - 1.0)
        returns_all.at[next_date] = daily_return

    # Filter to requested date range
    mask = returns_all.index >= start_date
    if end_date is not None:
        mask = mask & (returns_all.index <= end_date)
    return returns_all[mask]


# ---------------------------------------------------------------------------
# Leg 2: Russell Top50 Leader Rotation (simplified multi-factor)
# ---------------------------------------------------------------------------


def _zscore(values: pd.Series) -> pd.Series:
    std = float(values.std(ddof=0))
    if pd.isna(std) or std == 0:
        return pd.Series(0.0, index=values.index, dtype=float)
    return ((values - values.mean()) / std).fillna(0.0)


def _rank_candidates(
    price_matrix: pd.DataFrame,
    date: pd.Timestamp,
    current_holdings: set[str],
) -> list[tuple[str, float]]:
    """Score mega-cap candidates using multi-factor ranking.

    Returns list of (symbol, score) sorted descending.
    """
    scores: list[tuple[str, float, float, float, str]] = []  # (symbol, score, mom_6m, rel_mom, sector)
    for symbol in MEGA_CAP_POOL:
        closes = _close_series(price_matrix, symbol)
        close_at_date = closes.loc[closes.index <= date]
        if len(close_at_date) < 273:
            continue
        c = close_at_date.iloc[-1]
        # Compute features
        mom_3m = c / close_at_date.iloc[-63] - 1.0 if len(close_at_date) >= 63 else float("nan")
        mom_6m = c / close_at_date.iloc[-126] - 1.0 if len(close_at_date) >= 126 else float("nan")
        mom_12_1 = (
            close_at_date.iloc[-21] / close_at_date.iloc[-273] - 1.0
            if len(close_at_date) >= 274
            else float("nan")
        )
        sma200 = close_at_date.rolling(200).mean().iloc[-1] if len(close_at_date) >= 200 else float("nan")
        high252 = close_at_date.rolling(252).max().iloc[-1] if len(close_at_date) >= 252 else float("nan")
        returns_63 = close_at_date.pct_change().iloc[-63:].std() * np.sqrt(252) if len(close_at_date) >= 63 else float("nan")
        maxdd_126 = _max_drawdown(close_at_date.iloc[-126:]) if len(close_at_date) >= 126 else float("nan")

        # Benchmarks
        spy_close = _close_series(price_matrix, "SPY")
        spy_at_date = spy_close.loc[spy_close.index <= date]
        spy_mom_6m = (
            spy_at_date.iloc[-1] / spy_at_date.iloc[-126] - 1.0 if len(spy_at_date) >= 126 else float("nan")
        )
        qqq_close = _close_series(price_matrix, "QQQ")
        qqq_at_date = qqq_close.loc[qqq_close.index <= date]
        qqq_mom_6m = (
            qqq_at_date.iloc[-1] / qqq_at_date.iloc[-126] - 1.0 if len(qqq_at_date) >= 126 else float("nan")
        )
        rel_mom_vs_qqq = mom_6m - qqq_mom_6m if not np.isnan(mom_6m) and not np.isnan(qqq_mom_6m) else float("nan")
        rel_mom_vs_spy = mom_6m - spy_mom_6m if not np.isnan(mom_6m) and not np.isnan(spy_mom_6m) else float("nan")
        sma200_gap = c / sma200 - 1.0 if not np.isnan(sma200) and sma200 > 0 else float("nan")
        high252_gap = c / high252 - 1.0 if not np.isnan(high252) and high252 > 0 else float("nan")

        # Skip if too many missing features
        required = [mom_3m, mom_6m, mom_12_1, rel_mom_vs_qqq, rel_mom_vs_spy, sma200_gap, high252_gap, returns_63, maxdd_126]
        if any(np.isnan(v) for v in required):
            continue

        score = (
            mom_6m * 0.25
            + mom_3m * 0.20
            + rel_mom_vs_qqq * 0.20
            + rel_mom_vs_spy * 0.10
            + high252_gap * 0.10
            + sma200_gap * 0.10
            - returns_63 * 0.025
            - abs(maxdd_126) * 0.025
        )
        if symbol in current_holdings:
            score += RUSSELL_HOLD_BONUS
        scores.append((symbol, score, mom_6m, rel_mom_vs_qqq, ""))

    # Sort and return ranked symbols with scores
    scores.sort(key=lambda x: (-x[1], -x[2], -x[3], x[0]))
    return [(s[0], s[1]) for s in scores]


def _is_last_trading_day_of_month(price_matrix: pd.DataFrame, date: pd.Timestamp) -> bool:
    """Check if date is the last trading day of its month in the price matrix."""
    month_dates = price_matrix.index[price_matrix.index.month == date.month]
    return len(month_dates) > 0 and date == month_dates[-1]


def run_russell_leader_rotation_leg(
    price_matrix: pd.DataFrame,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp | None = None,
) -> pd.Series:
    """Run Russell Top50 Leader Rotation backtest leg and return daily returns.

    Uses the full price_matrix for warmup (momentum history) but only
    records returns from start_date onward.
    """
    all_dates = price_matrix.index.sort_values()
    if len(all_dates) < 2:
        return pd.Series(dtype=float)

    returns_all = pd.Series(0.0, index=all_dates, name="russell_return")
    current_weights: dict[str, float] = {RUSSELL_SAFE_HAVEN: 1.0}
    current_holdings: set[str] = set()

    # Monthly rebalance on last trading day
    rebalance_set = set()
    for period in all_dates.to_period("M").unique():
        mask = all_dates.to_period("M") == period
        rebalance_set.add(all_dates[mask][-1])

    for i in range(len(all_dates) - 1):
        date = all_dates[i]
        next_date = all_dates[i + 1]

        # Determine stock exposure based on benchmark trend
        stock_exposure = 1.0
        if date in rebalance_set:
            # Check benchmark trend (QQQ above 200-day MA)
            qqq_closes = _close_series(price_matrix, RUSSELL_BENCHMARK)
            qqq_at_date = qqq_closes.loc[qqq_closes.index <= date]
            if len(qqq_at_date) >= 200:
                qqq_sma200 = qqq_at_date.iloc[-200:].mean()
                benchmark_trend_positive = float(qqq_at_date.iloc[-1]) > qqq_sma200
            else:
                benchmark_trend_positive = True

            if not benchmark_trend_positive:
                stock_exposure = 0.50  # soft defense

            ranked = _rank_candidates(price_matrix, date, current_holdings)
            if ranked:
                selected = [s for s, _ in ranked[:RUSSELL_HOLDINGS_COUNT]]
                per_weight = min(RUSSELL_SINGLE_NAME_CAP, stock_exposure / len(selected))
                new_weights: dict[str, float] = {s: per_weight for s in selected}
                invested = sum(new_weights.values())
                safe_weight = max(0.0, 1.0 - invested)
                if safe_weight > 1e-12:
                    new_weights[RUSSELL_SAFE_HAVEN] = safe_weight
                current_weights = new_weights
                current_holdings = {s for s in selected}
            else:
                current_weights = {RUSSELL_SAFE_HAVEN: 1.0}
                current_holdings = set()

        # Compute daily return
        next_close = price_matrix.loc[next_date] if next_date in price_matrix.index else None
        prev_close = price_matrix.loc[date] if date in price_matrix.index else None
        daily_return = 0.0
        for symbol, weight in current_weights.items():
            if symbol == RUSSELL_SAFE_HAVEN:
                daily_return += weight * 0.0  # BOXX ~0%
            else:
                if next_close is not None and prev_close is not None and symbol in price_matrix.columns:
                    nc = float(next_close.get(symbol, float("nan")))
                    pc = float(prev_close.get(symbol, float("nan")))
                    if not np.isnan(nc) and not np.isnan(pc) and pc > 0:
                        daily_return += weight * (nc / pc - 1.0)
        returns_all.at[next_date] = daily_return

    # Filter to requested date range
    mask = returns_all.index >= start_date
    if end_date is not None:
        mask = mask & (returns_all.index <= end_date)
    return returns_all[mask]


# ---------------------------------------------------------------------------
# Leg 3: Nasdaq 100 DCA (dollar-cost averaging into QQQ)
# ---------------------------------------------------------------------------


def run_nasdaq_dca_leg(
    price_matrix: pd.DataFrame,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp | None = None,
    monthly_contribution: float = 1.0,
) -> pd.Series:
    """Run DCA simulation: invest fixed amount monthly into QQQ.

    Returns daily total return series of the accumulated DCA position,
    filtered to the requested date range.
    """
    qqq_series = _close_series(price_matrix, DCA_TARGET_SYMBOL)
    all_dates = qqq_series.index.sort_values()
    if len(all_dates) < 2:
        return pd.Series(dtype=float)

    cash = 0.0
    shares = 0.0
    last_month = ""
    position_values: list[float] = []

    for date in all_dates:
        month_key = date.strftime("%Y-%m")
        price = float(qqq_series.loc[date])

        if month_key != last_month:
            cash += monthly_contribution
            last_month = month_key

        if cash > 0 and price > 0:
            shares += cash / price
            cash = 0.0

        position_values.append(shares * price + cash)

    position_series = pd.Series(position_values, index=all_dates, name="dca_value")
    dca_returns_all = position_series.pct_change().fillna(0.0)
    dca_returns_all.name = "dca_return"

    # Filter to requested date range
    mask = dca_returns_all.index >= start_date
    if end_date is not None:
        mask = mask & (dca_returns_all.index <= end_date)
    return dca_returns_all[mask]


# ---------------------------------------------------------------------------
# Dynamic mode: SPY 200-day MA risk signal
# ---------------------------------------------------------------------------


def compute_dynamic_exposure_multiplier(
    price_matrix: pd.DataFrame,
    date: pd.Timestamp,
    period: int = 200,
    reduction_pct: float = 0.30,
) -> float:
    """Compute risk multiplier: 1.0 if SPY > 200-day MA, else 1.0 - reduction_pct."""
    spy_closes = _close_series(price_matrix, SPY_SYMBOL)
    spy_at_date = spy_closes.loc[spy_closes.index <= date]
    if len(spy_at_date) < period:
        return 1.0
    sma = spy_at_date.iloc[-period:].mean()
    if float(spy_at_date.iloc[-1]) > sma:
        return 1.0
    return 1.0 - reduction_pct


# ---------------------------------------------------------------------------
# Combo Portfolio
# ---------------------------------------------------------------------------


@dataclass
class ComboResult:
    mode: str
    weights: tuple[float, float, float]
    daily_returns: pd.Series
    period_metrics: list[dict[str, Any]]
    leg_returns: dict[str, pd.Series] = field(default_factory=dict)


def run_combo(
    price_matrix: pd.DataFrame,
    *,
    weights: tuple[float, float, float] = RECOMMENDED_WEIGHTS,
    mode: str = "static",
    start_date: str = "2015-01-01",
    end_date: str | None = None,
) -> ComboResult:
    """Run the 3-leg combo backtest and return combined results."""
    start = pd.Timestamp(start_date).tz_localize(None).normalize()
    end = pd.Timestamp(end_date).tz_localize(None).normalize() if end_date else None

    # Run each leg independently
    print("  Running Global ETF Rotation leg...", flush=True)
    global_returns = run_global_etf_rotation_leg(price_matrix, start, end)
    print("  Running Russell Top50 Leader Rotation leg...", flush=True)
    russell_returns = run_russell_leader_rotation_leg(price_matrix, start, end)
    print("  Running Nasdaq 100 DCA leg...", flush=True)
    dca_returns = run_nasdaq_dca_leg(price_matrix, start, end)

    # Align all leg returns to common index
    all_legs = {"global_etf": global_returns, "russell": russell_returns, "dca": dca_returns}
    common_idx = global_returns.index.intersection(russell_returns.index).intersection(dca_returns.index)
    if len(common_idx) < 2:
        raise RuntimeError(f"Insufficient overlapping dates across legs: {len(common_idx)}")

    # Combine
    w1, w2, w3 = weights
    combo_returns = pd.Series(0.0, index=common_idx, name="combo_return")

    for date in common_idx[1:]:
        r1 = float(global_returns.loc[date])
        r2 = float(russell_returns.loc[date])
        r3 = float(dca_returns.loc[date])

        # Dynamic risk overlay: reduce stock legs when SPY is below 200-day MA
        if mode == "dynamic":
            mult = compute_dynamic_exposure_multiplier(price_matrix, common_idx[common_idx < date][-1] if len(common_idx[common_idx < date]) > 0 else date)
            combo_returns.at[date] = w1 * mult * r1 + w2 * mult * r2 + w3 * r3 + (1.0 - w1 * mult - w2 * mult - w3) * 0.0
        else:
            combo_returns.at[date] = w1 * r1 + w2 * r2 + w3 * r3

    # Compute period metrics
    period_metrics: list[dict[str, Any]] = []
    for period_def in PERIODS:
        period_label = period_def["label"]
        p_start = pd.Timestamp(period_def["start"]) if period_def["start"] else combo_returns.index[0]
        p_end = pd.Timestamp(period_def["end"]) if period_def["end"] else combo_returns.index[-1]
        mask = (combo_returns.index >= p_start) & (combo_returns.index <= p_end)
        period_returns = combo_returns[mask].dropna()
        if len(period_returns) > 0:
            metrics = _compute_period_metrics(period_returns, label=period_label)
            period_metrics.append(metrics)

    return ComboResult(
        mode=mode,
        weights=weights,
        daily_returns=combo_returns,
        period_metrics=period_metrics,
        leg_returns={k: v.reindex(common_idx).fillna(0.0) for k, v in all_legs.items()},
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="US Equity Combo Backtest: Static vs Dynamic allocation comparison."
    )
    parser.add_argument(
        "--mode",
        choices=["static", "dynamic", "both"],
        default="both",
        help="Which mode(s) to run (default: both)",
    )
    parser.add_argument(
        "--weights",
        default=None,
        help="Comma-separated weights for global_etf,russell,dca (default: 50,30,20). "
        "For sweep mode you can pass multiple sets separated by semicolons, e.g. 50,30,20;40,30,30",
    )
    parser.add_argument(
        "--start",
        default="2015-01-01",
        help="Backtest start date (default: 2015-01-01)",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="Backtest end date (default: today)",
    )
    parser.add_argument(
        "--json-output",
        default=None,
        help="Optional file path to save results as JSON",
    )
    parser.add_argument(
        "--symbols-file",
        default=None,
        help="Optional CSV file with pre-downloaded price data (symbol=columns, date=index)",
    )
    return parser


def parse_weight_sets(raw: str | None) -> list[tuple[float, float, float]]:
    """Parse comma-separated weights or semicolon-separated weight sets."""
    if raw is None:
        return [RECOMMENDED_WEIGHTS]
    raw = str(raw).strip()
    if ";" in raw:
        sets_raw = raw.split(";")
    else:
        sets_raw = [raw]
    result: list[tuple[float, float, float]] = []
    for s in sets_raw:
        parts = [float(x.strip()) for x in s.split(",") if x.strip()]
        if len(parts) == 3:
            total = sum(parts)
            result.append(tuple(p / total for p in parts))
        else:
            print(f"  Warning: skipping invalid weight spec '{s}' (need 3 values)", flush=True)
    if not result:
        result = [RECOMMENDED_WEIGHTS]
    return result


def _format_pct(v: float | None, width: int = 10) -> str:
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return f"{'N/A':>{width}}"
    return f"{v:>{width}.2%}"


def _format_float(v: float | None, width: int = 10) -> str:
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return f"{'N/A':>{width}}"
    return f"{v:>{width}.2f}"


def print_metrics_table(
    metrics: list[dict[str, Any]],
    title: str,
) -> None:
    """Print a formatted metrics table."""
    print(f"\n{'=' * 90}", flush=True)
    print(f"  {title}", flush=True)
    print(f"{'=' * 90}", flush=True)

    header = (
        f"{'Period':<25}"
        f"{'Total Return':>14}{'CAGR':>10}{'Volatility':>12}"
        f"{'Max DD':>10}{'Sharpe':>8}{'Win Rate':>10}"
    )
    print(header, flush=True)
    print("-" * len(header), flush=True)
    for m in metrics:
        row = (
            f"{str(m.get('Period', '')):<25}"
            f"{_format_pct(m.get('Total Return')):>14}"
            f"{_format_pct(m.get('CAGR')):>10}"
            f"{_format_pct(m.get('Volatility')):>12}"
            f"{_format_pct(m.get('Max Drawdown')):>10}"
            f"{_format_float(m.get('Sharpe')):>8}"
            f"{_format_pct(m.get('Win Rate')):>10}"
        )
        print(row, flush=True)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    modes: list[str] = ["static", "dynamic"] if args.mode == "both" else [args.mode]
    weight_sets = parse_weight_sets(args.weights)

    # --- Download data ---
    print("=" * 80, flush=True)
    print("  US Equity Combo Backtest", flush=True)
    print("=" * 80, flush=True)

    all_symbols: list[str] = list(
        dict.fromkeys(
            [SPY_SYMBOL, DCA_TARGET_SYMBOL, RUSSELL_BENCHMARK, RUSSELL_SAFE_HAVEN, GLOBAL_SAFE_HAVEN]
            + list(MEGA_CAP_POOL)
            + list(GLOBAL_ETF_RANKING_POOL)
            + list(GLOBAL_CANARY_ASSETS)
        )
    )

    if args.symbols_file:
        print(f"\n  Loading price data from: {args.symbols_file}", flush=True)
        price_matrix = pd.read_csv(args.symbols_file, index_col=0, parse_dates=True)
    else:
        # Download with 2-year warmup before the requested start date
        warmup_start = _compute_warmup_start(args.start)
        print(f"\n  Downloading {len(all_symbols)} symbols from Yahoo Finance...", flush=True)
        print(f"    Start: {args.start} (warmup from {warmup_start}), End: {args.end or 'today'}", flush=True)
        price_matrix = download_yfinance_prices(all_symbols, start=warmup_start, end=args.end)
        print(f"    Downloaded {len(price_matrix)} trading days x {len(price_matrix.columns)} symbols", flush=True)

    print(f"\n  Price matrix: {price_matrix.shape[0]} rows x {price_matrix.shape[1]} columns", flush=True)
    print(f"  Date range: {price_matrix.index[0].date()} to {price_matrix.index[-1].date()}", flush=True)

    # --- Run backtest ---
    all_results: list[ComboResult] = []
    for ws in weight_sets:
        print(f"\n{'─' * 80}", flush=True)
        ws_pct = [f"{w:.0%}" for w in ws]
        print(f"  Weights: Global ETF={ws_pct[0]}, Russell={ws_pct[1]}, Nasdaq100 DCA={ws_pct[2]}", flush=True)
        print(f"{'─' * 80}", flush=True)

        for mode in modes:
            print(f"\n  Mode: {mode.upper()}", flush=True)
            result = run_combo(
                price_matrix,
                weights=ws,
                mode=mode,
                start_date=args.start,
                end_date=args.end,
            )
            all_results.append(result)
            print_metrics_table(result.period_metrics, f"Combo ({mode}) - {ws_pct[0]}/{ws_pct[1]}/{ws_pct[2]}")

    # --- Compare static vs dynamic for recommended weights ---
    if len(modes) == 2 and len(weight_sets) == 1:
        static_result = all_results[0]
        dynamic_result = all_results[1]
        print(f"\n{'=' * 90}", flush=True)
        print("  STATIC vs DYNAMIC COMPARISON", flush=True)
        print(f"{'=' * 90}", flush=True)

        header = (
            f"{'Period':<25}"
            f"{'Static TR':>12}{'Dynamic TR':>12}{'Diff':>10}"
            f"{'Static MDD':>12}{'Dynamic MDD':>12}"
        )
        print(header, flush=True)
        print("-" * len(header), flush=True)

        for sm, dm in zip(static_result.period_metrics, dynamic_result.period_metrics, strict=False):
            label = str(sm.get("Period", ""))
            s_tr = sm.get("Total Return")
            d_tr = dm.get("Total Return")
            diff = (d_tr - s_tr) if s_tr is not None and d_tr is not None and not (isinstance(s_tr, float) and math.isnan(s_tr)) and not (isinstance(d_tr, float) and math.isnan(d_tr)) else None
            s_mdd = sm.get("Max Drawdown")
            d_mdd = dm.get("Max Drawdown")
            row = (
                f"{label:<25}"
                f"{_format_pct(s_tr):>12}"
                f"{_format_pct(d_tr):>12}"
                + (f"{_format_pct(diff):>12}" if diff is not None else f"{'N/A':>12}")
                + f"{_format_pct(s_mdd):>12}"
                + f"{_format_pct(d_mdd):>12}"
            )
            print(row, flush=True)

    # --- Weight sweep results ---
    if len(weight_sets) > 1:
        print(f"\n{'=' * 90}", flush=True)
        print("  WEIGHT SWEEP SUMMARY", flush=True)
        print(f"{'=' * 90}", flush=True)
        header = (
            f"{'Weights':<25}"
            f"{'Mode':<10}"
            f"{'Total Return':>14}"
            f"{'CAGR':>10}"
            f"{'Max DD':>10}"
            f"{'Sharpe':>8}"
        )
        print(header, flush=True)
        print("-" * len(header), flush=True)

        for result in all_results:
            ws_pct_str = "/".join(f"{w:.0%}" for w in result.weights)
            full_metrics = [m for m in result.period_metrics if m.get("Period") == "Full Period"]
            if full_metrics:
                m = full_metrics[0]
                row = (
                    f"{ws_pct_str:<25}"
                    f"{result.mode:<10}"
                    f"{_format_pct(m.get('Total Return')):>14}"
                    f"{_format_pct(m.get('CAGR')):>10}"
                    f"{_format_pct(m.get('Max Drawdown')):>10}"
                    f"{_format_float(m.get('Sharpe')):>8}"
                )
                print(row, flush=True)

    # --- JSON output ---
    if args.json_output:
        output: dict[str, Any] = {
            "generated_at": datetime.now().isoformat(),
            "config": {
                "modes": modes,
                "weight_sets": [[float(f"{w:.4f}") for w in ws] for ws in weight_sets],
                "start_date": args.start,
                "end_date": args.end,
            },
            "results": [],
        }
        for result in all_results:
            result_dict: dict[str, Any] = {
                "mode": result.mode,
                "weights": [float(f"{w:.4f}") for w in result.weights],
                "period_metrics": result.period_metrics,
                "daily_returns": [
                    {"date": str(d.date()), "return": float(f"{v:.8f}")}
                    for d, v in result.daily_returns.items()
                    if not (isinstance(v, float) and math.isnan(v))
                ],
            }
            if result.leg_returns:
                result_dict["leg_returns"] = {
                    name: [float(f"{v:.8f}") for _, v in series.items() if not (isinstance(v, float) and math.isnan(v))]
                    for name, series in result.leg_returns.items()
                }
            output["results"].append(result_dict)

        output_path = Path(args.json_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(_json_safe(output), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\n  Saved results to: {output_path}", flush=True)

    # --- Leg comparison ---
    if "both" in args.mode or args.mode == "static":
        ref_result = all_results[0]
        if ref_result.leg_returns:
            print(f"\n{'=' * 90}", flush=True)
            print("  INDIVIDUAL LEG PERFORMANCE (Full Period)", flush=True)
            print(f"{'=' * 90}", flush=True)
            header = (
                f"{'Leg':<30}"
                f"{'Total Return':>14}{'CAGR':>10}"
                f"{'Max DD':>10}{'Sharpe':>8}"
            )
            print(header, flush=True)
            print("-" * len(header), flush=True)
            for leg_name, leg_ret in ref_result.leg_returns.items():
                leg_ret_clean = leg_ret.dropna()
                if len(leg_ret_clean) > 0:
                    m = _compute_period_metrics(leg_ret_clean, label=leg_name.replace("_", " ").title())
                    row = (
                        f"{str(m.get('Period', '')):<30}"
                        f"{_format_pct(m.get('Total Return')):>14}"
                        f"{_format_pct(m.get('CAGR')):>10}"
                        f"{_format_pct(m.get('Max Drawdown')):>10}"
                        f"{_format_float(m.get('Sharpe')):>8}"
                    )
                    print(row, flush=True)

    print(f"\n{'=' * 80}", flush=True)
    print("  Done.", flush=True)
    print(f"{'=' * 80}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
