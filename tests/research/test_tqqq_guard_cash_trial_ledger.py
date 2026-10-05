"""Explicitly synthetic, network-blocked tests; no qualified data or private run."""

from copy import deepcopy
from dataclasses import replace
from datetime import date
import hashlib
import json
import socket

import pandas as pd
import pytest

from quant_platform_kit.strategy_lifecycle.contracts import (
    ResearchTrialRecord, ResearchTrialStatus,
)
from us_equity_strategies.research import tqqq_qqq_guard_cash_research as simulator
from us_equity_strategies.research.tqqq_guard_cash_trial_ledger import build_guard_cash_trial_ledger


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise AssertionError("SYNTHETIC_LEDGER_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


def _case(monkeypatch, *, cost_bps=0, with_actions=True, same_day=False, unpaid=False,
          split_trade=0, initial_target=4500.0, sell_last=False, zero_rate=False,
          with_split=True, sessions=5):
    warmup = pd.bdate_range(end="2024-12-26", periods=257)
    days = [day.date().isoformat() for day in warmup] + [
        "2024-12-27", "2024-12-30", "2024-12-31", "2025-01-02", "2025-01-03"]
    rows = [{"date": day, "qqq_close": 100.0, "tqqq_open": 50.0,
             "tqqq_close": 50.0} for day in days]
    actions = {"forward_splits": [], "cash_dividends": []}
    if with_actions:
        for row in rows[258:]:
            row["tqqq_open"] = row["tqqq_close"] = 25.0
        for row in rows[259:]:
            row["tqqq_open"] = row["tqqq_close"] = 24.0
        actions = {
            "forward_splits": [{"id": "synthetic-split-1", "symbol": "TQQQ",
                                "ex_date": "2024-12-30", "new_rate": 2, "old_rate": 1}],
            "cash_dividends": [{"id": "synthetic-dividend-1", "symbol": "TQQQ",
                                "ex_date": "2024-12-31", "rate": 0.0 if zero_rate else 1.0,
                                "payable_date": "2025-01-10" if unpaid else
                                "2024-12-31" if same_day else "2025-01-01"}],
        }
    if not with_split:
        actions["forward_splits"] = []
    contract = {"candidate_id": simulator.CANDIDATE,
                "core": {"first_signal_min_qqq_bars": 257},
                "portfolio": {"research_initial_usd": 10000},
                "window": {"first_signal_rule": "index 256 after 257 complete common bars"}}
    # Already-computed output from the actual simulator, with toy decisions only.
    def toy_decision(rows, signal_index, *, shares, nav, **_kwargs):
        target = initial_target if signal_index == 256 else shares * rows[signal_index + 1]["tqqq_open"]
        if with_actions and with_split and signal_index == 257:
            target = target * 2 + split_trade * 25
        if sell_last and signal_index == 260:
            target = 0.0
        return {"signal_date": rows[signal_index]["date"], "guard_route": "no_action",
                "applied_route": "no_action", "member_budget_ratio": 1.0,
                "member_budget_usd": nav, "target_tqqq_ratio": target / nav,
                "target_tqqq_usd": target}
    monkeypatch.setattr(simulator, "_decision", toy_decision)
    metrics, daily_rows = simulator._simulate(rows, actions, contract, cost_bps=cost_bps,
                                               use_guard=True, sessions=sessions)
    return rows, actions, contract, metrics, daily_rows


def _digest(rows, actions, contract):
    return hashlib.sha256(json.dumps({"rows": rows, "actions": actions, "contract": contract},
                                     sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _started(case, *, cost_bps=0, scenario="synthetic_guard_5_sessions"):
    rows, actions, contract, _, daily_rows = case
    return ResearchTrialRecord(
        trial_id="synthetic-trial-1", domain="us_equity", strategy_profile=simulator.CANDIDATE,
        status=ResearchTrialStatus.STARTED, candidate_config_id="synthetic-contract-1",
        actual_params={"scenario_id": scenario, "cost_bps": cost_bps, "use_guard": True, "sessions": len(daily_rows)},
        param_set_id="synthetic-params-1", source_revision="22cfd987aaf3514cbf11b6237e51e9bc3227db47",
        input_id="synthetic-input-1", window_start=date.fromisoformat(rows[256]["date"]),
        window_end=date.fromisoformat(daily_rows[-1]["date"]), calendar_id="XNYS",
        periods_per_year=252.0, cost_source="synthetic_bps_only",
        cost_inputs={"cost_bps": float(cost_bps)}, reason_code="", synthetic=True,
        run_id=None, param_version=None,
        research_identity={"guard_cash_input_sha256": _digest(rows, actions, contract),
                           "actions_source_id": "synthetic-fixture/actions/TQQQ/page-001.json",
                           "external_cashflow_scope": "closed_research_no_external_flows"})


def _build(case, started=None):
    rows, actions, contract, metrics, daily_rows = case
    return build_guard_cash_trial_ledger(
        rows=rows, actions=actions, contract=contract, metrics=metrics, daily_rows=daily_rows,
        started=_started(case) if started is None else started,
        run_id="synthetic-run-1", param_version=1, computed_at="2026-10-05T15:30:00Z")


def test_maps_exact_simulator_split_dividend_payment_and_matching_pair(monkeypatch):
    case = _case(monkeypatch)
    original = deepcopy(case)
    result, ledger = _build(case)
    assert case == original
    assert ledger.synthetic and ledger.events_complete
    assert ledger.initial_cash == ledger.initial_nav == 10000
    assert ledger.initial_positions == ()
    assert result.start_date == ledger.window_start == date(2024, 12, 26)
    assert result.end_date == ledger.window_end == date(2025, 1, 3)
    assert result.run_id == ledger.run_id == "synthetic-run-1"
    assert result.param_version == ledger.param_version == 1
    assert result.observation_count == ledger.observation_count == 5
    assert ledger.trial_id == "synthetic-trial-1" and ledger.input_id == "synthetic-input-1"
    assert result.params["research_identity"] == _started(case).research_identity
    assert result.total_return == ledger.total_return == case[3]["total_return"]
    assert ledger.days[-1].nav == case[3]["end_nav_usd"] == 10000
    first, split, ex, payment, after = ledger.days
    assert first.positions[0].quantity == 90 and first.trade_net_cashflow == -4500
    assert split.positions[0].quantity == 180 and split.trade_net_cashflow == 0
    assert split.events[0].ratio == 2
    assert ex.dividend_receivable == 180 and ex.income_cashflow == 0
    assert ex.nav == ex.cash + ex.positions[0].valuation + ex.dividend_receivable
    assert payment.session_date == date(2025, 1, 2)  # Next supplied session after toy holiday.
    assert payment.dividend_receivable == 0 and payment.income_cashflow == 180
    assert payment.events[0].reference_event_id == ex.events[0].event_id
    assert after.income_cashflow == 0 and after.events == ()
    assert all(day.external_cashflow == 0 for day in ledger.days)
    assert all(mark.quantity.is_integer() for day in ledger.days for mark in day.positions)
    assert all(day.daily_return == day.nav / (10000 if i == 0 else ledger.days[i - 1].nav) - 1
               for i, day in enumerate(ledger.days))
    assert _build(case)[1] == ledger


def test_fees_are_separate_from_trade_cash_and_preserve_terminal_nav(monkeypatch):
    case = _case(monkeypatch, cost_bps=20, with_actions=False)
    result, ledger = _build(case, _started(case, cost_bps=20))
    assert ledger.days[0].trade_net_cashflow == -4500 and ledger.days[0].fees == 9
    assert ledger.total_fees == case[3]["total_cost_usd"] == 9
    assert ledger.days[-1].nav == 9991
    assert result.cagr == case[3]["annualized_return_252_sessions"]
    assert result.max_drawdown == case[3]["max_drawdown"]


@pytest.mark.parametrize("same_day,unpaid", [(True, False), (False, True)])
def test_same_session_payment_or_terminal_receivable(monkeypatch, same_day, unpaid):
    case = _case(monkeypatch, same_day=same_day, unpaid=unpaid)
    _, ledger = _build(case)
    if same_day:
        assert [e.event_type for e in ledger.days[2].events] == ["dividend_accrual", "dividend_payment"]
        assert ledger.days[2].income_cashflow == 180 and ledger.days[2].dividend_receivable == 0
    else:
        assert ledger.days[-1].dividend_receivable == 180
        assert sum(day.income_cashflow for day in ledger.days) == 0


@pytest.mark.parametrize("kind", ["forward_splits", "cash_dividends"])
@pytest.mark.parametrize("conflict", [False, True])
def test_rejects_duplicate_or_conflicting_original_actions(monkeypatch, kind, conflict):
    case = _case(monkeypatch)
    duplicate = deepcopy(case[1][kind][0])
    if conflict:
        duplicate["new_rate" if kind == "forward_splits" else "rate"] = 3.0
    case[1][kind].append(duplicate)
    with pytest.raises(ValueError, match="ACTION_DUPLICATE_OR_CONFLICT"):
        _build(case)


def test_missing_actions_and_wrong_input_identity_reject(monkeypatch):
    case = _case(monkeypatch)
    started = _started(case)
    case[1]["cash_dividends"] = []
    with pytest.raises(ValueError, match="INPUT_IDENTITY_MISMATCH"):
        _build(case, started)
    with pytest.raises(ValueError, match="DIVIDEND_ACCRUAL_MISMATCH"):
        _build(case)  # Even a newly attested digest cannot hide missing dividend facts.


@pytest.mark.parametrize("field,value", [("strategy_profile", "tqqq_growth_income"),
    ("domain", "cn_equity"), ("calendar_id", "XSHG"), ("window_start", date(2024, 12, 27)),
    ("window_end", date(2025, 1, 2)), ("actual_params", None), ("research_identity", None),
    ("cost_inputs", {"cost_bps": 1.0})])
def test_wrong_trial_identity_rejects(monkeypatch, field, value):
    case = _case(monkeypatch)
    with pytest.raises(ValueError, match="TRIAL_IDENTITY_MISMATCH"):
        _build(case, replace(_started(case), **{field: value}))


def test_source_or_scenario_changes_event_identity(monkeypatch):
    case = _case(monkeypatch)
    started = _started(case)
    original = _build(case, started)[1].days[2].events[0].event_id
    scenario = _started(case, scenario="synthetic_control_5_sessions")
    assert _build(case, scenario)[1].days[2].events[0].event_id != original
    source = replace(started, research_identity={**started.research_identity,
                                               "actions_source_id": "synthetic-other-source"})
    assert _build(case, source)[1].days[2].events[0].event_id != original


def test_rejects_wrong_marks_trade_fees_or_summary_without_adjusting_numbers(monkeypatch):
    case = _case(monkeypatch, with_actions=False)
    for field, value in [("trade_shares", 89.0), ("shares", 90.5), ("cost_usd", 1.0),
                         ("tqqq_close", 51.0), ("nav_close", 10000.0000005)]:
        changed = deepcopy(case)
        changed[4][0][field] = value
        with pytest.raises(ValueError):
            _build(changed)
    changed = deepcopy(case)
    changed[3]["end_nav_usd"] += 1
    with pytest.raises(ValueError, match="METRICS_MISMATCH"):
        _build(changed)


def test_valid_simulator_split_plus_trade_is_explicit_qpk_gap(monkeypatch):
    case = _case(monkeypatch, split_trade=1)
    assert case[4][1]["trade_shares"] == 1
    assert case[4][1]["shares"] == 181
    assert all(row["identity_error_usd"] < 1e-6 for row in case[4])
    with pytest.raises(ValueError, match="QPK_SPLIT_TRADE_UNSUPPORTED"):
        _build(case)


def test_zero_entitlement_and_zero_rate_are_explicit_qpk_gaps(monkeypatch):
    case = _case(monkeypatch, zero_rate=True)
    with pytest.raises(ValueError, match="QPK_ZERO_DIVIDEND_UNSUPPORTED"):
        _build(case)
    case = _case(monkeypatch, initial_target=0, with_split=False)
    assert all(row["shares"] == 0 for row in case[4])
    with pytest.raises(ValueError, match="QPK_ZERO_ENTITLEMENT_UNSUPPORTED"):
        _build(case)


def test_sell_cashflow_fees_and_empty_terminal_position(monkeypatch):
    case = _case(monkeypatch, cost_bps=20, with_actions=False, sell_last=True)
    _, ledger = _build(case, _started(case, cost_bps=20))
    assert ledger.days[-1].trade_net_cashflow == 4500
    assert ledger.days[-1].fees == 9 and ledger.days[-1].positions == ()
    assert ledger.total_fees == 18 and ledger.days[-1].nav == 9982


def test_qpk_rejects_dropped_wrong_reference_or_duplicate_events(monkeypatch):
    case = _case(monkeypatch)
    _, ledger = _build(case)
    for tamper in ("dropped", "reference", "duplicate"):
        days = list(ledger.days)
        payment = days[3]
        if tamper == "dropped":
            days[3] = replace(payment, events=())
        elif tamper == "reference":
            event = replace(payment.events[0], reference_event_id="synthetic-wrong-source")
            days[3] = replace(payment, events=(event,))
        else:
            event = replace(payment.events[0], event_id=days[2].events[0].event_id)
            days[3] = replace(payment, events=(event,), declared_event_ids=(event.event_id,))
        with pytest.raises(ValueError, match="ledger_event_set|ledger_dividend_pair|ledger_event_duplicate"):
            replace(ledger, days=tuple(days))


@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), -1.0])
def test_rejects_invalid_source_numbers(monkeypatch, value):
    case = _case(monkeypatch, with_actions=False)
    case[4][0]["cost_usd"] = value
    with pytest.raises(ValueError):
        _build(case)


