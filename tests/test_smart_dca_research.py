import json

import pandas as pd

from us_equity_strategies.backtests.smart_dca_research import (
    available_candidate_names,
    compare_monthly_execution_day_scenarios,
    compare_smart_dca_candidates,
    evaluate_candidate_results,
    results_to_decision_log_rows,
    results_to_metrics_rows,
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

    decision_rows = results_to_decision_log_rows(result)
    assert any(row["action"] == "buy" and row["name"] == "fixed" for row in decision_rows)
    assert any(
        row["action"] == "skip"
        and row["name"] == "nasdaq_sp500_price_defensive"
        and row["skip_reason"] == "valuation_too_expensive"
        for row in decision_rows
    )

    artifact_paths = write_research_artifacts(tmp_path, result, evaluations=evaluations)
    assert set(artifact_paths) == {"metrics", "evaluation_summary", "decision_log", "run_manifest"}
    assert "passed_promotion_gate" in artifact_paths["metrics"].read_text(encoding="utf-8")
    assert "skip_rate_too_high_without_drawdown_improvement" in artifact_paths[
        "evaluation_summary"
    ].read_text(encoding="utf-8")
    assert "valuation_too_expensive" in artifact_paths["decision_log"].read_text(encoding="utf-8")
    run_manifest = json.loads(artifact_paths["run_manifest"].read_text(encoding="utf-8"))
    assert run_manifest["schema_version"] == "smart_dca_research_artifacts.v1"
    assert {item["path"] for item in run_manifest["files"]} == {
        "metrics.csv",
        "evaluation_summary.csv",
        "decision_log.csv",
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

    artifact_paths = write_scenario_research_artifacts(tmp_path, scenarios)
    assert "scenario_index" in artifact_paths
    assert "scenario_manifest" in artifact_paths
    scenario_index = artifact_paths["scenario_index"].read_text(encoding="utf-8")
    assert "monthly_day_1" in scenario_index
    assert "monthly_day_25" in scenario_index
    assert (tmp_path / "monthly_day_1" / "metrics.csv").exists()
    assert (tmp_path / "monthly_day_25" / "decision_log.csv").exists()
    scenario_manifest = json.loads(artifact_paths["scenario_manifest"].read_text(encoding="utf-8"))
    assert scenario_manifest["artifact_type"] == "smart_dca_research_scenario_matrix"
    assert "scenario_index.csv" in {item["path"] for item in scenario_manifest["files"]}


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
