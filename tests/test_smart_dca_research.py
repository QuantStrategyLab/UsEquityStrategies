import inspect
import json

import pandas as pd

from us_equity_strategies.backtests.smart_dca_research import (
    DcaResearchResult,
    available_candidate_names,
    candidate_set_signal_consumers,
    candidate_set_signal_source_modes,
    candidate_signal_consumers,
    candidate_summaries_to_rows,
    candidate_specs_to_rows,
    compare_execution_day_contribution_scenarios,
    compare_monthly_execution_day_scenarios,
    compare_sample_window_scenarios,
    compare_smart_dca_candidates,
    evaluate_candidate_results,
    production_equivalent_candidate_name,
    results_to_cash_flow_rows,
    results_to_decision_log_rows,
    results_to_equity_curve_rows,
    results_to_metrics_rows,
    scenario_results_to_coverage_rows,
    scenario_results_to_robustness_rows,
    scenario_results_to_review_decision,
    scenario_results_to_selection_rows,
    summarize_candidate_evaluations,
    write_research_artifacts,
    write_scenario_research_artifacts,
)
from us_equity_strategies.signals import required_indicator_fields_for_consumer
from us_equity_strategies.strategies.ibit_smart_dca import (
    build_rebalance_plan as build_ibit_smart_dca_plan,
)
from us_equity_strategies.strategies.nasdaq_sp500_smart_dca import (
    build_rebalance_plan as build_nasdaq_sp500_smart_dca_plan,
)


IBIT_SMART_DCA_PROFILE = "ibit_smart_dca"
NASDAQ_SP500_SMART_DCA_PROFILE = "nasdaq_sp500_smart_dca"


def _series(values, *, start: str = "2024-01-02") -> pd.Series:
    index = pd.date_range(start, periods=len(values), freq="B")
    return pd.Series(values, index=index, dtype=float)


def _research_result(
    name: str,
    *,
    terminal_value: float,
    max_drawdown: float = 0.10,
    trade_count: int = 1,
    skipped_count: int = 0,
    deployment_rate: float = 1.0,
    cash: float = 0.0,
) -> DcaResearchResult:
    return DcaResearchResult(
        name=name,
        terminal_value=terminal_value,
        cash=cash,
        shares=max(terminal_value - cash, 0.0),
        invested=1000.0,
        contributions=1000.0,
        max_drawdown=max_drawdown,
        max_underwater_days=0,
        money_weighted_return=0.0,
        trade_count=trade_count,
        skipped_count=skipped_count,
        deployment_rate=deployment_rate,
        relative_terminal_value_pct=0.0,
        equity_curve=(
            {
                "date": "2025-01-01",
                "name": name,
                "equity": 1000.0,
                "cash": cash,
                "drawdown_pct": 0.0,
            },
            {
                "date": "2026-01-01",
                "name": name,
                "equity": terminal_value,
                "cash": cash,
                "drawdown_pct": max_drawdown * 100.0,
            },
        ),
        cash_flows=(
            {
                "date": "2025-01-01",
                "name": name,
                "cash_flow_type": "contribution",
                "amount": -1000.0,
            },
            {
                "date": "2026-01-01",
                "name": name,
                "cash_flow_type": "terminal_value",
                "amount": terminal_value,
            },
        ),
        trades=(),
        skips=(),
        last_signal_metrics={},
    )


def test_nasdaq_sp500_candidate_shares_fixed_contributions_and_can_skip(tmp_path) -> None:
    prices = _series([100.0 + i * 0.65 for i in range(360)])
    signals = pd.DataFrame({"QQQ": prices, "SPY": prices * 0.92})

    result = compare_smart_dca_candidates(
        signal_prices=signals,
        trade_prices=prices,
        candidate_set="nasdaq_sp500_price",
        monthly_contribution_usd=1000.0,
    )

    fixed = result["fixed"]
    smart = result["nasdaq_sp500_price_defensive"]

    assert fixed.contributions == smart.contributions
    assert fixed.trade_count > 0
    assert smart.skipped_count > 0
    assert smart.deployment_rate < fixed.deployment_rate
    assert smart.skips[0]["reason"] == "valuation_too_expensive"
    assert smart.skips[0]["multiplier"] == 0.0
    assert isinstance(smart.relative_terminal_value_pct, float)

    evaluations = evaluate_candidate_results(result)
    evaluation = evaluations["nasdaq_sp500_price_defensive"]
    assert evaluation.passed is False
    assert "skip_rate_too_high_without_drawdown_improvement" in evaluation.reasons
    assert summarize_candidate_evaluations(evaluations)[0].name == "nasdaq_sp500_price_defensive"

    metrics_rows = results_to_metrics_rows(result, evaluations=evaluations)
    smart_row = next(row for row in metrics_rows if row["name"] == "nasdaq_sp500_price_defensive")
    assert smart_row["passed_promotion_gate"] is False
    assert "skip_rate_too_high_without_drawdown_improvement" in smart_row["failure_reasons"]
    assert smart_row["max_underwater_days"] >= 0
    assert isinstance(smart_row["money_weighted_return_pct"], float)
    assert smart_row["average_cash_ratio_pct"] >= 0.0
    assert smart_row["max_cash_ratio_pct"] >= smart_row["average_cash_ratio_pct"]
    assert smart_row["terminal_cash_ratio_pct"] >= 0.0
    assert smart_row["scheduled_decision_count"] == (
        smart_row["trade_count"] + smart_row["skipped_count"]
    )
    assert smart_row["zero_multiplier_count"] == smart_row["skipped_count"]
    assert smart_row["zero_multiplier_ratio"] > 0.0
    assert smart_row["boosted_multiplier_count"] >= 0
    assert smart_row["max_scheduled_multiplier"] >= smart_row["min_scheduled_multiplier"]
    assert "very_expensive_overbought" in smart_row["regimes_seen"]
    assert "worst_relative_value_gap_after_1y_pct" in smart_row

    decision_rows = results_to_decision_log_rows(result)
    assert any(row["action"] == "buy" and row["name"] == "fixed" for row in decision_rows)
    assert any(
        row["action"] == "skip"
        and row["name"] == "nasdaq_sp500_price_defensive"
            and row["skip_reason"] == "valuation_too_expensive"
            for row in decision_rows
    )

    equity_rows = results_to_equity_curve_rows(result)
    assert any(row["name"] == "fixed" and row["equity"] > 0.0 for row in equity_rows)
    assert any(row["name"] == "nasdaq_sp500_price_defensive" for row in equity_rows)

    cash_flow_rows = results_to_cash_flow_rows(result)
    assert any(
        row["name"] == "fixed"
        and row["cash_flow_type"] == "contribution"
        and row["amount"] < 0.0
        for row in cash_flow_rows
    )
    assert any(
        row["name"] == "nasdaq_sp500_price_defensive"
        and row["cash_flow_type"] == "terminal_value"
        and row["amount"] > 0.0
        for row in cash_flow_rows
    )

    spec_rows = candidate_specs_to_rows(("nasdaq_sp500_price_defensive",))
    assert any(row["parameter_name"] == "base_multiplier" for row in spec_rows)
    assert all(row["rule_type"] == "trend_drawdown" for row in spec_rows)
    summary_rows = candidate_summaries_to_rows(("nasdaq_sp500_price_defensive",))
    summary_row = summary_rows[0]
    assert summary_row["name"] == "nasdaq_sp500_price_defensive"
    assert summary_row["family"] == "nasdaq_sp500_price"
    assert summary_row["rule_type"] == "trend_drawdown"
    assert summary_row["signal_source_mode"] == "market_history_price_indicators"
    assert summary_row["parameter_count"] == 15
    assert summary_row["open_parameter_search"] is False
    assert len(str(summary_row["candidate_definition_sha256"])) == 64

    precomputed_summary = candidate_summaries_to_rows(
        ("ibit_btc_precomputed_ahr999_sma_mayer_cycle",)
    )[0]
    assert precomputed_summary["signal_source_mode"] == (
        "external_precomputed_derived_indicators"
    )
    assert precomputed_summary["compatible_signal_consumers"] == (
        "research:ibit_btc_ahr999_mayer_precomputed_variants"
    )

    artifact_paths = write_research_artifacts(tmp_path, result, evaluations=evaluations)
    assert set(artifact_paths) == {
        "metrics",
        "evaluation_summary",
        "decision_log",
        "equity_curve",
        "cash_flows",
        "candidate_summary",
        "candidate_specs",
        "run_manifest",
    }
    assert "passed_promotion_gate" in artifact_paths["metrics"].read_text(encoding="utf-8")
    assert "max_underwater_days" in artifact_paths["metrics"].read_text(encoding="utf-8")
    assert "money_weighted_return_pct" in artifact_paths["metrics"].read_text(encoding="utf-8")
    assert "regimes_seen" in artifact_paths["metrics"].read_text(encoding="utf-8")
    assert "skip_rate_too_high_without_drawdown_improvement" in artifact_paths[
        "evaluation_summary"
    ].read_text(encoding="utf-8")
    assert "valuation_too_expensive" in artifact_paths["decision_log"].read_text(encoding="utf-8")
    assert "drawdown_pct" in artifact_paths["equity_curve"].read_text(encoding="utf-8")
    assert "terminal_value" in artifact_paths["cash_flows"].read_text(encoding="utf-8")
    assert "unique_multiplier_count" in artifact_paths["candidate_summary"].read_text(
        encoding="utf-8"
    )
    assert "compatible_signal_consumers" in artifact_paths[
        "candidate_summary"
    ].read_text(encoding="utf-8")
    assert "candidate_definition_sha256" in artifact_paths["candidate_summary"].read_text(
        encoding="utf-8"
    )
    assert "base_multiplier" in artifact_paths["candidate_specs"].read_text(encoding="utf-8")
    run_manifest = json.loads(artifact_paths["run_manifest"].read_text(encoding="utf-8"))
    assert run_manifest["schema_version"] == "smart_dca_research_artifacts.v1"
    assert {item["path"] for item in run_manifest["files"]} == {
        "metrics.csv",
        "evaluation_summary.csv",
        "decision_log.csv",
        "equity_curve.csv",
        "cash_flows.csv",
        "candidate_summary.csv",
        "candidate_specs.csv",
    }


