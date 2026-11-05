from tests.candidate_dependency_guard import require_candidate_dependencies
require_candidate_dependencies({'quant_platform_kit.risk.gate.replay': ('replay_apply_risk_gate',)})

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from tests.test_production_decision_replay import fixture
from us_equity_strategies.research.production_decision_replay import canonical_sha256, effective_config_sha256
from us_equity_strategies.research.production_economic_replay import SyntheticLedgerRequest, replay_synthetic_production_ledger

SYMBOLS = ("TQQQ", "QQQM", "BOXX", "SPYI", "QQQI", "DGRO", "SCHD", "SGOV")


def session(day, *, events=(), token=None, valid=None, prices=None):
    value = {symbol: 100.0 for symbol in SYMBOLS}
    if prices:
        value.update(prices)
    return {"session": day, "open_at": day + "T14:30:00+00:00", "close_at": day + "T21:00:00+00:00",
            "open_prices": dict(value), "close_prices": dict(value), "events": list(events),
            "buy_intent_id": token, "valid_buy_intent_id": valid}


def template(day):
    original = fixture("tqqq_growth_income")
    date = datetime.fromisoformat(day).date()
    data = dict(original.frozen_input)
    data["session"] = day
    data["benchmark_history"] = [{"session": (date-timedelta(days=259-i)).isoformat(),
        "available_at": (datetime.combine(date-timedelta(days=259-i), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=21)).isoformat(),
        "close": 100.0 + i * .5} for i in range(260)]
    data["instrument_prices"] = {symbol: 100.0 for symbol in SYMBOLS}
    data["instrument_prices_available_at"] = {symbol: day + "T21:00:00+00:00" for symbol in SYMBOLS}
    return replace(original, frozen_input=data, input_sha256=canonical_sha256(data), decision_at=day + "T21:00:00+00:00")


def ledger(rows, *, cash=100000.0, quantities=None, costs=None, fee_bps=10.0, minimum=1.0):
    decisions = []
    for row in rows[:-1]:
        base = template(row["session"])
        payload = dict(base.frozen_input)
        payload["instrument_prices"] = dict(row["close_prices"])
        token = row.get("buy_intent_id")
        intent = {"id": token, "alpha_epoch": 1, "signal_session": row["session"]} if token else None
        payload["state"] = {"tqqq_daily_state": {"status": "ready", "identity": {
            "strategy_profile": "tqqq_growth_income", "account_scope": "synthetic", "candidate_id": "fixture",
            "config_sha256": base.effective_config_sha256}, "previous_transition": None, "bootstrap": True,
            "input_sha256": "a" * 64, "expected_previous_session": None, "fresh_intent": intent}}
        decisions.append(replace(base, frozen_input=payload, input_sha256=canonical_sha256(payload)))
    return SyntheticLedgerRequest(decisions=tuple(decisions), sessions=tuple(rows),
        initial_cash=cash, initial_quantities=quantities or {}, initial_average_costs=costs or {},
        lot_sizes={symbol: 1.0 for symbol in SYMBOLS}, minimum_trade_value=minimum, fee_bps=fee_bps,
        slippage_bps=0.0, execution_lag_sessions=1, settlement_lag_sessions=0)


def test_next_open_cost_cash_carry_and_stale_intent():
    rows = [session("2026-06-19", token="fresh"), session("2026-06-22", valid="fresh", token="old"),
            session("2026-06-23", valid="different")]
    result = replay_synthetic_production_ledger(ledger(rows))
    day = result["rows"][1]
    assert day["fills"] and all(item["side"] == "buy" for item in day["fills"])
    assert day["fees"] == pytest.approx(sum(f["quantity"] * f["price"] for f in day["fills"]) * .001)
    assert day["cash"] >= 0 and day["nav"] == pytest.approx(100000.0 - day["fees"])
    assert not any(item["side"] == "buy" for item in result["rows"][2]["fills"])
    assert result["executable"] is False and result["historical_parity_established"] is False


def test_split_neutral_nav_dividend_entitlement_paid_once():
    events1 = ({"type": "split", "id": "split", "symbol": "TQQQ", "ratio": 2.0},
               {"type": "dividend_ex", "id": "ex", "symbol": "TQQQ", "per_share": 1.0})
    events2 = ({"type": "dividend_pay", "id": "pay", "ex_id": "ex", "symbol": "TQQQ"},)
    rows = [session("2026-06-19", events=events1, prices={"TQQQ": 50.0}),
            session("2026-06-22", events=events2, prices={"TQQQ": 50.0})]
    result = replay_synthetic_production_ledger(ledger(rows, cash=0, quantities={"TQQQ": 10.0},
                                                       costs={"TQQQ": 100.0}, fee_bps=0))
    first, second = result["rows"]
    assert first["quantities"]["TQQQ"] == 20
    assert first["average_costs"]["TQQQ"] == 50
    assert first["nav"] == pytest.approx(1020.0)
    assert first["receivables"]["ex"]["amount"] == 20
    assert second["event_cash"] == 20 and second["receivables"] == {}
    bad = [rows[0], session("2026-06-22", events=(*events2, {"type":"dividend_pay", "id":"pay2", "ex_id":"ex", "symbol":"TQQQ"}), prices={"TQQQ":50.0})]
    with pytest.raises(ValueError, match="already paid"):
        replay_synthetic_production_ledger(ledger(bad, cash=0, quantities={"TQQQ":10.0}, costs={"TQQQ":100.0}, fee_bps=0))


