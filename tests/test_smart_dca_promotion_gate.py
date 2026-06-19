from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from us_equity_strategies.backtests.smart_dca_promotion_gate import (
    audit_smart_dca_promotion_gate,
)
from us_equity_strategies.backtests.smart_dca_promotion_gate_cli import (
    main as promotion_gate_main,
)


FIELDNAMES = [
    "profile",
    "production_equivalent_candidate",
    "production_equivalent_candidate_definition_sha256",
    "production_equivalent_in_candidate_universe",
    "selection_group",
    "observed_best_candidate",
    "observed_best_candidate_definition_sha256",
    "observed_best_status",
    "observed_best_reason",
    "observed_best_dominant_performance_diagnosis",
    "observed_best_performance_diagnoses",
    "runtime_default_recommendation",
    "runtime_default_change_policy",
    "smart_mode_enablement_status",
    "manual_review_required_before_default_change",
    "default_change_allowed_by_research",
]


def test_smart_dca_promotion_gate_accepts_fixed_default_decisions(
    tmp_path,
) -> None:
    review_path, decisions_path = _write_gate_artifacts(tmp_path)

    audit = audit_smart_dca_promotion_gate(
        review_decision_path=review_path,
        production_profile_decisions_path=decisions_path,
    )

    assert audit["passed"] is True
    assert audit["failure_reasons"] == []
    assert [profile["profile"] for profile in audit["profiles"]] == [
        "ibit_smart_dca",
        "nasdaq_sp500_smart_dca",
    ]
    assert audit["review_decision"]["candidate_universe_policy"] == (
        "frozen_preset_names_no_parameter_search"
    )


def test_smart_dca_promotion_gate_accepts_scenario_manifest_evidence(
    tmp_path,
) -> None:
    review_path, decisions_path = _write_gate_artifacts(tmp_path)
    scenario_manifest_path = _write_scenario_manifest(
        tmp_path,
        review_path=review_path,
        decisions_path=decisions_path,
        runtime_consumer_coverage=True,
    )

    audit = audit_smart_dca_promotion_gate(
        review_decision_path=review_path,
        production_profile_decisions_path=decisions_path,
        scenario_manifest_path=scenario_manifest_path,
        require_runtime_consumer_coverage=True,
    )

    assert audit["passed"] is True
    assert audit["scenario_manifest"]["passed"] is True
    assert audit["scenario_manifest"]["review_decision_verified"] is True
    assert audit["scenario_manifest"][
        "production_profile_decisions_verified"
    ] is True
    assert audit["scenario_manifest"]["candidate_summary_present"] is True
    assert audit["scenario_manifest"]["candidate_specs_present"] is True
    assert audit["scenario_manifest"]["runtime_consumer_coverage_verified"] is True
    assert (
        audit["scenario_manifest"]["runtime_consumer_coverage_artifact_verified"]
        is True
    )


