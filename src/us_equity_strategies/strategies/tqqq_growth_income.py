"""Allocation and plan helpers for the unified value-mode growth income profile."""

from __future__ import annotations

import numpy as np
import pandas as pd


def get_hybrid_allocation(
    total_equity_usd,
    qqq_p,
    stop_line,
    *,
    alloc_tier1_breakpoints,
    alloc_tier1_values,
    alloc_tier2_breakpoints,
    alloc_tier2_values,
    risk_leverage_factor,
    risk_agg_cap,
    risk_numerator,
):
    if total_equity_usd <= alloc_tier2_breakpoints[0]:
        target_agg = float(
            np.interp(total_equity_usd, alloc_tier1_breakpoints, alloc_tier1_values)
        )
    elif total_equity_usd <= alloc_tier2_breakpoints[1]:
        target_agg = float(
            np.interp(total_equity_usd, alloc_tier2_breakpoints, alloc_tier2_values)
        )
    else:
        if qqq_p <= stop_line:
            target_agg = 0.0
        else:
            risk = max(0.01, (qqq_p - stop_line) / qqq_p * risk_leverage_factor)
            target_agg = min(risk_agg_cap, risk_numerator / risk)
    target_yield = max(0.0, 1.0 - target_agg)
    return target_agg, target_yield


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
    alloc_tier1_breakpoints,
    alloc_tier1_values,
    alloc_tier2_breakpoints,
    alloc_tier2_values,
    risk_leverage_factor,
    risk_agg_cap,
    risk_numerator,
    atr_exit_scale,
    atr_entry_scale,
    exit_line_floor,
    exit_line_cap,
    entry_line_floor,
    entry_line_cap,
    dual_drive_idle_symbol="BOXX",
    dual_drive_idle_fraction=0.0,
    dual_drive_idle_trigger="tqqq_active",
    attack_scale_mode="baseline",
    attack_scale_min=0.55,
    attack_scale_gap_limit=0.08,
    attack_allocation_mode="atr_staged",
    dual_drive_qqq_weight=0.45,
    dual_drive_tqqq_weight=0.45,
    dual_drive_cash_reserve_ratio=0.02,
    dual_drive_allow_pullback=True,
    dual_drive_require_ma20_slope=True,
):
    df_qqq = pd.DataFrame(qqq_history)
    qqq_p = df_qqq["close"].iloc[-1]
    ma200 = df_qqq["close"].rolling(200).mean().iloc[-1]
    ma20 = df_qqq["close"].rolling(20).mean()
    ma20_slope = ma20.diff().iloc[-1]

    true_range = pd.concat(
        [
            df_qqq["high"] - df_qqq["low"],
            abs(df_qqq["high"] - df_qqq["close"].shift(1)),
            abs(df_qqq["low"] - df_qqq["close"].shift(1)),
        ],
        axis=1,
    ).max(axis=1)
    atr_pct = true_range.rolling(14).mean().iloc[-1] / qqq_p
    exit_line = ma200 * max(
        exit_line_floor,
        min(exit_line_cap, 1.0 - (atr_pct * atr_exit_scale)),
    )
    entry_line = ma200 * max(
        entry_line_floor,
        min(entry_line_cap, 1.0 + (atr_pct * atr_entry_scale)),
    )

    allocation_mode = str(attack_allocation_mode or "atr_staged").strip().lower()
    fixed_dual_drive_enabled = allocation_mode == "fixed_qqq_tqqq_pullback"
    dual_drive_symbol = "QQQ" if fixed_dual_drive_enabled else str(dual_drive_idle_symbol or "BOXX").strip().upper()
    dual_drive_fraction = max(0.0, min(1.0, float(dual_drive_idle_fraction or 0.0)))
    dual_drive_enabled = fixed_dual_drive_enabled or (dual_drive_symbol == "QQQ" and dual_drive_fraction > 0.0)

    strategy_symbols = ["TQQQ", "BOXX", "SPYI", "QQQI"]
    if dual_drive_enabled and dual_drive_symbol not in strategy_symbols:
        strategy_symbols.insert(1, dual_drive_symbol)
    market_values = {symbol: 0.0 for symbol in strategy_symbols}
    quantities = {symbol: 0 for symbol in strategy_symbols}
    for position in snapshot.positions:
        if position.symbol in market_values:
            market_values[position.symbol] = float(position.market_value)
            quantities[position.symbol] = int(position.quantity)

    total_equity = snapshot.total_equity
    real_buying_power = float(snapshot.buying_power or 0.0)

    income_ratio = get_income_ratio(
        total_equity,
        income_threshold_usd=income_threshold_usd,
    )
    target_income_val = total_equity * income_ratio
    target_spyi_val = target_income_val * (1.0 - qqqi_income_ratio)
    target_qqqi_val = target_income_val * qqqi_income_ratio

    strategy_equity = max(0.0, total_equity - target_income_val)
    reserved = strategy_equity * cash_reserve_ratio
    agg_ratio, _ = get_hybrid_allocation(
        strategy_equity,
        qqq_p,
        exit_line,
        alloc_tier1_breakpoints=alloc_tier1_breakpoints,
        alloc_tier1_values=alloc_tier1_values,
        alloc_tier2_breakpoints=alloc_tier2_breakpoints,
        alloc_tier2_values=alloc_tier2_values,
        risk_leverage_factor=risk_leverage_factor,
        risk_agg_cap=risk_agg_cap,
        risk_numerator=risk_numerator,
    )

    target_tqqq_ratio, icon, _reason = 0.0, "idle", "no signal"
    if quantities["TQQQ"] > 0:
        if qqq_p < exit_line:
            target_tqqq_ratio, icon = 0.0, "exit"
        elif qqq_p < ma200:
            target_tqqq_ratio, icon = agg_ratio * 0.33, "reduce"
        else:
            target_tqqq_ratio, icon = agg_ratio, "hold"
    elif qqq_p > entry_line:
        target_tqqq_ratio, icon = agg_ratio, "entry"

    attack_scale = 1.0
    scale_mode = str(attack_scale_mode or "baseline").strip().lower()
    if target_tqqq_ratio > 0.0 and scale_mode == "ma20_gap_trim_only":
        latest_ma20 = ma20.iloc[-1]
        if pd.notna(latest_ma20) and latest_ma20 > 0.0:
            gap = qqq_p / latest_ma20 - 1.0
            if gap < 0.0:
                gap_limit = max(0.0001, float(attack_scale_gap_limit or 0.08))
                scale_floor = max(0.0, min(1.0, float(attack_scale_min or 0.55)))
                negative_gap = max(gap, -gap_limit)
                attack_scale = 1.0 + (negative_gap / gap_limit) * (1.0 - scale_floor)
                target_tqqq_ratio *= attack_scale

    target_dual_drive_idle_val = 0.0
    if fixed_dual_drive_enabled:
        latest_ma20 = ma20.iloc[-1]
        above_ma200 = qqq_p > ma200
        positive_ma20_slope = pd.notna(ma20_slope) and ma20_slope > 0.0
        slope_ok = positive_ma20_slope if bool(dual_drive_require_ma20_slope) else True
        current_risk_active = quantities.get("TQQQ", 0) > 0 or quantities.get("QQQ", 0) > 0
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
        )
        if risk_active or pullback_risk_on:
            dual_drive_reserve_ratio = 0.02 if dual_drive_cash_reserve_ratio is None else float(dual_drive_cash_reserve_ratio)
            reserved = strategy_equity * max(0.0, min(1.0, dual_drive_reserve_ratio))
            target_tqqq_ratio = max(0.0, min(1.0, float(dual_drive_tqqq_weight or 0.45)))
            target_dual_drive_idle_ratio = max(0.0, min(1.0, float(dual_drive_qqq_weight or 0.45)))
            total_risk_ratio = target_tqqq_ratio + target_dual_drive_idle_ratio
            max_risk_ratio = max(0.0, 1.0 - reserved / strategy_equity) if strategy_equity > 0.0 else 0.0
            if total_risk_ratio > max_risk_ratio and total_risk_ratio > 0.0:
                scale = max_risk_ratio / total_risk_ratio
                target_tqqq_ratio *= scale
                target_dual_drive_idle_ratio *= scale
            target_tqqq_val = strategy_equity * target_tqqq_ratio
            target_dual_drive_idle_val = strategy_equity * target_dual_drive_idle_ratio
            target_idle_val = max(0.0, (strategy_equity - reserved) - target_tqqq_val - target_dual_drive_idle_val)
            target_boxx_val = target_idle_val
        else:
            target_tqqq_ratio = 0.0
            reserved = strategy_equity * cash_reserve_ratio
            target_tqqq_val = 0.0
            target_dual_drive_idle_val = 0.0
            target_boxx_val = max(0.0, strategy_equity - reserved)
    else:
        target_tqqq_val = strategy_equity * target_tqqq_ratio
        target_idle_val = max(0.0, (strategy_equity - reserved) - target_tqqq_val)
        trigger_name = str(dual_drive_idle_trigger or "tqqq_active").strip().lower()
        if trigger_name == "tqqq_active":
            use_dual_drive_idle = target_tqqq_ratio > 0.0
        elif trigger_name == "above_ma200":
            use_dual_drive_idle = qqq_p > ma200
        elif trigger_name == "above_ma200_ma20_slope":
            use_dual_drive_idle = qqq_p > ma200 and ma20_slope > 0.0
        elif trigger_name == "always":
            use_dual_drive_idle = True
        else:
            use_dual_drive_idle = False
        target_dual_drive_idle_val = (
            target_idle_val * dual_drive_fraction
            if dual_drive_enabled and use_dual_drive_idle
            else 0.0
        )
        target_boxx_val = max(0.0, target_idle_val - target_dual_drive_idle_val)
    threshold = total_equity * rebalance_threshold_ratio

    sig_display = signal_text_fn(icon)
    separator = translator("separator")
    dashboard = (
        f"{translator('dashboard_label')} | {translator('equity')}: ${total_equity:,.2f}\n"
        f"TQQQ: ${market_values['TQQQ']:,.2f} | SPYI: ${market_values['SPYI']:,.2f} | "
        f"QQQI: ${market_values['QQQI']:,.2f} | BOXX: ${market_values['BOXX']:,.2f}\n"
        f"{translator('buying_power')}: ${real_buying_power:,.2f} | {translator('signal_label')}: {sig_display}\n"
        f"QQQ: {qqq_p:.2f} | MA200: {ma200:.2f} | Exit: {exit_line:.2f}"
    )
    sell_order_symbols = ("TQQQ", "SPYI", "QQQI", "BOXX")
    buy_order_symbols = ("SPYI", "QQQI", "TQQQ")
    if dual_drive_enabled:
        sell_order_symbols = tuple(dict.fromkeys(("TQQQ", dual_drive_symbol, "SPYI", "QQQI", "BOXX")))
        buy_order_symbols = tuple(dict.fromkeys(("SPYI", "QQQI", "TQQQ", dual_drive_symbol)))

    return {
        "strategy_symbols": strategy_symbols,
        # Execution metadata consumed by downstream platform repos.
        "sell_order_symbols": sell_order_symbols,
        "buy_order_symbols": buy_order_symbols,
        "cash_sweep_symbol": "BOXX",
        "portfolio_rows": (("TQQQ", dual_drive_symbol, "BOXX"), ("QQQI", "SPYI"))
        if dual_drive_enabled
        else (("TQQQ", "BOXX"), ("QQQI", "SPYI")),
        "account_hash": snapshot.metadata["account_hash"],
        "market_values": market_values,
        "quantities": quantities,
        "total_equity": total_equity,
        "real_buying_power": real_buying_power,
        "reserved": reserved,
        "threshold": threshold,
        "target_values": {
            "TQQQ": target_tqqq_val,
            "BOXX": target_boxx_val,
            "SPYI": target_spyi_val,
            "QQQI": target_qqqi_val,
            **({dual_drive_symbol: target_dual_drive_idle_val} if dual_drive_enabled else {}),
        },
        "sig_display": sig_display,
        "dashboard": dashboard,
        "qqq_p": qqq_p,
        "ma200": ma200,
        "exit_line": exit_line,
        "attack_scale": attack_scale,
        "separator": separator,
    }
