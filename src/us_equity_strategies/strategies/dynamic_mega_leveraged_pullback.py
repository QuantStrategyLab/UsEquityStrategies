from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

import pandas as pd

SIGNAL_SOURCE = "feature_snapshot+daily_market_history"
STATUS_ICON = "2x"
PROFILE_NAME = "dynamic_mega_leveraged_pullback"
BENCHMARK_SYMBOL = "QQQ"
SAFE_HAVEN = "BOXX"
DEFAULT_CANDIDATE_UNIVERSE_SIZE = 15
DEFAULT_HOLDINGS_COUNT = 3
DEFAULT_SINGLE_NAME_CAP = 0.25
DEFAULT_MIN_POSITION_VALUE_USD = 3_000.0
DEFAULT_HOLD_BUFFER = 1
DEFAULT_HOLD_BONUS = 0.05
DEFAULT_MAX_PRODUCT_EXPOSURE = 0.7
DEFAULT_LEVERAGE_MULTIPLE = 2.0
DEFAULT_MIN_ADV20_USD = 20_000_000.0
DEFAULT_ATR_PERIOD = 14
DEFAULT_SMA_PERIOD = 200
DEFAULT_ATR_ENTRY_SCALE = 2.5
DEFAULT_ENTRY_LINE_FLOOR = 1.04
DEFAULT_ENTRY_LINE_CAP = 1.08
DEFAULT_ATR_EXIT_SCALE = 0.0
DEFAULT_EXIT_LINE_FLOOR = 1.02
DEFAULT_EXIT_LINE_CAP = 1.02
DEFAULT_HISTORY_DURATION = "2 Y"
DEFAULT_HISTORY_BAR_SIZE = "1 day"
DEFAULT_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS = 3
DEFAULT_EXECUTION_CASH_RESERVE_RATIO = 0.0
DEFAULT_CONFIG: dict[str, object] = {
    "benchmark_symbol": BENCHMARK_SYMBOL,
    "safe_haven": SAFE_HAVEN,
    "candidate_universe_size": DEFAULT_CANDIDATE_UNIVERSE_SIZE,
    "holdings_count": DEFAULT_HOLDINGS_COUNT,
    "single_name_cap": DEFAULT_SINGLE_NAME_CAP,
    "min_position_value_usd": DEFAULT_MIN_POSITION_VALUE_USD,
    "hold_buffer": DEFAULT_HOLD_BUFFER,
    "hold_bonus": DEFAULT_HOLD_BONUS,
    "max_product_exposure": DEFAULT_MAX_PRODUCT_EXPOSURE,
    "leverage_multiple": DEFAULT_LEVERAGE_MULTIPLE,
    "min_adv20_usd": DEFAULT_MIN_ADV20_USD,
    "atr_period": DEFAULT_ATR_PERIOD,
    "sma_period": DEFAULT_SMA_PERIOD,
    "atr_entry_scale": DEFAULT_ATR_ENTRY_SCALE,
    "entry_line_floor": DEFAULT_ENTRY_LINE_FLOOR,
    "entry_line_cap": DEFAULT_ENTRY_LINE_CAP,
    "atr_exit_scale": DEFAULT_ATR_EXIT_SCALE,
    "exit_line_floor": DEFAULT_EXIT_LINE_FLOOR,
    "exit_line_cap": DEFAULT_EXIT_LINE_CAP,
    "history_duration": DEFAULT_HISTORY_DURATION,
    "history_bar_size": DEFAULT_HISTORY_BAR_SIZE,
    "runtime_execution_window_trading_days": DEFAULT_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS,
    "execution_cash_reserve_ratio": DEFAULT_EXECUTION_CASH_RESERVE_RATIO,
}
SNAPSHOT_DATE_COLUMNS = ("as_of", "snapshot_date")
MAX_SNAPSHOT_MONTH_LAG = 1
REQUIRE_SNAPSHOT_MANIFEST = True
SNAPSHOT_CONTRACT_VERSION = "dynamic_mega_leveraged_pullback.feature_snapshot.v1"

REQUIRED_FEATURE_COLUMNS = frozenset(
    {
        "symbol",
        "underlying_symbol",
        "sector",
        "candidate_rank",
        "product_leverage",
        "product_available",
    }
)

FEATURE_SIGNAL_KWARG_KEYS = (*DEFAULT_CONFIG.keys(), "portfolio_total_equity")


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


