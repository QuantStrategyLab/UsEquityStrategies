"""Artificial integration examples and explicit limits of the existing inputs.

The consumer does not select historical revisions, consume action-specific
clocks, validate an exchange holiday feed, or convert FX. Acceptance of ignored
raw metadata below is a gap counterexample, never a PIT/FX/calendar attestation.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import socket

import pytest

from us_equity_strategies.research import rs04_netting_candidate as caller
from us_equity_strategies.research.pit_session_inputs import session_payload_digests
from us_equity_strategies.research.trial_inventory import read_trial_inventory
from test_r_data_foundation import _provenance
from test_r_data_05_producer_integration import _files, _forbid_compute
from test_rs04_netting_candidate import (
    R1_FEE_RESERVE_POLICY, _synthetic_forward_input, _synthetic_forward_state,
    _synthetic_step, r1_mechanism_fixture, requires_security_netting,
)

pytestmark = requires_security_netting


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("R_DATA_06_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


@pytest.fixture
def bundle(tmp_path):
    args, body, _ = r1_mechanism_fixture(tmp_path, evaluation_sessions=4)
    args["r1_fee_reserve_policy"] = deepcopy(R1_FEE_RESERVE_POLICY)
    return args, body


def _options(args, body, label, provenance=None):
    projection = deepcopy(args["projection"])
    for group in projection["groups"]:
        if group["kind"] == "actions":
            group["count"] = sum(map(len, body["actions"][group["symbol"]].values()))
    return args | {"trial_id": "ARTIFICIAL_FIXTURE_R_DATA_06-" + label,
        "projection": projection, "input_body_sha256": caller._r0_r3_sha(body),
        "load_input_body": lambda: deepcopy(body),
        "pit_session_inputs": provenance or _provenance(body, args["source_revision"])}


def _assert_prefix(first, second, before):
    a, b = first["engineering"], second["engineering"]
    assert a["training"] == b["training"]
    prefix = [d for d in a["model_decisions"] if d["session"] < before]
    assert prefix and any(d["targets"] is not None for d in prefix)
    assert prefix == [d for d in b["model_decisions"] if d["session"] < before]
    for cell in a["cells"]:
        rows = [r for r in a["cells"][cell]["ledger"] if r["session"] < before]
        assert rows and rows == [r for r in b["cells"][cell]["ledger"] if r["session"] < before]


@pytest.mark.parametrize("kind", ["close", "splits", "dividends"])
@pytest.mark.parametrize("case", ["stale_digest", "late_revision", "same_time_conflict"])
def test_revision_candidates_are_explicit_and_conflicts_preserve_history(bundle, kind, case):
    args, body = bundle
    session = body["sessions"][-3]
    if kind != "close":
        body["actions"]["QQQ"][kind] = [[session, 2. if kind == "splits" else .1]]
    original = _options(args, body, "v1")
    first = caller.run_registered_r0_r3_batch(**original)
    saved = _files(args["store"])
    revised = deepcopy(body)
    if kind == "close":
        revised["bars"]["QQQ"][-3][2] *= 1.1
    else:
        revised["actions"]["QQQ"][kind][0][1] *= 1.5
    old, new = session_payload_digests(body), session_payload_digests(revised)
    assert old[session] != new[session]
    assert all(old[d] == new[d] for d in old if d < session)
    provenance = _provenance(revised, args["source_revision"] + "-v2")
    row = provenance["sessions"][-3]
    if case == "stale_digest":
        row["payload_sha256"] = old[session]
    elif case == "late_revision":
        row["first_seen"] = row["persisted_at"] = session + "T21:06:00Z"
    changed = _options(args, revised, "v2", provenance) | {"source_revision": provenance["source_revision"]}
    with pytest.raises(ValueError, match="^research_trial_conflict$"):
        caller.run_registered_r0_r3_batch(**(changed | {"trial_id": original["trial_id"],
            "load_input_body": lambda: pytest.fail("conflicting revision read body")}))
    assert _files(args["store"]) == saved
    if case != "same_time_conflict":
        reason = "PIT_SESSION_CONTENT_CONFLICT" if case == "stale_digest" else "PIT_SESSION_NOT_KNOWN_AT_DECISION"
        with pytest.raises(ValueError, match="^" + reason + "$"):
            caller.run_registered_r0_r3_batch(**changed)
        provenance = deepcopy(provenance)
        row = provenance["sessions"][-3]
        row["payload_sha256"] = new[session]
        if case == "late_revision":
            row["decision_cutoff"] = session + "T21:07:00Z"
        changed |= {"trial_id": changed["trial_id"] + "-explicit-correction", "pit_session_inputs": provenance}
    second = caller.run_registered_r0_r3_batch(**changed)
    assert first["status"] == second["status"] == "succeeded"
    assert first["PIT"] == second["PIT"] == "NOT_ATTESTED"
    assert first["trial"]["input_id"] != second["trial"]["input_id"]
    assert all(_files(args["store"])[p] == value for p, value in saved.items())
    _assert_prefix(first, second, session)
    # Equal receipts allow BOTH different candidates under different identities.
    # This counterexample must not be described as historical version selection.
    counts = read_trial_inventory(args["store"])["status_counts"]
    assert counts == ({"succeeded": 2} if case == "same_time_conflict" else {"succeeded": 2, "rejected": 1})


def _archive(root, args, body, *, action_fields=None, fx=None):
    """Write only generated JSON under tmp_path, with real parser hash bindings."""
    inputs, hashes = [], {}
    for symbol in body["bars"]:
        for kind in ("bars", "actions"):
            if kind == "bars":
                payload = {"symbol": symbol, "bars": [
                    {"t": d + "T00:00:00Z", "o": o, "c": c} for d, o, c in body["bars"][symbol]]}
                if symbol == "QQQ" and fx is not None:
                    payload["fx"] = deepcopy(fx)
            else:
                actions = body["actions"][symbol]
                payload = {"symbol": symbol, "corporate_actions": {
                    "forward_splits": [{"ex_date": d, "old_rate": 1., "new_rate": v,
                        **(action_fields or {})} for d, v in actions["splits"]],
                    "cash_dividends": [{"symbol": symbol, "ex_date": d, "rate": v,
                        **(action_fields or {})} for d, v in actions["dividends"]]}}
            payload["next_page_token"] = None
            raw = json.dumps(payload, sort_keys=True, allow_nan=False).encode()
            key = f"{kind}/{symbol}/page-001.json"
            path = root / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            hashes[key] = hashlib.sha256(raw).hexdigest()
            inputs.append({"symbol": symbol, "kind": kind, "complete_pagination": True,
                "pages": [{"sha256": hashes[key], "bytes": len(raw)}]})
    raw = json.dumps({"inputs": inputs}, sort_keys=True).encode()
    (root / "manifest.json").write_bytes(raw)
    hashes["manifest.json"] = hashlib.sha256(raw).hexdigest()
    return {"source_packet_sha256": args["identity_packet_sha256"],
        "manifest_sha256": hashes["manifest.json"], "object_sha256": hashes,
        "parser_sha256": caller.r0_r3_parser_identity()}


def _run_archive(args, body, root, registration, label, monkeypatch, parsed):
    native = caller.parse_registered_r0_r3_archive
    def parse(*a, **kw):
        assert read_trial_inventory(args["store"])["status_counts"].get("started") == 1
        result = native(*a, **kw)
        parsed.append(deepcopy(result["body"]))
        return result
    options = _options(args, body, label) | {"input_body_sha256": None,
        "input_source_registration": registration, "load_input_body": None,
        "load_source_root": lambda: root, "last_close_targets": None}
    with monkeypatch.context() as scoped:
        scoped.setattr(caller, "parse_registered_r0_r3_archive", parse)
        result = caller.run_registered_r0_r3_batch(**options)
    assert result["status"] == "succeeded" and result["PIT"] == "NOT_ATTESTED"
    assert result["derived_input_body_sha256"] == caller._r0_r3_sha(body)
    return result, options


def _assert_same_decisions_and_books(first, second):
    assert first["engineering"]["model_decisions"] == second["engineering"]["model_decisions"]
    assert first["engineering"]["training"] == second["engineering"]["training"]
    for cell, packet in first["engineering"]["cells"].items():
        assert packet["ledger"] == second["engineering"]["cells"][cell]["ledger"]


@pytest.mark.parametrize("kind", ["splits", "dividends"])
@pytest.mark.parametrize("clock_case", ["all_later", "unknown", "missing", "release_later", "received_later"])
def test_action_specific_clocks_are_unconsumed_gap_counterexamples(bundle, tmp_path, monkeypatch, kind, clock_case):
    args, body = bundle
    session = body["sessions"][-3]
    body["actions"]["QQQ"][kind] = [[session, 2. if kind == "splits" else .1]]
    fields = {"announcement_at": session + "T19:00:00Z", "release_at": session + "T19:01:00Z",
        "received_at": session + "T19:02:00Z", "record_date": session, "pay_date": session,
        "revision": "ARTIFICIAL_FIXTURE-v1"}
    changed = deepcopy(fields)
    if clock_case == "all_later":
        for key in ("announcement_at", "release_at", "received_at"):
            changed[key] = session + "T23:59:59Z"
        changed.update(record_date="2099-12-30", pay_date="2099-12-31", revision="ARTIFICIAL_FIXTURE-v2")
    elif clock_case == "unknown":
        changed = dict.fromkeys(fields)
    elif clock_case == "missing":
        changed = {}
    else:
        changed["release_at" if clock_case == "release_later" else "received_at"] = session + "T23:59:59Z"
    roots = (tmp_path / "ARTIFICIAL-action-on-time", tmp_path / "ARTIFICIAL-action-changed")
    registrations = [_archive(root, args, body, action_fields=value) for root, value in zip(roots, (fields, changed))]
    assert registrations[0]["object_sha256"] != registrations[1]["object_sha256"]
    parsed = []
    first, _ = _run_archive(args, body, roots[0], registrations[0], "action-on-time", monkeypatch, parsed)
    second, options = _run_archive(args, body, roots[1], registrations[1], "action-changed", monkeypatch, parsed)
    assert parsed == [body, body]  # The actual parser discarded ALL extra clocks.
    assert first["trial"]["input_id"] != second["trial"]["input_id"]
    _assert_same_decisions_and_books(first, second)
    saved = _files(args["store"])
    _forbid_compute(monkeypatch)
    resumed = caller.run_registered_r0_r3_batch(**(options | {
        "load_source_root": lambda: pytest.fail("replay reread action archive")}))
    assert resumed["resumed_without_compute"] and _files(args["store"]) == saved


@pytest.mark.parametrize("fx_case", ["on_time", "future_quote", "late_receipt", "missing_rate", "unknown_time"])
def test_raw_fx_quotes_are_unconsumed_gap_counterexamples(bundle, tmp_path, monkeypatch, fx_case):
    args, body = bundle
    session = body["sessions"][-3]
    fx = {"scope": "ARTIFICIAL_FIXTURE", "pair": "EUR/USD", "rate": 1.25,
        "quote_at": session + "T19:00:00Z", "release_at": session + "T19:01:00Z",
        "first_seen": session + "T19:02:00Z", "persisted_at": session + "T19:03:00Z"}
    if fx_case == "future_quote":
        fx["quote_at"] = session + "T23:00:00Z"
    elif fx_case == "late_receipt":
        fx["first_seen"] = fx["persisted_at"] = session + "T23:00:00Z"
    elif fx_case == "missing_rate":
        del fx["rate"]
    elif fx_case == "unknown_time":
        fx["quote_at"] = fx["first_seen"] = None
    roots = (tmp_path / "ARTIFICIAL-usd-no-fx", tmp_path / "ARTIFICIAL-usd-extra-fx")
    registrations = [_archive(roots[0], args, body), _archive(roots[1], args, body, fx=fx)]
    assert registrations[0]["object_sha256"] != registrations[1]["object_sha256"]
    parsed = []
    first, _ = _run_archive(args, body, roots[0], registrations[0], "usd-no-fx", monkeypatch, parsed)
    second, _ = _run_archive(args, body, roots[1], registrations[1], "usd-extra-fx", monkeypatch, parsed)
    assert parsed == [body, body]
    _assert_same_decisions_and_books(first, second)


@pytest.mark.parametrize("field,value,reason", [
    ("calendar", None, "UNAVAILABLE_SOURCE_RAW_ACTION_CALENDAR_CONTRACT"),
    ("calendar", "UNKNOWN", "UNAVAILABLE_SOURCE_RAW_ACTION_CALENDAR_CONTRACT"),
    ("calendar", "", "UNAVAILABLE_SOURCE_RAW_ACTION_CALENDAR_CONTRACT"),
    ("calendar", "MISSING", "UNAVAILABLE_SOURCE_SCHEMA"),
    ("currency", None, "UNAVAILABLE_SOURCE_RAW_ACTION_CALENDAR_CONTRACT"),
    ("currency", "", "UNAVAILABLE_SOURCE_RAW_ACTION_CALENDAR_CONTRACT"),
    ("currency", "MISSING", "UNAVAILABLE_SOURCE_SCHEMA"),
    ("currency", "EUR", "UNAVAILABLE_SOURCE_RAW_ACTION_CALENDAR_CONTRACT"),
])
def test_unknown_calendar_or_currency_refused_before_archive_read(bundle, field, value, reason):
    args, body = bundle
    options = _options(args, body, "unknown-source")
    metadata = options["projection"]["manifest_metadata"]
    if value == "MISSING":
        del metadata[field]
    else:
        metadata[field] = value
    options["load_input_body"] = lambda: pytest.fail("invalid calendar/currency read body")
    with pytest.raises(ValueError, match="^" + reason + "$"):
        caller.run_registered_r0_r3_batch(**options)
    assert not args["store"].local_root.exists()


def test_declared_holiday_gap_uses_next_listed_session_and_keeps_pending():
    # Reuse the existing artificial Friday -> Tuesday calendar, not a data feed.
    state = _synthetic_forward_state()
    before = deepcopy(state)
    friday, receipt = _synthetic_step(state, _synthetic_forward_input(state, 1))
    assert state == before
    for book in friday["books"].values():
        assert book["pending"]["fill_session"] == "2026-01-20"
    old = deepcopy(friday)
    tuesday, _ = _synthetic_step(friday, _synthetic_forward_input(friday, 2, shock=.1))
    assert friday == old and tuesday["completed_session"] == "2026-01-20"
    for book in tuesday["books"].values():
        assert book["pending"]["fill_session"] == "2026-01-21"
        assert book["trade_events"] > 0
    replayed, duplicate = _synthetic_step(tuesday, _synthetic_forward_input(state, 1))
    assert replayed == tuesday and duplicate == receipt


@pytest.mark.parametrize("day", ["2026-01-17", "2026-01-19", "2026-01-21"])
def test_unlisted_or_skipped_session_rejected_without_state_change(day):
    state, _ = _synthetic_step(_synthetic_forward_state(), _synthetic_forward_input(_synthetic_forward_state(), 1))
    before = deepcopy(state)
    payload = _synthetic_forward_input(state, 2)
    payload["session"] = day
    for field in ("event_at", "first_seen_at", "persisted_at", "decision_cutoff"):
        payload[field] = day + payload[field][10:]
    with pytest.raises(ValueError, match="^SYNTHETIC_INPUT_SEQUENCE$"):
        _synthetic_step(state, payload)
    assert state == before


@pytest.mark.parametrize("field", ["first_seen_at", "persisted_at"])
def test_post_holiday_late_bundle_rejected_without_filling_pending(field):
    state, _ = _synthetic_step(_synthetic_forward_state(), _synthetic_forward_input(_synthetic_forward_state(), 1))
    before = deepcopy(state)
    payload = _synthetic_forward_input(state, 2)
    late = (datetime.fromisoformat(payload["decision_cutoff"]) + timedelta(microseconds=1)).isoformat()
    payload[field] = late
    if field == "first_seen_at":
        payload["persisted_at"] = late
    with pytest.raises(ValueError, match="^SYNTHETIC_INPUT_LATE_OR_CLOCK$"):
        _synthetic_step(state, payload)
    assert state == before
