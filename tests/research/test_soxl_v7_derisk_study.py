import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from us_equity_strategies.research.soxl_v7_derisk_study import (
    ablation_plan, plugin_inventory, qualification_report, registration, workflow_report,
)


def test_plugin_union_includes_off_deprecated_noop_options_and_unknown_custom():
    inv = plugin_inventory(["custom_unresolved_fixture"])
    assert inv["status"] == "INCOMPLETE"
    assert inv["unresolved"] == ["custom_unresolved_fixture"]
    assert set(("market_regime_control", "panic_reversal_shadow", "income_layer",
        "taco_panic_rebound", "crisis_regime_guard", "soxx_put_credit_spread_income_v1",
        "qqq_price_regime_observer")).issubset(inv["discovered"])
    for row in inv["rows"]:
        assert set(row["scores"]) == set("DCSMOLVRHG")
        assert row["scores"]["V"]["status"] == "missing"
        assert row["gap_severity"] == 13.5/14
    arms = ablation_plan(inv["discovered"])
    assert len(arms) == 2+2*len(inv["discovered"])
    for name in inv["discovered"]:
        assert {"SINGLE_"+name, "FULL_MINUS_"+name}.issubset({a["arm"] for a in arms})
    assert all(a["status"] == "NOT_COMPARABLE" for a in arms)
    assert all(a["mandatory_protection_preserved"] for a in arms)


def test_missing_archive_or_supplied_locator_never_claim_replay_pass_or_shadow():
    for locator in ("", "gs://research-inputs/research/v2/input/fixture-rs04"):
        r = qualification_report(cloud=True, archive_prefix=locator)
        assert r["status"] == "PARKED" and r["real_bars_read"] == 0
        assert r["real_replay_executed"] is False
        assert r["baseline_binding"]["effective_config_verified"] is False
        assert r["shadow_draft_created"] is False and r["promotion_eligible"] is False
        assert r["calibration_report"]["status"] == "INCONCLUSIVE"
        assert set(r["stress_coverage_report"]) == {"2000", "2008", "2020", "2022"}
        assert all(s["status"] == "COVERAGE_BLOCKED" for s in r["stress_coverage_report"].values())
        assert r["real_only_report"]["metrics"] is None
        assert r["full_synthetic_extended_report"]["metrics"] is None
        json.dumps(r, allow_nan=False)
    with pytest.raises(ValueError, match="ARCHIVE_PREFIX_REJECTED"):
        qualification_report(cloud=True, archive_prefix="gs://unapproved-bucket/raw")


def test_configured_root_is_shared_with_b3_without_qualifying_an_archive(monkeypatch):
    root = "gs://synthetic-study-inputs/research/v2/input/"
    monkeypatch.setenv("RS04_INPUT_ROOT", root)
    result = qualification_report(cloud=True, archive_prefix=root+"fixture-rs04")
    assert result["status"] == "PARKED" and result["real_bars_read"] == 0
    assert result["approved_archive"]["locator_provided"] is True
    assert result["approved_archive"]["gcs_copy_verified"] is False
    assert result["baseline_binding"]["effective_config_verified"] is False
    assert "HISTORICAL_PLUGIN_CONTEXT_BINDINGS_UNAVAILABLE" in result["blockers"]
    with pytest.raises(ValueError, match="ARCHIVE_PREFIX_REJECTED"):
        qualification_report(cloud=True, archive_prefix="gs://research-inputs/research/v2/input/fixture-rs04")
    with pytest.raises(ValueError, match="ARCHIVE_PREFIX_REJECTED"):
        qualification_report(cloud=True, archive_prefix=root+"fixture/../other")


def test_registration_does_not_change_b1_b2_or_v7_window_or_call_weak_holdout_untouched():
    r = registration()
    assert r["pseudo_holdout"]["untouched"] is False
    assert r["pseudo_holdout"]["excluded_from_training"]
    assert r["shadow"]["does_not_replace_original_v7_252_sessions_or_human_review"]
    assert r["shadow"]["execution_parity_target_sessions"] == 20
    assert r["cost_multipliers"] == [1., 2.]
    assert r["config_reference_is_effective_live"] is False


def test_cloud_entry_rejects_mac_without_authentication_or_price_read(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(ValueError, match="CLOUD_ONLY"):
        workflow_report()


def test_cli_registration_only_and_cloud_boundary(tmp_path):
    root = Path(__file__).resolve().parents[2]
    out = tmp_path/"registration.json"
    cmd = [sys.executable, str(root/"scripts/run_soxl_v7_derisk_research.py"), "--output", str(out)]
    proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(out.read_text())["candidate_id"] == "soxl_v7_strict_derisk_only_v1"
    env = dict(os.environ)
    env.pop("GITHUB_ACTIONS", None)
    proc = subprocess.run(cmd+["--cloud-qualify"], cwd=root, capture_output=True, text=True, env=env)
    assert proc.returncode != 0 and "CLOUD_ONLY" in proc.stderr
