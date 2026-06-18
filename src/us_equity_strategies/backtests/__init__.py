"""Research backtest helpers for strategy candidates."""

__all__ = [
    "available_candidate_names",
    "compare_monthly_execution_day_scenarios",
    "compare_smart_dca_candidates",
    "compare_smart_vs_fixed_dca",
    "evaluate_candidate_results",
    "results_to_decision_log_rows",
    "results_to_metrics_rows",
    "summarize_candidate_evaluations",
    "write_research_artifacts",
    "write_scenario_research_artifacts",
]

def __getattr__(name: str):
    if name == "compare_smart_vs_fixed_dca":
        from .ibit_smart_dca import compare_smart_vs_fixed_dca as _compare_smart_vs_fixed_dca

        return _compare_smart_vs_fixed_dca
    if name in {
        "available_candidate_names",
        "compare_monthly_execution_day_scenarios",
        "compare_smart_dca_candidates",
        "evaluate_candidate_results",
        "results_to_decision_log_rows",
        "results_to_metrics_rows",
        "summarize_candidate_evaluations",
        "write_research_artifacts",
        "write_scenario_research_artifacts",
    }:
        from . import smart_dca_research as _smart_dca_research

        return getattr(_smart_dca_research, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
