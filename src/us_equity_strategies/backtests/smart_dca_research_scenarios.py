from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
import hashlib
import json
from os import PathLike
from pathlib import Path
from typing import Any

import pandas as pd

from .smart_dca_research_models import (
    DcaCandidateEvaluation,
    DcaResearchResult,
    PRESET_CANDIDATES,
    _resolve_candidate_names,
    SMART_DCA_RESEARCH_ARTIFACT_SCHEMA_VERSION,
    SmartDcaCandidate,
    candidate_specs_to_rows,
    candidate_summaries_to_rows,
)
from .smart_dca_research_reporting import (
    evaluate_candidate_results,
    results_to_cash_flow_rows,
    results_to_decision_log_rows,
    results_to_equity_curve_rows,
    results_to_metrics_rows,
    scenario_results_to_coverage_rows,
    scenario_results_to_review_decision,
    scenario_results_to_robustness_rows,
    scenario_results_to_selection_rows,
    summarize_candidate_evaluations,
)
from .smart_dca_research_signals import (
    _candidate_signal_frame,
    _close_series,
    _normalize_symbol,
    _price_frame,
)
from .smart_dca_research_simulation import (
    _cadence_contribution_amount,
    _normalize_cadence,
    _period_start_dates,
    _run_path,
    _scheduled_execution_dates,
    _with_relative_value,
)


def write_research_artifacts(
    output_dir: str | PathLike[str],
    results: Mapping[str, DcaResearchResult],
    *,
    fixed_name: str = "fixed",
    evaluations: Mapping[str, DcaCandidateEvaluation] | None = None,
) -> dict[str, Path]:
    """Write metrics, evaluation summary, and decision log CSV artifacts."""

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    resolved_evaluations = (
        evaluate_candidate_results(results, fixed_name=fixed_name)
        if evaluations is None
        else evaluations
    )
    metrics_path = output_path / "metrics.csv"
    evaluation_path = output_path / "evaluation_summary.csv"
    decision_log_path = output_path / "decision_log.csv"
    equity_curve_path = output_path / "equity_curve.csv"
    cash_flows_path = output_path / "cash_flows.csv"
    candidate_summary_path = output_path / "candidate_summary.csv"
    candidate_specs_path = output_path / "candidate_specs.csv"
    run_manifest_path = output_path / "run_manifest.json"
    candidate_names = tuple(name for name in results if name != fixed_name)

    pd.DataFrame(results_to_metrics_rows(results, fixed_name=fixed_name, evaluations=resolved_evaluations)).to_csv(
        metrics_path,
        index=False,
    )
    pd.DataFrame(
        {
            "name": item.name,
            "passed": item.passed,
            "reasons": ",".join(item.reasons),
            "relative_terminal_value_pct": item.relative_terminal_value_pct,
            "max_drawdown_delta_pct_points": item.max_drawdown_delta_pct_points,
            "skipped_buy_ratio": item.skipped_buy_ratio,
            "deployment_rate_delta_pct_points": item.deployment_rate_delta_pct_points,
            "rank_score": item.rank_score,
        }
        for item in summarize_candidate_evaluations(resolved_evaluations)
    ).to_csv(evaluation_path, index=False)
    pd.DataFrame(results_to_decision_log_rows(results)).to_csv(decision_log_path, index=False)
    pd.DataFrame(results_to_equity_curve_rows(results)).to_csv(equity_curve_path, index=False)
    pd.DataFrame(results_to_cash_flow_rows(results)).to_csv(cash_flows_path, index=False)
    pd.DataFrame(candidate_summaries_to_rows(candidate_names)).to_csv(candidate_summary_path, index=False)
    pd.DataFrame(candidate_specs_to_rows(candidate_names)).to_csv(candidate_specs_path, index=False)
    _write_artifact_manifest(
        run_manifest_path,
        artifact_type="smart_dca_research_run",
        files=(
            metrics_path,
            evaluation_path,
            decision_log_path,
            equity_curve_path,
            cash_flows_path,
            candidate_summary_path,
            candidate_specs_path,
        ),
        root=output_path,
        extra={
            "fixed_name": fixed_name,
            "result_names": tuple(results),
            "candidate_names": candidate_names,
        },
    )
    return {
        "metrics": metrics_path,
        "evaluation_summary": evaluation_path,
        "decision_log": decision_log_path,
        "equity_curve": equity_curve_path,
        "cash_flows": cash_flows_path,
        "candidate_summary": candidate_summary_path,
        "candidate_specs": candidate_specs_path,
        "run_manifest": run_manifest_path,
    }


