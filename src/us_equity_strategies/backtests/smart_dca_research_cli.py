from __future__ import annotations

import argparse
import csv
from collections.abc import Sequence
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

from us_equity_strategies.signals import (
    SignalBundleContractError,
    required_indicator_fields_for_consumer,
    signal_platform_handoff_audit_summary_from_index,
    signal_platform_handoff_audit_summary_from_manifest,
    signal_research_handoff_audit_summary_from_manifest,
    signal_consumer_contract_registry_audit_summary_from_manifest,
)

from .smart_dca_research import (
    candidate_set_signal_consumers,
    candidate_set_signal_source_modes,
    compare_execution_day_contribution_scenarios,
    compare_monthly_execution_day_scenarios,
    compare_sample_window_scenarios,
    SUPPORTED_DCA_CADENCES,
    write_scenario_research_artifacts,
)


RESEARCH_EXPORT_SCHEMA_VERSION = "research_export.v1"
ROBUSTNESS_PRESET_CUSTOM = "custom"
ROBUSTNESS_PRESET_STANDARD = "standard"
ROBUSTNESS_PRESETS = frozenset({ROBUSTNESS_PRESET_CUSTOM, ROBUSTNESS_PRESET_STANDARD})
STANDARD_ROBUSTNESS_CADENCES = "weekly,monthly,quarterly"
STANDARD_ROBUSTNESS_CONTRIBUTIONS = "500,1000,3000"
_FORBIDDEN_MANIFEST_KEY_FRAGMENTS = frozenset(
    {
        "api_key",
        "authorization",
        "cookie",
        "credential",
        "password",
        "secret",
        "signed_url",
        "token",
    }
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        signal_prices = _load_signal_frame(
            args.signal_csv,
            date_column=args.date_column,
            signal_columns=_parse_column_list(args.signal_columns),
        )
        trade_prices = _load_trade_series(
            args.trade_csv,
            date_column=args.date_column,
            trade_column=args.trade_column,
        )
        output_dir = Path(args.output_dir)
        execution_days = _parse_execution_days(args.execution_days)
        cadence_arg = _cadence_arg_for_preset(args)
        contribution_values = _parse_contribution_values(
            _contribution_values_arg_for_preset(args)
        )
        start_dates = _parse_start_dates(args.start_dates)
        cadences = _parse_cadences(cadence_arg)
        sample_windows = _parse_sample_windows(args.sample_windows)
        if sample_windows is not None:
            if contribution_values is None:
                contribution_values = (args.monthly_contribution_usd,)
            scenarios = compare_sample_window_scenarios(
                signal_prices=signal_prices,
                trade_prices=trade_prices,
                sample_windows=sample_windows,
                execution_days=execution_days,
                monthly_contribution_usd_values=contribution_values,
                start_dates=start_dates,
                cadences=cadences,
                candidate_set=args.candidate_set,
                start_date=args.start_date,
                end_date=args.end_date,
                align_start_after_warmup=not args.no_align_start_after_warmup,
                min_investment_usd=args.min_investment_usd,
            )
        elif contribution_values is None and start_dates is None and cadences == ("monthly",):
            contribution_values = (args.monthly_contribution_usd,)
            scenarios = compare_monthly_execution_day_scenarios(
                signal_prices=signal_prices,
                trade_prices=trade_prices,
                execution_days=execution_days,
                candidate_set=args.candidate_set,
                monthly_contribution_usd=args.monthly_contribution_usd,
                start_date=args.start_date,
                end_date=args.end_date,
                align_start_after_warmup=not args.no_align_start_after_warmup,
                min_investment_usd=args.min_investment_usd,
            )
        else:
            if contribution_values is None:
                contribution_values = (args.monthly_contribution_usd,)
            scenarios = compare_execution_day_contribution_scenarios(
                signal_prices=signal_prices,
                trade_prices=trade_prices,
                execution_days=execution_days,
                monthly_contribution_usd_values=contribution_values,
                start_dates=start_dates,
                cadences=cadences,
                candidate_set=args.candidate_set,
                start_date=args.start_date,
                end_date=args.end_date,
                align_start_after_warmup=not args.no_align_start_after_warmup,
                min_investment_usd=args.min_investment_usd,
            )
        metadata = _research_metadata(
            args=args,
            execution_days=execution_days,
            contribution_values=contribution_values,
            start_dates=start_dates,
            cadences=cadences,
            sample_windows=sample_windows,
        )
        artifact_paths = write_scenario_research_artifacts(
            output_dir,
            scenarios,
            metadata=metadata,
            min_review_scenarios=args.min_review_scenarios,
        )
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    summary = {
        "candidate_set": args.candidate_set,
        "robustness_preset": args.robustness_preset,
        "monthly_contribution_usd_values": contribution_values,
        "start_dates": None if start_dates is None else [item.date().isoformat() for item in start_dates],
        "sample_windows": _sample_window_summary(sample_windows),
        "cadences": cadences,
        "metadata": metadata,
        "output_dir": str(output_dir),
        "scenario_index": str(artifact_paths["scenario_index"]),
        "review_decision": str(artifact_paths["review_decision"]),
        "scenario_manifest": str(artifact_paths["scenario_manifest"]),
        "artifacts": {
            name: str(path)
            for name, path in sorted(artifact_paths.items())
        },
    }
    print(json.dumps(summary, indent=2 if args.pretty else None, sort_keys=True))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run fixed smart-DCA research candidates from local CSV files and "
            "write metrics, decision logs, and manifest artifacts."
        )
    )
    parser.add_argument("--signal-csv", required=True, type=Path)
    parser.add_argument("--trade-csv", required=True, type=Path)
    parser.add_argument(
        "--signal-manifest",
        type=Path,
        help="Optional upstream manifest describing the signal CSV artifact.",
    )
    parser.add_argument(
        "--signal-quality-report",
        type=Path,
        help=(
            "Optional upstream quality/availability report for the signal CSV "
            "or its source snapshots. Required when a us_equity_context "
            "research manifest is supplied."
        ),
    )
    parser.add_argument(
        "--trade-manifest",
        type=Path,
        help="Optional upstream manifest describing the trade price CSV artifact.",
    )
    parser.add_argument(
        "--signal-source-family-catalog-manifest",
        type=Path,
        help=(
            "Optional MarketSignalSources family catalog manifest proving source "
            "family and consumer-contract coverage."
        ),
    )
    parser.add_argument(
        "--signal-consumer-contract-registry-manifest",
        type=Path,
        help=(
            "Optional MarketSignalSources consumer contract registry manifest "
            "proving required indicator fields by consumer."
        ),
    )
    parser.add_argument(
        "--platform-signal-handoff-manifest",
        type=Path,
        help=(
            "Optional MarketSignalSources platform handoff manifest pinning the "
            "signal bundle, source-family catalog, and consumer registry manifests."
        ),
    )
    parser.add_argument(
        "--platform-signal-handoff-index",
        type=Path,
        help=(
            "Optional MarketSignalSources handoff index. The CLI resolves the latest "
            "matching handoff manifest for the candidate-set consumers before "
            "validating linked signal artifacts."
        ),
    )
    parser.add_argument(
        "--research-signal-handoff-manifest",
        type=Path,
        help=(
            "Optional MarketSignalSources research handoff manifest pinning the "
            "research export, source-family catalog, and consumer registry manifests."
        ),
    )
    parser.add_argument(
        "--research-signal-handoff-consumer",
        help=(
            "Research consumer to validate when the candidate set has multiple "
            "compatible research consumers."
        ),
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--candidate-set",
        default="nasdaq_sp500_price",
        help=(
            "Candidate set or preset name. Known sets include "
            "nasdaq_sp500_production_equivalent, nasdaq_sp500_price, "
            "nasdaq_sp500_price_variants, "
            "nasdaq_sp500_external_precomputed_variants, ibit_btc_ahr999_price, "
            "ibit_btc_ahr999_price_variants, ibit_btc_ahr999_precomputed, "
            "ibit_btc_ahr999_precomputed_variants, "
            "ibit_btc_ahr999_helper_precomputed_variants, legacy Mayer variants, "
            "and all."
        ),
    )
    parser.add_argument(
        "--signal-columns",
        help="Comma-separated signal CSV columns. Defaults to all non-date columns.",
    )
    parser.add_argument(
        "--trade-column",
        help="Trade price column. Defaults to close/adj_close if present, else first value column.",
    )
    parser.add_argument("--date-column", default="date")
    parser.add_argument("--monthly-contribution-usd", type=float, default=1000.0)
    parser.add_argument(
        "--monthly-contribution-usd-values",
        help=(
            "Comma-separated contribution amounts for robustness runs. "
            "When set, the CLI runs execution day x contribution amount scenarios."
        ),
    )
    parser.add_argument("--min-investment-usd", type=float, default=0.0)
    parser.add_argument(
        "--min-review-scenarios",
        type=int,
        default=3,
        help=(
            "Minimum scenario count before selection_summary can promote a "
            "candidate to manual review."
        ),
    )
    parser.add_argument(
        "--robustness-preset",
        default=ROBUSTNESS_PRESET_CUSTOM,
        choices=sorted(ROBUSTNESS_PRESETS),
        help=(
            "Use a fixed robustness matrix preset. 'standard' fills missing "
            "contribution values with 500,1000,3000 and missing cadences with "
            "weekly,monthly,quarterly."
        ),
    )
    parser.add_argument(
        "--cadences",
        default=None,
        help="Comma-separated DCA cadences for robustness runs: weekly, monthly, quarterly.",
    )
    parser.add_argument("--execution-days", default="1,10,15,20,25")
    parser.add_argument("--start-date")
    parser.add_argument(
        "--start-dates",
        help=(
            "Comma-separated explicit rolling-start dates. When set, the CLI "
            "runs start date x execution day x contribution amount scenarios."
        ),
    )
    parser.add_argument(
        "--sample-windows",
        help=(
            "Comma-separated named sample windows in label:start:end form, for "
            "example discovery:2018-01-01:2020-12-31,oos:2024-01-01:2026-06-18."
        ),
    )
    parser.add_argument("--end-date")
    parser.add_argument(
        "--no-align-start-after-warmup",
        action="store_true",
        help="Do not align the shared backtest start after candidate warm-up history.",
    )
    parser.add_argument("--pretty", action="store_true")
    return parser


