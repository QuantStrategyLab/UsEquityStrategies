from __future__ import annotations

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
    RSI2_MEAN_REVERSION_CANDIDATES,
    RSI2_MEAN_REVERSION_PLUGIN_CONTROL,
    RSI2_MEAN_REVERSION_SCHEMA,
    SCENARIOS,
    OptimizationError,
    _rsi2_values,
    _select_rsi2_mean_reversion_winner,
    load_persisted_rsi2_mean_reversion_result,
    persist_rsi2_mean_reversion_result,
    run_soxl_rsi2_mean_reversion,
    simulate_candidate,
    simulate_rsi2_mean_reversion_candidate,
)


def _canonical(rows: list[InputRow]) -> bytes:
    lines = ["symbol,as_of,open,high,low,close,volume"]
    for row in rows:
        lines.append(",".join((row.symbol, row.as_of, *(format(value, ".17g") for value in (row.open, row.high, row.low, row.close, row.volume)))))
    return ("\n".join(lines) + "\n").encode()


def _source(closes: list[float] | None = None) -> OfflineInput:
    closes = closes or [100.0 + index / 10.0 for index in range(753)]
    rows: list[InputRow] = []
    for index, soxx_close in enumerate(closes):
        day = (date(2023, 7, 14) + timedelta(days=index)).isoformat()
        soxl_open = 50.0 + index % 7
        soxl_close = soxl_open * (1.0 + ((index % 5) - 2) / 100.0)
        rows.extend((
            InputRow("SOXL", day, soxl_open, max(soxl_open, soxl_close), min(soxl_open, soxl_close), soxl_close, 1.0),
            InputRow("SOXX", day, soxx_close, soxx_close, soxx_close, soxx_close, 1.0),
        ))
    return OfflineInput(tuple(rows), _canonical(rows), "a" * 64, "fixture_v1")


