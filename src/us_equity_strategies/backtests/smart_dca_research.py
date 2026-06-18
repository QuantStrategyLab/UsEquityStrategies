from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
import hashlib
import json
import math
from os import PathLike
from pathlib import Path
from typing import Any

import pandas as pd


BITCOIN_GENESIS_DATE = pd.Timestamp("2009-01-03")
SMART_DCA_RESEARCH_ARTIFACT_SCHEMA_VERSION = "smart_dca_research_artifacts.v1"
SUPPORTED_DCA_CADENCES = frozenset({"weekly", "monthly", "quarterly"})


@dataclass(frozen=True)
class SmartDcaCandidate:
    name: str
    family: str
    rule_type: str
    signal_symbols: tuple[str, ...]
    min_history: int
    parameters: Mapping[str, float]


@dataclass(frozen=True)
class DcaResearchResult:
    name: str
    terminal_value: float
    cash: float
    shares: float
    invested: float
    contributions: float
    max_drawdown: float
    max_underwater_days: int
    money_weighted_return: float
    trade_count: int
    skipped_count: int
    deployment_rate: float
    relative_terminal_value_pct: float
    equity_curve: tuple[dict[str, object], ...]
    cash_flows: tuple[dict[str, object], ...]
    trades: tuple[dict[str, object], ...]
    skips: tuple[dict[str, object], ...]
    last_signal_metrics: Mapping[str, object]


@dataclass(frozen=True)
class DcaCandidateEvaluation:
    name: str
    passed: bool
    reasons: tuple[str, ...]
    relative_terminal_value_pct: float
    max_drawdown_delta_pct_points: float
    skipped_buy_ratio: float
    deployment_rate_delta_pct_points: float
    rank_score: float


IBIT_AHR999_MAYER_PARAMETERS: dict[str, float] = {
    "ahr999_bottom_threshold": 0.45,
    "ahr999_accumulation_threshold": 0.80,
    "ahr999_dca_threshold": 1.20,
    "mayer_deep_discount_threshold": 0.65,
    "mayer_discount_threshold": 0.80,
    "mayer_expensive_threshold": 2.40,
    "base_multiplier": 1.0,
    "ahr999_bottom_multiplier": 3.0,
    "ahr999_accumulation_multiplier": 2.25,
    "ahr999_dca_multiplier": 1.50,
    "ahr999_expensive_multiplier": 0.0,
}
IBIT_AHR999_MAYER_NO_SKIP_PARAMETERS: dict[str, float] = {
    **IBIT_AHR999_MAYER_PARAMETERS,
    "ahr999_expensive_multiplier": 1.0,
}
NASDAQ_SP500_PRICE_PARAMETERS: dict[str, float] = {
    "mild_drawdown_threshold": 0.08,
    "deep_drawdown_threshold": 0.15,
    "severe_drawdown_threshold": 0.25,
    "mild_discount_gap": 0.05,
    "deep_discount_gap": 0.10,
    "expensive_gap": 0.12,
    "very_expensive_gap": 0.20,
    "shallow_drawdown_threshold": 0.03,
    "overbought_rsi": 70.0,
    "base_multiplier": 1.0,
    "mild_pullback_multiplier": 1.10,
    "deep_pullback_multiplier": 1.25,
    "severe_pullback_multiplier": 1.50,
    "expensive_multiplier": 0.75,
    "very_expensive_multiplier": 0.0,
}
NASDAQ_SP500_PRICE_NO_SKIP_PARAMETERS: dict[str, float] = {
    **NASDAQ_SP500_PRICE_PARAMETERS,
    "expensive_multiplier": 1.0,
    "very_expensive_multiplier": 1.0,
}


PRESET_CANDIDATES: dict[str, SmartDcaCandidate] = {
    "nasdaq_sp500_price_defensive": SmartDcaCandidate(
        name="nasdaq_sp500_price_defensive",
        family="nasdaq_sp500_price",
        rule_type="trend_drawdown",
        signal_symbols=("QQQ", "SPY"),
        min_history=252,
        parameters=NASDAQ_SP500_PRICE_PARAMETERS,
    ),
    "nasdaq_sp500_price_no_skip": SmartDcaCandidate(
        name="nasdaq_sp500_price_no_skip",
        family="nasdaq_sp500_price_variant",
        rule_type="trend_drawdown",
        signal_symbols=("QQQ", "SPY"),
        min_history=252,
        parameters=NASDAQ_SP500_PRICE_NO_SKIP_PARAMETERS,
    ),
    "ibit_btc_ahr999_mayer_cycle": SmartDcaCandidate(
        name="ibit_btc_ahr999_mayer_cycle",
        family="ibit_btc_ahr999_mayer_price",
        rule_type="ahr999_mayer",
        signal_symbols=("BTC-USD",),
        min_history=200,
        parameters=IBIT_AHR999_MAYER_PARAMETERS,
    ),
    "ibit_btc_ahr999_mayer_no_skip_cycle": SmartDcaCandidate(
        name="ibit_btc_ahr999_mayer_no_skip_cycle",
        family="ibit_btc_ahr999_mayer_price_variant",
        rule_type="ahr999_mayer",
        signal_symbols=("BTC-USD",),
        min_history=200,
        parameters=IBIT_AHR999_MAYER_NO_SKIP_PARAMETERS,
    ),
    "ibit_btc_ahr999_sma_mayer_cycle": SmartDcaCandidate(
        name="ibit_btc_ahr999_sma_mayer_cycle",
        family="ibit_btc_ahr999_mayer_price_variant",
        rule_type="ahr999_sma_mayer",
        signal_symbols=("BTC-USD",),
        min_history=200,
        parameters=IBIT_AHR999_MAYER_PARAMETERS,
    ),
    "ibit_btc_precomputed_ahr999_mayer_cycle": SmartDcaCandidate(
        name="ibit_btc_precomputed_ahr999_mayer_cycle",
        family="ibit_btc_ahr999_mayer_precomputed",
        rule_type="precomputed_ahr999_mayer",
        signal_symbols=("ahr999", "mayer_multiple"),
        min_history=1,
        parameters=IBIT_AHR999_MAYER_PARAMETERS,
    ),
    "ibit_btc_precomputed_ahr999_mayer_no_skip_cycle": SmartDcaCandidate(
        name="ibit_btc_precomputed_ahr999_mayer_no_skip_cycle",
        family="ibit_btc_ahr999_mayer_precomputed_variant",
        rule_type="precomputed_ahr999_mayer",
        signal_symbols=("ahr999", "mayer_multiple"),
        min_history=1,
        parameters=IBIT_AHR999_MAYER_NO_SKIP_PARAMETERS,
    ),
    "ibit_btc_precomputed_ahr999_sma_mayer_cycle": SmartDcaCandidate(
        name="ibit_btc_precomputed_ahr999_sma_mayer_cycle",
        family="ibit_btc_ahr999_mayer_precomputed_variant",
        rule_type="precomputed_ahr999_sma_mayer",
        signal_symbols=("ahr999_sma", "mayer_multiple"),
        min_history=1,
        parameters=IBIT_AHR999_MAYER_PARAMETERS,
    ),
}

CANDIDATE_SETS: dict[str, tuple[str, ...]] = {
    "nasdaq_sp500_price": ("nasdaq_sp500_price_defensive",),
    "nasdaq_sp500_price_variants": (
        "nasdaq_sp500_price_defensive",
        "nasdaq_sp500_price_no_skip",
    ),
    "ibit_btc_ahr999_mayer_price": ("ibit_btc_ahr999_mayer_cycle",),
    "ibit_btc_ahr999_mayer_price_variants": (
        "ibit_btc_ahr999_mayer_cycle",
        "ibit_btc_ahr999_mayer_no_skip_cycle",
        "ibit_btc_ahr999_sma_mayer_cycle",
    ),
    "ibit_btc_ahr999_mayer_precomputed": ("ibit_btc_precomputed_ahr999_mayer_cycle",),
    "ibit_btc_ahr999_mayer_precomputed_variants": (
        "ibit_btc_precomputed_ahr999_mayer_cycle",
        "ibit_btc_precomputed_ahr999_mayer_no_skip_cycle",
        "ibit_btc_precomputed_ahr999_sma_mayer_cycle",
    ),
    "all": tuple(PRESET_CANDIDATES),
}


def available_candidate_names() -> tuple[str, ...]:
    """Return the small fixed preset universe used by this research helper."""

    return tuple(PRESET_CANDIDATES)


def candidate_specs_to_rows(candidate_names: Iterable[str]) -> tuple[dict[str, object], ...]:
    """Return CSV-friendly rows describing frozen preset candidate parameters."""

    names = tuple(candidate_names)
    unknown = [name for name in names if name not in PRESET_CANDIDATES]
    if unknown:
        raise ValueError(f"unknown smart DCA candidates: {unknown}")

    rows: list[dict[str, object]] = []
    for name in names:
        candidate = PRESET_CANDIDATES[name]
        for parameter_name, parameter_value in sorted(candidate.parameters.items()):
            rows.append(
                {
                    "name": candidate.name,
                    "family": candidate.family,
                    "rule_type": candidate.rule_type,
                    "signal_symbols": ",".join(candidate.signal_symbols),
                    "min_history": candidate.min_history,
                    "parameter_name": parameter_name,
                    "parameter_value": parameter_value,
                }
            )
    return tuple(rows)


def candidate_summaries_to_rows(candidate_names: Iterable[str]) -> tuple[dict[str, object], ...]:
    """Return candidate-level anti-overfit audit rows for frozen presets."""

    names = tuple(candidate_names)
    unknown = [name for name in names if name not in PRESET_CANDIDATES]
    if unknown:
        raise ValueError(f"unknown smart DCA candidates: {unknown}")

    rows: list[dict[str, object]] = []
    for name in names:
        candidate = PRESET_CANDIDATES[name]
        multiplier_values = _candidate_multiplier_values(candidate)
        rows.append(
            {
                "name": candidate.name,
                "family": candidate.family,
                "rule_type": candidate.rule_type,
                "signal_symbols": ",".join(candidate.signal_symbols),
                "signal_symbol_count": len(candidate.signal_symbols),
                "min_history": candidate.min_history,
                "parameter_count": len(candidate.parameters),
                "threshold_parameter_count": sum(
                    1
                    for parameter_name in candidate.parameters
                    if not parameter_name.endswith("_multiplier")
                ),
                "multiplier_parameter_count": len(multiplier_values),
                "unique_multiplier_count": len(set(multiplier_values)),
                "min_multiplier": min(multiplier_values) if multiplier_values else float("nan"),
                "max_multiplier": max(multiplier_values) if multiplier_values else float("nan"),
                "zero_multiplier_allowed": any(value <= 0.0 for value in multiplier_values),
                "open_parameter_search": False,
                "candidate_definition_sha256": _candidate_definition_sha256(candidate.name),
            }
        )
    return tuple(rows)


