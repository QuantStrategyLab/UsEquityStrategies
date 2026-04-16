from __future__ import annotations

import numpy as np


FIXED_DUAL_DRIVE_MODE = "fixed_soxx_soxl_pullback"
SOXX_GATE_BLEND_MODE = "soxx_gate_blend"
SOXX_GATE_TIERED_BLEND_MODE = "soxx_gate_tiered_blend"


def _translate_with_fallback(translator, key, fallback, **kwargs):
    rendered = translator(key, **kwargs)
    return fallback if rendered == key else rendered


def _indicator_value(indicators, symbol: str, key: str, default=None):
    payload = indicators.get(symbol.lower()) or indicators.get(symbol.upper()) or {}
    return payload.get(key, default)


def _as_float_or_none(value):
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(result):
        return None
    return result


def _as_clamped_ratio(value, *, default=0.0, upper=1.0):
    result = _as_float_or_none(value)
    if result is None:
        result = float(default)
    return max(0.0, min(float(upper), result))


def get_dynamic_allocation(
    total_equity_usd,
    *,
    small_account_deploy_ratio,
    mid_account_deploy_ratio,
    large_account_deploy_ratio,
    trade_layer_decay_coeff,
):
    if total_equity_usd <= 10000:
        return small_account_deploy_ratio

    if total_equity_usd <= 80000:
        return float(
            np.interp(
                total_equity_usd,
                [10000, 80000],
                [small_account_deploy_ratio, mid_account_deploy_ratio],
            )
        )

    if total_equity_usd <= 180000:
        return float(
            np.interp(
                total_equity_usd,
                [80000, 180000],
                [mid_account_deploy_ratio, large_account_deploy_ratio],
            )
        )

    decayed_ratio = large_account_deploy_ratio - (
        trade_layer_decay_coeff * np.log10(total_equity_usd / 180000)
    )
    return max(0.0, decayed_ratio)


def get_income_layer_ratio(
    total_equity_usd,
    *,
    income_layer_start_usd,
    income_layer_max_ratio,
):
    if total_equity_usd <= income_layer_start_usd:
        return 0.0

    if total_equity_usd <= (income_layer_start_usd * 2):
        return float(
            np.interp(
                total_equity_usd,
                [income_layer_start_usd, income_layer_start_usd * 2],
                [0.0, income_layer_max_ratio],
            )
        )

    return income_layer_max_ratio


