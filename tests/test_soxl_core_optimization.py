from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

import us_equity_strategies.research.soxl_core_optimization as optimization
from us_equity_strategies.research.soxl_soxx_offline_input_contract import InputRow, OfflineInput
from us_equity_strategies.research.soxl_soxx_typed_baseline_result import run_typed_baseline
from us_equity_strategies.research.soxl_core_optimization import (
    BASELINE_WINDOW_DAYS,
    CANDIDATE_WINDOWS,
    DailyPoint,
    PLUGIN_CONTROL,
    SCENARIOS,
    OptimizationError,
    _eligibility,
    _relative_volatility_multiplier,
    _select_volatility_scaling_winner,
    _select_winner,
    _volatility_window_metrics,
    load_persisted_result,
    persist_result,
    run_soxl_core_optimization,
    simulate_candidate,
    VOLATILITY_SCALING_CANDIDATES,
    VOLATILITY_SCALING_PLUGIN_CONTROL,
    load_persisted_volatility_scaling_result,
    persist_volatility_scaling_result,
    run_soxl_volatility_scaling,
    simulate_volatility_scaling_candidate,
)


def _canonical(rows: list[InputRow]) -> bytes:
    lines = ["symbol,as_of,open,high,low,close,volume"]
    for row in rows:
        lines.append(",".join((row.symbol, row.as_of, *(format(value, ".17g") for value in (row.open, row.high, row.low, row.close, row.volume)))))
    return ("\n".join(lines) + "\n").encode()


def _source(count: int = 753) -> OfflineInput:
    rows: list[InputRow] = []
    for index in range(count):
        day = (date(2023, 7, 14) + timedelta(days=index)).isoformat()
        soxx_close = 100.0 + (index % 31)
        soxl_open = 50.0 + (index % 7)
        soxl_close = soxl_open * (1.0 + ((index % 5) - 2) / 100.0)
        rows.extend((
            InputRow("SOXL", day, soxl_open, max(soxl_open, soxl_close), min(soxl_open, soxl_close), soxl_close, 1.0),
            InputRow("SOXX", day, soxx_close, soxx_close, soxx_close, soxx_close, 1.0),
        ))
    return OfflineInput(tuple(rows), _canonical(rows), "a" * 64, "fixture_v1")


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.run(("git", "-C", str(repo), *arguments), check=True, capture_output=True, text=True).stdout.strip()


def _provenance_repo(tmp_path: Path) -> tuple[Path, str, dict[str, str]]:
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    paths = (
        "src/us_equity_strategies/research/soxl_core_optimization.py",
        "src/us_equity_strategies/research/soxl_soxx_offline_input_contract.py",
        "src/us_equity_strategies/research/soxl_soxx_typed_baseline_result.py",
    )
    for relative in paths:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {relative}\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")
    head = _git(repo, "rev-parse", "HEAD")
    blobs = {relative: _git(repo, "rev-parse", f"HEAD:{relative}") for relative in paths}
    return repo, head, blobs


def _persist(result: dict, root: Path, repo: Path, commit: str, blobs: dict[str, str]):
    return persist_result(result, root, source_commit=commit, source_blobs=blobs, repo_root=repo)


def test_frozen_candidates_timing_parity_and_plugin_contract() -> None:
    source = _source()
    assert CANDIDATE_WINDOWS == (140, 160, 180, 200)
    assert BASELINE_WINDOW_DAYS == 200
    assert PLUGIN_CONTROL == {"state": "ABSENT", "enabled": False, "optimization_eligible": False}
    points = simulate_candidate(source, 200, SCENARIOS[0])
    baseline = run_typed_baseline(source)
    assert [(point.date, point.end_equity.hex(), point.cash.hex(), point.quantity.hex()) for point in points] == [
        (point.date, point.equity.hex(), point.cash.hex(), point.soxl_quantity.hex()) for point in baseline.equity_curve
    ]
    assert points[0].date == baseline.equity_curve[0].date


