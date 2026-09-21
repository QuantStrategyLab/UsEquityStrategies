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
