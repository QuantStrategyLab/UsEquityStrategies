from __future__ import annotations

import json
import math
import importlib
import inspect
import os
import socket
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


@pytest.fixture(autouse=True)
def no_consumer_network(monkeypatch):
    attempts = []

    def denied(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("LOCAL_CONSUMER_FIXTURE_NETWORK_FORBIDDEN")

    class DeniedSocket(socket.socket):
        def __new__(cls, *args, **kwargs):
            return denied(*args, **kwargs)

    # Preserve the socket type for stdlib SSL class declarations while refusing
    # any actual allocation; replacing it with a function breaks lazy imports.
    monkeypatch.setattr(socket, "socket", DeniedSocket)
    for name in ("create_connection", "getaddrinfo", "gethostbyname"):
        monkeypatch.setattr(socket, name, denied)
    yield
    assert not attempts, "Report consumer attempted network access"


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


def test_positive_rebalance_fee_requires_explicit_fee_bearing_members() -> None:
    result = evaluate_batch_a_existing_member_baselines(
        frozen_member_pack=_contract_pack(),
        capital_path_options={
            "rebalance_fee_bps": 10.0,
            "rebalance_indices": (0,),
        },
    )
    assert result["status"] == "PARKED"
    assert "FEE_BEARING_MEMBER_IDS_REQUIRED" in result["reason_codes"]
    assert result["execution_authorized"] is False
    assert result["no_order"] is True


def test_invalid_fee_bearing_member_parks() -> None:
    result = evaluate_batch_a_existing_member_baselines(
        frozen_member_pack=_contract_pack(),
        capital_path_options={
            "rebalance_fee_bps": 10.0,
            "rebalance_indices": (0,),
            "fee_bearing_member_ids": ("soxl_core", "missing_member"),
        },
    )
    assert result["status"] == "PARKED"
    assert "FEE_BEARING_MEMBER_IDS_INVALID" in result["reason_codes"]
    assert result["execution_authorized"] is False
    assert result["no_order"] is True


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
        # Synthetic contract: charge the two security sleeves; cash is the
        # funding leg and is not itself a security order.
        "fee_bearing_member_ids": ("soxl_core", "tqqq_core"),
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
        assert result["capital_path_options"]["fee_bearing_member_ids"] == (
            "soxl_core",
            "tqqq_core",
        )
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
    # Only the two security sleeves are fee-bearing; cash is their funding leg.
    fee_notional = math.fsum(
        abs(target[key] - drifted[key]) for key in ("soxl_core", "tqqq_core")
    )
    fee_fraction = fee_notional * 0.01
    expected_nav = nav_pre * (1.0 - fee_fraction)
    assert path["one_way_turnovers"][0] == pytest.approx(one_way)
    assert path["pre_trade_fee_notionals"][0] == pytest.approx(fee_notional)
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


@pytest.fixture
def historical_sources(tmp_path: Path):
    """Generated protocol data only; never financial or calendar evidence."""
    import runpy

    from us_equity_strategies.research.batch_a_dataset import load_price_snapshot_v2
    from us_equity_strategies.research.batch_a_member_pack import materialize_batch_a_member_pack

    fixture = runpy.run_path(str(Path(__file__).with_name("test_batch_a_v2_contracts.py")))
    dates = fixture["_dates"](240)
    staging = tmp_path / "generated-staging"
    staging.mkdir()
    snapshots = []
    for name, symbols in (("soxl_soxx", ("SOXL", "SOXX")), ("tqqq_qqq", ("TQQQ", "QQQ"))):
        root = fixture["_write_dataset"](
            staging, dataset_id="batch-fixture/" + name, symbols=symbols, dates=dates)
        root.rename(staging / name)
        snapshots.append(load_price_snapshot_v2(staging / name))
    pack = materialize_batch_a_member_pack(soxl_snapshot=snapshots[0], tqqq_snapshot=snapshots[1])
    pack_path = tmp_path / "generated-pack.json"
    pack_path.write_text(json.dumps(pack), encoding="utf-8")
    return staging, snapshots, pack, pack_path, dates


def _historical_result(sources):
    from us_equity_strategies.research.c3_batch_a_existing_member_baseline import (
        report_historical_batch_a_baselines,
    )

    _, snapshots, pack, _, _ = sources
    return report_historical_batch_a_baselines(
        frozen_member_pack=pack, soxl_snapshot=snapshots[0], tqqq_snapshot=snapshots[1],
        annual_risk_free_rate=0.0, annual_minimum_acceptable_return=0.0)


def _historical_cli_args(sources):
    staging, _, _, pack_path, _ = sources
    return ["--frozen-member-pack", str(pack_path), "--historical-account-report",
            "--snapshot-input-root", str(staging), "--annual-risk-free-rate", "0",
            "--annual-minimum-acceptable-return", "0"]


def test_historical_actual_consumer_preserves_legacy_and_declares_assumptions(historical_sources):
    result = _historical_result(historical_sources)
    report = result.pop("historical_account_report")
    legacy = evaluate_batch_a_existing_member_baselines(frozen_member_pack=historical_sources[2])
    result.pop("evidence_digest")
    legacy.pop("evidence_digest")
    assert result == legacy
    assert report["synthetic"] is False
    assert report["window_role"] == "SEEN_DEVELOPMENT"
    assert report["data_qualified"] is report["oos_qualified"] is False
    assert report["execution_authorized"] is report["promotion_authorized"] is False
    assert report["input_binding"]["independent_calendar_authority_verified"] is False
    assert report["input_binding"]["historical_available_at_verified"] is False
    assumptions = report["assumptions"]
    assert assumptions["combo_fee_bps"] == [0.0, 5.0, 10.0, 15.0]
    assert assumptions["fees_are_actual_quotes"] is False
    assert assumptions["cash_return_policy"] == CASH_RETURN_POLICY
    assert assumptions["member_costs"] == "TYPED_BASELINE_ZERO"
    assert len(report["baselines"]) == 4
    assert len(report["standalone"]) == 3
    for baseline in report["baselines"]:
        assert len(baseline["books"]) == 7
        assert baseline["books"]["raw_drift"]["total_combo_fees"] == 0
        fees = [baseline["books"][f"risk_scaled_month_end_fee_{bps}bps"]["total_combo_fees"]
                for bps in (0, 5, 10, 15)]
        assert fees[0] == 0 < fees[1] < fees[2] < fees[3]
        metrics = baseline["books"]["raw_drift"]["metric_report"]
        assert metrics["evaluation_contract"]["periods_per_year"] == 252
        assert metrics["evaluation_contract"]["volatility_ddof"] == 1
        source_dates = historical_sources[4]
        first_index = source_dates.index(metrics["start_session"])
        assert metrics["initial_session"] == source_dates[first_index - 1]
    json.dumps(report, allow_nan=False)


def test_historical_original_cli_emits_only_aggregate_reports(historical_sources, capsys):
    assert batch_a_cli.main(_historical_cli_args(historical_sources)) == 0
    output = capsys.readouterr().out
    report = json.loads(output)["historical_account_report"]
    assert report["status"] == "COMPUTED"
    assert report["input_binding"]["pack_digest"] == historical_sources[2]["pack_digest"]
    assert "daily_returns" not in output
    assert '"positions"' not in output
    assert str(historical_sources[0]) not in output


def test_historical_cli_refuses_changed_source_without_partial_report(historical_sources, capsys):
    source = historical_sources[0] / "soxl_soxx" / "prices.csv"
    source.write_bytes(source.read_bytes() + b"tampered\n")
    assert batch_a_cli.main(_historical_cli_args(historical_sources)) == 2
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["reason_codes"] == ["HISTORICAL_ACCOUNT_REPORT_REFUSED"]
    assert report["batch_a_accepted"] is report["data_qualified"] is report["oos_qualified"] is False
    assert "historical_account_report" not in report
    assert str(source) not in output


def test_historical_cli_requires_original_object_identity(historical_sources, capsys):
    identity = historical_sources[0] / "soxl_soxx" / "object_identity.json"
    identity.unlink()
    assert batch_a_cli.main(_historical_cli_args(historical_sources)) == 2
    assert json.loads(capsys.readouterr().out)["reason_codes"] == ["HISTORICAL_ACCOUNT_REPORT_REFUSED"]


def test_historical_cli_refuses_failed_fee_path_atomically(historical_sources, capsys, monkeypatch):
    from us_equity_strategies.research import c3_self_financing_combo_ledger as ledger_module

    original = ledger_module.build_historical_self_financing_ledger

    def fail_fee_path(**kwargs):
        if kwargs["combo_fee_bps"] > 0:
            raise ArithmeticError("private-source-must-not-be-echoed")
        return original(**kwargs)

    monkeypatch.setattr(ledger_module, "build_historical_self_financing_ledger", fail_fee_path)
    assert batch_a_cli.main(_historical_cli_args(historical_sources)) == 2
    output = capsys.readouterr().out
    assert "private-source" not in output
    assert "historical_account_report" not in json.loads(output)


def test_historical_refuses_unbound_contract_pack_and_nonfinite_rates(historical_sources):
    from us_equity_strategies.research.c3_batch_a_existing_member_baseline import (
        report_historical_batch_a_baselines,
    )

    _, snapshots, pack, _, _ = historical_sources
    kwargs = {"soxl_snapshot": snapshots[0], "tqqq_snapshot": snapshots[1],
              "annual_risk_free_rate": 0.0, "annual_minimum_acceptable_return": 0.0}
    with pytest.raises(ValueError, match="HISTORICAL_PACK_SOURCE_MISMATCH"):
        report_historical_batch_a_baselines(frozen_member_pack=_contract_pack(), **kwargs)
    for rate in (math.nan, math.inf, True):
        with pytest.raises(ValueError, match="RESEARCH_RATE_INVALID"):
            report_historical_batch_a_baselines(
                frozen_member_pack=pack, **{**kwargs, "annual_risk_free_rate": rate})


def test_historical_cli_requires_opt_in_and_complete_inputs(historical_sources, capsys):
    assert batch_a_cli.main(["--snapshot-input-root", str(historical_sources[0])]) == 2
    assert json.loads(capsys.readouterr().out)["reason_codes"] == ["HISTORICAL_REPORT_OPT_IN_REQUIRED"]
    assert batch_a_cli.main(["--historical-account-report"]) == 2
    assert json.loads(capsys.readouterr().out)["reason_codes"] == ["HISTORICAL_ACCOUNT_REPORT_REFUSED"]


def _unified_capability():
    try:
        module = importlib.import_module("quant_platform_kit.research_stats.unified_report")
    except ModuleNotFoundError as exc:
        if exc.name not in {"quant_platform_kit.research_stats", "quant_platform_kit.research_stats.unified_report"}:
            raise
        builder = None
    else:
        builder = getattr(module, "build_unified_net_report", None)
        if not callable(builder) or "include_outcome_diagnostics" not in inspect.signature(builder).parameters:
            builder = None
    if os.environ.get("UES_REQUIRE_UNIFIED_NET_REPORT") == "1":
        assert builder is not None, "Required latest QPK unified/outcome capability unavailable"
    return builder


def _unified_cli(sources, capsys):
    code = batch_a_cli.main([*_historical_cli_args(sources), "--include-unified-net-report"])
    text = capsys.readouterr().out
    return code, text, json.loads(text)


def test_unified_flag_requires_historical_mode_before_input_read(tmp_path, capsys, monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("wrong opt-in mode read private input")
    monkeypatch.setattr(Path, "read_text", denied)
    assert batch_a_cli.main(["--include-unified-net-report", "--frozen-member-pack",
                            str(tmp_path / "private-missing.json")]) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["reason_codes"] == ["HISTORICAL_REPORT_OPT_IN_REQUIRED"]
    assert str(tmp_path) not in output


def test_unified_capability_refused_before_private_input_read(tmp_path, capsys, monkeypatch):
    from us_equity_strategies.research import tqqq_core_trial_journal as journal
    original = importlib.import_module

    def missing(name, *args, **kwargs):
        if name == "quant_platform_kit.research_stats.unified_report":
            raise ModuleNotFoundError("private-marker", name=name)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(journal.importlib, "import_module", missing)
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: pytest.fail("missing capability read input"))
    assert batch_a_cli.main(["--historical-account-report", "--include-unified-net-report",
                            "--frozen-member-pack", str(tmp_path / "private-missing.json")]) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["reason_codes"] == ["UNIFIED_NET_REPORT_CAPABILITY_UNAVAILABLE"]
    assert "private-marker" not in output and str(tmp_path) not in output


def test_unified_cli_uses_actual_selected_net_returns_and_preserves_legacy(historical_sources, capsys, monkeypatch):
    from us_equity_strategies.research import tqqq_core_trial_journal as journal
    builder = _unified_capability()
    baseline = json.loads(json.dumps(_historical_result(historical_sources)))
    before = {p: p.read_bytes() for p in historical_sources[0].rglob("*") if p.is_file()}
    if builder is None:
        code, _, refused = _unified_cli(historical_sources, capsys)
        assert code == 2 and refused["reason_codes"] == ["UNIFIED_NET_REPORT_CAPABILITY_UNAVAILABLE"]
        assert "historical_account_report" not in refused
        return
    captured = []

    def actual(net, **kwargs):
        captured.append((tuple(net), kwargs))
        return builder(net, **kwargs)

    monkeypatch.setattr(journal, "_unified_net_report_builder", lambda: actual)
    code, output, current = _unified_cli(historical_sources, capsys)
    assert code == 0
    report = current["historical_account_report"]
    metric_reports = [book["metric_report"] for row in report["baselines"] for book in row["books"].values()]
    metric_reports += [book["metric_report"] for book in report["standalone"].values()]
    # CLI JSON sorts object keys, while source construction order is preserved in captured input.
    sidecars = sorted((item["unified_net_report"] for item in metric_reports),
                      key=lambda item: (item["metrics"][-1]["value"], item["observations"]))
    assert len(captured) == len(sidecars) == 31
    for net, kwargs in captured:
        assert kwargs == {"include_outcome_diagnostics": True}
        assert len(net) == metric_reports[0]["observation_count"]
    expected_means = sorted(math.fsum(net) / len(net) for net, _ in captured)
    assert [item["metrics"][-1]["value"] for item in sidecars] == pytest.approx(expected_means)
    for item in metric_reports:
        sidecar = item.pop("unified_net_report")
        basis = item.pop("unified_net_report_basis")
        assert sidecar["status"] == "DEVELOPMENT_ONLY"
        assert sidecar["volatility_ddof"] == 1
        assert sidecar["live_ready"] is False and sidecar["no_order"] is True
        assert sidecar["observations"] == item["observation_count"]
        assert len(sidecar["metrics"]) == 12
        values = {m["name"]: m for m in sidecar["metrics"]}
        assert values["deflated_sharpe_ratio"]["reason_code"] == "missing_trial_evidence"
        assert values["cost_log_drag_per_period"]["reason_code"] == "missing_gross_returns"
        assert values["deflated_sharpe_ratio"]["value"] is values["cost_log_drag_per_period"]["value"] is None
        assert basis["return_basis"] == "RAW_NET"
        assert basis["observation_unit"] == "STRATEGY_ACCOUNT_SESSION_SIMPLE_NET_RETURN"
        assert basis["metric_role"] == "REPORT_ONLY"
        assert basis["asset_benchmark"] is None and basis["benchmark_alignment_count"] is None
        assert basis["costs_deducted_again"] is False
        assert basis["legacy_rf_adjusted_annualized_metrics_replaced"] is False
    current.pop("evidence_digest")
    baseline.pop("evidence_digest")
    assert current == baseline
    assert before == {p: p.read_bytes() for p in historical_sources[0].rglob("*") if p.is_file()}
    for forbidden in (str(historical_sources[0]), '"positions"', '"daily_returns"', '"net_returns"'):
        assert forbidden not in output


def test_unified_missing_outcome_flag_refuses_old_builder_before_read(tmp_path, capsys, monkeypatch):
    from types import SimpleNamespace
    from us_equity_strategies.research import tqqq_core_trial_journal as journal
    original = importlib.import_module

    def old_builder(net_returns):
        pytest.fail("older eight-metric builder was substituted")

    def old_module(name, *args, **kwargs):
        if name == "quant_platform_kit.research_stats.unified_report":
            return SimpleNamespace(build_unified_net_report=old_builder)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(journal.importlib, "import_module", old_module)
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: pytest.fail("old capability read input"))
    assert batch_a_cli.main(["--historical-account-report", "--include-unified-net-report",
                            "--frozen-member-pack", str(tmp_path / "private.json")]) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["reason_codes"] == ["UNIFIED_NET_REPORT_CAPABILITY_UNAVAILABLE"]
    assert str(tmp_path) not in output


