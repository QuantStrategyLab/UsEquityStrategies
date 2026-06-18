from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from us_equity_strategies.backtests.smart_dca_research_cli import main


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
            "nasdaq_sp500_price_variants",
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
    assert summary["candidate_set"] == "nasdaq_sp500_price_variants"
    assert summary["monthly_contribution_usd_values"] == [500.0, 1000.0]
    assert summary["cadences"] == ["weekly", "monthly", "quarterly"]
    assert summary["start_dates"] == ["2025-01-02", "2025-04-01"]
    assert summary["metadata"]["research_config"]["candidate_set"] == "nasdaq_sp500_price_variants"
    assert summary["metadata"]["research_config"]["min_review_scenarios"] == 3
    assert summary["metadata"]["research_config"]["cadences"] == [
        "weekly",
        "monthly",
        "quarterly",
    ]
    assert summary["metadata"]["input_artifacts"]["signal_csv"]["sha256"]
    assert summary["scenario_index"] == str(output_dir / "scenario_index.csv")
    assert summary["review_decision"] == str(output_dir / "review_decision.json")
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
    assert (output_dir / "selection_summary.csv").exists()
    assert (output_dir / "scenario_coverage.csv").exists()
    assert (output_dir / "review_decision.json").exists()
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
    selection_summary = (output_dir / "selection_summary.csv").read_text(
        encoding="utf-8"
    )
    scenario_coverage = (output_dir / "scenario_coverage.csv").read_text(
        encoding="utf-8"
    )
    scenario_manifest = json.loads((output_dir / "scenario_manifest.json").read_text(encoding="utf-8"))
    review_decision = json.loads((output_dir / "review_decision.json").read_text(encoding="utf-8"))
    assert "nasdaq_sp500_price_defensive" in scenario_index
    assert "nasdaq_sp500_price_no_skip" in scenario_index
    assert "pass_rate" in robustness_summary
    assert "recommendation_status" in selection_summary
    assert "selected_name" in selection_summary
    assert "min_review_scenarios" in selection_summary
    assert "selected_candidate_definition_sha256" in selection_summary
    assert "selection_policy" in selection_summary
    assert "effect_size_policy" in selection_summary
    assert "selected_effect_size_gate_passed" in selection_summary
    assert "min_effect_median_relative_terminal_value_pct" in selection_summary
    assert "matrix_coverage_gate_passed" in selection_summary
    assert "matrix_coverage_status" in selection_summary
    assert "matrix_scenario_count" in selection_summary
    assert "matrix_candidate_set_consistent" in selection_summary
    assert "coverage_gate_passed" in scenario_coverage
    assert "ready_for_selection_review" in scenario_coverage
    assert review_decision["artifact_type"] == "smart_dca_review_decision"
    assert review_decision["selection_policy"] == "fixed_preset_no_parameter_search"
    assert review_decision["effect_size_policy"] == (
        "fixed_minimum_effect_no_parameter_search"
    )
    assert review_decision["effect_size_thresholds"][
        "min_median_relative_terminal_value_pct"
    ] == 1.0
    assert review_decision["matrix_coverage_gate_passed"] is True
    assert review_decision["selection_gate_summary"][
        "matrix_coverage_gate_passed"
    ] is True
    assert review_decision["selection_count"] == 1
    assert review_decision["selections"][0]["selected_candidate_definition_sha256"]
    assert "review_status" in robustness_summary
    assert "weakest_scenario" in robustness_summary
    assert "max_terminal_cash_ratio_pct" in robustness_summary
    assert scenario_manifest["min_review_scenarios"] == 3
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
    assert "review_decision.json" in {item["path"] for item in scenario_manifest["files"]}
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
    assert "nasdaq_sp500_price_no_skip" in candidate_specs
    assert "base_multiplier" in candidate_specs
    assert "open_parameter_search" in candidate_summary
    assert "unique_multiplier_count" in candidate_summary
    assert "candidate_definition_sha256" in candidate_summary


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


