from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import pandas as pd

from us_equity_strategies.strategies.nasdaq_sp500_smart_dca import (
    SymbolIndicator,
    _coerce_float,
    _extract_close_series,
    _indicator_from_series,
    _is_in_execution_window,
    _localized_execution_window,
    _normalize_allocations,
    _normalize_symbol,
    _portfolio_cash,
    _portfolio_market_values,
    _translate_with_fallback,
)


STATUS_ICON = "₿"
SIGNAL_SOURCE = "market_history+portfolio_snapshot"
DEFAULT_SIGNAL_SYMBOLS = ("IBIT",)
DEFAULT_TRADE_ALLOCATIONS = {"IBIT": 1.0}
DEFAULT_MANAGED_SYMBOLS = tuple(DEFAULT_TRADE_ALLOCATIONS)


def _portfolio_total_equity(portfolio: Any, *, current_values: Mapping[str, float], cash: float) -> float:
    value = getattr(portfolio, "total_equity", None)
    if value is None:
        metadata = getattr(portfolio, "metadata", {}) or {}
        if isinstance(metadata, Mapping):
            value = metadata.get("total_equity")
    total = _coerce_float(value, default=0.0)
    if total > 0.0:
        return total
    return max(0.0, sum(float(item) for item in current_values.values()) + float(cash))


def _clamp_ratio(value: object, *, default: float = 0.0, upper: float = 1.0) -> float:
    return max(0.0, min(float(upper), _coerce_float(value, default=default)))


def _dynamic_target_allocation_ratio(
    total_equity: float,
    *,
    base_ratio: float,
    growth_per_log10k: float,
    max_ratio: float,
) -> float:
    safe_equity = max(float(total_equity), 1.0)
    ratio = float(base_ratio) + float(growth_per_log10k) * math.log1p(safe_equity / 10000.0)
    return _clamp_ratio(ratio, upper=float(max_ratio))


def _resolve_target_allocation_ratio(
    total_equity: float,
    *,
    target_allocation_mode: str,
    target_allocation_ratio: float,
    target_allocation_base_ratio: float,
    target_allocation_growth_per_log10k: float,
    max_target_allocation_ratio: float,
) -> float:
    max_ratio = _clamp_ratio(max_target_allocation_ratio, default=0.10, upper=1.0)
    mode = str(target_allocation_mode or "dynamic").strip().lower()
    if mode == "fixed":
        return _clamp_ratio(target_allocation_ratio, default=0.05, upper=max_ratio)
    if mode != "dynamic":
        raise ValueError("target_allocation_mode must be 'dynamic' or 'fixed'")
    return _dynamic_target_allocation_ratio(
        total_equity,
        base_ratio=_clamp_ratio(target_allocation_base_ratio, default=0.03, upper=max_ratio),
        growth_per_log10k=max(0.0, float(target_allocation_growth_per_log10k)),
        max_ratio=max_ratio,
    )


def _localized_regime(regime: str, translator) -> str:
    labels = {
        "normal": ("normal", "正常"),
        "weak_trend": ("weak trend", "弱趋势"),
        "expensive": ("expensive", "偏贵"),
        "very_expensive_overbought": ("very expensive and overbought", "极贵且超买"),
        "mild_pullback": ("mild pullback", "温和回撤"),
        "deep_pullback": ("deep pullback", "深度回撤"),
        "severe_pullback": ("severe pullback", "严重回撤"),
    }
    fallback_en, fallback_zh = labels.get(regime, (regime, regime))
    return _translate_with_fallback(
        translator,
        f"ibit_smart_dca_regime_{regime}",
        fallback_en=fallback_en,
        fallback_zh=fallback_zh,
    )


def _localized_skip_reason(skip_reason: str, translator) -> str:
    labels = {
        "outside_execution_window": ("outside execution window", "不在执行窗口"),
        "valuation_too_expensive": ("valuation too expensive", "估值过贵"),
        "target_allocation_reached": ("target allocation reached", "目标仓位已满"),
        "insufficient_cash": ("insufficient cash", "可投资现金不足"),
    }
    fallback_en, fallback_zh = labels.get(skip_reason, (skip_reason, skip_reason))
    return _translate_with_fallback(
        translator,
        f"ibit_smart_dca_skip_reason_{skip_reason}",
        fallback_en=fallback_en,
        fallback_zh=fallback_zh,
    )