def _provenance_repo(tmp_path: Path) -> tuple[Path, str, dict[str, str]]:
    repo = tmp_path / "source"
    repo.mkdir()
    def git(*args: str) -> str:
        return subprocess.run(("git", "-C", str(repo), *args), check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    paths = (
        "src/us_equity_strategies/research/soxl_core_optimization.py",
        "src/us_equity_strategies/research/soxl_soxx_offline_input_contract.py",
        "src/us_equity_strategies/research/soxl_soxx_typed_baseline_result.py",
    )
    for relative in paths:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {relative}\n")
    git("add", ".")
    git("commit", "-qm", "fixture")
    head = git("rev-parse", "HEAD")
    return repo, head, {relative: git("rev-parse", f"HEAD:{relative}") for relative in paths}


def test_frozen_candidates_plugin_and_exact_rsi2_edges() -> None:
    assert RSI2_MEAN_REVERSION_CANDIDATES == (
        "UNSCALED_SMA200", "RSI2_ENTRY_5_EXIT_70", "RSI2_ENTRY_10_EXIT_70", "RSI2_ENTRY_15_EXIT_70",
    )
    assert RSI2_MEAN_REVERSION_PLUGIN_CONTROL == {"state": "ABSENT_DISABLED", "enabled": False, "optimization_eligible": False}
    assert _rsi2_values((100.0, 100.0, 100.0))[2] == 50.0
    assert _rsi2_values((100.0, 101.0, 102.0))[2] == 100.0
    assert _rsi2_values((102.0, 101.0, 100.0))[2] == 0.0
    values = _rsi2_values((100.0, 102.0, 101.0, 103.0))
    assert values[3] == pytest.approx(85.71428571428571)


def test_unscaled_parity_strict_trend_and_next_open_no_lookahead() -> None:
    source = _source()
    for scenario in SCENARIOS:
        actual = simulate_rsi2_mean_reversion_candidate(source, "UNSCALED_SMA200", scenario)
        expected = simulate_candidate(source, BASELINE_WINDOW_DAYS, scenario)
        assert [(p.date, p.end_equity.hex(), p.cash.hex(), p.quantity.hex()) for p in actual] == [(p.date, p.end_equity.hex(), p.cash.hex(), p.quantity.hex()) for p in expected]
    equality = _source([100.0] * 753)
    assert all(point.quantity == 0.0 for point in simulate_rsi2_mean_reversion_candidate(equality, "UNSCALED_SMA200", SCENARIOS[0]))
    original = simulate_rsi2_mean_reversion_candidate(source, "RSI2_ENTRY_15_EXIT_70", SCENARIOS[2])
    rows = list(source.rows)
    execution = 250 * 2
    row = rows[execution]
    rows[execution] = InputRow(row.symbol, row.as_of, row.open, max(row.open, row.close * 3.0), min(row.open, row.close * 3.0), row.close * 3.0, row.volume)
    changed = OfflineInput(tuple(rows), _canonical(rows), source.input_digest, source.source_revision)
    assert original[50].quantity.hex() == simulate_rsi2_mean_reversion_candidate(changed, "RSI2_ENTRY_15_EXIT_70", SCENARIOS[2])[50].quantity.hex()


def test_rsi_state_machine_costs_and_exposure_bounds() -> None:
    closes = [100.0 + index / 10.0 for index in range(200)] + [118.0, 117.0, 116.0, 117.0, 119.0] + [120.0 + index / 10.0 for index in range(548)]
    source = _source(closes)
    for candidate in RSI2_MEAN_REVERSION_CANDIDATES[1:]:
        for scenario in SCENARIOS:
            points = simulate_rsi2_mean_reversion_candidate(source, candidate, scenario)
            assert all(point.cash >= 0.0 and point.quantity >= 0.0 for point in points)
            assert all(point.quantity == 0.0 or point.cash == 0.0 for point in points)
            assert any(point.transition for point in points)


def test_validation_selection_tie_and_runner_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    metric = {"max_drawdown": -0.1, "expected_shortfall_95": -0.02, "annualized_volatility": 0.2, "cagr": 0.1, "turnover": 1.0}
    validation = {candidate: [metric] * 3 for candidate in RSI2_MEAN_REVERSION_CANDIDATES}
    assert _select_rsi2_mean_reversion_winner(validation) == "UNSCALED_SMA200"
    monkeypatch.setattr("us_equity_strategies.research.soxl_core_optimization._terminal_loss_probability", lambda _: 0.0)
    result = run_soxl_rsi2_mean_reversion(_source())
    assert result["schema"] == RSI2_MEAN_REVERSION_SCHEMA
    assert result["research_only"] is True and result["live_adoption_authorized"] is False and result["size_zero_required"] is True
    assert set(result["post_lock_metrics"]) <= {result["locked_winner"], "UNSCALED_SMA200"}
    assert run_soxl_rsi2_mean_reversion(_source(), plugin_control={"state": "ABSENT_DISABLED"})["evidence_valid"] is False
    assert run_soxl_rsi2_mean_reversion(None)["evidence_valid"] is False


def test_persistence_set_once_strict_readback_and_symlink_rejection(tmp_path: Path) -> None:
    repo, head, blobs = _provenance_repo(tmp_path)
    result = {"schema": RSI2_MEAN_REVERSION_SCHEMA, "value": 1.0}
    output = tmp_path / "out"
    paths = persist_rsi2_mean_reversion_result(result, output, source_commit=head, source_blobs=blobs, repo_root=repo)
    bundle = paths.bundle.read_bytes()
    assert paths.bundle.name == "soxl_rsi2_mean_reversion_v1.json"
    assert paths.sidecar.read_text() == hashlib.sha256(bundle).hexdigest() + "\n"
    assert load_persisted_rsi2_mean_reversion_result(output) == json.loads(bundle)
    assert persist_rsi2_mean_reversion_result(result, output, source_commit=head, source_blobs=blobs, repo_root=repo).bundle.read_bytes() == bundle
    paths.bundle.write_bytes(b"different")
    with pytest.raises(OptimizationError, match="EXISTING_DIFFERENT_BYTES"):
        persist_rsi2_mean_reversion_result(result, output, source_commit=head, source_blobs=blobs, repo_root=repo)
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "soxl_rsi2_mean_reversion_v1.json").symlink_to(tmp_path / "outside")
    with pytest.raises(OptimizationError, match="OUTPUT_PATH_INVALID"):
        persist_rsi2_mean_reversion_result(result, linked, source_commit=head, source_blobs=blobs, repo_root=repo)