def test_smart_dca_research_cli_can_use_precomputed_ibit_cycle_columns(
    tmp_path,
    capsys,
) -> None:
    dates = pd.date_range("2025-01-02", periods=120, freq="B")
    signal_csv = tmp_path / "btc_cycle.csv"
    signal_manifest = tmp_path / "btc_cycle.manifest.json"
    trade_csv = tmp_path / "ibit.csv"
    output_dir = tmp_path / "ibit-precomputed-artifacts"

    pd.DataFrame(
        {
            "date": dates.date,
            "ahr999": [1.5 for _ in dates],
            "ahr999_sma": [1.4 for _ in dates],
            "mayer_multiple": [2.5 for _ in dates],
            "unused": [1.0 for _ in dates],
        }
    ).to_csv(signal_csv, index=False)
    pd.DataFrame(
        {
            "date": dates.date,
            "ibit_close": [50.0 + index * 0.02 for index in range(len(dates))],
        }
    ).to_csv(trade_csv, index=False)
    signal_manifest.write_text(
        json.dumps(
            {
                "schema_version": "research_export.v1",
                "artifact_type": "btc_cycle_research_csv",
                "transform": "crypto.btc.ahr999_mayer.v1",
                "source_version": "0.1.0",
                "as_of": "2025-06-18",
                "min_history": 200,
                "row_count": len(dates),
                "first_date": str(dates[0].date()),
                "last_date": str(dates[-1].date()),
                "columns": ["date", "ahr999", "ahr999_sma", "mayer_multiple", "unused"],
                "output_csv": {
                    "path": str(signal_csv),
                    "sha256": _sha256_file(signal_csv),
                    "size_bytes": signal_csv.stat().st_size,
                },
            }
        ),
        encoding="utf-8",
    )

    result = main(
        [
            "--signal-csv",
            str(signal_csv),
            "--trade-csv",
            str(trade_csv),
            "--signal-manifest",
            str(signal_manifest),
            "--output-dir",
            str(output_dir),
            "--candidate-set",
            "ibit_btc_ahr999_mayer_precomputed_variants",
            "--signal-columns",
            "ahr999,ahr999_sma,mayer_multiple",
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
    decision_log = (output_dir / "monthly_day_15" / "decision_log.csv").read_text(
        encoding="utf-8"
    )
    candidate_summary = (
        output_dir / "monthly_day_15" / "candidate_summary.csv"
    ).read_text(encoding="utf-8")
    assert "ibit_btc_precomputed_ahr999_mayer_cycle" in metrics
    assert "ibit_btc_precomputed_ahr999_mayer_no_skip_cycle" in metrics
    assert "ibit_btc_precomputed_ahr999_sma_mayer_cycle" in metrics
    assert "precomputed_derived_indicators" in decision_log
    assert "precomputed_ahr999_mayer" in candidate_summary
    summary = json.loads(capsys.readouterr().out)
    signal_manifest_record = summary["metadata"]["input_artifacts"]["signal_manifest"]
    assert signal_manifest_record["schema_version"] == "research_export.v1"
    assert signal_manifest_record["transform"] == "crypto.btc.ahr999_mayer.v1"
    assert signal_manifest_record["columns"] == [
        "date",
        "ahr999",
        "ahr999_sma",
        "mayer_multiple",
        "unused",
    ]
    assert signal_manifest_record["linked_csv_sha256_verified"] is True
    scenario_manifest = json.loads(
        (output_dir / "scenario_manifest.json").read_text(encoding="utf-8")
    )
    assert (
        scenario_manifest["metadata"]["input_artifacts"]["signal_manifest"][
            "linked_csv_sha256"
        ]
        == _sha256_file(signal_csv)
    )


def test_smart_dca_research_cli_rejects_signal_manifest_hash_mismatch(
    tmp_path,
    capsys,
) -> None:
    dates = pd.date_range("2025-01-02", periods=120, freq="B")
    signal_csv = tmp_path / "btc_cycle.csv"
    signal_manifest = tmp_path / "btc_cycle.manifest.json"
    trade_csv = tmp_path / "ibit.csv"
    output_dir = tmp_path / "ibit-precomputed-artifacts"

    pd.DataFrame(
        {
            "date": dates.date,
            "ahr999": [1.5 for _ in dates],
            "mayer_multiple": [2.5 for _ in dates],
        }
    ).to_csv(signal_csv, index=False)
    pd.DataFrame(
        {
            "date": dates.date,
            "ibit_close": [50.0 + index * 0.02 for index in range(len(dates))],
        }
    ).to_csv(trade_csv, index=False)
    signal_manifest.write_text(
        json.dumps(
            {
                "schema_version": "research_export.v1",
                "output_csv": {
                    "path": str(signal_csv),
                    "sha256": "0" * 64,
                },
            }
        ),
        encoding="utf-8",
    )

    result = main(
        [
            "--signal-csv",
            str(signal_csv),
            "--trade-csv",
            str(trade_csv),
            "--signal-manifest",
            str(signal_manifest),
            "--output-dir",
            str(output_dir),
            "--candidate-set",
            "ibit_btc_ahr999_mayer_precomputed",
            "--signal-columns",
            "ahr999,mayer_multiple",
            "--trade-column",
            "ibit_close",
            "--execution-days",
            "15",
        ]
    )

    assert result == 2
    assert "output_csv.sha256 mismatch" in capsys.readouterr().err


def test_smart_dca_research_cli_rejects_sensitive_manifest_fields(
    tmp_path,
    capsys,
) -> None:
    dates = pd.date_range("2025-01-02", periods=120, freq="B")
    signal_csv = tmp_path / "btc_cycle.csv"
    signal_manifest = tmp_path / "btc_cycle.manifest.json"
    trade_csv = tmp_path / "ibit.csv"
    output_dir = tmp_path / "ibit-precomputed-artifacts"

    pd.DataFrame(
        {
            "date": dates.date,
            "ahr999": [1.5 for _ in dates],
            "mayer_multiple": [2.5 for _ in dates],
        }
    ).to_csv(signal_csv, index=False)
    pd.DataFrame(
        {
            "date": dates.date,
            "ibit_close": [50.0 + index * 0.02 for index in range(len(dates))],
        }
    ).to_csv(trade_csv, index=False)
    signal_manifest.write_text(
        json.dumps(
            {
                "schema_version": "research_export.v1",
                "output_csv": {"sha256": _sha256_file(signal_csv)},
                "provenance": {"signed_url": "https://example.invalid/private.csv"},
            }
        ),
        encoding="utf-8",
    )

    result = main(
        [
            "--signal-csv",
            str(signal_csv),
            "--trade-csv",
            str(trade_csv),
            "--signal-manifest",
            str(signal_manifest),
            "--output-dir",
            str(output_dir),
            "--candidate-set",
            "ibit_btc_ahr999_mayer_precomputed",
            "--signal-columns",
            "ahr999,mayer_multiple",
            "--trade-column",
            "ibit_close",
            "--execution-days",
            "15",
        ]
    )

    assert result == 2
    assert "sensitive field" in capsys.readouterr().err
