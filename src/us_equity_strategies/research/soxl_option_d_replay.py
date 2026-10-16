"""Default-OFF SOXL Option D consumer for explicit offline research reports."""
from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence

from quant_platform_kit.common import (
    StrategyRiskStateIdentity,
    StrategyRiskStateTransition,
    validate_strategy_risk_state_chain,
)

from .soxl_option_d import OptionDBinding, SessionFacts, XnysCalendar, advance_option_d


def binding_from_frozen_bytes(
    *, identity: StrategyRiskStateIdentity, calendar: XnysCalendar,
    config_bytes: bytes, policy_bytes: bytes, overlay_bytes: bytes,
) -> OptionDBinding:
    """Hash independent immutable artifacts; never rewrite candidate JSON."""
    if any(type(v) is not bytes or not v for v in (config_bytes, policy_bytes, overlay_bytes)):
        raise ValueError("config, policy and overlay require nonempty frozen bytes")
    if hashlib.sha256(config_bytes).hexdigest() != identity.config_sha256:
        raise ValueError("frozen config bytes do not match the candidate config identity")
    return OptionDBinding(identity, hashlib.sha256(policy_bytes).hexdigest(),
                          hashlib.sha256(overlay_bytes).hexdigest(), calendar)


def replay_option_d(
    baseline_report: Mapping[str, object], *, option_d_research_replay_enabled: bool = False,
    binding: OptionDBinding | None = None, observations: Sequence[SessionFacts] = (),
    through_session: str | None = None, requested_new_risk_usd: Mapping[str, float] | None = None,
    existing_transitions: Sequence[StrategyRiskStateTransition] = (), execution_mode: str = "RESEARCH_ONLY",
) -> Mapping[str, object]:
    """Return the exact baseline when OFF, otherwise add an offline appendix.

    No broker/executor/store/client argument exists. Caller-owned storage may
    persist the returned QPK transitions. Restart re-evaluates and checks the
    entire frozen prefix, not just its last digest. through_session includes
    trailing missing sessions; by default evaluate through the last supplied
    observation, never beyond the declared frozen calendar.
    """
    if type(option_d_research_replay_enabled) is not bool:
        raise ValueError("enable flag must be a boolean")
    if execution_mode != "RESEARCH_ONLY":
        raise ValueError("Option D replay accepts RESEARCH_ONLY execution mode only")
    if not option_d_research_replay_enabled:
        return baseline_report
    if not isinstance(binding, OptionDBinding) or not isinstance(baseline_report, Mapping):
        raise ValueError("enabled replay requires a frozen binding and baseline report")
    rows = list(observations)
    if any(not isinstance(row, SessionFacts) for row in rows):
        raise ValueError("observations require SessionFacts")
    days = [r.session for r in rows]
    if days != sorted(set(days)) or any(day not in binding.calendar.sessions for day in days):
        raise ValueError("observations must be unique ordered frozen XNYS sessions")
    end = through_session if through_session is not None else (days[-1] if days else None)
    if end not in binding.calendar.sessions or (days and end < days[-1]):
        raise ValueError("through_session must include all observations within the frozen XNYS calendar")
    evaluation_days = binding.calendar.sessions[:binding.calendar.sessions.index(end) + 1]
    budgets = dict(requested_new_risk_usd or {})
    if any(day not in evaluation_days or type(v) not in (int, float) or not math.isfinite(v) or v < 0
           for day, v in budgets.items()):
        raise ValueError("requested new-risk budgets must be finite nonnegative values on evaluated sessions")
    validate_strategy_risk_state_chain(list(existing_transitions))
    if len(existing_transitions) > len(evaluation_days):
        raise ValueError("existing chain extends beyond the requested frozen prefix")
    by_day = {r.session:r for r in rows}
    output = []
    previous = None
    for idx, day in enumerate(evaluation_days):
        transition = advance_option_d(binding=binding, facts=by_day.get(day, SessionFacts(day)), previous_transition=previous)
        if idx < len(existing_transitions) and transition != existing_transitions[idx]:
            raise ValueError("existing chain differs from replayed frozen prefix")
        state = transition.state
        output.append({"session":day, "transition":transition.to_dict(), "gates":state["gates"],
                       "data_gap":state["status"] == "INCONCLUSIVE",
                       "research_budget_fraction":state["research_budget_fraction"],
                       "simulated_new_risk_budget_usd":budgets.get(day, 0.) * state["research_budget_fraction"]})
        previous = transition
    return {**baseline_report,
            "option_d_replay":{"schema_version":"soxl_option_d_replay.research.v1", "enabled":True,
                               "binding":binding.to_dict(), "through_session":end, "sessions":output,
                               "budget_scope":"simulated_new_risk_only"},
            "no_order":True, "execution_authorized":False, "live_ready":False, "schwab_parked":True}
