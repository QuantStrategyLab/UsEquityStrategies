from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import json
from os import PathLike
from pathlib import Path
from typing import Any

from .smart_dca_promotion_gate import (
    DEFAULT_PROFILES,
    audit_smart_dca_promotion_gate,
)


SMART_DCA_DECISION_SUMMARY_SCHEMA_VERSION = "smart_dca_decision_summary.v1"
SMART_DCA_DEFAULT_DECISION_CONTRACT_SCHEMA_VERSION = (
    "smart_dca_default_decision_contract.v1"
)
SMART_DCA_NEXT_ACTION_GUARDRAILS_BY_PRIORITY = {
    "avoid_parameter_tuning_without_new_independent_signal": (
        "do_not_parameter_search_current_candidate_family"
    ),
    "avoid_skip_heavy_cash_drag_variants_as_default": (
        "reject_skip_heavy_cash_drag_default_candidates"
    ),
    "expand_contract_covered_signal_family_before_retest": (
        "add_contract_covered_independent_signal_before_retest"
    ),
    "hold_fixed_default": "keep_fixed_dca_default",
    "improve_cross_scenario_robustness_before_manual_review": (
        "require_cross_scenario_robustness_before_manual_review"
    ),
    "require_material_terminal_edge_before_promotion": (
        "require_effect_size_gate_before_default_change"
    ),
    "require_terminal_value_non_regression_before_promotion": (
        "require_terminal_value_non_regression"
    ),
}
SMART_DCA_REQUIRED_DEFAULT_DECISION_GUARDRAILS_BY_PROFILE = {
    "ibit_smart_dca": (
        "reject_skip_heavy_cash_drag_default_candidates",
        "require_cross_scenario_robustness_before_manual_review",
        "require_terminal_value_non_regression",
    ),
    "nasdaq_sp500_smart_dca": (
        "require_effect_size_gate_before_default_change",
    ),
}


def summarize_smart_dca_decision_matrices(
    matrix_dirs: Iterable[str | PathLike[str]],
    *,
    profiles: Iterable[str] = DEFAULT_PROFILES,
) -> dict[str, Any]:
    """Summarize existing smart-DCA matrix decision artifacts.

    This helper is deliberately read-only. It does not rerun backtests or search
    parameters; it only aggregates each matrix's review decision and profile gate.
    """

    selected_profiles = _normalize_profiles(profiles)
    resolved_dirs = tuple(Path(path) for path in matrix_dirs)
    if not resolved_dirs:
        raise ValueError("at least one matrix directory is required")

    matrices = tuple(
        _summarize_matrix(matrix_dir, profiles=selected_profiles)
        for matrix_dir in resolved_dirs
    )
    profile_rollups = tuple(
        _profile_rollup(profile, matrices)
        for profile in selected_profiles
    )
    default_decision_contract = _default_decision_contract(
        profile_rollups,
        matrices,
    )
    failure_reasons = [
        reason
        for matrix in matrices
        for reason in matrix["failure_reasons"]
    ] + list(default_decision_contract["failure_reasons"])
    return {
        "schema_version": SMART_DCA_DECISION_SUMMARY_SCHEMA_VERSION,
        "artifact_type": "smart_dca_decision_summary",
        "passed": not failure_reasons,
        "failure_reasons": failure_reasons,
        "matrix_count": len(matrices),
        "profiles": selected_profiles,
        "promotion_blocker_counts": _promotion_blocker_counts(profile_rollups),
        "performance_diagnosis_counts": _performance_diagnosis_counts(
            profile_rollups
        ),
        "research_priority_counts": _research_priority_counts(profile_rollups),
        "next_action_guardrail_counts": _next_action_guardrail_counts(
            profile_rollups
        ),
        "default_decision_contract": default_decision_contract,
        "profile_rollups": profile_rollups,
        "matrices": matrices,
    }


def write_smart_dca_decision_summary_json(
    path: str | PathLike[str],
    summary: Mapping[str, Any],
) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(_json_safe_value(summary), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_smart_dca_decision_summary_markdown(
    path: str | PathLike[str],
    summary: Mapping[str, Any],
) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        smart_dca_decision_summary_markdown(summary),
        encoding="utf-8",
    )


