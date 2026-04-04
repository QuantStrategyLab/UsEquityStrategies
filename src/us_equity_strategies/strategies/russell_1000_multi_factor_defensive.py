from __future__ import annotations

import math
from collections.abc import Mapping

import pandas as pd

SIGNAL_SOURCE = "feature_snapshot"
STATUS_ICON = "📏"
BENCHMARK_SYMBOL = "SPY"
SAFE_HAVEN = "BOXX"
DEFAULT_HOLDINGS_COUNT = 24
DEFAULT_SINGLE_NAME_CAP = 0.06
DEFAULT_SECTOR_CAP = 0.20
DEFAULT_HOLD_BONUS = 0.15
DEFAULT_SOFT_DEFENSE_EXPOSURE = 0.50
DEFAULT_HARD_DEFENSE_EXPOSURE = 0.10
DEFAULT_SOFT_BREADTH_THRESHOLD = 0.55
DEFAULT_HARD_BREADTH_THRESHOLD = 0.35

REQUIRED_FEATURE_COLUMNS = frozenset(
    {
        "symbol",
        "sector",
        "mom_6_1",
        "mom_12_1",
        "sma200_gap",
        "vol_63",
        "maxdd_126",
    }
)


def _coerce_bool(value) -> bool:
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


def _normalize_holdings(current_holdings) -> set[str]:
    if current_holdings is None:
        return set()
    if isinstance(current_holdings, Mapping):
        raw_symbols = current_holdings.keys()
    else:
        raw_symbols = current_holdings

    normalized: set[str] = set()
    for item in raw_symbols:
        symbol = getattr(item, "symbol", item)
        symbol_text = str(symbol or "").strip().upper()
        if symbol_text:
            normalized.add(symbol_text)
    return normalized


