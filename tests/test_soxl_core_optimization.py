from dataclasses import replace
from datetime import date, timedelta
import hashlib
import json
import subprocess

import pytest

import us_equity_strategies.research.soxl_core_optimization as optimization
from us_equity_strategies.research.soxl_soxx_offline_input_contract import InputRow, OfflineInput
from us_equity_strategies.research.soxl_soxx_typed_baseline_result import run_typed_baseline
from us_equity_strategies.research.soxl_core_optimization import (
    BASELINE_WINDOW_DAYS,
    CANDIDATE_WINDOWS,
    EXPECTED_SOURCE_BLOBS,
    MC_BLOCK_LENGTH,
    MC_PATH_LENGTH,
    MC_SEED_HEX,
    MC_TRIALS,
    PLUGIN_CONTROL,
    SCENARIOS,
    WINDOW_SPECS,
    _later_predicates,
    _select_validation_winner,
    load_persisted_result,
    persist_result,
    run_soxl_core_optimization,
    simulate_candidate,
)


def _canonical_bytes(rows: list[InputRow]) -> bytes:
    lines = ["symbol,as_of,open,high,low,close,volume"]
    for row in rows:
        lines.append(
            ",".join(
                (
                    row.symbol,
                    row.as_of,
                    *(format(value, ".17g") for value in (row.open, row.high, row.low, row.close, row.volume)),
                )
            )
        )
    return ("\n".join(lines) + "\n").encode()


def _source(count: int = 753) -> OfflineInput:
    rows: list[InputRow] = []
    for index in range(count):
        day = (date(2023, 7, 14) + timedelta(days=index)).isoformat()
        soxx_close = 100.0 + (index % 31)
        soxl_open = 50.0 + (index % 7)
        soxl_close = soxl_open * (1.0 + ((index % 5) - 2) / 100.0)
        rows.extend(
            (
                InputRow("SOXL", day, soxl_open, max(soxl_open, soxl_close), min(soxl_open, soxl_close), soxl_close, 1.0),
                InputRow("SOXX", day, soxx_close, soxx_close, soxx_close, soxx_close, 1.0),
            )
        )
    return OfflineInput(
        tuple(rows),
        _canonical_bytes(rows),
        "78c056c9a4541b7612b4f077ca25df6093aa6eb2f17783097c5b5f83a31dd5c6",
        "c" * 40,
    )