def _parse_column_list(raw: str | None) -> tuple[str, ...] | None:
    if raw is None:
        return None
    columns = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not columns:
        raise ValueError("signal-columns must include at least one column")
    return columns


def _parse_sample_windows(
    raw: str | None,
) -> tuple[tuple[str, pd.Timestamp | None, pd.Timestamp | None], ...] | None:
    if raw is None:
        return None
    windows: list[tuple[str, pd.Timestamp | None, pd.Timestamp | None]] = []
    seen_labels: set[str] = set()
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        pieces = value.split(":")
        if len(pieces) != 3:
            raise ValueError(
                "sample-windows entries must use label:start:end form"
            )
        label = pieces[0].strip()
        if not label:
            raise ValueError("sample-windows label must not be empty")
        if label in seen_labels:
            raise ValueError(f"duplicate sample-windows label: {label!r}")
        seen_labels.add(label)
        start = _optional_timestamp(pieces[1])
        end = _optional_timestamp(pieces[2])
        if start is None and end is None:
            raise ValueError(
                f"sample-windows entry {label!r} must include start or end"
            )
        if start is not None and end is not None and start > end:
            raise ValueError(
                f"sample-windows entry {label!r} start must be <= end"
            )
        windows.append((label, start, end))
    if not windows:
        raise ValueError("sample-windows must include at least one window")
    return tuple(windows)


def _optional_timestamp(raw: str) -> pd.Timestamp | None:
    value = raw.strip()
    if not value:
        return None
    return pd.Timestamp(value).tz_localize(None).normalize()


def _sample_window_summary(
    sample_windows: tuple[tuple[str, pd.Timestamp | None, pd.Timestamp | None], ...]
    | None,
) -> tuple[dict[str, str], ...] | None:
    if sample_windows is None:
        return None
    return tuple(
        {
            "label": label,
            "start_date": "" if start is None else start.date().isoformat(),
            "end_date": "" if end is None else end.date().isoformat(),
        }
        for label, start, end in sample_windows
    )


def _research_metadata(
    *,
    args: argparse.Namespace,
    execution_days: tuple[int, ...],
    contribution_values: tuple[float, ...],
    start_dates: tuple[pd.Timestamp, ...] | None,
    cadences: tuple[str, ...],
    sample_windows: tuple[tuple[str, pd.Timestamp | None, pd.Timestamp | None], ...] | None,
) -> dict[str, object]:
    return {
        "research_config": {
            "candidate_set": args.candidate_set,
            "robustness_preset": args.robustness_preset,
            "signal_source_modes": candidate_set_signal_source_modes(
                args.candidate_set
            ),
            "compatible_signal_consumers": candidate_set_signal_consumers(
                args.candidate_set
            ),
            "date_column": args.date_column,
            "signal_columns": _parse_column_list(args.signal_columns),
            "trade_column": args.trade_column,
            "monthly_contribution_usd": args.monthly_contribution_usd,
            "monthly_contribution_usd_values": contribution_values,
            "cadences": cadences,
            "execution_days": execution_days,
            "start_date": args.start_date,
            "start_dates": None
            if start_dates is None
            else tuple(item.date().isoformat() for item in start_dates),
            "sample_windows": _sample_window_summary(sample_windows),
            "end_date": args.end_date,
            "align_start_after_warmup": not args.no_align_start_after_warmup,
            "min_investment_usd": args.min_investment_usd,
            "min_review_scenarios": args.min_review_scenarios,
        },
        "input_artifacts": {
            "signal_csv": _file_record(args.signal_csv),
            "trade_csv": _file_record(args.trade_csv),
            **_optional_manifest_records(
                args,
                start_dates=start_dates,
                sample_windows=sample_windows,
            ),
        },
    }


def _optional_manifest_records(
    args: argparse.Namespace,
    *,
    start_dates: tuple[pd.Timestamp, ...] | None = None,
    sample_windows: tuple[tuple[str, pd.Timestamp | None, pd.Timestamp | None], ...]
    | None = None,
) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    signal_manifest_expectations = _signal_manifest_expectations(args.candidate_set)
    required_consumers = candidate_set_signal_consumers(args.candidate_set)
    signal_contract_validation_window = _signal_contract_validation_window(
        args=args,
        start_dates=start_dates,
        sample_windows=sample_windows,
    )
    if (
        signal_manifest_expectations["artifact_type"] == "us_equity_context_research_csv"
        and args.signal_manifest is not None
        and args.signal_quality_report is None
    ):
        raise ValueError(
            "us_equity_context signal manifests require --signal-quality-report"
        )
    if args.signal_manifest is not None:
        records["signal_manifest"] = _manifest_record(
            args.signal_manifest,
            linked_csv_path=args.signal_csv,
            role="signal",
            date_column=args.date_column,
            expected_artifact_type=signal_manifest_expectations["artifact_type"],
            expected_transform=signal_manifest_expectations["transform"],
            required_signal_fields=_required_signal_fields_for_artifact_type(
                signal_manifest_expectations["artifact_type"],
                requested_signal_columns=_parse_column_list(args.signal_columns),
                required_consumers=required_consumers,
            ),
            signal_contract_validation_window=signal_contract_validation_window,
        )
    if args.signal_quality_report is not None:
        records["signal_quality_report"] = _signal_quality_report_record(
            args.signal_quality_report,
            expected_artifact_type=signal_manifest_expectations["artifact_type"],
            signal_manifest=records.get("signal_manifest"),
        )
    if args.trade_manifest is not None:
        records["trade_manifest"] = _manifest_record(
            args.trade_manifest,
            linked_csv_path=args.trade_csv,
            role="trade",
            date_column=args.date_column,
            expected_artifact_type=None,
            expected_transform=None,
        )
    if args.signal_source_family_catalog_manifest is not None:
        records["signal_source_family_catalog_manifest"] = (
            _source_catalog_manifest_record(
                args.signal_source_family_catalog_manifest,
                required_consumers=required_consumers,
                expected_transform=signal_manifest_expectations["transform"],
            )
        )
        if "signal_quality_report" in records:
            _validate_signal_quality_report_source_lag_policies(
                records["signal_quality_report"],
                source_catalog=records["signal_source_family_catalog_manifest"],
            )
    if args.signal_consumer_contract_registry_manifest is not None:
        records["signal_consumer_contract_registry_manifest"] = (
            _consumer_contract_registry_manifest_record(
                args.signal_consumer_contract_registry_manifest,
                required_consumers=required_consumers,
            )
        )
    if args.platform_signal_handoff_manifest is not None:
        records["platform_signal_handoff_manifest"] = _platform_handoff_manifest_record(
            args.platform_signal_handoff_manifest,
            required_consumers=required_consumers,
            expected_transform=signal_manifest_expectations["transform"],
        )
    if args.platform_signal_handoff_index is not None:
        records["platform_signal_handoff_index"] = _platform_handoff_index_record(
            args.platform_signal_handoff_index,
            required_consumers=required_consumers,
            expected_transform=signal_manifest_expectations["transform"],
            as_of=args.end_date,
        )
    if args.research_signal_handoff_manifest is not None:
        handoff_consumer = _research_handoff_consumer(
            required_consumers=required_consumers,
            requested_consumer=args.research_signal_handoff_consumer,
        )
        records["research_signal_handoff_manifest"] = _research_handoff_manifest_record(
            args.research_signal_handoff_manifest,
            consumer=handoff_consumer,
            required_consumers=required_consumers,
            expected_artifact_type=signal_manifest_expectations["artifact_type"],
            expected_transform=signal_manifest_expectations["transform"],
        )
        if "signal_manifest" in records:
            handoff_record = records["research_signal_handoff_manifest"]
            signal_manifest_record = records["signal_manifest"]
            if (
                handoff_record["research_export_manifest_sha256"]
                != signal_manifest_record["sha256"]
            ):
                raise ValueError(
                    "research signal handoff research_export_manifest_sha256 "
                    "does not match --signal-manifest"
                )
    elif args.research_signal_handoff_consumer:
        raise ValueError(
            "--research-signal-handoff-consumer requires "
            "--research-signal-handoff-manifest"
        )
    return records


