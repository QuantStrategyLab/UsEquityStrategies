from __future__ import annotations

from us_equity_strategies.research.batch_a_member_pack import CASH_RETURN_POLICY
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


def _pack(members: tuple[dict[str, object], ...], **overrides: object) -> dict[str, object]:
    import hashlib
    import json

    payload: dict[str, object] = {
        "schema_version": "qsl.c3-batch-a-frozen-member-pack.v2",
        "research_only": True,
        "execution_authorized": False,
        "cash_return_policy": CASH_RETURN_POLICY,
        "source_note": "CONTRACT_FIXTURE_NOT_PRIVATE_EVIDENCE",
        "members": list(members),
    }
    payload.update(overrides)
    if "pack_digest" not in payload:
        body = {key: value for key, value in payload.items() if key != "pack_digest"}
        payload["pack_digest"] = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
    return payload


def test_default_path_parks_without_inventing_returns() -> None:
    result = evaluate_batch_a_existing_member_baselines()
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
    assert "BATCH_A_C3_MEMBER_PACK_NOT_PROVIDED" in result["reason_codes"]
    assert "BATCH_A_REFUSE_TO_INVENT_RETURNS" in result["reason_codes"]
    assert "BATCH_A_LEGACY_R3_ENTRY_EXITED" in result["reason_codes"]
    assert result["standalone_members"] == []
    assert result["c3_comparison"] is None
    assert result["boundaries"]["return_invention"] == "FORBIDDEN"
    assert result["boundaries"]["legacy_combo_derived_returns"] == (
        "REJECTED_NOT_BATCH_A_EVIDENCE"
    )
    assert result["boundaries"]["legacy_r3_private_root"] == "EXITED_ACTIVE_BATCH_A_ENTRY"


def test_frozen_pack_contract_can_form_reviewable_batch_a_result() -> None:
    """Contract-only pack verifies wiring; not a claim of production evidence."""

    pack = _pack(
        (
            _member("cash_sleeve", (0.0, 0.0, 0.0, 0.0), evidence_digest="d" * 64),
            _member("soxl_core", (0.01, -0.02, 0.03, 0.00), evidence_digest="e" * 64),
            _member("tqqq_core", (0.02, -0.01, 0.01, -0.01), evidence_digest="f" * 64),
        )
    )
    result = evaluate_batch_a_existing_member_baselines(frozen_member_pack=pack)
    assert result["status"] == "READY_RESEARCH_ONLY"
    assert result["batch_a_accepted"] is True
    assert result["execution_authorized"] is False
    assert result["no_order"] is True
    assert result["cash_return_policy"] == CASH_RETURN_POLICY
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
    assert first["capital_path"] is None
    assert first["metrics_accounting"]["risk_scaling_applied"] is False
    assert first["metrics_accounting"]["rebalance_fees_applied"] is False
    assert comparison["boundaries"]["risk_scaling_applied_to_returns"] == "NOT_APPLIED"
    assert comparison["boundaries"]["rebalance_fee_reconstruction"] == "NOT_COMPUTED"
    assert result["boundaries"]["rebalance_fee_reconstruction"] == (
        "NOT_COMPUTED_MISSING_COMBO_FEE_BPS_AND_REBALANCE_SCHEDULE"
    )


def test_nonzero_assumed_zero_cash_parks() -> None:
    pack = _pack(
        (
            _member("cash_sleeve", (0.0, 0.0, 0.01, 0.0)),
            _member("soxl_core", (0.01, -0.02, 0.03, 0.00)),
            _member("tqqq_core", (0.02, -0.01, 0.01, -0.01)),
        )
    )
    result = evaluate_batch_a_existing_member_baselines(frozen_member_pack=pack)
    assert result["status"] == "PARKED"
    assert result["batch_a_accepted"] is False
    assert "ASSUMED_ZERO_CASH_RETURNS_NONZERO" in result["reason_codes"]


def test_v1_pack_is_rejected() -> None:
    result = evaluate_batch_a_existing_member_baselines(
        frozen_member_pack={
            "schema_version": "qsl.c3-batch-a-frozen-member-pack.v1",
            "research_only": True,
            "execution_authorized": False,
            "cash_return_policy": "ASSUMED_ZERO_CASH_SLEEVE",
            "members": (),
        }
    )
    assert result["status"] == "PARKED"
    assert "FROZEN_MEMBER_PACK_V1_REJECTED" in result["reason_codes"]
