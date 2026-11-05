from dataclasses import replace
from datetime import datetime, timezone

import pytest

from tests.candidate_dependency_guard import require_candidate_dependencies
require_candidate_dependencies({"quant_platform_kit.common.execution_state": ("ConditionalPlanningHandle",)})

from quant_platform_kit.common.account_domain import account_domain_key
from quant_platform_kit.common.account_identity import BrokerAccountIdentity
from quant_platform_kit.common.execution_state import AccountSubmissionError, AccountSubmissionSession, ExecutionMarkerStore, submission_binding_digest
from quant_platform_kit.common.strategy_contracts import StrategyContext
from quant_platform_kit.common.models import PortfolioSnapshot, Position
from quant_platform_kit.common.strategy_risk_state import StrategyRiskStateIdentity, StrategyRiskStateStore, build_strategy_risk_state_transition
from us_equity_strategies.conditional_tqqq_candidate import build_tqqq_conditional_candidate, canonical_tqqq_context_sha256


def test_conditional_candidate_requires_current_session_owned_journal_handle(tmp_path):
    domain = {"identity": {"environment": "LIVE", "platform_id": "ibkr", "server": "synthetic",
              "account_id_fingerprint": "sha256:" + "1" * 64, "lifecycle_id": "one"},
              "policy": {"capital_kind": "OWN_REAL", "product": "BROKERAGE", "phase": "LIVE",
              "account_type": "CASH", "style": "INTRADAY", "preference": "CAPITAL_PRESERVATION",
              "currency": "USD", "source": "synthetic", "effective_at": "2026-10-01T00:00:00Z",
              "valid_until": "2026-11-01T00:00:00Z", "version": "one"}}
    observed = BrokerAccountIdentity(platform_id="ibkr", account_types=("CASH",), account_modes=("LIVE",),
                                     account_id_fingerprint="sha256:" + "1" * 64)
    session = AccountSubmissionSession(ExecutionMarkerStore(local_dir=tmp_path), broker="ibkr", account_id="U00000001",
        owner_id="owner", profile="tqqq_growth_income", intent_id="a" * 64, release_id="b" * 64,
        account_domain=domain, observed_account_id="U00000001", broker_identity=observed,
        observed_at="2026-10-30T02:00:00Z", decision_at="2026-10-30T02:00:00Z", observed_environment="LIVE",
        observed_server="synthetic", observed_currency="USD", observed_lifecycle_id="one",
        clock=lambda: "2026-10-30T02:00:00Z")
    identity = StrategyRiskStateIdentity("tqqq_growth_income", "synthetic", "candidate", "c" * 64)
    intent = {"id": "intent-1", "alpha_epoch": 1, "signal_session": "2026-10-30"}
    portfolio = PortfolioSnapshot(as_of=datetime(2026, 10, 30, tzinfo=timezone.utc),
        positions=(Position(symbol="TQQQ", quantity=10, market_value=8000.0),
                   Position(symbol="QQQM", quantity=20, market_value=10000.0)),
        total_equity=100000., buying_power=1000., metadata={})
    base_ctx = StrategyContext(as_of="2026-10-30", portfolio=portfolio,
        market_data={"benchmark_history": [{"close": 100 + i * .5} for i in range(260)]},
        runtime_config={"income_layer_enabled": False})
    digest = canonical_tqqq_context_sha256(base_ctx)
    transition = build_strategy_risk_state_transition(identity=identity, effective_session="2026-10-30", input_sha256=digest,
        state={"schema_version": "tqqq_daily_state.v1", "session": "2026-10-30", "alpha_trend_active": True,
               "alpha_epoch": 1, "volatility_latch_active": False, "pending_intent": intent, "intent_invalidation_reason": None})
    legacy = StrategyRiskStateStore(local_dir=tmp_path / "legacy"); legacy.append(transition)
    session.enroll_strategy_state(transition, legacy_store=legacy, migration_authorization={
        "account_domain_key": account_domain_key(domain), "account_binding_sha256": submission_binding_digest({"broker": "ibkr", "account_id": "U00000001"}),
        "owner_id": "owner", "release_id": "b" * 64, "legacy_head_sha256": transition.transition_sha256,
        "strategy_identity": identity.to_dict(), "authority_receipt_sha256": "e" * 64})
    handle = session.conditional_planning_handle(strategy_state_sha256=transition.transition_sha256,
        strategy_intent_sha256=submission_binding_digest(intent), strategy_session="2026-10-30", input_sha256=digest)
    request = {"status": "ready", "bootstrap": False, "identity": identity.to_dict(), "previous_transition": transition.to_dict(),
               "input_sha256": digest, "fresh_intent": intent}
    ctx = replace(base_ctx, state={"tqqq_daily_state": request})
    candidate = build_tqqq_conditional_candidate(ctx, journal_session=session, planning_handle=handle)
    assert candidate.status == "CONDITIONAL_NOT_EXECUTABLE" and not candidate.executable and not candidate.order_authorized
    assert next(row for row in candidate.positions if row["symbol"] == "TQQQ")["target_value"] > 8000
    mutations = (replace(ctx, market_data={"benchmark_history": [{"close": 500.0}]}),
                 replace(ctx, runtime_config={"income_layer_enabled": True}),
                 replace(ctx, artifacts={"market_regime_control": {"risk": "on"}}),
                 replace(ctx, portfolio=replace(portfolio, total_equity=100001.)),
                 replace(ctx, portfolio=replace(portfolio, positions=(replace(portfolio.positions[0], quantity=11),))))
    for changed in mutations:
        with pytest.raises(ValueError, match="BINDING_MISMATCH"):
            build_tqqq_conditional_candidate(changed, journal_session=session, planning_handle=handle)
    with pytest.raises(ValueError, match="UNSUPPORTED_CONTEXT_VALUE"):
        canonical_tqqq_context_sha256(replace(ctx, artifacts={"opaque": object()}))
    with pytest.raises(ValueError, match="NONFINITE_CONTEXT_VALUE"):
        canonical_tqqq_context_sha256(replace(ctx, runtime_config={"value": float("nan")}))
    with pytest.raises(AccountSubmissionError):
        build_tqqq_conditional_candidate(ctx, journal_session=session.for_intent("f" * 64), planning_handle=handle)
    with pytest.raises(ValueError):
        build_tqqq_conditional_candidate(ctx, journal_session=session, planning_handle=True)
    successor = build_strategy_risk_state_transition(identity=identity, effective_session="2026-10-31",
        input_sha256="f" * 64, state={**transition.state, "session": "2026-10-31", "pending_intent": None,
        "intent_invalidation_reason": "new_session"}, previous_transition=transition)
    session.advance_strategy_state(successor)
    with pytest.raises(AccountSubmissionError):
        build_tqqq_conditional_candidate(ctx, journal_session=session, planning_handle=handle)
