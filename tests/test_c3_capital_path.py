from __future__ import annotations

import math

import pytest

from us_equity_strategies.portfolio_risk_budget import (
    PortfolioAssetRiskSpec,
    PortfolioRiskBudgetPolicy,
)
from us_equity_strategies.research.c3_capital_path import (
    diagnose_capital_path_inputs,
    one_way_turnover,
    scale_member_budgets_to_cash,
    simulate_fixed_budget_capital_path,
)
from us_equity_strategies.research.c3_fixed_budget_baseline_comparison import (
    compare_fixed_member_budget_baselines,
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


def _member(
    member_id: str, returns: tuple[float, ...], **overrides: object
) -> dict[str, object]:
    payload = {
        "member_id": member_id,
        "evidence_digest": "a" * 64,
        "input_digest": "b" * 64,
        "dates": ("2026-01-02", "2026-01-05", "2026-01-06"),
        "returns": returns,
        **_comp(),
    }
    payload.update(overrides)
    return payload


def test_capital_conserved_and_weights_drift_without_rebalance() -> None:
    # Start 50/50. Day0: A +10%, B 0% → NAV 1.05, weights 11/21 and 10/21.
    # Day1: A 0%, B +10% → NAV 1.10 without free rebalance.
    path = simulate_fixed_budget_capital_path(
        member_ids=("left", "right"),
        member_returns={"left": (0.10, 0.0), "right": (0.0, 0.10)},
        target_weights={"left": 0.5, "right": 0.5},
        rebalance_fee_bps=None,
        rebalance_indices=None,
    )
    assert path["daily_returns"] == pytest.approx((0.05, 1.10 / 1.05 - 1.0))
    assert path["terminal_nav"] == pytest.approx(1.10)
    assert path["weight_path"][0]["left"] == pytest.approx(0.55 / 1.05)
    assert path["weight_path"][0]["right"] == pytest.approx(0.50 / 1.05)
    assert path["final_weights"]["left"] == pytest.approx(0.5)
    assert path["final_weights"]["right"] == pytest.approx(0.5)
    assert math.fsum(path["final_weights"].values()) == pytest.approx(1.0)
    assert path["rebalance_fees_applied"] is False
    assert path["member_costs_recharged"] is False


def test_rebalance_fee_is_incremental_and_does_not_recharge_member_costs() -> None:
    # After day0 A +100% / B flat from 50/50: NAV=1.5, weights 2/3 and 1/3.
    # One-way turnover back to 50/50 = 1/6. fee_bps=100 → fee = 2*(1/6)*0.01 = 1/300.
    path = simulate_fixed_budget_capital_path(
        member_ids=("left", "right"),
        member_returns={"left": (1.0,), "right": (0.0,)},
        target_weights={"left": 0.5, "right": 0.5},
        rebalance_fee_bps=100.0,
        rebalance_indices=(0,),
        member_costs_already_embedded=True,
    )
    expected_nav = 1.5 * (1.0 - (1.0 / 300.0))
    assert path["one_way_turnovers"][0] == pytest.approx(1.0 / 6.0)
    assert path["fee_fractions"][0] == pytest.approx(1.0 / 300.0)
    assert path["terminal_nav"] == pytest.approx(expected_nav)
    assert path["daily_returns"][0] == pytest.approx(expected_nav - 1.0)
    assert path["final_weights"] == {"left": 0.5, "right": 0.5}
    assert path["member_costs_recharged"] is False
    assert path["rebalance_fees_applied"] is True
    assert path["rebalance_fee_reconstruction"] == "COMPUTED_EXPLICIT_BPS_AND_SCHEDULE"


def test_risk_scaling_redirects_residual_to_cash_and_is_explainable() -> None:
    scaled = scale_member_budgets_to_cash(
        budgets={"cash_sleeve": 0.20, "soxl_core": 0.40, "tqqq_core": 0.40},
        risk_scalar=0.5,
        cash_member_id="cash_sleeve",
    )
    assert scaled == {
        "cash_sleeve": pytest.approx(0.60),
        "soxl_core": pytest.approx(0.20),
        "tqqq_core": pytest.approx(0.20),
    }
    assert math.fsum(scaled.values()) == pytest.approx(1.0)


def test_c3_default_path_keeps_honesty_labels_without_flipping_flags() -> None:
    result = compare_fixed_member_budget_baselines(
        members=(
            _member("soxl_core", (0.01, -0.02, 0.03)),
            _member("tqqq_core", (0.02, -0.01, 0.01)),
        ),
        baselines=(
            {
                "baseline_id": "balanced_50_50",
                "member_budget_weights": {"soxl_core": 0.50, "tqqq_core": 0.50},
            },
            {
                "baseline_id": "soxl_heavy_60_40",
                "member_budget_weights": {"soxl_core": 0.60, "tqqq_core": 0.40},
            },
        ),
    )
    assert result["status"] == "READY_RESEARCH_ONLY"
    assert result["execution_authorized"] is False
    assert result["promotion_authorized"] is False
    assert result["no_order"] is True
    assert result["capital_path_requested"] is False
    assert result["boundaries"]["risk_scaling_applied_to_returns"] == "NOT_APPLIED"
    assert result["boundaries"]["rebalance_fee_reconstruction"] == "NOT_COMPUTED"
    assert result["baselines"][0]["capital_path"] is None
    accounting = result["baselines"][0]["metrics_accounting"]
    assert accounting["risk_scaling_applied"] is False
    assert accounting["rebalance_fees_applied"] is False


def test_c3_capital_path_applies_scaling_and_fees_with_explicit_inputs() -> None:
    specs = {
        "USD_CASH": PortfolioAssetRiskSpec("USD_CASH", 1.0, "CASH", is_cash=True),
        "SOXL": PortfolioAssetRiskSpec("SOXL", 3.0, "SEMICONDUCTOR"),
        "TQQQ": PortfolioAssetRiskSpec("TQQQ", 3.0, "NASDAQ100"),
    }
    policy = PortfolioRiskBudgetPolicy(
        cash_symbol="USD_CASH",
        max_effective_risk_exposure=1.2,
        max_symbol_weights={"SOXL": 1.0, "TQQQ": 1.0},
        max_underlying_effective_exposure={"SEMICONDUCTOR": 3.0, "NASDAQ100": 3.0},
    )
    # Effective exposure 0.4*3 + 0.4*3 = 2.4 → scalar 1.2/2.4 = 0.5.
    result = compare_fixed_member_budget_baselines(
        members=(
            _member("cash_sleeve", (0.0, 0.0, 0.0)),
            _member("soxl_core", (0.10, 0.0, 0.0)),
            _member("tqqq_core", (0.0, 0.0, 0.0)),
        ),
        baselines=(
            {
                "baseline_id": "balanced_with_cash",
                "member_budget_weights": {
                    "cash_sleeve": 0.20,
                    "soxl_core": 0.40,
                    "tqqq_core": 0.40,
                },
                "representative_target_weights": {
                    "USD_CASH": 0.20,
                    "SOXL": 0.40,
                    "TQQQ": 0.40,
                },
            },
            {
                "baseline_id": "cash_heavy",
                "member_budget_weights": {
                    "cash_sleeve": 0.60,
                    "soxl_core": 0.20,
                    "tqqq_core": 0.20,
                },
                "representative_target_weights": {
                    "USD_CASH": 0.60,
                    "SOXL": 0.20,
                    "TQQQ": 0.20,
                },
            },
        ),
        asset_risk_specs=specs,
        risk_policy=policy,
        capital_path_options={
            "apply_risk_scaling": True,
            "cash_member_id": "cash_sleeve",
            "rebalance_fee_bps": 100.0,
            "rebalance_indices": (0,),
            "member_costs_already_embedded": True,
        },
    )
    assert result["status"] == "READY_RESEARCH_ONLY"
    assert result["execution_authorized"] is False
    assert result["promotion_authorized"] is False
    assert result["no_order"] is True
    assert result["boundaries"]["risk_scaling_applied_to_returns"] == (
        "APPLIED_IN_CAPITAL_PATH"
    )
    assert result["boundaries"]["rebalance_fee_reconstruction"] == (
        "COMPUTED_EXPLICIT_BPS_AND_SCHEDULE"
    )
    first = result["baselines"][0]
    # Raw metrics remain unscaled constant-weight combination.
    assert first["metrics_accounting"]["risk_scaling_applied"] is False
    path = first["capital_path"]
    assert path is not None
    assert path["risk_scalar"] == pytest.approx(0.5)
    assert path["cash_weight_after_scaling"] == pytest.approx(0.60)
    assert path["non_cash_nominal_exposure_after_scaling"] == pytest.approx(0.40)
    assert path["target_weights_used"] == {
        "cash_sleeve": pytest.approx(0.60),
        "soxl_core": pytest.approx(0.20),
        "tqqq_core": pytest.approx(0.20),
    }
    assert path["metrics_accounting"]["risk_scaling_applied"] is True
    assert path["metrics_accounting"]["rebalance_fees_applied"] is True
    assert path["metrics_accounting"]["member_costs_recharged"] is False
    # Day0 growth then fee uses only contemporaneous returns + declared schedule.
    assert path["fee_fractions"][0] >= 0.0


def test_positive_fee_without_schedule_parks() -> None:
    result = compare_fixed_member_budget_baselines(
        members=(
            _member("soxl_core", (0.01, -0.02, 0.03)),
            _member("tqqq_core", (0.02, -0.01, 0.01)),
        ),
        baselines=(
            {
                "baseline_id": "balanced_50_50",
                "member_budget_weights": {"soxl_core": 0.50, "tqqq_core": 0.50},
            },
            {
                "baseline_id": "soxl_heavy_60_40",
                "member_budget_weights": {"soxl_core": 0.60, "tqqq_core": 0.40},
            },
        ),
        capital_path_options={"rebalance_fee_bps": 10.0},
    )
    assert result["status"] == "PARKED"
    assert result["reason_codes"] == ("REBALANCE_SCHEDULE_REQUIRED_FOR_POSITIVE_FEE",)
    assert result["execution_authorized"] is False
    assert result["no_order"] is True


def test_diagnose_lists_missing_fee_and_schedule_without_inventing_formula() -> None:
    gaps = diagnose_capital_path_inputs(
        apply_risk_scaling=False,
        rebalance_fee_bps=None,
        rebalance_indices=None,
        cash_member_id=None,
        has_risk_diagnosis=False,
    )
    assert "CAPITAL_PATH_OPTIONAL_INPUTS_NOT_REQUESTED" in gaps
    fee_gaps = diagnose_capital_path_inputs(
        apply_risk_scaling=True,
        rebalance_fee_bps=None,
        rebalance_indices=(0,),
        cash_member_id=None,
        has_risk_diagnosis=False,
    )
    assert "NEED_EXPLICIT_COMBO_REBALANCE_FEE_BPS" in fee_gaps
    assert "NEED_CASH_MEMBER_ID_FOR_RISK_SCALING_RESIDUAL" in fee_gaps
    assert "NEED_RISK_POLICY_AND_TARGET_WEIGHTS_FOR_SCALING" in fee_gaps


def test_one_way_turnover_matches_half_l1() -> None:
    assert one_way_turnover(
        {"a": 0.6, "b": 0.4}, {"a": 0.5, "b": 0.5}
    ) == pytest.approx(0.1)