def test_known_sell_fee_lot_and_minimum_trade():
    rows = [session("2026-06-19"), session("2026-06-22")]
    result = replay_synthetic_production_ledger(ledger(rows, cash=0,
        quantities={"TQQQ": 1000.0}, costs={"TQQQ": 100.0}, fee_bps=10))
    sales = [fill for fill in result["rows"][1]["fills"] if fill["symbol"] == "TQQQ"]
    assert len(sales) == 1 and sales[0]["side"] == "sell"
    assert sales[0]["quantity"] == 550 and sales[0]["fee"] == pytest.approx(55.0)
    assert result["rows"][1]["cash"] == pytest.approx(54945.0)
    suppressed = replay_synthetic_production_ledger(ledger(rows, cash=0, quantities={"TQQQ": 1000.0},
                                                          costs={"TQQQ": 100.0}, minimum=60000.0))
    assert not suppressed["rows"][1]["fills"]


def test_earlier_ledger_state_is_invariant_to_future_perturbation():
    rows = [session("2026-06-19", token="i"), session("2026-06-22", valid="i", token="j"),
            session("2026-06-23", valid="j")]
    original = replay_synthetic_production_ledger(ledger(rows))
    changed = list(rows)
    changed[-1] = session("2026-06-23", valid="j", prices={"TQQQ": 500.0})
    perturbed = replay_synthetic_production_ledger(ledger(changed))
    assert original["rows"][:2] == perturbed["rows"][:2]


def test_soxl_ledger_uses_explicit_typed_synthetic_gate_evidence():
    day = "2026-06-19"
    source = fixture("soxl_soxx_trend_income")
    symbols = ("SOXL", "SOXX", "BOXX", "SPYI", "QQQI", "DGRO", "SCHD", "SGOV")
    config = {"income_layer_enabled": False, "blend_gate_soxl_weight": .1, "blend_gate_active_soxx_weight": .1}
    payload = dict(source.frozen_input)
    payload["instrument_prices"] = {symbol: 100.0 for symbol in symbols}
    payload["instrument_prices_available_at"] = {symbol: day + "T21:00:00+00:00" for symbol in symbols}
    payload["capabilities"] = {
        "capital_base": {"reported_equity": 100000.0, "reported_currency": "USD", "target_currency": "USD",
            "fx_rate_to_target": 1.0, "as_of": day + "T21:00:00+00:00", "account_scope": "synthetic",
            "runtime_scope": "synthetic", "strategy_scope": "soxl_soxx_trend_income", "source_digest_sha256": "a"*64,
            "capital_scope": "account", "valuation_basis": "broker_account_net_liquidation"},
        "capital_base_binding": {"account_scope": "synthetic", "runtime_scope": "synthetic",
            "strategy_scope": "soxl_soxx_trend_income", "target_currency": "USD", "capital_scope": "account",
            "valuation_basis": "broker_account_net_liquidation"},
        "runtime_risk_limits": {"allowed_symbols": list(symbols), "product_leverage_factors": {s: 1 for s in symbols},
            "nominal_caps": {s: 1.0 for s in symbols}, "total_nominal_exposure_cap": 1.0,
            "total_effective_exposure_cap": 1.0, "max_positions": len(symbols)}}
    decision = replace(source, config=config, config_sha256=canonical_sha256(config),
                       effective_config_sha256=effective_config_sha256("soxl_soxx_trend_income", config),
                       frozen_input=payload, input_sha256=canonical_sha256(payload))
    prices = {s: 100.0 for s in symbols}
    rows = ({"session": day, "open_at": day+"T14:30:00+00:00", "close_at": day+"T21:00:00+00:00",
             "open_prices": prices, "close_prices": prices, "events": [], "buy_intent_id": "soxl"},
            {"session": "2026-06-22", "open_at": "2026-06-22T14:30:00+00:00", "close_at": "2026-06-22T21:00:00+00:00",
             "open_prices": prices, "close_prices": prices, "events": [], "valid_buy_intent_id": "soxl"})
    result = replay_synthetic_production_ledger(SyntheticLedgerRequest(decisions=(decision,), sessions=rows,
        initial_cash=100000.0, initial_quantities={}, initial_average_costs={}, lot_sizes={s: 1.0 for s in symbols},
        minimum_trade_value=1.0, fee_bps=10.0, slippage_bps=0.0, execution_lag_sessions=1, settlement_lag_sessions=0))
    assert result["rows"][0]["decision_trace"]["offline_risk_decision"]["risk_flags"] == ("risk_gate:passed",)
    assert any(fill["symbol"] == "SOXL" for fill in result["rows"][1]["fills"])

    import copy
    next_payload = copy.deepcopy(payload)
    next_payload["session"] = "2026-06-22"
    next_payload["indicators_available_at"] = "2026-06-22T21:00:00+00:00"
    next_payload["instrument_prices_available_at"] = {s: "2026-06-22T21:00:00+00:00" for s in symbols}
    next_payload["capabilities"]["capital_base"]["as_of"] = "2026-06-22T21:00:00+00:00"
    second = replace(decision, decision_at="2026-06-22T21:00:00+00:00", frozen_input=next_payload,
                     input_sha256=canonical_sha256(next_payload))
    delayed_rows = (rows[0], {**rows[1], "buy_intent_id": "soxl"},
                    {"session": "2026-06-23", "open_at": "2026-06-23T14:30:00+00:00",
                     "close_at": "2026-06-23T21:00:00+00:00", "open_prices": prices,
                     "close_prices": prices, "events": [], "valid_buy_intent_id": "soxl"})
    delayed = replay_synthetic_production_ledger(SyntheticLedgerRequest(decisions=(decision, second),
        sessions=delayed_rows, initial_cash=100000.0, initial_quantities={}, initial_average_costs={},
        lot_sizes={s: 1.0 for s in symbols}, minimum_trade_value=1.0, fee_bps=10.0,
        slippage_bps=0.0, execution_lag_sessions=2, settlement_lag_sessions=0))
    assert not delayed["rows"][1]["fills"]
    assert any(fill["symbol"] == "SOXL" and fill["side"] == "buy" for fill in delayed["rows"][2]["fills"])


