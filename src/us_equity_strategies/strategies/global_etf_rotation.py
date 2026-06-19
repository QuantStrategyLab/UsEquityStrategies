from __future__ import annotations

import time
from datetime import datetime
from importlib import import_module
from typing import Any, Callable

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

SIGNAL_SOURCE = "feature_snapshot"
SNAPSHOT_CONTRACT_VERSION = "global_etf_rotation.feature_snapshot.v1"
REQUIRED_FEATURE_COLUMNS = frozenset(
    {
        "as_of",
        "symbol",
        "role",
        "close",
        "momentum_13612w",
        "sma_pass",
        "eligible",
        "vol_126",
    }
)
SNAPSHOT_DATE_COLUMNS = ("as_of", "snapshot_date")
MAX_SNAPSHOT_MONTH_LAG = 1
REQUIRE_SNAPSHOT_MANIFEST = True


def _coerce_bool(value: Any) -> bool:
    if pd.isna(value):
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    return bool(normalized)


def _to_feature_frame(feature_snapshot) -> pd.DataFrame:
    frame = (
        feature_snapshot.copy()
        if isinstance(feature_snapshot, pd.DataFrame)
        else pd.DataFrame(list(feature_snapshot))
    )
    if frame.empty:
        raise ValueError("feature_snapshot must contain at least one row")
    missing = REQUIRED_FEATURE_COLUMNS - set(frame.columns)
    if missing:
        missing_text = ", ".join(sorted(missing))
        raise ValueError(f"feature_snapshot missing required columns: {missing_text}")
    frame = frame.copy()
    frame["symbol"] = frame["symbol"].astype(str).str.strip().str.upper()
    frame["role"] = frame["role"].astype(str).str.strip().str.lower()
    frame["momentum_13612w"] = pd.to_numeric(frame["momentum_13612w"], errors="coerce")
    frame["score"] = pd.to_numeric(frame.get("score", frame["momentum_13612w"]), errors="coerce")
    frame["vol_126"] = pd.to_numeric(frame["vol_126"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["sma_pass"] = frame["sma_pass"].map(_coerce_bool)
    frame["eligible"] = frame["eligible"].map(_coerce_bool)
    return frame


def extract_managed_symbols(
    feature_snapshot,
    *,
    benchmark_symbol: str | None = None,
    safe_haven_symbol: str | None = None,
) -> tuple[str, ...]:
    frame = _to_feature_frame(feature_snapshot)
    symbols: list[str] = []
    for symbol in frame.loc[frame["role"].isin({"ranking_pool_etf", "safe_haven"}), "symbol"]:
        if symbol and symbol not in symbols:
            symbols.append(str(symbol))
    safe = str(safe_haven_symbol or "").strip().upper()
    if safe and safe not in symbols:
        symbols.append(safe)
    return tuple(symbols)


def _snapshot_rebalance_day(as_of_date, *, rebalance_months) -> bool:
    tz_ny = pytz.timezone("America/New_York")
    timestamp = pd.Timestamp(as_of_date)
    if timestamp.tzinfo is None:
        now_ny = tz_ny.localize(timestamp.to_pydatetime())
    else:
        now_ny = timestamp.tz_convert(tz_ny).to_pydatetime()
    return _is_rebalance_day(now_ny, rebalance_months=rebalance_months)


def _snapshot_confidence_weighting(
    rows: pd.DataFrame,
    top: list[tuple[str, float]],
    *,
    top_n: int,
    confidence_metric: str,
    confidence_threshold: float,
    confidence_top1_weight: float,
    confidence_volatility_gate_enabled: bool,
    confidence_volatility_max_ratio: float,
) -> tuple[dict[str, float], str]:
    per_weight = 1.0 / float(top_n)
    weights = {ticker: per_weight for ticker, _score in top}
    if top_n != 2 or len(top) != 2:
        return weights, ""
    score_pairs = [(str(row.symbol), float(row.rank_score)) for row in rows.itertuples(index=False)]
    confidence = _score_confidence(score_pairs, metric=str(confidence_metric))
    top1, top2 = top[0][0], top[1][0]
    use_confidence_weight = not np.isnan(confidence) and confidence >= float(confidence_threshold)
    top1_vol = top2_vol = float("nan")
    if use_confidence_weight and confidence_volatility_gate_enabled:
        vol_by_symbol = rows.set_index("symbol")["vol_126"].to_dict()
        top1_vol = float(vol_by_symbol.get(top1, float("nan")))
        top2_vol = float(vol_by_symbol.get(top2, float("nan")))
        use_confidence_weight = not (
            np.isnan(top1_vol)
            or np.isnan(top2_vol)
            or top2_vol <= 0.0
            or top1_vol > top2_vol * float(confidence_volatility_max_ratio)
        )
    if not use_confidence_weight:
        return weights, ""
    top1_weight = min(1.0, max(per_weight, float(confidence_top1_weight)))
    weights = {top1: top1_weight, top2: 1.0 - top1_weight}
    note = f"confidence={confidence:.2f}"
    if confidence_volatility_gate_enabled:
        note += f" vol={top1_vol:.2%}/{top2_vol:.2%}"
    return weights, note


def compute_signals_from_feature_snapshot(
    feature_snapshot,
    current_holdings,
    *,
    as_of_date=None,
    ranking_pool=RANKING_POOL,
    canary_assets=CANARY_ASSETS,
    safe_haven: str = SAFE_HAVEN,
    top_n: int = TOP_N,
    hold_bonus: float = HOLD_BONUS,
    canary_bad_threshold: int = CANARY_BAD_THRESHOLD,
    rebalance_months=REBALANCE_MONTHS,
    translator: Callable,
    sma_period: int = SMA_PERIOD,
    confidence_weighting_enabled: bool = False,
    confidence_metric: str = CONFIDENCE_METRIC_Z_GAP,
    confidence_threshold: float = 1.0,
    confidence_top1_weight: float = 0.75,
    confidence_volatility_gate_enabled: bool = False,
    confidence_volatility_window: int = 126,
    confidence_volatility_max_ratio: float = 1.3,
):
    del sma_period, confidence_volatility_window
    frame = _to_feature_frame(feature_snapshot)
    safe_haven = str(safe_haven or SAFE_HAVEN).strip().upper()
    snapshot_ranking_symbols = frame.loc[frame["role"].eq("ranking_pool_etf"), "symbol"].dropna().astype(str).tolist()
    snapshot_canary_symbols = frame.loc[frame["role"].eq("canary_asset"), "symbol"].dropna().astype(str).tolist()
    ranking_symbols = snapshot_ranking_symbols or [symbol.upper() for symbol in ranking_pool]
    canary_symbols = snapshot_canary_symbols or [symbol.upper() for symbol in canary_assets]
    current_holding_symbols = {str(symbol or "").strip().upper() for symbol in current_holdings or ()}

    by_symbol = frame.drop_duplicates(subset=["symbol"], keep="last").set_index("symbol")
    n_bad = 0
    canary_details: list[str] = []
    for ticker in canary_symbols:
        if ticker not in by_symbol.index:
            n_bad += 1
            canary_details.append(f"{ticker}:❌(no data)")
            continue
        mom = float(by_symbol.at[ticker, "momentum_13612w"])
        if np.isnan(mom) or mom < 0:
            n_bad += 1
            canary_details.append(f"{ticker}:❌({mom:.3f})" if not np.isnan(mom) else f"{ticker}:❌(nan)")
        else:
            canary_details.append(f"{ticker}:✅({mom:.3f})")
    canary_str = ", ".join(canary_details)
    if n_bad >= int(canary_bad_threshold):
        signal_desc = translator("emergency", n_bad=n_bad, safe=safe_haven)
        return {safe_haven: 1.0}, signal_desc, True, canary_str

    rebalance_as_of = as_of_date if as_of_date is not None else pd.Timestamp.now(tz=pytz.timezone("America/New_York"))
    if not _snapshot_rebalance_day(rebalance_as_of, rebalance_months=rebalance_months):
        signal_desc = translator("daily_check")
        return None, signal_desc, False, canary_str

    rows = frame.loc[frame["symbol"].isin(ranking_symbols)].copy()
    rows = rows.loc[rows["eligible"] & rows["sma_pass"] & rows["momentum_13612w"].notna()].copy()
    if rows.empty:
        signal_desc = translator("emergency", n_bad="SMA", safe=safe_haven)
        return {safe_haven: 1.0}, signal_desc, False, canary_str
    rows["rank_score"] = rows["score"].fillna(rows["momentum_13612w"])
    rows.loc[rows["symbol"].isin(current_holding_symbols), "rank_score"] += float(hold_bonus)
    ranked = rows.sort_values(["rank_score", "momentum_13612w", "symbol"], ascending=[False, False, True])
    top = [(str(row.symbol), float(row.rank_score)) for row in ranked.head(int(top_n)).itertuples(index=False)]
    if not top:
        signal_desc = translator("emergency", n_bad="SMA", safe=safe_haven)
        return {safe_haven: 1.0}, signal_desc, False, canary_str

    weights = {ticker: 1.0 / float(top_n) for ticker, _score in top}
    confidence_note = ""
    if confidence_weighting_enabled:
        weights, confidence_note = _snapshot_confidence_weighting(
            ranked,
            top,
            top_n=int(top_n),
            confidence_metric=str(confidence_metric),
            confidence_threshold=float(confidence_threshold),
            confidence_top1_weight=float(confidence_top1_weight),
            confidence_volatility_gate_enabled=bool(confidence_volatility_gate_enabled),
            confidence_volatility_max_ratio=float(confidence_volatility_max_ratio),
        )
    if len(top) < int(top_n):
        weights[safe_haven] = weights.get(safe_haven, 0.0) + (1.0 / float(top_n)) * (int(top_n) - len(top))
    selected = ", ".join(f"{ticker}({score:.3f})" for ticker, score in top)
    signal_desc = f"Global ETF snapshot rotation selected: {selected}"
    if confidence_note:
        signal_desc = f"{signal_desc} | {confidence_note}"
    return weights, signal_desc, False, canary_str
