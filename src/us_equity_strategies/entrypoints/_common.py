from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from quant_platform_kit.risk.gate import apply_risk_gate as _qpk_apply_risk_gate
from quant_platform_kit.risk.gate import enrich_decision_risk_diagnostics
from quant_platform_kit.risk.portfolio_diagnostics import extract_portfolio_risk_diagnostics
from quant_platform_kit.strategy_contracts import PositionTarget, StrategyContext, StrategyDecision
from quant_platform_kit.strategy_lifecycle.performance_monitor import PerformanceMonitor

logger = logging.getLogger(__name__)
from us_equity_strategies.income_layer import (
    build_income_layer_plan,
    normalize_income_layer_allocations,
)
from us_equity_strategies.market_regime_control_contract import (
    resolve_market_regime_position_control_authorization,
)
from us_equity_strategies.option_overlay import (
    OPTION_OVERLAY_CONFIG_KEYS as OPTION_OVERLAY_CONFIG_KEYS,
    OPTION_OVERLAY_RECIPE_DETAILS as OPTION_OVERLAY_RECIPE_DETAILS,
    build_option_overlay_diagnostics as build_option_overlay_diagnostics,
)


# ---------------------------------------------------------------------------
# 风控硬门 — 每个 entrypoint 返回 StrategyDecision 前必须调用
# ---------------------------------------------------------------------------

_performance_monitor: PerformanceMonitor | None = None


