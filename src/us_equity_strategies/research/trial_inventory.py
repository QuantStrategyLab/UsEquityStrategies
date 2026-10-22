"""Local, read-only inventory of all journaled attempts, not just winners."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def _event_key(store, record, event: str) -> str:
    native = store._research_key(record.domain, record.strategy_profile, record.trial_id, event)
    return native.replace("research_trial/", "research_trial_events/", 1).removesuffix(".json") + ".jsonl"


def record_trial_event(store, record, event: str) -> None:
    """Create a timestamp sidecar without changing immutable native trial records."""
    if event not in {"started", "terminal"}:
        raise ValueError("TRIAL_EVENT_INVALID")
    payload = {
        "schema_version": "qsl.research.trial_event.v1",
        "trial_id": record.trial_id, "status": record.status.value,
        "domain": record.domain, "strategy_profile": record.strategy_profile,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    key = _event_key(store, record, event)
    store._create_same(key, payload)
    if store._read_research_json(key) != payload:
        raise ValueError("TRIAL_EVENT_READBACK_INVALID")


def read_trial_inventory(store) -> dict:
    """Enumerate STARTED slots including failures, interrupts and unknown exits.

    Legacy timestamps remain unknown. Raw attempt count is not an estimate of
    independent/effective trials, and no missing return is imputed.
    """
    from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore

    if (type(store) is not PerformanceStore or store.cloud_bucket or store.client_factory is not None
            or not isinstance(store.local_root, Path) or not store.local_root.is_dir()):
        raise ValueError("LOCAL_EXPLICIT_PERFORMANCE_STORE_REQUIRED")
    entries = []
    for path in sorted((store.local_root / "research_trial").glob("*/started.json")):
        raw = json.loads(path.read_text())
        record = store.load_research_trial(raw["domain"], raw["strategy_profile"], raw["trial_id"])
        if record is None:
            raise ValueError("RESEARCH_TRIAL_READBACK_INVALID")
        key = store._research_key(record.domain, record.strategy_profile, record.trial_id, "started")
        if path != store.local_root / key:
            raise ValueError("RESEARCH_TRIAL_IDENTITY_INVALID")
        times = {}
        for event in ("started", "terminal"):
            sidecar = store.local_root / _event_key(store, record, event)
            value = json.loads(sidecar.read_text()) if sidecar.exists() else None
            if value is not None:
                expected = "started" if event == "started" else record.status.value
                if (value.get("schema_version") != "qsl.research.trial_event.v1"
                        or value.get("trial_id") != record.trial_id or value.get("status") != expected
                        or value.get("domain") != record.domain
                        or value.get("strategy_profile") != record.strategy_profile):
                    raise ValueError("TRIAL_EVENT_IDENTITY_INVALID")
                stamp = datetime.fromisoformat(value["recorded_at"])
                if stamp.tzinfo is None or stamp.utcoffset() is None:
                    raise ValueError("TRIAL_EVENT_TIME_INVALID")
            times[event + "_at"] = None if value is None else value["recorded_at"]
        summary = None if record.actual_params is None else hashlib.sha256(json.dumps(
            record.actual_params, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        entries.append({"trial_id": record.trial_id, "strategy_profile": record.strategy_profile,
            "status": record.status.value, "reason_code": record.reason_code, **times,
            "parameter_sha256": summary, "param_set_id": record.param_set_id,
            "source_revision": record.source_revision, "input_id": record.input_id,
            "data_window": [record.window_start.isoformat(), record.window_end.isoformat()],
            "record_pointer": key, "run_id": record.run_id, "param_version": record.param_version,
            "result_pointer": None if record.run_id is None else {"domain": record.domain,
                "strategy_profile": record.strategy_profile, "run_id": record.run_id,
                "param_version": record.param_version},
            "ledger_pointer": None if record.run_id is None else store._research_key(
                record.domain, record.strategy_profile, record.trial_id, "ledger"),
            "research_identity": record.research_identity})
    return {"schema_version": "qsl.research.trial_inventory.v1", "recorded_trial_count": len(entries),
        "status_counts": dict(Counter(entry["status"] for entry in entries)), "trials": entries,
        "effective_trial_count": None, "DSR": "NOT_COMPUTED", "research_only": True,
        "promotion_eligible": False, "no_order": True}