def test_nasdaq_sp500_variants_compare_defensive_and_no_skip() -> None:
    prices = _series([100.0 + i * 0.65 for i in range(360)])
    signals = pd.DataFrame({"QQQ": prices, "SPY": prices * 0.92})

    result = compare_smart_dca_candidates(
        signal_prices=signals,
        trade_prices=prices,
        candidate_set="nasdaq_sp500_price_variants",
        monthly_contribution_usd=1000.0,
    )

    assert set(result) == {
        "fixed",
        "nasdaq_sp500_price_defensive",
        "nasdaq_sp500_price_no_skip",
    }
    defensive = result["nasdaq_sp500_price_defensive"]
    no_skip = result["nasdaq_sp500_price_no_skip"]
    assert defensive.skipped_count > 0
    assert no_skip.skipped_count == 0
    assert no_skip.deployment_rate >= defensive.deployment_rate
    assert no_skip.trades[0]["regime"] == "very_expensive_overbought"
    assert no_skip.trades[0]["multiplier"] == 1.0

    summary_rows = candidate_summaries_to_rows(("nasdaq_sp500_price_no_skip",))
    assert summary_rows[0]["zero_multiplier_allowed"] is False
    assert summary_rows[0]["unique_multiplier_count"] == 4


def test_execution_day_scenarios_keep_candidate_set_fixed(tmp_path) -> None:
    prices = _series([100.0 + i * 0.12 for i in range(360)])
    signals = pd.DataFrame({"QQQ": prices, "SPY": prices * 0.95})

    scenarios = compare_monthly_execution_day_scenarios(
        signal_prices=signals,
        trade_prices=prices,
        execution_days=(1, 25),
        candidate_set="nasdaq_sp500_price",
        monthly_contribution_usd=1000.0,
    )

    assert set(scenarios) == {"monthly_day_1", "monthly_day_25"}
    assert set(scenarios["monthly_day_1"]) == set(scenarios["monthly_day_25"])
    first_day_1_trade = scenarios["monthly_day_1"]["fixed"].trades[0]["date"]
    first_day_25_trade = scenarios["monthly_day_25"]["fixed"].trades[0]["date"]
    assert first_day_1_trade < first_day_25_trade
    assert int(str(first_day_25_trade).split("-")[2]) >= 25

    artifact_paths = write_scenario_research_artifacts(
        tmp_path,
        scenarios,
        metadata={"research_config": {"candidate_set": "nasdaq_sp500_price"}},
    )
    assert "scenario_index" in artifact_paths
    assert "robustness_summary" in artifact_paths
    assert "selection_summary" in artifact_paths
    assert "scenario_coverage" in artifact_paths
    assert "production_profile_decisions" in artifact_paths
    assert "review_decision" in artifact_paths
    assert "scenario_manifest" in artifact_paths
    scenario_index = artifact_paths["scenario_index"].read_text(encoding="utf-8")
    robustness_summary = artifact_paths["robustness_summary"].read_text(encoding="utf-8")
    selection_summary = artifact_paths["selection_summary"].read_text(encoding="utf-8")
    scenario_coverage = artifact_paths["scenario_coverage"].read_text(encoding="utf-8")
    production_profile_decisions = artifact_paths[
        "production_profile_decisions"
    ].read_text(encoding="utf-8")
    assert "monthly_day_1" in scenario_index
    assert "monthly_day_25" in scenario_index
    assert "pass_rate" in robustness_summary
    assert "recommendation_status" in selection_summary
    assert (
        "hold_default_fixed_dca" in selection_summary
        or "promote_to_manual_review" in selection_summary
    )
    assert "review_status" in robustness_summary
    assert "coverage_gate_passed" in scenario_coverage
    assert "scenario_count_below_min_review_scenarios" in scenario_coverage
    assert "profile" in production_profile_decisions
    assert "runtime_default_recommendation" in production_profile_decisions
    assert "default_change_allowed_by_research" in production_profile_decisions
    assert "nasdaq_sp500_smart_dca" in production_profile_decisions
    assert "ibit_smart_dca" in production_profile_decisions
    assert "fixed_dca" in production_profile_decisions
    review_decision = json.loads(artifact_paths["review_decision"].read_text(encoding="utf-8"))
    assert review_decision["artifact_type"] == "smart_dca_review_decision"
    assert review_decision["selection_policy"] == "fixed_preset_no_parameter_search"
    assert review_decision["effect_size_policy"] == (
        "fixed_minimum_effect_no_parameter_search"
    )
    assert review_decision["candidate_universe_policy"] == (
        "frozen_preset_names_no_parameter_search"
    )
    assert review_decision["candidate_universe_names"] == [
        "nasdaq_sp500_price_defensive",
    ]
    assert len(review_decision["candidate_universe_definition_sha256s"][0]) == 64
    assert review_decision["runtime_default_recommendation"] == "fixed_dca"
    assert review_decision["runtime_default_change_policy"] == (
        "manual_review_required_no_auto_enable"
    )
    assert (
        review_decision["smart_mode_enablement_status"]
        == "not_recommended_for_enablement"
    )
    assert review_decision["observed_best_smart_candidates"][0]["name"] == (
        "nasdaq_sp500_price_defensive"
    )
    assert review_decision["manual_review_candidate_names"] == []
    assert review_decision["effect_size_thresholds"] == {
        "min_worst_relative_terminal_value_pct": 0.0,
        "min_median_relative_terminal_value_pct": 1.0,
        "min_worst_rank_score": 0.0,
        "max_terminal_cash_ratio_pct": 35.0,
    }
    assert review_decision["overall_recommendation_status"] == "hold_default_fixed_dca"
    assert "scenario_count_below_min_review_scenarios" in review_decision["blocking_reasons"]
    assert review_decision["matrix_coverage_gate_passed"] is False
    assert review_decision["selection_gate_summary"]["matrix_coverage_gate_passed"] is False
    assert isinstance(
        review_decision["selection_gate_summary"][
            "all_selection_effect_size_gate_passed"
        ],
        bool,
    )
    assert review_decision["selection_count"] == 1
    assert "weakest_scenario" in robustness_summary
    assert "median_money_weighted_return_pct" in robustness_summary
    assert "max_terminal_cash_ratio_pct" in robustness_summary
    assert "worst_relative_value_gap_after_1y_pct" in robustness_summary
    assert (tmp_path / "monthly_day_1" / "metrics.csv").exists()
    assert (tmp_path / "monthly_day_25" / "decision_log.csv").exists()
    assert (tmp_path / "monthly_day_25" / "equity_curve.csv").exists()
    assert (tmp_path / "monthly_day_25" / "cash_flows.csv").exists()
    assert (tmp_path / "monthly_day_25" / "candidate_summary.csv").exists()
    assert (tmp_path / "monthly_day_25" / "candidate_specs.csv").exists()
    scenario_manifest = json.loads(artifact_paths["scenario_manifest"].read_text(encoding="utf-8"))
    assert scenario_manifest["artifact_type"] == "smart_dca_research_scenario_matrix"
    assert scenario_manifest["min_review_scenarios"] == 3
    assert scenario_manifest["metadata"]["research_config"]["candidate_set"] == "nasdaq_sp500_price"
    assert "scenario_index.csv" in {item["path"] for item in scenario_manifest["files"]}
    assert "robustness_summary.csv" in {item["path"] for item in scenario_manifest["files"]}
    assert "selection_summary.csv" in {item["path"] for item in scenario_manifest["files"]}
    assert "scenario_coverage.csv" in {item["path"] for item in scenario_manifest["files"]}
    assert "production_profile_decisions.csv" in {
        item["path"] for item in scenario_manifest["files"]
    }
    assert "review_decision.json" in {item["path"] for item in scenario_manifest["files"]}


