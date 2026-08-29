"""Pure, replayable cooldown state for volatility-deleveraging controls.

This module only calculates strategy state.  It neither reads broker state nor
changes an allocation.  A platform that wants to use it must persist the
result through QuantPlatformKit's immutable strategy-risk-state contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from quant_platform_kit.common import (
    StrategyRiskStateIdentity,
    StrategyRiskStateTransition,
    build_strategy_risk_state_transition,
)

VOLATILITY_DELEVER_COOLDOWN_STATE_SCHEMA_VERSION = "volatility_delever_cooldown.v1"
MAX_VOLATILITY_DELEVER_COOLDOWN_SESSIONS = 252

_STATE_FIELDS = frozenset(
    {
        "schema_version",
        "effective_session",
        "cooldown_sessions",
        "blocked_sessions_remaining",
        "reentry_allowed",
        "last_deleveraging_session",
        "reason_code",
    }
)
_REASON_CODES = frozenset(
    {
        "no_prior_deleveraging",
        "deleveraging_triggered",
        "cooldown_active",
        "cooldown_elapsed",
    }
)


def _effective_session(value: object, *, field_name: str = "effective_session") -> str:
    normalized = str(value or "").strip()
    try:
        return date.fromisoformat(normalized).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 date") from exc


def _cooldown_sessions(value: object) -> int:
    if isinstance(value, bool):
        raise TypeError("cooldown_sessions must be an integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("cooldown_sessions must be an integer") from exc
    if str(value).strip() not in {str(normalized), f"+{normalized}"}:
        raise ValueError("cooldown_sessions must be an integer")
    if not 0 <= normalized <= MAX_VOLATILITY_DELEVER_COOLDOWN_SESSIONS:
        raise ValueError(
            f"cooldown_sessions must be between 0 and {MAX_VOLATILITY_DELEVER_COOLDOWN_SESSIONS}"
        )
    return normalized


def _triggered(value: object) -> bool:
    if not isinstance(value, bool):
        raise TypeError("deleveraging_triggered must be a boolean")
    return value


def _validated_prior_state(value: Mapping[str, object] | None) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != _STATE_FIELDS:
        raise ValueError("previous cooldown state has invalid fields")
    if value.get("schema_version") != VOLATILITY_DELEVER_COOLDOWN_STATE_SCHEMA_VERSION:
        raise ValueError("previous cooldown state has an unsupported schema version")
    session = _effective_session(value.get("effective_session"), field_name="previous effective_session")
    cooldown_sessions = _cooldown_sessions(value.get("cooldown_sessions"))
    remaining = value.get("blocked_sessions_remaining")
    if isinstance(remaining, bool) or not isinstance(remaining, int) or not 0 <= remaining <= cooldown_sessions:
        raise ValueError("previous blocked_sessions_remaining is invalid")
    reentry_allowed = value.get("reentry_allowed")
    if not isinstance(reentry_allowed, bool):
        raise TypeError("previous reentry_allowed must be a boolean")
    last_deleveraging_session = value.get("last_deleveraging_session")
    if last_deleveraging_session is not None:
        last_deleveraging_session = _effective_session(
            last_deleveraging_session,
            field_name="previous last_deleveraging_session",
        )
        if last_deleveraging_session > session:
            raise ValueError("previous last_deleveraging_session cannot be after the previous effective_session")
    reason_code = value.get("reason_code")
    if reason_code not in _REASON_CODES:
        raise ValueError("previous cooldown state has an unknown reason_code")
    if reentry_allowed and remaining != 0:
        raise ValueError("previous cooldown state cannot allow re-entry while sessions remain blocked")
    return {
        "schema_version": VOLATILITY_DELEVER_COOLDOWN_STATE_SCHEMA_VERSION,
        "effective_session": session,
        "cooldown_sessions": cooldown_sessions,
        "blocked_sessions_remaining": remaining,
        "reentry_allowed": reentry_allowed,
        "last_deleveraging_session": last_deleveraging_session,
        "reason_code": reason_code,
    }


def advance_volatility_delever_cooldown(
    *,
    previous_state: Mapping[str, object] | None,
    effective_session: object,
    cooldown_sessions: object,
    deleveraging_triggered: object,
) -> dict[str, object]:
    """Calculate the next re-entry state from one frozen session input.

    A trigger blocks re-entry in its own session and the configured number of
    following sessions.  A fresh trigger during a cooldown resets that
    cooldown.  Input validation is deliberately strict: an absent, malformed,
    stale, or differently configured predecessor raises instead of silently
    allowing re-entry.
    """

    session = _effective_session(effective_session)
    configured_cooldown = _cooldown_sessions(cooldown_sessions)
    triggered = _triggered(deleveraging_triggered)
    prior = _validated_prior_state(previous_state)
    if prior is not None:
        if configured_cooldown != prior["cooldown_sessions"]:
            raise ValueError("cooldown_sessions must not change within a risk-state chain")
        if session <= prior["effective_session"]:
            raise ValueError("effective_session must advance beyond the previous cooldown state")

    if triggered:
        return {
            "schema_version": VOLATILITY_DELEVER_COOLDOWN_STATE_SCHEMA_VERSION,
            "effective_session": session,
            "cooldown_sessions": configured_cooldown,
            "blocked_sessions_remaining": configured_cooldown,
            "reentry_allowed": False,
            "last_deleveraging_session": session,
            "reason_code": "deleveraging_triggered",
        }

    if prior is None:
        return {
            "schema_version": VOLATILITY_DELEVER_COOLDOWN_STATE_SCHEMA_VERSION,
            "effective_session": session,
            "cooldown_sessions": configured_cooldown,
            "blocked_sessions_remaining": 0,
            "reentry_allowed": True,
            "last_deleveraging_session": None,
            "reason_code": "no_prior_deleveraging",
        }

    prior_remaining = int(prior["blocked_sessions_remaining"])
    if prior_remaining > 0:
        return {
            "schema_version": VOLATILITY_DELEVER_COOLDOWN_STATE_SCHEMA_VERSION,
            "effective_session": session,
            "cooldown_sessions": configured_cooldown,
            "blocked_sessions_remaining": prior_remaining - 1,
            "reentry_allowed": False,
            "last_deleveraging_session": prior["last_deleveraging_session"],
            "reason_code": "cooldown_active",
        }

    return {
        "schema_version": VOLATILITY_DELEVER_COOLDOWN_STATE_SCHEMA_VERSION,
        "effective_session": session,
        "cooldown_sessions": configured_cooldown,
        "blocked_sessions_remaining": 0,
        "reentry_allowed": True,
        "last_deleveraging_session": prior["last_deleveraging_session"],
        "reason_code": "cooldown_elapsed",
    }


def build_volatility_delever_cooldown_transition(
    *,
    identity: StrategyRiskStateIdentity | Mapping[str, object],
    effective_session: object,
    frozen_input_sha256: object,
    cooldown_sessions: object,
    deleveraging_triggered: object,
    previous_transition: StrategyRiskStateTransition | None = None,
) -> StrategyRiskStateTransition:
    """Build an immutable QPK transition around the pure cooldown result.

    The caller is responsible for supplying a frozen input digest and for
    durably storing the returned transition.  This helper does not read or
    write a broker, a database, or a runtime configuration.
    """

    if previous_transition is not None and not isinstance(previous_transition, StrategyRiskStateTransition):
        raise ValueError("previous_transition must be a StrategyRiskStateTransition")
    state = advance_volatility_delever_cooldown(
        previous_state=previous_transition.state if previous_transition is not None else None,
        effective_session=effective_session,
        cooldown_sessions=cooldown_sessions,
        deleveraging_triggered=deleveraging_triggered,
    )
    return build_strategy_risk_state_transition(
        identity=identity,
        effective_session=effective_session,
        input_sha256=frozen_input_sha256,
        state=state,
        previous_transition=previous_transition,
    )


__all__ = [
    "MAX_VOLATILITY_DELEVER_COOLDOWN_SESSIONS",
    "VOLATILITY_DELEVER_COOLDOWN_STATE_SCHEMA_VERSION",
    "advance_volatility_delever_cooldown",
    "build_volatility_delever_cooldown_transition",
]
