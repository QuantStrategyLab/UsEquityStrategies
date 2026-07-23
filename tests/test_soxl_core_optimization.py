from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from us_equity_strategies.research.soxl_soxx_offline_input_contract import InputRow, OfflineInput
from us_equity_strategies.research.soxl_soxx_typed_baseline_result import run_typed_baseline
from us_equity_strategies.research.soxl_core_optimization import (
    BASELINE_WINDOW_DAYS,
    CANDIDATE_WINDOWS,
    PLUGIN_CONTROL,
    SCENARIOS,
    OptimizationError,
    _eligibility,
    _relative_volatility_multiplier,
    _select_volatility_scaling_winner,
    _select_winner,
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