def test_execution_day_contribution_scenarios_cover_scale_robustness(tmp_path) -> None:
    prices = _series([100.0 + i * 0.10 for i in range(360)])
    signals = pd.DataFrame({"QQQ": prices, "SPY": prices * 0.95})

    scenarios = compare_execution_day_contribution_scenarios(
        signal_prices=signals,
        trade_prices=prices,
        execution_days=(1, 25),
        monthly_contribution_usd_values=(500.0, 1000.0),
        candidate_set="nasdaq_sp500_price",
    )

    assert set(scenarios) == {
        "monthly_day_1_contribution_usd_500",
        "monthly_day_25_contribution_usd_500",
        "monthly_day_1_contribution_usd_1000",
        "monthly_day_25_contribution_usd_1000",
    }
    assert scenarios["monthly_day_1_contribution_usd_500"]["fixed"].contributions < scenarios[
        "monthly_day_1_contribution_usd_1000"
    ]["fixed"].contributions

    artifact_paths = write_scenario_research_artifacts(tmp_path, scenarios)
    scenario_index = artifact_paths["scenario_index"].read_text(encoding="utf-8")
    robustness_rows = scenario_results_to_robustness_rows(scenarios)
    selection_rows = scenario_results_to_selection_rows(scenarios)
    coverage_rows = scenario_results_to_coverage_rows(scenarios)
    review_decision = scenario_results_to_review_decision(scenarios)
    assert "monthly_day_25_contribution_usd_1000" in scenario_index
    assert (tmp_path / "monthly_day_1_contribution_usd_500" / "metrics.csv").exists()
    assert robustness_rows[0]["name"] == "nasdaq_sp500_price_defensive"
    assert robustness_rows[0]["review_rank"] == 1
    assert robustness_rows[0]["scenario_count"] == 4
    assert 0.0 <= robustness_rows[0]["pass_rate"] <= 1.0
    assert robustness_rows[0]["review_status"] in {
        "candidate_passed_robustness_gate",
        "mixed_scenario_results",
        "failed_robustness_gate",
    }
    assert str(robustness_rows[0]["weakest_scenario"]).startswith("monthly_day_")
    assert "min_money_weighted_return_pct" in robustness_rows[0]
    assert "max_average_cash_ratio_pct" in robustness_rows[0]
    assert "max_zero_multiplier_ratio" in robustness_rows[0]
    assert "max_boosted_multiplier_ratio" in robustness_rows[0]
    assert "dominant_performance_diagnosis" in robustness_rows[0]
    assert "performance_diagnoses" in robustness_rows[0]
    assert "regimes_seen" in robustness_rows[0]
    assert "worst_max_drawdown_delta_pct_points" in robustness_rows[0]
    assert selection_rows[0]["selection_group"] == "nasdaq_sp500_price"
    assert selection_rows[0]["selected_name"] == "nasdaq_sp500_price_defensive"
    assert selection_rows[0]["selected_family"] == "nasdaq_sp500_price"
    assert selection_rows[0]["selected_rule_type"] == "trend_drawdown"
    assert selection_rows[0]["selected_parameter_count"] == 15
    assert len(str(selection_rows[0]["selected_candidate_definition_sha256"])) == 64
    assert selection_rows[0]["selected_candidate_role"] == (
        "best_observed_smart_candidate"
    )
    assert selection_rows[0]["selection_policy"] == "fixed_preset_no_parameter_search"
    assert selection_rows[0]["runtime_default_recommendation"] == "fixed_dca"
    assert selection_rows[0]["runtime_default_change_policy"] == (
        "manual_review_required_no_auto_enable"
    )
    assert selection_rows[0]["effect_size_policy"] == (
        "fixed_minimum_effect_no_parameter_search"
    )
    assert selection_rows[0]["recommendation_status"] in {
        "promote_to_manual_review",
        "hold_default_fixed_dca",
    }
    assert selection_rows[0]["min_review_scenarios"] == 3
    assert selection_rows[0]["review_scenario_gate_passed"] is True
    assert selection_rows[0]["matrix_coverage_gate_passed"] is True
    assert selection_rows[0]["matrix_coverage_status"] == "ready_for_selection_review"
    assert selection_rows[0]["matrix_coverage_failure_reasons"] == ""
    assert selection_rows[0]["matrix_scenario_count"] == 4
    assert selection_rows[0]["matrix_candidate_count"] == 1
    assert selection_rows[0]["matrix_candidate_set_consistent"] is True
    assert selection_rows[0]["matrix_fixed_benchmark_present_all"] is True
    assert selection_rows[0]["matrix_candidate_names"] == "nasdaq_sp500_price_defensive"
    assert selection_rows[0]["matrix_candidate_universe_policy"] == (
        "frozen_preset_names_no_parameter_search"
    )
    assert selection_rows[0]["matrix_candidate_definition_hash_count"] == 1
    assert len(str(selection_rows[0]["matrix_candidate_definition_sha256s"])) == 64
    assert "selected_effect_size_gate_passed" in selection_rows[0]
    assert "selected_median_relative_terminal_value_pct" in selection_rows[0]
    assert "selected_max_zero_multiplier_ratio" in selection_rows[0]
    assert "selected_max_boosted_multiplier_ratio" in selection_rows[0]
    assert "selected_dominant_performance_diagnosis" in selection_rows[0]
    assert "selected_performance_diagnoses" in selection_rows[0]
    assert "selected_regimes_seen" in selection_rows[0]
    assert selection_rows[0]["max_effect_terminal_cash_ratio_pct"] == 35.0
    assert selection_rows[0]["fixed_benchmark"] == "fixed"
    assert len(str(selection_rows[0]["compared_candidate_definition_sha256s"])) == 64
    assert coverage_rows[0]["scenario_count"] == 4
    assert coverage_rows[0]["coverage_status"] == "ready_for_selection_review"
    assert coverage_rows[0]["failure_reasons"] == ""
    assert coverage_rows[0]["candidate_universe_policy"] == (
        "frozen_preset_names_no_parameter_search"
    )
    assert coverage_rows[0]["candidate_definition_hash_count"] == 1
    assert len(str(coverage_rows[0]["candidate_definition_sha256s"])) == 64
    assert coverage_rows[0]["scenario_cadences"] == "monthly"
    assert coverage_rows[0]["scenario_cadence_count"] == 1
    assert coverage_rows[0]["scenario_execution_days"] == "1,25"
    assert coverage_rows[0]["scenario_execution_day_count"] == 2
    assert coverage_rows[0]["scenario_contribution_amounts_usd"] == "500,1000"
    assert coverage_rows[0]["scenario_contribution_amount_count"] == 2
    assert coverage_rows[0]["scenario_start_dates"] == ""
    assert coverage_rows[0]["scenario_start_date_count"] == 0
    assert coverage_rows[0]["scenario_sample_window_count"] == 1
    assert coverage_rows[0]["scenario_sample_first_date_count"] == 1
    assert coverage_rows[0]["scenario_sample_last_date_count"] == 1
    assert coverage_rows[0]["scenario_sample_window_audit_passed"] is True
    assert coverage_rows[0]["scenario_recognized_dimension_count"] == 3
    assert coverage_rows[0]["scenario_varied_dimensions"] == (
        "execution_day,contribution_amount"
    )
    assert coverage_rows[0]["scenario_varied_dimension_count"] == 2
    assert coverage_rows[0]["scenario_dimension_coverage_gate_passed"] is True
    assert selection_rows[0]["matrix_scenario_cadences"] == "monthly"
    assert selection_rows[0]["matrix_scenario_execution_days"] == "1,25"
    assert selection_rows[0]["matrix_scenario_contribution_amounts_usd"] == "500,1000"
    assert selection_rows[0]["matrix_scenario_sample_window_count"] == 1
    assert selection_rows[0]["matrix_scenario_sample_window_audit_passed"] is True
    assert selection_rows[0]["matrix_scenario_varied_dimensions"] == (
        "execution_day,contribution_amount"
    )
    assert selection_rows[0]["matrix_scenario_dimension_coverage_gate_passed"] is True
    assert review_decision["matrix_coverage_gate_passed"] is True
    assert review_decision["matrix_coverage"][
        "scenario_sample_window_audit_passed"
    ] is True
    assert review_decision["selection_gate_summary"][
        "matrix_dimension_coverage_gate_passed"
    ] is True
    assert review_decision["selection_groups"] == ("nasdaq_sp500_price",)
    assert review_decision["selection_count"] == 1
    assert review_decision["candidate_universe_count"] == 1
    assert len(review_decision["candidate_universe_definition_sha256s"][0]) == 64
    assert review_decision["runtime_default_recommendation"] == "fixed_dca"
    assert review_decision["observed_best_smart_candidates"][0]["selection_group"] == (
        "nasdaq_sp500_price"
    )
    assert "dominant_performance_diagnosis" in (
        review_decision["observed_best_smart_candidates"][0]
    )
    assert "performance_diagnoses" in (
        review_decision["observed_best_smart_candidates"][0]
    )
    assert len(
        review_decision["observed_best_smart_candidates"][0][
            "candidate_definition_sha256"
        ]
    ) == 64
    profile_decisions = {
        row["profile"]: row
        for row in review_decision["production_profile_decisions"]
    }
    assert set(profile_decisions) == {
        IBIT_SMART_DCA_PROFILE,
        NASDAQ_SP500_SMART_DCA_PROFILE,
    }
    assert profile_decisions[NASDAQ_SP500_SMART_DCA_PROFILE][
        "runtime_default_recommendation"
    ] == "fixed_dca"
    assert profile_decisions[NASDAQ_SP500_SMART_DCA_PROFILE][
        "default_change_allowed_by_research"
    ] is False
    assert "observed_best_dominant_performance_diagnosis" in (
        profile_decisions[NASDAQ_SP500_SMART_DCA_PROFILE]
    )
    assert profile_decisions[IBIT_SMART_DCA_PROFILE][
        "observed_best_status"
    ] == "not_evaluated"
    assert review_decision["overall_recommendation_status"] in {
        "promote_to_manual_review",
        "hold_default_fixed_dca",
    }


