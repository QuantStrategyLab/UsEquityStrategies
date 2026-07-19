from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from us_equity_strategies.research.tqqq_decision_snapshot import (
    DecisionSnapshotError,
    capture_tqqq_decision_snapshot_if_enabled,
    read_tqqq_decision_snapshot_package,
    write_tqqq_decision_snapshot,
)


def _capture(path: Path) -> dict[str, object]:
    return {
        "path": str(path),
        "session": {"id": "tqqq_growth_income:2026-07-17", "sequence": 0},
        "timestamp": "2026-07-17T20:00:00.000000Z",
        "source": {
            "identity": "benchmark_history",
            "raw_provenance": {"as_of": "2026-07-17T20:00:00.000000Z"},
            "resolved": {"symbol": "QQQ"},
        },
        "input": {
            "identity": "normalized_qqq_closes",
            "raw_provenance": {"available_at": "2026-07-17T20:00:00.000000Z"},
            "resolved": {"count": 260},
        },
        "control": {
            "identity": "tqqq_controls",
            "raw_provenance": {"origin": "runtime_config"},
            "resolved": {"enabled": True},
        },
        "plugin": {
            "identity": "tqqq_growth_income",
            "raw_provenance": {"module": "us_equity_strategies.entrypoints"},
            "resolved": {"profile": "tqqq_growth_income"},
        },
    }


def _decision(symbol: str = "TQQQ") -> dict[str, object]:
    facts = {
        "positions": [{"symbol": symbol, "target_weight": "0.2", "target_value": None}],
        "budgets": [],
        "risk_flags": [],
    }
    return {**facts, "identity": sha256(_canonical_bytes(facts)).hexdigest()}


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def test_missing_opt_in_is_a_filesystem_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: pytest.fail("filesystem access"))

    assert capture_tqqq_decision_snapshot_if_enabled(
        {}, pre_risk_decision=_decision(), final_decision=_decision()
    ) is None


def test_exact_opt_in_captures_one_canonical_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "tqqq-decision.json"

    written = capture_tqqq_decision_snapshot_if_enabled(
        _capture(path), pre_risk_decision=_decision(), final_decision=_decision()
    )

    assert written is not None
    assert written.path == path
    assert read_tqqq_decision_snapshot_package(path) == written


def test_capture_does_not_mutate_existing_decision_facts(tmp_path: Path) -> None:
    decision = _decision()
    original = json.loads(json.dumps(decision))

    capture_tqqq_decision_snapshot_if_enabled(
        _capture(tmp_path / "tqqq-decision.json"),
        pre_risk_decision=decision,
        final_decision=decision,
    )

    assert decision == original


def test_readback_rejects_missing_fields_and_bool_for_uint(tmp_path: Path) -> None:
    path = tmp_path / "tqqq-decision.json"
    write_tqqq_decision_snapshot(path, _capture(path), pre_risk_decision=_decision(), final_decision=_decision())
    value = json.loads(path.read_text(encoding="utf-8"))
    value["session"]["sequence"] = True
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    with pytest.raises(DecisionSnapshotError, match="^INVALID_DECISION_SNAPSHOT$"):
        read_tqqq_decision_snapshot_package(path)


def test_readback_fails_closed_for_digest_tampering(tmp_path: Path) -> None:
    path = tmp_path / "tqqq-decision.json"
    write_tqqq_decision_snapshot(path, _capture(path), pre_risk_decision=_decision(), final_decision=_decision())
    path.write_bytes(path.read_bytes().replace(b"benchmark_history", b"benchmark_hxstory"))

    with pytest.raises(DecisionSnapshotError, match="^INVALID_DECISION_SNAPSHOT$"):
        read_tqqq_decision_snapshot_package(path)


def test_write_failure_removes_temporary_file_and_preserves_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "tqqq-decision.json"
    path.write_text("existing", encoding="utf-8")
    import us_equity_strategies.research.tqqq_decision_snapshot as snapshot

    monkeypatch.setattr(snapshot.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("blocked")))

    with pytest.raises(DecisionSnapshotError, match="^DECISION_SNAPSHOT_WRITE_FAILED$"):
        write_tqqq_decision_snapshot(path, _capture(path), pre_risk_decision=_decision(), final_decision=_decision())

    assert path.read_text(encoding="utf-8") == "existing"
    assert not list(tmp_path.glob(".tqqq-decision.json.*.tmp"))
