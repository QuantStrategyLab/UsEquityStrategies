from __future__ import annotations

from collections.abc import Iterable, Mapping
import csv
import hashlib
import json
from os import PathLike
from pathlib import Path
from typing import Any


SMART_DCA_PROMOTION_GATE_AUDIT_SCHEMA_VERSION = "smart_dca_promotion_gate_audit.v1"
SMART_DCA_RESEARCH_ARTIFACT_SCHEMA_VERSION = "smart_dca_research_artifacts.v1"
SMART_DCA_REVIEW_DECISION_ARTIFACT_TYPE = "smart_dca_review_decision"
EXPECTED_SELECTION_POLICY = "fixed_preset_no_parameter_search"
EXPECTED_CANDIDATE_UNIVERSE_POLICY = "frozen_preset_names_no_parameter_search"
EXPECTED_EFFECT_SIZE_POLICY = "fixed_minimum_effect_no_parameter_search"
EXPECTED_RUNTIME_DEFAULT_RECOMMENDATION = "fixed_dca"
EXPECTED_RUNTIME_DEFAULT_CHANGE_POLICY = "manual_review_required_no_auto_enable"
ALLOWED_SMART_MODE_ENABLEMENT_STATUSES = frozenset(
    {
        "manual_review_candidate",
        "not_evaluated",
        "not_recommended_for_enablement",
        "partial_manual_review_candidates",
    }
)
DEFAULT_PROFILES = ("ibit_smart_dca", "nasdaq_sp500_smart_dca")


def audit_smart_dca_promotion_gate(
    *,
    review_decision_path: str | PathLike[str],
    production_profile_decisions_path: str | PathLike[str],
    scenario_manifest_path: str | PathLike[str] | None = None,
    profiles: Iterable[str] = DEFAULT_PROFILES,
    require_runtime_consumer_coverage: bool = False,
) -> dict[str, Any]:
    """Audit smart-DCA research outputs before any runtime default change.

    The gate is intentionally narrow: it checks that matrix artifacts were
    produced by the frozen preset selection flow and that profile-level decisions
    still block automatic smart-mode/default promotion.
    """

    review_path = Path(review_decision_path)
    decisions_path = Path(production_profile_decisions_path)
    review_decision = _load_review_decision(review_path)
    csv_decisions = _load_profile_decisions(decisions_path)
    scenario_manifest_audit = (
        _audit_scenario_manifest(
            Path(scenario_manifest_path),
            review_decision_path=review_path,
            production_profile_decisions_path=decisions_path,
            require_runtime_consumer_coverage=require_runtime_consumer_coverage,
        )
        if scenario_manifest_path is not None
        else None
    )
    review_profile_decisions = _profile_decisions_from_review(review_decision)
    selected_profiles = _normalize_profiles(profiles)
    profile_audits = [
        _audit_profile_decision(
            profile,
            csv_decisions=csv_decisions,
            review_profile_decisions=review_profile_decisions,
        )
        for profile in selected_profiles
    ]
    failure_reasons = [
        *list(_review_decision_failure_reasons(review_decision)),
        *[
            reason
            for profile_audit in profile_audits
            for reason in profile_audit["failure_reasons"]
        ],
    ]
    if scenario_manifest_audit is not None:
        failure_reasons.extend(scenario_manifest_audit["failure_reasons"])
    elif require_runtime_consumer_coverage:
        failure_reasons.append(
            "scenario_manifest_required_for_runtime_consumer_coverage"
        )
    return {
        "schema_version": SMART_DCA_PROMOTION_GATE_AUDIT_SCHEMA_VERSION,
        "artifact_type": "smart_dca_promotion_gate_audit",
        "passed": not failure_reasons,
        "failure_reasons": failure_reasons,
        "review_decision_path": str(review_path),
        "production_profile_decisions_path": str(decisions_path),
        "scenario_manifest_path": (
            "" if scenario_manifest_path is None else str(Path(scenario_manifest_path))
        ),
        "review_decision": {
            "schema_version": review_decision.get("schema_version"),
            "artifact_type": review_decision.get("artifact_type"),
            "selection_policy": review_decision.get("selection_policy"),
            "candidate_universe_policy": review_decision.get(
                "candidate_universe_policy"
            ),
            "effect_size_policy": review_decision.get("effect_size_policy"),
            "runtime_default_recommendation": review_decision.get(
                "runtime_default_recommendation"
            ),
            "runtime_default_change_policy": review_decision.get(
                "runtime_default_change_policy"
            ),
            "smart_mode_enablement_status": review_decision.get(
                "smart_mode_enablement_status"
            ),
            "matrix_coverage_gate_passed": review_decision.get(
                "matrix_coverage_gate_passed"
            ),
            "manual_review_gate_passed": review_decision.get(
                "manual_review_gate_passed"
            ),
            "overall_recommendation_status": review_decision.get(
                "overall_recommendation_status"
            ),
            "overall_recommendation_reason": review_decision.get(
                "overall_recommendation_reason"
            ),
        },
        "scenario_manifest": scenario_manifest_audit,
        "profiles": profile_audits,
    }


