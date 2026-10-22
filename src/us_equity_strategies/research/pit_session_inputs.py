"""Declared, content-bound session availability; not producer authentication."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping


PIT_SESSION_SCHEMA = "qsl.research.pit_session_inputs.v1"


def session_payload_digests(body: Mapping) -> dict[str, str]:
    """Bind each session's raw bars and effective actions, including empty lists."""
    payloads = {session: {"bars": {symbol: [] for symbol in body["bars"]},
        "actions": {symbol: {kind: [] for kind in actions}
                    for symbol, actions in body["actions"].items()}} for session in body["sessions"]}
    for symbol, rows in body["bars"].items():
        for row in rows:
            payloads[row[0]]["bars"][symbol].append(row)
    for symbol, actions in body["actions"].items():
        for kind, rows in actions.items():
            for row in rows:
                payloads[row[0]]["actions"][symbol][kind].append(row)
    return {session: hashlib.sha256(json.dumps(payload, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        for session, payload in payloads.items()}


def validate_pit_session_inputs(body: Mapping, provenance: Mapping) -> dict[str, dict]:
    """Refuse missing, revised or late bundles before any model or core generation.

    This conservative contract rejects a late session rather than silently
    dropping it, backfilling it, or reusing its eventual corrected value.
    """
    from .security_netting_runner import _rs04_time

    if (not isinstance(provenance, Mapping)
            or set(provenance) != {"schema_version", "producer_id", "source_revision", "sessions"}
            or provenance["schema_version"] != PIT_SESSION_SCHEMA
            or any(not isinstance(provenance[name], str) or not provenance[name].strip()
                   for name in ("producer_id", "source_revision"))
            or not isinstance(provenance["sessions"], list)):
        raise ValueError("PIT_SESSION_PROVENANCE_INVALID")
    rows = provenance["sessions"]
    if ([row.get("session") for row in rows if isinstance(row, Mapping)] != body["sessions"]
            or len(rows) != len(body["sessions"])):
        raise ValueError("PIT_SESSION_COVERAGE_INVALID")
    digests = session_payload_digests(body)
    result = {}
    fields = ("event_at", "first_seen", "persisted_at", "decision_cutoff")
    for row in rows:
        if set(row) != {"session", "payload_sha256", *fields}:
            raise ValueError("PIT_SESSION_PROVENANCE_INVALID")
        times = {name: _rs04_time(row[name]) for name in fields}
        if (times["event_at"].date().isoformat() != row["session"]
                or times["decision_cutoff"].date().isoformat() != row["session"]
                or not times["event_at"] <= times["first_seen"] <= times["persisted_at"]):
            raise ValueError("PIT_SESSION_TIME_ORDER_INVALID")
        if times["persisted_at"] > times["decision_cutoff"]:
            raise ValueError("PIT_SESSION_NOT_KNOWN_AT_DECISION")
        if row["payload_sha256"] != digests[row["session"]]:
            raise ValueError("PIT_SESSION_CONTENT_CONFLICT")
        result[row["session"]] = dict(row, **{name: value.isoformat() for name, value in times.items()})
    return result
