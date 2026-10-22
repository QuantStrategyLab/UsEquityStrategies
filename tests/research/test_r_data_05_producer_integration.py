"""Synthetic declared producer/session clocks through the registered consumer.

There is no producer_at or trusted receipt API here: event_at is a caller's
session event declaration. These fixtures never attest real first reception or
select a historical revision automatically. All journal writes use tmp_path.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import socket

import pytest

from us_equity_strategies.research import bayesian_kelly as b4
from us_equity_strategies.research import portfolio_kelly_walk_forward as fit
from us_equity_strategies.research import rs04_netting_candidate as caller
from us_equity_strategies.research.pit_session_inputs import session_payload_digests
from us_equity_strategies.research.trial_inventory import read_trial_inventory
from test_r_data_foundation import _provenance
from test_rs04_netting_candidate import (
    R1_FEE_RESERVE_POLICY, r1_mechanism_fixture, requires_security_netting,
)

pytestmark = requires_security_netting
CLOCKS = ("event_at", "first_seen", "persisted_at", "decision_cutoff")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("R_DATA_05_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


@pytest.fixture
def bundle(tmp_path):
    args, body, _ = r1_mechanism_fixture(tmp_path, evaluation_sessions=4)
    provenance = _provenance(body, args["source_revision"])
    provenance["producer_id"] = "ARTIFICIAL_FIXTURE_R_DATA_05_PRODUCER"
    args.update(pit_session_inputs=provenance,
                r1_fee_reserve_policy=deepcopy(R1_FEE_RESERVE_POLICY))
    # Confirm the consumer registers before reading these in-memory fake bars.
    def load():
        inventory = read_trial_inventory(args["store"])
        assert inventory["status_counts"]["started"] == 1
        return deepcopy(body)
    args["load_input_body"] = load
    return args, body, provenance


def _files(store):
    return {str(p.relative_to(store.local_root)): p.read_bytes()
            for p in store.local_root.rglob("*") if p.is_file()}


def _forbid_compute(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid or replayed PIT reached generator/fit/B4")
    for owner, name in ((caller, "_r0_r3_generate"), (fit, "ledoit_wolf"),
                        (b4, "initialise_b4"), (b4, "update_b4")):
        monkeypatch.setattr(owner, name, forbidden)


@pytest.mark.parametrize("clock_case", ["distinct", "equal", "one_microsecond_before"])
def test_declared_producer_clocks_reach_native_model_and_saved_replay(bundle, monkeypatch, clock_case):
    args, body, provenance = bundle
    for index, row in enumerate(provenance["sessions"]):
        event = datetime.fromisoformat(row["session"] + "T19:00:00+00:00") + timedelta(microseconds=index)
        offsets = (0, 7, 13, 19) if clock_case == "distinct" else (0, 0, 0, 0)
        for field, seconds in zip(CLOCKS, offsets):
            row[field] = (event + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")
        if clock_case == "one_microsecond_before":
            row["decision_cutoff"] = (event + timedelta(microseconds=1)).isoformat()
    before = deepcopy((body, provenance))
    observations, initial = [], []
    native_update, native_initialise = b4.update_b4, b4.initialise_b4
    def update(state, observation, **kwargs):
        observations.append((observation, kwargs["decision_cutoff"]))
        return native_update(state, observation, **kwargs)
    def initialise(spec, *a, **kw):
        initial.append(spec)
        return native_initialise(spec, *a, **kw)
    monkeypatch.setattr(b4, "update_b4", update)
    monkeypatch.setattr(b4, "initialise_b4", initialise)
    result = caller.run_registered_r0_r3_batch(**args)
    assert result["status"] == "succeeded" and result["PIT"] == "NOT_ATTESTED"
    assert (body, provenance) == before
    assert len(initial) == 1 and len(observations) == 3
    rows = {r["session"]: r for r in provenance["sessions"]}
    decisions = result["engineering"]["model_decisions"]
    assert len(decisions) == 4 and any(d["targets"] is not None for d in decisions)
    # Training cutoff uses the same declared clock as subsequent updates.
    assert initial[0].training_cutoff == datetime.fromisoformat(rows[decisions[0]["session"]]["decision_cutoff"])
    for observation, cutoff in observations:
        row = rows[observation.observed_at.date().isoformat()]
        assert observation.observed_at == datetime.fromisoformat(row["event_at"])
        assert observation.available_at == datetime.fromisoformat(row["persisted_at"])
        assert cutoff == datetime.fromisoformat(row["decision_cutoff"])
        if clock_case == "distinct":
            assert observation.observed_at < datetime.fromisoformat(row["first_seen"]) < observation.available_at < cutoff
    for decision in decisions:
        row = rows[decision["session"]]
        expected = dict(row, **{k: datetime.fromisoformat(row[k]).isoformat() for k in CLOCKS})
        assert decision["declared_pit_session"] == expected
    record = args["store"].load_research_trial("us_equity", "r0_r3_batch_additive_audit_v1", args["trial_id"])
    assert record.synthetic is True and record.actual_params["pit_session_inputs"] == provenance
    saved = _files(args["store"])
    _forbid_compute(monkeypatch)
    resumed = caller.run_registered_r0_r3_batch(**(args | {
        "load_input_body": lambda: pytest.fail("replay reread fixture")}))
    assert resumed["resumed_without_compute"] and resumed["status"] == "succeeded"
    assert resumed["PIT"] == "NOT_ATTESTED" and _files(args["store"]) == saved
    assert read_trial_inventory(args["store"])["status_counts"] == {"succeeded": 1}


# Warmup, final training and first evaluation rows of the existing fixture.
@pytest.mark.parametrize("index", [0, 562, 563], ids=["warmup", "training", "evaluation"])
@pytest.mark.parametrize("mutation,reason", [
    ("missing_producer", "PIT_SESSION_PROVENANCE_INVALID"),
    ("blank_producer", "PIT_SESSION_PROVENANCE_INVALID"),
    ("missing_event_at", "PIT_SESSION_PROVENANCE_INVALID"),
    ("missing_first_seen", "PIT_SESSION_PROVENANCE_INVALID"),
    ("missing_persisted_at", "PIT_SESSION_PROVENANCE_INVALID"),
    ("missing_decision_cutoff", "PIT_SESSION_PROVENANCE_INVALID"),
    ("missing_digest", "PIT_SESSION_PROVENANCE_INVALID"),
    ("unknown_first_seen", "RS04_EXPLICIT_UTC_TIME_REQUIRED"),
    ("naive_event", "RS04_EXPLICIT_UTC_TIME_REQUIRED"),
    ("event_after_seen", "PIT_SESSION_TIME_ORDER_INVALID"),
    ("seen_after_persist", "PIT_SESSION_TIME_ORDER_INVALID"),
    ("late_first_seen", "PIT_SESSION_NOT_KNOWN_AT_DECISION"),
    ("late_persist", "PIT_SESSION_NOT_KNOWN_AT_DECISION"),
    ("stale_digest", "PIT_SESSION_CONTENT_CONFLICT"),
])
def test_missing_or_late_producer_bundle_is_journaled_without_compute(bundle, monkeypatch, index, mutation, reason):
    args, body, provenance = bundle
    row = provenance["sessions"][index]
    if mutation == "missing_producer":
        del provenance["producer_id"]
    elif mutation == "blank_producer":
        provenance["producer_id"] = " \t"
    elif mutation == "missing_digest":
        del row["payload_sha256"]
    elif mutation.startswith("missing_"):
        del row[mutation.removeprefix("missing_")]
    elif mutation == "unknown_first_seen":
        row["first_seen"] = None
    elif mutation == "naive_event":
        row["event_at"] = row["event_at"].removesuffix("Z")
    elif mutation == "event_after_seen":
        row["event_at"] = (datetime.fromisoformat(row["first_seen"]) + timedelta(microseconds=1)).isoformat()
    elif mutation == "seen_after_persist":
        row["first_seen"] = (datetime.fromisoformat(row["persisted_at"]) + timedelta(microseconds=1)).isoformat()
    elif mutation == "late_first_seen":
        row["first_seen"] = row["persisted_at"] = (datetime.fromisoformat(row["decision_cutoff"]) + timedelta(microseconds=1)).isoformat()
    elif mutation == "late_persist":
        row["persisted_at"] = (datetime.fromisoformat(row["decision_cutoff"]) + timedelta(microseconds=1)).isoformat()
    else:
        body["bars"]["QQQ"][index][2] *= 1.01
        args["input_body_sha256"] = caller._r0_r3_sha(body)
    before = deepcopy((body, provenance))
    _forbid_compute(monkeypatch)
    with pytest.raises(ValueError, match="^" + reason + "$"):
        caller.run_registered_r0_r3_batch(**args)
    assert (body, provenance) == before
    inventory = read_trial_inventory(args["store"])
    assert inventory["status_counts"] == {"rejected": 1}
    trial, = inventory["trials"]
    assert trial["reason_code"] == "simulation_rejected" and trial["result_pointer"] is None
    assert trial["started_at"] and trial["terminal_at"]


@pytest.mark.parametrize("mutation", ["producer_id", "source_revision", *CLOCKS])
def test_changed_producer_or_receipt_conflicts_before_replay_input(bundle, monkeypatch, mutation):
    args, _, provenance = bundle
    first = caller.run_registered_r0_r3_batch(**args)
    saved = _files(args["store"])
    changed = deepcopy(provenance)
    if mutation in ("producer_id", "source_revision"):
        changed[mutation] += "-v2"
    else:
        row = changed["sessions"][-3]
        row[mutation] = (datetime.fromisoformat(row[mutation]) + timedelta(microseconds=1)).isoformat()
    _forbid_compute(monkeypatch)
    with pytest.raises(ValueError, match="^research_trial_conflict$"):
        caller.run_registered_r0_r3_batch(**(args | {"pit_session_inputs": changed,
            "source_revision": changed["source_revision"],
            "load_input_body": lambda: pytest.fail("conflicting replay read body")}))
    assert first["PIT"] == "NOT_ATTESTED" and _files(args["store"]) == saved
    assert read_trial_inventory(args["store"])["status_counts"] == {"succeeded": 1}


@pytest.mark.parametrize("kind", ["close", "splits", "dividends"])
def test_revised_bundle_needs_new_identity_and_later_declared_cutoff(bundle, kind):
    args, body, provenance = bundle
    first = caller.run_registered_r0_r3_batch(**args)
    saved = _files(args["store"])
    revised, receipt = deepcopy(body), deepcopy(provenance)
    session = body["sessions"][-3]
    if kind == "close":
        revised["bars"]["QQQ"][-3][2] *= 1.01
    else:
        revised["actions"]["QQQ"][kind].append([session, 2. if kind == "splits" else .1])
    projection = deepcopy(args["projection"])
    for group in projection["groups"]:
        if group["kind"] == "actions":
            group["count"] = sum(len(v) for v in revised["actions"][group["symbol"]].values())
    receipt["source_revision"] += "-v2"
    row = receipt["sessions"][-3]
    row["payload_sha256"] = session_payload_digests(revised)[session]
    row["first_seen"] = row["persisted_at"] = row["session"] + "T21:06:00Z"
    changed = args | {"source_revision": receipt["source_revision"], "projection": projection,
        "input_body_sha256": caller._r0_r3_sha(revised), "pit_session_inputs": receipt,
        "load_input_body": lambda: deepcopy(revised)}
    with pytest.raises(ValueError, match="^research_trial_conflict$"):
        caller.run_registered_r0_r3_batch(**changed)
    assert _files(args["store"]) == saved
    with pytest.raises(ValueError, match="^PIT_SESSION_NOT_KNOWN_AT_DECISION$"):
        caller.run_registered_r0_r3_batch(**(changed | {"trial_id": "ARTIFICIAL_FIXTURE-late-v2"}))
    after_rejection = _files(args["store"])
    assert all(after_rejection[p] == v for p, v in saved.items())
    # Caller explicitly supplies the later revision/cutoff. No version lookup.
    row["decision_cutoff"] = row["persisted_at"]
    second = caller.run_registered_r0_r3_batch(**(changed | {"trial_id": "ARTIFICIAL_FIXTURE-later-v2"}))
    assert first["status"] == second["status"] == "succeeded"
    assert first["fingerprint"] != second["fingerprint"]
    assert first["trial"]["input_id"] != second["trial"]["input_id"]
    assert first["PIT"] == second["PIT"] == "NOT_ATTESTED"
    decision = next(d for d in second["engineering"]["model_decisions"] if d["session"] == session)
    assert decision["declared_pit_session"]["first_seen"] == session + "T21:06:00+00:00"
    assert all(_files(args["store"])[p] == v for p, v in after_rejection.items())
    assert read_trial_inventory(args["store"])["status_counts"] == {"succeeded": 2, "rejected": 1}
