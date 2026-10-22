"""Read-only metadata projection; never registers trials or computes statistics.

PerformanceStore remains authoritative. This deliberately incomplete inventory
cannot certify a cumulative selection family, even when every local file exists.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

IDENTITY = ("domain", "strategy_profile", "trial_id")
FIELDS = (*IDENTITY, "status", "reason_code", "source_revision", "candidate_config_id",
          "param_set_id", "input_id", "window_start", "window_end", "calendar_id",
          "periods_per_year", "cost_source", "synthetic", "run_id", "param_version")
PARAM_FIELDS = ("purpose", "ledger_role", "cell_unit", "train_complete_sessions", "OOS",
                "PIT", "R4", "no_ledger_terminal", "economic_relation")
STATUSES = {"started", "succeeded", "rejected", "failed", "aborted"}
KINDS = {"started", "terminal", "ledger"}


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _identity(record):
    values = tuple(record.get(key) for key in IDENTITY)
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError("NATIVE_IDENTITY_MISSING")
    return _digest("\0".join(values).encode())


def _metadata(raw, kind):
    result = {key: raw.get(key) for key in FIELDS}
    if kind != "ledger":
        params = raw.get("actual_params") or {}
        result["parameter_sha256"] = _digest(json.dumps(
            params, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
        result["context"] = {key: params.get(key) for key in PARAM_FIELDS}
        research = raw.get("research_identity") or {}
        result["declared_dependent_cells"] = research.get("dependent_cells")
        result["declared_purpose"] = research.get("purpose")
    return result


def _assemble(roots, objects):
    if not roots or roots != sorted(set(roots)):
        raise ValueError("EXPLICIT_UNIQUE_STORE_ROOTS_REQUIRED")
    slots = defaultdict(dict)
    native_objects = {}
    for obj in objects:
        if set(obj) != {"store", "path", "kind", "sha256", "metadata"}:
            raise ValueError("SOURCE_FIELDS_INVALID")
        store, kind, meta = obj["store"], obj["kind"], obj["metadata"]
        if store not in roots or kind not in KINDS:
            raise ValueError("SOURCE_SCOPE_INVALID")
        digest = obj["sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("SOURCE_DIGEST_INVALID")
        expected_fields = set(FIELDS) | (set() if kind == "ledger" else {
            "parameter_sha256", "context", "declared_dependent_cells", "declared_purpose"})
        if set(meta) != expected_fields or (kind != "ledger" and set(meta["context"]) != set(PARAM_FIELDS)):
            raise ValueError("METADATA_FIELDS_INVALID")
        key = _identity(meta)
        native_object = (key, kind)
        signature = (digest, meta)
        if native_object in native_objects and native_objects[native_object] != signature:
            raise ValueError("CROSS_STORE_OBJECT_CONFLICT")
        native_objects[native_object] = signature
        expected = str(Path(store) / "research_trial" / key / (kind + ".json"))
        if obj["path"] != expected:
            raise ValueError("NATIVE_KEY_MISMATCH")
        slot = slots[(store, key)]
        if kind in slot:
            raise ValueError("DUPLICATE_SOURCE_OBJECT")
        slot[kind] = obj

    trials, ledgers = {}, []
    for (store, key), slot in sorted(slots.items()):
        if "ledger" in slot:
            ledgers.append(slot["ledger"])
        if not ({"started", "terminal"} & slot.keys()):
            continue  # A ledger-only object is never silently promoted to a trial.
        start, terminal = slot.get("started"), slot.get("terminal")
        record = (terminal or start)["metadata"]
        status = record["status"]
        if status not in STATUSES or (terminal and status == "started") or (start and start["metadata"]["status"] != "started"):
            raise ValueError("NATIVE_STATUS_INVALID")
        if start and terminal:
            mutable = {"status", "reason_code", "run_id", "param_version"}
            if any(start["metadata"][k] != record[k] for k in record if k not in mutable):
                raise ValueError("START_TERMINAL_CONFLICT")
        if status != "succeeded" and (record["run_id"] is not None or record["param_version"] is not None):
            raise ValueError("NON_SUCCESS_RESULT_IDENTITY")
        if status == "succeeded" and (not record["run_id"] or record["param_version"] is None):
            raise ValueError("SUCCESS_RESULT_IDENTITY_MISSING")
        signature = {kind: slot[kind]["sha256"] for kind in ("started", "terminal") if kind in slot}
        location = {"store": store, "objects": signature}
        if key in trials:
            if trials[key]["locations"][0]["objects"] != signature:
                raise ValueError("CROSS_STORE_IDENTITY_CONFLICT")
            trials[key]["locations"].append(location)
            continue
        trials[key] = {"identity_key": key, "native": record, "locations": [location],
                       "attempt_state": "UNKNOWN_TERMINAL" if status == "started" else status,
                       "missing_started": start is None,
                       "study_id": None, "candidate_id": None, "parent_trial_id": None,
                       "repair_or_sensitivity_kind": None, "selection_used": None,
                       "dependence_group": None, "registered_at": None, "started_at": None,
                       "terminal_at": None, "first_failure": None,
                       "relationship_status": "NEED_USER", "time_status": "NOT_ATTESTED"}

    bindings = []
    for obj in ledgers:
        meta = obj["metadata"]
        parents = [key for key, trial in trials.items() if
                   any(loc["store"] == obj["store"] for loc in trial["locations"])
                   and trial["native"]["status"] == "succeeded"
                   and all(trial["native"][k] == meta[k] for k in
                           ("domain", "trial_id", "run_id", "param_version", "input_id"))]
        parent = parents[0] if len(parents) == 1 else None
        role = "UNRESOLVED_LEDGER_ONLY"
        if parent is not None:
            if trials[parent]["native"]["strategy_profile"] != meta["strategy_profile"]:
                role = "DEPENDENT_SUBRESULT_NOT_TRIAL"
            elif (trials[parent]["native"]["context"]["ledger_role"] or "").startswith("ADDITIVE_AUDIT"):
                role = "ADDITIVE_AUDIT_NOT_SELECTION_PATH"
            else:
                role = "NATIVE_LEDGER_COMPARABILITY_NOT_ATTESTED"
        bindings.append({"path": obj["path"], "sha256": obj["sha256"],
                         "parent_identity_key": parent, "role": role,
                         "strategy_profile": meta["strategy_profile"], "comparability": "NOT_ATTESTED"})

    dependence_groups = []
    for key, trial in trials.items():
        children = [b["path"] for b in bindings if b["parent_identity_key"] == key
                    and b["role"] == "DEPENDENT_SUBRESULT_NOT_TRIAL"]
        if children:
            trial["dependence_group"] = "native_batch:" + key
            dependence_groups.append({"group_id": trial["dependence_group"],
                                      "parent_identity_key": key,
                                      "dependent_ledger_paths": children,
                                      "independent_trial_count": None})

    fingerprints = defaultdict(list)
    for key, trial in trials.items():
        r = trial["native"]
        fingerprints[(r["source_revision"], r["input_id"], r["parameter_sha256"])].append(key)
    return {
        "schema_version": "qsl.research.trial_family_registry.v1",
        "authority": "READ_ONLY_PROJECTION_NOT_NATIVE_REGISTRY",
        "store_roots": roots, "source_objects": objects,
        "trials": list(trials.values()), "ledger_bindings": bindings,
        "observed_dependence_groups": dependence_groups,
        "same_fingerprint_distinct_id_groups": [keys for keys in fingerprints.values() if len(keys) > 1],
        "local_counts": {"native_identity_count": len(trials),
                         "status_counts": dict(sorted(Counter(t["native"]["status"] for t in trials.values()).items())),
                         "ledger_object_count": len(ledgers)},
        "cumulative_family": {
            "status": "NEED_USER", "coverage": "EXPLICIT_LOCAL_STORES_ONLY_NOT_COMPLETE_HISTORY",
            "member_identity_keys": sorted(trials), "owner": None, "history_cutoff": None,
            "selection_decision": None, "candidate_control_definition": None,
            "inclusion_exclusion_evidence": None, "comparable_window": None,
            "calendar_digest": None, "net_1x_return_definition": None, "cost_definition": None,
            "rf_definition": None, "prefix_rule": None, "dependence_method": None,
            "consumer_build_digest": None, "real_data_admission": None,
            "legacy_48_mapping": None, "omissions_attestation": None,
            "raw_cumulative_attempts": None, "financial_selection_candidates": None,
            "effective_independent_trials": None,
        },
        "dsr": {"value": None, "status": "UNCOMPUTABLE",
                "reason": "COMPLETE_CUMULATIVE_TRIAL_SOURCE_NOT_VERIFIED"},
        "research_only": True, "promotion_eligible": False, "no_order": True,
    }


def build_trial_family_registry(store_roots):
    """Read only explicit local stores; retain failures, unknowns and copies.

    Only metadata is exported. Ledger bytes are hashed and parsed for identity;
    daily values are neither exported nor used in any financial calculation.
    """
    roots = sorted({str(Path(root).resolve()) for root in store_roots})
    objects = []
    for root in roots:
        base = Path(root) / "research_trial"
        if not base.is_dir():
            raise ValueError("LOCAL_RESEARCH_TRIAL_ROOT_REQUIRED")
        for path in sorted(base.glob("*/*.json")):
            if path.stem not in KINDS:
                continue
            raw = path.read_bytes()
            record = json.loads(raw)
            objects.append({"store": root, "path": str(path), "kind": path.stem,
                            "sha256": _digest(raw), "metadata": _metadata(record, path.stem)})
    return _assemble(roots, objects)


def validate_trial_family_registry(registry, *, verify_sources=False):
    """Check exact derived structure; source verification also detects omissions.

    Offline validation proves internal consistency only, never source coverage,
    native authenticity or financial comparability. No certification mode exists.
    """
    expected = _assemble(registry["store_roots"], registry["source_objects"])
    if registry != expected:
        raise ValueError("REGISTRY_PROJECTION_MISMATCH")
    if verify_sources and build_trial_family_registry(registry["store_roots"]) != registry:
        raise ValueError("NATIVE_SOURCE_DRIFT_OR_OMISSION")
    return registry["local_counts"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", action="append", help="Explicit existing local PerformanceStore")
    parser.add_argument("--validate", type=Path, help="Validate an existing metadata projection")
    parser.add_argument("--verify-sources", action="store_true")
    args = parser.parse_args()
    if args.validate:
        if args.store:
            parser.error("--store and --validate are mutually exclusive")
        registry = json.loads(args.validate.read_text())
        print(json.dumps(validate_trial_family_registry(registry, verify_sources=args.verify_sources), sort_keys=True))
    else:
        if not args.store or args.verify_sources:
            parser.error("generation requires --store; --verify-sources requires --validate")
        print(json.dumps(build_trial_family_registry(args.store), indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
