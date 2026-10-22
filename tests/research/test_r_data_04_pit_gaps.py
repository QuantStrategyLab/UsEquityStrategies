"""R-DATA-04: declared PIT contracts on artificial fixtures, never real receipts.

No historical version selector, action announcement/pay clock or FX-rate consumer
exists on this path. These tests do not attest those missing capabilities.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta
import socket

import pandas as pd
import pytest

from us_equity_strategies.research import kelly_core_return_generator as gen
from us_equity_strategies.research import rs04_netting_candidate as caller
from us_equity_strategies.research.lookahead_smoke import assert_no_lookahead
from us_equity_strategies.research.pit_session_inputs import (
    session_payload_digests, validate_pit_session_inputs,
)
from us_equity_strategies.research.trial_inventory import read_trial_inventory
from test_r_data_foundation import _provenance, _tiny_body
from test_rs04_netting_candidate import (
    R1_FEE_RESERVE_POLICY, _m1_implementation, r1_mechanism_fixture,
    requires_security_netting,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("R_DATA_04_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


@pytest.mark.parametrize("field", ["producer_id", "source_revision"])
@pytest.mark.parametrize("value", [None, "", " \t"])
def test_producer_and_revision_require_explicit_nonblank_declarations(field, value):
    body = _tiny_body()
    provenance = _provenance(body, "ARTIFICIAL_FIXTURE-v1")
    provenance[field] = value
    before = deepcopy((body, provenance))
    with pytest.raises(ValueError, match="^PIT_SESSION_PROVENANCE_INVALID$"):
        validate_pit_session_inputs(body, provenance)
    assert (body, provenance) == before


@pytest.mark.parametrize("left,right,reason", [
    ("event_at", "first_seen", "PIT_SESSION_TIME_ORDER_INVALID"),
    ("first_seen", "persisted_at", "PIT_SESSION_TIME_ORDER_INVALID"),
    ("persisted_at", "decision_cutoff", "PIT_SESSION_NOT_KNOWN_AT_DECISION"),
])
@pytest.mark.parametrize("microseconds", [-1, 0, 1])
def test_declared_time_adjacent_boundaries(left, right, reason, microseconds):
    body = _tiny_body()
    provenance = _provenance(body, "ARTIFICIAL_FIXTURE-v1")
    row = provenance["sessions"][0]
    row[left] = (datetime.fromisoformat(row[right]) + timedelta(microseconds=microseconds)).isoformat()
    before = deepcopy((body, provenance))
    if microseconds > 0:
        with pytest.raises(ValueError, match="^" + reason + "$"):
            validate_pit_session_inputs(body, provenance)
    else:
        result = validate_pit_session_inputs(body, provenance)[row["session"]]
        assert datetime.fromisoformat(result[left]) <= datetime.fromisoformat(result[right])
    assert (body, provenance) == before


def test_equal_times_and_reordered_session_coverage():
    body = _tiny_body()
    provenance = _provenance(body, "ARTIFICIAL_FIXTURE-v1")
    for row in provenance["sessions"]:
        for field in ("event_at", "first_seen", "persisted_at", "decision_cutoff"):
            row[field] = row["session"] + "T21:00:00Z"
    assert list(validate_pit_session_inputs(body, provenance)) == body["sessions"]
    provenance["sessions"].reverse()
    with pytest.raises(ValueError, match="^PIT_SESSION_COVERAGE_INVALID$"):
        validate_pit_session_inputs(body, provenance)


@pytest.mark.parametrize("mutation,reason", [
    ("producer", "PIT_SESSION_PROVENANCE_INVALID"),
    ("event", "PIT_SESSION_TIME_ORDER_INVALID"),
    ("first_seen", "PIT_SESSION_TIME_ORDER_INVALID"),
    ("persisted", "PIT_SESSION_NOT_KNOWN_AT_DECISION"),
    ("unknown", "RS04_EXPLICIT_UTC_TIME_REQUIRED"),
    ("revision", "PIT_SOURCE_REVISION_MISMATCH"),
])
@requires_security_netting
def test_invalid_pit_stops_before_generator_fit_and_update(tmp_path, monkeypatch, mutation, reason):
    from us_equity_strategies.research import bayesian_kelly as b4
    from us_equity_strategies.research import portfolio_kelly_walk_forward as fit
    args, body, _ = r1_mechanism_fixture(tmp_path, evaluation_sessions=4)
    provenance = _provenance(body, args["source_revision"])
    row = provenance["sessions"][0]
    if mutation == "producer":
        provenance["producer_id"] = " \t"
    elif mutation == "event":
        row["event_at"] = row["first_seen"][:-1] + ".000001Z"
    elif mutation == "first_seen":
        row["first_seen"] = row["persisted_at"][:-1] + ".000001Z"
    elif mutation == "persisted":
        row["persisted_at"] = row["decision_cutoff"][:-1] + ".000001Z"
    elif mutation == "unknown":
        row["first_seen"] = None
    else:
        provenance["source_revision"] += "-other"
        args["load_input_body"] = lambda: pytest.fail("revision mismatch read body")
    def forbidden(*args, **kwargs):
        pytest.fail("invalid PIT reached generation or model")
    for owner, name in ((caller, "_r0_r3_generate"), (fit, "ledoit_wolf"),
                        (b4, "initialise_b4"), (b4, "update_b4")):
        monkeypatch.setattr(owner, name, forbidden)
    with pytest.raises(ValueError, match="^" + reason + "$"):
        caller.run_registered_r0_r3_batch(**(args | {"pit_session_inputs": provenance}))
    inventory = read_trial_inventory(args["store"])
    assert inventory["status_counts"] == {"rejected": 1}


@pytest.mark.parametrize("kind", ["close", "splits", "dividends"])
def test_revised_content_and_late_receipt_do_not_replace_old_cutoff_bundle(kind):
    original = _tiny_body()
    original["actions"]["QQQ"] = {"splits": [], "dividends": []}
    if kind != "close":
        original["actions"]["QQQ"][kind] = [[original["sessions"][1], 2. if kind == "splits" else .1]]
    v1 = _provenance(original, "ARTIFICIAL_FIXTURE-v1")
    validated_v1 = validate_pit_session_inputs(original, v1)
    revised = deepcopy(original)
    if kind == "close":
        revised["bars"]["QQQ"][1][2] += 1.
    else:
        revised["actions"]["QQQ"][kind][0][1] *= 2.
    with pytest.raises(ValueError, match="^PIT_SESSION_CONTENT_CONFLICT$"):
        validate_pit_session_inputs(revised, v1)
    # A new digest/version does not erase the declared later receipt. The caller
    # supplies both versions: this is rejection, not automatic version selection.
    v2 = _provenance(revised, "ARTIFICIAL_FIXTURE-v2")
    row = v2["sessions"][1]
    row["first_seen"] = row["persisted_at"] = row["session"] + "T21:06:00Z"
    with pytest.raises(ValueError, match="^PIT_SESSION_NOT_KNOWN_AT_DECISION$"):
        validate_pit_session_inputs(revised, v2)
    row["decision_cutoff"] = row["persisted_at"]
    assert validate_pit_session_inputs(revised, v2)[row["session"]]["payload_sha256"] != validated_v1[row["session"]]["payload_sha256"]
    assert validate_pit_session_inputs(original, v1) == validated_v1


def _files(store):
    return {str(p.relative_to(store.local_root)): p.read_bytes()
            for p in store.local_root.rglob("*") if p.is_file()}


def _run(args, body, *, label, revision=None):
    projection = deepcopy(args["projection"])
    for group in projection["groups"]:
        if group["kind"] == "actions":
            group["count"] = sum(len(rows) for rows in body["actions"][group["symbol"]].values())
    revision = args["source_revision"] if revision is None else revision
    return caller.run_registered_r0_r3_batch(**(args | {
        "trial_id": label, "source_revision": revision, "projection": projection,
        "input_body_sha256": caller._r0_r3_sha(body), "implementation_sha256": _m1_implementation(),
        "load_input_body": lambda: deepcopy(body), "pit_session_inputs": _provenance(body, revision),
        "r1_fee_reserve_policy": deepcopy(R1_FEE_RESERVE_POLICY)}))


@requires_security_netting
def test_rehashed_revision_conflicts_without_changing_saved_history(tmp_path):
    args, body, _ = r1_mechanism_fixture(tmp_path, evaluation_sessions=4)
    first = _run(args, body, label="ARTIFICIAL_FIXTURE-v1")
    before = _files(args["store"])
    revised = deepcopy(body)
    revised["bars"]["TQQQ"][-2][2] *= 1.1
    with pytest.raises(ValueError, match="^research_trial_conflict$"):
        _run(args, revised, label="ARTIFICIAL_FIXTURE-v1", revision="ARTIFICIAL_FIXTURE-v2")
    assert _files(args["store"]) == before
    second = _run(args, revised, label="ARTIFICIAL_FIXTURE-v2", revision="ARTIFICIAL_FIXTURE-v2")
    assert first["status"] == second["status"] == "succeeded"
    assert first["trial"]["input_id"] != second["trial"]["input_id"]
    assert first["fingerprint"] != second["fingerprint"]
    assert first["PIT"] == second["PIT"] == "NOT_ATTESTED"
    after = _files(args["store"])
    assert all(after[name] == content for name, content in before.items())


def _returns(body, symbol):
    bars = [gen.RawBar(date.fromisoformat(d), o, c) for d, o, c in body["bars"][symbol]]
    actions = gen.CorporateActions(**{kind: tuple((date.fromisoformat(d), v) for d, v in rows)
        for kind, rows in body["actions"][symbol].items()})
    return gen.total_return_series(bars, actions, tuple(map(date.fromisoformat, body["sessions"])))


@pytest.mark.parametrize("symbol", ["TQQQ", "QQQ", "QQQM", "SOXL", "SOXX", "BOXX"])
@pytest.mark.parametrize("kind", ["open", "close", "splits", "dividends"])
@requires_security_netting
def test_each_asset_future_input_preserves_native_prefix(tmp_path, monkeypatch, symbol, kind):
    args, body, days = r1_mechanism_fixture(tmp_path, evaluation_sessions=4)
    first_future = days[-2].isoformat()
    revised = deepcopy(body)
    if kind in ("open", "close"):
        for row in revised["bars"][symbol]:
            if row[0] >= first_future:
                row[1 if kind == "open" else 2] *= 1.1
    else:
        revised["actions"][symbol][kind].append([first_future, 2. if kind == "splits" else .5])
    old_hashes, new_hashes = session_payload_digests(body), session_payload_digests(revised)
    assert all(old_hashes[d] == new_hashes[d] for d in body["sessions"] if d < first_future)
    assert old_hashes[first_future] != new_hashes[first_future]
    if kind != "open":
        assert _returns(body, symbol)[-2:] != _returns(revised, symbol)[-2:]
    prepared = []
    native_prepare = caller._r0_r3_prepare_signal_inputs
    def prepare(*a, **kw):
        result = native_prepare(*a, **kw)
        prepared.append(result)
        return result
    monkeypatch.setattr(caller, "_r0_r3_prepare_signal_inputs", prepare)
    baseline = _run(args, body, label="ARTIFICIAL_FIXTURE-baseline")
    mutated = _run(args, revised, label="ARTIFICIAL_FIXTURE-mutated")
    assert baseline["status"] == mutated["status"] == "succeeded"
    assert baseline["PIT"] == mutated["PIT"] == "NOT_ATTESTED"
    assert len(prepared) == 2
    boundary = body["sessions"].index(first_future)
    for profile in ("TQQQ", "SOXL"):
        a, b = (p["paths"][profile] for p in prepared)
        assert a.gross_returns[:boundary] == b.gross_returns[:boundary]
        assert a.weights[:boundary] == b.weights[:boundary]
        assert {s: r[:boundary] for s, r in a.asset_returns.items()} == {s: r[:boundary] for s, r in b.asset_returns.items()}
    a, b = baseline["engineering"], mutated["engineering"]
    assert a["training"] == b["training"]
    prefix_a = [r for r in a["model_decisions"] if r["session"] < first_future]
    prefix_b = [r for r in b["model_decisions"] if r["session"] < first_future]
    assert prefix_a == prefix_b
    assert any(r["targets"] is not None for r in prefix_a)
    for cell, packet in a["cells"].items():
        earlier = [r for r in packet["ledger"] if r["session"] < first_future]
        assert earlier  # includes actual returns, trades, fees and book values
        assert earlier == [r for r in b["cells"][cell]["ledger"] if r["session"] < first_future]


def test_future_split_back_adjustment_conflicts_with_historical_payload():
    body = _tiny_body()
    provenance = _provenance(body, "ARTIFICIAL_FIXTURE-v1")
    revised = deepcopy(body)
    revised["actions"]["QQQ"]["splits"].append([body["sessions"][-1], 2.])
    # Deliberately unsafe back-adjustment changes a price already bound at the
    # earlier cutoff. A close-only future smoke would never mutate this prefix.
    revised["bars"]["QQQ"][0][1] /= 2.
    revised["bars"]["QQQ"][0][2] /= 2.
    assert session_payload_digests(body)[body["sessions"][0]] != session_payload_digests(revised)[body["sessions"][0]]
    with pytest.raises(ValueError, match="^PIT_SESSION_CONTENT_CONFLICT$"):
        validate_pit_session_inputs(revised, provenance)


@pytest.mark.parametrize("currency", ["EUR", "HKD", "USD/EUR"])
@requires_security_netting
def test_non_usd_source_refused_before_input_read(tmp_path, currency):
    args, _, _ = r1_mechanism_fixture(tmp_path, evaluation_sessions=4)
    args["projection"]["manifest_metadata"]["currency"] = currency
    args["load_input_body"] = lambda: pytest.fail("non-USD source read body")
    with pytest.raises(ValueError, match="^UNAVAILABLE_SOURCE_RAW_ACTION_CALENDAR_CONTRACT$"):
        caller.run_registered_r0_r3_batch(**args)
    assert not args["store"].local_root.exists()


def test_common_future_scaling_can_miss_ratio_leakage():
    closes = pd.Series([1., 2., 10., 20.], index=pd.date_range("2030-01-01", periods=4))
    cutoff = closes.index[1]
    def unsafe_ratio(values):
        return values.iloc[-1] / values.iloc[-2]
    # Record the blind spot explicitly; this result is never a PIT assertion.
    result = assert_no_lookahead(unsafe_ratio, closes, cutoff=cutoff)
    assert result.status == "NO_LEAKAGE" and result.promotion_eligible is False
    independent = closes.copy()
    independent.iloc[-1] *= 2.
    assert unsafe_ratio(independent) != unsafe_ratio(closes)
    with pytest.raises(AssertionError, match="^LEAKAGE_DETECTED$"):
        assert_no_lookahead(lambda values: values.iloc[-1], closes, cutoff=cutoff)