def _candidate_multiplier_values(candidate: SmartDcaCandidate) -> tuple[float, ...]:
    return tuple(
        float(value)
        for parameter_name, value in sorted(candidate.parameters.items())
        if parameter_name.endswith("_multiplier")
    )


def _normalize_symbol(symbol: object) -> str:
    return str(symbol or "").strip().upper().removesuffix(".US")


def _close_series(values: Any) -> pd.Series:
    if isinstance(values, pd.Series):
        series = values.copy()
    elif isinstance(values, pd.DataFrame):
        if values.empty:
            return pd.Series(dtype=float)
        series = values["close"] if "close" in values.columns else values.iloc[:, 0]
    else:
        series = pd.Series(values)
    series = pd.to_numeric(series, errors="coerce").dropna().astype(float)
    series = series[series > 0.0]
    if series.empty:
        return pd.Series(dtype=float)
    series.index = pd.to_datetime(series.index).tz_localize(None).normalize()
    return series.sort_index()


def _price_frame(values: Any) -> pd.DataFrame:
    if isinstance(values, Mapping):
        columns = {
            _normalize_symbol(symbol): _close_series(series)
            for symbol, series in values.items()
        }
        frame = pd.DataFrame(columns)
    elif isinstance(values, pd.DataFrame):
        if "close" in values.columns and len(values.columns) == 1:
            frame = pd.DataFrame({"SIGNAL": _close_series(values["close"])})
        else:
            columns = {
                _normalize_symbol(column): _close_series(values[column])
                for column in values.columns
            }
            frame = pd.DataFrame(columns)
    else:
        frame = pd.DataFrame({"SIGNAL": _close_series(values)})
    return frame.dropna(how="all").sort_index()


def _resolve_candidate_names(candidate_set: str | Iterable[str]) -> tuple[str, ...]:
    if isinstance(candidate_set, str):
        if candidate_set in CANDIDATE_SETS:
            return CANDIDATE_SETS[candidate_set]
        if candidate_set in PRESET_CANDIDATES:
            return (candidate_set,)
        raise ValueError(f"unknown smart DCA candidate_set: {candidate_set}")

    names = tuple(candidate_set)
    unknown = [name for name in names if name not in PRESET_CANDIDATES]
    if unknown:
        raise ValueError(f"unknown smart DCA candidates: {unknown}")
    return names


def _candidate_signal_frame(signals: pd.DataFrame, candidate: SmartDcaCandidate) -> pd.DataFrame:
    normalized_columns = {_normalize_symbol(column): column for column in signals.columns}
    resolved: dict[str, pd.Series] = {}
    for symbol in candidate.signal_symbols:
        normalized = _normalize_symbol(symbol)
        column = normalized_columns.get(normalized)
        if column is not None:
            resolved[normalized] = signals[column]

    if len(resolved) == len(candidate.signal_symbols):
        return pd.DataFrame(resolved).dropna(how="any")
    if len(candidate.signal_symbols) == 1 and len(signals.columns) == 1:
        symbol = _normalize_symbol(candidate.signal_symbols[0])
        return pd.DataFrame({symbol: signals.iloc[:, 0]}).dropna(how="any")

    available = ", ".join(str(column) for column in signals.columns)
    required = ", ".join(candidate.signal_symbols)
    raise ValueError(f"{candidate.name} requires signal columns {required}; available: {available}")


def _rsi(series: pd.Series, window: int = 14) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if len(values) <= window:
        return float("nan")
    delta = values.diff().dropna()
    gains = delta.clip(lower=0.0)
    losses = -delta.clip(upper=0.0)
    avg_gain = float(gains.iloc[-window:].mean())
    avg_loss = float(losses.iloc[-window:].mean())
    if avg_loss <= 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    return float(100.0 - 100.0 / (1.0 + avg_gain / avg_loss))


def _trend_drawdown_metrics(signal_history: pd.DataFrame) -> dict[str, float]:
    drawdowns: list[float] = []
    gaps: list[float] = []
    rsi_values: list[float] = []

    for column in signal_history.columns:
        series = pd.to_numeric(signal_history[column], errors="coerce").dropna()
        latest = float(series.iloc[-1])
        sma200 = float(series.iloc[-200:].mean())
        high252 = float(series.iloc[-252:].max())
        drawdowns.append(0.0 if high252 <= 0.0 else max(0.0, 1.0 - latest / high252))
        gaps.append(0.0 if sma200 <= 0.0 else latest / sma200 - 1.0)
        rsi_value = _rsi(series)
        if not pd.isna(rsi_value):
            rsi_values.append(float(rsi_value))

    return {
        "avg_drawdown_252d": float(sum(drawdowns) / len(drawdowns)),
        "avg_sma200_gap": float(sum(gaps) / len(gaps)),
        "avg_rsi14": float(sum(rsi_values) / len(rsi_values)) if rsi_values else float("nan"),
    }


def _trend_drawdown_multiplier(
    signal_history: pd.DataFrame,
    parameters: Mapping[str, float],
) -> tuple[float, str, dict[str, object]]:
    metrics = _trend_drawdown_metrics(signal_history)
    avg_drawdown = float(metrics["avg_drawdown_252d"])
    avg_gap = float(metrics["avg_sma200_gap"])
    avg_rsi = float(metrics["avg_rsi14"])
    all_overbought = not pd.isna(avg_rsi) and avg_rsi >= parameters["overbought_rsi"]

    if avg_drawdown >= parameters["severe_drawdown_threshold"]:
        regime = "severe_pullback"
        multiplier = parameters["severe_pullback_multiplier"]
    elif avg_drawdown >= parameters["deep_drawdown_threshold"] or avg_gap <= -abs(parameters["deep_discount_gap"]):
        regime = "deep_pullback"
        multiplier = parameters["deep_pullback_multiplier"]
    elif avg_drawdown >= parameters["mild_drawdown_threshold"] or avg_gap <= -abs(parameters["mild_discount_gap"]):
        regime = "mild_pullback"
        multiplier = parameters["mild_pullback_multiplier"]
    elif (
        avg_gap >= parameters["very_expensive_gap"]
        and avg_drawdown <= parameters["shallow_drawdown_threshold"]
        and all_overbought
    ):
        regime = "very_expensive_overbought"
        multiplier = parameters["very_expensive_multiplier"]
    elif avg_gap >= parameters["expensive_gap"] and avg_drawdown <= parameters["shallow_drawdown_threshold"]:
        regime = "expensive"
        multiplier = parameters["expensive_multiplier"]
    else:
        regime = "normal"
        multiplier = parameters["base_multiplier"]

    return float(multiplier), regime, metrics


def _bitcoin_age_estimate_price(as_of: object) -> float:
    timestamp = pd.Timestamp(as_of)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("UTC").tz_localize(None)
    age_days = max(1, int((timestamp.normalize() - BITCOIN_GENESIS_DATE).days))
    return float(10 ** (5.84 * math.log10(age_days) - 17.01))


def _ahr999_mayer_metrics(signal_history: pd.DataFrame, as_of: pd.Timestamp) -> dict[str, float | str]:
    series = pd.to_numeric(signal_history.iloc[:, 0], errors="coerce").dropna()
    latest = float(series.iloc[-1])
    sma200 = float(series.iloc[-200:].mean())
    gma200 = float(math.exp(sum(math.log(float(value)) for value in series.iloc[-200:]) / 200.0))
    estimate_price = _bitcoin_age_estimate_price(as_of)
    mayer = float(latest / sma200) if sma200 > 0.0 else float("nan")
    ahr999 = float((latest / gma200) * (latest / estimate_price)) if gma200 > 0.0 else float("nan")
    ahr999_sma = float((latest / sma200) * (latest / estimate_price)) if sma200 > 0.0 else float("nan")
    return {
        "ahr999": ahr999,
        "ahr999_sma": ahr999_sma,
        "mayer_multiple": mayer,
        "ahr999_estimate_price": estimate_price,
        "cycle_indicator_source": "price_derived",
    }


def _ahr999_mayer_multiplier(
    signal_history: pd.DataFrame,
    parameters: Mapping[str, float],
    *,
    as_of: pd.Timestamp,
    ahr999_metric: str = "ahr999",
) -> tuple[float, str, dict[str, object]]:
    metrics = _ahr999_mayer_metrics(signal_history, as_of)
    ahr999 = float(metrics[ahr999_metric])
    mayer = float(metrics["mayer_multiple"])
    metrics = {
        **metrics,
        "ahr999_metric": ahr999_metric,
        "ahr999_selected": ahr999,
    }

    if ahr999 <= parameters["ahr999_bottom_threshold"] or mayer <= parameters["mayer_deep_discount_threshold"]:
        regime = "ahr999_bottom"
        multiplier = parameters["ahr999_bottom_multiplier"]
    elif ahr999 <= parameters["ahr999_accumulation_threshold"] or mayer <= parameters["mayer_discount_threshold"]:
        regime = "ahr999_accumulation"
        multiplier = parameters["ahr999_accumulation_multiplier"]
    elif ahr999 <= parameters["ahr999_dca_threshold"]:
        regime = "ahr999_dca"
        multiplier = parameters["ahr999_dca_multiplier"]
    elif mayer >= parameters["mayer_expensive_threshold"] or ahr999 > parameters["ahr999_dca_threshold"]:
        regime = "ahr999_expensive"
        multiplier = parameters["ahr999_expensive_multiplier"]
    else:
        regime = "normal"
        multiplier = parameters["base_multiplier"]

    return float(multiplier), regime, metrics


def _precomputed_ahr999_mayer_metrics(
    signal_history: pd.DataFrame,
    *,
    ahr999_column: str = "ahr999",
) -> dict[str, float | str]:
    latest = signal_history.iloc[-1]
    normalized_column = _normalize_symbol(ahr999_column)
    ahr999_value = float(latest[normalized_column])
    return {
        "ahr999": ahr999_value,
        "ahr999_selected": ahr999_value,
        "ahr999_metric": normalized_column.lower(),
        "mayer_multiple": float(latest[_normalize_symbol("mayer_multiple")]),
        "cycle_indicator_source": "precomputed_derived_indicators",
    }


