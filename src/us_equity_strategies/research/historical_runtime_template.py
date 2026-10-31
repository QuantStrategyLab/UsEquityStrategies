"""Deterministic, fictitious input example for the offline runtime bundle CLI."""
from __future__ import annotations

from .historical_runtime_bundle import PROFILES, SCHEMA, canonical_sha256


def template_records():
    account = "acct_0000000000000000"
    day = "2026-10-30"
    tqqq, soxl = PROFILES
    manifest = {"schema_version": SCHEMA, "evidence_kind": "synthetic_fixture",
                "bundle_id": "synthetic-example-001", "created_at": "2026-10-31T00:00:00Z",
                "account_alias": account, "currency": "USD", "profiles": list(PROFILES),
                "first_session": day, "last_session": day, "files": []}
    evidence = {"evidence_id": "source_synthetic", "kind": "synthetic_fixture",
                "available_at": day + "T13:00:00Z", "observed_at": day + "T20:01:00Z",
                "payload": {"fictitious": True, "purpose": "schema_and_accounting_example"}}
    evidence["payload_sha256"] = canonical_sha256(evidence["payload"])
    source = {"source_evidence_id": evidence["evidence_id"]}
    config = {"income_layer_enabled": False}
    release = {"release_id": "release_synthetic", "source_revision": "0" * 40,
               "source_hashes": {"unavailable-example-source.py": "0" * 64},
               "dependency_lock_sha256": "0" * 64, "image_sha256": "0" * 64,
               "effective_from": day + "T00:00:00Z", "effective_until": "2026-10-31T00:00:00Z",
               "config": config, "config_sha256": canonical_sha256(config),
               "effective_config_sha256": "0" * 64, **source}
    runs = []
    for profile, symbol in ((tqqq, "TQQQ"), (soxl, "SOXL")):
        payload = {"session": day, "template_only": True}
        output = {"template_only": True}
        runs.append({"run_id": "run_" + symbol, "profile": profile, "account_alias": account,
                     "session": day, "decision_at": day + "T13:30:00Z", "release_id": release["release_id"],
                     "input": payload, "input_sha256": canonical_sha256(payload),
                     "output": output, "output_sha256": canonical_sha256(output),
                     "availability": [{"name": "frozen_input", "available_at": day + "T13:00:00Z",
                                       "sha256": canonical_sha256(payload), **source}],
                     "order_observation": "observed", "intent_ids": ["intent_" + symbol], **source})
    sessions = [{"session": day, "open_at": day + "T13:30:00Z", "close_at": day + "T20:00:00Z",
                 "calendar_id": "XNYS-example-not-attested", "runs_by_profile": {
                     tqqq: ["run_TQQQ"], soxl: ["run_SOXL"]}, "no_run_reasons": {}, **source}]
    events = []

    def add(kind, intent_symbol, **fields):
        sequence = len(events) + 1
        timestamp = f"{day}T13:31:{sequence:02d}Z"
        events.append({"event_id": f"event_{sequence:03d}", "sequence": sequence,
                       "event_at": timestamp, "observed_at": timestamp, "account_alias": account,
                       "kind": kind, "intent_id": "intent_" + intent_symbol, **source, **fields})

    for profile, symbol in ((tqqq, "TQQQ"), (soxl, "SOXL")):
        add("reserve", symbol, run_id="run_" + symbol, profile=profile,
            observation_id="snapshot_open", side="buy", symbol=symbol, quantity="2", reserved_cash="110")
    for symbol in ("TQQQ", "SOXL"):
        add("submit", symbol, attempt_id="attempt_" + symbol, quantity="2")
        add("acknowledged", symbol, attempt_id="attempt_" + symbol, broker_order_id="order_" + symbol)
        add("fill", symbol, attempt_id="attempt_" + symbol, broker_order_id="order_" + symbol,
            execution_id="execution_" + symbol, quantity="2", price="50", fee="1",
            cumulative_quantity="2", remaining_quantity="0")
        add("release", symbol)
    snapshots = [
        {"snapshot_id": "snapshot_open", "account_alias": account, "session": day, "boundary": "opening",
         "at": day + "T13:30:00Z", "valid_until": day + "T20:00:00Z", "sequence": 0,
         "cash": "1000", "settled_cash": "1000", "positions": {},
         "allocations": {tqqq: {}, soxl: {}}, "reserved_cash": {}, "receivables": {}, **source},
        {"snapshot_id": "snapshot_close", "account_alias": account, "session": day, "boundary": "closing",
         "at": day + "T20:00:00Z", "valid_until": day + "T20:00:00Z", "sequence": len(events),
         "cash": "798", "settled_cash": "798", "positions": {"TQQQ": "2", "SOXL": "2"},
         "allocations": {tqqq: {"TQQQ": "2"}, soxl: {"SOXL": "2"}},
         "reserved_cash": {}, "receivables": {}, **source},
    ]
    return manifest, {"releases": [release], "sessions": sessions, "runs": runs,
                      "order_events": events, "account_events": [], "account_snapshots": snapshots,
                      "source_evidence": [evidence]}
