from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import date, datetime

from quant_platform_kit.strategy_contracts import StrategyContext


OPTION_OVERLAY_CONFIG_KEYS = {
    "option_overlay_enabled",
    "option_growth_overlay_enabled",
    "option_growth_overlay_recipe",
    "option_growth_overlay_start_usd",
    "option_growth_overlay_nav_budget_ratio",
    "option_income_overlay_enabled",
    "option_income_overlay_recipe",
    "option_income_overlay_start_usd",
    "option_income_overlay_nav_risk_ratio",
}

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
    "spy_leaps_growth_v1": {
        "structure": "long_call_leaps",
        "underlier": "SPY",
        "premium_budget_ratio": 0.03,
        "target_delta": 0.75,
        "target_dte_months": 24,
        "roll_dte_months": 12,
        "contract_multiplier": 100,
        "min_dte_days": 540,
        "max_dte_days": 930,
        "max_bid_ask_spread_ratio": 0.10,
        "entry_gate": "spy_above_200dma_and_spy_63d_momentum_positive",
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

OPTION_OVERLAY_RESEARCH_CANDIDATES = {
    "tqqq_leaps_growth_v1": {
        "status": "research",
        "promotion_evidence": False,
        "reason": "requires historical option-chain bid/ask validation before live defaults",
    },
    "qqq_leaps_growth_v1": {
        "status": "research",
        "promotion_evidence": False,
        "reason": "proxy backtest only; requires historical option-chain bid/ask validation",
    },
    "spy_leaps_growth_v1": {
        "status": "research",
        "promotion_evidence": False,
        "reason": "proxy backtest only; requires historical option-chain bid/ask validation",
    },
    "soxx_put_credit_spread_income_v1": {
        "status": "research",
        "promotion_evidence": False,
        "reason": "defined-risk income candidate; requires historical option-chain spread validation",
    },
}


OPTION_OVERLAY_DEFAULT_CONFIGS = {
    "global_etf_rotation": {
        "option_overlay_enabled": True,
        "option_growth_overlay_enabled": True,
        "option_growth_overlay_recipe": "spy_leaps_growth_v1",
        "option_growth_overlay_start_usd": 500000.0,
        "option_growth_overlay_nav_budget_ratio": 0.015,
        "option_income_overlay_enabled": False,
    },
    "tqqq_growth_income": {
        "option_overlay_enabled": True,
        "option_growth_overlay_enabled": True,
        "option_growth_overlay_recipe": "tqqq_leaps_growth_v1",
        "option_growth_overlay_start_usd": 250000.0,
        "option_growth_overlay_nav_budget_ratio": 0.03,
        "option_income_overlay_enabled": False,
    },
    "soxl_soxx_trend_income": {
        "option_overlay_enabled": True,
        "option_growth_overlay_enabled": False,
        "option_income_overlay_enabled": True,
        "option_income_overlay_recipe": "soxx_put_credit_spread_income_v1",
        "option_income_overlay_start_usd": 150000.0,
        "option_income_overlay_nav_risk_ratio": 0.01,
    },
    "tecl_xlk_trend_income": {
        "option_overlay_enabled": False,
        "option_growth_overlay_enabled": False,
        "option_income_overlay_enabled": False,
    },
    "russell_top50_leader_rotation": {
        "option_overlay_enabled": True,
        "option_growth_overlay_enabled": True,
        "option_growth_overlay_recipe": "spy_leaps_growth_v1",
        "option_growth_overlay_start_usd": 300000.0,
        "option_growth_overlay_nav_budget_ratio": 0.015,
        "option_income_overlay_enabled": False,
    },
}


def option_overlay_default_config(profile: str) -> dict[str, object]:
    normalized = str(profile or "").strip().lower()
    return deepcopy(OPTION_OVERLAY_DEFAULT_CONFIGS.get(normalized, {}))


def _recipe_live_allowed(recipe: str) -> tuple[bool, str, str]:
    candidate = OPTION_OVERLAY_RESEARCH_CANDIDATES.get(recipe)
    if not candidate:
        return False, "missing_promotion_record", ""
    status = str(candidate.get("status") or "").strip().lower()
    if status != "live" or candidate.get("promotion_evidence") is not True:
        return False, "research_only_recipe", status
    return True, "", status


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
            payload_underlier = str(
                payload.get("underlier") or payload.get("symbol") or ""
            ).strip().upper()
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


def _indicator_payload(ctx: StrategyContext, symbol: str) -> Mapping[str, object]:
    symbol = str(symbol or "").strip().upper()
    sources = (ctx.market_data, ctx.runtime_config)
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        for key in ("underlier_indicators", "market_indicators", "derived_indicators", "indicators"):
            payload = source.get(key)
            if isinstance(payload, Mapping):
                direct = payload.get(symbol) or payload.get(symbol.lower())
                if isinstance(direct, Mapping):
                    return direct
        direct = source.get(symbol) or source.get(symbol.lower())
        if isinstance(direct, Mapping):
            return direct
    return {}


def _optional_bool_indicator(payload: Mapping[str, object], *keys: str) -> bool | None:
    for key in keys:
        if key not in payload:
            continue
        value = payload.get(key)
        if value is None or value == "":
            continue
        return _as_bool(value, default=False)
    return None


def _optional_float_indicator(payload: Mapping[str, object], *keys: str) -> float | None:
    for key in keys:
        if key not in payload:
            continue
        value = payload.get(key)
        if value is None or value == "":
            continue
        return _as_float(value, default=0.0)
    return None


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


def _normalize_option_positions(
    ctx: StrategyContext,
    underlier: str,
    right: str,
) -> tuple[Mapping[str, object], ...]:
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


def _long_call_entry_gate_passed(
    recipe: str,
    underlier: str,
    ctx: StrategyContext,
    base_diagnostics: Mapping[str, object],
) -> tuple[bool, str | None]:
    signal_context = base_diagnostics.get("notification_context")
    if isinstance(signal_context, Mapping):
        raw_signal = signal_context.get("signal")
        if isinstance(raw_signal, Mapping):
            state = str(raw_signal.get("state") or "").strip().lower()
            if state:
                return state in {"entry", "hold"}, None if state in {"entry", "hold"} else "entry_gate_not_met"
    regime = str(base_diagnostics.get("regime") or "").strip().lower()
    if recipe in {"qqq_leaps_growth_v1", "spy_leaps_growth_v1"} and regime:
        if regime != "risk_on":
            return False, "entry_gate_not_met"
    indicators = _indicator_payload(ctx, underlier)
    if indicators:
        above_long_trend = _optional_bool_indicator(
            indicators,
            "above_200dma",
            "above_ma200",
            "sma200_pass",
            "ma200_pass",
            "trend_200d_pass",
        )
        if above_long_trend is False:
            return False, "entry_gate_not_met"
        momentum = _optional_float_indicator(
            indicators,
            "momentum_63d",
            "momentum_3m",
            "return_63d",
            "roc_63d",
        )
        if momentum is not None and momentum <= 0.0:
            return False, "entry_gate_not_met"
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


def _append_skip(
    intents_payload: dict[str, object],
    *,
    recipe: str,
    reason: str,
    underlier: str,
) -> None:
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
    gate_passed, gate_reason = _long_call_entry_gate_passed(recipe, underlier, ctx, base_diagnostics)
    if not gate_passed:
        _append_skip(
            intents_payload,
            recipe=recipe,
            underlier=underlier,
            reason=gate_reason or "entry_gate_not_met",
        )
        return

    payload = _option_chain_payload(ctx, underlier)
    rows = _chain_rows(payload)
    if not rows:
        _append_skip(
            intents_payload,
            recipe=recipe,
            underlier=underlier,
            reason="missing_option_chain",
        )
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
        candidates.append(
            (abs(dte - target_dte), abs(delta - target_delta), row, expiration, dte, delta, bid, ask, mid, strike)
        )
    if not candidates:
        _append_skip(
            intents_payload,
            recipe=recipe,
            underlier=underlier,
            reason="no_matching_option_contract",
        )
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
            position_expiration = _parse_date(
                position.get("expiration") or position.get("lastTradeDateOrContractMonth")
            )
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
                _append_skip(
                    intents_payload,
                    recipe=recipe,
                    underlier=underlier,
                    reason="roll_requires_existing_contract_quote",
                )
                return
        _append_skip(
            intents_payload,
            recipe=recipe,
            underlier=underlier,
            reason="existing_position_held",
        )
        intents_payload["intents"] = intents
        return

    if quantity < 1:
        _append_skip(
            intents_payload,
            recipe=recipe,
            underlier=underlier,
            reason="contract_not_affordable",
        )
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
        _append_skip(
            intents_payload,
            recipe=recipe,
            underlier=underlier,
            reason=gate_reason or "entry_gate_not_met",
        )
        return
    rows = _chain_rows(payload)
    spot = _chain_spot(payload, underlier, ctx)
    if not rows or spot <= 0.0:
        _append_skip(
            intents_payload,
            recipe=recipe,
            underlier=underlier,
            reason="missing_option_chain",
        )
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
        _append_skip(
            intents_payload,
            recipe=recipe,
            underlier=underlier,
            reason="no_matching_option_contract",
        )
        return

    expirations = sorted(puts_by_expiration, key=lambda expiry: abs((expiry - as_of).days - target_dte))
    best = None
    for expiration in expirations:
        rows_for_expiry = puts_by_expiration[expiration]
        short_row = min(rows_for_expiry, key=lambda row: abs(_as_float(row.get("strike"), default=0.0) - short_target))
        long_candidates = [
            row
            for row in rows_for_expiry
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
        _append_skip(
            intents_payload,
            recipe=recipe,
            underlier=underlier,
            reason="no_viable_credit_spread",
        )
        return

    expiration, _short_row, _long_row, short_strike, long_strike, net_credit, width = best
    max_loss = (width - net_credit) * multiplier
    risk_budget = total_equity * _as_float(recipe_detail.get("max_loss_budget_ratio"), default=0.01)
    quantity = int(risk_budget // max_loss) if max_loss > 0.0 else 0
    if quantity < 1:
        _append_skip(
            intents_payload,
            recipe=recipe,
            underlier=underlier,
            reason="spread_not_affordable",
        )
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

    option_overlay_enabled = _as_bool(option_overlay_config.get("option_overlay_enabled"), default=True)
    diagnostics["option_overlay_enabled"] = option_overlay_enabled
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
        live_allowed = False
        promotion_status = ""
        if not option_overlay_enabled:
            active = False
            skip_reason = "option_overlay_disabled"
        elif not enabled:
            active = False
            skip_reason = "disabled"
        elif not recipe:
            active = False
            skip_reason = "missing_recipe"
        elif not (live_gate := _recipe_live_allowed(recipe))[0]:
            active = False
            skip_reason = live_gate[1]
            promotion_status = live_gate[2]
        elif portfolio is None:
            active = False
            skip_reason = "missing_portfolio"
            live_allowed = True
            promotion_status = live_gate[2]
        elif total_equity < start_usd:
            active = False
            skip_reason = "below_start_usd"
            live_allowed = True
            promotion_status = live_gate[2]
        else:
            active = True
            skip_reason = ""
            live_allowed = True
            promotion_status = live_gate[2]

        diagnostics[f"{prefix}_enabled"] = enabled
        diagnostics[f"{prefix}_recipe"] = recipe
        diagnostics[f"{prefix}_start_usd"] = start_usd
        diagnostics[f"{prefix}_live_allowed"] = live_allowed
        if promotion_status:
            diagnostics[f"{prefix}_promotion_status"] = promotion_status
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


__all__ = [
    "OPTION_OVERLAY_CONFIG_KEYS",
    "OPTION_OVERLAY_DEFAULT_CONFIGS",
    "OPTION_OVERLAY_RESEARCH_CANDIDATES",
    "OPTION_OVERLAY_RECIPE_DETAILS",
    "build_option_overlay_diagnostics",
    "option_overlay_default_config",
]