def _precomputed_ahr999_mayer_multiplier(
    signal_history: pd.DataFrame,
    parameters: Mapping[str, float],
    *,
    ahr999_column: str = "ahr999",
) -> tuple[float, str, dict[str, object]]:
    metrics = _precomputed_ahr999_mayer_metrics(
        signal_history,
        ahr999_column=ahr999_column,
    )
    ahr999 = float(metrics["ahr999"])
    mayer = float(metrics["mayer_multiple"])

    if ahr999 <= parameters["ahr999_bottom_threshold"] or mayer <= parameters["mayer_deep_discount_threshold"]:
        regime = "ahr999_bottom"
        multiplier = parameters["ahr999_bottom_multiplier"]
    elif ahr999 <= parameters["ahr999_accumulation_threshold"] or mayer <= parameters["mayer_discount_threshold"]:
        regime = "ahr999_accumulation"
        multiplier = parameters["ahr999_accumulation_multiplier"]
    elif ahr999 <= parameters["ahr999_dca_threshold"]:
        regime = "ahr999_dca"
        multiplier = parameters["ahr999_dca_multiplier"]
    elif mayer >= parameters["mayer_expensive_threshold"] or ahr999 > parameters["ahr999_dca_threshold"]:
        regime = "ahr999_expensive"
        multiplier = parameters["ahr999_expensive_multiplier"]
    else:
        regime = "normal"
        multiplier = parameters["base_multiplier"]

    return float(multiplier), regime, metrics


def _candidate_multiplier(
    candidate: SmartDcaCandidate,
    signal_history: pd.DataFrame,
    *,
    as_of: pd.Timestamp,
) -> tuple[float, str, dict[str, object]]:
    if len(signal_history) < candidate.min_history:
        return 0.0, "insufficient_history", {"required_history": candidate.min_history}
    if candidate.rule_type == "trend_drawdown":
        return _trend_drawdown_multiplier(signal_history, candidate.parameters)
    if candidate.rule_type == "ahr999_mayer":
        return _ahr999_mayer_multiplier(signal_history, candidate.parameters, as_of=as_of)
    if candidate.rule_type == "ahr999_sma_mayer":
        return _ahr999_mayer_multiplier(
            signal_history,
            candidate.parameters,
            as_of=as_of,
            ahr999_metric="ahr999_sma",
        )
    if candidate.rule_type == "precomputed_ahr999_mayer":
        return _precomputed_ahr999_mayer_multiplier(signal_history, candidate.parameters)
    if candidate.rule_type == "precomputed_ahr999_sma_mayer":
        return _precomputed_ahr999_mayer_multiplier(
            signal_history,
            candidate.parameters,
            ahr999_column="ahr999_sma",
        )
    raise ValueError(f"unsupported smart DCA rule_type: {candidate.rule_type}")


def _max_drawdown(values: list[float]) -> float:
    peak = 0.0
    max_dd = 0.0
    for value in values:
        peak = max(peak, float(value))
        if peak > 0.0:
            max_dd = max(max_dd, 1.0 - float(value) / peak)
    return float(max_dd)


def _max_underwater_days(equity_curve: Iterable[Mapping[str, object]]) -> int:
    peak = 0.0
    peak_date: pd.Timestamp | None = None
    max_days = 0
    for row in equity_curve:
        date = pd.Timestamp(row["date"]).normalize()
        value = float(row["equity"])
        if value >= peak:
            peak = value
            peak_date = date
        elif peak_date is not None:
            max_days = max(max_days, int((date - peak_date).days))
    return max_days


def _xirr(cash_flows: Iterable[Mapping[str, object]]) -> float:
    rows = tuple(cash_flows)
    if len(rows) < 2:
        return float("nan")
    first_date = pd.Timestamp(rows[0]["date"]).normalize()
    dated_values = tuple(
        (
            max(0.0, (pd.Timestamp(row["date"]).normalize() - first_date).days / 365.25),
            float(row["amount"]),
        )
        for row in rows
    )
    if not any(amount < 0.0 for _, amount in dated_values) or not any(
        amount > 0.0 for _, amount in dated_values
    ):
        return float("nan")

    def npv(rate: float) -> float:
        return sum(amount / ((1.0 + rate) ** years) for years, amount in dated_values)

    low = -0.9999
    high = 10.0
    low_value = npv(low)
    high_value = npv(high)
    while low_value * high_value > 0.0 and high < 1_000_000.0:
        high *= 10.0
        high_value = npv(high)
    if low_value * high_value > 0.0:
        return float("nan")

    for _ in range(100):
        midpoint = (low + high) / 2.0
        midpoint_value = npv(midpoint)
        if abs(midpoint_value) < 1e-7:
            return float(midpoint)
        if low_value * midpoint_value <= 0.0:
            high = midpoint
            high_value = midpoint_value
        else:
            low = midpoint
            low_value = midpoint_value
    return float((low + high) / 2.0)


def _monthly_execution_dates(dates: pd.Index, monthly_execution_day: int) -> frozenset[pd.Timestamp]:
    day = int(max(1, min(31, monthly_execution_day)))
    monthly_dates: dict[str, list[pd.Timestamp]] = {}
    for raw_date in dates:
        date = pd.Timestamp(raw_date).normalize()
        monthly_dates.setdefault(date.strftime("%Y-%m"), []).append(date)

    selected: set[pd.Timestamp] = set()
    for values in monthly_dates.values():
        ordered = sorted(values)
        eligible = [date for date in ordered if date.day >= day]
        selected.add(eligible[0] if eligible else ordered[-1])
    return frozenset(selected)


def _period_key(date: pd.Timestamp, cadence: str) -> str:
    if cadence == "weekly":
        year, week, _ = date.isocalendar()
        return f"{year}-W{week:02d}"
    if cadence == "monthly":
        return date.strftime("%Y-%m")
    if cadence == "quarterly":
        quarter = (date.month - 1) // 3 + 1
        return f"{date.year}-Q{quarter}"
    raise ValueError(f"unsupported DCA cadence: {cadence}")


def _period_start_dates(dates: pd.Index, cadence: str) -> frozenset[pd.Timestamp]:
    period_dates: dict[str, list[pd.Timestamp]] = {}
    for raw_date in dates:
        date = pd.Timestamp(raw_date).normalize()
        period_dates.setdefault(_period_key(date, cadence), []).append(date)
    return frozenset(min(values) for values in period_dates.values())


def _scheduled_execution_dates(
    dates: pd.Index,
    *,
    cadence: str,
    monthly_execution_day: int,
) -> frozenset[pd.Timestamp]:
    normalized_cadence = _normalize_cadence(cadence)
    if normalized_cadence == "weekly":
        return _period_start_dates(dates, normalized_cadence)
    if normalized_cadence == "monthly":
        return _monthly_execution_dates(dates, monthly_execution_day)

    day = int(max(1, min(31, monthly_execution_day)))
    period_dates: dict[str, list[pd.Timestamp]] = {}
    for raw_date in dates:
        date = pd.Timestamp(raw_date).normalize()
        period_dates.setdefault(_period_key(date, normalized_cadence), []).append(date)

    selected: set[pd.Timestamp] = set()
    for values in period_dates.values():
        ordered = sorted(values)
        eligible = [date for date in ordered if date.day >= day]
        selected.add(eligible[0] if eligible else ordered[-1])
    return frozenset(selected)


def _cadence_contribution_amount(monthly_contribution_usd: float, cadence: str) -> float:
    normalized_cadence = _normalize_cadence(cadence)
    if normalized_cadence == "weekly":
        return float(monthly_contribution_usd) * 12.0 / 52.0
    if normalized_cadence == "monthly":
        return float(monthly_contribution_usd)
    if normalized_cadence == "quarterly":
        return float(monthly_contribution_usd) * 3.0
    raise ValueError(f"unsupported DCA cadence: {cadence}")


def _normalize_cadence(cadence: str) -> str:
    normalized = str(cadence).strip().lower()
    if normalized not in SUPPORTED_DCA_CADENCES:
        raise ValueError(f"unsupported DCA cadence: {cadence}")
    return normalized


