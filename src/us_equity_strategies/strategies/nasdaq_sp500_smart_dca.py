from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd


STATUS_ICON = "🧺"
SIGNAL_SOURCE = "derived_indicators/market_history+portfolio_snapshot"
DEFAULT_SIGNAL_SYMBOLS = ("QQQ", "SPY")
DEFAULT_TRADE_ALLOCATIONS = {"QQQM": 0.50, "SPLG": 0.50}
DEFAULT_MANAGED_SYMBOLS = tuple(DEFAULT_TRADE_ALLOCATIONS)


@dataclass(frozen=True)
class SymbolIndicator:
    symbol: str
    price: float
    sma50: float
    sma200: float
    high252: float
    drawdown_252d: float
    sma200_gap: float
    rsi14: float | None
    trend_positive: bool


def _translator_uses_zh(translator) -> bool:
    if translator is None:
        return False
    try:
        sample = str(translator("no_trades"))
    except Exception:
        return False
    return any("\u4e00" <= char <= "\u9fff" for char in sample)


def _translate_with_fallback(
    translator,
    key: str,
    *,
    fallback_en: str,
    fallback_zh: str | None = None,
    **kwargs,
) -> str:
    if translator is not None:
        rendered = translator(key, **kwargs)
        rendered_text = str(rendered)
        if rendered_text != key and not rendered_text.startswith(f"{key}("):
            return str(rendered)
    if fallback_zh is not None and _translator_uses_zh(translator):
        return fallback_zh.format(**kwargs)
    return fallback_en.format(**kwargs)


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
        f"smart_dca_regime_{regime}",
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
        f"smart_dca_skip_reason_{skip_reason}",
        fallback_en=fallback_en,
        fallback_zh=fallback_zh,
    )


def _localized_execution_window(window_text: str, translator) -> str:
    if window_text.startswith("weekly_day="):
        pieces = dict(part.split("=", 1) for part in window_text.split() if "=" in part)
        return _translate_with_fallback(
            translator,
            "smart_dca_execution_window_weekly",
            fallback_en="weekly_day={weekly_day} window_calendar_days={window}",
            fallback_zh="每周执行日={weekly_day}，窗口 {window} 个自然日",
            weekly_day=pieces.get("weekly_day", ""),
            window=pieces.get("window_calendar_days", ""),
        )
    if window_text.startswith("monthly_day="):
        pieces = dict(part.split("=", 1) for part in window_text.split() if "=" in part)
        return _translate_with_fallback(
            translator,
            "smart_dca_execution_window_monthly",
            fallback_en="monthly_day={monthly_day} window_calendar_days={window}",
            fallback_zh="每月第 {monthly_day} 日起，窗口 {window} 个自然日",
            monthly_day=pieces.get("monthly_day", ""),
            window=pieces.get("window_calendar_days", ""),
        )
    if window_text.startswith("quarterly_months="):
        pieces = dict(part.split("=", 1) for part in window_text.split() if "=" in part)
        return _translate_with_fallback(
            translator,
            "smart_dca_execution_window_quarterly",
            fallback_en="quarterly_months={months} quarterly_day={day} window_calendar_days={window}",
            fallback_zh="季度月份={months}，每季第 {day} 日起，窗口 {window} 个自然日",
            months=pieces.get("quarterly_months", ""),
            day=pieces.get("quarterly_day", ""),
            window=pieces.get("window_calendar_days", ""),
        )
    return window_text


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if pd.isna(numeric):
        return default
    return numeric


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return bool(value)


def _normalize_symbol(symbol: object) -> str:
    return str(symbol or "").strip().upper().removesuffix(".US")