def _normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _normalize_holdings(current_holdings) -> set[str]:
    if current_holdings is None:
        return set()
    raw_symbols = current_holdings.keys() if isinstance(current_holdings, Mapping) else current_holdings
    normalized: set[str] = set()
    for item in raw_symbols:
        symbol = getattr(item, "symbol", item)
        symbol_text = _normalize_symbol(symbol)
        if symbol_text:
            normalized.add(symbol_text)
    return normalized


def _to_frame(feature_snapshot) -> pd.DataFrame:
    frame = feature_snapshot.copy() if isinstance(feature_snapshot, pd.DataFrame) else pd.DataFrame(list(feature_snapshot))
    if frame.empty:
        raise ValueError("feature_snapshot must contain at least one row")

    if "underlying_symbol" not in frame.columns and "symbol" in frame.columns:
        frame["underlying_symbol"] = frame["symbol"]
    if "trade_symbol" in frame.columns and "symbol" not in frame.columns:
        frame["symbol"] = frame["trade_symbol"]
    if "trade_symbol" not in frame.columns and "symbol" in frame.columns:
        frame["trade_symbol"] = frame["symbol"]
    if "candidate_rank" not in frame.columns and "mega_rank" in frame.columns:
        frame["candidate_rank"] = frame["mega_rank"]
    if "product_leverage" not in frame.columns:
        frame["product_leverage"] = DEFAULT_LEVERAGE_MULTIPLE
    if "product_available" not in frame.columns:
        frame["product_available"] = True

    missing = REQUIRED_FEATURE_COLUMNS - set(frame.columns)
    if missing:
        missing_text = ", ".join(sorted(missing))
        raise ValueError(f"feature_snapshot missing required columns: {missing_text}")

    frame["symbol"] = frame["symbol"].map(_normalize_symbol)
    frame["trade_symbol"] = frame["trade_symbol"].map(_normalize_symbol)
    frame["underlying_symbol"] = frame["underlying_symbol"].map(_normalize_symbol)
    frame["sector"] = frame["sector"].fillna("unknown").astype(str).str.strip().replace("", "unknown")
    if "as_of" in frame.columns:
        frame["as_of"] = pd.to_datetime(frame["as_of"], utc=False).dt.tz_localize(None).dt.normalize()
    if "eligible" not in frame.columns:
        frame["eligible"] = True
    frame["eligible"] = frame["eligible"].map(_coerce_bool)
    frame["product_available"] = frame["product_available"].map(_coerce_bool)
    for column in (
        "candidate_rank",
        "mega_rank",
        "product_leverage",
        "product_expense_ratio",
        "min_adv20_usd",
        "source_weight",
    ):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.loc[frame["symbol"].ne("") & frame["underlying_symbol"].ne("")].copy()


