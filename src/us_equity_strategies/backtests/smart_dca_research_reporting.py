from __future__ import annotations

from collections.abc import Iterable, Mapping
import math

from .smart_dca_research_models import (
    DcaCandidateEvaluation,
    DcaResearchResult,
    PRESET_CANDIDATES,
    PRODUCTION_EQUIVALENT_CANDIDATES,
    SMART_DCA_RESEARCH_ARTIFACT_SCHEMA_VERSION,
    SUPPORTED_DCA_CADENCES,
    _candidate_definition_sha256,
    _candidate_production_equivalent_profile,
    _candidate_role,
    _candidate_signal_source_mode,
    candidate_signal_consumers,
)
from .smart_dca_research_simulation import (
    _average_cash_ratio,
    _average_scheduled_multiplier,
    _dominant_csv_value,
    _float_value,
    _max_cash_ratio,
    _max_scheduled_multiplier,
    _min_scheduled_multiplier,
    _performance_diagnoses,
    _scheduled_decision_events,
    _scheduled_multiplier_count,
    _scheduled_multiplier_ratio,
    _scheduled_regimes_seen,
    _skipped_buy_ratio,
    _terminal_cash_ratio,
    _worst_relative_value_gap_pct,
)


def evaluate_candidate_results(
    results: Mapping[str, DcaResearchResult],
    *,
    fixed_name: str = "fixed",
    max_terminal_underperformance_pct: float = -1.0,
    min_drawdown_improvement_pct_points: float = 2.0,
    max_drawdown_worse_pct_points: float = 1.0,
    min_terminal_edge_when_drawdown_worse_pct: float = 3.0,
    max_skipped_buy_ratio: float = 0.30,
) -> dict[str, DcaCandidateEvaluation]:
    """Apply the research promotion-gate rules to fixed-vs-candidate results.

    The thresholds mirror the research plan. They are deliberately coarse and
    named; this function evaluates already-run candidates and does not search
    for better parameters.
    """

    if fixed_name not in results:
        raise ValueError(f"results must include fixed_name={fixed_name!r}")
    fixed = results[fixed_name]
    evaluations: dict[str, DcaCandidateEvaluation] = {}

    for name, result in results.items():
        if name == fixed_name:
            continue
        relative_terminal = (
            float((result.terminal_value / fixed.terminal_value - 1.0) * 100.0)
            if fixed.terminal_value > 0.0
            else float(result.relative_terminal_value_pct)
        )
        drawdown_delta_pct_points = float((result.max_drawdown - fixed.max_drawdown) * 100.0)
        drawdown_improvement_pct_points = -drawdown_delta_pct_points
        skipped_ratio = _skipped_buy_ratio(result)
        deployment_delta_pct_points = float((result.deployment_rate - fixed.deployment_rate) * 100.0)
        reasons: list[str] = []

        if result.trade_count <= 0:
            reasons.append("no_candidate_trades")
        if (
            relative_terminal < max_terminal_underperformance_pct
            and drawdown_improvement_pct_points < min_drawdown_improvement_pct_points
        ):
            reasons.append("terminal_underperformance_without_drawdown_improvement")
        if (
            drawdown_delta_pct_points > max_drawdown_worse_pct_points
            and relative_terminal < min_terminal_edge_when_drawdown_worse_pct
        ):
            reasons.append("drawdown_worse_without_terminal_edge")
        if (
            skipped_ratio > max_skipped_buy_ratio
            and drawdown_improvement_pct_points < min_drawdown_improvement_pct_points
        ):
            reasons.append("skip_rate_too_high_without_drawdown_improvement")

        rank_score = float(
            relative_terminal
            + drawdown_improvement_pct_points * 0.50
            + deployment_delta_pct_points * 0.05
            - max(0.0, skipped_ratio - max_skipped_buy_ratio) * 10.0
        )
        evaluations[name] = DcaCandidateEvaluation(
            name=name,
            passed=not reasons,
            reasons=tuple(reasons),
            relative_terminal_value_pct=relative_terminal,
            max_drawdown_delta_pct_points=drawdown_delta_pct_points,
            skipped_buy_ratio=skipped_ratio,
            deployment_rate_delta_pct_points=deployment_delta_pct_points,
            rank_score=rank_score,
        )

    return evaluations


def summarize_candidate_evaluations(
    evaluations: Mapping[str, DcaCandidateEvaluation],
) -> tuple[DcaCandidateEvaluation, ...]:
    """Return candidate evaluations sorted for review reports."""

    return tuple(
        sorted(
            evaluations.values(),
            key=lambda item: (item.passed, item.rank_score, item.relative_terminal_value_pct),
            reverse=True,
        )
    )


