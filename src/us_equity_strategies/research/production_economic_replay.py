"""Bounded, long-only synthetic ledger using production decision replay.

Inputs are hypothetical. Results are research records and contain no orders.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_DOWN
from typing import Any, Mapping
from quant_platform_kit.common.cash_conservation import expected_cash_delta

from .production_decision_replay import (
    ProductionDecisionReplayRequest, _clock, canonical_sha256, replay_production_decision,
)


@dataclass(frozen=True)
class SyntheticLedgerRequest:
    decisions: tuple[ProductionDecisionReplayRequest, ...]
    sessions: tuple[Mapping[str, Any], ...]
    initial_cash: float
    initial_quantities: Mapping[str, float]
    initial_average_costs: Mapping[str, float]
    lot_sizes: Mapping[str, float]
    minimum_trade_value: float
    fee_bps: float
    slippage_bps: float
    execution_lag_sessions: int
    settlement_lag_sessions: int


def _amount(value, *, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError("invalid non-negative finite amount")
    return float(value)


def _floor_lots(quantity, lot):
    return float((Decimal(str(max(0.0, quantity))) / Decimal(str(lot))).to_integral_value(rounding=ROUND_DOWN) * Decimal(str(lot)))


def replay_synthetic_production_ledger(request: SyntheticLedgerRequest) -> dict[str, Any]:
    """Run next-open fills and close decisions with explicit synthetic events.

    A buy is considered only if its prior-close intent token is still valid at
    the next open. Missing prices, inconsistent times or unsupported events
    reject the entire run. No external data or real account is accessed.
    """
    if type(request) is not SyntheticLedgerRequest or len(request.sessions) != len(request.decisions) + 1:
        raise ValueError("one execution session beyond decisions is required")
    if not request.decisions or any(row.evidence_kind != "synthetic_fixture" for row in request.decisions):
        raise ValueError("synthetic decision inputs required")
    if len({row.profile for row in request.decisions}) != 1:
        raise ValueError("a ledger has one profile")
    identities = {(row.profile, row.source_revision, canonical_sha256(row.source_hashes), row.effective_config_sha256)
                  for row in request.decisions}
    if len(identities) != 1:
        raise ValueError("profile, configuration and source must remain fixed")
    if (type(request.execution_lag_sessions) is not int or request.execution_lag_sessions < 1
            or type(request.settlement_lag_sessions) is not int or request.settlement_lag_sessions < 0):
        raise ValueError("invalid execution or settlement lag")
    input_identity = canonical_sha256({"sessions": request.sessions,
        "decisions": [{"profile": row.profile, "source_revision": row.source_revision,
            "source_hashes": row.source_hashes, "config_sha256": row.config_sha256,
            "effective_config_sha256": row.effective_config_sha256,
            "input_sha256": row.input_sha256, "decision_at": row.decision_at,
            "evidence_kind": row.evidence_kind, "execution_evidence": row.execution_evidence,
            "publication_evidence": row.publication_evidence} for row in request.decisions],
        "initial_cash": request.initial_cash, "initial_quantities": request.initial_quantities,
        "initial_average_costs": request.initial_average_costs, "lot_sizes": request.lot_sizes,
        "minimum_trade_value": request.minimum_trade_value, "fee_bps": request.fee_bps,
        "slippage_bps": request.slippage_bps, "execution_lag_sessions": request.execution_lag_sessions,
        "settlement_lag_sessions": request.settlement_lag_sessions})
    cash = _amount(request.initial_cash)
    minimum = _amount(request.minimum_trade_value)
    fee_rate = _amount(request.fee_bps) / 10000.0
    slippage = _amount(request.slippage_bps) / 10000.0
    if fee_rate >= 1 or slippage >= 1:
        raise ValueError("fee and slippage rates must be less than one")
    quantities = {str(symbol): _amount(value) for symbol, value in request.initial_quantities.items()}
    costs = {str(symbol): _amount(value, positive=True) for symbol, value in request.initial_average_costs.items()}
    if any(quantity > 0 and symbol not in costs for symbol, quantity in quantities.items()):
        raise ValueError("initial average cost required for held positions")
    lots = {str(symbol): _amount(value, positive=True) for symbol, value in request.lot_sizes.items()}
    if not set(quantities) <= set(lots):
        raise ValueError("initial position lot size missing")
    receivables: dict[str, dict[str, Any]] = {}
    event_ids: set[str] = set()
    pending = []
    unsettled: list[tuple[int, float]] = []
    carried_transition = None
    state_enrolled = None
    latest_signal_session = None
    latest_signal_token = None
    intent_generation = 0
    rows = []
    prior_session = ""
    for index, raw in enumerate(request.sessions):
        session = str(raw["session"])
        if session <= prior_session:
            raise ValueError("sessions must strictly increase")
        prior_session = session
        open_at, close_at = _clock(raw["open_at"]), _clock(raw["close_at"])
        if open_at.date().isoformat() != session or close_at.date().isoformat() != session or open_at >= close_at:
            raise ValueError("invalid session clocks")
        opens, closes = raw["open_prices"], raw["close_prices"]
        if not isinstance(opens, Mapping) or not isinstance(closes, Mapping):
            raise ValueError("missing session prices")
        for symbol in lots:
            _amount(opens.get(symbol), positive=True)
            _amount(closes.get(symbol), positive=True)
        cash_before = cash
        unsettled = [(due, amount) for due, amount in unsettled if due > index]
        receivable_before = sum(item["amount"] for item in receivables.values())
        trade_cash = fees = event_cash = 0.0
        event_records = []
        for event in raw.get("events", []):
            kind, identifier = event.get("type"), event.get("id")
            if type(identifier) is not str or not identifier or identifier in event_ids:
                raise ValueError("missing or duplicate event identity")
            event_ids.add(identifier)
            symbol = event.get("symbol")
            if kind == "split":
                if symbol not in lots:
                    raise ValueError("split symbol missing")
                ratio = _amount(event.get("ratio"), positive=True)
                quantities[symbol] = quantities.get(symbol, 0.0) * ratio
                if symbol in costs:
                    costs[symbol] /= ratio
            elif kind == "dividend_ex":
                if symbol not in lots:
                    raise ValueError("dividend symbol missing")
                receivables[identifier] = {"symbol": symbol, "ex_session": session,
                    "amount": quantities.get(symbol, 0.0) * _amount(event.get("per_share"))}
            elif kind == "dividend_pay":
                ex_id = event.get("ex_id")
                if ex_id not in receivables:
                    raise ValueError("dividend entitlement missing or already paid")
                entitlement = receivables[ex_id]
                if symbol != entitlement["symbol"] or session <= entitlement["ex_session"]:
                    raise ValueError("dividend payment symbol or chronology mismatch")
                amount = receivables.pop(ex_id)["amount"]
                cash += amount
                event_cash += amount
            elif kind == "external_cashflow":
                amount = event.get("amount")
                if type(amount) not in (int, float) or not math.isfinite(amount) or cash - sum(v for _, v in unsettled) + amount < 0:
                    raise ValueError("invalid external cash flow")
                cash += amount
                event_cash += amount
            else:
                raise ValueError("unsupported ledger event")
            event_records.append(dict(event))
        fills = []
        for due_pending in (item for item in pending if item[0] == index):
            _, targets, token, signal_session, pending_state_sha, pending_generation = due_pending
            if signal_session >= session:
                raise ValueError("signal must precede execution")
            for symbol, value in sorted(targets.items()):
                if symbol not in lots:
                    raise ValueError("target instrument price or lot missing")
                price = float(opens[symbol]) * (1 - slippage)
                current = quantities.get(symbol, 0.0)
                target = _floor_lots(float(value) / price, lots[symbol])
                sell = _floor_lots(max(0.0, current - target), lots[symbol])
                notional = sell * price
                if sell and notional >= minimum:
                    fee = notional * fee_rate
                    quantities[symbol] = current - sell
                    if quantities[symbol] == 0:
                        costs.pop(symbol, None)
                    cash += notional - fee
                    if request.settlement_lag_sessions:
                        unsettled.append((index + request.settlement_lag_sessions, notional - fee))
                    trade_cash += notional
                    fees += fee
                    fills.append({"symbol": symbol, "side": "sell", "quantity": sell, "price": price, "fee": fee})
            intent_valid = (token is not None and token == raw.get("valid_buy_intent_id")
                            and pending_generation == intent_generation
                            and (signal_session == latest_signal_session
                                 or (request.decisions[0].profile != "tqqq_growth_income"
                                     and token == latest_signal_token)))
            if request.decisions[0].profile == "tqqq_growth_income":
                state_wire = carried_transition if isinstance(carried_transition, Mapping) else {}
                state_value = state_wire.get("state", {})
                intent = state_value.get("pending_intent") if isinstance(state_value, Mapping) else None
                intent_valid = bool(intent_valid and state_wire.get("transition_sha256") == pending_state_sha
                    and isinstance(intent, Mapping) and intent.get("id") == token
                    and intent.get("signal_session") == signal_session
                    and intent.get("alpha_epoch") == state_value.get("alpha_epoch")
                    and state_value.get("alpha_trend_active") is True
                    and state_value.get("volatility_latch_active") is False)
            if intent_valid:
                for symbol, value in sorted(targets.items()):
                    price = float(opens[symbol]) * (1 + slippage)
                    target = _floor_lots(float(value) / price, lots[symbol])
                    desired = _floor_lots(max(0.0, target - quantities.get(symbol, 0.0)), lots[symbol])
                    settled = max(0.0, cash - sum(v for _, v in unsettled))
                    affordable = _floor_lots(settled / (price * (1 + fee_rate)), lots[symbol])
                    buy = min(desired, affordable)
                    notional = buy * price
                    if buy and notional >= minimum:
                        fee = notional * fee_rate
                        current = quantities.get(symbol, 0.0)
                        costs[symbol] = ((current * costs.get(symbol, 0.0)) + notional + fee) / (current + buy)
                        quantities[symbol] = current + buy
                        cash -= notional + fee
                        trade_cash -= notional
                        fees += fee
                        fills.append({"symbol": symbol, "side": "buy", "quantity": buy, "price": price, "fee": fee})
        expected = expected_cash_delta(fills, [fees], [event_cash], None)
        if cash - sum(v for _, v in unsettled) < -1e-7 or abs((cash - cash_before) - expected) > 1e-6:
            raise ValueError("cash conservation failed")
        cash = max(0.0, cash)
        receivable_total = sum(item["amount"] for item in receivables.values())
        nav = cash + receivable_total + sum(q * float(closes[s]) for s, q in quantities.items())
        trace = None
        pending = [item for item in pending if item[0] > index]
        if index < len(request.decisions):
            template = request.decisions[index]
            if template.frozen_input.get("session") != session or _clock(template.decision_at) != close_at:
                raise ValueError("decision session or clock mismatch")
            if canonical_sha256(template.frozen_input) != template.input_sha256:
                raise ValueError("frozen decision input mismatch")
            data = copy.deepcopy(dict(template.frozen_input))
            for symbol in lots:
                if data.get("instrument_prices", {}).get(symbol) != closes[symbol]:
                    raise ValueError("decision marks do not match ledger close prices")
                if _clock(data.get("instrument_prices_available_at", {}).get(symbol)) > close_at:
                    raise ValueError("decision mark unavailable at close")
            original_metadata = dict(data["portfolio"].get("metadata") or {})
            original_metadata.update({"market_currency_cash": cash, "dividend_receivable": receivable_total})
            data["portfolio"] = {"as_of": close_at.isoformat(), "total_equity": nav, "buying_power": cash - sum(v for _, v in unsettled),
                "cash_balance": cash, "positions": [{"symbol": symbol, "quantity": quantity,
                    "market_value": quantity * float(closes[symbol]), "average_cost": costs[symbol]}
                    for symbol, quantity in sorted(quantities.items()) if quantity],
                "metadata": original_metadata}
            state = dict(data.get("state") or {})
            daily = state.get("tqqq_daily_state")
            if state_enrolled is None:
                state_enrolled = daily is not None
            if (daily is not None) != state_enrolled:
                raise ValueError("daily state enrollment cannot change during a ledger")
            if state_enrolled:
                if template.profile != "tqqq_growth_income" or not isinstance(daily, Mapping):
                    raise ValueError("invalid daily state template")
                daily = dict(daily)
                if not isinstance(daily.get("identity"), Mapping) or daily["identity"].get("config_sha256") != template.effective_config_sha256:
                    raise ValueError("daily state configuration identity mismatch")
                if index:
                    daily.update({"previous_transition": carried_transition, "bootstrap": False,
                                  "expected_previous_session": request.sessions[index - 1]["session"]})
                state.pop("tqqq_daily_state")
                data["state"] = state
                prior_wire = daily.get("previous_transition")
                daily["input_sha256"] = canonical_sha256({"observation": data,
                    "identity": daily["identity"], "fresh_intent": daily.get("fresh_intent"),
                    "previous_transition_sha256": prior_wire.get("transition_sha256")
                    if isinstance(prior_wire, Mapping) else None})
                daily["status"] = "ready"
                daily["execution_fence_confirmed"] = False
                state["tqqq_daily_state"] = daily
                data["state"] = state
            replay = replay_production_decision(replace(template, frozen_input=data, input_sha256=canonical_sha256(data)))
            trace = replay.trace
            if state_enrolled:
                if not isinstance(trace, Mapping) or "offline_risk_decision" not in trace:
                    raise ValueError("state proposal unavailable")
                # The builder transition is in gate diagnostics; this local readback
                # is simulation only and grants no runtime execution authority.
                diag = trace["offline_risk_decision"].get("diagnostics", {})
                carried_transition = diag.get("tqqq_daily_state_transition")
                if diag.get("tqqq_daily_state_status") != "proposed" or not isinstance(carried_transition, Mapping):
                    raise ValueError("state proposal unavailable")
                daily.update({"previous_transition": carried_transition, "bootstrap": False,
                              "execution_fence_confirmed": True})
                replay = replay_production_decision(replace(template, frozen_input=data, input_sha256=canonical_sha256(data)))
                trace = replay.trace
                diag = trace["offline_risk_decision"].get("diagnostics", {})
                if diag.get("tqqq_daily_state_status") != "confirmed" or diag.get("tqqq_daily_state_transition") != carried_transition:
                    raise ValueError("state confirmation mismatch")
            if not isinstance(trace, Mapping) or "offline_risk_decision" not in trace:
                raise ValueError("production decision unavailable")
            gate = trace["offline_risk_decision"]
            positions = gate.get("positions")
            if not isinstance(positions, (list, tuple)):
                raise ValueError("offline risk gate record invalid")
            targets = {}
            for position in positions:
                symbol, value = position.get("symbol"), position.get("target_value")
                if type(symbol) is not str or symbol in targets or symbol not in lots:
                    raise ValueError("risk target instrument missing")
                targets[symbol] = _amount(value)
            token = raw.get("buy_intent_id")
            if token is not None and (type(token) is not str or not token):
                raise ValueError("invalid buy intent")
            latest_signal_session = session
            if token != latest_signal_token:
                intent_generation += 1
            latest_signal_token = token
            pending.append((index + request.execution_lag_sessions, targets, token, session,
                            carried_transition.get("transition_sha256") if isinstance(carried_transition, Mapping) else None,
                            intent_generation))
        record = {"session": session, "cash": cash, "settled_buying_power": cash - sum(v for _, v in unsettled),
                  "unsettled_cash": sum(v for _, v in unsettled), "nav": nav, "quantities": dict(sorted(quantities.items())),
                  "average_costs": dict(sorted(costs.items())), "receivables": dict(sorted(receivables.items())), "receivable_before": receivable_before,
                  "fills": fills, "fees": fees, "trade_notional_cash": trade_cash, "event_cash": event_cash, "events": event_records,
                  "decision_trace": trace}
        record["row_sha256"] = canonical_sha256(record)
        rows.append(record)
    limitations = ["CALLER_SUPPLIED_SESSION_CALENDAR", "NO_BROKER_PARTIAL_FILLS", "NO_OPTIONS_OR_TAXES"]
    if request.decisions[0].profile == "tqqq_growth_income" and request.execution_lag_sessions > 1:
        limitations.append("DAILY_INTENT_EXPIRY_CANCELS_PRIOR_SESSION_BUYS")
    if request.decisions[0].profile == "tqqq_growth_income" and not state_enrolled:
        limitations.append("TQQQ_STATE_NOT_ENROLLED_BUYS_DISABLED")
    return {"schema_version": "qsl.synthetic_production_ledger.v1", "rows": rows,
            "input_sha256": input_identity, "ledger_sha256": canonical_sha256(rows), "executable": False,
            "execution_authorized": False, "promotion_eligible": False, "live_ready": False,
            "historical_parity_established": False, "evidence_kind": "synthetic_fixture",
            "limitations": limitations}
