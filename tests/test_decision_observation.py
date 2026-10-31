from tests.candidate_dependency_guard import require_candidate_dependencies
require_candidate_dependencies({'quant_platform_kit.common.decision_consumption_receipts': ('build_decision_consumption_receipt',)})

from datetime import datetime, timezone
from dataclasses import replace

import pytest
from quant_platform_kit.common.models import PortfolioSnapshot
from quant_platform_kit.common.strategy_contracts import StrategyContext
from us_equity_strategies.entrypoints import compute_tqqq_growth_income_decision, evaluate_soxl_soxx_trend_income
from us_equity_strategies.decision_observation import consumer_payload_sha256


def context(profile, *, payload_match=True):
    clock = datetime.now(timezone.utc).replace(microsecond=0)
    mrc = {"plugin": "market_regime_control", "canonical_route": "risk_off",
           "strategy_runtime_metadata_non_authoritative": True,
           "execution_controls": {"position_control_allowed": False, "consumption_evidence_status": "shadow_only"}}
    portfolio = PortfolioSnapshot(as_of=clock, total_equity=100000.0, buying_power=100000.0,
                                  metadata={"market_regime_control": mrc})
    data = ({"benchmark_history": [{"close": 100+i*.5} for i in range(260)]}
            if profile == "tqqq_growth_income" else {"derived_indicators": {
                "soxl": {"price": 80.0, "ma_trend": 75.0},
                "soxx": {"price": 80.0, "ma_trend": 75.0, "realized_volatility_10": .2}}})
    observation = {"reference": "gs://signals/market-regime.json", "consumer_key": "market_regime_control", "status": "observed", "artifact_sha256": "a"*64,
                   "digest_status": "verified", "availability_at": clock, "observed_at": clock,
                   "consumer_payload_sha256": consumer_payload_sha256(mrc) if payload_match else "b"*64}
    cap = {"run_id": "synthetic-run", "decision_at": clock, "strategy_revision": None,
           "config_sha256": None, "observations": [observation]}
    return StrategyContext(as_of=clock.date().isoformat(), portfolio=portfolio, market_data=data,
        runtime_config={"income_layer_enabled": False}, capabilities={"logical_evaluation_time": clock,
        "decision_consumption_receipt": cap})


@pytest.mark.parametrize("profile", ["tqqq_growth_income", "soxl_soxx_trend_income"])
def test_actual_entrypoint_records_denied_observation_and_real_targets(profile):
    ctx = context(profile)
    evaluate = compute_tqqq_growth_income_decision if profile == "tqqq_growth_income" else evaluate_soxl_soxx_trend_income
    result = evaluate(ctx)
    assert result.diagnostics["decision_consumption_receipt_status"] == "recorded"
    receipt = result.diagnostics["decision_consumption_receipt"]
    assert receipt["signal_session"] == ctx.as_of
    assert receipt["run_id"] == "synthetic-run"
    assert receipt["pre_risk_target_sha256"] and receipt["post_risk_target_sha256"]
    assert receipt["execution_receipt_sha256"] is None
    assert receipt["observations"][0]["status"] == "observed"
    assert receipt["observations"][0]["authority"] == "denied"


def test_unbound_loader_artifact_is_not_claimed_consumed():
    result = compute_tqqq_growth_income_decision(context("tqqq_growth_income", payload_match=False))
    row = result.diagnostics["decision_consumption_receipt"]["observations"][0]
    assert row["status"] == "unavailable" and row["artifact_sha256"] is None and row["authority"] == "unknown"


def test_missing_explicit_clock_is_observable_without_changing_targets():
    ctx = context("tqqq_growth_income")
    no_clock = replace(ctx, capabilities={"decision_consumption_receipt": ctx.capabilities["decision_consumption_receipt"]})
    result = compute_tqqq_growth_income_decision(no_clock)
    default = compute_tqqq_growth_income_decision(replace(ctx, capabilities={}))
    assert result.positions == default.positions
    assert result.diagnostics["decision_consumption_receipt_status"] == "unavailable"
    assert "decision_consumption_receipt" not in result.diagnostics


def test_authorized_observation_is_derived_from_consumer_not_loader():
    ctx = context("tqqq_growth_income")
    mrc = {"plugin": "market_regime_control", "canonical_route": "risk_reduced",
           "execution_controls": {"position_control_allowed": True, "consumption_evidence_status": "automation_approved"},
           "position_control": {"final_route": "risk_reduced", "risk_asset_scalar": .8, "leverage_scalar": .8}}
    portfolio = replace(ctx.portfolio, metadata={"market_regime_control": mrc})
    cap = dict(ctx.capabilities["decision_consumption_receipt"])
    cap["observations"] = [{**cap["observations"][0], "consumer_payload_sha256": consumer_payload_sha256(mrc),
                            "authority": "denied"}]
    updated = replace(ctx, portfolio=portfolio, capabilities={**ctx.capabilities, "decision_consumption_receipt": cap})
    receipt = compute_tqqq_growth_income_decision(updated).diagnostics["decision_consumption_receipt"]
    assert receipt["observations"][0]["authority"] == "authorized"


def test_old_qpk_gate_without_clock_is_safe_and_observable(monkeypatch):
    from us_equity_strategies import decision_observation
    ctx = context("tqqq_growth_income")
    original = compute_tqqq_growth_income_decision(ctx)
    monkeypatch.setattr(decision_observation, "_qpk_apply_risk_gate", lambda decision: decision)
    result = decision_observation.attach_optional_decision_receipt(ctx, original, original, profile="tqqq_growth_income")
    assert result.positions == original.positions
    assert result.diagnostics["decision_consumption_receipt_status"] == "unavailable"
    assert "decision_consumption_receipt" not in result.diagnostics


def test_soxl_receipt_binds_actual_portfolio_metadata_not_competing_artifact():
    ctx = context("soxl_soxx_trend_income")
    attached = ctx.portfolio.metadata["market_regime_control"]
    competing = {"plugin": "market_regime_control", "canonical_route": "risk_reduced",
        "execution_controls": {"position_control_allowed": True, "consumption_evidence_status": "automation_approved"}}
    cap = dict(ctx.capabilities["decision_consumption_receipt"])
    cap["observations"] = [{**cap["observations"][0], "consumer_payload_sha256": consumer_payload_sha256(competing)}]
    changed = replace(ctx, artifacts={"market_regime_control": competing}, capabilities={**ctx.capabilities,
        "decision_consumption_receipt": cap})
    row = evaluate_soxl_soxx_trend_income(changed).diagnostics["decision_consumption_receipt"]["observations"][0]
    assert row["status"] == "unavailable" and row["authority"] == "unknown"
    cap["observations"] = [{**cap["observations"][0], "consumer_payload_sha256": consumer_payload_sha256(attached)}]
    changed = replace(changed, capabilities={**ctx.capabilities, "decision_consumption_receipt": cap})
    row = evaluate_soxl_soxx_trend_income(changed).diagnostics["decision_consumption_receipt"]["observations"][0]
    assert row["status"] == "observed" and row["authority"] == "denied"
