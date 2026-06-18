"""Allocation and plan helpers for the live TQQQ growth income profile."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd

from quant_platform_kit.common.history import normalize_history_frame
from us_equity_strategies.income_layer import (
    INCOME_LAYER_RATIO_MODE_LOG_TOTAL_DRAWDOWN_BUDGET,
    INCOME_LAYER_RATIO_MODES,
    as_clamped_ratio,
    build_income_layer_plan,
    get_income_layer_ratio,
    normalize_income_layer_allocations,
)
from us_equity_strategies.market_regime_control_contract import (
    resolve_market_regime_position_control_authorization,
)
from us_equity_strategies.volatility_delever_retention import (
    POLICY_TQQQ_STEP_SOFTZERO_025_050,
    RETENTION_MODE_NONE,
    resolve_volatility_delever_retention,
)

PULLBACK_REBOUND_THRESHOLD_MODE_FIXED = "fixed"
PULLBACK_REBOUND_THRESHOLD_MODE_VOLATILITY_SCALED = "volatility_scaled"
PULLBACK_REBOUND_THRESHOLD_MODES = {
    PULLBACK_REBOUND_THRESHOLD_MODE_FIXED,
    PULLBACK_REBOUND_THRESHOLD_MODE_VOLATILITY_SCALED,
}
VOLATILITY_DELEVER_THRESHOLD_MODE_FIXED = "fixed"
VOLATILITY_DELEVER_THRESHOLD_MODE_ROLLING_PERCENTILE = "rolling_percentile"
VOLATILITY_DELEVER_THRESHOLD_MODES = {
    VOLATILITY_DELEVER_THRESHOLD_MODE_FIXED,
    VOLATILITY_DELEVER_THRESHOLD_MODE_ROLLING_PERCENTILE,
}
CORE_ASSETS = ("TQQQ", "BOXX")
TACO_REBOUND_ROUTES = frozenset({"taco_rebound", "taco_fake_crisis"})
TRUE_CRISIS_ROUTE = "true_crisis"
MARKET_REGIME_CONTROL_PROFILE = "market_regime_control"
MARKET_REGIME_POSITION_ROUTES = frozenset({"risk_reduced", "risk_off"})
MACRO_RISK_GOVERNOR_PROFILE = "macro_risk_governor"
MACRO_RISK_GOVERNOR_ROUTES = frozenset({"delever", "crisis"})
__all__ = [
    "INCOME_LAYER_RATIO_MODE_LOG_TOTAL_DRAWDOWN_BUDGET",
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


def _translate_context_with_fallback(translator, key: str, fallback: str, **kwargs) -> str:
    rendered = translator(key, **kwargs)
    if rendered == key or str(rendered).startswith(f"{key}("):
        return fallback
    return rendered


def _as_bool(value, *, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", ""}:
        return False
    return bool(default)


def _as_positive_int(value, *, default: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = int(default)
    return max(1, result)


def _as_float_or_none(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(result):
        return None
    return result


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


def _is_taco_rebound_context_active(metadata: Mapping) -> bool:
    if _as_bool(metadata.get("taco_rebound_context_active"), default=False):
        return True
    if _as_bool(metadata.get("taco_fake_crisis_active"), default=False):
        return True
    for payload in _iter_mapping_payloads(metadata):
        route = _route_from_payload(payload)
        plugin = str(payload.get("plugin") or payload.get("profile") or "").strip().lower()
        rebound_context_active = _as_bool(payload.get("rebound_context_active"), default=False)
        if route in TACO_REBOUND_ROUTES and (
            rebound_context_active
            or route == "taco_fake_crisis"
            or _as_bool(payload.get("event_context_active"), default=False)
        ):
            return True
        if rebound_context_active and "taco" in plugin:
            return True
    return False


def _is_true_crisis_active(metadata: Mapping) -> bool:
    for key in ("true_crisis_active", "crisis_response_true_crisis_active"):
        if _as_bool(metadata.get(key), default=False):
            return True
    for payload in _iter_mapping_payloads(metadata):
        if _route_from_payload(payload) == TRUE_CRISIS_ROUTE:
            return True
        if _as_bool(payload.get("true_crisis_active"), default=False):
            return True
    return False


def _clamped_ratio_or_default(value, *, default: float) -> float:
    result = _as_float_or_none(value)
    if result is None:
        result = float(default)
    return max(0.0, min(1.0, float(result)))


def _resolve_macro_risk_governor_context(metadata: Mapping) -> dict[str, object]:
    for payload in _iter_mapping_payloads(metadata):
        plugin = str(payload.get("plugin") or payload.get("profile") or "").strip().lower()
        if plugin != MACRO_RISK_GOVERNOR_PROFILE:
            continue
        route = _route_from_payload(payload)
        kill_switch_active = _as_bool(payload.get("kill_switch_active"), default=False)
        active = route in MACRO_RISK_GOVERNOR_ROUTES and not kill_switch_active
        reason_codes = payload.get("reason_codes")
        if isinstance(reason_codes, str):
            normalized_reasons = tuple(item.strip() for item in reason_codes.split(",") if item.strip())
        elif isinstance(reason_codes, Sequence) and not isinstance(reason_codes, (bytes, bytearray)):
            normalized_reasons = tuple(str(item).strip() for item in reason_codes if str(item).strip())
        else:
            normalized_reasons = ()
        return {
            "found": True,
            "active": active,
            "route": route,
            "suggested_action": str(payload.get("suggested_action") or "").strip().lower(),
            "leverage_scalar": _clamped_ratio_or_default(payload.get("leverage_scalar"), default=1.0),
            "risk_asset_scalar": _clamped_ratio_or_default(payload.get("risk_asset_scalar"), default=1.0),
            "actionable_score": _as_float_or_none(payload.get("actionable_score")),
            "total_score": _as_float_or_none(payload.get("total_score")),
            "reason_codes": normalized_reasons,
            "kill_switch_active": kill_switch_active,
            "source": MACRO_RISK_GOVERNOR_PROFILE,
        }
    return {
        "found": False,
        "active": False,
        "route": "",
        "suggested_action": "",
        "leverage_scalar": 1.0,
        "risk_asset_scalar": 1.0,
        "actionable_score": None,
        "total_score": None,
        "reason_codes": (),
        "kill_switch_active": False,
        "source": MACRO_RISK_GOVERNOR_PROFILE,
    }


def _normalized_text_tuple(value) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _format_ratio_text(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{max(0.0, min(1.0, float(value))) * 100:.1f}%"


def _build_signal_reason(
    *,
    icon: str,
    translator,
    pullback_risk_on: bool,
    above_ma200: bool,
    slope_ok: bool,
) -> tuple[str, str]:
    if icon == "entry":
        if pullback_risk_on:
            key = "tqqq_signal_reason_entry_pullback"
            fallback = "reason: QQQ is below MA200 but above MA20 with a confirmed pullback rebound"
        else:
            key = "tqqq_signal_reason_entry_trend"
            fallback = "reason: QQQ is above MA200 and MA20 slope is positive"
    elif icon == "hold":
        key = "tqqq_signal_reason_hold_trend"
        fallback = "reason: existing risk sleeve remains active while QQQ stays above MA200"
    elif icon == "exit":
        key = "tqqq_signal_reason_exit_ma200"
        fallback = "reason: QQQ fell below the MA200 exit line"
    elif icon == "idle":
        key = "tqqq_signal_reason_idle_waiting"
        if not above_ma200:
            fallback = "reason: waiting for QQQ to reclaim MA200"
        elif not slope_ok:
            fallback = "reason: QQQ is above MA200, but MA20 slope is not positive yet"
        else:
            fallback = "reason: no active risk position"
    elif icon == "macro_delever":
        key = "tqqq_signal_reason_macro_delever"
        fallback = "reason: macro risk governor reduced leverage"
    elif icon == "macro_risk_defense":
        key = "tqqq_signal_reason_macro_defense"
        fallback = "reason: macro risk governor moved the strategy defensive"
    elif icon == "crisis_defense":
        key = "tqqq_signal_reason_crisis_defense"
        fallback = "reason: crisis defense moved the strategy to the safe sleeve"
    else:
        key = "tqqq_signal_reason_unknown"
        fallback = "reason: strategy state evaluated from QQQ trend and risk controls"
    return key, _translate_context_with_fallback(translator, key, fallback)


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
        reason_codes = (
            _normalized_text_tuple(position_control.get("reason_codes"))
            or _normalized_text_tuple(arbiter.get("reason_codes"))
            or _normalized_text_tuple(payload.get("reason_codes"))
        )
        vetoes = _normalized_text_tuple(position_control.get("vetoes")) or _normalized_text_tuple(arbiter.get("vetoes"))
        component_signals = payload.get("component_signals")
        macro_component = {}
        if isinstance(component_signals, Mapping) and isinstance(component_signals.get("macro"), Mapping):
            macro_component = component_signals["macro"]
        volatility_delever_context = position_control.get("volatility_delever_context")
        if not isinstance(volatility_delever_context, Mapping):
            volatility_delever_context = payload.get("volatility_delever_context")
        if not isinstance(volatility_delever_context, Mapping):
            volatility_delever_context = {}
        return {
            "found": True,
            "schema_version": str(payload.get("schema_version") or "").strip(),
            "active": route in MARKET_REGIME_POSITION_ROUTES and not blocked and position_control_authorized,
            "route": route,
            "route_source": str(position_control.get("route_source") or arbiter.get("route_source") or "").strip(),
            "suggested_action": suggested_action,
            "position_control_allowed": position_control_allowed,
            "position_control_authorized": position_control_authorized,
            "consumption_evidence_status": evidence_status,
            "risk_budget_scalar": _clamped_ratio_or_default(position_control.get("risk_budget_scalar"), default=1.0),
            "leverage_scalar": _clamped_ratio_or_default(position_control.get("leverage_scalar"), default=1.0),
            "risk_asset_scalar": _clamped_ratio_or_default(position_control.get("risk_asset_scalar"), default=1.0),
            "taco_allowed": position_control_authorized
            and _as_bool(position_control.get("taco_allowed"), default=False),
            "local_delever_veto_allowed": position_control_authorized
            and _as_bool(position_control.get("local_delever_veto_allowed"), default=False),
            "crisis_defense_required": position_control_authorized
            and _as_bool(position_control.get("crisis_defense_required"), default=False),
            "blocked_actions": _normalized_text_tuple(position_control.get("blocked_actions")),
            "vetoes": vetoes,
            "actionable_score": _as_float_or_none(macro_component.get("actionable_score")),
            "total_score": _as_float_or_none(macro_component.get("total_score")),
            "reason_codes": reason_codes,
            "localized_messages": payload.get("localized_messages")
            if isinstance(payload.get("localized_messages"), Mapping)
            else {},
            "log_record": payload.get("log_record") if isinstance(payload.get("log_record"), Mapping) else {},
            "notification": payload.get("notification") if isinstance(payload.get("notification"), Mapping) else {},
            "blocked": blocked,
            "volatility_delever_context": volatility_delever_context if position_control_authorized else {},
        }
    return {
        "found": False,
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
        "actionable_score": None,
        "total_score": None,
        "reason_codes": (),
        "localized_messages": {},
        "log_record": {},
        "notification": {},
        "blocked": False,
        "volatility_delever_context": {},
    }


def _market_regime_control_as_macro_context(context: Mapping[str, object]) -> dict[str, object]:
    route = str(context.get("route") or "").strip().lower()
    macro_route = "crisis" if route == "risk_off" else "delever" if route == "risk_reduced" else route
    return {
        "found": bool(context.get("found")),
        "schema_version": str(context.get("schema_version") or "").strip(),
        "active": bool(context.get("active")),
        "route": macro_route,
        "suggested_action": str(context.get("suggested_action") or "").strip().lower(),
        "leverage_scalar": _clamped_ratio_or_default(context.get("leverage_scalar"), default=1.0),
        "risk_asset_scalar": _clamped_ratio_or_default(context.get("risk_asset_scalar"), default=1.0),
        "actionable_score": context.get("actionable_score"),
        "total_score": context.get("total_score"),
        "reason_codes": _normalized_text_tuple(context.get("reason_codes")),
        "kill_switch_active": bool(context.get("blocked")),
        "source": MARKET_REGIME_CONTROL_PROFILE,
    }


def _resolve_realized_volatility(close: pd.Series, *, window: int) -> float | None:
    returns = pd.to_numeric(close, errors="coerce").pct_change(fill_method=None)
    volatility = returns.rolling(int(window), min_periods=int(window)).std().iloc[-1]
    if pd.isna(volatility):
        return None
    return float(volatility * np.sqrt(252))


def _resolve_volatility_delever_thresholds(
    close: pd.Series,
    *,
    volatility_window: int,
    mode: str,
    fixed_entry_threshold: float,
    fixed_exit_threshold: float | None,
    percentile_lookback: int,
    percentile: float,
    min_periods: int,
    floor: float | None,
    cap: float | None,
) -> dict[str, object]:
    threshold_mode = str(mode or VOLATILITY_DELEVER_THRESHOLD_MODE_FIXED).strip().lower()
    if threshold_mode not in VOLATILITY_DELEVER_THRESHOLD_MODES:
        modes = ", ".join(sorted(VOLATILITY_DELEVER_THRESHOLD_MODES))
        raise ValueError(f"Unsupported volatility delever threshold mode: {threshold_mode!r}; expected one of {modes}")

    fixed_entry = _as_float_or_none(fixed_entry_threshold)
    if fixed_entry is None:
        fixed_entry = 0.28
    fixed_entry = max(0.0, float(fixed_entry))
    fixed_exit = _as_float_or_none(fixed_exit_threshold)
    if fixed_exit is None:
        fixed_exit = fixed_entry
    fixed_exit = max(0.0, min(fixed_entry, float(fixed_exit)))

    returns = pd.to_numeric(close, errors="coerce").pct_change(fill_method=None)
    realized_volatility = returns.rolling(int(volatility_window), min_periods=int(volatility_window)).std() * np.sqrt(252)
    metric = realized_volatility.iloc[-1]
    metric_value = None if pd.isna(metric) else float(metric)

    dynamic_threshold = None
    dynamic_sample_count = 0
    lookback = _as_positive_int(percentile_lookback, default=252)
    min_count = max(1, min(lookback, _as_positive_int(min_periods, default=min(126, lookback))))
    quantile_value = _as_float_or_none(percentile)
    if quantile_value is None:
        quantile_value = 0.90
    quantile = max(0.0, min(1.0, float(quantile_value)))
    floor_value = _as_float_or_none(floor)
    cap_value = _as_float_or_none(cap)
    if threshold_mode == VOLATILITY_DELEVER_THRESHOLD_MODE_ROLLING_PERCENTILE:
        recent = realized_volatility.dropna().tail(lookback)
        dynamic_sample_count = int(recent.count())
        if dynamic_sample_count >= min_count:
            threshold = float(recent.quantile(quantile))
            if floor_value is not None:
                threshold = max(float(floor_value), threshold)
            if cap_value is not None:
                threshold = min(float(cap_value), threshold)
            dynamic_threshold = max(0.0, threshold)

    entry_threshold = dynamic_threshold if dynamic_threshold is not None else fixed_entry
    exit_threshold = entry_threshold if dynamic_threshold is not None else fixed_exit
    return {
        "mode": threshold_mode,
        "metric": metric_value,
        "entry_threshold": float(entry_threshold),
        "exit_threshold": float(max(0.0, min(entry_threshold, exit_threshold))),
        "dynamic_threshold": dynamic_threshold,
        "dynamic_sample_count": dynamic_sample_count,
        "dynamic_lookback": lookback,
        "dynamic_percentile": quantile,
        "dynamic_min_periods": min_count,
        "dynamic_floor": None if floor_value is None else float(floor_value),
        "dynamic_cap": None if cap_value is None else float(cap_value),
    }


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
    cash_reserve_floor_usd=0.0,
    rebalance_threshold_ratio,
    income_layer_start_usd=None,
    income_layer_max_ratio=None,
    income_layer_qqqi_weight=None,
    income_layer_spyi_weight=None,
    income_layer_allocations=None,
    income_layer_enabled=True,
    income_layer_activation_band_ratio=0.0,
    income_layer_ratio_mode=INCOME_LAYER_RATIO_MODE_LOG_TOTAL_DRAWDOWN_BUDGET,
    income_layer_core_stress_drawdown_ratio=0.45,
    income_layer_income_stress_drawdown_ratio=0.08,
    income_layer_base_drawdown_budget_ratio=0.45,
    income_layer_min_drawdown_budget_ratio=0.25,
    income_layer_drawdown_budget_decay_per_double=0.05,
    attack_allocation_mode="fixed_qqq_tqqq_pullback",
    dual_drive_qqq_weight=0.45,
    dual_drive_tqqq_weight=0.45,
    dual_drive_unlevered_symbol="QQQM",
    dual_drive_cash_reserve_ratio=0.02,
    dual_drive_allow_pullback=True,
    dual_drive_require_ma20_slope=True,
    dual_drive_pullback_rebound_window=20,
    dual_drive_pullback_rebound_threshold_mode=PULLBACK_REBOUND_THRESHOLD_MODE_VOLATILITY_SCALED,
    dual_drive_pullback_rebound_threshold=0.0,
    dual_drive_pullback_rebound_volatility_multiplier=2.0,
    dual_drive_volatility_delever_enabled=True,
    dual_drive_volatility_delever_window=5,
    dual_drive_volatility_delever_threshold=0.28,
    dual_drive_volatility_delever_exit_threshold=None,
    dual_drive_volatility_delever_threshold_mode=VOLATILITY_DELEVER_THRESHOLD_MODE_ROLLING_PERCENTILE,
    dual_drive_volatility_delever_dynamic_lookback=252,
    dual_drive_volatility_delever_dynamic_percentile=0.90,
    dual_drive_volatility_delever_dynamic_min_periods=126,
    dual_drive_volatility_delever_dynamic_floor=0.24,
    dual_drive_volatility_delever_dynamic_cap=0.36,
    dual_drive_volatility_delever_taco_veto_enabled=True,
    dual_drive_volatility_delever_retention_mode=RETENTION_MODE_NONE,
    dual_drive_volatility_delever_retention_ratio=0.0,
    dual_drive_volatility_delever_retention_policy=POLICY_TQQQ_STEP_SOFTZERO_025_050,
    dual_drive_volatility_delever_retention_context_required=True,
    dual_drive_volatility_delever_max_retention_ratio=0.50,
    dual_drive_macro_risk_governor_enabled=True,
    dual_drive_crisis_defense_enabled=True,
    market_regime_control_enabled=True,
):
    df_qqq = normalize_history_frame(qqq_history, label="benchmark_history")
    qqq_p = df_qqq["close"].iloc[-1]
    ma200 = df_qqq["close"].rolling(200).mean().iloc[-1]
    ma20 = df_qqq["close"].rolling(20).mean()
    ma20_slope = ma20.diff().iloc[-1]

    allocation_mode = str(attack_allocation_mode or "fixed_qqq_tqqq_pullback").strip().lower()
    if allocation_mode != "fixed_qqq_tqqq_pullback":
        raise ValueError("tqqq_growth_income only supports fixed_qqq_tqqq_pullback")

    unlevered_symbol = str(dual_drive_unlevered_symbol or "QQQM").strip().upper()
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
    snapshot_metadata = getattr(snapshot, "metadata", {}) or {}
    if not isinstance(snapshot_metadata, Mapping):
        snapshot_metadata = {}

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
        income_layer_core_stress_drawdown_ratio=income_layer_core_stress_drawdown_ratio,
        income_layer_income_stress_drawdown_ratio=income_layer_income_stress_drawdown_ratio,
        income_layer_base_drawdown_budget_ratio=income_layer_base_drawdown_budget_ratio,
        income_layer_min_drawdown_budget_ratio=income_layer_min_drawdown_budget_ratio,
        income_layer_drawdown_budget_decay_per_double=income_layer_drawdown_budget_decay_per_double,
    )
    target_income_values = income_layer_plan.target_values

    strategy_equity = max(0.0, total_equity - income_layer_plan.locked_value)
    cash_reserve_floor = max(0.0, float(cash_reserve_floor_usd or 0.0))
    reserved = max(strategy_equity * cash_reserve_ratio, cash_reserve_floor)

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
    volatility_delever_enabled = _as_bool(dual_drive_volatility_delever_enabled, default=True)
    volatility_delever_window = _as_positive_int(dual_drive_volatility_delever_window, default=5)
    volatility_delever_thresholds = _resolve_volatility_delever_thresholds(
        df_qqq["close"],
        volatility_window=volatility_delever_window,
        mode=dual_drive_volatility_delever_threshold_mode,
        fixed_entry_threshold=dual_drive_volatility_delever_threshold,
        fixed_exit_threshold=dual_drive_volatility_delever_exit_threshold,
        percentile_lookback=dual_drive_volatility_delever_dynamic_lookback,
        percentile=dual_drive_volatility_delever_dynamic_percentile,
        min_periods=dual_drive_volatility_delever_dynamic_min_periods,
        floor=dual_drive_volatility_delever_dynamic_floor,
        cap=dual_drive_volatility_delever_dynamic_cap,
    )
    volatility_delever_threshold_mode = str(volatility_delever_thresholds["mode"])
    volatility_delever_threshold = float(volatility_delever_thresholds["entry_threshold"])
    volatility_delever_exit_threshold = float(volatility_delever_thresholds["exit_threshold"])
    volatility_delever_dynamic_threshold = volatility_delever_thresholds["dynamic_threshold"]
    volatility_delever_dynamic_sample_count = int(volatility_delever_thresholds["dynamic_sample_count"])
    volatility_delever_dynamic_lookback = int(volatility_delever_thresholds["dynamic_lookback"])
    volatility_delever_dynamic_percentile = float(volatility_delever_thresholds["dynamic_percentile"])
    volatility_delever_dynamic_min_periods = int(volatility_delever_thresholds["dynamic_min_periods"])
    volatility_delever_dynamic_floor = volatility_delever_thresholds["dynamic_floor"]
    volatility_delever_dynamic_cap = volatility_delever_thresholds["dynamic_cap"]
    volatility_delever_metric = volatility_delever_thresholds["metric"] if volatility_delever_enabled else None
    taco_veto_enabled = _as_bool(dual_drive_volatility_delever_taco_veto_enabled, default=True)
    market_regime_control_enabled = _as_bool(market_regime_control_enabled, default=True)
    market_regime_control_context = (
        _resolve_market_regime_control_context(snapshot_metadata)
        if market_regime_control_enabled
        else _resolve_market_regime_control_context({})
    )
    if market_regime_control_context["found"]:
        taco_rebound_context_active = bool(
            market_regime_control_context["taco_allowed"]
            and market_regime_control_context["local_delever_veto_allowed"]
        )
        true_crisis_active = bool(market_regime_control_context["crisis_defense_required"])
    else:
        taco_rebound_context_active = _is_taco_rebound_context_active(snapshot_metadata)
        true_crisis_active = _is_true_crisis_active(snapshot_metadata)
    macro_risk_governor_enabled = _as_bool(dual_drive_macro_risk_governor_enabled, default=True)
    macro_risk_governor_context = (
        _market_regime_control_as_macro_context(market_regime_control_context)
        if market_regime_control_context["found"]
        else _resolve_macro_risk_governor_context(snapshot_metadata)
    )
    macro_risk_governor_active = bool(macro_risk_governor_enabled and macro_risk_governor_context["active"])
    retention_decision = resolve_volatility_delever_retention(
        mode=dual_drive_volatility_delever_retention_mode,
        fixed_ratio=dual_drive_volatility_delever_retention_ratio,
        policy=dual_drive_volatility_delever_retention_policy,
        max_ratio=dual_drive_volatility_delever_max_retention_ratio,
        context_required=dual_drive_volatility_delever_retention_context_required,
        market_regime_context=market_regime_control_context,
    )
    crisis_defense_enabled = _as_bool(dual_drive_crisis_defense_enabled, default=True)
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
        reserved = max(
            strategy_equity * max(0.0, min(1.0, dual_drive_reserve_ratio)),
            cash_reserve_floor,
        )
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
    macro_risk_governor_applied = False
    macro_risk_governor_removed_value = 0.0
    macro_risk_governor_redirected_to_unlevered = 0.0
    if macro_risk_governor_active:
        before_macro_risk_value = float(target_tqqq_val + target_unlevered_val)
        leverage_scalar = float(macro_risk_governor_context["leverage_scalar"])
        risk_asset_scalar = float(macro_risk_governor_context["risk_asset_scalar"])
        leverage_removed_value = max(0.0, target_tqqq_val * (1.0 - leverage_scalar))
        target_tqqq_val *= leverage_scalar
        if str(macro_risk_governor_context["route"]) == "delever":
            target_unlevered_val += leverage_removed_value
            macro_risk_governor_redirected_to_unlevered = leverage_removed_value
        if risk_asset_scalar < 1.0:
            target_tqqq_val *= risk_asset_scalar
            target_unlevered_val *= risk_asset_scalar
        after_macro_risk_value = float(target_tqqq_val + target_unlevered_val)
        macro_risk_governor_removed_value = max(0.0, before_macro_risk_value - after_macro_risk_value)
        target_boxx_val += macro_risk_governor_removed_value
        macro_risk_governor_applied = (
            leverage_removed_value > 1e-9 or before_macro_risk_value > after_macro_risk_value + 1e-9
        )
        if macro_risk_governor_applied:
            icon = "macro_risk_defense" if str(macro_risk_governor_context["route"]) == "crisis" else "macro_delever"
    crisis_defense_applied = bool(crisis_defense_enabled and true_crisis_active)
    crisis_defense_removed_value = 0.0
    if crisis_defense_applied:
        crisis_defense_removed_value = float(target_tqqq_val + target_unlevered_val)
        target_tqqq_val = 0.0
        target_unlevered_val = 0.0
        target_boxx_val = max(0.0, strategy_equity - reserved)
        icon = "crisis_defense"
    currently_volatility_delevered = (
        current_risk_active
        and quantities.get("TQQQ", 0) <= 0
        and quantities.get(unlevered_symbol, 0) > 0
    )
    volatility_delever_entry_triggered = (
        volatility_delever_enabled
        and target_tqqq_val > 0.0
        and volatility_delever_metric is not None
        and volatility_delever_metric >= volatility_delever_threshold
    )
    volatility_delever_hysteresis_triggered = (
        volatility_delever_enabled
        and target_tqqq_val > 0.0
        and currently_volatility_delevered
        and volatility_delever_metric is not None
        and volatility_delever_metric >= volatility_delever_exit_threshold
    )
    volatility_delever_triggered = bool(
        volatility_delever_entry_triggered or volatility_delever_hysteresis_triggered
    )
    volatility_delever_trigger_reason = (
        "entry_threshold"
        if volatility_delever_entry_triggered
        else "hysteresis_hold"
        if volatility_delever_hysteresis_triggered
        else None
    )
    volatility_delever_vetoed = bool(
        volatility_delever_triggered
        and taco_veto_enabled
        and taco_rebound_context_active
        and not true_crisis_active
        and str(retention_decision.get("mode") or "").strip().lower() != "environment"
    )
    volatility_delever_applied = bool(volatility_delever_triggered and not volatility_delever_vetoed)
    volatility_delever_source_value = 0.0
    volatility_delever_retained_value = 0.0
    volatility_delever_removed_value = 0.0
    volatility_delever_retained_ratio = None
    volatility_delever_redirected_ratio = None
    volatility_delever_veto_reason = None
    if volatility_delever_vetoed:
        volatility_delever_veto_reason = "taco_rebound_context"
    if volatility_delever_applied:
        volatility_delever_source_value = float(target_tqqq_val)
        retention_ratio = float(retention_decision.get("retention_ratio") or 0.0)
        retained_value = float(target_tqqq_val) * retention_ratio
        volatility_delever_retained_value = retained_value
        volatility_delever_removed_value = max(0.0, float(target_tqqq_val) - retained_value)
        if volatility_delever_source_value > 0.0:
            volatility_delever_retained_ratio = max(
                0.0,
                min(1.0, volatility_delever_retained_value / volatility_delever_source_value),
            )
            volatility_delever_redirected_ratio = max(0.0, min(1.0, 1.0 - volatility_delever_retained_ratio))
        target_unlevered_val += volatility_delever_removed_value
        target_tqqq_val = retained_value
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
    signal_reason_code, signal_reason = _build_signal_reason(
        icon=icon,
        translator=translator,
        pullback_risk_on=pullback_risk_on,
        above_ma200=above_ma200,
        slope_ok=slope_ok,
    )
    signal_context = {
        "state": icon,
        "reason_code": signal_reason_code,
        "reason": signal_reason,
    }
    risk_control_context = {
        "dual_drive_volatility_delever": {
            "enabled": volatility_delever_enabled,
            "window": volatility_delever_window,
            "threshold_mode": volatility_delever_threshold_mode,
            "threshold": float(volatility_delever_threshold),
            "exit_threshold": float(volatility_delever_exit_threshold),
            "dynamic_threshold": volatility_delever_dynamic_threshold,
            "dynamic_sample_count": volatility_delever_dynamic_sample_count,
            "dynamic_lookback": volatility_delever_dynamic_lookback,
            "dynamic_percentile": volatility_delever_dynamic_percentile,
            "dynamic_min_periods": volatility_delever_dynamic_min_periods,
            "dynamic_floor": volatility_delever_dynamic_floor,
            "dynamic_cap": volatility_delever_dynamic_cap,
            "metric": volatility_delever_metric,
            "triggered": volatility_delever_triggered,
            "entry_triggered": volatility_delever_entry_triggered,
            "hysteresis_triggered": volatility_delever_hysteresis_triggered,
            "trigger_reason": volatility_delever_trigger_reason,
            "applied": volatility_delever_applied,
            "vetoed": volatility_delever_vetoed,
            "veto_reason": volatility_delever_veto_reason,
            "taco_rebound_context_active": taco_rebound_context_active,
            "true_crisis_active": true_crisis_active,
            "retention_mode": retention_decision["mode"],
            "retention_policy": retention_decision["policy"],
            "retention_ratio": retention_decision["retention_ratio"],
            "retention_source": retention_decision["source"],
            "retention_context_found": retention_decision["context_found"],
            "retention_reason_codes": retention_decision["reason_codes"],
            "redirect_symbol": unlevered_symbol,
            "source_value": volatility_delever_source_value,
            "retained_value": volatility_delever_retained_value,
            "removed_value": volatility_delever_removed_value,
            "retained_ratio": volatility_delever_retained_ratio,
            "redirected_ratio": volatility_delever_redirected_ratio,
            "allocation_detail": _translate_context_with_fallback(
                translator,
                "tqqq_volatility_delever_allocation_detail",
                (
                    f"TQQQ sleeve retained {_format_ratio_text(volatility_delever_retained_ratio)}, "
                    f"redirected to {unlevered_symbol} {_format_ratio_text(volatility_delever_redirected_ratio)}"
                ),
                retained_ratio=_format_ratio_text(volatility_delever_retained_ratio),
                redirected_ratio=_format_ratio_text(volatility_delever_redirected_ratio),
                redirect_symbol=unlevered_symbol,
            ),
        },
        "dual_drive_macro_risk_governor": {
            "enabled": macro_risk_governor_enabled,
            "found": bool(macro_risk_governor_context["found"]),
            "route": macro_risk_governor_context["route"],
            "suggested_action": macro_risk_governor_context["suggested_action"],
            "active": macro_risk_governor_active,
            "applied": macro_risk_governor_applied,
            "leverage_scalar": macro_risk_governor_context["leverage_scalar"],
            "risk_asset_scalar": macro_risk_governor_context["risk_asset_scalar"],
            "actionable_score": macro_risk_governor_context["actionable_score"],
            "total_score": macro_risk_governor_context["total_score"],
            "reason_codes": macro_risk_governor_context["reason_codes"],
            "removed_value": macro_risk_governor_removed_value,
            "redirected_to_unlevered": macro_risk_governor_redirected_to_unlevered,
        },
        "market_regime_control": {
            "enabled": market_regime_control_enabled,
            "found": bool(market_regime_control_context["found"]),
            "schema_version": market_regime_control_context["schema_version"],
            "route": market_regime_control_context["route"],
            "route_source": market_regime_control_context["route_source"],
            "active": bool(market_regime_control_context["active"]),
            "suggested_action": market_regime_control_context["suggested_action"],
            "risk_budget_scalar": market_regime_control_context["risk_budget_scalar"],
            "leverage_scalar": market_regime_control_context["leverage_scalar"],
            "risk_asset_scalar": market_regime_control_context["risk_asset_scalar"],
            "taco_allowed": market_regime_control_context["taco_allowed"],
            "local_delever_veto_allowed": market_regime_control_context["local_delever_veto_allowed"],
            "crisis_defense_required": market_regime_control_context["crisis_defense_required"],
            "blocked_actions": market_regime_control_context["blocked_actions"],
            "vetoes": market_regime_control_context["vetoes"],
            "reason_codes": market_regime_control_context["reason_codes"],
            "localized_messages": market_regime_control_context["localized_messages"],
            "log_record": market_regime_control_context["log_record"],
            "notification": market_regime_control_context["notification"],
        },
        "dual_drive_crisis_defense": {
            "enabled": crisis_defense_enabled,
            "triggered": true_crisis_active,
            "applied": crisis_defense_applied,
            "destination": "BOXX",
            "removed_value": crisis_defense_removed_value,
        },
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
        "risk_controls": risk_control_context,
    }

    sig_display = f"{signal_text_fn(icon)} | {signal_reason}"
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
    if volatility_delever_enabled and volatility_delever_metric is not None:
        status = "applied" if volatility_delever_applied else "vetoed" if volatility_delever_vetoed else "watch"
        dashboard += (
            f"\nVol Delever: {status} | QQQ {volatility_delever_window}d vol "
            f"{volatility_delever_metric * 100:.1f}% / enter {volatility_delever_threshold * 100:.1f}%"
            f" / exit {volatility_delever_exit_threshold * 100:.1f}%"
        )
        if volatility_delever_threshold_mode == VOLATILITY_DELEVER_THRESHOLD_MODE_ROLLING_PERCENTILE:
            dashboard += (
                f" | mode p{volatility_delever_dynamic_percentile * 100:.0f}"
                f"/{volatility_delever_dynamic_lookback}d"
            )
    if macro_risk_governor_enabled and macro_risk_governor_context["found"]:
        status = "applied" if macro_risk_governor_applied else "watch"
        score = macro_risk_governor_context["actionable_score"]
        score_text = "n/a" if score is None else f"{float(score):.1f}"
        risk_control_label = (
            "Market Regime Control"
            if macro_risk_governor_context.get("source") == MARKET_REGIME_CONTROL_PROFILE
            else "Macro Risk Governor"
        )
        dashboard += f"\n{risk_control_label}: {status} | route {macro_risk_governor_context['route'] or 'none'} | score {score_text}"
    if crisis_defense_enabled and true_crisis_active:
        status = "applied" if crisis_defense_applied else "watch"
        dashboard += f"\nCrisis Defense: {status} | destination BOXX"
    sell_order_symbols = ("TQQQ", unlevered_symbol, *income_symbols, "BOXX")
    buy_order_symbols = (*income_symbols, "TQQQ", unlevered_symbol)
    account_hash = snapshot_metadata.get("account_hash") if isinstance(snapshot_metadata, Mapping) else None

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
        "dual_drive_volatility_delever_enabled": volatility_delever_enabled,
        "dual_drive_volatility_delever_window": volatility_delever_window,
        "dual_drive_volatility_delever_threshold_mode": volatility_delever_threshold_mode,
        "dual_drive_volatility_delever_threshold": float(volatility_delever_threshold),
        "dual_drive_volatility_delever_exit_threshold": float(volatility_delever_exit_threshold),
        "dual_drive_volatility_delever_dynamic_threshold": volatility_delever_dynamic_threshold,
        "dual_drive_volatility_delever_dynamic_sample_count": volatility_delever_dynamic_sample_count,
        "dual_drive_volatility_delever_dynamic_lookback": volatility_delever_dynamic_lookback,
        "dual_drive_volatility_delever_dynamic_percentile": volatility_delever_dynamic_percentile,
        "dual_drive_volatility_delever_dynamic_min_periods": volatility_delever_dynamic_min_periods,
        "dual_drive_volatility_delever_dynamic_floor": volatility_delever_dynamic_floor,
        "dual_drive_volatility_delever_dynamic_cap": volatility_delever_dynamic_cap,
        "dual_drive_volatility_delever_metric": volatility_delever_metric,
        "dual_drive_volatility_delever_triggered": volatility_delever_triggered,
        "dual_drive_volatility_delever_entry_triggered": volatility_delever_entry_triggered,
        "dual_drive_volatility_delever_hysteresis_triggered": volatility_delever_hysteresis_triggered,
        "dual_drive_volatility_delever_trigger_reason": volatility_delever_trigger_reason,
        "dual_drive_volatility_delever_applied": volatility_delever_applied,
        "dual_drive_volatility_delever_vetoed": volatility_delever_vetoed,
        "dual_drive_volatility_delever_veto_reason": volatility_delever_veto_reason,
        "dual_drive_volatility_delever_taco_veto_enabled": taco_veto_enabled,
        "dual_drive_volatility_delever_taco_rebound_context_active": taco_rebound_context_active,
        "dual_drive_volatility_delever_true_crisis_active": true_crisis_active,
        "dual_drive_volatility_delever_retention_mode": retention_decision["mode"],
        "dual_drive_volatility_delever_retention_policy": retention_decision["policy"],
        "dual_drive_volatility_delever_retention_ratio": retention_decision["retention_ratio"],
        "dual_drive_volatility_delever_retention_source": retention_decision["source"],
        "dual_drive_volatility_delever_retention_context_found": retention_decision["context_found"],
        "dual_drive_volatility_delever_retention_reason_codes": retention_decision["reason_codes"],
        "dual_drive_volatility_delever_redirect_symbol": unlevered_symbol,
        "dual_drive_volatility_delever_source_value": volatility_delever_source_value,
        "dual_drive_volatility_delever_retained_value": volatility_delever_retained_value,
        "dual_drive_volatility_delever_removed_value": volatility_delever_removed_value,
        "dual_drive_volatility_delever_retained_ratio": volatility_delever_retained_ratio,
        "dual_drive_volatility_delever_redirected_ratio": volatility_delever_redirected_ratio,
        "dual_drive_macro_risk_governor_enabled": macro_risk_governor_enabled,
        "dual_drive_macro_risk_governor_found": bool(macro_risk_governor_context["found"]),
        "dual_drive_macro_risk_governor_route": macro_risk_governor_context["route"],
        "dual_drive_macro_risk_governor_active": macro_risk_governor_active,
        "dual_drive_macro_risk_governor_applied": macro_risk_governor_applied,
        "dual_drive_macro_risk_governor_leverage_scalar": macro_risk_governor_context["leverage_scalar"],
        "dual_drive_macro_risk_governor_risk_asset_scalar": macro_risk_governor_context["risk_asset_scalar"],
        "dual_drive_macro_risk_governor_removed_value": macro_risk_governor_removed_value,
        "dual_drive_macro_risk_governor_redirected_to_unlevered": macro_risk_governor_redirected_to_unlevered,
        "market_regime_control_enabled": market_regime_control_enabled,
        "market_regime_control_found": bool(market_regime_control_context["found"]),
        "market_regime_control_schema_version": market_regime_control_context["schema_version"],
        "market_regime_control_route": market_regime_control_context["route"],
        "market_regime_control_route_source": market_regime_control_context["route_source"],
        "market_regime_control_active": bool(market_regime_control_context["active"]),
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
        "market_regime_control_taco_allowed": market_regime_control_context["taco_allowed"],
        "market_regime_control_local_delever_veto_allowed": market_regime_control_context[
            "local_delever_veto_allowed"
        ],
        "market_regime_control_crisis_defense_required": market_regime_control_context["crisis_defense_required"],
        "market_regime_control_blocked_actions": market_regime_control_context["blocked_actions"],
        "market_regime_control_vetoes": market_regime_control_context["vetoes"],
        "market_regime_control_reason_codes": market_regime_control_context["reason_codes"],
        "dual_drive_crisis_defense_enabled": crisis_defense_enabled,
        "dual_drive_crisis_defense_triggered": true_crisis_active,
        "dual_drive_crisis_defense_applied": crisis_defense_applied,
        "dual_drive_crisis_defense_destination": "BOXX",
        "dual_drive_crisis_defense_removed_value": crisis_defense_removed_value,
        "allocation_mode": allocation_mode,
        "separator": separator,
    }