def test_scenario_cost_session_and_external_flow_scope_must_be_explicit(monkeypatch):
    case = _case(monkeypatch)
    started = _started(case)
    for key, value in [("sessions", 4), ("use_guard", 1), ("scenario_id", " "),
                       ("cost_bps", True)]:
        with pytest.raises(ValueError, match="TRIAL_IDENTITY_MISMATCH"):
            _build(case, replace(started, actual_params={**started.actual_params, key: value}))
    with pytest.raises(ValueError, match="TRIAL_IDENTITY_MISMATCH"):
        _build(case, replace(started, research_identity={**started.research_identity,
                "external_cashflow_scope": "unspecified"}))


def test_complete_source_action_payload_changes_bound_identity(monkeypatch):
    case = _case(monkeypatch)
    prior = _build(case)[1].days[2].events[0].event_id
    case[1]["cash_dividends"][0]["process_date"] = "2024-12-31"
    assert _build(case)[1].days[2].events[0].event_id != prior


def test_missing_split_event_cannot_be_relabelled_as_a_trade(monkeypatch):
    case = _case(monkeypatch)
    case[1]["forward_splits"] = []
    with pytest.raises(ValueError, match="SPLIT_EVENT_MISMATCH"):
        _build(case)


def test_short_window_keeps_unpaid_receivable_and_exact_identity(monkeypatch):
    case = _case(monkeypatch, sessions=3)
    result, ledger = _build(case)
    assert result.observation_count == ledger.observation_count == 3
    assert result.start_date == ledger.window_start == date(2024, 12, 26)
    assert result.end_date == ledger.window_end == date(2024, 12, 31)
    assert ledger.days[-1].dividend_receivable == 180 and ledger.days[-1].nav == 10000


