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
    trade_count: int
    skipped_count: int
    deployment_rate: float
    relative_terminal_value_pct: float
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


PRESET_CANDIDATES: dict[str, SmartDcaCandidate] = {
    "nasdaq_sp500_price_defensive": SmartDcaCandidate(
        name="nasdaq_sp500_price_defensive",
        family="nasdaq_sp500_price",
        rule_type="trend_drawdown",
        signal_symbols=("QQQ", "SPY"),
        min_history=252,
        parameters={
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
        },
    ),
    "ibit_btc_ahr999_mayer_cycle": SmartDcaCandidate(
        name="ibit_btc_ahr999_mayer_cycle",
        family="ibit_btc_ahr999_mayer_price",
        rule_type="ahr999_mayer",
        signal_symbols=("BTC-USD",),
        min_history=200,
        parameters={
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
        },
    ),
}

CANDIDATE_SETS: dict[str, tuple[str, ...]] = {
    "nasdaq_sp500_price": ("nasdaq_sp500_price_defensive",),
    "ibit_btc_ahr999_mayer_price": ("ibit_btc_ahr999_mayer_cycle",),
    "all": tuple(PRESET_CANDIDATES),
}


def available_candidate_names() -> tuple[str, ...]:
    """Return the small fixed preset universe used by this research helper."""

    return tuple(PRESET_CANDIDATES)


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
) -> tuple[float, str, dict[str, object]]:
    metrics = _ahr999_mayer_metrics(signal_history, as_of)
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
    raise ValueError(f"unsupported smart DCA rule_type: {candidate.rule_type}")


def _max_drawdown(values: list[float]) -> float:
    peak = 0.0
    max_dd = 0.0
    for value in values:
        peak = max(peak, float(value))
        if peak > 0.0:
            max_dd = max(max_dd, 1.0 - float(value) / peak)
    return float(max_dd)


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


def _run_path(
    *,
    name: str,
    trade_prices: pd.Series,
    signal_prices: pd.DataFrame | None,
    monthly_contribution_usd: float,
    candidate: SmartDcaCandidate | None,
    min_investment_usd: float,
    execution_dates: frozenset[pd.Timestamp],
) -> DcaResearchResult:
    cash = 0.0
    shares = 0.0
    invested = 0.0
    contributions = 0.0
    last_month = ""
    equity_curve: list[float] = []
    trades: list[dict[str, object]] = []
    skips: list[dict[str, object]] = []
    last_metrics: dict[str, object] = {}

    for raw_date, raw_price in trade_prices.items():
        date = pd.Timestamp(raw_date).normalize()
        price = float(raw_price)
        month_key = date.strftime("%Y-%m")
        is_contribution_day = month_key != last_month

        if is_contribution_day:
            cash += float(monthly_contribution_usd)
            contributions += float(monthly_contribution_usd)
            last_month = month_key

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

            requested_buy = float(monthly_contribution_usd) * max(0.0, float(multiplier))
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

        equity_curve.append(cash + shares * price)

    final_price = float(trade_prices.iloc[-1]) if not trade_prices.empty else 0.0
    terminal_value = cash + shares * final_price
    deployment_rate = invested / contributions if contributions > 0.0 else 0.0
    return DcaResearchResult(
        name=name,
        terminal_value=float(terminal_value),
        cash=float(cash),
        shares=float(shares),
        invested=float(invested),
        contributions=float(contributions),
        max_drawdown=_max_drawdown(equity_curve),
        trade_count=len(trades),
        skipped_count=len(skips),
        deployment_rate=float(deployment_rate),
        relative_terminal_value_pct=0.0,
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
            "trade_count": result.trade_count,
            "skipped_count": result.skipped_count,
            "skipped_buy_ratio": _skipped_buy_ratio(result),
            "deployment_rate_pct": result.deployment_rate * 100.0,
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
    run_manifest_path = output_path / "run_manifest.json"

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
    _write_artifact_manifest(
        run_manifest_path,
        artifact_type="smart_dca_research_run",
        files=(metrics_path, evaluation_path, decision_log_path),
        root=output_path,
        extra={
            "fixed_name": fixed_name,
            "result_names": tuple(results),
            "candidate_names": tuple(name for name in results if name != fixed_name),
        },
    )
    return {
        "metrics": metrics_path,
        "evaluation_summary": evaluation_path,
        "decision_log": decision_log_path,
        "run_manifest": run_manifest_path,
    }


def write_scenario_research_artifacts(
    output_dir: str | PathLike[str],
    scenarios: Mapping[str, Mapping[str, DcaResearchResult]],
    *,
    fixed_name: str = "fixed",
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
                }
            )
        for key, path in paths.items():
            artifact_paths[f"{safe_name}_{key}"] = path

    scenario_index_path = output_path / "scenario_index.csv"
    scenario_manifest_path = output_path / "scenario_manifest.json"
    pd.DataFrame(index_rows).to_csv(scenario_index_path, index=False)
    artifact_paths["scenario_index"] = scenario_index_path
    _write_artifact_manifest(
        scenario_manifest_path,
        artifact_type="smart_dca_research_scenario_matrix",
        files=tuple(artifact_paths.values()),
        root=output_path,
        extra={
            "fixed_name": fixed_name,
            "scenario_names": tuple(scenarios),
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
    execution_dates = _monthly_execution_dates(trade_path.index, monthly_execution_day)
    results: dict[str, DcaResearchResult] = {
        "fixed": _run_path(
            name="fixed",
            trade_prices=trade_path,
            signal_prices=None,
            monthly_contribution_usd=monthly_contribution_usd,
            candidate=None,
            min_investment_usd=min_investment_usd,
            execution_dates=execution_dates,
        )
    }

    for candidate in candidates:
        results[candidate.name] = _run_path(
            name=candidate.name,
            trade_prices=trade_path,
            signal_prices=candidate_frames[candidate.name],
            monthly_contribution_usd=monthly_contribution_usd,
            candidate=candidate,
            min_investment_usd=min_investment_usd,
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
) -> dict[str, dict[str, DcaResearchResult]]:
    """Run the same fixed candidate universe across monthly execution days."""

    scenarios: dict[str, dict[str, DcaResearchResult]] = {}
    for raw_day in execution_days:
        day = int(max(1, min(31, raw_day)))
        scenarios[f"monthly_day_{day}"] = compare_smart_dca_candidates(
            signal_prices=signal_prices,
            trade_prices=trade_prices,
            candidate_set=candidate_set,
            monthly_contribution_usd=monthly_contribution_usd,
            start_date=start_date,
            end_date=end_date,
            align_start_after_warmup=align_start_after_warmup,
            min_investment_usd=min_investment_usd,
            monthly_execution_day=day,
        )
    return scenarios