def test_volatility_scaling_candidates_are_lagged_bounded_and_unscaled_is_exact_parity() -> None:
    source = _source()
    assert VOLATILITY_SCALING_CANDIDATES == (
        "UNSCALED_SMA200",
        "REL_VOL_SQRT_20",
        "REL_VOL_LINEAR_20",
        "REL_VOL_SQUARED_20",
    )
    for scenario in SCENARIOS:
        unscaled = simulate_volatility_scaling_candidate(source, "UNSCALED_SMA200", scenario)
        baseline = simulate_candidate(source, 200, scenario)
        assert [(point.date, point.end_equity.hex(), point.cash.hex(), point.quantity.hex()) for point in unscaled] == [
            (point.date, point.end_equity.hex(), point.cash.hex(), point.quantity.hex()) for point in baseline
        ]
        for candidate_id in VOLATILITY_SCALING_CANDIDATES[1:]:
            points = simulate_volatility_scaling_candidate(source, candidate_id, scenario)
            assert len(points) == len(baseline)
            assert all(point.quantity >= 0.0 and point.cash >= 0.0 for point in points)


def test_relative_volatility_formula_zero_cases_and_validation_ties() -> None:
    flat = tuple(InputRow("SOXL", f"2024-01-{index + 1:02d}", 100.0, 100.0, 100.0, 100.0, 1.0) for index in range(42))
    assert _relative_volatility_multiplier(flat, 41, "REL_VOL_LINEAR_20") == 1.0
    volatile = tuple(
        InputRow("SOXL", f"2024-02-{index + 1:02d}", 100.0, 100.0, 100.0, 100.0 if index <= 20 else 100.0 + (index % 2), 1.0)
        for index in range(42)
    )
    assert _relative_volatility_multiplier(volatile, 41, "REL_VOL_LINEAR_20") == 0.0
    for candidate_id in VOLATILITY_SCALING_CANDIDATES[1:]:
        assert 0.0 <= _relative_volatility_multiplier(volatile, 41, candidate_id) <= 1.0

    metrics = [{"max_drawdown": -0.1, "expected_shortfall_95": -0.02, "annualized_volatility": 0.3, "cagr": 0.1, "turnover": 1.0}] * 3
    validation = {candidate_id: metrics for candidate_id in VOLATILITY_SCALING_CANDIDATES}
    assert _select_volatility_scaling_winner(validation) == "UNSCALED_SMA200"


def test_volatility_window_recovers_to_starting_peak_after_first_day_drawdown() -> None:
    points = (
        DailyPoint("2024-01-01", 100.0, 90.0, 90.0, 0.0, -0.1, False, 0.0, 0.0, 0.0),
        DailyPoint("2024-01-02", 90.0, 95.0, 95.0, 0.0, 95.0 / 90.0 - 1.0, False, 0.0, 0.0, 0.0),
        DailyPoint("2024-01-03", 95.0, 100.0, 100.0, 0.0, 100.0 / 95.0 - 1.0, False, 0.0, 0.0, 0.0),
    )
    metrics = _volatility_window_metrics(points, BASELINE_WINDOW_DAYS, BASELINE_WINDOW_DAYS + 2)
    assert metrics["max_drawdown_recovery_sessions"] == 3
    assert metrics["max_drawdown_unrecovered_sessions"] == 0


def test_volatility_scaling_result_is_research_only_and_persists_with_strict_readback(tmp_path: Path, monkeypatch) -> None:
    source = _source()
    monkeypatch.setattr("us_equity_strategies.research.soxl_core_optimization._terminal_loss_probability", lambda _: 0.0)
    result = run_soxl_volatility_scaling(source)
    assert result["schema"] == "qsl.research.soxl_volatility_scaling.v1"
    assert result["evidence_valid"] is True
    assert result["research_only"] is True
    assert result["live_adoption_authorized"] is False
    assert result["size_zero_required"] is True
    assert result["plugin_control"] == VOLATILITY_SCALING_PLUGIN_CONTROL
    assert set(result) >= {
        "baseline", "candidates", "lookback_rule", "validation_metrics_c2_5", "locked_winner",
        "post_lock_metrics", "evidence_gates", "soxx_drawdown_comparison",
    }
    assert run_soxl_volatility_scaling(source, plugin_control={"state": "ABSENT_DISABLED"})["evidence_valid"] is False

    repo, head, blobs = _provenance_repo(tmp_path)
    output = tmp_path / "out"
    persist_volatility_scaling_result(result, output, source_commit=head, source_blobs=blobs, repo_root=repo)
    assert load_persisted_volatility_scaling_result(output) == json.loads((output / "soxl_volatility_scaling_v1.json").read_bytes())


