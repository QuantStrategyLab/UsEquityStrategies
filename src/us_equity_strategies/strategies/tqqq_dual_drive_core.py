"""Pure TQQQ dual-drive allocation and risk-control decision boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class DualDriveCoreInput:
    qqq_price: float
    ma200: float
    latest_ma20: float | None
    ma20_slope: float | None
    pullback_rebound: float | None
    pullback_rebound_threshold: float
    current_tqqq_quantity: float
    current_unlevered_quantity: float
    require_ma20_slope: bool
    allow_pullback: bool
    strategy_equity: float
    initial_reserved: float
    cash_reserve_floor: float
    risk_on_cash_reserve_ratio: float | int | None
    tqqq_weight: float | int | None
    unlevered_weight: float | int | None
    macro_active: bool
    macro_route: str | None
    macro_leverage_scalar: float
    macro_risk_asset_scalar: float
    crisis_defense_enabled: bool
    true_crisis_active: bool
    volatility_enabled: bool
    volatility_metric: float | None
    volatility_entry_threshold: float
    volatility_exit_threshold: float
    taco_veto_enabled: bool
    taco_rebound_context_active: bool
    retention_mode: str | None
    retention_ratio: float


@dataclass(frozen=True, slots=True)
class DualDriveCoreDecision:
    state: Literal[
        "entry", "hold", "exit", "idle", "macro_delever", "macro_risk_defense", "crisis_defense"
    ]
    above_ma200: bool
    slope_ok: bool
    pullback_risk_on: bool
    reserved: float
    target_tqqq_value: float
    target_unlevered_value: float
    target_boxx_value: float
    macro_applied: bool
    macro_removed_value: float
    macro_redirected_to_unlevered_value: float
    crisis_applied: bool
    crisis_removed_value: float
    volatility_triggered: bool
    volatility_entry_triggered: bool
    volatility_hysteresis_triggered: bool
    volatility_trigger_reason: Literal["entry_threshold", "hysteresis_hold"] | None
    volatility_applied: bool
    volatility_vetoed: bool
    volatility_veto_reason: Literal["taco_rebound_context"] | None
    volatility_source_value: float
    volatility_retained_value: float
    volatility_removed_value: float
    volatility_retained_ratio: float | None
    volatility_redirected_ratio: float | None


def decide_tqqq_dual_drive(value: DualDriveCoreInput, /) -> DualDriveCoreDecision:
    pullback_rebound_ok = (
        value.pullback_rebound_threshold <= 0.0
        or (
            value.pullback_rebound is not None
            and value.pullback_rebound > value.pullback_rebound_threshold
        )
    )
    above_ma200 = value.qqq_price > value.ma200
    positive_ma20_slope = value.ma20_slope is not None and value.ma20_slope > 0.0
    slope_ok = positive_ma20_slope if value.require_ma20_slope else True
    current_risk_active = value.current_tqqq_quantity > 0 or value.current_unlevered_quantity > 0
    risk_active = current_risk_active
    if current_risk_active and not above_ma200:
        risk_active = False
    elif not current_risk_active and above_ma200 and slope_ok:
        risk_active = True
    pullback_risk_on = (
        value.allow_pullback
        and not above_ma200
        and value.latest_ma20 is not None
        and value.qqq_price > value.latest_ma20
        and positive_ma20_slope
        and pullback_rebound_ok
    )

    reserved = value.initial_reserved
    target_unlevered_value = 0.0
    if risk_active or pullback_risk_on:
        dual_drive_reserve_ratio = (
            0.02 if value.risk_on_cash_reserve_ratio is None else float(value.risk_on_cash_reserve_ratio)
        )
        reserved = max(
            value.strategy_equity * max(0.0, min(1.0, dual_drive_reserve_ratio)),
            value.cash_reserve_floor,
        )
        target_tqqq_ratio = max(0.0, min(1.0, float(value.tqqq_weight or 0.45)))
        target_unlevered_ratio = max(0.0, min(1.0, float(value.unlevered_weight or 0.45)))
        total_risk_ratio = target_tqqq_ratio + target_unlevered_ratio
        max_risk_ratio = (
            max(0.0, 1.0 - reserved / value.strategy_equity) if value.strategy_equity > 0.0 else 0.0
        )
        if total_risk_ratio > max_risk_ratio and total_risk_ratio > 0.0:
            scale = max_risk_ratio / total_risk_ratio
            target_tqqq_ratio *= scale
            target_unlevered_ratio *= scale
        target_tqqq_value = value.strategy_equity * target_tqqq_ratio
        target_unlevered_value = value.strategy_equity * target_unlevered_ratio
        target_boxx_value = max(
            0.0,
            (value.strategy_equity - reserved) - target_tqqq_value - target_unlevered_value,
        )
        state = "hold" if current_risk_active else "entry"
    else:
        target_tqqq_value = 0.0
        target_boxx_value = max(0.0, value.strategy_equity - reserved)
        state = "exit" if current_risk_active else "idle"

    macro_applied = False
    macro_removed_value = 0.0
    macro_redirected_to_unlevered_value = 0.0
    if value.macro_active:
        before_macro_risk_value = float(target_tqqq_value + target_unlevered_value)
        leverage_removed_value = max(0.0, target_tqqq_value * (1.0 - value.macro_leverage_scalar))
        target_tqqq_value *= value.macro_leverage_scalar
        if value.macro_route == "delever":
            target_unlevered_value += leverage_removed_value
            macro_redirected_to_unlevered_value = leverage_removed_value
        if value.macro_risk_asset_scalar < 1.0:
            target_tqqq_value *= value.macro_risk_asset_scalar
            target_unlevered_value *= value.macro_risk_asset_scalar
        after_macro_risk_value = float(target_tqqq_value + target_unlevered_value)
        macro_removed_value = max(0.0, before_macro_risk_value - after_macro_risk_value)
        target_boxx_value += macro_removed_value
        macro_applied = (
            leverage_removed_value > 1e-9 or before_macro_risk_value > after_macro_risk_value + 1e-9
        )
        if macro_applied:
            state = "macro_risk_defense" if value.macro_route == "crisis" else "macro_delever"

    crisis_applied = bool(value.crisis_defense_enabled and value.true_crisis_active)
    crisis_removed_value = 0.0
    if crisis_applied:
        crisis_removed_value = float(target_tqqq_value + target_unlevered_value)
        target_tqqq_value = 0.0
        target_unlevered_value = 0.0
        target_boxx_value = max(0.0, value.strategy_equity - reserved)
        state = "crisis_defense"

    currently_volatility_delevered = (
        current_risk_active
        and value.current_tqqq_quantity <= 0
        and value.current_unlevered_quantity > 0
    )
    volatility_entry_triggered = (
        value.volatility_enabled
        and target_tqqq_value > 0.0
        and value.volatility_metric is not None
        and value.volatility_metric >= value.volatility_entry_threshold
    )
    volatility_hysteresis_triggered = (
        value.volatility_enabled
        and target_tqqq_value > 0.0
        and currently_volatility_delevered
        and value.volatility_metric is not None
        and value.volatility_metric >= value.volatility_exit_threshold
    )
    volatility_triggered = bool(volatility_entry_triggered or volatility_hysteresis_triggered)
    volatility_trigger_reason = (
        "entry_threshold"
        if volatility_entry_triggered
        else "hysteresis_hold"
        if volatility_hysteresis_triggered
        else None
    )
    volatility_vetoed = bool(
        volatility_triggered
        and value.taco_veto_enabled
        and value.taco_rebound_context_active
        and not value.true_crisis_active
        and str(value.retention_mode or "").strip().lower() != "environment"
    )
    volatility_applied = bool(volatility_triggered and not volatility_vetoed)
    volatility_source_value = 0.0
    volatility_retained_value = 0.0
    volatility_removed_value = 0.0
    volatility_retained_ratio = None
    volatility_redirected_ratio = None
    volatility_veto_reason = None
    if volatility_vetoed:
        volatility_veto_reason = "taco_rebound_context"
    if volatility_applied:
        volatility_source_value = float(target_tqqq_value)
        retained_value = float(target_tqqq_value) * value.retention_ratio
        volatility_retained_value = retained_value
        volatility_removed_value = max(0.0, float(target_tqqq_value) - retained_value)
        if volatility_source_value > 0.0:
            volatility_retained_ratio = max(
                0.0,
                min(1.0, volatility_retained_value / volatility_source_value),
            )
            volatility_redirected_ratio = max(0.0, min(1.0, 1.0 - volatility_retained_ratio))
        target_unlevered_value += volatility_removed_value
        target_tqqq_value = retained_value

    return DualDriveCoreDecision(
        state=state,
        above_ma200=above_ma200,
        slope_ok=slope_ok,
        pullback_risk_on=pullback_risk_on,
        reserved=reserved,
        target_tqqq_value=target_tqqq_value,
        target_unlevered_value=target_unlevered_value,
        target_boxx_value=target_boxx_value,
        macro_applied=macro_applied,
        macro_removed_value=macro_removed_value,
        macro_redirected_to_unlevered_value=macro_redirected_to_unlevered_value,
        crisis_applied=crisis_applied,
        crisis_removed_value=crisis_removed_value,
        volatility_triggered=volatility_triggered,
        volatility_entry_triggered=volatility_entry_triggered,
        volatility_hysteresis_triggered=volatility_hysteresis_triggered,
        volatility_trigger_reason=volatility_trigger_reason,
        volatility_applied=volatility_applied,
        volatility_vetoed=volatility_vetoed,
        volatility_veto_reason=volatility_veto_reason,
        volatility_source_value=volatility_source_value,
        volatility_retained_value=volatility_retained_value,
        volatility_removed_value=volatility_removed_value,
        volatility_retained_ratio=volatility_retained_ratio,
        volatility_redirected_ratio=volatility_redirected_ratio,
    )
