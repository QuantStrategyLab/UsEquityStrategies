from __future__ import annotations

from dataclasses import replace
import math

from us_equity_strategies.portfolio_risk_budget import (
    PortfolioAssetRiskSpec,
    PortfolioRiskBudgetPolicy,
)
from us_equity_strategies.research.virtual_combo_targets import (
    CorrelationRiskGroup,
    build_frozen_strategy_virtual_target,
    build_frozen_virtual_combo_baseline,
    build_virtual_combo_policy,
    construct_virtual_combo_target,
)


def _policy(*, turnover_limit: float | None = None, max_gross_risk_weight: float = 0.80):
    specs = {
        "BOXX": PortfolioAssetRiskSpec("BOXX", 1.0, "CASH", is_cash=True),
        "QQQM": PortfolioAssetRiskSpec("QQQM", 1.0, "NASDAQ100"),
        "SOXL": PortfolioAssetRiskSpec("SOXL", 3.0, "SEMICONDUCTOR"),
        "TQQQ": PortfolioAssetRiskSpec("TQQQ", 3.0, "NASDAQ100"),
    }
    return build_virtual_combo_policy(
        asset_risk_specs=specs,
        portfolio_risk_budget=PortfolioRiskBudgetPolicy(
            cash_symbol="BOXX",
            max_effective_risk_exposure=1.3,
            max_symbol_weights={"TQQQ": 0.60, "SOXL": 0.60, "QQQM": 0.60},
            max_underlying_effective_exposure={"NASDAQ100": 0.9, "SEMICONDUCTOR": 0.9},
            max_one_way_risk_turnover=turnover_limit,
        ),
        max_gross_risk_weight=max_gross_risk_weight,
        max_strategy_weights={"soxl_core": 0.60, "tqqq_core": 0.60},
        correlation_groups=(
            CorrelationRiskGroup(
                group_id="nasdaq_levered_cluster",
                symbols=("QQQM", "TQQQ"),
                max_effective_exposure=0.80,
            ),
        ),
    )


def _targets():
    return (
        build_frozen_strategy_virtual_target(
            strategy_id="soxl_core",
            source_p1_sha256="1" * 64,
            target_weights={"BOXX": 0.60, "SOXL": 0.40},
        ),
        build_frozen_strategy_virtual_target(
            strategy_id="tqqq_core",
            source_p1_sha256="2" * 64,
            target_weights={"BOXX": 0.60, "TQQQ": 0.40},
        ),
    )


def test_constructs_research_only_virtual_combo_without_performance_evidence() -> None:
    result = construct_virtual_combo_target(
        strategy_targets=_targets(),
        strategy_budget_weights={"soxl_core": 0.50, "tqqq_core": 0.50},
        policy=_policy(),
    )

    assert result["status"] == "APPROVE"
    assert result["research_only"] is True
    assert result["execution_authorized"] is False
    assert result["evidence_scope"] == "VIRTUAL_TARGET_CONSTRUCTION_ONLY"
    assert result["combo_target_weights"] == {"BOXX": 0.60, "SOXL": 0.20, "TQQQ": 0.20}
    summary = result["summary"]
    assert summary["gross_risk_weight"] == 0.4
    assert summary["strategy_budget_weights"] == {"soxl_core": 0.5, "tqqq_core": 0.5}
    assert math.isclose(float(summary["effective_risk_exposure"]), 1.2)
    assert summary["underlying_effective_exposure"] == {
        "NASDAQ100": 0.6000000000000001,
        "SEMICONDUCTOR": 0.6000000000000001,
    }
    assert summary["correlation_group_effective_exposure"] == {
        "nasdaq_levered_cluster": 0.6000000000000001
    }
    assert summary["rebalancing"] == {
        "basis": "NONE",
        "turnover_limit_enabled": False,
        "one_way_risk_turnover": 0.4,
    }
    assert isinstance(result["combo_target_sha256"], str)


