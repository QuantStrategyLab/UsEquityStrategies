from __future__ import annotations

from collections.abc import Iterable, Mapping
import csv
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
    profiles: Iterable[str] = DEFAULT_PROFILES,
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
    return {
        "schema_version": SMART_DCA_PROMOTION_GATE_AUDIT_SCHEMA_VERSION,
        "artifact_type": "smart_dca_promotion_gate_audit",
        "passed": not failure_reasons,
        "failure_reasons": failure_reasons,
        "review_decision_path": str(review_path),
        "production_profile_decisions_path": str(decisions_path),
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
