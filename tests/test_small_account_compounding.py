"""Account-level synthetic proof using both frozen full-configuration builders."""

import importlib.util
import math
from pathlib import Path


DEMO = Path(__file__).resolve().parents[1] / "docs/research/first_compounding_20260925/small_account_demo.py"
SPEC = importlib.util.spec_from_file_location("small_account_demo", DEMO)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_full_builder_account_replays_without_future_position_answers():
    result = MODULE.run()
    assert result["status"] == "SYNTHETIC_BUILDER_REPLAY"
    assert result["allocation"]["funding_check_usd"] == 10_000
    assert result["qpk_readback_days"] == {"SOXL": 2, "TQQQ": 2}
    assert result["whole_share_qpk_readback_days"] == {"SOXL": 2, "TQQQ": 2}
    days = result["account_days"]
    assert [day["session"] for day in days] == ["2024-01-02", "2024-01-03", "2024-01-04"]
    assert days[1]["trading_fees_usd"] > 0
    assert days[1]["account_nav_usd"] < days[0]["account_nav_usd"]
    assert all(day["account_cash_usd"] >= 0 for day in days)
    assert all(day["member_nav_usd"]["SOXL"] + day["member_nav_usd"]["TQQQ"]
               == day["account_nav_usd"] for day in days)
    preview = result["first_trade_whole_share_preview"]
    assert preview["account_cash_usd"] > 0
    assert sum(item["stock_value_usd"] + item["trading_fees_usd"] + item["cash_usd"]
               for item in preview["members"].values()) == 10_000
    assert all(isinstance(quantity, int) for item in preview["members"].values()
               for quantity in item["holdings_shares"].values())
    whole_days = result["whole_share_account_days"]
    assert whole_days[1]["account_cash_usd"] == preview["account_cash_usd"]
    assert all(value.is_integer() for day in whole_days
               for value in day["holdings_shares"].values())
    assert whole_days[2]["account_nav_usd"] != whole_days[1]["account_nav_usd"]
    proposals = result["daily_capital_proposals"]
    assert [item["current_nav_usd"] for item in proposals] == [
        item["account_nav_usd"] for item in whole_days]
    assert all(item["high_water_usd"] == 10_000 and item["wealth_floor_usd"] == 9_000
               and item["proposal_only"] for item in proposals)
    assert proposals[-1]["preserved_securities_for_proposal_usd"] == {"SOXL": 487.0, "TQQQ": 9032.0}
    assert sum(proposals[-1]["proposed_budget_usd"].values()) == whole_days[-1]["account_nav_usd"]
    dynamic = result["dynamic_cash_only_path"]
    assert dynamic["status"] == "SYNTHETIC_CASH_ONLY_CONTINUATION"
    assert dynamic["account_days"][:2] == [
        {key: day[key] for key in ("session", "account_nav_usd", "account_cash_usd",
                                    "member_nav_usd", "holdings_shares")}
        for day in whole_days[:2]]
    assert dynamic["account_days"][-1]["account_nav_usd"] == whole_days[-1]["account_nav_usd"]
    assert dynamic["account_days"][-1]["holdings_shares"] != whole_days[-1]["holdings_shares"]
    assert dynamic["transfers"][1]["member_cash_delta_usd"] == {"SOXL": 200.5, "TQQQ": -300.0}
    assert dynamic["transfers"][1]["outer_cash_delta_usd"] == 99.5
    assert len(dynamic["segment_qpk_readbacks"]) == 4
    assert [item["session"] for item in dynamic["checkpoint_restorations"]] == [
        "2024-01-03", "2024-01-04"]
    checkpoint = dynamic["last_checkpoint"]
    assert checkpoint["high_water_usd"] == 10_000
    assert checkpoint["outer_cash_usd"] + sum(item["nav_usd"] for item in
        checkpoint["members"].values()) == dynamic["account_days"][-1]["account_nav_usd"]
    sale = result["sale_funding_path"]
    assert sale["status"] == "SYNTHETIC_SELL_FIRST_CONTINUATION"
    assert sale["candidate_budget_usd"] == {"SOXL": 2900.0, "TQQQ": 7000.0, "CASH": 99.5}
    assert sale["planned_sales"]["TQQQ"]["shares"] == 21
    assert sale["planned_sales"]["TQQQ"]["decision_reference_price_usd"] == 100
    assert sale["realized_sales"]["TQQQ"]["actual_fill_usd"] == 90
    assert sale["realized_sales"]["TQQQ"]["net_cash_released_usd"] == 1890
    assert sale["post_sale_reallocation_usd"] == {"SOXL": 2700.0, "TQQQ": 7100.0, "CASH": 94.5}
    assert sale["post_sale_member_cash_delta_usd"] == {"SOXL": 2058.5, "TQQQ": -2153.0}
    assert sale["post_sale_account_cash_before_and_after_usd"] == 2289.5
    assert sale["next_day"]["account_nav_usd"] == 9892.517
    assert sale["next_day"]["trading_fees_usd"] == 1.983
    for member, amounts in sale["next_day"]["builder_decision_target_value_usd"].items():
        realized = sale["next_day"]["member_holdings_shares"][member]
        whole = {symbol: math.floor(amount / sale["reference_prices_usd"][symbol] + 1e-9)
            for symbol, amount in amounts.items()}
        assert {symbol: quantity for symbol, quantity in whole.items() if quantity} == realized
    assert sale["segment_qpk_readbacks"] == ["SOXL", "TQQQ"]
    continuous = sale["continuous_qpk_readbacks"]
    assert set(continuous) == {"SOXL", "TQQQ"}
    assert all(item["initial_session"] == "2024-01-03" and item["sessions"] == [
        "2024-01-04", "2024-01-05"] for item in continuous.values())
    assert continuous["TQQQ"]["sale_trade_cashflow_usd"] == 1890
    assert {member: item["next_interval_member_flow_usd"] for member, item in
        continuous.items()} == sale["post_sale_member_cash_delta_usd"]
    assert sum(item["nav_usd"][0] for item in continuous.values()) == sale[
        "post_sale_account_nav_usd"]
    assert sum(item["nav_usd"][1] for item in continuous.values()) + sale[
        "post_sale_outer_cash_usd"] == sale["next_day"]["account_nav_usd"]
    assert sum(item["total_fees_usd"] for item in continuous.values()) == sale[
        "next_day"]["trading_fees_usd"]
    account_ledger = sale["account_qpk_readback"]
    assert account_ledger["initial_session"] == "2024-01-03"
    assert account_ledger["sessions"] == ["2024-01-04", "2024-01-05"]
    assert account_ledger["nav_usd"] == [sale["post_sale_account_nav_usd"],
        sale["next_day"]["account_nav_usd"]]
    assert account_ledger["cash_usd"][1] == sale["next_day"]["account_cash_usd"]
    assert account_ledger["holdings_shares"][1] == sale["next_day"]["holdings_shares"]
    assert account_ledger["cash_usd"][0] == account_ledger["initial_cash_usd"] + account_ledger[
        "trade_net_cashflow_usd"][0]
    assert math.isclose(account_ledger["cash_usd"][1], account_ledger["cash_usd"][0] + account_ledger[
        "trade_net_cashflow_usd"][1] - account_ledger["total_fees_usd"], abs_tol=1e-8)
    assert account_ledger["external_cashflow_usd"] == [0.0, 0.0]
    assert account_ledger["total_fees_usd"] == sale["next_day"]["trading_fees_usd"]
    assert account_ledger["member_nav_reconciled"] is True
    assert [event["phase"] for event in sale["funding_events"]] == ["sale", "transfer"]
    assert sale["funding_events"][0]["shares_delta"] == -21
    assert sale["funding_events"][0]["cash_delta_usd"] == 1890
    assert sum(sale["funding_events"][1]["cash_delta_usd"].values()) == 0
    assert len(sale["funding_event_readback_sha256"]) == 64


