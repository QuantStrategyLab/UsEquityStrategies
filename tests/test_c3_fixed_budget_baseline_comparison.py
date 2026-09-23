from __future__ import annotations

import math

from us_equity_strategies.portfolio_risk_budget import (
    PortfolioAssetRiskSpec,
    PortfolioRiskBudgetPolicy,
)
from us_equity_strategies.research.c3_fixed_budget_baseline_comparison import (
    EVIDENCE_SCOPE,
    SCHEMA_VERSION,
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


def _baseline(baseline_id: str, left: float, right: float, **overrides: object) -> dict[str, object]:
    payload = {
        "baseline_id": baseline_id,
        "member_budget_weights": {"soxl_core": left, "tqqq_core": right},
    }
    payload.update(overrides)
    return payload


SPECS = {
    "BOXX": PortfolioAssetRiskSpec("BOXX", 1.0, "CASH", is_cash=True),
    "SOXL": PortfolioAssetRiskSpec("SOXL", 3.0, "SEMICONDUCTOR"),
    "TQQQ": PortfolioAssetRiskSpec("TQQQ", 3.0, "NASDAQ100"),
}
POLICY = PortfolioRiskBudgetPolicy(
    cash_symbol="BOXX",
    max_effective_risk_exposure=1.5,
    max_symbol_weights={"SOXL": 0.40, "TQQQ": 0.40},
    max_underlying_effective_exposure={"SEMICONDUCTOR": 1.2, "NASDAQ100": 1.2},
)


def test_compares_two_fixed_budgets_without_authorizing_execution() -> None:
    result = compare_fixed_member_budget_baselines(
        members=(
            _member("soxl_core", (0.01, -0.02, 0.03, 0.00)),
            _member("tqqq_core", (0.02, -0.01, 0.01, -0.01)),
        ),
        baselines=(
            _baseline("balanced_50_50", 0.50, 0.50),
            _baseline("soxl_heavy_60_40", 0.60, 0.40),
        ),
    )

    assert result["schema_version"] == SCHEMA_VERSION
    assert result["status"] == "READY_RESEARCH_ONLY"
    assert result["research_only"] is True
    assert result["shadow_only"] is True
    assert result["execution_authorized"] is False
    assert result["promotion_authorized"] is False
    assert result["no_order"] is True
    assert result["evidence_scope"] == EVIDENCE_SCOPE
    assert result["comparability"]["quote_currency"] == "USD"
    assert result["policy_digest"] == "3" * 64
    assert len(result["evidence_digest"]) == 64
    assert [item["baseline_id"] for item in result["baselines"]] == [
        "balanced_50_50",
        "soxl_heavy_60_40",
    ]

    balanced = result["baselines"][0]
    assert balanced["member_budget_weights"] == {"soxl_core": 0.5, "tqqq_core": 0.5}
    expected = (0.015, -0.015, 0.02, -0.005)
    metrics = balanced["metrics"]
    equity = 1.0
    for value in expected:
        equity *= 1.0 + value
    assert math.isclose(float(metrics["terminal_nav"]), equity)
    assert math.isclose(float(metrics["cumulative_return"]), equity - 1.0)
    assert metrics["session_count"] == 4
    assert metrics["max_drawdown"] <= 0.0
    assert metrics["annualized_volatility"] >= 0.0
    assert result["boundaries"]["optimization"] == "DISABLED_FIXED_BUDGETS_ONLY"
    assert result["boundaries"]["leverage_expansion"] == "NOT_AUTHORIZED_NOT_COMPUTED"
    assert result["boundaries"]["integer_share_sizing"] == "DIAGNOSTIC_ONLY_NOT_COMPUTED"
    assert result["boundaries"]["concentration_underlying_diagnosis"] == "NOT_PROVIDED"
    assert result["boundaries"]["metrics_basis"] == "RAW_FIXED_MEMBER_BUDGETS_UNSCALED"
    assert result["boundaries"]["risk_scaling_applied_to_returns"] == "NOT_APPLIED"
    assert result["boundaries"]["rebalance_fee_reconstruction"] == "NOT_COMPUTED"
    assert balanced["metrics_accounting"] == {
        "weight_source": "declared_member_budget_weights",
        "risk_scaling_applied": False,
        "rebalance_fees_applied": False,
        "realized_vs_recommended": "METRICS_ARE_RAW_FIXED_BUDGET_NOT_RISK_SCALED_RECOMMENDATION",
    }


def test_mismatched_comparability_parks_fail_closed() -> None:
    result = compare_fixed_member_budget_baselines(
        members=(
            _member("soxl_core", (0.01, -0.02, 0.03, 0.00)),
            _member("tqqq_core", (0.02, -0.01, 0.01, -0.01), as_of="2026-09-20"),
        ),
        baselines=(
            _baseline("balanced_50_50", 0.50, 0.50),
            _baseline("soxl_heavy_60_40", 0.60, 0.40),
        ),
    )
    assert result["status"] == "PARKED"
    assert result["reason_codes"] == ("MEMBER_COMPARABILITY_MISMATCH",)
    assert result["execution_authorized"] is False
    assert result["no_order"] is True
    assert result["baselines"] == []


def test_date_alignment_mismatch_parks() -> None:
    result = compare_fixed_member_budget_baselines(
        members=(
            _member("soxl_core", (0.01, -0.02, 0.03, 0.00)),
            _member(
                "tqqq_core",
                (0.02, -0.01, 0.01),
                dates=("2026-01-02", "2026-01-05", "2026-01-06"),
            ),
        ),
        baselines=(
            _baseline("balanced_50_50", 0.50, 0.50),
            _baseline("soxl_heavy_60_40", 0.60, 0.40),
        ),
    )
    assert result["status"] == "PARKED"
    assert result["reason_codes"] == ("MEMBER_DATE_ALIGNMENT_MISMATCH",)


def test_budget_not_fully_funded_parks() -> None:
    result = compare_fixed_member_budget_baselines(
        members=(
            _member("soxl_core", (0.01, -0.02, 0.03, 0.00)),
            _member("tqqq_core", (0.02, -0.01, 0.01, -0.01)),
        ),
        baselines=(
            _baseline("balanced_50_50", 0.50, 0.50),
            _baseline("broken_70_40", 0.70, 0.40),
        ),
    )
    assert result["status"] == "PARKED"
    assert result["reason_codes"] == ("BASELINE_BUDGETS_NOT_FULLY_FUNDED",)


def test_partial_turnover_declaration_parks() -> None:
    result = compare_fixed_member_budget_baselines(
        members=(
            _member("soxl_core", (0.01, -0.02, 0.03, 0.00)),
            _member("tqqq_core", (0.02, -0.01, 0.01, -0.01)),
        ),
        baselines=(
            _baseline("balanced_50_50", 0.50, 0.50, declared_one_way_turnover=0.10),
            _baseline("soxl_heavy_60_40", 0.60, 0.40),
        ),
    )
    assert result["status"] == "PARKED"
    assert result["reason_codes"] == ("BASELINE_TURNOVER_INCOMPLETE",)


def test_concentration_snapshot_reuses_portfolio_risk_budget() -> None:
    result = compare_fixed_member_budget_baselines(
        members=(
            _member("soxl_core", (0.01, -0.02, 0.03, 0.00)),
            _member("tqqq_core", (0.02, -0.01, 0.01, -0.01)),
        ),
        baselines=(
            _baseline(
                "balanced_50_50",
                0.50,
                0.50,
                representative_target_weights={"BOXX": 0.60, "SOXL": 0.20, "TQQQ": 0.20},
                declared_one_way_turnover=0.05,
            ),
            _baseline(
                "soxl_heavy_60_40",
                0.60,
                0.40,
                representative_target_weights={"BOXX": 0.50, "SOXL": 0.30, "TQQQ": 0.20},
                declared_one_way_turnover=0.08,
            ),
        ),
        asset_risk_specs=SPECS,
        risk_policy=POLICY,
    )
    assert result["status"] == "READY_RESEARCH_ONLY"
    assert result["boundaries"]["concentration_underlying_diagnosis"] == (
        "PORTFOLIO_RISK_BUDGET_SNAPSHOT_ONLY"
    )
    first = result["baselines"][0]["concentration"]
    assert first is not None
    assert first["execution_authorized"] is False
    assert first["status"] in {"APPROVE", "REDUCE"}
    assert "effective_risk_exposure" in first["metrics"]
    assert result["baselines"][0]["declared_one_way_turnover"] == 0.05
    accounting = result["baselines"][0]["metrics_accounting"]
    assert accounting["risk_scaling_applied"] is False
    assert accounting["rebalance_fees_applied"] is False
    assert accounting["weight_source"] == "declared_member_budget_weights"
    assert accounting["realized_vs_recommended"] == (
        "METRICS_ARE_RAW_FIXED_BUDGET_NOT_RISK_SCALED_RECOMMENDATION"
    )
    # Diagnostic recommendation must not rewrite raw fixed-budget metrics.
    assert "recommended_target_weights" in first
    assert result["baselines"][0]["metrics"]["session_count"] == 4
    assert result["boundaries"]["risk_scaling_applied_to_returns"] == "NOT_APPLIED"
    assert result["boundaries"]["rebalance_fee_reconstruction"] == "NOT_COMPUTED"


def test_comparison_forwards_explicit_fee_bearing_members() -> None:
    dates = ("2026-01-02", "2026-01-05", "2026-01-06")
    members = (
        _member("cash_sleeve", (0.0, 0.0, 0.0), dates=dates),
        _member("soxl_core", (0.5, 0.0, 0.10), dates=dates),
        _member("tqqq_core", (0.0, 0.0, 0.0), dates=dates),
    )
    baselines = (
        {
            "baseline_id": "risk_tilt_20_40_40",
            "member_budget_weights": {
                "cash_sleeve": 0.2,
                "soxl_core": 0.4,
                "tqqq_core": 0.4,
            },
        },
        {
            "baseline_id": "split_50_25_25",
            "member_budget_weights": {
                "cash_sleeve": 0.5,
                "soxl_core": 0.25,
                "tqqq_core": 0.25,
            },
        },
    )
    missing = compare_fixed_member_budget_baselines(
        members=members,
        baselines=baselines,
        capital_path_options={
            "rebalance_fee_bps": 100.0,
            "rebalance_indices": (1,),
        },
    )
    assert missing["status"] == "PARKED"
    assert missing["reason_codes"] == ("FEE_BEARING_MEMBER_IDS_REQUIRED",)
    assert missing["execution_authorized"] is False
    assert missing["no_order"] is True

    illegal = compare_fixed_member_budget_baselines(
        members=members,
        baselines=baselines,
        capital_path_options={
            "rebalance_fee_bps": 100.0,
            "rebalance_indices": (1,),
            "fee_bearing_member_ids": ("not_a_member",),
        },
    )
    assert illegal["status"] == "PARKED"
    assert illegal["reason_codes"] == ("FEE_BEARING_MEMBER_IDS_INVALID",)

    zero_fee = compare_fixed_member_budget_baselines(
        members=members,
        baselines=baselines,
        capital_path_options={
            "rebalance_fee_bps": 0.0,
            "rebalance_indices": (1,),
        },
    )
    assert zero_fee["status"] == "READY_RESEARCH_ONLY"
    zero_path = zero_fee["baselines"][0]["capital_path"]
    assert zero_path["metrics_accounting"]["rebalance_fees_applied"] is False
    assert zero_path["fee_fractions"] == (0.0, 0.0, 0.0)
    assert zero_path["rebalance_fee_basis"] == "ZERO_FEE_RATE_NO_COST"

    def _ready(fee_bearing_member_ids: tuple[str, ...]) -> dict[str, object]:
        return compare_fixed_member_budget_baselines(
            members=members,
            baselines=baselines,
            capital_path_options={
                "rebalance_fee_bps": 100.0,
                "rebalance_indices": (1,),
                "fee_bearing_member_ids": fee_bearing_member_ids,
            },
        )

    exclude_cash = _ready(("soxl_core", "tqqq_core"))
    include_cash = _ready(("cash_sleeve", "soxl_core", "tqqq_core"))
    assert exclude_cash["status"] == "READY_RESEARCH_ONLY"
    assert include_cash["status"] == "READY_RESEARCH_ONLY"
    assert exclude_cash["boundaries"]["rebalance_fee_reconstruction"] == (
        "COMPUTED_EXPLICIT_BPS_AND_SCHEDULE"
    )
    exclude_path = next(
        item["capital_path"]
        for item in exclude_cash["baselines"]
        if item["baseline_id"] == "risk_tilt_20_40_40"
    )
    include_path = next(
        item["capital_path"]
        for item in include_cash["baselines"]
        if item["baseline_id"] == "risk_tilt_20_40_40"
    )
    exclude_fee = (1.0 / 6.0) * 0.01
    assert exclude_path["fee_bearing_member_ids"] == ("soxl_core", "tqqq_core")
    assert exclude_path["fee_fractions"][0] == 0.0
    assert exclude_path["fee_fractions"][2] == 0.0
    assert math.isclose(float(exclude_path["fee_fractions"][1]), exclude_fee)
    assert math.isclose(float(include_path["fee_fractions"][1]), 0.2 * 0.01)
    assert not math.isclose(
        float(exclude_path["fee_fractions"][1]),
        float(include_path["fee_fractions"][1]),
    )
    terminal = 1.2 * (1.0 - exclude_fee) * 1.04
    assert math.isclose(float(exclude_path["metrics"]["terminal_nav"]), terminal)
    assert math.isclose(math.fsum(exclude_path["final_weights"].values()), 1.0)
    assert math.isclose(exclude_path["final_weights"]["cash_sleeve"], 0.2 / 1.04)
    assert math.isclose(exclude_path["final_weights"]["soxl_core"], 0.44 / 1.04)
    assert math.isclose(exclude_path["final_weights"]["tqqq_core"], 0.4 / 1.04)
    assert exclude_path["rebalance_fee_basis"] == (
        "PRE_TRADE_NOTIONAL_TURNOVER_APPROXIMATION"
    )


def test_incomplete_comparability_fields_park() -> None:
    left = _member("soxl_core", (0.01, -0.02, 0.03, 0.00))
    right = _member("tqqq_core", (0.02, -0.01, 0.01, -0.01))
    del left["data_scope_digest"]
    result = compare_fixed_member_budget_baselines(
        members=(left, right),
        baselines=(
            _baseline("balanced_50_50", 0.50, 0.50),
            _baseline("soxl_heavy_60_40", 0.60, 0.40),
        ),
    )
    assert result["status"] == "PARKED"
    assert result["reason_codes"] == ("MEMBER_COMPARABILITY_INCOMPLETE",)
