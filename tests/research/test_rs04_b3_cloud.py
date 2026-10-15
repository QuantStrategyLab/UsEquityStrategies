"""Cloud contract tests use metadata stubs only; never cloud or real prices."""
from datetime import date

import pytest

from us_equity_strategies.research.rs04_b3_cloud import (
    RS04_ARCHIVE_SHA256, cloud_report, input_root, inspect_manifest, walk_forward_plan,
)


def test_cloud_preflight_no_local_real_data_and_no_implicit_source_override():
    calls = []
    def reader(uri):
        calls.append(uri)
        return {"schema": "qsl.research.price_snapshot.v2", "provider": "alpaca", "feed": "sip",
                "adjustment": "raw", "symbols": ["QQQM", "BOXX"]}, {"generation": "1", "sha256": "a"*64}
    result = cloud_report(execute=True, cloud=False, today=date(2026, 10, 15),
                          sources=["gs://research-inputs/research/v2/input/fixture/manifest.json"],
                          reader=reader)
    assert calls == [] and result["status"] == "PARKED"
    assert "CLOUD_ONLY" in result["blockers"]


def test_unknown_or_total_return_source_cannot_become_provisional_raw_source():
    inspected = inspect_manifest({"schema": "unrecognised", "adjustment": "all"}, {})
    assert inspected["raw_plus_actions_qualified"] is False
    assert inspected["live_baseline_qualified"] is False


def test_registered_archive_identity_does_not_qualify_replay_or_alias_legacy_bytes(monkeypatch):
    monkeypatch.setenv("RS04_INPUT_ROOT", "gs://synthetic-study-inputs/research/v2/input/")
    legacy = "b5a3dd40cb0d4260fb15492108bde7ab544ac0d191807943637b8216a6808482"
    for digest, matching in ((RS04_ARCHIVE_SHA256, True), (legacy, False)):
        def reader(uri):
            return {"schema_version": "qsl.research.raw_sip_input.v1"}, {
                "generation": "1", "sha256": digest,
            }
        report = cloud_report(execute=True, cloud=True, today=date(2026, 10, 15),
                              sources=[input_root()+"fixture/manifest.json"], reader=reader)
        assert report["approved_archive"]["matching_metadata_found"] is matching
        assert report["status"] == "PARKED" and report["acceptance"] == "INSUFFICIENT_EVIDENCE"
        assert report["real_replay_executed"] is False and report["recommend_shadow"] is False
        assert "EFFECTIVE_MERGED_LIVE_CONFIG_NOT_BOUND" in report["blockers"]
        assert report["source_metadata"][0]["raw_plus_actions_qualified"] is False


def test_cloud_read_error_preserves_artifact_and_never_echoes_error():
    def denied(uri):
        raise RuntimeError("secret-like-provider-diagnostic-do-not-echo")
    result = cloud_report(execute=True, cloud=True, today=date(2026, 10, 15),
                          sources=["gs://research-inputs/research/v2/input/fixture/manifest.json"], reader=denied)
    assert result["status"] == "PARKED" and result["source_metadata"][0]["status"] == "UNAVAILABLE"
    assert "secret-like" not in str(result)
    assert result["baseline_vs_b3"]["SOXL"]["1.0"]["baseline"]["cagr"] is None
    assert result["recommend_shadow"] is False


def test_future_holdout_not_renamed_history_and_no_unqualified_real_replay():
    result = cloud_report(execute=True, cloud=True, today=date(2026, 10, 15), sources=[], reader=None)
    assert result["holdout"]["status"] == "NOT_YET_OBSERVABLE"
    assert result["holdout"]["start"] == "2026-11-01"
    assert result["acceptance"] == "INSUFFICIENT_EVIDENCE"
    assert "LIVE_AS_IS_BASELINE_NOT_BOUND" in result["blockers"]
    assert result["real_bars_read"] == 0 and result["real_replay_executed"] is False


def test_no_execute_does_not_read_any_manifest():
    def forbidden(uri):
        raise AssertionError("should not read")
    result = cloud_report(execute=False, cloud=True, today=date(2026, 10, 15), sources=[], reader=forbidden)
    assert result["status"] == "PLAN_ONLY"


