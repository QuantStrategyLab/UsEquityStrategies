from tests.candidate_dependency_guard import require_candidate_dependencies
require_candidate_dependencies({'quant_platform_kit.risk.gate.replay': ('replay_apply_risk_gate',)})

from datetime import datetime, timezone

import pytest
from quant_platform_kit.common.models import PortfolioSnapshot
from quant_platform_kit.common.strategy_contracts import StrategyContext
from tests.test_production_decision_replay import fixture
from us_equity_strategies.research.range_rsi2_forward import (
    aggregate_research_contributions, evaluate_production_trend_fixture, validate_forward_observation, build_research_forward_receipt,
)
from us_equity_strategies.research.range_rsi2_study import FROZEN_PREREGISTRATION_SHA256


@pytest.mark.parametrize("profile", ["tqqq_growth_income", "soxl_soxx_trend_income"])
def test_actual_production_trend_fixture_plus_research_sleeve(profile):
    frozen = fixture(profile)
    data = frozen.frozen_input
    market = ({"benchmark_history": [{"close": item["close"]} for item in data["benchmark_history"]]}
              if profile == "tqqq_growth_income" else {"derived_indicators": data["derived_indicators"]})
    portfolio = PortfolioSnapshot(as_of=datetime.fromisoformat(frozen.decision_at), total_equity=100000.0,
        buying_power=100000.0, positions=(), metadata={"market_currency_cash": 100000.0})
    ctx = StrategyContext(as_of=data["session"], market_data=market, portfolio=portfolio, runtime_config=frozen.config)
    without = evaluate_production_trend_fixture(ctx, profile=profile, mr_active=False, risk_cap=1.0)
    with_mr = evaluate_production_trend_fixture(ctx, profile=profile, mr_active=True, risk_cap=.8)
    assert with_mr.trend == without.trend
    assert sum(with_mr.risk_final.values()) + with_mr.cash_weight == pytest.approx(1.0)
    assert sum(v for k, v in with_mr.risk_final.items() if k != "BOXX") <= .8 + 1e-12
    assert sum(with_mr.mean_reversion.values()) <= .10
    assert not with_mr.promotion_eligible


def test_sleeve_does_not_exceed_available_cash():
    result = aggregate_research_contributions({"TQQQ": .8, "BOXX": .19}, leveraged_symbol="TQQQ", mr_active=True, risk_cap=1)
    assert result.mean_reversion["TQQQ"] == pytest.approx(.10)
    assert result.mean_reversion["BOXX"] == pytest.approx(-.09)
    assert sum(result.risk_final.values()) + result.cash_weight == pytest.approx(1)


def policy():
    from quant_platform_kit.strategy_lifecycle.forward_observation import ForwardObservationPolicy
    return ForwardObservationPolicy(candidate_id="trend_mr_10", strategy_profile="tqqq_growth_income",
        domain="us_equity", benchmark_symbol="QQQ", required_trading_sessions=20,
        review_milestones=(10,), automatic_non_live_modes=("shadow", "paper"), auto_resume_clean_sessions=2,
        observation_calendar="XNYS", observation_window_type="fixed", observation_start_session="2026-11-02",
        window_rationale_ref="sha256:synthetic-frozen-policy", non_live_evidence_modes=("shadow_decision", "simulated_replay"))


def evidence():
    from quant_platform_kit.strategy_lifecycle.forward_observation_receipt import forward_observation_policy_sha256
    registration = {"registration_id": "forward-1", "registered_at": "2026-10-31T12:00:00Z",
        "candidate_id": "trend_mr_10", "candidate_sha256": "a"*64, "rules_sha256": FROZEN_PREREGISTRATION_SHA256,
        "source_sha256": "b"*64, "config_sha256": "c"*64, "forward_start": "2026-11-02",
        "forward_end": "2026-12-01", "max_age_seconds": 3600, "evidence": {"ref": "independent"},
        "policy_sha256": forward_observation_policy_sha256(policy()), "strategy_profile": "tqqq_growth_income",
        "dependency_digests": {key: "f"*64 for key in ("p1_manifest", "p2_config", "p3_evidence", "risk_policy", "strategy_release", "plugin_bundle")},
        "evidence_modes": ["shadow_decision", "simulated_replay"]}
    observation = {key: registration[key] for key in ("registration_id", "candidate_id", "candidate_sha256", "rules_sha256", "source_sha256", "config_sha256")}
    observation.update(policy_sha256=registration["policy_sha256"], dependency_digests=registration["dependency_digests"],
                       evidence_modes=registration["evidence_modes"])
    observation.update(signal_session="2026-11-02", available_at="2026-11-02T20:30:00Z",
        decision_at="2026-11-02T21:00:00Z", observed_at="2026-11-02T21:00:01Z", input_sha256="d"*64,
        data_status="ready", risk_status="pass", shadow_status="healthy", paper_status="healthy",
        observation_index=1, previous_receipt_sha256=None)
    return registration, observation