def _cadence_arg_for_preset(args: argparse.Namespace) -> str:
    if args.cadences:
        return str(args.cadences)
    if args.robustness_preset == ROBUSTNESS_PRESET_STANDARD:
        return STANDARD_ROBUSTNESS_CADENCES
    return "monthly"


def _contribution_values_arg_for_preset(args: argparse.Namespace) -> str | None:
    if args.monthly_contribution_usd_values:
        return str(args.monthly_contribution_usd_values)
    if args.robustness_preset == ROBUSTNESS_PRESET_STANDARD:
        return STANDARD_ROBUSTNESS_CONTRIBUTIONS
    return None


def _signal_manifest_expectations(candidate_set: str) -> dict[str, str | None]:
    modes = candidate_set_signal_source_modes(candidate_set)
    if "external_precomputed_us_equity_context" in modes:
        return {
            "artifact_type": "us_equity_context_research_csv",
            "transform": "us_equity.nasdaq_sp500.context.v1",
        }
    if "external_precomputed_derived_indicators" in modes:
        return {
            "artifact_type": "btc_cycle_research_csv",
            "transform": "crypto.btc.ahr999.v1",
        }
    return {"artifact_type": None, "transform": None}


def _signal_contract_validation_window(
    *,
    args: argparse.Namespace,
    start_dates: tuple[pd.Timestamp, ...] | None,
    sample_windows: tuple[tuple[str, pd.Timestamp | None, pd.Timestamp | None], ...]
    | None,
) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    start_candidates: list[pd.Timestamp] = []
    end_candidates: list[pd.Timestamp] = []
    if args.start_date:
        start_candidates.append(pd.Timestamp(args.start_date).normalize())
    if args.end_date:
        end_candidates.append(pd.Timestamp(args.end_date).normalize())
    if start_dates is not None:
        start_candidates.extend(item.normalize() for item in start_dates)
    if sample_windows is not None:
        for _, start, end in sample_windows:
            if start is not None:
                start_candidates.append(start.normalize())
            if end is not None:
                end_candidates.append(end.normalize())
    return (
        min(start_candidates) if start_candidates else None,
        max(end_candidates) if end_candidates else None,
    )


def _manifest_record(
    path: Path,
    *,
    linked_csv_path: Path,
    role: str,
    date_column: str,
    expected_artifact_type: str | None,
    expected_transform: str | None,
    required_signal_fields: tuple[str, ...] = (),
    signal_contract_validation_window: tuple[pd.Timestamp | None, pd.Timestamp | None]
    = (None, None),
) -> dict[str, object]:
    manifest = _read_manifest(path)
    _validate_no_sensitive_manifest_fields(manifest, path=f"{role}_manifest")
    linked_csv_sha256 = _sha256_file(linked_csv_path)
    linked_csv_size_bytes = linked_csv_path.stat().st_size
    linked_csv_shape = _csv_shape_record(linked_csv_path, date_column=date_column)
    schema_version = str(manifest.get("schema_version", "")).strip()
    if not schema_version:
        raise ValueError(f"{role} manifest schema_version is required")
    declared_output = manifest.get("output_csv")
    if not isinstance(declared_output, dict):
        raise ValueError(f"{role} manifest output_csv must be an object")
    declared_output_sha256 = str(declared_output.get("sha256", "")).strip().lower()
    if not declared_output_sha256:
        raise ValueError(f"{role} manifest output_csv.sha256 is required")
    if declared_output_sha256 != linked_csv_sha256:
        raise ValueError(
            f"{role} manifest output_csv.sha256 mismatch: "
            f"expected {linked_csv_sha256}, got {declared_output_sha256}"
        )
    declared_output_size = declared_output.get("size_bytes")
    if declared_output_size is not None:
        if (
            not isinstance(declared_output_size, int)
            or isinstance(declared_output_size, bool)
            or declared_output_size != linked_csv_size_bytes
        ):
            raise ValueError(
                f"{role} manifest output_csv.size_bytes mismatch: "
                f"expected {linked_csv_size_bytes}, got {declared_output_size!r}"
            )
    if schema_version == RESEARCH_EXPORT_SCHEMA_VERSION:
        _validate_research_export_manifest(
            manifest,
            linked_csv_shape=linked_csv_shape,
            role=role,
        )
    _validate_manifest_expectations(
        manifest,
        schema_version=schema_version,
        role=role,
        expected_artifact_type=expected_artifact_type,
        expected_transform=expected_transform,
    )
    signal_contract_record: dict[str, object] = {}
    if role == "signal":
        signal_contract_record = _validate_signal_research_csv_contract(
            linked_csv_path,
            manifest=manifest,
            linked_csv_shape=linked_csv_shape,
            date_column=date_column,
            expected_artifact_type=expected_artifact_type,
            required_signal_fields=required_signal_fields,
            validation_window=signal_contract_validation_window,
        )
    declared_quality_report = _research_manifest_quality_report_record(
        path,
        manifest,
        role=role,
        required=(
            role == "signal"
            and expected_artifact_type == "us_equity_context_research_csv"
        ),
    )

    return {
        **_file_record(path),
        "schema_version": schema_version,
        "artifact_type": str(manifest.get("artifact_type", "")),
        "transform": str(manifest.get("transform", "")),
        "source_version": str(manifest.get("source_version", "")),
        "as_of": str(manifest.get("as_of", "")),
        "min_history": manifest.get("min_history"),
        "row_count": manifest.get("row_count"),
        "first_date": str(manifest.get("first_date", "")),
        "last_date": str(manifest.get("last_date", "")),
        "columns": tuple(str(column) for column in manifest.get("columns", ()) or ()),
        "linked_csv_sha256": linked_csv_sha256,
        "linked_csv_size_bytes": linked_csv_size_bytes,
        "linked_csv_row_count": linked_csv_shape["row_count"],
        "linked_csv_first_date": linked_csv_shape["first_date"],
        "linked_csv_last_date": linked_csv_shape["last_date"],
        "declared_output_csv_sha256": declared_output_sha256,
        "declared_output_csv_size_bytes": declared_output_size,
        "linked_csv_sha256_verified": True,
        "linked_csv_size_bytes_verified": declared_output_size is not None,
        **declared_quality_report,
        **signal_contract_record,
    }