def _zscore(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    std = numeric.std(ddof=0)
    if pd.isna(std) or std == 0:
        return pd.Series(0.0, index=values.index, dtype=float)
    return ((numeric - numeric.mean()) / std).fillna(0.0)


def _to_frame(feature_snapshot) -> pd.DataFrame:
    if isinstance(feature_snapshot, pd.DataFrame):
        frame = feature_snapshot.copy()
    else:
        frame = pd.DataFrame(list(feature_snapshot))

    if frame.empty:
        raise ValueError("feature_snapshot must contain at least one row")

    missing = REQUIRED_FEATURE_COLUMNS - set(frame.columns)
    if missing:
        missing_text = ", ".join(sorted(missing))
        raise ValueError(f"feature_snapshot missing required columns: {missing_text}")

    frame["symbol"] = frame["symbol"].astype(str).str.upper().str.strip()
    frame["sector"] = frame["sector"].fillna("unknown").astype(str).str.strip().replace("", "unknown")
    if "eligible" in frame.columns:
        frame["eligible"] = frame["eligible"].where(frame["eligible"].notna(), True)
    else:
        frame["eligible"] = True
    frame["eligible"] = frame["eligible"].map(_coerce_bool)

    for column in ("mom_6_1", "mom_12_1", "sma200_gap", "vol_63", "maxdd_126"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    return frame


def _resolve_regime(
    *,
    benchmark_trend_positive: bool,
    breadth_ratio: float,
    soft_breadth_threshold: float,
    hard_breadth_threshold: float,
) -> str:
    if (not benchmark_trend_positive) and breadth_ratio < hard_breadth_threshold:
        return "hard_defense"
    if (not benchmark_trend_positive) or breadth_ratio < soft_breadth_threshold:
        return "soft_defense"
    return "risk_on"


def _stock_exposure_for_regime(
    regime: str,
    *,
    soft_defense_exposure: float,
    hard_defense_exposure: float,
) -> float:
    if regime == "hard_defense":
        return float(hard_defense_exposure)
    if regime == "soft_defense":
        return float(soft_defense_exposure)
    return 1.0


def _select_symbols(
    ranked: pd.DataFrame,
    *,
    holdings_count: int,
    sector_slot_cap: int,
) -> pd.DataFrame:
    selected_rows = []
    sector_counts: dict[str, int] = {}
    for row in ranked.itertuples(index=False):
        sector = row.sector
        if sector_counts.get(sector, 0) >= sector_slot_cap:
            continue
        selected_rows.append(row._asdict())
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        if len(selected_rows) >= holdings_count:
            break

    return pd.DataFrame(selected_rows)


def build_target_weights(
    feature_snapshot,
    current_holdings,
    *,
    benchmark_symbol: str = BENCHMARK_SYMBOL,
    safe_haven: str = SAFE_HAVEN,
    holdings_count: int = DEFAULT_HOLDINGS_COUNT,
    single_name_cap: float = DEFAULT_SINGLE_NAME_CAP,
    sector_cap: float = DEFAULT_SECTOR_CAP,
    hold_bonus: float = DEFAULT_HOLD_BONUS,
    soft_defense_exposure: float = DEFAULT_SOFT_DEFENSE_EXPOSURE,
    hard_defense_exposure: float = DEFAULT_HARD_DEFENSE_EXPOSURE,
    soft_breadth_threshold: float = DEFAULT_SOFT_BREADTH_THRESHOLD,
    hard_breadth_threshold: float = DEFAULT_HARD_BREADTH_THRESHOLD,
):
    """
    Build a price-only Russell 1000 target-weight plan from a precomputed feature snapshot.

    Expected feature columns:
      - symbol
      - sector
      - mom_6_1
      - mom_12_1
      - sma200_gap
      - vol_63
      - maxdd_126
      - eligible (optional, defaults to True)

    The benchmark row (default `SPY`) can be included in the same snapshot.
    If present, its `sma200_gap` drives the market-regime filter.
    """
    if holdings_count <= 0:
        raise ValueError("holdings_count must be positive")
    if single_name_cap <= 0 or sector_cap <= 0:
        raise ValueError("single_name_cap and sector_cap must be positive")

    frame = _to_frame(feature_snapshot)
    benchmark_symbol = str(benchmark_symbol or "").strip().upper()
    safe_haven = str(safe_haven or "").strip().upper()
    current_holdings_set = _normalize_holdings(current_holdings)

    benchmark_rows = frame.loc[frame["symbol"] == benchmark_symbol]
    benchmark_trend_positive = True
    if not benchmark_rows.empty:
        benchmark_trend_positive = bool(benchmark_rows.iloc[-1]["sma200_gap"] > 0)

    universe = frame.loc[
        (frame["symbol"] != benchmark_symbol) & (frame["symbol"] != safe_haven)
    ].copy()
    eligible = universe.loc[
        universe["eligible"]
        & universe["mom_6_1"].notna()
        & universe["mom_12_1"].notna()
        & universe["sma200_gap"].notna()
        & universe["vol_63"].notna()
        & universe["maxdd_126"].notna()
    ].copy()

    breadth_ratio = float((eligible["sma200_gap"] > 0).mean()) if not eligible.empty else 0.0
    regime = _resolve_regime(
        benchmark_trend_positive=benchmark_trend_positive,
        breadth_ratio=breadth_ratio,
        soft_breadth_threshold=soft_breadth_threshold,
        hard_breadth_threshold=hard_breadth_threshold,
    )
    stock_exposure = _stock_exposure_for_regime(
        regime,
        soft_defense_exposure=soft_defense_exposure,
        hard_defense_exposure=hard_defense_exposure,
    )

    if eligible.empty or stock_exposure <= 0:
        signal = (
            f"regime={regime} breadth={breadth_ratio:.1%} "
            f"benchmark_trend={'up' if benchmark_trend_positive else 'down'}"
        )
        return {safe_haven: 1.0}, signal, {
            "benchmark_symbol": benchmark_symbol,
            "benchmark_trend_positive": benchmark_trend_positive,
            "breadth_ratio": breadth_ratio,
            "regime": regime,
            "stock_exposure": 0.0,
            "selected_symbols": (),
            "candidate_count": int(len(eligible)),
        }

    eligible["z_mom_6_1"] = eligible.groupby("sector")["mom_6_1"].transform(_zscore)
    eligible["z_mom_12_1"] = eligible.groupby("sector")["mom_12_1"].transform(_zscore)
    eligible["z_sma200_gap"] = eligible.groupby("sector")["sma200_gap"].transform(_zscore)
    eligible["z_vol_63"] = eligible.groupby("sector")["vol_63"].transform(_zscore)
    eligible["drawdown_abs"] = eligible["maxdd_126"].abs()
    eligible["z_drawdown_abs"] = eligible.groupby("sector")["drawdown_abs"].transform(_zscore)
    eligible["score"] = (
        (eligible["z_mom_6_1"] * 0.35)
        + (eligible["z_mom_12_1"] * 0.30)
        + (eligible["z_sma200_gap"] * 0.15)
        - (eligible["z_vol_63"] * 0.10)
        - (eligible["z_drawdown_abs"] * 0.10)
    )
    eligible.loc[
        eligible["symbol"].isin(current_holdings_set),
        "score",
    ] += float(hold_bonus)

    ranked = eligible.sort_values(
        by=["score", "mom_12_1", "mom_6_1", "symbol"],
        ascending=[False, False, False, True],
    )

    per_name_target = stock_exposure / holdings_count
    if per_name_target <= 0:
        sector_slot_cap = holdings_count
    else:
        sector_slot_cap = max(1, int(math.floor(sector_cap / per_name_target)))

    selected = _select_symbols(
        ranked,
        holdings_count=holdings_count,
        sector_slot_cap=sector_slot_cap,
    )

    if selected.empty:
        signal = (
            f"regime={regime} breadth={breadth_ratio:.1%} "
            f"benchmark_trend={'up' if benchmark_trend_positive else 'down'} no_selection"
        )
        return {safe_haven: 1.0}, signal, {
            "benchmark_symbol": benchmark_symbol,
            "benchmark_trend_positive": benchmark_trend_positive,
            "breadth_ratio": breadth_ratio,
            "regime": regime,
            "stock_exposure": 0.0,
            "selected_symbols": (),
            "candidate_count": int(len(eligible)),
        }

    per_name_weight = min(single_name_cap, stock_exposure / len(selected))
    invested_weight = per_name_weight * len(selected)
    weights = {row.symbol: per_name_weight for row in selected.itertuples(index=False)}
    if invested_weight < 1.0:
        weights[safe_haven] = 1.0 - invested_weight

    top_preview = ", ".join(
        f"{row.symbol}({row.score:.2f})"
        for row in selected.head(5).itertuples(index=False)
    )
    signal = (
        f"regime={regime} breadth={breadth_ratio:.1%} "
        f"benchmark_trend={'up' if benchmark_trend_positive else 'down'} "
        f"stock_exposure={stock_exposure:.1%} selected={len(selected)} top={top_preview}"
    )
    metadata = {
        "benchmark_symbol": benchmark_symbol,
        "benchmark_trend_positive": benchmark_trend_positive,
        "breadth_ratio": breadth_ratio,
        "regime": regime,
        "stock_exposure": stock_exposure,
        "selected_symbols": tuple(selected["symbol"].tolist()),
        "candidate_count": int(len(eligible)),
        "sector_slot_cap": sector_slot_cap,
    }
    return weights, signal, metadata


def extract_managed_symbols(
    feature_snapshot,
    *,
    benchmark_symbol: str = BENCHMARK_SYMBOL,
    safe_haven: str = SAFE_HAVEN,
) -> tuple[str, ...]:
    frame = _to_frame(feature_snapshot)
    benchmark_symbol = str(benchmark_symbol or "").strip().upper()
    safe_haven = str(safe_haven or "").strip().upper()

    symbols = []
    for symbol in frame["symbol"].tolist():
        if symbol == benchmark_symbol:
            continue
        symbols.append(symbol)
    if safe_haven and safe_haven not in symbols:
        symbols.append(safe_haven)
    return tuple(dict.fromkeys(symbols))


def compute_signals(feature_snapshot, current_holdings, **kwargs):
    weights, signal_desc, metadata = build_target_weights(
        feature_snapshot,
        current_holdings,
        **kwargs,
    )
    benchmark_symbol = kwargs.get("benchmark_symbol", BENCHMARK_SYMBOL)
    safe_haven = kwargs.get("safe_haven", SAFE_HAVEN)
    managed_symbols = extract_managed_symbols(
        feature_snapshot,
        benchmark_symbol=benchmark_symbol,
        safe_haven=safe_haven,
    )
    status_desc = (
        f"breadth={metadata['breadth_ratio']:.1%} | "
        f"regime={metadata['regime']} | "
        f"benchmark={'up' if metadata['benchmark_trend_positive'] else 'down'}"
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
        },
    )