def test_forward_evidence_requires_trusted_prospective_fresh_identity():
    registration, observation = evidence()
    now = datetime(2026, 11, 2, 22, tzinfo=timezone.utc)
    assert validate_forward_observation(registration, observation, now=now)["status"] == "BLOCKED"
    verify = lambda kind, item: True
    accepted = validate_forward_observation(registration, observation, now=now, verifier=verify)
    assert accepted["status"] == "OBSERVED_RESEARCH_ONLY" and accepted["promotion_status"] == "NOT_QUALIFIED"
    for changes in ({"available_at": "2026-11-02T19:00:00Z"}, {"config_sha256": "e"*64},
                    {"paper_status": "unknown"}, {"decision_at": "2026-10-30T21:00:00Z"}):
        assert validate_forward_observation(registration, {**observation, **changes}, now=now, verifier=verify)["status"] == "BLOCKED"
    assert validate_forward_observation({**registration, "forward_start": "2026-10-30"}, observation,
                                        now=now, verifier=verify)["status"] == "BLOCKED"
    assert validate_forward_observation({**registration, "max_age_seconds": 9999999},
        {**observation, "available_at": "2026-10-30T20:00:00Z"}, now=now, verifier=verify)["status"] == "BLOCKED"


def test_qpk_forward_receipt_requires_valid_frozen_policy_and_chain():
    registration, observation = evidence()
    now = datetime(2026, 11, 2, 22, tzinfo=timezone.utc)
    verify = lambda kind, item: True
    first = build_research_forward_receipt(registration, observation, policy=policy(), observation_index=1,
                                           previous_receipt=None, now=now, verifier=verify)
    assert first["status"] == "OBSERVED_RESEARCH_ONLY" and first["receipt"]["observation_index"] == 1
    assert first["promotion_status"] == "NOT_QUALIFIED" and not first["execution_authorized"]
    changed = build_research_forward_receipt(registration, {**observation, "input_sha256": "e"*64},
        policy=policy(), observation_index=1, previous_receipt=None, now=now, verifier=verify)
    assert changed["receipt"] == first["receipt"]
    assert changed["observation_binding"]["binding_sha256"] != first["observation_binding"]["binding_sha256"]
    wrong_index = build_research_forward_receipt(registration, observation, policy=policy(), observation_index=2,
                                                 previous_receipt=None, now=now, verifier=verify)
    assert wrong_index["status"] == "BLOCKED" and wrong_index["receipt"] is None
    from dataclasses import replace
    wrong_policy = build_research_forward_receipt(registration, observation,
        policy=replace(policy(), benchmark_symbol="SOXX"), observation_index=1, previous_receipt=None, now=now, verifier=verify)
    assert wrong_policy["status"] == "BLOCKED" and wrong_policy["receipt"] is None
    blocked = build_research_forward_receipt(registration, {**observation, "risk_status": "unknown"}, policy=policy(),
        observation_index=1, previous_receipt=None, now=now, verifier=verify)
    assert blocked["status"] == "BLOCKED" and blocked["receipt"] is None
    wrong_dependency = build_research_forward_receipt(registration, {**observation, "dependency_digests": {}},
        policy=policy(), observation_index=1, previous_receipt=None, now=now, verifier=verify)
    assert wrong_dependency["status"] == "BLOCKED" and wrong_dependency["receipt"] is None
    next_obs = {**observation, "signal_session": "2026-11-03", "available_at": "2026-11-03T20:30:00Z",
        "decision_at": "2026-11-03T21:00:00Z", "observed_at": "2026-11-03T21:00:01Z", "observation_index": 2,
        "previous_receipt_sha256": first["receipt"]["receipt_sha256"],
        "previous_binding_sha256": first["observation_binding"]["binding_sha256"]}
    second = build_research_forward_receipt(registration, next_obs, policy=policy(), observation_index=2,
        previous_receipt=first["receipt"], previous_binding=first["observation_binding"],
        now=datetime(2026,11,3,22,tzinfo=timezone.utc), verifier=verify)
    assert second["receipt"]["previous_receipt_sha256"] == first["receipt"]["receipt_sha256"]
    spliced = build_research_forward_receipt({**registration, "evidence": {"ref": "different"}}, next_obs,
        policy=policy(), observation_index=2, previous_receipt=first["receipt"], previous_binding=first["observation_binding"],
        now=datetime(2026,11,3,22,tzinfo=timezone.utc), verifier=verify)
    assert spliced["status"] == "BLOCKED" and spliced["receipt"] is None
