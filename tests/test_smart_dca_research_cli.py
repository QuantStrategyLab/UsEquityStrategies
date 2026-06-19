from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from us_equity_strategies.backtests.smart_dca_research_cli import main


FIXTURE_SIGNAL_BUNDLE_PATH = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "signal_bundles"
    / "crypto"
    / "btc"
    / "derived_indicators"
    / "2026-06-19"
    / "signal_bundle.json"
)
FIXTURE_SIGNAL_BUNDLE_MANIFEST_PATH = FIXTURE_SIGNAL_BUNDLE_PATH.with_name(
    "manifest.json"
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_runtime_signal_bundle_manifest(tmp_path: Path) -> Path:
    bundle_dir = tmp_path / "runtime_bundle"
    bundle_dir.mkdir()
    bundle_path = bundle_dir / "signal_bundle.json"
    bundle_path.write_text(
        FIXTURE_SIGNAL_BUNDLE_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    manifest = json.loads(
        FIXTURE_SIGNAL_BUNDLE_MANIFEST_PATH.read_text(encoding="utf-8")
    )
    manifest["bundle_sha256"] = _sha256_file(bundle_path)
    manifest_path = bundle_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def _write_research_manifest(
    path: Path,
    *,
    csv_path: Path,
    artifact_type: str,
    transform: str,
    as_of: str,
    columns: list[str],
    first_date: str,
    last_date: str,
    row_count: int,
    quality_report_path: Path | None = None,
) -> None:
    manifest = {
        "schema_version": "research_export.v1",
        "artifact_type": artifact_type,
        "transform": transform,
        "source_version": "0.1.0",
        "as_of": as_of,
        "min_history": 1,
        "row_count": row_count,
        "first_date": first_date,
        "last_date": last_date,
        "columns": columns,
        "output_csv": {
            "path": str(csv_path),
            "sha256": _sha256_file(csv_path),
            "size_bytes": csv_path.stat().st_size,
        },
    }
    if quality_report_path is not None:
        manifest["quality_report"] = {
            "path": str(quality_report_path),
            "sha256": _sha256_file(quality_report_path),
            "size_bytes": quality_report_path.stat().st_size,
        }
    path.write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )


def _write_us_equity_quality_report(
    path: Path,
    *,
    as_of: str,
    public: bool = False,
    quality_status: str = "pass",
    failure_reasons: list[str] | None = None,
    warning_reasons: list[str] | None = None,
    input_sources: list[dict[str, object]] | None = None,
) -> None:
    schema_version = (
        "us_equity_public_context_availability_report.v1"
        if public
        else "us_equity_context_availability_report.v1"
    )
    artifact_type = (
        "us_equity_public_context_availability_report"
        if public
        else "us_equity_context_availability_report"
    )
    report = {
        "schema_version": schema_version,
        "artifact_type": artifact_type,
        "quality_status": quality_status,
        "failure_reasons": failure_reasons or [],
        "warning_reasons": warning_reasons or [],
        "as_of": as_of,
    }
    if input_sources is not None:
        report["input_sources"] = input_sources
    path.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")


def _public_context_quality_sources(
    *,
    fred_lag_days: int = 0,
    fred_max_lag_days: int = 10,
    shiller_lag_days: int = 1,
    shiller_max_lag_days: int = 120,
) -> list[dict[str, object]]:
    return [
        {
            "source_id": "fred.vixcls",
            "latest_observation_lag_days": fred_lag_days,
            "max_allowed_lag_days": fred_max_lag_days,
        },
        {
            "source_id": "shiller.cape_monthly",
            "latest_observation_lag_days": shiller_lag_days,
            "max_allowed_lag_days": shiller_max_lag_days,
        },
    ]


def _write_public_context_source_catalog_manifest(
    tmp_path: Path,
    *,
    fred_max_lag_days: int = 10,
    shiller_max_lag_days: int = 120,
) -> Path:
    source_catalog = tmp_path / "signal_source_families.json"
    source_catalog_manifest = tmp_path / "signal_source_families.manifest.json"
    source_catalog.write_text(
        json.dumps(
            {
                "schema_version": "market_signal_source_families.v1",
                "families": [
                    {
                        "family": "us_equity.nasdaq_sp500_public_context_daily",
                        "domain": "us_equity",
                        "bundle_type": "derived_indicators",
                        "bundle_id_prefix": "us_equity.nasdaq_sp500.public_context",
                        "canonical_input": "derived_indicators",
                        "transform": "us_equity.nasdaq_sp500.context.v1",
                        "provider_dataset": "nasdaq_sp500_public_context_daily",
                        "freshness_policy": "us_equity_research_context_t_plus_1",
                        "minimum_history_rows": 1,
                        "symbols": ["US-EQUITY-CONTEXT"],
                        "derived_indicator_fields": [
                            "cape_percentile",
                            "provider_timestamp",
                            "vix_percentile",
                        ],
                        "source_profiles": [
                            {
                                "source_id": "fred.vixcls",
                                "max_allowed_lag_days": fred_max_lag_days,
                            },
                            {
                                "source_id": "shiller.cape_monthly",
                                "max_allowed_lag_days": shiller_max_lag_days,
                            },
                        ],
                        "compatible_profiles": [
                            "research:nasdaq_sp500_cape_vix_external_context_precomputed",
                        ],
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    source_catalog_manifest.write_text(
        json.dumps(
            {
                "schema_version": "market_signal_source_family_catalog_manifest.v1",
                "artifact_type": "market_signal_source_family_catalog",
                "catalog_path": source_catalog.name,
                "catalog_sha256": _sha256_file(source_catalog),
                "catalog_size_bytes": source_catalog.stat().st_size,
                "catalog_schema_version": "market_signal_source_families.v1",
                "family_count": 1,
                "known_family_count": 1,
                "missing_known_families": [],
                "all_known_families_present": True,
                "all_consumer_contracts_satisfied": True,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return source_catalog_manifest


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
    assert summary["robustness_preset"] == "custom"
    assert summary["metadata"]["research_config"]["candidate_set"] == "nasdaq_sp500_price_variants"
    assert summary["metadata"]["research_config"]["robustness_preset"] == "custom"
    assert summary["metadata"]["research_config"]["signal_source_modes"] == [
        "market_history_price_indicators"
    ]
    assert summary["metadata"]["research_config"]["compatible_signal_consumers"] == [
        "research:nasdaq_sp500_price_proxy"
    ]
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
    assert summary["artifacts"]["production_profile_decisions"] == str(
        output_dir / "production_profile_decisions.csv"
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
    assert (output_dir / "production_profile_decisions.csv").exists()
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
    production_profile_decisions = (
        output_dir / "production_profile_decisions.csv"
    ).read_text(encoding="utf-8")
    scenario_manifest = json.loads((output_dir / "scenario_manifest.json").read_text(encoding="utf-8"))
    review_decision = json.loads((output_dir / "review_decision.json").read_text(encoding="utf-8"))
    assert "nasdaq_sp500_price_defensive" in scenario_index
    assert "nasdaq_sp500_price_no_skip" in scenario_index
    assert "pass_rate" in robustness_summary
    assert "recommendation_status" in selection_summary
    assert "selected_name" in selection_summary
    assert "min_review_scenarios" in selection_summary
    assert "selected_candidate_definition_sha256" in selection_summary
    assert "selected_candidate_role" in selection_summary
    assert "selection_policy" in selection_summary
    assert "runtime_default_recommendation" in selection_summary
    assert "runtime_default_change_policy" in selection_summary
    assert "smart_mode_enablement_status" in selection_summary
    assert "effect_size_policy" in selection_summary
    assert "selected_effect_size_gate_passed" in selection_summary
    assert "min_effect_median_relative_terminal_value_pct" in selection_summary
    assert "max_effect_terminal_cash_ratio_pct" in selection_summary
    assert "matrix_coverage_gate_passed" in selection_summary
    assert "matrix_coverage_status" in selection_summary
    assert "matrix_scenario_count" in selection_summary
    assert "matrix_scenario_sample_windows" in selection_summary
    assert "matrix_scenario_sample_window_audit_passed" in selection_summary
    assert "matrix_candidate_set_consistent" in selection_summary
    assert "matrix_candidate_universe_policy" in selection_summary
    assert "matrix_candidate_definition_sha256s" in selection_summary
    assert "compared_candidate_definition_sha256s" in selection_summary
    assert "coverage_gate_passed" in scenario_coverage
    assert "scenario_sample_windows" in scenario_coverage
    assert "scenario_sample_window_audit_passed" in scenario_coverage
    assert "ready_for_selection_review" in scenario_coverage
    assert "default_change_allowed_by_research" in production_profile_decisions
    assert "fixed_dca" in production_profile_decisions
    assert review_decision["artifact_type"] == "smart_dca_review_decision"
    assert review_decision["selection_policy"] == "fixed_preset_no_parameter_search"
    assert review_decision["effect_size_policy"] == (
        "fixed_minimum_effect_no_parameter_search"
    )
    assert review_decision["candidate_universe_policy"] == (
        "frozen_preset_names_no_parameter_search"
    )
    assert review_decision["candidate_universe_count"] == 2
    assert len(review_decision["candidate_universe_definition_sha256s"]) == 2
    assert review_decision["effect_size_thresholds"][
        "min_median_relative_terminal_value_pct"
    ] == 1.0
    assert review_decision["effect_size_thresholds"][
        "max_terminal_cash_ratio_pct"
    ] == 35.0
    assert review_decision["runtime_default_recommendation"] == "fixed_dca"
    assert review_decision["runtime_default_change_policy"] == (
        "manual_review_required_no_auto_enable"
    )
    assert "observed_best_smart_candidates" in review_decision
    assert "manual_review_candidate_names" in review_decision
    assert review_decision["matrix_coverage_gate_passed"] is True
    assert review_decision["matrix_coverage"][
        "scenario_sample_window_audit_passed"
    ] is True
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
    assert scenario_manifest["metadata"]["research_config"][
        "compatible_signal_consumers"
    ] == ["research:nasdaq_sp500_price_proxy"]
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
    assert "production_profile_decisions.csv" in {
        item["path"] for item in scenario_manifest["files"]
    }
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
    assert "compatible_signal_consumers" in candidate_summary


def test_smart_dca_research_cli_accepts_price_proxy_manifest(
    tmp_path,
    capsys,
) -> None:
    dates = pd.date_range("2024-01-02", periods=360, freq="B")
    prices = pd.Series([100.0 + index * 0.12 for index in range(len(dates))])
    signal_csv = tmp_path / "us_equity_price_proxy.csv"
    price_manifest = tmp_path / "us_equity_price_proxy.manifest.json"
    trade_csv = tmp_path / "trade.csv"
    output_dir = tmp_path / "price-proxy-artifacts"

    pd.DataFrame(
        {
            "date": dates.date,
            "QQQ": prices,
            "SPY": prices * 0.94,
            "provider_timestamp": [
                f"{item.date().isoformat()}T00:00:00Z" for item in dates
            ],
        }
    ).to_csv(signal_csv, index=False)
    pd.DataFrame(
        {
            "date": dates.date,
            "close": prices * 0.50,
        }
    ).to_csv(trade_csv, index=False)
    _write_research_manifest(
        price_manifest,
        csv_path=signal_csv,
        artifact_type="us_equity_price_proxy_research_csv",
        transform="us_equity.nasdaq_sp500.price_proxy.v1",
        as_of=str(dates[-1].date()),
        columns=["date", "QQQ", "SPY", "provider_timestamp"],
        first_date=str(dates[0].date()),
        last_date=str(dates[-1].date()),
        row_count=len(dates),
    )

    result = main(
        [
            "--signal-csv",
            str(signal_csv),
            "--trade-csv",
            str(trade_csv),
            "--price-manifest",
            str(price_manifest),
            "--output-dir",
            str(output_dir),
            "--candidate-set",
            "nasdaq_sp500_price_variants",
            "--execution-days",
            "15",
            "--monthly-contribution-usd",
            "1000",
            "--pretty",
        ]
    )

    assert result == 0
    summary = json.loads(capsys.readouterr().out)
    price_manifest_record = summary["metadata"]["input_artifacts"]["price_manifest"]
    assert price_manifest_record["artifact_type"] == (
        "us_equity_price_proxy_research_csv"
    )
    assert price_manifest_record["transform"] == (
        "us_equity.nasdaq_sp500.price_proxy.v1"
    )
    assert price_manifest_record["linked_csv_sha256"] == _sha256_file(signal_csv)
    assert price_manifest_record["linked_csv_sha256_verified"] is True
    assert price_manifest_record["signal_contract_validation_required_columns"] == [
        "QQQ",
        "SPY",
    ]
    assert "nasdaq_sp500_price_no_skip" in (
        output_dir / "monthly_day_15" / "metrics.csv"
    ).read_text(encoding="utf-8")


def test_smart_dca_research_cli_rejects_price_manifest_for_non_nasdaq_candidate_set(
    tmp_path,
    capsys,
) -> None:
    dates = pd.date_range("2024-01-02", periods=260, freq="B")
    prices = pd.Series([50_000.0 + index * 10.0 for index in range(len(dates))])
    signal_csv = tmp_path / "btc.csv"
    price_manifest = tmp_path / "price_proxy.manifest.json"
    trade_csv = tmp_path / "trade.csv"
    output_dir = tmp_path / "unsupported-price-manifest-artifacts"

    pd.DataFrame(
        {
            "date": dates.date,
            "BTC-USD": prices,
        }
    ).to_csv(signal_csv, index=False)
    pd.DataFrame(
        {
            "date": dates.date,
            "close": prices * 0.01,
        }
    ).to_csv(trade_csv, index=False)
    _write_research_manifest(
        price_manifest,
        csv_path=signal_csv,
        artifact_type="us_equity_price_proxy_research_csv",
        transform="us_equity.nasdaq_sp500.price_proxy.v1",
        as_of=str(dates[-1].date()),
        columns=["date", "BTC-USD"],
        first_date=str(dates[0].date()),
        last_date=str(dates[-1].date()),
        row_count=len(dates),
    )

    result = main(
        [
            "--signal-csv",
            str(signal_csv),
            "--trade-csv",
            str(trade_csv),
            "--price-manifest",
            str(price_manifest),
            "--output-dir",
            str(output_dir),
            "--candidate-set",
            "ibit_btc_ahr999_price",
        ]
    )

    assert result == 2
    assert "--price-manifest is supported only" in capsys.readouterr().err


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


def test_smart_dca_research_cli_accepts_us_equity_context_manifest(
    tmp_path,
    capsys,
) -> None:
    dates = pd.date_range("2025-01-02", periods=280, freq="B")
    prices = pd.Series([100.0 + index * 0.08 for index in range(len(dates))])
    signal_csv = tmp_path / "us_equity_context.csv"
    signal_manifest = tmp_path / "us_equity_context.manifest.json"
    signal_quality_report = tmp_path / "us_equity_context.quality.json"
    trade_csv = tmp_path / "trade.csv"
    output_dir = tmp_path / "nasdaq-context-artifacts"

    pd.DataFrame(
        {
            "date": dates.date,
            "QQQ": prices,
            "SPY": prices * 0.94,
            "cape_percentile": [0.90 for _ in dates],
            "vix_percentile": [0.85 for _ in dates],
            "breadth_above_sma200_pct": [0.35 for _ in dates],
        }
    ).to_csv(signal_csv, index=False)
    pd.DataFrame(
        {
            "date": dates.date,
            "close": prices * 0.50,
        }
    ).to_csv(trade_csv, index=False)
    _write_us_equity_quality_report(
        signal_quality_report,
        as_of=str(dates[-1].date()),
        warning_reasons=["missing_provider_timestamp_column"],
    )
    _write_research_manifest(
        signal_manifest,
        csv_path=signal_csv,
        artifact_type="us_equity_context_research_csv",
        transform="us_equity.nasdaq_sp500.context.v1",
        as_of=str(dates[-1].date()),
        columns=[
            "date",
            "QQQ",
            "SPY",
            "cape_percentile",
            "vix_percentile",
            "breadth_above_sma200_pct",
        ],
        first_date=str(dates[0].date()),
        last_date=str(dates[-1].date()),
        row_count=len(dates),
        quality_report_path=signal_quality_report,
    )

    result = main(
        [
            "--signal-csv",
            str(signal_csv),
            "--trade-csv",
            str(trade_csv),
            "--signal-manifest",
            str(signal_manifest),
            "--signal-quality-report",
            str(signal_quality_report),
            "--output-dir",
            str(output_dir),
            "--candidate-set",
            "nasdaq_sp500_external_precomputed_variants",
            "--signal-columns",
            "QQQ,SPY,cape_percentile,vix_percentile,breadth_above_sma200_pct",
            "--execution-days",
            "15",
            "--monthly-contribution-usd",
            "500",
            "--pretty",
        ]
    )

    assert result == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["metadata"]["research_config"]["signal_source_modes"] == [
        "external_precomputed_us_equity_context",
        "market_history_price_indicators",
    ]
    assert summary["metadata"]["research_config"]["compatible_signal_consumers"] == [
        "research:nasdaq_sp500_external_context_precomputed",
        "research:nasdaq_sp500_price_proxy",
    ]
    signal_manifest_record = summary["metadata"]["input_artifacts"]["signal_manifest"]
    assert signal_manifest_record["artifact_type"] == "us_equity_context_research_csv"
    assert signal_manifest_record["transform"] == "us_equity.nasdaq_sp500.context.v1"
    assert signal_manifest_record["declared_quality_report_sha256"] == _sha256_file(
        signal_quality_report
    )
    assert signal_manifest_record["declared_quality_report_sha256_verified"] is True
    quality_report_record = summary["metadata"]["input_artifacts"][
        "signal_quality_report"
    ]
    assert quality_report_record["schema_version"] == (
        "us_equity_context_availability_report.v1"
    )
    assert quality_report_record["quality_status"] == "pass"
    assert quality_report_record["matches_signal_manifest_quality_report"] is True
    assert "nasdaq_sp500_precomputed_valuation_guard" in (
        output_dir / "monthly_day_15" / "metrics.csv"
    ).read_text(encoding="utf-8")


def test_smart_dca_research_cli_accepts_cape_vix_context_without_breadth(
    tmp_path,
    capsys,
) -> None:
    dates = pd.date_range("2025-01-02", periods=280, freq="B")
    prices = pd.Series([100.0 + index * 0.08 for index in range(len(dates))])
    signal_csv = tmp_path / "us_equity_public_context.csv"
    signal_manifest = tmp_path / "us_equity_public_context.manifest.json"
    signal_quality_report = tmp_path / "us_equity_public_context.quality.json"
    trade_csv = tmp_path / "trade.csv"
    output_dir = tmp_path / "nasdaq-cape-vix-context-artifacts"

    pd.DataFrame(
        {
            "date": dates.date,
            "cape_percentile": [0.70 for _ in dates],
            "vix_percentile": [0.85 for _ in dates],
        }
    ).to_csv(signal_csv, index=False)
    pd.DataFrame(
        {
            "date": dates.date,
            "close": prices * 0.50,
        }
    ).to_csv(trade_csv, index=False)
    _write_us_equity_quality_report(
        signal_quality_report,
        as_of=str(dates[-1].date()),
        public=True,
    )
    _write_research_manifest(
        signal_manifest,
        csv_path=signal_csv,
        artifact_type="us_equity_context_research_csv",
        transform="us_equity.nasdaq_sp500.context.v1",
        as_of=str(dates[-1].date()),
        columns=["date", "cape_percentile", "vix_percentile"],
        first_date=str(dates[0].date()),
        last_date=str(dates[-1].date()),
        row_count=len(dates),
        quality_report_path=signal_quality_report,
    )

    result = main(
        [
            "--signal-csv",
            str(signal_csv),
            "--trade-csv",
            str(trade_csv),
            "--signal-manifest",
            str(signal_manifest),
            "--signal-quality-report",
            str(signal_quality_report),
            "--output-dir",
            str(output_dir),
            "--candidate-set",
            "nasdaq_sp500_cape_vix_precomputed_variants",
            "--signal-columns",
            "cape_percentile,vix_percentile",
            "--execution-days",
            "15",
            "--monthly-contribution-usd",
            "500",
            "--pretty",
        ]
    )

    assert result == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["metadata"]["research_config"]["signal_source_modes"] == [
        "external_precomputed_us_equity_context",
    ]
    assert summary["metadata"]["research_config"]["compatible_signal_consumers"] == [
        "research:nasdaq_sp500_cape_vix_external_context_precomputed"
    ]
    assert summary["metadata"]["input_artifacts"]["signal_quality_report"][
        "schema_version"
    ] == "us_equity_public_context_availability_report.v1"
    assert summary["metadata"]["input_artifacts"]["signal_quality_report"][
        "matches_signal_manifest_quality_report"
    ] is True
    assert "nasdaq_sp500_precomputed_cape_vix_guard" in (
        output_dir / "monthly_day_15" / "metrics.csv"
    ).read_text(encoding="utf-8")


def test_smart_dca_research_cli_accepts_public_context_source_lag_policy_catalog(
    tmp_path,
    capsys,
) -> None:
    dates = pd.date_range("2025-01-02", periods=280, freq="B")
    prices = pd.Series([100.0 + index * 0.08 for index in range(len(dates))])
    signal_csv = tmp_path / "us_equity_public_context.csv"
    signal_manifest = tmp_path / "us_equity_public_context.manifest.json"
    signal_quality_report = tmp_path / "us_equity_public_context.quality.json"
    source_catalog_manifest = _write_public_context_source_catalog_manifest(tmp_path)
    trade_csv = tmp_path / "trade.csv"
    output_dir = tmp_path / "nasdaq-cape-vix-source-catalog-artifacts"

    pd.DataFrame(
        {
            "date": dates.date,
            "cape_percentile": [0.70 for _ in dates],
            "vix_percentile": [0.85 for _ in dates],
        }
    ).to_csv(signal_csv, index=False)
    pd.DataFrame(
        {
            "date": dates.date,
            "close": prices * 0.50,
        }
    ).to_csv(trade_csv, index=False)
    _write_us_equity_quality_report(
        signal_quality_report,
        as_of=str(dates[-1].date()),
        public=True,
        input_sources=_public_context_quality_sources(),
    )
    _write_research_manifest(
        signal_manifest,
        csv_path=signal_csv,
        artifact_type="us_equity_context_research_csv",
        transform="us_equity.nasdaq_sp500.context.v1",
        as_of=str(dates[-1].date()),
        columns=["date", "cape_percentile", "vix_percentile"],
        first_date=str(dates[0].date()),
        last_date=str(dates[-1].date()),
        row_count=len(dates),
        quality_report_path=signal_quality_report,
    )

    result = main(
        [
            "--signal-csv",
            str(signal_csv),
            "--trade-csv",
            str(trade_csv),
            "--signal-manifest",
            str(signal_manifest),
            "--signal-quality-report",
            str(signal_quality_report),
            "--signal-source-family-catalog-manifest",
            str(source_catalog_manifest),
            "--output-dir",
            str(output_dir),
            "--candidate-set",
            "nasdaq_sp500_cape_vix_precomputed_variants",
            "--signal-columns",
            "cape_percentile,vix_percentile",
            "--execution-days",
            "15",
            "--monthly-contribution-usd",
            "500",
            "--pretty",
        ]
    )

    assert result == 0
    summary = json.loads(capsys.readouterr().out)
    input_artifacts = summary["metadata"]["input_artifacts"]
    assert input_artifacts["signal_quality_report"]["input_sources"][1][
        "max_allowed_lag_days"
    ] == 120
    source_catalog_record = input_artifacts["signal_source_family_catalog_manifest"]
    assert source_catalog_record["matched_source_profile_lag_policies"][1] == {
        "family": "us_equity.nasdaq_sp500_public_context_daily",
        "source_id": "shiller.cape_monthly",
        "max_allowed_lag_days": 120,
    }


def test_smart_dca_research_cli_rejects_relaxed_public_context_source_lag_policy(
    tmp_path,
    capsys,
) -> None:
    dates = pd.date_range("2025-01-02", periods=280, freq="B")
    prices = pd.Series([100.0 + index * 0.08 for index in range(len(dates))])
    signal_csv = tmp_path / "us_equity_public_context.csv"
    signal_manifest = tmp_path / "us_equity_public_context.manifest.json"
    signal_quality_report = tmp_path / "us_equity_public_context.quality.json"
    source_catalog_manifest = _write_public_context_source_catalog_manifest(tmp_path)
    trade_csv = tmp_path / "trade.csv"
    output_dir = tmp_path / "nasdaq-cape-vix-relaxed-source-policy-artifacts"

    pd.DataFrame(
        {
            "date": dates.date,
            "cape_percentile": [0.70 for _ in dates],
            "vix_percentile": [0.85 for _ in dates],
        }
    ).to_csv(signal_csv, index=False)
    pd.DataFrame(
        {
            "date": dates.date,
            "close": prices * 0.50,
        }
    ).to_csv(trade_csv, index=False)
    _write_us_equity_quality_report(
        signal_quality_report,
        as_of=str(dates[-1].date()),
        public=True,
        input_sources=_public_context_quality_sources(
            shiller_lag_days=130,
            shiller_max_lag_days=999,
        ),
    )
    _write_research_manifest(
        signal_manifest,
        csv_path=signal_csv,
        artifact_type="us_equity_context_research_csv",
        transform="us_equity.nasdaq_sp500.context.v1",
        as_of=str(dates[-1].date()),
        columns=["date", "cape_percentile", "vix_percentile"],
        first_date=str(dates[0].date()),
        last_date=str(dates[-1].date()),
        row_count=len(dates),
        quality_report_path=signal_quality_report,
    )

    result = main(
        [
            "--signal-csv",
            str(signal_csv),
            "--trade-csv",
            str(trade_csv),
            "--signal-manifest",
            str(signal_manifest),
            "--signal-quality-report",
            str(signal_quality_report),
            "--signal-source-family-catalog-manifest",
            str(source_catalog_manifest),
            "--output-dir",
            str(output_dir),
            "--candidate-set",
            "nasdaq_sp500_cape_vix_precomputed_variants",
            "--signal-columns",
            "cape_percentile,vix_percentile",
        ]
    )

    assert result == 2
    assert "exceeds catalog max_allowed_lag_days" in capsys.readouterr().err


def test_smart_dca_research_cli_requires_context_quality_report(
    tmp_path,
    capsys,
) -> None:
    dates = pd.date_range("2025-01-02", periods=40, freq="B")
    signal_csv = tmp_path / "us_equity_public_context.csv"
    signal_manifest = tmp_path / "us_equity_public_context.manifest.json"
    trade_csv = tmp_path / "trade.csv"
    pd.DataFrame(
        {
            "date": dates.date,
            "cape_percentile": [0.70 for _ in dates],
            "vix_percentile": [0.85 for _ in dates],
        }
    ).to_csv(signal_csv, index=False)
    pd.DataFrame(
        {
            "date": dates.date,
            "close": [50.0 + index for index in range(len(dates))],
        }
    ).to_csv(trade_csv, index=False)
    _write_research_manifest(
        signal_manifest,
        csv_path=signal_csv,
        artifact_type="us_equity_context_research_csv",
        transform="us_equity.nasdaq_sp500.context.v1",
        as_of=str(dates[-1].date()),
        columns=["date", "cape_percentile", "vix_percentile"],
        first_date=str(dates[0].date()),
        last_date=str(dates[-1].date()),
        row_count=len(dates),
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
            str(tmp_path / "missing-quality-report-artifacts"),
            "--candidate-set",
            "nasdaq_sp500_cape_vix_precomputed_variants",
            "--signal-columns",
            "cape_percentile,vix_percentile",
        ]
    )

    assert result == 2
    assert "require --signal-quality-report" in capsys.readouterr().err


def test_smart_dca_research_cli_requires_manifest_pinned_quality_report(
    tmp_path,
    capsys,
) -> None:
    dates = pd.date_range("2025-01-02", periods=280, freq="B")
    signal_csv = tmp_path / "us_equity_public_context.csv"
    signal_manifest = tmp_path / "us_equity_public_context.manifest.json"
    signal_quality_report = tmp_path / "us_equity_public_context.quality.json"
    trade_csv = tmp_path / "trade.csv"
    pd.DataFrame(
        {
            "date": dates.date,
            "cape_percentile": [0.70 for _ in dates],
            "vix_percentile": [0.85 for _ in dates],
        }
    ).to_csv(signal_csv, index=False)
    pd.DataFrame(
        {
            "date": dates.date,
            "close": [50.0 + index for index in range(len(dates))],
        }
    ).to_csv(trade_csv, index=False)
    _write_us_equity_quality_report(
        signal_quality_report,
        as_of=str(dates[-1].date()),
        public=True,
    )
    _write_research_manifest(
        signal_manifest,
        csv_path=signal_csv,
        artifact_type="us_equity_context_research_csv",
        transform="us_equity.nasdaq_sp500.context.v1",
        as_of=str(dates[-1].date()),
        columns=["date", "cape_percentile", "vix_percentile"],
        first_date=str(dates[0].date()),
        last_date=str(dates[-1].date()),
        row_count=len(dates),
    )

    result = main(
        [
            "--signal-csv",
            str(signal_csv),
            "--trade-csv",
            str(trade_csv),
            "--signal-manifest",
            str(signal_manifest),
            "--signal-quality-report",
            str(signal_quality_report),
            "--output-dir",
            str(tmp_path / "missing-pinned-quality-report-artifacts"),
            "--candidate-set",
            "nasdaq_sp500_cape_vix_precomputed_variants",
            "--signal-columns",
            "cape_percentile,vix_percentile",
        ]
    )

    assert result == 2
    assert "missing quality_report" in capsys.readouterr().err


def test_smart_dca_research_cli_rejects_unpinned_quality_report(
    tmp_path,
    capsys,
) -> None:
    dates = pd.date_range("2025-01-02", periods=280, freq="B")
    signal_csv = tmp_path / "us_equity_public_context.csv"
    signal_manifest = tmp_path / "us_equity_public_context.manifest.json"
    pinned_quality_report = tmp_path / "us_equity_public_context.quality.json"
    other_quality_report = tmp_path / "other_us_equity_public_context.quality.json"
    trade_csv = tmp_path / "trade.csv"
    pd.DataFrame(
        {
            "date": dates.date,
            "cape_percentile": [0.70 for _ in dates],
            "vix_percentile": [0.85 for _ in dates],
        }
    ).to_csv(signal_csv, index=False)
    pd.DataFrame(
        {
            "date": dates.date,
            "close": [50.0 + index for index in range(len(dates))],
        }
    ).to_csv(trade_csv, index=False)
    _write_us_equity_quality_report(
        pinned_quality_report,
        as_of=str(dates[-1].date()),
        public=True,
    )
    _write_us_equity_quality_report(
        other_quality_report,
        as_of=str(dates[-1].date()),
        public=True,
        warning_reasons=["different_report"],
    )
    _write_research_manifest(
        signal_manifest,
        csv_path=signal_csv,
        artifact_type="us_equity_context_research_csv",
        transform="us_equity.nasdaq_sp500.context.v1",
        as_of=str(dates[-1].date()),
        columns=["date", "cape_percentile", "vix_percentile"],
        first_date=str(dates[0].date()),
        last_date=str(dates[-1].date()),
        row_count=len(dates),
        quality_report_path=pinned_quality_report,
    )

    result = main(
        [
            "--signal-csv",
            str(signal_csv),
            "--trade-csv",
            str(trade_csv),
            "--signal-manifest",
            str(signal_manifest),
            "--signal-quality-report",
            str(other_quality_report),
            "--output-dir",
            str(tmp_path / "unpinned-quality-report-artifacts"),
            "--candidate-set",
            "nasdaq_sp500_cape_vix_precomputed_variants",
            "--signal-columns",
            "cape_percentile,vix_percentile",
        ]
    )

    assert result == 2
    assert "quality_report.path" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("mutation", "as_of_offset", "expected_error"),
    (
        ("duplicate_date", 0, "duplicate dates"),
        ("non_monotonic_date", 0, "monotonically increasing"),
        ("last_date_after_as_of", -1, "last_date must not be after manifest as_of"),
        ("out_of_range_percentile", 0, "must be between 0 and 1"),
    ),
)
def test_smart_dca_research_cli_rejects_bad_us_equity_context_signal_csv(
    tmp_path,
    capsys,
    mutation,
    as_of_offset,
    expected_error,
) -> None:
    dates = pd.date_range("2025-01-02", periods=280, freq="B")
    prices = pd.Series([100.0 + index * 0.08 for index in range(len(dates))])
    frame = pd.DataFrame(
        {
            "date": list(dates.date),
            "QQQ": prices,
            "SPY": prices * 0.94,
            "cape_percentile": [0.90 for _ in dates],
            "vix_percentile": [0.85 for _ in dates],
            "breadth_above_sma200_pct": [0.35 for _ in dates],
        }
    )
    if mutation == "duplicate_date":
        frame.loc[10, "date"] = frame.loc[9, "date"]
    if mutation == "non_monotonic_date":
        frame.loc[10, "date"], frame.loc[11, "date"] = (
            frame.loc[11, "date"],
            frame.loc[10, "date"],
        )
    if mutation == "out_of_range_percentile":
        frame.loc[10, "cape_percentile"] = 1.20

    signal_csv = tmp_path / "us_equity_context.csv"
    signal_manifest = tmp_path / "us_equity_context.manifest.json"
    signal_quality_report = tmp_path / "us_equity_context.quality.json"
    trade_csv = tmp_path / "trade.csv"
    output_dir = tmp_path / f"nasdaq-context-artifacts-{mutation}"
    frame.to_csv(signal_csv, index=False)
    pd.DataFrame(
        {
            "date": dates.date,
            "close": prices * 0.50,
        }
    ).to_csv(trade_csv, index=False)
    as_of_index = len(frame) - 1 + int(as_of_offset)
    _write_us_equity_quality_report(
        signal_quality_report,
        as_of=str(frame.iloc[as_of_index]["date"]),
    )
    _write_research_manifest(
        signal_manifest,
        csv_path=signal_csv,
        artifact_type="us_equity_context_research_csv",
        transform="us_equity.nasdaq_sp500.context.v1",
        as_of=str(frame.iloc[as_of_index]["date"]),
        columns=list(frame.columns),
        first_date=str(frame.iloc[0]["date"]),
        last_date=str(frame.iloc[-1]["date"]),
        row_count=len(frame),
        quality_report_path=signal_quality_report,
    )

    result = main(
        [
            "--signal-csv",
            str(signal_csv),
            "--trade-csv",
            str(trade_csv),
            "--signal-manifest",
            str(signal_manifest),
            "--signal-quality-report",
            str(signal_quality_report),
            "--output-dir",
            str(output_dir),
            "--candidate-set",
            "nasdaq_sp500_external_precomputed_variants",
            "--signal-columns",
            "QQQ,SPY,cape_percentile,vix_percentile,breadth_above_sma200_pct",
            "--execution-days",
            "15",
            "--monthly-contribution-usd",
            "500",
        ]
    )

    assert result == 2
    assert expected_error in capsys.readouterr().err


def test_smart_dca_research_cli_rejects_bad_precomputed_ibit_signal_csv(
    tmp_path,
    capsys,
) -> None:
    dates = pd.date_range("2025-01-02", periods=120, freq="B")
    signal_csv = tmp_path / "btc_cycle.csv"
    signal_manifest = tmp_path / "btc_cycle.manifest.json"
    trade_csv = tmp_path / "ibit.csv"
    output_dir = tmp_path / "ibit-bad-signal-artifacts"
    pd.DataFrame(
        {
            "date": dates.date,
            "ahr999": [-1.0 for _ in dates],
            "ahr999_sma": [1.4 for _ in dates],
            "mayer_multiple": [2.5 for _ in dates],
        }
    ).to_csv(signal_csv, index=False)
    pd.DataFrame(
        {
            "date": dates.date,
            "ibit_close": [50.0 + index * 0.02 for index in range(len(dates))],
        }
    ).to_csv(trade_csv, index=False)
    _write_research_manifest(
        signal_manifest,
        csv_path=signal_csv,
        artifact_type="btc_cycle_research_csv",
        transform="crypto.btc.ahr999.v1",
        as_of=str(dates[-1].date()),
        columns=["date", "ahr999", "ahr999_sma", "mayer_multiple"],
        first_date=str(dates[0].date()),
        last_date=str(dates[-1].date()),
        row_count=len(dates),
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
            "ahr999,ahr999_sma,mayer_multiple",
            "--trade-column",
            "ibit_close",
            "--execution-days",
            "15",
            "--monthly-contribution-usd",
            "500",
        ]
    )

    assert result == 2
    assert "signal CSV column 'ahr999' must be positive" in capsys.readouterr().err


def test_smart_dca_research_cli_scopes_ibit_signal_contract_to_sample_window(
    tmp_path,
    capsys,
) -> None:
    dates = pd.date_range("2025-01-02", periods=120, freq="B")
    validation_start = dates[10]
    signal_csv = tmp_path / "btc_cycle.csv"
    signal_manifest = tmp_path / "btc_cycle.manifest.json"
    trade_csv = tmp_path / "ibit.csv"
    output_dir = tmp_path / "ibit-warmup-signal-artifacts"
    slope = [None if index < 10 else 0.01 for index in range(len(dates))]
    pd.DataFrame(
        {
            "date": dates.date,
            "ahr999": [1.5 for _ in dates],
            "ahr999_365d_percentile": [0.40 for _ in dates],
            "ahr999_30d_slope": slope,
        }
    ).to_csv(signal_csv, index=False)
    pd.DataFrame(
        {
            "date": dates.date,
            "ibit_close": [50.0 + index * 0.02 for index in range(len(dates))],
        }
    ).to_csv(trade_csv, index=False)
    _write_research_manifest(
        signal_manifest,
        csv_path=signal_csv,
        artifact_type="btc_cycle_research_csv",
        transform="crypto.btc.ahr999.v1",
        as_of=str(dates[-1].date()),
        columns=[
            "date",
            "ahr999",
            "ahr999_365d_percentile",
            "ahr999_30d_slope",
        ],
        first_date=str(dates[0].date()),
        last_date=str(dates[-1].date()),
        row_count=len(dates),
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
            "ibit_btc_ahr999_helper_precomputed_variants",
            "--signal-columns",
            "ahr999,ahr999_365d_percentile,ahr999_30d_slope",
            "--trade-column",
            "ibit_close",
            "--execution-days",
            "15",
            "--monthly-contribution-usd",
            "500",
            "--sample-windows",
            f"validation:{validation_start.date()}:{dates[-1].date()}",
        ]
    )

    assert result == 0
    summary = json.loads(capsys.readouterr().out)
    signal_manifest_record = summary["metadata"]["input_artifacts"][
        "signal_manifest"
    ]
    assert signal_manifest_record["linked_csv_row_count"] == len(dates)
    assert signal_manifest_record["signal_contract_validation_first_date"] == str(
        validation_start.date()
    )
    assert signal_manifest_record["signal_contract_validation_last_date"] == str(
        dates[-1].date()
    )
    assert signal_manifest_record["signal_contract_validation_required_columns"] == [
        "ahr999",
        "ahr999_365d_percentile",
        "ahr999_30d_slope",
    ]

    rejected_result = main(
        [
            "--signal-csv",
            str(signal_csv),
            "--trade-csv",
            str(trade_csv),
            "--signal-manifest",
            str(signal_manifest),
            "--output-dir",
            str(tmp_path / "ibit-warmup-signal-rejected-artifacts"),
            "--candidate-set",
            "ibit_btc_ahr999_helper_precomputed_variants",
            "--signal-columns",
            "ahr999,ahr999_365d_percentile,ahr999_30d_slope",
            "--trade-column",
            "ibit_close",
            "--execution-days",
            "15",
            "--monthly-contribution-usd",
            "500",
            "--sample-windows",
            f"validation:{dates[0].date()}:{dates[-1].date()}",
        ]
    )

    assert rejected_result == 2
    assert "ahr999_30d_slope" in capsys.readouterr().err


def test_smart_dca_research_cli_standard_preset_expands_robustness_matrix(
    tmp_path,
    capsys,
) -> None:
    dates = pd.date_range("2024-01-02", periods=280, freq="B")
    prices = pd.Series([100.0 + index * 0.10 for index in range(len(dates))])
    signal_csv = tmp_path / "signals.csv"
    trade_csv = tmp_path / "trade.csv"
    output_dir = tmp_path / "standard-preset-artifacts"

    pd.DataFrame(
        {
            "date": dates.date,
            "QQQ": prices,
            "SPY": prices * 0.95,
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
            "15",
            "--robustness-preset",
            "standard",
        ]
    )

    assert result == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["robustness_preset"] == "standard"
    assert summary["monthly_contribution_usd_values"] == [500.0, 1000.0, 3000.0]
    assert summary["cadences"] == ["weekly", "monthly", "quarterly"]
    assert (output_dir / "weekly_contribution_usd_500" / "metrics.csv").exists()
    assert (
        output_dir / "monthly_day_15_contribution_usd_1000" / "metrics.csv"
    ).exists()
    assert (
        output_dir / "quarterly_day_15_contribution_usd_3000" / "metrics.csv"
    ).exists()

    scenario_manifest = json.loads(
        (output_dir / "scenario_manifest.json").read_text(encoding="utf-8")
    )
    assert scenario_manifest["metadata"]["research_config"][
        "robustness_preset"
    ] == "standard"
    assert scenario_manifest["metadata"]["research_config"]["cadences"] == [
        "weekly",
        "monthly",
        "quarterly",
    ]


def test_smart_dca_research_cli_runs_named_sample_windows(tmp_path, capsys) -> None:
    dates = pd.date_range("2023-01-02", periods=760, freq="B")
    prices = pd.Series([100.0 + index * 0.05 for index in range(len(dates))])
    signal_csv = tmp_path / "signals.csv"
    trade_csv = tmp_path / "trade.csv"
    output_dir = tmp_path / "sample-window-artifacts"

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
            "15",
            "--monthly-contribution-usd-values",
            "1000",
            "--sample-windows",
            "validation:2024-01-02:2024-12-31,oos:2025-01-02:2025-12-31",
            "--pretty",
        ]
    )

    assert result == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["sample_windows"] == [
        {
            "label": "validation",
            "start_date": "2024-01-02",
            "end_date": "2024-12-31",
        },
        {
            "label": "oos",
            "start_date": "2025-01-02",
            "end_date": "2025-12-31",
        },
    ]
    assert (
        summary["metadata"]["research_config"]["sample_windows"]
        == summary["sample_windows"]
    )
    scenario_index = (output_dir / "scenario_index.csv").read_text(encoding="utf-8")
    scenario_coverage = (output_dir / "scenario_coverage.csv").read_text(
        encoding="utf-8"
    )
    selection_summary = (output_dir / "selection_summary.csv").read_text(
        encoding="utf-8"
    )
    assert "sample_window_validation__monthly_day_15" in scenario_index
    assert "sample_window_oos__monthly_day_15" in scenario_index
    assert "scenario_sample_window_labels" in scenario_coverage
    assert "oos,validation" in scenario_coverage
    assert "sample_window,start_date" in scenario_coverage
    assert "matrix_scenario_sample_window_labels" in selection_summary


def test_smart_dca_research_cli_can_use_precomputed_ibit_cycle_columns(
    tmp_path,
    capsys,
) -> None:
    dates = pd.date_range("2025-01-02", periods=120, freq="B")
    signal_csv = tmp_path / "btc_cycle.csv"
    signal_manifest = tmp_path / "btc_cycle.manifest.json"
    source_catalog = tmp_path / "signal_source_families.json"
    source_catalog_manifest = tmp_path / "signal_source_families.manifest.json"
    consumer_contract_registry = tmp_path / "market_signal_consumers.json"
    consumer_contract_registry_manifest = (
        tmp_path / "market_signal_consumers.manifest.json"
    )
    platform_handoff_manifest = tmp_path / "platform_handoff.json"
    platform_handoff_index = tmp_path / "platform_handoff_index.json"
    signal_consumption_audit = tmp_path / "consumption_audit.json"
    research_handoff_manifest = tmp_path / "research_handoff.json"
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
                "transform": "crypto.btc.ahr999.v1",
                "source_version": "0.1.0",
                "as_of": str(dates[-1].date()),
                "min_history": 200,
                "row_count": len(dates),
                "first_date": str(dates[0].date()),
                "last_date": str(dates[-1].date()),
                "columns": ["date", "ahr999", "ahr999_sma", "mayer_multiple", "unused"],
                "input_csv": {
                    "path": str(signal_csv),
                    "sha256": _sha256_file(signal_csv),
                    "size_bytes": signal_csv.stat().st_size,
                },
                "output_csv": {
                    "path": str(signal_csv),
                    "sha256": _sha256_file(signal_csv),
                    "size_bytes": signal_csv.stat().st_size,
                },
            }
        ),
        encoding="utf-8",
    )
    source_catalog.write_text(
        json.dumps(
            {
                "schema_version": "market_signal_source_families.v1",
                "families": [
                        {
                            "family": "crypto.btc_cycle_daily",
                        "domain": "crypto",
                        "bundle_type": "derived_indicators",
                        "bundle_id_prefix": "crypto.btc.derived_indicators",
                        "canonical_input": "derived_indicators",
                        "transform": "crypto.btc.ahr999.v1",
                        "provider_dataset": "btc_usd_daily_ohlcv",
                        "freshness_policy": "crypto_daily_close_t_plus_1",
                        "minimum_history_rows": 200,
                        "symbols": ["BTC-USD"],
                            "derived_indicator_fields": [
                                "ahr999",
                                "ahr999_sma",
                                "close",
                                "mayer_multiple",
                                "rsi14",
                                "sma200",
                                "sma200_gap",
                            ],
                        "compatible_profiles": [
                            "us_equity:ibit_smart_dca",
                            "research:ibit_btc_ahr999_precomputed",
                            "research:ibit_btc_ahr999_mayer_precomputed",
                            "research:ibit_btc_ahr999_mayer_precomputed_variants",
                        ],
                        "runtime_consumers": ["us_equity:ibit_smart_dca"],
                        "research_consumers": [
                            "research:ibit_btc_ahr999_precomputed",
                            "research:ibit_btc_ahr999_mayer_precomputed",
                            "research:ibit_btc_ahr999_mayer_precomputed_variants",
                            ],
                        },
                        {
                            "family": "us_equity.technical_daily",
                            "domain": "us_equity",
                            "bundle_type": "derived_indicators",
                            "bundle_id_prefix": "us_equity.technical.daily",
                            "canonical_input": "derived_indicators",
                            "transform": "technical.daily_ohlcv.v1",
                            "provider_dataset": "us_equity_daily_ohlcv",
                            "freshness_policy": "us_equity_daily_close_t_plus_1",
                            "minimum_history_rows": 252,
                            "symbols": ["QQQ", "SPY"],
                            "derived_indicator_fields": [
                                "close",
                                "sma50",
                                "sma200",
                                "high252",
                                "drawdown_252d",
                                "sma200_gap",
                                "rsi14",
                            ],
                            "compatible_profiles": [
                                "us_equity:nasdaq_sp500_smart_dca",
                            ],
                            "runtime_consumers": [
                                "us_equity:nasdaq_sp500_smart_dca",
                            ],
                            "research_consumers": [],
                        },
                    ],
                },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    source_catalog_manifest.write_text(
        json.dumps(
            {
                "schema_version": (
                    "market_signal_source_family_catalog_manifest.v1"
                ),
                "artifact_type": "market_signal_source_family_catalog",
                "catalog_path": source_catalog.name,
                "catalog_sha256": _sha256_file(source_catalog),
                "catalog_size_bytes": source_catalog.stat().st_size,
                "catalog_schema_version": "market_signal_source_families.v1",
                "family_count": 2,
                "known_family_count": 2,
                "missing_known_families": [],
                "all_known_families_present": True,
                "all_consumer_contracts_satisfied": True,
                "all_runtime_consumers_covered": True,
            }
        ),
        encoding="utf-8",
    )
    consumer_contract_registry.write_text(
        json.dumps(
            {
                "schema_version": "market_signal_consumer_contracts.v1",
                "canonical_input": "derived_indicators",
                "contracts": [
                    {
                        "consumer": (
                            "research:ibit_btc_ahr999_mayer_precomputed"
                        ),
                        "canonical_input": "derived_indicators",
                        "required_indicator_fields_by_symbol": {
                            "BTC-USD": ["ahr999", "mayer_multiple"],
                        },
                    },
                    {
                        "consumer": (
                            "research:ibit_btc_ahr999_mayer_precomputed_variants"
                        ),
                        "canonical_input": "derived_indicators",
                        "required_indicator_fields_by_symbol": {
                            "BTC-USD": [
                                "ahr999",
                                "ahr999_sma",
                                "mayer_multiple",
                            ],
                        },
                    },
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    consumer_contract_registry_manifest.write_text(
        json.dumps(
            {
                "schema_version": "market_signal_consumer_contract_manifest.v1",
                "artifact_type": "market_signal_consumer_contract_registry",
                "registry_path": consumer_contract_registry.name,
                "registry_sha256": _sha256_file(consumer_contract_registry),
                "registry_size_bytes": consumer_contract_registry.stat().st_size,
                "registry_schema_version": "market_signal_consumer_contracts.v1",
                "canonical_input": "derived_indicators",
                "consumer_count": 2,
                "known_consumer_count": 9,
                "missing_known_consumers": [
                    "research:ibit_btc_ahr999_helper_precomputed_variants",
                    "research:ibit_btc_ahr999_precomputed",
                    "research:nasdaq_sp500_cape_vix_external_context_precomputed",
                    "research:nasdaq_sp500_external_context_precomputed",
                    "research:nasdaq_sp500_price_proxy",
                    "us_equity:ibit_smart_dca",
                    "us_equity:nasdaq_sp500_smart_dca",
                ],
                "all_known_consumers_present": False,
            }
        ),
        encoding="utf-8",
    )
    runtime_signal_manifest = _write_runtime_signal_bundle_manifest(tmp_path)
    consumer_contract_payload = json.loads(
        consumer_contract_registry.read_text(encoding="utf-8")
    )
    contract_consumers = [
        str(contract["consumer"])
        for contract in consumer_contract_payload["contracts"]
    ]
    research_manifest = json.loads(signal_manifest.read_text(encoding="utf-8"))
    research_handoff_manifest.write_text(
        json.dumps(
            {
                "schema_version": "market_signal_research_handoff.v1",
                "artifact_type": "market_signal_research_handoff",
                "consumer": "research:ibit_btc_ahr999_mayer_precomputed",
                "research_export_manifest_path": (
                    signal_manifest.relative_to(tmp_path).as_posix()
                ),
                "research_export_manifest_sha256": _sha256_file(signal_manifest),
                "research_artifact_type": research_manifest["artifact_type"],
                "research_transform": research_manifest["transform"],
                "research_as_of": research_manifest["as_of"],
                "research_output_csv_sha256": research_manifest["output_csv"][
                    "sha256"
                ],
                "research_quality_report_sha256": "",
                "source_family_catalog_manifest_path": (
                    source_catalog_manifest.relative_to(tmp_path).as_posix()
                ),
                "source_family_catalog_manifest_sha256": _sha256_file(
                    source_catalog_manifest
                ),
                "source_family_count": 1,
                "source_families": ["crypto.btc_cycle_daily"],
                "all_known_source_families_present": True,
                "all_consumer_contracts_satisfied": True,
                "consumer_contract_registry_manifest_path": (
                    consumer_contract_registry_manifest.relative_to(
                        tmp_path
                    ).as_posix()
                ),
                "consumer_contract_registry_manifest_sha256": _sha256_file(
                    consumer_contract_registry_manifest
                ),
                "consumer_contract_count": len(contract_consumers),
                "consumer_contracts": contract_consumers,
                "all_known_consumers_present": False,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    runtime_manifest = json.loads(runtime_signal_manifest.read_text(encoding="utf-8"))
    platform_handoff_manifest.write_text(
        json.dumps(
            {
                "schema_version": "market_signal_platform_handoff.v1",
                "artifact_type": "market_signal_platform_handoff",
                "consumer": "",
                "canonical_input": runtime_manifest["canonical_input"],
                "bundle_id": runtime_manifest["bundle_id"],
                "as_of": runtime_manifest["as_of"],
                "freshness_status": runtime_manifest["freshness_status"],
                "signal_bundle_manifest_path": (
                    runtime_signal_manifest.relative_to(tmp_path).as_posix()
                ),
                "signal_bundle_manifest_sha256": _sha256_file(
                    runtime_signal_manifest
                ),
                "source_family_catalog_manifest_path": (
                    source_catalog_manifest.relative_to(tmp_path).as_posix()
                ),
                "source_family_catalog_manifest_sha256": _sha256_file(
                    source_catalog_manifest
                ),
                "consumer_contract_registry_manifest_path": (
                    consumer_contract_registry_manifest.relative_to(
                        tmp_path
                    ).as_posix()
                ),
                "consumer_contract_registry_manifest_sha256": _sha256_file(
                    consumer_contract_registry_manifest
                ),
                "source_family_count": 2,
                "source_families": [
                    "crypto.btc_cycle_daily",
                    "us_equity.technical_daily",
                ],
                "all_known_source_families_present": True,
                "all_consumer_contracts_satisfied": True,
                "consumer_contract_count": len(contract_consumers),
                "consumer_contracts": contract_consumers,
                "all_known_consumers_present": False,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    platform_handoff = json.loads(
        platform_handoff_manifest.read_text(encoding="utf-8")
    )
    platform_handoff_index.write_text(
        json.dumps(
            {
                "schema_version": "market_signal_platform_handoff_index.v1",
                "artifact_type": "market_signal_platform_handoff_index",
                "generated_at": "2026-06-19T00:30:00Z",
                "handoffs": [
                    {
                        "handoff_manifest_path": (
                            platform_handoff_manifest.relative_to(
                                tmp_path
                            ).as_posix()
                        ),
                        "handoff_manifest_sha256": _sha256_file(
                            platform_handoff_manifest
                        ),
                        "consumer": platform_handoff["consumer"],
                        "canonical_input": platform_handoff["canonical_input"],
                        "bundle_id": platform_handoff["bundle_id"],
                        "as_of": platform_handoff["as_of"],
                        "freshness_status": platform_handoff["freshness_status"],
                        "source_families": platform_handoff["source_families"],
                        "consumer_contracts": platform_handoff[
                            "consumer_contracts"
                        ],
                        "all_known_source_families_present": platform_handoff[
                            "all_known_source_families_present"
                        ],
                        "all_consumer_contracts_satisfied": platform_handoff[
                            "all_consumer_contracts_satisfied"
                        ],
                        "all_known_consumers_present": platform_handoff[
                            "all_known_consumers_present"
                        ],
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    signal_consumption_audit.write_text(
        json.dumps(
            {
                "schema_version": "market_signal_consumption_audit.v1",
                "artifact_type": "market_signal_consumption_audit",
                "consumption_mode": "runtime_platform",
                "handoff_source": "platform_handoff_index",
                "consumer": "us_equity:ibit_smart_dca",
                "consumer_role": "runtime",
                "ready_for_consumption": True,
                "ready_for_runtime_injection": True,
                "ready_for_research_consumption": False,
                "runtime_injection_allowed": True,
                "research_csv_runtime_injection_allowed": False,
                "runtime_market_data_key": "derived_indicators",
                "runtime_payload_field": "derived_indicators",
                "canonical_input": runtime_manifest["canonical_input"],
                "bundle_id": runtime_manifest["bundle_id"],
                "as_of": runtime_manifest["as_of"],
                "lookup_as_of": str(dates[-1].date()),
                "freshness_status": runtime_manifest["freshness_status"],
                "handoff_manifest_path": str(platform_handoff_manifest.resolve()),
                "handoff_manifest_sha256": _sha256_file(platform_handoff_manifest),
                "index_path": str(platform_handoff_index.resolve()),
                "index_handoff_count": 1,
                "signal_bundle_manifest_path": str(runtime_signal_manifest.resolve()),
                "signal_bundle_manifest_sha256": _sha256_file(runtime_signal_manifest),
                "source_family_catalog_manifest_path": str(
                    source_catalog_manifest.resolve()
                ),
                "source_family_catalog_manifest_sha256": _sha256_file(
                    source_catalog_manifest
                ),
                "consumer_contract_registry_manifest_path": str(
                    consumer_contract_registry_manifest.resolve()
                ),
                "consumer_contract_registry_manifest_sha256": _sha256_file(
                    consumer_contract_registry_manifest
                ),
                "source_family_count": 2,
                "source_families": [
                    "crypto.btc_cycle_daily",
                    "us_equity.technical_daily",
                ],
                "matched_source_family_count": 1,
                "matched_source_families": ["crypto.btc_cycle_daily"],
                "all_known_source_families_present": True,
                "all_consumer_contracts_satisfied": True,
                "consumer_contract_count": len(contract_consumers),
                "consumer_contracts": contract_consumers,
                "all_known_consumers_present": False,
                "all_runtime_consumers_covered": True,
                "linked_manifest_sha256s_verified": True,
                "consumer_contract_verified": True,
                "source_catalog_verified": True,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    ambiguous_consumer_result = main(
        [
            "--signal-csv",
            str(signal_csv),
            "--trade-csv",
            str(trade_csv),
            "--signal-manifest",
            str(signal_manifest),
            "--research-signal-handoff-manifest",
            str(research_handoff_manifest),
            "--output-dir",
            str(tmp_path / "ibit-precomputed-ambiguous-handoff-artifacts"),
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

    assert ambiguous_consumer_result == 2
    ambiguous_consumer_output = capsys.readouterr()
    assert "--research-signal-handoff-consumer is required" in (
        ambiguous_consumer_output.err
    )

    result = main(
        [
            "--signal-csv",
            str(signal_csv),
            "--trade-csv",
            str(trade_csv),
            "--signal-manifest",
            str(signal_manifest),
            "--signal-source-family-catalog-manifest",
            str(source_catalog_manifest),
            "--signal-consumer-contract-registry-manifest",
            str(consumer_contract_registry_manifest),
            "--platform-signal-handoff-manifest",
            str(platform_handoff_manifest),
            "--research-signal-handoff-manifest",
            str(research_handoff_manifest),
            "--research-signal-handoff-consumer",
            "research:ibit_btc_ahr999_mayer_precomputed",
            "--require-runtime-consumer-coverage",
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
    assert summary["metadata"]["research_config"]["signal_source_modes"] == [
        "external_precomputed_derived_indicators"
    ]
    assert summary["metadata"]["research_config"]["compatible_signal_consumers"] == [
        "research:ibit_btc_ahr999_mayer_precomputed",
        "research:ibit_btc_ahr999_mayer_precomputed_variants",
    ]
    assert summary["metadata"]["research_config"][
        "require_runtime_consumer_coverage"
    ] is True
    signal_manifest_record = summary["metadata"]["input_artifacts"]["signal_manifest"]
    assert signal_manifest_record["schema_version"] == "research_export.v1"
    assert signal_manifest_record["transform"] == "crypto.btc.ahr999.v1"
    assert signal_manifest_record["columns"] == [
        "date",
        "ahr999",
        "ahr999_sma",
        "mayer_multiple",
        "unused",
    ]
    assert signal_manifest_record["linked_csv_sha256_verified"] is True
    assert signal_manifest_record["linked_csv_size_bytes_verified"] is True
    assert signal_manifest_record["declared_output_csv_size_bytes"] == (
        signal_csv.stat().st_size
    )
    assert signal_manifest_record["linked_csv_row_count"] == len(dates)
    assert signal_manifest_record["linked_csv_first_date"] == str(dates[0].date())
    assert signal_manifest_record["linked_csv_last_date"] == str(dates[-1].date())
    source_catalog_manifest_record = summary["metadata"]["input_artifacts"][
        "signal_source_family_catalog_manifest"
    ]
    assert source_catalog_manifest_record["schema_version"] == (
        "market_signal_source_family_catalog_manifest.v1"
    )
    assert source_catalog_manifest_record["artifact_type"] == (
        "market_signal_source_family_catalog"
    )
    assert source_catalog_manifest_record["catalog_sha256"] == (
        _sha256_file(source_catalog)
    )
    assert source_catalog_manifest_record["catalog_sha256_verified"] is True
    assert source_catalog_manifest_record["catalog_size_bytes_verified"] is True
    assert source_catalog_manifest_record["all_consumer_contracts_satisfied"] is True
    assert source_catalog_manifest_record["runtime_consumer_coverage_present"] is True
    assert source_catalog_manifest_record["all_runtime_consumers_covered"] is True
    assert source_catalog_manifest_record["expected_transform"] == "crypto.btc.ahr999.v1"
    assert source_catalog_manifest_record["required_signal_consumers"] == [
        "research:ibit_btc_ahr999_mayer_precomputed",
        "research:ibit_btc_ahr999_mayer_precomputed_variants",
    ]
    assert source_catalog_manifest_record["matched_families"] == [
        "crypto.btc_cycle_daily"
    ]
    assert source_catalog_manifest_record["required_signal_consumers_present"] is True
    consumer_contract_registry_record = summary["metadata"]["input_artifacts"][
        "signal_consumer_contract_registry_manifest"
    ]
    assert consumer_contract_registry_record["schema_version"] == (
        "market_signal_consumer_contract_manifest.v1"
    )
    assert consumer_contract_registry_record["artifact_type"] == (
        "market_signal_consumer_contract_registry"
    )
    assert consumer_contract_registry_record["registry_sha256"] == (
        _sha256_file(consumer_contract_registry)
    )
    assert consumer_contract_registry_record["canonical_input"] == "derived_indicators"
    assert consumer_contract_registry_record["registry_sha256_verified"] is True
    assert consumer_contract_registry_record["registry_size_bytes_verified"] is True
    assert consumer_contract_registry_record["registry_contract_fields_verified"] is True
    assert consumer_contract_registry_record["required_signal_consumers"] == [
        "research:ibit_btc_ahr999_mayer_precomputed",
        "research:ibit_btc_ahr999_mayer_precomputed_variants",
    ]
    assert consumer_contract_registry_record["required_signal_consumers_present"] is True
    platform_handoff_record = summary["metadata"]["input_artifacts"][
        "platform_signal_handoff_manifest"
    ]
    assert platform_handoff_record["schema_version"] == (
        "market_signal_platform_handoff.v1"
    )
    assert platform_handoff_record["bundle_id"] == (
        "crypto.btc.derived_indicators.2026-06-19"
    )
    assert platform_handoff_record["source_families"] == [
        "crypto.btc_cycle_daily",
        "us_equity.technical_daily",
    ]
    assert platform_handoff_record["matched_source_families"] == [
        "crypto.btc_cycle_daily"
    ]
    assert platform_handoff_record["consumer_contracts"] == [
        "research:ibit_btc_ahr999_mayer_precomputed",
        "research:ibit_btc_ahr999_mayer_precomputed_variants",
    ]
    assert platform_handoff_record["required_signal_consumers"] == [
        "research:ibit_btc_ahr999_mayer_precomputed",
        "research:ibit_btc_ahr999_mayer_precomputed_variants",
    ]
    assert platform_handoff_record["all_runtime_consumers_covered"] is True
    assert platform_handoff_record["handoff_linked_manifest_sha256s_verified"] is True
    research_handoff_record = summary["metadata"]["input_artifacts"][
        "research_signal_handoff_manifest"
    ]
    assert research_handoff_record["schema_version"] == (
        "market_signal_research_handoff.v1"
    )
    assert research_handoff_record["consumer"] == (
        "research:ibit_btc_ahr999_mayer_precomputed"
    )
    assert research_handoff_record["research_artifact_type"] == (
        "btc_cycle_research_csv"
    )
    assert research_handoff_record["research_transform"] == "crypto.btc.ahr999.v1"
    assert research_handoff_record["research_export_manifest_sha256"] == (
        _sha256_file(signal_manifest)
    )
    assert research_handoff_record["source_families"] == ["crypto.btc_cycle_daily"]
    assert research_handoff_record["matched_source_families"] == [
        "crypto.btc_cycle_daily"
    ]
    assert research_handoff_record["all_runtime_consumers_covered"] is True
    assert research_handoff_record["research_export_output_csv_verified"] is True
    assert (
        research_handoff_record["handoff_linked_manifest_sha256s_verified"] is True
    )
    scenario_manifest = json.loads(
        (output_dir / "scenario_manifest.json").read_text(encoding="utf-8")
    )
    assert scenario_manifest["metadata"]["research_config"][
        "compatible_signal_consumers"
    ] == [
        "research:ibit_btc_ahr999_mayer_precomputed",
        "research:ibit_btc_ahr999_mayer_precomputed_variants",
    ]
    assert (
        scenario_manifest["metadata"]["input_artifacts"]["signal_manifest"][
            "linked_csv_sha256"
        ]
        == _sha256_file(signal_csv)
    )
    assert (
        scenario_manifest["metadata"]["input_artifacts"]["signal_manifest"][
            "linked_csv_size_bytes"
        ]
        == signal_csv.stat().st_size
    )
    assert (
        scenario_manifest["metadata"]["input_artifacts"]["signal_manifest"][
            "linked_csv_row_count"
        ]
        == len(dates)
    )
    assert (
        scenario_manifest["metadata"]["input_artifacts"][
            "signal_source_family_catalog_manifest"
        ]["catalog_sha256"]
        == _sha256_file(source_catalog)
    )
    assert (
        scenario_manifest["metadata"]["input_artifacts"][
            "signal_consumer_contract_registry_manifest"
        ]["registry_sha256"]
        == _sha256_file(consumer_contract_registry)
    )
    assert (
        scenario_manifest["metadata"]["input_artifacts"][
            "platform_signal_handoff_manifest"
        ]["consumer_contract_registry_manifest_sha256"]
        == _sha256_file(consumer_contract_registry_manifest)
    )
    assert (
        scenario_manifest["metadata"]["input_artifacts"][
            "research_signal_handoff_manifest"
        ]["research_export_manifest_sha256"]
        == _sha256_file(signal_manifest)
    )

    index_result = main(
        [
            "--signal-csv",
            str(signal_csv),
            "--trade-csv",
            str(trade_csv),
            "--signal-manifest",
            str(signal_manifest),
            "--platform-signal-handoff-index",
            str(platform_handoff_index),
            "--require-runtime-consumer-coverage",
            "--output-dir",
            str(tmp_path / "ibit-precomputed-index-artifacts"),
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

    assert index_result == 0
    index_summary = json.loads(capsys.readouterr().out)
    handoff_index_record = index_summary["metadata"]["input_artifacts"][
        "platform_signal_handoff_index"
    ]
    assert handoff_index_record["schema_version"] == (
        "market_signal_platform_handoff_index.v1"
    )
    assert handoff_index_record["artifact_type"] == (
        "market_signal_platform_handoff_index"
    )
    assert handoff_index_record["sha256"] == _sha256_file(platform_handoff_index)
    assert handoff_index_record["size_bytes"] == platform_handoff_index.stat().st_size
    assert handoff_index_record["handoff_count"] == 1
    assert handoff_index_record["resolved_handoff_manifest_sha256"] == (
        _sha256_file(platform_handoff_manifest)
    )
    assert handoff_index_record["consumer_contracts"] == [
        "research:ibit_btc_ahr999_mayer_precomputed",
        "research:ibit_btc_ahr999_mayer_precomputed_variants",
    ]
    assert handoff_index_record["all_runtime_consumers_covered"] is True

    consumption_audit_result = main(
        [
            "--signal-csv",
            str(signal_csv),
            "--trade-csv",
            str(trade_csv),
            "--signal-manifest",
            str(signal_manifest),
            "--signal-consumption-audit-json",
            str(signal_consumption_audit),
            "--require-runtime-consumer-coverage",
            "--output-dir",
            str(tmp_path / "ibit-precomputed-consumption-audit-artifacts"),
            "--candidate-set",
            "ibit_btc_ahr999_precomputed",
            "--signal-columns",
            "ahr999",
            "--trade-column",
            "ibit_close",
            "--execution-days",
            "15",
            "--monthly-contribution-usd",
            "500",
        ]
    )

    assert consumption_audit_result == 0
    consumption_summary = json.loads(capsys.readouterr().out)
    consumption_audit_record = consumption_summary["metadata"]["input_artifacts"][
        "signal_consumption_audit"
    ]
    assert consumption_audit_record["schema_version"] == (
        "market_signal_consumption_audit.v1"
    )
    assert consumption_audit_record["consumer"] == "us_equity:ibit_smart_dca"
    assert consumption_audit_record["signal_bundle_manifest_sha256"] == (
        _sha256_file(runtime_signal_manifest)
    )
    assert consumption_audit_record["handoff_manifest_sha256"] == (
        _sha256_file(platform_handoff_manifest)
    )
    assert consumption_audit_record["all_runtime_consumers_covered"] is True
    assert consumption_audit_record["bundle_identity_verified"] is True
    consumption_scenario_manifest = json.loads(
        (
            tmp_path
            / "ibit-precomputed-consumption-audit-artifacts"
            / "scenario_manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert (
        consumption_scenario_manifest["metadata"]["input_artifacts"][
            "signal_consumption_audit"
        ]["sha256"]
        == _sha256_file(signal_consumption_audit)
    )

    source_catalog_manifest_payload = json.loads(
        source_catalog_manifest.read_text(encoding="utf-8")
    )
    source_catalog_manifest_payload["all_runtime_consumers_covered"] = False
    source_catalog_manifest.write_text(
        json.dumps(source_catalog_manifest_payload, sort_keys=True),
        encoding="utf-8",
    )
    incomplete_runtime_coverage_result = main(
        [
            "--signal-csv",
            str(signal_csv),
            "--trade-csv",
            str(trade_csv),
            "--signal-manifest",
            str(signal_manifest),
            "--signal-source-family-catalog-manifest",
            str(source_catalog_manifest),
            "--require-runtime-consumer-coverage",
            "--output-dir",
            str(tmp_path / "incomplete-runtime-coverage-artifacts"),
            "--candidate-set",
            "ibit_btc_ahr999_mayer_precomputed_variants",
            "--signal-columns",
            "ahr999,ahr999_sma,mayer_multiple",
            "--trade-column",
            "ibit_close",
            "--execution-days",
            "15",
        ]
    )
    assert incomplete_runtime_coverage_result == 2
    assert "runtime consumer coverage is incomplete" in capsys.readouterr().err

    source_catalog_payload = json.loads(source_catalog.read_text(encoding="utf-8"))
    source_catalog_payload["families"][0]["compatible_profiles"] = [
        "research:ibit_btc_ahr999_mayer_precomputed"
    ]
    source_catalog.write_text(
        json.dumps(source_catalog_payload, sort_keys=True),
        encoding="utf-8",
    )
    source_catalog_manifest_payload = json.loads(
        source_catalog_manifest.read_text(encoding="utf-8")
    )
    source_catalog_manifest_payload["catalog_sha256"] = _sha256_file(source_catalog)
    source_catalog_manifest_payload["catalog_size_bytes"] = source_catalog.stat().st_size
    source_catalog_manifest.write_text(
        json.dumps(source_catalog_manifest_payload),
        encoding="utf-8",
    )
    missing_catalog_consumer_result = main(
        [
            "--signal-csv",
            str(signal_csv),
            "--trade-csv",
            str(trade_csv),
            "--signal-manifest",
            str(signal_manifest),
            "--signal-source-family-catalog-manifest",
            str(source_catalog_manifest),
            "--output-dir",
            str(tmp_path / "missing-catalog-consumer-artifacts"),
            "--candidate-set",
            "ibit_btc_ahr999_mayer_precomputed_variants",
            "--signal-columns",
            "ahr999,ahr999_sma,mayer_multiple",
            "--trade-column",
            "ibit_close",
            "--execution-days",
            "15",
        ]
    )
    assert missing_catalog_consumer_result == 2
    assert "source family catalog missing family" in capsys.readouterr().err

    consumer_contract_registry.write_text(
        json.dumps(
            {
                "schema_version": "market_signal_consumer_contracts.v1",
                "canonical_input": "derived_indicators",
                "contracts": [
                    {
                        "consumer": (
                            "research:ibit_btc_ahr999_mayer_precomputed"
                        ),
                        "canonical_input": "derived_indicators",
                        "required_indicator_fields_by_symbol": {
                            "BTC-USD": ["ahr999", "mayer_multiple"],
                        },
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    consumer_contract_registry_manifest.write_text(
        json.dumps(
            {
                "schema_version": "market_signal_consumer_contract_manifest.v1",
                "artifact_type": "market_signal_consumer_contract_registry",
                "registry_path": consumer_contract_registry.name,
                "registry_sha256": _sha256_file(consumer_contract_registry),
                "registry_size_bytes": consumer_contract_registry.stat().st_size,
                "registry_schema_version": "market_signal_consumer_contracts.v1",
                "canonical_input": "derived_indicators",
                "consumer_count": 1,
                "known_consumer_count": 9,
                "missing_known_consumers": [
                    "research:ibit_btc_ahr999_helper_precomputed_variants",
                    "research:ibit_btc_ahr999_mayer_precomputed_variants",
                    "research:ibit_btc_ahr999_precomputed",
                    "research:nasdaq_sp500_cape_vix_external_context_precomputed",
                    "research:nasdaq_sp500_external_context_precomputed",
                    "research:nasdaq_sp500_price_proxy",
                    "us_equity:ibit_smart_dca",
                    "us_equity:nasdaq_sp500_smart_dca",
                ],
                "all_known_consumers_present": False,
            }
        ),
        encoding="utf-8",
    )

    missing_consumer_result = main(
        [
            "--signal-csv",
            str(signal_csv),
            "--trade-csv",
            str(trade_csv),
            "--signal-manifest",
            str(signal_manifest),
            "--signal-consumer-contract-registry-manifest",
            str(consumer_contract_registry_manifest),
            "--output-dir",
            str(tmp_path / "missing-consumer-artifacts"),
            "--candidate-set",
            "ibit_btc_ahr999_mayer_precomputed_variants",
            "--signal-columns",
            "ahr999,ahr999_sma,mayer_multiple",
            "--trade-column",
            "ibit_close",
            "--execution-days",
            "15",
        ]
    )
    assert missing_consumer_result == 2
    assert "missing required consumers" in capsys.readouterr().err


def test_smart_dca_research_cli_rejects_precomputed_signal_transform_mismatch(
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
                "row_count": len(dates),
                "first_date": str(dates[0].date()),
                "last_date": str(dates[-1].date()),
                "columns": ["date", "ahr999", "ahr999_sma", "mayer_multiple"],
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
        ]
    )

    assert result == 2
    assert "transform mismatch" in capsys.readouterr().err


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


def test_smart_dca_research_cli_rejects_unpinned_signal_manifest(
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
                "artifact_type": "btc_cycle_research_csv",
                "transform": "crypto.btc.ahr999.v1",
                "source_version": "0.1.0",
                "row_count": len(dates),
                "first_date": str(dates[0].date()),
                "last_date": str(dates[-1].date()),
                "columns": ["date", "ahr999", "mayer_multiple"],
                "output_csv": {"path": str(signal_csv)},
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
    assert "output_csv.sha256 is required" in capsys.readouterr().err


def test_smart_dca_research_cli_rejects_research_manifest_shape_mismatch(
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

    base_manifest = {
        "schema_version": "research_export.v1",
        "artifact_type": "btc_cycle_research_csv",
        "transform": "crypto.btc.ahr999.v1",
        "source_version": "0.1.0",
        "row_count": len(dates),
        "first_date": str(dates[0].date()),
        "last_date": str(dates[-1].date()),
        "columns": ["date", "ahr999", "mayer_multiple"],
        "output_csv": {
            "path": str(signal_csv),
            "sha256": _sha256_file(signal_csv),
            "size_bytes": signal_csv.stat().st_size,
        },
    }
    signal_manifest.write_text(
        json.dumps({**base_manifest, "row_count": 1}),
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
    assert "row_count mismatch" in capsys.readouterr().err

    signal_manifest.write_text(
        json.dumps({**base_manifest, "first_date": "1900-01-01"}),
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
    assert "first_date mismatch" in capsys.readouterr().err


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
