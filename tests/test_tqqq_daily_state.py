from tests.candidate_dependency_guard import require_candidate_dependencies
require_candidate_dependencies({'quant_platform_kit.common.strategy_risk_state': ('StrategyRiskStateTransition', 'build_strategy_risk_state_transition')})

from us_equity_strategies.tqqq_daily_state import prepare_tqqq_daily_state, finish_tqqq_daily_state


def request(**updates):
    value = {"status": "ready", "identity": {"strategy_profile": "tqqq_growth_income",
             "account_scope": "test", "candidate_id": "candidate", "config_sha256": "a" * 64},
             "previous_transition": None, "bootstrap": True, "input_sha256": "b" * 64,
             "expected_previous_session": None, "fresh_intent": None}
    value.update(updates)
    return value


def propose(req, session, alpha, latch):
    prepared = prepare_tqqq_daily_state(req, session)
    return finish_tqqq_daily_state(prepared, req, alpha_active=alpha, volatility_latch=latch)


def test_daily_state_explicit_bootstrap_confirm_and_stale_intent():
    intent = {"id": "i1", "alpha_epoch": 1, "signal_session": "2026-06-19"}
    first = request(fresh_intent=intent)
    root = propose(first, "2026-06-19", True, False)
    assert root["status"] == "proposed" and not root["risk_increase_allowed"]
    confirmed = request(bootstrap=False, previous_transition=root["transition"], fresh_intent=intent, execution_fence_confirmed=True)
    assert propose(confirmed, "2026-06-19", True, False)["risk_increase_allowed"]
    assert not propose(confirmed, "2026-06-19", False, True)["risk_increase_allowed"]
    next_req = request(bootstrap=False, previous_transition=root["transition"],
                       expected_previous_session="2026-06-19", input_sha256="c" * 64, fresh_intent=intent)
    next_state = propose(next_req, "2026-06-22", False, True)["transition"]["state"]
    assert next_state["pending_intent"] is None
    assert next_state["intent_invalidation_reason"] == "alpha_exit"
    assert next_state["volatility_latch_active"] is True


def test_daily_state_missing_gap_malformed_and_conflicting_retry():
    assert prepare_tqqq_daily_state(None, "2026-06-19")["status"] == "unavailable"
    assert prepare_tqqq_daily_state(request(bootstrap=False), "2026-06-19")["status"] == "unavailable"
    root = propose(request(), "2026-06-19", True, True)["transition"]
    assert prepare_tqqq_daily_state(request(bootstrap=False, previous_transition=root,
        expected_previous_session="2026-06-20"), "2026-06-22")["status"] == "unavailable"
    assert prepare_tqqq_daily_state(request(bootstrap=False, previous_transition=root,
        input_sha256="c" * 64), "2026-06-19")["status"] == "unavailable"
    assert prepare_tqqq_daily_state(request(bootstrap=False, previous_transition={}), "2026-06-19")["status"] == "unavailable"


def test_entrypoint_proposal_confirmation_and_unavailable_reduction():
    from types import SimpleNamespace
    from quant_platform_kit.common.strategy_contracts import StrategyContext
    from us_equity_strategies.entrypoints import _build_tqqq_growth_income_decision

    history = [{"close": 100.0 + i * .5} for i in range(260)]
    portfolio = SimpleNamespace(positions=[SimpleNamespace(symbol="TQQQ", quantity=10, market_value=8000.0),
        SimpleNamespace(symbol="QQQM", quantity=20, market_value=10000.0)],
        total_equity=100000.0, buying_power=1000.0, metadata={})
    intent = {"id": "new-entry", "alpha_epoch": 1, "signal_session": "2026-06-19"}
    def decision(state):
        return _build_tqqq_growth_income_decision(StrategyContext(as_of="2026-06-19", portfolio=portfolio,
            market_data={"benchmark_history": history}, runtime_config={"income_layer_enabled": False},
            state={"tqqq_daily_state": state}))
    proposed = decision(request(fresh_intent=intent))
    assert proposed.diagnostics["tqqq_daily_state_status"] == "proposed"
    targets = {p.symbol: p.target_value for p in proposed.positions}
    assert targets["TQQQ"] <= 8000 and targets["QQQM"] <= 10000
    root = proposed.diagnostics["tqqq_daily_state_transition"]
    confirmed = decision(request(bootstrap=False, previous_transition=root, fresh_intent=intent, execution_fence_confirmed=True))
    assert confirmed.diagnostics["tqqq_daily_state_risk_increase_allowed"] is True
    targets = {p.symbol: p.target_value for p in confirmed.positions}
    assert targets["TQQQ"] > 8000
    unavailable = decision({"status": "unavailable"})
    targets = {p.symbol: p.target_value for p in unavailable.positions}
    assert targets["TQQQ"] <= 8000 and targets["QQQM"] <= 10000
    falling = [{"close": 300.0 - i * .5} for i in range(260)]
    reduction = _build_tqqq_growth_income_decision(StrategyContext(as_of="2026-06-19", portfolio=portfolio,
        market_data={"benchmark_history": falling}, runtime_config={"income_layer_enabled": False},
        state={"tqqq_daily_state": {"status": "unavailable"}}))
    targets = {p.symbol: p.target_value for p in reduction.positions}
    assert targets["TQQQ"] == 0 and targets["QQQM"] == 0


