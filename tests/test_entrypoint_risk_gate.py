from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest

from quant_platform_kit.common.models import PortfolioSnapshot, Position
from quant_platform_kit.risk.contracts import CandidateRiskIdentity, RiskAction
from quant_platform_kit.common.strategy_contracts import PositionTarget, StrategyContext, StrategyDecision

import us_equity_strategies.entrypoints as entrypoints
import us_equity_strategies.entrypoints._common as common
from us_equity_strategies.entrypoints._common import apply_risk_gate


_SOXL_ASSETS = ("SOXL", "SOXX", "BOXX", "SCHD", "DGRO", "SGOV", "SPYI", "QQQI", "QQQ")
_SOXL_NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
_TQQQ_CORE_ASSETS = ("TQQQ", "QQQM", "BOXX")
_TQQQ_NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def _tqqq_core_runtime_config(**overrides: object) -> dict[str, object]:
    config: dict[str, object] = {
        "benchmark_symbol": "QQQ",
        "managed_symbols": _TQQQ_CORE_ASSETS,
        "signal_effective_after_trading_days": 1,
        "dual_drive_unlevered_symbol": "QQQM",
        "income_layer_enabled": False,
        "option_overlay_enabled": False,
        "option_growth_overlay_enabled": False,
        "option_income_overlay_enabled": False,
        "ai_extensions": {"enabled": False},
        "dual_drive_volatility_delever_retention_mode": "none",
        "dual_drive_volatility_delever_retention_ratio": 0.0,
        "dual_drive_volatility_delever_taco_veto_enabled": False,
        "dual_drive_macro_risk_governor_enabled": False,
        "dual_drive_crisis_defense_enabled": False,
        "market_regime_control_enabled": False,
    }
    config.update(overrides)
    return config


def _tqqq_candidate() -> CandidateRiskIdentity:
    return CandidateRiskIdentity(
        strategy_profile="tqqq_core_parity_v1",
        account_mode="single_strategy_account_v1",
        strategy_revision="1" * 40,
        runner_revision="2" * 40,
        config_sha256="3" * 64,
        input_manifest_sha256="4" * 64,
        authority_receipt_sha256="5" * 64,
    )


def _tqqq_mandate(candidate: CandidateRiskIdentity) -> dict[str, object]:
    return {
        "mandate_id": "tqqq_core_parity_v1",
        "mandate_version": "2026-08-11.1",
        "authority_receipt_sha256": candidate.authority_receipt_sha256,
        "authority_scope": "RESEARCH_ONLY",
        "strategy_profile": candidate.strategy_profile,
        "account_mode": candidate.account_mode,
        "strategy_revision": candidate.strategy_revision,
        "runner_revision": candidate.runner_revision,
        "config_sha256": candidate.config_sha256,
        "input_manifest_sha256": candidate.input_manifest_sha256,
        "candidate_identity_sha256": candidate.candidate_sha256,
        "effective_at": "2026-08-11T11:59:00Z",
        "expires_at": "2026-08-11T12:01:00Z",
        "max_snapshot_age_seconds": 300,
        "effective_exposure_cap": 0.50,
        "loss_budget": 0.01,
        "product_caps": {"TQQQ": 0.15, "QQQM": 0.50, "BOXX": 0.50},
        "nominal_caps": {"TQQQ": 0.15, "QQQM": 0.50, "BOXX": 0.50},
        "product_effective_caps": {"TQQQ": 0.45, "QQQM": 0.50, "BOXX": 0.50},
        "product_leverage_factors": {"TQQQ": 3, "QQQM": 1, "BOXX": 1},
        "allowed_nonzero_assets": list(_TQQQ_CORE_ASSETS),
        "source_revision": "730ad9f3983bd90cd75adecb67fcf483ffb96736",
    }


def _tqqq_context(*, runtime_config: dict[str, object] | None = None) -> StrategyContext:
    snapshot = PortfolioSnapshot(
        as_of=_TQQQ_NOW,
        total_equity=100_000.0,
        buying_power=100_000.0,
        cash_balance=100_000.0,
        positions=(),
        metadata={"observed_effective_exposure": 0.0},
    )
    history = tuple(
        {
            "close": 100.0 + index,
            "high": 101.0 + index,
            "low": 99.0 + index,
        }
        for index in range(260)
    )
    return StrategyContext(
        as_of=_TQQQ_NOW,
        portfolio=snapshot,
        market_data={"benchmark_history": history},
        runtime_config=runtime_config or _tqqq_core_runtime_config(),
    )


