"""Metadata fixtures only: no simulator, trial runner, returns or statistics."""
import copy
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

import pytest

from us_equity_strategies.research.trial_family_registry import (
    build_trial_family_registry,
    validate_trial_family_registry,
)


def record(trial_id="a", status="started", profile="batch"):
    return {"domain": "us_equity", "strategy_profile": profile, "trial_id": trial_id,
            "status": status, "reason_code": "" if status == "succeeded" else "native_reason",
            "source_revision": "source", "input_id": "input", "synthetic": True,
            "window_start": "2023-03-28", "window_end": "2026-08-25",
            "calendar_id": "XNYS", "periods_per_year": 252,
            "cost_source": "test_cost", "actual_params": {
                "ledger_role": "ADDITIVE_AUDIT_TEST", "train_complete_sessions": 63},
            "run_id": "run-" + trial_id if status == "succeeded" else None,
            "param_version": 1 if status == "succeeded" else None}


def write(root, value, kind):
    key = hashlib.sha256("\0".join(value[k] for k in (
        "domain", "strategy_profile", "trial_id")).encode()).hexdigest()
    path = root / "research_trial" / key / (kind + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True))
    return path


def succeeded(root, trial_id="a"):
    write(root, record(trial_id), "started")
    value = record(trial_id, "succeeded")
    write(root, value, "terminal")
    write(root, value, "ledger")
    child = {**value, "strategy_profile": "batch__child"}
    write(root, child, "ledger")


def snapshot_bytes(root):
    return {str(p): p.read_bytes() for p in root.rglob("*.json")}


def test_failures_unknown_and_terminal_only_preserved_without_results(tmp_path):
    for status in ("started", "failed", "rejected", "aborted"):
        if status != "aborted":
            write(tmp_path, record(status), "started")
        if status != "started":
            write(tmp_path, record(status, status), "terminal")
    before = snapshot_bytes(tmp_path)
    result = build_trial_family_registry([tmp_path])
    assert validate_trial_family_registry(result, verify_sources=True)["native_identity_count"] == 4
    trials = {t["native"]["trial_id"]: t for t in result["trials"]}
    assert trials["started"]["attempt_state"] == "UNKNOWN_TERMINAL"
    assert trials["aborted"]["missing_started"] is True
    for trial in trials.values():
        assert trial["native"]["run_id"] is None
        assert trial["native"]["reason_code"] == "native_reason"
        assert all(trial[key] is None for key in (
            "registered_at", "started_at", "terminal_at", "parent_trial_id", "selection_used", "first_failure"))
    assert result["dsr"]["value"] is None
    assert snapshot_bytes(tmp_path) == before


def test_dependency_ledgers_not_counted_and_unmatched_kept(tmp_path):
    succeeded(tmp_path)
    write(tmp_path, record("orphan", "succeeded", "unmatched"), "ledger")
    result = build_trial_family_registry([tmp_path])
    assert result["local_counts"]["native_identity_count"] == 1
    assert result["local_counts"]["ledger_object_count"] == 3
    bindings = {b["role"]: b for b in result["ledger_bindings"]}
    assert set(bindings) == {"DEPENDENT_SUBRESULT_NOT_TRIAL", "ADDITIVE_AUDIT_NOT_SELECTION_PATH", "UNRESOLVED_LEDGER_ONLY"}
    assert bindings["UNRESOLVED_LEDGER_ONLY"]["parent_identity_key"] is None
    assert bindings["DEPENDENT_SUBRESULT_NOT_TRIAL"]["parent_identity_key"] == result["trials"][0]["identity_key"]


