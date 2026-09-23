from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from us_equity_strategies.research.batch_a_member_pack import (
    CASH_RETURN_POLICY,
    frozen_cost_model_digest,
)
from us_equity_strategies.research.c3_batch_a_existing_member_baseline import (
    EVIDENCE_SCOPE,
    MEMBER_COST_SCENARIO,
    REQUIRED_MEMBER_IDS,
    SCHEMA_VERSION,
    evaluate_batch_a_existing_member_baselines,
)
import scripts.run_c3_batch_a_existing_member_baseline as batch_a_cli


def _comp(**overrides: object) -> dict[str, object]:
    payload = {
        "as_of": "2026-09-21",
        "quote_currency": "USD",
        "capital_basis_digest": "1" * 64,
        "cost_model_digest": frozen_cost_model_digest(),
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


def _contract_pack(
    *,
    cash: tuple[float, ...] = (0.0, 0.0, 0.0, 0.0),
    soxl: tuple[float, ...] = (0.01, -0.02, 0.03, 0.00),
    tqqq: tuple[float, ...] = (0.02, -0.01, 0.01, -0.01),
    dates: tuple[str, ...] = ("2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"),
) -> dict[str, object]:
    return _pack(
        (
            _member("cash_sleeve", cash, dates=dates, evidence_digest="d" * 64),
            _member("soxl_core", soxl, dates=dates, evidence_digest="e" * 64),
            _member("tqqq_core", tqqq, dates=dates, evidence_digest="f" * 64),
        )
    )


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
    assert "member_transaction_cost_scenario" not in result["boundaries"]
    assert "member_transaction_costs" not in result["boundaries"]
    assert result["boundaries"]["capital_path_requested"] is False


def test_frozen_pack_contract_can_form_reviewable_batch_a_result() -> None:
    """Contract-only pack verifies wiring; not a claim of production evidence."""

    pack = _contract_pack()
    result = evaluate_batch_a_existing_member_baselines(frozen_member_pack=pack)
    assert result["status"] == "READY_RESEARCH_ONLY"
    assert result["batch_a_accepted"] is True
    assert result["execution_authorized"] is False
    assert result["promotion_authorized"] is False
    assert result["no_order"] is True
    assert result["cash_return_policy"] == CASH_RETURN_POLICY
    assert result["member_cost_scenario"] == MEMBER_COST_SCENARIO
    assert result["capital_path_requested"] is False
    assert result["capital_path_options"] is None
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
    assert result["boundaries"]["member_transaction_costs"] == (
        "TYPED_BASELINE_ZERO_ASSUMED_NO_MEMBER_FEES"
    )
    assert result["boundaries"]["member_transaction_cost_scenario"] == (
        MEMBER_COST_SCENARIO
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
    assert "member_transaction_cost_scenario" not in result["boundaries"]
    assert "member_transaction_costs" not in result["boundaries"]


def test_missing_fee_or_schedule_returns_explicit_gaps() -> None:
    pack = _contract_pack()
    fee_only = evaluate_batch_a_existing_member_baselines(
        frozen_member_pack=pack,
        capital_path_options={"rebalance_fee_bps": 10.0},
    )
    assert fee_only["status"] == "PARKED"
    assert fee_only["batch_a_accepted"] is False
    assert fee_only["execution_authorized"] is False
    assert fee_only["promotion_authorized"] is False
    assert fee_only["no_order"] is True
    assert fee_only["capital_path_requested"] is True
    assert "BATCH_A_CAPITAL_PATH_INPUTS_INCOMPLETE" in fee_only["reason_codes"]
    assert "NEED_EXPLICIT_REBALANCE_SCHEDULE_INDICES" in fee_only["reason_codes"]
    assert "NEED_EXPLICIT_COMBO_REBALANCE_FEE_BPS" not in fee_only["reason_codes"]
    assert fee_only["c3_comparison"] is None
    assert fee_only["boundaries"]["member_transaction_cost_scenario"] == (
        MEMBER_COST_SCENARIO
    )
    assert fee_only["boundaries"]["member_transaction_costs"] == (
        "TYPED_BASELINE_ZERO_ASSUMED_NO_MEMBER_FEES"
    )

    schedule_only = evaluate_batch_a_existing_member_baselines(
        frozen_member_pack=pack,
        capital_path_options={"rebalance_indices": (0,)},
    )
    assert schedule_only["status"] == "PARKED"
    assert "NEED_EXPLICIT_COMBO_REBALANCE_FEE_BPS" in schedule_only["reason_codes"]
    assert "NEED_EXPLICIT_REBALANCE_SCHEDULE_INDICES" not in schedule_only["reason_codes"]

    empty_request = evaluate_batch_a_existing_member_baselines(
        frozen_member_pack=pack,
        capital_path_options={},
    )
    assert empty_request["status"] == "PARKED"
    assert "NEED_EXPLICIT_COMBO_REBALANCE_FEE_BPS" in empty_request["reason_codes"]
    assert "NEED_EXPLICIT_REBALANCE_SCHEDULE_INDICES" in empty_request["reason_codes"]


def test_unknown_capital_path_option_parks_instead_of_ignoring_typo() -> None:
    result = evaluate_batch_a_existing_member_baselines(
        frozen_member_pack=_contract_pack(),
        capital_path_options={
            "apply_risk_scalnig": True,
            "rebalance_fee_bps": 0.0,
            "rebalance_indices": (),
        },
    )
    assert result["status"] == "PARKED"
    assert "CAPITAL_PATH_OPTIONS_UNKNOWN_KEYS" in result["reason_codes"]
    assert result["execution_authorized"] is False
    assert result["promotion_authorized"] is False
    assert result["no_order"] is True


def test_invalid_cash_member_id_parks_even_without_risk_scaling() -> None:
    result = evaluate_batch_a_existing_member_baselines(
        frozen_member_pack=_contract_pack(),
        capital_path_options={
            "apply_risk_scaling": False,
            "cash_member_id": "missing_cash",
            "rebalance_fee_bps": 0.0,
            "rebalance_indices": (),
        },
    )
    assert result["status"] == "PARKED"
    assert "CASH_MEMBER_ID_INVALID" in result["reason_codes"]
    assert result["execution_authorized"] is False
    assert result["promotion_authorized"] is False
    assert result["no_order"] is True


def test_missing_fee_schedule_parked_digest_binds_final_payload() -> None:
    import hashlib

    result = evaluate_batch_a_existing_member_baselines(
        frozen_member_pack=_contract_pack(),
        capital_path_options={"rebalance_fee_bps": 10.0},
    )
    digest_input = {key: value for key, value in result.items() if key != "evidence_digest"}
    canonical = json.dumps(
        digest_input,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    assert result["status"] == "PARKED"
    assert result["evidence_digest"] == hashlib.sha256(canonical).hexdigest()


def test_arbitrary_nonempty_cost_digest_does_not_claim_typed_baseline_zero() -> None:
    source = _contract_pack()
    members = [dict(member) for member in source["members"]]
    members[1]["cost_model_digest"] = "f" * 64
    result = evaluate_batch_a_existing_member_baselines(
        frozen_member_pack=_pack(tuple(members)),
        capital_path_options={"rebalance_fee_bps": 10.0},
    )
    assert result["status"] == "PARKED"
    assert "BATCH_A_MEMBER_COST_MODEL_DIGEST_MISMATCH" in result["reason_codes"]
    assert result.get("member_cost_scenario") is None
    assert "member_transaction_cost_scenario" not in result["boundaries"]
    assert "member_transaction_costs" not in result["boundaries"]
    assert result["execution_authorized"] is False
    assert result["promotion_authorized"] is False
    assert result["no_order"] is True


def test_risk_scaling_without_cash_member_id_parks() -> None:
    pack = _contract_pack()
    result = evaluate_batch_a_existing_member_baselines(
        frozen_member_pack=pack,
        capital_path_options={
            "apply_risk_scaling": True,
            "rebalance_fee_bps": 0.0,
            "rebalance_indices": (),
        },
    )
    assert result["status"] == "PARKED"
    assert "NEED_CASH_MEMBER_ID_FOR_RISK_SCALING_RESIDUAL" in result["reason_codes"]


def test_capital_path_synthetic_conserves_cash_fees_and_drift() -> None:
    """Hand-checkable synthetic path: drift, fee, cash residual, conservation."""

    dates = ("2026-01-02", "2026-01-05", "2026-01-06")
    pack = _contract_pack(
        cash=(0.0, 0.0, 0.0),
        soxl=(0.10, 0.0, 0.0),
        tqqq=(0.0, 0.0, 0.0),
        dates=dates,
    )
    shared = {
        "cash_member_id": "cash_sleeve",
        "rebalance_fee_bps": 100.0,
        "rebalance_indices": (0,),
        "member_costs_already_embedded": True,
    }
    unscaled = evaluate_batch_a_existing_member_baselines(
        frozen_member_pack=pack,
        capital_path_options={**shared, "apply_risk_scaling": False},
    )
    scaled = evaluate_batch_a_existing_member_baselines(
        frozen_member_pack=pack,
        capital_path_options={**shared, "apply_risk_scaling": True},
    )
    for result in (unscaled, scaled):
        assert result["status"] == "READY_RESEARCH_ONLY"
        assert result["batch_a_accepted"] is True
        assert result["execution_authorized"] is False
        assert result["promotion_authorized"] is False
        assert result["no_order"] is True
        assert result["member_cost_scenario"] == MEMBER_COST_SCENARIO
        assert result["capital_path_requested"] is True
        assert result["capital_path_options"]["rebalance_fee_bps"] == 100.0
        assert result["capital_path_options"]["rebalance_indices"] == (0,)
        assert result["boundaries"]["rebalance_fee_reconstruction"] == (
            "COMPUTED_EXPLICIT_BPS_AND_SCHEDULE"
        )
        assert result["boundaries"]["member_transaction_cost_scenario"] == (
            MEMBER_COST_SCENARIO
        )
        # Research input identity binds the actual capital-path parameters used.
        assert result["input_digest"] != evaluate_batch_a_existing_member_baselines(
            frozen_member_pack=pack
        )["input_digest"]

    assert unscaled["boundaries"]["risk_scaling_applied_to_returns"] == "NOT_APPLIED"
    assert scaled["boundaries"]["risk_scaling_applied_to_returns"] == (
        "APPLIED_IN_CAPITAL_PATH"
    )

    balanced_unscaled = next(
        item
        for item in unscaled["c3_comparison"]["baselines"]
        if item["baseline_id"] == "balanced_risk_with_cash_40_40_20"
    )
    path = balanced_unscaled["capital_path"]
    assert path is not None
    # Day0 growth under 20/40/40 with soxl=+10%: NAV 1.04 before fee.
    nav_pre = 1.04
    drifted = {
        "cash_sleeve": 0.20 / nav_pre,
        "soxl_core": 0.44 / nav_pre,
        "tqqq_core": 0.40 / nav_pre,
    }
    target = {"cash_sleeve": 0.20, "soxl_core": 0.40, "tqqq_core": 0.40}
    one_way = 0.5 * math.fsum(
        abs(target[key] - drifted[key]) for key in target
    )
    fee_fraction = (2.0 * one_way) * 0.01
    expected_nav = nav_pre * (1.0 - fee_fraction)
    assert path["one_way_turnovers"][0] == pytest.approx(one_way)
    assert path["fee_fractions"][0] == pytest.approx(fee_fraction)
    assert path["metrics"]["terminal_nav"] == pytest.approx(expected_nav)
    assert math.fsum(path["final_weights"].values()) == pytest.approx(1.0)
    assert path["metrics_accounting"]["member_costs_recharged"] is False
    assert path["metrics_accounting"]["rebalance_fees_applied"] is True
    assert path["metrics_accounting"]["risk_scaling_applied"] is False

    balanced_scaled = next(
        item
        for item in scaled["c3_comparison"]["baselines"]
        if item["baseline_id"] == "balanced_risk_with_cash_40_40_20"
    )
    scaled_path = balanced_scaled["capital_path"]
    assert scaled_path is not None
    # Default Batch A risk policy max_effective=1.5; 0.4*3+0.4*3=2.4 → scalar 0.625.
    assert scaled_path["risk_scalar"] == pytest.approx(0.625)
    assert scaled_path["cash_weight_after_scaling"] == pytest.approx(0.5)
    assert scaled_path["non_cash_nominal_exposure_after_scaling"] == pytest.approx(0.5)
    assert scaled_path["target_weights_used"] == {
        "cash_sleeve": pytest.approx(0.5),
        "soxl_core": pytest.approx(0.25),
        "tqqq_core": pytest.approx(0.25),
    }
    assert scaled_path["metrics_accounting"]["risk_scaling_applied"] is True
    assert math.fsum(scaled_path["final_weights"].values()) == pytest.approx(1.0)
    # Same capital-path schedule/fee/dates for unscaled vs scaled request.
    assert unscaled["capital_path_options"]["rebalance_indices"] == scaled[
        "capital_path_options"
    ]["rebalance_indices"]
    assert unscaled["capital_path_options"]["rebalance_fee_bps"] == scaled[
        "capital_path_options"
    ]["rebalance_fee_bps"]
    assert balanced_unscaled["metrics"]["start_date"] == dates[0]
    assert balanced_scaled["metrics"]["start_date"] == dates[0]
    assert path["metrics"]["start_date"] == dates[0]
    assert scaled_path["metrics"]["start_date"] == dates[0]


def test_cli_wires_capital_path_and_reports_gaps(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pack_path = tmp_path / "pack.json"
    pack_path.write_text(json.dumps(_contract_pack()), encoding="utf-8")

    ready = batch_a_cli.main(
        [
            "--frozen-member-pack",
            str(pack_path),
            "--rebalance-fee-bps",
            "0",
            "--rebalance-indices",
            "",
            "--cash-member-id",
            "cash_sleeve",
            "--apply-risk-scaling",
        ]
    )
    assert ready == 0
    ready_payload = json.loads(capsys.readouterr().out)
    assert ready_payload["status"] == "READY_RESEARCH_ONLY"
    assert ready_payload["capital_path_requested"] is True
    assert ready_payload["execution_authorized"] is False
    assert ready_payload["no_order"] is True
    assert ready_payload["boundaries"]["risk_scaling_applied_to_returns"] == (
        "APPLIED_IN_CAPITAL_PATH"
    )

    parked = batch_a_cli.main(
        [
            "--frozen-member-pack",
            str(pack_path),
            "--rebalance-fee-bps",
            "25",
        ]
    )
    assert parked == 2
    parked_payload = json.loads(capsys.readouterr().out)
    assert parked_payload["status"] == "PARKED"
    assert "NEED_EXPLICIT_REBALANCE_SCHEDULE_INDICES" in parked_payload["reason_codes"]