def smart_dca_decision_summary_markdown(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Smart DCA Promotion Gate / Default Decision",
        "",
        f"- Passed: `{str(bool(summary.get('passed'))).lower()}`",
        f"- Matrix count: `{int(summary.get('matrix_count', 0))}`",
        "",
        "## Profile Rollup",
        "",
        "| Profile | Gate | Runtime defaults | Smart statuses | Default change allowed | Observed best candidates | Promotion blockers | Research priorities | Next-action guardrails |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for profile in summary.get("profile_rollups", ()):
        lines.append(
            "| "
            + " | ".join(
                (
                    _markdown_cell(profile.get("profile", "")),
                    _markdown_cell("passed" if profile.get("passed") else "failed"),
                    _markdown_cell(
                        ", ".join(profile.get("runtime_default_recommendations", ()))
                    ),
                    _markdown_cell(
                        ", ".join(profile.get("smart_mode_enablement_statuses", ()))
                    ),
                    _markdown_cell(
                        str(profile.get("default_change_allowed_by_any_matrix"))
                    ),
                    _markdown_cell(
                        ", ".join(profile.get("observed_best_candidates", ()))
                    ),
                    _markdown_cell(
                        ", ".join(profile.get("promotion_blockers", ()))
                    ),
                    _markdown_cell(
                        ", ".join(profile.get("research_priorities", ()))
                    ),
                    _markdown_cell(
                        ", ".join(profile.get("next_action_guardrails", ()))
                    ),
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Overall Diagnostics",
            "",
            "| Type | Counts |",
            "| --- | --- |",
            "| Promotion blockers | "
            + _markdown_cell(
                _format_counts(summary.get("promotion_blocker_counts", {}))
            )
            + " |",
            "| Performance diagnoses | "
            + _markdown_cell(
                _format_counts(summary.get("performance_diagnosis_counts", {}))
            )
            + " |",
            "| Research priorities | "
            + _markdown_cell(
                _format_counts(summary.get("research_priority_counts", {}))
            )
            + " |",
            "| Next-action guardrails | "
            + _markdown_cell(
                _format_counts(summary.get("next_action_guardrail_counts", {}))
            )
            + " |",
        ]
    )
    contract = summary.get("default_decision_contract", {})
    if isinstance(contract, Mapping):
        lines.extend(
            [
                "",
                "## Default Decision Contract",
                "",
                f"- Passed: `{str(bool(contract.get('passed'))).lower()}`",
                "- Failure reasons: "
                + _markdown_cell(", ".join(contract.get("failure_reasons", ()))),
                "- Evidence hashes present: `"
                + str(bool(contract.get("evidence_hashes_present"))).lower()
                + "`",
                "",
                "| Profile | Runtime default fixed | Default change blocked | Required guardrails present | Required guardrails | Missing guardrails |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for profile in contract.get("profiles", ()):
            lines.append(
                "| "
                + " | ".join(
                    (
                        _markdown_cell(profile.get("profile", "")),
                        _markdown_cell(
                            str(bool(profile.get("runtime_default_fixed")))
                        ),
                        _markdown_cell(
                            str(bool(profile.get("default_change_blocked")))
                        ),
                        _markdown_cell(
                            str(bool(profile.get("required_guardrails_present")))
                        ),
                        _markdown_cell(
                            ", ".join(profile.get("required_guardrails", ()))
                        ),
                        _markdown_cell(
                            ", ".join(profile.get("missing_guardrails", ()))
                        ),
                    )
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## Profile Evidence",
            "",
            "| Profile | Matrix | Observed best | Pass rate | Worst terminal vs fixed | Median terminal vs fixed | Min rank score | Robustness gate | Effect gate | Diagnosis | Hold reason |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for profile in summary.get("profile_rollups", ()):
        for evidence in profile.get("observed_best_evidence", ()):
            lines.append(
                "| "
                + " | ".join(
                    (
                        _markdown_cell(profile.get("profile", "")),
                        _markdown_cell(evidence.get("matrix", "")),
                        _markdown_cell(evidence.get("observed_best_candidate", "")),
                        _markdown_cell(
                            _format_rate(evidence.get("observed_best_pass_rate"))
                        ),
                        _markdown_cell(
                            _format_pct(
                                evidence.get(
                                    "observed_best_min_relative_terminal_value_pct"
                                )
                            )
                        ),
                        _markdown_cell(
                            _format_pct(
                                evidence.get(
                                    "observed_best_median_relative_terminal_value_pct"
                                )
                            )
                        ),
                        _markdown_cell(
                            _format_decimal(
                                evidence.get("observed_best_min_rank_score")
                            )
                        ),
                        _markdown_cell(
                            _format_bool(
                                evidence.get(
                                    "observed_best_robustness_gate_passed"
                                )
                            )
                        ),
                        _markdown_cell(
                            _format_bool(
                                evidence.get(
                                    "observed_best_effect_size_gate_passed"
                                )
                            )
                        ),
                        _markdown_cell(
                            evidence.get(
                                "observed_best_dominant_performance_diagnosis",
                                "",
                            )
                        ),
                        _markdown_cell(evidence.get("observed_best_reason", "")),
                    )
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## Matrix Decisions",
            "",
            "| Matrix | Profile | Gate | Runtime default | Smart mode | Default change allowed | Observed best | Pass rate | Worst terminal vs fixed | Median terminal vs fixed | Robustness gate | Effect gate | Diagnosis | Reason |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for matrix in summary.get("matrices", ()):
        for profile in matrix.get("profiles", ()):
            lines.append(
                "| "
                + " | ".join(
                    (
                        _markdown_cell(matrix.get("label", "")),
                        _markdown_cell(profile.get("profile", "")),
                        _markdown_cell("passed" if profile.get("passed") else "failed"),
                        _markdown_cell(
                            profile.get("runtime_default_recommendation", "")
                        ),
                        _markdown_cell(profile.get("smart_mode_enablement_status", "")),
                        _markdown_cell(
                            str(profile.get("default_change_allowed_by_research"))
                        ),
                        _markdown_cell(profile.get("observed_best_candidate", "")),
                        _markdown_cell(
                            _format_rate(profile.get("observed_best_pass_rate"))
                        ),
                        _markdown_cell(
                            _format_pct(
                                profile.get(
                                    "observed_best_min_relative_terminal_value_pct"
                                )
                            )
                        ),
                        _markdown_cell(
                            _format_pct(
                                profile.get(
                                    "observed_best_median_relative_terminal_value_pct"
                                )
                            )
                        ),
                        _markdown_cell(
                            _format_bool(
                                profile.get("observed_best_robustness_gate_passed")
                            )
                        ),
                        _markdown_cell(
                            _format_bool(
                                profile.get("observed_best_effect_size_gate_passed")
                            )
                        ),
                        _markdown_cell(
                            profile.get(
                                "observed_best_dominant_performance_diagnosis",
                                "",
                            )
                        ),
                        _markdown_cell(profile.get("observed_best_reason", "")),
                    )
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## Evidence Hashes",
            "",
            "| Matrix | Review decision SHA-256 | Profile decisions SHA-256 |",
            "| --- | --- | --- |",
        ]
    )
    for matrix in summary.get("matrices", ()):
        lines.append(
            "| "
            + " | ".join(
                (
                    _markdown_cell(matrix.get("label", "")),
                    _markdown_cell(matrix.get("review_decision_sha256", "")),
                    _markdown_cell(
                        matrix.get("production_profile_decisions_sha256", "")
                    ),
                )
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def _summarize_matrix(
    matrix_dir: Path,
    *,
    profiles: tuple[str, ...],
) -> dict[str, Any]:
    review_decision_path = matrix_dir / "review_decision.json"
    production_profile_decisions_path = matrix_dir / "production_profile_decisions.csv"
    audit = audit_smart_dca_promotion_gate(
        review_decision_path=review_decision_path,
        production_profile_decisions_path=production_profile_decisions_path,
        profiles=profiles,
    )
    review_decision = _load_json_mapping(review_decision_path)
    observed_candidates = _observed_best_candidates_by_name(review_decision)
    return {
        "label": matrix_dir.name,
        "matrix_dir": str(matrix_dir),
        "passed": audit["passed"],
        "failure_reasons": tuple(audit["failure_reasons"]),
        "review_decision_path": str(review_decision_path),
        "review_decision_sha256": _sha256_file(review_decision_path),
        "production_profile_decisions_path": str(production_profile_decisions_path),
        "production_profile_decisions_sha256": _sha256_file(
            production_profile_decisions_path
        ),
        "overall_recommendation_status": audit["review_decision"][
            "overall_recommendation_status"
        ],
        "overall_recommendation_reason": audit["review_decision"][
            "overall_recommendation_reason"
        ],
        "matrix_coverage_gate_passed": audit["review_decision"][
            "matrix_coverage_gate_passed"
        ],
        "manual_review_gate_passed": audit["review_decision"][
            "manual_review_gate_passed"
        ],
        "profiles": tuple(
            _profile_with_observed_metrics(profile, observed_candidates)
            for profile in audit["profiles"]
        ),
    }


def _profile_with_observed_metrics(
    profile: Mapping[str, Any],
    observed_candidates: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    enriched = dict(profile)
    observed_best = str(profile.get("observed_best_candidate", "")).strip()
    candidate = observed_candidates.get(observed_best)
    if not candidate:
        return enriched
    for source_field, target_field in (
        ("pass_rate", "observed_best_pass_rate"),
        (
            "min_relative_terminal_value_pct",
            "observed_best_min_relative_terminal_value_pct",
        ),
        (
            "median_relative_terminal_value_pct",
            "observed_best_median_relative_terminal_value_pct",
        ),
        ("min_rank_score", "observed_best_min_rank_score"),
        ("robustness_gate_passed", "observed_best_robustness_gate_passed"),
        ("effect_size_gate_passed", "observed_best_effect_size_gate_passed"),
        (
            "dominant_performance_diagnosis",
            "observed_best_dominant_performance_diagnosis",
        ),
        ("performance_diagnoses", "observed_best_performance_diagnoses"),
    ):
        if source_field in candidate:
            enriched[target_field] = candidate[source_field]
    return enriched


def _observed_best_candidates_by_name(
    review_decision: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    raw_candidates = review_decision.get("observed_best_smart_candidates", ())
    if not isinstance(raw_candidates, list | tuple):
        return {}
    candidates: dict[str, Mapping[str, Any]] = {}
    for raw_candidate in raw_candidates:
        if not isinstance(raw_candidate, Mapping):
            continue
        name = str(raw_candidate.get("name", "")).strip()
        if name:
            candidates[name] = raw_candidate
    return candidates


def _load_json_mapping(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file_obj:
        payload = json.load(file_obj)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return payload


def _profile_rollup(
    profile: str,
    matrices: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    matrix_rows = [
        (matrix, row)
        for matrix in matrices
        for row in matrix.get("profiles", ())
        if row.get("profile") == profile
    ]
    rows = [row for _, row in matrix_rows]
    default_recommendations = tuple(
        sorted({str(row.get("runtime_default_recommendation", "")) for row in rows})
    )
    smart_statuses = tuple(
        sorted({str(row.get("smart_mode_enablement_status", "")) for row in rows})
    )
    default_change_allowed = any(
        row.get("default_change_allowed_by_research") is True
        for row in rows
    )
    observed_best_evidence = tuple(
        _observed_best_evidence(matrix, row)
        for matrix, row in matrix_rows
        if str(row.get("observed_best_candidate", "")).strip()
    )
    promotion_blockers = _profile_promotion_blockers(rows)
    research_priorities = _profile_research_priorities(
        promotion_blockers,
        observed_best_evidence,
    )
    return {
        "profile": profile,
        "matrix_count": len(rows),
        "passed": bool(rows) and all(bool(row.get("passed")) for row in rows),
        "runtime_default_recommendations": default_recommendations,
        "smart_mode_enablement_statuses": smart_statuses,
        "default_change_allowed_by_any_matrix": default_change_allowed,
        "observed_best_candidates": tuple(
            str(row.get("observed_best_candidate", ""))
            for row in rows
            if str(row.get("observed_best_candidate", "")).strip()
        ),
        "observed_best_evidence": observed_best_evidence,
        "promotion_blockers": promotion_blockers,
        "research_priorities": research_priorities,
        "next_action_guardrails": _profile_next_action_guardrails(
            research_priorities
        ),
    }


def _profile_promotion_blockers(rows: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    blocker_set: set[str] = set()
    materialized_rows = tuple(rows)
    if not materialized_rows:
        return ("profile_not_evaluated",)
    if any(row.get("passed") is not True for row in materialized_rows):
        blocker_set.add("promotion_gate_audit_failed")
    observed_rows = tuple(
        row
        for row in materialized_rows
        if str(row.get("observed_best_candidate", "")).strip()
    )
    if not observed_rows:
        blocker_set.add("no_observed_smart_candidate")
    if not any(
        row.get("default_change_allowed_by_research") is True
        for row in materialized_rows
    ):
        blocker_set.add("default_change_not_allowed_by_research")
    if any(
        row.get("manual_review_required_before_default_change") is True
        for row in materialized_rows
    ):
        blocker_set.add("manual_review_required_before_default_change")
    smart_statuses = {
        str(row.get("smart_mode_enablement_status", "")).strip()
        for row in materialized_rows
    }
    if smart_statuses <= {"not_evaluated", "not_recommended_for_enablement"}:
        blocker_set.add("smart_mode_not_recommended_for_enablement")
    for row in observed_rows:
        if row.get("observed_best_robustness_gate_passed") is False:
            blocker_set.add("robustness_gate_failed")
        if row.get("observed_best_effect_size_gate_passed") is False:
            blocker_set.add("effect_size_gate_failed")
        reason = str(row.get("observed_best_reason", "")).strip()
        if reason == "insufficient_effect_size_vs_fixed_dca":
            blocker_set.add("effect_size_gate_failed")
        elif reason == "no_candidate_passed_robustness_gate":
            blocker_set.add("robustness_gate_failed")
    return tuple(sorted(blocker_set))


def _observed_best_evidence(
    matrix: Mapping[str, Any],
    row: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "matrix": matrix.get("label", ""),
        "observed_best_candidate": row.get("observed_best_candidate", ""),
        "observed_best_status": row.get("observed_best_status", ""),
        "observed_best_reason": row.get("observed_best_reason", ""),
        "observed_best_pass_rate": row.get("observed_best_pass_rate"),
        "observed_best_min_relative_terminal_value_pct": row.get(
            "observed_best_min_relative_terminal_value_pct"
        ),
        "observed_best_median_relative_terminal_value_pct": row.get(
            "observed_best_median_relative_terminal_value_pct"
        ),
        "observed_best_min_rank_score": row.get("observed_best_min_rank_score"),
        "observed_best_robustness_gate_passed": row.get(
            "observed_best_robustness_gate_passed"
        ),
        "observed_best_effect_size_gate_passed": row.get(
            "observed_best_effect_size_gate_passed"
        ),
        "observed_best_dominant_performance_diagnosis": row.get(
            "observed_best_dominant_performance_diagnosis",
            "",
        ),
        "observed_best_performance_diagnoses": row.get(
            "observed_best_performance_diagnoses",
            (),
        ),
    }


def _profile_research_priorities(
    promotion_blockers: Iterable[str],
    observed_best_evidence: Iterable[Mapping[str, Any]],
) -> tuple[str, ...]:
    blockers = set(promotion_blockers)
    diagnoses = {
        str(diagnosis)
        for evidence in observed_best_evidence
        for diagnosis in evidence.get("observed_best_performance_diagnoses", ())
        if str(diagnosis).strip()
    }
    priorities: set[str] = {"hold_fixed_default"}
    if "effect_size_gate_failed" in blockers:
        priorities.add("require_material_terminal_edge_before_promotion")
        priorities.add("avoid_parameter_tuning_without_new_independent_signal")
    if "robustness_gate_failed" in blockers:
        priorities.add("improve_cross_scenario_robustness_before_manual_review")
    if {
        "excess_terminal_cash",
        "lower_deployment_rate",
        "skipped_buy_cash_drag",
    } & diagnoses:
        priorities.add("avoid_skip_heavy_cash_drag_variants_as_default")
    if "terminal_underperformance_vs_fixed" in diagnoses:
        priorities.add("require_terminal_value_non_regression_before_promotion")
    if "no_observed_smart_candidate" in blockers:
        priorities.add("expand_contract_covered_signal_family_before_retest")
    return tuple(sorted(priorities))


def _profile_next_action_guardrails(
    research_priorities: Iterable[str],
) -> tuple[str, ...]:
    guardrails = {
        SMART_DCA_NEXT_ACTION_GUARDRAILS_BY_PRIORITY[priority]
        for priority in research_priorities
        if priority in SMART_DCA_NEXT_ACTION_GUARDRAILS_BY_PRIORITY
    }
    return tuple(sorted(guardrails))


def _default_decision_contract(
    profile_rollups: Iterable[Mapping[str, Any]],
    matrices: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    profile_contracts = tuple(
        _profile_default_decision_contract(profile)
        for profile in profile_rollups
    )
    matrix_hash_failures = tuple(
        reason
        for matrix in matrices
        for reason in _matrix_evidence_hash_failure_reasons(matrix)
    )
    failure_reasons = tuple(
        [
            reason
            for profile in profile_contracts
            for reason in profile["failure_reasons"]
        ]
        + list(matrix_hash_failures)
    )
    return {
        "schema_version": SMART_DCA_DEFAULT_DECISION_CONTRACT_SCHEMA_VERSION,
        "artifact_type": "smart_dca_default_decision_contract",
        "passed": not failure_reasons,
        "failure_reasons": failure_reasons,
        "evidence_hashes_present": not matrix_hash_failures,
        "profiles": profile_contracts,
    }


def _profile_default_decision_contract(
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    profile_name = str(profile.get("profile", ""))
    runtime_defaults = tuple(profile.get("runtime_default_recommendations", ()))
    actual_guardrails = tuple(profile.get("next_action_guardrails", ()))
    required_guardrails = tuple(
        sorted(
            {
                "keep_fixed_dca_default",
                *SMART_DCA_REQUIRED_DEFAULT_DECISION_GUARDRAILS_BY_PROFILE.get(
                    profile_name,
                    (),
                ),
            }
        )
    )
    missing_guardrails = tuple(
        guardrail
        for guardrail in required_guardrails
        if guardrail not in actual_guardrails
    )
    runtime_default_fixed = runtime_defaults == ("fixed_dca",)
    default_change_blocked = (
        profile.get("default_change_allowed_by_any_matrix") is False
    )
    failure_reasons: list[str] = []
    if not runtime_default_fixed:
        failure_reasons.append(f"default_contract_runtime_default_not_fixed:{profile_name}")
    if not default_change_blocked:
        failure_reasons.append(f"default_contract_default_change_not_blocked:{profile_name}")
    failure_reasons.extend(
        f"default_contract_missing_guardrail:{profile_name}:{guardrail}"
        for guardrail in missing_guardrails
    )
    return {
        "profile": profile_name,
        "passed": not failure_reasons,
        "failure_reasons": tuple(failure_reasons),
        "runtime_default_recommendations": runtime_defaults,
        "runtime_default_fixed": runtime_default_fixed,
        "default_change_blocked": default_change_blocked,
        "required_guardrails": required_guardrails,
        "missing_guardrails": missing_guardrails,
        "required_guardrails_present": not missing_guardrails,
    }


def _matrix_evidence_hash_failure_reasons(
    matrix: Mapping[str, Any],
) -> tuple[str, ...]:
    label = str(matrix.get("label", ""))
    failures: list[str] = []
    for field in (
        "review_decision_sha256",
        "production_profile_decisions_sha256",
    ):
        value = str(matrix.get(field, ""))
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            failures.append(f"default_contract_missing_evidence_hash:{label}:{field}")
    return tuple(failures)


def _promotion_blocker_counts(
    profile_rollups: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    values = [
        str(blocker)
        for profile in profile_rollups
        for blocker in profile.get("promotion_blockers", ())
        if str(blocker).strip()
    ]
    return _count_values(values)


def _performance_diagnosis_counts(
    profile_rollups: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    values = [
        str(diagnosis)
        for profile in profile_rollups
        for evidence in profile.get("observed_best_evidence", ())
        for diagnosis in evidence.get("observed_best_performance_diagnoses", ())
        if str(diagnosis).strip()
    ]
    return _count_values(values)


def _research_priority_counts(
    profile_rollups: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    values = [
        str(priority)
        for profile in profile_rollups
        for priority in profile.get("research_priorities", ())
        if str(priority).strip()
    ]
    return _count_values(values)


def _next_action_guardrail_counts(
    profile_rollups: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    values = [
        str(guardrail)
        for profile in profile_rollups
        for guardrail in profile.get("next_action_guardrails", ())
        if str(guardrail).strip()
    ]
    return _count_values(values)


def _count_values(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return {
        key: counts[key]
        for key in sorted(counts)
    }


def _normalize_profiles(profiles: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(str(profile or "").strip() for profile in profiles)
    if not normalized or any(not profile for profile in normalized):
        raise ValueError("at least one non-empty profile is required")
    duplicates = sorted({profile for profile in normalized if normalized.count(profile) > 1})
    if duplicates:
        raise ValueError(f"duplicate profiles requested: {', '.join(duplicates)}")
    return normalized


def _format_rate(value: object) -> str:
    if value in (None, ""):
        return ""
    return f"{float(value) * 100:.2f}%"


def _format_pct(value: object) -> str:
    if value in (None, ""):
        return ""
    return f"{float(value):.2f}%"


def _format_bool(value: object) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if value in (None, ""):
        return ""
    return str(value)


def _format_decimal(value: object) -> str:
    if value in (None, ""):
        return ""
    return f"{float(value):.2f}"


def _format_counts(value: object) -> str:
    if not isinstance(value, Mapping) or not value:
        return ""
    return ", ".join(
        f"{key}: {int(count)}"
        for key, count in value.items()
    )


def _markdown_cell(value: object) -> str:
    text = str(value or "").replace("\n", " ").replace("|", "\\|")
    return text if text else "-"


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe_value(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
