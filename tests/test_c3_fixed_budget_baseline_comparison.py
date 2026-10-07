from __future__ import annotations

import math
from datetime import date
from dataclasses import replace
from copy import deepcopy

import pytest

from us_equity_strategies.research import c3_fixed_budget_baseline_comparison as comparison
from us_equity_strategies.research.c3_self_financing_combo_ledger import (
    build_soxl_tqqq_self_financing_ledgers,
)

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


COMBO_INPUTS = {
    "soxl_net_returns": (-0.1, 0.0, 0.1),
    "tqqq_net_returns": (0.0, 0.1, -0.1),
    "initial_session_date": date(2026, 1, 2),
    "session_dates": (date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)),
    "initial_capital": 1_000.0,
}


def _combo_baseline(identity, soxl, tqqq, cash=0.0, scalar=0.5, **overrides):
    declaration = {"target_weights": {"SOXL": soxl, "TQQQ": tqqq, "CASH": cash},
                   "risk_scalar": scalar, "rebalance_indices": (1,),
                   "combo_fee_bps": 100.0, "fee_bearing_members": ("SOXL", "TQQQ")}
    declaration.update(overrides)
    return {"baseline_id": identity, **declaration,
            "ledgers": build_soxl_tqqq_self_financing_ledgers(**COMBO_INPUTS, **declaration)}


def _combo_report(**overrides):
    inputs = {**COMBO_INPUTS, "as_of": COMBO_INPUTS["session_dates"][-1],
              "baselines": (_combo_baseline("balanced_50_50", 0.5, 0.5),
                            _combo_baseline("soxl_heavy_60_40", 0.6, 0.4)),
              "asset_risk_specs": SPECS, "risk_policy": POLICY,
              "annual_risk_free_rate": 0.252, "annual_minimum_acceptable_return": 0.504}
    return comparison.report_self_financing_combo_baselines(**{**inputs, **overrides})


def test_self_financing_report_consumes_actual_three_ledgers_and_preserves_legacy():
    members = (_member("soxl_core", (0.01, -0.02, 0.03, 0.00)),
               _member("tqqq_core", (0.02, -0.01, 0.01, -0.01)))
    baselines = (_baseline("balanced_50_50", 0.5, 0.5), _baseline("soxl_heavy_60_40", 0.6, 0.4))
    legacy = compare_fixed_member_budget_baselines(members=members, baselines=baselines)
    report = _combo_report()
    assert report["schema_version"] == "qsl.c3-self-financing-baseline-metric-report.v1"
    assert report["promotion_authorized"] is report["execution_authorized"] is False
    assert report["synthetic"] is report["no_order"] is True
    assert [item["baseline_id"] for item in report["baselines"]] == ["balanced_50_50", "soxl_heavy_60_40"]
    for item in report["baselines"]:
        assert set(item["books"]) == {"raw_fixed_budget", "risk_scaled", "risk_scaled_with_synthetic_combo_fee"}
        for book in item["books"].values():
            contract = book["metric_report"]["evaluation_contract"]
            assert contract["version"] == "synthetic_declared_session_account_metrics_v1"
            assert contract["calendar_id"] == "synthetic_declared_sessions"
            assert contract["volatility_ddof"] == 1
            assert contract["real_calendar_verified"] is False
    assert compare_fixed_member_budget_baselines(members=members, baselines=baselines) == legacy


