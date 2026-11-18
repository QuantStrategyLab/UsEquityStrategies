from __future__ import annotations

from collections.abc import Iterable, Mapping
import math
from typing import Any

import pandas as pd

from .smart_dca_research_models import (
    BITCOIN_GENESIS_DATE,
    CANDIDATE_SETS,
    PRESET_CANDIDATES,
    SmartDcaCandidate,
    available_candidate_names,
)


def _normalize_symbol(symbol: object) -> str:
    return str(symbol or "").strip().upper().removesuffix(".US")


def _close_series(values: Any, *, positive_only: bool = True) -> pd.Series:
    if isinstance(values, pd.Series):
        series = values.copy()
    elif isinstance(values, pd.DataFrame):
        if values.empty:
            return pd.Series(dtype=float)
        series = values["close"] if "close" in values.columns else values.iloc[:, 0]
    else:
        series = pd.Series(values)
    series = pd.to_numeric(series, errors="coerce").dropna().astype(float)
    if positive_only:
        series = series[series > 0.0]
    if series.empty:
        return pd.Series(dtype=float)
    series.index = pd.to_datetime(series.index).tz_localize(None).normalize()
    return series.sort_index()


def _price_frame(values: Any) -> pd.DataFrame:
    if isinstance(values, Mapping):
        columns = {
            _normalize_symbol(symbol): _close_series(series, positive_only=False)
            for symbol, series in values.items()
        }
        frame = pd.DataFrame(columns)
    elif isinstance(values, pd.DataFrame):
        if "close" in values.columns and len(values.columns) == 1:
            frame = pd.DataFrame(
                {"SIGNAL": _close_series(values["close"], positive_only=False)}
            )
        else:
            columns = {
                _normalize_symbol(column): _close_series(
                    values[column],
                    positive_only=False,
                )
                for column in values.columns
            }
            frame = pd.DataFrame(columns)
    else:
        frame = pd.DataFrame({"SIGNAL": _close_series(values, positive_only=False)})
    return frame.dropna(how="all").sort_index()


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


def _precomputed_nasdaq_sp500_valuation_guard_multiplier(
    signal_history: pd.DataFrame,
    parameters: Mapping[str, float],
) -> tuple[float, str, dict[str, object]]:
    latest = signal_history.iloc[-1]
    cape_percentile = float(latest[_normalize_symbol("cape_percentile")])
    metrics: dict[str, object] = {
        "cape_percentile": cape_percentile,
        "cycle_indicator_source": "external_precomputed_us_equity_context",
    }
    if cape_percentile >= parameters["cape_expensive_percentile_threshold"]:
        return (
            float(parameters["valuation_guard_multiplier"]),
            "valuation_expensive_guard",
            metrics,
        )
    return float(parameters["base_multiplier"]), "valuation_normal", metrics


def _precomputed_nasdaq_sp500_vol_breadth_stress_multiplier(
    signal_history: pd.DataFrame,
    parameters: Mapping[str, float],
) -> tuple[float, str, dict[str, object]]:
    latest = signal_history.iloc[-1]
    vix_percentile = float(latest[_normalize_symbol("vix_percentile")])
    breadth = float(latest[_normalize_symbol("breadth_above_sma200_pct")])
    metrics: dict[str, object] = {
        "vix_percentile": vix_percentile,
        "breadth_above_sma200_pct": breadth,
        "cycle_indicator_source": "external_precomputed_us_equity_context",
    }
    stressed = vix_percentile >= parameters["vix_stress_percentile_threshold"]
    weak_breadth = breadth <= parameters["breadth_weak_threshold"]
    if stressed and weak_breadth:
        return (
            float(parameters["stress_pullback_multiplier"]),
            "volatility_breadth_stress_add",
            metrics,
        )
    return float(parameters["base_multiplier"]), "volatility_breadth_normal", metrics


