"""Research backtest helpers for strategy candidates."""

__all__ = [
    "available_candidate_names",
    "audit_smart_dca_promotion_gate",
    "candidate_summaries_to_rows",
    "candidate_specs_to_rows",
    "compare_execution_day_contribution_scenarios",
    "compare_monthly_execution_day_scenarios",
    "compare_sample_window_scenarios",
    "compare_smart_dca_candidates",
    "compare_smart_vs_fixed_dca",
    "evaluate_candidate_results",
    "results_to_cash_flow_rows",
    "results_to_decision_log_rows",
    "results_to_equity_curve_rows",
    "results_to_metrics_rows",
    "scenario_results_to_coverage_rows",
    "scenario_results_to_robustness_rows",
    "scenario_results_to_review_decision",
    "scenario_results_to_selection_rows",
    "summarize_candidate_evaluations",
    "write_research_artifacts",
    "write_scenario_research_artifacts",
]

def __getattr__(name: str):
    if name == "compare_smart_vs_fixed_dca":
        from .ibit_smart_dca import compare_smart_vs_fixed_dca as _compare_smart_vs_fixed_dca

        return _compare_smart_vs_fixed_dca
    if name == "audit_smart_dca_promotion_gate":
        from .smart_dca_promotion_gate import (
            audit_smart_dca_promotion_gate as _audit_smart_dca_promotion_gate,
        )

        return _audit_smart_dca_promotion_gate
    if name in {
        "available_candidate_names",
        "candidate_summaries_to_rows",
        "candidate_specs_to_rows",
        "compare_execution_day_contribution_scenarios",
        "compare_monthly_execution_day_scenarios",
        "compare_sample_window_scenarios",
        "compare_smart_dca_candidates",
        "evaluate_candidate_results",
        "results_to_cash_flow_rows",
        "results_to_decision_log_rows",
        "results_to_equity_curve_rows",
        "results_to_metrics_rows",
        "scenario_results_to_coverage_rows",
        "scenario_results_to_robustness_rows",
        "scenario_results_to_review_decision",
        "scenario_results_to_selection_rows",
        "summarize_candidate_evaluations",
        "write_research_artifacts",
        "write_scenario_research_artifacts",
    }:
        from . import smart_dca_research as _smart_dca_research

        return getattr(_smart_dca_research, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
