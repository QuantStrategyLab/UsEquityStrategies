from __future__ import annotations

import json
import math
from collections.abc import Mapping
from importlib import import_module
from pathlib import Path
from typing import Any

import pandas as pd

SIGNAL_SOURCE = "feature_snapshot"
STATUS_ICON = "🧲"
PROFILE_NAME = "tech_pullback_cash_buffer"
BRANCH_ROLE = "cash-buffered parallel branch"
BENCHMARK_SYMBOL = "QQQ"
SAFE_HAVEN = "BOXX"
DEFAULT_HOLDINGS_COUNT = 8
DEFAULT_SINGLE_NAME_CAP = 0.10
DEFAULT_SECTOR_CAP = 0.40
DEFAULT_HOLD_BONUS = 0.10
DEFAULT_RISK_ON_EXPOSURE = 0.80
DEFAULT_SOFT_DEFENSE_EXPOSURE = 0.60
DEFAULT_HARD_DEFENSE_EXPOSURE = 0.00
DEFAULT_SOFT_BREADTH_THRESHOLD = 0.55
DEFAULT_HARD_BREADTH_THRESHOLD = 0.35
DEFAULT_MIN_ADV20_USD = 50_000_000.0
DEFAULT_NORMALIZATION = "universe_cross_sectional"
DEFAULT_SCORE_TEMPLATE = "balanced_pullback"
DEFAULT_SECTOR_WHITELIST = ("Information Technology", "Communication")
DEFAULT_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS = 3
DEFAULT_EXECUTION_CASH_RESERVE_RATIO = 0.0
SNAPSHOT_DATE_COLUMNS = ("as_of", "snapshot_date")
MAX_SNAPSHOT_MONTH_LAG = 1
REQUIRE_SNAPSHOT_MANIFEST = True
SNAPSHOT_CONTRACT_VERSION = "qqq_tech_enhancement.feature_snapshot.v1"

REQUIRED_FEATURE_COLUMNS = frozenset(
    {
        "symbol",
        "sector",
        "close",
        "adv20_usd",
        "history_days",
        "mom_6_1",
        "mom_12_1",
        "sma20_gap",
        "sma50_gap",
        "sma200_gap",
        "ma50_over_ma200",
        "vol_63",
        "maxdd_126",
        "breakout_252",
        "dist_63_high",
        "dist_126_high",
        "rebound_20",
    }
)

FEATURE_SIGNAL_KWARG_KEYS = (
    "benchmark_symbol",
    "safe_haven",
    "holdings_count",
    "single_name_cap",
    "sector_cap",
    "hold_bonus",
    "risk_on_exposure",
    "soft_defense_exposure",
    "hard_defense_exposure",
    "soft_breadth_threshold",
    "hard_breadth_threshold",
    "min_adv20_usd",
    "sector_whitelist",
    "normalization",
    "score_template",
    "run_as_of",
    "runtime_execution_window_trading_days",
    "runtime_config_name",
    "runtime_config_path",
    "runtime_config_source",
    "residual_proxy",
)


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


def _normalize_symbol_series(values: pd.Series) -> pd.Series:
    return values.astype(str).str.upper().str.strip()


def _normalize_holdings(current_holdings) -> set[str]:
    if current_holdings is None:
        return set()
    raw_symbols = current_holdings.keys() if isinstance(current_holdings, Mapping) else current_holdings
    normalized: set[str] = set()
    for item in raw_symbols:
        symbol = getattr(item, "symbol", item)
        symbol_text = str(symbol or "").strip().upper()
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

    frame["symbol"] = _normalize_symbol_series(frame["symbol"])
    frame["sector"] = frame["sector"].fillna("unknown").astype(str).str.strip().replace("", "unknown")
    if "as_of" in frame.columns:
        frame["as_of"] = pd.to_datetime(frame["as_of"], utc=False).dt.tz_localize(None).dt.normalize()
    if "base_eligible" not in frame.columns:
        if "eligible" in frame.columns:
            frame["base_eligible"] = frame["eligible"]
        else:
            frame["base_eligible"] = True
    frame["base_eligible"] = frame["base_eligible"].map(_coerce_bool)

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


