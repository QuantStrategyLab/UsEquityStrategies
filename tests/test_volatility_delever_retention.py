from __future__ import annotations

from us_equity_strategies.volatility_delever_retention import resolve_volatility_delever_retention


def _context(**values):
    return {
        "route": "no_action",
        "crisis_defense_required": False,
        "volatility_delever_context": {
            "actionable_for_position_control": True,
            "hard_risk": False,
            "soft_risk": False,
            "constructive": False,
            "rebound_confirm": False,
            **values,
        },
    }


def test_environment_retention_is_zero_without_context() -> None:
    decision = resolve_volatility_delever_retention(
        mode="environment",
        fixed_ratio=0.50,
        policy="tqqq_step_softzero_0.25_0.50",
        max_ratio=0.50,
        context_required=True,
        market_regime_context={},
    )

    assert decision["retention_ratio"] == 0.0
    assert decision["source"] == "missing_context"


def test_environment_retention_is_zero_when_market_regime_is_risk_off() -> None:
    decision = resolve_volatility_delever_retention(
        mode="environment",
        fixed_ratio=0.0,
        policy="soxl_step_rebound_0.25_0.50",
        max_ratio=0.50,
        context_required=True,
        market_regime_context={
            **_context(constructive=True, rebound_confirm=True),
            "route": "risk_off",
        },
    )

    assert decision["retention_ratio"] == 0.0
    assert decision["source"] == "market_regime_risk_off"


def test_tqqq_softzero_policy_retains_half_for_constructive_rebound() -> None:
    decision = resolve_volatility_delever_retention(
        mode="environment",
        fixed_ratio=0.0,
        policy="tqqq_step_softzero_0.25_0.50",
        max_ratio=0.50,
        context_required=True,
        market_regime_context=_context(constructive=True, rebound_confirm=True),
    )

    assert decision["retention_ratio"] == 0.50
    assert decision["source"] == "local_policy"


def test_tqqq_softzero_policy_clears_retention_for_soft_risk() -> None:
    decision = resolve_volatility_delever_retention(
        mode="environment",
        fixed_ratio=0.0,
        policy="tqqq_step_softzero_0.25_0.50",
        max_ratio=0.50,
        context_required=True,
        market_regime_context=_context(soft_risk=True, constructive=False, rebound_confirm=True),
    )

    assert decision["retention_ratio"] == 0.0
    assert decision["reason_codes"] == ("soft_risk",)


def test_soxl_rebound_policy_requires_rebound_confirmation() -> None:
    decision = resolve_volatility_delever_retention(
        mode="environment",
        fixed_ratio=0.0,
        policy="soxl_step_rebound_0.25_0.50",
        max_ratio=0.50,
        context_required=True,
        market_regime_context=_context(constructive=True, rebound_confirm=False),
    )

    assert decision["retention_ratio"] == 0.0
    assert decision["reason_codes"] == ("rebound_not_confirmed",)


def test_context_profile_ratio_is_capped_and_used_when_present() -> None:
    decision = resolve_volatility_delever_retention(
        mode="environment",
        fixed_ratio=0.0,
        policy="custom_policy",
        max_ratio=0.50,
        context_required=True,
        market_regime_context=_context(
            retention_profiles={"custom_policy": {"retention_ratio": 0.80, "reason_codes": ["custom"]}}
        ),
    )

    assert decision["retention_ratio"] == 0.50
    assert decision["source"] == "context_profile"
    assert decision["reason_codes"] == ("custom",)
