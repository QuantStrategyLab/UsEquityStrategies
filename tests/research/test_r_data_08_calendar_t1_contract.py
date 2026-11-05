"""Artificial T1 examples against the existing validator, not a calendar feed.

Carried forward unchanged in behavior from the R-DATA-08 local evidence.
B/U1 structural tests live in test_calendar_contract.py.
This file does not implement them or attest a market, receipt, or PIT history.
"""
from copy import deepcopy

import pytest

from us_equity_strategies.research.pit_session_inputs import (
    PIT_SESSION_SCHEMA, session_payload_digests, validate_pit_session_inputs,
)


def _bundle():
    day = "2098-12-30"
    body = {"sessions": [day], "bars": {"ARTIFICIAL": [[day, 1., 2.]]},
            "actions": {"ARTIFICIAL": {"splits": [], "dividends": []}}}
    provenance = {"schema_version": PIT_SESSION_SCHEMA,
        "producer_id": "ARTIFICIAL_FIXTURE_ONLY",
        "source_revision": "ARTIFICIAL_R_DATA_08_v1", "sessions": [{
            "session": day, "payload_sha256": session_payload_digests(body)[day],
            "event_at": day + "T23:59:59.999996Z",
            "first_seen": day + "T23:59:59.999997Z",
            "persisted_at": day + "T23:59:59.999998Z",
            "decision_cutoff": day + "T23:59:59.999999Z"}]}
    return body, provenance


@pytest.mark.parametrize("case", ["before_midnight", "equal_at_cutoff", "explicit_zero_offset"])
def test_t1_same_utc_day_is_accepted_without_mutating_inputs(case):
    body, provenance = _bundle()
    row = provenance["sessions"][0]
    fields = ("event_at", "first_seen", "persisted_at", "decision_cutoff")
    if case == "equal_at_cutoff":
        for field in fields:
            row[field] = row["decision_cutoff"]
    elif case == "explicit_zero_offset":
        for field in fields:
            row[field] = row[field].replace("Z", "+00:00")
    before = deepcopy((body, provenance))
    accepted = validate_pit_session_inputs(body, provenance)[row["session"]]
    assert accepted["payload_sha256"] == row["payload_sha256"]
    for field in fields:
        assert accepted[field] == row[field].replace("Z", "+00:00")
    assert (body, provenance) == before


@pytest.mark.parametrize("changes,reason", [
    ({"first_seen": "2098-12-31T00:00:00Z", "persisted_at": "2098-12-31T00:00:00Z"},
     "PIT_SESSION_NOT_KNOWN_AT_DECISION"),
    ({"persisted_at": "2098-12-31T00:00:00Z"}, "PIT_SESSION_NOT_KNOWN_AT_DECISION"),
    # Giving a later cutoff preserves the real receipt but still violates T1.
    ({"first_seen": "2098-12-31T00:00:00Z", "persisted_at": "2098-12-31T00:00:00Z",
      "decision_cutoff": "2098-12-31T00:00:01Z"}, "PIT_SESSION_TIME_ORDER_INVALID"),
    ({"decision_cutoff": "2098-12-31T00:00:00Z"}, "PIT_SESSION_TIME_ORDER_INVALID"),
    ({"event_at": "2098-12-29T23:59:59Z"}, "PIT_SESSION_TIME_ORDER_INVALID"),
    ({"event_at": "2098-12-31T00:00:00Z", "first_seen": "2098-12-31T00:00:00Z",
      "persisted_at": "2098-12-31T00:00:00Z", "decision_cutoff": "2098-12-31T00:00:01Z"},
     "PIT_SESSION_TIME_ORDER_INVALID"),
    ({"first_seen": "2098-12-30T23:59:59.999995Z"}, "PIT_SESSION_TIME_ORDER_INVALID"),
    ({"persisted_at": "2098-12-30T23:59:59.999996Z"}, "PIT_SESSION_TIME_ORDER_INVALID"),
    ({"first_seen": None}, "RS04_EXPLICIT_UTC_TIME_REQUIRED"),
    ({"first_seen": "2098-12-30T23:59:59.999997"}, "RS04_EXPLICIT_UTC_TIME_REQUIRED"),
    # An equivalent SGT representation must be explicitly converted upstream.
    ({"first_seen": "2098-12-31T07:59:59.999997+08:00"}, "RS04_EXPLICIT_UTC_TIME_REQUIRED"),
    ({"payload_sha256": "0" * 64}, "PIT_SESSION_CONTENT_CONFLICT"),
])
def test_t1_cross_day_unknown_and_conflicting_inputs_are_refused(changes, reason):
    body, provenance = _bundle()
    provenance["sessions"][0].update(changes)
    before = deepcopy((body, provenance))
    with pytest.raises(ValueError, match="^" + reason + "$"):
        validate_pit_session_inputs(body, provenance)
    assert (body, provenance) == before


def test_t1_relabelling_session_cannot_reuse_previous_day_content():
    body, provenance = _bundle()
    provenance["sessions"][0]["session"] = "2098-12-31"
    before = deepcopy((body, provenance))
    with pytest.raises(ValueError, match="^PIT_SESSION_COVERAGE_INVALID$"):
        validate_pit_session_inputs(body, provenance)
    assert (body, provenance) == before
