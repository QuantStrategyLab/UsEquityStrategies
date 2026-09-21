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


def _comparable(candidate_id: str = "TQQQ_P3", **overrides: object) -> dict[str, object]:
    payload = _component(candidate_id)
    payload.update(
        {
            "as_of": "2026-09-21",
            "quote_currency": "USD",
            "capital_basis_digest": "1" * 64,
            "cost_model_digest": "2" * 64,
            "risk_policy_digest": "3" * 64,
            "data_scope_digest": "4" * 64,
        }
    )
    payload.update(overrides)
    return payload


def test_aggregates_exact_component_refs_and_calls_risk_budget() -> None:
    result = aggregate_combo_evidence(
        combo_candidate_id="combo-2026-08-23",
        combo_revision="r1",
        components=[_comparable("TQQQ_P3"), _comparable("SOXL_P3")],
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
    assert result["comparability"] == {
        "as_of": "2026-09-21",
        "quote_currency": "USD",
        "capital_basis_digest": "1" * 64,
        "cost_model_digest": "2" * 64,
        "risk_policy_digest": "3" * 64,
        "data_scope_digest": "4" * 64,
    }
    assert len(result["evidence_digest"]) == 64


def test_ineligible_component_parks_and_preserves_identity_refs() -> None:
    component = _comparable()
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
    component = _comparable()
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
    first = aggregate_combo_evidence(components=[_comparable()], **kwargs)
    changed = _comparable()
    changed["evidence_digest"] = "c" * 64
    second = aggregate_combo_evidence(components=[changed], **kwargs)
    assert first["evidence_digest"] != second["evidence_digest"]


def test_matching_comparability_fields_remain_research_only() -> None:
    result = aggregate_combo_evidence(
        combo_candidate_id="combo-2026-08-23",
        combo_revision="r1",
        components=[_comparable("TQQQ_P3"), _comparable("SOXL_P3")],
        target_weights={"TQQQ": 0.3, "SOXL": 0.1, "BOXX": 0.6},
        asset_risk_specs=SPECS,
        policy=POLICY,
    )
    assert result["status"] == "READY_RESEARCH_ONLY"
    assert result["execution_authorized"] is False
    assert result["promotion_authorized"] is False


def test_jointly_missing_comparability_fields_park() -> None:
    result = aggregate_combo_evidence(
        combo_candidate_id="combo-2026-08-23",
        combo_revision="r1",
        components=[_component(), _component("SOXL_P3")],
        target_weights={"TQQQ": 0.3, "SOXL": 0.1, "BOXX": 0.6},
        asset_risk_specs=SPECS,
        policy=POLICY,
    )
    assert result["status"] == "PARKED"
    assert result["reason_codes"] == ("COMPONENT_COMPARABILITY_MISSING",)
    assert result["execution_authorized"] is False
    assert result["promotion_authorized"] is False


def test_shared_partial_comparability_subset_parks_incomplete() -> None:
    left = _component("TQQQ_P3")
    right = _component("SOXL_P3")
    left["as_of"] = "2026-09-21"
    right["as_of"] = "2026-09-21"
    result = aggregate_combo_evidence(
        combo_candidate_id="combo-2026-08-23",
        combo_revision="r1",
        components=[left, right],
        target_weights={"TQQQ": 0.3, "SOXL": 0.1, "BOXX": 0.6},
        asset_risk_specs=SPECS,
        policy=POLICY,
    )
    assert result["status"] == "PARKED"
    assert result["reason_codes"] == ("COMPONENT_COMPARABILITY_INCOMPLETE",)


def test_mismatched_as_of_parks_for_c2_comparability() -> None:
    result = aggregate_combo_evidence(
        combo_candidate_id="combo-2026-08-23",
        combo_revision="r1",
        components=[
            _comparable("TQQQ_P3"),
            _comparable("SOXL_P3", as_of="2026-09-20"),
        ],
        target_weights={"TQQQ": 0.3, "SOXL": 0.1, "BOXX": 0.6},
        asset_risk_specs=SPECS,
        policy=POLICY,
    )
    assert result["status"] == "PARKED"
    assert result["reason_codes"] == ("COMPONENT_COMPARABILITY_MISMATCH",)
    assert result["execution_authorized"] is False
    assert result["promotion_authorized"] is False
    assert result["component_refs"][0]["candidate_id"] == "TQQQ_P3"


def test_partial_comparability_declaration_parks_incomplete() -> None:
    partial = _component("SOXL_P3")
    partial["as_of"] = "2026-09-21"
    result = aggregate_combo_evidence(
        combo_candidate_id="combo-2026-08-23",
        combo_revision="r1",
        components=[_comparable("TQQQ_P3"), partial],
        target_weights={"TQQQ": 0.3, "SOXL": 0.1, "BOXX": 0.6},
        asset_risk_specs=SPECS,
        policy=POLICY,
    )
    assert result["status"] == "PARKED"
    assert result["reason_codes"] == ("COMPONENT_COMPARABILITY_INCOMPLETE",)
    assert result["execution_authorized"] is False


def test_invalid_declared_comparability_digest_parks_incomplete() -> None:
    result = aggregate_combo_evidence(
        combo_candidate_id="combo-2026-08-23",
        combo_revision="r1",
        components=[
            _comparable("TQQQ_P3", cost_model_digest="not-a-digest"),
            _comparable("SOXL_P3"),
        ],
        target_weights={"TQQQ": 0.3, "SOXL": 0.1, "BOXX": 0.6},
        asset_risk_specs=SPECS,
        policy=POLICY,
    )
    assert result["status"] == "PARKED"
    assert result["reason_codes"] == ("COMPONENT_COMPARABILITY_INCOMPLETE",)
    assert result["execution_authorized"] is False
    assert result["promotion_authorized"] is False


def test_illegal_as_of_or_currency_parks_incomplete() -> None:
    bad_as_of = aggregate_combo_evidence(
        combo_candidate_id="combo-2026-08-23",
        combo_revision="r1",
        components=[
            _comparable("TQQQ_P3", as_of="09/21/2026"),
            _comparable("SOXL_P3", as_of="09/21/2026"),
        ],
        target_weights={"TQQQ": 0.3, "SOXL": 0.1, "BOXX": 0.6},
        asset_risk_specs=SPECS,
        policy=POLICY,
    )
    assert bad_as_of["status"] == "PARKED"
    assert bad_as_of["reason_codes"] == ("COMPONENT_COMPARABILITY_INCOMPLETE",)

    bad_currency = aggregate_combo_evidence(
        combo_candidate_id="combo-2026-08-23",
        combo_revision="r1",
        components=[
            _comparable("TQQQ_P3", quote_currency="usd"),
            _comparable("SOXL_P3", quote_currency="usd"),
        ],
        target_weights={"TQQQ": 0.3, "SOXL": 0.1, "BOXX": 0.6},
        asset_risk_specs=SPECS,
        policy=POLICY,
    )
    assert bad_currency["status"] == "PARKED"
    assert bad_currency["reason_codes"] == ("COMPONENT_COMPARABILITY_INCOMPLETE",)
