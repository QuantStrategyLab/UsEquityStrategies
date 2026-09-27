"""Bounded S4 research comparison for the frozen TQQQ/QQQ guard candidate.

Only reads the previously verified local private bundle. Daily outputs stay in
that private directory; stdout contains no prices or holdings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from auto_allocate import allocate_single_member_budget
from us_equity_strategies.research.tqqq_qqq_guard_cash_research import (
    _decision,
    _simulate,
    _validated_bars,
    _verified_bundle,
)

POLICY_NAME = "s4_budget_policy.v1.json"
POLICY_SHA256 = "8c7a4410717c52222bb09c91a9c5d6774524625b8b2ad3226ffb2cd28dd31bbd"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _policy(root: Path) -> dict:
    path = root / POLICY_NAME
    if path.is_symlink() or _sha(path) != POLICY_SHA256:
        raise ValueError("S4_FROZEN_POLICY_CHANGED")
    return json.loads(path.read_text())


def _past_scenarios(prefix: list[dict], past_actions: dict, count: int) -> list[dict]:
    if len(prefix) <= count:
        raise ValueError("S4_SCENARIO_WARMUP_INCOMPLETE")
    start = len(prefix) - count
    if any(event["ex_date"] in {row["date"] for row in prefix[start:]}
           for event in past_actions.get("forward_splits", [])):
        raise ValueError("S4_SPLIT_SCENARIO_UNSUPPORTED")
    dividends = {}
    for event in past_actions.get("cash_dividends", []):
        dividends[event["ex_date"]] = dividends.get(event["ex_date"], 0.0) + float(event["rate"])
    scenarios = []
    for i in range(start, len(prefix)):
        row, prior = prefix[i], prefix[i - 1]
        scenarios.append({"date": row["date"],
                          "overnight_open_to_prior_close": row["tqqq_open"] / prior["tqqq_close"],
                          "intraday_close_to_open": row["tqqq_close"] / row["tqqq_open"],
                          "dividend_per_prior_close": dividends.get(row["date"], 0.0) / prior["tqqq_close"]})
    return scenarios


class _AutomaticBudget:
    def __init__(self, policy: dict):
        self.policy = policy
        self.high_water = float(policy["initial_high_water_usd"])
        self.decision_count = 0
        self.zero_budget_count = 0
        self.min_feasible_grid_points = None

    def __call__(self, prefix: list[dict], *, shares: float, cash: float, receivable: float,
                 nav: float, cost_bps: int, past_actions: dict, contract: dict) -> float:
        self.high_water = max(self.high_water, nav)
        signal = _decision(prefix, len(prefix) - 1, shares=shares, nav=nav,
                           use_guard=True, config=contract, member_budget_usd=nav)
        exposure = signal["target_tqqq_usd"] / nav
        auto = self.policy["automatic"]
        scenario_rows = _past_scenarios(prefix, past_actions, auto["trailing_completed_sessions"])
        decision = allocate_single_member_budget({
            "decision_date": prefix[-1]["date"], "nav_usd": nav,
            "cash_usd": cash, "receivable_usd": receivable,
            "current_shares": shares, "current_close": prefix[-1]["tqqq_close"],
            "member_tqqq_exposure_ratio": exposure,
            "observed_high_water_usd": self.high_water,
            "initial_principal_usd": self.policy["initial_principal_usd"],
            "wealth_reference_usd": self.high_water,
            "security_trade_cost_bps": cost_bps,
            "max_worst_scenario_loss_ratio": auto["max_worst_scenario_loss_ratio_of_current_nav"],
            "search_step_usd": auto["member_budget_grid_usd"],
            "capital_curve": auto["curve"], "scenarios": scenario_rows,
        })
        if decision["status"] != "MECHANISM_APPROXIMATION":
            raise ValueError(f"S4_AUTO_BUDGET_{decision['status']}_AT_{prefix[-1]['date']}: {decision['reason']}")
        budget = float(decision["member_budget_usd"])
        actual = _decision(prefix, len(prefix) - 1, shares=shares, nav=nav,
                           use_guard=True, config=contract, member_budget_usd=budget)
        if abs(actual["target_tqqq_usd"] - exposure * budget) > 1e-6:
            raise ValueError("S4_NONLINEAR_BUDGET_ESTIMATE_MISMATCH")
        self.decision_count += 1
        self.zero_budget_count += budget == 0
        feasible = decision["feasible_grid_points"]
        self.min_feasible_grid_points = feasible if self.min_feasible_grid_points is None else min(
            self.min_feasible_grid_points, feasible)
        return budget


def _fixed_budget(_prefix: list[dict], *, shares: float, cash: float,
                  receivable: float, nav: float, **_kwargs) -> float:
    budget = 0.5 * nav
    if budget > cash + shares * _prefix[-1]["tqqq_close"] + 1e-8:
        raise ValueError("S4_FIXED_BUDGET_EXCEEDS_MOVABLE_NAV")
    return budget


def _recovery(ledger: list[dict], initial: float) -> dict:
    """Measure recovery of the deepest percentage drawdown from its latest peak."""
    peak = initial
    peak_index = -1
    trough_index = -1
    worst = 0.0
    worst_peak_nav = initial
    worst_trough_nav = initial
    for i, row in enumerate(ledger):
        nav = row["nav_close"]
        if nav >= peak:
            peak, peak_index = nav, i
        drawdown = nav / peak - 1
        if drawdown < worst:
            worst = drawdown
            trough_index = i
            worst_peak_nav = peak
            worst_trough_nav = nav
            worst_peak_index = peak_index
    if trough_index < 0:
        return {"max_drawdown_amount_usd": 0.0,
                "max_drawdown_peak_to_recovery_sessions": 0,
                "max_drawdown_trough_to_recovery_sessions": 0,
                "max_drawdown_recovered": True}
    recovered = next((i for i in range(trough_index + 1, len(ledger))
                      if ledger[i]["nav_close"] >= worst_peak_nav), None)
    return {"max_drawdown_amount_usd": worst_peak_nav - worst_trough_nav,
            "max_drawdown_peak_to_recovery_sessions": None if recovered is None else recovered - worst_peak_index,
            "max_drawdown_trough_to_recovery_sessions": None if recovered is None else recovered - trough_index,
            "max_drawdown_recovered": recovered is not None,
            "unrecovered_sessions_through_end": len(ledger) - 1 - worst_peak_index if recovered is None else 0}


def run(root: Path) -> dict:
    policy = _policy(root)
    contract, qqq, tqqq, actions = _verified_bundle(root)
    rows = _validated_bars(qqq, tqqq)
    if policy["candidate_contract_sha256"] != _sha(root / "tqqq_qqq_guard_cash_contract.v1.json"):
        raise ValueError("S4_CANDIDATE_IDENTITY_CHANGED")
    if rows[0]["date"] != "2022-01-03" or rows[-1]["date"] != "2024-12-31":
        raise ValueError("S4_DATA_COVERAGE_CHANGED")
    outcomes = {}
    for cost in policy["cost_bps"]:
        for name in ("fixed_member_budget_cash", "automatic_member_budget_cash"):
            selector = _fixed_budget if name == "fixed_member_budget_cash" else _AutomaticBudget(policy)
            metrics, ledger = _simulate(rows, actions, contract, cost_bps=cost,
                                        use_guard=True, budget_selector=selector)
            metrics.update(_recovery(ledger, policy["initial_nav_usd"]))
            metrics["average_cash_ratio"] = sum(row["cash_ratio"] for row in ledger) / len(ledger)
            metrics["end_cash_ratio"] = ledger[-1]["cash_ratio"]
            metrics["average_member_budget_ratio"] = sum(row["member_budget_ratio"] for row in ledger) / len(ledger)
            if isinstance(selector, _AutomaticBudget):
                metrics["budget_decision_count"] = selector.decision_count
                metrics["zero_budget_decision_count"] = selector.zero_budget_count
                metrics["min_feasible_grid_points"] = selector.min_feasible_grid_points
            path = root / f"private_daily_s4_{name}_{cost}bps.json"
            path.write_text(json.dumps(ledger, separators=(",", ":")) + "\n")
            path.chmod(0o600)
            outcomes[f"{name}_{cost}bps"] = metrics
    cash_only = {"start": rows[257]["date"], "end": rows[-1]["date"], "sessions": len(rows) - 257,
                 "end_nav_usd": policy["initial_nav_usd"], "total_return": 0.0,
                 "annualized_return_252_sessions": 0.0, "max_drawdown": 0.0,
                 "max_drawdown_amount_usd": 0.0,
                 "max_drawdown_peak_to_recovery_sessions": 0,
                 "max_drawdown_trough_to_recovery_sessions": 0,
                 "total_cost_usd": 0.0, "average_cash_ratio": 1.0, "end_cash_ratio": 1.0}
    report = {"candidate_id": policy["candidate_id"], "research_only": True,
              "guard_enabled_in_both_invested_paths": True,
              "policy_sha256": POLICY_SHA256,
              "candidate_contract_sha256": policy["candidate_contract_sha256"],
              "source_sha256": {"s4_runner": _sha(Path(__file__)),
                                "allocator": _sha(Path(__file__).with_name("auto_allocate.py")),
                                "candidate_runner": _sha(Path(_simulate.__code__.co_filename))},
              "coverage": {"first_trade": rows[257]["date"], "last_trade_or_mark": rows[-1]["date"],
                           "sessions": len(rows) - 257},
              "outcomes": outcomes, "cash_only": cash_only,
              "limitations": ["2016-2024 remains seen development.",
                              "The 60 past paired scenarios are an estimator, not a future-return oracle or actual drawdown guarantee.",
                              "Provider corporate action known_at is unproven; accounting is retrospective.",
                              "No original v2, personal setting, paper, shadow, live or trading claim."]}
    output = root / "s4_budget_comparison_summary.v1.json"
    output.write_text(json.dumps(report, indent=2) + "\n")
    output.chmod(0o600)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.private_root)
    print(json.dumps({"candidate_id": result["candidate_id"], "coverage": result["coverage"],
                      "policy_sha256": result["policy_sha256"],
                      "private_summary": str(args.private_root / "s4_budget_comparison_summary.v1.json")}, indent=2))


if __name__ == "__main__":
    main()