def _run_path(
    *,
    name: str,
    trade_prices: pd.Series,
    signal_prices: pd.DataFrame | None,
    contribution_amount_usd: float,
    candidate: SmartDcaCandidate | None,
    min_investment_usd: float,
    contribution_dates: frozenset[pd.Timestamp],
    execution_dates: frozenset[pd.Timestamp],
) -> DcaResearchResult:
    cash = 0.0
    shares = 0.0
    invested = 0.0
    contributions = 0.0
    equity_curve: list[float] = []
    equity_rows: list[dict[str, object]] = []
    cash_flows: list[dict[str, object]] = []
    trades: list[dict[str, object]] = []
    skips: list[dict[str, object]] = []
    last_metrics: dict[str, object] = {}

    for raw_date, raw_price in trade_prices.items():
        date = pd.Timestamp(raw_date).normalize()
        price = float(raw_price)
        is_contribution_day = date in contribution_dates

        if is_contribution_day:
            cash += float(contribution_amount_usd)
            contributions += float(contribution_amount_usd)
            cash_flows.append(
                {
                    "date": date.date().isoformat(),
                    "name": name,
                    "cash_flow_type": "contribution",
                    "amount": -float(contribution_amount_usd),
                }
            )

        if date in execution_dates:
            if candidate is None:
                multiplier = 1.0
                regime = "fixed_dca"
                metrics: dict[str, object] = {}
            else:
                assert signal_prices is not None
                history = signal_prices.loc[signal_prices.index <= date]
                multiplier, regime, metrics = _candidate_multiplier(candidate, history, as_of=date)
            last_metrics = dict(metrics)

            requested_buy = float(contribution_amount_usd) * max(0.0, float(multiplier))
            if regime == "insufficient_history":
                skips.append(
                    {
                        "date": date.date().isoformat(),
                        "name": name,
                        "regime": regime,
                        "multiplier": float(multiplier),
                        "reason": "insufficient_history",
                    }
                )
            elif requested_buy <= 0.0:
                skips.append(
                    {
                        "date": date.date().isoformat(),
                        "name": name,
                        "regime": regime,
                        "multiplier": float(multiplier),
                        "reason": "valuation_too_expensive",
                        **metrics,
                    }
                )
            else:
                buy_value = min(cash, requested_buy)
                if buy_value < float(min_investment_usd):
                    skips.append(
                        {
                            "date": date.date().isoformat(),
                            "name": name,
                            "regime": regime,
                            "multiplier": float(multiplier),
                            "reason": "below_minimum",
                            **metrics,
                        }
                    )
                else:
                    bought_shares = buy_value / price
                    shares += bought_shares
                    cash -= buy_value
                    invested += buy_value
                    trades.append(
                        {
                            "date": date.date().isoformat(),
                            "name": name,
                            "regime": regime,
                            "multiplier": float(multiplier),
                            "buy_value": float(buy_value),
                            "requested_buy_value": float(requested_buy),
                            "cash_capped": bool(buy_value < requested_buy),
                            "price": price,
                            "shares": float(bought_shares),
                            **metrics,
                        }
                    )

        equity = cash + shares * price
        equity_curve.append(equity)
        running_peak = max(equity_curve)
        equity_rows.append(
            {
                "date": date.date().isoformat(),
                "name": name,
                "equity": float(equity),
                "cash": float(cash),
                "shares": float(shares),
                "price": price,
                "invested": float(invested),
                "contributions": float(contributions),
                "drawdown_pct": 0.0
                if running_peak <= 0.0
                else float((1.0 - equity / running_peak) * 100.0),
            }
        )

    final_price = float(trade_prices.iloc[-1]) if not trade_prices.empty else 0.0
    terminal_value = cash + shares * final_price
    if not trade_prices.empty:
        cash_flows.append(
            {
                "date": pd.Timestamp(trade_prices.index[-1]).date().isoformat(),
                "name": name,
                "cash_flow_type": "terminal_value",
                "amount": float(terminal_value),
            }
        )
    deployment_rate = invested / contributions if contributions > 0.0 else 0.0
    equity_curve_rows = tuple(equity_rows)
    cash_flow_rows = tuple(cash_flows)
    return DcaResearchResult(
        name=name,
        terminal_value=float(terminal_value),
        cash=float(cash),
        shares=float(shares),
        invested=float(invested),
        contributions=float(contributions),
        max_drawdown=_max_drawdown(equity_curve),
        max_underwater_days=_max_underwater_days(equity_curve_rows),
        money_weighted_return=_xirr(cash_flow_rows),
        trade_count=len(trades),
        skipped_count=len(skips),
        deployment_rate=float(deployment_rate),
        relative_terminal_value_pct=0.0,
        equity_curve=equity_curve_rows,
        cash_flows=cash_flow_rows,
        trades=tuple(trades),
        skips=tuple(skips),
        last_signal_metrics=last_metrics,
    )


def _with_relative_value(
    results: dict[str, DcaResearchResult],
    *,
    fixed_name: str,
) -> dict[str, DcaResearchResult]:
    fixed_terminal = results[fixed_name].terminal_value
    if fixed_terminal <= 0.0:
        return results
    return {
        name: replace(
            result,
            relative_terminal_value_pct=0.0
            if name == fixed_name
            else float((result.terminal_value / fixed_terminal - 1.0) * 100.0),
        )
        for name, result in results.items()
    }


def _skipped_buy_ratio(result: DcaResearchResult) -> float:
    scheduled = result.trade_count + result.skipped_count
    return float(result.skipped_count / scheduled) if scheduled > 0 else 0.0


def _equity_series(result: DcaResearchResult) -> pd.Series:
    if not result.equity_curve:
        return pd.Series(dtype=float)
    values = {
        pd.Timestamp(row["date"]).normalize(): float(row["equity"])
        for row in result.equity_curve
    }
    return pd.Series(values, dtype=float).sort_index()


def _worst_relative_value_gap_pct(
    result: DcaResearchResult,
    *,
    fixed: DcaResearchResult,
    min_elapsed_days: int,
) -> float:
    if result.name == fixed.name:
        return 0.0
    candidate_series = _equity_series(result)
    fixed_series = _equity_series(fixed)
    common_index = candidate_series.index.intersection(fixed_series.index).sort_values()
    if common_index.empty:
        return 0.0
    start = common_index[0]
    eligible_index = common_index[common_index >= start + pd.Timedelta(days=min_elapsed_days)]
    if eligible_index.empty:
        return 0.0
    candidate_values = candidate_series.loc[eligible_index]
    fixed_values = fixed_series.loc[eligible_index]
    valid = fixed_values > 0.0
    if not valid.any():
        return 0.0
    gaps = (candidate_values[valid] / fixed_values[valid] - 1.0) * 100.0
    return float(gaps.min())


def _cash_ratio_values(result: DcaResearchResult) -> tuple[float, ...]:
    values: list[float] = []
    for row in result.equity_curve:
        equity = float(row.get("equity", 0.0))
        if equity <= 0.0:
            continue
        values.append(float(row.get("cash", 0.0)) / equity)
    return tuple(values)


def _average_cash_ratio(result: DcaResearchResult) -> float:
    values = _cash_ratio_values(result)
    return float(sum(values) / len(values)) if values else 0.0


def _max_cash_ratio(result: DcaResearchResult) -> float:
    values = _cash_ratio_values(result)
    return float(max(values)) if values else 0.0


def _terminal_cash_ratio(result: DcaResearchResult) -> float:
    return float(result.cash / result.terminal_value) if result.terminal_value > 0.0 else 0.0


def evaluate_candidate_results(
    results: Mapping[str, DcaResearchResult],
    *,
    fixed_name: str = "fixed",
    max_terminal_underperformance_pct: float = -1.0,
    min_drawdown_improvement_pct_points: float = 2.0,
    max_drawdown_worse_pct_points: float = 1.0,
    min_terminal_edge_when_drawdown_worse_pct: float = 3.0,
    max_skipped_buy_ratio: float = 0.30,
) -> dict[str, DcaCandidateEvaluation]:
    """Apply the research promotion-gate rules to fixed-vs-candidate results.

    The thresholds mirror the research plan. They are deliberately coarse and
    named; this function evaluates already-run candidates and does not search
    for better parameters.
    """

    if fixed_name not in results:
        raise ValueError(f"results must include fixed_name={fixed_name!r}")
    fixed = results[fixed_name]
    evaluations: dict[str, DcaCandidateEvaluation] = {}

    for name, result in results.items():
        if name == fixed_name:
            continue
        relative_terminal = (
            float((result.terminal_value / fixed.terminal_value - 1.0) * 100.0)
            if fixed.terminal_value > 0.0
            else float(result.relative_terminal_value_pct)
        )
        drawdown_delta_pct_points = float((result.max_drawdown - fixed.max_drawdown) * 100.0)
        drawdown_improvement_pct_points = -drawdown_delta_pct_points
        skipped_ratio = _skipped_buy_ratio(result)
        deployment_delta_pct_points = float((result.deployment_rate - fixed.deployment_rate) * 100.0)
        reasons: list[str] = []

        if result.trade_count <= 0:
            reasons.append("no_candidate_trades")
        if (
            relative_terminal < max_terminal_underperformance_pct
            and drawdown_improvement_pct_points < min_drawdown_improvement_pct_points
        ):
            reasons.append("terminal_underperformance_without_drawdown_improvement")
        if (
            drawdown_delta_pct_points > max_drawdown_worse_pct_points
            and relative_terminal < min_terminal_edge_when_drawdown_worse_pct
        ):
            reasons.append("drawdown_worse_without_terminal_edge")
        if (
            skipped_ratio > max_skipped_buy_ratio
            and drawdown_improvement_pct_points < min_drawdown_improvement_pct_points
        ):
            reasons.append("skip_rate_too_high_without_drawdown_improvement")

        rank_score = float(
            relative_terminal
            + drawdown_improvement_pct_points * 0.50
            + deployment_delta_pct_points * 0.05
            - max(0.0, skipped_ratio - max_skipped_buy_ratio) * 10.0
        )
        evaluations[name] = DcaCandidateEvaluation(
            name=name,
            passed=not reasons,
            reasons=tuple(reasons),
            relative_terminal_value_pct=relative_terminal,
            max_drawdown_delta_pct_points=drawdown_delta_pct_points,
            skipped_buy_ratio=skipped_ratio,
            deployment_rate_delta_pct_points=deployment_delta_pct_points,
            rank_score=rank_score,
        )

    return evaluations


def summarize_candidate_evaluations(
    evaluations: Mapping[str, DcaCandidateEvaluation],
) -> tuple[DcaCandidateEvaluation, ...]:
    """Return candidate evaluations sorted for review reports."""

    return tuple(
        sorted(
            evaluations.values(),
            key=lambda item: (item.passed, item.rank_score, item.relative_terminal_value_pct),
            reverse=True,
        )
    )


def results_to_metrics_rows(
    results: Mapping[str, DcaResearchResult],
    *,
    fixed_name: str = "fixed",
    evaluations: Mapping[str, DcaCandidateEvaluation] | None = None,
) -> tuple[dict[str, object], ...]:
    """Convert result/evaluation objects into CSV-friendly metrics rows."""

    resolved_evaluations = (
        evaluate_candidate_results(results, fixed_name=fixed_name)
        if evaluations is None
        else evaluations
    )
    rows: list[dict[str, object]] = []
    fixed = results[fixed_name]
    for name, result in results.items():
        evaluation = resolved_evaluations.get(name)
        row: dict[str, object] = {
            "name": name,
            "is_fixed_benchmark": name == fixed_name,
            "terminal_value": result.terminal_value,
            "cash": result.cash,
            "shares": result.shares,
            "invested": result.invested,
            "contributions": result.contributions,
            "max_drawdown_pct": result.max_drawdown * 100.0,
            "max_underwater_days": result.max_underwater_days,
            "money_weighted_return_pct": result.money_weighted_return * 100.0,
            "average_cash_ratio_pct": _average_cash_ratio(result) * 100.0,
            "max_cash_ratio_pct": _max_cash_ratio(result) * 100.0,
            "terminal_cash_ratio_pct": _terminal_cash_ratio(result) * 100.0,
            "trade_count": result.trade_count,
            "skipped_count": result.skipped_count,
            "skipped_buy_ratio": _skipped_buy_ratio(result),
            "deployment_rate_pct": result.deployment_rate * 100.0,
            "worst_relative_value_gap_after_1y_pct": _worst_relative_value_gap_pct(
                result,
                fixed=fixed,
                min_elapsed_days=365,
            ),
            "worst_relative_value_gap_after_2y_pct": _worst_relative_value_gap_pct(
                result,
                fixed=fixed,
                min_elapsed_days=365 * 2,
            ),
            "worst_relative_value_gap_after_3y_pct": _worst_relative_value_gap_pct(
                result,
                fixed=fixed,
                min_elapsed_days=365 * 3,
            ),
            "relative_terminal_value_pct": (
                0.0
                if name == fixed_name
                else (
                    evaluation.relative_terminal_value_pct
                    if evaluation is not None
                    else result.relative_terminal_value_pct
                )
            ),
        }
        if evaluation is not None:
            row.update(
                {
                    "passed_promotion_gate": evaluation.passed,
                    "failure_reasons": ",".join(evaluation.reasons),
                    "max_drawdown_delta_pct_points": evaluation.max_drawdown_delta_pct_points,
                    "deployment_rate_delta_pct_points": evaluation.deployment_rate_delta_pct_points,
                    "rank_score": evaluation.rank_score,
                }
            )
        else:
            row.update(
                {
                    "passed_promotion_gate": None,
                    "failure_reasons": "",
                    "max_drawdown_delta_pct_points": 0.0,
                    "deployment_rate_delta_pct_points": 0.0,
                    "rank_score": 0.0,
                }
            )
        rows.append(row)
    return tuple(rows)