def test_non_session_effective_action_is_missing_semantics(monkeypatch):
    case = _case(monkeypatch)
    case[1]["cash_dividends"][0]["ex_date"] = "2025-01-01"
    with pytest.raises(ValueError, match="ACTION_EFFECTIVE_SESSION_MISSING"):
        _build(case)


def test_nonzero_external_flow_conflicts_with_explicit_closed_scope(monkeypatch):
    case = _case(monkeypatch, with_actions=False)
    case[4][0]["external_cashflow"] = 10.0
    with pytest.raises(ValueError, match="EXTERNAL_FLOW_SCOPE_MISMATCH"):
        _build(case)


def test_every_result_and_ledger_identity_field_matches_caller(monkeypatch):
    case = _case(monkeypatch)
    started = _started(case)
    result, ledger = _build(case, started)
    for field in ("domain", "strategy_profile", "calendar_id", "periods_per_year"):
        assert getattr(result, field) == getattr(ledger, field) == getattr(started, field)
    assert result.param_set_id == started.param_set_id
    assert result.source_revision == started.source_revision
    assert result.cost_model == ledger.cost_source == started.cost_source
    assert result.cost_inputs == ledger.cost_inputs == started.cost_inputs
    assert {k: v for k, v in result.params.items() if k != "research_identity"} == started.actual_params
    assert started.status is ResearchTrialStatus.STARTED and started.run_id is None
    assert result.validation_identity is None and result.oos_sharpe is None