def test_smart_dca_promotion_gate_accepts_consumption_audit_runtime_evidence(
    tmp_path,
) -> None:
    review_path, decisions_path = _write_gate_artifacts(tmp_path)
    scenario_manifest_path = _write_scenario_manifest(
        tmp_path,
        review_path=review_path,
        decisions_path=decisions_path,
        runtime_consumer_coverage=True,
    )
    scenario_manifest = json.loads(
        scenario_manifest_path.read_text(encoding="utf-8")
    )
    consumption_audit_path = _write_text_file(
        tmp_path / "consumption_audit.json",
        json.dumps(
            {
                "schema_version": "market_signal_consumption_audit.v1",
                "all_runtime_consumers_covered": True,
            },
            sort_keys=True,
        )
        + "\n",
    )
    consumption_audit_record = _file_record(consumption_audit_path, root=tmp_path)
    consumption_audit_record.update(
        {
            "schema_version": "market_signal_consumption_audit.v1",
            "all_runtime_consumers_covered": True,
        }
    )
    scenario_manifest["metadata"]["input_artifacts"] = {
        "signal_consumption_audit": consumption_audit_record
    }
    scenario_manifest_path.write_text(
        json.dumps(scenario_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    audit = audit_smart_dca_promotion_gate(
        review_decision_path=review_path,
        production_profile_decisions_path=decisions_path,
        scenario_manifest_path=scenario_manifest_path,
        require_runtime_consumer_coverage=True,
    )

    assert audit["passed"] is True
    assert audit["scenario_manifest"]["runtime_consumer_coverage_verified"] is True
    assert (
        audit["scenario_manifest"]["runtime_consumer_coverage_artifact_verified"]
        is True
    )


def test_smart_dca_promotion_gate_rejects_runtime_coverage_hash_mismatch(
    tmp_path,
) -> None:
    review_path, decisions_path = _write_gate_artifacts(tmp_path)
    scenario_manifest_path = _write_scenario_manifest(
        tmp_path,
        review_path=review_path,
        decisions_path=decisions_path,
        runtime_consumer_coverage=True,
    )
    coverage_path = tmp_path / "runtime_coverage_manifest.json"
    coverage_path.write_text("tampered\n", encoding="utf-8")

    audit = audit_smart_dca_promotion_gate(
        review_decision_path=review_path,
        production_profile_decisions_path=decisions_path,
        scenario_manifest_path=scenario_manifest_path,
        require_runtime_consumer_coverage=True,
    )

    assert audit["passed"] is False
    assert (
        "scenario_manifest_input_artifact_sha256_mismatch:"
        "signal_source_family_catalog_manifest"
    ) in audit["failure_reasons"]
    assert "scenario_manifest_runtime_consumer_coverage_not_verified" in audit[
        "failure_reasons"
    ]


def test_smart_dca_promotion_gate_rejects_missing_runtime_coverage_evidence(
    tmp_path,
) -> None:
    review_path, decisions_path = _write_gate_artifacts(tmp_path)
    scenario_manifest_path = _write_scenario_manifest(
        tmp_path,
        review_path=review_path,
        decisions_path=decisions_path,
        runtime_consumer_coverage=False,
    )

    audit = audit_smart_dca_promotion_gate(
        review_decision_path=review_path,
        production_profile_decisions_path=decisions_path,
        scenario_manifest_path=scenario_manifest_path,
        require_runtime_consumer_coverage=True,
    )

    assert audit["passed"] is False
    assert "scenario_manifest_runtime_consumer_coverage_not_required" in audit[
        "failure_reasons"
    ]
    assert "scenario_manifest_runtime_consumer_coverage_not_verified" in audit[
        "failure_reasons"
    ]


def test_smart_dca_promotion_gate_rejects_default_change_allowed(
    tmp_path,
) -> None:
    review_path, decisions_path = _write_gate_artifacts(
        tmp_path,
        ibit_overrides={
            "runtime_default_recommendation": "smart_dca",
            "manual_review_required_before_default_change": "False",
            "default_change_allowed_by_research": "True",
        },
    )

    audit = audit_smart_dca_promotion_gate(
        review_decision_path=review_path,
        production_profile_decisions_path=decisions_path,
    )

    assert audit["passed"] is False
    assert "profile_runtime_default_not_fixed:ibit_smart_dca" in audit[
        "failure_reasons"
    ]
    assert "profile_manual_review_not_required:ibit_smart_dca" in audit[
        "failure_reasons"
    ]
    assert "profile_default_change_allowed:ibit_smart_dca" in audit[
        "failure_reasons"
    ]


def test_smart_dca_promotion_gate_cli_reports_gate_failures(
    tmp_path,
    capsys,
) -> None:
    review_path, decisions_path = _write_gate_artifacts(tmp_path)
    scenario_manifest_path = _write_scenario_manifest(
        tmp_path,
        review_path=review_path,
        decisions_path=decisions_path,
        runtime_consumer_coverage=True,
    )

    result = promotion_gate_main(
        [
            "--review-decision",
            str(review_path),
            "--production-profile-decisions",
            str(decisions_path),
            "--scenario-manifest",
            str(scenario_manifest_path),
            "--require-runtime-consumer-coverage",
            "--profile",
            "nasdaq_sp500_smart_dca",
            "--profile",
            "missing_profile",
            "--pretty",
        ]
    )

    assert result == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is False
    assert payload["scenario_manifest"]["runtime_consumer_coverage_verified"] is True
    assert "profile_missing_from_csv:missing_profile" in payload["failure_reasons"]
    assert "profile_missing_from_review_decision:missing_profile" in payload[
        "failure_reasons"
    ]


def test_smart_dca_promotion_gate_cli_rejects_bad_schema(
    tmp_path,
    capsys,
) -> None:
    review_path, decisions_path = _write_gate_artifacts(
        tmp_path,
        review_overrides={"schema_version": "unexpected"},
    )

    result = promotion_gate_main(
        [
            "--review-decision",
            str(review_path),
            "--production-profile-decisions",
            str(decisions_path),
        ]
    )

    assert result == 1
    payload = json.loads(capsys.readouterr().out)
    assert "review_schema_version_mismatch" in payload["failure_reasons"][0]


def _write_gate_artifacts(
    tmp_path: Path,
    *,
    ibit_overrides: dict[str, str] | None = None,
    review_overrides: dict[str, object] | None = None,
) -> tuple[Path, Path]:
    rows = [
        _profile_decision(
            "ibit_smart_dca",
            production_equivalent_candidate="ibit_btc_precomputed_ahr999_cycle",
            selection_group="ibit_btc_ahr999_precomputed",
            observed_best_candidate="ibit_btc_precomputed_ahr999_guarded_cycle",
            observed_best_status="hold_default_fixed_dca",
            observed_best_reason="no_candidate_passed_robustness_gate",
            overrides=ibit_overrides,
        ),
        _profile_decision(
            "nasdaq_sp500_smart_dca",
            production_equivalent_candidate="nasdaq_sp500_price_no_skip",
            selection_group="nasdaq_sp500_price",
            observed_best_candidate="nasdaq_sp500_price_no_skip",
            observed_best_status="hold_default_fixed_dca",
            observed_best_reason="insufficient_effect_size_vs_fixed_dca",
        ),
    ]
    decisions_path = tmp_path / "production_profile_decisions.csv"
    with decisions_path.open("w", newline="", encoding="utf-8") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    review_payload = {
        "schema_version": "smart_dca_research_artifacts.v1",
        "artifact_type": "smart_dca_review_decision",
        "selection_policy": "fixed_preset_no_parameter_search",
        "candidate_universe_policy": "frozen_preset_names_no_parameter_search",
        "effect_size_policy": "fixed_minimum_effect_no_parameter_search",
        "runtime_default_recommendation": "fixed_dca",
        "runtime_default_change_policy": "manual_review_required_no_auto_enable",
        "smart_mode_enablement_status": "not_recommended_for_enablement",
        "matrix_coverage_gate_passed": True,
        "manual_review_gate_passed": False,
        "overall_recommendation_status": "hold_default_fixed_dca",
        "overall_recommendation_reason": "insufficient_effect_size_vs_fixed_dca",
        "production_profile_decisions": [
            _review_profile_decision(row) for row in rows
        ],
    }
    if review_overrides:
        review_payload.update(review_overrides)
    review_path = tmp_path / "review_decision.json"
    review_path.write_text(
        json.dumps(review_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return review_path, decisions_path


def _write_scenario_manifest(
    tmp_path: Path,
    *,
    review_path: Path,
    decisions_path: Path,
    runtime_consumer_coverage: bool,
) -> Path:
    scenario_files = [
        review_path,
        decisions_path,
        _write_text_file(tmp_path / "scenario_index.csv", "scenario,name\n"),
        _write_text_file(tmp_path / "robustness_summary.csv", "name,pass_rate\n"),
        _write_text_file(tmp_path / "selection_summary.csv", "name,status\n"),
        _write_text_file(tmp_path / "scenario_coverage.csv", "gate,passed\n"),
        _write_text_file(
            tmp_path / "monthly_day_15" / "candidate_summary.csv",
            "name,open_parameter_search\nsmart,False\n",
        ),
        _write_text_file(
            tmp_path / "monthly_day_15" / "candidate_specs.csv",
            "name,parameter_name,parameter_value\nsmart,multiplier,1.0\n",
        ),
    ]
    runtime_coverage_artifact = _write_text_file(
        tmp_path / "runtime_coverage_manifest.json",
        json.dumps(
            {
                "schema_version": "market_signal_source_family_catalog_manifest.v1",
                "all_runtime_consumers_covered": runtime_consumer_coverage,
            },
            sort_keys=True,
        )
        + "\n",
    )
    runtime_coverage_record = _file_record(runtime_coverage_artifact, root=tmp_path)
    runtime_coverage_record.update(
        {
            "schema_version": "market_signal_source_family_catalog_manifest.v1",
            "all_runtime_consumers_covered": runtime_consumer_coverage,
        }
    )
    scenario_manifest_path = tmp_path / "scenario_manifest.json"
    payload = {
        "schema_version": "smart_dca_research_artifacts.v1",
        "artifact_type": "smart_dca_research_scenario_matrix",
        "min_review_scenarios": 3,
        "scenario_names": ["monthly_day_15"],
        "metadata": {
            "research_config": {
                "candidate_set": "ibit_btc_ahr999_mayer_precomputed_variants",
                "require_runtime_consumer_coverage": runtime_consumer_coverage,
            },
            "input_artifacts": {
                "signal_source_family_catalog_manifest": runtime_coverage_record
            },
        },
        "files": [_file_record(path, root=tmp_path) for path in scenario_files],
    }
    scenario_manifest_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return scenario_manifest_path


def _write_text_file(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _file_record(path: Path, *, root: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _profile_decision(
    profile: str,
    *,
    production_equivalent_candidate: str,
    selection_group: str,
    observed_best_candidate: str,
    observed_best_status: str,
    observed_best_reason: str,
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    row = {
        "profile": profile,
        "production_equivalent_candidate": production_equivalent_candidate,
        "production_equivalent_candidate_definition_sha256": "a" * 64,
        "production_equivalent_in_candidate_universe": "True",
        "selection_group": selection_group,
        "observed_best_candidate": observed_best_candidate,
        "observed_best_candidate_definition_sha256": "b" * 64,
        "observed_best_status": observed_best_status,
        "observed_best_reason": observed_best_reason,
        "observed_best_dominant_performance_diagnosis": (
            "terminal_underperformance_vs_fixed"
        ),
        "observed_best_performance_diagnoses": "terminal_underperformance_vs_fixed",
        "runtime_default_recommendation": "fixed_dca",
        "runtime_default_change_policy": "manual_review_required_no_auto_enable",
        "smart_mode_enablement_status": "not_recommended_for_enablement",
        "manual_review_required_before_default_change": "True",
        "default_change_allowed_by_research": "False",
    }
    if overrides:
        row.update(overrides)
    return row


def _review_profile_decision(row: dict[str, str]) -> dict[str, object]:
    return {
        key: _review_value(value)
        for key, value in row.items()
    }


def _review_value(value: str) -> object:
    if value == "True":
        return True
    if value == "False":
        return False
    return value
