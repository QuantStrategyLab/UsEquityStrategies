import hashlib
import json
from pathlib import Path

import pytest

from us_equity_strategies.research.legacy_combo_derived_returns_replay import (
    CLASSIFICATION,
    EXPECTED_LEGACY_ARTIFACT_SHA256,
    SCHEMA,
    TRIAL_LEDGER,
    LegacyReplayError,
    persist_report,
    run_pinned_legacy_combo_replay,
)

ARTIFACT = Path(__file__).parents[1] / "docs/research/us_equity_combo_backtest_20260628.json"


def test_pinned_artifact_replays_a_fixed_non_promotable_trial_ledger() -> None:
    artifact = ARTIFACT.read_bytes()
    assert hashlib.sha256(artifact).hexdigest() == EXPECTED_LEGACY_ARTIFACT_SHA256

    first = run_pinned_legacy_combo_replay(artifact)
    second = run_pinned_legacy_combo_replay(artifact)

    assert first == second
    assert first["schema"] == SCHEMA
    assert first["classification"] == CLASSIFICATION
    assert first["research_only"] is True
    assert first["candidate_selection"] == "PROHIBITED"
    assert first["promotion_recommendation"] is None
    assert first["p4_paper_authorized"] is False
    assert first["p5_shadow_authorized"] is False
    assert first["p6_live_authorized"] is False
    assert [item["trial_id"] for item in first["trial_results"]] == [item[0] for item in TRIAL_LEDGER]
    assert first["source"]["date_range"] == {"start": "2015-01-02", "end": "2026-06-26", "sessions": 2887}
    assert first["source"]["immutable_raw_market_input_available"] is False
    assert first["source"]["point_in_time_membership_available"] is False
    assert first["source"]["execution_cost_model_available"] is False


def test_artifact_bytes_are_pinned_before_any_result_can_be_reported() -> None:
    with pytest.raises(LegacyReplayError, match="LEGACY_ARTIFACT_SHA256_MISMATCH"):
        run_pinned_legacy_combo_replay(ARTIFACT.read_bytes() + b" ")


def test_persist_is_canonical_create_only_and_idempotent(tmp_path: Path) -> None:
    report = run_pinned_legacy_combo_replay(ARTIFACT.read_bytes())
    output = tmp_path / "legacy-report.json"

    assert persist_report(report, output) == output
    expected = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode() + b"\n"
    assert output.read_bytes() == expected
    assert persist_report(report, output) == output

    output.write_bytes(b"different\n")
    with pytest.raises(LegacyReplayError, match="REPORT_PATH_ALREADY_CONTAINS_DIFFERENT_CONTENT"):
        persist_report(report, output)