def results_to_equity_curve_rows(
    results: Mapping[str, DcaResearchResult],
) -> tuple[dict[str, object], ...]:
    """Convert daily account value curves into CSV-friendly rows."""

    rows: list[dict[str, object]] = []
    for result in results.values():
        rows.extend(dict(row) for row in result.equity_curve)
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                str(row.get("date", "")),
                str(row.get("name", "")),
            ),
        )
    )


def results_to_cash_flow_rows(
    results: Mapping[str, DcaResearchResult],
) -> tuple[dict[str, object], ...]:
    """Convert contribution and terminal-value cash flows into CSV-friendly rows."""

    rows: list[dict[str, object]] = []
    for result in results.values():
        rows.extend(dict(row) for row in result.cash_flows)
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                str(row.get("date", "")),
                str(row.get("name", "")),
                str(row.get("cash_flow_type", "")),
            ),
        )
    )


def scenario_results_to_robustness_rows(
    scenarios: Mapping[str, Mapping[str, DcaResearchResult]],
    *,
    fixed_name: str = "fixed",
) -> tuple[dict[str, object], ...]:
    """Aggregate candidate performance across scenario perturbations."""

    candidate_rows: dict[str, list[dict[str, object]]] = {}
    for scenario_name, results in scenarios.items():
        evaluations = evaluate_candidate_results(results, fixed_name=fixed_name)
        for row in results_to_metrics_rows(results, fixed_name=fixed_name, evaluations=evaluations):
            if row["name"] == fixed_name:
                continue
            enriched = dict(row)
            enriched["scenario"] = scenario_name
            candidate_rows.setdefault(str(row["name"]), []).append(enriched)

    rows: list[dict[str, object]] = []
    for name, values in candidate_rows.items():
        passed_values = [bool(row["passed_promotion_gate"]) for row in values]
        scenario_count = len(values)
        passed_count = sum(1 for value in passed_values if value)
        failure_reasons = sorted(
            {
                reason
                for row in values
                for reason in str(row.get("failure_reasons", "")).split(",")
                if reason
            }
        )
        robustness_gate_passed = scenario_count > 0 and passed_count == scenario_count
        rows.append(
            {
                "name": name,
                "scenario_count": scenario_count,
                "passed_count": passed_count,
                "pass_rate": float(passed_count / scenario_count) if scenario_count else 0.0,
                "robustness_gate_passed": robustness_gate_passed,
                "review_status": _robustness_review_status(
                    passed_count=passed_count,
                    scenario_count=scenario_count,
                ),
                "passed_all_scenarios": robustness_gate_passed,
                "failed_scenarios": scenario_count - passed_count,
                "failure_reasons": ",".join(failure_reasons),
                "weakest_scenario": _scenario_for_min_metric(values, "rank_score"),
                "worst_terminal_scenario": _scenario_for_min_metric(
                    values,
                    "relative_terminal_value_pct",
                ),
                "worst_drawdown_scenario": _scenario_for_max_metric(
                    values,
                    "max_drawdown_delta_pct_points",
                ),
                "min_relative_terminal_value_pct": _min_metric(
                    values,
                    "relative_terminal_value_pct",
                ),
                "median_relative_terminal_value_pct": _median_metric(
                    values,
                    "relative_terminal_value_pct",
                ),
                "min_money_weighted_return_pct": _min_metric(
                    values,
                    "money_weighted_return_pct",
                ),
                "median_money_weighted_return_pct": _median_metric(
                    values,
                    "money_weighted_return_pct",
                ),
                "max_average_cash_ratio_pct": _max_metric(
                    values,
                    "average_cash_ratio_pct",
                ),
                "max_cash_ratio_pct": _max_metric(values, "max_cash_ratio_pct"),
                "max_terminal_cash_ratio_pct": _max_metric(
                    values,
                    "terminal_cash_ratio_pct",
                ),
                "worst_max_drawdown_delta_pct_points": _max_metric(
                    values,
                    "max_drawdown_delta_pct_points",
                ),
                "max_skipped_buy_ratio": _max_metric(values, "skipped_buy_ratio"),
                "min_deployment_rate_delta_pct_points": _min_metric(
                    values,
                    "deployment_rate_delta_pct_points",
                ),
                "min_rank_score": _min_metric(values, "rank_score"),
                "worst_relative_value_gap_after_1y_pct": _min_metric(
                    values,
                    "worst_relative_value_gap_after_1y_pct",
                ),
                "worst_relative_value_gap_after_2y_pct": _min_metric(
                    values,
                    "worst_relative_value_gap_after_2y_pct",
                ),
                "worst_relative_value_gap_after_3y_pct": _min_metric(
                    values,
                    "worst_relative_value_gap_after_3y_pct",
                ),
            }
        )

    sorted_rows = sorted(
        rows,
        key=lambda row: (
            bool(row["robustness_gate_passed"]),
            float(row["pass_rate"]),
            float(row["min_relative_terminal_value_pct"]),
            float(row["min_rank_score"]),
        ),
        reverse=True,
    )
    return tuple(
        {
            "review_rank": rank,
            **row,
        }
        for rank, row in enumerate(sorted_rows, start=1)
    )


def scenario_results_to_selection_rows(
    scenarios: Mapping[str, Mapping[str, DcaResearchResult]],
    *,
    fixed_name: str = "fixed",
    min_review_scenarios: int = 3,
    min_effect_worst_relative_terminal_value_pct: float = 0.0,
    min_effect_median_relative_terminal_value_pct: float = 1.0,
    min_effect_worst_rank_score: float = 0.0,
) -> tuple[dict[str, object], ...]:
    """Select the strongest fixed candidate per family without parameter search.

    Selection is intentionally conservative: a candidate that does not pass the
    robustness gate can be named as the best observed variant, but it is not
    recommended for promotion.
    """

    robustness_rows = scenario_results_to_robustness_rows(
        scenarios,
        fixed_name=fixed_name,
    )
    if min_review_scenarios < 1:
        raise ValueError("min_review_scenarios must be at least 1")
    coverage_row = scenario_results_to_coverage_rows(
        scenarios,
        fixed_name=fixed_name,
        min_review_scenarios=min_review_scenarios,
    )[0]
    matrix_coverage_gate_passed = bool(coverage_row["coverage_gate_passed"])
    groups: dict[str, list[Mapping[str, object]]] = {}
    for row in robustness_rows:
        name = str(row["name"])
        groups.setdefault(_selection_group_for_candidate(name), []).append(row)

    selection_rows: list[dict[str, object]] = []
    for group_name, rows in sorted(groups.items()):
        ordered = sorted(
            rows,
            key=lambda row: (
                bool(row["robustness_gate_passed"]),
                float(row["pass_rate"]),
                float(row["min_relative_terminal_value_pct"]),
                float(row["min_rank_score"]),
            ),
            reverse=True,
        )
        selected = ordered[0]
        selected_passed = bool(selected["robustness_gate_passed"])
        selected_scenario_count = int(selected["scenario_count"])
        scenario_gate_passed = selected_scenario_count >= min_review_scenarios
        effect_size_failure_reasons = _selection_effect_size_failure_reasons(
            selected,
            min_worst_relative_terminal_value_pct=(
                min_effect_worst_relative_terminal_value_pct
            ),
            min_median_relative_terminal_value_pct=(
                min_effect_median_relative_terminal_value_pct
            ),
            min_worst_rank_score=min_effect_worst_rank_score,
        )
        effect_size_gate_passed = not effect_size_failure_reasons
        promotion_ready = (
            selected_passed
            and scenario_gate_passed
            and matrix_coverage_gate_passed
            and effect_size_gate_passed
        )
        selection_rows.append(
            {
                "selection_group": group_name,
                "selected_name": selected["name"],
                "selected_family": _candidate_family(str(selected["name"])),
                "selected_rule_type": _candidate_rule_type(str(selected["name"])),
                "selected_parameter_count": _candidate_parameter_count(str(selected["name"])),
                "selected_candidate_definition_sha256": _candidate_definition_sha256(
                    str(selected["name"])
                ),
                "selection_policy": "fixed_preset_no_parameter_search",
                "recommendation_status": (
                    "promote_to_manual_review"
                    if promotion_ready
                    else "hold_default_fixed_dca"
                ),
                "recommendation_reason": _selection_recommendation_reason(
                    selected_passed=selected_passed,
                    scenario_gate_passed=scenario_gate_passed,
                    matrix_coverage_gate_passed=matrix_coverage_gate_passed,
                    effect_size_gate_passed=effect_size_gate_passed,
                ),
                "selected_review_rank": selected["review_rank"],
                "selected_review_status": selected["review_status"],
                "selected_robustness_gate_passed": selected_passed,
                "selected_scenario_count": selected_scenario_count,
                "min_review_scenarios": min_review_scenarios,
                "review_scenario_gate_passed": scenario_gate_passed,
                "effect_size_policy": "fixed_minimum_effect_no_parameter_search",
                "selected_effect_size_gate_passed": effect_size_gate_passed,
                "selected_effect_size_failure_reasons": ",".join(
                    effect_size_failure_reasons
                ),
                "min_effect_worst_relative_terminal_value_pct": (
                    min_effect_worst_relative_terminal_value_pct
                ),
                "min_effect_median_relative_terminal_value_pct": (
                    min_effect_median_relative_terminal_value_pct
                ),
                "min_effect_worst_rank_score": min_effect_worst_rank_score,
                "matrix_coverage_gate_passed": matrix_coverage_gate_passed,
                "matrix_coverage_status": coverage_row["coverage_status"],
                "matrix_coverage_failure_reasons": coverage_row["failure_reasons"],
                "matrix_scenario_count": coverage_row["scenario_count"],
                "matrix_candidate_count": coverage_row["candidate_count"],
                "matrix_candidate_set_consistent": coverage_row["candidate_set_consistent"],
                "matrix_fixed_benchmark_present_all": coverage_row[
                    "fixed_benchmark_present_all"
                ],
                "matrix_candidate_names": coverage_row["candidate_names"],
                "selected_pass_rate": selected["pass_rate"],
                "selected_min_relative_terminal_value_pct": selected[
                    "min_relative_terminal_value_pct"
                ],
                "selected_median_relative_terminal_value_pct": selected[
                    "median_relative_terminal_value_pct"
                ],
                "selected_min_rank_score": selected["min_rank_score"],
                "selected_failure_reasons": selected["failure_reasons"],
                "candidate_count": len(ordered),
                "compared_candidates": ",".join(str(row["name"]) for row in ordered),
                "fixed_benchmark": fixed_name,
            }
        )
    return tuple(selection_rows)


