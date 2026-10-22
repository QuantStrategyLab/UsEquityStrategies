"""R-DATA-01: artificial fixtures, local temporary stores, network denied."""
from copy import deepcopy
from datetime import datetime
import json
import socket

import pandas as pd
import pytest

from us_equity_strategies.research import rs04_netting_candidate as caller
from us_equity_strategies.research.fractional_kelly_walk_forward_plan import WalkForwardPlan
from us_equity_strategies.research.lookahead_smoke import assert_no_lookahead
from us_equity_strategies.research.pit_session_inputs import (
    PIT_SESSION_SCHEMA, session_payload_digests, validate_pit_session_inputs,
)
from us_equity_strategies.research.trial_inventory import read_trial_inventory
from test_rs04_netting_candidate import (
    r1_mechanism_fixture, R1_FEE_RESERVE_POLICY, _m1_implementation, requires_security_netting,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("R_DATA_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def _provenance(body, revision):
    return {"schema_version": PIT_SESSION_SCHEMA, "producer_id": "ARTIFICIAL_FIXTURE_ONLY",
        "source_revision": revision, "sessions": [{"session": session,
            "payload_sha256": digest, "event_at": session + "T21:00:00Z",
            "first_seen": session + "T21:01:00Z", "persisted_at": session + "T21:02:00Z",
            "decision_cutoff": session + "T21:05:00Z"}
            for session, digest in session_payload_digests(body).items()]}


def _tiny_body():
    return {"sessions": ["2030-01-02", "2030-01-03"],
        "bars": {"QQQ": [["2030-01-02", 100., 101.], ["2030-01-03", 102., 103.]]},
        "actions": {"QQQ": {"splits": [], "dividends": [["2030-01-03", .1]]}}}


def test_pit_normal_utc_inputs_and_complete_content_binding():
    body = _tiny_body()
    provenance = _provenance(body, "fixture-v1")
    before = deepcopy(provenance)
    rows = validate_pit_session_inputs(body, provenance)
    assert rows["2030-01-02"]["first_seen"] == "2030-01-02T21:01:00+00:00"
    assert provenance == before


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "future_first_seen", "late_persist",
    "unknown_first_seen", "naive_time", "wrong_day", "revised_bar", "revised_action"])
def test_pit_missing_late_and_revised_inputs_are_refused(mutation):
    body = _tiny_body()
    provenance = _provenance(body, "fixture-v1")
    row = provenance["sessions"][0]
    if mutation == "missing":
        provenance["sessions"].pop()
    elif mutation == "duplicate":
        provenance["sessions"][1] = deepcopy(row)
    elif mutation == "future_first_seen":
        row["first_seen"] = row["persisted_at"] = "2030-01-02T21:06:00Z"
    elif mutation == "late_persist":
        row["persisted_at"] = "2030-01-02T21:06:00Z"
    elif mutation == "unknown_first_seen":
        row["first_seen"] = None
    elif mutation == "naive_time":
        row["first_seen"] = "2030-01-02T21:01:00"
    elif mutation == "wrong_day":
        row["decision_cutoff"] = "2030-01-03T21:05:00Z"
    elif mutation == "revised_bar":
        body["bars"]["QQQ"][0][2] = 999.
    else:
        body["actions"]["QQQ"]["dividends"][0][1] = 999.
    with pytest.raises(ValueError):
        validate_pit_session_inputs(body, provenance)