def _normalize_allocations(raw_allocations: Mapping[str, object] | None) -> dict[str, float]:
    allocations: dict[str, float] = {}
    for symbol, value in dict(raw_allocations or {}).items():
        normalized = _normalize_symbol(symbol)
        weight = _coerce_float(value)
        if normalized and weight > 0.0:
            allocations[normalized] = weight
    total = sum(allocations.values())
    if total <= 0.0:
        raise ValueError("trade_allocations must contain at least one positive allocation")
    return {symbol: weight / total for symbol, weight in allocations.items()}


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
    if any(
        str(key).lower()
        in {
            "close",
            "price",
            "sma200",
            "sma_200",
            "sma200_gap",
            "rsi14",
            "rsi_14",
        }
        for key in indicator_snapshot
    ):
        return indicator_snapshot

    candidates = {
        symbol,
        symbol.upper(),
        symbol.removesuffix(".US"),
        symbol.upper().removesuffix(".US"),
    }
    for key in candidates:
        value = indicator_snapshot.get(key)
        if isinstance(value, Mapping):
            return value
    normalized_snapshot = {
        _normalize_symbol(key): value
        for key, value in indicator_snapshot.items()
    }
    value = normalized_snapshot.get(_normalize_symbol(symbol))
    return value if isinstance(value, Mapping) else None


def _indicator_from_payload(symbol: str, payload: Mapping[str, object]) -> SymbolIndicator | None:
    price = _payload_numeric(payload, "close", "price", "last", "last_price")
    sma200 = _payload_numeric(payload, "sma200", "ma200", "sma_200")
    high252 = _payload_numeric(payload, "high252", "high_252", "high252d", "high_252d")
    if pd.isna(price) or pd.isna(sma200):
        return None
    sma50 = _payload_numeric(payload, "sma50", "ma50", "sma_50")
    if pd.isna(sma50):
        sma50 = sma200
    if pd.isna(high252):
        high252 = max(price, sma200)
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


def _extract_close_series(price_history: Any) -> pd.Series:
    if isinstance(price_history, pd.DataFrame):
        if price_history.empty:
            return pd.Series(dtype=float)
        if "close" in price_history.columns:
            raw = price_history["close"]
        else:
            raw = price_history.iloc[:, 0]
        return pd.to_numeric(raw, errors="coerce").dropna().astype(float)
    if isinstance(price_history, pd.Series):
        return pd.to_numeric(price_history, errors="coerce").dropna().astype(float)

    values: list[float] = []
    try:
        iterator = iter(price_history or ())
    except TypeError:
        iterator = iter((price_history,))
    for item in iterator:
        if isinstance(item, Mapping):
            candidate = item.get("close")
        else:
            candidate = getattr(item, "close", item)
        value = _coerce_float(candidate, default=float("nan"))
        if not pd.isna(value):
            values.append(value)
    return pd.Series(values, dtype=float).dropna()


def _latest(series: pd.Series) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna()
    values = values[values > 0.0]
    if values.empty:
        return float("nan")
    return float(values.iloc[-1])


def _sma(series: pd.Series, window: int) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if len(values) < window:
        return float("nan")
    return float(values.iloc[-window:].mean())


def _rolling_high(series: pd.Series, window: int) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if len(values) < window:
        return float("nan")
    return float(values.iloc[-window:].max())


def _rsi(series: pd.Series, window: int = 14) -> float | None:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if len(values) <= window:
        return None
    delta = values.diff().dropna()
    gains = delta.clip(lower=0.0)
    losses = -delta.clip(upper=0.0)
    avg_gain = gains.iloc[-window:].mean()
    avg_loss = losses.iloc[-window:].mean()
    if pd.isna(avg_gain) or pd.isna(avg_loss):
        return None
    if avg_loss <= 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


def _indicator_from_series(symbol: str, series: pd.Series) -> SymbolIndicator:
    price = _latest(series)
    sma50 = _sma(series, 50)
    sma200 = _sma(series, 200)
    high252 = _rolling_high(series, 252)
    if pd.isna(price) or pd.isna(sma50) or pd.isna(sma200) or pd.isna(high252):
        raise ValueError(f"{symbol} requires at least 252 valid daily closes")
    drawdown = 0.0 if high252 <= 0.0 else max(0.0, 1.0 - price / high252)
    sma_gap = 0.0 if sma200 <= 0.0 else price / sma200 - 1.0
    return SymbolIndicator(
        symbol=symbol,
        price=price,
        sma50=sma50,
        sma200=sma200,
        high252=high252,
        drawdown_252d=drawdown,
        sma200_gap=sma_gap,
        rsi14=_rsi(series),
        trend_positive=bool(price >= sma200 and sma50 >= sma200),
    )