def write_scenario_research_artifacts(
    output_dir: str | PathLike[str],
    scenarios: Mapping[str, Mapping[str, DcaResearchResult]],
    *,
    fixed_name: str = "fixed",
    metadata: Mapping[str, object] | None = None,
    min_review_scenarios: int = 3,
) -> dict[str, Path]:
    """Write per-scenario artifacts and a top-level scenario index CSV."""

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    index_rows: list[dict[str, object]] = []
    artifact_paths: dict[str, Path] = {}

    for scenario_name, results in scenarios.items():
        safe_name = str(scenario_name).replace("/", "_").replace("\\", "_")
        scenario_dir = output_path / safe_name
        paths = write_research_artifacts(scenario_dir, results, fixed_name=fixed_name)
        evaluations = evaluate_candidate_results(results, fixed_name=fixed_name)
        for evaluation in summarize_candidate_evaluations(evaluations):
            index_rows.append(
                {
                    "scenario": scenario_name,
                    "name": evaluation.name,
                    "passed": evaluation.passed,
                    "reasons": ",".join(evaluation.reasons),
                    "relative_terminal_value_pct": evaluation.relative_terminal_value_pct,
                    "max_drawdown_delta_pct_points": evaluation.max_drawdown_delta_pct_points,
                    "skipped_buy_ratio": evaluation.skipped_buy_ratio,
                    "deployment_rate_delta_pct_points": evaluation.deployment_rate_delta_pct_points,
                    "rank_score": evaluation.rank_score,
                    "metrics_path": paths["metrics"].relative_to(output_path).as_posix(),
                    "evaluation_summary_path": paths["evaluation_summary"].relative_to(output_path).as_posix(),
                    "decision_log_path": paths["decision_log"].relative_to(output_path).as_posix(),
                    "candidate_summary_path": paths["candidate_summary"].relative_to(output_path).as_posix(),
                }
            )
        for key, path in paths.items():
            artifact_paths[f"{safe_name}_{key}"] = path

    scenario_index_path = output_path / "scenario_index.csv"
    robustness_summary_path = output_path / "robustness_summary.csv"
    selection_summary_path = output_path / "selection_summary.csv"
    scenario_coverage_path = output_path / "scenario_coverage.csv"
    production_profile_decisions_path = output_path / "production_profile_decisions.csv"
    review_decision_path = output_path / "review_decision.json"
    scenario_manifest_path = output_path / "scenario_manifest.json"
    pd.DataFrame(index_rows).to_csv(scenario_index_path, index=False)
    pd.DataFrame(
        scenario_results_to_robustness_rows(scenarios, fixed_name=fixed_name)
    ).to_csv(robustness_summary_path, index=False)
    pd.DataFrame(
        scenario_results_to_selection_rows(
            scenarios,
            fixed_name=fixed_name,
            min_review_scenarios=min_review_scenarios,
        )
    ).to_csv(selection_summary_path, index=False)
    pd.DataFrame(
        scenario_results_to_coverage_rows(
            scenarios,
            fixed_name=fixed_name,
            min_review_scenarios=min_review_scenarios,
        )
    ).to_csv(scenario_coverage_path, index=False)
    review_decision = scenario_results_to_review_decision(
        scenarios,
        fixed_name=fixed_name,
        min_review_scenarios=min_review_scenarios,
    )
    pd.DataFrame(review_decision["production_profile_decisions"]).to_csv(
        production_profile_decisions_path,
        index=False,
    )
    review_decision_path.write_text(
        json.dumps(review_decision, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    artifact_paths["scenario_index"] = scenario_index_path
    artifact_paths["robustness_summary"] = robustness_summary_path
    artifact_paths["selection_summary"] = selection_summary_path
    artifact_paths["scenario_coverage"] = scenario_coverage_path
    artifact_paths["production_profile_decisions"] = production_profile_decisions_path
    artifact_paths["review_decision"] = review_decision_path
    _write_artifact_manifest(
        scenario_manifest_path,
        artifact_type="smart_dca_research_scenario_matrix",
        files=tuple(artifact_paths.values()),
        root=output_path,
        extra={
            "fixed_name": fixed_name,
            "min_review_scenarios": min_review_scenarios,
            "scenario_names": tuple(scenarios),
            "metadata": dict(metadata or {}),
        },
    )
    artifact_paths["scenario_manifest"] = scenario_manifest_path
    return artifact_paths


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_file_record(path: Path, *, root: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _write_artifact_manifest(
    path: Path,
    *,
    artifact_type: str,
    files: Iterable[Path],
    root: Path,
    extra: Mapping[str, object],
) -> None:
    manifest = {
        "schema_version": SMART_DCA_RESEARCH_ARTIFACT_SCHEMA_VERSION,
        "artifact_type": artifact_type,
        "files": tuple(_artifact_file_record(item, root=root) for item in files),
        **dict(extra),
    }
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def compare_smart_dca_candidates(
    *,
    signal_prices: Any,
    trade_prices: Any,
    candidate_set: str | Iterable[str] = "nasdaq_sp500_price",
    monthly_contribution_usd: float = 1000.0,
    start_date: object | None = None,
    end_date: object | None = None,
    align_start_after_warmup: bool = True,
    min_investment_usd: float = 0.0,
    monthly_execution_day: int = 1,
    cadence: str = "monthly",
) -> dict[str, DcaResearchResult]:
    """Compare fixed DCA with a small named preset universe of smart DCA rules.

    The helper is intentionally offline and anti-overfit oriented: callers pass
    pandas-compatible price inputs, and candidate rules come only from
    PRESET_CANDIDATES rather than an open parameter search.
    """

    if monthly_contribution_usd <= 0.0:
        raise ValueError("monthly_contribution_usd must be positive")

    candidate_names = _resolve_candidate_names(candidate_set)
    candidates = tuple(PRESET_CANDIDATES[name] for name in candidate_names)
    trade_series = _close_series(trade_prices)
    signal_frame = _price_frame(signal_prices)
    if trade_series.empty:
        raise ValueError("trade_prices must contain at least one positive price")
    if signal_frame.empty:
        raise ValueError("signal_prices must contain at least one positive price series")

    candidate_frames = {
        candidate.name: _candidate_signal_frame(signal_frame, candidate)
        for candidate in candidates
    }
    common_index = trade_series.index
    for frame in candidate_frames.values():
        common_index = common_index.intersection(frame.dropna(how="any").index)
    common_index = common_index.sort_values()

    if align_start_after_warmup and candidates:
        warmup = max(candidate.min_history for candidate in candidates)
        # First simulated session needs this many earlier sessions of signal.
        if len(common_index) > warmup:
            warmup_start = common_index[warmup]
            common_index = common_index[common_index >= warmup_start]
        else:
            common_index = common_index[:0]

    if start_date is not None:
        start = pd.Timestamp(start_date).tz_localize(None).normalize()
        common_index = common_index[common_index >= start]
    if end_date is not None:
        end = pd.Timestamp(end_date).tz_localize(None).normalize()
        common_index = common_index[common_index <= end]

    if common_index.empty:
        raise ValueError("no overlapping price history remains after date and warmup filters")

    trade_path = trade_series.loc[common_index]
    normalized_cadence = _normalize_cadence(cadence)
    contribution_dates = _period_start_dates(trade_path.index, normalized_cadence)
    execution_dates = _scheduled_execution_dates(
        trade_path.index,
        cadence=normalized_cadence,
        monthly_execution_day=monthly_execution_day,
    )
    contribution_amount_usd = _cadence_contribution_amount(
        monthly_contribution_usd,
        normalized_cadence,
    )
    results: dict[str, DcaResearchResult] = {
        "fixed": _run_path(
            name="fixed",
            trade_prices=trade_path,
            signal_prices=None,
            contribution_amount_usd=contribution_amount_usd,
            candidate=None,
            min_investment_usd=min_investment_usd,
            contribution_dates=contribution_dates,
            execution_dates=execution_dates,
        )
    }

    for candidate in candidates:
        results[candidate.name] = _run_path(
            name=candidate.name,
            trade_prices=trade_path,
            signal_prices=candidate_frames[candidate.name],
            contribution_amount_usd=contribution_amount_usd,
            candidate=candidate,
            min_investment_usd=min_investment_usd,
            contribution_dates=contribution_dates,
            execution_dates=execution_dates,
        )

    return _with_relative_value(results, fixed_name="fixed")


def compare_monthly_execution_day_scenarios(
    *,
    signal_prices: Any,
    trade_prices: Any,
    execution_days: Iterable[int] = (1, 10, 15, 20, 25),
    candidate_set: str | Iterable[str] = "nasdaq_sp500_price",
    monthly_contribution_usd: float = 1000.0,
    start_date: object | None = None,
    end_date: object | None = None,
    align_start_after_warmup: bool = True,
    min_investment_usd: float = 0.0,
    cadence: str = "monthly",
) -> dict[str, dict[str, DcaResearchResult]]:
    """Run the same fixed candidate universe across monthly execution days."""

    normalized_cadence = _normalize_cadence(cadence)
    scenarios: dict[str, dict[str, DcaResearchResult]] = {}
    for raw_day in execution_days:
        day = int(max(1, min(31, raw_day)))
        scenarios[f"{normalized_cadence}_day_{day}"] = compare_smart_dca_candidates(
            signal_prices=signal_prices,
            trade_prices=trade_prices,
            candidate_set=candidate_set,
            monthly_contribution_usd=monthly_contribution_usd,
            start_date=start_date,
            end_date=end_date,
            align_start_after_warmup=align_start_after_warmup,
            min_investment_usd=min_investment_usd,
            monthly_execution_day=day,
            cadence=normalized_cadence,
        )
    return scenarios


def compare_execution_day_contribution_scenarios(
    *,
    signal_prices: Any,
    trade_prices: Any,
    execution_days: Iterable[int] = (1, 10, 15, 20, 25),
    monthly_contribution_usd_values: Iterable[float] = (500.0, 1000.0, 3000.0),
    start_dates: Iterable[object] | None = None,
    cadences: Iterable[str] = ("monthly",),
    candidate_set: str | Iterable[str] = "nasdaq_sp500_price",
    start_date: object | None = None,
    end_date: object | None = None,
    align_start_after_warmup: bool = True,
    min_investment_usd: float = 0.0,
) -> dict[str, dict[str, DcaResearchResult]]:
    """Run a fixed candidate universe across execution-day and contribution scales.

    This is a bounded anti-overfit robustness matrix. It does not search
    thresholds; it repeats the same preset rules across plausible monthly cash
    contribution sizes and monthly execution days.
    """

    amounts = tuple(float(value) for value in monthly_contribution_usd_values)
    if not amounts:
        raise ValueError("monthly_contribution_usd_values must include at least one amount")
    invalid_amounts = [value for value in amounts if value <= 0.0]
    if invalid_amounts:
        raise ValueError(
            "monthly_contribution_usd_values must be positive: "
            + ", ".join(str(value) for value in invalid_amounts)
        )

    resolved_start_dates = _scenario_start_dates(start_dates, fallback_start_date=start_date)
    resolved_cadences = _scenario_cadences(cadences)
    resolved_execution_days = tuple(execution_days)
    if not resolved_execution_days:
        raise ValueError("execution_days must include at least one day")

    scenarios: dict[str, dict[str, DcaResearchResult]] = {}
    for scenario_start_date in resolved_start_dates:
        for cadence in resolved_cadences:
            cadence_days = (None,) if cadence == "weekly" else resolved_execution_days
            for amount in amounts:
                for raw_day in cadence_days:
                    day = 1 if raw_day is None else int(max(1, min(31, raw_day)))
                    amount_label = _scenario_amount_label(amount)
                    if cadence == "weekly":
                        scenario_name = f"weekly_contribution_usd_{amount_label}"
                    else:
                        scenario_name = f"{cadence}_day_{day}_contribution_usd_{amount_label}"
                    if scenario_start_date is not None:
                        scenario_name += f"_start_{_scenario_date_label(scenario_start_date)}"
                    scenarios[scenario_name] = compare_smart_dca_candidates(
                        signal_prices=signal_prices,
                        trade_prices=trade_prices,
                        candidate_set=candidate_set,
                        monthly_contribution_usd=amount,
                        start_date=scenario_start_date,
                        end_date=end_date,
                        align_start_after_warmup=align_start_after_warmup,
                        min_investment_usd=min_investment_usd,
                        monthly_execution_day=day,
                        cadence=cadence,
                    )
    return scenarios


def compare_sample_window_scenarios(
    *,
    signal_prices: Any,
    trade_prices: Any,
    sample_windows: Mapping[str, tuple[object | None, object | None]]
    | Iterable[tuple[str, object | None, object | None]],
    execution_days: Iterable[int] = (1, 10, 15, 20, 25),
    monthly_contribution_usd_values: Iterable[float] = (500.0, 1000.0, 3000.0),
    start_dates: Iterable[object] | None = None,
    cadences: Iterable[str] = ("monthly",),
    candidate_set: str | Iterable[str] = "nasdaq_sp500_price",
    start_date: object | None = None,
    end_date: object | None = None,
    align_start_after_warmup: bool = True,
    min_investment_usd: float = 0.0,
) -> dict[str, dict[str, DcaResearchResult]]:
    """Run the robustness matrix across named sample windows.

    Sample windows are fixed before the run and become an explicit scenario
    dimension. This supports discovery/validation/out-of-sample and stress-window
    checks without adding any parameter search.
    """

    windows = _scenario_sample_windows(sample_windows)
    base_start_dates = _scenario_start_dates(
        start_dates,
        fallback_start_date=start_date,
    )
    scenarios: dict[str, dict[str, DcaResearchResult]] = {}
    for label, window_start, window_end in windows:
        effective_start_dates = _unique_optional_scenario_dates(
            _latest_scenario_date(window_start, base_start_date)
            for base_start_date in base_start_dates
        )
        effective_end_date = _earliest_scenario_date(window_end, end_date)
        if (
            effective_end_date is not None
            and all(
                start is not None and start > effective_end_date
                for start in effective_start_dates
            )
        ):
            raise ValueError(
                f"sample window {label!r} has no overlap after start/end filters"
            )
        window_scenarios = compare_execution_day_contribution_scenarios(
            signal_prices=signal_prices,
            trade_prices=trade_prices,
            execution_days=execution_days,
            monthly_contribution_usd_values=monthly_contribution_usd_values,
            start_dates=effective_start_dates,
            cadences=cadences,
            candidate_set=candidate_set,
            start_date=None,
            end_date=effective_end_date,
            align_start_after_warmup=align_start_after_warmup,
            min_investment_usd=min_investment_usd,
        )
        prefix = f"sample_window_{label}__"
        for scenario_name, results in window_scenarios.items():
            scenarios[f"{prefix}{scenario_name}"] = results
    return scenarios


def _scenario_sample_windows(
    sample_windows: Mapping[str, tuple[object | None, object | None]]
    | Iterable[tuple[str, object | None, object | None]],
) -> tuple[tuple[str, pd.Timestamp | None, pd.Timestamp | None], ...]:
    if isinstance(sample_windows, Mapping):
        raw_items = tuple(
            (label, bounds[0], bounds[1])
            for label, bounds in sample_windows.items()
        )
    else:
        raw_items = tuple(sample_windows)
    if not raw_items:
        raise ValueError("sample_windows must include at least one window")

    normalized: list[tuple[str, pd.Timestamp | None, pd.Timestamp | None]] = []
    seen_labels: set[str] = set()
    for label, raw_start, raw_end in raw_items:
        normalized_label = _scenario_text_label(label)
        if normalized_label in seen_labels:
            raise ValueError(f"duplicate sample window label: {label!r}")
        seen_labels.add(normalized_label)
        start = None if raw_start is None else _normalize_scenario_date(raw_start)
        end = None if raw_end is None else _normalize_scenario_date(raw_end)
        if start is None and end is None:
            raise ValueError(f"sample window {label!r} must include start or end")
        if start is not None and end is not None and start > end:
            raise ValueError(f"sample window {label!r} start must be <= end")
        normalized.append((normalized_label, start, end))
    return tuple(normalized)


def _scenario_text_label(value: object) -> str:
    raw = str(value or "").strip().lower()
    label = "".join(char if char.isalnum() else "_" for char in raw).strip("_")
    while "__" in label:
        label = label.replace("__", "_")
    if not label:
        raise ValueError("sample window label must not be empty")
    return label


def _latest_scenario_date(
    left: pd.Timestamp | None,
    right: pd.Timestamp | None,
) -> pd.Timestamp | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


def _unique_optional_scenario_dates(
    dates: Iterable[pd.Timestamp | None],
) -> tuple[pd.Timestamp | None, ...]:
    values: list[pd.Timestamp | None] = []
    for date in dates:
        if date not in values:
            values.append(date)
    return tuple(values)


def _earliest_scenario_date(
    left: pd.Timestamp | None,
    right: object | None,
) -> pd.Timestamp | None:
    normalized_right = None if right is None else _normalize_scenario_date(right)
    if left is None:
        return normalized_right
    if normalized_right is None:
        return left
    return min(left, normalized_right)


def _scenario_amount_label(amount: float) -> str:
    value = float(amount)
    if value.is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", "_")


def _scenario_start_dates(
    start_dates: Iterable[object] | None,
    *,
    fallback_start_date: object | None,
) -> tuple[pd.Timestamp | None, ...]:
    if start_dates is None:
        return (None if fallback_start_date is None else _normalize_scenario_date(fallback_start_date),)
    dates = tuple(_normalize_scenario_date(value) for value in start_dates)
    if not dates:
        raise ValueError("start_dates must include at least one date when provided")
    return dates


def _scenario_cadences(cadences: Iterable[str]) -> tuple[str, ...]:
    values = tuple(_normalize_cadence(cadence) for cadence in cadences)
    if not values:
        raise ValueError("cadences must include at least one cadence")
    return values


def _normalize_scenario_date(value: object) -> pd.Timestamp:
    return pd.Timestamp(value).tz_localize(None).normalize()


def _scenario_date_label(value: object) -> str:
    return _normalize_scenario_date(value).date().isoformat().replace("-", "_")