def _validation_metrics(
    sharpe: float,
    cumulative_return: float,
    max_drawdown: float,
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    return tuple(
        {
            "sharpe": sharpe,
            "cumulative_return": cumulative_return,
            "max_drawdown": max_drawdown,
        }
        for _ in range(3)
    )


def _source_commit() -> str:
    return subprocess.check_output(
        ("git", "rev-parse", "HEAD"), text=True
    ).strip()


def _trusted_arguments(result: dict[str, object], source_commit: str) -> dict[str, object]:
    return {
        "expected_source_commit": source_commit,
        "expected_source_blobs": EXPECTED_SOURCE_BLOBS,
        "expected_csv_sha256": optimization.EXPECTED_ARTIFACT_SHA256,
        "expected_manifest_sha256": optimization.EXPECTED_MANIFEST_SHA256,
        "expected_readback_sha256": optimization.EXPECTED_READBACK_SHA256,
        "expected_typed_digest": optimization.EXPECTED_INPUT_DIGEST,
        "expected_result_digest": optimization._digest(optimization._wire(result)),
    }


def test_frozen_candidates_plugin_costs_and_r3_windows() -> None:
    assert CANDIDATE_WINDOWS == (140, 160, 180, 200)
    assert BASELINE_WINDOW_DAYS == 200
    assert PLUGIN_CONTROL == {"state": "ABSENT", "enabled": False, "optimization_eligible": False}
    assert (MC_SEED_HEX, MC_TRIALS, MC_PATH_LENGTH, MC_BLOCK_LENGTH) == (
        "08a73485a70548df5262ad66ac86e02c0c5cc6255469156832aec2e86b501e2b", 10_000, 126, 12,
    )
    assert [(item.scenario_id, item.commission_bps, item.slippage_bps) for item in SCENARIOS] == [
        ("ZERO", 0, 0), ("C1_2", 1, 2), ("C2_5", 2, 5), ("C5_10_STRESS", 5, 10),
    ]
    assert WINDOW_SPECS == (
        ("SMA_WARMUP", 0, 199), ("F1_TRAIN", 200, 368), ("F1_EMBARGO_1", 369, 369),
        ("F1_VALIDATION", 370, 411), ("F1_EMBARGO_2", 412, 412), ("F1_TEST", 413, 454),
        ("F2_TRAIN", 200, 454), ("F2_EMBARGO_1", 455, 455), ("F2_VALIDATION", 456, 497),
        ("F2_EMBARGO_2", 498, 498), ("F2_TEST", 499, 540), ("F3_TRAIN", 200, 540),
        ("F3_EMBARGO_1", 541, 541), ("F3_VALIDATION", 542, 583), ("F3_EMBARGO_2", 584, 584),
        ("F3_TEST", 585, 626), ("FINAL_HOLDOUT", 627, 752),
    )


def test_common_start_prior_close_next_open_and_sma200_zero_parity() -> None:
    source = _source()
    baseline = run_typed_baseline(source)
    for window in CANDIDATE_WINDOWS:
        points = simulate_candidate(source, window, SCENARIOS[0])
        assert len(points) == len(baseline.equity_curve)
        assert points[0].date == baseline.equity_curve[0].date
    zero = simulate_candidate(source, 200, SCENARIOS[0])
    assert [(point.date, point.end_equity.hex(), point.cash.hex(), point.quantity.hex()) for point in zero] == [
        (point.date, point.equity.hex(), point.cash.hex(), point.soxl_quantity.hex()) for point in baseline.equity_curve
    ]


def test_validation_selection_uses_ordered_ties_without_holdout_input() -> None:
    metrics = {
        140: _validation_metrics(1.0, 0.10, -0.20),
        160: _validation_metrics(1.0, 0.15, -0.30),
        180: _validation_metrics(1.0, 0.15, -0.05),
        200: _validation_metrics(1.0, 0.15, -0.10),
    }
    assert _select_validation_winner(metrics) == 180
    assert _select_validation_winner({**metrics, 180: _validation_metrics(1.0, 0.15, -0.10), 200: _validation_metrics(1.0, 0.15, -0.10)}) == 200


def test_later_predicates_are_strict_and_do_not_reselect() -> None:
    accepted = _later_predicates(
        winner=180,
        baseline=200,
        winner_validation_sharpe=0.2,
        baseline_validation_sharpe=0.1,
        selected_wfa_c2_5_returns=(0.1, 0.0, 0.2),
        final_c2_5_return=0.2,
        baseline_final_c2_5_return=0.1,
        final_c2_5_drawdown=-0.1,
        baseline_final_c2_5_drawdown=-0.1,
        final_stress_return=0.01,
        terminal_loss_probability=0.49,
    )
    assert accepted == (True, ())
    rejected, failures = _later_predicates(
        winner=200,
        baseline=200,
        winner_validation_sharpe=0.1,
        baseline_validation_sharpe=0.1,
        selected_wfa_c2_5_returns=(0.1, 0.0, -0.1),
        final_c2_5_return=0.0,
        baseline_final_c2_5_return=0.0,
        final_c2_5_drawdown=-0.2,
        baseline_final_c2_5_drawdown=-0.1,
        final_stress_return=0.0,
        terminal_loss_probability=0.5,
    )
    assert rejected is False
    assert failures


def test_each_wfa_test_uses_only_its_own_prior_validation_lock() -> None:
    validation_by_fold = (
        {
            140: _validation_metrics(0.4, 0.1, -0.2)[0],
            160: _validation_metrics(0.3, 0.1, -0.2)[0],
            180: _validation_metrics(0.2, 0.1, -0.2)[0],
            200: _validation_metrics(0.1, 0.1, -0.2)[0],
        },
        {
            140: _validation_metrics(0.1, 0.1, -0.2)[0],
            160: _validation_metrics(0.4, 0.1, -0.2)[0],
            180: _validation_metrics(0.3, 0.1, -0.2)[0],
            200: _validation_metrics(0.2, 0.1, -0.2)[0],
        },
        {
            140: _validation_metrics(0.1, 0.1, -0.2)[0],
            160: _validation_metrics(0.2, 0.1, -0.2)[0],
            180: _validation_metrics(0.4, 0.1, -0.2)[0],
            200: _validation_metrics(0.3, 0.1, -0.2)[0],
        },
    )

    assert optimization._select_fold_winners(validation_by_fold) == (140, 160, 180)


def test_invalid_baseline_source_contract_becomes_invalid_evidence(monkeypatch) -> None:
    monkeypatch.setattr(optimization, "_verify_immutable_input", lambda source: None)
    invalid = run_soxl_core_optimization(replace(_source(), source_revision=""))

    assert invalid["evidence_valid"] is False
    assert invalid["failure_codes"] == ["BASELINE_INPUT_CONTRACT_INVALID"]


def test_invalid_input_plugin_and_identity_fail_closed_without_provider_path() -> None:
    source = _source()
    for result in (
        run_soxl_core_optimization(None),
        run_soxl_core_optimization(source, plugin_control={"state": "PRESENT"}),
        run_soxl_core_optimization(replace(source, input_digest="b" * 64)),
        run_soxl_core_optimization(source, expected_input_digest="b" * 64),
    ):
        assert result["evidence_valid"] is False
        assert result["outcome"] == "NO_IMPROVEMENT"
        assert result["research_recommendation"] is None
        assert result["research_only"] is True
        assert result["live_adoption_authorized"] is False
        assert result["size_zero_required"] is True
        assert result["failure_codes"]


def test_canonical_persistence_is_atomic_idempotent_and_strict(tmp_path) -> None:
    result = run_soxl_core_optimization(None)
    source_commit = _source_commit()
    trusted = _trusted_arguments(result, source_commit)
    paths = persist_result(result, tmp_path, source_commit=source_commit)
    bundle = paths.bundle.read_bytes()
    assert paths.bundle.name == "soxl_core_optimization_v1.json"
    assert bundle.endswith(b"\n")
    assert paths.sidecar.read_text() == hashlib.sha256(bundle).hexdigest() + "\n"
    assert load_persisted_result(tmp_path, **trusted) == json.loads(bundle)
    assert json.loads(paths.readback.read_text())["source_blobs"] == EXPECTED_SOURCE_BLOBS
    assert persist_result(result, tmp_path, source_commit=source_commit).bundle.read_bytes() == bundle
    paths.bundle.write_bytes(b"different")
    with pytest.raises(ValueError):
        persist_result(result, tmp_path, source_commit=source_commit)
    paths.readback.write_text("{}\n")
    with pytest.raises(ValueError):
        load_persisted_result(tmp_path, **trusted)


def test_persistence_rejects_false_commit_provenance(tmp_path) -> None:
    with pytest.raises(ValueError):
        persist_result(run_soxl_core_optimization(None), tmp_path, source_commit="0" * 40)


def test_loader_rejects_self_consistent_rewritten_bundle_without_trusted_result_digest(tmp_path) -> None:
    result = run_soxl_core_optimization(None)
    source_commit = _source_commit()
    trusted = _trusted_arguments(result, source_commit)
    paths = persist_result(result, tmp_path, source_commit=source_commit)
    rewritten = json.loads(paths.bundle.read_text())
    rewritten["forged"] = True
    bundle = optimization._canonical_bytes(rewritten)
    readback = json.loads(paths.readback.read_text())
    readback["bundle_sha256"] = hashlib.sha256(bundle).hexdigest()
    readback["bundle_bytes"] = len(bundle)
    readback["result_digest"] = optimization._digest(rewritten)
    paths.bundle.write_bytes(bundle)
    paths.sidecar.write_text(readback["bundle_sha256"] + "\n")
    paths.readback.write_bytes(optimization._canonical_bytes(readback))

    with pytest.raises(ValueError):
        load_persisted_result(tmp_path, **trusted)
