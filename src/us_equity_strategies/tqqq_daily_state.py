"""Daily TQQQ alpha, risk-latch and intent transition contract."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
import re

from quant_platform_kit.common.strategy_risk_state import (
    StrategyRiskStateIdentity,
    StrategyRiskStateTransition,
    build_strategy_risk_state_transition,
)

_SCHEMA = "tqqq_daily_state.v1"
from us_equity_strategies._decision_candidate_marker import RESEARCH_SIMULATED_FENCE as _RESEARCH_SIMULATED_FENCE


def prepare_tqqq_daily_state(request, session):
    """Validate a platform-supplied daily head; unavailable state is explicit."""
    if not isinstance(request, Mapping) or request.get("status") != "ready":
        return {"status": "unavailable"}
    try:
        if isinstance(session, datetime):
            session = session.date()
        session = date.fromisoformat(str(session)).isoformat()
        identity = StrategyRiskStateIdentity.from_dict(request["identity"])
        if identity.strategy_profile != "tqqq_growth_income":
            raise ValueError("wrong strategy identity")
        digest = request["input_sha256"]
        if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("invalid input digest")
        bootstrap = request.get("bootstrap")
        if type(bootstrap) is not bool:
            raise ValueError("bootstrap must be explicit")
        wire = request.get("previous_transition")
        if wire is None:
            if not bootstrap or request.get("expected_previous_session") is not None:
                raise ValueError("genesis requires explicit bootstrap")
            return {"status": "bootstrap", "session": session, "identity": identity,
                    "input_sha256": digest, "previous": None, "prior": None}
        if bootstrap:
            raise ValueError("bootstrap cannot have a previous transition")
        previous = StrategyRiskStateTransition.from_dict(wire)
        if previous.identity != identity:
            raise ValueError("identity mismatch")
        prior = previous.state
        _validate_state(prior, previous.effective_session)
        if previous.effective_session == session:
            if previous.input_sha256 != digest:
                raise ValueError("same-session input conflict")
            return {"status": "confirmed", "session": session, "identity": identity,
                    "input_sha256": digest, "previous": previous, "prior": prior}
        if previous.effective_session >= session or request.get("expected_previous_session") != previous.effective_session:
            raise ValueError("previous session missing or non-contiguous")
        return {"status": "ready", "session": session, "identity": identity,
                "input_sha256": digest, "previous": previous, "prior": prior}
    except (KeyError, TypeError, ValueError):
        return {"status": "unavailable"}


def _validate_state(state, session):
    fields = {"schema_version", "session", "alpha_trend_active", "alpha_epoch",
              "volatility_latch_active", "pending_intent", "intent_invalidation_reason"}
    if not isinstance(state, Mapping) or set(state) != fields or state["schema_version"] != _SCHEMA:
        raise ValueError("invalid TQQQ state")
    if state["session"] != session or type(state["alpha_trend_active"]) is not bool or type(state["volatility_latch_active"]) is not bool:
        raise ValueError("invalid TQQQ state values")
    if type(state["alpha_epoch"]) is not int or state["alpha_epoch"] < 0:
        raise ValueError("invalid alpha epoch")
    if state["alpha_trend_active"] and state["alpha_epoch"] == 0:
        raise ValueError("active alpha requires a positive epoch")
    intent = state["pending_intent"]
    if intent is not None:
        if not isinstance(intent, Mapping) or set(intent) != {"id", "alpha_epoch", "signal_session"}:
            raise ValueError("invalid pending intent")
        if type(intent["id"]) is not str or not 0 < len(intent["id"]) <= 128 or type(intent["alpha_epoch"]) is not int:
            raise ValueError("invalid pending intent identity")
        if (intent["alpha_epoch"] != state["alpha_epoch"] or intent["signal_session"] != session
                or not state["alpha_trend_active"] or state["volatility_latch_active"]):
            raise ValueError("stale pending intent")
    if state["intent_invalidation_reason"] not in (None, "alpha_exit", "new_session", "risk_latched"):
        raise ValueError("invalid intent invalidation")


def finish_tqqq_daily_state(prepared, request, *, alpha_active, volatility_latch, _research_simulated_fence=None):
    """Propose a transition; only a confirmed current head can permit an intent."""
    status = prepared["status"]
    if status == "unavailable":
        return {"status": status, "transition": None, "risk_increase_allowed": False}
    prior = prepared["prior"]
    session = prepared["session"]
    if status == "confirmed":
        if (prior["alpha_trend_active"] != alpha_active
                or prior["volatility_latch_active"] != volatility_latch):
            return {"status": "conflict", "transition": None, "risk_increase_allowed": False}
        intent = request.get("fresh_intent")
        allowed = bool(prior["alpha_trend_active"] and prior["pending_intent"] is not None
                       and intent == prior["pending_intent"] and alpha_active
                       and not prior["volatility_latch_active"] and not volatility_latch
                       and _research_simulated_fence is _RESEARCH_SIMULATED_FENCE
                       and request.get("execution_fence_confirmed") is True)
        return {"status": status, "transition": prepared["previous"].to_dict(),
                "risk_increase_allowed": allowed}
    old_alpha = prior["alpha_trend_active"] if prior else False
    epoch = (prior["alpha_epoch"] if prior else 0) + int(alpha_active and not old_alpha)
    reason = ("alpha_exit" if old_alpha and not alpha_active else "risk_latched" if volatility_latch
              else "new_session" if prior and prior["pending_intent"] else None)
    intent = request.get("fresh_intent")
    if not (isinstance(intent, Mapping) and set(intent) == {"id", "alpha_epoch", "signal_session"}
            and type(intent.get("id")) is str and 0 < len(intent["id"]) <= 128 and type(intent.get("alpha_epoch")) is int
            and intent["alpha_epoch"] == epoch and intent["signal_session"] == session and alpha_active
            and not volatility_latch):
        intent = None
    state = {"schema_version": _SCHEMA, "session": session, "alpha_trend_active": bool(alpha_active),
             "alpha_epoch": epoch, "volatility_latch_active": bool(volatility_latch),
             "pending_intent": dict(intent) if intent is not None else None,
             "intent_invalidation_reason": reason}
    transition = build_strategy_risk_state_transition(identity=prepared["identity"], effective_session=session,
        input_sha256=prepared["input_sha256"], state=state, previous_transition=prepared["previous"])
    return {"status": "proposed", "transition": transition.to_dict(), "risk_increase_allowed": False}
