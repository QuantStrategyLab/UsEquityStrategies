from datetime import date

import pytest

from quant_platform_kit.common.strategy_contracts import PositionTarget, StrategyDecision
from us_equity_strategies.research.portfolio_candidate_adapter import (
    adapt_buy_hold_reference,
    adapt_catalog_decision,
    adapt_guard_research_decision,
)


@pytest.mark.parametrize("profile", ["tqqq_growth_income", "soxl_soxx_trend_income"])
def test_catalog_adapter_preserves_builder_value_targets(profile: str) -> None:
    original = StrategyDecision(
        positions=(PositionTarget(symbol="TQQQ", target_value=25.0),),
        risk_flags=("guard:active",), diagnostics={"cooldown": 2},
    )
    result = adapt_catalog_decision(
        candidate_id=f"{profile}:research", profile=profile,
        decision_date=date(2024, 3, 1), member_budget_usd=100.0,
        decision=original, cost_model_id="research-5bps",
    )
    assert result.targets == (("TQQQ", 25.0),)
    assert result.target_mode == "value"
    assert result.internal_state == original.diagnostics
    assert result.risk_flags == original.risk_flags
    assert result.evidence_status == "unvalidated"
    assert result.historical_return_eligible is False
    assert result.research_only is True


def test_guard_candidate_keeps_distinct_identity_and_budget() -> None:
    result = adapt_guard_research_decision(decision={
        "signal_date": "2024-03-01", "member_budget_usd": 50.0,
        "target_tqqq_usd": 20.0, "guard_route": "risk_reduced",
        "applied_route": "risk_reduced", "core_state": "hold",
        "volatility_applied": True,
    })
    assert result.candidate_id == "tqqq_qqq_guard_cash_research_v1"
    assert result.profile is None
    assert result.targets == (("TQQQ", 20.0),)
    assert result.member_budget_usd == 50.0
    assert result.evidence_status == "development"
    assert result.historical_return_eligible is False


def test_adapter_rejects_reference_asset_and_unsupported_evidence_upgrade() -> None:
    with pytest.raises(ValueError, match="member scope"):
        adapt_catalog_decision(
            candidate_id="QQQM", profile="global_etf_rotation",
            decision_date=date(2024, 3, 1), member_budget_usd=100.0,
            decision=StrategyDecision(),
        )
    with pytest.raises(ValueError, match="unsupported evidence status"):
        adapt_catalog_decision(
            candidate_id="tqqq", profile="tqqq_growth_income",
            decision_date=date(2024, 3, 1), member_budget_usd=100.0,
            decision=StrategyDecision(), evidence_status="qualified",
        )


def test_guard_target_cannot_spend_more_than_member_budget() -> None:
    with pytest.raises(ValueError, match="exceeds member budget"):
        adapt_guard_research_decision(decision={
            "signal_date": "2024-03-01", "member_budget_usd": 50.0,
            "target_tqqq_usd": 60.0, "guard_route": "no_action",
            "applied_route": "no_action", "core_state": "buy",
            "volatility_applied": False,
        })


@pytest.mark.parametrize("symbol", ["QQQM", "BOXX"])
def test_buy_hold_reference_is_not_a_portfolio_member(symbol: str) -> None:
    reference = adapt_buy_hold_reference(symbol=symbol, decision_date=date(2024, 3, 1))
    assert reference.symbol == symbol
    assert reference.portfolio_member is False
    assert reference.research_only is True