def test_validation_only_selection_and_strict_tie_breaks() -> None:
    metrics = {
        140: [{"sharpe": 1.0, "cumulative_return": 0.2, "max_drawdown": -0.1}] * 3,
        160: [{"sharpe": 1.0, "cumulative_return": 0.2, "max_drawdown": -0.1}] * 3,
        180: [{"sharpe": 1.0, "cumulative_return": 0.2, "max_drawdown": -0.1}] * 3,
        200: [{"sharpe": 1.0, "cumulative_return": 0.2, "max_drawdown": -0.1}] * 3,
    }
    assert _select_winner(metrics) == 200
    metrics[180] = [{"sharpe": 1.1, "cumulative_return": 0.0, "max_drawdown": -0.9}] * 3
    assert _select_winner(metrics) == 180
    metrics[180] = [{"sharpe": float("nan"), "cumulative_return": 1.0, "max_drawdown": 0.0}] * 3
    with pytest.raises(OptimizationError):
        _select_winner(metrics)


def test_deterministic_results_and_later_predicates_are_strict() -> None:
    source = _source()
    assert run_soxl_core_optimization(source) == run_soxl_core_optimization(source)
    assert _eligibility((0.1, 0.0, 0.2), 0.01, 0.01, 0.49) == ("PASS", ())
    status, failures = _eligibility((0.1, 0.0, -0.1), 0.0, 0.0, 0.5)
    assert status == "FAIL"
    assert len(failures) == 4
    invalid = run_soxl_core_optimization(None)
    assert invalid["outcome"] == "NO_IMPROVEMENT"
    assert invalid["research_recommendation"] is None
    assert invalid["size_zero_required"] is True
    assert run_soxl_core_optimization(source, plugin_control={"state": "PRESENT"})["evidence_valid"] is False
    assert run_soxl_core_optimization(replace(source, canonical_bytes=b"wrong"))["evidence_valid"] is False


def test_current_head_provenance_success_and_shallow_current_head(tmp_path: Path) -> None:
    repo, head, blobs = _provenance_repo(tmp_path)
    shallow = tmp_path / "shallow"
    subprocess.run(("git", "clone", "--quiet", "--depth=1", f"file://{repo}", str(shallow)), check=True)
    result = {"schema": "fixture", "value": 1.0}
    _persist(result, tmp_path / "out", shallow, head, blobs)
    assert load_persisted_result(tmp_path / "out") == json.loads((tmp_path / "out" / "soxl_core_optimization_v1.json").read_bytes())


@pytest.mark.parametrize("kind", ("source_commit", "blob", "missing_path", "dirty"))
def test_provenance_mismatches_fail_closed(tmp_path: Path, kind: str) -> None:
    repo, head, blobs = _provenance_repo(tmp_path)
    output = tmp_path / "out"
    if kind == "source_commit":
        with pytest.raises(OptimizationError, match="SOURCE_COMMIT_MISMATCH"):
            _persist({}, output, repo, "0" * 40, blobs)
    elif kind == "blob":
        changed = dict(blobs)
        changed[next(iter(changed))] = "0" * 40
        with pytest.raises(OptimizationError, match="SOURCE_BLOB_MISMATCH"):
            _persist({}, output, repo, head, changed)
    elif kind == "missing_path":
        changed = dict(blobs)
        changed["src/us_equity_strategies/research/missing.py"] = "0" * 40
        with pytest.raises(OptimizationError, match="SOURCE_BLOB_MAP_INVALID"):
            _persist({}, output, repo, head, changed)
    else:
        (repo / "src/us_equity_strategies/research/soxl_core_optimization.py").write_text("dirty\n")
        with pytest.raises(OptimizationError, match="SOURCE_CHECKOUT_DIRTY"):
            _persist({}, output, repo, head, blobs)
    assert not output.exists()


