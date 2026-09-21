from __future__ import annotations

from types import SimpleNamespace

from us_equity_strategies.research.c3_batch_a_existing_member_baseline import (
    EVIDENCE_SCOPE,
    REQUIRED_MEMBER_IDS,
    SCHEMA_VERSION,
    evaluate_batch_a_existing_member_baselines,
)


def _comp(**overrides: object) -> dict[str, object]:
    payload = {
        "as_of": "2026-09-21",
        "quote_currency": "USD",
        "capital_basis_digest": "1" * 64,
        "cost_model_digest": "2" * 64,
        "risk_policy_digest": "3" * 64,
        "data_scope_digest": "4" * 64,
    }
    payload.update(overrides)
    return payload


def _member(member_id: str, returns: tuple[float, ...], **overrides: object) -> dict[str, object]:
    payload = {
        "member_id": member_id,
        "evidence_digest": "a" * 64,
        "input_digest": "b" * 64,
        "dates": ("2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"),
        "returns": returns,
        **_comp(),
    }
    payload.update(overrides)
    return payload


def _ready_readiness() -> SimpleNamespace:
    return SimpleNamespace(
        is_ready=True,
        to_dict=lambda: {
            "schema_version": "qsl.research.r3_evidence_readiness.v1",
            "ready": True,
            "source_commit": "0" * 40,
            "findings": [],
        },
    )


def _not_ready_readiness() -> SimpleNamespace:
    return SimpleNamespace(
        is_ready=False,
        to_dict=lambda: {
            "schema_version": "qsl.research.r3_evidence_readiness.v1",
            "ready": False,
            "source_commit": "0" * 40,
            "findings": [
                "TQQQ_INPUT_IDENTITY_MISMATCH",
                "SOXL_INPUT_IDENTITY_MISMATCH",
            ],
        },
    )


def test_default_path_parks_without_inventing_returns() -> None:
    result = evaluate_batch_a_existing_member_baselines(
        private_root="/tmp/missing-batch-a-private-research",
        _r3_readiness_reader=lambda **_: _not_ready_readiness(),
    )
    assert result["schema_version"] == SCHEMA_VERSION
    assert result["evidence_scope"] == EVIDENCE_SCOPE
    assert result["status"] == "PARKED"
    assert result["batch_a_accepted"] is False
    assert result["research_only"] is True
    assert result["shadow_only"] is True
    assert result["execution_authorized"] is False
    assert result["promotion_authorized"] is False
    assert result["no_order"] is True
    assert "BATCH_A_FROZEN_COMPARABLE_INPUTS_UNAVAILABLE" in result["reason_codes"]
    assert "BATCH_A_PRIVATE_R3_NOT_READY" in result["reason_codes"]
    assert "BATCH_A_C3_MEMBER_PACK_NOT_PROVIDED" in result["reason_codes"]
    assert "BATCH_A_REFUSE_TO_INVENT_RETURNS" in result["reason_codes"]
    assert result["standalone_members"] == []
    assert result["c3_comparison"] is None
    assert "IN_REPO_ALIGNED_SOXL_TQQQ_DAILY_RETURNS_MISSING" in result["evidence_gaps"]
    assert result["boundaries"]["return_invention"] == "FORBIDDEN"
    assert result["boundaries"]["legacy_combo_derived_returns"] == (
        "REJECTED_NOT_BATCH_A_EVIDENCE"
    )


def test_r3_ready_but_missing_pack_still_parks() -> None:
    result = evaluate_batch_a_existing_member_baselines(
        _r3_readiness_reader=lambda **_: _ready_readiness(),
    )
    assert result["status"] == "PARKED"
    assert result["batch_a_accepted"] is False
    assert "BATCH_A_C3_MEMBER_PACK_NOT_PROVIDED" in result["reason_codes"]
    assert "BATCH_A_REFUSE_TO_INVENT_RETURNS" in result["reason_codes"]
    assert result["execution_authorized"] is False
    assert result["c3_comparison"] is None


def test_frozen_pack_contract_can_form_reviewable_batch_a_result() -> None:
    """Contract-only pack verifies wiring; not a claim of production R3 evidence."""

    pack = {
        "schema_version": "qsl.c3-batch-a-frozen-member-pack.v1",
        "research_only": True,
        "execution_authorized": False,
        "cash_return_policy": "ASSUMED_ZERO_CASH_SLEEVE",
        "pack_digest": "c" * 64,
        "source_note": "CONTRACT_FIXTURE_NOT_PRIVATE_R3_EVIDENCE",
        "members": (
            _member("cash_sleeve", (0.0, 0.0, 0.0, 0.0), evidence_digest="d" * 64),
            _member("soxl_core", (0.01, -0.02, 0.03, 0.00), evidence_digest="e" * 64),
            _member("tqqq_core", (0.02, -0.01, 0.01, -0.01), evidence_digest="f" * 64),
        ),
    }
    result = evaluate_batch_a_existing_member_baselines(
        frozen_member_pack=pack,
        _r3_readiness_reader=lambda **_: _ready_readiness(),
    )
    assert result["status"] == "READY_RESEARCH_ONLY"
    assert result["batch_a_accepted"] is True
    assert result["execution_authorized"] is False
    assert result["no_order"] is True
    assert result["cash_return_policy"] == "ASSUMED_ZERO_CASH_SLEEVE"
    assert [item["member_id"] for item in result["standalone_members"]] == list(
        REQUIRED_MEMBER_IDS
    )
    assert all(item["metrics"]["session_count"] == 4 for item in result["standalone_members"])
    comparison = result["c3_comparison"]
    assert comparison is not None
    assert comparison["status"] == "READY_RESEARCH_ONLY"
    assert comparison["execution_authorized"] is False
    assert comparison["no_order"] is True
    assert len(comparison["baselines"]) >= 2
    assert any(
        item["baseline_id"] == "shadow_like_352045_tqqq_soxl_cash"
        for item in comparison["baselines"]
    )
    first = comparison["baselines"][0]
    assert "terminal_nav" in first["metrics"]
    assert "max_drawdown" in first["metrics"]
    assert "annualized_volatility" in first["metrics"]
    assert "tail_loss_proxy_min_daily_return" in first["metrics"]
    assert first["declared_one_way_turnover"] is not None
    assert first["concentration"] is not None
    assert first["concentration"]["execution_authorized"] is False


def test_nonzero_assumed_zero_cash_parks() -> None:
    pack = {
        "schema_version": "qsl.c3-batch-a-frozen-member-pack.v1",
        "research_only": True,
        "execution_authorized": False,
        "cash_return_policy": "ASSUMED_ZERO_CASH_SLEEVE",
        "members": (
            _member("cash_sleeve", (0.0, 0.0, 0.01, 0.0)),
            _member("soxl_core", (0.01, -0.02, 0.03, 0.00)),
            _member("tqqq_core", (0.02, -0.01, 0.01, -0.01)),
        ),
    }
    result = evaluate_batch_a_existing_member_baselines(
        frozen_member_pack=pack,
        _r3_readiness_reader=lambda **_: _ready_readiness(),
    )
    assert result["status"] == "PARKED"
    assert result["batch_a_accepted"] is False
    assert "ASSUMED_ZERO_CASH_RETURNS_NONZERO" in result["reason_codes"]
