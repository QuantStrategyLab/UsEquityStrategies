from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np

from us_equity_strategies.income_layer import (
    INCOME_LAYER_RATIO_MODE_LOG_TOTAL_DRAWDOWN_BUDGET,
    INCOME_LAYER_RATIO_MODES,
    build_income_layer_plan,
    get_income_layer_ratio,
    normalize_income_layer_allocations,
)
from us_equity_strategies.market_regime_control_contract import (
    resolve_market_regime_position_control_authorization,
)
from us_equity_strategies.volatility_delever_retention import (
    POLICY_SOXL_STEP_REBOUND_025_050,
    RETENTION_MODE_FIXED,
    resolve_volatility_delever_retention,
)


SOXX_GATE_TIERED_BLEND_MODE = "soxx_gate_tiered_blend"
CORE_ASSETS = ("SOXL", "SOXX", "BOXX")
VOLATILITY_DELEVER_THRESHOLD_MODE_FIXED = "fixed"
VOLATILITY_DELEVER_THRESHOLD_MODE_ROLLING_PERCENTILE = "rolling_percentile"
VOLATILITY_DELEVER_THRESHOLD_MODES = frozenset(
    {VOLATILITY_DELEVER_THRESHOLD_MODE_FIXED, VOLATILITY_DELEVER_THRESHOLD_MODE_ROLLING_PERCENTILE}
)
MARKET_REGIME_CONTROL_PROFILE = "market_regime_control"
MARKET_REGIME_POSITION_ROUTES = frozenset({"risk_reduced", "risk_off"})
LEGACY_CRISIS_RESPONSE_PROFILE = "crisis_response_shadow"
LEGACY_TRUE_CRISIS_ROUTE = "true_crisis"
__all__ = [
    "INCOME_LAYER_RATIO_MODE_LOG_TOTAL_DRAWDOWN_BUDGET",
    "INCOME_LAYER_RATIO_MODES",
    "SOXX_GATE_TIERED_BLEND_MODE",
    "VOLATILITY_DELEVER_THRESHOLD_MODE_FIXED",
    "VOLATILITY_DELEVER_THRESHOLD_MODE_ROLLING_PERCENTILE",
    "build_rebalance_plan",
    "get_income_layer_ratio",
]


def _translate_with_fallback(translator, key, fallback, **kwargs):
    rendered = translator(key, **kwargs)
    return fallback if rendered == key else rendered


def _format_percent(value) -> str:
    result = _as_float_or_none(value)
    if result is None:
        return "n/a"
    return f"{result * 100:.1f}%"


def _format_percentile(value) -> str:
    result = _as_float_or_none(value)
    if result is None:
        return "p?"
    percentile = result * 100
    if float(percentile).is_integer():
        return f"p{int(percentile)}"
    return f"p{percentile:.1f}"


def _format_sample_count(value) -> str:
    result = _as_float_or_none(value)
    if result is None:
        return "n/a"
    if float(result).is_integer():
        return str(int(result))
    return f"{result:.1f}"


def _format_volatility_delever_threshold_detail(
    translator,
    *,
    threshold_mode,
    fixed_threshold,
    dynamic_threshold,
    dynamic_sample_count,
    dynamic_lookback,
    dynamic_percentile,
    dynamic_min_periods,
    dynamic_floor,
    dynamic_cap,
) -> str:
    mode = str(threshold_mode or "").strip().lower()
    if mode == VOLATILITY_DELEVER_THRESHOLD_MODE_ROLLING_PERCENTILE:
        base_kwargs = {
            "percentile": _format_percentile(dynamic_percentile),
            "lookback": _format_sample_count(dynamic_lookback),
            "min_periods": _format_sample_count(dynamic_min_periods),
            "sample_count": _format_sample_count(dynamic_sample_count),
            "floor": _format_percent(dynamic_floor),
            "cap": _format_percent(dynamic_cap),
            "fixed_threshold": _format_percent(fixed_threshold),
        }
        if dynamic_threshold is not None:
            return _translate_with_fallback(
                translator,
                "blend_gate_volatility_threshold_detail_dynamic",
                (
                    f"dynamic {base_kwargs['percentile']}, {base_kwargs['lookback']}d lookback, "
                    f"bounded {base_kwargs['floor']}-{base_kwargs['cap']}, "
                    f"samples {base_kwargs['sample_count']}"
                ),
                **base_kwargs,
            )
        return _translate_with_fallback(
            translator,
            "blend_gate_volatility_threshold_detail_dynamic_fallback",
            (
                f"dynamic warm-up, fallback fixed {base_kwargs['fixed_threshold']} "
                f"(samples {base_kwargs['sample_count']}/{base_kwargs['min_periods']}, "
                f"{base_kwargs['percentile']})"
            ),
            **base_kwargs,
        )
    return _translate_with_fallback(
        translator,
        "blend_gate_volatility_threshold_detail_fixed",
        f"fixed threshold {_format_percent(fixed_threshold)}",
        threshold=_format_percent(fixed_threshold),
    )


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