def test_wfa_boundaries_purge_embargo_and_no_partial_success_fold():
    plan = walk_forward_plan(1600, feature_warmup=272)
    folds = plan["folds"]
    assert len(folds) >= 4
    for fold in folds:
        assert fold["train_end"]-fold["train_start"] == 504
        assert fold["validation_start"]-fold["train_end"] == 5
        assert fold["validation_end"]-fold["validation_start"] == 126
        assert fold["test_start"]-fold["validation_end"] == 5
        assert fold["test_end"]-fold["test_start"] == 126
    assert all(a["test_end"] == b["test_start"] for a, b in zip(folds, folds[1:]))
    assert walk_forward_plan(917, feature_warmup=272)["status"] == "INSUFFICIENT_COVERAGE"


def test_synthetic_failure_blocks_cloud_reads_and_is_not_reported_as_auth_failure():
    calls = []
    def forbidden(uri):
        calls.append(uri)
        raise AssertionError("should not read")
    result = cloud_report(execute=True, cloud=True, today=date(2026, 10, 15),
                          sources=["gs://research-inputs/research/v2/input/fixture/manifest.json"],
                          reader=forbidden, auth_ok=False, synthetic_ok=False)
    assert calls == []
    assert "SYNTHETIC_ACCEPTANCE_NOT_PASSED" in result["blockers"]
    assert "EXISTING_WIF_AUTHENTICATION_UNAVAILABLE" not in result["blockers"]


def test_native_reader_cannot_run_on_mac_even_with_inherited_ci_environment(monkeypatch):
    import pytest
    import us_equity_strategies.research.rs04_b3_cloud as module
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_OS", "Linux")
    monkeypatch.setenv("GITHUB_REPOSITORY", "QuantStrategyLab/UsEquityStrategies")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    with pytest.raises(ValueError, match="CLOUD_ONLY"):
        module.read_cloud_manifest("gs://research-inputs/research/v2/input/fixture/manifest.json")


def test_configured_root_accepts_only_its_manifest_and_preserves_evidence_gaps(monkeypatch):
    root = "gs://synthetic-study-inputs/research/v2/input/"
    monkeypatch.setenv("RS04_INPUT_ROOT", root)
    calls = []

    def reader(uri):
        calls.append(uri)
        return {}, {"generation": "1", "sha256": "a"*64}

    result = cloud_report(execute=True, cloud=True, today=date(2026, 10, 15),
                          sources=[root+"fixture/manifest.json",
                                   "gs://research-inputs/research/v2/input/fixture/manifest.json"],
                          reader=reader)
    assert calls == [root+"fixture/manifest.json"]
    assert result["source_metadata"][1]["reason"] == "SOURCE_PREFIX_REJECTED"
    assert result["status"] == "PARKED" and result["real_bars_read"] == 0
    assert "EFFECTIVE_MERGED_LIVE_CONFIG_NOT_BOUND" in result["blockers"]
    assert result["approved_archive"]["matching_metadata_found"] is False
    assert result["live_ready"] is False and result["promotion_eligible"] is False


@pytest.mark.parametrize("root", [
    "https://synthetic-study-inputs/research/v2/input/",
    "gs://synthetic-study-inputs/research/v2/input/../",
    "gs://synthetic-study-inputs/research/v2/input/?extra=1",
    "gs://synthetic-study-inputs/other/",
    "gs://synthetic-study-inputs/research/v2/input",
])
def test_invalid_configured_root_is_rejected_before_a_reader_call(monkeypatch, root):
    monkeypatch.setenv("RS04_INPUT_ROOT", root)

    def forbidden(uri):
        raise AssertionError("reader must not run")

    with pytest.raises(ValueError, match="INPUT_ROOT_REJECTED"):
        cloud_report(execute=True, cloud=True, today=date(2026, 10, 15),
                     sources=[root+"fixture/manifest.json"], reader=forbidden)


def test_native_reader_rejects_another_root_before_subprocess(monkeypatch):
    import us_equity_strategies.research.rs04_b3_cloud as module
    monkeypatch.setenv("RS04_INPUT_ROOT", "gs://synthetic-study-inputs/research/v2/input/")
    monkeypatch.setattr(module, "_cloud_environment", lambda: True)

    def forbidden(*args, **kwargs):
        raise AssertionError("no Cloud SDK command is permitted")

    monkeypatch.setattr(module.subprocess, "run", forbidden)
    with pytest.raises(ValueError, match="SOURCE_PREFIX_REJECTED"):
        module.read_cloud_manifest("gs://research-inputs/research/v2/input/fixture/manifest.json")
