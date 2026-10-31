"""Journal-bound conditional planning; no order or execution authority."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass, replace
from collections.abc import Mapping
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
import math
from typing import Any


def _canonical(value):
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("NONFINITE_CONTEXT_VALUE")
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("NAIVE_CONTEXT_DATETIME")
        return {"__datetime__": value.isoformat(), "timezone": str(value.tzinfo), "fold": value.fold}
    if type(value) is date:
        return {"__date__": value.isoformat()}
    if is_dataclass(value) and not isinstance(value, type):
        return {"__dataclass__": f"{type(value).__module__}.{type(value).__qualname__}",
                "fields": {f.name: _canonical(getattr(value, f.name)) for f in fields(value)}}
    if isinstance(value, Mapping):
        if any(type(k) is not str for k in value):
            raise ValueError("NONSTRING_CONTEXT_KEY")
        return {"__mapping__": {k: _canonical(v) for k, v in value.items()}}
    if type(value) in (tuple, list):
        return {"__tuple__" if type(value) is tuple else "__list__": [_canonical(v) for v in value]}
    raise ValueError("UNSUPPORTED_CONTEXT_VALUE")


def canonical_tqqq_context_sha256(ctx):
    """Hash all context inputs except the journal-bound self-referential state."""
    from quant_platform_kit.common.strategy_contracts import StrategyContext
    if type(ctx) is not StrategyContext or not isinstance(ctx.state, Mapping):
        raise ValueError("STRATEGY_CONTEXT_REQUIRED")
    projection = {"as_of": ctx.as_of, "market_data": ctx.market_data, "portfolio": ctx.portfolio,
                  "state": {k: v for k, v in ctx.state.items() if k != "tqqq_daily_state"},
                  "runtime_config": ctx.runtime_config, "capabilities": ctx.capabilities, "artifacts": ctx.artifacts}
    raw = json.dumps(_canonical(projection), sort_keys=True, separators=(",", ":"),
                     ensure_ascii=True, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class ConditionalTqqqCandidate:
    status: str
    strategy_state_sha256: str
    strategy_intent_sha256: str
    input_sha256: str
    strategy_session: str
    positions: tuple[dict[str, Any], ...]
    budgets: tuple[dict[str, Any], ...]
    risk_flags: tuple[str, ...]
    diagnostics: dict[str, Any]
    executable: bool = field(default=False, init=False)
    order_authorized: bool = field(default=False, init=False)


def build_tqqq_conditional_candidate(ctx, *, journal_session, planning_handle):
    """Revalidate an in-process handle and produce a non-executable candidate.

    Submission remains subject to the central risk gate and a fresh, joint
    journal reservation and dispatch for each concrete order.
    """
    from quant_platform_kit.common.execution_state import (
        AccountSubmissionSession, ConditionalPlanningHandle, submission_binding_digest,
    )
    from quant_platform_kit.common.strategy_risk_state import StrategyRiskStateTransition
    from us_equity_strategies.entrypoints import _build_tqqq_growth_income_decision
    from us_equity_strategies._decision_candidate_marker import RESEARCH_SIMULATED_FENCE

    if type(journal_session) is not AccountSubmissionSession or type(planning_handle) is not ConditionalPlanningHandle:
        raise ValueError("JOURNAL_PLANNING_HANDLE_REQUIRED")
    ctx = deepcopy(ctx)
    request = ctx.state.get("tqqq_daily_state")
    if not isinstance(request, dict) or request.get("status") != "ready":
        raise ValueError("CONFIRMED_STRATEGY_STATE_REQUIRED")
    transition = StrategyRiskStateTransition.from_dict(request["previous_transition"])
    intent = transition.state.get("pending_intent")
    if (canonical_tqqq_context_sha256(ctx) != transition.input_sha256
            or request.get("identity") != transition.identity.to_dict()
            or request.get("input_sha256") != transition.input_sha256
            or request.get("fresh_intent") != intent or not isinstance(intent, dict)
            or str(ctx.as_of) != transition.effective_session
            or planning_handle.strategy_state_sha256 != transition.transition_sha256
            or planning_handle.strategy_intent_sha256 != submission_binding_digest(intent)
            or planning_handle.input_sha256 != transition.input_sha256
            or planning_handle.strategy_session != transition.effective_session
            or journal_session.profile != transition.identity.strategy_profile):
        raise ValueError("CONDITIONAL_PLANNING_BINDING_MISMATCH")
    if journal_session.validate_conditional_planning_handle(planning_handle, input_sha256=transition.input_sha256) is not True:
        raise ValueError("CONDITIONAL_PLANNING_HANDLE_REJECTED")
    actual = StrategyRiskStateTransition.from_dict(journal_session.read_state()["strategy_state"])
    if actual != transition:
        raise ValueError("JOURNAL_HEAD_MISMATCH")
    state = dict(ctx.state)
    state["tqqq_daily_state"] = {**request, "execution_fence_confirmed": True}
    decision = _build_tqqq_growth_income_decision(replace(ctx, state=state),
                                                 _research_simulated_fence=RESEARCH_SIMULATED_FENCE)
    if decision.diagnostics.get("tqqq_daily_state_risk_increase_allowed") is not True:
        raise ValueError("CONDITIONAL_STRATEGY_PLAN_UNAVAILABLE")
    diagnostics = dict(decision.diagnostics)
    diagnostics["tqqq_daily_state_risk_increase_allowed"] = False
    diagnostics["tqqq_conditional_candidate"] = True
    return ConditionalTqqqCandidate("CONDITIONAL_NOT_EXECUTABLE", transition.transition_sha256,
        planning_handle.strategy_intent_sha256, transition.input_sha256, transition.effective_session,
        tuple(asdict(x) for x in decision.positions), tuple(asdict(x) for x in decision.budgets),
        tuple(decision.risk_flags), diagnostics)