def record_strategy_decision(
    ctx: StrategyContext,
    decision: StrategyDecision,
    *,
    profile_id: str,
    domain: str,
) -> None:
    """Record per-run decision for live monitoring (roadmap 5a)."""
    global _performance_monitor
    try:
        if _performance_monitor is None:
            _performance_monitor = PerformanceMonitor()
        _performance_monitor.record(
            profile_id,
            decision,
            execution_result={},
            domain=domain,
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("PerformanceMonitor.record failed: %s", exc)


def apply_risk_gate(
    decision: StrategyDecision,
    *,
    ctx: StrategyContext | None = None,
    max_single_weight: float = 1.0,
    max_positions: int = 20,
    max_total_exposure: float = 1.0,
    portfolio_snapshot: Any | None = None,
    market_data: Mapping[str, Any] | None = None,
) -> StrategyDecision:
    """QPK unified risk gate: stop-loss, circuit breaker, concentration (task 8)."""
    snapshot = portfolio_snapshot if portfolio_snapshot is not None else (
        ctx.portfolio if ctx is not None else None
    )
    if snapshot is not None:
        portfolio_diag = extract_portfolio_risk_diagnostics(snapshot)
        decision = enrich_decision_risk_diagnostics(
            decision,
            unrealized_pnl_pct=portfolio_diag.get("unrealized_pnl_pct"),
            consecutive_losses=portfolio_diag.get("consecutive_losses"),
        )
    if market_data is None and ctx is not None:
        market_data = dict(ctx.market_data or {})
    return _qpk_apply_risk_gate(
        decision,
        max_single_weight=max_single_weight,
        max_positions=max_positions,
        max_total_exposure=max_total_exposure,
        portfolio_snapshot=snapshot,
        market_data=market_data,
    )


SAFE_HAVENS = {"BIL", "BOXX"}
INCOME_SYMBOLS = {"SCHD", "DGRO", "VIG", "VYM", "HDV", "SGOV", "SPYI", "QQQI"}
INCOME_LAYER_CONFIG_KEYS = {
    "income_layer_enabled",
    "income_layer_start_usd",
    "income_layer_max_ratio",
    "income_layer_activation_band_ratio",
    "income_layer_ratio_mode",
    "income_layer_core_stress_drawdown_ratio",
    "income_layer_income_stress_drawdown_ratio",
    "income_layer_base_drawdown_budget_ratio",
    "income_layer_min_drawdown_budget_ratio",
    "income_layer_drawdown_budget_decay_per_double",
    "income_layer_qqqi_weight",
    "income_layer_spyi_weight",
    "income_layer_allocations",
}
EXECUTION_ONLY_CONFIG_KEYS = {
    "execution_cash_reserve_ratio",
    "execution_rebalance_threshold_ratio",
    "reserved_cash_floor_usd",
    "reserved_cash_ratio",
}
MARKET_REGIME_CONTROL_CONFIG_KEYS = {
    "market_regime_control_enabled",
    "market_regime_control_apply_risk_reduced",
    "market_regime_control_apply_risk_off",
    "market_regime_control_risk_reduced_scalar",
    "market_regime_control_risk_off_scalar",
    "market_regime_control_safe_haven",
}
MARKET_REGIME_CONTROL_PROFILE = "market_regime_control"
MARKET_REGIME_POSITION_ROUTES = frozenset({"risk_reduced", "risk_off"})


def merge_runtime_config(manifest_default_config: Mapping[str, object], ctx: StrategyContext) -> dict[str, object]:
    config = dict(manifest_default_config)
    config.update(dict(ctx.runtime_config))
    return config


def pop_income_layer_config(config: dict[str, object]) -> dict[str, object]:
    return {key: config.pop(key) for key in INCOME_LAYER_CONFIG_KEYS if key in config}


def pop_option_overlay_config(config: dict[str, object]) -> dict[str, object]:
    return {key: config.pop(key) for key in OPTION_OVERLAY_CONFIG_KEYS if key in config}


def pop_execution_only_config(config: dict[str, object]) -> None:
    for key in EXECUTION_ONLY_CONFIG_KEYS:
        config.pop(key, None)


def pop_reserved_cash_policy_config(config: dict[str, object]) -> dict[str, float]:
    policy: dict[str, float] = {}
    if "reserved_cash_floor_usd" in config:
        policy["reserved_cash_floor_usd"] = max(
            0.0,
            _as_float(config.pop("reserved_cash_floor_usd"), default=0.0),
        )
    if "reserved_cash_ratio" in config:
        policy["reserved_cash_ratio"] = _clamped_ratio(
            config.pop("reserved_cash_ratio"),
            default=0.0,
            upper=1.0,
        )
    return policy


def apply_reserved_cash_policy_to_ratio_config(
    config: dict[str, object],
    policy: Mapping[str, float],
) -> None:
    if not policy:
        return
    policy_ratio = float(policy.get("reserved_cash_ratio", 0.0) or 0.0)
    if policy_ratio > 0.0:
        config["cash_reserve_ratio"] = max(
            _clamped_ratio(config.get("cash_reserve_ratio"), default=0.0, upper=1.0),
            policy_ratio,
        )
    policy_floor = float(policy.get("reserved_cash_floor_usd", 0.0) or 0.0)
    if policy_floor > 0.0:
        config["cash_reserve_floor_usd"] = max(
            0.0,
            _as_float(config.get("cash_reserve_floor_usd"), default=0.0),
            policy_floor,
        )


def apply_reserved_cash_policy_to_usd_config(
    config: dict[str, object],
    policy: Mapping[str, float],
    *,
    total_equity: float,
) -> None:
    if not policy:
        return
    reserved_cash = max(
        float(policy.get("reserved_cash_floor_usd", 0.0) or 0.0),
        max(0.0, float(total_equity or 0.0))
        * float(policy.get("reserved_cash_ratio", 0.0) or 0.0),
    )
    if reserved_cash <= 0.0:
        return
    config["cash_reserve_usd"] = max(
        0.0,
        _as_float(config.get("cash_reserve_usd"), default=0.0),
        reserved_cash,
    )


def pop_market_regime_control_config(config: dict[str, object]) -> dict[str, object]:
    return {key: config.pop(key) for key in MARKET_REGIME_CONTROL_CONFIG_KEYS if key in config}


def _as_bool(value: object, *, default: bool = False) -> bool:
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


def _as_float(value: object, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamped_ratio(value: object, *, default: float, upper: float) -> float:
    return max(0.0, min(float(upper), _as_float(value, default=default)))


def _iter_mapping_payloads(value: object, *, _depth: int = 0):
    if _depth > 4:
        return
    if isinstance(value, Mapping):
        yield value
        for item in value.values():
            yield from _iter_mapping_payloads(item, _depth=_depth + 1)
    elif isinstance(value, (str, bytes, bytearray)):
        return
    else:
        try:
            iterator = iter(value)  # type: ignore[arg-type]
        except TypeError:
            return
        for item in iterator:
            yield from _iter_mapping_payloads(item, _depth=_depth + 1)


def _normalized_text_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, (bytes, bytearray)):
        return ()
    try:
        return tuple(str(item).strip() for item in value if str(item).strip())  # type: ignore[union-attr]
    except TypeError:
        return ()


def _market_regime_control_not_found() -> dict[str, object]:
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
        "crisis_defense_required": False,
        "blocked_actions": (),
        "vetoes": (),
        "reason_codes": (),
        "localized_messages": {},
        "log_record": {},
        "notification": {},
        "blocked": False,
    }