def results_to_metrics_rows(
    results: Mapping[str, DcaResearchResult],
    *,
    fixed_name: str = "fixed",
    evaluations: Mapping[str, DcaCandidateEvaluation] | None = None,
) -> tuple[dict[str, object], ...]:
    """Convert result/evaluation objects into CSV-friendly metrics rows."""

    resolved_evaluations = (
        evaluate_candidate_results(results, fixed_name=fixed_name)
        if evaluations is None
        else evaluations
    )
    rows: list[dict[str, object]] = []
    fixed = results[fixed_name]
    for name, result in results.items():
        evaluation = resolved_evaluations.get(name)
        row: dict[str, object] = {
            "name": name,
            "is_fixed_benchmark": name == fixed_name,
            "terminal_value": result.terminal_value,
            "cash": result.cash,
            "shares": result.shares,
            "invested": result.invested,
            "contributions": result.contributions,
            "max_drawdown_pct": result.max_drawdown * 100.0,
            "max_underwater_days": result.max_underwater_days,
            "money_weighted_return_pct": result.money_weighted_return * 100.0,
            "average_cash_ratio_pct": _average_cash_ratio(result) * 100.0,
            "max_cash_ratio_pct": _max_cash_ratio(result) * 100.0,
            "terminal_cash_ratio_pct": _terminal_cash_ratio(result) * 100.0,
            "trade_count": result.trade_count,
            "skipped_count": result.skipped_count,
            "skipped_buy_ratio": _skipped_buy_ratio(result),
            "deployment_rate_pct": result.deployment_rate * 100.0,
            "scheduled_decision_count": len(_scheduled_decision_events(result)),
            "zero_multiplier_count": _scheduled_multiplier_count(
                result,
                lambda value: value <= 0.0,
            ),
            "zero_multiplier_ratio": _scheduled_multiplier_ratio(
                result,
                lambda value: value <= 0.0,
            ),
            "boosted_multiplier_count": _scheduled_multiplier_count(
                result,
                lambda value: value > 1.0,
            ),
            "boosted_multiplier_ratio": _scheduled_multiplier_ratio(
                result,
                lambda value: value > 1.0,
            ),
            "average_scheduled_multiplier": _average_scheduled_multiplier(result),
            "min_scheduled_multiplier": _min_scheduled_multiplier(result),
            "max_scheduled_multiplier": _max_scheduled_multiplier(result),
            "regime_count": len(_scheduled_regimes_seen(result)),
            "regimes_seen": ",".join(_scheduled_regimes_seen(result)),
            "worst_relative_value_gap_after_1y_pct": _worst_relative_value_gap_pct(
                result,
                fixed=fixed,
                min_elapsed_days=365,
            ),
            "worst_relative_value_gap_after_2y_pct": _worst_relative_value_gap_pct(
                result,
                fixed=fixed,
                min_elapsed_days=365 * 2,
            ),
            "worst_relative_value_gap_after_3y_pct": _worst_relative_value_gap_pct(
                result,
                fixed=fixed,
                min_elapsed_days=365 * 3,
            ),
            "relative_terminal_value_pct": (
                0.0
                if name == fixed_name
                else (
                    evaluation.relative_terminal_value_pct
                    if evaluation is not None
                    else result.relative_terminal_value_pct
                )
            ),
        }
        if evaluation is not None:
            row.update(
                {
                    "passed_promotion_gate": evaluation.passed,
                    "failure_reasons": ",".join(evaluation.reasons),
                    "max_drawdown_delta_pct_points": evaluation.max_drawdown_delta_pct_points,
                    "deployment_rate_delta_pct_points": evaluation.deployment_rate_delta_pct_points,
                    "rank_score": evaluation.rank_score,
                }
            )
        else:
            row.update(
                {
                    "passed_promotion_gate": None,
                    "failure_reasons": "",
                    "max_drawdown_delta_pct_points": 0.0,
                    "deployment_rate_delta_pct_points": 0.0,
                    "rank_score": 0.0,
                }
            )
        diagnoses = _performance_diagnoses(row)
        row["primary_performance_diagnosis"] = diagnoses[0] if diagnoses else ""
        row["performance_diagnoses"] = ",".join(diagnoses)
        rows.append(row)
    return tuple(rows)


def results_to_equity_curve_rows(
    results: Mapping[str, DcaResearchResult],
) -> tuple[dict[str, object], ...]:
    """Convert daily account value curves into CSV-friendly rows."""

    rows: list[dict[str, object]] = []
    for result in results.values():
        rows.extend(dict(row) for row in result.equity_curve)
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                str(row.get("date", "")),
                str(row.get("name", "")),
            ),
        )
    )


def results_to_cash_flow_rows(
    results: Mapping[str, DcaResearchResult],
) -> tuple[dict[str, object], ...]:
    """Convert contribution and terminal-value cash flows into CSV-friendly rows."""

    rows: list[dict[str, object]] = []
    for result in results.values():
        rows.extend(dict(row) for row in result.cash_flows)
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                str(row.get("date", "")),
                str(row.get("name", "")),
                str(row.get("cash_flow_type", "")),
            ),
        )
    )


def scenario_results_to_robustness_rows(
    scenarios: Mapping[str, Mapping[str, DcaResearchResult]],
    *,
    fixed_name: str = "fixed",
) -> tuple[dict[str, object], ...]:
    """Aggregate candidate performance across scenario perturbations."""

    candidate_rows: dict[str, list[dict[str, object]]] = {}
    for scenario_name, results in scenarios.items():
        evaluations = evaluate_candidate_results(results, fixed_name=fixed_name)
        for row in results_to_metrics_rows(results, fixed_name=fixed_name, evaluations=evaluations):
            if row["name"] == fixed_name:
                continue
            enriched = dict(row)
            enriched["scenario"] = scenario_name
            candidate_rows.setdefault(str(row["name"]), []).append(enriched)

    rows: list[dict[str, object]] = []
    for name, values in candidate_rows.items():
        passed_values = [bool(row["passed_promotion_gate"]) for row in values]
        scenario_count = len(values)
        passed_count = sum(1 for value in passed_values if value)
        failure_reasons = sorted(
            {
                reason
                for row in values
                for reason in str(row.get("failure_reasons", "")).split(",")
                if reason
            }
        )
        robustness_gate_passed = scenario_count > 0 and passed_count == scenario_count
        rows.append(
            {
                "name": name,
                "scenario_count": scenario_count,
                "passed_count": passed_count,
                "pass_rate": float(passed_count / scenario_count) if scenario_count else 0.0,
                "robustness_gate_passed": robustness_gate_passed,
                "review_status": _robustness_review_status(
                    passed_count=passed_count,
                    scenario_count=scenario_count,
                ),
                "passed_all_scenarios": robustness_gate_passed,
                "failed_scenarios": scenario_count - passed_count,
                "failure_reasons": ",".join(failure_reasons),
                "dominant_performance_diagnosis": _dominant_csv_value(
                    values,
                    "performance_diagnoses",
                ),
                "performance_diagnoses": ",".join(
                    _union_csv_values(values, "performance_diagnoses")
                ),
                "weakest_scenario": _scenario_for_min_metric(values, "rank_score"),
                "worst_terminal_scenario": _scenario_for_min_metric(
                    values,
                    "relative_terminal_value_pct",
                ),
                "worst_drawdown_scenario": _scenario_for_max_metric(
                    values,
                    "max_drawdown_delta_pct_points",
                ),
                "min_relative_terminal_value_pct": _min_metric(
                    values,
                    "relative_terminal_value_pct",
                ),
                "median_relative_terminal_value_pct": _median_metric(
                    values,
                    "relative_terminal_value_pct",
                ),
                "min_money_weighted_return_pct": _min_metric(
                    values,
                    "money_weighted_return_pct",
                    missing=float("nan"),
                ),
                "median_money_weighted_return_pct": _median_metric(
                    values,
                    "money_weighted_return_pct",
                    missing=float("nan"),
                ),
                "max_average_cash_ratio_pct": _max_metric(
                    values,
                    "average_cash_ratio_pct",
                ),
                "max_cash_ratio_pct": _max_metric(values, "max_cash_ratio_pct"),
                "max_terminal_cash_ratio_pct": _max_metric(
                    values,
                    "terminal_cash_ratio_pct",
                ),
                "worst_max_drawdown_delta_pct_points": _max_metric(
                    values,
                    "max_drawdown_delta_pct_points",
                ),
                "max_skipped_buy_ratio": _max_metric(values, "skipped_buy_ratio"),
                "max_zero_multiplier_ratio": _max_metric(
                    values,
                    "zero_multiplier_ratio",
                ),
                "max_boosted_multiplier_ratio": _max_metric(
                    values,
                    "boosted_multiplier_ratio",
                ),
                "min_average_scheduled_multiplier": _min_metric(
                    values,
                    "average_scheduled_multiplier",
                ),
                "max_average_scheduled_multiplier": _max_metric(
                    values,
                    "average_scheduled_multiplier",
                ),
                "min_scheduled_multiplier": _min_metric(
                    values,
                    "min_scheduled_multiplier",
                ),
                "max_scheduled_multiplier": _max_metric(
                    values,
                    "max_scheduled_multiplier",
                ),
                "regime_count": len(_union_csv_values(values, "regimes_seen")),
                "regimes_seen": ",".join(_union_csv_values(values, "regimes_seen")),
                "min_deployment_rate_delta_pct_points": _min_metric(
                    values,
                    "deployment_rate_delta_pct_points",
                ),
                "min_rank_score": _min_metric(values, "rank_score"),
                "worst_relative_value_gap_after_1y_pct": _min_metric(
                    values,
                    "worst_relative_value_gap_after_1y_pct",
                ),
                "worst_relative_value_gap_after_2y_pct": _min_metric(
                    values,
                    "worst_relative_value_gap_after_2y_pct",
                ),
                "worst_relative_value_gap_after_3y_pct": _min_metric(
                    values,
                    "worst_relative_value_gap_after_3y_pct",
                ),
            }
        )

    sorted_rows = sorted(
        rows,
        key=lambda row: (
            bool(row["robustness_gate_passed"]),
            float(row["pass_rate"]),
            float(row["min_relative_terminal_value_pct"]),
            float(row["min_rank_score"]),
        ),
        reverse=True,
    )
    return tuple(
        {
            "review_rank": rank,
            **row,
        }
        for rank, row in enumerate(sorted_rows, start=1)
    )