def test_selection_rows_hold_fixed_when_no_variant_passes() -> None:
    scenarios = {
        "scenario_a": {
            "fixed": _research_result("fixed", terminal_value=1000.0),
            "nasdaq_sp500_price_defensive": _research_result(
                "nasdaq_sp500_price_defensive",
                terminal_value=975.0,
                max_drawdown=0.12,
                skipped_count=1,
                deployment_rate=0.60,
            ),
            "nasdaq_sp500_price_no_skip": _research_result(
                "nasdaq_sp500_price_no_skip",
                terminal_value=980.0,
                max_drawdown=0.12,
            ),
        }
    }

    rows = scenario_results_to_selection_rows(scenarios)

    assert rows[0]["selection_group"] == "nasdaq_sp500_price"
    assert rows[0]["recommendation_status"] == "hold_default_fixed_dca"
    assert rows[0]["runtime_default_recommendation"] == "fixed_dca"
    assert (
        rows[0]["smart_mode_enablement_status"]
        == "not_recommended_for_enablement"
    )
    assert rows[0]["recommendation_reason"] == "no_candidate_passed_robustness_gate"
    assert "nasdaq_sp500_price_no_skip" in rows[0]["compared_candidates"]


def test_performance_diagnosis_flags_cash_drag_without_changing_gates() -> None:
    scenarios = {
        "monthly_day_1": {
            "fixed": _research_result("fixed", terminal_value=1000.0),
            "nasdaq_sp500_price_no_skip": _research_result(
                "nasdaq_sp500_price_no_skip",
                terminal_value=970.0,
                skipped_count=2,
                deployment_rate=0.70,
                cash=200.0,
            ),
        },
        "monthly_day_25": {
            "fixed": _research_result("fixed", terminal_value=1000.0),
            "nasdaq_sp500_price_no_skip": _research_result(
                "nasdaq_sp500_price_no_skip",
                terminal_value=965.0,
                skipped_count=1,
                deployment_rate=0.75,
                cash=175.0,
            ),
        },
    }

    metrics_rows = results_to_metrics_rows(scenarios["monthly_day_1"])
    smart_metrics = {
        row["name"]: row
        for row in metrics_rows
    }["nasdaq_sp500_price_no_skip"]
    robustness_rows = scenario_results_to_robustness_rows(scenarios)
    selection_rows = scenario_results_to_selection_rows(
        scenarios,
        min_review_scenarios=1,
    )
    review_decision = scenario_results_to_review_decision(
        scenarios,
        min_review_scenarios=1,
    )

    assert smart_metrics["primary_performance_diagnosis"] == (
        "terminal_underperformance_vs_fixed"
    )
    assert "skipped_buy_cash_drag" in smart_metrics["performance_diagnoses"]
    assert "lower_deployment_rate" in smart_metrics["performance_diagnoses"]
    assert "excess_terminal_cash" in smart_metrics["performance_diagnoses"]
    assert robustness_rows[0]["dominant_performance_diagnosis"] == (
        "terminal_underperformance_vs_fixed"
    )
    assert "skipped_buy_cash_drag" in robustness_rows[0]["performance_diagnoses"]
    assert selection_rows[0]["selected_dominant_performance_diagnosis"] == (
        "terminal_underperformance_vs_fixed"
    )
    observed = review_decision["observed_best_smart_candidates"][0]
    assert observed["dominant_performance_diagnosis"] == (
        "terminal_underperformance_vs_fixed"
    )
    assert "lower_deployment_rate" in observed["performance_diagnoses"]
    profile_decisions = {
        row["profile"]: row
        for row in review_decision["production_profile_decisions"]
    }
    nasdaq_profile = profile_decisions[NASDAQ_SP500_SMART_DCA_PROFILE]
    assert nasdaq_profile["observed_best_dominant_performance_diagnosis"] == (
        "terminal_underperformance_vs_fixed"
    )
    assert "excess_terminal_cash" in nasdaq_profile[
        "observed_best_performance_diagnoses"
    ]
    assert selection_rows[0]["recommendation_status"] == "hold_default_fixed_dca"
    assert review_decision["runtime_default_recommendation"] == "fixed_dca"


def test_review_decision_keeps_fixed_default_while_naming_manual_review_candidate() -> None:
    scenarios = {
        "monthly_day_1": {
            "fixed": _research_result("fixed", terminal_value=1000.0),
            "nasdaq_sp500_price_no_skip": _research_result(
                "nasdaq_sp500_price_no_skip",
                terminal_value=1030.0,
                max_drawdown=0.08,
            ),
        },
        "monthly_day_25": {
            "fixed": _research_result("fixed", terminal_value=1000.0),
            "nasdaq_sp500_price_no_skip": _research_result(
                "nasdaq_sp500_price_no_skip",
                terminal_value=1040.0,
                max_drawdown=0.08,
            ),
        },
    }

    rows = scenario_results_to_selection_rows(
        scenarios,
        min_review_scenarios=1,
    )
    decision = scenario_results_to_review_decision(
        scenarios,
        min_review_scenarios=1,
    )

    assert rows[0]["selected_name"] == "nasdaq_sp500_price_no_skip"
    assert rows[0]["recommendation_status"] == "promote_to_manual_review"
    assert rows[0]["runtime_default_recommendation"] == "fixed_dca"
    assert rows[0]["smart_mode_enablement_status"] == "manual_review_candidate"
    assert decision["overall_recommendation_status"] == "promote_to_manual_review"
    assert decision["runtime_default_recommendation"] == "fixed_dca"
    assert decision["runtime_default_change_policy"] == (
        "manual_review_required_no_auto_enable"
    )
    assert decision["smart_mode_enablement_status"] == "manual_review_candidate"
    assert decision["manual_review_candidate_names"] == (
        "nasdaq_sp500_price_no_skip",
    )
    observed = decision["observed_best_smart_candidates"][0]
    assert observed["selection_group"] == "nasdaq_sp500_price"
    assert observed["name"] == "nasdaq_sp500_price_no_skip"
    assert observed["status"] == "promote_to_manual_review"
    assert observed["reason"] == "selected_candidate_passed_all_scenarios"
    assert observed["candidate_role"] == "best_observed_smart_candidate"
    assert len(observed["candidate_definition_sha256"]) == 64
    assert observed["compared_candidates"] == ("nasdaq_sp500_price_no_skip",)
    profile_decisions = {
        row["profile"]: row
        for row in decision["production_profile_decisions"]
    }
    nasdaq_profile = profile_decisions[NASDAQ_SP500_SMART_DCA_PROFILE]
    assert nasdaq_profile["production_equivalent_candidate"] == (
        "nasdaq_sp500_price_no_skip"
    )
    assert nasdaq_profile["production_equivalent_in_candidate_universe"] is True
    assert nasdaq_profile["observed_best_candidate"] == "nasdaq_sp500_price_no_skip"
    assert nasdaq_profile["observed_best_status"] == "promote_to_manual_review"
    assert nasdaq_profile["runtime_default_recommendation"] == "fixed_dca"
    assert nasdaq_profile["manual_review_required_before_default_change"] is True
    assert nasdaq_profile["default_change_allowed_by_research"] is False