def test_full_candidates_replay_identically_without_inactive_income_prices():
    full = MODULE.run()
    sparse = MODULE.run(omit_inactive_income_prices=True)
    for key in ("account_days", "whole_share_account_days", "daily_capital_proposals"):
        assert sparse[key] == full[key]
    assert sparse["dynamic_cash_only_path"]["account_days"] == full[
        "dynamic_cash_only_path"]["account_days"]
    assert sparse["sale_funding_path"]["next_day"] == full["sale_funding_path"]["next_day"]


def test_explicit_member_budget_cap_keeps_outer_cash_through_sale_and_transfer():
    result = MODULE.run(capital_curve_policy={
        "version": "explicit_reference_v1", "wealth_reference_usd": 10000,
        "a0_usd": 100000, "lower": 0.9, "upper": 1.0, "curvature": 1,
    })
    allocation = result["allocation"]
    outer_before = allocation["budget_usd"]["CASH"]
    assert outer_before > 0
    assert sum(allocation["budget_usd"].values()) == 10000
    assert allocation["budget_usd"]["SOXL"] + allocation["budget_usd"]["TQQQ"] <= allocation[
        "member_budget_cap_usd"]
    sale = result["sale_funding_path"]
    assert sale["outer_cash_before_usd"] == outer_before
    transfer = sale["funding_events"][-1]["cash_delta_usd"]
    assert math.isclose(transfer["CASH"], sale["post_sale_outer_cash_usd"] - outer_before,
                        abs_tol=1e-8)
    assert math.isclose(sum(transfer.values()), 0, abs_tol=1e-8)
    assert math.isclose(sale["post_sale_account_cash_before_and_after_usd"],
        sale["post_sale_outer_cash_usd"] + sum(
            state["cash_usd"] for state in sale["post_sale_funded_member_states"].values()),
        abs_tol=1e-6)
    assert math.isclose(sale["post_sale_account_nav_usd"] - sale["next_day"]["trading_fees_usd"],
        sale["next_day"]["account_nav_usd"], abs_tol=1e-6)
    assert sale["account_qpk_readback"]["initial_outer_cash_usd"] == outer_before
    assert sale["account_qpk_readback"]["external_cashflow_usd"] == [0.0, 0.0]
    assert sale["account_qpk_readback"]["member_nav_reconciled"] is True