def test_tqqq_core_parity_uses_real_builder_and_assesses_exactly_once(monkeypatch) -> None:
    candidate = _tqqq_candidate()
    engine = Mock()
    engine.assess.return_value = RiskAction(action="approve", reason="passed")
    legacy_gate = Mock()
    monitor = Mock()
    monkeypatch.setattr(entrypoints, "apply_risk_gate", legacy_gate)
    monkeypatch.setattr(entrypoints, "record_strategy_decision", monitor)

    with (
        patch("quant_platform_kit.risk.gate._utc_now", return_value=_TQQQ_NOW),
        patch("quant_platform_kit.risk.gate.build_risk_engine", return_value=engine),
    ):
        result = entrypoints.evaluate_tqqq_growth_income_promotion_research(
            _tqqq_context(),
            candidate_identity=candidate,
            mandate_provenance=_tqqq_mandate(candidate),
            stop_loss_distances={symbol: 0.05 for symbol in _TQQQ_CORE_ASSETS},
            drawdown_scalar=1.0,
            inputs_fresh=True,
        )

    assert result.assessment.outcome == "APPROVE"
    assert result.assessment.execution_authorized is False
    assert {position.symbol for position in result.decision.positions} == set(_TQQQ_CORE_ASSETS)
    assert result.decision.diagnostics["tqqq_core_parity_research"] is True
    assert result.decision.diagnostics["option_overlay_enabled"] is False
    assert not {"SCHD", "DGRO", "SGOV", "SPYI", "QQQI"} & {
        position.symbol for position in result.decision.positions
    }
    engine.assess.assert_called_once()
    legacy_gate.assert_not_called()
    monitor.assert_not_called()


def test_tqqq_core_parity_rejects_non_research_mandate_after_one_assessment() -> None:
    candidate = _tqqq_candidate()
    mandate = _tqqq_mandate(candidate)
    mandate["authority_scope"] = "LIVE"
    engine = Mock()
    engine.assess.return_value = RiskAction(action="approve", reason="passed")

    with (
        patch("quant_platform_kit.risk.gate._utc_now", return_value=_TQQQ_NOW),
        patch("quant_platform_kit.risk.gate.build_risk_engine", return_value=engine),
    ):
        result = entrypoints.evaluate_tqqq_growth_income_promotion_research(
            _tqqq_context(),
            candidate_identity=candidate,
            mandate_provenance=mandate,
            stop_loss_distances={symbol: 0.05 for symbol in _TQQQ_CORE_ASSETS},
            drawdown_scalar=1.0,
            inputs_fresh=True,
        )

    assert result.assessment.outcome == "REJECT"
    assert result.assessment.execution_authorized is False
    assert result.decision.positions == ()
    engine.assess.assert_called_once()


@pytest.mark.parametrize(
    "runtime_config",
    (
        {"managed_symbols": _TQQQ_CORE_ASSETS},
        {
            "managed_symbols": _TQQQ_CORE_ASSETS,
            "signal_effective_after_trading_days": 1,
            "income_layer_enabled": False,
            "option_overlay_enabled": True,
            "option_growth_overlay_enabled": False,
            "option_income_overlay_enabled": False,
        },
        {
            "managed_symbols": ("TQQQ", "BOXX"),
            "signal_effective_after_trading_days": 1,
            "income_layer_enabled": False,
            "option_overlay_enabled": False,
            "option_growth_overlay_enabled": False,
            "option_income_overlay_enabled": False,
        },
    ),
)
def test_tqqq_core_parity_invalid_overrides_fail_closed_after_one_assessment(
    monkeypatch,
    runtime_config,
) -> None:
    candidate = _tqqq_candidate()
    engine = Mock()
    engine.assess.return_value = RiskAction(action="approve", reason="passed")

    with (
        patch("quant_platform_kit.risk.gate._utc_now", return_value=_TQQQ_NOW),
        patch("quant_platform_kit.risk.gate.build_risk_engine", return_value=engine),
    ):
        result = entrypoints.evaluate_tqqq_growth_income_promotion_research(
            _tqqq_context(runtime_config=runtime_config),
            candidate_identity=candidate,
            mandate_provenance=_tqqq_mandate(candidate),
            stop_loss_distances={symbol: 0.05 for symbol in _TQQQ_CORE_ASSETS},
            drawdown_scalar=1.0,
            inputs_fresh=True,
        )

    assert result.assessment.outcome == "REJECT"
    assert result.decision.positions == ()
    assert result.decision.budgets == ()
    assert "invalid_scope" in result.assessment.reason_codes
    engine.assess.assert_called_once()