def _zscore(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    std = float(numeric.std(ddof=0))
    if pd.isna(std) or std == 0.0:
        return pd.Series(0.0, index=values.index, dtype=float)
    return ((numeric - numeric.mean()) / std).fillna(0.0)


def _to_close_series(history) -> pd.Series:
    if isinstance(history, pd.Series):
        series = pd.to_numeric(history, errors="coerce")
        if not isinstance(series.index, pd.DatetimeIndex):
            series.index = pd.to_datetime(series.index, errors="coerce")
        return series.dropna()
    frame = pd.DataFrame(history)
    if frame.empty:
        return pd.Series(dtype=float)
    if "close" in frame.columns:
        values = pd.to_numeric(frame["close"], errors="coerce")
    else:
        values = pd.to_numeric(frame.iloc[:, 0], errors="coerce")
    if "as_of" in frame.columns:
        index = pd.to_datetime(frame["as_of"], errors="coerce")
    elif "date" in frame.columns:
        index = pd.to_datetime(frame["date"], errors="coerce")
    else:
        index = pd.to_datetime(frame.index, errors="coerce")
    series = pd.Series(values.to_numpy(), index=index)
    return series.dropna().sort_index()


def _to_ohlc_frame(history) -> pd.DataFrame:
    frame = pd.DataFrame(history).copy()
    if frame.empty:
        return pd.DataFrame(columns=["close", "high", "low"])
    rename_map = {column: str(column).strip().lower() for column in frame.columns}
    frame = frame.rename(columns=rename_map)
    if "date" in frame.columns and "as_of" not in frame.columns:
        frame["as_of"] = frame["date"]
    if "as_of" in frame.columns:
        frame.index = pd.to_datetime(frame["as_of"], errors="coerce")
    else:
        frame.index = pd.to_datetime(frame.index, errors="coerce")
    for column in ("close", "high", "low"):
        if column not in frame.columns:
            if column == "high" and "close" in frame.columns:
                frame[column] = frame["close"]
            elif column == "low" and "close" in frame.columns:
                frame[column] = frame["close"]
            else:
                frame[column] = float("nan")
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.loc[:, ["close", "high", "low"]].dropna(subset=["close"]).sort_index()


def _call_market_history(loader, ib, symbol: str, *, duration: str, bar_size: str):
    try:
        return loader(ib, symbol, duration=duration, bar_size=bar_size)
    except TypeError:
        return loader(symbol, duration=duration, bar_size=bar_size)


def _benchmark_metrics(
    benchmark_history,
    *,
    sma_period: int,
    atr_period: int,
    atr_entry_scale: float,
    entry_line_floor: float,
    entry_line_cap: float,
    atr_exit_scale: float,
    exit_line_floor: float,
    exit_line_cap: float,
) -> dict[str, float]:
    frame = _to_ohlc_frame(benchmark_history)
    required = max(int(sma_period), int(atr_period) + 1, 126)
    if len(frame) < required:
        raise ValueError(f"benchmark_history requires at least {required} daily rows")

    close = frame["close"]
    high = frame["high"]
    low = frame["low"]
    current = float(close.iloc[-1])
    sma = float(close.rolling(int(sma_period)).mean().iloc[-1])
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = float(true_range.rolling(int(atr_period)).mean().iloc[-1])
    atr_pct = atr / current if current > 0 else float("nan")
    entry_multiplier = max(float(entry_line_floor), min(float(entry_line_cap), 1.0 + atr_pct * float(atr_entry_scale)))
    exit_multiplier = max(float(exit_line_floor), min(float(exit_line_cap), 1.0 - atr_pct * float(atr_exit_scale)))
    mom_126 = float(close.iloc[-1] / close.iloc[-127] - 1.0)
    return {
        "benchmark_close": current,
        "benchmark_sma": sma,
        "benchmark_sma_gap": current / sma - 1.0 if sma else float("nan"),
        "benchmark_atr": atr,
        "benchmark_atr_pct": atr_pct,
        "benchmark_entry_line": sma * entry_multiplier,
        "benchmark_exit_line": sma * exit_multiplier,
        "benchmark_entry_multiplier": entry_multiplier,
        "benchmark_exit_multiplier": exit_multiplier,
        "benchmark_mom_126": mom_126,
    }


def _resolve_market_exposure(
    benchmark_history,
    current_holdings,
    risk_symbols: Iterable[str],
    *,
    max_product_exposure: float,
    sma_period: int,
    atr_period: int,
    atr_entry_scale: float,
    entry_line_floor: float,
    entry_line_cap: float,
    atr_exit_scale: float,
    exit_line_floor: float,
    exit_line_cap: float,
) -> tuple[float, str, dict[str, object]]:
    metrics = _benchmark_metrics(
        benchmark_history,
        sma_period=int(sma_period),
        atr_period=int(atr_period),
        atr_entry_scale=float(atr_entry_scale),
        entry_line_floor=float(entry_line_floor),
        entry_line_cap=float(entry_line_cap),
        atr_exit_scale=float(atr_exit_scale),
        exit_line_floor=float(exit_line_floor),
        exit_line_cap=float(exit_line_cap),
    )
    current = float(metrics["benchmark_close"])
    entry_line = float(metrics["benchmark_entry_line"])
    exit_line = float(metrics["benchmark_exit_line"])
    current_risk_active = bool(_normalize_holdings(current_holdings) & {_normalize_symbol(symbol) for symbol in risk_symbols})

    if current < exit_line:
        return 0.0, "hard_defense", {**metrics, "current_risk_active": current_risk_active}
    if current_risk_active:
        return float(max_product_exposure), "risk_on_hold_band", {**metrics, "current_risk_active": current_risk_active}
    if current > entry_line:
        return float(max_product_exposure), "risk_on_entry", {**metrics, "current_risk_active": current_risk_active}
    return 0.0, "entry_wait", {**metrics, "current_risk_active": current_risk_active}


def _pullback_multiplier(row: Mapping[str, object]) -> float:
    sma_200_gap = float(row.get("sma_200_gap", float("nan")))
    mom_126 = float(row.get("mom_126", float("nan")))
    pullback = abs(min(float(row.get("high_63_gap", 0.0)), 0.0))
    if pd.isna(sma_200_gap) or pd.isna(mom_126) or sma_200_gap <= 0.0 or mom_126 <= 0.0:
        return 0.0
    if pullback < 0.02:
        multiplier = 0.45
    elif pullback < 0.06:
        multiplier = 0.85
    elif pullback < 0.16:
        multiplier = 1.25
    elif pullback < 0.28:
        multiplier = 0.75
    else:
        return 0.0

    if float(row.get("mom_20", 0.0)) > 0.18 or float(row.get("sma_50_gap", 0.0)) > 0.16:
        multiplier *= 0.50
    if sma_200_gap < 0.03:
        multiplier *= 0.60
    if float(row.get("rel_mom_126_vs_benchmark", 0.0)) < -0.08:
        multiplier *= 0.50
    return float(max(0.0, min(multiplier, 1.30)))


def _candidate_snapshot(frame: pd.DataFrame, *, candidate_universe_size: int) -> pd.DataFrame:
    candidates = frame.loc[frame["eligible"] & frame["product_available"]].copy()
    candidates = candidates.loc[candidates["symbol"].ne("") & candidates["underlying_symbol"].ne("")]
    if candidates.empty:
        return candidates
    candidates = candidates.sort_values(["candidate_rank", "underlying_symbol"], ascending=[True, True])
    candidates = candidates.drop_duplicates(subset=["underlying_symbol"], keep="first")
    return candidates.head(int(candidate_universe_size)).reset_index(drop=True)


def _feature_row_for_candidate(
    candidate: Mapping[str, object],
    close: pd.Series,
    *,
    benchmark_mom_126: float,
) -> dict[str, object] | None:
    close = pd.to_numeric(close, errors="coerce").dropna().sort_index()
    if len(close) < 253:
        return None
    current = float(close.iloc[-1])
    returns = close.pct_change()
    sma_50 = float(close.iloc[-50:].mean())
    sma_200 = float(close.iloc[-200:].mean())
    high_63 = float(close.iloc[-63:].max())
    high_252 = float(close.iloc[-252:].max())
    low_20 = float(close.iloc[-20:].min())
    mom_20 = float(close.iloc[-1] / close.iloc[-21] - 1.0)
    mom_63 = float(close.iloc[-1] / close.iloc[-64] - 1.0)
    mom_126 = float(close.iloc[-1] / close.iloc[-127] - 1.0)
    mom_252 = float(close.iloc[-1] / close.iloc[-253] - 1.0)
    raw_rank = pd.to_numeric(pd.Series([candidate.get("candidate_rank")]), errors="coerce").iloc[0]
    candidate_rank = int(raw_rank) if pd.notna(raw_rank) else 999
    return {
        "symbol": _normalize_symbol(candidate["symbol"]),
        "trade_symbol": _normalize_symbol(candidate.get("trade_symbol") or candidate["symbol"]),
        "underlying_symbol": _normalize_symbol(candidate["underlying_symbol"]),
        "sector": str(candidate.get("sector") or "unknown"),
        "candidate_rank": candidate_rank,
        "close": current,
        "mom_20": mom_20,
        "mom_63": mom_63,
        "mom_126": mom_126,
        "mom_252": mom_252,
        "rel_mom_126_vs_benchmark": mom_126 - float(benchmark_mom_126),
        "sma_50_gap": current / sma_50 - 1.0 if sma_50 else float("nan"),
        "sma_200_gap": current / sma_200 - 1.0 if sma_200 else float("nan"),
        "high_63_gap": current / high_63 - 1.0 if high_63 else float("nan"),
        "high_252_gap": current / high_252 - 1.0 if high_252 else float("nan"),
        "low_20_gap": current / low_20 - 1.0 if low_20 else float("nan"),
        "vol_63": float(returns.iloc[-63:].std(ddof=0) * math.sqrt(252)),
    }


def build_daily_feature_frame(
    feature_snapshot,
    market_history,
    *,
    benchmark_mom_126: float,
    ib=None,
    candidate_universe_size: int = DEFAULT_CANDIDATE_UNIVERSE_SIZE,
    history_duration: str = DEFAULT_HISTORY_DURATION,
    history_bar_size: str = DEFAULT_HISTORY_BAR_SIZE,
) -> pd.DataFrame:
    snapshot = _candidate_snapshot(_to_frame(feature_snapshot), candidate_universe_size=int(candidate_universe_size))
    rows: list[dict[str, object]] = []
    for candidate in snapshot.to_dict("records"):
        close = _to_close_series(
            _call_market_history(
                market_history,
                ib,
                str(candidate["underlying_symbol"]),
                duration=str(history_duration),
                bar_size=str(history_bar_size),
            )
        )
        row = _feature_row_for_candidate(candidate, close, benchmark_mom_126=float(benchmark_mom_126))
        if row is not None:
            rows.append(row)
    return pd.DataFrame(rows)


def rank_candidates(
    feature_frame: pd.DataFrame,
    current_holdings: Iterable[str] | None = None,
    *,
    hold_bonus: float = DEFAULT_HOLD_BONUS,
) -> pd.DataFrame:
    if feature_frame.empty:
        return pd.DataFrame()
    frame = feature_frame.copy()
    required = ["mom_63", "mom_126", "mom_252", "rel_mom_126_vs_benchmark", "sma_200_gap", "vol_63", "high_63_gap"]
    frame = frame.loc[frame[required].notna().all(axis=1)].copy()
    if frame.empty:
        return pd.DataFrame()

    frame["pullback_depth"] = frame["high_63_gap"].clip(upper=0.0).abs()
    frame["pullback_quality"] = (1.0 - (frame["pullback_depth"] - 0.10).abs() / 0.10).clip(lower=-1.0, upper=1.0)
    frame["size_multiplier"] = frame.apply(_pullback_multiplier, axis=1)
    frame["eligible"] = frame["size_multiplier"] > 0.0
    frame = frame.loc[frame["eligible"]].copy()
    if frame.empty:
        return pd.DataFrame()

    current = _normalize_holdings(current_holdings)
    frame["score"] = (
        _zscore(frame["mom_126"]) * 0.35
        + _zscore(frame["mom_252"]) * 0.20
        + _zscore(frame["rel_mom_126_vs_benchmark"]) * 0.20
        + _zscore(frame["pullback_quality"]) * 0.20
        - _zscore(frame["vol_63"]) * 0.05
    )
    if current:
        frame.loc[frame["symbol"].isin(current), "score"] += float(hold_bonus)
    ranked = frame.sort_values(
        ["score", "pullback_quality", "mom_126", "underlying_symbol"],
        ascending=[False, False, False, True],
    ).reset_index(drop=True)
    ranked.insert(0, "rank", range(1, len(ranked) + 1))
    return ranked


def _resolve_effective_holdings_count(
    *,
    holdings_count: int,
    product_exposure: float,
    portfolio_total_equity: float | None,
    min_position_value_usd: float,
) -> int:
    requested = int(holdings_count)
    if requested <= 0:
        raise ValueError("holdings_count must be positive")
    if product_exposure <= 0:
        return 0
    if portfolio_total_equity is None or min_position_value_usd <= 0:
        return requested
    target_value = float(portfolio_total_equity) * float(product_exposure)
    if target_value <= 0:
        return 0
    count_by_value = max(1, int(math.floor(target_value / float(min_position_value_usd))))
    return max(1, min(requested, count_by_value))


def build_target_weights(
    feature_snapshot,
    market_history,
    benchmark_history,
    current_holdings,
    *,
    ib=None,
    portfolio_total_equity: float | None = None,
    benchmark_symbol: str = BENCHMARK_SYMBOL,
    safe_haven: str = SAFE_HAVEN,
    candidate_universe_size: int = DEFAULT_CANDIDATE_UNIVERSE_SIZE,
    holdings_count: int = DEFAULT_HOLDINGS_COUNT,
    single_name_cap: float = DEFAULT_SINGLE_NAME_CAP,
    min_position_value_usd: float = DEFAULT_MIN_POSITION_VALUE_USD,
    hold_buffer: int = DEFAULT_HOLD_BUFFER,
    hold_bonus: float = DEFAULT_HOLD_BONUS,
    max_product_exposure: float = DEFAULT_MAX_PRODUCT_EXPOSURE,
    leverage_multiple: float = DEFAULT_LEVERAGE_MULTIPLE,
    min_adv20_usd: float = DEFAULT_MIN_ADV20_USD,
    atr_period: int = DEFAULT_ATR_PERIOD,
    sma_period: int = DEFAULT_SMA_PERIOD,
    atr_entry_scale: float = DEFAULT_ATR_ENTRY_SCALE,
    entry_line_floor: float = DEFAULT_ENTRY_LINE_FLOOR,
    entry_line_cap: float = DEFAULT_ENTRY_LINE_CAP,
    atr_exit_scale: float = DEFAULT_ATR_EXIT_SCALE,
    exit_line_floor: float = DEFAULT_EXIT_LINE_FLOOR,
    exit_line_cap: float = DEFAULT_EXIT_LINE_CAP,
    history_duration: str = DEFAULT_HISTORY_DURATION,
    history_bar_size: str = DEFAULT_HISTORY_BAR_SIZE,
):
    del benchmark_symbol, min_adv20_usd
    safe_haven = _normalize_symbol(safe_haven)
    snapshot = _candidate_snapshot(_to_frame(feature_snapshot), candidate_universe_size=int(candidate_universe_size))
    risk_symbols = tuple(snapshot["symbol"].astype(str).tolist())
    product_exposure, regime, market_metadata = _resolve_market_exposure(
        benchmark_history,
        current_holdings,
        risk_symbols,
        max_product_exposure=float(max_product_exposure),
        sma_period=int(sma_period),
        atr_period=int(atr_period),
        atr_entry_scale=float(atr_entry_scale),
        entry_line_floor=float(entry_line_floor),
        entry_line_cap=float(entry_line_cap),
        atr_exit_scale=float(atr_exit_scale),
        exit_line_floor=float(exit_line_floor),
        exit_line_cap=float(exit_line_cap),
    )
    feature_frame = build_daily_feature_frame(
        snapshot,
        market_history,
        benchmark_mom_126=float(market_metadata["benchmark_mom_126"]),
        ib=ib,
        candidate_universe_size=int(candidate_universe_size),
        history_duration=str(history_duration),
        history_bar_size=str(history_bar_size),
    )
    ranked = rank_candidates(feature_frame, current_holdings, hold_bonus=float(hold_bonus))
    effective_holdings_count = _resolve_effective_holdings_count(
        holdings_count=int(holdings_count),
        product_exposure=float(product_exposure),
        portfolio_total_equity=portfolio_total_equity,
        min_position_value_usd=float(min_position_value_usd),
    )
    metadata: dict[str, object] = {
        **market_metadata,
        "regime": regime,
        "target_product_exposure": float(product_exposure),
        "realized_product_exposure": 0.0,
        "underlying_exposure": 0.0,
        "safe_haven_weight": 1.0,
        "selected_symbols": (),
        "selected_underlyings": (),
        "selected_count": 0,
        "candidate_count": int(len(ranked)),
        "candidate_universe_size": int(candidate_universe_size),
        "requested_holdings_count": int(holdings_count),
        "effective_holdings_count": int(effective_holdings_count),
        "portfolio_total_equity": portfolio_total_equity,
        "min_position_value_usd": float(min_position_value_usd),
    }
    if ranked.empty or product_exposure <= 0.0 or effective_holdings_count <= 0:
        return {safe_haven: 1.0}, ranked, metadata

    current = _normalize_holdings(current_holdings)
    rank_map = dict(zip(ranked["symbol"].astype(str), ranked["rank"].astype(int)))
    max_hold_rank = int(effective_holdings_count) + max(0, int(hold_buffer))
    selected = [
        symbol
        for symbol in ranked["symbol"].astype(str)
        if symbol in current and int(rank_map[symbol]) <= max_hold_rank
    ]
    for symbol in ranked["symbol"].astype(str):
        if len(selected) >= int(effective_holdings_count):
            break
        if symbol not in selected:
            selected.append(symbol)
    selected = selected[: int(effective_holdings_count)]
    selected_frame = ranked.loc[ranked["symbol"].isin(selected)].copy()
    if selected_frame.empty:
        return {safe_haven: 1.0}, ranked, metadata

    base_weight = float(product_exposure) / len(selected_frame)
    raw_weights = {
        str(row.symbol): min(float(single_name_cap), base_weight * float(row.size_multiplier))
        for row in selected_frame.itertuples(index=False)
    }
    realized_product_exposure = float(sum(raw_weights.values()))
    if realized_product_exposure > float(product_exposure) and realized_product_exposure > 0.0:
        scale = float(product_exposure) / realized_product_exposure
        raw_weights = {symbol: weight * scale for symbol, weight in raw_weights.items()}
        realized_product_exposure = float(sum(raw_weights.values()))
    safe_weight = max(0.0, 1.0 - realized_product_exposure)
    weights = {symbol: weight for symbol, weight in raw_weights.items() if weight > 1e-12}
    if safe_weight > 1e-12:
        weights[safe_haven] = safe_weight

    metadata.update(
        {
            "realized_product_exposure": realized_product_exposure,
            "underlying_exposure": realized_product_exposure * float(leverage_multiple),
            "safe_haven_weight": safe_weight,
            "selected_symbols": tuple(symbol for symbol in weights if symbol != safe_haven),
            "selected_underlyings": tuple(selected_frame["underlying_symbol"].astype(str).tolist()),
            "selected_count": int(sum(1 for symbol in weights if symbol != safe_haven)),
            "avg_pullback_depth": float(selected_frame["pullback_depth"].mean()),
        }
    )
    return weights, ranked, metadata


def extract_managed_symbols(
    feature_snapshot,
    *,
    benchmark_symbol: str = BENCHMARK_SYMBOL,
    safe_haven: str = SAFE_HAVEN,
) -> tuple[str, ...]:
    del benchmark_symbol
    frame = _to_frame(feature_snapshot)
    symbols = [symbol for symbol in frame["symbol"].astype(str).tolist() if symbol]
    safe_haven = _normalize_symbol(safe_haven)
    if safe_haven and safe_haven not in symbols:
        symbols.append(safe_haven)
    return tuple(dict.fromkeys(symbols))


def compute_signals(
    feature_snapshot,
    current_holdings,
    *,
    market_history,
    benchmark_history,
    portfolio=None,
    ib=None,
    portfolio_total_equity: float | None = None,
    **kwargs,
):
    kwargs.pop("translator", None)
    kwargs.pop("signal_text_fn", None)
    kwargs.pop("runtime_execution_window_trading_days", None)
    kwargs.pop("execution_cash_reserve_ratio", None)
    if portfolio_total_equity is None and portfolio is not None:
        total_equity = getattr(portfolio, "total_equity", None)
        if total_equity is not None:
            portfolio_total_equity = float(total_equity)
    weights, ranked, metadata = build_target_weights(
        feature_snapshot,
        market_history,
        benchmark_history,
        current_holdings,
        ib=ib,
        portfolio_total_equity=portfolio_total_equity,
        **kwargs,
    )
    top_preview = ", ".join(
        f"{row.underlying_symbol}->{row.symbol}({row.score:.2f})"
        for row in ranked.head(5).itertuples(index=False)
    )
    signal_desc = (
        f"regime={metadata['regime']} qqq={float(metadata['benchmark_close']):.2f} "
        f"entry={float(metadata['benchmark_entry_line']):.2f} exit={float(metadata['benchmark_exit_line']):.2f} "
        f"target_product={float(metadata['target_product_exposure']):.1%} "
        f"realized_product={float(metadata['realized_product_exposure']):.1%} "
        f"selected={metadata['selected_count']} top={top_preview}"
    )
    status_desc = (
        f"regime={metadata['regime']} | "
        f"QQQ close={float(metadata['benchmark_close']):.2f} | "
        f"entry={float(metadata['benchmark_entry_line']):.2f} | "
        f"exit={float(metadata['benchmark_exit_line']):.2f} | "
        f"product={float(metadata['realized_product_exposure']):.1%}"
    )
    managed_symbols = extract_managed_symbols(
        feature_snapshot,
        safe_haven=str(kwargs.get("safe_haven", SAFE_HAVEN)),
    )
    return (
        weights,
        signal_desc,
        metadata["regime"] == "hard_defense",
        status_desc,
        {
            **metadata,
            "managed_symbols": managed_symbols,
            "status_icon": STATUS_ICON,
            "signal_source": SIGNAL_SOURCE,
        },
    )