def _determine_multiplier(
    indicators: tuple[SymbolIndicator, ...],
    *,
    mild_drawdown_threshold: float,
    deep_drawdown_threshold: float,
    severe_drawdown_threshold: float,
    mild_discount_gap: float,
    deep_discount_gap: float,
    expensive_gap: float,
    very_expensive_gap: float,
    shallow_drawdown_threshold: float,
    overbought_rsi: float,
    weak_trend_multiplier: float,
    mild_pullback_multiplier: float,
    deep_pullback_multiplier: float,
    severe_pullback_multiplier: float,
    expensive_multiplier: float,
    very_expensive_multiplier: float,
    base_multiplier: float,
) -> tuple[float, str, dict[str, float]]:
    if not indicators:
        raise ValueError("at least one signal indicator is required")
    avg_drawdown = sum(item.drawdown_252d for item in indicators) / len(indicators)
    avg_gap = sum(item.sma200_gap for item in indicators) / len(indicators)
    trend_positive_ratio = sum(1.0 for item in indicators if item.trend_positive) / len(indicators)
    rsi_values = [item.rsi14 for item in indicators if item.rsi14 is not None]
    avg_rsi = sum(rsi_values) / len(rsi_values) if rsi_values else float("nan")
    all_overbought = bool(rsi_values) and all(value >= overbought_rsi for value in rsi_values)

    metrics = {
        "avg_drawdown_252d": float(avg_drawdown),
        "avg_sma200_gap": float(avg_gap),
        "avg_rsi14": float(avg_rsi) if not pd.isna(avg_rsi) else float("nan"),
        "trend_positive_ratio": float(trend_positive_ratio),
    }

    if avg_drawdown >= severe_drawdown_threshold:
        return float(severe_pullback_multiplier), "severe_pullback", metrics
    if avg_drawdown >= deep_drawdown_threshold or avg_gap <= -abs(float(deep_discount_gap)):
        return float(deep_pullback_multiplier), "deep_pullback", metrics
    if avg_drawdown >= mild_drawdown_threshold or avg_gap <= -abs(float(mild_discount_gap)):
        return float(mild_pullback_multiplier), "mild_pullback", metrics
    if avg_gap >= very_expensive_gap and avg_drawdown <= shallow_drawdown_threshold and all_overbought:
        return float(very_expensive_multiplier), "very_expensive_overbought", metrics
    if avg_gap >= expensive_gap and avg_drawdown <= shallow_drawdown_threshold:
        return float(expensive_multiplier), "expensive", metrics
    if trend_positive_ratio < 1.0:
        return float(weak_trend_multiplier), "weak_trend", metrics
    return float(base_multiplier), "normal", metrics