def _signal_quality_report_record(
    path: Path,
    *,
    expected_artifact_type: str | None,
    signal_manifest: dict[str, object] | None,
) -> dict[str, object]:
    report = _read_manifest(path)
    _validate_no_sensitive_manifest_fields(report, path="signal_quality_report")
    file_record = _file_record(path)
    schema_version = str(report.get("schema_version", "")).strip()
    artifact_type = str(report.get("artifact_type", "")).strip()
    if expected_artifact_type != "us_equity_context_research_csv":
        raise ValueError(
            "--signal-quality-report is currently supported only for "
            "us_equity_context_research_csv signals"
        )
    expected_artifact_types = {
        "us_equity_context_availability_report.v1": (
            "us_equity_context_availability_report"
        ),
        "us_equity_public_context_availability_report.v1": (
            "us_equity_public_context_availability_report"
        ),
    }
    expected_report_artifact_type = expected_artifact_types.get(schema_version)
    if expected_report_artifact_type is None:
        raise ValueError(
            "signal quality report schema_version must be one of: "
            + ", ".join(sorted(expected_artifact_types))
        )
    if artifact_type != expected_report_artifact_type:
        raise ValueError(
            "signal quality report artifact_type mismatch: "
            f"{artifact_type!r} != {expected_report_artifact_type!r}"
        )
    quality_status = str(report.get("quality_status", "")).strip()
    if quality_status not in {"pass", "warn", "fail"}:
        raise ValueError("signal quality report quality_status is invalid")
    failure_reasons = _string_tuple(report.get("failure_reasons"))
    warning_reasons = _string_tuple(report.get("warning_reasons"))
    input_sources = _quality_report_input_sources(report)
    if quality_status == "fail" or failure_reasons:
        raise ValueError(
            "signal quality report failed: "
            + ", ".join(failure_reasons or (quality_status,))
        )
    report_as_of = str(report.get("as_of", "") or "").strip()
    manifest_as_of = "" if signal_manifest is None else str(
        signal_manifest.get("as_of", "")
    ).strip()
    if report_as_of and manifest_as_of:
        if pd.Timestamp(report_as_of).normalize() > pd.Timestamp(manifest_as_of).normalize():
            raise ValueError(
                "signal quality report as_of must not be after signal manifest as_of: "
                f"{report_as_of} > {manifest_as_of}"
            )
    if signal_manifest is not None and bool(
        signal_manifest.get("declared_quality_report_present")
    ):
        _validate_signal_quality_report_matches_manifest(
            path,
            file_record=file_record,
            signal_manifest=signal_manifest,
        )
    return {
        **file_record,
        "schema_version": schema_version,
        "artifact_type": artifact_type,
        "quality_status": quality_status,
        "failure_reasons": failure_reasons,
        "warning_reasons": warning_reasons,
        "input_sources": input_sources,
        "input_source_count": len(input_sources),
        "as_of": report_as_of,
        "signal_manifest_as_of": manifest_as_of,
        "quality_status_accepted": True,
        "matches_signal_manifest_quality_report": bool(
            signal_manifest
            and signal_manifest.get("declared_quality_report_present")
        ),
    }


def _quality_report_input_sources(report: dict[str, object]) -> tuple[dict[str, object], ...]:
    raw_sources = report.get("input_sources")
    if raw_sources is None:
        return ()
    if not isinstance(raw_sources, list):
        raise ValueError("signal quality report input_sources must be a list")
    sources: list[dict[str, object]] = []
    for raw_source in raw_sources:
        if not isinstance(raw_source, dict):
            raise ValueError("signal quality report input_sources records must be objects")
        source_id = str(raw_source.get("source_id", "")).strip()
        if not source_id:
            raise ValueError("signal quality report input_sources source_id is required")
        source_record: dict[str, object] = {"source_id": source_id}
        for field in ("latest_observation_lag_days", "max_allowed_lag_days"):
            value = raw_source.get(field)
            if value is not None:
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise ValueError(
                        "signal quality report input_sources "
                        f"{source_id} {field} must be a non-negative integer"
                    )
                source_record[field] = value
        sources.append(source_record)
    return tuple(sources)


def _research_manifest_quality_report_record(
    manifest_path: Path,
    manifest: dict[str, object],
    *,
    role: str,
    required: bool,
) -> dict[str, object]:
    quality_report = manifest.get("quality_report")
    if quality_report is None:
        if required:
            raise ValueError(
                f"{role} research export manifest missing quality_report"
            )
        return {"declared_quality_report_present": False}
    if not isinstance(quality_report, dict):
        raise ValueError(f"{role} research export manifest quality_report must be an object")

    raw_path = str(quality_report.get("path", "")).strip()
    expected_sha256 = str(quality_report.get("sha256", "")).strip().lower()
    expected_size = quality_report.get("size_bytes")
    if not raw_path:
        raise ValueError(
            f"{role} research export manifest quality_report.path is required"
        )
    if not expected_sha256:
        raise ValueError(
            f"{role} research export manifest quality_report.sha256 is required"
        )
    quality_report_path = _resolve_research_manifest_file_path(
        manifest_path,
        raw_path,
        role=role,
        field="quality_report.path",
    )
    actual_sha256 = _sha256_file(quality_report_path)
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"{role} manifest quality_report.sha256 mismatch: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )
    actual_size = quality_report_path.stat().st_size
    if (
        not isinstance(expected_size, int)
        or isinstance(expected_size, bool)
        or expected_size != actual_size
    ):
        raise ValueError(
            f"{role} manifest quality_report.size_bytes mismatch: "
            f"expected {actual_size}, got {expected_size!r}"
        )
    return {
        "declared_quality_report_present": True,
        "declared_quality_report_path": str(quality_report_path),
        "declared_quality_report_sha256": expected_sha256,
        "declared_quality_report_size_bytes": expected_size,
        "declared_quality_report_sha256_verified": True,
        "declared_quality_report_size_bytes_verified": True,
    }


def _validate_signal_quality_report_matches_manifest(
    path: Path,
    *,
    file_record: dict[str, object],
    signal_manifest: dict[str, object],
) -> None:
    declared_path = Path(
        str(signal_manifest.get("declared_quality_report_path", ""))
    ).resolve()
    if path.resolve() != declared_path:
        raise ValueError(
            "signal quality report does not match signal manifest quality_report.path: "
            f"expected {declared_path}, got {path.resolve()}"
        )
    declared_sha256 = str(
        signal_manifest.get("declared_quality_report_sha256", "")
    ).strip().lower()
    if str(file_record["sha256"]) != declared_sha256:
        raise ValueError(
            "signal quality report does not match signal manifest "
            "quality_report.sha256"
        )
    declared_size = signal_manifest.get("declared_quality_report_size_bytes")
    if file_record["size_bytes"] != declared_size:
        raise ValueError(
            "signal quality report does not match signal manifest "
            "quality_report.size_bytes"
        )


def _source_catalog_manifest_record(
    path: Path,
    *,
    required_consumers: tuple[str, ...],
    expected_transform: str | None,
) -> dict[str, object]:
    manifest = _read_manifest(path)
    _validate_no_sensitive_manifest_fields(
        manifest,
        path="signal_source_family_catalog_manifest",
    )
    schema_version = str(manifest.get("schema_version", "")).strip()
    if schema_version != "market_signal_source_family_catalog_manifest.v1":
        raise ValueError(
            "signal source family catalog manifest schema_version must be "
            "'market_signal_source_family_catalog_manifest.v1'"
        )
    artifact_type = str(manifest.get("artifact_type", "")).strip()
    if artifact_type != "market_signal_source_family_catalog":
        raise ValueError(
            "signal source family catalog manifest artifact_type mismatch: "
            f"{artifact_type!r}"
        )
    catalog_path = _resolve_manifest_artifact_path(
        path,
        str(manifest.get("catalog_path", "")),
        role="signal source family catalog manifest",
        field="catalog_path",
    )
    expected_catalog_sha256 = str(manifest.get("catalog_sha256", "")).strip().lower()
    if not expected_catalog_sha256:
        raise ValueError(
            "signal source family catalog manifest catalog_sha256 is required"
        )
    actual_catalog_sha256 = _sha256_file(catalog_path)
    if actual_catalog_sha256 != expected_catalog_sha256:
        raise ValueError(
            "signal source family catalog manifest catalog_sha256 mismatch: "
            f"expected {expected_catalog_sha256}, got {actual_catalog_sha256}"
        )
    expected_catalog_size = manifest.get("catalog_size_bytes")
    actual_catalog_size = catalog_path.stat().st_size
    if (
        not isinstance(expected_catalog_size, int)
        or isinstance(expected_catalog_size, bool)
        or expected_catalog_size != actual_catalog_size
    ):
        raise ValueError(
            "signal source family catalog manifest catalog_size_bytes mismatch: "
            f"expected {actual_catalog_size}, got {expected_catalog_size!r}"
        )
    catalog = _read_manifest(catalog_path)
    _validate_no_sensitive_manifest_fields(
        catalog,
        path="signal_source_family_catalog",
    )
    catalog_schema_version = str(catalog.get("schema_version", "")).strip()
    if catalog_schema_version != "market_signal_source_families.v1":
        raise ValueError(
            "signal source family catalog schema_version must be "
            "'market_signal_source_families.v1'"
        )
    manifest_catalog_schema_version = str(
        manifest.get("catalog_schema_version", "")
    ).strip()
    if (
        manifest_catalog_schema_version
        and manifest_catalog_schema_version != catalog_schema_version
    ):
        raise ValueError(
            "signal source family catalog manifest catalog_schema_version mismatch: "
            f"{manifest_catalog_schema_version!r} != {catalog_schema_version!r}"
        )
    families = catalog.get("families")
    if not isinstance(families, list) or not families:
        raise ValueError("signal source family catalog families must be a non-empty list")
    matched_family_records = _matching_source_catalog_family_records(
        families,
        required_consumers=required_consumers,
        expected_transform=expected_transform,
    )
    matched_families = tuple(
        str(record["family"])
        for record in matched_family_records
    )
    if required_consumers and not matched_families:
        raise ValueError(
            "signal source family catalog missing family for required consumers: "
            + ", ".join(required_consumers)
        )
    matched_source_lag_policies = _source_lag_policies_for_matched_families(
        matched_family_records
    )
    return {
        **_file_record(path),
        "schema_version": schema_version,
        "artifact_type": artifact_type,
        "catalog_path": str(catalog_path),
        "catalog_sha256": expected_catalog_sha256,
        "catalog_size_bytes": expected_catalog_size,
        "catalog_schema_version": catalog_schema_version,
        "family_count": manifest.get("family_count"),
        "known_family_count": manifest.get("known_family_count"),
        "missing_known_families": tuple(
            str(family)
            for family in manifest.get("missing_known_families", ()) or ()
        ),
        "expected_transform": expected_transform or "",
        "required_signal_consumers": required_consumers,
        "required_signal_consumer_count": len(required_consumers),
        "matched_family_count": len(matched_families),
        "matched_families": matched_families,
        "matched_source_profile_lag_policy_count": len(matched_source_lag_policies),
        "matched_source_profile_lag_policies": matched_source_lag_policies,
        "required_signal_consumers_present": not required_consumers
        or bool(matched_families),
        "all_known_families_present": bool(
            manifest.get("all_known_families_present", False)
        ),
        "all_consumer_contracts_satisfied": bool(
            manifest.get("all_consumer_contracts_satisfied", False)
        ),
        "catalog_sha256_verified": True,
        "catalog_size_bytes_verified": True,
    }