def test_selection_rows_require_minimum_robustness_scenarios() -> None:
    scenarios = {
        "scenario_a": {
            "fixed": _research_result("fixed", terminal_value=1000.0),
            "nasdaq_sp500_price_no_skip": _research_result(
                "nasdaq_sp500_price_no_skip",
                terminal_value=1020.0,
                max_drawdown=0.09,
            ),
        }
    }

    rows = scenario_results_to_selection_rows(scenarios)
    assert rows[0]["selected_robustness_gate_passed"] is True
    assert rows[0]["selected_scenario_count"] == 1
    assert rows[0]["min_review_scenarios"] == 3
    assert rows[0]["review_scenario_gate_passed"] is False
    assert rows[0]["matrix_coverage_gate_passed"] is False
    assert rows[0]["recommendation_status"] == "hold_default_fixed_dca"
    assert rows[0]["recommendation_reason"] == "insufficient_robustness_scenarios"

    relaxed_rows = scenario_results_to_selection_rows(
        scenarios,
        min_review_scenarios=1,
    )
    assert relaxed_rows[0]["matrix_coverage_gate_passed"] is False
    assert relaxed_rows[0]["matrix_coverage_failure_reasons"] == (
        "scenario_dimension_coverage_missing"
    )
    assert relaxed_rows[0]["recommendation_status"] == "hold_default_fixed_dca"
    assert relaxed_rows[0]["recommendation_reason"] == (
        "insufficient_scenario_matrix_coverage"
    )


def test_selection_rows_hold_fixed_when_effect_size_is_too_small() -> None:
    scenarios = {
        "monthly_day_1": {
            "fixed": _research_result("fixed", terminal_value=1000.0),
            "nasdaq_sp500_price_no_skip": _research_result(
                "nasdaq_sp500_price_no_skip",
                terminal_value=1001.0,
                max_drawdown=0.10,
            ),
        },
        "monthly_day_25": {
            "fixed": _research_result("fixed", terminal_value=1000.0),
            "nasdaq_sp500_price_no_skip": _research_result(
                "nasdaq_sp500_price_no_skip",
                terminal_value=1001.0,
                max_drawdown=0.10,
            ),
        },
    }

    rows = scenario_results_to_selection_rows(
        scenarios,
        min_review_scenarios=1,
    )
    decision = scenario_results_to_review_decision(
        scenarios,
        min_review_scenarios=1,
    )

    assert rows[0]["selected_robustness_gate_passed"] is True
    assert rows[0]["review_scenario_gate_passed"] is True
    assert rows[0]["matrix_coverage_gate_passed"] is True
    assert rows[0]["matrix_scenario_dimension_coverage_gate_passed"] is True
    assert rows[0]["selected_effect_size_gate_passed"] is False
    assert rows[0]["selected_median_relative_terminal_value_pct"] < 1.0
    assert rows[0]["selected_effect_size_failure_reasons"] == (
        "median_terminal_edge_below_min_effect_size"
    )
    assert rows[0]["recommendation_status"] == "hold_default_fixed_dca"
    assert rows[0]["recommendation_reason"] == "insufficient_effect_size_vs_fixed_dca"
    assert decision["overall_recommendation_status"] == "hold_default_fixed_dca"
    assert "insufficient_effect_size_vs_fixed_dca" in decision["blocking_reasons"]
    assert decision["effect_size_thresholds"][
        "min_median_relative_terminal_value_pct"
    ] == 1.0
    assert decision["effect_size_thresholds"]["max_terminal_cash_ratio_pct"] == 35.0
    assert decision["selection_gate_summary"][
        "all_selection_effect_size_gate_passed"
    ] is False
    assert decision["selection_gate_summary"][
        "all_selection_robustness_gate_passed"
    ] is True


def test_selection_rows_hold_fixed_when_candidate_keeps_too_much_cash() -> None:
    scenarios = {
        "monthly_day_1": {
            "fixed": _research_result("fixed", terminal_value=1000.0),
            "nasdaq_sp500_price_no_skip": _research_result(
                "nasdaq_sp500_price_no_skip",
                terminal_value=1100.0,
                max_drawdown=0.09,
                cash=500.0,
            ),
        },
        "monthly_day_25": {
            "fixed": _research_result("fixed", terminal_value=1000.0),
            "nasdaq_sp500_price_no_skip": _research_result(
                "nasdaq_sp500_price_no_skip",
                terminal_value=1100.0,
                max_drawdown=0.09,
                cash=500.0,
            ),
        },
    }

    rows = scenario_results_to_selection_rows(
        scenarios,
        min_review_scenarios=1,
        max_effect_terminal_cash_ratio_pct=20.0,
    )
    decision = scenario_results_to_review_decision(
        scenarios,
        min_review_scenarios=1,
        max_effect_terminal_cash_ratio_pct=20.0,
    )

    assert rows[0]["selected_robustness_gate_passed"] is True
    assert rows[0]["review_scenario_gate_passed"] is True
    assert rows[0]["matrix_coverage_gate_passed"] is True
    assert rows[0]["selected_effect_size_gate_passed"] is False
    assert rows[0]["selected_effect_size_failure_reasons"] == (
        "terminal_cash_ratio_above_max_effect_size"
    )
    assert rows[0]["max_effect_terminal_cash_ratio_pct"] == 20.0
    assert rows[0]["recommendation_status"] == "hold_default_fixed_dca"
    assert rows[0]["recommendation_reason"] == "insufficient_effect_size_vs_fixed_dca"
    assert decision["overall_recommendation_status"] == "hold_default_fixed_dca"
    assert "insufficient_effect_size_vs_fixed_dca" in decision["blocking_reasons"]
    assert decision["effect_size_thresholds"]["max_terminal_cash_ratio_pct"] == 20.0


def test_selection_rows_hold_fixed_when_matrix_coverage_fails() -> None:
    scenarios = {
        "scenario_a": {
            "fixed": _research_result("fixed", terminal_value=1000.0),
            "nasdaq_sp500_price_no_skip": _research_result(
                "nasdaq_sp500_price_no_skip",
                terminal_value=1020.0,
                max_drawdown=0.09,
            ),
            "nasdaq_sp500_price_defensive": _research_result(
                "nasdaq_sp500_price_defensive",
                terminal_value=990.0,
                max_drawdown=0.12,
            ),
        },
        "scenario_b": {
            "fixed": _research_result("fixed", terminal_value=1000.0),
            "nasdaq_sp500_price_no_skip": _research_result(
                "nasdaq_sp500_price_no_skip",
                terminal_value=1020.0,
                max_drawdown=0.09,
            ),
        },
        "scenario_c": {
            "fixed": _research_result("fixed", terminal_value=1000.0),
            "nasdaq_sp500_price_no_skip": _research_result(
                "nasdaq_sp500_price_no_skip",
                terminal_value=1020.0,
                max_drawdown=0.09,
            ),
        },
    }

    rows = scenario_results_to_selection_rows(scenarios)

    assert rows[0]["selected_name"] == "nasdaq_sp500_price_no_skip"
    assert rows[0]["selected_robustness_gate_passed"] is True
    assert rows[0]["review_scenario_gate_passed"] is True
    assert rows[0]["matrix_coverage_gate_passed"] is False
    assert rows[0]["matrix_coverage_status"] == "insufficient_coverage"
    assert rows[0]["matrix_coverage_failure_reasons"] == (
        "candidate_set_inconsistent,scenario_dimension_coverage_missing"
    )
    assert rows[0]["matrix_scenario_count"] == 3
    assert rows[0]["matrix_candidate_set_consistent"] is False
    assert rows[0]["matrix_fixed_benchmark_present_all"] is True
    assert rows[0]["matrix_scenario_dimension_coverage_gate_passed"] is False
    assert rows[0]["recommendation_status"] == "hold_default_fixed_dca"
    assert rows[0]["recommendation_reason"] == "insufficient_scenario_matrix_coverage"


def test_scenario_coverage_rows_flag_incomplete_or_inconsistent_matrix() -> None:
    scenarios = {
        "scenario_a": {
            "fixed": _research_result("fixed", terminal_value=1000.0),
            "nasdaq_sp500_price_defensive": _research_result(
                "nasdaq_sp500_price_defensive",
                terminal_value=990.0,
            ),
        },
        "scenario_b": {
            "fixed": _research_result("fixed", terminal_value=1000.0),
            "nasdaq_sp500_price_no_skip": _research_result(
                "nasdaq_sp500_price_no_skip",
                terminal_value=1010.0,
            ),
        },
    }

    rows = scenario_results_to_coverage_rows(scenarios)

    assert rows[0]["scenario_count"] == 2
    assert rows[0]["coverage_gate_passed"] is False
    assert rows[0]["coverage_status"] == "insufficient_coverage"
    assert rows[0]["candidate_set_consistent"] is False
    assert rows[0]["fixed_benchmark_present_all"] is True
    assert rows[0]["failure_reasons"] == (
        "scenario_count_below_min_review_scenarios,"
        "candidate_set_inconsistent,"
        "scenario_dimension_coverage_missing"
    )


