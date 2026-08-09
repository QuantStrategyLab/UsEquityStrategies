from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest

from quant_platform_kit.common.models import PortfolioSnapshot
from quant_platform_kit.risk.contracts import CandidateRiskIdentity, RiskAction
from quant_platform_kit.risk.gate import assess_with_evidence
from quant_platform_kit.strategy_contracts import (
    BudgetIntent,
    PositionTarget,
    StrategyDecision,
)
from us_equity_strategies.production_parity.tqqq_contract import (
    TqqqProductionParityEvidence,
    evaluate_tqqq_research_contract,
)


def _candidate() -> CandidateRiskIdentity:
    return CandidateRiskIdentity(
        strategy_profile="tqqq_etf_only_single_strategy_research_v1",
        account_mode="single_strategy_account_v1",
        strategy_revision="1" * 40,
        runner_revision="2" * 40,
        config_sha256="3" * 64,
        input_manifest_sha256="4" * 64,
        authority_receipt_sha256="5" * 64,
    )


def _mandate(now: datetime, candidate: CandidateRiskIdentity) -> dict[str, object]:
    return {
        "mandate_id": "tqqq_etf_only_research_v1",
        "mandate_version": "v1",
        "authority_receipt_sha256": candidate.authority_receipt_sha256,
        "authority_scope": "RESEARCH_ONLY",
        "strategy_profile": candidate.strategy_profile,
        "account_mode": candidate.account_mode,
        "strategy_revision": candidate.strategy_revision,
        "runner_revision": candidate.runner_revision,
        "config_sha256": candidate.config_sha256,
        "input_manifest_sha256": candidate.input_manifest_sha256,
        "candidate_identity_sha256": candidate.candidate_sha256,
        "effective_at": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(days=30)).isoformat().replace("+00:00", "Z"),
        "max_snapshot_age_seconds": 300,
        "effective_exposure_cap": 0.50,
        "loss_budget": 0.01,
        "loss_budget_equity_reference": "completed_session_equity",
        "product_caps": {"TQQQ": 0.15, "BOXX": 0.50},
        "nominal_caps": {"TQQQ": 0.15, "BOXX": 0.50},
        "product_effective_caps": {"TQQQ": 0.45, "BOXX": 0.50},
        "product_leverage_factors": {"TQQQ": 3, "BOXX": 1},
        "allowed_nonzero_assets": ["TQQQ", "BOXX"],
        "max_nonzero_assets": 1,
        "broker_margin_factor": 1,
        "margin_stacking": False,
        "borrowing": False,
        "shorting": False,
        "income_sleeve_enabled": False,
        "option_overlay_enabled": False,
        "precommitted_executable_stop_distance": 0.05,
        "max_consecutive_completed_losing_exits": 5,
        "source_revision": "6" * 40,
    }


def _risk_state(now: datetime, candidate: CandidateRiskIdentity) -> dict[str, object]:
    return {
        "as_of": now.isoformat().replace("+00:00", "Z"),
        "mandate_id": "tqqq_etf_only_research_v1",
        "candidate_identity_sha256": candidate.candidate_sha256,
        "stop_loss_distance": 0.05,
        "stop_intent_ready": True,
        "tqqq_entry_fill_identity_sha256": "7" * 64,
        "stop_entry_fill_identity_sha256": "7" * 64,
        "consecutive_completed_losing_exits": 0,
        "account_drawdown_fraction": 0.04,
        "drawdown_scalar": 1.0,
    }


def _evidence(candidate: CandidateRiskIdentity) -> TqqqProductionParityEvidence:
    return TqqqProductionParityEvidence(
        contract_version="qsl.tqqq_production_parity.v1",
        config_sha256=candidate.config_sha256,
        input_manifest_sha256=candidate.input_manifest_sha256,
        candidate_identity_sha256=candidate.candidate_sha256,
        prior_state_sha256="8" * 64,
        signal_state_sha256="9" * 64,
        risk_active_state_sha256="a" * 64,
        volatility_hysteresis_state_sha256="b" * 64,
        retention_state_sha256="c" * 64,
        market_regime_control_sha256="d" * 64,
        signal_session=date(2026, 8, 6),
        execution_session=date(2026, 8, 7),
        signal_effective_after_trading_days=1,
        warmup_sessions=252,
        state_continuity="continuous",
        cash_reset=False,
        income_layer_enabled=False,
        option_overlay_enabled=False,
        option_growth_overlay_enabled=False,
        option_income_overlay_enabled=False,
        option_order_intents=(),
    )


def _evaluate(
    decision: StrategyDecision,
    *,
    evidence: TqqqProductionParityEvidence | None = None,
):
    now = datetime.now(timezone.utc)
    candidate = _candidate()
    engine = Mock()
    engine.assess.return_value = RiskAction(action="approve", reason="passed")
    with (
        patch("quant_platform_kit.risk.gate.build_risk_engine", return_value=engine),
        patch(
            "us_equity_strategies.production_parity.tqqq_contract._qpk_assess_with_evidence",
            wraps=assess_with_evidence,
        ) as assess,
    ):
        result = evaluate_tqqq_research_contract(
            decision,
            PortfolioSnapshot(
                as_of=now,
                total_equity=100_000.0,
                metadata={"observed_effective_exposure": 0.0},
            ),
            mandate_provenance=_mandate(now, candidate),
            candidate_identity=candidate,
            risk_control_state=_risk_state(now, candidate),
            production_parity_evidence=evidence or _evidence(candidate),
            market_data={},
        )
    assess.assert_called_once()
    engine.assess.assert_called_once()
    return result


