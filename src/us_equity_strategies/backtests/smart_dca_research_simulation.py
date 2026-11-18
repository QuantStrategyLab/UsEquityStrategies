from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
import math

import pandas as pd

from .smart_dca_research_models import (
    DcaResearchResult,
    SUPPORTED_DCA_CADENCES,
    SmartDcaCandidate,
)
from .smart_dca_research_signals import (
    _candidate_multiplier,
    _candidate_signal_frame,
    _close_series,
    _signal_history_before_execution,
)


def _max_drawdown(values: list[float]) -> float:
    peak = 0.0
    max_dd = 0.0
    for value in values:
        peak = max(peak, float(value))
        if peak > 0.0:
            max_dd = max(max_dd, 1.0 - float(value) / peak)
    return float(max_dd)


def _max_underwater_days(
    equity_curve: Iterable[Mapping[str, object]],
    *,
    value_key: str = "equity",
) -> int:
    peak = 0.0
    peak_date: pd.Timestamp | None = None
    max_days = 0
    for row in equity_curve:
        date = pd.Timestamp(row["date"]).normalize()
        value = float(row[value_key])
        if value >= peak:
            peak = value
            peak_date = date
        elif peak_date is not None:
            max_days = max(max_days, int((date - peak_date).days))
    return max_days


def _annotate_investment_risk(rows: list[dict[str, object]]) -> tuple[float, int]:
    """Drawdown of a contribution-neutral index, leaving the asset curve in place.

    Each session adds the external contribution before it marks equity at that
    session close. The new cash is therefore in the close mark and does not
    earn the previous-close to current-close price move. Session return is
    ``equity_t / (equity_t-1 + contribution_t) - 1``. Two 1000 contributions
    with price 100 then 50 mark assets at 1000 then 1500: the index falls 25%,
    not 0% and not the 50% that would apply if the second contribution arrived
    after the close mark. A later pure contribution earns zero and cannot clear
    that underwater period.
    """

    index = 1.0
    previous_equity = 0.0
    previous_contributions = 0.0
    asset_peak = 0.0
    index_peak = 0.0
    index_values: list[float] = []
    for row in rows:
        equity = float(row["equity"])
        contributions = float(row["contributions"])
        external = contributions - previous_contributions
        base = previous_equity + external
        if base > 0.0:
            index *= equity / base
        asset_peak = max(asset_peak, equity)
        index_peak = max(index_peak, index)
        row["investment_index"] = float(index)
        row["asset_drawdown_pct"] = (
            0.0 if asset_peak <= 0.0 else float((1.0 - equity / asset_peak) * 100.0)
        )
        row["drawdown_pct"] = (
            0.0 if index_peak <= 0.0 else float((1.0 - index / index_peak) * 100.0)
        )
        index_values.append(float(index))
        previous_equity = equity
        previous_contributions = contributions
    return _max_drawdown(index_values), _max_underwater_days(
        rows,
        value_key="investment_index",
    )


def _xirr(cash_flows: Iterable[Mapping[str, object]]) -> float:
    totals: dict[pd.Timestamp, float] = {}
    for row in cash_flows:
        amount = float(row["amount"])
        if not math.isfinite(amount):
            return float("nan")
        date = pd.Timestamp(row["date"]).normalize()
        totals[date] = totals.get(date, 0.0) + amount
    aggregated = sorted(
        (date, amount) for date, amount in totals.items() if amount != 0.0
    )
    if len(aggregated) < 2:
        return float("nan")
    first_date = aggregated[0][0]
    if int((aggregated[-1][0] - first_date).days) <= 0:
        return float("nan")
    if not any(amount < 0.0 for _, amount in aggregated) or not any(
        amount > 0.0 for _, amount in aggregated
    ):
        return float("nan")
    if math.isclose(
        sum(amount for _, amount in aggregated),
        0.0,
        rel_tol=0.0,
        abs_tol=1e-8,
    ):
        return 0.0
    dated_values = tuple(
        ((date - first_date).days / 365.25, amount) for date, amount in aggregated
    )

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
                history = _signal_history_before_execution(signal_prices, date)
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
    investment_drawdown, investment_underwater_days = _annotate_investment_risk(equity_rows)
    equity_curve_rows = tuple(equity_rows)
    cash_flow_rows = tuple(cash_flows)
    return DcaResearchResult(
        name=name,
        terminal_value=float(terminal_value),
        cash=float(cash),
        shares=float(shares),
        invested=float(invested),
        contributions=float(contributions),
        max_drawdown=investment_drawdown,
        max_underwater_days=investment_underwater_days,
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


def _scheduled_decision_events(result: DcaResearchResult) -> tuple[Mapping[str, object], ...]:
    return tuple(result.trades) + tuple(result.skips)


def _scheduled_multiplier_values(result: DcaResearchResult) -> tuple[float, ...]:
    return tuple(
        float(event.get("multiplier", 0.0))
        for event in _scheduled_decision_events(result)
    )


def _scheduled_regimes_seen(result: DcaResearchResult) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(event.get("regime", "")).strip()
                for event in _scheduled_decision_events(result)
                if str(event.get("regime", "")).strip()
            }
        )
    )