def _resolve_market_regime_control_from_payloads(*sources: object) -> dict[str, object]:
    for source in sources:
        for payload in _iter_mapping_payloads(source):
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
                "risk_budget_scalar": _clamped_ratio(position_control.get("risk_budget_scalar"), default=1.0, upper=1.0),
                "leverage_scalar": _clamped_ratio(position_control.get("leverage_scalar"), default=1.0, upper=1.0),
                "risk_asset_scalar": _clamped_ratio(position_control.get("risk_asset_scalar"), default=1.0, upper=1.0),
                "crisis_defense_required": _as_bool(position_control.get("crisis_defense_required"), default=False),
                "blocked_actions": _normalized_text_tuple(position_control.get("blocked_actions")),
                "vetoes": _normalized_text_tuple(position_control.get("vetoes"))
                or _normalized_text_tuple(arbiter.get("vetoes")),
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
            }
    return _market_regime_control_not_found()


def resolve_market_regime_control_context(ctx: StrategyContext) -> dict[str, object]:
    portfolio_metadata = {}
    if ctx.portfolio is not None:
        raw_metadata = getattr(ctx.portfolio, "metadata", {}) or {}
        if isinstance(raw_metadata, Mapping):
            portfolio_metadata = raw_metadata
    return _resolve_market_regime_control_from_payloads(
        ctx.artifacts.get(MARKET_REGIME_CONTROL_PROFILE),
        ctx.market_data.get(MARKET_REGIME_CONTROL_PROFILE),
        ctx.runtime_config.get(MARKET_REGIME_CONTROL_PROFILE),
        portfolio_metadata,
    )


def _portfolio_market_values(ctx: StrategyContext, symbols: tuple[str, ...]) -> dict[str, float]:
    market_values = {symbol: 0.0 for symbol in symbols}
    portfolio = ctx.portfolio
    if portfolio is None:
        return market_values
    for position in getattr(portfolio, "positions", ()) or ():
        symbol = str(getattr(position, "symbol", "") or "").strip().upper()
        if symbol in market_values:
            market_values[symbol] += float(getattr(position, "market_value", 0.0) or 0.0)
    return market_values