def test_execution_day_contribution_scenarios_cover_cadence_robustness() -> None:
    prices = _series([100.0 + i * 0.07 for i in range(520)])
    signals = pd.DataFrame({"QQQ": prices, "SPY": prices * 0.95})

    scenarios = compare_execution_day_contribution_scenarios(
        signal_prices=signals,
        trade_prices=prices,
        execution_days=(15,),
        monthly_contribution_usd_values=(1000.0,),
        cadences=("weekly", "monthly", "quarterly"),
        candidate_set="nasdaq_sp500_price",
    )

    assert set(scenarios) == {
        "weekly_contribution_usd_1000",
        "monthly_day_15_contribution_usd_1000",
        "quarterly_day_15_contribution_usd_1000",
    }
    weekly_fixed = scenarios["weekly_contribution_usd_1000"]["fixed"]
    monthly_fixed = scenarios["monthly_day_15_contribution_usd_1000"]["fixed"]
    quarterly_fixed = scenarios["quarterly_day_15_contribution_usd_1000"]["fixed"]

    assert weekly_fixed.trade_count > monthly_fixed.trade_count
    assert quarterly_fixed.trade_count < monthly_fixed.trade_count
    assert weekly_fixed.contributions > 0.0
    assert quarterly_fixed.contributions > 0.0

    coverage_rows = scenario_results_to_coverage_rows(scenarios)
    assert coverage_rows[0]["scenario_cadences"] == "monthly,quarterly,weekly"
    assert coverage_rows[0]["scenario_cadence_count"] == 3
    assert coverage_rows[0]["scenario_execution_days"] == "15"
    assert coverage_rows[0]["scenario_contribution_amounts_usd"] == "1000"
    assert coverage_rows[0]["scenario_sample_window_count"] == 1
    assert coverage_rows[0]["scenario_sample_window_audit_passed"] is True
    assert coverage_rows[0]["scenario_varied_dimensions"] == "cadence"
    assert coverage_rows[0]["scenario_dimension_coverage_gate_passed"] is True


def test_execution_day_contribution_scenarios_cover_rolling_starts() -> None:
    prices = _series([100.0 + i * 0.08 for i in range(520)])
    signals = pd.DataFrame({"QQQ": prices, "SPY": prices * 0.95})

    scenarios = compare_execution_day_contribution_scenarios(
        signal_prices=signals,
        trade_prices=prices,
        execution_days=(15,),
        monthly_contribution_usd_values=(1000.0,),
        start_dates=("2025-01-02", "2025-07-01"),
        candidate_set="nasdaq_sp500_price",
    )

    assert set(scenarios) == {
        "monthly_day_15_contribution_usd_1000_start_2025_01_02",
        "monthly_day_15_contribution_usd_1000_start_2025_07_01",
    }
    early = scenarios["monthly_day_15_contribution_usd_1000_start_2025_01_02"]["fixed"]
    late = scenarios["monthly_day_15_contribution_usd_1000_start_2025_07_01"]["fixed"]
    assert early.contributions > late.contributions
    assert early.equity_curve[0]["date"] >= "2025-01-02"
    assert late.equity_curve[0]["date"] >= "2025-07-01"

    coverage_rows = scenario_results_to_coverage_rows(scenarios)
    assert coverage_rows[0]["scenario_start_dates"] == "2025-01-02,2025-07-01"
    assert coverage_rows[0]["scenario_start_date_count"] == 2
    assert coverage_rows[0]["scenario_sample_first_dates"] == (
        "2025-01-02,2025-07-01"
    )
    assert coverage_rows[0]["scenario_sample_first_date_count"] == 2
    assert coverage_rows[0]["scenario_sample_window_count"] == 2
    assert coverage_rows[0]["scenario_sample_window_audit_passed"] is True
    assert coverage_rows[0]["scenario_varied_dimensions"] == "start_date"
    assert coverage_rows[0]["scenario_dimension_coverage_gate_passed"] is True


def test_sample_window_scenarios_cover_out_of_sample_windows() -> None:
    prices = _series([100.0 + i * 0.08 for i in range(760)], start="2023-01-02")
    signals = pd.DataFrame({"QQQ": prices, "SPY": prices * 0.95})

    scenarios = compare_sample_window_scenarios(
        signal_prices=signals,
        trade_prices=prices,
        sample_windows={
            "validation": ("2024-01-02", "2024-12-31"),
            "oos": ("2025-01-02", "2025-12-31"),
        },
        execution_days=(15,),
        monthly_contribution_usd_values=(1000.0,),
        candidate_set="nasdaq_sp500_price",
    )

    assert set(scenarios) == {
        "sample_window_validation__monthly_day_15_contribution_usd_1000_start_2024_01_02",
        "sample_window_oos__monthly_day_15_contribution_usd_1000_start_2025_01_02",
    }
    validation_fixed = scenarios[
        "sample_window_validation__monthly_day_15_contribution_usd_1000_start_2024_01_02"
    ]["fixed"]
    oos_fixed = scenarios[
        "sample_window_oos__monthly_day_15_contribution_usd_1000_start_2025_01_02"
    ]["fixed"]
    assert validation_fixed.equity_curve[0]["date"] >= "2024-01-02"
    assert validation_fixed.equity_curve[-1]["date"] <= "2024-12-31"
    assert oos_fixed.equity_curve[0]["date"] >= "2025-01-02"
    assert oos_fixed.equity_curve[-1]["date"] <= "2025-12-31"

    coverage_rows = scenario_results_to_coverage_rows(scenarios)
    assert coverage_rows[0]["scenario_sample_window_labels"] == "oos,validation"
    assert coverage_rows[0]["scenario_sample_window_label_count"] == 2
    assert coverage_rows[0]["scenario_sample_window_count"] == 2
    assert coverage_rows[0]["scenario_start_dates"] == "2024-01-02,2025-01-02"
    assert coverage_rows[0]["scenario_varied_dimensions"] == (
        "sample_window,start_date"
    )
    assert coverage_rows[0]["scenario_dimension_coverage_gate_passed"] is True


def test_ibit_btc_candidate_derives_ahr999_mayer_from_prices() -> None:
    btc = _series([250_000.0 for _ in range(280)], start="2025-01-02")
    ibit = _series([50.0 + i * 0.02 for i in range(280)], start="2025-01-02")

    result = compare_smart_dca_candidates(
        signal_prices={"BTC-USD": btc},
        trade_prices=ibit,
        candidate_set="ibit_btc_ahr999_mayer_price",
        monthly_contribution_usd=500.0,
    )

    fixed = result["fixed"]
    smart = result["ibit_btc_ahr999_mayer_cycle"]

    assert fixed.contributions == smart.contributions
    assert fixed.trade_count > 0
    assert smart.skipped_count > 0
    assert smart.skips[0]["reason"] == "valuation_too_expensive"
    assert smart.last_signal_metrics["cycle_indicator_source"] == "price_derived"
    assert smart.last_signal_metrics["ahr999_metric"] == "ahr999"
    assert smart.last_signal_metrics["ahr999"] > 1.2
    assert smart.last_signal_metrics["mayer_multiple"] == 1.0


def test_ibit_btc_price_variants_compare_sma_and_no_skip() -> None:
    btc = _series([250_000.0 for _ in range(280)], start="2025-01-02")
    ibit = _series([50.0 + i * 0.02 for i in range(280)], start="2025-01-02")

    result = compare_smart_dca_candidates(
        signal_prices={"BTC-USD": btc},
        trade_prices=ibit,
        candidate_set="ibit_btc_ahr999_mayer_price_variants",
        monthly_contribution_usd=500.0,
    )

    assert set(result) == {
        "fixed",
        "ibit_btc_ahr999_mayer_cycle",
        "ibit_btc_ahr999_mayer_no_skip_cycle",
        "ibit_btc_ahr999_sma_mayer_cycle",
    }
    no_skip = result["ibit_btc_ahr999_mayer_no_skip_cycle"]
    sma = result["ibit_btc_ahr999_sma_mayer_cycle"]
    assert no_skip.skipped_count == 0
    assert no_skip.last_signal_metrics["ahr999_metric"] == "ahr999"
    assert sma.last_signal_metrics["ahr999_metric"] == "ahr999_sma"
    assert sma.last_signal_metrics["ahr999_selected"] == sma.last_signal_metrics["ahr999_sma"]


