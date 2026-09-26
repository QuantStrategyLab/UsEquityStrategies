import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "docs/research/first_compounding_20260925"
SPEC = importlib.util.spec_from_file_location("auto_allocate", RESEARCH / "auto_allocate.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def input_data():
    return json.loads((RESEARCH / "auto_allocation_input.json").read_text())


def test_solver_funds_once_and_obeys_stress_limit():
    result = MODULE.allocate(input_data())
    assert result["status"] == "MECHANISM_APPROXIMATION"
    assert result["funding_check_usd"] == 100000
    assert result["stress_loss_usd"] <= 5000
    assert result["budget_usd"]["CASH"] >= 0
    assert result["fee_usd"] > 0


def test_actual_locked_holdings_can_make_policy_infeasible():
    data = input_data()
    data["current_usd"] = {"SOXL": 90000, "TQQQ": 0, "CASH": 10000}
    data["locked_usd"] = {"SOXL": 90000, "TQQQ": 0, "CASH": 0}
    data["stress_loss_limit_usd"] = 1000
    result = MODULE.allocate(data)
    assert result["status"] == "INFEASIBLE"
    assert "locked" in result["reason"]


def test_future_observation_and_missing_scenario_are_not_optimized():
    data = input_data()
    data["evidence"]["observed_through"] = "2026-09-26"
    assert MODULE.allocate(data)["status"] == "DATA_INSUFFICIENT"
    data = input_data()
    del data["evidence"]["scenarios"]
    assert MODULE.allocate(data)["status"] == "DATA_INSUFFICIENT"


def test_fee_cannot_create_negative_cash_at_small_account_boundary():
    data = input_data()
    data.update(nav_usd=10, current_usd={"SOXL": 0, "TQQQ": 0, "CASH": 10},
                search_step_usd=10, stress_loss_limit_usd=10)
    data["evidence"]["scenarios"] = [
        {"weight": 1.0, "returns": {"SOXL": 0.1, "TQQQ": 0.0}}
    ]
    result = MODULE.allocate(data)
    assert result["status"] == "MECHANISM_APPROXIMATION"
    assert result["budget_usd"]["CASH"] >= 0
    assert result["funding_check_usd"] == 10


def test_existing_locked_holdings_off_grid_remain_feasible():
    data = input_data()
    data.update(nav_usd=100, current_usd={"SOXL": 50, "TQQQ": 0, "CASH": 50},
                locked_usd={"SOXL": 50, "TQQQ": 0, "CASH": 50},
                transfer_fee_bps=0, search_step_usd=30, stress_loss_limit_usd=100)
    result = MODULE.allocate(data)
    assert result["status"] == "MECHANISM_APPROXIMATION"
    assert result["budget_usd"] == {"SOXL": 50.0, "TQQQ": 0.0, "CASH": 50.0}


def test_high_water_floor_does_not_reset_after_a_loss():
    data = input_data()
    data.update(nav_usd=9400, current_usd={"SOXL": 9400, "TQQQ": 0, "CASH": 0},
                locked_usd={"SOXL": 9400, "TQQQ": 0, "CASH": 0},
                transfer_fee_bps=0, search_step_usd=100,
                stress_loss_limit_usd=1000,
                high_water_policy={"version": "synthetic_no_external_flow_v1",
                    "initial_principal_usd": 10000, "high_water_usd": 10000,
                    "cumulative_loss_limit_usd": 1000})
    data["evidence"]["scenarios"] = [
        {"weight": 1.0, "returns": {"SOXL": -0.05, "TQQQ": 0.0}}
    ]
    assert MODULE.allocate(data)["status"] == "INFEASIBLE"
    data["locked_usd"]["SOXL"] = 0
    funded = MODULE.allocate(data)
    assert funded["status"] == "MECHANISM_APPROXIMATION"
    assert funded["wealth_floor_usd"] == 9000
    assert min(funded["scenario_terminal_wealth_usd"]) >= 9000
    data["nav_usd"] = 8800
    data["current_usd"] = {"SOXL": 0, "TQQQ": 0, "CASH": 8800}
    assert "already breached" in MODULE.allocate(data)["reason"]


def test_explicit_reference_curve_caps_current_member_budgets_without_replacing_loss_policy():
    data = input_data()
    data.update(nav_usd=100, current_usd={"SOXL": 0, "TQQQ": 0, "CASH": 100},
                locked_usd={"SOXL": 0, "TQQQ": 0, "CASH": 0},
                transfer_fee_bps=0, search_step_usd=10, stress_loss_limit_usd=100,
                capital_curve_policy={"version": "explicit_reference_v1",
                    "wealth_reference_usd": 200, "a0_usd": 100,
                    "lower": 0.2, "upper": 0.8, "curvature": 1})
    data["evidence"]["scenarios"] = [
        {"weight": 1.0, "returns": {"SOXL": 0.1, "TQQQ": 0.0}}
    ]
    result = MODULE.allocate(data)
    assert result["status"] == "MECHANISM_APPROXIMATION"
    assert result["member_budget_cap_usd"] == 40
    assert result["budget_usd"] == {"SOXL": 40, "TQQQ": 0, "CASH": 60}
    assert result["reference_curve_risk_capital_usd"] == 80
    assert result["reference_curve_risk_capital_role"] == "not_current_available_funds"
    assert result["stress_loss_limit_usd"] == 100

    data["current_usd"] = {"SOXL": 50, "TQQQ": 0, "CASH": 50}
    data["locked_usd"] = {"SOXL": 50, "TQQQ": 0, "CASH": 0}
    blocked = MODULE.allocate(data)
    assert blocked["status"] == "INFEASIBLE"
    assert "locked member" in blocked["reason"]


def test_curve_policy_requires_complete_explicit_parameters():
    data = input_data()
    data["capital_curve_policy"] = {"version": "explicit_reference_v1", "wealth_reference_usd": 100}
    assert MODULE.allocate(data)["status"] == "DATA_INSUFFICIENT"
    data["capital_curve_policy"] = {"version": "explicit_reference_v1",
        "wealth_reference_usd": 100, "a0_usd": 100,
        "lower": 0.2, "upper": 0.8, "curvature": 1, "unknown": 1}
    assert MODULE.allocate(data)["status"] == "DATA_INSUFFICIENT"