def test_alpha_exit_and_reentry_never_revives_old_intent():
    intent = {"id": "old", "alpha_epoch": 1, "signal_session": "2026-06-19"}
    root = propose(request(fresh_intent=intent), "2026-06-19", True, False)["transition"]
    exit_request = request(bootstrap=False, previous_transition=root, expected_previous_session="2026-06-19",
                           input_sha256="c" * 64, fresh_intent=intent)
    exit_state = propose(exit_request, "2026-06-22", False, True)["transition"]
    reentry = request(bootstrap=False, previous_transition=exit_state, expected_previous_session="2026-06-22",
                      input_sha256="d" * 64, fresh_intent=intent)
    state = propose(reentry, "2026-06-23", True, True)["transition"]["state"]
    assert state["alpha_epoch"] == 2
    assert state["pending_intent"] is None
    assert state["volatility_latch_active"] is True


def test_risk_latch_invalidates_intent_and_cannot_revive_it():
    intent = {"id": "entry", "alpha_epoch": 1, "signal_session": "2026-06-19"}
    root = propose(request(fresh_intent=intent), "2026-06-19", True, False)["transition"]
    retry = request(bootstrap=False, previous_transition=root, fresh_intent=intent)
    assert not propose(retry, "2026-06-19", True, True)["risk_increase_allowed"]
    next_req = request(bootstrap=False, previous_transition=root, expected_previous_session="2026-06-19",
                       input_sha256="c" * 64, fresh_intent=intent)
    latched = propose(next_req, "2026-06-22", True, True)["transition"]
    assert latched["state"]["pending_intent"] is None
    assert latched["state"]["intent_invalidation_reason"] == "risk_latched"
    released_req = request(bootstrap=False, previous_transition=latched, expected_previous_session="2026-06-22",
                           input_sha256="d" * 64, fresh_intent=intent)
    released = propose(released_req, "2026-06-23", True, False)["transition"]
    assert released["state"]["pending_intent"] is None
    assert not propose(request(bootstrap=False, previous_transition=released, input_sha256="d" * 64,
                               fresh_intent=intent), "2026-06-23", True, False)["risk_increase_allowed"]


def test_fence_absent_preserves_confirmed_latch_and_blocks_buys():
    root = propose(request(), "2026-06-19", True, True)["transition"]
    confirmed = request(bootstrap=False, previous_transition=root)
    result = propose(confirmed, "2026-06-19", True, True)
    assert result["status"] == "confirmed" and result["transition"] == root
    assert not result["risk_increase_allowed"]
    conflict = propose(confirmed, "2026-06-19", True, False)
    assert conflict["status"] == "conflict" and not conflict["risk_increase_allowed"]


def test_confirmed_latch_without_fence_still_applies_partial_reduction():
    from types import SimpleNamespace
    from unittest.mock import patch
    from quant_platform_kit.common.strategy_contracts import StrategyContext
    from us_equity_strategies.entrypoints import _build_tqqq_growth_income_decision

    root = propose(request(), "2026-06-19", True, True)["transition"]
    portfolio = SimpleNamespace(positions=[SimpleNamespace(symbol="TQQQ", quantity=30, market_value=30000.0),
        SimpleNamespace(symbol="QQQM", quantity=30, market_value=30000.0)], total_equity=100000.0,
        buying_power=0.0, metadata={})
    history = [{"close": 100.0 + i * .5} for i in range(260)]
    context = StrategyContext(as_of="2026-06-19", portfolio=portfolio, market_data={"benchmark_history": history},
        runtime_config={"income_layer_enabled": False, "dual_drive_volatility_delever_retention_mode": "fixed",
                        "dual_drive_volatility_delever_retention_ratio": .5},
        state={"tqqq_daily_state": request(bootstrap=False, previous_transition=root)})
    with patch("us_equity_strategies.strategies.tqqq_growth_income._resolve_volatility_delever_thresholds") as resolve:
        resolve.return_value = {"mode": "fixed", "entry_threshold": .28, "exit_threshold": .20,
            "dynamic_threshold": None, "dynamic_sample_count": 0, "dynamic_lookback": 252,
            "dynamic_percentile": .9, "dynamic_min_periods": 126, "dynamic_floor": .24,
            "dynamic_cap": .36, "metric": .25}
        decision = _build_tqqq_growth_income_decision(context)
    assert decision.diagnostics["tqqq_daily_state_status"] == "confirmed"
    assert not decision.diagnostics["tqqq_daily_state_risk_increase_allowed"]
    targets = {p.symbol: p.target_value for p in decision.positions}
    assert targets["TQQQ"] == 22500.0
    assert targets["QQQM"] <= 30000.0