@requires_security_netting
def test_r1_first_seen_persisted_and_actual_cutoff_consumed(tmp_path, monkeypatch):
    from us_equity_strategies.research import bayesian_kelly
    args, body, days = r1_mechanism_fixture(tmp_path, evaluation_sessions=4)
    provenance = _provenance(body, args["source_revision"])
    args.update(pit_session_inputs=provenance, r1_fee_reserve_policy=deepcopy(R1_FEE_RESERVE_POLICY))
    observed = []
    native_update = bayesian_kelly.update_b4
    def update(state, observation, **kwargs):
        observed.append((observation, kwargs["decision_cutoff"]))
        return native_update(state, observation, **kwargs)
    monkeypatch.setattr(bayesian_kelly, "update_b4", update)
    result = caller.run_registered_r0_r3_batch(**args)
    assert result["status"] == "succeeded"
    record = args["store"].load_research_trial("us_equity", "r0_r3_batch_additive_audit_v1", args["trial_id"])
    assert record.actual_params["pit_session_inputs"] == provenance
    assert observed
    for observation, cutoff in observed:
        assert observation.observed_at.hour == observation.available_at.hour == cutoff.hour == 21
        assert observation.available_at.minute == 2 and cutoff.minute == 5
        assert observation.observed_at <= observation.available_at <= cutoff
    assert result["PIT"] == "NOT_ATTESTED"
    inventory = read_trial_inventory(args["store"])
    assert inventory["recorded_trial_count"] == 1
    trial = inventory["trials"][0]
    assert trial["status"] == "succeeded" and trial["result_pointer"]
    assert datetime.fromisoformat(trial["started_at"]) <= datetime.fromisoformat(trial["terminal_at"])
    before = {path: path.read_bytes() for path in args["store"].local_root.rglob("*") if path.is_file()}
    resumed = caller.run_registered_r0_r3_batch(**(args | {"load_input_body": lambda: pytest.fail("replay")}))
    assert resumed["resumed_without_compute"]
    assert before == {path: path.read_bytes() for path in args["store"].local_root.rglob("*") if path.is_file()}
    revised = deepcopy(provenance)
    revised["sessions"][0]["first_seen"] = revised["sessions"][0]["session"] + "T21:01:30Z"
    with pytest.raises(ValueError, match="research_trial_conflict"):
        caller.run_registered_r0_r3_batch(**(args | {"pit_session_inputs": revised}))
    assert before == {path: path.read_bytes() for path in args["store"].local_root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("index", [0, 562, 564])
def test_r1_late_warmup_training_or_evaluation_never_enters_model(tmp_path, monkeypatch, index):
    args, body, days = r1_mechanism_fixture(tmp_path, evaluation_sessions=4)
    provenance = _provenance(body, args["source_revision"])
    row = provenance["sessions"][index]
    row["first_seen"] = row["persisted_at"] = row["session"] + "T21:06:00Z"
    args["pit_session_inputs"] = provenance
    monkeypatch.setattr(caller, "_r0_r3_generate", lambda *args, **kwargs: pytest.fail("late input consumed"))
    with pytest.raises(ValueError, match="PIT_SESSION_NOT_KNOWN_AT_DECISION"):
        caller.run_registered_r0_r3_batch(**args)
    inventory = read_trial_inventory(args["store"])
    assert inventory["status_counts"] == {"rejected": 1}
    assert inventory["trials"][0]["parameter_sha256"]


def test_r6_strict_smoke_pit_passes_and_deliberate_future_window_fails():
    closes = pd.Series([100., 101., 102., 300., 400.], index=pd.date_range("2030-01-01", periods=5))
    cutoff = closes.index[2]
    result = assert_no_lookahead(lambda values: values.loc[:cutoff].mean(), closes, cutoff=cutoff)
    assert result.status == "NO_LEAKAGE" and result.promotion_eligible is False
    with pytest.raises(AssertionError, match="LEAKAGE_DETECTED"):
        assert_no_lookahead(lambda values: values.mean(), closes, cutoff=cutoff)
    with pytest.raises(AssertionError, match="NO_FUTURE_MUTATION"):
        assert_no_lookahead(lambda values: 1., closes, cutoff=closes.index[-1])
    with pytest.raises(AssertionError, match="NO_TRIGGER"):
        assert_no_lookahead(lambda values: None, closes, cutoff=cutoff)


@requires_security_netting
def test_r1_future_bars_and_actions_cannot_change_earlier_native_decisions(tmp_path, monkeypatch):
    from us_equity_strategies.research import bayesian_kelly
    args, body, days = r1_mechanism_fixture(tmp_path, evaluation_sessions=4)
    first_future = days[-2].isoformat()
    baseline = deepcopy(body)
    mutated = deepcopy(body)
    for symbol, rows in mutated["bars"].items():
        for row in rows:
            if row[0] >= first_future:
                row[1] *= 3.
                row[2] *= 3.
        mutated["actions"][symbol]["dividends"].append([first_future, .5])
    captured = []
    native_target = bayesian_kelly.b4_fractional_target
    def target(*args, **kwargs):
        result = native_target(*args, **kwargs)
        captured.append(result)
        return result
    monkeypatch.setattr(bayesian_kelly, "b4_fractional_target", target)
    targets = []
    for label, input_body in (("baseline", baseline), ("mutated", mutated)):
        projection = deepcopy(args["projection"])
        if label == "mutated":
            for group in projection["groups"]:
                if group["kind"] == "actions":
                    group["count"] += 1
        captured.clear()
        result = caller.run_registered_r0_r3_batch(**(args | {
            "trial_id": label, "projection": projection,
            "implementation_sha256": _m1_implementation(),
            "input_body_sha256": caller._r0_r3_sha(input_body),
            "load_input_body": lambda: deepcopy(input_body),
            "pit_session_inputs": _provenance(input_body, args["source_revision"]),
            "r1_fee_reserve_policy": deepcopy(R1_FEE_RESERVE_POLICY)}))
        assert result["status"] == "succeeded"
        targets.append(tuple(captured[:2]))
    assert targets[0] == targets[1]
    assert any(target.targets is not None for target in targets[0])


@pytest.mark.parametrize("count", [-1, True, 10.5, None])
def test_r2_invalid_sample_count_does_not_make_unbounded_folds(count):
    with pytest.raises(ValueError, match="SAMPLE_COUNT_INVALID"):
        WalkForwardPlan(10, 2, 1).folds(count)


@pytest.mark.parametrize("factor", [1., 0., -1., float("nan"), float("inf"), True])
def test_r6_strict_smoke_refuses_noop_mutation(factor):
    closes = pd.Series([1., 2.], index=pd.date_range("2030-01-01", periods=2))
    with pytest.raises(ValueError, match="MUTATION_INVALID"):
        assert_no_lookahead(lambda values: 1., closes, cutoff=closes.index[0], future_factor=factor)


def test_r2_nested_purge_and_embargo_have_independent_interval_oracle():
    plan = WalkForwardPlan(40, 5, 3, label_horizon_sessions=3, embargo_sessions=4)
    for fold in plan.nested_folds(100, inner_splits=3):
        for start, train_end, val_start, val_end in (fold.outer, *fold.inner):
            assert all(index + 3 < val_start for index in range(start, train_end))
            assert val_end <= fold.outer[3]
        for previous, current in zip(fold.inner, fold.inner[1:]):
            assert set(range(previous[3], previous[3] + 4)).isdisjoint(range(current[2], current[3]))
            assert current[2] - previous[3] == 4
        assert all(val_end <= fold.outer[1] for _, _, _, val_end in fold.inner)


@pytest.mark.parametrize("error,status", [(RuntimeError("fixture"), "failed"),
    (ValueError("fixture"), "rejected"), (KeyboardInterrupt(), "aborted"),
    (SystemExit(1), "aborted"), (GeneratorExit(), "aborted")])
def test_r4_failed_and_interrupted_trial_is_counted_before_input(tmp_path, error, status):
    args, body, days = r1_mechanism_fixture(tmp_path, evaluation_sessions=4)
    def fail():
        started = read_trial_inventory(args["store"])
        assert started["status_counts"] == {"started": 1}
        assert started["trials"][0]["started_at"]
        raise error
    args["load_input_body"] = fail
    with pytest.raises(type(error)):
        caller.run_registered_r0_r3_batch(**args)
    inventory = read_trial_inventory(args["store"])
    assert inventory["recorded_trial_count"] == 1 and inventory["status_counts"] == {status: 1}
    trial = inventory["trials"][0]
    assert trial["parameter_sha256"] and trial["input_id"] and trial["source_revision"]
    assert trial["data_window"] == [args["requested_start"].isoformat(), args["requested_end"].isoformat()]
    assert trial["terminal_at"] and trial["result_pointer"] is None
    assert trial["research_identity"]["dependent_cells"] == 2
    assert inventory["effective_trial_count"] is None


def test_r4_missing_trial_returns_do_not_produce_success_only_dsr(monkeypatch):
    from us_equity_strategies.research import trial_overfitting_report as report
    from test_trial_overfitting_report import _FakeStats, _dated, _use
    provider = _FakeStats()
    _use(monkeypatch, provider)
    result = report.compute_trial_set_overfitting(_dated(), selected_trial_id="t0",
        recorded_trial_count=5, annual_risk_free_rate=0., synthetic=True)
    assert result["recorded_trial_count"] == 5 and result["not_succeeded_trial_count"] == 1
    assert result["metrics"]["dsr"] is None
    assert result["metric_reason"]["dsr"] == "INCOMPLETE_TRIAL_RETURN_HISTORY"
    assert "dsr" not in provider.calls


def test_r4_unknown_process_exit_keeps_started_slot_without_inventing_terminal(tmp_path, monkeypatch):
    from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore
    args, body, days = r1_mechanism_fixture(tmp_path, evaluation_sessions=4)
    native_save = PerformanceStore.save_research_trial
    def ambiguous(store, record):
        native_save(store, record)
        raise OSError("ARTIFICIAL_RESPONSE_LOST")
    monkeypatch.setattr(PerformanceStore, "save_research_trial", ambiguous)
    with pytest.raises(OSError, match="RESPONSE_LOST"):
        caller.run_registered_r0_r3_batch(**args)
    inventory = read_trial_inventory(args["store"])
    assert inventory["status_counts"] == {"started": 1}
    assert inventory["trials"][0]["terminal_at"] is None
    monkeypatch.setattr(PerformanceStore, "save_research_trial", native_save)
    result = caller.run_registered_r0_r3_batch(**(args | {"load_input_body": lambda: pytest.fail("unknown replay")}))
    assert result["resumed_without_compute"]
    assert result["status"] == "started"


def test_r4_legacy_record_keeps_unknown_times_and_reader_never_writes(tmp_path):
    from dataclasses import replace
    from quant_platform_kit.strategy_lifecycle.contracts import ResearchTrialStatus
    args, body, days = r1_mechanism_fixture(tmp_path, evaluation_sessions=4)
    args["load_input_body"] = lambda: (_ for _ in ()).throw(RuntimeError("fixture"))
    with pytest.raises(RuntimeError):
        caller.run_registered_r0_r3_batch(**args)
    store = args["store"]
    record = store.load_research_trial("us_equity", "r0_r3_batch_additive_audit_v1", args["trial_id"])
    old = replace(record, trial_id="ARTIFICIAL_LEGACY", status=ResearchTrialStatus.STARTED, reason_code="")
    store.save_research_trial(old)
    before = {path: path.read_bytes() for path in store.local_root.rglob("*") if path.is_file()}
    inventory = read_trial_inventory(store)
    assert inventory["recorded_trial_count"] == 2
    legacy = next(trial for trial in inventory["trials"] if trial["trial_id"] == "ARTIFICIAL_LEGACY")
    assert legacy["started_at"] is legacy["terminal_at"] is None
    assert before == {path: path.read_bytes() for path in store.local_root.rglob("*") if path.is_file()}


def test_r4_missing_start_event_readback_stops_before_input(tmp_path, monkeypatch):
    from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore
    args, body, days = r1_mechanism_fixture(tmp_path, evaluation_sessions=4)
    native_create = PerformanceStore._create_same
    def omit_event(store, key, payload):
        if not key.endswith("started.jsonl"):
            native_create(store, key, payload)
    monkeypatch.setattr(PerformanceStore, "_create_same", omit_event)
    args["load_input_body"] = lambda: pytest.fail("unrecorded trial must not compute")
    with pytest.raises(ValueError, match="TRIAL_EVENT_READBACK_INVALID"):
        caller.run_registered_r0_r3_batch(**args)
    assert read_trial_inventory(args["store"])["status_counts"] == {"failed": 1}