def test_atomic_publication_readback_idempotence_and_failure_cleanup(tmp_path: Path, monkeypatch) -> None:
    repo, head, blobs = _provenance_repo(tmp_path)
    output = tmp_path / "out"
    result = {"schema": "fixture", "value": 1.0}
    paths = _persist(result, output, repo, head, blobs)
    bundle = paths.bundle.read_bytes()
    assert paths.sidecar.read_text() == hashlib.sha256(bundle).hexdigest() + "\n"
    assert load_persisted_result(output) == json.loads(bundle)
    assert _persist(result, output, repo, head, blobs).bundle.read_bytes() == bundle
    paths.bundle.write_bytes(b"different")
    with pytest.raises(OptimizationError, match="EXISTING_DIFFERENT_BYTES"):
        _persist(result, output, repo, head, blobs)

    failure = tmp_path / "failure"
    import us_equity_strategies.research.soxl_core_optimization as subject
    monkeypatch.setattr(subject.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("blocked")))
    with pytest.raises(OptimizationError, match="PERSIST_WRITE_FAILED"):
        _persist(result, failure, repo, head, blobs)
    assert not list(failure.glob(".*.tmp"))


def test_precreated_symlink_never_overwrites_external_target(tmp_path: Path) -> None:
    repo, head, blobs = _provenance_repo(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    external = tmp_path / "external"
    external.write_text("unchanged")
    (output / "soxl_core_optimization_v1.json").symlink_to(external)
    with pytest.raises(OptimizationError, match="OUTPUT_PATH_INVALID"):
        _persist({"schema": "fixture"}, output, repo, head, blobs)
    assert external.read_text() == "unchanged"
    assert not (output / "soxl_core_optimization_v1.sha256").exists()


def test_symlinked_output_root_ancestor_cannot_redirect_publication(tmp_path: Path) -> None:
    repo, head, blobs = _provenance_repo(tmp_path)
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (trusted / "link").symlink_to(external, target_is_directory=True)

    with pytest.raises(OptimizationError, match="OUTPUT_ROOT_INVALID"):
        _persist({"schema": "fixture"}, trusted / "link" / "run", repo, head, blobs)

    assert not (external / "run").exists()


def _assert_trial_manifest(result, source=None):
    manifest = result["trial_manifest"]
    assert set(manifest) == {"schema", "scope", "input_validation_stage", "input_linkage", "attempts"}
    assert manifest["schema"] == "qsl.research.soxl_trial_manifest.v1"
    assert manifest["scope"] == "THIS_INVOCATION_SIMULATIONS_ONLY"
    if source is None:
        assert manifest["input_validation_stage"] == "ROWS_CANONICAL_BYTES_NOT_CHECKED"
        assert manifest["input_linkage"] is None
    else:
        assert manifest["input_validation_stage"] == "ROWS_CANONICAL_BYTES_MATCH"
        assert manifest["input_linkage"] == {
            "accepted_input_digest": source.input_digest,
            "input_digest_semantics": "FORMAT_CHECK_ONLY_NOT_RECOMPUTED",
            "matched_rows_canonical_bytes_sha256": hashlib.sha256(source.canonical_bytes).hexdigest(),
            "matched_rows_canonical_bytes": len(source.canonical_bytes),
        }
    expected = [(window, scenario) for window in CANDIDATE_WINDOWS for scenario in SCENARIOS]
    assert len(manifest["attempts"]) == len(expected) == 16
    for attempt, (window, scenario) in zip(manifest["attempts"], expected, strict=True):
        assert set(attempt) == {"window_days", "scenario_id", "commission_bps", "slippage_bps", "status", "reason_code"}
        assert (attempt["window_days"], attempt["scenario_id"]) == (window, scenario.scenario_id)
        assert (attempt["commission_bps"], attempt["slippage_bps"]) == (scenario.commission_bps, scenario.slippage_bps)
        assert attempt["status"] in {"not_started", "succeeded", "failed"}
        assert attempt["reason_code"] == (
            "CANDIDATE_SIMULATION_FAILED" if attempt["status"] == "failed" else None
        )
    return manifest


def _result_with_simulation_failure(monkeypatch, failure_index, exception):
    source = _source()
    original = optimization.simulate_candidate
    calls = []

    def fail_one_call(input_source, window, scenario):
        index = len(calls)
        calls.append((window, scenario.scenario_id))
        if index == failure_index:
            raise exception
        return original(input_source, window, scenario)

    with monkeypatch.context() as patch:
        patch.setattr(optimization, "simulate_candidate", fail_one_call)
        result = run_soxl_core_optimization(source)
    return source, result, calls


@pytest.mark.parametrize("failure_index", range(16))
def test_every_sma_simulation_failure_preserves_this_invocation_attempts(monkeypatch, failure_index):
    source, result, calls = _result_with_simulation_failure(
        monkeypatch, failure_index, OptimizationError("SIMULATION_EQUITY_INVALID"),
    )
    manifest = _assert_trial_manifest(result, source)
    assert [item["status"] for item in manifest["attempts"]] == (
        ["succeeded"] * failure_index + ["failed"] + ["not_started"] * (15 - failure_index)
    )
    assert calls == [(window, scenario.scenario_id) for window in CANDIDATE_WINDOWS for scenario in SCENARIOS][:failure_index + 1]
    assert result["failure_codes"] == ["SIMULATION_EQUITY_INVALID"]
    assert result["evidence_valid"] is False
    assert result["outcome"] == "NO_IMPROVEMENT"
    assert result["research_recommendation"] is None
    assert result["live_adoption_authorized"] is False
    assert result["size_zero_required"] is True
    assert "locked_winner" not in result


@pytest.mark.parametrize("exception", [
    RuntimeError("SYNTHETIC_PRIVATE_PROVIDER_DETAIL"),
    OptimizationError("SYNTHETIC_PRIVATE_PROVIDER_DETAIL"),
    OptimizationError("SIMULATION_EQUITY_INVALID", "SYNTHETIC_PRIVATE_PROVIDER_DETAIL"),
])
def test_unexpected_sma_exception_text_is_sanitized_in_return_and_persistence(tmp_path, monkeypatch, exception):
    source, result, calls = _result_with_simulation_failure(monkeypatch, 4, exception)
    manifest = _assert_trial_manifest(result, source)
    assert [item["status"] for item in manifest["attempts"]] == ["succeeded"] * 4 + ["failed"] + ["not_started"] * 11
    assert len(calls) == 5
    assert result["failure_codes"] == ["OPTIMIZATION_FAILED"]
    assert "SYNTHETIC_PRIVATE" not in json.dumps(result)
    repo, head, blobs = _provenance_repo(tmp_path)
    paths = _persist(result, tmp_path / "out", repo, head, blobs)
    assert load_persisted_result(tmp_path / "out")["trial_manifest"] == manifest
    assert all(b"SYNTHETIC_PRIVATE" not in path.read_bytes() for path in (paths.bundle, paths.sidecar, paths.readback))


@pytest.mark.parametrize("rejection", ["plugin", "source_type", "digest_format", "canonical_bytes", "row_count", "row_value"])
def test_early_sma_rejection_has_no_input_linkage_or_started_simulations(monkeypatch, rejection):
    source = _source()
    kwargs = {}
    if rejection == "plugin":
        kwargs["plugin_control"] = {"state": "PRESENT"}
    elif rejection == "source_type":
        source = None
    elif rejection == "digest_format":
        source = replace(source, input_digest="SYNTHETIC_PRIVATE_DIGEST")
    elif rejection == "canonical_bytes":
        source = replace(source, canonical_bytes=b"SYNTHETIC_PRIVATE_BYTES")
    elif rejection == "row_count":
        source = _source(752)
    else:
        source = replace(source, rows=(replace(source.rows[0], close=-1.0), *source.rows[1:]))
    monkeypatch.setattr(optimization, "simulate_candidate", lambda *_: pytest.fail("simulation started"))
    result = run_soxl_core_optimization(source, **kwargs)
    manifest = _assert_trial_manifest(result)
    assert [item["status"] for item in manifest["attempts"]] == ["not_started"] * 16
    assert result["evidence_valid"] is False
    assert result["research_recommendation"] is None
    assert "SYNTHETIC_PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("boundary", ["plugin_comparison", "typed_rows"])
def test_sma_ordinary_exception_before_simulations_is_sanitized(monkeypatch, boundary):
    class FailingPluginComparison:
        def __eq__(self, _):
            raise RuntimeError("SYNTHETIC_PRIVATE_PLUGIN_DETAIL")

    kwargs = {}
    if boundary == "plugin_comparison":
        kwargs["plugin_control"] = FailingPluginComparison()
    else:
        def fail(*_):
            raise RuntimeError("SYNTHETIC_PRIVATE_INPUT_DETAIL")

        monkeypatch.setattr(optimization, "_typed_rows", fail)
    monkeypatch.setattr(optimization, "simulate_candidate", lambda *_: pytest.fail("simulation started"))
    result = run_soxl_core_optimization(_source(), **kwargs)
    manifest = _assert_trial_manifest(result)
    assert [item["status"] for item in manifest["attempts"]] == ["not_started"] * 16
    assert result["failure_codes"] == ["OPTIMIZATION_FAILED"]
    assert "SYNTHETIC_PRIVATE" not in json.dumps(result)


def test_sma_parity_failure_retains_all_completed_simulations(monkeypatch):
    original = optimization.simulate_candidate
    calls = []

    def mismatch_one_call(source, window, scenario):
        calls.append((window, scenario.scenario_id))
        points = original(source, window, scenario)
        if window == BASELINE_WINDOW_DAYS and scenario.scenario_id == "ZERO":
            return (replace(points[0], date="2099-01-01"), *points[1:])
        return points

    monkeypatch.setattr(optimization, "simulate_candidate", mismatch_one_call)
    source = _source()
    result = run_soxl_core_optimization(source)
    manifest = _assert_trial_manifest(result, source)
    assert len(calls) == 16
    assert [item["status"] for item in manifest["attempts"]] == ["succeeded"] * 16
    assert result["failure_codes"] == ["SMA200_ZERO_PARITY_FAILED"]
    assert result["evidence_valid"] is False
    assert result["research_recommendation"] is None


@pytest.mark.parametrize(("helper", "exception", "failure_code"), [
    ("run_typed_baseline", RuntimeError("SYNTHETIC_PRIVATE_BASELINE_DETAIL"), "OPTIMIZATION_FAILED"),
    ("_window_metrics", OptimizationError("WINDOW_BOUNDARY_INVALID"), "WINDOW_BOUNDARY_INVALID"),
    ("_select_winner", OptimizationError("VALIDATION_METRICS_INVALID"), "VALIDATION_METRICS_INVALID"),
    ("_select_winner", RuntimeError("SYNTHETIC_PRIVATE_SELECTION_DETAIL"), "OPTIMIZATION_FAILED"),
    ("_terminal_loss_probability", OptimizationError("MONTE_CARLO_INPUT_INVALID"), "MONTE_CARLO_INPUT_INVALID"),
    ("_eligibility", RuntimeError("SYNTHETIC_PRIVATE_ELIGIBILITY_DETAIL"), "OPTIMIZATION_FAILED"),
])
def test_sma_postprocessing_failure_retains_all_successful_simulations(monkeypatch, helper, exception, failure_code):
    source = _source()

    def fail(*_):
        raise exception

    monkeypatch.setattr(optimization, helper, fail)
    result = run_soxl_core_optimization(source)
    manifest = _assert_trial_manifest(result, source)
    assert [item["status"] for item in manifest["attempts"]] == ["succeeded"] * 16
    assert result["evidence_valid"] is False
    assert result["failure_codes"] == [failure_code]
    assert result["research_recommendation"] is None
    assert result["live_adoption_authorized"] is False
    assert "SYNTHETIC_PRIVATE" not in json.dumps(result)


def test_sma_manifest_is_deterministic_and_independent_between_invocations(monkeypatch):
    source, first, _ = _result_with_simulation_failure(monkeypatch, 3, RuntimeError("synthetic failure"))
    _, second, _ = _result_with_simulation_failure(monkeypatch, 3, RuntimeError("synthetic failure"))
    assert first == second
    assert first["trial_manifest"] is not second["trial_manifest"]
    assert first["trial_manifest"]["attempts"] is not second["trial_manifest"]["attempts"]
    first["trial_manifest"]["attempts"][0]["reason_code"] = "synthetic mutation"
    first["trial_manifest"]["input_linkage"]["accepted_input_digest"] = "synthetic mutation"
    _assert_trial_manifest(second, source)


def test_success_sma_manifest_preserves_legacy_result_and_labels_only_checked_linkage():
    source = replace(_source(), source_revision="SYNTHETIC_UNVERIFIED_SOURCE_REVISION")
    result = run_soxl_core_optimization(source)
    manifest = _assert_trial_manifest(result, source)
    assert [item["status"] for item in manifest["attempts"]] == ["succeeded"] * 16
    assert source.source_revision not in json.dumps(result)
    legacy_projection = {key: value for key, value in result.items() if key != "trial_manifest"}
    # Frozen from the reviewed pre-patch tree and this same synthetic fixture.
    raw = optimization._canonical_bytes(optimization._wire(legacy_projection))
    assert hashlib.sha256(raw).hexdigest() == "1e09a2a83f93fd806855aedd298a359a00736f6935f2b7be68a3902ef8582e23"


def test_sma_input_digest_is_format_checked_not_artifact_or_composite_verified(monkeypatch):
    monkeypatch.setattr(optimization, "_terminal_loss_probability", lambda _: 0.0)
    first = _source()
    second = replace(first, input_digest="b" * 64)
    assert first.input_digest != hashlib.sha256(first.canonical_bytes).hexdigest()
    _assert_trial_manifest(run_soxl_core_optimization(first), first)
    _assert_trial_manifest(run_soxl_core_optimization(second), second)


def test_legacy_sma_bundle_is_readable_and_new_metadata_cannot_overwrite_it(tmp_path, monkeypatch):
    monkeypatch.setattr(optimization, "_terminal_loss_probability", lambda _: 0.0)
    source = _source()
    current = run_soxl_core_optimization(source)
    _assert_trial_manifest(current, source)
    legacy = {key: value for key, value in current.items() if key != "trial_manifest"}
    repo, head, blobs = _provenance_repo(tmp_path)
    output = tmp_path / "out"
    paths = _persist(legacy, output, repo, head, blobs)
    original = {path: path.read_bytes() for path in (paths.bundle, paths.sidecar, paths.readback)}
    restored = load_persisted_result(output)
    assert "trial_manifest" not in restored
    assert restored == optimization._wire(legacy)
    with pytest.raises(OptimizationError, match="EXISTING_DIFFERENT_BYTES"):
        _persist(current, output, repo, head, blobs)
    assert all(path.read_bytes() == raw for path, raw in original.items())


@pytest.mark.parametrize("repair_sidecar", [False, True])
def test_sma_manifest_tampering_is_bound_by_existing_persistence_digests(tmp_path, monkeypatch, repair_sidecar):
    _, result, _ = _result_with_simulation_failure(monkeypatch, 2, RuntimeError("synthetic failure"))
    repo, head, blobs = _provenance_repo(tmp_path)
    output = tmp_path / "out"
    paths = _persist(result, output, repo, head, blobs)
    parsed = json.loads(paths.bundle.read_bytes())
    parsed["trial_manifest"]["attempts"][2]["status"] = "succeeded"
    tampered = optimization._canonical_bytes(parsed)
    paths.bundle.write_bytes(tampered)
    if repair_sidecar:
        paths.sidecar.write_text(hashlib.sha256(tampered).hexdigest() + "\n")
    with pytest.raises(OptimizationError, match="READBACK_MISMATCH" if repair_sidecar else "SIDECAR_MISMATCH"):
        load_persisted_result(output)


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit, GeneratorExit])
@pytest.mark.parametrize("helper", ["_typed_rows", "simulate_candidate", "run_typed_baseline"])
def test_sma_process_control_exceptions_are_not_converted_to_completed_accounting(monkeypatch, exception_type, helper):
    def interrupt(*_):
        raise exception_type("synthetic interruption")

    monkeypatch.setattr(optimization, helper, interrupt)
    with pytest.raises(exception_type):
        run_soxl_core_optimization(_source())


@pytest.mark.parametrize(("runner", "expected_digest"), [
    (optimization.run_soxl_volatility_scaling, "8f172dfc65114b40f2f8a45dbf8ffc4ea5bdc066c898f986d80effa9ae86037c"),
    (optimization.run_soxl_rsi2_mean_reversion, "720aa1de98a93381db39753966bbbcdca509660bf18ccc43808329cd23c87960"),
])
def test_other_soxl_variants_keep_exact_existing_result_projection(runner, expected_digest):
    result = runner(_source())
    assert "trial_manifest" not in result
    assert hashlib.sha256(optimization._canonical_bytes(optimization._wire(result))).hexdigest() == expected_digest


@pytest.mark.parametrize("runner", [optimization.run_soxl_volatility_scaling, optimization.run_soxl_rsi2_mean_reversion])
def test_other_soxl_variants_keep_existing_ordinary_exception_behavior(monkeypatch, runner):
    def fail(*_):
        raise RuntimeError("synthetic interruption")

    monkeypatch.setattr(optimization, "_typed_rows", fail)
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        runner(_source())
