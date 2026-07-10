from __future__ import annotations

import importlib.util
import json
from pathlib import Path

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
    assert isinstance(parameters, dict)
    defaults = global_etf_rotation_manifest.default_config

    for name, expected in parameters.items():
        expected_value = tuple(expected) if isinstance(defaults[name], tuple) else expected
        assert defaults[name] == expected_value


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
    paths = gate._discover_strategy_specs(diff)

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
