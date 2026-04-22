from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any


Translator = Callable[..., str]
_COMPACT_UNIVERSE_THRESHOLD = 12


def _normalize_symbol(symbol: object) -> str:
    return str(symbol or "").strip().upper().removesuffix(".US")


def _normalize_symbols(symbols: Iterable[object] | None) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for symbol in symbols or ():
        value = _normalize_symbol(symbol)
        if not value or value in seen:
            continue
        normalized.append(value)
        seen.add(value)
    return tuple(normalized)


def _translator_uses_zh(translator: Translator | None) -> bool:
    if translator is None:
        return False
    try:
        sample = str(translator("no_trades"))
    except Exception:
        return False
    return any("\u4e00" <= ch <= "\u9fff" for ch in sample)


def _format_quantity(quantity: object) -> str:
    try:
        value = float(quantity)
    except (TypeError, ValueError):
        return "0"
    if value.is_integer():
        return str(int(value))
    return f"{value:g}"


def _format_money(value: object) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        amount = 0.0
    return f"${amount:,.2f}"


def _metadata_mapping(snapshot: Any) -> Mapping[str, Any]:
    metadata = getattr(snapshot, "metadata", {}) or {}
    return metadata if isinstance(metadata, Mapping) else {}


def _cash_by_currency(snapshot: Any) -> dict[str, float]:
    raw_cash = _metadata_mapping(snapshot).get("cash_by_currency")
    if not isinstance(raw_cash, Mapping):
        return {}
    cash_by_currency: dict[str, float] = {}
    for currency, amount in raw_cash.items():
        normalized_currency = str(currency or "").strip().upper()
        if not normalized_currency:
            continue
        cash_by_currency[normalized_currency] = float(amount)
    return cash_by_currency


def _format_cash_by_currency(cash_by_currency: Mapping[str, float]) -> str:
    parts: list[str] = []
    for currency in sorted(cash_by_currency, key=lambda value: (value != "USD", value)):
        amount = float(cash_by_currency[currency])
        if amount == 0.0:
            continue
        parts.append(f"{currency} {amount:,.2f}")
    return ", ".join(parts)


def _format_symbol_preview(symbols: Iterable[object], *, limit: int = 5) -> str:
    normalized = _normalize_symbols(symbols)
    if not normalized:
        return ""
    shown = list(normalized[:limit])
    remaining = len(normalized) - len(shown)
    if remaining > 0:
        shown.append(f"+{remaining}")
    return ", ".join(shown)


def _snapshot_buying_power(snapshot: Any) -> float:
    metadata = _metadata_mapping(snapshot)
    for attr in ("buying_power", "cash_balance"):
        value = getattr(snapshot, attr, None)
        if value is not None:
            return float(value)
    for key in ("cash_available_for_trading",):
        if metadata.get(key) is not None:
            return float(metadata[key])
    return 0.0


