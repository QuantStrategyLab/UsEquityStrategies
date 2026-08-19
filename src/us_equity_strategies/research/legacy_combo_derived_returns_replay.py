"""Strictly exploratory replay of one pinned legacy three-leg return artifact.

This module deliberately consumes *derived returns*, not immutable market data.
It can compare the fixed trial ledger below, but it cannot produce P1/P2/P3
evidence, a promotion recommendation, or an execution instruction.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, NoReturn

SCHEMA = "qsl.research.legacy_combo_derived_returns_replay.v1"
CLASSIFICATION = "EXPLORATORY_LEGACY_DERIVED_RETURNS_NOT_P3_OR_PROMOTION_EVIDENCE"
EXPECTED_LEGACY_ARTIFACT_SHA256 = "9382b6d371de7c96c0fb508434007228f52d36b94e52c5eb5c044bae87eb5c4b"
EXPECTED_LEG_KEYS = ("global_etf", "russell", "dca")
EXPECTED_ROW_COUNT = 2887
SELECTION_WINDOW = ("2015-01-01", "2021-12-31")
CHECK_WINDOW = ("2022-01-01", "2026-12-31")
TRIAL_LEDGER = (
    ("baseline_50_30_20", (0.50, 0.30, 0.20)),
    ("global_heavy_60_20_20", (0.60, 0.20, 0.20)),
    ("russell_heavy_40_40_20", (0.40, 0.40, 0.20)),
    ("dca_heavy_40_30_30", (0.40, 0.30, 0.30)),
    ("balanced_50_20_30", (0.50, 0.20, 0.30)),
    ("equal_weight", (1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0)),
)


class LegacyReplayError(ValueError):
    """Sanitized boundary error for this non-promotable research tool."""


def _fail(code: str) -> NoReturn:
    raise LegacyReplayError(code) from None


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode() + b"\n"
    except (TypeError, ValueError):
        _fail("CANONICAL_JSON_INVALID")


def _artifact_sha256(artifact_bytes: bytes) -> str:
    if type(artifact_bytes) is not bytes:
        _fail("ARTIFACT_BYTES_INVALID")
    return hashlib.sha256(artifact_bytes).hexdigest()


def _load_pinned_legacy_artifact(artifact_bytes: bytes) -> dict[str, Any]:
    if _artifact_sha256(artifact_bytes) != EXPECTED_LEGACY_ARTIFACT_SHA256:
        _fail("LEGACY_ARTIFACT_SHA256_MISMATCH")
    try:
        parsed = json.loads(artifact_bytes)
    except (TypeError, json.JSONDecodeError):
        _fail("LEGACY_ARTIFACT_JSON_INVALID")
    if type(parsed) is not dict or set(parsed) != {"config", "generated_at", "results"}:
        _fail("LEGACY_ARTIFACT_SCHEMA_INVALID")
    if parsed["config"] != {
        "modes": ["static", "dynamic"],
        "weight_sets": [[0.5, 0.3, 0.2]],
        "start_date": "2015-01-01",
        "end_date": None,
    } or type(parsed["generated_at"]) is not str:
        _fail("LEGACY_ARTIFACT_CONFIG_INVALID")
    if type(parsed["results"]) is not list or len(parsed["results"]) != 2:
        _fail("LEGACY_ARTIFACT_RESULTS_INVALID")
    static = next((item for item in parsed["results"] if type(item) is dict and item.get("mode") == "static"), None)
    dynamic = next((item for item in parsed["results"] if type(item) is dict and item.get("mode") == "dynamic"), None)
    if static is None or dynamic is None or static.get("weights") != [0.5, 0.3, 0.2] or dynamic.get("weights") != [0.5, 0.3, 0.2]:
        _fail("LEGACY_ARTIFACT_MODE_INVALID")
    return static


@dataclass(frozen=True, slots=True)
class LegacyReturnRows:
    dates: tuple[str, ...]
    global_etf: tuple[float, ...]
    russell: tuple[float, ...]
    dca: tuple[float, ...]


def _as_return(value: object) -> float:
    if type(value) not in (int, float) or type(value) is bool:
        _fail("LEGACY_RETURN_TYPE_INVALID")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= -1.0:
        _fail("LEGACY_RETURN_VALUE_INVALID")
    return numeric


def _extract_rows(static: dict[str, Any]) -> LegacyReturnRows:
    if set(static) != {"mode", "weights", "period_metrics", "daily_returns", "leg_returns"}:
        _fail("LEGACY_STATIC_RESULT_SCHEMA_INVALID")
    daily = static["daily_returns"]
    legs = static["leg_returns"]
    if type(daily) is not list or len(daily) != EXPECTED_ROW_COUNT or type(legs) is not dict or set(legs) != set(EXPECTED_LEG_KEYS):
        _fail("LEGACY_RETURN_SERIES_SCHEMA_INVALID")
    if any(type(legs[key]) is not list or len(legs[key]) != EXPECTED_ROW_COUNT for key in EXPECTED_LEG_KEYS):
        _fail("LEGACY_RETURN_SERIES_LENGTH_INVALID")
    dates: list[str] = []
    benchmark: list[float] = []
    for row in daily:
        if type(row) is not dict or set(row) != {"date", "return"} or type(row["date"]) is not str:
            _fail("LEGACY_DAILY_ROW_SCHEMA_INVALID")
        try:
            date.fromisoformat(row["date"])
        except ValueError:
            _fail("LEGACY_DAILY_DATE_INVALID")
        dates.append(row["date"])
        benchmark.append(_as_return(row["return"]))
    if tuple(dates) != tuple(sorted(dates)) or len(set(dates)) != EXPECTED_ROW_COUNT:
        _fail("LEGACY_DAILY_DATE_ORDER_INVALID")
    values = {key: tuple(_as_return(item) for item in legs[key]) for key in EXPECTED_LEG_KEYS}
    if benchmark[0] != 0.0:
        _fail("LEGACY_INITIAL_RETURN_INVALID")
    for index, actual in enumerate(benchmark[1:], start=1):
        expected = 0.5 * values["global_etf"][index] + 0.3 * values["russell"][index] + 0.2 * values["dca"][index]
        if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=0.00000002):
            _fail("LEGACY_BASELINE_RECOMBINATION_MISMATCH")
    return LegacyReturnRows(tuple(dates), values["global_etf"], values["russell"], values["dca"])


def _window(points: tuple[tuple[str, float], ...], bounds: tuple[str, str]) -> tuple[tuple[str, float], ...]:
    selected = tuple(point for point in points if bounds[0] <= point[0] <= bounds[1])
    if len(selected) < 2:
        _fail("WINDOW_OBSERVATIONS_INSUFFICIENT")
    return selected


def _metrics(points: tuple[tuple[str, float], ...]) -> dict[str, float | int | str | None]:
    returns = tuple(item[1] for item in points)
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for item in returns:
        equity *= 1.0 + item
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1.0)
    start = date.fromisoformat(points[0][0])
    end = date.fromisoformat(points[-1][0])
    years = (end - start).days / 365.2425
    if years <= 0.0 or equity <= 0.0:
        _fail("METRICS_DATE_OR_EQUITY_INVALID")
    mean = math.fsum(returns) / len(returns)
    variance = math.fsum((item - mean) ** 2 for item in returns) / (len(returns) - 1)
    annualized_volatility = math.sqrt(variance) * math.sqrt(252.0)
    return {
        "start_date": points[0][0],
        "end_date": points[-1][0],
        "session_count": len(points),
        "cumulative_return": equity - 1.0,
        "cagr": equity ** (1.0 / years) - 1.0,
        "max_drawdown": max_drawdown,
        "annualized_volatility": annualized_volatility,
        "zero_rate_sharpe": None if annualized_volatility == 0.0 else mean / math.sqrt(variance) * math.sqrt(252.0),
    }


def run_pinned_legacy_combo_replay(artifact_bytes: bytes) -> dict[str, Any]:
    """Replay the fixed ledger from the one hash-pinned legacy artifact.

    The result is deliberately non-promotable: no candidate is selected and no
    P4/P5/P6 capability is granted, regardless of its reported metrics.
    """
    static = _load_pinned_legacy_artifact(artifact_bytes)
    rows = _extract_rows(static)
    trial_results = []
    for trial_id, weights in TRIAL_LEDGER:
        combined = tuple(
            (day, 0.0 if index == 0 else weights[0] * rows.global_etf[index] + weights[1] * rows.russell[index] + weights[2] * rows.dca[index])
            for index, day in enumerate(rows.dates)
        )
        trial_results.append({
            "trial_id": trial_id,
            "weights": {key: weight for key, weight in zip(EXPECTED_LEG_KEYS, weights, strict=True)},
            "selection_window_metrics": _metrics(_window(combined, SELECTION_WINDOW)),
            "check_window_metrics": _metrics(_window(combined, CHECK_WINDOW)),
        })
    return {
        "schema": SCHEMA,
        "classification": CLASSIFICATION,
        "research_only": True,
        "candidate_selection": "PROHIBITED",
        "promotion_recommendation": None,
        "p4_paper_authorized": False,
        "p5_shadow_authorized": False,
        "p6_live_authorized": False,
        "source": {
            "artifact": "docs/research/us_equity_combo_backtest_20260628.json",
            "artifact_sha256": _artifact_sha256(artifact_bytes),
            "derived_return_legs": list(EXPECTED_LEG_KEYS),
            "date_range": {"start": rows.dates[0], "end": rows.dates[-1], "sessions": len(rows.dates)},
            "immutable_raw_market_input_available": False,
            "point_in_time_membership_available": False,
            "execution_cost_model_available": False,
        },
        "trial_ledger": [
            {"trial_id": trial_id, "weights": {key: weight for key, weight in zip(EXPECTED_LEG_KEYS, weights, strict=True)}}
            for trial_id, weights in TRIAL_LEDGER
        ],
        "windows": {"selection": {"start": SELECTION_WINDOW[0], "end": SELECTION_WINDOW[1]}, "check": {"start": CHECK_WINDOW[0], "end": CHECK_WINDOW[1]}},
        "trial_results": trial_results,
        "required_before_candidate_registration": [
            "RECREATE_FROM_IMMUTABLE_P1_INPUT",
            "DEFINE_POINT_IN_TIME_UNIVERSE_AND_MEMBERSHIP",
            "DECLARE_AND_TEST_EXECUTION_COST_MODEL",
            "CREATE_SEPARATE_P2_CONTRACT_AND_P3_EVIDENCE",
        ],
    }


def persist_report(report: dict[str, Any], output_path: str | Path) -> Path:
    """Create one canonical report; an existing different report is refused."""
    output = Path(output_path)
    payload = _canonical_bytes(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if output.read_bytes() != payload:
            _fail("REPORT_PATH_ALREADY_CONTAINS_DIFFERENT_CONTENT")
        return output
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
    return output