def _precomputed_nasdaq_sp500_cape_vix_guard_multiplier(
    signal_history: pd.DataFrame,
    parameters: Mapping[str, float],
) -> tuple[float, str, dict[str, object]]:
    latest = signal_history.iloc[-1]
    cape_percentile = float(latest[_normalize_symbol("cape_percentile")])
    vix_percentile = float(latest[_normalize_symbol("vix_percentile")])
    metrics: dict[str, object] = {
        "cape_percentile": cape_percentile,
        "vix_percentile": vix_percentile,
        "cycle_indicator_source": "external_precomputed_us_equity_context",
    }
    if cape_percentile >= parameters["cape_expensive_percentile_threshold"]:
        return (
            float(parameters["valuation_guard_multiplier"]),
            "cape_vix_valuation_expensive_guard",
            metrics,
        )
    if vix_percentile >= parameters["vix_stress_percentile_threshold"]:
        return (
            float(parameters["vix_stress_multiplier"]),
            "cape_vix_volatility_stress_add",
            metrics,
        )
    return float(parameters["base_multiplier"]), "cape_vix_normal", metrics


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


def _ahr999_multiplier(
    signal_history: pd.DataFrame,
    parameters: Mapping[str, float],
    *,
    as_of: pd.Timestamp,
) -> tuple[float, str, dict[str, object]]:
    metrics = _ahr999_mayer_metrics(signal_history, as_of)
    ahr999 = float(metrics["ahr999"])
    metrics = {
        **metrics,
        "ahr999_metric": "ahr999",
        "ahr999_selected": ahr999,
    }
    multiplier, regime = _ahr999_regime_multiplier(ahr999, parameters)
    return multiplier, regime, metrics


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


def _precomputed_ahr999_multiplier(
    signal_history: pd.DataFrame,
    parameters: Mapping[str, float],
) -> tuple[float, str, dict[str, object]]:
    latest = signal_history.iloc[-1]
    ahr999 = float(latest[_normalize_symbol("ahr999")])
    metrics: dict[str, object] = {
        "ahr999": ahr999,
        "ahr999_selected": ahr999,
        "ahr999_metric": "ahr999",
        "cycle_indicator_source": "precomputed_derived_indicators",
    }
    if _normalize_symbol("mayer_multiple") in latest.index:
        metrics["mayer_multiple"] = float(latest[_normalize_symbol("mayer_multiple")])
    multiplier, regime = _ahr999_regime_multiplier(ahr999, parameters)
    return multiplier, regime, metrics


def _precomputed_ahr999_percentile_multiplier(
    signal_history: pd.DataFrame,
    parameters: Mapping[str, float],
) -> tuple[float, str, dict[str, object]]:
    latest = signal_history.iloc[-1]
    percentile = float(latest[_normalize_symbol("ahr999_365d_percentile")])
    metrics: dict[str, object] = {
        "ahr999_365d_percentile": percentile,
        "ahr999_metric": "ahr999_365d_percentile",
        "ahr999_selected": percentile,
        "cycle_indicator_source": "precomputed_derived_indicators",
    }

    if percentile <= parameters["ahr999_bottom_percentile_threshold"]:
        return (
            float(parameters["ahr999_bottom_multiplier"]),
            "ahr999_percentile_bottom",
            metrics,
        )
    if percentile <= parameters["ahr999_accumulation_percentile_threshold"]:
        return (
            float(parameters["ahr999_accumulation_multiplier"]),
            "ahr999_percentile_accumulation",
            metrics,
        )
    if percentile <= parameters["ahr999_dca_percentile_threshold"]:
        return (
            float(parameters["ahr999_dca_multiplier"]),
            "ahr999_percentile_dca",
            metrics,
        )
    if percentile >= parameters["ahr999_expensive_percentile_threshold"]:
        return (
            float(parameters["ahr999_expensive_multiplier"]),
            "ahr999_percentile_expensive",
            metrics,
        )
    return float(parameters["base_multiplier"]), "ahr999_percentile_normal", metrics


