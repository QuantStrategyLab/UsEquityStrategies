from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from importlib import import_module
from typing import Any

import pandas as pd

SIGNAL_SOURCE = "feature_snapshot"
STATUS_ICON = "👑"
PROFILE_NAME = "russell_top50_leader_rotation"
BENCHMARK_SYMBOL = "QQQ"
BROAD_BENCHMARK_SYMBOL = "SPY"
SAFE_HAVEN = "BOXX"
DEFAULT_DYNAMIC_UNIVERSE_SIZE = 20
DEFAULT_HOLDINGS_COUNT = 4
DEFAULT_SINGLE_NAME_CAP = 0.25
DEFAULT_MIN_POSITION_VALUE_USD = 3_000.0
DEFAULT_HOLD_BUFFER = 2
DEFAULT_HOLD_BONUS = 0.10
DEFAULT_RISK_ON_EXPOSURE = 1.0
DEFAULT_SOFT_DEFENSE_EXPOSURE = 0.50
DEFAULT_HARD_DEFENSE_EXPOSURE = 0.50
DEFAULT_SOFT_BREADTH_THRESHOLD = 0.0
DEFAULT_HARD_BREADTH_THRESHOLD = 0.0
DEFAULT_MIN_ADV20_USD = 20_000_000.0
DEFAULT_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS = 3
DEFAULT_EXECUTION_CASH_RESERVE_RATIO = 0.0
SNAPSHOT_DATE_COLUMNS = ("as_of", "snapshot_date")
MAX_SNAPSHOT_MONTH_LAG = 1
REQUIRE_SNAPSHOT_MANIFEST = True
SNAPSHOT_CONTRACT_VERSION = "russell_top50_leader_rotation.feature_snapshot.v1"

REQUIRED_FEATURE_COLUMNS = frozenset(
    {
        "symbol",
        "sector",
        "close",
        "adv20_usd",
        "history_days",
        "mom_3m",
        "mom_6m",
        "mom_12_1",
        "rel_mom_6m_vs_benchmark",
        "rel_mom_6m_vs_broad_benchmark",
        "high_252_gap",
        "sma200_gap",
        "vol_63",
        "maxdd_126",
    }
)

FEATURE_SIGNAL_KWARG_KEYS = (
    "benchmark_symbol",
    "broad_benchmark_symbol",
    "safe_haven",
    "dynamic_universe_size",
    "leader_rotation_profile_variant",
    "leader_rotation_shadow_variants",
    "blend_sleeves",
    "holdings_count",
    "single_name_cap",
    "min_position_value_usd",
    "portfolio_total_equity",
    "hold_buffer",
    "hold_bonus",
    "risk_on_exposure",
    "soft_defense_exposure",
    "hard_defense_exposure",
    "soft_breadth_threshold",
    "hard_breadth_threshold",
    "min_adv20_usd",
    "run_as_of",
    "runtime_execution_window_trading_days",
    "execution_cash_reserve_ratio",
)

PROFILE_VARIANT_TOP4_BASELINE = "top4_baseline"
PROFILE_VARIANT_CONSERVATIVE_BLEND = "blend_top2_25_top4_75"
PROFILE_VARIANT_BALANCED_BLEND = "blend_top2_50_top4_50"
SHADOW_REVIEW_SCHEMA_VERSION = "russell_top50_shadow_review.v1"
SHADOW_REVIEW_ROW_FIELDS = (
    "schema_version",
    "active_variant",
    "shadow_variant",
    "selected_count",
    "realized_stock_weight",
    "safe_haven_weight",
    "turnover_delta_vs_active",
    "largest_increase_symbol",
    "largest_increase_delta",
    "largest_decrease_symbol",
    "largest_decrease_delta",
    "review_note",
)
DEFAULT_SHADOW_PROFILE_VARIANTS = (
    PROFILE_VARIANT_TOP4_BASELINE,
    PROFILE_VARIANT_CONSERVATIVE_BLEND,
    PROFILE_VARIANT_BALANCED_BLEND,
)