def test_same_native_identity_copies_dedup_but_different_ids_not(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    succeeded(a)
    shutil.copytree(a, b)
    result = build_trial_family_registry([a, b])
    assert len(result["trials"]) == 1
    assert len(result["trials"][0]["locations"]) == 2
    write(b, record("other"), "started")
    result = build_trial_family_registry([a, b])
    assert len(result["trials"]) == 2
    assert len(result["same_fingerprint_distinct_id_groups"]) == 1
    assert len(result["same_fingerprint_distinct_id_groups"][0]) == 2
    assert result["cumulative_family"]["effective_independent_trials"] is None


def test_conflicting_same_identity_copies_rejected(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    write(a, record(), "started")
    write(b, {**record(), "source_revision": "changed"}, "started")
    with pytest.raises(ValueError, match="CROSS_STORE_OBJECT_CONFLICT"):
        build_trial_family_registry([a, b])


@pytest.mark.parametrize("profile", ["batch", "batch__child"])
def test_cross_store_ledger_conflicts_rejected_including_ledger_only(tmp_path, profile):
    a, b = tmp_path / "a", tmp_path / "b"
    succeeded(a)
    shutil.copytree(a, b)
    value = {**record(status="succeeded", profile=profile), "cost_source": "conflicting"}
    write(b, value, "ledger")
    with pytest.raises(ValueError, match="CROSS_STORE_OBJECT_CONFLICT"):
        build_trial_family_registry([a, b])


def test_wrong_native_key_and_start_terminal_conflict_rejected(tmp_path):
    path = write(tmp_path, record(), "started")
    path.write_text(json.dumps(record("wrong-id")))
    with pytest.raises(ValueError, match="NATIVE_KEY_MISMATCH"):
        build_trial_family_registry([tmp_path])
    write(tmp_path, record(), "started")
    write(tmp_path, {**record(status="succeeded"), "input_id": "changed"}, "terminal")
    with pytest.raises(ValueError, match="START_TERMINAL_CONFLICT"):
        build_trial_family_registry([tmp_path])


def test_non_success_cannot_get_success_result_identity(tmp_path):
    write(tmp_path, {**record(status="failed"), "run_id": "fake"}, "terminal")
    with pytest.raises(ValueError, match="NON_SUCCESS_RESULT_IDENTITY"):
        build_trial_family_registry([tmp_path])


@pytest.mark.parametrize("field,value", [
    ("window_end", "2027-01-01"), ("calendar_id", "different"),
    ("cost_source", "different"), ("input_id", "different"), ("run_id", "different"),
])
def test_wrong_binding_metadata_cannot_become_comparable_family(tmp_path, field, value):
    succeeded(tmp_path)
    child = {**record(status="succeeded", profile="batch__child"), field: value}
    write(tmp_path, child, "ledger")
    result = build_trial_family_registry([tmp_path])
    # Mechanical lineage alone never attests a common window, cost or rf.
    assert result["cumulative_family"]["status"] == "NEED_USER"
    assert result["cumulative_family"]["comparable_window"] is None
    assert result["cumulative_family"]["rf_definition"] is None
    assert result["dsr"]["value"] is None
    if field in {"input_id", "run_id"}:
        child_binding = next(b for b in result["ledger_bindings"] if b["strategy_profile"] == "batch__child")
        assert child_binding["parent_identity_key"] is None


@pytest.mark.parametrize("section,field,value", [
    ("dsr", "value", 0.0), ("dsr", "status", "COMPUTED"),
    ("cumulative_family", "status", "COMPLETE"),
    ("cumulative_family", "effective_independent_trials", 2),
    ("cumulative_family", "raw_cumulative_attempts", 9),
    ("cumulative_family", "comparable_window", ["2023-03-28", "2026-08-25"]),
    ("cumulative_family", "rf_definition", "zero"),
])
def test_fabricated_financial_claims_rejected(tmp_path, section, field, value):
    write(tmp_path, record(), "started")
    result = build_trial_family_registry([tmp_path])
    result[section][field] = value
    with pytest.raises(ValueError, match="REGISTRY_PROJECTION_MISMATCH"):
        validate_trial_family_registry(result)


def test_omitted_failure_or_source_drift_detected_by_native_verification(tmp_path):
    write(tmp_path, record(), "started")
    result = build_trial_family_registry([tmp_path])
    write(tmp_path, record("later", "failed"), "terminal")
    # Internal validation has no authority to certify completeness.
    validate_trial_family_registry(result)
    with pytest.raises(ValueError, match="NATIVE_SOURCE_DRIFT_OR_OMISSION"):
        validate_trial_family_registry(result, verify_sources=True)


def test_no_extra_returns_or_made_up_relations_allowed(tmp_path):
    write(tmp_path, record(), "started")
    result = build_trial_family_registry([tmp_path])
    altered = copy.deepcopy(result)
    altered["source_objects"][0]["metadata"]["sharpe"] = 0.0
    with pytest.raises(ValueError, match="METADATA_FIELDS_INVALID"):
        validate_trial_family_registry(altered)
    result["trials"][0]["parent_trial_id"] = "guessed-from-name"
    with pytest.raises(ValueError, match="REGISTRY_PROJECTION_MISMATCH"):
        validate_trial_family_registry(result)


def test_committed_nine_slot_snapshot_is_internally_consistent():
    path = Path(__file__).resolve().parents[2] / "docs/research/trial_registry/r_math_04.json"
    result = json.loads(path.read_text())
    counts = validate_trial_family_registry(result)
    assert counts == {"native_identity_count": 9, "status_counts": {
        "succeeded": 4, "rejected": 2, "aborted": 2, "started": 1}, "ledger_object_count": 18}
    assert dict(Counter(b["role"] for b in result["ledger_bindings"])) == {"DEPENDENT_SUBRESULT_NOT_TRIAL": 14, "ADDITIVE_AUDIT_NOT_SELECTION_PATH": 4}
    assert len(result["cumulative_family"]["member_identity_keys"]) == 9
    assert all(t["native"]["synthetic"] is False for t in result["trials"])