def scenario_results_to_selection_rows(
    scenarios: Mapping[str, Mapping[str, DcaResearchResult]],
    *,
    fixed_name: str = "fixed",
    min_review_scenarios: int = 3,
    min_effect_worst_relative_terminal_value_pct: float = 0.0,
    min_effect_median_relative_terminal_value_pct: float = 1.0,
    min_effect_worst_rank_score: float = 0.0,
    max_effect_terminal_cash_ratio_pct: float = 35.0,
) -> tuple[dict[str, object], ...]:
    """Select the strongest fixed candidate per family without parameter search.

    Selection is intentionally conservative: a candidate that does not pass the
    robustness gate can be named as the best observed variant, but it is not
    recommended for promotion.
    """

    robustness_rows = scenario_results_to_robustness_rows(
        scenarios,
        fixed_name=fixed_name,
    )
    if min_review_scenarios < 1:
        raise ValueError("min_review_scenarios must be at least 1")
    coverage_row = scenario_results_to_coverage_rows(
        scenarios,
        fixed_name=fixed_name,
        min_review_scenarios=min_review_scenarios,
    )[0]
    matrix_coverage_gate_passed = bool(coverage_row["coverage_gate_passed"])
    groups: dict[str, list[Mapping[str, object]]] = {}
    for row in robustness_rows:
        name = str(row["name"])
        groups.setdefault(_selection_group_for_candidate(name), []).append(row)

    selection_rows: list[dict[str, object]] = []
    for group_name, rows in sorted(groups.items()):
        ordered = sorted(
            rows,
            key=lambda row: (
                bool(row["robustness_gate_passed"]),
                float(row["pass_rate"]),
                float(row["min_relative_terminal_value_pct"]),
                float(row["min_rank_score"]),
            ),
            reverse=True,
        )
        selected = ordered[0]
        ordered_candidate_names = tuple(str(row["name"]) for row in ordered)
        ordered_candidate_definition_sha256s = _candidate_definition_sha256s(
            ordered_candidate_names
        )
        selected_passed = bool(selected["robustness_gate_passed"])
        selected_scenario_count = int(selected["scenario_count"])
        scenario_gate_passed = selected_scenario_count >= min_review_scenarios
        effect_size_failure_reasons = _selection_effect_size_failure_reasons(
            selected,
            min_worst_relative_terminal_value_pct=(
                min_effect_worst_relative_terminal_value_pct
            ),
            min_median_relative_terminal_value_pct=(
                min_effect_median_relative_terminal_value_pct
            ),
            min_worst_rank_score=min_effect_worst_rank_score,
            max_terminal_cash_ratio_pct=max_effect_terminal_cash_ratio_pct,
        )
        effect_size_gate_passed = not effect_size_failure_reasons
        promotion_ready = (
            selected_passed
            and scenario_gate_passed
            and matrix_coverage_gate_passed
            and effect_size_gate_passed
        )
        selection_rows.append(
            {
                "selection_group": group_name,
                "selected_name": selected["name"],
                "selected_family": _candidate_family(str(selected["name"])),
                "selected_rule_type": _candidate_rule_type(str(selected["name"])),
                "selected_parameter_count": _candidate_parameter_count(str(selected["name"])),
                "selected_candidate_definition_sha256": _candidate_definition_sha256(
                    str(selected["name"])
                ),
                "selected_candidate_role": "best_observed_smart_candidate",
                "selection_policy": "fixed_preset_no_parameter_search",
                "recommendation_status": (
                    "promote_to_manual_review"
                    if promotion_ready
                    else "hold_default_fixed_dca"
                ),
                "runtime_default_recommendation": "fixed_dca",
                "runtime_default_change_policy": "manual_review_required_no_auto_enable",
                "smart_mode_enablement_status": (
                    "manual_review_candidate"
                    if promotion_ready
                    else "not_recommended_for_enablement"
                ),
                "recommendation_reason": _selection_recommendation_reason(
                    selected_passed=selected_passed,
                    scenario_gate_passed=scenario_gate_passed,
                    matrix_coverage_gate_passed=matrix_coverage_gate_passed,
                    effect_size_gate_passed=effect_size_gate_passed,
                ),
                "selected_review_rank": selected["review_rank"],
                "selected_review_status": selected["review_status"],
                "selected_robustness_gate_passed": selected_passed,
                "selected_scenario_count": selected_scenario_count,
                "min_review_scenarios": min_review_scenarios,
                "review_scenario_gate_passed": scenario_gate_passed,
                "effect_size_policy": "fixed_minimum_effect_no_parameter_search",
                "selected_effect_size_gate_passed": effect_size_gate_passed,
                "selected_effect_size_failure_reasons": ",".join(
                    effect_size_failure_reasons
                ),
                "min_effect_worst_relative_terminal_value_pct": (
                    min_effect_worst_relative_terminal_value_pct
                ),
                "min_effect_median_relative_terminal_value_pct": (
                    min_effect_median_relative_terminal_value_pct
                ),
                "min_effect_worst_rank_score": min_effect_worst_rank_score,
                "max_effect_terminal_cash_ratio_pct": (
                    max_effect_terminal_cash_ratio_pct
                ),
                "matrix_coverage_gate_passed": matrix_coverage_gate_passed,
                "matrix_coverage_status": coverage_row["coverage_status"],
                "matrix_coverage_failure_reasons": coverage_row["failure_reasons"],
                "matrix_scenario_count": coverage_row["scenario_count"],
                "matrix_scenario_sample_window_labels": coverage_row[
                    "scenario_sample_window_labels"
                ],
                "matrix_scenario_sample_window_label_count": coverage_row[
                    "scenario_sample_window_label_count"
                ],
                "matrix_scenario_cadences": coverage_row["scenario_cadences"],
                "matrix_scenario_cadence_count": coverage_row[
                    "scenario_cadence_count"
                ],
                "matrix_scenario_execution_days": coverage_row[
                    "scenario_execution_days"
                ],
                "matrix_scenario_execution_day_count": coverage_row[
                    "scenario_execution_day_count"
                ],
                "matrix_scenario_contribution_amounts_usd": coverage_row[
                    "scenario_contribution_amounts_usd"
                ],
                "matrix_scenario_contribution_amount_count": coverage_row[
                    "scenario_contribution_amount_count"
                ],
                "matrix_scenario_start_dates": coverage_row["scenario_start_dates"],
                "matrix_scenario_start_date_count": coverage_row[
                    "scenario_start_date_count"
                ],
                "matrix_scenario_sample_windows": coverage_row[
                    "scenario_sample_windows"
                ],
                "matrix_scenario_sample_window_count": coverage_row[
                    "scenario_sample_window_count"
                ],
                "matrix_scenario_sample_first_dates": coverage_row[
                    "scenario_sample_first_dates"
                ],
                "matrix_scenario_sample_first_date_count": coverage_row[
                    "scenario_sample_first_date_count"
                ],
                "matrix_scenario_sample_last_dates": coverage_row[
                    "scenario_sample_last_dates"
                ],
                "matrix_scenario_sample_last_date_count": coverage_row[
                    "scenario_sample_last_date_count"
                ],
                "matrix_scenario_sample_window_audit_passed": coverage_row[
                    "scenario_sample_window_audit_passed"
                ],
                "matrix_scenario_recognized_dimension_count": coverage_row[
                    "scenario_recognized_dimension_count"
                ],
                "matrix_scenario_varied_dimensions": coverage_row[
                    "scenario_varied_dimensions"
                ],
                "matrix_scenario_varied_dimension_count": coverage_row[
                    "scenario_varied_dimension_count"
                ],
                "matrix_scenario_dimension_coverage_gate_passed": coverage_row[
                    "scenario_dimension_coverage_gate_passed"
                ],
                "matrix_candidate_count": coverage_row["candidate_count"],
                "matrix_candidate_set_consistent": coverage_row["candidate_set_consistent"],
                "matrix_fixed_benchmark_present_all": coverage_row[
                    "fixed_benchmark_present_all"
                ],
                "matrix_candidate_names": coverage_row["candidate_names"],
                "matrix_candidate_universe_policy": coverage_row[
                    "candidate_universe_policy"
                ],
                "matrix_candidate_definition_sha256s": coverage_row[
                    "candidate_definition_sha256s"
                ],
                "matrix_candidate_definition_hash_count": coverage_row[
                    "candidate_definition_hash_count"
                ],
                "selected_pass_rate": selected["pass_rate"],
                "selected_min_relative_terminal_value_pct": selected[
                    "min_relative_terminal_value_pct"
                ],
                "selected_median_relative_terminal_value_pct": selected[
                    "median_relative_terminal_value_pct"
                ],
                "selected_min_rank_score": selected["min_rank_score"],
                "selected_max_zero_multiplier_ratio": selected[
                    "max_zero_multiplier_ratio"
                ],
                "selected_max_boosted_multiplier_ratio": selected[
                    "max_boosted_multiplier_ratio"
                ],
                "selected_min_average_scheduled_multiplier": selected[
                    "min_average_scheduled_multiplier"
                ],
                "selected_max_average_scheduled_multiplier": selected[
                    "max_average_scheduled_multiplier"
                ],
                "selected_min_scheduled_multiplier": selected[
                    "min_scheduled_multiplier"
                ],
                "selected_max_scheduled_multiplier": selected[
                    "max_scheduled_multiplier"
                ],
                "selected_regime_count": selected["regime_count"],
                "selected_regimes_seen": selected["regimes_seen"],
                "selected_failure_reasons": selected["failure_reasons"],
                "selected_dominant_performance_diagnosis": selected[
                    "dominant_performance_diagnosis"
                ],
                "selected_performance_diagnoses": selected[
                    "performance_diagnoses"
                ],
                "candidate_count": len(ordered_candidate_names),
                "compared_candidates": ",".join(ordered_candidate_names),
                "compared_candidate_definition_sha256s": ",".join(
                    ordered_candidate_definition_sha256s
                ),
                "fixed_benchmark": fixed_name,
            }
        )
    return tuple(selection_rows)