DEFAULT_TOP50_CONSERVATIVE_BLEND_SLEEVES = (
    {"name": "top2_cap50", "weight": 0.25, "holdings_count": 2, "single_name_cap": 0.50},
    {"name": "top4_cap25", "weight": 0.75, "holdings_count": 4, "single_name_cap": 0.25},
)
DEFAULT_TOP50_BALANCED_BLEND_SLEEVES = (
    {"name": "top2_cap50", "weight": 0.50, "holdings_count": 2, "single_name_cap": 0.50},
    {"name": "top4_cap25", "weight": 0.50, "holdings_count": 4, "single_name_cap": 0.25},
)
PROFILE_VARIANT_BLEND_SLEEVES = {
    PROFILE_VARIANT_CONSERVATIVE_BLEND: DEFAULT_TOP50_CONSERVATIVE_BLEND_SLEEVES,
    PROFILE_VARIANT_BALANCED_BLEND: DEFAULT_TOP50_BALANCED_BLEND_SLEEVES,
}
PROFILE_VARIANT_ALIASES = {
    PROFILE_VARIANT_TOP4_BASELINE: PROFILE_VARIANT_TOP4_BASELINE,
    "top4": PROFILE_VARIANT_TOP4_BASELINE,
    "baseline": PROFILE_VARIANT_TOP4_BASELINE,
    "fallback": PROFILE_VARIANT_TOP4_BASELINE,
    PROFILE_VARIANT_CONSERVATIVE_BLEND: PROFILE_VARIANT_CONSERVATIVE_BLEND,
    "conservative": PROFILE_VARIANT_CONSERVATIVE_BLEND,
    "conservative_live_design": PROFILE_VARIANT_CONSERVATIVE_BLEND,
    PROFILE_VARIANT_BALANCED_BLEND: PROFILE_VARIANT_BALANCED_BLEND,
    "balanced": PROFILE_VARIANT_BALANCED_BLEND,
    "balanced_offensive": PROFILE_VARIANT_BALANCED_BLEND,
    "balanced_offensive_live_design": PROFILE_VARIANT_BALANCED_BLEND,
}


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

    missing = REQUIRED_FEATURE_COLUMNS - set(frame.columns)
    if missing:
        missing_text = ", ".join(sorted(missing))
        raise ValueError(f"feature_snapshot missing required columns: {missing_text}")

    frame["symbol"] = frame["symbol"].map(_normalize_symbol)
    frame["sector"] = frame["sector"].fillna("unknown").astype(str).str.strip().replace("", "unknown")
    if "as_of" in frame.columns:
        frame["as_of"] = pd.to_datetime(frame["as_of"], utc=False).dt.tz_localize(None).dt.normalize()
    if "eligible" not in frame.columns:
        frame["eligible"] = True
    frame["eligible"] = frame["eligible"].map(_coerce_bool)

    numeric_columns = REQUIRED_FEATURE_COLUMNS - {"symbol", "sector"}
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _zscore(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    std = float(numeric.std(ddof=0))
    if pd.isna(std) or std == 0:
        return pd.Series(0.0, index=values.index, dtype=float)
    return ((numeric - numeric.mean()) / std).fillna(0.0)


def _candidate_frame(
    frame: pd.DataFrame,
    *,
    benchmark_symbol: str,
    broad_benchmark_symbol: str,
    safe_haven: str,
    min_adv20_usd: float,
) -> pd.DataFrame:
    excluded = {_normalize_symbol(benchmark_symbol), _normalize_symbol(broad_benchmark_symbol), _normalize_symbol(safe_haven)}
    feature_columns = [
        "mom_3m",
        "mom_6m",
        "mom_12_1",
        "rel_mom_6m_vs_benchmark",
        "rel_mom_6m_vs_broad_benchmark",
        "high_252_gap",
        "sma200_gap",
        "vol_63",
        "maxdd_126",
    ]
    return frame.loc[
        ~frame["symbol"].isin(excluded)
        & frame["eligible"]
        & frame["adv20_usd"].ge(float(min_adv20_usd))
        & frame[feature_columns].notna().all(axis=1)
    ].copy()


def score_candidates(
    feature_snapshot,
    current_holdings: Iterable[str] | None = None,
    *,
    benchmark_symbol: str = BENCHMARK_SYMBOL,
    broad_benchmark_symbol: str = BROAD_BENCHMARK_SYMBOL,
    safe_haven: str = SAFE_HAVEN,
    min_adv20_usd: float = DEFAULT_MIN_ADV20_USD,
    hold_bonus: float = DEFAULT_HOLD_BONUS,
) -> pd.DataFrame:
    frame = _to_frame(feature_snapshot)
    eligible = _candidate_frame(
        frame,
        benchmark_symbol=benchmark_symbol,
        broad_benchmark_symbol=broad_benchmark_symbol,
        safe_haven=safe_haven,
        min_adv20_usd=float(min_adv20_usd),
    )
    if eligible.empty:
        return pd.DataFrame(columns=["rank", "symbol", "sector", "score", "eligible"])

    for column in (
        "mom_3m",
        "mom_6m",
        "mom_12_1",
        "rel_mom_6m_vs_benchmark",
        "rel_mom_6m_vs_broad_benchmark",
        "high_252_gap",
        "sma200_gap",
        "vol_63",
    ):
        eligible[f"z_{column}"] = _zscore(eligible[column])
    eligible["drawdown_abs"] = eligible["maxdd_126"].abs()
    eligible["z_drawdown_abs"] = _zscore(eligible["drawdown_abs"])
    eligible["score"] = (
        eligible["z_mom_6m"] * 0.25
        + eligible["z_mom_3m"] * 0.20
        + eligible["z_rel_mom_6m_vs_benchmark"] * 0.20
        + eligible["z_rel_mom_6m_vs_broad_benchmark"] * 0.10
        + eligible["z_high_252_gap"] * 0.10
        + eligible["z_sma200_gap"] * 0.10
        - eligible["z_vol_63"] * 0.025
        - eligible["z_drawdown_abs"] * 0.025
    )
    current_holdings_set = _normalize_holdings(current_holdings)
    if current_holdings_set:
        eligible.loc[eligible["symbol"].isin(current_holdings_set), "score"] += float(hold_bonus)
    ranked = eligible.sort_values(
        by=["score", "rel_mom_6m_vs_benchmark", "mom_6m", "symbol"],
        ascending=[False, False, False, True],
    ).reset_index(drop=True)
    ranked.insert(0, "rank", range(1, len(ranked) + 1))
    output_columns = [
        "rank",
        "symbol",
        "sector",
        "score",
        "eligible",
        "close",
        "adv20_usd",
        "mom_3m",
        "mom_6m",
        "mom_12_1",
        "rel_mom_6m_vs_benchmark",
        "rel_mom_6m_vs_broad_benchmark",
        "high_252_gap",
        "sma200_gap",
        "vol_63",
        "maxdd_126",
    ]
    return ranked.loc[:, [column for column in output_columns if column in ranked.columns]]


def _resolve_stock_exposure(
    frame: pd.DataFrame,
    *,
    benchmark_symbol: str,
    broad_benchmark_symbol: str,
    safe_haven: str,
    min_adv20_usd: float,
    risk_on_exposure: float,
    soft_defense_exposure: float,
    hard_defense_exposure: float,
    soft_breadth_threshold: float,
    hard_breadth_threshold: float,
) -> tuple[float, str, float, bool]:
    candidates = _candidate_frame(
        frame,
        benchmark_symbol=benchmark_symbol,
        broad_benchmark_symbol=broad_benchmark_symbol,
        safe_haven=safe_haven,
        min_adv20_usd=float(min_adv20_usd),
    )
    breadth_ratio = float((candidates["sma200_gap"] > 0).mean()) if not candidates.empty else 0.0
    benchmark_rows = frame.loc[frame["symbol"] == _normalize_symbol(benchmark_symbol)]
    benchmark_trend_positive = bool(
        not benchmark_rows.empty
        and pd.notna(benchmark_rows["sma200_gap"].iloc[-1])
        and float(benchmark_rows["sma200_gap"].iloc[-1]) > 0
    )
    if (not benchmark_trend_positive) and breadth_ratio < float(hard_breadth_threshold):
        return float(hard_defense_exposure), "hard_defense", breadth_ratio, benchmark_trend_positive
    if (not benchmark_trend_positive) or breadth_ratio < float(soft_breadth_threshold):
        return float(soft_defense_exposure), "soft_defense", breadth_ratio, benchmark_trend_positive
    return float(risk_on_exposure), "risk_on", breadth_ratio, benchmark_trend_positive


def _resolve_effective_holdings_count(
    *,
    holdings_count: int,
    stock_exposure: float,
    portfolio_total_equity: float | None,
    min_position_value_usd: float,
) -> int:
    requested_count = int(holdings_count)
    if requested_count <= 0:
        raise ValueError("holdings_count must be positive")
    if stock_exposure <= 0:
        return 0
    if portfolio_total_equity is None or min_position_value_usd <= 0:
        return requested_count

    target_stock_value = float(portfolio_total_equity) * float(stock_exposure)
    if target_stock_value <= 0:
        return 0
    count_by_value = max(1, int(math.floor(target_stock_value / float(min_position_value_usd))))
    return max(1, min(requested_count, count_by_value))


def build_target_weights(
    feature_snapshot,
    current_holdings: Iterable[str] | None = None,
    *,
    benchmark_symbol: str = BENCHMARK_SYMBOL,
    broad_benchmark_symbol: str = BROAD_BENCHMARK_SYMBOL,
    safe_haven: str = SAFE_HAVEN,
    dynamic_universe_size: int = DEFAULT_DYNAMIC_UNIVERSE_SIZE,
    holdings_count: int = DEFAULT_HOLDINGS_COUNT,
    single_name_cap: float = DEFAULT_SINGLE_NAME_CAP,
    min_position_value_usd: float = DEFAULT_MIN_POSITION_VALUE_USD,
    portfolio_total_equity: float | None = None,
    hold_buffer: int = DEFAULT_HOLD_BUFFER,
    hold_bonus: float = DEFAULT_HOLD_BONUS,
    risk_on_exposure: float = DEFAULT_RISK_ON_EXPOSURE,
    soft_defense_exposure: float = DEFAULT_SOFT_DEFENSE_EXPOSURE,
    hard_defense_exposure: float = DEFAULT_HARD_DEFENSE_EXPOSURE,
    soft_breadth_threshold: float = DEFAULT_SOFT_BREADTH_THRESHOLD,
    hard_breadth_threshold: float = DEFAULT_HARD_BREADTH_THRESHOLD,
    min_adv20_usd: float = DEFAULT_MIN_ADV20_USD,
) -> tuple[dict[str, float], pd.DataFrame, dict[str, object]]:
    if holdings_count <= 0:
        raise ValueError("holdings_count must be positive")
    if single_name_cap <= 0:
        raise ValueError("single_name_cap must be positive")

    frame = _to_frame(feature_snapshot)
    benchmark_symbol = _normalize_symbol(benchmark_symbol)
    broad_benchmark_symbol = _normalize_symbol(broad_benchmark_symbol)
    safe_haven = _normalize_symbol(safe_haven)
    stock_exposure, regime, breadth_ratio, benchmark_trend_positive = _resolve_stock_exposure(
        frame,
        benchmark_symbol=benchmark_symbol,
        broad_benchmark_symbol=broad_benchmark_symbol,
        safe_haven=safe_haven,
        min_adv20_usd=float(min_adv20_usd),
        risk_on_exposure=float(risk_on_exposure),
        soft_defense_exposure=float(soft_defense_exposure),
        hard_defense_exposure=float(hard_defense_exposure),
        soft_breadth_threshold=float(soft_breadth_threshold),
        hard_breadth_threshold=float(hard_breadth_threshold),
    )
    effective_holdings_count = _resolve_effective_holdings_count(
        holdings_count=int(holdings_count),
        stock_exposure=stock_exposure,
        portfolio_total_equity=portfolio_total_equity,
        min_position_value_usd=float(min_position_value_usd),
    )
    ranked = score_candidates(
        frame,
        current_holdings,
        benchmark_symbol=benchmark_symbol,
        broad_benchmark_symbol=broad_benchmark_symbol,
        safe_haven=safe_haven,
        min_adv20_usd=float(min_adv20_usd),
        hold_bonus=float(hold_bonus),
    )
    metadata: dict[str, object] = {
        "benchmark_symbol": benchmark_symbol,
        "broad_benchmark_symbol": broad_benchmark_symbol,
        "benchmark_trend_positive": benchmark_trend_positive,
        "breadth_ratio": breadth_ratio,
        "regime": regime,
        "target_stock_weight": float(stock_exposure),
        "realized_stock_weight": 0.0,
        "safe_haven_weight": 1.0,
        "selected_symbols": (),
        "selected_count": 0,
        "candidate_count": int(len(ranked)),
        "dynamic_universe_size": int(dynamic_universe_size),
        "requested_holdings_count": int(holdings_count),
        "effective_holdings_count": int(effective_holdings_count),
        "portfolio_total_equity": portfolio_total_equity,
        "min_position_value_usd": float(min_position_value_usd),
        "single_name_cap": float(single_name_cap),
    }
    if ranked.empty or stock_exposure <= 0 or effective_holdings_count <= 0:
        return {safe_haven: 1.0}, ranked, metadata

    current_holdings_set = _normalize_holdings(current_holdings)
    ranked_symbols = ranked["symbol"].astype(str).tolist()
    rank_map = dict(zip(ranked["symbol"].astype(str), ranked["rank"].astype(int)))
    max_hold_rank = int(effective_holdings_count) + max(int(hold_buffer), 0)
    selected = [
        symbol
        for symbol in ranked_symbols
        if symbol in current_holdings_set and int(rank_map[symbol]) <= max_hold_rank
    ]
    for symbol in ranked_symbols:
        if len(selected) >= int(effective_holdings_count):
            break
        if symbol not in selected:
            selected.append(symbol)
    selected = selected[: int(effective_holdings_count)]
    if not selected:
        return {safe_haven: 1.0}, ranked, metadata

    per_name_weight = min(float(single_name_cap), float(stock_exposure) / len(selected))
    weights = {symbol: float(per_name_weight) for symbol in selected}
    invested_weight = float(sum(weights.values()))
    safe_weight = max(0.0, float(1.0 - invested_weight))
    if safe_weight > 1e-12:
        weights[safe_haven] = safe_weight

    metadata.update(
        {
            "realized_stock_weight": invested_weight,
            "safe_haven_weight": safe_weight,
            "selected_symbols": tuple(selected),
            "selected_count": int(len(selected)),
            "effective_single_name_cap": float(single_name_cap),
        }
    )
    return weights, ranked, metadata


def _normalize_blend_sleeves(blend_sleeves) -> tuple[dict[str, object], ...]:
    if not blend_sleeves:
        return ()
    normalized: list[dict[str, object]] = []
    for idx, sleeve in enumerate(blend_sleeves, start=1):
        sleeve_map = dict(sleeve or {})
        name = str(sleeve_map.get("name") or f"sleeve_{idx}").strip() or f"sleeve_{idx}"
        weight = float(sleeve_map.get("weight", 0.0))
        holdings_count = int(sleeve_map.get("holdings_count", 0))
        single_name_cap = float(sleeve_map.get("single_name_cap", 0.0))
        if weight <= 0:
            raise ValueError("blend sleeve weight must be positive")
        if holdings_count <= 0:
            raise ValueError("blend sleeve holdings_count must be positive")
        if single_name_cap <= 0:
            raise ValueError("blend sleeve single_name_cap must be positive")
        normalized.append(
            {
                "name": name,
                "weight": weight,
                "holdings_count": holdings_count,
                "single_name_cap": single_name_cap,
            }
        )
    total_weight = sum(float(sleeve["weight"]) for sleeve in normalized)
    if total_weight <= 0:
        raise ValueError("blend sleeve weights must sum to a positive value")
    return tuple(
        {
            **sleeve,
            "weight": float(sleeve["weight"]) / total_weight,
        }
        for sleeve in normalized
    )


def _normalize_profile_variant(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value or "").strip().lower().replace("-", "_")
    if not normalized:
        return None
    if normalized in {"custom", "custom_blend"}:
        return "custom_blend"
    variant = PROFILE_VARIANT_ALIASES.get(normalized)
    if variant is None:
        known = ", ".join(sorted(PROFILE_VARIANT_ALIASES))
        raise ValueError(f"unknown leader_rotation_profile_variant {value!r}; known variants: {known}")
    return variant


def _blend_sleeves_match(left, right) -> bool:
    left_sleeves = _normalize_blend_sleeves(left)
    right_sleeves = _normalize_blend_sleeves(right)
    if len(left_sleeves) != len(right_sleeves):
        return False
    for left_sleeve, right_sleeve in zip(left_sleeves, right_sleeves, strict=True):
        if int(left_sleeve["holdings_count"]) != int(right_sleeve["holdings_count"]):
            return False
        if abs(float(left_sleeve["single_name_cap"]) - float(right_sleeve["single_name_cap"])) > 1e-12:
            return False
        if abs(float(left_sleeve["weight"]) - float(right_sleeve["weight"])) > 1e-12:
            return False
    return True


def _infer_profile_variant_from_blend_sleeves(blend_sleeves) -> str:
    for variant, sleeves in PROFILE_VARIANT_BLEND_SLEEVES.items():
        if _blend_sleeves_match(blend_sleeves, sleeves):
            return variant
    return "custom_blend"


def _resolve_profile_variant_config(profile_variant, blend_sleeves) -> tuple[str, object | None]:
    variant = _normalize_profile_variant(profile_variant)
    if variant == PROFILE_VARIANT_TOP4_BASELINE:
        return PROFILE_VARIANT_TOP4_BASELINE, None
    if variant in PROFILE_VARIANT_BLEND_SLEEVES:
        return variant, PROFILE_VARIANT_BLEND_SLEEVES[variant]
    if blend_sleeves:
        return _infer_profile_variant_from_blend_sleeves(blend_sleeves), blend_sleeves
    return PROFILE_VARIANT_TOP4_BASELINE, None


def _normalize_shadow_profile_variants(value) -> tuple[str, ...]:
    if value in (None, False):
        return ()
    if value is True:
        return DEFAULT_SHADOW_PROFILE_VARIANTS
    if isinstance(value, str):
        normalized = value.strip().lower()
        if not normalized or normalized in {"0", "false", "no", "none", "off"}:
            return ()
        if normalized in {"1", "true", "yes", "all", "default"}:
            return DEFAULT_SHADOW_PROFILE_VARIANTS
        raw_values = tuple(part.strip() for part in value.split(","))
    else:
        raw_values = tuple(value)

    variants: list[str] = []
    for raw_value in raw_values:
        variant = _normalize_profile_variant(raw_value)
        if variant in (None, "custom_blend"):
            continue
        if variant not in variants:
            variants.append(variant)
    return tuple(variants)


def _build_profile_variant_target_weights(
    feature_snapshot,
    current_holdings,
    *,
    profile_variant: str,
    portfolio_total_equity: float | None,
    kwargs: Mapping[str, object],
) -> tuple[dict[str, float], pd.DataFrame, dict[str, object]]:
    resolved_profile_variant, resolved_blend_sleeves = _resolve_profile_variant_config(profile_variant, None)
    if resolved_blend_sleeves:
        weights, ranked, metadata = build_blended_target_weights(
            feature_snapshot,
            current_holdings,
            blend_sleeves=resolved_blend_sleeves,
            portfolio_total_equity=portfolio_total_equity,
            **dict(kwargs),
        )
    else:
        weights, ranked, metadata = build_target_weights(
            feature_snapshot,
            current_holdings,
            portfolio_total_equity=portfolio_total_equity,
            **dict(kwargs),
        )
    metadata["leader_rotation_profile_variant"] = resolved_profile_variant
    return weights, ranked, metadata


def _largest_weight_delta(weight_delta_vs_active: Mapping[str, float], *, increase: bool) -> dict[str, object] | None:
    deltas = {
        _normalize_symbol(symbol): float(delta)
        for symbol, delta in dict(weight_delta_vs_active or {}).items()
        if (float(delta) > 0 if increase else float(delta) < 0)
    }
    if not deltas:
        return None
    symbol, delta = sorted(
        deltas.items(),
        key=lambda item: ((-item[1] if increase else item[1]), item[0]),
    )[0]
    return {"symbol": symbol, "delta": delta}


def _build_shadow_profile_variant_diagnostics(
    feature_snapshot,
    current_holdings,
    *,
    shadow_profile_variants,
    active_weights: Mapping[str, float],
    portfolio_total_equity: float | None,
    kwargs: Mapping[str, object],
) -> dict[str, object]:
    diagnostics: dict[str, object] = {}
    normalized_active_weights = {
        _normalize_symbol(symbol): float(weight)
        for symbol, weight in dict(active_weights or {}).items()
    }
    for variant in _normalize_shadow_profile_variants(shadow_profile_variants):
        weights, _ranked, metadata = _build_profile_variant_target_weights(
            feature_snapshot,
            current_holdings,
            profile_variant=variant,
            portfolio_total_equity=portfolio_total_equity,
            kwargs=kwargs,
        )
        normalized_weights = {
            _normalize_symbol(symbol): float(weight)
            for symbol, weight in dict(weights or {}).items()
        }
        delta_symbols = sorted(set(normalized_weights) | set(normalized_active_weights))
        weight_delta_vs_active = {
            symbol: normalized_weights.get(symbol, 0.0) - normalized_active_weights.get(symbol, 0.0)
            for symbol in delta_symbols
            if abs(normalized_weights.get(symbol, 0.0) - normalized_active_weights.get(symbol, 0.0)) > 1e-12
        }
        turnover_delta_vs_active = 0.5 * sum(abs(delta) for delta in weight_delta_vs_active.values())
        diagnostics[variant] = {
            "weights": normalized_weights,
            "weight_delta_vs_active": weight_delta_vs_active,
            "turnover_delta_vs_active": float(turnover_delta_vs_active),
            "largest_weight_increase_vs_active": _largest_weight_delta(weight_delta_vs_active, increase=True),
            "largest_weight_decrease_vs_active": _largest_weight_delta(weight_delta_vs_active, increase=False),
            "selected_symbols": tuple(metadata.get("selected_symbols", ())),
            "selected_count": int(metadata.get("selected_count", 0)),
            "realized_stock_weight": float(metadata.get("realized_stock_weight", 0.0)),
            "safe_haven_weight": float(metadata.get("safe_haven_weight", 0.0)),
        }
    return diagnostics


def build_shadow_operator_review_rows(
    shadow_diagnostics: Mapping[str, object],
    *,
    active_variant: str,
) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for variant, raw_diagnostics in dict(shadow_diagnostics or {}).items():
        diagnostics = dict(raw_diagnostics or {})
        largest_increase = diagnostics.get("largest_weight_increase_vs_active") or {}
        largest_decrease = diagnostics.get("largest_weight_decrease_vs_active") or {}
        turnover_delta = float(diagnostics.get("turnover_delta_vs_active", 0.0))
        largest_increase_symbol = str(largest_increase.get("symbol", ""))
        largest_increase_delta = float(largest_increase.get("delta", 0.0) or 0.0)
        largest_decrease_symbol = str(largest_decrease.get("symbol", ""))
        largest_decrease_delta = float(largest_decrease.get("delta", 0.0) or 0.0)
        row = {
            "schema_version": SHADOW_REVIEW_SCHEMA_VERSION,
            "active_variant": str(active_variant or ""),
            "shadow_variant": str(variant or ""),
            "selected_count": int(diagnostics.get("selected_count", 0)),
            "realized_stock_weight": float(diagnostics.get("realized_stock_weight", 0.0)),
            "safe_haven_weight": float(diagnostics.get("safe_haven_weight", 0.0)),
            "turnover_delta_vs_active": turnover_delta,
            "largest_increase_symbol": largest_increase_symbol,
            "largest_increase_delta": largest_increase_delta,
            "largest_decrease_symbol": largest_decrease_symbol,
            "largest_decrease_delta": largest_decrease_delta,
        }
        row["review_note"] = (
            f"active={row['active_variant']} shadow={row['shadow_variant']} "
            f"turnover_delta={turnover_delta:.2%} "
            f"max_increase={largest_increase_symbol or '-'}:{largest_increase_delta:+.2%} "
            f"max_decrease={largest_decrease_symbol or '-'}:{largest_decrease_delta:+.2%}"
        )
        rows.append({field: row[field] for field in SHADOW_REVIEW_ROW_FIELDS})
    return tuple(rows)


def build_blended_target_weights(
    feature_snapshot,
    current_holdings: Iterable[str] | None = None,
    *,
    blend_sleeves=DEFAULT_TOP50_BALANCED_BLEND_SLEEVES,
    portfolio_total_equity: float | None = None,
    **kwargs,
) -> tuple[dict[str, float], pd.DataFrame, dict[str, object]]:
    sleeves = _normalize_blend_sleeves(blend_sleeves)
    if not sleeves:
        raise ValueError("blend_sleeves must contain at least one sleeve")

    safe_haven = _normalize_symbol(kwargs.get("safe_haven", SAFE_HAVEN))
    combined_weights: dict[str, float] = {}
    ranked = pd.DataFrame()
    sleeve_rows: list[dict[str, object]] = []
    first_metadata: dict[str, object] | None = None
    selected_symbols: list[str] = []

    for sleeve in sleeves:
        sleeve_weight = float(sleeve["weight"])
        sleeve_kwargs = dict(kwargs)
        sleeve_kwargs["holdings_count"] = int(sleeve["holdings_count"])
        sleeve_kwargs["single_name_cap"] = float(sleeve["single_name_cap"])
        if portfolio_total_equity is not None:
            sleeve_kwargs["portfolio_total_equity"] = float(portfolio_total_equity) * sleeve_weight
        sleeve_weights, sleeve_ranked, sleeve_metadata = build_target_weights(
            feature_snapshot,
            current_holdings,
            **sleeve_kwargs,
        )
        if ranked.empty:
            ranked = sleeve_ranked
        if first_metadata is None:
            first_metadata = dict(sleeve_metadata)
        for symbol, value in sleeve_weights.items():
            symbol_text = _normalize_symbol(symbol)
            combined_weights[symbol_text] = combined_weights.get(symbol_text, 0.0) + sleeve_weight * float(value)
        sleeve_selected = tuple(str(symbol) for symbol in sleeve_metadata.get("selected_symbols", ()))
        selected_symbols.extend(symbol for symbol in sleeve_selected if symbol != safe_haven)
        sleeve_rows.append(
            {
                "name": sleeve["name"],
                "weight": sleeve_weight,
                "holdings_count": int(sleeve["holdings_count"]),
                "single_name_cap": float(sleeve["single_name_cap"]),
                "selected_symbols": sleeve_selected,
                "realized_stock_weight": float(sleeve_metadata.get("realized_stock_weight", 0.0)),
                "safe_haven_weight": float(sleeve_metadata.get("safe_haven_weight", 0.0)),
            }
        )

    total_weight = sum(combined_weights.values())
    if total_weight > 0:
        combined_weights = {symbol: value / total_weight for symbol, value in combined_weights.items()}
    safe_haven_weight = float(combined_weights.get(safe_haven, 0.0))
    realized_stock_weight = max(0.0, 1.0 - safe_haven_weight)
    selected_unique = tuple(
        symbol
        for symbol in dict.fromkeys(selected_symbols)
        if float(combined_weights.get(symbol, 0.0)) > 1e-12
    )

    metadata = dict(first_metadata or {})
    metadata.update(
        {
            "target_stock_weight": realized_stock_weight,
            "realized_stock_weight": realized_stock_weight,
            "safe_haven_weight": safe_haven_weight,
            "selected_symbols": selected_unique,
            "selected_count": int(len(selected_unique)),
            "blend_sleeves": tuple(sleeve_rows),
            "blend_mode": "fixed_weighted_sleeves",
            "portfolio_total_equity": portfolio_total_equity,
            "requested_holdings_count": max(int(sleeve["holdings_count"]) for sleeve in sleeves),
            "effective_holdings_count": int(len(selected_unique)),
            "single_name_cap": max(float(sleeve["single_name_cap"]) for sleeve in sleeves),
        }
    )
    return combined_weights, ranked, metadata


def extract_managed_symbols(
    feature_snapshot,
    *,
    benchmark_symbol: str = BENCHMARK_SYMBOL,
    broad_benchmark_symbol: str = BROAD_BENCHMARK_SYMBOL,
    safe_haven: str = SAFE_HAVEN,
) -> tuple[str, ...]:
    frame = _to_frame(feature_snapshot)
    excluded = {_normalize_symbol(benchmark_symbol), _normalize_symbol(broad_benchmark_symbol)}
    safe_haven = _normalize_symbol(safe_haven)
    symbols = [symbol for symbol in frame["symbol"].tolist() if symbol not in excluded]
    if safe_haven and safe_haven not in symbols:
        symbols.append(safe_haven)
    return tuple(dict.fromkeys(symbols))


def _load_calendar() -> tuple[Any | None, str]:
    try:
        module = import_module("pandas_market_calendars")
    except Exception:
        return None, "business_day_fallback"
    try:
        calendar = module.get_calendar("NYSE")
    except Exception:
        return None, "business_day_fallback"
    if calendar is None:
        return None, "business_day_fallback"
    return calendar, "nyse_calendar"


def _next_trading_days(after_date: pd.Timestamp, *, count: int) -> tuple[tuple[pd.Timestamp, ...], str]:
    if count <= 0:
        return (), "disabled"
    start_date = pd.Timestamp(after_date).normalize() + pd.Timedelta(days=1)
    calendar, calendar_source = _load_calendar()
    if calendar is None:
        return tuple(pd.bdate_range(start=start_date, periods=count).normalize()), calendar_source
    end_date = start_date + pd.Timedelta(days=max(10, count * 5))
    schedule = calendar.schedule(start_date=start_date, end_date=end_date)
    if getattr(schedule, "index", None) is None or len(schedule.index) == 0:
        return (), calendar_source
    sessions = pd.to_datetime(schedule.index)
    if getattr(sessions, "tz", None) is not None:
        sessions = sessions.tz_localize(None)
    sessions = sessions.normalize()
    return tuple(sessions[:count]), calendar_source


def evaluate_execution_window(
    feature_snapshot,
    *,
    run_as_of=None,
    runtime_execution_window_trading_days: int = DEFAULT_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS,
) -> dict[str, object]:
    frame = _to_frame(feature_snapshot)
    if "as_of" not in frame.columns:
        return {"should_execute": True, "no_op_reason": None, "snapshot_as_of": None, "execution_window": ()}

    snapshot_as_of = pd.Timestamp(frame["as_of"].max()).normalize()
    if run_as_of is None:
        return {"should_execute": True, "no_op_reason": None, "snapshot_as_of": snapshot_as_of, "execution_window": ()}

    run_date = pd.Timestamp(run_as_of).normalize()
    allowed_days, calendar_source = _next_trading_days(
        snapshot_as_of,
        count=int(runtime_execution_window_trading_days),
    )
    if not allowed_days:
        return {
            "should_execute": False,
            "no_op_reason": f"no_execution_window_after_snapshot:{snapshot_as_of.date()}",
            "snapshot_as_of": snapshot_as_of,
            "execution_window": (),
            "calendar_source": calendar_source,
        }
    if run_date not in allowed_days:
        allowed_text = ",".join(day.date().isoformat() for day in allowed_days)
        return {
            "should_execute": False,
            "no_op_reason": f"outside_monthly_execution_window snapshot={snapshot_as_of.date()} allowed={allowed_text}",
            "snapshot_as_of": snapshot_as_of,
            "execution_window": tuple(day.date().isoformat() for day in allowed_days),
            "calendar_source": calendar_source,
        }
    return {
        "should_execute": True,
        "no_op_reason": None,
        "snapshot_as_of": snapshot_as_of,
        "execution_window": tuple(day.date().isoformat() for day in allowed_days),
        "calendar_source": calendar_source,
    }


def compute_signals(
    feature_snapshot,
    current_holdings,
    *,
    run_as_of=None,
    runtime_execution_window_trading_days: int = DEFAULT_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS,
    **kwargs,
):
    kwargs.pop("translator", None)
    kwargs.pop("signal_text_fn", None)
    kwargs.pop("execution_cash_reserve_ratio", None)
    profile_variant = kwargs.pop("leader_rotation_profile_variant", None)
    shadow_profile_variants = kwargs.pop("leader_rotation_shadow_variants", None)
    blend_sleeves = kwargs.pop("blend_sleeves", None)
    portfolio_total_equity = kwargs.pop("portfolio_total_equity", None)
    resolved_profile_variant, resolved_blend_sleeves = _resolve_profile_variant_config(profile_variant, blend_sleeves)
    benchmark_symbol = kwargs.get("benchmark_symbol", BENCHMARK_SYMBOL)
    broad_benchmark_symbol = kwargs.get("broad_benchmark_symbol", BROAD_BENCHMARK_SYMBOL)
    safe_haven = kwargs.get("safe_haven", SAFE_HAVEN)
    managed_symbols = extract_managed_symbols(
        feature_snapshot,
        benchmark_symbol=benchmark_symbol,
        broad_benchmark_symbol=broad_benchmark_symbol,
        safe_haven=safe_haven,
    )
    execution_window = evaluate_execution_window(
        feature_snapshot,
        run_as_of=run_as_of,
        runtime_execution_window_trading_days=runtime_execution_window_trading_days,
    )
    if not execution_window["should_execute"]:
        snapshot_as_of_text = (
            pd.Timestamp(execution_window["snapshot_as_of"]).date().isoformat()
            if execution_window["snapshot_as_of"] is not None
            else "<none>"
        )
        allowed_dates = ", ".join(str(day) for day in execution_window["execution_window"]) or "<none>"
        status_code = (
            "status_no_execution_window_after_snapshot"
            if str(execution_window["no_op_reason"] or "").startswith("no_execution_window_after_snapshot")
            else "status_monthly_snapshot_waiting_window"
        )
        status_desc = f"no-op | reason={execution_window['no_op_reason']} | snapshot_as_of={execution_window['snapshot_as_of']}"
        return (
            None,
            "monthly snapshot cadence | waiting inside execution window",
            False,
            status_desc,
            {
                "managed_symbols": managed_symbols,
                "status_icon": STATUS_ICON,
                "snapshot_as_of": execution_window["snapshot_as_of"],
                "execution_window": execution_window["execution_window"],
                "no_op_reason": execution_window["no_op_reason"],
                "execution_calendar_source": execution_window.get("calendar_source"),
                "notification_context": {
                    "signal": {
                        "code": "signal_monthly_snapshot_waiting",
                        "params": {},
                    },
                    "status": {
                        "code": status_code,
                        "params": {
                            "snapshot_as_of": snapshot_as_of_text,
                            "allowed_dates": allowed_dates,
                        },
                    },
                },
            },
        )

    if resolved_blend_sleeves:
        weights, ranked, metadata = build_blended_target_weights(
            feature_snapshot,
            current_holdings,
            blend_sleeves=resolved_blend_sleeves,
            portfolio_total_equity=portfolio_total_equity,
            **kwargs,
        )
    else:
        weights, ranked, metadata = build_target_weights(
            feature_snapshot,
            current_holdings,
            portfolio_total_equity=portfolio_total_equity,
            **kwargs,
        )
    metadata["leader_rotation_profile_variant"] = resolved_profile_variant
    shadow_diagnostics = _build_shadow_profile_variant_diagnostics(
        feature_snapshot,
        current_holdings,
        shadow_profile_variants=shadow_profile_variants,
        active_weights=weights,
        portfolio_total_equity=portfolio_total_equity,
        kwargs=kwargs,
    )
    if shadow_diagnostics:
        metadata["leader_rotation_shadow_variants"] = shadow_diagnostics
        metadata["leader_rotation_shadow_review_schema_version"] = SHADOW_REVIEW_SCHEMA_VERSION
        metadata["leader_rotation_shadow_review_row_fields"] = SHADOW_REVIEW_ROW_FIELDS
        metadata["leader_rotation_shadow_review_rows"] = build_shadow_operator_review_rows(
            shadow_diagnostics,
            active_variant=resolved_profile_variant,
        )
    top_preview = ", ".join(
        f"{row.symbol}({row.score:.2f})"
        for row in ranked.head(5).itertuples(index=False)
    )
    signal_desc = (
        f"regime={metadata['regime']} breadth={metadata['breadth_ratio']:.1%} "
        f"benchmark_trend={'up' if metadata['benchmark_trend_positive'] else 'down'} "
        f"target_stock={metadata['target_stock_weight']:.1%} realized_stock={metadata['realized_stock_weight']:.1%} "
        f"selected={metadata['selected_count']} top={top_preview}"
    )
    status_desc = (
        f"regime={metadata['regime']} | "
        f"breadth={metadata['breadth_ratio']:.1%} | "
        f"target_stock={metadata['target_stock_weight']:.1%} | "
        f"realized_stock={metadata['realized_stock_weight']:.1%}"
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
            "snapshot_as_of": execution_window["snapshot_as_of"],
            "execution_window": execution_window["execution_window"],
            "execution_calendar_source": execution_window.get("calendar_source"),
        },
    )
