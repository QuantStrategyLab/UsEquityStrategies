from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from us_equity_strategies.manifests import global_etf_rotation_manifest


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = ROOT / "docs" / "evidence" / "global_etf_rotation"


def _load(name: str) -> dict[str, object]:
    return json.loads((EVIDENCE_ROOT / name).read_text(encoding="utf-8"))


def _load_gate_module():
    path = ROOT / "scripts" / "gate_evidence_package.py"
    spec = importlib.util.spec_from_file_location("gate_evidence_package", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_config_snapshot_matches_current_global_etf_defaults() -> None:
    snapshot = _load("config-snapshot.json")
    parameters = snapshot["parameters"]
    excluded = snapshot["excluded_runtime_default_keys"]
    assert isinstance(parameters, dict)
    assert isinstance(excluded, list)
    defaults = global_etf_rotation_manifest.default_config

    for name, expected in parameters.items():
        expected_value = tuple(expected) if isinstance(defaults[name], tuple) else expected
        assert defaults[name] == expected_value
    assert set(parameters) | set(excluded) == set(defaults)
    assert set(parameters).isdisjoint(excluded)
    assert snapshot["source"]["scope"] == "core_signal_only"


def test_research_spec_is_explicitly_blocked_without_oos_claim() -> None:
    spec = _load("research-spec.json")
    data = spec["data"]
    evaluation = spec["evaluation"]
    trial_ledger = spec["trial_ledger"]
    assert isinstance(data, dict)
    assert isinstance(evaluation, dict)
    assert isinstance(trial_ledger, dict)
    assert isinstance(evaluation["out_of_sample"], dict)

    assert spec["review_state"] == "blocked"
    assert data["point_in_time_validated"] is False
    assert data["survivorship_bias_controlled"] is False
    assert evaluation["frozen_before_oos"] is False
    assert evaluation["out_of_sample"]["locked"] is False
    assert trial_ledger["record_all_trials"] is False


def test_optimization_spec_is_a_bounded_future_plan() -> None:
    spec = _load("optimization-spec.json")
    parameters = spec["allowed_parameters"]
    promotion = spec["promotion"]
    assert isinstance(parameters, list)
    assert isinstance(promotion, dict)

    allowed = {item["name"] for item in parameters if isinstance(item, dict)}
    assert allowed <= set(global_etf_rotation_manifest.default_config)
    assert spec["execution_state"] == "not_started_no_results"
    assert spec["search"]["method"] == "random"
    assert spec["search"]["max_trials"] == 72
    assert promotion["automatic_risk_increase_allowed"] is False
    assert promotion["full_kelly_allowed"] is False
    assert promotion["requires_human_approval"] is True


def test_cost_and_trial_artifacts_expose_unresolved_research_work() -> None:
    costs = _load("cost-model.json")
    ledger = _load("trial-ledger.json")

    assert costs["state"] == "provisional_not_cost_stress_validated"
    assert costs["implementation"]["cost_bps"] == 10.0
    assert ledger["state"] == "empty_pending_real_data_research"
    assert ledger["entries"] == []


def test_promotion_gate_requires_changed_valid_strategy_specs() -> None:
    gate = _load_gate_module()
    diff = "\n".join(
        (
            "+++ b/docs/evidence/global_etf_rotation/research-spec.json",
            "+++ b/docs/evidence/global_etf_rotation/optimization-spec.json",
        )
    )
    bundles = gate._discover_strategy_specs(diff)
    bundle = bundles[Path("docs/evidence/global_etf_rotation")]
    paths = [bundle[name] for name in sorted(bundle)]

    assert [path.name for path in paths] == ["optimization-spec.json", "research-spec.json"]
    valid, issues = gate._validate_strategy_specs(paths, validator=lambda _path: [])
    assert valid is True
    assert issues == []

    valid, issues = gate._validate_strategy_specs(paths, validator=lambda _path: ["blocked by missing PIT data"])
    assert valid is False
    assert issues == [
        "docs/evidence/global_etf_rotation/optimization-spec.json: blocked by missing PIT data",
        "docs/evidence/global_etf_rotation/research-spec.json: blocked by missing PIT data",
    ]


def test_promotion_gate_resolves_profile_and_requires_matching_bundle() -> None:
    gate = _load_gate_module()
    diff = "\n".join(
        (
            "diff --git a/src/us_equity_strategies/catalog.py b/src/us_equity_strategies/catalog.py",
            "+++ b/src/us_equity_strategies/catalog.py",
            "@@ -550,20 +550,20 @@",
            "     GLOBAL_ETF_ROTATION_PROFILE: StrategyMetadata(",
            "         canonical_profile=GLOBAL_ETF_ROTATION_PROFILE,",
            '-        status="research_backtest_only",',
            '+        status="runtime_enabled",',
            "+++ b/docs/evidence/global_etf_rotation/research-spec.json",
            "+++ b/docs/evidence/global_etf_rotation/optimization-spec.json",
        )
    )
    bundles = gate._discover_strategy_specs(diff)

    assert gate._promoted_profiles(diff) == {"global_etf_rotation"}
    paths, issues = gate._spec_bundle_for_profile("global_etf_rotation", bundles)
    assert [path.name for path in paths] == ["optimization-spec.json", "research-spec.json"]
    assert issues == []
    paths, issues = gate._spec_bundle_for_profile("ibit_smart_dca", bundles)
    assert paths == []
    assert issues == ["ibit_smart_dca: expected one changed strategy-spec directory named 'ibit_smart_dca'"]


def test_promotion_detection_ignores_status_examples_outside_src() -> None:
    gate = _load_gate_module()
    diff = "\n".join(
        (
            "diff --git a/tests/test_example.py b/tests/test_example.py",
            "+++ b/tests/test_example.py",
            '+    text = \'+        status="runtime_enabled"\'',
        )
    )

    assert gate._promotion_detected(diff) is False
    assert gate._promoted_profiles(diff) == set()


def test_git_diff_includes_src_and_evidence_paths(monkeypatch) -> None:
    gate = _load_gate_module()
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="diff")

    monkeypatch.setattr(gate.subprocess, "run", fake_run)

    assert gate._git_diff("main") == "diff"
    assert calls == [["git", "diff", "--unified=20", "origin/main...HEAD"]]