def test_sale_proceeds_obey_settlement_and_slippage():
    rows = [session("2026-06-19", token="first"), session("2026-06-22", valid="first", token="second"),
            session("2026-06-23", valid="second")]
    request = replace(ledger(rows, cash=0, quantities={"TQQQ": 1000.0}, costs={"TQQQ": 100.0}),
                      settlement_lag_sessions=1, slippage_bps=100.0)
    result = replay_synthetic_production_ledger(request)
    day2, day3 = result["rows"][1:]
    sells = [fill for fill in day2["fills"] if fill["side"] == "sell"]
    assert sells and sells[0]["price"] == 99.0
    assert not any(fill["side"] == "buy" for fill in day2["fills"])
    assert day2["unsettled_cash"] > 0 and day2["settled_buying_power"] == pytest.approx(0.0)
    buys = [fill for fill in day3["fills"] if fill["side"] == "buy"]
    assert buys and buys[0]["price"] == 101.0
    assert day3["settled_buying_power"] >= 0


def test_dividend_payment_rejects_wrong_symbol():
    ex = {"type": "dividend_ex", "id": "ex", "symbol": "TQQQ", "per_share": 1.0}
    pay = {"type": "dividend_pay", "id": "pay", "ex_id": "ex", "symbol": "QQQM"}
    rows = [session("2026-06-19", events=(ex,)), session("2026-06-22", events=(pay,))]
    with pytest.raises(ValueError, match="symbol or chronology"):
        replay_synthetic_production_ledger(ledger(rows, quantities={"TQQQ":10.0}, costs={"TQQQ":100.0}))


def test_state_head_is_carried_and_older_buy_token_cannot_revive():
    rows = [session("2026-06-19", token="first"), session("2026-06-22", valid="first", token="second"),
            session("2026-06-23", valid="first")]
    result = replay_synthetic_production_ledger(ledger(rows))
    first = result["rows"][0]["decision_trace"]["offline_risk_decision"]["diagnostics"]["tqqq_daily_state_transition"]
    second = result["rows"][1]["decision_trace"]["offline_risk_decision"]["diagnostics"]["tqqq_daily_state_transition"]
    assert second["previous_transition_sha256"] == first["transition_sha256"]
    assert second["state"]["pending_intent"]["id"] == "second"
    assert not any(fill["side"] == "buy" for fill in result["rows"][2]["fills"])


def test_delayed_pending_buy_is_canceled_by_new_generation():
    rows = [session("2026-06-19", token="old"), session("2026-06-22", token="new"),
            session("2026-06-23", valid="old")]
    result = replay_synthetic_production_ledger(replace(ledger(rows), execution_lag_sessions=2))
    assert not any(fill["side"] == "buy" for row in result["rows"] for fill in row["fills"])