def _matching_source_catalog_family_records(
    families: list[object],
    *,
    required_consumers: tuple[str, ...],
    expected_transform: str | None,
) -> tuple[dict[str, object], ...]:
    matched: list[dict[str, object]] = []
    for record in families:
        if not isinstance(record, dict):
            raise ValueError("signal source family catalog records must be objects")
        family = str(record.get("family", "")).strip()
        if not family:
            raise ValueError("signal source family catalog record family is required")
        transform = str(record.get("transform", "")).strip()
        if expected_transform is not None and transform != expected_transform:
            continue
        compatible_profiles = _string_tuple(record.get("compatible_profiles"))
        if all(consumer in compatible_profiles for consumer in required_consumers):
            matched.append(record)
    return tuple(matched)


def _source_lag_policies_for_matched_families(
    records: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    policies: list[dict[str, object]] = []
    for record in records:
        family = str(record.get("family", "")).strip()
        source_profiles = record.get("source_profiles")
        if source_profiles is None:
            continue
        if not isinstance(source_profiles, list):
            raise ValueError(
                f"signal source family catalog {family} source_profiles must be a list"
            )
        for raw_profile in source_profiles:
            if not isinstance(raw_profile, dict):
                raise ValueError(
                    f"signal source family catalog {family} source_profiles "
                    "records must be objects"
                )
            source_id = str(raw_profile.get("source_id", "")).strip()
            if not source_id:
                raise ValueError(
                    f"signal source family catalog {family} source_profiles "
                    "source_id is required"
                )
            max_allowed_lag_days = raw_profile.get("max_allowed_lag_days")
            if max_allowed_lag_days is None:
                continue
            if (
                not isinstance(max_allowed_lag_days, int)
                or isinstance(max_allowed_lag_days, bool)
                or max_allowed_lag_days < 0
            ):
                raise ValueError(
                    f"signal source family catalog {family} source profile "
                    f"{source_id} max_allowed_lag_days must be a non-negative integer"
                )
            policies.append(
                {
                    "family": family,
                    "source_id": source_id,
                    "max_allowed_lag_days": max_allowed_lag_days,
                }
            )
    return tuple(policies)


def _validate_signal_quality_report_source_lag_policies(
    signal_quality_report: dict[str, object],
    *,
    source_catalog: dict[str, object],
) -> None:
    policies = source_catalog.get("matched_source_profile_lag_policies")
    if not isinstance(policies, tuple) or not policies:
        return
    input_sources = signal_quality_report.get("input_sources")
    if not isinstance(input_sources, tuple):
        input_sources = ()
    if (
        signal_quality_report.get("schema_version")
        == "us_equity_public_context_availability_report.v1"
        and not input_sources
    ):
        raise ValueError(
            "signal quality report input_sources are required when source catalog "
            "declares source lag policies"
        )
    if not input_sources:
        return
    report_sources = {
        str(source["source_id"]): source
        for source in input_sources
        if isinstance(source, dict) and str(source.get("source_id", "")).strip()
    }
    for policy in policies:
        if not isinstance(policy, dict):
            continue
        source_id = str(policy.get("source_id", "")).strip()
        if not source_id:
            continue
        if source_id not in report_sources:
            raise ValueError(
                "signal quality report missing source freshness proof for "
                f"{source_id}"
            )
        catalog_max_lag = int(policy["max_allowed_lag_days"])
        report_source = report_sources[source_id]
        report_max_lag = report_source.get("max_allowed_lag_days")
        if not isinstance(report_max_lag, int) or isinstance(report_max_lag, bool):
            raise ValueError(
                "signal quality report source freshness missing max_allowed_lag_days "
                f"for {source_id}"
            )
        if report_max_lag > catalog_max_lag:
            raise ValueError(
                "signal quality report source freshness policy exceeds catalog "
                f"max_allowed_lag_days for {source_id}: "
                f"{report_max_lag} > {catalog_max_lag}"
            )
        latest_lag = report_source.get("latest_observation_lag_days")
        if latest_lag is not None:
            if not isinstance(latest_lag, int) or isinstance(latest_lag, bool):
                raise ValueError(
                    "signal quality report source freshness latest_observation_lag_days "
                    f"for {source_id} must be a non-negative integer"
                )
            if latest_lag > catalog_max_lag:
                raise ValueError(
                    "signal quality report source latest observation exceeds catalog "
                    f"max_allowed_lag_days for {source_id}: "
                    f"{latest_lag} > {catalog_max_lag}"
                )


def _consumer_contract_registry_manifest_record(
    path: Path,
    *,
    required_consumers: tuple[str, ...],
) -> dict[str, object]:
    try:
        summary = signal_consumer_contract_registry_audit_summary_from_manifest(path)
    except SignalBundleContractError as exc:
        raise ValueError(str(exc)) from exc
    consumers = tuple(str(consumer) for consumer in summary["consumers"])
    missing_required_consumers = tuple(
        consumer
        for consumer in required_consumers
        if consumer not in consumers
    )
    if missing_required_consumers:
        raise ValueError(
            "signal consumer contract registry missing required consumers: "
            + ", ".join(missing_required_consumers)
        )
    return {
        "path": summary["manifest_path"],
        "sha256": summary["manifest_sha256"],
        "size_bytes": summary["manifest_size_bytes"],
        "schema_version": summary["manifest_schema_version"],
        "artifact_type": summary["artifact_type"],
        "registry_path": summary["registry_path"],
        "registry_sha256": summary["registry_sha256"],
        "registry_size_bytes": summary["registry_size_bytes"],
        "registry_schema_version": summary["registry_schema_version"],
        "canonical_input": summary["canonical_input"],
        "consumer_count": summary["consumer_count"],
        "consumers": consumers,
        "known_consumer_count": summary["known_consumer_count"],
        "missing_known_consumers": summary["missing_known_consumers"],
        "all_known_consumers_present": summary["all_known_consumers_present"],
        "required_signal_consumers": required_consumers,
        "required_signal_consumer_count": len(required_consumers),
        "missing_required_signal_consumers": missing_required_consumers,
        "required_signal_consumers_present": True,
        "registry_sha256_verified": True,
        "registry_size_bytes_verified": True,
        "registry_contract_fields_verified": True,
    }


def _platform_handoff_manifest_record(
    path: Path,
    *,
    required_consumers: tuple[str, ...],
    expected_transform: str | None,
) -> dict[str, object]:
    try:
        summary = signal_platform_handoff_audit_summary_from_manifest(
            path,
            required_consumers=required_consumers,
            expected_source_transform=expected_transform,
        )
    except SignalBundleContractError as exc:
        raise ValueError(str(exc)) from exc
    return {
        "path": summary["path"],
        "sha256": summary["sha256"],
        "size_bytes": summary["size_bytes"],
        "schema_version": summary["schema_version"],
        "artifact_type": summary["artifact_type"],
        "consumer": summary["consumer"],
        "required_signal_consumers": required_consumers,
        "canonical_input": summary["canonical_input"],
        "bundle_id": summary["bundle_id"],
        "as_of": summary["as_of"],
        "freshness_status": summary["freshness_status"],
        "signal_bundle_manifest_path": summary["signal_bundle_manifest_path"],
        "signal_bundle_manifest_sha256": summary["signal_bundle_manifest_sha256"],
        "source_family_catalog_manifest_path": summary[
            "source_family_catalog_manifest_path"
        ],
        "source_family_catalog_manifest_sha256": summary[
            "source_family_catalog_manifest_sha256"
        ],
        "consumer_contract_registry_manifest_path": summary[
            "consumer_contract_registry_manifest_path"
        ],
        "consumer_contract_registry_manifest_sha256": summary[
            "consumer_contract_registry_manifest_sha256"
        ],
        "source_family_count": summary["source_family_count"],
        "source_families": summary["source_families"],
        "matched_source_families": summary["matched_source_families"],
        "all_known_source_families_present": summary[
            "all_known_source_families_present"
        ],
        "all_consumer_contracts_satisfied": summary[
            "all_consumer_contracts_satisfied"
        ],
        "consumer_contract_count": summary["consumer_contract_count"],
        "consumer_contracts": summary["consumer_contracts"],
        "all_known_consumers_present": summary["all_known_consumers_present"],
        "handoff_linked_manifest_sha256s_verified": summary[
            "handoff_linked_manifest_sha256s_verified"
        ],
        "consumer_registry_contract_fields_verified": summary[
            "consumer_registry_contract_fields_verified"
        ],
    }


def _platform_handoff_index_record(
    path: Path,
    *,
    required_consumers: tuple[str, ...],
    expected_transform: str | None,
    as_of: str | None,
) -> dict[str, object]:
    try:
        summary = signal_platform_handoff_audit_summary_from_index(
            path,
            required_consumers=required_consumers,
            expected_source_transform=expected_transform,
            as_of=as_of,
        )
    except SignalBundleContractError as exc:
        raise ValueError(str(exc)) from exc
    return {
        "path": summary["index_path"],
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
        "schema_version": summary["index_schema_version"],
        "artifact_type": summary["index_artifact_type"],
        "handoff_count": summary["index_handoff_count"],
        "resolved_handoff_manifest_path": summary["handoff_manifest_path"],
        "resolved_handoff_manifest_sha256": summary["handoff_manifest_sha256"],
        "resolved_handoff_schema_version": summary["schema_version"],
        "resolved_handoff_artifact_type": summary["artifact_type"],
        "consumer": summary["consumer"],
        "required_signal_consumers": required_consumers,
        "canonical_input": summary["canonical_input"],
        "bundle_id": summary["bundle_id"],
        "as_of": summary["as_of"],
        "freshness_status": summary["freshness_status"],
        "signal_bundle_manifest_path": summary["signal_bundle_manifest_path"],
        "signal_bundle_manifest_sha256": summary["signal_bundle_manifest_sha256"],
        "source_family_catalog_manifest_path": summary[
            "source_family_catalog_manifest_path"
        ],
        "source_family_catalog_manifest_sha256": summary[
            "source_family_catalog_manifest_sha256"
        ],
        "consumer_contract_registry_manifest_path": summary[
            "consumer_contract_registry_manifest_path"
        ],
        "consumer_contract_registry_manifest_sha256": summary[
            "consumer_contract_registry_manifest_sha256"
        ],
        "source_family_count": summary["source_family_count"],
        "source_families": summary["source_families"],
        "matched_source_families": summary["matched_source_families"],
        "all_known_source_families_present": summary[
            "all_known_source_families_present"
        ],
        "all_consumer_contracts_satisfied": summary[
            "all_consumer_contracts_satisfied"
        ],
        "consumer_contract_count": summary["consumer_contract_count"],
        "consumer_contracts": summary["consumer_contracts"],
        "all_known_consumers_present": summary["all_known_consumers_present"],
        "handoff_linked_manifest_sha256s_verified": summary[
            "handoff_linked_manifest_sha256s_verified"
        ],
        "consumer_registry_contract_fields_verified": summary[
            "consumer_registry_contract_fields_verified"
        ],
    }


def _research_handoff_consumer(
    *,
    required_consumers: tuple[str, ...],
    requested_consumer: str | None,
) -> str:
    normalized = str(requested_consumer or "").strip()
    research_consumers = tuple(
        consumer
        for consumer in required_consumers
        if str(consumer).startswith("research:")
    )
    if normalized:
        if not normalized.startswith("research:"):
            raise ValueError(
                "--research-signal-handoff-consumer must be a research consumer"
            )
        if required_consumers and normalized not in required_consumers:
            raise ValueError(
                "--research-signal-handoff-consumer must be one of the "
                "candidate set compatible consumers"
            )
        return normalized
    if len(research_consumers) == 1:
        return research_consumers[0]
    if not research_consumers:
        raise ValueError(
            "--research-signal-handoff-manifest requires a candidate set with a "
            "research signal consumer"
        )
    raise ValueError(
        "--research-signal-handoff-consumer is required when the candidate set "
        "has multiple compatible research consumers"
    )


def _research_handoff_manifest_record(
    path: Path,
    *,
    consumer: str,
    required_consumers: tuple[str, ...],
    expected_artifact_type: str | None,
    expected_transform: str | None,
) -> dict[str, object]:
    try:
        summary = signal_research_handoff_audit_summary_from_manifest(
            path,
            consumer=consumer,
            expected_research_artifact_type=expected_artifact_type,
        )
    except SignalBundleContractError as exc:
        raise ValueError(str(exc)) from exc
    if (
        expected_transform is not None
        and summary["research_transform"] != expected_transform
    ):
        raise ValueError(
            "research signal handoff transform mismatch: "
            f"{summary['research_transform']!r} != {expected_transform!r}"
        )
    return {
        "path": summary["path"],
        "sha256": summary["sha256"],
        "size_bytes": summary["size_bytes"],
        "schema_version": summary["schema_version"],
        "artifact_type": summary["artifact_type"],
        "consumer": summary["consumer"],
        "required_signal_consumers": required_consumers,
        "research_export_manifest_path": summary["research_export_manifest_path"],
        "research_export_manifest_sha256": summary[
            "research_export_manifest_sha256"
        ],
        "research_artifact_type": summary["research_artifact_type"],
        "research_transform": summary["research_transform"],
        "research_as_of": summary["research_as_of"],
        "research_output_csv_sha256": summary["research_output_csv_sha256"],
        "research_quality_report_sha256": summary[
            "research_quality_report_sha256"
        ],
        "source_family_catalog_manifest_path": summary[
            "source_family_catalog_manifest_path"
        ],
        "source_family_catalog_manifest_sha256": summary[
            "source_family_catalog_manifest_sha256"
        ],
        "consumer_contract_registry_manifest_path": summary[
            "consumer_contract_registry_manifest_path"
        ],
        "consumer_contract_registry_manifest_sha256": summary[
            "consumer_contract_registry_manifest_sha256"
        ],
        "source_family_count": summary["source_family_count"],
        "source_families": summary["source_families"],
        "matched_source_families": summary["matched_source_families"],
        "all_known_source_families_present": summary[
            "all_known_source_families_present"
        ],
        "all_consumer_contracts_satisfied": summary[
            "all_consumer_contracts_satisfied"
        ],
        "consumer_contract_count": summary["consumer_contract_count"],
        "consumer_contracts": summary["consumer_contracts"],
        "all_known_consumers_present": summary["all_known_consumers_present"],
        "research_export_output_csv_verified": summary[
            "research_export_output_csv_verified"
        ],
        "handoff_linked_manifest_sha256s_verified": summary[
            "handoff_linked_manifest_sha256s_verified"
        ],
        "consumer_registry_contract_fields_verified": summary[
            "consumer_registry_contract_fields_verified"
        ],
    }


def _string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _resolve_manifest_artifact_path(
    manifest_path: Path,
    value: str,
    *,
    role: str,
    field: str,
) -> Path:
    raw_path = Path(str(value).strip())
    if not str(value).strip():
        raise ValueError(f"{role} {field} must not be empty")
    if raw_path.is_absolute() or ".." in raw_path.parts:
        raise ValueError(f"{role} {field} must stay inside manifest directory")
    artifact_path = (manifest_path.parent / raw_path).resolve()
    if not artifact_path.exists():
        raise ValueError(f"{role} {field} does not exist: {value}")
    return artifact_path


def _resolve_research_manifest_file_path(
    manifest_path: Path,
    value: str,
    *,
    role: str,
    field: str,
) -> Path:
    raw_path = Path(str(value).strip())
    if not str(value).strip():
        raise ValueError(f"{role} {field} must not be empty")
    if raw_path.is_absolute():
        if raw_path.exists():
            return raw_path.resolve()
        raise ValueError(f"{role} {field} does not exist: {value}")
    manifest_relative_path = (manifest_path.parent / raw_path).resolve()
    if manifest_relative_path.exists():
        return manifest_relative_path
    cwd_relative_path = raw_path.resolve()
    if cwd_relative_path.exists():
        return cwd_relative_path
    raise ValueError(
        f"{role} {field} does not exist relative to manifest or cwd: {value}"
    )


def _validate_manifest_expectations(
    manifest: dict[str, object],
    *,
    schema_version: str,
    role: str,
    expected_artifact_type: str | None,
    expected_transform: str | None,
) -> None:
    if expected_artifact_type is None and expected_transform is None:
        return
    if schema_version != RESEARCH_EXPORT_SCHEMA_VERSION:
        raise ValueError(
            f"{role} manifest schema_version must be {RESEARCH_EXPORT_SCHEMA_VERSION!r} "
            "for this candidate set"
        )
    artifact_type = str(manifest.get("artifact_type", "")).strip()
    if expected_artifact_type is not None and artifact_type != expected_artifact_type:
        raise ValueError(
            f"{role} manifest artifact_type mismatch: "
            f"expected {expected_artifact_type!r}, got {artifact_type!r}"
        )
    transform = str(manifest.get("transform", "")).strip()
    if expected_transform is not None and transform != expected_transform:
        raise ValueError(
            f"{role} manifest transform mismatch: "
            f"expected {expected_transform!r}, got {transform!r}"
        )


def _validate_research_export_manifest(
    manifest: dict[str, object],
    *,
    linked_csv_shape: dict[str, object],
    role: str,
) -> None:
    required_fields = (
        "artifact_type",
        "transform",
        "source_version",
        "row_count",
        "first_date",
        "last_date",
        "columns",
        "output_csv",
    )
    for field in required_fields:
        if field not in manifest:
            raise ValueError(f"{role} research export manifest missing field: {field}")
    if not str(manifest.get("artifact_type", "")).strip():
        raise ValueError(f"{role} research export manifest artifact_type is required")
    if not str(manifest.get("transform", "")).strip():
        raise ValueError(f"{role} research export manifest transform is required")
    row_count = manifest.get("row_count")
    if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 0:
        raise ValueError(
            f"{role} research export manifest row_count must be non-negative"
        )
    columns = manifest.get("columns")
    if not isinstance(columns, list) or not all(
        isinstance(column, str) and column.strip()
        for column in columns
    ):
        raise ValueError(f"{role} research export manifest columns must be strings")
    declared_columns = tuple(str(column) for column in columns)
    linked_csv_columns = linked_csv_shape["columns"]
    if declared_columns != linked_csv_columns:
        raise ValueError(
            f"{role} research export manifest columns mismatch: "
            f"expected {linked_csv_columns}, got {declared_columns}"
        )
    if int(row_count) != linked_csv_shape["row_count"]:
        raise ValueError(
            f"{role} research export manifest row_count mismatch: "
            f"expected {linked_csv_shape['row_count']}, got {row_count}"
        )
    first_date = str(manifest.get("first_date", ""))
    if first_date != linked_csv_shape["first_date"]:
        raise ValueError(
            f"{role} research export manifest first_date mismatch: "
            f"expected {linked_csv_shape['first_date']!r}, got {first_date!r}"
        )
    last_date = str(manifest.get("last_date", ""))
    if last_date != linked_csv_shape["last_date"]:
        raise ValueError(
            f"{role} research export manifest last_date mismatch: "
            f"expected {linked_csv_shape['last_date']!r}, got {last_date!r}"
        )


def _validate_signal_research_csv_contract(
    path: Path,
    *,
    manifest: dict[str, object],
    linked_csv_shape: dict[str, object],
    date_column: str,
    expected_artifact_type: str | None,
    required_signal_fields: tuple[str, ...] = (),
    validation_window: tuple[pd.Timestamp | None, pd.Timestamp | None] = (None, None),
) -> dict[str, object]:
    if expected_artifact_type not in {
        "btc_cycle_research_csv",
        "us_equity_context_research_csv",
    }:
        return {}

    frame = pd.read_csv(path)
    if date_column not in frame.columns:
        raise ValueError(f"signal CSV missing date column {date_column!r}: {path}")
    dates = pd.to_datetime(frame[date_column], errors="coerce", utc=True)
    if dates.isna().any():
        raise ValueError(f"signal CSV contains invalid dates in {date_column!r}: {path}")
    normalized_dates = pd.DatetimeIndex(dates).tz_convert(None).normalize()
    if normalized_dates.duplicated().any():
        raise ValueError("signal CSV contains duplicate dates")
    if not normalized_dates.is_monotonic_increasing:
        raise ValueError("signal CSV dates must be monotonically increasing")

    as_of = str(manifest.get("as_of", "")).strip()
    if not as_of:
        raise ValueError("signal research export manifest as_of is required")
    last_date = str(linked_csv_shape["last_date"])
    if pd.Timestamp(last_date).normalize() > pd.Timestamp(as_of).normalize():
        raise ValueError(
            "signal CSV last_date must not be after manifest as_of: "
            f"{last_date} > {as_of}"
        )

    validation_frame, validation_dates = _contract_validation_frame(
        frame,
        normalized_dates,
        validation_window=validation_window,
    )
    if expected_artifact_type == "us_equity_context_research_csv":
        _validate_us_equity_context_signal_columns(
            validation_frame,
            required_columns=required_signal_fields,
        )
    if expected_artifact_type == "btc_cycle_research_csv":
        _validate_btc_cycle_signal_columns(
            validation_frame,
            required_columns=required_signal_fields,
        )
    return _signal_contract_validation_record(
        validation_dates,
        required_columns=required_signal_fields,
        validation_window=validation_window,
    )


def _required_us_equity_context_fields(consumers: tuple[str, ...]) -> tuple[str, ...]:
    fields: set[str] = set()
    for consumer in consumers:
        for symbol, required_fields in required_indicator_fields_for_consumer(
            consumer
        ).items():
            if symbol == "US-EQUITY-CONTEXT":
                fields.update(str(field) for field in required_fields)
    return tuple(sorted(fields))


def _required_btc_cycle_fields(consumers: tuple[str, ...]) -> tuple[str, ...]:
    fields: set[str] = set()
    for consumer in consumers:
        for symbol, required_fields in required_indicator_fields_for_consumer(
            consumer
        ).items():
            if symbol == "BTC-USD":
                fields.update(str(field) for field in required_fields)
    return tuple(sorted(fields))


def _required_signal_fields_for_artifact_type(
    artifact_type: str | None,
    *,
    requested_signal_columns: tuple[str, ...] | None,
    required_consumers: tuple[str, ...],
) -> tuple[str, ...]:
    if artifact_type == "us_equity_context_research_csv":
        return _required_us_equity_context_fields(required_consumers)
    if artifact_type == "btc_cycle_research_csv":
        if requested_signal_columns is not None:
            return requested_signal_columns
        return _required_btc_cycle_fields(required_consumers)
    return ()


def _validate_us_equity_context_signal_columns(
    frame: pd.DataFrame,
    *,
    required_columns: tuple[str, ...] = (),
) -> None:
    columns = required_columns or (
        "cape_percentile",
        "vix_percentile",
        "breadth_above_sma200_pct",
    )
    for column in columns:
        values = _finite_numeric_column(frame, column, role="signal CSV")
        if not values.between(0.0, 1.0).all():
            raise ValueError(f"signal CSV column {column!r} must be between 0 and 1")


def _validate_btc_cycle_signal_columns(
    frame: pd.DataFrame,
    *,
    required_columns: tuple[str, ...] = (),
) -> None:
    positive_columns = ("ahr999", "ahr999_sma", "mayer_multiple")
    percentile_columns = ("ahr999_365d_percentile", "mayer_multiple_365d_percentile")
    slope_columns = ("ahr999_30d_slope",)
    columns = required_columns or tuple(
        column
        for column in (*positive_columns, *percentile_columns, *slope_columns)
        if column in frame.columns
    )
    for column in columns:
        if column in positive_columns:
            values = _finite_numeric_column(frame, column, role="signal CSV")
            if not (values > 0.0).all():
                raise ValueError(f"signal CSV column {column!r} must be positive")
        elif column in percentile_columns:
            values = _finite_numeric_column(frame, column, role="signal CSV")
            if not values.between(0.0, 1.0).all():
                raise ValueError(
                    f"signal CSV column {column!r} must be between 0 and 1"
                )
        elif column in slope_columns:
            _finite_numeric_column(frame, column, role="signal CSV")
        else:
            _finite_numeric_column(frame, column, role="signal CSV")


def _contract_validation_frame(
    frame: pd.DataFrame,
    dates: pd.DatetimeIndex,
    *,
    validation_window: tuple[pd.Timestamp | None, pd.Timestamp | None],
) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    start, end = validation_window
    mask = pd.Series(True, index=frame.index)
    if start is not None:
        mask &= pd.Series(dates >= start.normalize(), index=frame.index)
    if end is not None:
        mask &= pd.Series(dates <= end.normalize(), index=frame.index)
    scoped = frame.loc[mask]
    if scoped.empty:
        raise ValueError("signal CSV has no rows in contract validation window")
    scoped_dates = pd.DatetimeIndex(dates[mask.to_numpy()])
    return scoped, scoped_dates


def _signal_contract_validation_record(
    dates: pd.DatetimeIndex,
    *,
    required_columns: tuple[str, ...],
    validation_window: tuple[pd.Timestamp | None, pd.Timestamp | None],
) -> dict[str, object]:
    start, end = validation_window
    return {
        "signal_contract_validation_window_start_date": (
            "" if start is None else start.date().isoformat()
        ),
        "signal_contract_validation_window_end_date": (
            "" if end is None else end.date().isoformat()
        ),
        "signal_contract_validation_first_date": dates[0].date().isoformat(),
        "signal_contract_validation_last_date": dates[-1].date().isoformat(),
        "signal_contract_validation_row_count": len(dates),
        "signal_contract_validation_required_columns": required_columns,
    }


def _finite_numeric_column(
    frame: pd.DataFrame,
    column: str,
    *,
    role: str,
) -> pd.Series:
    if column not in frame.columns:
        raise ValueError(f"{role} missing required column: {column}")
    values = pd.to_numeric(frame[column], errors="coerce")
    finite = values.notna() & (values != float("inf")) & (values != float("-inf"))
    if not finite.all():
        raise ValueError(f"{role} column {column!r} must contain finite numbers")
    return values.astype(float)


def _csv_shape_record(path: Path, *, date_column: str) -> dict[str, object]:
    with path.open(newline="", encoding="utf-8") as file_obj:
        reader = csv.reader(file_obj)
        try:
            header = tuple(next(reader))
        except StopIteration as exc:
            raise ValueError(f"CSV is empty: {path}") from exc
        date_index = header.index(date_column) if date_column in header else None
        row_count = 0
        first_date = ""
        last_date = ""
        for row in reader:
            row_count += 1
            if date_index is None:
                continue
            if date_index >= len(row):
                raise ValueError(f"CSV row missing date column {date_column!r}: {path}")
            if not first_date:
                first_date = str(row[date_index])
            last_date = str(row[date_index])
    return {
        "columns": header,
        "row_count": row_count,
        "first_date": first_date,
        "last_date": last_date,
    }


def _read_manifest(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as file_obj:
        payload = json.load(file_obj)
    if not isinstance(payload, dict):
        raise ValueError(f"manifest JSON root must be an object: {path}")
    return payload


def _validate_no_sensitive_manifest_fields(value: object, *, path: str) -> None:
    if isinstance(value, dict):
        for raw_key, item in value.items():
            key = str(raw_key).strip().lower()
            if any(fragment in key for fragment in _FORBIDDEN_MANIFEST_KEY_FRAGMENTS):
                raise ValueError(f"sensitive field is not allowed in {path}: {raw_key}")
            _validate_no_sensitive_manifest_fields(item, path=f"{path}.{raw_key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_no_sensitive_manifest_fields(item, path=f"{path}[{index}]")


def _file_record(path: Path) -> dict[str, object]:
    return {
        "path": str(path),
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_execution_days(raw: str) -> tuple[int, ...]:
    days: list[int] = []
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        try:
            day = int(value)
        except ValueError as exc:
            raise ValueError(f"execution day must be an integer: {value!r}") from exc
        if day < 1 or day > 31:
            raise ValueError(f"execution day must be between 1 and 31: {day}")
        days.append(day)
    if not days:
        raise ValueError("execution-days must include at least one day")
    return tuple(days)


def _parse_contribution_values(raw: str | None) -> tuple[float, ...] | None:
    if raw is None:
        return None
    amounts: list[float] = []
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        try:
            amount = float(value)
        except ValueError as exc:
            raise ValueError(f"monthly contribution must be numeric: {value!r}") from exc
        if amount <= 0.0:
            raise ValueError(f"monthly contribution must be positive: {amount}")
        amounts.append(amount)
    if not amounts:
        raise ValueError("monthly-contribution-usd-values must include at least one amount")
    return tuple(amounts)


def _parse_start_dates(raw: str | None) -> tuple[pd.Timestamp, ...] | None:
    if raw is None:
        return None
    dates: list[pd.Timestamp] = []
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        timestamp = pd.Timestamp(value)
        if pd.isna(timestamp):
            raise ValueError(f"start date must be parseable: {value!r}")
        dates.append(timestamp.tz_localize(None).normalize())
    if not dates:
        raise ValueError("start-dates must include at least one date")
    return tuple(dates)


def _parse_cadences(raw: str) -> tuple[str, ...]:
    values = tuple(item.strip().lower() for item in raw.split(",") if item.strip())
    if not values:
        raise ValueError("cadences must include at least one cadence")
    unsupported = [value for value in values if value not in SUPPORTED_DCA_CADENCES]
    if unsupported:
        raise ValueError(
            "unsupported cadences: "
            + ", ".join(unsupported)
            + "; supported: "
            + ", ".join(sorted(SUPPORTED_DCA_CADENCES))
        )
    return values


def _load_signal_frame(
    path: Path,
    *,
    date_column: str,
    signal_columns: tuple[str, ...] | None,
) -> pd.DataFrame:
    frame = _read_csv_with_date_index(path, date_column=date_column)
    columns = signal_columns or tuple(frame.columns)
    _require_columns(frame, columns, role="signal")
    result = frame.loc[:, list(columns)].apply(pd.to_numeric, errors="coerce")
    result = result.dropna(how="all")
    if result.empty:
        raise ValueError(f"signal CSV has no numeric signal values: {path}")
    return result


def _load_trade_series(
    path: Path,
    *,
    date_column: str,
    trade_column: str | None,
) -> pd.Series:
    frame = _read_csv_with_date_index(path, date_column=date_column)
    column = trade_column or _default_trade_column(frame)
    _require_columns(frame, (column,), role="trade")
    result = pd.to_numeric(frame[column], errors="coerce").dropna()
    result = result[result > 0.0]
    if result.empty:
        raise ValueError(f"trade CSV has no positive trade prices: {path}")
    return result


def _read_csv_with_date_index(path: Path, *, date_column: str) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if date_column not in frame.columns:
        raise ValueError(f"CSV missing date column {date_column!r}: {path}")
    index = pd.to_datetime(frame[date_column], errors="coerce", utc=True)
    if index.isna().any():
        raise ValueError(f"CSV contains invalid dates in {date_column!r}: {path}")
    frame = frame.drop(columns=[date_column])
    frame.index = pd.DatetimeIndex(index).tz_convert(None).normalize()
    frame = frame.sort_index()
    return frame.groupby(level=0).last()


def _default_trade_column(frame: pd.DataFrame) -> str:
    normalized = {str(column).strip().lower(): str(column) for column in frame.columns}
    for candidate in ("close", "adj_close", "adj close"):
        if candidate in normalized:
            return normalized[candidate]
    if len(frame.columns) == 1:
        return str(frame.columns[0])
    raise ValueError(
        "trade CSV must include --trade-column when it has multiple non-date columns"
    )


def _require_columns(frame: pd.DataFrame, columns: Sequence[str], *, role: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        available = ", ".join(str(column) for column in frame.columns)
        raise ValueError(f"missing {role} columns {missing}; available columns: {available}")


if __name__ == "__main__":
    raise SystemExit(main())
