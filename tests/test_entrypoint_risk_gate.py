from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest

from quant_platform_kit.common.models import PortfolioSnapshot, Position
from quant_platform_kit.risk.contracts import CandidateRiskIdentity, RiskAction
from quant_platform_kit.strategy_contracts import PositionTarget, StrategyContext, StrategyDecision

import us_equity_strategies.entrypoints as entrypoints
import us_equity_strategies.entrypoints._common as common
from us_equity_strategies.entrypoints._common import apply_risk_gate


_SOXL_ASSETS = ("SOXL", "SOXX", "BOXX", "SCHD", "DGRO", "SGOV", "SPYI", "QQQI")
_SOXL_NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


def _soxl_candidate(**overrides: object) -> CandidateRiskIdentity:
    values: dict[str, object] = {
        "strategy_profile": "soxl_soxx_trend_income",
        "account_mode": "single_strategy",
        "strategy_revision": "b" * 40,
        "runner_revision": "c" * 40,
        "config_sha256": "d" * 64,
        "input_manifest_sha256": "e" * 64,
        "authority_receipt_sha256": "a" * 64,
    }
    values.update(overrides)
    return CandidateRiskIdentity(**values)


def _soxl_mandate(candidate: CandidateRiskIdentity | None = None) -> dict[str, object]:
    candidate = candidate or _soxl_candidate()
    factors = {symbol: 3 if symbol == "SOXL" else 1 for symbol in _SOXL_ASSETS}
    caps = {symbol: 0.15 if symbol == "SOXL" else 0.50 for symbol in _SOXL_ASSETS}
    return {
        "mandate_id": "soxl_p3_promotion_research_v1",
        "mandate_version": "2026-08-05.1",
        "authority_receipt_sha256": candidate.authority_receipt_sha256,
        "authority_scope": "RESEARCH_ONLY",
        "strategy_profile": candidate.strategy_profile,
        "account_mode": candidate.account_mode,
        "strategy_revision": candidate.strategy_revision,
        "runner_revision": candidate.runner_revision,
        "config_sha256": candidate.config_sha256,
        "input_manifest_sha256": candidate.input_manifest_sha256,
        "candidate_identity_sha256": candidate.candidate_sha256,
        "effective_at": "2026-08-05T11:59:00Z",
        "expires_at": "2026-08-05T12:01:00Z",
        "max_snapshot_age_seconds": 300,
        "effective_exposure_cap": 0.50,
        "loss_budget": 0.01,
        "product_caps": caps,
        "nominal_caps": caps,
        "product_leverage_factors": factors,
        "allowed_nonzero_assets": list(_SOXL_ASSETS),
        "source_revision": "2f75b59289ef24ab47a3ed8d522c9ef8d6aea6b2",
    }


def _soxl_context() -> StrategyContext:
    snapshot = PortfolioSnapshot(
        as_of=_SOXL_NOW,
        total_equity=100_000.0,
        positions=(),
        metadata={"observed_effective_exposure": 0.0},
    )
    return StrategyContext(
        as_of=_SOXL_NOW,
        portfolio=snapshot,
        market_data={"derived_indicators": {"synthetic": True}},
    )


def _soxl_raw_decision() -> StrategyDecision:
    return StrategyDecision(
        positions=(
            PositionTarget(symbol="SOXL", target_value=70_000.0),
            PositionTarget(symbol="BOXX", target_value=30_000.0, role="safe_haven"),
        ),
        diagnostics={"source": "shared_builder"},
    )