def _portfolio_market_values(portfolio: Any, symbols: tuple[str, ...]) -> dict[str, float]:
    market_values = {symbol: 0.0 for symbol in symbols}
    for position in getattr(portfolio, "positions", ()) or ():
        symbol = _normalize_symbol(getattr(position, "symbol", ""))
        if symbol not in market_values:
            continue
        market_values[symbol] += _coerce_float(getattr(position, "market_value", 0.0))
    return market_values


def _portfolio_cash(portfolio: Any) -> float:
    for attr in ("buying_power", "cash_balance", "available_cash"):
        value = getattr(portfolio, attr, None)
        if value is not None:
            return max(0.0, _coerce_float(value))
    metadata = getattr(portfolio, "metadata", {}) or {}
    if isinstance(metadata, Mapping):
        for key in ("buying_power", "cash_balance", "available_cash"):
            if key in metadata:
                return max(0.0, _coerce_float(metadata[key]))
    return 0.0


def _as_timestamp(value: object) -> pd.Timestamp:
    if value is None:
        return pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("America/New_York").tz_localize(None)
    return timestamp.normalize()


def _normalize_quarterly_months(raw_months: object) -> tuple[int, ...]:
    if raw_months is None:
        candidates: object = (1, 4, 7, 10)
    elif isinstance(raw_months, str):
        candidates = raw_months.replace(";", ",").split(",")
    else:
        candidates = raw_months

    months: list[int] = []
    try:
        iterator = iter(candidates)  # type: ignore[arg-type]
    except TypeError:
        iterator = iter((candidates,))
    for item in iterator:
        try:
            month = int(item)
        except (TypeError, ValueError):
            continue
        if 1 <= month <= 12 and month not in months:
            months.append(month)
    return tuple(months) or (1, 4, 7, 10)


def _is_in_execution_window(
    as_of: object,
    *,
    cadence: str,
    monthly_day: int,
    monthly_window_calendar_days: int,
    weekly_day: int,
    weekly_window_calendar_days: int,
    quarterly_months: object,
    quarterly_day: int,
    quarterly_window_calendar_days: int,
) -> tuple[bool, str]:
    timestamp = _as_timestamp(as_of)
    cadence_key = str(cadence or "monthly").strip().lower()
    if cadence_key == "weekly":
        day = int(max(0, min(6, weekly_day)))
        window = int(max(1, min(7, weekly_window_calendar_days)))
        days_since_start = (int(timestamp.weekday()) - day) % 7
        return days_since_start < window, f"weekly_day={day} window_calendar_days={window}"
    if cadence_key == "quarterly":
        months = _normalize_quarterly_months(quarterly_months)
        start_day = int(max(1, min(31, quarterly_day)))
        window = int(max(1, quarterly_window_calendar_days))
        day = int(timestamp.day)
        months_text = ",".join(str(month) for month in months)
        return timestamp.month in months and start_day <= day < start_day + window, (
            f"quarterly_months={months_text} quarterly_day={start_day} "
            f"window_calendar_days={window}"
        )
    if cadence_key != "monthly":
        raise ValueError("cadence must be 'monthly', 'weekly', or 'quarterly'")
    start_day = int(max(1, min(31, monthly_day)))
    window = int(max(1, monthly_window_calendar_days))
    day = int(timestamp.day)
    return start_day <= day < start_day + window, (
        f"monthly_day={start_day} window_calendar_days={window}"
    )


def _resolve_base_investment_budget(
    *,
    investment_amount_mode: str,
    base_investment_usd: float,
) -> float:
    mode = _normalize_investment_amount_mode(investment_amount_mode)
    if mode == "fixed":
        return max(0.0, float(base_investment_usd))
    raise ValueError("investment_amount_mode must be 'fixed'")