def _load_review_decision(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file_obj:
        payload = json.load(file_obj)
    if not isinstance(payload, dict):
        raise ValueError("review decision must be a JSON object")
    return payload


def _load_profile_decisions(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as file_obj:
        rows = list(csv.DictReader(file_obj))
    decisions: dict[str, dict[str, str]] = {}
    for index, row in enumerate(rows, start=2):
        profile = str(row.get("profile", "")).strip()
        if not profile:
            raise ValueError(f"profile decision row {index} is missing profile")
        if profile in decisions:
            raise ValueError(f"duplicate profile decision: {profile}")
        decisions[profile] = {str(key): str(value or "") for key, value in row.items()}
    return decisions


def _audit_scenario_manifest(
    path: Path,
    *,
    review_decision_path: Path,
    production_profile_decisions_path: Path,
    require_runtime_consumer_coverage: bool,
) -> dict[str, Any]:
    manifest = _load_scenario_manifest(path)
    root = path.parent.resolve()
    failure_reasons: list[str] = []
    if manifest.get("schema_version") != SMART_DCA_RESEARCH_ARTIFACT_SCHEMA_VERSION:
        failure_reasons.append("scenario_manifest_schema_version_mismatch")
    if manifest.get("artifact_type") != "smart_dca_research_scenario_matrix":
        failure_reasons.append("scenario_manifest_artifact_type_mismatch")

    file_records = _scenario_manifest_file_records(manifest)
    file_paths = set(file_records)
    for suffix in (
        "review_decision.json",
        "production_profile_decisions.csv",
        "scenario_index.csv",
        "robustness_summary.csv",
        "selection_summary.csv",
        "scenario_coverage.csv",
    ):
        if not any(item.endswith(suffix) for item in file_paths):
            failure_reasons.append(f"scenario_manifest_missing_file:{suffix}")

    candidate_summary_present = any(
        item.endswith("candidate_summary.csv") for item in file_paths
    )
    candidate_specs_present = any(
        item.endswith("candidate_specs.csv") for item in file_paths
    )
    if not candidate_summary_present:
        failure_reasons.append("scenario_manifest_missing_candidate_summary")
    if not candidate_specs_present:
        failure_reasons.append("scenario_manifest_missing_candidate_specs")

    review_verified = _verify_manifest_file_record(
        file_records,
        root=root,
        path=review_decision_path,
        label="review_decision",
        failure_reasons=failure_reasons,
    )
    decisions_verified = _verify_manifest_file_record(
        file_records,
        root=root,
        path=production_profile_decisions_path,
        label="production_profile_decisions",
        failure_reasons=failure_reasons,
    )

    metadata = manifest.get("metadata")
    research_config = (
        metadata.get("research_config", {})
        if isinstance(metadata, Mapping)
        else {}
    )
    input_artifacts = (
        metadata.get("input_artifacts", {})
        if isinstance(metadata, Mapping)
        else {}
    )
    candidate_set = str(research_config.get("candidate_set", "")).strip()
    if not candidate_set:
        failure_reasons.append("scenario_manifest_missing_candidate_set")

    runtime_consumer_coverage_verified = False
    runtime_consumer_coverage_artifact_verified = False
    if require_runtime_consumer_coverage:
        if research_config.get("require_runtime_consumer_coverage") is not True:
            failure_reasons.append(
                "scenario_manifest_runtime_consumer_coverage_not_required"
            )
        (
            runtime_consumer_coverage_verified,
            runtime_consumer_coverage_artifact_verified,
        ) = _input_artifacts_runtime_coverage_ok(
            input_artifacts,
            root=root,
            failure_reasons=failure_reasons,
        )
        if not runtime_consumer_coverage_verified:
            failure_reasons.append(
                "scenario_manifest_runtime_consumer_coverage_not_verified"
            )

    return {
        "path": str(path),
        "schema_version": manifest.get("schema_version"),
        "artifact_type": manifest.get("artifact_type"),
        "passed": not failure_reasons,
        "failure_reasons": failure_reasons,
        "file_count": len(file_records),
        "candidate_set": candidate_set,
        "candidate_summary_present": candidate_summary_present,
        "candidate_specs_present": candidate_specs_present,
        "review_decision_verified": review_verified,
        "production_profile_decisions_verified": decisions_verified,
        "require_runtime_consumer_coverage": require_runtime_consumer_coverage,
        "runtime_consumer_coverage_verified": runtime_consumer_coverage_verified,
        "runtime_consumer_coverage_artifact_verified": (
            runtime_consumer_coverage_artifact_verified
        ),
    }


def _load_scenario_manifest(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file_obj:
        payload = json.load(file_obj)
    if not isinstance(payload, dict):
        raise ValueError("scenario manifest must be a JSON object")
    return payload


def _scenario_manifest_file_records(
    manifest: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    raw_files = manifest.get("files", ())
    if not isinstance(raw_files, list | tuple):
        raise ValueError("scenario manifest files must be a list")
    records: dict[str, Mapping[str, Any]] = {}
    for index, raw_record in enumerate(raw_files):
        if not isinstance(raw_record, Mapping):
            raise ValueError(f"scenario manifest files[{index}] must be an object")
        path = str(raw_record.get("path", "")).strip()
        if not path:
            raise ValueError(f"scenario manifest files[{index}] missing path")
        if path in records:
            raise ValueError(f"scenario manifest duplicate file path: {path}")
        records[path] = raw_record
    return records


def _verify_manifest_file_record(
    records: Mapping[str, Mapping[str, Any]],
    *,
    root: Path,
    path: Path,
    label: str,
    failure_reasons: list[str],
) -> bool:
    try:
        relative_path = path.resolve().relative_to(root).as_posix()
    except ValueError:
        failure_reasons.append(f"scenario_manifest_{label}_outside_root")
        return False
    record = records.get(relative_path)
    if record is None:
        failure_reasons.append(f"scenario_manifest_missing_{label}_record")
        return False
    verified = True
    if record.get("sha256") != _sha256_file(path):
        failure_reasons.append(f"scenario_manifest_{label}_sha256_mismatch")
        verified = False
    if record.get("size_bytes") != path.stat().st_size:
        failure_reasons.append(f"scenario_manifest_{label}_size_bytes_mismatch")
        verified = False
    return verified


def _input_artifacts_runtime_coverage_ok(
    input_artifacts: object,
    *,
    root: Path,
    failure_reasons: list[str],
) -> tuple[bool, bool]:
    if not isinstance(input_artifacts, Mapping):
        return False, False
    coverage_found = False
    artifact_verified = False
    for key in (
        "signal_source_family_catalog_manifest",
        "platform_signal_handoff_manifest",
        "platform_signal_handoff_index",
        "research_signal_handoff_manifest",
        "signal_consumption_audit",
    ):
        record = input_artifacts.get(key)
        if (
            isinstance(record, Mapping)
            and record.get("all_runtime_consumers_covered") is True
        ):
            coverage_found = True
            if _verify_input_artifact_file_record(
                record,
                root=root,
                label=key,
                failure_reasons=failure_reasons,
            ):
                artifact_verified = True
    return coverage_found and artifact_verified, artifact_verified


def _verify_input_artifact_file_record(
    record: Mapping[str, Any],
    *,
    root: Path,
    label: str,
    failure_reasons: list[str],
) -> bool:
    raw_path = str(record.get("path", "") or "").strip()
    if not raw_path:
        failure_reasons.append(f"scenario_manifest_input_artifact_missing_path:{label}")
        return False
    artifact_path = Path(raw_path)
    if not artifact_path.is_absolute():
        artifact_path = root / artifact_path
    if not artifact_path.exists():
        failure_reasons.append(f"scenario_manifest_input_artifact_missing_file:{label}")
        return False
    verified = True
    if record.get("sha256") != _sha256_file(artifact_path):
        failure_reasons.append(
            f"scenario_manifest_input_artifact_sha256_mismatch:{label}"
        )
        verified = False
    if record.get("size_bytes") != artifact_path.stat().st_size:
        failure_reasons.append(
            f"scenario_manifest_input_artifact_size_bytes_mismatch:{label}"
        )
        verified = False
    return verified


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _profile_decisions_from_review(
    review_decision: Mapping[str, Any],
) -> dict[str, dict[str, str]]:
    raw_decisions = review_decision.get("production_profile_decisions", ())
    if not isinstance(raw_decisions, list | tuple):
        raise ValueError("review decision production_profile_decisions must be a list")
    decisions: dict[str, dict[str, str]] = {}
    for index, raw_decision in enumerate(raw_decisions):
        if not isinstance(raw_decision, Mapping):
            raise ValueError(
                f"review decision production_profile_decisions[{index}] must be an object"
            )
        profile = str(raw_decision.get("profile", "")).strip()
        if not profile:
            raise ValueError(
                f"review decision production_profile_decisions[{index}] missing profile"
            )
        decisions[profile] = {
            str(key): _stringify_decision_value(value)
            for key, value in raw_decision.items()
        }
    return decisions


def _normalize_profiles(profiles: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(str(profile or "").strip() for profile in profiles)
    if not normalized or any(not profile for profile in normalized):
        raise ValueError("at least one non-empty profile is required")
    duplicates = sorted({profile for profile in normalized if normalized.count(profile) > 1})
    if duplicates:
        raise ValueError(f"duplicate profiles requested: {', '.join(duplicates)}")
    return normalized


def _review_decision_failure_reasons(
    review_decision: Mapping[str, Any],
) -> tuple[str, ...]:
    checks = (
        (
            "review_schema_version",
            review_decision.get("schema_version"),
            SMART_DCA_RESEARCH_ARTIFACT_SCHEMA_VERSION,
        ),
        (
            "review_artifact_type",
            review_decision.get("artifact_type"),
            SMART_DCA_REVIEW_DECISION_ARTIFACT_TYPE,
        ),
        (
            "selection_policy",
            review_decision.get("selection_policy"),
            EXPECTED_SELECTION_POLICY,
        ),
        (
            "candidate_universe_policy",
            review_decision.get("candidate_universe_policy"),
            EXPECTED_CANDIDATE_UNIVERSE_POLICY,
        ),
        (
            "effect_size_policy",
            review_decision.get("effect_size_policy"),
            EXPECTED_EFFECT_SIZE_POLICY,
        ),
        (
            "runtime_default_recommendation",
            review_decision.get("runtime_default_recommendation"),
            EXPECTED_RUNTIME_DEFAULT_RECOMMENDATION,
        ),
        (
            "runtime_default_change_policy",
            review_decision.get("runtime_default_change_policy"),
            EXPECTED_RUNTIME_DEFAULT_CHANGE_POLICY,
        ),
    )
    failures = [
        f"{name}_mismatch:{actual!r}!={expected!r}"
        for name, actual, expected in checks
        if actual != expected
    ]
    smart_mode_status = str(
        review_decision.get("smart_mode_enablement_status", "")
    ).strip()
    if smart_mode_status not in ALLOWED_SMART_MODE_ENABLEMENT_STATUSES:
        failures.append(f"unexpected_smart_mode_enablement_status:{smart_mode_status}")
    if review_decision.get("matrix_coverage_gate_passed") is not True:
        failures.append("matrix_coverage_gate_not_passed")
    return tuple(failures)


def _audit_profile_decision(
    profile: str,
    *,
    csv_decisions: Mapping[str, Mapping[str, str]],
    review_profile_decisions: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    csv_decision = csv_decisions.get(profile)
    review_decision = review_profile_decisions.get(profile)
    failure_reasons: list[str] = []
    if csv_decision is None:
        failure_reasons.append(f"profile_missing_from_csv:{profile}")
        csv_decision = {}
    if review_decision is None:
        failure_reasons.append(f"profile_missing_from_review_decision:{profile}")
        review_decision = {}

    checked_fields = (
        "production_equivalent_candidate",
        "production_equivalent_candidate_definition_sha256",
        "production_equivalent_in_candidate_universe",
        "selection_group",
        "observed_best_candidate",
        "observed_best_candidate_definition_sha256",
        "observed_best_status",
        "observed_best_reason",
        "runtime_default_recommendation",
        "runtime_default_change_policy",
        "smart_mode_enablement_status",
        "manual_review_required_before_default_change",
        "default_change_allowed_by_research",
    )
    for field in checked_fields:
        if csv_decision.get(field, "") != review_decision.get(field, ""):
            failure_reasons.append(f"profile_field_mismatch:{profile}:{field}")

    if csv_decision.get("runtime_default_recommendation") != (
        EXPECTED_RUNTIME_DEFAULT_RECOMMENDATION
    ):
        failure_reasons.append(f"profile_runtime_default_not_fixed:{profile}")
    if csv_decision.get("runtime_default_change_policy") != (
        EXPECTED_RUNTIME_DEFAULT_CHANGE_POLICY
    ):
        failure_reasons.append(f"profile_runtime_default_change_policy_changed:{profile}")
    if _string_bool(csv_decision.get("manual_review_required_before_default_change")) is not True:
        failure_reasons.append(f"profile_manual_review_not_required:{profile}")
    if _string_bool(csv_decision.get("default_change_allowed_by_research")) is not False:
        failure_reasons.append(f"profile_default_change_allowed:{profile}")
    smart_mode_status = csv_decision.get("smart_mode_enablement_status", "")
    if smart_mode_status not in ALLOWED_SMART_MODE_ENABLEMENT_STATUSES:
        failure_reasons.append(
            f"profile_unexpected_smart_mode_enablement_status:{profile}:{smart_mode_status}"
        )

    return {
        "profile": profile,
        "passed": not failure_reasons,
        "failure_reasons": failure_reasons,
        "production_equivalent_candidate": csv_decision.get(
            "production_equivalent_candidate", ""
        ),
        "production_equivalent_candidate_definition_sha256": csv_decision.get(
            "production_equivalent_candidate_definition_sha256", ""
        ),
        "observed_best_candidate": csv_decision.get("observed_best_candidate", ""),
        "observed_best_status": csv_decision.get("observed_best_status", ""),
        "observed_best_reason": csv_decision.get("observed_best_reason", ""),
        "runtime_default_recommendation": csv_decision.get(
            "runtime_default_recommendation", ""
        ),
        "runtime_default_change_policy": csv_decision.get(
            "runtime_default_change_policy", ""
        ),
        "smart_mode_enablement_status": smart_mode_status,
        "manual_review_required_before_default_change": _string_bool(
            csv_decision.get("manual_review_required_before_default_change")
        ),
        "default_change_allowed_by_research": _string_bool(
            csv_decision.get("default_change_allowed_by_research")
        ),
    }


def _string_bool(value: object) -> bool | None:
    normalized = str(value or "").strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return None


def _stringify_decision_value(value: object) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if value is None:
        return ""
    return str(value)
