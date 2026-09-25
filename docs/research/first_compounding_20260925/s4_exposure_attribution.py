"""One-shot, post-hoc exposure attribution using existing private S4 ledgers.

The lower fixed budget is determined only by historical mean actual stock
exposure. It is an explanatory comparator, not an ex-ante policy or tuning run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from tqqq_cash_budget_compare import _recovery
from us_equity_strategies.research.tqqq_qqq_guard_cash_research import (
    _decision,
    _simulate,
    _validated_bars,
    _verified_bundle,
)

POLICY_SHA256 = "842622d795f2a33633314d91d3ea1a89bc31c0e1401c6e1c47a724a1b94bd4d1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mean_exposure(ledger: list[dict]) -> float:
    return sum(row["shares"] * row["tqqq_close"] / row["nav_close"] for row in ledger) / len(ledger)


def _summary(ledger: list[dict], initial: float) -> dict:
    prior_nav = initial
    prior_receivable = 0.0
    planned_outer_cash_ratios = []
    for row in ledger:
        planned_outer_cash_ratios.append(
            (prior_nav - prior_receivable - row["member_budget_usd"]) / prior_nav)
        prior_nav, prior_receivable = row["nav_close"], row["receivable_usd"]
    return {"mean_actual_tqqq_exposure": _mean_exposure(ledger),
            "mean_cash_ratio": sum(row["cash_usd"] / row["nav_close"] for row in ledger) / len(ledger),
            "mean_receivable_ratio": sum(row["receivable_usd"] / row["nav_close"] for row in ledger) / len(ledger),
            "mean_member_budget_ratio": sum(row["member_budget_ratio"] for row in ledger) / len(ledger),
            "mean_planned_member_internal_cash_ratio": sum(
                row["member_budget_ratio"] - row["target_tqqq_ratio"] for row in ledger) / len(ledger),
            "mean_planned_outer_cash_budget_ratio": sum(planned_outer_cash_ratios) / len(ledger),
            "guard_route_counts": dict(Counter(row["signal_guard_route"] for row in ledger)),
            "zero_budget_days": sum(row["member_budget_usd"] == 0 for row in ledger),
            "zero_stock_days": sum(row["shares"] == 0 for row in ledger),
            "recovery": _recovery(ledger, initial)}


def _core_state_counts(rows: list[dict], contract: dict, ledger: list[dict]) -> dict:
    counts = Counter()
    prior_nav = float(contract["portfolio"]["research_initial_usd"])
    prior_shares = 0.0
    for offset, row in enumerate(ledger):
        signal_index = 256 + offset
        decision = _decision(rows, signal_index, shares=prior_shares, nav=prior_nav,
                             use_guard=True, config=contract,
                             member_budget_usd=row["member_budget_usd"])
        if decision["signal_date"] != row["signal_date"] or abs(
            decision["target_tqqq_ratio"] - row["target_tqqq_ratio"]
        ) > 1e-10:
            raise ValueError("STORED_LEDGER_DECISION_MISMATCH")
        counts[f"core_{decision['core_state']}"] += 1
        if decision["volatility_applied"]:
            counts["core_volatility_reduction_applied"] += 1
        if row["member_budget_usd"] == 0:
            full = _decision(rows, signal_index, shares=prior_shares, nav=prior_nav,
                             use_guard=True, config=contract, member_budget_usd=prior_nav)
            counts["zero_budget_when_core_already_zero" if full["target_tqqq_usd"] == 0
                   else "zero_budget_when_core_positive"] += 1
        prior_nav, prior_shares = row["nav_close"], row["shares"]
    return dict(counts)


def run(root: Path) -> dict:
    policy_path = root / "s4_exposure_attribution_policy.v1.json"
    if policy_path.is_symlink() or _sha(policy_path) != POLICY_SHA256:
        raise ValueError("ATTRIBUTION_POLICY_CHANGED")
    policy = json.loads(policy_path.read_text())
    if policy["max_new_replays"] != 1 or policy["research_cost_bps"] != 5:
        raise ValueError("ATTRIBUTION_RUN_BOUNDS_CHANGED")
    output = root / "s4_exposure_attribution_summary.v1.json"
    daily_output = root / "private_daily_s4_lower_fixed_5bps.json"
    if output.exists() or daily_output.exists():
        raise ValueError("ONE_SHOT_ATTRIBUTION_ALREADY_RUN")
    existing = {}
    for name, digest in policy["source_ledgers"].items():
        path = root / f"private_daily_s4_{name}.json"
        if _sha(path) != digest:
            raise ValueError("EXISTING_S4_LEDGER_CHANGED")
        existing[name] = json.loads(path.read_text())
    if {len(value) for value in existing.values()} != {496}:
        raise ValueError("ATTRIBUTION_WINDOW_CHANGED")
    fixed = existing["fixed_member_budget_cash_5bps"]
    automatic = existing["automatic_member_budget_cash_5bps"]
    if [row["date"] for row in fixed] != [row["date"] for row in automatic]:
        raise ValueError("ATTRIBUTION_DATES_DIFFER")
    target_exposure = _mean_exposure(automatic)
    reference_exposure = _mean_exposure(fixed)
    ratio = round(0.5 * target_exposure / reference_exposure, 2)
    if ratio != policy["fixed_member_budget_ratio"] or abs(
        target_exposure - policy["target_mean_actual_stock_exposure"]
    ) > 1e-9:
        raise ValueError("ATTRIBUTION_CALIBRATION_CHANGED")
    contract, qqq, tqqq, actions = _verified_bundle(root)
    rows = _validated_bars(qqq, tqqq)
    metrics, lower = _simulate(
        rows, actions, contract, cost_bps=5, use_guard=True,
        budget_selector=lambda _prefix, *, nav, **_kwargs: ratio * nav,
    )
    if [row["date"] for row in lower] != [row["date"] for row in automatic] or [
        row["signal_guard_route"] for row in lower
    ] != [row["signal_guard_route"] for row in automatic]:
        raise ValueError("LOWER_FIXED_IDENTITY_OR_WINDOW_CHANGED")
    match_error = _mean_exposure(lower) - target_exposure
    daily_output.write_text(json.dumps(lower, separators=(",", ":")) + "\n")
    daily_output.chmod(0o600)
    report = {"candidate_id": policy["candidate_id"],
              "status": "post_hoc_attribution_only_not_ex_ante_policy_or_optimum",
              "cost_bps": 5, "sessions": len(lower),
              "attribution_policy_sha256": POLICY_SHA256,
              "source_s4_ledgers_sha256": policy["source_ledgers"],
              "lower_fixed_member_budget_ratio": ratio,
              "target_automatic_mean_stock_exposure": target_exposure,
              "lower_fixed_mean_stock_exposure": _mean_exposure(lower),
              "exposure_match_error_ratio": match_error,
              "exposure_tolerance_ratio": policy["absolute_exposure_tolerance_ratio"],
              "exposure_tolerance_met": abs(match_error) <= policy["absolute_exposure_tolerance_ratio"],
              "new_replays_used": 1,
              "outcomes": {
                  "fixed_50pct": {"metrics": json.loads((root / "s4_budget_comparison_summary.v1.json").read_text())["outcomes"]["fixed_member_budget_cash_5bps"],
                                  "exposure": _summary(fixed, 10000),
                                  "core_states": _core_state_counts(rows, contract, fixed)},
                  "automatic": {"metrics": json.loads((root / "s4_budget_comparison_summary.v1.json").read_text())["outcomes"]["automatic_member_budget_cash_5bps"],
                                "exposure": _summary(automatic, 10000),
                                "core_states": _core_state_counts(rows, contract, automatic)},
                  "lower_fixed": {"metrics": {**metrics, **_recovery(lower, 10000),
                                              "average_cash_ratio": sum(row["cash_ratio"] for row in lower) / len(lower)},
                                  "exposure": _summary(lower, 10000),
                                  "core_states": _core_state_counts(rows, contract, lower)},
              },
              "interpretation_limit": "Uses mean exposure from this same seen-development window to set one fixed budget. No ex-ante, OOS, optimal or adoption claim."}
    output.write_text(json.dumps(report, indent=2) + "\n")
    output.chmod(0o600)
    return {"ratio": ratio, "sessions": len(lower),
            "exposure_match_error_ratio": match_error,
            "tolerance_met": report["exposure_tolerance_met"], "summary": str(output)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.private_root), indent=2))


if __name__ == "__main__":
    main()
