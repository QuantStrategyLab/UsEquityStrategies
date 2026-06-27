"""Cash-only strategy equity helpers (no margin / AvailableFunds double-counting)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_CASH_METADATA_KEYS = (
    "market_currency_cash",
    "cash_available_for_trading",
    "cash_balance",
)


def resolve_raw_cash_from_snapshot(snapshot: Any) -> float:
    metadata = getattr(snapshot, "metadata", {}) or {}
    if isinstance(metadata, Mapping):
        for key in _CASH_METADATA_KEYS:
            if metadata.get(key) is not None:
                return float(metadata[key])
        cash_by_currency = metadata.get("cash_by_currency")
        if isinstance(cash_by_currency, Mapping):
            currency = str(metadata.get("currency") or "USD").strip().upper()
            if currency in cash_by_currency:
                return float(cash_by_currency[currency])
    for attr in ("cash_balance", "buying_power"):
        value = getattr(snapshot, attr, None)
        if value is not None:
            return float(value)
    return 0.0


def sum_market_values(market_values: Mapping[str, float]) -> float:
    return sum(float(value or 0.0) for value in market_values.values())


def compute_strategy_total_equity(
    market_values: Mapping[str, float],
    raw_cash: float,
) -> float:
    return float(raw_cash) + sum_market_values(market_values)


def resolve_strategy_equity_for_targets(
    *,
    market_values: Mapping[str, float],
    raw_cash: float,
    cash_only_execution: bool,
) -> tuple[float, bool]:
    """Return (equity_for_target_translation, cash_only_deleverage_mode)."""
    total_equity = compute_strategy_total_equity(market_values, raw_cash)
    if cash_only_execution and total_equity <= 0.0:
        gross_positions = sum_market_values(market_values)
        if gross_positions > 0.0:
            return gross_positions, True
    return total_equity, False


def resolve_weight_translation_equity(
    portfolio_inputs: Any,
    *,
    cash_only_execution: bool,
) -> tuple[float, bool, bool]:
    """Return (equity_for_translation, block_execution, deleverage_mode)."""
    total_equity = float(getattr(portfolio_inputs, "total_equity", 0.0) or 0.0)
    market_values = dict(getattr(portfolio_inputs, "market_values", None) or {})
    raw_cash = float(getattr(portfolio_inputs, "liquid_cash", 0.0) or 0.0)
    if total_equity <= 0.0:
        effective_equity, deleverage_mode = resolve_strategy_equity_for_targets(
            market_values=market_values,
            raw_cash=raw_cash,
            cash_only_execution=cash_only_execution,
        )
        if deleverage_mode:
            return effective_equity, False, True
        return total_equity, True, False
    return total_equity, False, False


def apply_cash_only_account_state(account_state: Mapping[str, Any], *, raw_cash: float) -> dict[str, Any]:
    normalized = dict(account_state)
    market_values = dict(normalized.get("market_values") or {})
    normalized["available_cash"] = float(raw_cash)
    normalized["total_strategy_equity"] = compute_strategy_total_equity(market_values, raw_cash)
    return normalized


def build_cash_only_portfolio_inputs_from_snapshot(snapshot: Any, **kwargs):
    from quant_platform_kit.common.execution_translation import (
        ValueTargetPortfolioInputs,
        build_value_target_portfolio_inputs_from_snapshot,
    )

    raw_cash = resolve_raw_cash_from_snapshot(snapshot)
    base = build_value_target_portfolio_inputs_from_snapshot(
        snapshot,
        liquid_cash=raw_cash,
        **kwargs,
    )
    metadata = getattr(snapshot, "metadata", {}) or {}
    strategy_symbols = metadata.get("strategy_symbols") if isinstance(metadata, Mapping) else None
    market_values = dict(base.market_values)
    quantities = dict(base.quantities)
    if isinstance(strategy_symbols, (list, tuple)) and strategy_symbols:
        allowed = {str(symbol or "").strip().upper() for symbol in strategy_symbols if str(symbol or "").strip()}
        market_values = {
            str(symbol): float(market_values.get(symbol, 0.0))
            for symbol in allowed
        }
        quantities = {
            str(symbol): float(quantities.get(symbol, 0.0))
            for symbol in allowed
        }
    total_equity = compute_strategy_total_equity(market_values, raw_cash)
    sellable = base.sellable_quantities
    if sellable is not None and isinstance(strategy_symbols, (list, tuple)) and strategy_symbols:
        allowed = {str(symbol or "").strip().upper() for symbol in strategy_symbols if str(symbol or "").strip()}
        sellable = {
            str(symbol): float(sellable.get(symbol, 0.0))
            for symbol in allowed
        }
    return ValueTargetPortfolioInputs(
        market_values=market_values,
        quantities=quantities,
        total_equity=total_equity,
        liquid_cash=raw_cash,
        sellable_quantities=sellable,
    )


def build_portfolio_inputs_from_snapshot(snapshot: Any, *, cash_only_execution: bool = True, **kwargs):
    if cash_only_execution:
        return build_cash_only_portfolio_inputs_from_snapshot(snapshot, **kwargs)
    from quant_platform_kit.common.execution_translation import (
        build_value_target_portfolio_inputs_from_snapshot,
    )

    return build_value_target_portfolio_inputs_from_snapshot(snapshot, **kwargs)


def normalize_account_state_from_snapshot(
    account_state: Mapping[str, Any],
    snapshot: Any,
    *,
    cash_only_execution: bool = True,
) -> dict[str, Any]:
    if not cash_only_execution:
        return dict(account_state)
    return apply_cash_only_account_state(
        account_state,
        raw_cash=resolve_raw_cash_from_snapshot(snapshot),
    )