def test_event_id_binds_exact_original_record_source_index_and_scenario(monkeypatch):
    case = _case(monkeypatch)
    started = _started(case)
    _, ledger = _build(case, started)
    expected = hashlib.sha256(json.dumps({
        "source_id": started.research_identity["actions_source_id"],
        "input_sha256": started.research_identity["guard_cash_input_sha256"],
        "trial_id": started.trial_id, "scenario_id": started.actual_params["scenario_id"],
        "source_array": "cash_dividends", "source_index": 0,
        "original_action": case[1]["cash_dividends"][0], "accounting_phase": "dividend_accrual",
    }, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    assert ledger.days[2].events[0].event_id == "tqqq-source-event-sha256:" + expected
    assert ledger.days[2].events[0].event_id != case[1]["cash_dividends"][0]["id"]


@pytest.mark.parametrize("change", ["symbol", "missing_array", "unsupported_action"])
def test_wrong_or_incomplete_source_actions_reject(monkeypatch, change):
    case = _case(monkeypatch)
    if change == "symbol":
        case[1]["cash_dividends"][0]["symbol"] = "QQQ"
    elif change == "missing_array":
        del case[1]["cash_dividends"]
    else:
        case[1]["reverse_splits"] = [{"ex_date": "2024-12-31", "new_rate": 1, "old_rate": 2}]
    with pytest.raises(ValueError, match="ACTION_IDENTITY_INVALID|ACTION_SET_INCOMPLETE_OR_UNSUPPORTED"):
        _build(case)


def test_explicit_empty_unused_provider_action_arrays_are_retained(monkeypatch):
    case = _case(monkeypatch)
    case[1]["reverse_splits"] = []
    _, ledger = _build(case)
    assert ledger.days[2].dividend_receivable == 180
    assert case[1]["reverse_splits"] == []


def _guard_case(monkeypatch, *, use_guard, risk_route="risk_off"):
    rows, actions, contract, _, _ = _case(monkeypatch)
    original_decision = simulator._decision

    def synthetic_guard_decision(rows, signal_index, *, use_guard, **kwargs):
        decision = original_decision(rows, signal_index, use_guard=use_guard, **kwargs)
        if signal_index >= 258:
            decision["guard_route"] = risk_route
            decision["applied_route"] = risk_route if use_guard else "no_action"
            if use_guard:
                decision["target_tqqq_usd"] *= 0.0 if risk_route == "risk_off" else 0.5
                decision["target_tqqq_ratio"] = decision["target_tqqq_usd"] / kwargs["nav"]
        return decision

    monkeypatch.setattr(simulator, "_decision", synthetic_guard_decision)
    metrics, daily_rows = simulator._simulate(rows, actions, contract, cost_bps=0,
                                               use_guard=use_guard, sessions=5)
    return rows, actions, contract, metrics, daily_rows


@pytest.mark.parametrize("risk_route", ["risk_off", "risk_reduced"])
def test_guard_on_output_cannot_be_attributed_to_guard_off_params(monkeypatch, risk_route):
    case = _guard_case(monkeypatch, use_guard=True, risk_route=risk_route)
    started = _started(case)
    forged = replace(started, actual_params={**started.actual_params, "use_guard": False})
    assert forged.research_identity["guard_cash_input_sha256"] == started.research_identity["guard_cash_input_sha256"]
    assert risk_route in [row["signal_applied_route"] for row in case[4]]
    with pytest.raises(ValueError, match="GUARD_SCENARIO_CONTRADICTION"):
        _build(case, forged)
    result, ledger = _build(case, started)
    assert result.params["use_guard"] is True and ledger.days[-1].nav == case[3]["end_nav_usd"]


def test_guard_off_may_keep_raw_risk_off_signal_while_applying_no_action(monkeypatch):
    case = _guard_case(monkeypatch, use_guard=False)
    started = _started(case)
    started = replace(started, actual_params={**started.actual_params, "use_guard": False})
    assert "risk_off" in [row["signal_guard_route"] for row in case[4]]
    assert all(row["signal_applied_route"] == "no_action" for row in case[4])
    result, ledger = _build(case, started)
    assert result.params["use_guard"] is False
    assert ledger.days[-1].positions[0].quantity == 180


def test_same_route_output_cannot_prove_which_guard_parameter_was_used(monkeypatch):
    case = _case(monkeypatch)
    started = _started(case)
    assert all(row["signal_applied_route"] == "no_action" for row in case[4])
    on_result, on_ledger = _build(case, started)
    off_result, off_ledger = _build(case, replace(started, actual_params={**started.actual_params, "use_guard": False}))
    # A pure adapter cannot distinguish these asserted params from identical output.
    # Admission must remain with the trusted caller's single frozen parameter path.
    assert on_ledger == off_ledger
    assert on_result.params["use_guard"] is True and off_result.params["use_guard"] is False
    assert on_result.validation_identity is off_result.validation_identity is None


@pytest.mark.parametrize("route", [None, "unsupported_route", "risk_on", ["no_action"], {"route": "no_action"}, True])
def test_missing_or_unknown_applied_route_is_not_accepted(monkeypatch, route):
    case = _case(monkeypatch)
    if route is None:
        del case[4][0]["signal_applied_route"]
    else:
        case[4][0]["signal_applied_route"] = route
    with pytest.raises(ValueError, match="SIMULATOR_ROUTE_INVALID"):
        _build(case)
