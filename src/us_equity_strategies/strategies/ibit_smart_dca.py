from __future__ import annotations

from collections.abc import Mapping
import math

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
SIGNAL_SOURCE = "derived_indicators/market_history+portfolio_snapshot"
DEFAULT_SIGNAL_SYMBOLS = ("BTC-USD",)
DEFAULT_TRADE_ALLOCATIONS = {"IBIT": 1.0}
DEFAULT_MANAGED_SYMBOLS = tuple(DEFAULT_TRADE_ALLOCATIONS)
BITCOIN_GENESIS_DATE = pd.Timestamp("2009-01-03")


def _localized_regime(regime: str, translator) -> str:
    labels = {
        "ordinary_dca": ("ordinary DCA", "普通定投"),
        "normal": ("normal", "正常"),
        "expensive": ("expensive", "偏贵"),
        "very_expensive_overbought": ("very expensive and overbought", "极贵且超买"),
        "mild_pullback": ("mild pullback", "温和回撤"),
        "deep_pullback": ("deep pullback", "深度回撤"),
        "severe_pullback": ("severe pullback", "严重回撤"),
        "ahr999_bottom": ("AHR999 bottom zone", "AHR999 底部区"),
        "ahr999_accumulation": ("AHR999 accumulation zone", "AHR999 囤币区"),
        "ahr999_dca": ("AHR999 DCA zone", "AHR999 定投区"),
        "ahr999_expensive": ("AHR999 expensive zone", "AHR999 偏贵区"),
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


def _payload_numeric(payload: Mapping[str, object], *keys: str) -> float:
    lowered = {str(key).strip().lower(): value for key, value in payload.items()}
    for key in keys:
        value = lowered.get(key.lower())
        numeric = _coerce_float(value, default=float("nan"))
        if not pd.isna(numeric):
            return numeric
    return float("nan")


def _resolve_indicator_payload(
    indicator_snapshot: Mapping[str, object] | None,
    symbol: str,
) -> Mapping[str, object] | None:
    if not isinstance(indicator_snapshot, Mapping):
        return None
    if any(str(key).lower() in {"ahr999", "ahr_999", "ahr999_gma", "close", "price"} for key in indicator_snapshot):
        return indicator_snapshot

    candidates = {
        symbol,
        symbol.upper(),
        symbol.replace("-", ""),
        symbol.replace("-", "").upper(),
        "BTC",
        "BTC-USD",
        "BTCUSDT",
    }
    for key in candidates:
        value = indicator_snapshot.get(key)
        if isinstance(value, Mapping):
            return value
    normalized_snapshot = {
        str(key).strip().upper().replace("-", ""): value
        for key, value in indicator_snapshot.items()
    }
    for key in candidates:
        value = normalized_snapshot.get(key.upper().replace("-", ""))
        if isinstance(value, Mapping):
            return value
    return None


def _indicator_from_payload(symbol: str, payload: Mapping[str, object]) -> SymbolIndicator | None:
    price = _payload_numeric(payload, "close", "price", "last", "last_price")
    sma200 = _payload_numeric(payload, "sma200", "ma200", "sma_200")
    high252 = _payload_numeric(payload, "high252", "high_252", "high252d", "high_252d")
    if pd.isna(price) or pd.isna(sma200) or pd.isna(high252):
        return None
    sma50 = _payload_numeric(payload, "sma50", "ma50", "sma_50")
    if pd.isna(sma50):
        sma50 = sma200
    drawdown = _payload_numeric(payload, "drawdown_252d", "drawdown252", "drawdown")
    if pd.isna(drawdown):
        drawdown = 0.0 if high252 <= 0.0 else max(0.0, 1.0 - price / high252)
    sma_gap = _payload_numeric(payload, "sma200_gap", "gap_vs_sma200", "price_vs_sma200")
    if pd.isna(sma_gap):
        sma_gap = 0.0 if sma200 <= 0.0 else price / sma200 - 1.0
    rsi14 = _payload_numeric(payload, "rsi14", "rsi_14", "rsi")
    return SymbolIndicator(
        symbol=symbol,
        price=float(price),
        sma50=float(sma50),
        sma200=float(sma200),
        high252=float(high252),
        drawdown_252d=float(drawdown),
        sma200_gap=float(sma_gap),
        rsi14=None if pd.isna(rsi14) else float(rsi14),
        trend_positive=bool(price >= sma200 and sma50 >= sma200),
    )


def _bitcoin_age_estimate_price(as_of: object) -> float:
    timestamp = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.utcnow()
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("UTC").tz_localize(None)
    age_days = max(1, int((timestamp.normalize() - BITCOIN_GENESIS_DATE).days))
    return float(10 ** (5.84 * math.log10(age_days) - 17.01))


def _cycle_metrics_from_payload(
    payload: Mapping[str, object] | None,
    *,
    as_of: object,
) -> dict[str, float | str]:
    if not isinstance(payload, Mapping):
        return {}
    ahr999_gma = _payload_numeric(payload, "ahr999_gma", "ahr999", "ahr_999", "ahr999_index")
    ahr999_sma = _payload_numeric(payload, "ahr999_sma", "ahr999_sma200")
    mayer = _payload_numeric(payload, "mayer_multiple", "mayer", "price_sma200_ratio")
    price = _payload_numeric(payload, "close", "price", "last", "last_price")
    sma200 = _payload_numeric(payload, "sma200", "ma200", "sma_200")
    estimate_price = _payload_numeric(payload, "ahr999_estimate_price", "estimate_price", "growth_estimate_price")
    if pd.isna(estimate_price):
        estimate_price = _bitcoin_age_estimate_price(as_of)
    if pd.isna(mayer) and not pd.isna(price) and not pd.isna(sma200) and sma200 > 0.0:
        mayer = price / sma200
    if pd.isna(ahr999_sma) and not pd.isna(price) and not pd.isna(sma200) and sma200 > 0.0 and estimate_price > 0.0:
        ahr999_sma = (price / sma200) * (price / estimate_price)
    if pd.isna(ahr999_gma):
        ahr999_gma = ahr999_sma
    if pd.isna(ahr999_gma):
        return {}
    metrics: dict[str, float | str] = {}
    metrics["ahr999"] = float(ahr999_gma)
    if not pd.isna(ahr999_sma):
        metrics["ahr999_sma"] = float(ahr999_sma)
    if not pd.isna(mayer):
        metrics["mayer_multiple"] = float(mayer)
    if not pd.isna(estimate_price):
        metrics["ahr999_estimate_price"] = float(estimate_price)
    if metrics:
        metrics["cycle_indicator_source"] = "derived_indicators"
    return metrics


def _cycle_metrics_from_series(series: pd.Series, *, as_of: object) -> dict[str, float | str]:
    values = pd.to_numeric(series, errors="coerce").dropna()
    values = values[values > 0.0]
    if len(values) < 200:
        return {}
    latest = float(values.iloc[-1])
    sma200 = float(values.iloc[-200:].mean())
    gma200 = float(math.exp(sum(math.log(float(value)) for value in values.iloc[-200:]) / 200.0))
    estimate_price = _bitcoin_age_estimate_price(as_of)
    if sma200 <= 0.0 or gma200 <= 0.0 or estimate_price <= 0.0:
        return {}
    return {
        "ahr999": float((latest / gma200) * (latest / estimate_price)),
        "ahr999_sma": float((latest / sma200) * (latest / estimate_price)),
        "mayer_multiple": float(latest / sma200),
        "ahr999_estimate_price": float(estimate_price),
        "cycle_indicator_source": "market_history",
    }


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


def _determine_cycle_multiplier(
    cycle_metrics: Mapping[str, object],
    *,
    ahr999_bottom_threshold: float,
    ahr999_accumulation_threshold: float,
    ahr999_dca_threshold: float,
    ahr999_bottom_multiplier: float,
    ahr999_accumulation_multiplier: float,
    ahr999_dca_multiplier: float,
    ahr999_expensive_multiplier: float,
    base_multiplier: float,
) -> tuple[float, str]:
    ahr999 = _coerce_float(cycle_metrics.get("ahr999"), default=float("nan"))
    if pd.isna(ahr999):
        return float(base_multiplier), "normal"
    if ahr999 <= float(ahr999_bottom_threshold):
        return float(ahr999_bottom_multiplier), "ahr999_bottom"
    if ahr999 <= float(ahr999_accumulation_threshold):
        return float(ahr999_accumulation_multiplier), "ahr999_accumulation"
    if ahr999 <= float(ahr999_dca_threshold):
        return float(ahr999_dca_multiplier), "ahr999_dca"
    return float(ahr999_expensive_multiplier), "ahr999_expensive"


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
    mild_pullback_multiplier: float = 1.50,
    deep_pullback_multiplier: float = 2.25,
    severe_pullback_multiplier: float = 3.0,
    expensive_multiplier: float = 1.0,
    very_expensive_multiplier: float = 1.0,
    cycle_indicator_enabled: bool = True,
    ahr999_bottom_threshold: float = 0.45,
    ahr999_accumulation_threshold: float = 0.80,
    ahr999_dca_threshold: float = 1.20,
    ahr999_bottom_multiplier: float = 3.0,
    ahr999_accumulation_multiplier: float = 2.25,
    ahr999_dca_multiplier: float = 1.50,
    ahr999_expensive_multiplier: float = 0.0,
    crypto_indicator_snapshot: Mapping[str, object] | None = None,
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
    cycle_metrics: dict[str, float | str] = {}
    resolved_signal_symbols: list[str] = []
    for raw_symbol in signal_symbols or ():
        symbol = _normalize_symbol(raw_symbol)
        if not symbol:
            continue
        resolved_signal_symbols.append(symbol)
        if smart_enabled:
            payload = _resolve_indicator_payload(crypto_indicator_snapshot, symbol)
            if not cycle_metrics and _coerce_bool(cycle_indicator_enabled, default=True):
                cycle_metrics = _cycle_metrics_from_payload(payload, as_of=as_of)
            payload_indicator = _indicator_from_payload(symbol, payload) if payload else None
            if payload_indicator is not None:
                indicators.append(payload_indicator)
                continue
            if cycle_metrics:
                continue
            history = market_history(broker_client, symbol)
            series = _extract_close_series(history)
            indicators.append(_indicator_from_series(symbol, series))
            if not cycle_metrics and _coerce_bool(cycle_indicator_enabled, default=True):
                cycle_metrics = _cycle_metrics_from_series(series, as_of=as_of)

    if smart_enabled:
        if cycle_metrics:
            regime_multiplier, regime = _determine_cycle_multiplier(
                cycle_metrics,
                ahr999_bottom_threshold=float(ahr999_bottom_threshold),
                ahr999_accumulation_threshold=float(ahr999_accumulation_threshold),
                ahr999_dca_threshold=float(ahr999_dca_threshold),
                ahr999_bottom_multiplier=float(ahr999_bottom_multiplier),
                ahr999_accumulation_multiplier=float(ahr999_accumulation_multiplier),
                ahr999_dca_multiplier=float(ahr999_dca_multiplier),
                ahr999_expensive_multiplier=float(ahr999_expensive_multiplier),
                base_multiplier=float(base_multiplier),
            )
            if indicators:
                aggregate_metrics = {
                    "avg_drawdown_252d": sum(item.drawdown_252d for item in indicators) / len(indicators),
                    "avg_sma200_gap": sum(item.sma200_gap for item in indicators) / len(indicators),
                    "avg_rsi14": float("nan"),
                }
                rsi_values = [item.rsi14 for item in indicators if item.rsi14 is not None]
                if rsi_values:
                    aggregate_metrics["avg_rsi14"] = sum(rsi_values) / len(rsi_values)
            else:
                aggregate_metrics = {
                    "avg_drawdown_252d": float("nan"),
                    "avg_sma200_gap": float("nan"),
                    "avg_rsi14": float("nan"),
                }
        elif not indicators:
            raise ValueError("signal_symbols must contain at least one valid symbol")
        else:
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
    aggregate_metrics.update(
        {
            "ahr999": float(_coerce_float(cycle_metrics.get("ahr999"), default=float("nan"))),
            "ahr999_sma": float(_coerce_float(cycle_metrics.get("ahr999_sma"), default=float("nan"))),
            "mayer_multiple": float(_coerce_float(cycle_metrics.get("mayer_multiple"), default=float("nan"))),
            "cycle_indicator_source": str(cycle_metrics.get("cycle_indicator_source", "none")),
        }
    )
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
        cycle_text = ""
        if not pd.isna(aggregate_metrics["ahr999"]):
            cycle_text = f", AHR999 {aggregate_metrics['ahr999']:.2f}"
        status_desc = _translate_with_fallback(
            translator,
            "ibit_smart_dca_status",
            fallback_en="{window} | avg drawdown {avg_drawdown}, avg gap vs SMA200 {avg_sma200_gap}{cycle_text}",
            fallback_zh="{window} | 平均回撤 {avg_drawdown}，相对 SMA200 均值 {avg_sma200_gap}{cycle_text}",
            window=_localized_execution_window(window_text, translator),
            avg_drawdown=f"{aggregate_metrics['avg_drawdown_252d']:.1%}",
            avg_sma200_gap=f"{aggregate_metrics['avg_sma200_gap']:.1%}",
            cycle_text=cycle_text,
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