def scenario_results_to_review_decision(
    scenarios: Mapping[str, Mapping[str, DcaResearchResult]],
    *,
    fixed_name: str = "fixed",
    min_review_scenarios: int = 3,
    min_effect_worst_relative_terminal_value_pct: float = 0.0,
    min_effect_median_relative_terminal_value_pct: float = 1.0,
    min_effect_worst_rank_score: float = 0.0,
) -> dict[str, object]:
    """Return a single JSON-safe decision summary for scenario review gates."""

    coverage_row = scenario_results_to_coverage_rows(
        scenarios,
        fixed_name=fixed_name,
        min_review_scenarios=min_review_scenarios,
    )[0]
    selection_rows = scenario_results_to_selection_rows(
        scenarios,
        fixed_name=fixed_name,
        min_review_scenarios=min_review_scenarios,
        min_effect_worst_relative_terminal_value_pct=(
            min_effect_worst_relative_terminal_value_pct
        ),
        min_effect_median_relative_terminal_value_pct=(
            min_effect_median_relative_terminal_value_pct
        ),
        min_effect_worst_rank_score=min_effect_worst_rank_score,
    )
    blocking_reasons = _review_decision_blocking_reasons(
        coverage_row=coverage_row,
        selection_rows=selection_rows,
    )
    manual_review_ready = not blocking_reasons
    effect_size_thresholds = {
        "min_worst_relative_terminal_value_pct": (
            min_effect_worst_relative_terminal_value_pct
        ),
        "min_median_relative_terminal_value_pct": (
            min_effect_median_relative_terminal_value_pct
        ),
        "min_worst_rank_score": min_effect_worst_rank_score,
    }
    return {
        "schema_version": SMART_DCA_RESEARCH_ARTIFACT_SCHEMA_VERSION,
        "artifact_type": "smart_dca_review_decision",
        "fixed_name": fixed_name,
        "min_review_scenarios": min_review_scenarios,
        "selection_policy": "fixed_preset_no_parameter_search",
        "effect_size_policy": "fixed_minimum_effect_no_parameter_search",
        "effect_size_thresholds": effect_size_thresholds,
        "selection_gate_summary": {
            "matrix_coverage_gate_passed": coverage_row["coverage_gate_passed"],
            "all_selection_effect_size_gate_passed": bool(selection_rows) and all(
                bool(row["selected_effect_size_gate_passed"])
                for row in selection_rows
            ),
            "all_selection_robustness_gate_passed": bool(selection_rows) and all(
                bool(row["selected_robustness_gate_passed"])
                for row in selection_rows
            ),
            "all_selection_review_scenario_gate_passed": bool(selection_rows) and all(
                bool(row["review_scenario_gate_passed"])
                for row in selection_rows
            ),
        },
        "manual_review_gate_passed": manual_review_ready,
        "overall_recommendation_status": (
            "promote_to_manual_review"
            if manual_review_ready
            else "hold_default_fixed_dca"
        ),
        "overall_recommendation_reason": (
            "all_selection_groups_ready_for_manual_review"
            if manual_review_ready
            else ",".join(blocking_reasons)
        ),
        "blocking_reasons": blocking_reasons,
        "matrix_coverage_gate_passed": coverage_row["coverage_gate_passed"],
        "matrix_coverage": coverage_row,
        "selection_count": len(selection_rows),
        "selection_groups": tuple(
            str(row["selection_group"])
            for row in selection_rows
        ),
        "selections": selection_rows,
    }