@pytest.mark.parametrize(
    ("key", "value"),
    (
        ("dual_drive_volatility_delever_retention_mode", "environment"),
        ("dual_drive_volatility_delever_retention_ratio", 0.25),
        ("dual_drive_volatility_delever_taco_veto_enabled", True),
        ("dual_drive_macro_risk_governor_enabled", True),
        ("dual_drive_crisis_defense_enabled", True),
        ("market_regime_control_enabled", True),
    ),
)
def test_tqqq_core_parity_rejects_reenabled_p2_components_after_one_assessment(
    key: str,
    value: object,
) -> None:
    candidate = _tqqq_candidate()
    runtime_config = _tqqq_core_runtime_config(**{key: value})
    engine = Mock()
    engine.assess.return_value = RiskAction(action="approve", reason="passed")

    with (
        patch("quant_platform_kit.risk.gate._utc_now", return_value=_TQQQ_NOW),
        patch("quant_platform_kit.risk.gate.build_risk_engine", return_value=engine),
    ):
        result = entrypoints.evaluate_tqqq_growth_income_promotion_research(
            _tqqq_context(runtime_config=runtime_config),
            candidate_identity=candidate,
            mandate_provenance=_tqqq_mandate(candidate),
            stop_loss_distances={symbol: 0.05 for symbol in _TQQQ_CORE_ASSETS},
            drawdown_scalar=1.0,
            inputs_fresh=True,
        )

    assert result.assessment.outcome == "REJECT"
    assert result.decision.positions == ()
    assert "invalid_scope" in result.assessment.reason_codes
    engine.assess.assert_called_once()


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
        "source_revision": "9618b4bd8e179760ac174914713598762cab15d7",
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


