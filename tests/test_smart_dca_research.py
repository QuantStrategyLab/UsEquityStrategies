import json

import pandas as pd

from us_equity_strategies.backtests.smart_dca_research import (
    available_candidate_names,
    candidate_specs_to_rows,
    compare_execution_day_contribution_scenarios,
    compare_monthly_execution_day_scenarios,
    compare_smart_dca_candidates,
    evaluate_candidate_results,
    results_to_cash_flow_rows,
    results_to_decision_log_rows,
    results_to_equity_curve_rows,
    results_to_metrics_rows,
    scenario_results_to_robustness_rows,
    summarize_candidate_evaluations,
    write_research_artifacts,
    write_scenario_research_artifacts,
)


def _series(values, *, start: str = "2024-01-02") -> pd.Series:
    index = pd.date_range(start, periods=len(values), freq="B")
    return pd.Series(values, index=index, dtype=float)


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

    artifact_paths = write_research_artifacts(tmp_path, result, evaluations=evaluations)
    assert set(artifact_paths) == {
        "metrics",
        "evaluation_summary",
        "decision_log",
        "equity_curve",
        "cash_flows",
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
    assert "base_multiplier" in artifact_paths["candidate_specs"].read_text(encoding="utf-8")
    run_manifest = json.loads(artifact_paths["run_manifest"].read_text(encoding="utf-8"))
    assert run_manifest["schema_version"] == "smart_dca_research_artifacts.v1"
    assert {item["path"] for item in run_manifest["files"]} == {
        "metrics.csv",
        "evaluation_summary.csv",
        "decision_log.csv",
        "equity_curve.csv",
        "cash_flows.csv",
        "candidate_specs.csv",
    }


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
    assert "scenario_manifest" in artifact_paths
    scenario_index = artifact_paths["scenario_index"].read_text(encoding="utf-8")
    robustness_summary = artifact_paths["robustness_summary"].read_text(encoding="utf-8")
    assert "monthly_day_1" in scenario_index
    assert "monthly_day_25" in scenario_index
    assert "pass_rate" in robustness_summary
    assert "review_status" in robustness_summary
    assert "weakest_scenario" in robustness_summary
    assert "median_money_weighted_return_pct" in robustness_summary
    assert "max_terminal_cash_ratio_pct" in robustness_summary
    assert "worst_relative_value_gap_after_1y_pct" in robustness_summary
    assert (tmp_path / "monthly_day_1" / "metrics.csv").exists()
    assert (tmp_path / "monthly_day_25" / "decision_log.csv").exists()
    assert (tmp_path / "monthly_day_25" / "equity_curve.csv").exists()
    assert (tmp_path / "monthly_day_25" / "cash_flows.csv").exists()
    assert (tmp_path / "monthly_day_25" / "candidate_specs.csv").exists()
    scenario_manifest = json.loads(artifact_paths["scenario_manifest"].read_text(encoding="utf-8"))
    assert scenario_manifest["artifact_type"] == "smart_dca_research_scenario_matrix"
    assert scenario_manifest["metadata"]["research_config"]["candidate_set"] == "nasdaq_sp500_price"
    assert "scenario_index.csv" in {item["path"] for item in scenario_manifest["files"]}
    assert "robustness_summary.csv" in {item["path"] for item in scenario_manifest["files"]}


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
    assert smart.last_signal_metrics["ahr999"] > 1.2
    assert smart.last_signal_metrics["mayer_multiple"] == 1.0


def test_candidate_universe_is_named_and_bounded() -> None:
    assert available_candidate_names() == (
        "nasdaq_sp500_price_defensive",
        "ibit_btc_ahr999_mayer_cycle",
    )