def test_fractional_lots_and_cash_shortfall():
    rows = [session("2026-06-19", token="first"), session("2026-06-22", valid="first")]
    request = replace(ledger(rows, cash=151, fee_bps=0), lot_sizes={symbol: .5 for symbol in SYMBOLS})
    result = replay_synthetic_production_ledger(request)
    fills = result["rows"][1]["fills"]
    assert fills and all(fill["quantity"] * 2 == int(fill["quantity"] * 2) for fill in fills)
    assert result["rows"][1]["cash"] >= 0
    assert sum(fill["quantity"] * fill["price"] for fill in fills) <= 151


def test_portfolio_risk_metadata_survives_snapshot_reconstruction():
    rows = [session("2026-06-19", token="first"), session("2026-06-22", valid="first")]
    request = ledger(rows)
    first = request.decisions[0]
    payload = dict(first.frozen_input)
    payload["portfolio"] = {**payload["portfolio"], "metadata": {"unrealized_pnl_pct": -.50}}
    first = replace(first, frozen_input=payload, input_sha256=canonical_sha256(payload))
    result = replay_synthetic_production_ledger(replace(request, decisions=(first,)))
    gate = result["rows"][0]["decision_trace"]["offline_risk_decision"]
    assert "rejected:stop_loss" in gate["risk_flags"]
    assert result["rows"][1]["fills"] == []


def test_lag_two_queue_preserves_due_independent_reductions():
    rows = [session("2026-06-19", token="first"), session("2026-06-22", token="second"),
            session("2026-06-23", valid="first")]
    result = replay_synthetic_production_ledger(replace(ledger(rows, cash=0,
        quantities={"TQQQ": 1000.0}, costs={"TQQQ": 100.0}), execution_lag_sessions=2))
    fills = result["rows"][2]["fills"]
    assert any(fill["symbol"] == "TQQQ" and fill["side"] == "sell" for fill in fills)
    assert not any(fill["side"] == "buy" for fill in fills)


def test_lag_two_reused_text_token_does_not_revive_old_generation():
    rows = [session("2026-06-19", token="same"), session("2026-06-22", token="same"),
            session("2026-06-23", valid="same")]
    result = replay_synthetic_production_ledger(replace(ledger(rows), execution_lag_sessions=2))
    assert not any(fill["side"] == "buy" for fill in result["rows"][2]["fills"])


def test_carried_partial_retention_latch_across_sessions():
    from unittest.mock import patch
    rows = [session("2026-06-19"), session("2026-06-22"), session("2026-06-23")]
    base = ledger(rows, cash=0, quantities={"TQQQ": 1000.0}, costs={"TQQQ": 100.0}, fee_bps=0)
    config = {"income_layer_enabled": False, "dual_drive_volatility_delever_retention_mode": "fixed",
              "dual_drive_volatility_delever_retention_ratio": .5}
    digest = canonical_sha256(config)
    effective_digest = effective_config_sha256("tqqq_growth_income", config)
    decisions = []
    for index, item in enumerate(base.decisions):
        data = dict(item.frozen_input)
        data["benchmark_history"] = [dict(bar) for bar in data["benchmark_history"]]
        data["benchmark_history"][-1]["close"] += index
        data["state"] = {"tqqq_daily_state": {**data["state"]["tqqq_daily_state"], "identity": {
            **data["state"]["tqqq_daily_state"]["identity"], "config_sha256": effective_digest}}}
        decisions.append(replace(item, config=config, config_sha256=digest, effective_config_sha256=effective_digest, frozen_input=data,
                                 input_sha256=canonical_sha256(data)))
    def thresholds(close, **kwargs):
        return {"mode": "fixed", "entry_threshold": .28, "exit_threshold": .20,
            "dynamic_threshold": None, "dynamic_sample_count": 0, "dynamic_lookback": 252,
            "dynamic_percentile": .9, "dynamic_min_periods": 126, "dynamic_floor": .24,
            "dynamic_cap": .36, "metric": .30 if close.iloc[-1] == 229.5 else .25}
    with patch("us_equity_strategies.strategies.tqqq_growth_income_live_v2._resolve_volatility_delever_thresholds", thresholds):
        result = replay_synthetic_production_ledger(replace(base, decisions=tuple(decisions)))
    first, second = [row["decision_trace"]["offline_risk_decision"]["diagnostics"] for row in result["rows"][:2]]
    assert first["tqqq_daily_state_transition"]["state"]["volatility_latch_active"] is True
    assert second["tqqq_daily_state_transition"]["state"]["volatility_latch_active"] is True
    assert second["dual_drive_volatility_delever_hysteresis_triggered"] is True
    assert second["tqqq_daily_state_risk_increase_allowed"] is False
