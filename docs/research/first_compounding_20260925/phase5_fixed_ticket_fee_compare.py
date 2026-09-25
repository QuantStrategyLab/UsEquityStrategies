"""Bounded fixed-ticket-fee sensitivity using the frozen Phase 4 account replay."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from contextlib import contextmanager
from pathlib import Path

import phase4_qqqm_tqqq_boxx_compare as phase4

HERE = Path(__file__).resolve().parent
POLICY = HERE / "phase5_fixed_ticket_fee_policy.v1.json"
POLICY_SHA = "dc94e426bfe0e48a59465898ab3dac2b8c32a7b0537c92fd5742bf85aba20809"
SYMBOLS = ("TQQQ", "QQQM", "BOXX")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _policy(root: Path) -> dict:
    if _sha(POLICY) != POLICY_SHA:
        raise ValueError("PHASE5_TICKET_POLICY_CHANGED")
    policy = json.loads(POLICY.read_text())
    if (policy["schema"] != "qsl.research.phase5_fixed_ticket_fee_policy.v1"
            or policy["research_only"] is not True or policy["development"] is not True
            or any(policy[key] is not False for key in
                   ("paper_authorized", "shadow_authorized", "live_authorized"))
            or policy["initial_research_principal_usd"] != [1000, 1250, 10000]
            or policy["fixed_fee_usd_per_executed_ticket"] != 1
            or policy["cost_bps_per_side"] != 10
            or _sha(HERE / "phase4_qqqm_tqqq_boxx_compare.py") !=
               policy["source_phase4_runner_sha256"]
            or _sha(HERE / "phase4_qqqm_tqqq_boxx_policy.v3.json") !=
               policy["source_phase4_policy_sha256"]):
        raise ValueError("PHASE5_TICKET_SOURCE_IDENTITY_CHANGED")
    source_summary = root / "phase5_capital_scale_v1" / "phase5_capital_scale_summary.v1.json"
    if _sha(source_summary) != policy["source_phase5_scale_summary_sha256"]:
        raise ValueError("PHASE5_TICKET_SCALE_SOURCE_CHANGED")
    return policy


def _execute_with_ticket_fee(state: dict, row: dict, *, signal_day: str, index: int,
                             budget: float, target_usd: dict[str, float],
                             cash_target: float, fee_rate: float,
                             fixed_fee_usd: float) -> dict:
    """Phase 4 funding order with one additional fee on each executed side."""
    if fixed_fee_usd < 0 or not math.isfinite(fixed_fee_usd):
        raise ValueError("PHASE5_FIXED_FEE_INVALID")
    opens = {symbol: row[symbol.lower() + "_open"] for symbol in SYMBOLS}
    targets = {symbol: math.floor(target_usd[symbol] / opens[symbol])
               for symbol in SYMBOLS}
    trades = {symbol: 0.0 for symbol in SYMBOLS}
    costs = {symbol: 0.0 for symbol in SYMBOLS}
    for symbol in SYMBOLS:
        quantity = max(0.0, state["shares"][symbol] - targets[symbol])
        if quantity:
            gross = quantity * opens[symbol]
            cost = gross * fee_rate + fixed_fee_usd
            proceeds = gross - cost
            if proceeds <= 0:
                raise ValueError("PHASE5_SALE_CANNOT_COVER_TICKET_FEE")
            state["shares"][symbol] -= quantity
            trades[symbol] -= quantity
            costs[symbol] += cost
            state["pending"].append({"release": index + 2,
                                     "amount": proceeds, "symbol": symbol})
    restricted = phase4._restricted_paid_cash(state, signal_day)
    if restricted > state["cash"] + 1e-7:
        raise ValueError("PHASE5_RESTRICTED_CASH_IDENTITY")
    for symbol in SYMBOLS:
        quantity = max(0.0, targets[symbol] - state["shares"][symbol])
        member_reserve = (0.0 if symbol == "TQQQ" else
                          phase4._member_reserved_cash(state, budget, opens["TQQQ"], signal_day))
        available = max(0.0, state["cash"] - restricted - cash_target - member_reserve)
        fill = 0
        if quantity and available + 1e-9 >= opens[symbol] * (1.0 + fee_rate) + fixed_fee_usd:
            fill = min(quantity, math.floor((available - fixed_fee_usd + 1e-9) /
                                            (opens[symbol] * (1.0 + fee_rate))))
        if fill:
            cost = fill * opens[symbol] * fee_rate + fixed_fee_usd
            state["shares"][symbol] += fill
            trades[symbol] += fill
            costs[symbol] += cost
            state["cash"] -= fill * opens[symbol] + cost
    if state["cash"] < -1e-7:
        raise ValueError("PHASE5_NEGATIVE_CASH")
    return {"trades": trades, "costs": costs,
            "member_reserved_cash_at_open_usd": phase4._member_reserved_cash(
                state, budget, opens["TQQQ"], signal_day),
            "restricted_paid_cash_at_open_usd": restricted}


@contextmanager
def _isolated_execution(fixed_fee_usd: float):
    """Swap only the frozen replay's execution callback in this serial CLI run."""
    original = phase4._execute

    def execute(state, row, *, signal_day, index, budget, target_usd, cash_target, fee_rate):
        return _execute_with_ticket_fee(
            state, row, signal_day=signal_day, index=index, budget=budget,
            target_usd=target_usd, cash_target=cash_target, fee_rate=fee_rate,
            fixed_fee_usd=fixed_fee_usd)

    phase4._execute = execute
    try:
        yield
    finally:
        phase4._execute = original