def apply_income_layer_to_weights(
    weights,
    *,
    income_layer_config: Mapping[str, object],
    ctx: StrategyContext,
    excluded_symbols=(),
) -> tuple[dict[str, float] | None, dict[str, object]]:
    if not weights:
        return weights, {}
    if ctx.portfolio is None:
        return dict(weights), {"income_layer_applied": False, "income_layer_skip_reason": "missing_portfolio"}

    total_equity = float(getattr(ctx.portfolio, "total_equity", 0.0) or 0.0)
    if total_equity <= 0.0:
        return dict(weights), {"income_layer_applied": False, "income_layer_skip_reason": "missing_total_equity"}

    fallback_allocations = (
        ("SPYI", income_layer_config.get("income_layer_spyi_weight", 0.0)),
        ("QQQI", income_layer_config.get("income_layer_qqqi_weight", 0.0)),
    )
    normalized_exclusions = {
        str(symbol or "").strip().upper()
        for symbol in (*dict(weights).keys(), *tuple(excluded_symbols or ()))
    }
    allocations = normalize_income_layer_allocations(
        income_layer_config.get("income_layer_allocations"),
        fallback_allocations=fallback_allocations,
        excluded_symbols=normalized_exclusions,
    )
    if not allocations:
        return dict(weights), {"income_layer_applied": False, "income_layer_skip_reason": "empty_allocations"}

    market_values = _portfolio_market_values(ctx, tuple(allocations))
    plan = build_income_layer_plan(
        total_equity_usd=total_equity,
        market_values=market_values,
        allocations=allocations,
        income_layer_enabled=income_layer_config.get("income_layer_enabled", True),
        income_layer_start_usd=income_layer_config.get("income_layer_start_usd", 0.0),
        income_layer_max_ratio=income_layer_config.get("income_layer_max_ratio", 0.0),
        income_layer_activation_band_ratio=income_layer_config.get(
            "income_layer_activation_band_ratio",
            0.0,
        ),
        income_layer_ratio_mode=income_layer_config.get("income_layer_ratio_mode", "log_total_drawdown_budget"),
        income_layer_core_stress_drawdown_ratio=income_layer_config.get(
            "income_layer_core_stress_drawdown_ratio",
            0.40,
        ),
        income_layer_income_stress_drawdown_ratio=income_layer_config.get(
            "income_layer_income_stress_drawdown_ratio",
            0.08,
        ),
        income_layer_base_drawdown_budget_ratio=income_layer_config.get(
            "income_layer_base_drawdown_budget_ratio",
            0.30,
        ),
        income_layer_min_drawdown_budget_ratio=income_layer_config.get(
            "income_layer_min_drawdown_budget_ratio",
            0.15,
        ),
        income_layer_drawdown_budget_decay_per_double=income_layer_config.get(
            "income_layer_drawdown_budget_decay_per_double",
            0.05,
        ),
    )
    locked_ratio = min(1.0, plan.locked_value / total_equity)
    core_scale = max(0.0, 1.0 - locked_ratio)
    target_weights = {
        str(symbol).strip().upper(): float(weight) * core_scale
        for symbol, weight in dict(weights).items()
        if str(symbol).strip().upper() not in plan.symbols
    }
    for symbol, target_value in plan.target_values.items():
        target_weight = float(target_value) / total_equity
        if abs(target_weight) > 1e-12:
            target_weights[symbol] = target_weight

    diagnostics = {
        "income_layer_applied": True,
        "income_layer_allocations": plan.allocations,
        "income_layer_symbols": plan.symbols,
        "income_layer_ratio": plan.ratio,
        "income_layer_value": plan.locked_value,
        "income_layer_core_scale": core_scale,
        **plan.diagnostics,
    }
    return target_weights, diagnostics


