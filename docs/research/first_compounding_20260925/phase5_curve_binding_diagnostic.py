"""Read-only S4 curve-cap diagnostic over the frozen Phase 5 private ledgers."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
from pathlib import Path

from us_equity_strategies.research.c3_capital_path import smooth_bounded_capital_risk_ratio

HERE = Path(__file__).resolve().parent
POLICY = HERE / "phase5_curve_binding_diagnostic.v1.json"
POLICY_SHA = "10f0d2b5b56269713d33c98a0ac9f48d74d80751a547b7c8a1f63b2553355a93"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _policy(root: Path) -> dict:
    if _sha(POLICY) != POLICY_SHA:
        raise ValueError("PHASE5_CURVE_DIAGNOSTIC_POLICY_CHANGED")
    policy = json.loads(POLICY.read_text())
    if (policy["schema"] != "qsl.research.phase5_curve_binding_diagnostic.v1"
            or policy["research_only"] is not True or policy["development"] is not True
            or policy["changes_trades_or_ledger"] is not False
            or policy["covered_member_set_M"] != ["TQQQ"]
            or policy["curve"] != {"a0_usd": 10000, "lower": 0.2,
                                    "upper": 0.8, "curvature": 1}):
        raise ValueError("PHASE5_CURVE_DIAGNOSTIC_POLICY_CHANGED")
    if _sha(root / "s4_budget_policy.v1.json") != policy["source_s4_policy_sha256"]:
        raise ValueError("PHASE5_S4_POLICY_CHANGED")
    s4 = json.loads((root / "s4_budget_policy.v1.json").read_text())
    if s4["automatic"]["curve"] != {key: float(value) for key, value in policy["curve"].items()}:
        raise ValueError("PHASE5_S4_CURVE_CHANGED")
    return policy


def _ledger(root: Path, principal: int, cost: int, summary: dict) -> list[dict]:
    name = f"enhanced_{cost}bps"
    if principal == 10000:
        phase4 = root / "phase4_fixed_pair_v3" / "phase4_comparison_summary.v3.json"
        if _sha(phase4) != summary["source_phase4_summary_sha256"]:
            raise ValueError("PHASE5_PHASE4_SOURCE_CHANGED")
        source = json.loads(phase4.read_text())
        digest = source["private_ledger_sha256"][name]
        path = phase4.parent / f"private_daily_{name}.json"
    else:
        key = f"capital_{principal}_{name}"
        digest = summary["private_new_ledger_sha256"][key]
        path = root / "phase5_capital_scale_v1" / f"private_daily_{key}.json"
    if _sha(path) != digest:
        raise ValueError("PHASE5_LEDGER_CHANGED")
    return json.loads(path.read_text())


def _diagnose(ledger: list[dict], principal: int, curve: dict) -> dict:
    if len(ledger) != 444 or ledger[0]["date"] != "2023-03-28" or ledger[-1]["date"] != "2024-12-31":
        raise ValueError("PHASE5_CURVE_WINDOW_CHANGED")
    high_water = float(principal)
    prior_date = "2023-03-27"
    ratios, caps, budgets, references, navs, slacks = [], [], [], [], [], []
    cash, restricted, pending, receivable, reserve, security = [], [], [], [], [], []
    for row in ledger:
        if row["signal_date"] != prior_date:
            raise ValueError("PHASE5_CURVE_SIGNAL_SEQUENCE_CHANGED")
        nav = row["decision_equity_usd"]
        budget = row["member_budget_usd"]
        if nav <= 0 or budget < 0 or not math.isfinite(nav):
            raise ValueError("PHASE5_CURVE_DECISION_INPUT_INVALID")
        high_water = max(high_water, nav)
        result = smooth_bounded_capital_risk_ratio(
            capital=high_water, a0=curve["a0_usd"], lower=curve["lower"],
            upper=curve["upper"], curvature=curve["curvature"])
        ratio = float(result["risk_ratio"])
        cap = nav * ratio
        if not math.isclose(float(result["risk_capital"]), high_water * ratio,
                            rel_tol=0, abs_tol=1e-7):
            raise ValueError("PHASE5_CURVE_REFERENCE_AMOUNT_INVALID")
        references.append(high_water)
        navs.append(nav)
        ratios.append(ratio)
        caps.append(cap)
        budgets.append(budget)
        slacks.append(cap - budget)
        cash.append(row["settled_cash_usd"])
        restricted.append(row["restricted_paid_cash_usd"])
        pending.append(row["pending_sale_usd"])
        receivable.append(row["receivable_usd"])
        reserve.append(row["member_reserved_cash_at_open_usd"])
        security.append(row["actual_security_value_to_nav"])
        prior_date = row["date"]
    return {
        "first_signal_wealth_reference_usd": references[0],
        "first_signal_decision_nav_usd": navs[0],
        "first_signal_ratio": ratios[0],
        "first_signal_current_cap_usd": caps[0],
        "first_signal_reference_amount_usd": references[0] * ratios[0],
        "first_signal_recorded_member_budget_usd": budgets[0],
        "last_signal_wealth_reference_usd": references[-1],
        "last_signal_decision_nav_usd": navs[-1],
        "last_signal_ratio": ratios[-1],
        "last_signal_current_cap_usd": caps[-1],
        "last_signal_reference_amount_usd": references[-1] * ratios[-1],
        "last_signal_recorded_member_budget_usd": budgets[-1],
        "minimum_cap_minus_budget_usd": min(slacks),
        "minimum_cap_minus_budget_to_decision_nav": min(
            gap / nav for gap, nav in zip(slacks, navs, strict=True)),
        "binding_sessions": sum(gap <= 1e-7 for gap in slacks),
        "mean_post_execution_settled_cash_usd": statistics.mean(cash),
        "mean_post_execution_restricted_paid_cash_usd": statistics.mean(restricted),
        "mean_post_execution_pending_sale_usd": statistics.mean(pending),
        "mean_post_execution_receivable_usd": statistics.mean(receivable),
        "mean_member_reserved_cash_at_open_usd": statistics.mean(reserve),
        "mean_actual_security_value_to_nav": statistics.mean(security),
    }


def analyze(root: Path) -> dict:
    policy = _policy(root)
    source = root / "phase5_capital_scale_v1" / "phase5_capital_scale_summary.v1.json"
    if _sha(source) != policy["source_phase5_summary_sha256"]:
        raise ValueError("PHASE5_SCALE_SUMMARY_CHANGED")
    summary = json.loads(source.read_text())
    if (summary["development"] is not True or summary["research_only"] is not True
            or len(summary["results"]) != 24):
        raise ValueError("PHASE5_SCALE_IDENTITY_CHANGED")
    paths = {}
    for principal in (1000, 1250, 10000, 100000):
        for cost in (5, 10, 15):
            paths[f"capital_{principal}_{cost}bps"] = _diagnose(
                _ledger(root, principal, cost, summary), principal, policy["curve"])
    return {"schema": policy["schema"], "study_id": policy["study_id"],
            "research_only": True, "development": True,
            "diagnostic_policy_sha256": _sha(POLICY),
            "source_phase5_summary_sha256": _sha(source),
            "source_s4_policy_sha256": policy["source_s4_policy_sha256"],
            "covered_member_set_M": policy["covered_member_set_M"],
            "curve_applied_to_strategy": False,
            "paths": paths}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = analyze(args.private_root)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        if args.output.parent != args.private_root / "phase5_capital_scale_v1":
            raise ValueError("PHASE5_DIAGNOSTIC_OUTPUT_OUTSIDE_PRIVATE_ROOT")
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(rendered)
        print(json.dumps({"schema": result["schema"], "paths": len(result["paths"]),
                          "output_sha256": _sha(args.output)}, sort_keys=True))
    else:
        print(rendered, end="")