def _tickets(ledger: list[dict]) -> int:
    return sum(1 for row in ledger for symbol in SYMBOLS
               if row["trade_shares"][symbol] != 0)


def _zero_fee_source(root: Path, principal: int, path_name: str, summary: dict) -> tuple[dict, int]:
    name = f"{path_name}_10bps"
    key = f"capital_{principal}_{name}"
    source = summary["results"][key]
    if principal == 10000:
        phase4_summary_path = root / "phase4_fixed_pair_v3" / "phase4_comparison_summary.v3.json"
        if _sha(phase4_summary_path) != summary["source_phase4_summary_sha256"]:
            raise ValueError("PHASE5_TICKET_PHASE4_SOURCE_CHANGED")
        phase4_summary = json.loads(phase4_summary_path.read_text())
        digest = phase4_summary["private_ledger_sha256"][name]
        ledger_path = phase4_summary_path.parent / f"private_daily_{name}.json"
    else:
        digest = summary["private_new_ledger_sha256"][key]
        ledger_path = root / "phase5_capital_scale_v1" / f"private_daily_{key}.json"
    if _sha(ledger_path) != digest:
        raise ValueError("PHASE5_TICKET_ZERO_FEE_LEDGER_CHANGED")
    ledger = json.loads(ledger_path.read_text())
    if len(ledger) != 444 or source["metrics"]["initial_nav_usd"] != principal:
        raise ValueError("PHASE5_TICKET_ZERO_FEE_WINDOW_CHANGED")
    return source["metrics"], _tickets(ledger)


def analyze(root: Path) -> tuple[dict, dict[str, list[dict]]]:
    policy = _policy(root)
    source_policy = phase4._policy()
    old_policy, _, contract, rows, actions = phase4._load(root)
    if old_policy["core_candidate_id"] != source_policy["core_candidate_id"]:
        raise ValueError("PHASE5_TICKET_CORE_IDENTITY_CHANGED")
    events = phase4._events(actions)
    scale_summary = json.loads((root / "phase5_capital_scale_v1" /
                                "phase5_capital_scale_summary.v1.json").read_text())
    results = {}
    ledgers = {}
    for principal in policy["initial_research_principal_usd"]:
        scoped = copy.deepcopy(source_policy)
        scoped["initial_research_nav_usd"] = principal
        scoped["candidate_id"] = policy["enhanced_candidate_id"]
        scoped["comparator_id"] = policy["comparator_id"]
        for path_name in ("enhanced", "matched_defense_baseline"):
            name = f"capital_{principal}_{path_name}_10bps"
            old_metrics, old_tickets = _zero_fee_source(root, principal, path_name, scale_summary)
            with _isolated_execution(policy["fixed_fee_usd_per_executed_ticket"]):
                metrics, ledger = phase4._replay(
                    rows, events, scoped, contract, path_name=path_name,
                    cost_bps=policy["cost_bps_per_side"])
            tickets = _tickets(ledger)
            results[name] = {
                "fixed_ticket_fee_metrics": metrics,
                "fixed_ticket_fee_ticket_count": tickets,
                "fixed_ticket_fee_component_usd": tickets * policy["fixed_fee_usd_per_executed_ticket"],
                "zero_fixed_fee_metrics": old_metrics,
                "zero_fixed_fee_ticket_count": old_tickets,
                "delta_cumulative_return": metrics["cumulative_return"] - old_metrics["cumulative_return"],
                "delta_max_drawdown": metrics["max_drawdown"] - old_metrics["max_drawdown"],
                "delta_total_cost_usd": metrics["total_cost_usd"] - old_metrics["total_cost_usd"],
            }
            ledgers[name] = ledger
    summary = {"schema": "qsl.research.phase5_fixed_ticket_fee_comparison.v1",
               "study_id": policy["study_id"], "research_only": True, "development": True,
               "policy_sha256": POLICY_SHA,
               "source_phase4_policy_sha256": policy["source_phase4_policy_sha256"],
               "source_phase4_runner_sha256": policy["source_phase4_runner_sha256"],
               "source_phase5_scale_summary_sha256": policy["source_phase5_scale_summary_sha256"],
               "runner_sha256": _sha(Path(__file__)),
               "source_manifest_sha256": _sha(root / "manifest.json"),
               "results": results,
               "limits": "Hypothetical USD 1 executed ticket fee, not an observed broker tariff; seen development and provider-process-date availability proxy. No personal capital policy, paper/shadow/live or trading authorization."}
    return summary, ledgers


def run(root: Path) -> dict:
    summary, ledgers = analyze(root)
    destination = root / "phase5_fixed_ticket_fee_v1"
    destination.mkdir(mode=0o700, exist_ok=False)
    hashes = {}
    for name, ledger in sorted(ledgers.items()):
        data = (json.dumps(ledger, indent=2, sort_keys=True) + "\n").encode()
        path = destination / f"private_daily_{name}.json"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    summary["private_ledger_sha256"] = hashes
    output = destination / "phase5_fixed_ticket_fee_summary.v1.json"
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
        "summary_sha256": _sha(args.private_root / "phase5_fixed_ticket_fee_v1" /
                               "phase5_fixed_ticket_fee_summary.v1.json"),
        "paths": len(report["results"]), "private_ledgers": len(report["private_ledger_sha256"])},
        indent=2))