def _precomputed_ahr999_guarded_multiplier(
    signal_history: pd.DataFrame,
    parameters: Mapping[str, float],
) -> tuple[float, str, dict[str, object]]:
    latest = signal_history.iloc[-1]
    ahr999 = float(latest[_normalize_symbol("ahr999")])
    percentile = float(latest[_normalize_symbol("ahr999_365d_percentile")])
    slope = float(latest[_normalize_symbol("ahr999_30d_slope")])
    multiplier, regime = _ahr999_regime_multiplier(ahr999, parameters)
    metrics: dict[str, object] = {
        "ahr999": ahr999,
        "ahr999_365d_percentile": percentile,
        "ahr999_30d_slope": slope,
        "ahr999_metric": "ahr999",
        "ahr999_selected": ahr999,
        "cycle_indicator_source": "precomputed_derived_indicators",
    }
    if regime != "ahr999_expensive":
        return multiplier, regime, metrics

    high_percentile = percentile >= parameters["ahr999_high_percentile_threshold"]
    rising = slope >= parameters["ahr999_rising_slope_threshold"]
    if high_percentile and rising:
        return multiplier, "ahr999_expensive_high_percentile_rising", metrics
    return (
        float(parameters["guarded_expensive_multiplier"]),
        "ahr999_expensive_guarded_dca",
        metrics,
    )


def _ahr999_regime_multiplier(
    ahr999: float,
    parameters: Mapping[str, float],
) -> tuple[float, str]:
    if ahr999 <= parameters["ahr999_bottom_threshold"]:
        return float(parameters["ahr999_bottom_multiplier"]), "ahr999_bottom"
    if ahr999 <= parameters["ahr999_accumulation_threshold"]:
        return float(parameters["ahr999_accumulation_multiplier"]), "ahr999_accumulation"
    if ahr999 <= parameters["ahr999_dca_threshold"]:
        return float(parameters["ahr999_dca_multiplier"]), "ahr999_dca"
    return float(parameters["ahr999_expensive_multiplier"]), "ahr999_expensive"


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
    if candidate.rule_type == "precomputed_nasdaq_sp500_valuation_guard":
        return _precomputed_nasdaq_sp500_valuation_guard_multiplier(
            signal_history,
            candidate.parameters,
        )
    if candidate.rule_type == "precomputed_nasdaq_sp500_vol_breadth_stress":
        return _precomputed_nasdaq_sp500_vol_breadth_stress_multiplier(
            signal_history,
            candidate.parameters,
        )
    if candidate.rule_type == "precomputed_nasdaq_sp500_cape_vix_guard":
        return _precomputed_nasdaq_sp500_cape_vix_guard_multiplier(
            signal_history,
            candidate.parameters,
        )
    if candidate.rule_type == "ahr999":
        return _ahr999_multiplier(signal_history, candidate.parameters, as_of=as_of)
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
    if candidate.rule_type == "precomputed_ahr999":
        return _precomputed_ahr999_multiplier(signal_history, candidate.parameters)
    if candidate.rule_type == "precomputed_ahr999_sma_mayer":
        return _precomputed_ahr999_mayer_multiplier(
            signal_history,
            candidate.parameters,
            ahr999_column="ahr999_sma",
        )
    if candidate.rule_type == "precomputed_ahr999_percentile":
        return _precomputed_ahr999_percentile_multiplier(
            signal_history,
            candidate.parameters,
        )
    if candidate.rule_type == "precomputed_ahr999_guarded":
        return _precomputed_ahr999_guarded_multiplier(
            signal_history,
            candidate.parameters,
        )
    raise ValueError(f"unsupported smart DCA rule_type: {candidate.rule_type}")


def _signal_history_before_execution(prices, execution_date: pd.Timestamp):
    """Research signals must be observable before the execution session."""
    return prices.loc[prices.index < execution_date]