def test_future_price_change_cannot_change_prior_decisions():
    shocked = MODULE.run()
    flat = MODULE.run(terminal_price_shock=False)
    for key in ("account_days", "whole_share_account_days", "daily_capital_proposals"):
        assert shocked[key][:2] == flat[key][:2]
        nav_field = "current_nav_usd" if key == "daily_capital_proposals" else "account_nav_usd"
        assert shocked[key][-1][nav_field] != flat[key][-1][nav_field]
    assert shocked["dynamic_cash_only_path"]["account_days"][:2] == flat[
        "dynamic_cash_only_path"]["account_days"][:2]
    assert shocked["dynamic_cash_only_path"]["account_days"][-1]["account_nav_usd"] != flat[
        "dynamic_cash_only_path"]["account_days"][-1]["account_nav_usd"]
    assert shocked["sale_funding_path"]["candidate_budget_usd"] == flat[
        "sale_funding_path"]["candidate_budget_usd"]
    assert shocked["sale_funding_path"]["planned_sales"] == flat[
        "sale_funding_path"]["planned_sales"]
    assert shocked["sale_funding_path"]["post_sale_account_nav_usd"] != flat[
        "sale_funding_path"]["post_sale_account_nav_usd"]


def test_initially_frozen_fixed_budget_uses_same_continuous_builders():
    automatic = MODULE.run()
    initial = automatic["allocation"]["budget_usd"]
    weights = {member: initial[member] / 10_000 for member in ("SOXL", "TQQQ")}
    fixed = MODULE.run(fixed_member_weights=weights)
    fixed_flat_future = MODULE.run(fixed_member_weights=weights, terminal_price_shock=False)
    assert fixed["allocation"]["allocation_mode"] == "fixed_member_weights_v1"
    assert fixed["allocation"]["budget_usd"] == initial
    automatic_path = automatic["dynamic_cash_only_path"]
    fixed_path = fixed["dynamic_cash_only_path"]
    assert fixed_path["status"] == automatic_path["status"] == "SYNTHETIC_CASH_ONLY_CONTINUATION"
    assert fixed_path["account_days"][:2] == automatic_path["account_days"][:2]
    assert fixed_path["transfers"][0]["target_budget_usd"] == automatic_path[
        "transfers"][0]["target_budget_usd"]
    second = fixed_path["transfers"][1]
    assert second["target_budget_usd"]["SOXL"] == 599.97
    assert second["target_budget_usd"]["TQQQ"] == 9399.53
    assert second["target_budget_usd"] != automatic_path["transfers"][1]["target_budget_usd"]
    assert fixed_path["account_days"][-1]["holdings_shares"] != automatic_path[
        "account_days"][-1]["holdings_shares"]
    assert fixed_path["account_days"][-1]["account_nav_usd"] == automatic_path[
        "account_days"][-1]["account_nav_usd"]
    assert len(fixed_path["segment_qpk_readbacks"]) == 4
    assert fixed_path["transfers"] == fixed_flat_future["dynamic_cash_only_path"]["transfers"]
    assert fixed_path["account_days"][-1]["account_nav_usd"] != fixed_flat_future[
        "dynamic_cash_only_path"]["account_days"][-1]["account_nav_usd"]