def test_reduces_virtual_target_for_gross_and_correlation_cluster_limits() -> None:
    aggressive_targets = (
        build_frozen_strategy_virtual_target(
            strategy_id="soxl_core",
            source_p1_sha256="3" * 64,
            target_weights={"BOXX": 0.20, "SOXL": 0.80},
        ),
        build_frozen_strategy_virtual_target(
            strategy_id="tqqq_core",
            source_p1_sha256="4" * 64,
            target_weights={"BOXX": 0.20, "TQQQ": 0.80},
        ),
    )

    result = construct_virtual_combo_target(
        strategy_targets=aggressive_targets,
        strategy_budget_weights={"soxl_core": 0.50, "tqqq_core": 0.50},
        policy=_policy(max_gross_risk_weight=0.60),
    )

    assert result["status"] == "REDUCE"
    assert result["reason_codes"] == (
        "COMBO_GROSS_RISK_WEIGHT_REDUCED",
        "COMBO_CORRELATION_GROUP_REDUCED",
        "PORTFOLIO_RISK_BUDGET_REDUCED",
    )
    assert result["combo_target_weights"] == {
        "BOXX": 0.5666666666666667,
        "SOXL": 0.21666666666666667,
        "TQQQ": 0.21666666666666667,
    }
    assert result["summary"]["gross_risk_weight"] == 0.43333333333333335


def test_fails_closed_when_a_strategy_exceeds_its_frozen_budget_limit() -> None:
    result = construct_virtual_combo_target(
        strategy_targets=_targets(),
        strategy_budget_weights={"soxl_core": 0.70, "tqqq_core": 0.30},
        policy=_policy(),
    )

    assert result["status"] == "PARKED"
    assert result["reason_codes"] == ("strategy budget exceeds frozen limit",)
    assert result["combo_target_weights"] == {}


def test_fails_closed_if_a_frozen_target_or_policy_is_mutated() -> None:
    targets = _targets()
    mutated_target = replace(targets[0], target_weights={"BOXX": 0.50, "SOXL": 0.50})
    target_result = construct_virtual_combo_target(
        strategy_targets=(mutated_target, targets[1]),
        strategy_budget_weights={"soxl_core": 0.50, "tqqq_core": 0.50},
        policy=_policy(),
    )
    mutated_policy = replace(_policy(), max_gross_risk_weight=0.60)
    policy_result = construct_virtual_combo_target(
        strategy_targets=targets,
        strategy_budget_weights={"soxl_core": 0.50, "tqqq_core": 0.50},
        policy=mutated_policy,
    )

    assert target_result["reason_codes"] == ("frozen strategy virtual target digest mismatch",)
    assert policy_result["reason_codes"] == ("virtual combo policy digest mismatch",)


def test_turnover_limit_requires_and_consumes_a_frozen_virtual_baseline() -> None:
    policy = _policy(turnover_limit=0.10)
    missing_baseline = construct_virtual_combo_target(
        strategy_targets=_targets(),
        strategy_budget_weights={"soxl_core": 0.50, "tqqq_core": 0.50},
        policy=policy,
    )
    baseline = build_frozen_virtual_combo_baseline(
        source_combo_target_sha256="5" * 64,
        target_weights={"BOXX": 0.80, "SOXL": 0.10, "TQQQ": 0.10},
    )
    with_baseline = construct_virtual_combo_target(
        strategy_targets=_targets(),
        strategy_budget_weights={"soxl_core": 0.50, "tqqq_core": 0.50},
        policy=policy,
        rebalance_baseline=baseline,
    )

    assert missing_baseline["reason_codes"] == ("missing frozen virtual combo rebalance baseline",)
    assert with_baseline["status"] == "REDUCE"
    assert with_baseline["summary"]["rebalancing"]["basis"] == "FROZEN_VIRTUAL_COMBO_BASELINE"
    assert with_baseline["summary"]["rebalancing"]["turnover_limit_enabled"] is True
    assert math.isclose(
        float(with_baseline["summary"]["rebalancing"]["one_way_risk_turnover"]), 0.10
    )