def test_strategy_spec_discovery_does_not_mix_evidence_directories(tmp_path: Path, monkeypatch) -> None:
    gate = _load_gate_module()
    for path in (
        tmp_path / "docs/evidence/strategy_a/research-spec.json",
        tmp_path / "docs/evidence/strategy_b/optimization-spec.json",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    diff = "\n".join(
        (
            "+++ b/docs/evidence/strategy_a/research-spec.json",
            "+++ b/docs/evidence/strategy_b/optimization-spec.json",
        )
    )

    bundles = gate._discover_strategy_specs(diff)

    assert bundles == {
        Path("docs/evidence/strategy_a"): {
            "research-spec.json": Path("docs/evidence/strategy_a/research-spec.json")
        },
        Path("docs/evidence/strategy_b"): {
            "optimization-spec.json": Path("docs/evidence/strategy_b/optimization-spec.json")
        },
    }


def test_promotion_evidence_discovery_excludes_research_artifacts() -> None:
    gate = _load_gate_module()
    diff = "\n".join(
        (
            "+++ b/docs/evidence/global_etf_rotation/research-spec.json",
            "+++ b/docs/evidence/global_etf_rotation/cost-model.json",
            "+++ b/docs/evidence/global_etf_rotation/promotion-evidence.json",
        )
    )

    assert gate._evidence_paths_from_diff(diff) == [
        Path("docs/evidence/global_etf_rotation/promotion-evidence.json")
    ]