def build_rebalance_plan(
    market_history,
    portfolio,
    *,
    as_of=None,
    signal_symbols=DEFAULT_SIGNAL_SYMBOLS,
    trade_allocations: Mapping[str, object] | None = None,
    managed_symbols=DEFAULT_MANAGED_SYMBOLS,
    base_investment_usd: float = 250.0,
    max_investment_usd: float | None = 750.0,
    cash_reserve_usd: float = 50.0,
    min_investment_usd: float = 50.0,
    cadence: str = "monthly",
    monthly_day: int = 25,
    monthly_window_calendar_days: int = 5,
    weekly_day: int = 4,
    target_allocation_mode: str = "dynamic",
    target_allocation_ratio: float = 0.05,
    target_allocation_base_ratio: float = 0.03,
    target_allocation_growth_per_log10k: float = 0.02,
    max_target_allocation_ratio: float = 0.10,
    mild_drawdown_threshold: float = 0.12,
    deep_drawdown_threshold: float = 0.25,
    severe_drawdown_threshold: float = 0.40,
    mild_discount_gap: float = 0.08,
    deep_discount_gap: float = 0.18,
    expensive_gap: float = 0.30,
    very_expensive_gap: float = 0.60,
    shallow_drawdown_threshold: float = 0.05,
    overbought_rsi: float = 75.0,
    base_multiplier: float = 1.0,
    weak_trend_multiplier: float = 0.50,
    mild_pullback_multiplier: float = 1.25,
    deep_pullback_multiplier: float = 1.75,
    severe_pullback_multiplier: float = 2.50,
    expensive_multiplier: float = 0.50,
    very_expensive_multiplier: float = 0.0,
    broker_client=None,
    translator=None,
) -> dict[str, object]:
    allocations = _normalize_allocations(trade_allocations or DEFAULT_TRADE_ALLOCATIONS)
    strategy_symbols = tuple(dict.fromkeys((*(_normalize_symbol(s) for s in managed_symbols), *allocations)))
    is_window, window_text = _is_in_execution_window(
        as_of,
        cadence=cadence,
        monthly_day=monthly_day,
        monthly_window_calendar_days=monthly_window_calendar_days,
        weekly_day=weekly_day,
    )

    current_values = _portfolio_market_values(portfolio, strategy_symbols)
    available_cash = _portfolio_cash(portfolio)
    total_equity = _portfolio_total_equity(portfolio, current_values=current_values, cash=available_cash)
    target_allocation = _resolve_target_allocation_ratio(
        total_equity,
        target_allocation_mode=target_allocation_mode,
        target_allocation_ratio=float(target_allocation_ratio),
        target_allocation_base_ratio=float(target_allocation_base_ratio),
        target_allocation_growth_per_log10k=float(target_allocation_growth_per_log10k),
        max_target_allocation_ratio=float(max_target_allocation_ratio),
    )
    target_allocation_value = max(0.0, total_equity * target_allocation)
    current_strategy_value = sum(float(current_values.get(symbol, 0.0)) for symbol in strategy_symbols)
    remaining_capacity = max(0.0, target_allocation_value - current_strategy_value)

    reserved_cash = max(0.0, _coerce_float(cash_reserve_usd))
    investable_cash = max(0.0, available_cash - reserved_cash)

    indicators: list[SymbolIndicator] = []
    for raw_symbol in signal_symbols or ():
        symbol = _normalize_symbol(raw_symbol)
        if not symbol:
            continue
        history = market_history(broker_client, symbol)
        indicators.append(_indicator_from_series(symbol, _extract_close_series(history)))
    if not indicators:
        raise ValueError("signal_symbols must contain at least one valid symbol")

    multiplier, regime, aggregate_metrics = _determine_multiplier(
        tuple(indicators),
        mild_drawdown_threshold=float(mild_drawdown_threshold),
        deep_drawdown_threshold=float(deep_drawdown_threshold),
        severe_drawdown_threshold=float(severe_drawdown_threshold),
        mild_discount_gap=float(mild_discount_gap),
        deep_discount_gap=float(deep_discount_gap),
        expensive_gap=float(expensive_gap),
        very_expensive_gap=float(very_expensive_gap),
        shallow_drawdown_threshold=float(shallow_drawdown_threshold),
        overbought_rsi=float(overbought_rsi),
        weak_trend_multiplier=float(weak_trend_multiplier),
        mild_pullback_multiplier=float(mild_pullback_multiplier),
        deep_pullback_multiplier=float(deep_pullback_multiplier),
        severe_pullback_multiplier=float(severe_pullback_multiplier),
        expensive_multiplier=float(expensive_multiplier),
        very_expensive_multiplier=float(very_expensive_multiplier),
        base_multiplier=float(base_multiplier),
    )
    requested_investment = max(0.0, float(base_investment_usd) * max(0.0, multiplier))
    if max_investment_usd is not None:
        requested_investment = min(requested_investment, max(0.0, float(max_investment_usd)))
    cash_limited_investment = min(requested_investment, investable_cash)
    planned_investment = min(cash_limited_investment, remaining_capacity)
    cash_capped = cash_limited_investment < requested_investment
    target_capped = planned_investment < cash_limited_investment
    cash_shortfall = max(0.0, min(requested_investment, remaining_capacity) - planned_investment)
    target_shortfall = max(0.0, requested_investment - min(requested_investment, remaining_capacity))

    skip_reason = None
    actionable = True
    if not is_window:
        skip_reason = "outside_execution_window"
        actionable = False
    elif multiplier <= 0.0 or requested_investment <= 0.0:
        skip_reason = "valuation_too_expensive"
        actionable = False
    elif remaining_capacity < float(min_investment_usd):
        skip_reason = "target_allocation_reached"
        actionable = False
    elif planned_investment < float(min_investment_usd):
        skip_reason = "insufficient_cash"
        actionable = False

    target_values = dict(current_values)
    if actionable:
        for symbol, weight in allocations.items():
            target_values[symbol] = target_values.get(symbol, 0.0) + planned_investment * weight

    indicator_rows = tuple(
        {
            "symbol": item.symbol,
            "price": item.price,
            "sma50": item.sma50,
            "sma200": item.sma200,
            "high252": item.high252,
            "drawdown_252d": item.drawdown_252d,
            "sma200_gap": item.sma200_gap,
            "rsi14": item.rsi14,
            "trend_positive": item.trend_positive,
        }
        for item in indicators
    )
    localized_regime = _localized_regime(regime, translator)
    signal_desc = _translate_with_fallback(
        translator,
        "ibit_smart_dca_signal",
        fallback_en=(
            "IBIT Smart DCA {regime}: multiplier {multiplier}, "
            "planned buy ${planned_investment}, target sleeve {target_allocation}"
        ),
        fallback_zh=(
            "IBIT 智能定投 {regime}: 倍数 {multiplier}，计划买入 ${planned_investment}，"
            "目标仓位 {target_allocation}"
        ),
        regime=localized_regime,
        multiplier=f"{multiplier:.2f}x",
        planned_investment=f"{planned_investment:,.2f}",
        target_allocation=f"{target_allocation:.1%}",
    )
    if cash_capped and planned_investment > 0.0:
        signal_desc = _translate_with_fallback(
            translator,
            "ibit_smart_dca_cash_capped",
            fallback_en="{signal} | cash capped from requested ${requested_investment}",
            fallback_zh="{signal} | 因现金限制，低于请求金额 ${requested_investment}",
            signal=signal_desc,
            requested_investment=f"{requested_investment:,.2f}",
        )
    if target_capped and planned_investment > 0.0:
        signal_desc = _translate_with_fallback(
            translator,
            "ibit_smart_dca_target_capped",
            fallback_en="{signal} | target capped by remaining sleeve capacity ${remaining_capacity}",
            fallback_zh="{signal} | 因目标仓位上限，剩余容量 ${remaining_capacity}",
            signal=signal_desc,
            remaining_capacity=f"{remaining_capacity:,.2f}",
        )
    status_desc = _translate_with_fallback(
        translator,
        "ibit_smart_dca_status",
        fallback_en=(
            "{window} | avg drawdown {avg_drawdown}, avg gap vs SMA200 {avg_sma200_gap}, "
            "IBIT sleeve {current_value}/{target_value}"
        ),
        fallback_zh=(
            "{window} | 平均回撤 {avg_drawdown}，相对 SMA200 均值 {avg_sma200_gap}，"
            "IBIT 仓位 {current_value}/{target_value}"
        ),
        window=_localized_execution_window(window_text, translator),
        avg_drawdown=f"{aggregate_metrics['avg_drawdown_252d']:.1%}",
        avg_sma200_gap=f"{aggregate_metrics['avg_sma200_gap']:.1%}",
        current_value=f"${current_strategy_value:,.2f}",
        target_value=f"${target_allocation_value:,.2f}",
    )
    if skip_reason:
        signal_desc = _translate_with_fallback(
            translator,
            "ibit_smart_dca_skip",
            fallback_en="{signal} | skip: {skip_reason}",
            fallback_zh="{signal} | 跳过：{skip_reason}",
            signal=signal_desc,
            skip_reason=_localized_skip_reason(skip_reason, translator),
        )

    return {
        "actionable": actionable,
        "skip_reason": skip_reason,
        "target_values": target_values if actionable else {},
        "strategy_symbols": strategy_symbols,
        "managed_symbols": strategy_symbols,
        "trade_allocations": allocations,
        "signal_symbols": tuple(item.symbol for item in indicators),
        "signal_description": signal_desc,
        "status_description": status_desc,
        "regime": regime,
        "multiplier": float(multiplier),
        "base_investment_usd": float(base_investment_usd),
        "requested_investment_usd": float(requested_investment),
        "planned_investment_usd": float(planned_investment if actionable else 0.0),
        "cash_capped": bool(cash_capped),
        "target_capped": bool(target_capped),
        "cash_shortfall_usd": float(cash_shortfall),
        "target_shortfall_usd": float(target_shortfall),
        "available_cash": float(available_cash),
        "reserved_cash": float(reserved_cash),
        "investable_cash": float(investable_cash),
        "min_investment_usd": float(min_investment_usd),
        "total_equity": float(total_equity),
        "target_allocation_mode": str(target_allocation_mode or "dynamic").strip().lower(),
        "target_allocation_ratio": float(target_allocation),
        "target_allocation_value_usd": float(target_allocation_value),
        "current_strategy_value_usd": float(current_strategy_value),
        "remaining_capacity_usd": float(remaining_capacity),
        "execution_window": window_text,
        "in_execution_window": bool(is_window),
        "indicator_rows": indicator_rows,
        **aggregate_metrics,
    }