def test_combo_report_hand_net_nav_fees_cash_and_signed_ratios_once():
    baselines = (_combo_baseline("balanced_50_50", 0.5, 0.5), _combo_baseline("soxl_heavy_60_40", 0.6, 0.4))
    before = deepcopy(baselines)
    report = _combo_report(baselines=baselines)
    balanced = report["baselines"][0]
    assert balanced["risk_diagnosis"]["status"] == "REDUCE"
    assert balanced["scaled_target_weights"] == {"SOXL": 0.25, "TQQQ": 0.25, "CASH": 0.5}
    assert balanced["books"]["raw_fixed_budget"]["terminal_nav"] == pytest.approx(1000.0)
    assert balanced["books"]["risk_scaled"]["terminal_nav"] == pytest.approx(1000.0)
    charged = balanced["books"]["risk_scaled_with_synthetic_combo_fee"]
    assert charged["terminal_nav"] == pytest.approx(999.5)
    assert charged["initial_cash"] == pytest.approx(500.0)
    assert charged["terminal_cash"] == pytest.approx(499.75)
    assert charged["total_fees"] == pytest.approx(0.5)
    metrics = charged["metric_report"]["metrics"]
    assert metrics["cumulative_return"] == pytest.approx(-0.0005)
    assert metrics["max_drawdown"] == pytest.approx(-0.025)
    returns = (-0.025, 999.5 / 975.0 - 1.0, 0.0)
    mean = sum(returns) / 3
    std = math.sqrt(sum((r - mean) ** 2 for r in returns) / 2)
    shortfall = math.sqrt(sum(min(r - 0.002, 0.0) ** 2 for r in returns) / 3)
    assert metrics["sharpe"] == pytest.approx((mean - 0.001) / std * math.sqrt(252))
    assert metrics["sortino"] == pytest.approx((mean - 0.002) / shortfall * math.sqrt(252))
    assert metrics["sharpe"] < 0 and metrics["sortino"] < 0
    assert metrics["cagr"] == pytest.approx((999.5 / 1000.0) ** (252 / 3) - 1)
    for declared, output in zip(baselines, report["baselines"], strict=True):
        for name, ledger in declared["ledgers"].items():
            previous_cash = ledger.initial_cash
            for day in ledger.days:
                assert day.cash == pytest.approx(previous_cash + day.trade_net_cashflow - day.fees)
                assert day.nav == pytest.approx(day.cash + sum(mark.valuation for mark in day.positions))
                previous_cash = day.cash
            assert output["books"][name]["metric_report"]["metrics"]["cumulative_return"] == pytest.approx(ledger.total_return)
    assert baselines == before
    assert _combo_report(baselines=baselines) == report


def test_combo_report_retains_drift_without_free_rebalance():
    baselines = (_combo_baseline("balanced_50_50", 0.5, 0.5, rebalance_indices=(), combo_fee_bps=0.0, fee_bearing_members=()),
                 _combo_baseline("soxl_heavy_60_40", 0.6, 0.4, rebalance_indices=(), combo_fee_bps=0.0, fee_bearing_members=()))
    drift = _combo_report(baselines=baselines)["baselines"][0]["books"]
    assert drift["raw_fixed_budget"]["terminal_nav"] == pytest.approx(990.0)
    assert drift["risk_scaled"]["terminal_nav"] == pytest.approx(995.0)
    assert drift["risk_scaled_with_synthetic_combo_fee"]["terminal_nav"] == pytest.approx(995.0)
    assert all(book["total_fees"] == 0 for book in drift.values())
    ledger = baselines[0]["ledgers"]["raw_fixed_budget"]
    assert [mark.quantity for mark in ledger.days[1].positions] == [mark.quantity for mark in ledger.initial_positions]
    assert ledger.days[1].positions[0].valuation / ledger.days[1].nav != 0.5


def test_combo_report_all_cash_and_single_leg_are_legal_declared_budgets():
    cash = (_combo_baseline("cash_a", 0.0, 0.0, 1.0, scalar=1.0),
            _combo_baseline("cash_b", 0.0, 0.0, 1.0, scalar=1.0))
    for baseline in _combo_report(baselines=cash)["baselines"]:
        assert baseline["risk_diagnosis"]["status"] == "APPROVE"
        for book in baseline["books"].values():
            assert book["initial_cash"] == book["terminal_cash"] == 1000.0
            assert book["total_fees"] == 0.0
            assert book["metric_report"]["metrics"]["cumulative_return"] == 0.0
            assert book["metric_report"]["metrics"]["sharpe"] is None
    single = (_combo_baseline("soxl_only", 1.0, 0.0, scalar=0.4),
              _combo_baseline("tqqq_only", 0.0, 1.0, scalar=0.4))
    for baseline in _combo_report(baselines=single)["baselines"]:
        assert baseline["scaled_target_weights"]["CASH"] == pytest.approx(0.6)
        assert baseline["books"]["risk_scaled"]["initial_cash"] == 600.0


