from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from quant_platform_kit.strategy_spec import validate_strategy_spec_file
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


def test_pinned_qpk_validator_accepts_plan_and_blocks_unproven_research() -> None:
    assert validate_strategy_spec_file(EVIDENCE_ROOT / "optimization-spec.json") == []
    assert validate_strategy_spec_file(EVIDENCE_ROOT / "research-spec.json") == [
        "data.point_in_time_validated must be True",
        "data.survivorship_bias_controlled must be True",
        "evaluation.frozen_before_oos must be True",
        "evaluation.out_of_sample.locked must be True",
        "trial_ledger.record_all_trials must be True",
    ]


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


def test_strategy_bundle_satisfies_evidence_prerequisite_without_legacy_package(monkeypatch) -> None:
    gate = _load_gate_module()
    paths = [
        Path("docs/evidence/global_etf_rotation/optimization-spec.json"),
        Path("docs/evidence/global_etf_rotation/research-spec.json"),
    ]
    monkeypatch.setattr(gate, "_git_diff", lambda _base: "promotion diff")
    monkeypatch.setattr(gate, "_promotion_detected", lambda _diff: True)
    monkeypatch.setattr(gate, "_discover_evidence_files", lambda _diff: [])
    monkeypatch.setattr(
        gate, "_resolve_promoted_profiles", lambda _diff: ({"global_etf_rotation"}, [])
    )
    monkeypatch.setattr(gate, "_discover_strategy_specs", lambda _diff: {Path("bundle"): {}})
    monkeypatch.setattr(gate, "_spec_bundle_for_profile", lambda _profile, _bundles: (paths, []))
    monkeypatch.setattr(gate, "_validate_strategy_specs", lambda _paths: (True, []))
    monkeypatch.setattr(gate, "_run_promotion_dual_review", lambda _files: 0)

    assert gate.main() == 0


def test_promotion_gate_resolves_profile_and_requires_matching_bundle() -> None:
    gate = _load_gate_module()
    diff = "\n".join(
        (
            "diff --git a/src/us_equity_strategies/catalog.py b/src/us_equity_strategies/catalog.py",
            "+++ b/src/us_equity_strategies/catalog.py",
            "@@ -558,3 +558,3 @@",
            "         role=\"defensive_rotation\",",
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


def test_promotion_gate_rejects_specs_from_different_research_runs(
    tmp_path: Path, monkeypatch
) -> None:
    gate = _load_gate_module()
    monkeypatch.chdir(tmp_path)
    bundle_root = Path("docs/evidence/global_etf_rotation")
    bundle_root.mkdir(parents=True)
    research_path = bundle_root / "research-spec.json"
    optimization_path = bundle_root / "optimization-spec.json"
    research_path.write_text(
        json.dumps({"strategy_profile": "global_etf_rotation", "spec_id": "research.current"}),
        encoding="utf-8",
    )
    optimization_path.write_text(
        json.dumps(
            {"strategy_profile": "global_etf_rotation", "research_spec_id": "research.stale"}
        ),
        encoding="utf-8",
    )

    paths, issues = gate._spec_bundle_for_profile(
        "global_etf_rotation",
        {
            bundle_root: {
                "research-spec.json": research_path,
                "optimization-spec.json": optimization_path,
            }
        },
    )

    assert paths == [optimization_path, research_path]
    assert (
        f"{optimization_path}: research_spec_id must match {research_path} spec_id 'research.current'"
        in issues
    )


def test_promotion_profile_constants_are_resolved_in_the_changed_module(
    tmp_path: Path, monkeypatch
) -> None:
    gate = _load_gate_module()
    source_root = tmp_path / "src/package"
    source_root.mkdir(parents=True)
    (source_root / "other.py").write_text('STRATEGY_PROFILE = "wrong_profile"\n', encoding="utf-8")
    catalog = source_root / "catalog.py"
    catalog.write_text(
        '\n'.join(
            (
                'STRATEGY_PROFILE = "right_profile"',
                'metadata = StrategyMetadata(',
                '    canonical_profile=STRATEGY_PROFILE,',
                '    status="runtime_enabled",',
                ')',
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    diff = "\n".join(
        (
            "diff --git a/src/package/catalog.py b/src/package/catalog.py",
            "+++ b/src/package/catalog.py",
            "@@ -3,2 +3,2 @@",
            "     canonical_profile=STRATEGY_PROFILE,",
            '-    status="research_backtest_only",',
            '+    status="runtime_enabled",',
        )
    )

    assert gate._promoted_profiles(diff) == {"right_profile"}


def test_promotion_gate_reports_every_unresolved_status_location(tmp_path: Path, monkeypatch) -> None:
    gate = _load_gate_module()
    source = tmp_path / "src/package/catalog.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join(
            (
                'PROFILE_A = "strategy_a"',
                "first = StrategyMetadata(",
                "    canonical_profile=PROFILE_A,",
                '    status="runtime_enabled",',
                ")",
                "second = StrategyMetadata(",
                "    canonical_profile=make_profile(),",
                '    status="shadow_candidate",',
                ")",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    diff = "\n".join(
        (
            "diff --git a/src/package/catalog.py b/src/package/catalog.py",
            "+++ b/src/package/catalog.py",
            "@@ -3,6 +3,6 @@",
            "     canonical_profile=PROFILE_A,",
            '-    status="research_backtest_only",',
            '+    status="runtime_enabled",',
            " )",
            " second = StrategyMetadata(",
            "     canonical_profile=make_profile(),",
            '-    status="research_backtest_only",',
            '+    status="shadow_candidate",',
        )
    )

    profiles, unresolved = gate._resolve_promoted_profiles(diff)

    assert profiles == {"strategy_a"}
    assert unresolved == [
        "src/package/catalog.py:8: promoted status 'shadow_candidate' must resolve to exactly one profile"
    ]


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


def test_strategy_spec_discovery_rejects_noncanonical_evidence_roots(
    tmp_path: Path, monkeypatch
) -> None:
    gate = _load_gate_module()
    for name in ("research-spec.json", "optimization-spec.json"):
        path = tmp_path / "tests/evidence/global_etf_rotation" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    diff = "\n".join(
        (
            "+++ b/tests/evidence/global_etf_rotation/research-spec.json",
            "+++ b/tests/evidence/global_etf_rotation/optimization-spec.json",
        )
    )

    assert gate._discover_strategy_specs(diff) == {}


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


def test_research_artifact_names_remain_valid_legacy_evidence_outside_bundle(tmp_path: Path, monkeypatch) -> None:
    gate = _load_gate_module()
    evidence_path = tmp_path / "docs/evidence/cost-model.json"
    evidence_path.parent.mkdir(parents=True)
    evidence_path.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert gate._evidence_paths_from_diff("+++ b/docs/evidence/cost-model.json") == [
        Path("docs/evidence/cost-model.json")
    ]