def build_rebalance_plan(
    indicators,
    account_state,
    *,
    trend_ma_window,
    translator,
    cash_reserve_ratio,
    min_trade_ratio,
    min_trade_floor,
    rebalance_threshold_ratio,
    small_account_deploy_ratio,
    mid_account_deploy_ratio,
    large_account_deploy_ratio,
    trade_layer_decay_coeff,
    income_layer_start_usd,
    income_layer_max_ratio,
    income_layer_qqqi_weight,
    income_layer_spyi_weight,
    trend_entry_buffer=0.03,
    trend_mid_buffer=0.06,
    trend_exit_buffer=0.03,
    attack_allocation_mode="trend_switch",
    dual_drive_soxx_weight=0.45,
    dual_drive_soxl_weight=0.45,
    dual_drive_allow_pullback=True,
    dual_drive_require_ma20_slope=True,
    dual_drive_trend_source="SOXX",
    blend_gate_trend_source="SOXX",
    blend_gate_soxl_weight=0.75,
    blend_gate_mid_soxl_weight=0.65,
    blend_gate_active_soxx_weight=0.20,
    blend_gate_defensive_soxx_weight=0.15,
):
    strategy_assets = ["SOXL", "SOXX", "BOXX", "QQQI", "SPYI"]
    available_cash = account_state["available_cash"]
    market_values = account_state["market_values"]
    quantities = account_state["quantities"]
    sellable_quantities = account_state["sellable_quantities"]
    total_strategy_equity = account_state["total_strategy_equity"]
    current_min_trade = max(min_trade_floor, total_strategy_equity * min_trade_ratio)

    current_income_layer_value = market_values["QQQI"] + market_values["SPYI"]
    income_layer_ratio = get_income_layer_ratio(
        total_strategy_equity,
        income_layer_start_usd=income_layer_start_usd,
        income_layer_max_ratio=income_layer_max_ratio,
    )
    desired_income_layer_value = total_strategy_equity * income_layer_ratio
    locked_income_layer_value = max(current_income_layer_value, desired_income_layer_value)
    income_layer_add_value = max(0.0, locked_income_layer_value - current_income_layer_value)
    core_equity = max(0.0, total_strategy_equity - locked_income_layer_value)
    deploy_ratio = get_dynamic_allocation(
        core_equity,
        small_account_deploy_ratio=small_account_deploy_ratio,
        mid_account_deploy_ratio=mid_account_deploy_ratio,
        large_account_deploy_ratio=large_account_deploy_ratio,
        trade_layer_decay_coeff=trade_layer_decay_coeff,
    )
    deployed_capital = core_equity * deploy_ratio
    deploy_ratio_text = f"{deploy_ratio * 100:.1f}%"
    income_ratio_text = f"{income_layer_ratio * 100:.1f}%"
    income_locked_ratio_text = (
        f"{(locked_income_layer_value / total_strategy_equity) * 100:.1f}%"
        if total_strategy_equity > 0
        else "0.0%"
    )

    soxl_price = float(_indicator_value(indicators, "SOXL", "price"))
    soxl_ma_trend = float(_indicator_value(indicators, "SOXL", "ma_trend"))
    allocation_mode = str(attack_allocation_mode or "trend_switch").strip().lower()
    fixed_dual_drive_enabled = allocation_mode == FIXED_DUAL_DRIVE_MODE
    soxx_gate_blend_enabled = allocation_mode == SOXX_GATE_BLEND_MODE
    soxx_gate_tiered_blend_enabled = allocation_mode == SOXX_GATE_TIERED_BLEND_MODE
    entry_buffer = _as_clamped_ratio(trend_entry_buffer, default=0.03, upper=0.25)
    mid_buffer = _as_clamped_ratio(trend_mid_buffer, default=min(0.06, entry_buffer), upper=0.25)
    mid_buffer = min(mid_buffer, entry_buffer)
    exit_buffer = _as_clamped_ratio(trend_exit_buffer, default=0.03, upper=0.25)
    soxl_entry_line = soxl_ma_trend * (1.0 + entry_buffer)
    soxl_exit_line = soxl_ma_trend * (1.0 - exit_buffer)
    current_soxl_active = quantities.get("SOXL", 0) > 0 or market_values.get("SOXL", 0.0) > current_min_trade
    if current_soxl_active:
        active_risk_asset = "SOXL" if soxl_price > soxl_exit_line else "SOXX"
    else:
        active_risk_asset = "SOXL" if soxl_price > soxl_entry_line else "SOXX"

    trend_source = (
        blend_gate_trend_source
        if soxx_gate_blend_enabled or soxx_gate_tiered_blend_enabled
        else dual_drive_trend_source
    )
    trend_symbol = str(trend_source or "SOXX").strip().upper()
    trend_price = _as_float_or_none(_indicator_value(indicators, trend_symbol, "price"))
    trend_ma = _as_float_or_none(_indicator_value(indicators, trend_symbol, "ma_trend"))
    trend_ma20 = _as_float_or_none(_indicator_value(indicators, trend_symbol, "ma20"))
    trend_ma20_slope = _as_float_or_none(_indicator_value(indicators, trend_symbol, "ma20_slope"))
    if trend_price is None:
        trend_price = soxl_price
    if trend_ma is None:
        trend_ma = soxl_ma_trend
    trend_entry_line = trend_ma * (1.0 + entry_buffer)
    trend_mid_line = trend_ma * (1.0 + mid_buffer)
    trend_exit_line = trend_ma * (1.0 - exit_buffer)
    market_status = _translate_with_fallback(
        translator,
        "market_status_risk_on" if active_risk_asset == "SOXL" else "market_status_delever",
        f"🚀 RISK-ON ({active_risk_asset})"
        if active_risk_asset == "SOXL"
        else f"🛡️ DE-LEVER ({active_risk_asset})",
        asset=active_risk_asset,
    )
    signal_message = _translate_with_fallback(
        translator,
        "signal_risk_on" if active_risk_asset == "SOXL" else "signal_delever",
        f"SOXL above {trend_ma_window}d MA, hold SOXL, risk {deploy_ratio_text}"
        if active_risk_asset == "SOXL"
        else f"SOXL below {trend_ma_window}d MA, switch to SOXX, risk {deploy_ratio_text}",
        window=trend_ma_window,
        ratio=deploy_ratio_text,
    )
    blend_tier = None

    if soxx_gate_blend_enabled or soxx_gate_tiered_blend_enabled:
        current_blend_active = quantities.get("SOXL", 0) > 0 or market_values.get("SOXL", 0.0) > current_min_trade
        target_soxl_ratio = _as_clamped_ratio(blend_gate_soxl_weight, default=0.75)
        target_mid_soxl_ratio = _as_clamped_ratio(blend_gate_mid_soxl_weight, default=0.65)
        target_active_soxx_ratio = _as_clamped_ratio(blend_gate_active_soxx_weight, default=0.20)
        target_defensive_soxx_ratio = _as_clamped_ratio(blend_gate_defensive_soxx_weight, default=0.15)

        blend_tier = "defensive"
        if soxx_gate_tiered_blend_enabled:
            if trend_price > trend_entry_line:
                blend_tier = "full"
            elif trend_price > trend_mid_line or (current_blend_active and trend_price > trend_exit_line):
                blend_tier = "mid"
        elif current_blend_active:
            blend_tier = "full" if trend_price > trend_exit_line else "defensive"
        else:
            blend_tier = "full" if trend_price > trend_entry_line else "defensive"

        selected_soxl_ratio = target_soxl_ratio
        if blend_tier == "mid":
            selected_soxl_ratio = min(target_mid_soxl_ratio, target_soxl_ratio)

        active_risk_ratio = selected_soxl_ratio + target_active_soxx_ratio
        if active_risk_ratio > 1.0:
            scale = 1.0 / active_risk_ratio
            selected_soxl_ratio *= scale
            target_active_soxx_ratio *= scale
            active_risk_ratio = 1.0

        if blend_tier in {"full", "mid"}:
            soxl_target = core_equity * selected_soxl_ratio
            soxx_target = core_equity * target_active_soxx_ratio
            active_risk_asset = "SOXX+SOXL"
            deploy_ratio_text = f"{active_risk_ratio * 100:.1f}%"
            market_status = _translate_with_fallback(
                translator,
                "market_status_blend_gate_risk_on",
                f"🚀 RISK-ON ({active_risk_asset})",
                asset=active_risk_asset,
            )
            signal_message = _translate_with_fallback(
                translator,
                "signal_blend_gate_risk_on",
                (
                    f"{trend_symbol} above {trend_ma_window}d gated entry, hold "
                    f"SOXL {selected_soxl_ratio * 100:.1f}% + SOXX {target_active_soxx_ratio * 100:.1f}%"
                ),
                trend_symbol=trend_symbol,
                window=trend_ma_window,
                soxl_ratio=f"{selected_soxl_ratio * 100:.1f}%",
                soxx_ratio=f"{target_active_soxx_ratio * 100:.1f}%",
            )
        else:
            soxl_target = 0.0
            soxx_target = core_equity * target_defensive_soxx_ratio
            active_risk_asset = "SOXX"
            deploy_ratio_text = f"{target_defensive_soxx_ratio * 100:.1f}%"
            market_status = _translate_with_fallback(
                translator,
                "market_status_blend_gate_defensive",
                f"🛡️ DE-LEVER ({active_risk_asset})",
                asset=active_risk_asset,
            )
            signal_message = _translate_with_fallback(
                translator,
                "signal_blend_gate_defensive",
                f"{trend_symbol} below gated entry, hold defensive SOXX {target_defensive_soxx_ratio * 100:.1f}%",
                trend_symbol=trend_symbol,
                window=trend_ma_window,
                soxx_ratio=f"{target_defensive_soxx_ratio * 100:.1f}%",
            )
        targets = {
            "SOXL": soxl_target,
            "SOXX": soxx_target,
            "QQQI": market_values["QQQI"] + (income_layer_add_value * income_layer_qqqi_weight),
            "SPYI": market_values["SPYI"] + (income_layer_add_value * income_layer_spyi_weight),
            "BOXX": max(0.0, core_equity - soxl_target - soxx_target),
        }
    elif fixed_dual_drive_enabled:
        above_trend = bool(trend_price > trend_ma)
        positive_ma20_slope = trend_ma20_slope is not None and trend_ma20_slope > 0.0
        slope_ok = positive_ma20_slope if bool(dual_drive_require_ma20_slope) else True
        current_risk_active = quantities.get("SOXL", 0) > 0 or quantities.get("SOXX", 0) > 0
        risk_active = current_risk_active
        if current_risk_active and not above_trend:
            risk_active = False
        elif not current_risk_active and above_trend and slope_ok:
            risk_active = True
        pullback_risk_on = (
            bool(dual_drive_allow_pullback)
            and not above_trend
            and trend_ma20 is not None
            and trend_price > trend_ma20
            and positive_ma20_slope
        )
        if risk_active or pullback_risk_on:
            target_soxl_ratio = max(0.0, min(1.0, float(dual_drive_soxl_weight or 0.45)))
            target_soxx_ratio = max(0.0, min(1.0, float(dual_drive_soxx_weight or 0.45)))
            total_risk_ratio = target_soxl_ratio + target_soxx_ratio
            if total_risk_ratio > 1.0 and total_risk_ratio > 0.0:
                scale = 1.0 / total_risk_ratio
                target_soxl_ratio *= scale
                target_soxx_ratio *= scale
            soxl_target = core_equity * target_soxl_ratio
            soxx_target = core_equity * target_soxx_ratio
            active_risk_asset = "SOXX+SOXL"
            market_status = _translate_with_fallback(
                translator,
                "market_status_dual_drive",
                f"🚀 RISK-ON ({active_risk_asset})",
                asset=active_risk_asset,
            )
            signal_message = _translate_with_fallback(
                translator,
                "signal_dual_drive_risk_on",
                (
                    f"{trend_symbol} risk gate active, hold SOXX {target_soxx_ratio * 100:.1f}% "
                    f"+ SOXL {target_soxl_ratio * 100:.1f}%"
                ),
                trend_symbol=trend_symbol,
                soxx_ratio=f"{target_soxx_ratio * 100:.1f}%",
                soxl_ratio=f"{target_soxl_ratio * 100:.1f}%",
            )
        else:
            soxl_target = 0.0
            soxx_target = 0.0
            deployed_capital = 0.0
            active_risk_asset = "BOXX"
            market_status = _translate_with_fallback(
                translator,
                "market_status_dual_drive_flat",
                "🛡️ DEFENSIVE (BOXX)",
                asset=active_risk_asset,
            )
            signal_message = _translate_with_fallback(
                translator,
                "signal_dual_drive_flat",
                f"{trend_symbol} below trend gate, park core sleeve in BOXX",
                trend_symbol=trend_symbol,
            )
        targets = {
            "SOXL": soxl_target,
            "SOXX": soxx_target,
            "QQQI": market_values["QQQI"] + (income_layer_add_value * income_layer_qqqi_weight),
            "SPYI": market_values["SPYI"] + (income_layer_add_value * income_layer_spyi_weight),
            "BOXX": max(0.0, core_equity - soxl_target - soxx_target),
        }
    else:
        targets = {
            "SOXL": deployed_capital if active_risk_asset == "SOXL" else 0.0,
            "SOXX": deployed_capital if active_risk_asset == "SOXX" else 0.0,
            "QQQI": market_values["QQQI"] + (income_layer_add_value * income_layer_qqqi_weight),
            "SPYI": market_values["SPYI"] + (income_layer_add_value * income_layer_spyi_weight),
            "BOXX": max(0.0, core_equity - deployed_capital),
        }

    return {
        "strategy_assets": strategy_assets,
        # Execution metadata consumed by downstream platform repos.
        "limit_order_symbols": ("SOXL", "SOXX", "QQQI", "SPYI"),
        "portfolio_rows": (("SOXL", "SOXX"), ("QQQI", "SPYI"), ("BOXX",)),
        "available_cash": available_cash,
        "market_values": market_values,
        "quantities": quantities,
        "sellable_quantities": sellable_quantities,
        "total_strategy_equity": total_strategy_equity,
        "current_min_trade": current_min_trade,
        "targets": targets,
        "market_status": market_status,
        "signal_message": signal_message,
        "deploy_ratio_text": deploy_ratio_text,
        "income_ratio_text": income_ratio_text,
        "income_locked_ratio_text": income_locked_ratio_text,
        "active_risk_asset": active_risk_asset,
        "investable_cash": max(0, available_cash - (total_strategy_equity * cash_reserve_ratio)),
        "threshold_value": total_strategy_equity * rebalance_threshold_ratio,
        "allocation_mode": allocation_mode,
        "trend_entry_buffer": entry_buffer,
        "trend_mid_buffer": mid_buffer,
        "trend_exit_buffer": exit_buffer,
        "blend_tier": blend_tier,
        "soxl_entry_line": soxl_entry_line,
        "soxl_exit_line": soxl_exit_line,
        "trend_entry_line": trend_entry_line,
        "trend_mid_line": trend_mid_line,
        "trend_exit_line": trend_exit_line,
        "trend_symbol": trend_symbol,
        "trend_price": trend_price,
        "trend_ma": trend_ma,
        "trend_ma20": trend_ma20,
        "trend_ma20_slope": trend_ma20_slope,
    }