def scenario_results_to_review_decision(
    scenarios: Mapping[str, Mapping[str, DcaResearchResult]],
    *,
    fixed_name: str = "fixed",
    min_review_scenarios: int = 3,
    min_effect_worst_relative_terminal_value_pct: float = 0.0,
    min_effect_median_relative_terminal_value_pct: float = 1.0,
    min_effect_worst_rank_score: float = 0.0,
    max_effect_terminal_cash_ratio_pct: float = 35.0,
) -> dict[str, object]:
    """Return a single JSON-safe decision summary for scenario review gates."""

    coverage_row = scenario_results_to_coverage_rows(
        scenarios,
        fixed_name=fixed_name,
        min_review_scenarios=min_review_scenarios,
    )[0]
    selection_rows = scenario_results_to_selection_rows(
        scenarios,
        fixed_name=fixed_name,
        min_review_scenarios=min_review_scenarios,
        min_effect_worst_relative_terminal_value_pct=(
            min_effect_worst_relative_terminal_value_pct
        ),
        min_effect_median_relative_terminal_value_pct=(
            min_effect_median_relative_terminal_value_pct
        ),
        min_effect_worst_rank_score=min_effect_worst_rank_score,
        max_effect_terminal_cash_ratio_pct=max_effect_terminal_cash_ratio_pct,
    )
    blocking_reasons = _review_decision_blocking_reasons(
        coverage_row=coverage_row,
        selection_rows=selection_rows,
    )
    manual_review_ready = not blocking_reasons
    manual_review_candidate_names = tuple(
        str(row["selected_name"])
        for row in selection_rows
        if row["recommendation_status"] == "promote_to_manual_review"
    )
    candidate_universe_names = _csv_values_tuple(coverage_row["candidate_names"])
    candidate_universe_definition_sha256s = _csv_values_tuple(
        coverage_row["candidate_definition_sha256s"]
    )
    effect_size_thresholds = {
        "min_worst_relative_terminal_value_pct": (
            min_effect_worst_relative_terminal_value_pct
        ),
        "min_median_relative_terminal_value_pct": (
            min_effect_median_relative_terminal_value_pct
        ),
        "min_worst_rank_score": min_effect_worst_rank_score,
        "max_terminal_cash_ratio_pct": max_effect_terminal_cash_ratio_pct,
    }
    return {
        "schema_version": SMART_DCA_RESEARCH_ARTIFACT_SCHEMA_VERSION,
        "artifact_type": "smart_dca_review_decision",
        "fixed_name": fixed_name,
        "min_review_scenarios": min_review_scenarios,
        "selection_policy": "fixed_preset_no_parameter_search",
        "effect_size_policy": "fixed_minimum_effect_no_parameter_search",
        "effect_size_thresholds": effect_size_thresholds,
        "candidate_universe_policy": coverage_row["candidate_universe_policy"],
        "candidate_universe_count": coverage_row["candidate_count"],
        "candidate_universe_names": candidate_universe_names,
        "candidate_universe_definition_sha256s": candidate_universe_definition_sha256s,
        "runtime_default_recommendation": "fixed_dca",
        "runtime_default_change_policy": "manual_review_required_no_auto_enable",
        "smart_mode_enablement_status": _smart_mode_enablement_status(
            selection_rows,
            manual_review_ready=manual_review_ready,
        ),
        "observed_best_smart_candidates": tuple(
            {
                "selection_group": str(row["selection_group"]),
                "name": str(row["selected_name"]),
                "status": str(row["recommendation_status"]),
                "reason": str(row["recommendation_reason"]),
                "candidate_definition_sha256": str(
                    row["selected_candidate_definition_sha256"]
                ),
                "candidate_role": str(row["selected_candidate_role"]),
                "review_rank": int(row["selected_review_rank"]),
                "robustness_gate_passed": bool(
                    row["selected_robustness_gate_passed"]
                ),
                "effect_size_gate_passed": bool(
                    row["selected_effect_size_gate_passed"]
                ),
                "pass_rate": float(row["selected_pass_rate"]),
                "min_relative_terminal_value_pct": float(
                    row["selected_min_relative_terminal_value_pct"]
                ),
                "median_relative_terminal_value_pct": float(
                    row["selected_median_relative_terminal_value_pct"]
                ),
                "min_rank_score": float(row["selected_min_rank_score"]),
                "dominant_performance_diagnosis": str(
                    row["selected_dominant_performance_diagnosis"]
                ),
                "performance_diagnoses": _csv_values_tuple(
                    row["selected_performance_diagnoses"]
                ),
                "compared_candidates": _csv_values_tuple(row["compared_candidates"]),
                "compared_candidate_definition_sha256s": _csv_values_tuple(
                    row["compared_candidate_definition_sha256s"]
                ),
            }
            for row in selection_rows
        ),
        "production_profile_decisions": _production_profile_decision_rows(
            selection_rows,
            candidate_universe_names=candidate_universe_names,
        ),
        "manual_review_candidate_names": manual_review_candidate_names,
        "selection_gate_summary": {
            "matrix_coverage_gate_passed": coverage_row["coverage_gate_passed"],
            "matrix_dimension_coverage_gate_passed": coverage_row[
                "scenario_dimension_coverage_gate_passed"
            ],
            "all_selection_effect_size_gate_passed": bool(selection_rows) and all(
                bool(row["selected_effect_size_gate_passed"])
                for row in selection_rows
            ),
            "all_selection_robustness_gate_passed": bool(selection_rows) and all(
                bool(row["selected_robustness_gate_passed"])
                for row in selection_rows
            ),
            "all_selection_review_scenario_gate_passed": bool(selection_rows) and all(
                bool(row["review_scenario_gate_passed"])
                for row in selection_rows
            ),
        },
        "manual_review_gate_passed": manual_review_ready,
        "overall_recommendation_status": (
            "promote_to_manual_review"
            if manual_review_ready
            else "hold_default_fixed_dca"
        ),
        "overall_recommendation_reason": (
            "all_selection_groups_ready_for_manual_review"
            if manual_review_ready
            else ",".join(blocking_reasons)
        ),
        "blocking_reasons": blocking_reasons,
        "matrix_coverage_gate_passed": coverage_row["coverage_gate_passed"],
        "matrix_coverage": coverage_row,
        "selection_count": len(selection_rows),
        "selection_groups": tuple(
            str(row["selection_group"])
            for row in selection_rows
        ),
        "selections": selection_rows,
    }


