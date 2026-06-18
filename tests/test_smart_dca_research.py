import json

import pandas as pd

from us_equity_strategies.backtests.smart_dca_research import (
    DcaResearchResult,
    available_candidate_names,
    candidate_summaries_to_rows,
    candidate_specs_to_rows,
    compare_execution_day_contribution_scenarios,
    compare_monthly_execution_day_scenarios,
    compare_smart_dca_candidates,
    evaluate_candidate_results,
    results_to_cash_flow_rows,
    results_to_decision_log_rows,
    results_to_equity_curve_rows,
    results_to_metrics_rows,
    scenario_results_to_coverage_rows,
    scenario_results_to_robustness_rows,
    scenario_results_to_selection_rows,
    summarize_candidate_evaluations,
    write_research_artifacts,
    write_scenario_research_artifacts,
)


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
) -> DcaResearchResult:
    return DcaResearchResult(
        name=name,
        terminal_value=terminal_value,
        cash=0.0,
        shares=terminal_value,
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
                "cash": 0.0,
                "drawdown_pct": 0.0,
            },
            {
                "date": "2026-01-01",
                "name": name,
                "equity": terminal_value,
                "cash": 0.0,
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
    assert summary_row["parameter_count"] == 15
    assert summary_row["open_parameter_search"] is False
    assert len(str(summary_row["candidate_definition_sha256"])) == 64

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
    assert "skip_rate_too_high_without_drawdown_improvement" in artifact_paths[
        "evaluation_summary"
    ].read_text(encoding="utf-8")
    assert "valuation_too_expensive" in artifact_paths["decision_log"].read_text(encoding="utf-8")
    assert "drawdown_pct" in artifact_paths["equity_curve"].read_text(encoding="utf-8")
    assert "terminal_value" in artifact_paths["cash_flows"].read_text(encoding="utf-8")
    assert "unique_multiplier_count" in artifact_paths["candidate_summary"].read_text(
        encoding="utf-8"
    )
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
    assert "scenario_manifest" in artifact_paths
    scenario_index = artifact_paths["scenario_index"].read_text(encoding="utf-8")
    robustness_summary = artifact_paths["robustness_summary"].read_text(encoding="utf-8")
    selection_summary = artifact_paths["selection_summary"].read_text(encoding="utf-8")
    scenario_coverage = artifact_paths["scenario_coverage"].read_text(encoding="utf-8")
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
    assert "worst_max_drawdown_delta_pct_points" in robustness_rows[0]
    assert selection_rows[0]["selection_group"] == "nasdaq_sp500_price"
    assert selection_rows[0]["selected_name"] == "nasdaq_sp500_price_defensive"
    assert selection_rows[0]["selected_family"] == "nasdaq_sp500_price"
    assert selection_rows[0]["selected_rule_type"] == "trend_drawdown"
    assert selection_rows[0]["selected_parameter_count"] == 15
    assert len(str(selection_rows[0]["selected_candidate_definition_sha256"])) == 64
    assert selection_rows[0]["selection_policy"] == "fixed_preset_no_parameter_search"
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
    assert selection_rows[0]["fixed_benchmark"] == "fixed"
    assert coverage_rows[0]["scenario_count"] == 4
    assert coverage_rows[0]["coverage_status"] == "ready_for_selection_review"
    assert coverage_rows[0]["failure_reasons"] == ""


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
    assert rows[0]["recommendation_reason"] == "no_candidate_passed_robustness_gate"
    assert "nasdaq_sp500_price_no_skip" in rows[0]["compared_candidates"]


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
    assert relaxed_rows[0]["recommendation_status"] == "promote_to_manual_review"
    assert relaxed_rows[0]["recommendation_reason"] == "selected_candidate_passed_all_scenarios"


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
    assert rows[0]["matrix_coverage_failure_reasons"] == "candidate_set_inconsistent"
    assert rows[0]["matrix_scenario_count"] == 3
    assert rows[0]["matrix_candidate_set_consistent"] is False
    assert rows[0]["matrix_fixed_benchmark_present_all"] is True
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
        "scenario_count_below_min_review_scenarios,candidate_set_inconsistent"
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


def test_candidate_universe_is_named_and_bounded() -> None:
    assert available_candidate_names() == (
        "nasdaq_sp500_price_defensive",
        "nasdaq_sp500_price_no_skip",
        "ibit_btc_ahr999_mayer_cycle",
        "ibit_btc_ahr999_mayer_no_skip_cycle",
        "ibit_btc_ahr999_sma_mayer_cycle",
        "ibit_btc_precomputed_ahr999_mayer_cycle",
        "ibit_btc_precomputed_ahr999_mayer_no_skip_cycle",
        "ibit_btc_precomputed_ahr999_sma_mayer_cycle",
    )