def _scheduled_multiplier_count(
    result: DcaResearchResult,
    predicate: Callable[[float], bool],
) -> int:
    return sum(1 for value in _scheduled_multiplier_values(result) if predicate(value))


def _scheduled_multiplier_ratio(
    result: DcaResearchResult,
    predicate: Callable[[float], bool],
) -> float:
    events = _scheduled_decision_events(result)
    return (
        float(_scheduled_multiplier_count(result, predicate) / len(events))
        if events
        else 0.0
    )


def _average_scheduled_multiplier(result: DcaResearchResult) -> float:
    values = _scheduled_multiplier_values(result)
    return float(sum(values) / len(values)) if values else 0.0


def _min_scheduled_multiplier(result: DcaResearchResult) -> float:
    values = _scheduled_multiplier_values(result)
    return float(min(values)) if values else 0.0


def _max_scheduled_multiplier(result: DcaResearchResult) -> float:
    values = _scheduled_multiplier_values(result)
    return float(max(values)) if values else 0.0


def _performance_diagnoses(row: Mapping[str, object]) -> tuple[str, ...]:
    """Return explanatory, non-gating diagnostics for fixed-vs-smart results."""

    if bool(row.get("is_fixed_benchmark", False)):
        return ("fixed_benchmark",)

    relative_terminal = _float_value(row.get("relative_terminal_value_pct"))
    drawdown_delta = _float_value(row.get("max_drawdown_delta_pct_points"))
    deployment_delta = _float_value(row.get("deployment_rate_delta_pct_points"))
    skipped_ratio = _float_value(row.get("skipped_buy_ratio"))
    terminal_cash_ratio = _float_value(row.get("terminal_cash_ratio_pct"))
    scheduled_count = int(_float_value(row.get("scheduled_decision_count")))
    average_multiplier = _float_value(row.get("average_scheduled_multiplier"))

    diagnoses: list[str] = []
    if relative_terminal >= 0.0:
        diagnoses.append("terminal_edge_non_negative")
    else:
        diagnoses.append("terminal_underperformance_vs_fixed")
        if skipped_ratio > 0.05:
            diagnoses.append("skipped_buy_cash_drag")
        if deployment_delta < -1.0:
            diagnoses.append("lower_deployment_rate")
        if terminal_cash_ratio > 5.0:
            diagnoses.append("excess_terminal_cash")
        if scheduled_count > 0 and average_multiplier < 0.95:
            diagnoses.append("below_fixed_average_multiplier")
        if drawdown_delta <= -2.0:
            diagnoses.append("paid_terminal_value_for_drawdown_relief")
        if len(diagnoses) == 1:
            diagnoses.append("no_clear_capital_drag_signal")

    if drawdown_delta > 1.0:
        diagnoses.append("drawdown_worse_than_fixed")
    elif drawdown_delta < -1.0:
        diagnoses.append("drawdown_better_than_fixed")
    return tuple(dict.fromkeys(diagnoses))


def _dominant_csv_value(
    rows: Iterable[Mapping[str, object]],
    field: str,
) -> str:
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for row in rows:
        for value in str(row.get(field, "")).split(","):
            normalized = value.strip()
            if not normalized:
                continue
            first_seen.setdefault(normalized, len(first_seen))
            counts[normalized] = counts.get(normalized, 0) + 1
    if not counts:
        return ""
    return sorted(
        counts,
        key=lambda value: (-counts[value], first_seen[value], value),
    )[0]


def _float_value(value: object) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(result):
        return 0.0
    return result