def test_unified_default_does_not_require_or_import_new_capability(historical_sources, capsys, monkeypatch):
    from us_equity_strategies.research import tqqq_core_trial_journal as journal
    expected = json.loads(json.dumps(_historical_result(historical_sources)))
    monkeypatch.setattr(journal, "_unified_net_report_builder",
                        lambda: pytest.fail("default report touched new optional capability"))
    assert batch_a_cli.main(_historical_cli_args(historical_sources)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == expected
    assert "unified_net_report" not in json.dumps(result)


def _unified_hand_ledger(navs):
    from datetime import date, timedelta
    from quant_platform_kit.strategy_lifecycle.contracts import (
        ResearchDailyLedger, ResearchLedgerDay, ResearchPositionMark,
    )
    initial = date(2024, 1, 1)
    return ResearchDailyLedger(
        trial_id="existing-hand-fixture", domain="us_equity", strategy_profile="report-only-hand-fixture",
        run_id="fixture-run", param_version=1, input_id="sha256:" + "a" * 64,
        calendar_id="XNYS", periods_per_year=252, cost_source="HAND_FIXTURE_NET_MARKS",
        cost_inputs={"commission_bps": 0.0, "slippage_bps": 0.0},
        initial_session_date=initial, initial_nav=navs[0], initial_cash=40.0,
        initial_positions=(ResearchPositionMark("TQQQ", 1.0, navs[0] - 40.0),),
        days=tuple(ResearchLedgerDay(
            session_date=initial + timedelta(days=i + 1), cash=40.0,
            positions=(ResearchPositionMark("TQQQ", 1.0, end - 40.0),),
            trade_net_cashflow=0.0, fees=0.0, nav=end, daily_return=end / start - 1.0,
        ) for i, (start, end) in enumerate(zip(navs, navs[1:]))), synthetic=False)


@pytest.mark.parametrize("navs,expected", [
    ((100.0, 90.0, 90.0, 108.0), (0.5, 2.0, 2.0, 1.0 / 30)),
    ((100.0, 110.0, 121.0), (1.0, "no_loss_observations", "no_loss_observations", 0.1)),
    ((100.0, 90.0, 81.0), (0.0, "no_win_observations", 0.0, -0.1)),
    ((100.0, 100.0, 100.0), ("no_nonzero_observations", "no_loss_observations", "no_loss_observations", 0.0)),
])
def test_unified_hand_math_and_known_zero_distinct_from_unknown(navs, expected):
    from us_equity_strategies.research.tqqq_core_trial_journal import compute_historical_research_metrics
    ledger = _unified_hand_ledger(navs)
    kwargs = {"input_binding": {"schema": "qsl.batch-a-historical-input-binding.v1", "pack_digest": "a" * 64},
              "annual_risk_free_rate": 0.252, "annual_minimum_acceptable_return": 0.504,
              "include_unified_net_report": True}
    if _unified_capability() is None:
        with pytest.raises(ValueError, match="^UNIFIED_NET_REPORT_CAPABILITY_UNAVAILABLE$"):
            compute_historical_research_metrics(ledger, **kwargs)
        return
    report = compute_historical_research_metrics(ledger, **kwargs)
    sidecar = report["unified_net_report"]
    assert sidecar["observations"] == len(navs) - 1
    values = {item["name"]: item for item in sidecar["metrics"]}
    names = ("net_win_rate_ex_zero_per_period", "net_payoff_ratio_per_period",
             "net_profit_factor_per_period", "mean_net_return_per_period")
    for name, value in zip(names, expected, strict=True):
        metric = values[name]
        assert metric["details"]["aggregation_level"] == "return_observations_not_trades"
        assert metric["details"]["descriptive_only"] is True
        if isinstance(value, str):
            assert metric["value"] is None and metric["status"] == "UNCOMPUTABLE"
            assert metric["reason_code"] == value
        else:
            assert metric["status"] == "COMPUTED" and metric["value"] == pytest.approx(value)
    assert report["evaluation_contract"]["rf"]["annual_simple_decimal"] == 0.252
    assert report["evaluation_contract"]["mar"]["annual_simple_decimal"] == 0.504
    assert report["metrics"]["dsr"] is None and report["metric_status"]["dsr"] == "NOT_COMPUTED"
    assert values["deflated_sharpe_ratio"]["reason_code"] == "missing_trial_evidence"
    assert values["cost_log_drag_per_period"]["reason_code"] == "missing_gross_returns"
    json.dumps(report, allow_nan=False)


def test_unified_exact_window_retains_return_count_and_does_not_add_initial_mark():
    from us_equity_strategies.research.tqqq_core_trial_journal import compute_historical_research_metrics
    ledger = _unified_hand_ledger((100.0, 90.0, 90.0, 108.0))
    kwargs = {"input_binding": {"schema": "qsl.batch-a-historical-input-binding.v1", "pack_digest": "a" * 64},
              "annual_risk_free_rate": 0.0, "annual_minimum_acceptable_return": 0.0,
              "start_session": ledger.days[1].session_date, "end_session": ledger.days[2].session_date,
              "include_unified_net_report": True}
    if _unified_capability() is None:
        with pytest.raises(ValueError, match="^UNIFIED_NET_REPORT_CAPABILITY_UNAVAILABLE$"):
            compute_historical_research_metrics(ledger, **kwargs)
        return
    report = compute_historical_research_metrics(ledger, **kwargs)
    values = {item["name"]: item for item in report["unified_net_report"]["metrics"]}
    assert report["observation_count"] == report["unified_net_report"]["observations"] == 2
    assert values["mean_net_return_per_period"]["value"] == pytest.approx(0.1)
    assert values["net_win_rate_ex_zero_per_period"]["value"] == 1.0
    assert values["net_win_rate_ex_zero_per_period"]["details"]["zero_observations"] == 1
    assert values["max_drawdown"]["value"] == 0.0
    assert report["unified_net_report_basis"]["start_session"] == kwargs["start_session"].isoformat()


@pytest.mark.parametrize("value", [None, 1, "true"])
def test_unified_api_requires_explicit_boolean_before_source_replay(value):
    from us_equity_strategies.research.c3_batch_a_existing_member_baseline import report_historical_batch_a_baselines
    with pytest.raises(ValueError, match="^UNIFIED_NET_REPORT_OPT_IN_INVALID$"):
        report_historical_batch_a_baselines(frozen_member_pack={}, soxl_snapshot=None, tqqq_snapshot=None,
                                          annual_risk_free_rate=0.0, annual_minimum_acceptable_return=0.0,
                                          include_unified_net_report=value)
