"""Synthetic checks for the optimized-member identity envelope.

Hand-built values only. A passing test does not make any history valid.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest

from us_equity_strategies.research.optimized_member_identity import (
    EVIDENCE_SCOPE,
    OPTIMIZED_MEMBER_IDENTITY_SCHEMA,
    OPTIMIZED_MEMBER_IDENTITY_SCHEMA_V2,
    OptimizedMemberIdentityError,
    build_optimized_member_identity,
    build_optimized_member_identity_v2,
    calculate_optimized_member_identity_sha256,
    validate_optimized_member_identity,
)


def _args(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "strategy_profile": "soxl_soxx_trend_income",
        "ues_revision": "a" * 40,
        "qpk_revision": "b" * 40,
        "ues_workspace_patch_sha256": "c" * 64,
        "param_set_id": "synthetic-param-set",
        "actual_params": {"synthetic_switch": "example", "synthetic_window": 5},
        "config_sha256": "",
        "input_sha256": "2" * 64,
        "window_start": "2020-01-02",
        "window_end": "2020-01-03",
        "calendar_id": "synthetic-calendar",
        "periods_per_year": 252.0,
        "cost_source": "SYNTHETIC_EXAMPLE",
        "cost_inputs": {"commission_bps": 1.0, "slippage_bps": 2.0, "market_impact_bps": 3.0},
        "fill_price_field": "open",
        "adjustment_contract": "synthetic adjustment declaration",
        "cash_contract": "synthetic cash declaration",
        "corporate_action_contract": "synthetic corporate action declaration",
        "external_cashflow_contract": "synthetic external cashflow declaration",
        "share_quantity_contract": "synthetic share quantity declaration",
    }
    values.update(overrides)
    if "config_sha256" not in overrides:
        encoded = json.dumps(
            values["actual_params"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        values["config_sha256"] = hashlib.sha256(encoded).hexdigest()
    return values


def _identity(**overrides: object) -> dict[str, object]:
    return build_optimized_member_identity(**_args(**overrides))


def _identity_v2(**overrides: object) -> dict[str, object]:
    values = _args(**overrides)
    values.setdefault("qpk_workspace_patch_sha256", "d" * 64)
    return build_optimized_member_identity_v2(**values)


def test_synthetic_identity_is_deterministic_and_does_not_mutate_caller() -> None:
    arguments = _args()
    original = deepcopy(arguments)
    params = arguments["actual_params"]
    costs = arguments["cost_inputs"]

    first = build_optimized_member_identity(**arguments)
    arguments["actual_params"]["synthetic_switch"] = "mutated"
    arguments["cost_inputs"]["commission_bps"] = 50.0
    second = build_optimized_member_identity(**original)
    same_numbers = build_optimized_member_identity(
        **_args(
            periods_per_year=252,
            cost_inputs={"commission_bps": 1, "slippage_bps": 2, "market_impact_bps": 3},
        )
    )

    assert arguments["strategy_profile"] == original["strategy_profile"]
    assert params is arguments["actual_params"]
    assert first["actual_params"] == {"synthetic_switch": "example", "synthetic_window": 5}
    assert first["cost_inputs"]["commission_bps"] == 1.0
    assert costs["commission_bps"] == 50.0
    assert first == second == same_numbers
    assert first["schema_version"] == OPTIMIZED_MEMBER_IDENTITY_SCHEMA
    assert first["research_only"] is True
    assert first["execution_authorized"] is False
    assert first["promotion_authorized"] is False
    assert first["evidence_scope"] == EVIDENCE_SCOPE
    assert first["contract_proof"] == "DECLARATION_ONLY"
    assert first["economic_identity_sha256"] == calculate_optimized_member_identity_sha256(first)
    assert validate_optimized_member_identity(first) == first
    assert validate_optimized_member_identity(deepcopy(first)) == first


def test_distinct_synthetic_economics_change_the_digest() -> None:
    baseline = _identity()["economic_identity_sha256"]
    variants = (
        _identity(actual_params={"synthetic_switch": "other", "synthetic_window": 5}),
        _identity(cost_inputs={"commission_bps": 4.0, "slippage_bps": 2.0, "market_impact_bps": 3.0}),
        _identity(input_sha256="3" * 64),
        _identity(fill_price_field="close"),
        _identity(ues_workspace_patch_sha256=None),
        _identity(strategy_profile="tqqq_growth_income"),
    )
    digests = {item["economic_identity_sha256"] for item in variants}

    assert baseline not in digests
    assert len(digests) == len(variants)


def test_v2_binds_both_workspace_patches_and_preserves_v1_identity() -> None:
    v1 = _identity(ues_workspace_patch_sha256=None)
    v2 = _identity_v2()
    qpk_patch_changed = _identity_v2(qpk_workspace_patch_sha256="e" * 64)
    ues_patch_changed = _identity_v2(ues_workspace_patch_sha256="f" * 64)

    assert v1["schema_version"] == OPTIMIZED_MEMBER_IDENTITY_SCHEMA
    assert "qpk_workspace_patch_sha256" not in v1
    assert validate_optimized_member_identity(v1) == v1
    assert v2["schema_version"] == OPTIMIZED_MEMBER_IDENTITY_SCHEMA_V2
    assert v2["research_only"] is True
    assert v2["execution_authorized"] is False
    assert v2["promotion_authorized"] is False
    assert validate_optimized_member_identity(v2) == v2
    assert len({
        v2["economic_identity_sha256"],
        qpk_patch_changed["economic_identity_sha256"],
        ues_patch_changed["economic_identity_sha256"],
    }) == 3


@pytest.mark.parametrize(
    "overrides",
    [
        {"ues_workspace_patch_sha256": None},
        {"ues_workspace_patch_sha256": "a" * 63},
        {"qpk_workspace_patch_sha256": None},
        {"qpk_workspace_patch_sha256": "b" * 65},
    ],
)
def test_v2_requires_two_valid_workspace_patch_digests(overrides: dict[str, object]) -> None:
    with pytest.raises(OptimizedMemberIdentityError, match="invalid optimized member identity"):
        _identity_v2(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"param_set_id": "UNKNOWN"},
        {"param_set_id": "n/a"},
        {"calendar_id": "default"},
        {"cost_source": "none"},
        {"actual_params": {}},
        {"actual_params": {"synthetic_note": "UNKNOWN"}},
        {"adjustment_contract": "null"},
        {"cash_contract": " N/A "},
        {"ues_workspace_patch_sha256": ""},
        {"ues_revision": "unknown"},
        {"config_sha256": "1" * 63},
        {"input_sha256": "g" * 64},
        {"qpk_revision": "b" * 39},
        {"window_start": "2020-13-01"},
        {"window_end": "2019-12-31"},
        {"periods_per_year": 0},
        {"periods_per_year": 12},
        {"periods_per_year": True},
        {"cost_inputs": {"commission_bps": -1.0, "slippage_bps": 2.0, "market_impact_bps": 3.0}},
        {"cost_inputs": {"commission_bps": True, "slippage_bps": 2.0, "market_impact_bps": 3.0}},
        {"fill_price_field": "mid"},
        {"strategy_profile": "synthetic_buy_hold"},
        {"actual_params": {"bad": {1, 2}}, "config_sha256": "1" * 64},
        {"share_quantity_contract": ""},
        {"external_cashflow_contract": "na"},
        {"actual_params": {"schema_version": [float("nan")]}},
        {"fill_price_field": []},
    ],
)
def test_missing_placeholder_and_illegal_synthetic_fields_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(OptimizedMemberIdentityError, match="invalid optimized member identity"):
        _identity(**overrides)


def test_literal_none_is_accepted_only_on_the_two_retention_mode_fields() -> None:
    params = {
        "blend_gate_volatility_delever_retention_mode": "none",
        "dual_drive_volatility_delever_retention_mode": "none",
        "blend_gate_volatility_delever_retention_policy": "soxl_step_rebound_0.25_0.50",
    }

    identity = _identity(actual_params=params)

    assert identity["actual_params"] == params


@pytest.mark.parametrize(
    "field",
    [
        "blend_gate_volatility_delever_retention_mode",
        "dual_drive_volatility_delever_retention_mode",
    ],
)
@pytest.mark.parametrize(
    "value",
    ["NONE", "None", " none ", "unknown", "default", "null", "na", "n/a"],
)
def test_known_retention_fields_reject_nonliteral_placeholders(field: str, value: str) -> None:
    with pytest.raises(OptimizedMemberIdentityError, match="invalid optimized member identity"):
        _identity(actual_params={field: value})


@pytest.mark.parametrize(
    "params",
    [
        {"other_retention_mode": "none"},
        {"other_retention_mode": "unknown"},
        {"volatility_delever_retention_mode": "none"},
        {"volatility_delever_retention_mode": "unknown"},
        {"blend_gate_volatility_delever_retention_mode_extra": "none"},
        {"prefix_dual_drive_volatility_delever_retention_mode": "none"},
        {"Blend_gate_volatility_delever_retention_mode": "none"},
        {"market_signal_fallback_mode": "none"},
        {"market_signal_fallback_mode": "unknown"},
        {"blend_gate_volatility_delever_retention_policy": "none"},
        {"blend_gate_volatility_delever_retention_policy": "unknown"},
        {"synthetic_switch": "none"},
        {"synthetic_switch": "unknown"},
        {"blend_gate_volatility_delever_retention_mode": ["none"]},
        {"blend_gate_volatility_delever_retention_mode": {"mode": "none"}},
        {
            "dual_drive_volatility_delever_retention_mode": "none",
            "notes": "none",
        },
        {
            "blend_gate_volatility_delever_retention_mode": "none",
            "custom_retention_mode": "unknown",
        },
        {"wrapper": {"dual_drive_volatility_delever_retention_mode": "unknown"}},
    ],
)
def test_literal_none_on_any_other_param_field_is_rejected(params: dict[str, object]) -> None:
    with pytest.raises(OptimizedMemberIdentityError, match="invalid optimized member identity"):
        _identity(actual_params=params)


@pytest.mark.parametrize(
    "params",
    [
        {"wrapper": {"blend_gate_volatility_delever_retention_mode": "none"}},
        {"wrapper": {"dual_drive_volatility_delever_retention_mode": "none"}},
        {
            "outer": {
                "inner": {"blend_gate_volatility_delever_retention_mode": "none"},
            }
        },
        {"levels": [{"dual_drive_volatility_delever_retention_mode": "none"}]},
        {
            "blend_gate_volatility_delever_retention_mode": "none",
            "nested": {"dual_drive_volatility_delever_retention_mode": "none"},
        },
        {
            "dual_drive_volatility_delever_retention_mode": "none",
            "nested": {"blend_gate_volatility_delever_retention_mode": "none"},
        },
    ],
)
def test_nested_retention_mode_literal_none_is_rejected(params: dict[str, object]) -> None:
    with pytest.raises(OptimizedMemberIdentityError, match="invalid optimized member identity"):
        _identity(actual_params=params)


def test_omitted_caller_field_and_nonfinite_numbers_are_rejected() -> None:
    arguments = _args()
    del arguments["input_sha256"]
    with pytest.raises(TypeError):
        build_optimized_member_identity(**arguments)

    incomplete = _identity()
    del incomplete["input_sha256"]
    with pytest.raises(OptimizedMemberIdentityError, match="invalid optimized member identity"):
        validate_optimized_member_identity(incomplete)

    for overrides in (
        {"periods_per_year": float("nan")},
        {"periods_per_year": float("inf")},
        {"cost_inputs": {"commission_bps": float("nan"), "slippage_bps": 2.0, "market_impact_bps": 3.0}},
    ):
        with pytest.raises(OptimizedMemberIdentityError, match="invalid optimized member identity"):
            _identity(**overrides)

    with pytest.raises(OptimizedMemberIdentityError, match="invalid optimized member identity"):
        _identity(config_sha256="1" * 64)


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": "qsl.c3-batch-a-frozen-member-pack.v2", "synthetic": True},
        {"schema_version": "qsl.c3-batch-a-frozen-member-pack.v1"},
        {"schema_version": "qsl.research.soxl_soxx_typed_baseline_result.v1"},
        {"schema_version": "qsl.research.tqqq_typed_baseline_result.v1"},
        {"evidence_use": "fixture", "strategy_profile": "soxl_soxx_trend_income"},
        {"params": {"evidence_use": "fixture", "promotion_eligible": False}},
        {"strategy_profile": "soxl_soxx_trend_income_parity_baseline_v1"},
        {"strategy_profile": "tqqq_growth_income_research_baseline_v1"},
        {"profile": "soxl_soxx_trend_income_parity_baseline_v1"},
        {"cost_scenario": "TYPED_BASELINE_ZERO"},
        {"cost_source": "TYPED_BASELINE_ZERO"},
        {"signal_timing": "SMA200_INCLUSIVE_CLOSE_V1"},
        {"signal_timing": "SOXX_SMA200_INCLUSIVE_CLOSE_NEXT_SOXL_OPEN_V1"},
    ],
)
def test_batch_a_typed_sma_and_fixture_shapes_are_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(OptimizedMemberIdentityError, match="rejected foreign evidence identity"):
        validate_optimized_member_identity(payload)


def test_build_rejects_typed_profile_and_baseline_cost_source() -> None:
    with pytest.raises(OptimizedMemberIdentityError, match="rejected foreign evidence identity"):
        _identity(strategy_profile="soxl_soxx_trend_income_parity_baseline_v1")
    with pytest.raises(OptimizedMemberIdentityError, match="rejected foreign evidence identity"):
        _identity(cost_source="TYPED_BASELINE_ZERO")


def test_tampered_digest_execution_and_permission_are_rejected() -> None:
    tampered = _identity()
    digest = str(tampered["economic_identity_sha256"])
    tampered["economic_identity_sha256"] = digest[:-1] + ("0" if digest[-1] != "0" else "1")
    with pytest.raises(OptimizedMemberIdentityError, match="digest mismatch"):
        validate_optimized_member_identity(tampered)

    retimed = _identity()
    retimed["execution"] = {
        **retimed["execution"],
        "execution_timing_contract": "same_day",
        "signal_effective_after_trading_days": 0,
        "nav_mark_field": "open",
    }
    retimed["economic_identity_sha256"] = calculate_optimized_member_identity_sha256(retimed)
    with pytest.raises(OptimizedMemberIdentityError, match="invalid execution contract"):
        validate_optimized_member_identity(retimed)

    authorized = _identity()
    authorized["execution_authorized"] = True
    authorized["promotion_authorized"] = True
    authorized["economic_identity_sha256"] = calculate_optimized_member_identity_sha256(authorized)
    with pytest.raises(OptimizedMemberIdentityError, match="cannot authorize execution"):
        validate_optimized_member_identity(authorized)

    researched = _identity()
    researched["research_only"] = False
    researched["economic_identity_sha256"] = calculate_optimized_member_identity_sha256(researched)
    with pytest.raises(OptimizedMemberIdentityError, match="must remain research only"):
        validate_optimized_member_identity(researched)

    promoted_scope = _identity()
    promoted_scope["evidence_scope"] = "optimized_history"
    promoted_scope["economic_identity_sha256"] = calculate_optimized_member_identity_sha256(promoted_scope)
    with pytest.raises(OptimizedMemberIdentityError, match="invalid evidence scope"):
        validate_optimized_member_identity(promoted_scope)

    proven = _identity()
    proven["contract_proof"] = "VERIFIED"
    proven["economic_identity_sha256"] = calculate_optimized_member_identity_sha256(proven)
    with pytest.raises(OptimizedMemberIdentityError, match="invalid contract proof"):
        validate_optimized_member_identity(proven)