def test_soxl_promotion_research_sizes_and_assesses_exactly_once(monkeypatch) -> None:
    candidate = _soxl_candidate()
    mandate = _soxl_mandate(candidate)
    engine = Mock()
    engine.assess.return_value = RiskAction(action="approve", reason="passed")
    builder = Mock(return_value=_soxl_raw_decision())
    monitor = Mock()
    legacy_gate = Mock()
    monkeypatch.setattr(entrypoints, "_build_soxl_soxx_trend_income_decision", builder)
    monkeypatch.setattr(entrypoints, "record_strategy_decision", monitor)
    monkeypatch.setattr(entrypoints, "apply_risk_gate", legacy_gate)

    with (
        patch("quant_platform_kit.risk.gate._utc_now", return_value=_SOXL_NOW),
        patch("quant_platform_kit.risk.gate.build_risk_engine", return_value=engine),
    ):
        result = entrypoints.evaluate_soxl_soxx_trend_income_promotion_research(
            _soxl_context(),
            candidate_identity=candidate,
            mandate_provenance=mandate,
            stop_loss_distances={"SOXL": 0.05, "BOXX": 0.05},
            drawdown_scalar=1.0,
            inputs_fresh=True,
        )

    assert result.assessment.outcome == "APPROVE"
    assert {position.symbol: position.target_weight for position in result.decision.positions} == pytest.approx({
        "SOXL": 0.14,
        "BOXX": 0.06,
    })
    builder.assert_called_once()
    engine.assess.assert_called_once()
    legacy_gate.assert_not_called()
    monitor.assert_not_called()


def test_soxl_promotion_research_identity_failures_clear_decision_and_assess_once(
    monkeypatch,
) -> None:
    base_candidate = _soxl_candidate()
    cases = (
        (None, "missing_candidate_identity"),
        (
            _soxl_candidate(input_manifest_sha256="f" * 64),
            "candidate_input_manifest_digest_mismatch",
        ),
    )
    builder = Mock(return_value=_soxl_raw_decision())
    monitor = Mock()
    legacy_gate = Mock()
    monkeypatch.setattr(entrypoints, "_build_soxl_soxx_trend_income_decision", builder)
    monkeypatch.setattr(entrypoints, "record_strategy_decision", monitor)
    monkeypatch.setattr(entrypoints, "apply_risk_gate", legacy_gate)

    for candidate, reason_code in cases:
        engine = Mock()
        engine.assess.return_value = RiskAction(action="approve", reason="passed")
        with (
            patch("quant_platform_kit.risk.gate._utc_now", return_value=_SOXL_NOW),
            patch("quant_platform_kit.risk.gate.build_risk_engine", return_value=engine),
        ):
            result = entrypoints.evaluate_soxl_soxx_trend_income_promotion_research(
                _soxl_context(),
                candidate_identity=candidate,
                mandate_provenance=_soxl_mandate(base_candidate),
                stop_loss_distances={"SOXL": 0.05, "BOXX": 0.05},
                drawdown_scalar=1.0,
                inputs_fresh=True,
            )

        assert result.assessment.outcome == "REJECT"
        assert reason_code in result.assessment.reason_codes
        assert result.decision.positions == ()
        assert result.decision.budgets == ()
        engine.assess.assert_called_once()

    assert builder.call_count == len(cases)
    legacy_gate.assert_not_called()
    monitor.assert_not_called()


def test_soxl_promotion_research_builder_exception_fails_closed_and_assesses_once(
    monkeypatch,
) -> None:
    candidate = _soxl_candidate()
    engine = Mock()
    engine.assess.return_value = RiskAction(action="approve", reason="passed")
    monitor = Mock()
    legacy_gate = Mock()
    monkeypatch.setattr(
        entrypoints,
        "_build_soxl_soxx_trend_income_decision",
        Mock(side_effect=RuntimeError("private input detail")),
    )
    monkeypatch.setattr(entrypoints, "record_strategy_decision", monitor)
    monkeypatch.setattr(entrypoints, "apply_risk_gate", legacy_gate)

    with (
        patch("quant_platform_kit.risk.gate._utc_now", return_value=_SOXL_NOW),
        patch("quant_platform_kit.risk.gate.build_risk_engine", return_value=engine),
    ):
        result = entrypoints.evaluate_soxl_soxx_trend_income_promotion_research(
            _soxl_context(),
            candidate_identity=candidate,
            mandate_provenance=_soxl_mandate(candidate),
            stop_loss_distances={"SOXL": 0.05, "BOXX": 0.05},
            drawdown_scalar=1.0,
            inputs_fresh=True,
        )

    assert result.assessment.outcome == "REJECT"
    assert "invalid_scope" in result.assessment.reason_codes
    assert result.decision.positions == ()
    assert result.decision.budgets == ()
    assert "private input detail" not in repr(result)
    engine.assess.assert_called_once()
    legacy_gate.assert_not_called()
    monitor.assert_not_called()