def _group_zscore(values: pd.Series, group_keys: pd.Series | None) -> pd.Series:
    if group_keys is None:
        return _zscore(values)
    numeric = pd.to_numeric(values, errors="coerce")
    return numeric.groupby(group_keys).transform(_zscore).fillna(0.0)


def _apply_universe_filter(
    frame: pd.DataFrame,
    *,
    benchmark_symbol: str,
    safe_haven: str,
    sector_whitelist: tuple[str, ...],
    min_adv20_usd: float,
) -> pd.DataFrame:
    filtered = frame.loc[
        ~frame["symbol"].isin([benchmark_symbol, safe_haven])
        & frame["base_eligible"]
        & frame["adv20_usd"].ge(min_adv20_usd)
    ].copy()
    if sector_whitelist:
        filtered = filtered.loc[filtered["sector"].isin(sector_whitelist)].copy()
    return filtered


def _compute_family_features(scored: pd.DataFrame, benchmark_rows: pd.DataFrame) -> pd.DataFrame:
    qqq_rows = benchmark_rows.loc[benchmark_rows["symbol"] == "QQQ"]
    if qqq_rows.empty:
        raise RuntimeError("QQQ benchmark row missing from snapshot")
    qqq_row = qqq_rows.iloc[-1]
    qqq_mom_6_1 = float(qqq_row["mom_6_1"])
    qqq_mom_12_1 = float(qqq_row["mom_12_1"])

    scored = scored.copy()
    scored["excess_mom_6_1"] = scored["mom_6_1"] - qqq_mom_6_1
    scored["excess_mom_12_1"] = scored["mom_12_1"] - qqq_mom_12_1
    scored["drawdown_abs"] = scored["maxdd_126"].abs()
    scored["trend_strength"] = (
        scored["sma200_gap"] * 0.45
        + scored["breakout_252"] * 0.35
        + scored["ma50_over_ma200"] * 0.20
    )
    scored["controlled_pullback_score"] = (
        -((scored["dist_63_high"] + 0.08).abs() * 0.55)
        -((scored["dist_126_high"] + 0.12).abs() * 0.25)
        -(((-scored["sma50_gap"]).clip(lower=0.0)) * 0.10)
        -(((-scored["sma200_gap"]).clip(lower=0.0)) * 0.10)
    )
    scored["recovery_confirmation"] = (
        scored["sma20_gap"] * 0.40
        + scored["sma50_gap"] * 0.35
        + scored["rebound_20"] * 0.25
    )
    if scored["sector"].nunique() > 1:
        group_median = scored.groupby("sector")["excess_mom_12_1"].transform("median")
    else:
        group_median = pd.Series(float(scored["excess_mom_12_1"].median()), index=scored.index)
    scored["rel_strength_vs_group"] = scored["excess_mom_12_1"] - group_median
    return scored