def _review_decision_blocking_reasons(
    *,
    coverage_row: Mapping[str, object],
    selection_rows: tuple[dict[str, object], ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if not bool(coverage_row["coverage_gate_passed"]):
        coverage_reasons = tuple(
            item
            for item in str(coverage_row["failure_reasons"]).split(",")
            if item
        )
        if coverage_reasons:
            reasons.extend(coverage_reasons)
        else:
            reasons.append("insufficient_scenario_matrix_coverage")
    if not selection_rows:
        reasons.append("no_candidate_selection_rows")
    for row in selection_rows:
        if row["recommendation_status"] != "promote_to_manual_review":
            reason = str(row["recommendation_reason"])
            if reason:
                reasons.append(reason)
    return tuple(dict.fromkeys(reasons))


def _selection_effect_size_failure_reasons(
    selected: Mapping[str, object],
    *,
    min_worst_relative_terminal_value_pct: float,
    min_median_relative_terminal_value_pct: float,
    min_worst_rank_score: float,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if (
        float(selected["min_relative_terminal_value_pct"])
        < min_worst_relative_terminal_value_pct
    ):
        reasons.append("worst_terminal_edge_below_min_effect_size")
    if (
        float(selected["median_relative_terminal_value_pct"])
        < min_median_relative_terminal_value_pct
    ):
        reasons.append("median_terminal_edge_below_min_effect_size")
    if float(selected["min_rank_score"]) < min_worst_rank_score:
        reasons.append("worst_rank_score_below_min_effect_size")
    return tuple(reasons)


def scenario_results_to_coverage_rows(
    scenarios: Mapping[str, Mapping[str, DcaResearchResult]],
    *,
    fixed_name: str = "fixed",
    min_review_scenarios: int = 3,
) -> tuple[dict[str, object], ...]:
    """Summarize scenario matrix coverage before reviewing selected candidates."""

    if min_review_scenarios < 1:
        raise ValueError("min_review_scenarios must be at least 1")

    scenario_names = tuple(str(name) for name in scenarios)
    candidate_sets = tuple(
        tuple(sorted(str(name) for name in results if name != fixed_name))
        for results in scenarios.values()
    )
    unique_candidate_sets = sorted(set(candidate_sets))
    fixed_present_all = all(fixed_name in results for results in scenarios.values())
    candidate_set_consistent = len(unique_candidate_sets) <= 1
    scenario_count = len(scenario_names)
    review_scenario_gate_passed = scenario_count >= min_review_scenarios
    candidate_names = unique_candidate_sets[0] if unique_candidate_sets else ()
    coverage_gate_passed = (
        scenario_count > 0
        and review_scenario_gate_passed
        and fixed_present_all
        and candidate_set_consistent
    )
    failure_reasons = _scenario_coverage_failure_reasons(
        scenario_count=scenario_count,
        review_scenario_gate_passed=review_scenario_gate_passed,
        fixed_present_all=fixed_present_all,
        candidate_set_consistent=candidate_set_consistent,
    )
    return (
        {
            "scenario_count": scenario_count,
            "min_review_scenarios": min_review_scenarios,
            "review_scenario_gate_passed": review_scenario_gate_passed,
            "fixed_benchmark": fixed_name,
            "fixed_benchmark_present_all": fixed_present_all,
            "candidate_set_consistent": candidate_set_consistent,
            "candidate_count": len(candidate_names),
            "candidate_names": ",".join(candidate_names),
            "scenario_names": ",".join(scenario_names),
            "coverage_gate_passed": coverage_gate_passed,
            "coverage_status": (
                "ready_for_selection_review"
                if coverage_gate_passed
                else "insufficient_coverage"
            ),
            "failure_reasons": ",".join(failure_reasons),
        },
    )


def _scenario_coverage_failure_reasons(
    *,
    scenario_count: int,
    review_scenario_gate_passed: bool,
    fixed_present_all: bool,
    candidate_set_consistent: bool,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if scenario_count <= 0:
        reasons.append("no_scenarios")
    if not review_scenario_gate_passed:
        reasons.append("scenario_count_below_min_review_scenarios")
    if not fixed_present_all:
        reasons.append("missing_fixed_benchmark")
    if not candidate_set_consistent:
        reasons.append("candidate_set_inconsistent")
    return tuple(reasons)


def _selection_recommendation_reason(
    *,
    selected_passed: bool,
    scenario_gate_passed: bool,
    matrix_coverage_gate_passed: bool,
    effect_size_gate_passed: bool,
) -> str:
    if (
        selected_passed
        and scenario_gate_passed
        and matrix_coverage_gate_passed
        and effect_size_gate_passed
    ):
        return "selected_candidate_passed_all_scenarios"
    if selected_passed:
        if not scenario_gate_passed:
            return "insufficient_robustness_scenarios"
        if not matrix_coverage_gate_passed:
            return "insufficient_scenario_matrix_coverage"
        return "insufficient_effect_size_vs_fixed_dca"
    return "no_candidate_passed_robustness_gate"


def _selection_group_for_candidate(name: str) -> str:
    candidate = PRESET_CANDIDATES.get(name)
    if candidate is None:
        return str(name)
    family = candidate.family
    for suffix in ("_variant",):
        if family.endswith(suffix):
            return family[: -len(suffix)]
    return family


def _candidate_family(name: str) -> str:
    candidate = PRESET_CANDIDATES.get(name)
    return "" if candidate is None else candidate.family


def _candidate_rule_type(name: str) -> str:
    candidate = PRESET_CANDIDATES.get(name)
    return "" if candidate is None else candidate.rule_type


def _candidate_parameter_count(name: str) -> int:
    candidate = PRESET_CANDIDATES.get(name)
    return 0 if candidate is None else len(candidate.parameters)


def _candidate_definition_sha256(name: str) -> str:
    candidate = PRESET_CANDIDATES.get(name)
    if candidate is None:
        return ""
    payload = {
        "name": candidate.name,
        "family": candidate.family,
        "rule_type": candidate.rule_type,
        "signal_symbols": candidate.signal_symbols,
        "min_history": candidate.min_history,
        "parameters": dict(sorted(candidate.parameters.items())),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _robustness_review_status(*, passed_count: int, scenario_count: int) -> str:
    if scenario_count <= 0:
        return "no_scenarios"
    if passed_count == scenario_count:
        return "candidate_passed_robustness_gate"
    if passed_count > 0:
        return "mixed_scenario_results"
    return "failed_robustness_gate"


def _scenario_for_min_metric(rows: Iterable[Mapping[str, object]], field: str) -> str:
    row = _scenario_for_metric(rows, field, reverse=False)
    return str(row.get("scenario", "")) if row is not None else ""


def _scenario_for_max_metric(rows: Iterable[Mapping[str, object]], field: str) -> str:
    row = _scenario_for_metric(rows, field, reverse=True)
    return str(row.get("scenario", "")) if row is not None else ""


def _scenario_for_metric(
    rows: Iterable[Mapping[str, object]],
    field: str,
    *,
    reverse: bool,
) -> Mapping[str, object] | None:
    candidates: list[tuple[float, Mapping[str, object]]] = []
    for row in rows:
        raw_value = row.get(field)
        if raw_value is None:
            continue
        value = float(raw_value)
        if not math.isnan(value):
            candidates.append((value, row))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[0], reverse=reverse)[0][1]


def _metric_values(rows: Iterable[Mapping[str, object]], field: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        raw_value = row.get(field)
        if raw_value is None:
            continue
        value = float(raw_value)
        if not math.isnan(value):
            values.append(value)
    return values


def _min_metric(rows: Iterable[Mapping[str, object]], field: str) -> float:
    values = _metric_values(rows, field)
    return float(min(values)) if values else 0.0


def _max_metric(rows: Iterable[Mapping[str, object]], field: str) -> float:
    values = _metric_values(rows, field)
    return float(max(values)) if values else 0.0


def _median_metric(rows: Iterable[Mapping[str, object]], field: str) -> float:
    values = sorted(_metric_values(rows, field))
    if not values:
        return 0.0
    midpoint = len(values) // 2
    if len(values) % 2:
        return float(values[midpoint])
    return float((values[midpoint - 1] + values[midpoint]) / 2.0)


def results_to_decision_log_rows(
    results: Mapping[str, DcaResearchResult],
) -> tuple[dict[str, object], ...]:
    """Convert trades and skipped buys into monthly decision-log rows."""

    rows: list[dict[str, object]] = []
    for result in results.values():
        for trade in result.trades:
            rows.append(
                {
                    "date": trade.get("date", ""),
                    "name": result.name,
                    "action": "buy",
                    "regime": trade.get("regime", ""),
                    "multiplier": trade.get("multiplier", 0.0),
                    "buy_value": trade.get("buy_value", 0.0),
                    "requested_buy_value": trade.get("requested_buy_value", 0.0),
                    "skip_reason": "",
                    **{
                        key: value
                        for key, value in trade.items()
                        if key
                        not in {
                            "date",
                            "name",
                            "regime",
                            "multiplier",
                            "buy_value",
                            "requested_buy_value",
                        }
                    },
                }
            )
        for skip in result.skips:
            rows.append(
                {
                    "date": skip.get("date", ""),
                    "name": result.name,
                    "action": "skip",
                    "regime": skip.get("regime", ""),
                    "multiplier": skip.get("multiplier", 0.0),
                    "buy_value": 0.0,
                    "requested_buy_value": 0.0,
                    "skip_reason": skip.get("reason", ""),
                    **{
                        key: value
                        for key, value in skip.items()
                        if key not in {"date", "name", "regime", "multiplier", "reason"}
                    },
                }
            )
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                str(row.get("date", "")),
                str(row.get("name", "")),
                str(row.get("action", "")),
            ),
        )
    )


def write_research_artifacts(
    output_dir: str | PathLike[str],
    results: Mapping[str, DcaResearchResult],
    *,
    fixed_name: str = "fixed",
    evaluations: Mapping[str, DcaCandidateEvaluation] | None = None,
) -> dict[str, Path]:
    """Write metrics, evaluation summary, and decision log CSV artifacts."""

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    resolved_evaluations = (
        evaluate_candidate_results(results, fixed_name=fixed_name)
        if evaluations is None
        else evaluations
    )
    metrics_path = output_path / "metrics.csv"
    evaluation_path = output_path / "evaluation_summary.csv"
    decision_log_path = output_path / "decision_log.csv"
    equity_curve_path = output_path / "equity_curve.csv"
    cash_flows_path = output_path / "cash_flows.csv"
    candidate_summary_path = output_path / "candidate_summary.csv"
    candidate_specs_path = output_path / "candidate_specs.csv"
    run_manifest_path = output_path / "run_manifest.json"
    candidate_names = tuple(name for name in results if name != fixed_name)

    pd.DataFrame(results_to_metrics_rows(results, fixed_name=fixed_name, evaluations=resolved_evaluations)).to_csv(
        metrics_path,
        index=False,
    )
    pd.DataFrame(
        {
            "name": item.name,
            "passed": item.passed,
            "reasons": ",".join(item.reasons),
            "relative_terminal_value_pct": item.relative_terminal_value_pct,
            "max_drawdown_delta_pct_points": item.max_drawdown_delta_pct_points,
            "skipped_buy_ratio": item.skipped_buy_ratio,
            "deployment_rate_delta_pct_points": item.deployment_rate_delta_pct_points,
            "rank_score": item.rank_score,
        }
        for item in summarize_candidate_evaluations(resolved_evaluations)
    ).to_csv(evaluation_path, index=False)
    pd.DataFrame(results_to_decision_log_rows(results)).to_csv(decision_log_path, index=False)
    pd.DataFrame(results_to_equity_curve_rows(results)).to_csv(equity_curve_path, index=False)
    pd.DataFrame(results_to_cash_flow_rows(results)).to_csv(cash_flows_path, index=False)
    pd.DataFrame(candidate_summaries_to_rows(candidate_names)).to_csv(candidate_summary_path, index=False)
    pd.DataFrame(candidate_specs_to_rows(candidate_names)).to_csv(candidate_specs_path, index=False)
    _write_artifact_manifest(
        run_manifest_path,
        artifact_type="smart_dca_research_run",
        files=(
            metrics_path,
            evaluation_path,
            decision_log_path,
            equity_curve_path,
            cash_flows_path,
            candidate_summary_path,
            candidate_specs_path,
        ),
        root=output_path,
        extra={
            "fixed_name": fixed_name,
            "result_names": tuple(results),
            "candidate_names": candidate_names,
        },
    )
    return {
        "metrics": metrics_path,
        "evaluation_summary": evaluation_path,
        "decision_log": decision_log_path,
        "equity_curve": equity_curve_path,
        "cash_flows": cash_flows_path,
        "candidate_summary": candidate_summary_path,
        "candidate_specs": candidate_specs_path,
        "run_manifest": run_manifest_path,
    }


def write_scenario_research_artifacts(
    output_dir: str | PathLike[str],
    scenarios: Mapping[str, Mapping[str, DcaResearchResult]],
    *,
    fixed_name: str = "fixed",
    metadata: Mapping[str, object] | None = None,
    min_review_scenarios: int = 3,
) -> dict[str, Path]:
    """Write per-scenario artifacts and a top-level scenario index CSV."""

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    index_rows: list[dict[str, object]] = []
    artifact_paths: dict[str, Path] = {}

    for scenario_name, results in scenarios.items():
        safe_name = str(scenario_name).replace("/", "_").replace("\\", "_")
        scenario_dir = output_path / safe_name
        paths = write_research_artifacts(scenario_dir, results, fixed_name=fixed_name)
        evaluations = evaluate_candidate_results(results, fixed_name=fixed_name)
        for evaluation in summarize_candidate_evaluations(evaluations):
            index_rows.append(
                {
                    "scenario": scenario_name,
                    "name": evaluation.name,
                    "passed": evaluation.passed,
                    "reasons": ",".join(evaluation.reasons),
                    "relative_terminal_value_pct": evaluation.relative_terminal_value_pct,
                    "max_drawdown_delta_pct_points": evaluation.max_drawdown_delta_pct_points,
                    "skipped_buy_ratio": evaluation.skipped_buy_ratio,
                    "deployment_rate_delta_pct_points": evaluation.deployment_rate_delta_pct_points,
                    "rank_score": evaluation.rank_score,
                    "metrics_path": paths["metrics"].relative_to(output_path).as_posix(),
                    "evaluation_summary_path": paths["evaluation_summary"].relative_to(output_path).as_posix(),
                    "decision_log_path": paths["decision_log"].relative_to(output_path).as_posix(),
                    "candidate_summary_path": paths["candidate_summary"].relative_to(output_path).as_posix(),
                }
            )
        for key, path in paths.items():
            artifact_paths[f"{safe_name}_{key}"] = path

    scenario_index_path = output_path / "scenario_index.csv"
    robustness_summary_path = output_path / "robustness_summary.csv"
    selection_summary_path = output_path / "selection_summary.csv"
    scenario_coverage_path = output_path / "scenario_coverage.csv"
    review_decision_path = output_path / "review_decision.json"
    scenario_manifest_path = output_path / "scenario_manifest.json"
    pd.DataFrame(index_rows).to_csv(scenario_index_path, index=False)
    pd.DataFrame(
        scenario_results_to_robustness_rows(scenarios, fixed_name=fixed_name)
    ).to_csv(robustness_summary_path, index=False)
    pd.DataFrame(
        scenario_results_to_selection_rows(
            scenarios,
            fixed_name=fixed_name,
            min_review_scenarios=min_review_scenarios,
        )
    ).to_csv(selection_summary_path, index=False)
    pd.DataFrame(
        scenario_results_to_coverage_rows(
            scenarios,
            fixed_name=fixed_name,
            min_review_scenarios=min_review_scenarios,
        )
    ).to_csv(scenario_coverage_path, index=False)
    review_decision = scenario_results_to_review_decision(
        scenarios,
        fixed_name=fixed_name,
        min_review_scenarios=min_review_scenarios,
    )
    review_decision_path.write_text(
        json.dumps(review_decision, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    artifact_paths["scenario_index"] = scenario_index_path
    artifact_paths["robustness_summary"] = robustness_summary_path
    artifact_paths["selection_summary"] = selection_summary_path
    artifact_paths["scenario_coverage"] = scenario_coverage_path
    artifact_paths["review_decision"] = review_decision_path
    _write_artifact_manifest(
        scenario_manifest_path,
        artifact_type="smart_dca_research_scenario_matrix",
        files=tuple(artifact_paths.values()),
        root=output_path,
        extra={
            "fixed_name": fixed_name,
            "min_review_scenarios": min_review_scenarios,
            "scenario_names": tuple(scenarios),
            "metadata": dict(metadata or {}),
        },
    )
    artifact_paths["scenario_manifest"] = scenario_manifest_path
    return artifact_paths


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_file_record(path: Path, *, root: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _write_artifact_manifest(
    path: Path,
    *,
    artifact_type: str,
    files: Iterable[Path],
    root: Path,
    extra: Mapping[str, object],
) -> None:
    manifest = {
        "schema_version": SMART_DCA_RESEARCH_ARTIFACT_SCHEMA_VERSION,
        "artifact_type": artifact_type,
        "files": tuple(_artifact_file_record(item, root=root) for item in files),
        **dict(extra),
    }
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def compare_smart_dca_candidates(
    *,
    signal_prices: Any,
    trade_prices: Any,
    candidate_set: str | Iterable[str] = "nasdaq_sp500_price",
    monthly_contribution_usd: float = 1000.0,
    start_date: object | None = None,
    end_date: object | None = None,
    align_start_after_warmup: bool = True,
    min_investment_usd: float = 0.0,
    monthly_execution_day: int = 1,
    cadence: str = "monthly",
) -> dict[str, DcaResearchResult]:
    """Compare fixed DCA with a small named preset universe of smart DCA rules.

    The helper is intentionally offline and anti-overfit oriented: callers pass
    pandas-compatible price inputs, and candidate rules come only from
    PRESET_CANDIDATES rather than an open parameter search.
    """

    if monthly_contribution_usd <= 0.0:
        raise ValueError("monthly_contribution_usd must be positive")

    candidate_names = _resolve_candidate_names(candidate_set)
    candidates = tuple(PRESET_CANDIDATES[name] for name in candidate_names)
    trade_series = _close_series(trade_prices)
    signal_frame = _price_frame(signal_prices)
    if trade_series.empty:
        raise ValueError("trade_prices must contain at least one positive price")
    if signal_frame.empty:
        raise ValueError("signal_prices must contain at least one positive price series")

    candidate_frames = {
        candidate.name: _candidate_signal_frame(signal_frame, candidate)
        for candidate in candidates
    }
    common_index = trade_series.index
    for frame in candidate_frames.values():
        common_index = common_index.intersection(frame.dropna(how="any").index)
    common_index = common_index.sort_values()

    if align_start_after_warmup and candidates:
        warmup = max(candidate.min_history for candidate in candidates)
        if len(common_index) >= warmup:
            warmup_start = common_index[warmup - 1]
            common_index = common_index[common_index >= warmup_start]
        else:
            common_index = common_index[:0]

    if start_date is not None:
        start = pd.Timestamp(start_date).tz_localize(None).normalize()
        common_index = common_index[common_index >= start]
    if end_date is not None:
        end = pd.Timestamp(end_date).tz_localize(None).normalize()
        common_index = common_index[common_index <= end]

    if common_index.empty:
        raise ValueError("no overlapping price history remains after date and warmup filters")

    trade_path = trade_series.loc[common_index]
    normalized_cadence = _normalize_cadence(cadence)
    contribution_dates = _period_start_dates(trade_path.index, normalized_cadence)
    execution_dates = _scheduled_execution_dates(
        trade_path.index,
        cadence=normalized_cadence,
        monthly_execution_day=monthly_execution_day,
    )
    contribution_amount_usd = _cadence_contribution_amount(
        monthly_contribution_usd,
        normalized_cadence,
    )
    results: dict[str, DcaResearchResult] = {
        "fixed": _run_path(
            name="fixed",
            trade_prices=trade_path,
            signal_prices=None,
            contribution_amount_usd=contribution_amount_usd,
            candidate=None,
            min_investment_usd=min_investment_usd,
            contribution_dates=contribution_dates,
            execution_dates=execution_dates,
        )
    }

    for candidate in candidates:
        results[candidate.name] = _run_path(
            name=candidate.name,
            trade_prices=trade_path,
            signal_prices=candidate_frames[candidate.name],
            contribution_amount_usd=contribution_amount_usd,
            candidate=candidate,
            min_investment_usd=min_investment_usd,
            contribution_dates=contribution_dates,
            execution_dates=execution_dates,
        )

    return _with_relative_value(results, fixed_name="fixed")


def compare_monthly_execution_day_scenarios(
    *,
    signal_prices: Any,
    trade_prices: Any,
    execution_days: Iterable[int] = (1, 10, 15, 20, 25),
    candidate_set: str | Iterable[str] = "nasdaq_sp500_price",
    monthly_contribution_usd: float = 1000.0,
    start_date: object | None = None,
    end_date: object | None = None,
    align_start_after_warmup: bool = True,
    min_investment_usd: float = 0.0,
    cadence: str = "monthly",
) -> dict[str, dict[str, DcaResearchResult]]:
    """Run the same fixed candidate universe across monthly execution days."""

    normalized_cadence = _normalize_cadence(cadence)
    scenarios: dict[str, dict[str, DcaResearchResult]] = {}
    for raw_day in execution_days:
        day = int(max(1, min(31, raw_day)))
        scenarios[f"{normalized_cadence}_day_{day}"] = compare_smart_dca_candidates(
            signal_prices=signal_prices,
            trade_prices=trade_prices,
            candidate_set=candidate_set,
            monthly_contribution_usd=monthly_contribution_usd,
            start_date=start_date,
            end_date=end_date,
            align_start_after_warmup=align_start_after_warmup,
            min_investment_usd=min_investment_usd,
            monthly_execution_day=day,
            cadence=normalized_cadence,
        )
    return scenarios


def compare_execution_day_contribution_scenarios(
    *,
    signal_prices: Any,
    trade_prices: Any,
    execution_days: Iterable[int] = (1, 10, 15, 20, 25),
    monthly_contribution_usd_values: Iterable[float] = (500.0, 1000.0, 3000.0),
    start_dates: Iterable[object] | None = None,
    cadences: Iterable[str] = ("monthly",),
    candidate_set: str | Iterable[str] = "nasdaq_sp500_price",
    start_date: object | None = None,
    end_date: object | None = None,
    align_start_after_warmup: bool = True,
    min_investment_usd: float = 0.0,
) -> dict[str, dict[str, DcaResearchResult]]:
    """Run a fixed candidate universe across execution-day and contribution scales.

    This is a bounded anti-overfit robustness matrix. It does not search
    thresholds; it repeats the same preset rules across plausible monthly cash
    contribution sizes and monthly execution days.
    """

    amounts = tuple(float(value) for value in monthly_contribution_usd_values)
    if not amounts:
        raise ValueError("monthly_contribution_usd_values must include at least one amount")
    invalid_amounts = [value for value in amounts if value <= 0.0]
    if invalid_amounts:
        raise ValueError(
            "monthly_contribution_usd_values must be positive: "
            + ", ".join(str(value) for value in invalid_amounts)
        )

    resolved_start_dates = _scenario_start_dates(start_dates, fallback_start_date=start_date)
    resolved_cadences = _scenario_cadences(cadences)
    resolved_execution_days = tuple(execution_days)
    if not resolved_execution_days:
        raise ValueError("execution_days must include at least one day")

    scenarios: dict[str, dict[str, DcaResearchResult]] = {}
    for scenario_start_date in resolved_start_dates:
        for cadence in resolved_cadences:
            cadence_days = (None,) if cadence == "weekly" else resolved_execution_days
            for amount in amounts:
                for raw_day in cadence_days:
                    day = 1 if raw_day is None else int(max(1, min(31, raw_day)))
                    amount_label = _scenario_amount_label(amount)
                    if cadence == "weekly":
                        scenario_name = f"weekly_contribution_usd_{amount_label}"
                    else:
                        scenario_name = f"{cadence}_day_{day}_contribution_usd_{amount_label}"
                    if scenario_start_date is not None:
                        scenario_name += f"_start_{_scenario_date_label(scenario_start_date)}"
                    scenarios[scenario_name] = compare_smart_dca_candidates(
                        signal_prices=signal_prices,
                        trade_prices=trade_prices,
                        candidate_set=candidate_set,
                        monthly_contribution_usd=amount,
                        start_date=scenario_start_date,
                        end_date=end_date,
                        align_start_after_warmup=align_start_after_warmup,
                        min_investment_usd=min_investment_usd,
                        monthly_execution_day=day,
                        cadence=cadence,
                    )
    return scenarios


def _scenario_amount_label(amount: float) -> str:
    value = float(amount)
    if value.is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", "_")


def _scenario_start_dates(
    start_dates: Iterable[object] | None,
    *,
    fallback_start_date: object | None,
) -> tuple[pd.Timestamp | None, ...]:
    if start_dates is None:
        return (None if fallback_start_date is None else _normalize_scenario_date(fallback_start_date),)
    dates = tuple(_normalize_scenario_date(value) for value in start_dates)
    if not dates:
        raise ValueError("start_dates must include at least one date when provided")
    return dates


def _scenario_cadences(cadences: Iterable[str]) -> tuple[str, ...]:
    values = tuple(_normalize_cadence(cadence) for cadence in cadences)
    if not values:
        raise ValueError("cadences must include at least one cadence")
    return values


def _normalize_scenario_date(value: object) -> pd.Timestamp:
    return pd.Timestamp(value).tz_localize(None).normalize()


def _scenario_date_label(value: object) -> str:
    return _normalize_scenario_date(value).date().isoformat().replace("-", "_")