def test_valid_etf_only_contract_preserves_research_decision_without_authority() -> (
    None
):
    decision = StrategyDecision(
        positions=(PositionTarget(symbol="TQQQ", target_weight=0.15),)
    )

    result = _evaluate(decision)

    assert result.outcome == "APPROVE"
    assert result.research_decision.positions == decision.positions
    assert result.executable_decision.positions == ()
    assert result.executable_decision.budgets == ()
    assert result.assessment.outcome == "APPROVE"
    assert result.assessment.execution_authorized is False
    assert result.authority_scope == "RESEARCH_ONLY"
    assert result.no_order is True
    assert result.size_zero_required is True
    assert result.promotion_eligible is False
    assert result.live_ready is False
    assert result.allowed_nonzero_assets == ("TQQQ", "BOXX")
    assert result.excluded_asset_weights == {
        "QQQM": 0.0,
        "SCHD": 0.0,
        "DGRO": 0.0,
        "SGOV": 0.0,
        "SPYI": 0.0,
        "QQQI": 0.0,
    }
    assert result.option_order_intents == ()


@pytest.mark.parametrize(
    "decision,evidence_update,reason",
    [
        (
            StrategyDecision(
                positions=(
                    PositionTarget(symbol="TQQQ", target_weight=0.10),
                    PositionTarget(symbol="BOXX", target_weight=0.10),
                )
            ),
            {},
            "nonzero_asset_count",
        ),
        (
            StrategyDecision(
                positions=(PositionTarget(symbol="QQQM", target_weight=0.10),)
            ),
            {},
            "excluded_asset",
        ),
        (
            StrategyDecision(budgets=(BudgetIntent(name="income", amount=1.0),)),
            {},
            "budget_intent_not_allowed",
        ),
        (StrategyDecision(), {"cash_reset": True}, "cash_reset_forbidden"),
        (
            StrategyDecision(),
            {"state_continuity": "daily_reset"},
            "continuous_state_required",
        ),
        (
            StrategyDecision(),
            {"warmup_sessions": 200},
            "insufficient_warmup",
        ),
        (
            StrategyDecision(),
            {"market_regime_control_sha256": ""},
            "market_regime_control_required",
        ),
        (
            StrategyDecision(),
            {"signal_effective_after_trading_days": 0},
            "invalid_signal_timing",
        ),
        (
            StrategyDecision(),
            {"income_layer_enabled": True},
            "income_layer_not_allowed",
        ),
        (
            StrategyDecision(),
            {"option_overlay_enabled": True},
            "option_overlay_not_allowed",
        ),
    ],
)
def test_noncanonical_or_legacy_inputs_fail_closed_after_one_qpk_assessment(
    decision: StrategyDecision,
    evidence_update: dict[str, object],
    reason: str,
) -> None:
    candidate = _candidate()
    evidence = replace(_evidence(candidate), **evidence_update)

    result = _evaluate(decision, evidence=evidence)

    assert result.outcome == "REJECT"
    assert reason in result.reason_codes
    assert result.research_decision.positions == ()
    assert result.research_decision.budgets == ()
    assert result.executable_decision.positions == ()
    assert result.executable_decision.budgets == ()
    assert result.assessment.outcome == "REJECT"
    assert result.assessment.execution_authorized is False
    assert result.no_order is True
    assert result.size_zero_required is True


def test_missing_typed_evidence_fails_closed_after_one_qpk_assessment() -> None:
    now = datetime.now(timezone.utc)
    candidate = _candidate()
    engine = Mock()
    engine.assess.return_value = RiskAction(action="approve", reason="passed")
    with (
        patch("quant_platform_kit.risk.gate.build_risk_engine", return_value=engine),
        patch(
            "us_equity_strategies.production_parity.tqqq_contract._qpk_assess_with_evidence",
            wraps=assess_with_evidence,
        ) as assess,
    ):
        result = evaluate_tqqq_research_contract(
            StrategyDecision(),
            PortfolioSnapshot(
                as_of=now,
                total_equity=100_000.0,
                metadata={"observed_effective_exposure": 0.0},
            ),
            mandate_provenance=_mandate(now, candidate),
            candidate_identity=candidate,
            risk_control_state=_risk_state(now, candidate),
            production_parity_evidence=None,
            market_data={},
        )

    assess.assert_called_once()
    engine.assess.assert_called_once()
    assert result.outcome == "REJECT"
    assert result.reason_codes == ("missing_production_parity_evidence",)
    assert result.assessment.outcome == "REJECT"
    assert result.research_decision.positions == ()
    assert result.executable_decision.positions == ()