def apply_market_regime_control_to_weights(
    weights,
    *,
    market_regime_control_config: Mapping[str, object],
    ctx: StrategyContext,
    safe_haven: str | None,
    excluded_symbols=(),
) -> tuple[dict[str, float] | None, dict[str, object]]:
    if not weights:
        return weights, {}

    enabled = _as_bool(market_regime_control_config.get("market_regime_control_enabled"), default=False)
    context = resolve_market_regime_control_context(ctx) if enabled else _market_regime_control_not_found()
    route = str(context.get("route") or "").strip().lower()
    apply_risk_reduced = _as_bool(
        market_regime_control_config.get("market_regime_control_apply_risk_reduced"),
        default=True,
    )
    apply_risk_off = _as_bool(
        market_regime_control_config.get("market_regime_control_apply_risk_off"),
        default=True,
    )
    route_allowed = (route == "risk_reduced" and apply_risk_reduced) or (route == "risk_off" and apply_risk_off)
    active = bool(enabled and context.get("active") and route_allowed)
    safe_haven_symbol = str(market_regime_control_config.get("market_regime_control_safe_haven") or safe_haven or "").strip().upper()
    if route == "risk_off":
        scalar = _clamped_ratio(
            market_regime_control_config.get("market_regime_control_risk_off_scalar"),
            default=float(context.get("risk_budget_scalar") or 0.0),
            upper=1.0,
        )
    elif route == "risk_reduced":
        scalar = _clamped_ratio(
            market_regime_control_config.get("market_regime_control_risk_reduced_scalar"),
            default=float(context.get("risk_budget_scalar") or 1.0),
            upper=1.0,
        )
    else:
        scalar = 1.0

    target_weights = {str(symbol).strip().upper(): float(weight) for symbol, weight in dict(weights).items()}
    excluded = {str(symbol or "").strip().upper() for symbol in (*tuple(excluded_symbols or ()), *INCOME_SYMBOLS)}
    if safe_haven_symbol:
        excluded.add(safe_haven_symbol)
    risk_symbols = tuple(symbol for symbol, weight in target_weights.items() if symbol not in excluded and weight > 0.0)
    removed_weight = 0.0
    if active and risk_symbols:
        for symbol in risk_symbols:
            before = target_weights[symbol]
            after = before * scalar
            target_weights[symbol] = after
            removed_weight += max(0.0, before - after)
        if safe_haven_symbol and removed_weight > 1e-12:
            target_weights[safe_haven_symbol] = target_weights.get(safe_haven_symbol, 0.0) + removed_weight

    applied = bool(active and removed_weight > 1e-12)
    diagnostics = {
        "market_regime_control_enabled": enabled,
        "market_regime_control_found": bool(context.get("found")),
        "market_regime_control_schema_version": context.get("schema_version"),
        "market_regime_control_route": route,
        "market_regime_control_route_source": context.get("route_source"),
        "market_regime_control_active": bool(context.get("active")),
        "market_regime_control_applied": applied,
        "market_regime_control_route_allowed": route_allowed,
        "market_regime_control_position_control_allowed": context.get("position_control_allowed"),
        "market_regime_control_position_control_authorized": context.get("position_control_authorized"),
        "market_regime_control_consumption_evidence_status": context.get("consumption_evidence_status"),
        "market_regime_control_risk_scalar": scalar,
        "market_regime_control_removed_weight": removed_weight,
        "market_regime_control_safe_haven": safe_haven_symbol,
        "market_regime_control_risk_symbols": risk_symbols,
        "market_regime_control_risk_budget_scalar": context.get("risk_budget_scalar"),
        "market_regime_control_leverage_scalar": context.get("leverage_scalar"),
        "market_regime_control_risk_asset_scalar": context.get("risk_asset_scalar"),
        "market_regime_control_crisis_defense_required": context.get("crisis_defense_required"),
        "market_regime_control_reason_codes": context.get("reason_codes"),
        "market_regime_control_notification_context": {
            "risk_controls": {
                "market_regime_control": {
                    "enabled": enabled,
                    "found": bool(context.get("found")),
                    "schema_version": context.get("schema_version"),
                    "route": route,
                    "route_source": context.get("route_source"),
                    "active": bool(context.get("active")),
                    "applied": applied,
                    "route_allowed": route_allowed,
                    "risk_scalar": scalar,
                    "removed_weight": removed_weight,
                    "safe_haven": safe_haven_symbol,
                    "reason_codes": context.get("reason_codes"),
                    "localized_messages": context.get("localized_messages"),
                    "log_record": context.get("log_record"),
                    "notification": context.get("notification"),
                }
            }
        },
    }
    return target_weights, diagnostics


def get_current_holdings(ctx: StrategyContext):
    if "current_holdings" in ctx.state:
        return ctx.state["current_holdings"]
    if ctx.portfolio is not None and hasattr(ctx.portfolio, "positions"):
        return tuple(getattr(position, "symbol", position) for position in ctx.portfolio.positions)
    return ()


def require_market_data(ctx: StrategyContext, key: str):
    if key not in ctx.market_data:
        raise ValueError(f"StrategyContext.market_data missing required key: {key}")
    return ctx.market_data[key]


def require_portfolio(ctx: StrategyContext):
    if ctx.portfolio is None:
        raise ValueError("StrategyContext.portfolio is required for this entrypoint")
    return ctx.portfolio


def default_translator(key: str, **kwargs) -> str:
    if not kwargs:
        return key
    pairs = ", ".join(f"{name}={value}" for name, value in sorted(kwargs.items()))
    return f"{key}({pairs})"


def default_signal_text_fn(icon: str) -> str:
    return str(icon)


def weights_to_positions(weights, *, safe_haven: str | None = None) -> tuple[PositionTarget, ...]:
    if not weights:
        return ()
    positions: list[PositionTarget] = []
    for symbol, weight in sorted(weights.items()):
        role = None
        if safe_haven and symbol == safe_haven:
            role = "safe_haven"
        elif symbol in INCOME_SYMBOLS:
            role = "income"
        positions.append(PositionTarget(symbol=symbol, target_weight=float(weight), role=role))
    return tuple(positions)


def target_values_to_positions(target_values: Mapping[str, float]) -> tuple[PositionTarget, ...]:
    positions: list[PositionTarget] = []
    for symbol, value in sorted(target_values.items()):
        role = None
        if symbol in SAFE_HAVENS:
            role = "safe_haven"
        elif symbol in INCOME_SYMBOLS:
            role = "income"
        positions.append(PositionTarget(symbol=symbol, target_value=float(value), role=role))
    return tuple(positions)