def _smart_mode_enablement_status(
    selection_rows: tuple[dict[str, object], ...],
    *,
    manual_review_ready: bool,
) -> str:
    if manual_review_ready:
        return "manual_review_candidate"
    if any(
        row["recommendation_status"] == "promote_to_manual_review"
        for row in selection_rows
    ):
        return "partial_manual_review_candidates"
    return "not_recommended_for_enablement"


def _production_profile_decision_rows(
    selection_rows: tuple[dict[str, object], ...],
    *,
    candidate_universe_names: tuple[str, ...],
) -> tuple[dict[str, object], ...]:
    selection_by_group = {
        str(row["selection_group"]): row
        for row in selection_rows
    }
    candidate_universe = set(candidate_universe_names)
    rows: list[dict[str, object]] = []
    for profile, production_candidate in sorted(PRODUCTION_EQUIVALENT_CANDIDATES.items()):
        selection_group = _selection_group_for_candidate(production_candidate)
        row = selection_by_group.get(selection_group)
        if row is None:
            rows.append(
                {
                    "profile": profile,
                    "production_equivalent_candidate": production_candidate,
                    "production_equivalent_candidate_definition_sha256": (
                        _candidate_definition_sha256(production_candidate)
                    ),
                    "production_equivalent_in_candidate_universe": (
                        production_candidate in candidate_universe
                    ),
                    "selection_group": selection_group,
                    "observed_best_candidate": "",
                    "observed_best_candidate_definition_sha256": "",
                    "observed_best_status": "not_evaluated",
                    "observed_best_reason": "profile_not_in_candidate_universe",
                    "observed_best_dominant_performance_diagnosis": "",
                    "observed_best_performance_diagnoses": "",
                    "runtime_default_recommendation": "fixed_dca",
                    "runtime_default_change_policy": (
                        "manual_review_required_no_auto_enable"
                    ),
                    "smart_mode_enablement_status": "not_evaluated",
                    "manual_review_required_before_default_change": True,
                    "default_change_allowed_by_research": False,
                }
            )
            continue

        rows.append(
            {
                "profile": profile,
                "production_equivalent_candidate": production_candidate,
                "production_equivalent_candidate_definition_sha256": (
                    _candidate_definition_sha256(production_candidate)
                ),
                "production_equivalent_in_candidate_universe": (
                    production_candidate in candidate_universe
                ),
                "selection_group": selection_group,
                "observed_best_candidate": str(row["selected_name"]),
                "observed_best_candidate_definition_sha256": str(
                    row["selected_candidate_definition_sha256"]
                ),
                "observed_best_status": str(row["recommendation_status"]),
                "observed_best_reason": str(row["recommendation_reason"]),
                "observed_best_dominant_performance_diagnosis": str(
                    row["selected_dominant_performance_diagnosis"]
                ),
                "observed_best_performance_diagnoses": str(
                    row["selected_performance_diagnoses"]
                ),
                "runtime_default_recommendation": "fixed_dca",
                "runtime_default_change_policy": (
                    "manual_review_required_no_auto_enable"
                ),
                "smart_mode_enablement_status": str(
                    row["smart_mode_enablement_status"]
                ),
                "manual_review_required_before_default_change": True,
                "default_change_allowed_by_research": False,
            }
        )
    return tuple(rows)


