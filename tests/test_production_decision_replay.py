from tests.candidate_dependency_guard import require_candidate_dependencies
require_candidate_dependencies({'quant_platform_kit.risk.gate.replay': ('replay_apply_risk_gate',)})

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from quant_platform_kit.common.models import PortfolioSnapshot
from quant_platform_kit.common.strategy_contracts import StrategyContext
from us_equity_strategies.entrypoints import _build_soxl_soxx_trend_income_decision, _build_tqqq_growth_income_decision
from us_equity_strategies.research.production_decision_replay import (
    ProductionDecisionReplayRequest, canonical_sha256, effective_config_sha256, production_source_hashes, replay_production_decision,
)


def fixture(profile):
    clock = datetime(2026, 6, 19, 21, tzinfo=timezone.utc)
    payload = {"session": "2026-06-19", "portfolio": {"as_of": clock.isoformat(), "total_equity": 100000.0, "buying_power": 100000.0,
        "positions": [], "metadata": {"market_currency_cash": 100000.0}},
        "instrument_prices": {"TQQQ": 50.0, "QQQM": 100.0, "BOXX": 100.0, "SOXL": 50.0,
                              "SOXX": 100.0, "SPYI": 50.0, "QQQI": 50.0},
        "instrument_prices_available_at": {symbol: clock.isoformat() for symbol in
                            ("TQQQ", "QQQM", "BOXX", "SOXL", "SOXX", "SPYI", "QQQI")}}
    if profile == "tqqq_growth_income":
        payload["benchmark_history"] = [{"session": (clock.date() - timedelta(days=259-i)).isoformat(),
            "available_at": (clock - timedelta(days=259-i)).isoformat(), "close": 100.0+i*.5} for i in range(260)]
    else:
        payload["derived_indicators"] = {"soxl": {"price": 80.0, "ma_trend": 75.0}, "soxx": {
            "price": 80.0, "ma_trend": 75.0, "realized_volatility_10": .20,
            "realized_volatility_10_dynamic_threshold": .50, "realized_volatility_10_dynamic_sample_count": 252.0}}
        payload["indicators_available_at"] = clock.isoformat()
    config = {"income_layer_enabled": False}
    return ProductionDecisionReplayRequest(profile=profile, source_revision="a"*40,
        source_hashes=production_source_hashes(), config=config, config_sha256=canonical_sha256(config),
        effective_config_sha256=effective_config_sha256(profile, config),
        frozen_input=payload, input_sha256=canonical_sha256(payload), decision_at=clock.isoformat(),
        evidence_kind="synthetic_fixture", publication_evidence=None, execution_evidence=None)


@pytest.mark.parametrize("profile", ["tqqq_growth_income", "soxl_soxx_trend_income"])
def test_decision_trace_matches_production_builder_and_is_non_executable(profile):
    request = fixture(profile)
    result = replay_production_decision(request)
    assert result.trace is not None and "decision_targets" in result.trace, result
    payload = request.frozen_input
    market = ({"benchmark_history": [{"close": row["close"]} for row in payload["benchmark_history"]]}
              if profile == "tqqq_growth_income" else {"derived_indicators": payload["derived_indicators"]})
    portfolio = PortfolioSnapshot(as_of=datetime.fromisoformat(request.decision_at), total_equity=100000.0,
        buying_power=100000.0, positions=(), metadata={"market_currency_cash": 100000.0})
    context = StrategyContext(as_of=payload["session"], market_data=market, portfolio=portfolio,
                              runtime_config=request.config)
    builder = _build_tqqq_growth_income_decision if profile == "tqqq_growth_income" else _build_soxl_soxx_trend_income_decision
    expected = builder(context)
    assert result.trace["decision_targets"] == {item.symbol: item.target_value for item in expected.positions}
    assert result.status == "BLOCKED" and not result.executable and not result.historical_parity_established
    assert result.trace["execution_authorized"] is False
    assert result.trace["offline_risk_decision"] is not None


