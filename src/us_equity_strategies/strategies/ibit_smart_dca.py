from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from us_equity_strategies.strategies.nasdaq_sp500_smart_dca import (
    SymbolIndicator,
    _coerce_float,
    _extract_close_series,
    _indicator_from_series,
    _is_in_execution_window,
    _localized_execution_window,
    _normalize_allocations,
    _normalize_investment_amount_mode,
    _normalize_symbol,
    _portfolio_cash,
    _portfolio_market_values,
    _coerce_bool,
    _resolve_base_investment_budget,
    _translate_with_fallback,
)


STATUS_ICON = "₿"
SIGNAL_SOURCE = "market_history+portfolio_snapshot"
DEFAULT_SIGNAL_SYMBOLS = ("BTC-USD",)
DEFAULT_TRADE_ALLOCATIONS = {"IBIT": 1.0}
DEFAULT_MANAGED_SYMBOLS = tuple(DEFAULT_TRADE_ALLOCATIONS)


def _localized_regime(regime: str, translator) -> str:
    labels = {
        "ordinary_dca": ("ordinary DCA", "普通定投"),
        "normal": ("normal", "正常"),
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
    rsi_values = [item.rsi14 for item in indicators if item.rsi14 is not None]
    avg_rsi = sum(rsi_values) / len(rsi_values) if rsi_values else float("nan")
    all_overbought = bool(rsi_values) and all(value >= overbought_rsi for value in rsi_values)

    metrics = {
        "avg_drawdown_252d": float(avg_drawdown),
        "avg_sma200_gap": float(avg_gap),
        "avg_rsi14": float(avg_rsi) if not pd.isna(avg_rsi) else float("nan"),
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
    return float(base_multiplier), "normal", metrics


def build_rebalance_plan(
    market_history,
    portfolio,
    *,
    as_of=None,
    signal_symbols=DEFAULT_SIGNAL_SYMBOLS,
    trade_allocations: Mapping[str, object] | None = None,
    managed_symbols=DEFAULT_MANAGED_SYMBOLS,
    base_investment_usd: float = 1000.0,
    max_investment_usd: float | None = None,
    cash_reserve_usd: float = 0.0,
    min_investment_usd: float = 5.0,
    investment_amount_mode: str = "fixed",
    available_cash_investment_ratio: float = 1.0,
    smart_multiplier_enabled: bool = False,
    cadence: str = "monthly",
    monthly_day: int = 25,
    monthly_window_calendar_days: int = 5,
    weekly_day: int = 4,
    weekly_window_calendar_days: int = 4,
    quarterly_months: object = (1, 4, 7, 10),
    quarterly_day: int = 25,
    quarterly_window_calendar_days: int = 5,
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
        weekly_window_calendar_days=weekly_window_calendar_days,
        quarterly_months=quarterly_months,
        quarterly_day=quarterly_day,
        quarterly_window_calendar_days=quarterly_window_calendar_days,
    )

    current_values = _portfolio_market_values(portfolio, strategy_symbols)
    available_cash = _portfolio_cash(portfolio)
    reserved_cash = max(0.0, _coerce_float(cash_reserve_usd))
    investable_cash = max(0.0, available_cash - reserved_cash)
    smart_enabled = _coerce_bool(smart_multiplier_enabled, default=False)

    indicators: list[SymbolIndicator] = []
    resolved_signal_symbols: list[str] = []
    for raw_symbol in signal_symbols or ():
        symbol = _normalize_symbol(raw_symbol)
        if not symbol:
            continue
        resolved_signal_symbols.append(symbol)
        if smart_enabled:
            history = market_history(broker_client, symbol)
            indicators.append(_indicator_from_series(symbol, _extract_close_series(history)))

    if smart_enabled:
        if not indicators:
            raise ValueError("signal_symbols must contain at least one valid symbol")
        regime_multiplier, regime, aggregate_metrics = _determine_multiplier(
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
            mild_pullback_multiplier=float(mild_pullback_multiplier),
            deep_pullback_multiplier=float(deep_pullback_multiplier),
            severe_pullback_multiplier=float(severe_pullback_multiplier),
            expensive_multiplier=float(expensive_multiplier),
            very_expensive_multiplier=float(very_expensive_multiplier),
            base_multiplier=float(base_multiplier),
        )
    else:
        regime_multiplier = 1.0
        regime = "ordinary_dca"
        aggregate_metrics = {
            "avg_drawdown_252d": float("nan"),
            "avg_sma200_gap": float("nan"),
            "avg_rsi14": float("nan"),
        }
    normalized_investment_amount_mode = _normalize_investment_amount_mode(investment_amount_mode)
    base_budget = _resolve_base_investment_budget(
        investment_amount_mode=normalized_investment_amount_mode,
        base_investment_usd=float(base_investment_usd),
    )
    multiplier = float(regime_multiplier if smart_enabled else 1.0)
    requested_investment = max(0.0, float(base_budget) * max(0.0, multiplier))
    if max_investment_usd is not None:
        requested_investment = min(requested_investment, max(0.0, float(max_investment_usd)))
    cash_capped = investable_cash < requested_investment
    cash_shortfall = max(0.0, requested_investment - investable_cash)
    planned_investment = requested_investment if not cash_capped else 0.0

    skip_reason = None
    actionable = True
    if not is_window:
        skip_reason = "outside_execution_window"
        actionable = False
    elif multiplier <= 0.0 or requested_investment <= 0.0:
        skip_reason = "valuation_too_expensive"
        actionable = False
    elif cash_capped or planned_investment < float(min_investment_usd):
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
    if smart_enabled:
        signal_desc = _translate_with_fallback(
            translator,
            "ibit_smart_dca_signal",
            fallback_en=(
                "IBIT Smart DCA {regime}: multiplier {multiplier}, "
                "planned buy ${planned_investment} from cash ${available_cash}"
            ),
            fallback_zh=(
                "IBIT 智能定投 {regime}: 倍数 {multiplier}，计划买入 ${planned_investment}，"
                "现金 ${available_cash}"
            ),
            regime=localized_regime,
            multiplier=f"{multiplier:.2f}x",
            planned_investment=f"{planned_investment:,.2f}",
            available_cash=f"{available_cash:,.2f}",
        )
    else:
        signal_desc = _translate_with_fallback(
            translator,
            "ibit_smart_dca_signal_ordinary",
            fallback_en="IBIT ordinary DCA: planned buy ${planned_investment} from cash ${available_cash}",
            fallback_zh="IBIT 普通定投：计划买入 ${planned_investment}，现金 ${available_cash}",
            planned_investment=f"{planned_investment:,.2f}",
            available_cash=f"{available_cash:,.2f}",
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
    if smart_enabled:
        status_desc = _translate_with_fallback(
            translator,
            "ibit_smart_dca_status",
            fallback_en="{window} | avg drawdown {avg_drawdown}, avg gap vs SMA200 {avg_sma200_gap}",
            fallback_zh="{window} | 平均回撤 {avg_drawdown}，相对 SMA200 均值 {avg_sma200_gap}",
            window=_localized_execution_window(window_text, translator),
            avg_drawdown=f"{aggregate_metrics['avg_drawdown_252d']:.1%}",
            avg_sma200_gap=f"{aggregate_metrics['avg_sma200_gap']:.1%}",
        )
    else:
        status_desc = _translate_with_fallback(
            translator,
            "ibit_smart_dca_status_ordinary",
            fallback_en="{window} | ordinary DCA without valuation multiplier",
            fallback_zh="{window} | 普通定投，不使用估值倍数",
            window=_localized_execution_window(window_text, translator),
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
        "signal_symbols": tuple(item.symbol for item in indicators) or tuple(resolved_signal_symbols),
        "signal_description": signal_desc,
        "status_description": status_desc,
        "regime": regime,
        "multiplier": float(multiplier),
        "regime_multiplier": float(regime_multiplier),
        "smart_multiplier_enabled": bool(smart_enabled),
        "investment_amount_mode": normalized_investment_amount_mode,
        "base_investment_usd": float(base_investment_usd),
        "base_investment_budget_usd": float(base_budget),
        "requested_investment_usd": float(requested_investment),
        "planned_investment_usd": float(planned_investment if actionable else 0.0),
        "cash_capped": bool(cash_capped),
        "cash_shortfall_usd": float(cash_shortfall),
        "available_cash": float(available_cash),
        "reserved_cash": float(reserved_cash),
        "investable_cash": float(investable_cash),
        "min_investment_usd": float(min_investment_usd),
        "execution_window": window_text,
        "in_execution_window": bool(is_window),
        "indicator_rows": indicator_rows,
        **aggregate_metrics,
    }
