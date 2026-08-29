from __future__ import annotations

import pytest
from quant_platform_kit.common import (
    StrategyRiskStateChainError,
    StrategyRiskStateIdentity,
    validate_strategy_risk_state_chain,
)

from us_equity_strategies.volatility_delever_cooldown import (
    VOLATILITY_DELEVER_COOLDOWN_STATE_SCHEMA_VERSION,
    advance_volatility_delever_cooldown,
    build_volatility_delever_cooldown_transition,
)


def _identity(*, candidate_id: str = "soxl_soxx_core_only_p2_v3") -> StrategyRiskStateIdentity:
    return StrategyRiskStateIdentity(
        strategy_profile="soxl_soxx_trend_income",
        account_scope="paper",
        candidate_id=candidate_id,
        config_sha256="a" * 64,
    )


def test_two_following_sessions_remain_blocked_after_a_deleveraging_trigger() -> None:
    trigger = advance_volatility_delever_cooldown(
        previous_state=None,
        effective_session="2026-08-24",
        cooldown_sessions=2,
        deleveraging_triggered=True,
    )
    first_blocked = advance_volatility_delever_cooldown(
        previous_state=trigger,
        effective_session="2026-08-25",
        cooldown_sessions=2,
        deleveraging_triggered=False,
    )
    second_blocked = advance_volatility_delever_cooldown(
        previous_state=first_blocked,
        effective_session="2026-08-26",
        cooldown_sessions=2,
        deleveraging_triggered=False,
    )
    released = advance_volatility_delever_cooldown(
        previous_state=second_blocked,
        effective_session="2026-08-27",
        cooldown_sessions=2,
        deleveraging_triggered=False,
    )

    assert trigger["blocked_sessions_remaining"] == 2
    assert trigger["reentry_allowed"] is False
    assert first_blocked["blocked_sessions_remaining"] == 1
    assert first_blocked["reentry_allowed"] is False
    assert second_blocked["blocked_sessions_remaining"] == 0
    assert second_blocked["reentry_allowed"] is False
    assert released["reentry_allowed"] is True
    assert released["reason_code"] == "cooldown_elapsed"


def test_a_new_trigger_resets_the_cooldown() -> None:
    trigger = advance_volatility_delever_cooldown(
        previous_state=None,
        effective_session="2026-08-24",
        cooldown_sessions=2,
        deleveraging_triggered=True,
    )
    reset = advance_volatility_delever_cooldown(
        previous_state=trigger,
        effective_session="2026-08-25",
        cooldown_sessions=2,
        deleveraging_triggered=True,
    )

    assert reset["blocked_sessions_remaining"] == 2
    assert reset["last_deleveraging_session"] == "2026-08-25"
    assert reset["reason_code"] == "deleveraging_triggered"


def test_cooldown_state_rejects_stale_or_changed_configuration() -> None:
    trigger = advance_volatility_delever_cooldown(
        previous_state=None,
        effective_session="2026-08-24",
        cooldown_sessions=2,
        deleveraging_triggered=True,
    )

    with pytest.raises(ValueError, match="advance"):
        advance_volatility_delever_cooldown(
            previous_state=trigger,
            effective_session="2026-08-24",
            cooldown_sessions=2,
            deleveraging_triggered=False,
        )
    with pytest.raises(ValueError, match="must not change"):
        advance_volatility_delever_cooldown(
            previous_state=trigger,
            effective_session="2026-08-25",
            cooldown_sessions=3,
            deleveraging_triggered=False,
        )
    with pytest.raises(TypeError, match="boolean"):
        advance_volatility_delever_cooldown(
            previous_state=trigger,
            effective_session="2026-08-25",
            cooldown_sessions=2,
            deleveraging_triggered="false",
        )


def test_transition_is_content_addressed_and_replayable() -> None:
    first = build_volatility_delever_cooldown_transition(
        identity=_identity(),
        effective_session="2026-08-24",
        frozen_input_sha256="b" * 64,
        cooldown_sessions=2,
        deleveraging_triggered=True,
    )
    second = build_volatility_delever_cooldown_transition(
        identity=_identity(),
        effective_session="2026-08-25",
        frozen_input_sha256="c" * 64,
        cooldown_sessions=2,
        deleveraging_triggered=False,
        previous_transition=first,
    )
    duplicate_first = build_volatility_delever_cooldown_transition(
        identity=_identity(),
        effective_session="2026-08-24",
        frozen_input_sha256="b" * 64,
        cooldown_sessions=2,
        deleveraging_triggered=True,
    )

    assert first.to_dict() == duplicate_first.to_dict()
    assert first.state["schema_version"] == VOLATILITY_DELEVER_COOLDOWN_STATE_SCHEMA_VERSION
    assert second.state["reentry_allowed"] is False
    validate_strategy_risk_state_chain([first, second])


def test_transition_cannot_cross_candidate_identity() -> None:
    first = build_volatility_delever_cooldown_transition(
        identity=_identity(),
        effective_session="2026-08-24",
        frozen_input_sha256="b" * 64,
        cooldown_sessions=2,
        deleveraging_triggered=True,
    )

    with pytest.raises(StrategyRiskStateChainError, match="identity"):
        build_volatility_delever_cooldown_transition(
            identity=_identity(candidate_id="soxl_soxx_core_only_p2_v4"),
            effective_session="2026-08-25",
            frozen_input_sha256="c" * 64,
            cooldown_sessions=2,
            deleveraging_triggered=False,
            previous_transition=first,
        )
