"""Allocation and plan helpers for the live TQQQ growth income profile."""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant_platform_kit.common.history import normalize_history_frame
from us_equity_strategies.income_layer import (
    INCOME_LAYER_RATIO_MODE_LINEAR_CAP,
    INCOME_LAYER_RATIO_MODE_LOG_LOSS_BUDGET,
    INCOME_LAYER_RATIO_MODES,
    as_clamped_ratio,
    build_income_layer_plan,
    get_income_layer_ratio,
    normalize_income_layer_allocations,
)

PULLBACK_REBOUND_THRESHOLD_MODE_FIXED = "fixed"
PULLBACK_REBOUND_THRESHOLD_MODE_VOLATILITY_SCALED = "volatility_scaled"
PULLBACK_REBOUND_THRESHOLD_MODES = {
    PULLBACK_REBOUND_THRESHOLD_MODE_FIXED,
    PULLBACK_REBOUND_THRESHOLD_MODE_VOLATILITY_SCALED,
}
CORE_ASSETS = ("TQQQ", "BOXX")
__all__ = [
    "INCOME_LAYER_RATIO_MODE_LINEAR_CAP",
    "INCOME_LAYER_RATIO_MODE_LOG_LOSS_BUDGET",
    "INCOME_LAYER_RATIO_MODES",
    "build_rebalance_plan",
    "get_income_layer_ratio",
    "get_income_ratio",
]


def get_income_ratio(total_equity_usd: float, *, income_threshold_usd: float) -> float:
    if total_equity_usd < income_threshold_usd:
        return 0.0
    if total_equity_usd <= 2 * income_threshold_usd:
        return float(
            np.interp(
                total_equity_usd,
                [income_threshold_usd, 2 * income_threshold_usd],
                [0.0, 0.40],
            )
        )
    return 0.60


def _translate_with_fallback(translator, key: str, fallback: str, **kwargs) -> str:
    rendered = translator(key, **kwargs)
    return fallback if rendered == key else rendered


def _resolve_pullback_rebound_threshold(
    close: pd.Series,
    *,
    window: int,
    mode: str,
    fixed_threshold: float,
    volatility_multiplier: float,
) -> tuple[float, float]:
    threshold_mode = str(mode or PULLBACK_REBOUND_THRESHOLD_MODE_FIXED).strip().lower()
    if threshold_mode not in PULLBACK_REBOUND_THRESHOLD_MODES:
        modes = ", ".join(sorted(PULLBACK_REBOUND_THRESHOLD_MODES))
        raise ValueError(f"Unsupported pullback rebound threshold mode: {threshold_mode!r}; expected one of {modes}")

    fixed_threshold = max(0.0, float(fixed_threshold or 0.0))
    if threshold_mode == PULLBACK_REBOUND_THRESHOLD_MODE_FIXED:
        return fixed_threshold, np.nan

    returns = pd.to_numeric(close, errors="coerce").pct_change(fill_method=None)
    rolling_volatility = returns.rolling(int(window), min_periods=int(window)).std().iloc[-1]
    if pd.isna(rolling_volatility):
        return fixed_threshold, np.nan
    multiplier = max(0.0, float(volatility_multiplier or 0.0))
    return max(0.0, float(rolling_volatility) * multiplier), float(rolling_volatility)


