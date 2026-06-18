from __future__ import annotations

import json

import pandas as pd

from us_equity_strategies.backtests.smart_dca_research_cli import main


def test_smart_dca_research_cli_writes_scenario_artifacts(tmp_path, capsys) -> None:
    dates = pd.date_range("2024-01-02", periods=360, freq="B")
    prices = pd.Series([100.0 + index * 0.15 for index in range(len(dates))])
    signal_csv = tmp_path / "signals.csv"
    trade_csv = tmp_path / "trade.csv"
    output_dir = tmp_path / "artifacts"

    pd.DataFrame(
        {
            "date": dates.date,
            "QQQ": prices,
            "SPY": prices * 0.94,
        }
    ).to_csv(signal_csv, index=False)
    pd.DataFrame(
        {
            "date": dates.date,
            "close": prices * 0.50,
        }
    ).to_csv(trade_csv, index=False)

    result = main(
        [
            "--signal-csv",
            str(signal_csv),
            "--trade-csv",
            str(trade_csv),
            "--output-dir",
            str(output_dir),
            "--candidate-set",
            "nasdaq_sp500_price",
            "--execution-days",
            "1,25",
            "--monthly-contribution-usd",
            "1000",
            "--monthly-contribution-usd-values",
            "500,1000",
            "--cadences",
            "weekly,monthly,quarterly",
            "--start-dates",
            "2025-01-02,2025-04-01",
            "--pretty",
        ]
    )

    assert result == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["candidate_set"] == "nasdaq_sp500_price"
    assert summary["monthly_contribution_usd_values"] == [500.0, 1000.0]
    assert summary["cadences"] == ["weekly", "monthly", "quarterly"]
    assert summary["start_dates"] == ["2025-01-02", "2025-04-01"]
    assert summary["metadata"]["research_config"]["candidate_set"] == "nasdaq_sp500_price"
    assert summary["metadata"]["research_config"]["cadences"] == [
        "weekly",
        "monthly",
        "quarterly",
    ]
    assert summary["metadata"]["input_artifacts"]["signal_csv"]["sha256"]
    assert summary["scenario_index"] == str(output_dir / "scenario_index.csv")
    assert summary["scenario_manifest"] == str(output_dir / "scenario_manifest.json")
    assert summary["artifacts"]["robustness_summary"] == str(
        output_dir / "robustness_summary.csv"
    )
    assert (
        output_dir
        / "monthly_day_1_contribution_usd_500_start_2025_01_02"
        / "metrics.csv"
    ).exists()
    assert (
        output_dir
        / "monthly_day_25_contribution_usd_1000_start_2025_04_01"
        / "decision_log.csv"
    ).exists()
    assert (
        output_dir
        / "monthly_day_25_contribution_usd_1000_start_2025_04_01"
        / "equity_curve.csv"
    ).exists()
    assert (
        output_dir
        / "monthly_day_25_contribution_usd_1000_start_2025_04_01"
        / "cash_flows.csv"
    ).exists()
    assert (
        output_dir / "weekly_contribution_usd_500_start_2025_01_02" / "metrics.csv"
    ).exists()
    assert (
        output_dir
        / "weekly_contribution_usd_500_start_2025_01_02"
        / "candidate_summary.csv"
    ).exists()
    assert (
        output_dir
        / "weekly_contribution_usd_500_start_2025_01_02"
        / "candidate_specs.csv"
    ).exists()
    assert (
        output_dir
        / "quarterly_day_25_contribution_usd_1000_start_2025_04_01"
        / "metrics.csv"
    ).exists()
    scenario_index = (output_dir / "scenario_index.csv").read_text(encoding="utf-8")
    robustness_summary = (output_dir / "robustness_summary.csv").read_text(
        encoding="utf-8"
    )
    scenario_manifest = json.loads((output_dir / "scenario_manifest.json").read_text(encoding="utf-8"))
    assert "nasdaq_sp500_price_defensive" in scenario_index
    assert "pass_rate" in robustness_summary
    assert "review_status" in robustness_summary
    assert "weakest_scenario" in robustness_summary
    assert "max_terminal_cash_ratio_pct" in robustness_summary
    assert scenario_manifest["metadata"]["research_config"]["execution_days"] == [1, 25]
    assert scenario_manifest["metadata"]["research_config"]["cadences"] == [
        "weekly",
        "monthly",
        "quarterly",
    ]
    assert scenario_manifest["metadata"]["research_config"]["monthly_contribution_usd_values"] == [
        500.0,
        1000.0,
    ]
    assert scenario_manifest["metadata"]["input_artifacts"]["trade_csv"]["size_bytes"] > 0
    metrics = (
        output_dir / "monthly_day_1_contribution_usd_500_start_2025_01_02" / "metrics.csv"
    ).read_text(encoding="utf-8")
    assert "worst_relative_value_gap_after_1y_pct" in metrics
    assert "money_weighted_return_pct" in metrics
    assert "average_cash_ratio_pct" in metrics
    candidate_specs = (
        output_dir
        / "weekly_contribution_usd_500_start_2025_01_02"
        / "candidate_specs.csv"
    ).read_text(encoding="utf-8")
    candidate_summary = (
        output_dir
        / "weekly_contribution_usd_500_start_2025_01_02"
        / "candidate_summary.csv"
    ).read_text(encoding="utf-8")
    assert "nasdaq_sp500_price_defensive" in candidate_specs
    assert "base_multiplier" in candidate_specs
    assert "open_parameter_search" in candidate_summary
    assert "unique_multiplier_count" in candidate_summary


def test_smart_dca_research_cli_can_select_single_signal_column(tmp_path) -> None:
    dates = pd.date_range("2025-01-02", periods=280, freq="B")
    signal_csv = tmp_path / "btc.csv"
    trade_csv = tmp_path / "ibit.csv"
    output_dir = tmp_path / "ibit-artifacts"

    pd.DataFrame(
        {
            "date": dates.date,
            "BTC-USD": [250_000.0 for _ in dates],
            "unused": [1.0 for _ in dates],
        }
    ).to_csv(signal_csv, index=False)
    pd.DataFrame(
        {
            "date": dates.date,
            "ibit_close": [50.0 + index * 0.02 for index in range(len(dates))],
        }
    ).to_csv(trade_csv, index=False)

    result = main(
        [
            "--signal-csv",
            str(signal_csv),
            "--trade-csv",
            str(trade_csv),
            "--output-dir",
            str(output_dir),
            "--candidate-set",
            "ibit_btc_ahr999_mayer_price",
            "--signal-columns",
            "BTC-USD",
            "--trade-column",
            "ibit_close",
            "--execution-days",
            "15",
            "--monthly-contribution-usd",
            "500",
        ]
    )

    assert result == 0
    metrics = (output_dir / "monthly_day_15" / "metrics.csv").read_text(encoding="utf-8")
    assert "ibit_btc_ahr999_mayer_cycle" in metrics