def _as_positive_int(value, *, default: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = int(default)
    return max(1, result)


def _as_unit_interval(value, *, default: float) -> float:
    result = _as_float_or_none(value)
    if result is None or result <= 0.0 or result >= 1.0:
        return float(default)
    return float(result)


def _indicator_first_float(indicators, symbol: str, keys: Sequence[str]) -> float | None:
    for key in keys:
        value = _as_float_or_none(_indicator_value(indicators, symbol, key))
        if value is not None:
            return value
    return None


def _iter_mapping_payloads(value, *, _depth: int = 0):
    if _depth > 4:
        return
    if isinstance(value, Mapping):
        yield value
        for item in value.values():
            yield from _iter_mapping_payloads(item, _depth=_depth + 1)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            yield from _iter_mapping_payloads(item, _depth=_depth + 1)


def _route_from_payload(payload: Mapping) -> str:
    return str(payload.get("canonical_route") or payload.get("route") or "").strip().lower()


def _normalized_text_tuple(value) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _market_regime_context_not_found() -> dict[str, object]:
    return {
        "found": False,
        "source": MARKET_REGIME_CONTROL_PROFILE,
        "schema_version": "",
        "active": False,
        "route": "",
        "route_source": "",
        "suggested_action": "",
        "position_control_allowed": False,
        "position_control_authorized": False,
        "consumption_evidence_status": "",
        "risk_budget_scalar": 1.0,
        "leverage_scalar": 1.0,
        "risk_asset_scalar": 1.0,
        "taco_allowed": False,
        "local_delever_veto_allowed": False,
        "crisis_defense_required": False,
        "blocked_actions": (),
        "vetoes": (),
        "reason_codes": (),
        "localized_messages": {},
        "log_record": {},
        "notification": {},
        "blocked": False,
        "volatility_delever_context": {},
    }


def _resolve_market_regime_control_context(metadata: Mapping) -> dict[str, object]:
    for payload in _iter_mapping_payloads(metadata):
        plugin = str(payload.get("plugin") or payload.get("profile") or "").strip().lower()
        if plugin != MARKET_REGIME_CONTROL_PROFILE:
            continue
        authorization = resolve_market_regime_position_control_authorization(payload)
        position_control_allowed = bool(authorization["position_control_allowed"])
        position_control_authorized = bool(authorization["position_control_authorized"])
        evidence_status = str(authorization["consumption_evidence_status"])
        position_control = payload.get("position_control")
        if not isinstance(position_control, Mapping):
            position_control = {}
        arbiter = payload.get("arbiter")
        if not isinstance(arbiter, Mapping):
            arbiter = {}
        route = str(
            position_control.get("final_route")
            or arbiter.get("final_route")
            or payload.get("canonical_route")
            or ""
        ).strip().lower()
        suggested_action = str(
            position_control.get("suggested_action")
            or arbiter.get("suggested_action")
            or payload.get("suggested_action")
            or ""
        ).strip().lower()
        blocked = suggested_action == "blocked"
        volatility_delever_context = position_control.get("volatility_delever_context")
        if not isinstance(volatility_delever_context, Mapping):
            volatility_delever_context = payload.get("volatility_delever_context")
        if not isinstance(volatility_delever_context, Mapping):
            volatility_delever_context = {}
        return {
            "found": True,
            "source": MARKET_REGIME_CONTROL_PROFILE,
            "schema_version": str(payload.get("schema_version") or "").strip(),
            "active": route in MARKET_REGIME_POSITION_ROUTES and not blocked and position_control_authorized,
            "route": route,
            "route_source": str(position_control.get("route_source") or arbiter.get("route_source") or "").strip(),
            "suggested_action": suggested_action,
            "position_control_allowed": position_control_allowed,
            "position_control_authorized": position_control_authorized,
            "consumption_evidence_status": evidence_status,
            "risk_budget_scalar": _as_clamped_ratio(position_control.get("risk_budget_scalar"), default=1.0),
            "leverage_scalar": _as_clamped_ratio(position_control.get("leverage_scalar"), default=1.0),
            "risk_asset_scalar": _as_clamped_ratio(position_control.get("risk_asset_scalar"), default=1.0),
            "taco_allowed": position_control_authorized
            and _as_bool(position_control.get("taco_allowed"), default=False),
            "local_delever_veto_allowed": position_control_authorized
            and _as_bool(position_control.get("local_delever_veto_allowed"), default=False),
            "crisis_defense_required": position_control_authorized
            and _as_bool(position_control.get("crisis_defense_required"), default=False),
            "blocked_actions": _normalized_text_tuple(position_control.get("blocked_actions")),
            "vetoes": _normalized_text_tuple(position_control.get("vetoes")) or _normalized_text_tuple(arbiter.get("vetoes")),
            "reason_codes": (
                _normalized_text_tuple(position_control.get("reason_codes"))
                or _normalized_text_tuple(arbiter.get("reason_codes"))
                or _normalized_text_tuple(payload.get("reason_codes"))
            ),
            "localized_messages": payload.get("localized_messages")
            if isinstance(payload.get("localized_messages"), Mapping)
            else {},
            "log_record": payload.get("log_record") if isinstance(payload.get("log_record"), Mapping) else {},
            "notification": payload.get("notification") if isinstance(payload.get("notification"), Mapping) else {},
            "blocked": blocked,
            "volatility_delever_context": volatility_delever_context if position_control_authorized else {},
        }
    return _market_regime_context_not_found()


def _resolve_legacy_crisis_response_context(metadata: Mapping) -> dict[str, object]:
    for payload in _iter_mapping_payloads(metadata):
        plugin = str(payload.get("plugin") or payload.get("profile") or "").strip().lower()
        if plugin != LEGACY_CRISIS_RESPONSE_PROFILE:
            continue
        route = _route_from_payload(payload)
        true_crisis = route == LEGACY_TRUE_CRISIS_ROUTE or _as_bool(payload.get("true_crisis_active"), default=False)
        return {
            **_market_regime_context_not_found(),
            "found": True,
            "source": LEGACY_CRISIS_RESPONSE_PROFILE,
            "schema_version": str(payload.get("schema_version") or "").strip(),
            "active": true_crisis,
            "route": "risk_off" if true_crisis else "no_action",
            "route_source": LEGACY_CRISIS_RESPONSE_PROFILE,
            "suggested_action": "defend" if true_crisis else "watch_only",
            "risk_budget_scalar": 0.0 if true_crisis else 1.0,
            "leverage_scalar": 0.0 if true_crisis else 1.0,
            "risk_asset_scalar": 0.0 if true_crisis else 1.0,
            "crisis_defense_required": true_crisis,
            "reason_codes": _normalized_text_tuple(payload.get("reason_codes")) or ((f"{LEGACY_CRISIS_RESPONSE_PROFILE}:true_crisis",) if true_crisis else ()),
        }
    return _market_regime_context_not_found()


def _active_risk_asset_from_ratios(soxl_ratio: float, soxx_ratio: float) -> str:
    if soxl_ratio > 0.0 and soxx_ratio > 0.0:
        return "SOXX+SOXL"
    if soxl_ratio > 0.0:
        return "SOXL"
    if soxx_ratio > 0.0:
        return "SOXX"
    return "BOXX"


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


def build_rebalance_plan(
    indicators,
    account_state,
    *,
    trend_ma_window,
    translator,
    cash_reserve_ratio,
    cash_reserve_floor_usd=0.0,
    min_trade_ratio,
    min_trade_floor,
    rebalance_threshold_ratio,
    income_layer_start_usd,
    income_layer_max_ratio,
    income_layer_qqqi_weight,
    income_layer_spyi_weight,
    income_layer_allocations=None,
    income_layer_enabled=True,
    income_layer_activation_band_ratio=0.0,
    income_layer_ratio_mode=INCOME_LAYER_RATIO_MODE_LOG_TOTAL_DRAWDOWN_BUDGET,
    income_layer_core_stress_drawdown_ratio=0.45,
    income_layer_income_stress_drawdown_ratio=0.06,
    income_layer_base_drawdown_budget_ratio=0.45,
    income_layer_min_drawdown_budget_ratio=0.25,
    income_layer_drawdown_budget_decay_per_double=0.05,
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
    blend_gate_dynamic_rsi_threshold_enabled=False,
    blend_gate_bollinger_cap_enabled=False,
    blend_gate_overlay_stack_triggers=False,
    blend_gate_volatility_delever_enabled=False,
    blend_gate_volatility_delever_symbol="SOXX",
    blend_gate_volatility_delever_window=10,
    blend_gate_volatility_delever_threshold=0.55,
    blend_gate_volatility_delever_threshold_mode=VOLATILITY_DELEVER_THRESHOLD_MODE_FIXED,
    blend_gate_volatility_delever_dynamic_lookback=252,
    blend_gate_volatility_delever_dynamic_percentile=0.95,
    blend_gate_volatility_delever_dynamic_min_periods=126,
    blend_gate_volatility_delever_dynamic_floor=0.50,
    blend_gate_volatility_delever_dynamic_cap=0.75,
    blend_gate_volatility_delever_retention_ratio=0.0,
    blend_gate_volatility_delever_retention_mode=RETENTION_MODE_FIXED,
    blend_gate_volatility_delever_retention_policy=POLICY_SOXL_STEP_REBOUND_025_050,
    blend_gate_volatility_delever_retention_context_required=True,
    blend_gate_volatility_delever_max_retention_ratio=0.50,
    blend_gate_volatility_delever_redirect_symbol="SOXX",
    market_regime_control_enabled=False,
    market_regime_control_apply_risk_reduced=False,
    market_regime_control_apply_risk_off=True,
):
    income_allocations = normalize_income_layer_allocations(
        income_layer_allocations,
        fallback_allocations=(
            ("QQQI", _as_clamped_ratio(income_layer_qqqi_weight)),
            ("SPYI", _as_clamped_ratio(income_layer_spyi_weight)),
        ),
        excluded_symbols=CORE_ASSETS,
    )
    income_symbols = tuple(income_allocations)
    strategy_assets = [*CORE_ASSETS, *income_symbols]
    available_cash = account_state["available_cash"]
    raw_market_values = account_state["market_values"]
    raw_quantities = account_state["quantities"]
    raw_sellable_quantities = account_state["sellable_quantities"]
    market_values = {symbol: float(raw_market_values.get(symbol, 0.0)) for symbol in strategy_assets}
    quantities = {symbol: float(raw_quantities.get(symbol, 0.0)) for symbol in strategy_assets}
    sellable_quantities = {symbol: float(raw_sellable_quantities.get(symbol, 0.0)) for symbol in strategy_assets}
    total_strategy_equity = account_state["total_strategy_equity"]
    account_metadata = account_state.get("metadata", {}) if isinstance(account_state, Mapping) else {}
    if not isinstance(account_metadata, Mapping):
        account_metadata = {}
    market_regime_control_enabled = _as_bool(market_regime_control_enabled, default=False)
    market_regime_control_context = (
        _resolve_market_regime_control_context(account_metadata)
        if market_regime_control_enabled
        else _market_regime_context_not_found()
    )
    if market_regime_control_enabled and not market_regime_control_context["found"]:
        market_regime_control_context = _resolve_legacy_crisis_response_context(account_metadata)
    market_regime_control_apply_risk_reduced = _as_bool(
        market_regime_control_apply_risk_reduced,
        default=False,
    )
    market_regime_control_apply_risk_off = _as_bool(
        market_regime_control_apply_risk_off,
        default=True,
    )
    current_min_trade = max(min_trade_floor, total_strategy_equity * min_trade_ratio)

    income_layer_plan = build_income_layer_plan(
        total_equity_usd=total_strategy_equity,
        market_values=market_values,
        allocations=income_allocations,
        income_layer_enabled=income_layer_enabled,
        income_layer_start_usd=income_layer_start_usd,
        income_layer_max_ratio=income_layer_max_ratio,
        income_layer_activation_band_ratio=income_layer_activation_band_ratio,
        income_layer_ratio_mode=income_layer_ratio_mode,
        income_layer_core_stress_drawdown_ratio=income_layer_core_stress_drawdown_ratio,
        income_layer_income_stress_drawdown_ratio=income_layer_income_stress_drawdown_ratio,
        income_layer_base_drawdown_budget_ratio=income_layer_base_drawdown_budget_ratio,
        income_layer_min_drawdown_budget_ratio=income_layer_min_drawdown_budget_ratio,
        income_layer_drawdown_budget_decay_per_double=income_layer_drawdown_budget_decay_per_double,
    )
    core_equity = max(0.0, total_strategy_equity - income_layer_plan.locked_value)
    deploy_ratio_text = "0.0%"
    income_ratio_text = f"{income_layer_plan.ratio * 100:.1f}%"
    income_locked_ratio_text = (
        f"{(income_layer_plan.locked_value / total_strategy_equity) * 100:.1f}%"
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
    trend_rsi14_dynamic_threshold = _as_float_or_none(
        _indicator_value(indicators, trend_symbol, "rsi14_dynamic_threshold")
    )
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
    use_dynamic_rsi_threshold = _as_bool(blend_gate_dynamic_rsi_threshold_enabled, default=False)
    use_bollinger_cap = _as_bool(blend_gate_bollinger_cap_enabled, default=False)
    stack_overlay_triggers = _as_bool(blend_gate_overlay_stack_triggers, default=False)
    use_volatility_delever = _as_bool(blend_gate_volatility_delever_enabled, default=False)
    volatility_delever_symbol = str(blend_gate_volatility_delever_symbol or trend_symbol).strip().upper()
    if not volatility_delever_symbol:
        volatility_delever_symbol = trend_symbol
    volatility_delever_window = _as_positive_int(blend_gate_volatility_delever_window, default=10)
    volatility_delever_fixed_threshold = _as_float_or_none(blend_gate_volatility_delever_threshold)
    if volatility_delever_fixed_threshold is None:
        volatility_delever_fixed_threshold = 0.55
    volatility_delever_threshold_mode = str(
        blend_gate_volatility_delever_threshold_mode or VOLATILITY_DELEVER_THRESHOLD_MODE_FIXED
    ).strip().lower()
    if volatility_delever_threshold_mode not in VOLATILITY_DELEVER_THRESHOLD_MODES:
        volatility_delever_threshold_mode = VOLATILITY_DELEVER_THRESHOLD_MODE_FIXED
    volatility_delever_dynamic_lookback = _as_positive_int(
        blend_gate_volatility_delever_dynamic_lookback,
        default=252,
    )
    volatility_delever_dynamic_percentile = _as_unit_interval(
        blend_gate_volatility_delever_dynamic_percentile,
        default=0.95,
    )
    volatility_delever_dynamic_min_periods = min(
        volatility_delever_dynamic_lookback,
        _as_positive_int(blend_gate_volatility_delever_dynamic_min_periods, default=126),
    )
    volatility_delever_dynamic_floor = _as_clamped_ratio(
        blend_gate_volatility_delever_dynamic_floor,
        default=0.50,
    )
    volatility_delever_dynamic_cap = _as_clamped_ratio(
        blend_gate_volatility_delever_dynamic_cap,
        default=0.75,
    )
    if volatility_delever_dynamic_cap < volatility_delever_dynamic_floor:
        volatility_delever_dynamic_cap = volatility_delever_dynamic_floor
    volatility_delever_retention_ratio = _as_clamped_ratio(
        blend_gate_volatility_delever_retention_ratio,
        default=0.0,
    )
    volatility_delever_retention_decision = resolve_volatility_delever_retention(
        mode=blend_gate_volatility_delever_retention_mode,
        fixed_ratio=volatility_delever_retention_ratio,
        policy=blend_gate_volatility_delever_retention_policy,
        max_ratio=blend_gate_volatility_delever_max_retention_ratio,
        context_required=blend_gate_volatility_delever_retention_context_required,
        market_regime_context=market_regime_control_context,
    )
    volatility_delever_redirect_symbol = str(
        blend_gate_volatility_delever_redirect_symbol or "SOXX"
    ).strip().upper()
    if volatility_delever_redirect_symbol not in {"SOXX", "BOXX"}:
        volatility_delever_redirect_symbol = "SOXX"
    volatility_delever_metric = _as_float_or_none(
        _indicator_value(
            indicators,
            volatility_delever_symbol,
            f"realized_volatility_{volatility_delever_window}",
        )
    )
    if volatility_delever_metric is None:
        volatility_delever_metric = _as_float_or_none(
            _indicator_value(indicators, volatility_delever_symbol, "realized_volatility")
        )
    volatility_delever_dynamic_threshold = _indicator_first_float(
        indicators,
        volatility_delever_symbol,
        (
            f"realized_volatility_{volatility_delever_window}_dynamic_threshold",
            "realized_volatility_dynamic_threshold",
        ),
    )
    volatility_delever_dynamic_sample_count = _indicator_first_float(
        indicators,
        volatility_delever_symbol,
        (
            f"realized_volatility_{volatility_delever_window}_dynamic_sample_count",
            "realized_volatility_dynamic_sample_count",
        ),
    )
    if volatility_delever_dynamic_threshold is not None:
        volatility_delever_dynamic_threshold = max(
            volatility_delever_dynamic_floor,
            min(volatility_delever_dynamic_cap, volatility_delever_dynamic_threshold),
        )
    if (
        volatility_delever_dynamic_sample_count is not None
        and volatility_delever_dynamic_sample_count < volatility_delever_dynamic_min_periods
    ):
        volatility_delever_dynamic_threshold = None
    volatility_delever_threshold = volatility_delever_fixed_threshold
    if (
        volatility_delever_threshold_mode == VOLATILITY_DELEVER_THRESHOLD_MODE_ROLLING_PERCENTILE
        and volatility_delever_dynamic_threshold is not None
    ):
        volatility_delever_threshold = volatility_delever_dynamic_threshold
    rsi_threshold = _as_float_or_none(blend_gate_rsi_threshold)
    if rsi_threshold is None:
        rsi_threshold = 70.0
    effective_rsi_threshold = rsi_threshold
    if use_dynamic_rsi_threshold and trend_rsi14_dynamic_threshold is not None:
        effective_rsi_threshold = max(rsi_threshold, trend_rsi14_dynamic_threshold)

    blend_tier = "defensive"
    if trend_price > trend_entry_line:
        blend_tier = "full"
    elif trend_price > trend_mid_line or (current_blend_active and trend_price > trend_exit_line):
        blend_tier = "mid"
    base_blend_tier = blend_tier
    overlay_trigger_reasons: list[str] = []
    overlay_trigger_codes: list[str] = []
    if base_blend_tier in {"full", "mid"}:
        if use_rsi_cap and trend_rsi14 is not None and trend_rsi14 > effective_rsi_threshold:
            overlay_trigger_codes.append("blend_gate_reason_rsi_cap")
            overlay_trigger_reasons.append(
                _translate_with_fallback(translator, "blend_gate_reason_rsi_cap", f"RSI>{effective_rsi_threshold:.0f}")
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
    tier_overlay_trigger_count = len(overlay_trigger_reasons)
    if tier_overlay_trigger_count > 0:
        blend_tier = _downgrade_tier(
            base_blend_tier,
            tier_overlay_trigger_count if stack_overlay_triggers else 1,
        )

    selected_soxl_ratio, selected_soxx_ratio, boxx_ratio, active_risk_asset = _resolve_tier_allocations(
        tier=blend_tier,
        full_soxl_ratio=target_soxl_ratio,
        mid_soxl_ratio=target_mid_soxl_ratio,
        active_soxx_ratio=target_active_soxx_ratio,
        defensive_soxx_ratio=target_defensive_soxx_ratio,
    )
    volatility_delever_triggered = (
        use_volatility_delever
        and selected_soxl_ratio > 0.0
        and volatility_delever_metric is not None
        and volatility_delever_metric >= volatility_delever_threshold
    )
    volatility_delever_removed_ratio = 0.0
    if volatility_delever_triggered:
        effective_retention_ratio = float(volatility_delever_retention_decision.get("retention_ratio") or 0.0)
        retained_soxl_ratio = selected_soxl_ratio * effective_retention_ratio
        volatility_delever_removed_ratio = max(0.0, selected_soxl_ratio - retained_soxl_ratio)
        selected_soxl_ratio = retained_soxl_ratio
        if volatility_delever_redirect_symbol == "BOXX":
            boxx_ratio += volatility_delever_removed_ratio
        else:
            selected_soxx_ratio += volatility_delever_removed_ratio
        if selected_soxl_ratio > 0.0 and selected_soxx_ratio > 0.0:
            active_risk_asset = "SOXX+SOXL"
        elif selected_soxl_ratio > 0.0:
            active_risk_asset = "SOXL"
        elif selected_soxx_ratio > 0.0:
            active_risk_asset = "SOXX"
        else:
            active_risk_asset = "BOXX"
        overlay_trigger_codes.append("blend_gate_reason_volatility_delever")
        volatility_delever_threshold_detail = _format_volatility_delever_threshold_detail(
            translator,
            threshold_mode=volatility_delever_threshold_mode,
            fixed_threshold=volatility_delever_fixed_threshold,
            dynamic_threshold=volatility_delever_dynamic_threshold,
            dynamic_sample_count=volatility_delever_dynamic_sample_count,
            dynamic_lookback=volatility_delever_dynamic_lookback,
            dynamic_percentile=volatility_delever_dynamic_percentile,
            dynamic_min_periods=volatility_delever_dynamic_min_periods,
            dynamic_floor=volatility_delever_dynamic_floor,
            dynamic_cap=volatility_delever_dynamic_cap,
        )
        overlay_trigger_reasons.append(
            _translate_with_fallback(
                translator,
                "blend_gate_reason_volatility_delever_dynamic",
                (
                    f"{volatility_delever_symbol} {volatility_delever_window}d volatility "
                    f"{volatility_delever_metric * 100:.1f}% >= "
                    f"effective threshold {volatility_delever_threshold * 100:.1f}% "
                    f"({volatility_delever_threshold_detail}), redirect SOXL to "
                    f"{volatility_delever_redirect_symbol}"
                ),
                symbol=volatility_delever_symbol,
                window=volatility_delever_window,
                volatility=f"{volatility_delever_metric * 100:.1f}%",
                threshold=f"{volatility_delever_threshold * 100:.1f}%",
                threshold_detail=volatility_delever_threshold_detail,
                redirect_symbol=volatility_delever_redirect_symbol,
            )
        )
    market_regime_control_applied = False
    market_regime_control_removed_ratio = 0.0
    market_regime_control_redirected_to_unlevered_ratio = 0.0
    market_regime_control_route = str(market_regime_control_context["route"])
    market_regime_control_route_allowed = (
        market_regime_control_route == "risk_reduced" and market_regime_control_apply_risk_reduced
    ) or (market_regime_control_route == "risk_off" and market_regime_control_apply_risk_off)
    if bool(market_regime_control_context["active"] and market_regime_control_route_allowed):
        before_market_regime_risk_ratio = selected_soxl_ratio + selected_soxx_ratio
        leverage_scalar = float(market_regime_control_context["leverage_scalar"])
        risk_asset_scalar = float(market_regime_control_context["risk_asset_scalar"])
        leverage_removed_ratio = max(0.0, selected_soxl_ratio * (1.0 - leverage_scalar))
        selected_soxl_ratio *= leverage_scalar
        if str(market_regime_control_context["route"]) == "risk_reduced":
            selected_soxx_ratio += leverage_removed_ratio
            market_regime_control_redirected_to_unlevered_ratio = leverage_removed_ratio
        if risk_asset_scalar < 1.0:
            selected_soxl_ratio *= risk_asset_scalar
            selected_soxx_ratio *= risk_asset_scalar
        after_market_regime_risk_ratio = selected_soxl_ratio + selected_soxx_ratio
        market_regime_control_removed_ratio = max(
            0.0,
            before_market_regime_risk_ratio - after_market_regime_risk_ratio,
        )
        boxx_ratio += market_regime_control_removed_ratio
        active_risk_asset = _active_risk_asset_from_ratios(selected_soxl_ratio, selected_soxx_ratio)
        market_regime_control_applied = (
            leverage_removed_ratio > 1e-9
            or before_market_regime_risk_ratio > after_market_regime_risk_ratio + 1e-9
        )
        if market_regime_control_applied:
            reason_code = (
                "blend_gate_reason_market_regime_control_risk_off"
                if market_regime_control_route == "risk_off"
                else "blend_gate_reason_market_regime_control_risk_reduced"
            )
            overlay_trigger_codes.append(reason_code)
            overlay_trigger_reasons.append(
                _translate_with_fallback(
                    translator,
                    reason_code,
                    "market regime risk-off" if market_regime_control_route == "risk_off" else "market regime risk-reduced",
                )
            )
    overlay_trigger_count = len(overlay_trigger_reasons)
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
            "fallback": f"🧯 RISK-CAP ({active_risk_asset})",
            "params": {"asset": active_risk_asset},
        }
        signal_context = {
            "code": "signal_blend_gate_overlay_capped",
            "fallback": (
                f"{trend_symbol} above {trend_ma_window}d gated entry, but risk cap "
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
        "BOXX": max(0.0, core_equity * boxx_ratio),
    }
    targets.update(income_layer_plan.target_values)
    reserved_cash = max(
        max(0.0, total_strategy_equity * cash_reserve_ratio),
        max(0.0, float(cash_reserve_floor_usd or 0.0)),
    )
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
        "rsi14_dynamic_threshold": trend_rsi14_dynamic_threshold,
        "rsi14_effective_threshold": effective_rsi_threshold,
        "bb_mid": trend_bb_mid,
        "bb_upper": trend_bb_upper,
        "bb_lower": trend_bb_lower,
        "overlay_trigger_count": overlay_trigger_count,
        "overlay_trigger_reasons": tuple(overlay_trigger_reasons),
        "volatility_delever_symbol": volatility_delever_symbol,
        "volatility_delever_window": volatility_delever_window,
        "volatility_delever_threshold": volatility_delever_threshold,
        "volatility_delever_threshold_mode": volatility_delever_threshold_mode,
        "volatility_delever_dynamic_threshold": volatility_delever_dynamic_threshold,
        "volatility_delever_dynamic_sample_count": volatility_delever_dynamic_sample_count,
        "volatility_delever_dynamic_lookback": volatility_delever_dynamic_lookback,
        "volatility_delever_dynamic_percentile": volatility_delever_dynamic_percentile,
        "volatility_delever_dynamic_min_periods": volatility_delever_dynamic_min_periods,
        "volatility_delever_dynamic_floor": volatility_delever_dynamic_floor,
        "volatility_delever_dynamic_cap": volatility_delever_dynamic_cap,
        "volatility_delever_metric": volatility_delever_metric,
        "volatility_delever_triggered": volatility_delever_triggered,
        "volatility_delever_retention_ratio": volatility_delever_retention_ratio,
        "volatility_delever_retention_mode": volatility_delever_retention_decision["mode"],
        "volatility_delever_retention_policy": volatility_delever_retention_decision["policy"],
        "volatility_delever_effective_retention_ratio": volatility_delever_retention_decision["retention_ratio"],
        "volatility_delever_retention_source": volatility_delever_retention_decision["source"],
        "volatility_delever_retention_context_found": volatility_delever_retention_decision["context_found"],
        "volatility_delever_retention_reason_codes": volatility_delever_retention_decision["reason_codes"],
        "volatility_delever_redirect_symbol": volatility_delever_redirect_symbol,
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
                "quantity": float(quantities[symbol]),
            }
            for symbol in strategy_assets
        },
    }
    risk_control_context = {
        "market_regime_control": {
            "enabled": market_regime_control_enabled,
            "found": bool(market_regime_control_context["found"]),
            "source": market_regime_control_context["source"],
            "schema_version": market_regime_control_context["schema_version"],
            "route": market_regime_control_context["route"],
            "route_source": market_regime_control_context["route_source"],
            "active": bool(market_regime_control_context["active"]),
            "route_allowed": market_regime_control_route_allowed,
            "applied": market_regime_control_applied,
            "suggested_action": market_regime_control_context["suggested_action"],
            "risk_budget_scalar": market_regime_control_context["risk_budget_scalar"],
            "leverage_scalar": market_regime_control_context["leverage_scalar"],
            "risk_asset_scalar": market_regime_control_context["risk_asset_scalar"],
            "crisis_defense_required": market_regime_control_context["crisis_defense_required"],
            "blocked_actions": market_regime_control_context["blocked_actions"],
            "vetoes": market_regime_control_context["vetoes"],
            "reason_codes": market_regime_control_context["reason_codes"],
            "localized_messages": market_regime_control_context["localized_messages"],
            "log_record": market_regime_control_context["log_record"],
            "notification": market_regime_control_context["notification"],
            "removed_ratio": market_regime_control_removed_ratio,
            "redirected_to_unlevered_ratio": market_regime_control_redirected_to_unlevered_ratio,
        },
    }
    notification_context = {
        "status": status_context,
        "signal": signal_context,
        "benchmark": benchmark_context,
        "portfolio": portfolio_context,
        "risk_controls": risk_control_context,
    }

    return {
        "strategy_assets": strategy_assets,
        # Execution metadata consumed by downstream platform repos.
        "limit_order_symbols": ("SOXL", "SOXX", *income_symbols),
        "portfolio_rows": (("SOXL", "SOXX"), income_symbols, ("BOXX",)),
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
        "income_layer_allocations": income_layer_plan.allocations,
        "income_layer_symbols": income_layer_plan.symbols,
        "income_layer_ratio": income_layer_plan.ratio,
        "income_layer_value": income_layer_plan.locked_value,
        **income_layer_plan.diagnostics,
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
        "trend_rsi14_dynamic_threshold": trend_rsi14_dynamic_threshold,
        "trend_rsi14_effective_threshold": effective_rsi_threshold,
        "trend_bb_mid": trend_bb_mid,
        "trend_bb_upper": trend_bb_upper,
        "trend_bb_lower": trend_bb_lower,
        "blend_gate_rsi_cap_enabled": use_rsi_cap,
        "blend_gate_rsi_threshold": rsi_threshold,
        "blend_gate_dynamic_rsi_threshold_enabled": use_dynamic_rsi_threshold,
        "blend_gate_bollinger_cap_enabled": use_bollinger_cap,
        "blend_gate_overlay_stack_triggers": stack_overlay_triggers,
        "blend_gate_volatility_delever_enabled": use_volatility_delever,
        "blend_gate_volatility_delever_symbol": volatility_delever_symbol,
        "blend_gate_volatility_delever_window": volatility_delever_window,
        "blend_gate_volatility_delever_threshold": volatility_delever_threshold,
        "blend_gate_volatility_delever_threshold_mode": volatility_delever_threshold_mode,
        "blend_gate_volatility_delever_dynamic_threshold": volatility_delever_dynamic_threshold,
        "blend_gate_volatility_delever_dynamic_sample_count": volatility_delever_dynamic_sample_count,
        "blend_gate_volatility_delever_dynamic_lookback": volatility_delever_dynamic_lookback,
        "blend_gate_volatility_delever_dynamic_percentile": volatility_delever_dynamic_percentile,
        "blend_gate_volatility_delever_dynamic_min_periods": volatility_delever_dynamic_min_periods,
        "blend_gate_volatility_delever_dynamic_floor": volatility_delever_dynamic_floor,
        "blend_gate_volatility_delever_dynamic_cap": volatility_delever_dynamic_cap,
        "blend_gate_volatility_delever_metric": volatility_delever_metric,
        "blend_gate_volatility_delever_triggered": volatility_delever_triggered,
        "blend_gate_volatility_delever_retention_ratio": volatility_delever_retention_ratio,
        "blend_gate_volatility_delever_retention_mode": volatility_delever_retention_decision["mode"],
        "blend_gate_volatility_delever_retention_policy": volatility_delever_retention_decision["policy"],
        "blend_gate_volatility_delever_effective_retention_ratio": volatility_delever_retention_decision[
            "retention_ratio"
        ],
        "blend_gate_volatility_delever_retention_source": volatility_delever_retention_decision["source"],
        "blend_gate_volatility_delever_retention_context_found": volatility_delever_retention_decision[
            "context_found"
        ],
        "blend_gate_volatility_delever_retention_reason_codes": volatility_delever_retention_decision[
            "reason_codes"
        ],
        "blend_gate_volatility_delever_redirect_symbol": volatility_delever_redirect_symbol,
        "blend_gate_volatility_delever_removed_ratio": volatility_delever_removed_ratio,
        "market_regime_control_enabled": market_regime_control_enabled,
        "market_regime_control_found": bool(market_regime_control_context["found"]),
        "market_regime_control_source": market_regime_control_context["source"],
        "market_regime_control_schema_version": market_regime_control_context["schema_version"],
        "market_regime_control_route": market_regime_control_context["route"],
        "market_regime_control_route_source": market_regime_control_context["route_source"],
        "market_regime_control_active": bool(market_regime_control_context["active"]),
        "market_regime_control_route_allowed": market_regime_control_route_allowed,
        "market_regime_control_applied": market_regime_control_applied,
        "market_regime_control_position_control_allowed": market_regime_control_context[
            "position_control_allowed"
        ],
        "market_regime_control_position_control_authorized": market_regime_control_context[
            "position_control_authorized"
        ],
        "market_regime_control_consumption_evidence_status": market_regime_control_context[
            "consumption_evidence_status"
        ],
        "market_regime_control_risk_budget_scalar": market_regime_control_context["risk_budget_scalar"],
        "market_regime_control_leverage_scalar": market_regime_control_context["leverage_scalar"],
        "market_regime_control_risk_asset_scalar": market_regime_control_context["risk_asset_scalar"],
        "market_regime_control_crisis_defense_required": market_regime_control_context["crisis_defense_required"],
        "market_regime_control_reason_codes": market_regime_control_context["reason_codes"],
        "market_regime_control_removed_ratio": market_regime_control_removed_ratio,
        "market_regime_control_redirected_to_unlevered_ratio": market_regime_control_redirected_to_unlevered_ratio,
    }
