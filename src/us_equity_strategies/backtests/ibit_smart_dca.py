from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from quant_platform_kit.common.models import PortfolioSnapshot, Position

from us_equity_strategies.strategies.ibit_smart_dca import build_rebalance_plan


@dataclass(frozen=True)
class DcaBacktestResult:
    name: str
    terminal_value: float
    cash: float
    shares: float
    contributions: float
    invested: float
    max_drawdown: float
    trades: tuple[dict[str, object], ...]


def _close_series(values: Any) -> pd.Series:
    if isinstance(values, pd.Series):
        series = values.copy()
    elif isinstance(values, pd.DataFrame):
        series = values["close"] if "close" in values.columns else values.iloc[:, 0]
    else:
        series = pd.Series(values)
    series = pd.to_numeric(series, errors="coerce").dropna().astype(float)
    series = series[series > 0.0]
    series.index = pd.to_datetime(series.index).tz_localize(None).normalize()
    return series.sort_index()


def _max_drawdown(values: list[float]) -> float:
    peak = 0.0
    max_dd = 0.0
    for value in values:
        peak = max(peak, float(value))
        if peak <= 0.0:
            continue
        max_dd = max(max_dd, 1.0 - float(value) / peak)
    return float(max_dd)


def _portfolio(as_of: pd.Timestamp, *, cash: float, shares: float, price: float) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        as_of=as_of.to_pydatetime(),
        total_equity=float(cash) + float(shares) * float(price),
        buying_power=float(cash),
        cash_balance=float(cash),
        positions=(Position(symbol="IBIT", quantity=float(shares), market_value=float(shares) * float(price)),),
        metadata={"account_hash": "backtest"},
    )


def _run_path(
    *,
    name: str,
    signal_prices: pd.Series,
    trade_prices: pd.Series,
    monthly_contribution_usd: float,
    start_date: pd.Timestamp | None,
    plan_overrides: dict[str, object],
) -> DcaBacktestResult:
    dates = sorted(set(signal_prices.index).intersection(set(trade_prices.index)))
    if start_date is not None:
        start = pd.Timestamp(start_date).normalize()
        dates = [date for date in dates if pd.Timestamp(date).normalize() >= start]
    cash = 0.0
    shares = 0.0
    contributions = 0.0
    invested = 0.0
    last_contribution_month = ""
    traded_months: set[str] = set()
    equity_curve: list[float] = []
    trades: list[dict[str, object]] = []
    uses_smart_multiplier = plan_overrides.get("smart_multiplier_enabled", True) is not False

    for raw_date in dates:
        date = pd.Timestamp(raw_date).normalize()
        price = float(trade_prices.loc[date])
        month_key = date.strftime("%Y-%m")
        if month_key != last_contribution_month:
            cash += float(monthly_contribution_usd)
            contributions += float(monthly_contribution_usd)
            last_contribution_month = month_key

        if month_key not in traded_months:
            history_to_date = signal_prices.loc[signal_prices.index <= date]
            if len(history_to_date) >= 252 or not uses_smart_multiplier:
                plan = build_rebalance_plan(
                    lambda _client, _symbol: history_to_date,
                    _portfolio(date, cash=cash, shares=shares, price=price),
                    as_of=date,
                    min_investment_usd=0.0,
                    cash_reserve_usd=0.0,
                    **plan_overrides,
                )
                if plan["actionable"]:
                    current_value = shares * price
                    target_value = float(plan["target_values"].get("IBIT", current_value))
                    buy_value = max(0.0, min(cash, target_value - current_value))
                    if buy_value > 0.0:
                        bought_shares = buy_value / price
                        shares += bought_shares
                        cash -= buy_value
                        invested += buy_value
                        traded_months.add(month_key)
                        trades.append(
                            {
                                "date": date.date().isoformat(),
                                "name": name,
                                "regime": plan["regime"],
                                "multiplier": float(plan["multiplier"]),
                                "buy_value": float(buy_value),
                                "price": float(price),
                                "shares": float(bought_shares),
                            }
                        )
                elif plan["skip_reason"] != "outside_execution_window":
                    traded_months.add(month_key)

        equity_curve.append(cash + shares * price)

    final_price = float(trade_prices.loc[dates[-1]]) if dates else 0.0
    terminal_value = cash + shares * final_price
    return DcaBacktestResult(
        name=name,
        terminal_value=float(terminal_value),
        cash=float(cash),
        shares=float(shares),
        contributions=float(contributions),
        invested=float(invested),
        max_drawdown=_max_drawdown(equity_curve),
        trades=tuple(trades),
    )


def compare_smart_vs_fixed_dca(
    *,
    signal_prices,
    trade_prices,
    monthly_contribution_usd: float = 1000.0,
    start_date: object | None = None,
    align_start_after_smart_warmup: bool = True,
    plan_overrides: dict[str, object] | None = None,
) -> dict[str, DcaBacktestResult]:
    signal_series = _close_series(signal_prices)
    trade_series = _close_series(trade_prices)
    overrides = dict(plan_overrides or {})
    common_start = pd.Timestamp(start_date).normalize() if start_date is not None else None
    if align_start_after_smart_warmup and len(signal_series) >= 252:
        warmup_start = pd.Timestamp(signal_series.index[251]).normalize()
        common_start = max(common_start, warmup_start) if common_start is not None else warmup_start
    smart = _run_path(
        name="smart",
        signal_prices=signal_series,
        trade_prices=trade_series,
        monthly_contribution_usd=monthly_contribution_usd,
        start_date=common_start,
        plan_overrides={**overrides, "smart_multiplier_enabled": True},
    )
    fixed = _run_path(
        name="fixed",
        signal_prices=signal_series,
        trade_prices=trade_series,
        monthly_contribution_usd=monthly_contribution_usd,
        start_date=common_start,
        plan_overrides={**overrides, "smart_multiplier_enabled": False},
    )
    return {"smart": smart, "fixed": fixed}
