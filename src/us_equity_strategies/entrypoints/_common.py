from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime

from quant_platform_kit.strategy_contracts import PositionTarget, StrategyContext
from us_equity_strategies.income_layer import (
    build_income_layer_plan,
    normalize_income_layer_allocations,
)
from us_equity_strategies.market_regime_control_contract import (
    resolve_market_regime_position_control_authorization,
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
OPTION_OVERLAY_CONFIG_KEYS = {
    "option_growth_overlay_enabled",
    "option_growth_overlay_recipe",
    "option_growth_overlay_start_usd",
    "option_growth_overlay_nav_budget_ratio",
    "option_income_overlay_enabled",
    "option_income_overlay_recipe",
    "option_income_overlay_start_usd",
    "option_income_overlay_nav_risk_ratio",
}
MARKET_REGIME_CONTROL_PROFILE = "market_regime_control"
MARKET_REGIME_POSITION_ROUTES = frozenset({"risk_reduced", "risk_off"})
OPTION_OVERLAY_RECIPE_DETAILS = {
    "tqqq_leaps_growth_v1": {
        "structure": "long_call_leaps",
        "underlier": "TQQQ",
        "premium_budget_ratio": 0.03,
        "target_delta": 0.75,
        "target_dte_months": 24,
        "roll_dte_months": 12,
        "contract_multiplier": 100,
        "min_dte_days": 540,
        "max_dte_days": 930,
        "max_bid_ask_spread_ratio": 0.12,
        "entry_gate": "core_risk_on_and_qqq_above_200dma_and_qqq_63d_momentum_positive",
        "principal_take_profit": "sell_enough_contracts_to_recover_cost_after_2x_when_contract_count_allows",
    },
    "qqq_leaps_growth_v1": {
        "structure": "long_call_leaps",
        "underlier": "QQQ",
        "premium_budget_ratio": 0.03,
        "target_delta": 0.75,
        "target_dte_months": 24,
        "roll_dte_months": 12,
        "contract_multiplier": 100,
        "min_dte_days": 540,
        "max_dte_days": 930,
        "max_bid_ask_spread_ratio": 0.12,
        "entry_gate": "qqq_above_200dma_and_qqq_63d_momentum_positive",
        "principal_take_profit": "sell_enough_contracts_to_recover_cost_after_2x_when_contract_count_allows",
    },
    "soxx_put_credit_spread_income_v1": {
        "structure": "put_credit_spread",
        "underlier": "SOXX",
        "target_dte_days": 45,
        "short_put_otm_pct": 0.08,
        "long_put_otm_pct": 0.18,
        "max_loss_budget_ratio": 0.01,
        "contract_multiplier": 100,
        "min_dte_days": 25,
        "max_dte_days": 65,
        "max_iv_rank": 0.80,
        "entry_gate": "soxx_trend_positive_and_iv_rank_not_extreme",
    },
}


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


def _effective_option_recipe_detail(
    family: str,
    recipe_detail: Mapping[str, object],
    option_overlay_config: Mapping[str, object],
) -> dict[str, object]:
    effective = dict(recipe_detail)
    if family == "growth":
        default = _as_float(recipe_detail.get("premium_budget_ratio"), default=0.03)
        effective["premium_budget_ratio"] = _clamped_ratio(
            option_overlay_config.get("option_growth_overlay_nav_budget_ratio"),
            default=default,
            upper=0.10,
        )
    elif family == "income":
        default = _as_float(recipe_detail.get("max_loss_budget_ratio"), default=0.01)
        effective["max_loss_budget_ratio"] = _clamped_ratio(
            option_overlay_config.get("option_income_overlay_nav_risk_ratio"),
            default=default,
            upper=0.02,
        )
    return effective


def _parse_date(value: object) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def _as_of_date(ctx: StrategyContext) -> date:
    parsed = _parse_date(ctx.as_of)
    return parsed or datetime.utcnow().date()


def _normalize_right(value: object) -> str:
    text = str(value or "").strip().upper()
    if text in {"CALL", "C"}:
        return "C"
    if text in {"PUT", "P"}:
        return "P"
    return text


def _option_chain_payload(ctx: StrategyContext, underlier: str) -> object:
    underlier = str(underlier or "").strip().upper()
    for source in (ctx.market_data, ctx.runtime_config):
        payload = source.get("option_chains") if isinstance(source, Mapping) else None
        if isinstance(payload, Mapping):
            direct = payload.get(underlier) or payload.get(underlier.lower())
            if direct is not None:
                return direct
        payload = source.get("option_chain") if isinstance(source, Mapping) else None
        if isinstance(payload, Mapping):
            payload_underlier = str(payload.get("underlier") or payload.get("symbol") or "").strip().upper()
            if payload_underlier == underlier:
                return payload
        elif payload:
            return payload
    return None


def _chain_rows(payload: object) -> tuple[Mapping[str, object], ...]:
    if payload is None:
        return ()
    if isinstance(payload, Mapping):
        for key in ("contracts", "options", "rows", "chain"):
            rows = payload.get(key)
            if rows is not None:
                return tuple(row for row in rows if isinstance(row, Mapping))
        return ()
    try:
        return tuple(row for row in payload if isinstance(row, Mapping))  # type: ignore[union-attr]
    except TypeError:
        return ()


def _chain_spot(payload: object, underlier: str, ctx: StrategyContext) -> float:
    if isinstance(payload, Mapping):
        for key in ("spot", "underlier_price", "underlying_price", "last_price"):
            price = _as_float(payload.get(key), default=0.0)
            if price > 0.0:
                return price
    quote = ctx.market_data.get(str(underlier).strip().upper())
    if isinstance(quote, Mapping):
        for key in ("last_price", "last", "close", "price"):
            price = _as_float(quote.get(key), default=0.0)
            if price > 0.0:
                return price
    return 0.0


def _row_date(row: Mapping[str, object]) -> date | None:
    for key in ("expiration", "expiry", "lastTradeDateOrContractMonth", "last_trade_date"):
        parsed = _parse_date(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _row_delta(row: Mapping[str, object]) -> float | None:
    for key in ("delta", "model_delta"):
        if key in row:
            return _as_float(row.get(key), default=0.0)
    greeks = row.get("greeks")
    if isinstance(greeks, Mapping) and "delta" in greeks:
        return _as_float(greeks.get("delta"), default=0.0)
    return None


def _row_bid_ask_mid(row: Mapping[str, object]) -> tuple[float, float, float]:
    bid = _as_float(row.get("bid") or row.get("bid_price"), default=0.0)
    ask = _as_float(row.get("ask") or row.get("ask_price"), default=0.0)
    raw_mid = _as_float(row.get("mid") or row.get("mark") or row.get("last"), default=0.0)
    if bid > 0.0 and ask > 0.0:
        return bid, ask, (bid + ask) / 2.0
    if raw_mid > 0.0 and ask <= 0.0:
        ask = raw_mid
    if raw_mid > 0.0:
        return bid, ask, raw_mid
    return bid, ask, ask if ask > 0.0 else bid


def _normalize_option_positions(ctx: StrategyContext, underlier: str, right: str) -> tuple[Mapping[str, object], ...]:
    portfolio = ctx.portfolio
    metadata = getattr(portfolio, "metadata", {}) if portfolio is not None else {}
    rows = metadata.get("option_positions") if isinstance(metadata, Mapping) else ()
    normalized = []
    for row in rows or ():
        if not isinstance(row, Mapping):
            continue
        row_underlier = str(row.get("underlier") or row.get("symbol") or "").strip().upper()
        row_right = _normalize_right(row.get("right") or row.get("put_call"))
        quantity = _as_float(row.get("quantity"), default=0.0)
        if row_underlier == underlier and row_right == right and quantity > 0.0:
            normalized.append(row)
    return tuple(normalized)


def _long_call_entry_gate_passed(recipe: str, base_diagnostics: Mapping[str, object]) -> tuple[bool, str | None]:
    signal_context = base_diagnostics.get("notification_context")
    if isinstance(signal_context, Mapping):
        raw_signal = signal_context.get("signal")
        if isinstance(raw_signal, Mapping):
            state = str(raw_signal.get("state") or "").strip().lower()
            if state:
                return state in {"entry", "hold"}, None if state in {"entry", "hold"} else "entry_gate_not_met"
    regime = str(base_diagnostics.get("regime") or "").strip().lower()
    if recipe == "qqq_leaps_growth_v1" and regime:
        return regime == "risk_on", None if regime == "risk_on" else "entry_gate_not_met"
    return True, None


def _put_credit_spread_gate_passed(
    payload: object,
    recipe_detail: Mapping[str, object],
    base_diagnostics: Mapping[str, object],
) -> tuple[bool, str | None]:
    blend_tier = str(base_diagnostics.get("blend_tier") or "").strip().lower()
    if blend_tier and blend_tier not in {"full", "mid"}:
        return False, "entry_gate_not_met"
    iv_rank = None
    if isinstance(payload, Mapping):
        for key in ("iv_rank", "implied_volatility_rank"):
            if key in payload:
                iv_rank = _as_float(payload.get(key), default=-1.0)
                break
    if iv_rank is not None and iv_rank > _as_float(recipe_detail.get("max_iv_rank"), default=0.80):
        return False, "iv_rank_too_high"
    return True, None


def _append_skip(intents_payload: dict[str, object], *, recipe: str, reason: str, underlier: str) -> None:
    skipped = list(intents_payload.get("skipped") or ())
    skipped.append({"recipe": recipe, "underlier": underlier, "reason": reason})
    intents_payload["skipped"] = skipped


def _build_long_call_intents(
    *,
    recipe: str,
    recipe_detail: Mapping[str, object],
    ctx: StrategyContext,
    total_equity: float,
    base_diagnostics: Mapping[str, object],
    intents_payload: dict[str, object],
) -> None:
    underlier = str(recipe_detail.get("underlier") or "").strip().upper()
    gate_passed, gate_reason = _long_call_entry_gate_passed(recipe, base_diagnostics)
    if not gate_passed:
        _append_skip(intents_payload, recipe=recipe, underlier=underlier, reason=gate_reason or "entry_gate_not_met")
        return

    payload = _option_chain_payload(ctx, underlier)
    rows = _chain_rows(payload)
    if not rows:
        _append_skip(intents_payload, recipe=recipe, underlier=underlier, reason="missing_option_chain")
        return

    as_of = _as_of_date(ctx)
    target_dte = int(_as_float(recipe_detail.get("target_dte_months"), default=24.0) * 30.4375)
    target_delta = _as_float(recipe_detail.get("target_delta"), default=0.75)
    min_dte = int(_as_float(recipe_detail.get("min_dte_days"), default=540.0))
    max_dte = int(_as_float(recipe_detail.get("max_dte_days"), default=930.0))
    max_spread_ratio = _as_float(recipe_detail.get("max_bid_ask_spread_ratio"), default=0.12)
    multiplier = int(_as_float(recipe_detail.get("contract_multiplier"), default=100.0))
    candidates = []
    for row in rows:
        if _normalize_right(row.get("right") or row.get("type")) != "C":
            continue
        expiration = _row_date(row)
        if expiration is None:
            continue
        dte = (expiration - as_of).days
        if dte < min_dte or dte > max_dte:
            continue
        delta = _row_delta(row)
        if delta is None or delta <= 0.0:
            continue
        bid, ask, mid = _row_bid_ask_mid(row)
        if ask <= 0.0 or mid <= 0.0:
            continue
        spread_ratio = ((ask - bid) / mid) if bid > 0.0 else 0.0
        if spread_ratio > max_spread_ratio:
            continue
        strike = _as_float(row.get("strike"), default=0.0)
        if strike <= 0.0:
            continue
        candidates.append((abs(dte - target_dte), abs(delta - target_delta), row, expiration, dte, delta, bid, ask, mid, strike))
    if not candidates:
        _append_skip(intents_payload, recipe=recipe, underlier=underlier, reason="no_matching_option_contract")
        return

    candidates.sort(key=lambda item: (item[0], item[1], item[9]))
    _dte_gap, _delta_gap, row, expiration, dte, delta, bid, ask, mid, strike = candidates[0]
    limit_price = round(min(ask, mid * 1.03), 2)
    premium_budget = total_equity * _as_float(recipe_detail.get("premium_budget_ratio"), default=0.03)
    quantity = int(premium_budget // (limit_price * multiplier)) if limit_price > 0.0 else 0
    existing_positions = _normalize_option_positions(ctx, underlier, "C")

    intents = list(intents_payload.get("intents") or ())
    if existing_positions:
        roll_dte = int(_as_float(recipe_detail.get("roll_dte_months"), default=12.0) * 30.4375)
        for position in existing_positions:
            position_expiration = _parse_date(position.get("expiration") or position.get("lastTradeDateOrContractMonth"))
            position_quantity = int(_as_float(position.get("quantity"), default=0.0))
            position_dte = (position_expiration - as_of).days if position_expiration is not None else 9999
            market_value = _as_float(position.get("market_value"), default=0.0)
            cost_basis = _as_float(position.get("cost_basis"), default=0.0)
            if position_quantity >= 2 and cost_basis > 0.0 and market_value >= cost_basis * 2.0 and bid > 0.0:
                recover_quantity = max(1, int(cost_basis // (bid * multiplier)) + 1)
                recover_quantity = min(position_quantity - 1, recover_quantity)
                intents.append(
                    {
                        "intent_type": "single_leg_option",
                        "asset_class": "option",
                        "action": "sell_to_close",
                        "underlier": underlier,
                        "right": "C",
                        "expiration": str(position_expiration or position.get("expiration")),
                        "strike": _as_float(position.get("strike"), default=0.0),
                        "quantity": recover_quantity,
                        "order_type": "limit",
                        "limit_price": round(max(bid, 0.01), 2),
                        "time_in_force": "DAY",
                        "contract_multiplier": multiplier,
                        "reason": "recover_leaps_principal_after_2x",
                    }
                )
                intents_payload["intents"] = intents
                return
            if position_dte <= roll_dte:
                _append_skip(intents_payload, recipe=recipe, underlier=underlier, reason="roll_requires_existing_contract_quote")
                return
        _append_skip(intents_payload, recipe=recipe, underlier=underlier, reason="existing_position_held")
        intents_payload["intents"] = intents
        return

    if quantity < 1:
        _append_skip(intents_payload, recipe=recipe, underlier=underlier, reason="contract_not_affordable")
        return

    intents.append(
        {
            "intent_type": "single_leg_option",
            "asset_class": "option",
            "action": "buy_to_open",
            "underlier": underlier,
            "right": "C",
            "expiration": expiration.isoformat(),
            "strike": strike,
            "quantity": quantity,
            "order_type": "limit",
            "limit_price": limit_price,
            "time_in_force": "DAY",
            "contract_multiplier": multiplier,
            "max_notional_usd": round(quantity * limit_price * multiplier, 2),
            "delta": round(delta, 4),
            "dte": dte,
            "reason": "open_leaps_growth",
        }
    )
    intents_payload["intents"] = intents


def _build_put_credit_spread_intents(
    *,
    recipe: str,
    recipe_detail: Mapping[str, object],
    ctx: StrategyContext,
    total_equity: float,
    base_diagnostics: Mapping[str, object],
    intents_payload: dict[str, object],
) -> None:
    underlier = str(recipe_detail.get("underlier") or "").strip().upper()
    payload = _option_chain_payload(ctx, underlier)
    gate_passed, gate_reason = _put_credit_spread_gate_passed(payload, recipe_detail, base_diagnostics)
    if not gate_passed:
        _append_skip(intents_payload, recipe=recipe, underlier=underlier, reason=gate_reason or "entry_gate_not_met")
        return
    rows = _chain_rows(payload)
    spot = _chain_spot(payload, underlier, ctx)
    if not rows or spot <= 0.0:
        _append_skip(intents_payload, recipe=recipe, underlier=underlier, reason="missing_option_chain")
        return

    as_of = _as_of_date(ctx)
    target_dte = int(_as_float(recipe_detail.get("target_dte_days"), default=45.0))
    min_dte = int(_as_float(recipe_detail.get("min_dte_days"), default=25.0))
    max_dte = int(_as_float(recipe_detail.get("max_dte_days"), default=65.0))
    short_target = spot * (1.0 - _as_float(recipe_detail.get("short_put_otm_pct"), default=0.08))
    long_target = spot * (1.0 - _as_float(recipe_detail.get("long_put_otm_pct"), default=0.18))
    multiplier = int(_as_float(recipe_detail.get("contract_multiplier"), default=100.0))

    puts_by_expiration: dict[date, list[Mapping[str, object]]] = {}
    for row in rows:
        if _normalize_right(row.get("right") or row.get("type")) != "P":
            continue
        expiration = _row_date(row)
        if expiration is None:
            continue
        dte = (expiration - as_of).days
        if min_dte <= dte <= max_dte:
            puts_by_expiration.setdefault(expiration, []).append(row)
    if not puts_by_expiration:
        _append_skip(intents_payload, recipe=recipe, underlier=underlier, reason="no_matching_option_contract")
        return

    expirations = sorted(puts_by_expiration, key=lambda expiry: abs((expiry - as_of).days - target_dte))
    best = None
    for expiration in expirations:
        rows_for_expiry = puts_by_expiration[expiration]
        short_row = min(rows_for_expiry, key=lambda row: abs(_as_float(row.get("strike"), default=0.0) - short_target))
        long_candidates = [
            row for row in rows_for_expiry
            if _as_float(row.get("strike"), default=0.0) < _as_float(short_row.get("strike"), default=0.0)
        ]
        if not long_candidates:
            continue
        long_row = min(long_candidates, key=lambda row: abs(_as_float(row.get("strike"), default=0.0) - long_target))
        short_bid, _short_ask, _short_mid = _row_bid_ask_mid(short_row)
        _long_bid, long_ask, _long_mid = _row_bid_ask_mid(long_row)
        short_strike = _as_float(short_row.get("strike"), default=0.0)
        long_strike = _as_float(long_row.get("strike"), default=0.0)
        net_credit = short_bid - long_ask
        width = short_strike - long_strike
        if short_bid > 0.0 and long_ask > 0.0 and net_credit > 0.05 and width > net_credit:
            best = (expiration, short_row, long_row, short_strike, long_strike, round(net_credit, 2), width)
            break
    if best is None:
        _append_skip(intents_payload, recipe=recipe, underlier=underlier, reason="no_viable_credit_spread")
        return

    expiration, _short_row, _long_row, short_strike, long_strike, net_credit, width = best
    max_loss = (width - net_credit) * multiplier
    risk_budget = total_equity * _as_float(recipe_detail.get("max_loss_budget_ratio"), default=0.01)
    quantity = int(risk_budget // max_loss) if max_loss > 0.0 else 0
    if quantity < 1:
        _append_skip(intents_payload, recipe=recipe, underlier=underlier, reason="spread_not_affordable")
        return

    intents = list(intents_payload.get("intents") or ())
    intents.append(
        {
            "intent_type": "multi_leg_option",
            "asset_class": "option",
            "action": "sell_to_open_put_credit_spread",
            "underlier": underlier,
            "expiration": expiration.isoformat(),
            "quantity": quantity,
            "order_type": "limit",
            "limit_price": net_credit,
            "time_in_force": "DAY",
            "contract_multiplier": multiplier,
            "max_loss_usd": round(quantity * max_loss, 2),
            "net_credit_usd": round(quantity * net_credit * multiplier, 2),
            "reason": "open_defined_risk_income_spread",
            "legs": (
                {
                    "action": "sell_to_open",
                    "right": "P",
                    "strike": short_strike,
                    "expiration": expiration.isoformat(),
                    "ratio": 1,
                },
                {
                    "action": "buy_to_open",
                    "right": "P",
                    "strike": long_strike,
                    "expiration": expiration.isoformat(),
                    "ratio": 1,
                },
            ),
        }
    )
    intents_payload["intents"] = intents


def _attach_option_order_intents(
    diagnostics: dict[str, object],
    *,
    recipe: str,
    recipe_detail: Mapping[str, object],
    ctx: StrategyContext,
    total_equity: float,
    base_diagnostics: Mapping[str, object],
) -> None:
    intents_payload = dict(
        diagnostics.get("option_order_intents")
        or {"schema_version": "option_order_intents.v1", "intents": (), "skipped": ()}
    )
    structure = str(recipe_detail.get("structure") or "").strip()
    if structure == "long_call_leaps":
        _build_long_call_intents(
            recipe=recipe,
            recipe_detail=recipe_detail,
            ctx=ctx,
            total_equity=total_equity,
            base_diagnostics=base_diagnostics,
            intents_payload=intents_payload,
        )
    elif structure == "put_credit_spread":
        _build_put_credit_spread_intents(
            recipe=recipe,
            recipe_detail=recipe_detail,
            ctx=ctx,
            total_equity=total_equity,
            base_diagnostics=base_diagnostics,
            intents_payload=intents_payload,
        )
    else:
        _append_skip(
            intents_payload,
            recipe=recipe,
            underlier=str(recipe_detail.get("underlier") or "").strip().upper(),
            reason="unsupported_option_overlay_structure",
        )
    intents_payload["intents"] = tuple(intents_payload.get("intents") or ())
    intents_payload["skipped"] = tuple(intents_payload.get("skipped") or ())
    diagnostics["option_order_intents"] = intents_payload
    diagnostics["option_order_intent_count"] = len(intents_payload["intents"])


def build_option_overlay_diagnostics(
    option_overlay_config: Mapping[str, object],
    ctx: StrategyContext,
    *,
    base_diagnostics: Mapping[str, object] | None = None,
) -> dict[str, object]:
    diagnostics: dict[str, object] = {}
    if not option_overlay_config:
        return diagnostics

    portfolio = ctx.portfolio
    total_equity = (
        float(getattr(portfolio, "total_equity", 0.0) or 0.0)
        if portfolio is not None
        else 0.0
    )
    for family in ("growth", "income"):
        prefix = f"option_{family}_overlay"
        enabled = _as_bool(option_overlay_config.get(f"{prefix}_enabled"), default=False)
        recipe = str(option_overlay_config.get(f"{prefix}_recipe") or "").strip()
        start_usd = max(0.0, float(option_overlay_config.get(f"{prefix}_start_usd") or 0.0))
        if not enabled:
            active = False
            skip_reason = "disabled"
        elif not recipe:
            active = False
            skip_reason = "missing_recipe"
        elif portfolio is None:
            active = False
            skip_reason = "missing_portfolio"
        elif total_equity < start_usd:
            active = False
            skip_reason = "below_start_usd"
        else:
            active = True
            skip_reason = ""

        diagnostics[f"{prefix}_enabled"] = enabled
        diagnostics[f"{prefix}_recipe"] = recipe
        diagnostics[f"{prefix}_start_usd"] = start_usd
        diagnostics[f"{prefix}_active"] = active
        if skip_reason:
            diagnostics[f"{prefix}_skip_reason"] = skip_reason
        if recipe in OPTION_OVERLAY_RECIPE_DETAILS:
            recipe_detail = _effective_option_recipe_detail(
                family,
                OPTION_OVERLAY_RECIPE_DETAILS[recipe],
                option_overlay_config,
            )
            diagnostics[f"{prefix}_recipe_detail"] = recipe_detail
        else:
            recipe_detail = {}
        if active and recipe_detail:
            _attach_option_order_intents(
                diagnostics,
                recipe=recipe,
                recipe_detail=recipe_detail,
                ctx=ctx,
                total_equity=total_equity,
                base_diagnostics=base_diagnostics or {},
            )
    return diagnostics


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
