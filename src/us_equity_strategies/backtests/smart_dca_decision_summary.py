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
    failure_reasons = [
        reason
        for matrix in matrices
        for reason in matrix["failure_reasons"]
    ]
    return {
        "schema_version": SMART_DCA_DECISION_SUMMARY_SCHEMA_VERSION,
        "artifact_type": "smart_dca_decision_summary",
        "passed": not failure_reasons,
        "failure_reasons": failure_reasons,
        "matrix_count": len(matrices),
        "profiles": selected_profiles,
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
        "| Profile | Gate | Runtime defaults | Smart statuses | Default change allowed | Observed best candidates |",
        "| --- | --- | --- | --- | --- | --- |",
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
    rows = [
        row
        for matrix in matrices
        for row in matrix.get("profiles", ())
        if row.get("profile") == profile
    ]
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