def _review_decision_blocking_reasons(
    *,
    coverage_row: Mapping[str, object],
    selection_rows: tuple[dict[str, object], ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if not bool(coverage_row["coverage_gate_passed"]):
        coverage_reasons = tuple(
            item
            for item in str(coverage_row["failure_reasons"]).split(",")
            if item
        )
        if coverage_reasons:
            reasons.extend(coverage_reasons)
        else:
            reasons.append("insufficient_scenario_matrix_coverage")
    if not selection_rows:
        reasons.append("no_candidate_selection_rows")
    for row in selection_rows:
        if row["recommendation_status"] != "promote_to_manual_review":
            reason = str(row["recommendation_reason"])
            if reason:
                reasons.append(reason)
    return tuple(dict.fromkeys(reasons))


def _selection_effect_size_failure_reasons(
    selected: Mapping[str, object],
    *,
    min_worst_relative_terminal_value_pct: float,
    min_median_relative_terminal_value_pct: float,
    min_worst_rank_score: float,
    max_terminal_cash_ratio_pct: float,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if (
        float(selected["min_relative_terminal_value_pct"])
        < min_worst_relative_terminal_value_pct
    ):
        reasons.append("worst_terminal_edge_below_min_effect_size")
    if (
        float(selected["median_relative_terminal_value_pct"])
        < min_median_relative_terminal_value_pct
    ):
        reasons.append("median_terminal_edge_below_min_effect_size")
    if float(selected["min_rank_score"]) < min_worst_rank_score:
        reasons.append("worst_rank_score_below_min_effect_size")
    if float(selected["max_terminal_cash_ratio_pct"]) > max_terminal_cash_ratio_pct:
        reasons.append("terminal_cash_ratio_above_max_effect_size")
    return tuple(reasons)


def scenario_results_to_coverage_rows(
    scenarios: Mapping[str, Mapping[str, DcaResearchResult]],
    *,
    fixed_name: str = "fixed",
    min_review_scenarios: int = 3,
) -> tuple[dict[str, object], ...]:
    """Summarize scenario matrix coverage before reviewing selected candidates."""

    if min_review_scenarios < 1:
        raise ValueError("min_review_scenarios must be at least 1")

    scenario_names = tuple(str(name) for name in scenarios)
    dimension_summary = _scenario_dimension_summary(scenario_names)
    sample_window_summary = _scenario_sample_window_summary(
        scenarios,
        fixed_name=fixed_name,
    )
    candidate_sets = tuple(
        tuple(sorted(str(name) for name in results if name != fixed_name))
        for results in scenarios.values()
    )
    unique_candidate_sets = sorted(set(candidate_sets))
    fixed_present_all = all(fixed_name in results for results in scenarios.values())
    candidate_set_consistent = len(unique_candidate_sets) <= 1
    scenario_count = len(scenario_names)
    review_scenario_gate_passed = scenario_count >= min_review_scenarios
    candidate_names = unique_candidate_sets[0] if unique_candidate_sets else ()
    candidate_definition_sha256s = _candidate_definition_sha256s(candidate_names)
    dimension_coverage_gate_passed = bool(
        dimension_summary["scenario_dimension_coverage_gate_passed"]
    )
    coverage_gate_passed = (
        scenario_count > 0
        and review_scenario_gate_passed
        and fixed_present_all
        and candidate_set_consistent
        and dimension_coverage_gate_passed
    )
    failure_reasons = _scenario_coverage_failure_reasons(
        scenario_count=scenario_count,
        review_scenario_gate_passed=review_scenario_gate_passed,
        fixed_present_all=fixed_present_all,
        candidate_set_consistent=candidate_set_consistent,
        dimension_coverage_gate_passed=dimension_coverage_gate_passed,
    )
    return (
        {
            "scenario_count": scenario_count,
            "min_review_scenarios": min_review_scenarios,
            "review_scenario_gate_passed": review_scenario_gate_passed,
            "fixed_benchmark": fixed_name,
            "fixed_benchmark_present_all": fixed_present_all,
            "candidate_set_consistent": candidate_set_consistent,
            "candidate_count": len(candidate_names),
            "candidate_names": ",".join(candidate_names),
            "candidate_universe_policy": "frozen_preset_names_no_parameter_search",
            "candidate_definition_sha256s": ",".join(candidate_definition_sha256s),
            "candidate_definition_hash_count": len(candidate_definition_sha256s),
            "scenario_names": ",".join(scenario_names),
            **dimension_summary,
            **sample_window_summary,
            "coverage_gate_passed": coverage_gate_passed,
            "coverage_status": (
                "ready_for_selection_review"
                if coverage_gate_passed
                else "insufficient_coverage"
            ),
            "failure_reasons": ",".join(failure_reasons),
        },
)


def _scenario_sample_window_summary(
    scenarios: Mapping[str, Mapping[str, DcaResearchResult]],
    *,
    fixed_name: str,
) -> dict[str, object]:
    windows: list[str] = []
    first_dates: list[str] = []
    last_dates: list[str] = []
    missing_window_scenarios: list[str] = []
    for scenario_name, results in scenarios.items():
        fixed = results.get(fixed_name)
        if fixed is None or not fixed.equity_curve:
            missing_window_scenarios.append(str(scenario_name))
            continue
        first_date = str(fixed.equity_curve[0].get("date", ""))
        last_date = str(fixed.equity_curve[-1].get("date", ""))
        if not first_date or not last_date:
            missing_window_scenarios.append(str(scenario_name))
            continue
        first_dates.append(first_date)
        last_dates.append(last_date)
        windows.append(f"{scenario_name}:{first_date}..{last_date}")

    unique_windows = _sorted_unique_text(
        window.split(":", 1)[1]
        for window in windows
        if ":" in window
    )
    return {
        "scenario_sample_windows": ",".join(windows),
        "scenario_sample_window_count": len(unique_windows),
        "scenario_sample_first_dates": ",".join(_sorted_unique_text(first_dates)),
        "scenario_sample_first_date_count": len(set(first_dates)),
        "scenario_sample_last_dates": ",".join(_sorted_unique_text(last_dates)),
        "scenario_sample_last_date_count": len(set(last_dates)),
        "scenario_sample_window_missing_scenarios": ",".join(
            _sorted_unique_text(missing_window_scenarios)
        ),
        "scenario_sample_window_audit_passed": not missing_window_scenarios,
    }


def _scenario_dimension_summary(
    scenario_names: Iterable[str],
) -> dict[str, object]:
    sample_window_labels: list[str] = []
    cadences: list[str] = []
    execution_days: list[str] = []
    contribution_amounts: list[str] = []
    start_dates: list[str] = []
    for scenario_name in scenario_names:
        dimensions = _parse_scenario_dimensions(str(scenario_name))
        if dimensions["sample_window"]:
            sample_window_labels.append(dimensions["sample_window"])
        if dimensions["cadence"]:
            cadences.append(dimensions["cadence"])
        if dimensions["execution_day"]:
            execution_days.append(dimensions["execution_day"])
        if dimensions["contribution_amount_usd"]:
            contribution_amounts.append(dimensions["contribution_amount_usd"])
        if dimensions["start_date"]:
            start_dates.append(dimensions["start_date"])

    sample_window_values = _sorted_unique_text(sample_window_labels)
    cadence_values = _sorted_unique_text(cadences)
    execution_day_values = _sorted_unique_numeric_text(execution_days)
    contribution_values = _sorted_unique_numeric_text(contribution_amounts)
    start_date_values = _sorted_unique_text(start_dates)
    varied_dimensions = tuple(
        name
        for name, values in (
            ("sample_window", sample_window_values),
            ("cadence", cadence_values),
            ("execution_day", execution_day_values),
            ("contribution_amount", contribution_values),
            ("start_date", start_date_values),
        )
        if len(values) > 1
    )
    recognized_dimension_count = sum(
        1
        for values in (
            sample_window_values,
            cadence_values,
            execution_day_values,
            contribution_values,
            start_date_values,
        )
        if values
    )
    return {
        "scenario_sample_window_labels": ",".join(sample_window_values),
        "scenario_sample_window_label_count": len(sample_window_values),
        "scenario_cadences": ",".join(cadence_values),
        "scenario_cadence_count": len(cadence_values),
        "scenario_execution_days": ",".join(execution_day_values),
        "scenario_execution_day_count": len(execution_day_values),
        "scenario_contribution_amounts_usd": ",".join(contribution_values),
        "scenario_contribution_amount_count": len(contribution_values),
        "scenario_start_dates": ",".join(start_date_values),
        "scenario_start_date_count": len(start_date_values),
        "scenario_recognized_dimension_count": recognized_dimension_count,
        "scenario_varied_dimensions": ",".join(varied_dimensions),
        "scenario_varied_dimension_count": len(varied_dimensions),
        "scenario_dimension_coverage_gate_passed": bool(varied_dimensions),
    }


def _parse_scenario_dimensions(scenario_name: str) -> dict[str, str]:
    sample_window, unscoped_name = _split_scenario_sample_window_label(scenario_name)
    base_name, start_date = _split_scenario_start_label(unscoped_name)
    cadence = ""
    execution_day = ""
    contribution_amount = ""
    for candidate_cadence in sorted(SUPPORTED_DCA_CADENCES):
        if candidate_cadence == "weekly":
            prefix = "weekly_contribution_usd_"
            if base_name.startswith(prefix):
                cadence = "weekly"
                contribution_amount = _scenario_amount_display(
                    base_name[len(prefix) :]
                )
                break
            continue

        prefix = f"{candidate_cadence}_day_"
        if base_name.startswith(prefix):
            cadence = candidate_cadence
            remainder = base_name[len(prefix) :]
            contribution_marker = "_contribution_usd_"
            if contribution_marker in remainder:
                execution_day, amount_label = remainder.split(contribution_marker, 1)
                contribution_amount = _scenario_amount_display(amount_label)
            else:
                execution_day = remainder
            break
    return {
        "sample_window": sample_window,
        "cadence": cadence,
        "execution_day": execution_day,
        "contribution_amount_usd": contribution_amount,
        "start_date": start_date,
    }


def _split_scenario_sample_window_label(scenario_name: str) -> tuple[str, str]:
    prefix = "sample_window_"
    separator = "__"
    if not scenario_name.startswith(prefix) or separator not in scenario_name:
        return "", scenario_name
    label, rest = scenario_name[len(prefix) :].split(separator, 1)
    return label, rest


def _split_scenario_start_label(scenario_name: str) -> tuple[str, str]:
    marker = "_start_"
    if marker not in scenario_name:
        return scenario_name, ""
    base_name, start_label = scenario_name.rsplit(marker, 1)
    return base_name, start_label.replace("_", "-")


def _scenario_amount_display(amount_label: str) -> str:
    return amount_label.replace("_", ".")


def _sorted_unique_text(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


def _sorted_unique_numeric_text(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            set(values),
            key=lambda value: (
                float(value) if _is_float_text(value) else math.inf,
                value,
            ),
        )
    )


def _is_float_text(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _scenario_coverage_failure_reasons(
    *,
    scenario_count: int,
    review_scenario_gate_passed: bool,
    fixed_present_all: bool,
    candidate_set_consistent: bool,
    dimension_coverage_gate_passed: bool,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if scenario_count <= 0:
        reasons.append("no_scenarios")
    if not review_scenario_gate_passed:
        reasons.append("scenario_count_below_min_review_scenarios")
    if not fixed_present_all:
        reasons.append("missing_fixed_benchmark")
    if not candidate_set_consistent:
        reasons.append("candidate_set_inconsistent")
    if not dimension_coverage_gate_passed:
        reasons.append("scenario_dimension_coverage_missing")
    return tuple(reasons)


def _selection_recommendation_reason(
    *,
    selected_passed: bool,
    scenario_gate_passed: bool,
    matrix_coverage_gate_passed: bool,
    effect_size_gate_passed: bool,
) -> str:
    if (
        selected_passed
        and scenario_gate_passed
        and matrix_coverage_gate_passed
        and effect_size_gate_passed
    ):
        return "selected_candidate_passed_all_scenarios"
    if selected_passed:
        if not scenario_gate_passed:
            return "insufficient_robustness_scenarios"
        if not matrix_coverage_gate_passed:
            return "insufficient_scenario_matrix_coverage"
        return "insufficient_effect_size_vs_fixed_dca"
    return "no_candidate_passed_robustness_gate"


def _selection_group_for_candidate(name: str) -> str:
    candidate = PRESET_CANDIDATES.get(name)
    if candidate is None:
        return str(name)
    family = candidate.family
    for suffix in ("_variant",):
        if family.endswith(suffix):
            return family[: -len(suffix)]
    return family


def _candidate_family(name: str) -> str:
    candidate = PRESET_CANDIDATES.get(name)
    return "" if candidate is None else candidate.family


def _candidate_rule_type(name: str) -> str:
    candidate = PRESET_CANDIDATES.get(name)
    return "" if candidate is None else candidate.rule_type


def _candidate_parameter_count(name: str) -> int:
    candidate = PRESET_CANDIDATES.get(name)
    return 0 if candidate is None else len(candidate.parameters)


def _candidate_definition_sha256s(names: Iterable[str]) -> tuple[str, ...]:
    return tuple(_candidate_definition_sha256(name) for name in names)


def _csv_values_tuple(value: object) -> tuple[str, ...]:
    return tuple(item.strip() for item in str(value).split(",") if item.strip())


def _robustness_review_status(*, passed_count: int, scenario_count: int) -> str:
    if scenario_count <= 0:
        return "no_scenarios"
    if passed_count == scenario_count:
        return "candidate_passed_robustness_gate"
    if passed_count > 0:
        return "mixed_scenario_results"
    return "failed_robustness_gate"


def _scenario_for_min_metric(rows: Iterable[Mapping[str, object]], field: str) -> str:
    row = _scenario_for_metric(rows, field, reverse=False)
    return str(row.get("scenario", "")) if row is not None else ""


def _scenario_for_max_metric(rows: Iterable[Mapping[str, object]], field: str) -> str:
    row = _scenario_for_metric(rows, field, reverse=True)
    return str(row.get("scenario", "")) if row is not None else ""


def _scenario_for_metric(
    rows: Iterable[Mapping[str, object]],
    field: str,
    *,
    reverse: bool,
) -> Mapping[str, object] | None:
    candidates: list[tuple[float, Mapping[str, object]]] = []
    for row in rows:
        raw_value = row.get(field)
        if raw_value is None:
            continue
        value = float(raw_value)
        if not math.isnan(value):
            candidates.append((value, row))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[0], reverse=reverse)[0][1]


def _metric_values(rows: Iterable[Mapping[str, object]], field: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        raw_value = row.get(field)
        if raw_value is None:
            continue
        value = float(raw_value)
        if not math.isnan(value):
            values.append(value)
    return values


def _min_metric(
    rows: Iterable[Mapping[str, object]],
    field: str,
    *,
    missing: float = 0.0,
) -> float:
    values = _metric_values(rows, field)
    return float(min(values)) if values else missing


def _max_metric(rows: Iterable[Mapping[str, object]], field: str) -> float:
    values = _metric_values(rows, field)
    return float(max(values)) if values else 0.0


def _median_metric(
    rows: Iterable[Mapping[str, object]],
    field: str,
    *,
    missing: float = 0.0,
) -> float:
    values = sorted(_metric_values(rows, field))
    if not values:
        return missing
    midpoint = len(values) // 2
    if len(values) % 2:
        return float(values[midpoint])
    return float((values[midpoint - 1] + values[midpoint]) / 2.0)


def _union_csv_values(rows: Iterable[Mapping[str, object]], field: str) -> tuple[str, ...]:
    values = {
        item.strip()
        for row in rows
        for item in str(row.get(field, "")).split(",")
        if item.strip()
    }
    return tuple(sorted(values))


def results_to_decision_log_rows(
    results: Mapping[str, DcaResearchResult],
) -> tuple[dict[str, object], ...]:
    """Convert trades and skipped buys into monthly decision-log rows."""

    rows: list[dict[str, object]] = []
    for result in results.values():
        for trade in result.trades:
            rows.append(
                {
                    "date": trade.get("date", ""),
                    "name": result.name,
                    "action": "buy",
                    "regime": trade.get("regime", ""),
                    "multiplier": trade.get("multiplier", 0.0),
                    "buy_value": trade.get("buy_value", 0.0),
                    "requested_buy_value": trade.get("requested_buy_value", 0.0),
                    "skip_reason": "",
                    **{
                        key: value
                        for key, value in trade.items()
                        if key
                        not in {
                            "date",
                            "name",
                            "regime",
                            "multiplier",
                            "buy_value",
                            "requested_buy_value",
                        }
                    },
                }
            )
        for skip in result.skips:
            rows.append(
                {
                    "date": skip.get("date", ""),
                    "name": result.name,
                    "action": "skip",
                    "regime": skip.get("regime", ""),
                    "multiplier": skip.get("multiplier", 0.0),
                    "buy_value": 0.0,
                    "requested_buy_value": 0.0,
                    "skip_reason": skip.get("reason", ""),
                    **{
                        key: value
                        for key, value in skip.items()
                        if key not in {"date", "name", "regime", "multiplier", "reason"}
                    },
                }
            )
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                str(row.get("date", "")),
                str(row.get("name", "")),
                str(row.get("action", "")),
            ),
        )
    )
