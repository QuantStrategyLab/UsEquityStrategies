from __future__ import annotations

import csv
import json
from pathlib import Path

from us_equity_strategies.backtests.smart_dca_decision_summary import (
    smart_dca_decision_summary_markdown,
    summarize_smart_dca_decision_matrices,
)
from us_equity_strategies.backtests.smart_dca_decision_summary_cli import (
    main as decision_summary_main,
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


def test_smart_dca_decision_summary_aggregates_matrix_artifacts(
    tmp_path,
) -> None:
    nasdaq_matrix = _write_matrix_artifacts(
        tmp_path / "nasdaq_price_proxy_matrix",
        nasdaq_best="nasdaq_sp500_price_no_skip",
        ibit_best="",
    )
    ibit_matrix = _write_matrix_artifacts(
        tmp_path / "ibit_helper_matrix",
        nasdaq_best="",
        ibit_best="ibit_btc_precomputed_ahr999_guarded_cycle",
    )

    summary = summarize_smart_dca_decision_matrices(
        [nasdaq_matrix, ibit_matrix],
    )

    assert summary["passed"] is True
    assert summary["matrix_count"] == 2
    assert summary["promotion_blocker_counts"]["effect_size_gate_failed"] == 2
    assert summary["promotion_blocker_counts"]["robustness_gate_failed"] == 1
    assert summary["performance_diagnosis_counts"][
        "terminal_edge_non_negative"
    ] == 2
    assert summary["profile_rollups"][0]["profile"] == "ibit_smart_dca"
    assert summary["profile_rollups"][0][
        "runtime_default_recommendations"
    ] == ("fixed_dca",)
    ibit_evidence = summary["profile_rollups"][0]["observed_best_evidence"][0]
    assert ibit_evidence["matrix"] == "ibit_helper_matrix"
    assert ibit_evidence["observed_best_pass_rate"] == 0.95
    assert ibit_evidence["observed_best_effect_size_gate_passed"] is False
    assert "robustness_gate_failed" in summary["profile_rollups"][0][
        "promotion_blockers"
    ]
    assert summary["profile_rollups"][1]["profile"] == "nasdaq_sp500_smart_dca"
    assert "nasdaq_sp500_price_no_skip" in summary["profile_rollups"][1][
        "observed_best_candidates"
    ]
    assert "effect_size_gate_failed" in summary["profile_rollups"][1][
        "promotion_blockers"
    ]
    ibit_profile = summary["matrices"][1]["profiles"][0]
    assert ibit_profile["observed_best_pass_rate"] == 0.95
    assert ibit_profile["observed_best_min_relative_terminal_value_pct"] == -4.4593
    assert ibit_profile["observed_best_robustness_gate_passed"] is False

    markdown = smart_dca_decision_summary_markdown(summary)
    assert "# Smart DCA Promotion Gate / Default Decision" in markdown
    assert "## Profile Rollup" in markdown
    assert "## Overall Diagnostics" in markdown
    assert "robustness_gate_failed: 1" in markdown
    assert "terminal_edge_non_negative: 2" in markdown
    assert "Promotion blockers" in markdown
    assert "default_change_not_allowed_by_research" in markdown
    assert "## Profile Evidence" in markdown
    assert "Min rank score" in markdown
    assert "Worst terminal vs fixed" in markdown
    assert "95.00%" in markdown
    assert "-3.61" in markdown
    assert "-4.46%" in markdown
    assert "## Evidence Hashes" in markdown
    assert "nasdaq_price_proxy_matrix" in markdown
    assert "ibit_btc_precomputed_ahr999_guarded_cycle" in markdown


def test_smart_dca_decision_summary_cli_writes_json_and_markdown(
    tmp_path,
    capsys,
) -> None:
    matrix_dir = _write_matrix_artifacts(
        tmp_path / "nasdaq_price_proxy_matrix",
        nasdaq_best="nasdaq_sp500_price_no_skip",
        ibit_best="",
    )
    output_json = tmp_path / "summary.json"
    output_md = tmp_path / "summary.md"

    result = decision_summary_main(
        [
            "--matrix-dir",
            str(matrix_dir),
            "--profile",
            "nasdaq_sp500_smart_dca",
            "--output-json",
            str(output_json),
            "--output-md",
            str(output_md),
            "--pretty",
        ]
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
    assert json.loads(output_json.read_text(encoding="utf-8"))["matrix_count"] == 1
    output_markdown = output_md.read_text(encoding="utf-8")
    assert "nasdaq_sp500_smart_dca" in output_markdown
    assert "100.00%" in output_markdown
    assert "terminal_edge_non_negative" in output_markdown
    assert "Profile decisions SHA-256" in output_markdown


def test_smart_dca_decision_summary_cli_returns_one_when_gate_fails(
    tmp_path,
    capsys,
) -> None:
    matrix_dir = _write_matrix_artifacts(
        tmp_path / "bad_matrix",
        nasdaq_best="nasdaq_sp500_price_no_skip",
        ibit_best="",
        nasdaq_overrides={"default_change_allowed_by_research": "True"},
    )

    result = decision_summary_main(
        [
            "--matrix-dir",
            str(matrix_dir),
            "--profile",
            "nasdaq_sp500_smart_dca",
        ]
    )

    assert result == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is False
    assert any("profile_default_change_allowed" in item for item in payload["failure_reasons"])


def _write_matrix_artifacts(
    matrix_dir: Path,
    *,
    nasdaq_best: str,
    ibit_best: str,
    nasdaq_overrides: dict[str, str] | None = None,
) -> Path:
    matrix_dir.mkdir(parents=True)
    rows = [
        _profile_decision(
            "ibit_smart_dca",
            production_equivalent_candidate="ibit_btc_precomputed_ahr999_cycle",
            selection_group="ibit_btc_ahr999_precomputed",
            observed_best_candidate=ibit_best,
            observed_best_reason=(
                "no_candidate_passed_robustness_gate"
                if ibit_best
                else "profile_not_in_candidate_universe"
            ),
        ),
        _profile_decision(
            "nasdaq_sp500_smart_dca",
            production_equivalent_candidate="nasdaq_sp500_price_no_skip",
            selection_group="nasdaq_sp500_price",
            observed_best_candidate=nasdaq_best,
            observed_best_reason=(
                "insufficient_effect_size_vs_fixed_dca"
                if nasdaq_best
                else "profile_not_in_candidate_universe"
            ),
            overrides=nasdaq_overrides,
        ),
    ]
    decisions_path = matrix_dir / "production_profile_decisions.csv"
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
        "observed_best_smart_candidates": _observed_best_smart_candidates(
            nasdaq_best=nasdaq_best,
            ibit_best=ibit_best,
        ),
        "production_profile_decisions": [
            _review_profile_decision(row) for row in rows
        ],
    }
    (matrix_dir / "review_decision.json").write_text(
        json.dumps(review_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return matrix_dir


def _profile_decision(
    profile: str,
    *,
    production_equivalent_candidate: str,
    selection_group: str,
    observed_best_candidate: str,
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
        "observed_best_status": (
            "hold_default_fixed_dca"
            if observed_best_candidate
            else "not_evaluated"
        ),
        "observed_best_reason": observed_best_reason,
        "observed_best_dominant_performance_diagnosis": (
            "terminal_underperformance_vs_fixed"
        ),
        "observed_best_performance_diagnoses": "terminal_underperformance_vs_fixed",
        "runtime_default_recommendation": "fixed_dca",
        "runtime_default_change_policy": "manual_review_required_no_auto_enable",
        "smart_mode_enablement_status": (
            "not_recommended_for_enablement"
            if observed_best_candidate
            else "not_evaluated"
        ),
        "manual_review_required_before_default_change": "True",
        "default_change_allowed_by_research": "False",
    }
    if overrides:
        row.update(overrides)
    return row


def _observed_best_smart_candidates(
    *,
    nasdaq_best: str,
    ibit_best: str,
) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    if nasdaq_best:
        candidates.append(
            {
                "name": nasdaq_best,
                "candidate_definition_sha256": "b" * 64,
                "selection_group": "nasdaq_sp500_price",
                "status": "hold_default_fixed_dca",
                "reason": "insufficient_effect_size_vs_fixed_dca",
                "pass_rate": 1.0,
                "min_relative_terminal_value_pct": 0.0,
                "median_relative_terminal_value_pct": 0.0,
                "min_rank_score": 0.0,
                "robustness_gate_passed": True,
                "effect_size_gate_passed": False,
                "dominant_performance_diagnosis": "terminal_edge_non_negative",
                "performance_diagnoses": ["terminal_edge_non_negative"],
            }
        )
    if ibit_best:
        candidates.append(
            {
                "name": ibit_best,
                "candidate_definition_sha256": "b" * 64,
                "selection_group": "ibit_btc_ahr999_precomputed",
                "status": "hold_default_fixed_dca",
                "reason": "no_candidate_passed_robustness_gate",
                "pass_rate": 0.95,
                "min_relative_terminal_value_pct": -4.4593,
                "median_relative_terminal_value_pct": 0.0,
                "min_rank_score": -3.6095,
                "robustness_gate_passed": False,
                "effect_size_gate_passed": False,
                "dominant_performance_diagnosis": "terminal_edge_non_negative",
                "performance_diagnoses": [
                    "drawdown_better_than_fixed",
                    "terminal_edge_non_negative",
                    "terminal_underperformance_vs_fixed",
                ],
            }
        )
    return candidates


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