def test_ibit_btc_candidate_can_use_precomputed_ahr999_mayer_indicators() -> None:
    dates = pd.date_range("2025-01-02", periods=120, freq="B")
    signals = pd.DataFrame(
        {
            "ahr999": [1.5 for _ in dates],
            "mayer_multiple": [2.5 for _ in dates],
        },
        index=dates,
    )
    ibit = pd.Series([50.0 + i * 0.02 for i in range(len(dates))], index=dates)

    result = compare_smart_dca_candidates(
        signal_prices=signals,
        trade_prices=ibit,
        candidate_set="ibit_btc_ahr999_mayer_precomputed",
        monthly_contribution_usd=500.0,
    )

    fixed = result["fixed"]
    smart = result["ibit_btc_precomputed_ahr999_mayer_cycle"]

    assert fixed.contributions == smart.contributions
    assert fixed.trade_count > 0
    assert smart.skipped_count > 0
    assert smart.skips[0]["reason"] == "valuation_too_expensive"
    assert smart.last_signal_metrics["cycle_indicator_source"] == "precomputed_derived_indicators"
    assert smart.last_signal_metrics["ahr999_metric"] == "ahr999"
    assert smart.last_signal_metrics["ahr999"] == 1.5
    assert smart.last_signal_metrics["mayer_multiple"] == 2.5


def test_ibit_btc_precomputed_variants_use_exported_ahr999_sma() -> None:
    dates = pd.date_range("2025-01-02", periods=120, freq="B")
    signals = pd.DataFrame(
        {
            "ahr999": [1.5 for _ in dates],
            "ahr999_sma": [0.7 for _ in dates],
            "mayer_multiple": [1.0 for _ in dates],
        },
        index=dates,
    )
    ibit = pd.Series([50.0 + i * 0.02 for i in range(len(dates))], index=dates)

    result = compare_smart_dca_candidates(
        signal_prices=signals,
        trade_prices=ibit,
        candidate_set="ibit_btc_ahr999_mayer_precomputed_variants",
        monthly_contribution_usd=500.0,
    )

    assert set(result) == {
        "fixed",
        "ibit_btc_precomputed_ahr999_mayer_cycle",
        "ibit_btc_precomputed_ahr999_mayer_no_skip_cycle",
        "ibit_btc_precomputed_ahr999_sma_mayer_cycle",
    }
    no_skip = result["ibit_btc_precomputed_ahr999_mayer_no_skip_cycle"]
    sma = result["ibit_btc_precomputed_ahr999_sma_mayer_cycle"]
    assert no_skip.skipped_count == 0
    assert sma.trades[0]["regime"] == "ahr999_accumulation"
    assert sma.last_signal_metrics["ahr999_metric"] == "ahr999_sma"
    assert sma.last_signal_metrics["ahr999_selected"] == 0.7


def test_ibit_btc_precomputed_helper_variants_use_percentile_and_slope() -> None:
    dates = pd.date_range("2025-01-02", periods=120, freq="B")
    signals = pd.DataFrame(
        {
            "ahr999": [1.5 for _ in dates],
            "ahr999_365d_percentile": [0.40 for _ in dates],
            "ahr999_30d_slope": [-0.01 for _ in dates],
        },
        index=dates,
    )
    ibit = pd.Series([50.0 + i * 0.02 for i in range(len(dates))], index=dates)

    result = compare_smart_dca_candidates(
        signal_prices=signals,
        trade_prices=ibit,
        candidate_set="ibit_btc_ahr999_helper_precomputed_variants",
        monthly_contribution_usd=500.0,
    )

    assert set(result) == {
        "fixed",
        "ibit_btc_precomputed_ahr999_cycle",
        "ibit_btc_precomputed_ahr999_percentile_cycle",
        "ibit_btc_precomputed_ahr999_guarded_cycle",
    }
    production_equivalent = result["ibit_btc_precomputed_ahr999_cycle"]
    percentile = result["ibit_btc_precomputed_ahr999_percentile_cycle"]
    guarded = result["ibit_btc_precomputed_ahr999_guarded_cycle"]
    assert production_equivalent.skipped_count > 0
    assert percentile.trades[0]["regime"] == "ahr999_percentile_dca"
    assert percentile.trades[0]["multiplier"] == 1.5
    assert percentile.last_signal_metrics["ahr999_metric"] == "ahr999_365d_percentile"
    assert guarded.trades[0]["regime"] == "ahr999_expensive_guarded_dca"
    assert guarded.trades[0]["multiplier"] == 1.0
    assert guarded.last_signal_metrics["ahr999_30d_slope"] == -0.01


def test_nasdaq_external_precomputed_candidates_use_context_columns() -> None:
    dates = pd.date_range("2025-01-02", periods=280, freq="B")
    prices = pd.Series([100.0 + i * 0.05 for i in range(len(dates))], index=dates)
    signals = pd.DataFrame(
        {
            "QQQ": prices,
            "SPY": prices * 0.95,
            "cape_percentile": [0.90 for _ in dates],
            "vix_percentile": [0.85 for _ in dates],
            "breadth_above_sma200_pct": [0.35 for _ in dates],
        },
        index=dates,
    )
    trade_prices = pd.Series(
        [50.0 + i * 0.03 for i in range(len(dates))],
        index=dates,
    )

    result = compare_smart_dca_candidates(
        signal_prices=signals,
        trade_prices=trade_prices,
        candidate_set="nasdaq_sp500_external_precomputed_variants",
        monthly_contribution_usd=500.0,
    )

    assert set(result) == {
        "fixed",
        "nasdaq_sp500_price_no_skip",
        "nasdaq_sp500_precomputed_valuation_guard",
        "nasdaq_sp500_precomputed_vol_breadth_stress",
    }
    valuation = result["nasdaq_sp500_precomputed_valuation_guard"]
    stress = result["nasdaq_sp500_precomputed_vol_breadth_stress"]
    assert valuation.trades[0]["regime"] == "valuation_expensive_guard"
    assert valuation.trades[0]["multiplier"] == 0.75
    assert valuation.last_signal_metrics["cape_percentile"] == 0.90
    assert stress.trades[0]["regime"] == "volatility_breadth_stress_add"
    assert stress.trades[0]["multiplier"] == 1.25
    assert stress.last_signal_metrics["vix_percentile"] == 0.85
    assert stress.last_signal_metrics["breadth_above_sma200_pct"] == 0.35


def test_nasdaq_cape_vix_precomputed_candidate_runs_without_breadth() -> None:
    dates = pd.date_range("2025-01-02", periods=280, freq="B")
    signals = pd.DataFrame(
        {
            "cape_percentile": [0.70 for _ in dates],
            "vix_percentile": [0.85 for _ in dates],
        },
        index=dates,
    )
    trade_prices = pd.Series(
        [50.0 + i * 0.03 for i in range(len(dates))],
        index=dates,
    )

    result = compare_smart_dca_candidates(
        signal_prices=signals,
        trade_prices=trade_prices,
        candidate_set="nasdaq_sp500_cape_vix_precomputed_variants",
        monthly_contribution_usd=500.0,
    )

    assert set(result) == {"fixed", "nasdaq_sp500_precomputed_cape_vix_guard"}
    candidate = result["nasdaq_sp500_precomputed_cape_vix_guard"]
    assert candidate.trades[0]["regime"] == "cape_vix_volatility_stress_add"
    assert candidate.trades[0]["multiplier"] == 1.25
    assert candidate.last_signal_metrics["cape_percentile"] == 0.70
    assert candidate.last_signal_metrics["vix_percentile"] == 0.85


def _candidate_parameters(name: str) -> dict[str, float]:
    return {
        str(row["parameter_name"]): float(row["parameter_value"])
        for row in candidate_specs_to_rows((name,))
    }


def _strategy_default_parameters(function) -> dict[str, object]:
    return {
        name: parameter.default
        for name, parameter in inspect.signature(function).parameters.items()
        if parameter.default is not inspect.Parameter.empty
    }


