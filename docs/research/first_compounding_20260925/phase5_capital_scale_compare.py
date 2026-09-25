"""Bounded capital-scale replay of the frozen Phase 4 development pair."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path

from phase4_qqqm_tqqq_boxx_compare import (
    HERE, _events, _load, _policy as phase4_policy, _replay,
)

POLICY = HERE / "phase5_capital_scale_policy.v1.json"
POLICY_SHA = "1321d29984780bd7643fcf1000ccad8934c0b20300fffaac717f2dbf80fb5058"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _study_policy() -> dict:
    if _sha(POLICY) != POLICY_SHA:
        raise ValueError("PHASE5_POLICY_CHANGED")
    study = json.loads(POLICY.read_text())
    if (study["schema"] != "qsl.research.phase5_capital_scale_policy.v1"
            or study["research_only"] is not True or study["development"] is not True
            or any(study[key] is not False for key in
                   ("paper_authorized", "shadow_authorized", "live_authorized"))
            or study["initial_research_principal_usd"] != [1000, 1250, 10000, 100000]
            or study["cost_bps"] != [5, 10, 15]
            or study["fixed_commission_usd"] != 0):
        raise ValueError("PHASE5_STUDY_IDENTITY_CHANGED")
    return study


def _activation(ledger: list[dict], rows_by_day: dict[str, dict]) -> dict:
    held = 0
    first_purchase = None
    positive_target_without_shares = 0
    tqqq_target_share_shortfall = 0
    qqqm_target_share_shortfall = 0
    boxx_target_share_shortfall = 0
    mean_tqqq_integer_gap_usd = 0.0
    for row in ledger:
        day = row["date"]
        prices = rows_by_day[day]
        if row["shares"]["TQQQ"] > 0:
            held += 1
        if row["trade_shares"]["TQQQ"] > 0 and first_purchase is None:
            first_purchase = day
        if row["target_usd"]["TQQQ"] > 0 and row["shares"]["TQQQ"] == 0:
            positive_target_without_shares += 1
        for symbol, counter_name in (("TQQQ", "tqqq"), ("QQQM", "qqqm"), ("BOXX", "boxx")):
            opening = prices[symbol.lower() + "_open"]
            target_shares = math.floor(row["target_usd"][symbol] / opening)
            if row["shares"][symbol] + 1e-9 < target_shares:
                if counter_name == "tqqq":
                    tqqq_target_share_shortfall += 1
                elif counter_name == "qqqm":
                    qqqm_target_share_shortfall += 1
                else:
                    boxx_target_share_shortfall += 1
        opening_tqqq = prices["tqqq_open"]
        mean_tqqq_integer_gap_usd += max(0.0, row["target_usd"]["TQQQ"] -
                                            math.floor(row["target_usd"]["TQQQ"] /
                                                       opening_tqqq) * opening_tqqq)
    return {
        "tqqq_held_sessions": held,
        "first_tqqq_purchase_date": first_purchase,
        "positive_tqqq_target_but_zero_shares_sessions": positive_target_without_shares,
        "tqqq_target_share_shortfall_sessions": tqqq_target_share_shortfall,
        "qqqm_target_share_shortfall_sessions": qqqm_target_share_shortfall,
        "boxx_target_share_shortfall_sessions": boxx_target_share_shortfall,
        "mean_tqqq_whole_share_rounding_gap_usd": mean_tqqq_integer_gap_usd / len(ledger),
    }


def _reused_phase4(root: Path, study: dict) -> tuple[dict, dict[str, list[dict]]]:
    path = root / "phase4_fixed_pair_v3" / "phase4_comparison_summary.v3.json"
    if _sha(path) != study["source_phase4_summary_sha256"]:
        raise ValueError("PHASE5_PHASE4_SUMMARY_CHANGED")
    report = json.loads(path.read_text())
    if (report["policy_sha256"] != study["source_phase4_policy_sha256"]
            or report["runner_sha256"] != _sha(HERE / "phase4_qqqm_tqqq_boxx_compare.py")
            or len(report["private_ledger_sha256"]) != 6):
        raise ValueError("PHASE5_PHASE4_SOURCE_MISMATCH")
    ledgers = {}
    for name, digest in report["private_ledger_sha256"].items():
        ledger_path = path.parent / f"private_daily_{name}.json"
        if _sha(ledger_path) != digest:
            raise ValueError("PHASE5_PHASE4_LEDGER_CHANGED:" + name)
        ledgers[name] = json.loads(ledger_path.read_text())
    return report, ledgers


def analyze(root: Path) -> tuple[dict, dict[str, list[dict]]]:
    study = _study_policy()
    source_policy = phase4_policy()
    if _sha(HERE / "phase4_qqqm_tqqq_boxx_policy.v3.json") != study["source_phase4_policy_sha256"]:
        raise ValueError("PHASE5_PHASE4_POLICY_CHANGED")
    old_policy, _, contract, rows, actions = _load(root)
    if old_policy["core_candidate_id"] != source_policy["core_candidate_id"]:
        raise ValueError("PHASE5_CORE_IDENTITY_MISMATCH")
    events = _events(actions)
    by_day = {row["date"]: row for row in rows}
    prior, prior_ledgers = _reused_phase4(root, study)
    results = {}
    new_ledgers = {}
    for principal in study["initial_research_principal_usd"]:
        scoped = copy.deepcopy(source_policy)
        scoped["initial_research_nav_usd"] = principal
        scoped["candidate_id"] = study["enhanced_candidate_id"]
        scoped["comparator_id"] = study["comparator_id"]
        for cost in study["cost_bps"]:
            for path_name in ("enhanced", "matched_defense_baseline"):
                name = f"{path_name}_{cost}bps"
                key = f"capital_{principal}_{name}"
                if principal == source_policy["initial_research_nav_usd"]:
                    metrics = prior["results"][name]
                    ledger = prior_ledgers[name]
                    if metrics["initial_nav_usd"] != principal:
                        raise ValueError("PHASE5_REUSED_PRINCIPAL_MISMATCH")
                    source = "reused_phase4_v3_sha_bound"
                else:
                    metrics, ledger = _replay(
                        rows, events, scoped, contract, path_name=path_name, cost_bps=cost)
                    new_ledgers[key] = ledger
                    source = "new_independent_account_recursion"
                results[key] = {"source": source, "metrics": metrics,
                                "activation": _activation(ledger, by_day)}
    pairs = {}
    for principal in study["initial_research_principal_usd"]:
        for cost in study["cost_bps"]:
            a = results[f"capital_{principal}_enhanced_{cost}bps"]["metrics"]
            b = results[f"capital_{principal}_matched_defense_baseline_{cost}bps"]["metrics"]
            pairs[f"capital_{principal}_{cost}bps"] = {
                "enhanced_minus_baseline_log_terminal_wealth": math.log(a["end_nav_usd"] / b["end_nav_usd"]),
                "enhanced_minus_baseline_cagr": a["cagr_252_sessions"] - b["cagr_252_sessions"],
                "enhanced_minus_baseline_max_drawdown": a["max_drawdown"] - b["max_drawdown"],
                "enhanced_minus_baseline_total_cost_usd": a["total_cost_usd"] - b["total_cost_usd"],
            }
    summary = {"schema": "qsl.research.phase5_capital_scale_comparison.v1",
        "study_id": study["study_id"], "development": True, "research_only": True,
        "policy_sha256": POLICY_SHA,
        "source_phase4_policy_sha256": study["source_phase4_policy_sha256"],
        "source_phase4_summary_sha256": study["source_phase4_summary_sha256"],
        "source_phase4_runner_sha256": _sha(HERE / "phase4_qqqm_tqqq_boxx_compare.py"),
        "runner_sha256": _sha(Path(__file__)),
        "source_manifest_sha256": _sha(root / "manifest.json"),
        "results": results, "paired_differences": pairs,
        "limits": "Seen development; process-date delayed recognition is a research proxy, not verified announcement known-at. Nonzero fixed commission, account-specific order minima, locked starting assets, external flows, capital curve and W_ref are not tested. No personal capital rule or execution authority."}
    return summary, new_ledgers


def run(root: Path) -> dict:
    summary, ledgers = analyze(root)
    destination = root / "phase5_capital_scale_v1"
    destination.mkdir(mode=0o700, exist_ok=False)
    hashes = {}
    for name, ledger in sorted(ledgers.items()):
        data = (json.dumps(ledger, indent=2, sort_keys=True) + "\n").encode()
        path = destination / f"private_daily_{name}.json"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    summary["private_new_ledger_sha256"] = hashes
    output = destination / "phase5_capital_scale_summary.v1.json"
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.private_root)
    print(json.dumps({"schema": report["schema"], "policy_sha256": report["policy_sha256"],
        "summary_sha256": _sha(args.private_root / "phase5_capital_scale_v1" /
                               "phase5_capital_scale_summary.v1.json"),
        "result_paths": len(report["results"]),
        "new_ledgers": len(report["private_new_ledger_sha256"])}, indent=2))