def _soxl_preinception_decision() -> StrategyDecision:
    return StrategyDecision(
        positions=(
            PositionTarget(symbol="SOXL", target_value=70_000.0),
            PositionTarget(symbol="BOXX", target_value=20_000.0, role="safe_haven"),
            PositionTarget(symbol="QQQI", target_value=10_000.0, role="income"),
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
            point_in_time_eligible_assets=frozenset(_SOXL_ASSETS),
            qqqi_preinception_fallback_symbol="QQQ",
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
                point_in_time_eligible_assets=frozenset(_SOXL_ASSETS),
                qqqi_preinception_fallback_symbol="QQQ",
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
            point_in_time_eligible_assets=frozenset(_SOXL_ASSETS),
            qqqi_preinception_fallback_symbol="QQQ",
        )

    assert result.assessment.outcome == "REJECT"
    assert "invalid_scope" in result.assessment.reason_codes
    assert result.decision.positions == ()
    assert result.decision.budgets == ()
    assert "private input detail" not in repr(result)
    engine.assess.assert_called_once()
    legacy_gate.assert_not_called()
    monitor.assert_not_called()


@pytest.mark.parametrize(
    ("fallback_symbol", "expected"),
    (
        ("QQQ", {"SOXL": 0.15, "QQQ": 0.021428571428571432}),
        (None, {"SOXL": 0.15}),
    ),
)
def test_soxl_promotion_research_keeps_unavailable_targets_as_cash(
    monkeypatch,
    fallback_symbol,
    expected,
) -> None:
    candidate = _soxl_candidate()
    engine = Mock()
    engine.assess.return_value = RiskAction(action="approve", reason="passed")
    monkeypatch.setattr(
        entrypoints,
        "_build_soxl_soxx_trend_income_decision",
        Mock(return_value=_soxl_preinception_decision()),
    )

    with (
        patch("quant_platform_kit.risk.gate._utc_now", return_value=_SOXL_NOW),
        patch("quant_platform_kit.risk.gate.build_risk_engine", return_value=engine),
    ):
        result = entrypoints.evaluate_soxl_soxx_trend_income_promotion_research(
            _soxl_context(),
            candidate_identity=candidate,
            mandate_provenance=_soxl_mandate(candidate),
            stop_loss_distances={
                symbol: 0.05 for symbol in {"SOXL", "SOXX", "SCHD", "DGRO", "QQQ"}
            },
            drawdown_scalar=1.0,
            inputs_fresh=True,
            point_in_time_eligible_assets=frozenset({"SOXL", "SOXX", "SCHD", "DGRO", "QQQ"}),
            qqqi_preinception_fallback_symbol=fallback_symbol,
        )

    assert result.assessment.outcome == "APPROVE"
    assert {position.symbol: position.target_weight for position in result.decision.positions} == pytest.approx(
        expected
    )
    assert not {"BOXX", "QQQI"} & {position.symbol for position in result.decision.positions}
    assert result.decision.diagnostics["promotion_research_ineligible_assets_to_cash"] == (
        ("BOXX",) if fallback_symbol == "QQQ" else ("BOXX", "QQQI")
    )
    engine.assess.assert_called_once()


@pytest.mark.parametrize(
    ("eligible_assets", "fallback_symbol", "raw_decision"),
    (
        (None, "QQQ", _soxl_preinception_decision()),
        (frozenset({"SOXL", "QQQ", "SPY"}), "QQQ", _soxl_preinception_decision()),
        (frozenset({"SOXL", "QQQ"}), "SPY", _soxl_preinception_decision()),
        (frozenset({"SOXL"}), "QQQ", _soxl_preinception_decision()),
        (
            frozenset(_SOXL_ASSETS),
            "QQQ",
            StrategyDecision(positions=(PositionTarget(symbol="QQQ", target_value=10_000.0),)),
        ),
    ),
)
def test_soxl_promotion_research_invalid_eligibility_fails_closed_and_assesses_once(
    monkeypatch,
    eligible_assets,
    fallback_symbol,
    raw_decision,
) -> None:
    candidate = _soxl_candidate()
    engine = Mock()
    engine.assess.return_value = RiskAction(action="approve", reason="passed")
    monkeypatch.setattr(
        entrypoints,
        "_build_soxl_soxx_trend_income_decision",
        Mock(return_value=raw_decision),
    )

    with (
        patch("quant_platform_kit.risk.gate._utc_now", return_value=_SOXL_NOW),
        patch("quant_platform_kit.risk.gate.build_risk_engine", return_value=engine),
    ):
        result = entrypoints.evaluate_soxl_soxx_trend_income_promotion_research(
            _soxl_context(),
            candidate_identity=candidate,
            mandate_provenance=_soxl_mandate(candidate),
            stop_loss_distances={symbol: 0.05 for symbol in _SOXL_ASSETS},
            drawdown_scalar=1.0,
            inputs_fresh=True,
            point_in_time_eligible_assets=eligible_assets,
            qqqi_preinception_fallback_symbol=fallback_symbol,
        )

    assert result.assessment.outcome == "REJECT"
    assert result.decision.positions == ()
    assert result.decision.budgets == ()
    engine.assess.assert_called_once()


def test_soxl_promotion_research_missing_eligibility_fails_closed_and_assesses_once(
    monkeypatch,
) -> None:
    candidate = _soxl_candidate()
    engine = Mock()
    engine.assess.return_value = RiskAction(action="approve", reason="passed")
    monkeypatch.setattr(
        entrypoints,
        "_build_soxl_soxx_trend_income_decision",
        Mock(return_value=_soxl_preinception_decision()),
    )

    with (
        patch("quant_platform_kit.risk.gate._utc_now", return_value=_SOXL_NOW),
        patch("quant_platform_kit.risk.gate.build_risk_engine", return_value=engine),
    ):
        result = entrypoints.evaluate_soxl_soxx_trend_income_promotion_research(
            _soxl_context(),
            candidate_identity=candidate,
            mandate_provenance=_soxl_mandate(candidate),
            stop_loss_distances={symbol: 0.05 for symbol in _SOXL_ASSETS},
            drawdown_scalar=1.0,
            inputs_fresh=True,
        )

    assert result.assessment.outcome == "REJECT"
    assert result.decision.positions == ()
    assert result.decision.budgets == ()
    engine.assess.assert_called_once()


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


def test_apply_risk_gate_forwards_only_explicit_capital_base_evidence(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def _gate(decision, **kwargs):
        captured.update(kwargs)
        return decision

    capital_base = {"reported_equity": 100_000.0}
    capital_base_binding = {"strategy_scope": "soxl_soxx_trend_income"}
    ctx = StrategyContext(
        as_of=datetime(2026, 7, 9, tzinfo=timezone.utc),
        portfolio=None,
        market_data={},
        state={},
        runtime_config={},
        capabilities={
            "capital_base": capital_base,
            "capital_base_binding": capital_base_binding,
        },
    )
    monkeypatch.setattr(common, "_qpk_apply_risk_gate", _gate)
    decision = StrategyDecision(
        positions=(PositionTarget(symbol="SOXL", target_value=10_000.0),)
    )

    assert apply_risk_gate(
        decision,
        ctx=ctx,
        enforce_value_target_exposure=True,
    ) is decision
    assert captured["capital_base"] is capital_base
    assert captured["capital_base_binding"] is capital_base_binding
    assert captured["enforce_value_target_exposure"] is True


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