def test_apply_risk_gate_enriches_stop_loss_diagnostics_from_portfolio() -> None:
    snapshot = PortfolioSnapshot(
        as_of=datetime(2026, 7, 9, tzinfo=timezone.utc),
        total_equity=1000.0,
        positions=(
            Position(symbol="SPY", quantity=10.0, market_value=700.0, average_cost=100.0),
        ),
        metadata={"consecutive_losses": 2},
    )
    ctx = StrategyContext(as_of=snapshot.as_of, portfolio=snapshot, market_data={}, state={}, runtime_config={})
    decision = StrategyDecision(positions=(PositionTarget(symbol="SPY", target_weight=0.5),))
    result = apply_risk_gate(decision, ctx=ctx)
    assert result.positions == ()
    assert "rejected:stop_loss" in result.risk_flags


def test_apply_risk_gate_passes_bootstrap_mandate_parameters_to_qpk(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def _gate(decision, **kwargs):
        captured.update(kwargs)
        return decision

    monkeypatch.setattr(common, "_qpk_apply_risk_gate", _gate)
    decision = StrategyDecision(
        positions=(PositionTarget(symbol="SPY", target_weight=0.20),)
    )

    result = apply_risk_gate(
        decision,
        risk_mandate_id="bootstrap_small_account_v2",
        product_leverage_factors={"SPY": 1},
        available_account_exposure=0.50,
    )

    assert result is decision
    assert captured["risk_mandate_id"] == "bootstrap_small_account_v2"
    assert captured["product_leverage_factors"] == {"SPY": 1}
    assert captured["available_account_exposure"] == 0.50


def test_unmandated_consumer_allows_only_explicit_1x_single_position_at_ten_percent() -> None:
    result = apply_risk_gate(
        StrategyDecision(
            positions=(PositionTarget(symbol="SPY", target_weight=0.10),),
        ),
        product_leverage_factors={"SPY": 1},
        max_single_weight=1.0,
        portfolio_snapshot={"total_equity": 100_000.0},
    )

    assert result.positions == (
        PositionTarget(symbol="SPY", target_weight=0.10),
    )
    assert result.budgets == ()
    assert result.risk_flags == ("risk_gate:passed",)
    assert result.diagnostics["risk_gate"] == "APPROVE"


def test_unmandated_consumer_fail_closed_matrix() -> None:
    single = (PositionTarget(symbol="SPY", target_weight=0.10),)
    cases = (
        (single, {"SPY": 1}, None, ("rejected:risk_engine",)),
        (single, {"SPY": 1}, {"total_equity": float("nan")}, ("rejected:risk_engine",)),
        (
            (PositionTarget(symbol="SPY", target_weight=0.11),),
            {"SPY": 1},
            {"total_equity": 100_000.0},
            ("rejected:concentration",),
        ),
        (
            (
                PositionTarget(symbol="SPY", target_weight=0.05),
                PositionTarget(symbol="BOXX", target_weight=0.05),
            ),
            {"SPY": 1, "BOXX": 1},
            {"total_equity": 100_000.0},
            ("rejected:too_many_positions",),
        ),
        (
            single,
            None,
            {"total_equity": 100_000.0},
            ("rejected:leverage_classification",),
        ),
        (
            single,
            {"QQQ": 1},
            {"total_equity": 100_000.0},
            ("rejected:leverage_classification",),
        ),
        (
            single,
            {"SPY": 2},
            {"total_equity": 100_000.0},
            ("rejected:leverage_classification",),
        ),
    )

    for positions, leverage_factors, snapshot, expected_flags in cases:
        result = apply_risk_gate(
            StrategyDecision(positions=positions),
            product_leverage_factors=leverage_factors,
            max_single_weight=1.0,
            portfolio_snapshot=snapshot,
        )

        assert result.positions == ()
        assert result.budgets == ()
        assert result.risk_flags == expected_flags
        assert result.diagnostics["risk_gate"] == "REJECT"