def _as_optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _portfolio_context_mapping(portfolio_context: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return portfolio_context if isinstance(portfolio_context, Mapping) else {}


def _portfolio_context_buying_power(portfolio_context: Mapping[str, Any]) -> float | None:
    for key in ("raw_buying_power", "buying_power", "available_cash"):
        value = _as_optional_float(portfolio_context.get(key))
        if value is not None:
            return value
    return None


def _portfolio_context_positions(
    portfolio_context: Mapping[str, Any],
) -> tuple[tuple[str, ...], dict[str, float], dict[str, object]]:
    holdings_order = _normalize_symbols(portfolio_context.get("holdings_order"))
    raw_holdings = portfolio_context.get("holdings")
    if not isinstance(raw_holdings, Mapping):
        return holdings_order, {}, {}

    market_values: dict[str, float] = {}
    quantities: dict[str, object] = {}
    for raw_symbol, payload in raw_holdings.items():
        symbol = _normalize_symbol(raw_symbol)
        if not symbol or not isinstance(payload, Mapping):
            continue
        market_value = _as_optional_float(payload.get("market_value"))
        quantity = payload.get("quantity", 0)
        market_values[symbol] = 0.0 if market_value is None else market_value
        quantities[symbol] = quantity
    if not holdings_order:
        holdings_order = tuple(market_values)
    return holdings_order, market_values, quantities


def _snapshot_strategy_symbols(snapshot: Any) -> tuple[str, ...]:
    metadata = _metadata_mapping(snapshot)
    raw_symbols = metadata.get("strategy_symbols")
    if isinstance(raw_symbols, Iterable) and not isinstance(raw_symbols, (str, bytes)):
        return _normalize_symbols(raw_symbols)
    return ()


def _position_maps(snapshot: Any) -> tuple[dict[str, float], dict[str, object]]:
    market_values: dict[str, float] = {}
    quantities: dict[str, object] = {}
    for position in getattr(snapshot, "positions", ()) or ():
        symbol = _normalize_symbol(getattr(position, "symbol", ""))
        if not symbol:
            continue
        market_values[symbol] = float(getattr(position, "market_value", 0.0) or 0.0)
        quantities[symbol] = getattr(position, "quantity", 0)
    return market_values, quantities


def _labels(translator: Translator | None) -> dict[str, str]:
    if _translator_uses_zh(translator):
        return {
            "title": "📌 策略账户概览",
            "total_assets": "总资产（策略标的+现金）",
            "buying_power": "购买力",
            "reserved_cash": "预留现金",
            "investable_cash": "可投资现金",
            "cash_by_currency": "各币种现金",
            "holdings": "💼 策略持仓",
            "empty": "空仓",
            "tracked_universe": "跟踪股票池",
            "tracked_count_suffix": "只",
            "shares": "股",
            "signal": "信号",
        }
    return {
        "title": "📌 Strategy portfolio",
        "total_assets": "Total assets (strategy symbols + cash)",
        "buying_power": "Buying power",
        "reserved_cash": "Reserved cash",
        "investable_cash": "Investable cash",
        "cash_by_currency": "Cash by currency",
        "holdings": "💼 Strategy holdings",
        "empty": "No positions",
        "tracked_universe": "Tracked universe",
        "tracked_count_suffix": " symbols",
        "shares": "shares",
        "signal": "Signal",
    }


def build_portfolio_dashboard(
    snapshot: Any,
    *,
    strategy_symbols: Iterable[object] | None = None,
    translator: Translator | None = None,
    signal_text: object | None = None,
    benchmark_text: object | None = None,
    portfolio_context: Mapping[str, Any] | None = None,
) -> str:
    labels = _labels(translator)
    context = _portfolio_context_mapping(portfolio_context)
    context_symbols, context_market_values, context_quantities = _portfolio_context_positions(context)
    if context_market_values or context_quantities:
        market_values, quantities = context_market_values, context_quantities
    else:
        market_values, quantities = _position_maps(snapshot)
    symbols = _normalize_symbols(strategy_symbols)
    if not symbols:
        symbols = context_symbols
    if not symbols:
        symbols = _snapshot_strategy_symbols(snapshot)
    if not symbols:
        symbols = tuple(sorted(market_values))

    total_equity = _as_optional_float(context.get("total_equity"))
    if total_equity is None:
        total_equity = float(getattr(snapshot, "total_equity", 0.0) or 0.0)
    buying_power = _portfolio_context_buying_power(context)
    if buying_power is None:
        buying_power = _snapshot_buying_power(snapshot)
    lines = [
        labels["title"],
        f"  - {labels['total_assets']}: {_format_money(total_equity)}",
        f"  - {labels['buying_power']}: {_format_money(buying_power)}",
    ]
    reserved_cash = _as_optional_float(context.get("reserved_cash"))
    if reserved_cash is not None:
        lines.append(f"  - {labels['reserved_cash']}: {_format_money(reserved_cash)}")
    investable_cash = _as_optional_float(context.get("investable_cash"))
    if investable_cash is not None:
        lines.append(f"  - {labels['investable_cash']}: {_format_money(investable_cash)}")

    cash_by_currency = _cash_by_currency(snapshot)
    nonzero_currencies = {
        currency: amount
        for currency, amount in cash_by_currency.items()
        if float(amount) != 0.0
    }
    formatted_cash = _format_cash_by_currency(nonzero_currencies)
    if formatted_cash and (len(nonzero_currencies) > 1 or "USD" not in nonzero_currencies):
        lines.append(f"  - {labels['cash_by_currency']}: {formatted_cash}")

    compact_universe = len(symbols) > _COMPACT_UNIVERSE_THRESHOLD
    if compact_universe:
        displayed_symbols = tuple(
            symbol
            for symbol in symbols
            if float(market_values.get(symbol, 0.0) or 0.0) != 0.0
            or float(quantities.get(symbol, 0) or 0.0) != 0.0
        )
    else:
        displayed_symbols = symbols
    lines.append(labels["holdings"])
    if displayed_symbols:
        for symbol in displayed_symbols:
            lines.append(
                f"  - {symbol}: {_format_money(market_values.get(symbol, 0.0))} / "
                f"{_format_quantity(quantities.get(symbol, 0))}{labels['shares']}"
            )
    else:
        lines.append(f"  - {labels['empty']}")
    if compact_universe and symbols and len(displayed_symbols) < len(symbols):
        omitted_symbols = tuple(symbol for symbol in symbols if symbol not in displayed_symbols) or symbols
        lines.append(
            f"  - {labels['tracked_universe']}: {len(symbols)}{labels['tracked_count_suffix']} "
            f"({_format_symbol_preview(omitted_symbols)})"
        )

    signal = str(signal_text or "").strip()
    if signal:
        lines.append(f"🎯 {labels['signal']}: {signal}")

    benchmark = str(benchmark_text or "").strip()
    if benchmark:
        lines.append(benchmark)

    return "\n".join(lines)
