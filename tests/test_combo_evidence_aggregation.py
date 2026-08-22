from __future__ import annotations

from us_equity_strategies.combo_evidence_aggregation import (
    SCHEMA_VERSION,
    aggregate_combo_evidence,
)
from us_equity_strategies.portfolio_risk_budget import (
    PortfolioAssetRiskSpec,
    PortfolioRiskBudgetPolicy,
)


SPECS = {
    "TQQQ": PortfolioAssetRiskSpec("TQQQ", 3.0, "NASDAQ100"),
    "SOXL": PortfolioAssetRiskSpec("SOXL", 3.0, "SEMIS"),
    "BOXX": PortfolioAssetRiskSpec("BOXX", 1.0, "USD", is_cash=True),
}
POLICY = PortfolioRiskBudgetPolicy(
    cash_symbol="BOXX",
    max_effective_risk_exposure=2.0,
    max_symbol_weights={"TQQQ": 0.4, "SOXL": 0.2},
    max_underlying_effective_exposure={"NASDAQ100": 1.2, "SEMIS": 0.8},
)


def _component(candidate_id: str = "TQQQ_P3") -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "evidence_digest": "a" * 64,
        "input_digest": "b" * 64,
        "evidence_valid": True,
        "research_eligibility_status": "ELIGIBLE",
    }


def test_aggregates_exact_component_refs_and_calls_risk_budget() -> None:
    result = aggregate_combo_evidence(
        combo_candidate_id="combo-2026-08-23",
        combo_revision="r1",
        components=[_component(), _component("SOXL_P3")],
        target_weights={"TQQQ": 0.3, "SOXL": 0.1, "BOXX": 0.6},
        asset_risk_specs=SPECS,
        policy=POLICY,
    )

    assert result["schema_version"] == SCHEMA_VERSION
    assert result["status"] == "READY_RESEARCH_ONLY"
    assert result["execution_authorized"] is False
    assert result["promotion_authorized"] is False
    assert result["component_refs"] == [
        {"candidate_id": "TQQQ_P3", "evidence_digest": "a" * 64, "input_digest": "b" * 64, "eligible": True},
        {"candidate_id": "SOXL_P3", "evidence_digest": "a" * 64, "input_digest": "b" * 64, "eligible": True},
    ]
    assert len(result["evidence_digest"]) == 64


def test_ineligible_component_parks_and_preserves_identity_refs() -> None:
    component = _component()
    component["research_eligibility_status"] = "NOT_EVALUATED"
    result = aggregate_combo_evidence(
        combo_candidate_id="combo-2026-08-23",
        combo_revision="r1",
        components=[component],
        target_weights={"TQQQ": 0.3, "BOXX": 0.7},
        asset_risk_specs=SPECS,
        policy=POLICY,
    )
    assert result["status"] == "PARKED"
    assert result["reason_codes"] == ("COMPONENT_EVIDENCE_NOT_ELIGIBLE",)
    assert result["component_refs"][0]["evidence_digest"] == "a" * 64


def test_invalid_digest_fails_closed() -> None:
    component = _component()
    component["input_digest"] = "not-a-digest"
    result = aggregate_combo_evidence(
        combo_candidate_id="combo-2026-08-23",
        combo_revision="r1",
        components=[component],
        target_weights={"TQQQ": 0.3, "BOXX": 0.7},
        asset_risk_specs=SPECS,
        policy=POLICY,
    )
    assert result["status"] == "PARKED"
    assert result["reason_codes"] == ("COMPONENT_IDENTITY_INVALID",)


def test_result_digest_changes_when_component_binding_changes() -> None:
    kwargs = dict(
        combo_candidate_id="combo-2026-08-23",
        combo_revision="r1",
        target_weights={"TQQQ": 0.3, "BOXX": 0.7},
        asset_risk_specs=SPECS,
        policy=POLICY,
    )
    first = aggregate_combo_evidence(components=[_component()], **kwargs)
    changed = _component()
    changed["evidence_digest"] = "c" * 64
    second = aggregate_combo_evidence(components=[changed], **kwargs)
    assert first["evidence_digest"] != second["evidence_digest"]
