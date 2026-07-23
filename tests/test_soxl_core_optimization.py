from datetime import date, timedelta
import hashlib
import json
import os

import pytest

from us_equity_strategies.research.soxl_soxx_offline_input_contract import InputRow, OfflineInput
from us_equity_strategies.research.soxl_soxx_typed_baseline_result import run_typed_baseline
from us_equity_strategies.research.soxl_core_optimization import (
    BASELINE_WINDOW_DAYS,
    CANDIDATE_WINDOWS,
    PLUGIN_CONTROL,
    SCENARIOS,
    WINDOW_SPECS,
    OptimizationError,
    _choose_fold_winner,
    _eligibility,
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
                (row.symbol, row.as_of, *(format(value, ".17g") for value in (row.open, row.high, row.low, row.close, row.volume)))
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
        rows.extend((
            InputRow("SOXL", day, soxl_open, max(soxl_open, soxl_close), min(soxl_open, soxl_close), soxl_close, 1.0),
            InputRow("SOXX", day, soxx_close, soxx_close, soxx_close, soxx_close, 1.0),
        ))
    return OfflineInput(tuple(rows), _canonical_bytes(rows), "78c056c9a4541b7612b4f077ca25df6093aa6eb2f17783097c5b5f83a31dd5c6", "c" * 40)


def _result() -> dict[str, object]:
    return run_soxl_core_optimization(_source())


def _anchors() -> dict[str, object]:
    return {
        "caller": "trusted",
        "input": "trusted",
        "result": "trusted",
        "source_commit": "de3a2abf87a76521bd6a525c0b4ad43d275482fa",
        "source_blobs": {
            "soxl_soxx_offline_input_contract.py": "b4a16842c33d39851724fa31993001cd27a4c986",
            "soxl_soxx_typed_baseline_result.py": "aa1b43a9e5ab59b34d41932b3b18653451ffe46b",
            "r3_joint_evidence.py": "118553cada8800dde80c30bbca5927da342b1e85",
            "soxl_core_optimization.py": None,
        },
    }


def _base_anchors() -> dict[str, object]:
    anchors = _anchors()
    anchors["source_commit"] = "0d65af0cdaae28d33f63f22e3a716349fdeaf004"
    return anchors

def test_frozen_candidates_baseline_r3_and_plugin_sentinel() -> None:
    assert CANDIDATE_WINDOWS == (140, 160, 180, 200)
    assert BASELINE_WINDOW_DAYS == 200
    assert PLUGIN_CONTROL == {"state": "ABSENT", "enabled": False, "optimization_eligible": False}
    assert [(item.scenario_id, item.commission_bps, item.slippage_bps) for item in SCENARIOS] == [
        ("ZERO", 0, 0), ("C1_2", 1, 2), ("C2_5", 2, 5), ("C5_10_STRESS", 5, 10),
    ]
    assert [(name, start, end) for name, start, end in WINDOW_SPECS] == [
        ("F1_VALIDATION", 370, 411), ("F1_TEST", 413, 454),
        ("F2_VALIDATION", 456, 497), ("F2_TEST", 499, 540),
        ("F3_VALIDATION", 542, 583), ("F3_TEST", 585, 626), ("FINAL_HOLDOUT", 627, 752),
    ]


def test_sma200_zero_parity_and_next_open_timing() -> None:
    source = _source()
    points = simulate_candidate(source, 200, SCENARIOS[0])
    baseline = run_typed_baseline(source)
    assert [(point.date, point.end_equity.hex()) for point in points] == [(point.date, point.equity.hex()) for point in baseline.equity_curve]
    assert points[0].date == baseline.equity_curve[0].date


def test_validation_selection_has_frozen_ordered_tiebreaks() -> None:
    rows = {
        140: {"sharpe": 1.0, "cumulative_return": 0.1, "max_drawdown": -0.2},
        160: {"sharpe": 1.0, "cumulative_return": 0.2, "max_drawdown": -0.3},
        180: {"sharpe": 1.0, "cumulative_return": 0.2, "max_drawdown": -0.1},
        200: {"sharpe": 1.0, "cumulative_return": 0.2, "max_drawdown": -0.1},
    }
    assert _choose_fold_winner(rows) == 200


def test_r3_fold_locks_and_later_gates_are_strict() -> None:
    assert _eligibility((0.1, 0.0, 0.2), 0.01, 0.01, 0.49) == ("PASS", ())
    status, failures = _eligibility((0.1, 0.0, -0.1), 0.0, 0.0, 0.5)
    assert status == "FAIL"
    assert len(failures) == 4


def test_invalid_input_and_plugin_fail_closed_with_size_zero() -> None:
    invalid = run_soxl_core_optimization(None)
    assert invalid["outcome"] == "NO_IMPROVEMENT"
    assert invalid["research_recommendation"] is None
    assert invalid["size_zero_required"] is True
    assert invalid["failure_codes"]
    assert run_soxl_core_optimization(_source(), plugin_control={"state": "PRESENT"})["evidence_valid"] is False


def test_source_commit_blob_and_trusted_anchor_verification_fail_closed(tmp_path) -> None:
    paths = persist_result(_result(), tmp_path, source_commit=_anchors()["source_commit"], trusted_anchors=_anchors())
    readback = json.loads(paths.readback.read_text())
    readback["source_commit"] = "d" * 40
    paths.readback.write_text(json.dumps(readback), encoding="utf-8")
    with pytest.raises(OptimizationError, match="SOURCE_COMMIT_ANCHOR_MISMATCH"):
        load_persisted_result(tmp_path, trusted_anchors=_anchors())


def test_source_commit_without_optimizer_module_is_rejected(tmp_path) -> None:
    anchors = _base_anchors()

    with pytest.raises(OptimizationError, match="SOURCE_BLOB_MISMATCH"):
        persist_result(_result(), tmp_path, source_commit=anchors["source_commit"], trusted_anchors=anchors)


def test_persistence_is_atomic_and_strict_with_trusted_anchors(tmp_path) -> None:
    result = _result()
    anchors = _anchors()
    paths = persist_result(result, tmp_path, source_commit=anchors["source_commit"], trusted_anchors=anchors)
    bundle = paths.bundle.read_bytes()
    assert paths.sidecar.read_text() == hashlib.sha256(bundle).hexdigest() + "\n"
    assert load_persisted_result(tmp_path, trusted_anchors=anchors) == json.loads(bundle)
    assert persist_result(result, tmp_path, source_commit=anchors["source_commit"], trusted_anchors=anchors).bundle.read_bytes() == bundle


def test_precreated_predictable_temp_symlink_cannot_overwrite_external_sentinel(tmp_path) -> None:
    external = tmp_path / "external-sentinel"
    external.write_text("unchanged", encoding="utf-8")
    target = tmp_path / ".soxl_core_optimization_v1.json.tmp"
    target.symlink_to(external)
    persist_result(_result(), tmp_path, source_commit=_anchors()["source_commit"], trusted_anchors=_anchors())
    assert external.read_text(encoding="utf-8") == "unchanged"


def test_injected_publication_failure_cleans_owned_temporaries_and_preserves_final(monkeypatch, tmp_path) -> None:
    import us_equity_strategies.research.soxl_core_optimization as module

    original_replace = os.replace
    monkeypatch.setattr(module.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("injected")))
    with pytest.raises(OptimizationError, match="PERSIST_WRITE_FAILED"):
        persist_result(_result(), tmp_path, source_commit=_anchors()["source_commit"], trusted_anchors=_anchors())
    assert not list(tmp_path.glob(".soxl-core-*.tmp"))
    assert not (tmp_path / "soxl_core_optimization_v1.json").exists()
    monkeypatch.setattr(module.os, "replace", original_replace)


def test_no_provider_path_or_semantic_adoption() -> None:
    result = _result()
    assert result["research_only"] is True
    assert result["live_adoption_authorized"] is False
    assert result["plugin_control"] == PLUGIN_CONTROL
