"""SOXL Option D, explicitly called offline session-end research only.

No runtime entrypoint imports this module. Outputs describe simulated *new*
 risk budgets, never target weights, orders, or permission to execute.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import date

from quant_platform_kit.common import (
    StrategyRiskStateIdentity,
    StrategyRiskStateTransition,
    build_strategy_risk_state_transition,
)

SCHEMA = "soxl_option_d.research.v1"


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _sha(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("expected a lowercase SHA-256 digest")


def _day(value: str) -> str:
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("expected an ISO session date")
    return value


def _money(value: float | None, *, nonnegative: bool = False) -> None:
    if value is None:
        return
    if type(value) not in (int, float) or not math.isfinite(value) or (nonnegative and value < 0):
        raise ValueError("money must be a finite number in the declared range")


@dataclass(frozen=True)
class XnysCalendar:
    """Frozen full schedule; use freeze_xnys_calendar, not observed row dates."""

    sessions: tuple[str, ...]
    opens: tuple[str, ...]
    closes: tuple[str, ...]
    source: str

    def __post_init__(self):
        if not self.sessions or len(self.sessions) != len(self.opens) or len(self.sessions) != len(self.closes):
            raise ValueError("calendar must contain a complete nonempty schedule")
        if tuple(sorted(set(self.sessions))) != self.sessions:
            raise ValueError("calendar sessions must be unique and ordered")
        for session in self.sessions:
            _day(session)


def freeze_xnys_calendar(start: str, end: str) -> XnysCalendar:
    """Explicit research helper; optional calendar package is loaded lazily.

    All sessions in the inclusive range are retained, including half days.
    The caller freezes this object once for the entire state/restart chain.
    """
    import importlib.metadata

    import pandas_market_calendars as mcal

    _day(start); _day(end)
    schedule = mcal.get_calendar("XNYS").schedule(start_date=start, end_date=end)
    return XnysCalendar(
        tuple(t.date().isoformat() for t in schedule.index),
        tuple(t.isoformat() for t in schedule.market_open),
        tuple(t.isoformat() for t in schedule.market_close),
        f"pandas_market_calendars:{importlib.metadata.version('pandas_market_calendars')}:XNYS",
    )


@dataclass(frozen=True)
class OptionDBinding:
    identity: StrategyRiskStateIdentity
    policy_sha256: str
    overlay_sha256: str
    calendar: XnysCalendar

    def __post_init__(self):
        if not isinstance(self.identity, StrategyRiskStateIdentity) or not isinstance(self.calendar, XnysCalendar):
            raise ValueError("binding requires a QPK identity and frozen XNYS calendar")
        _sha(self.policy_sha256); _sha(self.overlay_sha256)

    def to_dict(self):
        return {"identity": self.identity.to_dict(), "policy_sha256": self.policy_sha256,
                "overlay_sha256": self.overlay_sha256, "calendar_sha256": _digest(asdict(self.calendar))}


@dataclass(frozen=True)
class HaltEvidence:
    """Caller-owned frozen loss-cluster facts, never inferred from streaks.

    equity is sampled at the trigger instant; cluster_loss is positive and
    excludes external flows over the explicitly supplied cluster interval.
    """

    trigger_session: str
    equity: float
    cluster_loss: float
    cluster_start: str
    cluster_end: str
    source_sha256: str
    trigger_net_flows: float

    def __post_init__(self):
        for d in (self.trigger_session, self.cluster_start, self.cluster_end):
            _day(d)
        _money(self.equity, nonnegative=True); _money(self.cluster_loss, nonnegative=True)
        if self.equity is None or self.cluster_loss is None or self.cluster_loss <= 0:
            raise ValueError("halt evidence requires trigger equity and positive cluster_loss")
        if not self.cluster_start <= self.cluster_end <= self.trigger_session:
            raise ValueError("invalid loss-cluster interval")
        _sha(self.source_sha256)
        _money(self.trigger_net_flows)
        if self.trigger_net_flows is None:
            raise ValueError("halt evidence requires trigger-instant cumulative flows")


@dataclass(frozen=True)
class SessionFacts:
    """One completed session, or an explicit missing row with safe defaults.

    cumulative_net_flows uses a fixed ledger origin across the whole replay.
    open_data_ok/open_caps_ok describe the actual next regular-open check,
    independently of close facts. None never means that a check passed.
    """

    session: str
    equity_open: float | None = None
    equity_end: float | None = None
    deposits: float | None = None
    withdrawals: float | None = None
    cumulative_net_flows: float | None = None
    marks_complete: bool = False
    ledger_complete: bool = False
    flows_verified: bool = False
    daily_breach_observed: bool | None = None
    caps_ok: bool | None = None
    caps_restorable: bool | None = None
    integrity_crisis: bool = False
    manual_hard_stop: bool = False
    governance_violation: bool = False
    open_data_ok: bool | None = None
    open_caps_ok: bool | None = None
    halt_evidence: HaltEvidence | None = None

    def __post_init__(self):
        _day(self.session)
        for name in ("equity_open", "equity_end", "deposits", "withdrawals", "cumulative_net_flows"):
            _money(getattr(self, name), nonnegative=name != "cumulative_net_flows")
        for name in ("marks_complete", "ledger_complete", "flows_verified", "integrity_crisis", "manual_hard_stop", "governance_violation"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be boolean")
        for name in ("daily_breach_observed", "caps_ok", "caps_restorable", "open_data_ok", "open_caps_ok"):
            if getattr(self, name) is not None and type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be boolean or unknown")
        if self.halt_evidence is not None and not isinstance(self.halt_evidence, HaltEvidence):
            raise ValueError("halt_evidence must be frozen HaltEvidence")


def signed_daily_loss(equity_open: float, equity_end: float, deposits: float, withdrawals: float) -> float:
    for v in (equity_open, equity_end, deposits, withdrawals):
        _money(v, nonnegative=True)
        if v is None:
            raise ValueError("signed loss requires complete financial facts")
    return equity_open + deposits - withdrawals - equity_end


def advance_option_d(
    *, binding: OptionDBinding, facts: SessionFacts,
    previous_transition: StrategyRiskStateTransition | None = None,
) -> StrategyRiskStateTransition:
    """Pure session-end transition, with fixed C=2/K=2/S1=.5/H=3.

    Every frozen-calendar session must be evaluated exactly once in order.
    Missing observations must use SessionFacts(session=...). No clock can
    advance by silently substituting the next available price observation.
    """
    sessions = binding.calendar.sessions
    if facts.session not in sessions:
        raise ValueError("facts must name a frozen XNYS session")
    idx = sessions.index(facts.session)
    prior = previous_transition.state if previous_transition is not None else None
    if prior is not None:
        if previous_transition.identity != binding.identity or prior.get("schema_version") != SCHEMA or prior.get("binding") != binding.to_dict():
            raise ValueError("previous state binding/schema changed")
        if prior.get("effective_session") != previous_transition.effective_session or prior.get("step") not in {"S0", "S1", "S2", "HARD"}:
            raise ValueError("invalid previous Option D state")
        if idx == 0 or sessions[idx - 1] != previous_transition.effective_session:
            raise ValueError("must advance to the next XNYS session")
    elif idx != 0:
        raise ValueError("root must start at the first frozen XNYS session")

    state = dict(prior) if prior else {
        "schema_version": SCHEMA, "binding": binding.to_dict(), "step": "S2",
        "loss_streak": 0, "loss_streak_uncertain": False, "non_loss_streak": 0, "completed_since_halt": 0,
        "data_gap_sessions": 0, "daily_latched": False, "caps_latched": False,
        "recovery_latched": False, "halt_session": None, "halt_equity": None,
        "cluster_loss": None, "halt_cumulative_net_flows": None, "halt_evidence": None,
        "hard_reasons": [],
    }
    state["effective_session"] = facts.session
    state["daily_cleared_at_open"] = False
    qualified = (facts.marks_complete and facts.ledger_complete and facts.flows_verified
                 and all(v is not None for v in (facts.equity_open, facts.equity_end, facts.deposits,
                                                 facts.withdrawals, facts.cumulative_net_flows, facts.daily_breach_observed)))
    loss = signed_daily_loss(facts.equity_open, facts.equity_end, facts.deposits, facts.withdrawals) if qualified else None
    limit = max(20., min(facts.equity_open * .05, 100.)) if qualified else None
    breached = facts.daily_breach_observed is True or (qualified and loss >= limit)
    nonloss = qualified and loss <= 0 and not breached
    losing = breached or (qualified and loss > 0)
    old_step = state["step"]
    open_ok = facts.open_data_ok is True and facts.open_caps_ok is True and old_step != "HARD" and not (
        facts.integrity_crisis or facts.manual_hard_stop or facts.governance_violation)
    if open_ok and not state["recovery_latched"]:
        state["daily_cleared_at_open"] = state["daily_latched"]
        state["daily_latched"] = False
        state["caps_latched"] = False

    # Unknown data breaks continuity; it never resets a known losing streak.
    if losing:
        state["loss_streak"] = 1 if state["loss_streak_uncertain"] else state["loss_streak"] + 1
        state["loss_streak_uncertain"] = False
    elif nonloss:
        state["loss_streak"] = 0
        state["loss_streak_uncertain"] = False
    else:
        state["loss_streak_uncertain"] = True
    needs_restart = old_step == "S1" and (losing or not qualified)
    new_recovery = (state["loss_streak"] == 3 and losing) or (not qualified and not state["recovery_latched"])
    if needs_restart or new_recovery:
        state.update(recovery_latched=True, halt_session=facts.session, completed_since_halt=0,
                     non_loss_streak=0, halt_equity=None, cluster_loss=None,
                     halt_cumulative_net_flows=None, halt_evidence=None)
    elif state["recovery_latched"]:
        state["completed_since_halt"] += 1
        state["non_loss_streak"] = state["non_loss_streak"] + 1 if nonloss else 0

    ref = facts.halt_evidence
    if ref is not None:
        if not state["recovery_latched"] or ref.trigger_session != state["halt_session"]:
            raise ValueError("halt evidence must match the active trigger session")
        if state["halt_evidence"] is not None and state["halt_evidence"] != asdict(ref):
            raise ValueError("halt evidence is frozen until a new trigger")
        # A late evidence row cannot supply the unknown flow level at trigger.
        if state["halt_evidence"] is None:
            if facts.session != ref.trigger_session or not qualified:
                raise ValueError("new halt evidence requires qualified trigger-session facts")
            state.update(halt_equity=ref.equity, cluster_loss=ref.cluster_loss,
                         halt_cumulative_net_flows=ref.trigger_net_flows, halt_evidence=asdict(ref))

    if breached:
        state["daily_latched"] = True
    if facts.caps_ok is not True:
        state["caps_latched"] = True
    soft = state["recovery_latched"] or state["daily_latched"] or state["caps_latched"]
    state["data_gap_sessions"] = state["data_gap_sessions"] + 1 if soft and not qualified else 0
    hard_reasons = set(state["hard_reasons"])
    for hit, reason in ((facts.caps_ok is False and facts.caps_restorable is not True, "unrestorable_caps"),
                        (facts.integrity_crisis,"integrity_crisis"), (facts.manual_hard_stop,"manual_hard_stop"),
                        (facts.governance_violation,"governance_violation"),
                        (state["data_gap_sessions"] >= 3,"data_gap_h3")):
        if hit:
            hard_reasons.add(reason)
    state["hard_reasons"] = sorted(hard_reasons)
    recovery_equity = None
    if qualified and state["halt_cumulative_net_flows"] is not None:
        recovery_equity = facts.equity_end - (facts.cumulative_net_flows - state["halt_cumulative_net_flows"])
    gates = {"G1":state["completed_since_halt"] >= 2, "G2":state["non_loss_streak"] >= 2,
             "G3":qualified, "G4":facts.caps_ok is True, "G5":not hard_reasons and old_step != "HARD",
             "G6":recovery_equity is not None and state["halt_equity"] is not None
                  and recovery_equity >= state["halt_equity"] + .5 * state["cluster_loss"]}
    if not gates["G5"]:
        step = "HARD"
    elif needs_restart or new_recovery:
        step = "S0"
    elif state["recovery_latched"]:
        if old_step == "S1" and nonloss and all(gates[g] for g in ("G3","G4","G5")):
            step = "S2"
            state["recovery_latched"] = state["daily_latched"] = state["caps_latched"] = False
        elif old_step != "S1" and all(gates.values()):
            step = "S1"
        else:
            step = "S0"
    else:
        step = "S0" if soft else "S2"
    # A same-session breach/unknown caps must not be bypassed by recovery gates.
    if step == "S1" and (breached or facts.caps_ok is not True):
        step = "S0"
    fraction = {"S0":0., "S1":.5, "S2":1., "HARD":0.}[step]
    state.update(step=step, gates=gates, signed_daily_loss=loss, daily_limit_usd=limit,
                 recovery_equity=recovery_equity, research_budget_fraction=fraction,
                 status="INCONCLUSIVE" if not qualified else ("GUARD_HALTED" if fraction == 0 else "RESEARCH_ONLY"),
                 no_order=True, execution_authorized=False, schwab_parked=True)
    return build_strategy_risk_state_transition(
        identity=binding.identity, effective_session=facts.session,
        input_sha256=_digest({"binding":binding.to_dict(),"facts":asdict(facts)}), state=state,
        previous_transition=previous_transition,
    )