@pytest.mark.parametrize("change,reason", [
    ("missing_book", "COMBO_TYPED_THREE_LEDGERS_REQUIRED"),
    ("raw_dict", "COMBO_TYPED_THREE_LEDGERS_REQUIRED"),
    ("missing_session", "COMBO_LEDGER_DECLARATION_MISMATCH"),
    ("wrong_fee", "COMBO_LEDGER_DECLARATION_MISMATCH"),
    ("wrong_rebalance", "COMBO_LEDGER_DECLARATION_MISMATCH"),
    ("wrong_weights", "COMBO_LEDGER_DECLARATION_MISMATCH"),
    ("unscaled_risk", "COMBO_RISK_SCALAR_MISMATCH"),
    ("missing_member", "SELF_FINANCING_WEIGHT_INVALID"),
    ("wrong_calendar", "COMBO_LEDGER_DECLARATION_MISMATCH"),
    ("real_classification", "COMBO_LEDGER_DECLARATION_MISMATCH"),
])
def test_combo_report_refuses_incomplete_or_mismatched_ledger_and_risk_declarations(change, reason):
    baseline = _combo_baseline("balanced_50_50", 0.5, 0.5)
    if change == "missing_book":
        baseline["ledgers"].pop("risk_scaled")
    elif change == "raw_dict":
        baseline["ledgers"]["risk_scaled"] = {}
    elif change == "missing_session":
        ledger = baseline["ledgers"]["risk_scaled"]
        baseline["ledgers"]["risk_scaled"] = replace(ledger, days=ledger.days[:-1])
    elif change == "wrong_fee":
        baseline["combo_fee_bps"] = 10.0
    elif change == "wrong_rebalance":
        baseline["rebalance_indices"] = (2,)
    elif change == "wrong_weights":
        baseline["target_weights"] = {"SOXL": 0.6, "TQQQ": 0.4, "CASH": 0.0}
    elif change == "unscaled_risk":
        baseline = _combo_baseline("balanced_50_50", 0.5, 0.5, scalar=1.0)
    elif change in {"wrong_calendar", "real_classification"}:
        ledger = baseline["ledgers"]["risk_scaled"]
        baseline["ledgers"]["risk_scaled"] = replace(
            ledger, **({"calendar_id": "XNYS"} if change == "wrong_calendar" else {"synthetic": False}))
    else:
        baseline["target_weights"].pop("TQQQ")
    before = deepcopy(baseline)
    with pytest.raises(ValueError, match=reason):
        _combo_report(baselines=(baseline, _combo_baseline("soxl_heavy_60_40", 0.6, 0.4)))
    assert baseline == before


@pytest.mark.parametrize("overrides,reason", [
    ({"as_of": date(2026, 1, 6)}, "COMBO_SESSION_CUTOFF_INVALID"),
    ({"session_dates": (date(2026, 1, 6), date(2026, 1, 5), date(2026, 1, 7))}, "SELF_FINANCING_DATE_MISALIGNED"),
    ({"session_dates": (date(2026, 1, 5), date(2026, 1, 5), date(2026, 1, 7))}, "SELF_FINANCING_DATE_MISALIGNED"),
    ({"tqqq_net_returns": (0.0, 0.1)}, "SELF_FINANCING_RETURN_LENGTH_MISMATCH"),
    ({"soxl_net_returns": (None, 0.0, 0.1)}, "SELF_FINANCING_RETURN_INVALID"),
    ({"soxl_net_returns": (float("nan"), 0.0, 0.1)}, "SELF_FINANCING_RETURN_INVALID"),
    ({"tqqq_net_returns": (0.0, float("inf"), -0.1)}, "SELF_FINANCING_RETURN_INVALID"),
    ({"annual_risk_free_rate": float("nan")}, "RESEARCH_RATE_INVALID"),
])
def test_combo_report_refuses_future_late_missing_or_invalid_common_inputs(overrides, reason):
    with pytest.raises((ValueError, TypeError), match=reason):
        _combo_report(**overrides)


def test_combo_report_requires_two_unique_baselines_and_parks_no_failed_subset():
    first = _combo_baseline("balanced_50_50", 0.5, 0.5)
    with pytest.raises(ValueError, match="BASELINES_REQUIRED"):
        _combo_report(baselines=(first,))
    with pytest.raises(ValueError, match="BASELINE_IDS_NOT_UNIQUE_SORTED"):
        _combo_report(baselines=(first, first))
    invalid = _combo_baseline("soxl_heavy_60_40", 0.6, 0.4)
    invalid["ledgers"].pop("raw_fixed_budget")
    with pytest.raises(ValueError, match="COMBO_TYPED_THREE_LEDGERS_REQUIRED"):
        _combo_report(baselines=(first, invalid))


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
