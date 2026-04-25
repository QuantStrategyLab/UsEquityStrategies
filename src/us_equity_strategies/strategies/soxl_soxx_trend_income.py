from __future__ import annotations

import numpy as np


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


def _as_bool(value, *, default=False):
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _downgrade_tier(tier: str, steps: int) -> str:
    tiers = ("full", "mid", "defensive")
    return tiers[min(tiers.index(tier) + steps, len(tiers) - 1)]


def _resolve_tier_allocations(
    *,
    tier: str,
    full_soxl_ratio: float,
    mid_soxl_ratio: float,
    active_soxx_ratio: float,
    defensive_soxx_ratio: float,
) -> tuple[float, float, float, str]:
    if tier == "full":
        soxl_ratio = full_soxl_ratio
        soxx_ratio = active_soxx_ratio
        active_risk_asset = "SOXX+SOXL"
    elif tier == "mid":
        soxl_ratio = min(mid_soxl_ratio, full_soxl_ratio)
        soxx_ratio = active_soxx_ratio
        active_risk_asset = "SOXX+SOXL"
    elif tier == "defensive":
        soxl_ratio = 0.0
        soxx_ratio = defensive_soxx_ratio
        active_risk_asset = "SOXX"
    else:
        raise KeyError(f"Unknown blend tier: {tier}")

    active_risk_ratio = soxl_ratio + soxx_ratio
    if active_risk_ratio > 1.0:
        scale = 1.0 / active_risk_ratio
        soxl_ratio *= scale
        soxx_ratio *= scale
        active_risk_ratio = 1.0
    boxx_ratio = max(0.0, 1.0 - active_risk_ratio)
    return soxl_ratio, soxx_ratio, boxx_ratio, active_risk_asset


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
    income_layer_start_usd,
    income_layer_max_ratio,
    income_layer_qqqi_weight,
    income_layer_spyi_weight,
    trend_entry_buffer=0.03,
    trend_mid_buffer=0.06,
    trend_exit_buffer=0.03,
    attack_allocation_mode=SOXX_GATE_TIERED_BLEND_MODE,
    blend_gate_trend_source="SOXX",
    blend_gate_soxl_weight=0.75,
    blend_gate_mid_soxl_weight=0.65,
    blend_gate_active_soxx_weight=0.20,
    blend_gate_defensive_soxx_weight=0.15,
    blend_gate_rsi_cap_enabled=False,
    blend_gate_rsi_threshold=70.0,
    blend_gate_bollinger_cap_enabled=False,
    blend_gate_overlay_stack_triggers=False,
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
    deploy_ratio_text = "0.0%"
    income_ratio_text = f"{income_layer_ratio * 100:.1f}%"
    income_locked_ratio_text = (
        f"{(locked_income_layer_value / total_strategy_equity) * 100:.1f}%"
        if total_strategy_equity > 0
        else "0.0%"
    )

    soxl_price = float(_indicator_value(indicators, "SOXL", "price"))
    soxl_ma_trend = float(_indicator_value(indicators, "SOXL", "ma_trend"))
    allocation_mode = str(attack_allocation_mode or SOXX_GATE_TIERED_BLEND_MODE).strip().lower()
    if allocation_mode != SOXX_GATE_TIERED_BLEND_MODE:
        raise ValueError("soxl_soxx_trend_income only supports soxx_gate_tiered_blend")
    entry_buffer = _as_clamped_ratio(trend_entry_buffer, default=0.03, upper=0.25)
    mid_buffer = _as_clamped_ratio(trend_mid_buffer, default=min(0.06, entry_buffer), upper=0.25)
    mid_buffer = min(mid_buffer, entry_buffer)
    exit_buffer = _as_clamped_ratio(trend_exit_buffer, default=0.03, upper=0.25)
    soxl_entry_line = soxl_ma_trend * (1.0 + entry_buffer)
    soxl_exit_line = soxl_ma_trend * (1.0 - exit_buffer)

    trend_symbol = str(blend_gate_trend_source or "SOXX").strip().upper()
    trend_price = _as_float_or_none(_indicator_value(indicators, trend_symbol, "price"))
    trend_ma = _as_float_or_none(_indicator_value(indicators, trend_symbol, "ma_trend"))
    trend_ma20 = _as_float_or_none(_indicator_value(indicators, trend_symbol, "ma20"))
    trend_ma20_slope = _as_float_or_none(_indicator_value(indicators, trend_symbol, "ma20_slope"))
    trend_rsi14 = _as_float_or_none(_indicator_value(indicators, trend_symbol, "rsi14"))
    trend_bb_mid = _as_float_or_none(_indicator_value(indicators, trend_symbol, "bb_mid"))
    trend_bb_upper = _as_float_or_none(_indicator_value(indicators, trend_symbol, "bb_upper"))
    trend_bb_lower = _as_float_or_none(_indicator_value(indicators, trend_symbol, "bb_lower"))
    if trend_price is None:
        trend_price = soxl_price
    if trend_ma is None:
        trend_ma = soxl_ma_trend
    trend_entry_line = trend_ma * (1.0 + entry_buffer)
    trend_mid_line = trend_ma * (1.0 + mid_buffer)
    trend_exit_line = trend_ma * (1.0 - exit_buffer)
    current_blend_active = quantities.get("SOXL", 0) > 0 or market_values.get("SOXL", 0.0) > current_min_trade
    target_soxl_ratio = _as_clamped_ratio(blend_gate_soxl_weight, default=0.75)
    target_mid_soxl_ratio = _as_clamped_ratio(blend_gate_mid_soxl_weight, default=0.65)
    target_active_soxx_ratio = _as_clamped_ratio(blend_gate_active_soxx_weight, default=0.20)
    target_defensive_soxx_ratio = _as_clamped_ratio(blend_gate_defensive_soxx_weight, default=0.15)
    use_rsi_cap = _as_bool(blend_gate_rsi_cap_enabled, default=False)
    use_bollinger_cap = _as_bool(blend_gate_bollinger_cap_enabled, default=False)
    stack_overlay_triggers = _as_bool(blend_gate_overlay_stack_triggers, default=False)
    rsi_threshold = _as_float_or_none(blend_gate_rsi_threshold)
    if rsi_threshold is None:
        rsi_threshold = 70.0

    blend_tier = "defensive"
    if trend_price > trend_entry_line:
        blend_tier = "full"
    elif trend_price > trend_mid_line or (current_blend_active and trend_price > trend_exit_line):
        blend_tier = "mid"
    base_blend_tier = blend_tier
    overlay_trigger_reasons: list[str] = []
    overlay_trigger_codes: list[str] = []
    if base_blend_tier in {"full", "mid"}:
        if use_rsi_cap and trend_rsi14 is not None and trend_rsi14 > rsi_threshold:
            overlay_trigger_codes.append("blend_gate_reason_rsi_cap")
            overlay_trigger_reasons.append(
                _translate_with_fallback(translator, "blend_gate_reason_rsi_cap", f"RSI>{rsi_threshold:.0f}")
            )
        if use_bollinger_cap and trend_bb_upper is not None and trend_price > trend_bb_upper:
            overlay_trigger_codes.append("blend_gate_reason_bollinger_cap")
            overlay_trigger_reasons.append(
                _translate_with_fallback(
                    translator,
                    "blend_gate_reason_bollinger_cap",
                    "price>upper band",
                )
            )
    overlay_trigger_count = len(overlay_trigger_reasons)
    if overlay_trigger_count > 0:
        blend_tier = _downgrade_tier(base_blend_tier, overlay_trigger_count if stack_overlay_triggers else 1)

    selected_soxl_ratio, selected_soxx_ratio, boxx_ratio, active_risk_asset = _resolve_tier_allocations(
        tier=blend_tier,
        full_soxl_ratio=target_soxl_ratio,
        mid_soxl_ratio=target_mid_soxl_ratio,
        active_soxx_ratio=target_active_soxx_ratio,
        defensive_soxx_ratio=target_defensive_soxx_ratio,
    )
    soxl_target = core_equity * selected_soxl_ratio
    soxx_target = core_equity * selected_soxx_ratio
    deploy_ratio_text = f"{(selected_soxl_ratio + selected_soxx_ratio) * 100:.1f}%"
    if selected_soxl_ratio > 0.0:
        allocation_text = f"SOXL {selected_soxl_ratio * 100:.1f}% + SOXX {selected_soxx_ratio * 100:.1f}%"
    else:
        allocation_text = f"SOXX {selected_soxx_ratio * 100:.1f}%"

    if overlay_trigger_count > 0:
        status_context = {
            "code": "market_status_blend_gate_overlay_capped",
            "fallback": f"🧯 HEAT-CAP ({active_risk_asset})",
            "params": {"asset": active_risk_asset},
        }
        signal_context = {
            "code": "signal_blend_gate_overlay_capped",
            "fallback": (
                f"{trend_symbol} above {trend_ma_window}d gated entry, but overlay cap "
                f"({', '.join(overlay_trigger_reasons)}) reduces to {allocation_text}"
            ),
            "params": {
                "trend_symbol": trend_symbol,
                "window": trend_ma_window,
                "reasons": " + ".join(overlay_trigger_reasons),
                "allocation_text": allocation_text,
            },
        }
    elif blend_tier in {"full", "mid"}:
        status_context = {
            "code": "market_status_blend_gate_risk_on",
            "fallback": f"🚀 RISK-ON ({active_risk_asset})",
            "params": {"asset": active_risk_asset},
        }
        signal_context = {
            "code": "signal_blend_gate_risk_on",
            "fallback": (
                f"{trend_symbol} above {trend_ma_window}d gated entry, hold "
                f"SOXL {selected_soxl_ratio * 100:.1f}% + SOXX {selected_soxx_ratio * 100:.1f}%"
            ),
            "params": {
                "trend_symbol": trend_symbol,
                "window": trend_ma_window,
                "soxl_ratio": f"{selected_soxl_ratio * 100:.1f}%",
                "soxx_ratio": f"{selected_soxx_ratio * 100:.1f}%",
            },
        }
    else:
        status_context = {
            "code": "market_status_blend_gate_defensive",
            "fallback": f"🛡️ DE-LEVER ({active_risk_asset})",
            "params": {"asset": active_risk_asset},
        }
        signal_context = {
            "code": "signal_blend_gate_defensive",
            "fallback": (
                f"{trend_symbol} below gated entry, hold defensive SOXX {selected_soxx_ratio * 100:.1f}%"
            ),
            "params": {
                "trend_symbol": trend_symbol,
                "window": trend_ma_window,
                "soxx_ratio": f"{selected_soxx_ratio * 100:.1f}%",
            },
        }
    market_status = _translate_with_fallback(
        translator,
        status_context["code"],
        status_context["fallback"],
        **status_context["params"],
    )
    signal_message = _translate_with_fallback(
        translator,
        signal_context["code"],
        signal_context["fallback"],
        **signal_context["params"],
    )
    targets = {
        "SOXL": soxl_target,
        "SOXX": soxx_target,
        "QQQI": market_values["QQQI"] + (income_layer_add_value * income_layer_qqqi_weight),
        "SPYI": market_values["SPYI"] + (income_layer_add_value * income_layer_spyi_weight),
        "BOXX": max(0.0, core_equity * boxx_ratio),
    }
    reserved_cash = max(0.0, total_strategy_equity * cash_reserve_ratio)
    investable_cash = max(0.0, available_cash - reserved_cash)
    benchmark_context = {
        "symbol": trend_symbol,
        "price": trend_price,
        "long_trend_value": trend_ma,
        "entry_line": trend_entry_line,
        "mid_line": trend_mid_line,
        "exit_line": trend_exit_line,
        "ma20": trend_ma20,
        "ma20_slope": trend_ma20_slope,
        "rsi14": trend_rsi14,
        "bb_mid": trend_bb_mid,
        "bb_upper": trend_bb_upper,
        "bb_lower": trend_bb_lower,
        "overlay_trigger_count": overlay_trigger_count,
        "overlay_trigger_reasons": tuple(overlay_trigger_reasons),
    }
    portfolio_context = {
        "total_equity": float(total_strategy_equity),
        "raw_buying_power": float(available_cash),
        "available_cash": float(available_cash),
        "reserved_cash": float(reserved_cash),
        "investable_cash": float(investable_cash),
        "holdings_order": tuple(strategy_assets),
        "holdings": {
            symbol: {
                "market_value": float(market_values[symbol]),
                "quantity": int(quantities[symbol]),
            }
            for symbol in strategy_assets
        },
    }
    notification_context = {
        "status": status_context,
        "signal": signal_context,
        "benchmark": benchmark_context,
        "portfolio": portfolio_context,
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
        "notification_context": notification_context,
        "deploy_ratio_text": deploy_ratio_text,
        "income_ratio_text": income_ratio_text,
        "income_locked_ratio_text": income_locked_ratio_text,
        "active_risk_asset": active_risk_asset,
        "reserved_cash": reserved_cash,
        "investable_cash": investable_cash,
        "threshold_value": total_strategy_equity * rebalance_threshold_ratio,
        "allocation_mode": allocation_mode,
        "trend_entry_buffer": entry_buffer,
        "trend_mid_buffer": mid_buffer,
        "trend_exit_buffer": exit_buffer,
        "blend_tier": blend_tier,
        "base_blend_tier": base_blend_tier,
        "overlay_trigger_count": overlay_trigger_count,
        "overlay_trigger_reasons": tuple(overlay_trigger_reasons),
        "overlay_trigger_codes": tuple(overlay_trigger_codes),
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
        "trend_rsi14": trend_rsi14,
        "trend_bb_mid": trend_bb_mid,
        "trend_bb_upper": trend_bb_upper,
        "trend_bb_lower": trend_bb_lower,
        "blend_gate_rsi_cap_enabled": use_rsi_cap,
        "blend_gate_rsi_threshold": rsi_threshold,
        "blend_gate_bollinger_cap_enabled": use_bollinger_cap,
        "blend_gate_overlay_stack_triggers": stack_overlay_triggers,
    }