def test_replay_rejects_mutation_missing_real_prices_and_unverified_publication():
    original = fixture("tqqq_growth_income")
    tampered = dict(original.frozen_input, session="2026-06-18")
    assert replay_production_decision(replace(original, frozen_input=tampered)).gaps == ("INPUT_IDENTITY_MISMATCH",)
    data = dict(original.frozen_input, instrument_prices={"TQQQ": 50.0, "BOXX": 100.0})
    result = replay_production_decision(replace(original, frozen_input=data, input_sha256=canonical_sha256(data), evidence_kind="historical"))
    assert "ACTUAL_INSTRUMENT_PRICES_MISSING" in result.gaps
    assert "INDEPENDENT_PUBLICATION_EVIDENCE_UNVERIFIED" in result.gaps
    assert "EXECUTION_SIMULATION_NOT_IMPLEMENTED" in result.gaps


def test_future_history_does_not_change_earlier_decision():
    original = fixture("tqqq_growth_income")
    data = dict(original.frozen_input)
    data["benchmark_history"] = [*data["benchmark_history"], {"session": "2026-06-20",
        "available_at": "2026-06-20T21:00:00+00:00", "close": 999999.0}]
    result = replay_production_decision(replace(original, frozen_input=data, input_sha256=canonical_sha256(data)))
    assert result.trace["decision_targets"] == replay_production_decision(original).trace["decision_targets"]


def test_source_config_and_late_observations_fail_closed():
    original = fixture("tqqq_growth_income")
    assert replay_production_decision(replace(original, source_hashes={})).gaps == ("SOURCE_IDENTITY_MISMATCH",)
    assert replay_production_decision(replace(original, config={"income_layer_enabled": True})).gaps == ("CONFIG_IDENTITY_MISMATCH",)
    data = dict(original.frozen_input)
    history = [dict(row) for row in data["benchmark_history"]]
    history[-1]["available_at"] = "2026-06-19T22:00:00+00:00"
    data["benchmark_history"] = history
    result = replay_production_decision(replace(original, frozen_input=data, input_sha256=canonical_sha256(data)))
    assert result.gaps == ("INVALID_OR_INCOMPLETE_INPUT",)
    assert "decision_targets" not in result.trace


def test_multi_session_prefix_and_conflicting_clocks():
    from us_equity_strategies.research.production_decision_replay import replay_production_decision_series
    first = fixture("tqqq_growth_income")
    later = fixture("soxl_soxx_trend_income")
    later = replace(later, decision_at="2026-06-20T21:00:00+00:00")
    prefix = replay_production_decision_series((first,))
    series = replay_production_decision_series((first, later))
    assert prefix == series[:1]
    with pytest.raises(ValueError):
        replay_production_decision_series((first, first))


def test_position_cost_basis_survives_replay_and_triggers_live_stop_loss():
    from quant_platform_kit.common.models import Position
    from us_equity_strategies.entrypoints import compute_tqqq_growth_income_decision
    request = fixture("tqqq_growth_income")
    data = dict(request.frozen_input)
    data["portfolio"] = {**data["portfolio"], "positions": [{"symbol": "TQQQ", "quantity": 1000,
        "market_value": 50000, "average_cost": 100.0, "currency": "USD", "account_id": "synthetic"}]}
    replay = replay_production_decision(replace(request, frozen_input=data, input_sha256=canonical_sha256(data)))
    position = Position(symbol="TQQQ", quantity=1000, market_value=50000, average_cost=100.0,
                        currency="USD", account_id="synthetic")
    portfolio = PortfolioSnapshot(as_of=datetime.fromisoformat(request.decision_at), total_equity=100000.0,
        buying_power=100000.0, positions=(position,), metadata={"market_currency_cash": 100000.0})
    history = [{"close": row["close"]} for row in data["benchmark_history"]]
    live = compute_tqqq_growth_income_decision(StrategyContext(as_of=data["session"], portfolio=portfolio,
        market_data={"benchmark_history": history}, runtime_config=request.config))
    assert "rejected:stop_loss" in live.risk_flags
    assert "rejected:stop_loss" in replay.trace["offline_risk_decision"]["risk_flags"]
    with pytest.raises(TypeError):
        from us_equity_strategies.research.production_decision_replay import ProductionDecisionReplayResult
        ProductionDecisionReplayResult("BLOCKED", (), None, executable=True)