def _score_candidates(
    frame: pd.DataFrame,
    current_holdings: set[str],
    *,
    benchmark_symbol: str,
    safe_haven: str,
    sector_whitelist: tuple[str, ...],
    min_adv20_usd: float,
    normalization: str,
    score_template: str,
    hold_bonus: float,
) -> pd.DataFrame:
    benchmark_rows = frame.loc[frame["symbol"].isin([benchmark_symbol, "SPY", "QQQ", "XLK", "SMH"])].copy()
    eligible = _apply_universe_filter(
        frame,
        benchmark_symbol=benchmark_symbol,
        safe_haven=safe_haven,
        sector_whitelist=sector_whitelist,
        min_adv20_usd=min_adv20_usd,
    )
    if eligible.empty:
        return eligible
    scored = _compute_family_features(eligible, benchmark_rows)

    if normalization == "sector":
        group_keys = scored["sector"] if scored["sector"].nunique() > 1 else None
    elif normalization in {"universe", "universe_cross_sectional"}:
        group_keys = None
    else:
        raise ValueError(f"Unsupported normalization: {normalization}")

    for column in (
        "excess_mom_12_1",
        "excess_mom_6_1",
        "trend_strength",
        "controlled_pullback_score",
        "recovery_confirmation",
        "rel_strength_vs_group",
        "vol_63",
        "drawdown_abs",
    ):
        scored[f"z_{column}"] = _group_zscore(scored[column], group_keys)

    if score_template != "balanced_pullback":
        raise ValueError(f"Unsupported score_template: {score_template}")

    scored["score"] = (
        scored["z_excess_mom_12_1"] * 0.25
        + scored["z_excess_mom_6_1"] * 0.20
        + scored["z_trend_strength"] * 0.15
        + scored["z_controlled_pullback_score"] * 0.15
        + scored["z_recovery_confirmation"] * 0.10
        + scored["z_rel_strength_vs_group"] * 0.10
        - scored["z_vol_63"] * 0.03
        - scored["z_drawdown_abs"] * 0.02
    )
    if current_holdings:
        scored.loc[scored["symbol"].isin(current_holdings), "score"] += float(hold_bonus)
    return scored


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
    risk_on_exposure: float,
    soft_defense_exposure: float,
    hard_defense_exposure: float,
) -> float:
    if regime == "hard_defense":
        return float(hard_defense_exposure)
    if regime == "soft_defense":
        return float(soft_defense_exposure)
    return float(risk_on_exposure)


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
    risk_on_exposure: float = DEFAULT_RISK_ON_EXPOSURE,
    soft_defense_exposure: float = DEFAULT_SOFT_DEFENSE_EXPOSURE,
    hard_defense_exposure: float = DEFAULT_HARD_DEFENSE_EXPOSURE,
    soft_breadth_threshold: float = DEFAULT_SOFT_BREADTH_THRESHOLD,
    hard_breadth_threshold: float = DEFAULT_HARD_BREADTH_THRESHOLD,
    min_adv20_usd: float = DEFAULT_MIN_ADV20_USD,
    sector_whitelist: tuple[str, ...] = DEFAULT_SECTOR_WHITELIST,
    normalization: str = DEFAULT_NORMALIZATION,
    score_template: str = DEFAULT_SCORE_TEMPLATE,
    residual_proxy: str = "simple_excess_return_vs_QQQ",
    runtime_config_name: str | None = None,
    runtime_config_path: str | None = None,
    runtime_config_source: str | None = None,
):
    if holdings_count <= 0:
        raise ValueError("holdings_count must be positive")
    if single_name_cap <= 0 or sector_cap <= 0:
        raise ValueError("single_name_cap and sector_cap must be positive")

    benchmark_symbol = str(benchmark_symbol or "").strip().upper()
    safe_haven = str(safe_haven or "").strip().upper()
    frame = _to_frame(feature_snapshot)
    current_holdings_set = _normalize_holdings(current_holdings)

    benchmark_rows = frame.loc[frame["symbol"] == benchmark_symbol]
    benchmark_trend_positive = True
    if not benchmark_rows.empty:
        benchmark_trend_positive = bool(float(benchmark_rows.iloc[-1]["sma200_gap"]) > 0)

    eligible_for_breadth = _apply_universe_filter(
        frame,
        benchmark_symbol=benchmark_symbol,
        safe_haven=safe_haven,
        sector_whitelist=tuple(sector_whitelist or ()),
        min_adv20_usd=float(min_adv20_usd),
    )
    breadth_ratio = float((eligible_for_breadth["sma200_gap"] > 0).mean()) if not eligible_for_breadth.empty else 0.0
    regime = _resolve_regime(
        benchmark_trend_positive=benchmark_trend_positive,
        breadth_ratio=breadth_ratio,
        soft_breadth_threshold=float(soft_breadth_threshold),
        hard_breadth_threshold=float(hard_breadth_threshold),
    )
    stock_exposure = _stock_exposure_for_regime(
        regime,
        risk_on_exposure=float(risk_on_exposure),
        soft_defense_exposure=float(soft_defense_exposure),
        hard_defense_exposure=float(hard_defense_exposure),
    )

    if eligible_for_breadth.empty or stock_exposure <= 0:
        signal = (
            f"regime={regime} breadth={breadth_ratio:.1%} "
            f"benchmark_trend={'up' if benchmark_trend_positive else 'down'}"
        )
        return {safe_haven: 1.0}, signal, {
            "benchmark_symbol": benchmark_symbol,
            "benchmark_trend_positive": benchmark_trend_positive,
            "breadth_ratio": breadth_ratio,
            "regime": regime,
            "target_stock_weight": 0.0,
            "realized_stock_weight": 0.0,
            "safe_haven_weight": 1.0,
            "selected_symbols": (),
            "selected_count": 0,
            "candidate_count": int(len(eligible_for_breadth)),
            "runtime_config_name": runtime_config_name,
            "runtime_config_path": runtime_config_path,
            "runtime_config_source": runtime_config_source,
            "residual_proxy": residual_proxy,
        }

    scored = _score_candidates(
        frame,
        current_holdings_set,
        benchmark_symbol=benchmark_symbol,
        safe_haven=safe_haven,
        sector_whitelist=tuple(sector_whitelist or ()),
        min_adv20_usd=float(min_adv20_usd),
        normalization=normalization,
        score_template=score_template,
        hold_bonus=float(hold_bonus),
    )
    if scored.empty:
        signal = (
            f"regime={regime} breadth={breadth_ratio:.1%} "
            f"benchmark_trend={'up' if benchmark_trend_positive else 'down'} no_selection"
        )
        return {safe_haven: 1.0}, signal, {
            "benchmark_symbol": benchmark_symbol,
            "benchmark_trend_positive": benchmark_trend_positive,
            "breadth_ratio": breadth_ratio,
            "regime": regime,
            "target_stock_weight": 0.0,
            "realized_stock_weight": 0.0,
            "safe_haven_weight": 1.0,
            "selected_symbols": (),
            "selected_count": 0,
            "candidate_count": 0,
            "runtime_config_name": runtime_config_name,
            "runtime_config_path": runtime_config_path,
            "runtime_config_source": runtime_config_source,
            "residual_proxy": residual_proxy,
        }

    ranked = scored.sort_values(
        by=["score", "excess_mom_12_1", "trend_strength", "symbol"],
        ascending=[False, False, False, True],
    )

    per_name_target = stock_exposure / max(holdings_count, 1)
    sector_slot_cap = holdings_count if per_name_target <= 0 else max(1, int(math.floor(float(sector_cap) / per_name_target)))
    selected_rows = []
    sector_counts: dict[str, int] = {}
    for row in ranked.itertuples(index=False):
        sector = str(row.sector)
        if sector_counts.get(sector, 0) >= sector_slot_cap:
            continue
        selected_rows.append(row._asdict())
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        if len(selected_rows) >= holdings_count:
            break
    selected = pd.DataFrame(selected_rows)
    if selected.empty:
        return {safe_haven: 1.0}, "no_selection", {
            "benchmark_symbol": benchmark_symbol,
            "benchmark_trend_positive": benchmark_trend_positive,
            "breadth_ratio": breadth_ratio,
            "regime": regime,
            "target_stock_weight": 0.0,
            "realized_stock_weight": 0.0,
            "safe_haven_weight": 1.0,
            "selected_symbols": (),
            "selected_count": 0,
            "candidate_count": int(len(scored)),
            "sector_slot_cap": sector_slot_cap,
            "runtime_config_name": runtime_config_name,
            "runtime_config_path": runtime_config_path,
            "runtime_config_source": runtime_config_source,
            "residual_proxy": residual_proxy,
        }

    per_name_weight = min(float(single_name_cap), stock_exposure / len(selected))
    invested_weight = float(per_name_weight * len(selected))
    safe_haven_weight = max(0.0, float(1.0 - invested_weight))
    weights = {row.symbol: float(per_name_weight) for row in selected.itertuples(index=False)}
    if safe_haven_weight > 1e-12:
        weights[safe_haven] = safe_haven_weight

    top_preview = ", ".join(
        f"{row.symbol}({row.score:.2f})"
        for row in selected.head(5).itertuples(index=False)
    )
    signal = (
        f"regime={regime} breadth={breadth_ratio:.1%} "
        f"benchmark_trend={'up' if benchmark_trend_positive else 'down'} "
        f"target_stock={stock_exposure:.1%} realized_stock={invested_weight:.1%} "
        f"selected={len(selected)} top={top_preview}"
    )
    metadata = {
        "benchmark_symbol": benchmark_symbol,
        "benchmark_trend_positive": benchmark_trend_positive,
        "breadth_ratio": breadth_ratio,
        "regime": regime,
        "target_stock_weight": float(stock_exposure),
        "realized_stock_weight": invested_weight,
        "safe_haven_weight": safe_haven_weight,
        "selected_symbols": tuple(selected["symbol"].tolist()),
        "selected_count": int(len(selected)),
        "candidate_count": int(len(scored)),
        "sector_slot_cap": sector_slot_cap,
        "runtime_config_name": runtime_config_name,
        "runtime_config_path": runtime_config_path,
        "runtime_config_source": runtime_config_source,
        "residual_proxy": residual_proxy,
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


def _load_nyse_calendar() -> tuple[Any | None, str]:
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
    calendar, calendar_source = _load_nyse_calendar()
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
        return {
            "should_execute": True,
            "no_op_reason": None,
            "snapshot_as_of": None,
            "execution_window": (),
        }

    snapshot_as_of = pd.Timestamp(frame["as_of"].max()).normalize()
    if run_as_of is None:
        return {
            "should_execute": True,
            "no_op_reason": None,
            "snapshot_as_of": snapshot_as_of,
            "execution_window": (),
        }

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


def _noop_logger(_message) -> None:
    return None


def load_runtime_parameters(
    *,
    config_path: str | Path | None = None,
    logger=None,
) -> dict[str, object]:
    if logger is None:
        logger = _noop_logger

    runtime_params = {
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "safe_haven": SAFE_HAVEN,
        "holdings_count": DEFAULT_HOLDINGS_COUNT,
        "single_name_cap": DEFAULT_SINGLE_NAME_CAP,
        "sector_cap": DEFAULT_SECTOR_CAP,
        "hold_bonus": DEFAULT_HOLD_BONUS,
        "risk_on_exposure": DEFAULT_RISK_ON_EXPOSURE,
        "soft_defense_exposure": DEFAULT_SOFT_DEFENSE_EXPOSURE,
        "hard_defense_exposure": DEFAULT_HARD_DEFENSE_EXPOSURE,
        "soft_breadth_threshold": DEFAULT_SOFT_BREADTH_THRESHOLD,
        "hard_breadth_threshold": DEFAULT_HARD_BREADTH_THRESHOLD,
        "min_adv20_usd": DEFAULT_MIN_ADV20_USD,
        "sector_whitelist": DEFAULT_SECTOR_WHITELIST,
        "normalization": DEFAULT_NORMALIZATION,
        "score_template": DEFAULT_SCORE_TEMPLATE,
        "runtime_execution_window_trading_days": DEFAULT_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS,
        "execution_cash_reserve_ratio": DEFAULT_EXECUTION_CASH_RESERVE_RATIO,
        "runtime_config_name": PROFILE_NAME,
        "runtime_config_path": None,
        "runtime_config_source": "module_defaults",
        "residual_proxy": "simple_excess_return_vs_QQQ",
    }
    if config_path is None:
        logger(f"[{PROFILE_NAME}] runtime config source=module_defaults")
        return runtime_params

    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Runtime strategy config not found: {config_file}")
    payload = json.loads(config_file.read_text(encoding="utf-8"))
    if str(payload.get("name")).strip() != PROFILE_NAME:
        raise ValueError(f"Runtime config name must be {PROFILE_NAME!r}")
    if str(payload.get("family")).strip() != "tech_heavy_pullback":
        raise ValueError("Runtime config family must be 'tech_heavy_pullback'")
    if str(payload.get("branch_role")).strip() != BRANCH_ROLE:
        raise ValueError(f"Runtime config branch_role must be {BRANCH_ROLE!r}")

    exposures = payload.get("exposures") or {}
    breadth_thresholds = payload.get("breadth_thresholds") or {}
    runtime_params.update(
        {
            "benchmark_symbol": str(payload.get("benchmark_symbol") or BENCHMARK_SYMBOL).upper(),
            "holdings_count": int(payload.get("holdings_count", DEFAULT_HOLDINGS_COUNT)),
            "single_name_cap": float(payload.get("single_name_cap", DEFAULT_SINGLE_NAME_CAP)),
            "sector_cap": float(payload.get("sector_cap", DEFAULT_SECTOR_CAP)),
            "hold_bonus": float(payload.get("hold_bonus", DEFAULT_HOLD_BONUS)),
            "risk_on_exposure": float(exposures.get("risk_on", DEFAULT_RISK_ON_EXPOSURE)),
            "soft_defense_exposure": float(exposures.get("soft_defense", DEFAULT_SOFT_DEFENSE_EXPOSURE)),
            "hard_defense_exposure": float(exposures.get("hard_defense", DEFAULT_HARD_DEFENSE_EXPOSURE)),
            "soft_breadth_threshold": float(breadth_thresholds.get("soft", DEFAULT_SOFT_BREADTH_THRESHOLD)),
            "hard_breadth_threshold": float(breadth_thresholds.get("hard", DEFAULT_HARD_BREADTH_THRESHOLD)),
            "min_adv20_usd": float(payload.get("min_adv20_usd", DEFAULT_MIN_ADV20_USD)),
            "sector_whitelist": tuple(payload.get("sector_whitelist") or DEFAULT_SECTOR_WHITELIST),
            "normalization": str(payload.get("normalization") or DEFAULT_NORMALIZATION),
            "score_template": str(payload.get("score_template") or DEFAULT_SCORE_TEMPLATE),
            "execution_cash_reserve_ratio": float(
                payload.get("execution_cash_reserve_ratio", DEFAULT_EXECUTION_CASH_RESERVE_RATIO)
            ),
            "runtime_config_name": str(payload.get("name") or PROFILE_NAME),
            "runtime_config_path": str(config_file),
            "runtime_config_source": "external_config",
            "residual_proxy": str(payload.get("residual_proxy") or "simple_excess_return_vs_QQQ"),
        }
    )
    logger(f"[{PROFILE_NAME}] runtime config source=external_config path={config_file}")
    return runtime_params


def compute_signals(
    feature_snapshot,
    current_holdings,
    *,
    run_as_of=None,
    runtime_execution_window_trading_days: int = DEFAULT_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS,
    **kwargs,
):
    managed_symbols = extract_managed_symbols(
        feature_snapshot,
        benchmark_symbol=kwargs.get("benchmark_symbol", BENCHMARK_SYMBOL),
        safe_haven=kwargs.get("safe_haven", SAFE_HAVEN),
    )
    execution_window = evaluate_execution_window(
        feature_snapshot,
        run_as_of=run_as_of,
        runtime_execution_window_trading_days=runtime_execution_window_trading_days,
    )
    if not execution_window["should_execute"]:
        status_desc = (
            f"no-op | reason={execution_window['no_op_reason']} | "
            f"snapshot_as_of={execution_window['snapshot_as_of']}"
        )
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
            },
        )

    weights, signal_desc, metadata = build_target_weights(
        feature_snapshot,
        current_holdings,
        **kwargs,
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