def test_production_equivalent_candidates_match_strategy_defaults() -> None:
    nasdaq_candidate = production_equivalent_candidate_name(
        NASDAQ_SP500_SMART_DCA_PROFILE
    )
    ibit_candidate = production_equivalent_candidate_name(IBIT_SMART_DCA_PROFILE)

    assert nasdaq_candidate == "nasdaq_sp500_price_no_skip"
    assert ibit_candidate == "ibit_btc_precomputed_ahr999_cycle"

    nasdaq_config = _strategy_default_parameters(build_nasdaq_sp500_smart_dca_plan)
    nasdaq_params = _candidate_parameters(nasdaq_candidate)
    for key in (
        "mild_drawdown_threshold",
        "deep_drawdown_threshold",
        "severe_drawdown_threshold",
        "mild_discount_gap",
        "deep_discount_gap",
        "expensive_gap",
        "very_expensive_gap",
        "shallow_drawdown_threshold",
        "overbought_rsi",
        "base_multiplier",
        "mild_pullback_multiplier",
        "deep_pullback_multiplier",
        "severe_pullback_multiplier",
        "expensive_multiplier",
        "very_expensive_multiplier",
    ):
        assert nasdaq_params[key] == float(nasdaq_config[key])

    ibit_config = _strategy_default_parameters(build_ibit_smart_dca_plan)
    ibit_params = _candidate_parameters(ibit_candidate)
    for key in (
        "ahr999_bottom_threshold",
        "ahr999_accumulation_threshold",
        "ahr999_dca_threshold",
        "base_multiplier",
        "ahr999_bottom_multiplier",
        "ahr999_accumulation_multiplier",
        "ahr999_dca_multiplier",
        "ahr999_expensive_multiplier",
    ):
        assert ibit_params[key] == float(ibit_config[key])
    assert "mayer_discount_threshold" not in ibit_params

    summaries = candidate_summaries_to_rows((nasdaq_candidate, ibit_candidate))
    assert {row["candidate_role"] for row in summaries} == {"production_equivalent"}
    assert {
        row["production_equivalent_profile"]
        for row in summaries
    } == {NASDAQ_SP500_SMART_DCA_PROFILE, IBIT_SMART_DCA_PROFILE}


def test_precomputed_candidates_name_compatible_signal_consumers() -> None:
    assert candidate_set_signal_consumers("nasdaq_sp500_price_variants") == ()
    assert candidate_set_signal_consumers(
        "nasdaq_sp500_external_precomputed_variants"
    ) == ("research:nasdaq_sp500_external_context_precomputed",)
    assert candidate_set_signal_source_modes(
        "nasdaq_sp500_external_precomputed_variants"
    ) == (
        "external_precomputed_us_equity_context",
        "market_history_price_indicators",
    )
    assert candidate_set_signal_consumers(
        "nasdaq_sp500_cape_vix_precomputed_variants"
    ) == ("research:nasdaq_sp500_cape_vix_external_context_precomputed",)
    assert candidate_set_signal_source_modes(
        "nasdaq_sp500_cape_vix_precomputed_variants"
    ) == ("external_precomputed_us_equity_context",)
    assert candidate_set_signal_consumers("ibit_btc_ahr999_precomputed") == (
        "research:ibit_btc_ahr999_precomputed",
        "us_equity:ibit_smart_dca",
    )
    assert candidate_set_signal_consumers("ibit_btc_ahr999_precomputed_variants") == (
        "research:ibit_btc_ahr999_mayer_precomputed",
        "research:ibit_btc_ahr999_mayer_precomputed_variants",
        "research:ibit_btc_ahr999_precomputed",
        "us_equity:ibit_smart_dca",
    )
    assert candidate_set_signal_consumers("ibit_btc_ahr999_helper_precomputed_variants") == (
        "research:ibit_btc_ahr999_helper_precomputed_variants",
        "research:ibit_btc_ahr999_precomputed",
        "us_equity:ibit_smart_dca",
    )
    assert candidate_signal_consumers("nasdaq_sp500_price_no_skip") == ()
    assert candidate_signal_consumers("ibit_btc_precomputed_ahr999_cycle") == (
        "us_equity:ibit_smart_dca",
        "research:ibit_btc_ahr999_precomputed",
    )
    assert candidate_signal_consumers(
        "ibit_btc_precomputed_ahr999_mayer_no_skip_cycle"
    ) == ("research:ibit_btc_ahr999_mayer_precomputed",)
    assert candidate_signal_consumers(
        "ibit_btc_precomputed_ahr999_percentile_cycle"
    ) == ("research:ibit_btc_ahr999_helper_precomputed_variants",)
    assert candidate_signal_consumers("ibit_btc_precomputed_ahr999_guarded_cycle") == (
        "research:ibit_btc_ahr999_helper_precomputed_variants",
    )

    rows = candidate_summaries_to_rows(
        (
            "ibit_btc_precomputed_ahr999_cycle",
            "ibit_btc_precomputed_ahr999_mayer_cycle",
            "ibit_btc_precomputed_ahr999_sma_mayer_cycle",
        )
    )

    for row in rows:
        compatible_consumers = tuple(
            consumer
            for consumer in str(row["compatible_signal_consumers"]).split(",")
            if consumer
        )
        assert compatible_consumers
        signal_symbols = set(str(row["signal_symbols"]).split(","))
        for consumer in compatible_consumers:
            required_fields = required_indicator_fields_for_consumer(consumer)
            assert consumer.startswith(("us_equity:", "research:"))
            for fields in required_fields.values():
                assert signal_symbols.issubset(set(fields))

    helper_summary = candidate_summaries_to_rows(
        ("ibit_btc_precomputed_ahr999_percentile_cycle",)
    )[0]
    assert helper_summary["compatible_signal_consumers"] == (
        "research:ibit_btc_ahr999_helper_precomputed_variants"
    )
    assert helper_summary["signal_symbols"] == "ahr999_365d_percentile"
    assert required_indicator_fields_for_consumer(
        "research:ibit_btc_ahr999_helper_precomputed_variants"
    ) == {
        "BTC-USD": (
            "ahr999",
            "ahr999_365d_percentile",
            "ahr999_30d_slope",
        )
    }

    nasdaq_external_summary = candidate_summaries_to_rows(
        ("nasdaq_sp500_precomputed_valuation_guard",)
    )[0]
    assert nasdaq_external_summary["compatible_signal_consumers"] == (
        "research:nasdaq_sp500_external_context_precomputed"
    )
    assert nasdaq_external_summary["signal_source_mode"] == (
        "external_precomputed_us_equity_context"
    )
    assert nasdaq_external_summary["open_parameter_search"] is False
    assert required_indicator_fields_for_consumer(
        "research:nasdaq_sp500_external_context_precomputed"
    ) == {
        "US-EQUITY-CONTEXT": (
            "breadth_above_sma200_pct",
            "cape_percentile",
            "vix_percentile",
        )
    }
    cape_vix_summary = candidate_summaries_to_rows(
        ("nasdaq_sp500_precomputed_cape_vix_guard",)
    )[0]
    assert cape_vix_summary["compatible_signal_consumers"] == (
        "research:nasdaq_sp500_cape_vix_external_context_precomputed"
    )
    assert cape_vix_summary["signal_symbols"] == "cape_percentile,vix_percentile"
    assert required_indicator_fields_for_consumer(
        "research:nasdaq_sp500_cape_vix_external_context_precomputed"
    ) == {
        "US-EQUITY-CONTEXT": (
            "cape_percentile",
            "vix_percentile",
        )
    }


def test_ibit_production_equivalent_candidate_ignores_mayer_conflict() -> None:
    dates = pd.date_range("2025-01-02", periods=120, freq="B")
    signals = pd.DataFrame(
        {
            "ahr999": [1.0 for _ in dates],
            "ahr999_sma": [1.0 for _ in dates],
            "mayer_multiple": [0.50 for _ in dates],
        },
        index=dates,
    )
    ibit = pd.Series([50.0 + i * 0.02 for i in range(len(dates))], index=dates)

    result = compare_smart_dca_candidates(
        signal_prices=signals,
        trade_prices=ibit,
        candidate_set="ibit_btc_ahr999_precomputed_variants",
        monthly_contribution_usd=500.0,
    )

    production_equivalent = result["ibit_btc_precomputed_ahr999_cycle"]
    mayer_variant = result["ibit_btc_precomputed_ahr999_mayer_cycle"]

    assert production_equivalent.trades[0]["regime"] == "ahr999_dca"
    assert production_equivalent.trades[0]["multiplier"] == 1.5
    assert production_equivalent.last_signal_metrics["ahr999_selected"] == 1.0
    assert mayer_variant.trades[0]["regime"] == "ahr999_bottom"
    assert mayer_variant.trades[0]["multiplier"] == 3.0


def test_candidate_universe_is_named_and_bounded() -> None:
    assert available_candidate_names() == (
        "nasdaq_sp500_price_defensive",
        "nasdaq_sp500_price_no_skip",
        "nasdaq_sp500_precomputed_valuation_guard",
        "nasdaq_sp500_precomputed_vol_breadth_stress",
        "nasdaq_sp500_precomputed_cape_vix_guard",
        "ibit_btc_ahr999_cycle",
        "ibit_btc_ahr999_mayer_cycle",
        "ibit_btc_ahr999_mayer_no_skip_cycle",
        "ibit_btc_ahr999_sma_mayer_cycle",
        "ibit_btc_precomputed_ahr999_cycle",
        "ibit_btc_precomputed_ahr999_mayer_cycle",
        "ibit_btc_precomputed_ahr999_mayer_no_skip_cycle",
        "ibit_btc_precomputed_ahr999_sma_mayer_cycle",
        "ibit_btc_precomputed_ahr999_percentile_cycle",
        "ibit_btc_precomputed_ahr999_guarded_cycle",
    )