def _normalize_investment_amount_mode(value: object) -> str:
    mode = str(value or "fixed").strip().lower()
    if mode in {"fixed", "fixed_amount", "base_investment"}:
        return "fixed"
    raise ValueError("investment_amount_mode must be 'fixed'")


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
    mild_drawdown_threshold: float = 0.08,
    deep_drawdown_threshold: float = 0.15,
    severe_drawdown_threshold: float = 0.25,
    mild_discount_gap: float = 0.05,
    deep_discount_gap: float = 0.10,
    expensive_gap: float = 0.12,
    very_expensive_gap: float = 0.20,
    shallow_drawdown_threshold: float = 0.03,
    overbought_rsi: float = 70.0,
    base_multiplier: float = 1.0,
    mild_pullback_multiplier: float = 1.10,
    deep_pullback_multiplier: float = 1.25,
    severe_pullback_multiplier: float = 1.50,
    expensive_multiplier: float = 1.0,
    very_expensive_multiplier: float = 1.0,
    technical_indicator_snapshot: Mapping[str, object] | None = None,
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
            payload = _resolve_indicator_payload(technical_indicator_snapshot, symbol)
            payload_indicator = _indicator_from_payload(symbol, payload) if payload else None
            if payload_indicator is not None:
                indicators.append(payload_indicator)
                continue
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
    planned_investment = min(requested_investment, investable_cash)

    skip_reason = None
    actionable = True
    if not is_window:
        skip_reason = "outside_execution_window"
        actionable = False
    elif multiplier <= 0.0 or requested_investment <= 0.0:
        skip_reason = "valuation_too_expensive"
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
    if smart_enabled:
        signal_desc = _translate_with_fallback(
            translator,
            "smart_dca_signal",
            fallback_en=(
                "Smart DCA {regime}: multiplier {multiplier}, "
                "planned buy ${planned_investment} from cash ${available_cash}"
            ),
            fallback_zh=(
                "智能定投 {regime}: 倍数 {multiplier}，计划买入 ${planned_investment}，"
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
            "smart_dca_signal_ordinary",
            fallback_en="Ordinary DCA: planned buy ${planned_investment} from cash ${available_cash}",
            fallback_zh="普通定投：计划买入 ${planned_investment}，现金 ${available_cash}",
            planned_investment=f"{planned_investment:,.2f}",
            available_cash=f"{available_cash:,.2f}",
        )
    if cash_capped and planned_investment > 0.0:
        signal_desc = _translate_with_fallback(
            translator,
            "smart_dca_cash_capped",
            fallback_en="{signal} | cash capped from requested ${requested_investment}",
            fallback_zh="{signal} | 因现金限制，低于请求金额 ${requested_investment}",
            signal=signal_desc,
            requested_investment=f"{requested_investment:,.2f}",
        )
    if smart_enabled:
        status_desc = _translate_with_fallback(
            translator,
            "smart_dca_status",
            fallback_en="{window} | avg drawdown {avg_drawdown}, avg gap vs SMA200 {avg_sma200_gap}",
            fallback_zh="{window} | 平均回撤 {avg_drawdown}，相对 SMA200 均值 {avg_sma200_gap}",
            window=_localized_execution_window(window_text, translator),
            avg_drawdown=f"{aggregate_metrics['avg_drawdown_252d']:.1%}",
            avg_sma200_gap=f"{aggregate_metrics['avg_sma200_gap']:.1%}",
        )
    else:
        status_desc = _translate_with_fallback(
            translator,
            "smart_dca_status_ordinary",
            fallback_en="{window} | ordinary DCA without valuation multiplier",
            fallback_zh="{window} | 普通定投，不使用估值倍数",
            window=_localized_execution_window(window_text, translator),
        )
    if skip_reason:
        signal_desc = _translate_with_fallback(
            translator,
            "smart_dca_skip",
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