def build_rebalance_plan(
    qqq_history,
    snapshot,
    *,
    signal_text_fn,
    translator,
    income_threshold_usd,
    qqqi_income_ratio,
    cash_reserve_ratio,
    rebalance_threshold_ratio,
    income_layer_start_usd=None,
    income_layer_max_ratio=None,
    income_layer_qqqi_weight=None,
    income_layer_spyi_weight=None,
    income_layer_allocations=None,
    income_layer_enabled=True,
    income_layer_activation_band_ratio=0.0,
    income_layer_ratio_mode=INCOME_LAYER_RATIO_MODE_LINEAR_CAP,
    income_layer_log_growth_factor=0.70,
    income_layer_stress_drawdown_ratio=0.30,
    income_layer_base_loss_budget_ratio=0.08,
    income_layer_min_loss_budget_ratio=0.06,
    income_layer_loss_budget_decay_per_double=0.01,
    attack_allocation_mode="fixed_qqq_tqqq_pullback",
    dual_drive_qqq_weight=0.45,
    dual_drive_tqqq_weight=0.45,
    dual_drive_unlevered_symbol="QQQ",
    dual_drive_cash_reserve_ratio=0.02,
    dual_drive_allow_pullback=True,
    dual_drive_require_ma20_slope=True,
    dual_drive_pullback_rebound_window=20,
    dual_drive_pullback_rebound_threshold_mode=PULLBACK_REBOUND_THRESHOLD_MODE_VOLATILITY_SCALED,
    dual_drive_pullback_rebound_threshold=0.0,
    dual_drive_pullback_rebound_volatility_multiplier=2.0,
):
    df_qqq = normalize_history_frame(qqq_history, label="benchmark_history")
    qqq_p = df_qqq["close"].iloc[-1]
    ma200 = df_qqq["close"].rolling(200).mean().iloc[-1]
    ma20 = df_qqq["close"].rolling(20).mean()
    ma20_slope = ma20.diff().iloc[-1]

    allocation_mode = str(attack_allocation_mode or "fixed_qqq_tqqq_pullback").strip().lower()
    if allocation_mode != "fixed_qqq_tqqq_pullback":
        raise ValueError("tqqq_growth_income only supports fixed_qqq_tqqq_pullback")

    unlevered_symbol = str(dual_drive_unlevered_symbol or "QQQ").strip().upper()
    if not unlevered_symbol:
        raise ValueError("dual_drive_unlevered_symbol must be a non-empty ticker")
    legacy_qqqi_ratio = as_clamped_ratio(qqqi_income_ratio, default=0.5)
    fallback_qqqi_weight = (
        legacy_qqqi_ratio if income_layer_qqqi_weight is None else as_clamped_ratio(income_layer_qqqi_weight)
    )
    fallback_spyi_weight = (
        1.0 - legacy_qqqi_ratio if income_layer_spyi_weight is None else as_clamped_ratio(income_layer_spyi_weight)
    )
    income_allocations = normalize_income_layer_allocations(
        income_layer_allocations,
        fallback_allocations=(("SPYI", fallback_spyi_weight), ("QQQI", fallback_qqqi_weight)),
        excluded_symbols=(*CORE_ASSETS, unlevered_symbol),
    )
    income_symbols = tuple(income_allocations)
    if unlevered_symbol in {*CORE_ASSETS, *income_symbols}:
        raise ValueError("dual_drive_unlevered_symbol must not overlap another TQQQ profile sleeve")

    strategy_symbols = ["TQQQ", unlevered_symbol, "BOXX", *income_symbols]
    market_values = {symbol: 0.0 for symbol in strategy_symbols}
    quantities = {symbol: 0.0 for symbol in strategy_symbols}
    for position in snapshot.positions:
        if position.symbol in market_values:
            market_values[position.symbol] = float(position.market_value)
            quantities[position.symbol] = float(position.quantity)

    total_equity = snapshot.total_equity
    real_buying_power = float(snapshot.buying_power or 0.0)

    layer_start = income_threshold_usd if income_layer_start_usd is None else income_layer_start_usd
    layer_max_ratio = 0.60 if income_layer_max_ratio is None else income_layer_max_ratio
    income_layer_plan = build_income_layer_plan(
        total_equity_usd=total_equity,
        market_values=market_values,
        allocations=income_allocations,
        income_layer_enabled=income_layer_enabled,
        income_layer_start_usd=layer_start,
        income_layer_max_ratio=layer_max_ratio,
        income_layer_activation_band_ratio=income_layer_activation_band_ratio,
        income_layer_ratio_mode=income_layer_ratio_mode,
        income_layer_log_growth_factor=income_layer_log_growth_factor,
        income_layer_stress_drawdown_ratio=income_layer_stress_drawdown_ratio,
        income_layer_base_loss_budget_ratio=income_layer_base_loss_budget_ratio,
        income_layer_min_loss_budget_ratio=income_layer_min_loss_budget_ratio,
        income_layer_loss_budget_decay_per_double=income_layer_loss_budget_decay_per_double,
    )
    target_income_values = income_layer_plan.target_values

    strategy_equity = max(0.0, total_equity - income_layer_plan.locked_value)
    reserved = strategy_equity * cash_reserve_ratio

    latest_ma20 = ma20.iloc[-1]
    pullback_rebound_window = max(1, int(dual_drive_pullback_rebound_window or 20))
    pullback_rebound_threshold_mode = str(
        dual_drive_pullback_rebound_threshold_mode or PULLBACK_REBOUND_THRESHOLD_MODE_FIXED
    ).strip().lower()
    pullback_rebound_threshold, pullback_rebound_volatility = _resolve_pullback_rebound_threshold(
        df_qqq["close"],
        window=pullback_rebound_window,
        mode=pullback_rebound_threshold_mode,
        fixed_threshold=float(dual_drive_pullback_rebound_threshold or 0.0),
        volatility_multiplier=float(dual_drive_pullback_rebound_volatility_multiplier or 0.0),
    )
    pullback_low = df_qqq["close"].rolling(pullback_rebound_window).min().iloc[-1]
    pullback_rebound = qqq_p / pullback_low - 1.0 if pd.notna(pullback_low) and pullback_low > 0.0 else np.nan
    pullback_rebound_ok = (
        pullback_rebound_threshold <= 0.0
        or (pd.notna(pullback_rebound) and pullback_rebound > pullback_rebound_threshold)
    )
    above_ma200 = qqq_p > ma200
    positive_ma20_slope = pd.notna(ma20_slope) and ma20_slope > 0.0
    slope_ok = positive_ma20_slope if bool(dual_drive_require_ma20_slope) else True
    current_risk_active = quantities.get("TQQQ", 0) > 0 or quantities.get(unlevered_symbol, 0) > 0
    risk_active = current_risk_active
    if current_risk_active and not above_ma200:
        risk_active = False
    elif not current_risk_active and above_ma200 and slope_ok:
        risk_active = True
    pullback_risk_on = (
        bool(dual_drive_allow_pullback)
        and not above_ma200
        and pd.notna(latest_ma20)
        and qqq_p > latest_ma20
        and positive_ma20_slope
        and pullback_rebound_ok
    )

    target_unlevered_val = 0.0
    if risk_active or pullback_risk_on:
        dual_drive_reserve_ratio = 0.02 if dual_drive_cash_reserve_ratio is None else float(dual_drive_cash_reserve_ratio)
        reserved = strategy_equity * max(0.0, min(1.0, dual_drive_reserve_ratio))
        target_tqqq_ratio = max(0.0, min(1.0, float(dual_drive_tqqq_weight or 0.45)))
        target_unlevered_ratio = max(0.0, min(1.0, float(dual_drive_qqq_weight or 0.45)))
        total_risk_ratio = target_tqqq_ratio + target_unlevered_ratio
        max_risk_ratio = max(0.0, 1.0 - reserved / strategy_equity) if strategy_equity > 0.0 else 0.0
        if total_risk_ratio > max_risk_ratio and total_risk_ratio > 0.0:
            scale = max_risk_ratio / total_risk_ratio
            target_tqqq_ratio *= scale
            target_unlevered_ratio *= scale
        target_tqqq_val = strategy_equity * target_tqqq_ratio
        target_unlevered_val = strategy_equity * target_unlevered_ratio
        target_boxx_val = max(0.0, (strategy_equity - reserved) - target_tqqq_val - target_unlevered_val)
        icon = "hold" if current_risk_active else "entry"
    else:
        target_tqqq_val = 0.0
        target_boxx_val = max(0.0, strategy_equity - reserved)
        icon = "exit" if current_risk_active else "idle"
    threshold = total_equity * rebalance_threshold_ratio

    ma20_slope_text = "n/a" if pd.isna(ma20_slope) else f"{ma20_slope:+.2f}"
    benchmark_line = (
        f"QQQ: {qqq_p:.2f} | MA200 Exit: {ma200:.2f} | "
        f"MA20Δ: {ma20_slope_text}"
    )

    investable_buying_power = max(0.0, real_buying_power - reserved)

    benchmark_context = {
        "symbol": "QQQ",
        "price": float(qqq_p),
        "long_trend_value": float(ma200),
        "exit_line": float(ma200),
        "ma20_slope": None if pd.isna(ma20_slope) else float(ma20_slope),
        "ma20_slope_text": ma20_slope_text,
    }
    signal_context = {
        "state": icon,
    }
    portfolio_context = {
        "total_equity": float(total_equity),
        "raw_buying_power": float(real_buying_power),
        "buying_power": float(real_buying_power),
        "reserved_cash": float(reserved),
        "investable_cash": float(investable_buying_power),
        "holdings_order": tuple(strategy_symbols),
        "holdings": {
            symbol: {
                "market_value": float(market_values[symbol]),
                "quantity": float(quantities[symbol]),
            }
            for symbol in strategy_symbols
        },
    }
    notification_context = {
        "signal": signal_context,
        "benchmark": benchmark_context,
        "portfolio": portfolio_context,
    }

    sig_display = signal_text_fn(icon)
    separator = translator("separator")
    reserved_cash_label = _translate_with_fallback(translator, "reserved_cash", "Reserved Cash")
    investable_cash_label = _translate_with_fallback(translator, "investable_cash", "Investable Cash")
    dashboard = (
        f"{translator('dashboard_label')} | {translator('equity')}: ${total_equity:,.2f}\n"
        f"TQQQ: ${market_values['TQQQ']:,.2f} | {unlevered_symbol}: ${market_values[unlevered_symbol]:,.2f} | "
        f"BOXX: ${market_values['BOXX']:,.2f}\n"
        f"Income: {' | '.join(f'{symbol}: ${market_values[symbol]:,.2f}' for symbol in income_symbols) or '$0.00'}\n"
        f"{translator('buying_power')}: ${real_buying_power:,.2f} | "
        f"{reserved_cash_label}: ${reserved:,.2f} | "
        f"{investable_cash_label}: ${investable_buying_power:,.2f}\n"
        f"{translator('signal_label')}: {sig_display}\n"
        f"{benchmark_line}"
    )
    sell_order_symbols = ("TQQQ", unlevered_symbol, *income_symbols, "BOXX")
    buy_order_symbols = (*income_symbols, "TQQQ", unlevered_symbol)
    snapshot_metadata = getattr(snapshot, "metadata", {}) or {}
    account_hash = snapshot_metadata.get("account_hash") if isinstance(snapshot_metadata, dict) else None

    return {
        "strategy_symbols": strategy_symbols,
        # Execution metadata consumed by downstream platform repos.
        "sell_order_symbols": sell_order_symbols,
        "buy_order_symbols": buy_order_symbols,
        "cash_sweep_symbol": "BOXX",
        "portfolio_rows": (("TQQQ", unlevered_symbol, "BOXX"), income_symbols),
        "account_hash": account_hash,
        "market_values": market_values,
        "quantities": quantities,
        "total_equity": total_equity,
        "real_buying_power": real_buying_power,
        "investable_buying_power": investable_buying_power,
        "reserved": reserved,
        "threshold": threshold,
        "target_values": {
            "TQQQ": target_tqqq_val,
            unlevered_symbol: target_unlevered_val,
            "BOXX": target_boxx_val,
            **target_income_values,
        },
        "income_layer_allocations": income_layer_plan.allocations,
        "income_layer_symbols": income_layer_plan.symbols,
        "income_layer_ratio": income_layer_plan.ratio,
        "income_layer_value": income_layer_plan.locked_value,
        **income_layer_plan.diagnostics,
        "sig_display": sig_display,
        "dashboard": dashboard,
        "notification_context": notification_context,
        "qqq_p": qqq_p,
        "ma200": ma200,
        "exit_line": ma200,
        "pullback_rebound": pullback_rebound,
        "pullback_rebound_window": pullback_rebound_window,
        "pullback_rebound_threshold": pullback_rebound_threshold,
        "pullback_rebound_threshold_mode": pullback_rebound_threshold_mode,
        "pullback_rebound_volatility": pullback_rebound_volatility,
        "pullback_rebound_volatility_multiplier": float(dual_drive_pullback_rebound_volatility_multiplier or 0.0),
        "allocation_mode": allocation_mode,
        "separator": separator,
    }
