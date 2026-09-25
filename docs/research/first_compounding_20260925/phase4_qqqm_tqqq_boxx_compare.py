"""Frozen Phase 4 three-asset development comparison from approved private inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path

from boxx_outer_cash_compare import _decision, _load

HERE = Path(__file__).resolve().parent
POLICY = HERE / "phase4_qqqm_tqqq_boxx_policy.v3.json"
POLICY_SHA = "95d19f727a5e7cae1d9ab5a4251d260acca0f5370ef075c85312a06887e2e20e"
SYMBOLS = ("TQQQ", "QQQM", "BOXX")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _policy() -> dict:
    if _sha(POLICY) != POLICY_SHA:
        raise ValueError("PHASE4_POLICY_CHANGED")
    policy = json.loads(POLICY.read_text())
    if policy["schema"] != "qsl.research.phase4_qqqm_tqqq_boxx_policy.v3":
        raise ValueError("PHASE4_POLICY_SCHEMA_CHANGED")
    if policy["research_only"] is not True or policy["development"] is not True:
        raise ValueError("PHASE4_RESEARCH_IDENTITY_CHANGED")
    if any(policy[key] is not False for key in
           ("paper_authorized", "shadow_authorized", "live_authorized")):
        raise ValueError("PHASE4_EXECUTION_AUTHORITY_INVALID")
    if policy["capital_curve_policy"] != "not_applied_to_this_fixed_budget_pair":
        raise ValueError("PHASE4_UNREVIEWED_CAPITAL_CURVE")
    for path in policy["paths"].values():
        weights = [path[key] for key in (
            "qqqm_target_nav_fraction", "tqqq_member_budget_cap_nav_fraction",
            "boxx_target_nav_fraction", "outer_settled_cash_target_nav_fraction")]
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool)
                   and math.isfinite(x) and x >= 0 for x in weights):
            raise ValueError("PHASE4_INVALID_WEIGHTS")
        if not math.isclose(math.fsum(weights), 1.0, rel_tol=0, abs_tol=1e-12):
            raise ValueError("PHASE4_WEIGHTS_DO_NOT_CLOSE")
    return policy


def _events(actions: dict) -> dict[str, dict[str, list[dict]]]:
    events: dict[str, dict[str, list[dict]]] = {}
    for symbol in SYMBOLS:
        dividends: dict[str, list[dict]] = defaultdict(list)
        splits: dict[str, list[dict]] = defaultdict(list)
        source = actions[symbol]
        for event in source.get("cash_dividends", []):
            if event.get("symbol") != symbol or not all(event.get(key) for key in
                ("ex_date", "payable_date", "process_date")):
                raise ValueError("PHASE4_ACTION_AVAILABILITY_MISSING")
            ex_day = date.fromisoformat(event["ex_date"])
            process = date.fromisoformat(event["process_date"])
            payable = date.fromisoformat(event["payable_date"])
            if payable < ex_day or float(event["rate"]) < 0:
                raise ValueError("PHASE4_ACTION_CHRONOLOGY_INVALID")
            dividends[event["ex_date"]].append(event)
        for event in source.get("forward_splits", []):
            ex_day = date.fromisoformat(event["ex_date"])
            ratio = float(event["new_rate"]) / float(event["old_rate"])
            if ratio <= 0 or not math.isfinite(ratio) or splits[ex_day.isoformat()]:
                raise ValueError("PHASE4_SPLIT_INVALID")
            splits[ex_day.isoformat()].append(event)
        events[symbol] = {"dividends": dict(dividends), "splits": dict(splits)}
    return events


def _unrecognized_claims(state: dict, signal_day: str) -> float:
    return math.fsum(claim["amount"] for claim in state["claims"]
                     if claim["process_date"] >= signal_day)


def _decision_equity(state: dict, signal_day: str) -> float:
    equity = state["nav"] - _unrecognized_claims(state, signal_day)
    if equity <= 0 or not math.isfinite(equity):
        raise ValueError("PHASE4_DECISION_EQUITY_INVALID")
    return equity


def _due_events(state: dict, day: str, index: int) -> float:
    """Release prior sales and account for paid claims; no new sale is reusable."""
    released = math.fsum(item["amount"] for item in state["pending"]
                         if item["release"] <= index)
    state["cash"] += released
    state["pending"] = [item for item in state["pending"] if item["release"] > index]
    for claim in state["claims"]:
        if not claim["paid"] and claim["payable_date"] <= day:
            state["cash"] += claim["amount"]
            claim["paid"] = True
    return released


def _restricted_paid_cash(state: dict, signal_day: str) -> float:
    return math.fsum(claim["amount"] for claim in state["claims"]
                     if claim["paid"] and claim["process_date"] >= signal_day)


def _receivable(state: dict) -> float:
    return math.fsum(claim["amount"] for claim in state["claims"] if not claim["paid"])


def _member_reserved_cash(state: dict, budget: float, tqqq_open: float,
                          signal_day: str) -> float:
    member_pending = math.fsum(item["amount"] for item in state["pending"]
                               if item["symbol"] == "TQQQ")
    member_receivable = math.fsum(claim["amount"] for claim in state["claims"]
                                  if claim["symbol"] == "TQQQ" and not claim["paid"]
                                  and claim["process_date"] < signal_day)
    return max(0.0, budget - state["shares"]["TQQQ"] * tqqq_open
               - member_pending - member_receivable)


def _execute(state: dict, row: dict, *, signal_day: str, index: int,
             budget: float, target_usd: dict[str, float], cash_target: float,
             fee_rate: float) -> dict:
    opens = {symbol: row[symbol.lower() + "_open"] for symbol in SYMBOLS}
    targets = {symbol: math.floor(target_usd[symbol] / opens[symbol])
               for symbol in SYMBOLS}
    trades = {symbol: 0.0 for symbol in SYMBOLS}
    costs = {symbol: 0.0 for symbol in SYMBOLS}
    # Sales are placed into a dated queue and never reused at this open.
    for symbol in SYMBOLS:
        quantity = max(0.0, state["shares"][symbol] - targets[symbol])
        if quantity:
            state["shares"][symbol] -= quantity
            trades[symbol] -= quantity
            cost = quantity * opens[symbol] * fee_rate
            costs[symbol] += cost
            state["pending"].append({"release": index + 2,
                "amount": quantity * opens[symbol] - cost, "symbol": symbol})
    restricted = _restricted_paid_cash(state, signal_day)
    if restricted > state["cash"] + 1e-7:
        raise ValueError("PHASE4_RESTRICTED_CASH_IDENTITY")
    for symbol in SYMBOLS:
        quantity = max(0.0, targets[symbol] - state["shares"][symbol])
        member_reserve = (0.0 if symbol == "TQQQ" else
                          _member_reserved_cash(state, budget, opens["TQQQ"], signal_day))
        available = max(0.0, state["cash"] - restricted - cash_target - member_reserve)
        fill = min(quantity, math.floor((available + 1e-9) /
                                        (opens[symbol] * (1.0 + fee_rate))))
        if fill:
            state["shares"][symbol] += fill
            trades[symbol] += fill
            cost = fill * opens[symbol] * fee_rate
            costs[symbol] += cost
            state["cash"] -= fill * opens[symbol] + cost
    if state["cash"] < -1e-7:
        raise ValueError("PHASE4_NEGATIVE_CASH")
    return {"trades": trades, "costs": costs,
            "member_reserved_cash_at_open_usd": _member_reserved_cash(
                state, budget, opens["TQQQ"], signal_day),
            "restricted_paid_cash_at_open_usd": restricted}


def _metrics(ledger: list[dict], initial: float, initial_date: str) -> dict:
    peak = initial
    peak_index = -1
    worst_ratio = 0.0
    worst_amount = 0.0
    worst_peak_index = -1
    worst_trough_index = -1
    max_dollar_loss = 0.0
    for index, row in enumerate(ledger):
        nav = row["economic_nav_close_usd"]
        if nav > peak:
            peak = nav
            peak_index = index
        ratio = nav / peak - 1.0
        amount = peak - nav
        if ratio < worst_ratio:
            worst_ratio, worst_amount = ratio, amount
            worst_peak_index, worst_trough_index = peak_index, index
        max_dollar_loss = max(max_dollar_loss, amount)
    recovered_index = next((j for j in range(worst_trough_index + 1, len(ledger))
                            if ledger[j]["economic_nav_close_usd"] >=
                            (initial if worst_peak_index == -1 else
                             ledger[worst_peak_index]["economic_nav_close_usd"])), None) if worst_trough_index >= 0 else None
    n = len(ledger)
    daily_returns = []
    prev = initial
    for row in ledger:
        daily_returns.append(row["economic_nav_close_usd"] / prev - 1.0)
        prev = row["economic_nav_close_usd"]
    return {
        "sessions": n, "start": ledger[0]["date"], "end": ledger[-1]["date"],
        "initial_nav_usd": initial, "end_nav_usd": prev,
        "cumulative_return": prev / initial - 1.0,
        "cagr_252_sessions": (prev / initial) ** (252 / n) - 1.0,
        "max_drawdown": worst_ratio, "dollar_loss_at_max_drawdown_usd": worst_amount,
        "maximum_peak_to_trough_dollar_loss_usd": max_dollar_loss,
        "worst_drawdown_peak": ("INITIAL" if worst_peak_index == -1 else
                                ledger[worst_peak_index]["date"]),
        "worst_drawdown_trough": (None if worst_trough_index < 0 else
                                  ledger[worst_trough_index]["date"]),
        "worst_drawdown_recovered": (None if recovered_index is None else
                                     ledger[recovered_index]["date"]),
        "peak_to_recovery_sessions": (None if recovered_index is None else
                                       recovered_index - worst_peak_index),
        "peak_to_recovery_calendar_days": (None if recovered_index is None else
                                            (date.fromisoformat(ledger[recovered_index]["date"]) -
                                             date.fromisoformat(initial_date if worst_peak_index == -1 else
                                                                 ledger[worst_peak_index]["date"])).days),
        "total_cost_usd": math.fsum(row["total_cost_usd"] for row in ledger),
        "average_actual_security_value_to_nav": statistics.mean(
            row["actual_security_value_to_nav"] for row in ledger),
        "average_nasdaq_lookthrough_to_nav": statistics.mean(
            row["nasdaq_lookthrough_to_nav"] for row in ledger),
        "maximum_nasdaq_lookthrough_to_nav": max(
            row["nasdaq_lookthrough_to_nav"] for row in ledger),
        "average_tqqq_value_to_nav": statistics.mean(
            row["tqqq_value_usd"] / row["economic_nav_close_usd"] for row in ledger),
        "average_qqqm_value_to_nav": statistics.mean(
            row["qqqm_value_usd"] / row["economic_nav_close_usd"] for row in ledger),
        "average_boxx_value_to_nav": statistics.mean(
            row["boxx_value_usd"] / row["economic_nav_close_usd"] for row in ledger),
        "average_settled_cash_to_nav": statistics.mean(
            row["settled_cash_usd"] / row["economic_nav_close_usd"] for row in ledger),
        "average_outer_free_settled_cash_to_nav": statistics.mean(
            row["outer_free_settled_cash_usd"] / row["economic_nav_close_usd"]
            for row in ledger),
        "average_pending_sale_to_nav": statistics.mean(
            row["pending_sale_usd"] / row["economic_nav_close_usd"] for row in ledger),
        "average_receivable_to_nav": statistics.mean(
            row["receivable_usd"] / row["economic_nav_close_usd"] for row in ledger),
        "minimum_decision_equity_usd": min(row["decision_equity_usd"] for row in ledger),
        "maximum_unrecognized_claim_usd": max(row["unrecognized_claim_usd"] for row in ledger),
        "max_account_identity_error_usd": max(row["account_identity_error_usd"] for row in ledger),
    }


def _replay(rows: list[dict], events: dict, policy: dict, contract: dict,
            *, path_name: str, cost_bps: int) -> tuple[dict, list[dict]]:
    path = policy["paths"][path_name]
    by_date = {row["date"]: i for i, row in enumerate(rows)}
    start = by_date[policy["data_window"]["first_trade"]]
    if rows[start - 1]["date"] != policy["data_window"]["first_signal"]:
        raise ValueError("PHASE4_SIGNAL_WINDOW_CHANGED")
    initial = float(policy["initial_research_nav_usd"])
    state = {"cash": initial, "shares": {symbol: 0.0 for symbol in SYMBOLS},
             "pending": [], "claims": [], "nav": initial}
    ledger = []
    for i in range(start, len(rows)):
        signal_index = i - 1
        signal_day = rows[signal_index]["date"]
        decision_equity = _decision_equity(state, signal_day)
        budget = decision_equity * path["tqqq_member_budget_cap_nav_fraction"]
        signal = None
        if budget > 0:
            signal = _decision(rows, signal_index, shares=state["shares"]["TQQQ"],
                               nav=decision_equity, use_guard=True,
                               config=contract, member_budget_usd=budget)
            if signal["signal_date"] != signal_day or signal["target_tqqq_usd"] > budget + 1e-7:
                raise ValueError("PHASE4_CORE_DECISION_MISMATCH")
        target_usd = {"TQQQ": (0.0 if signal is None else signal["target_tqqq_usd"]),
            "QQQM": decision_equity * path["qqqm_target_nav_fraction"],
            "BOXX": decision_equity * path["boxx_target_nav_fraction"]}
        cash_target = decision_equity * path["outer_settled_cash_target_nav_fraction"]
        row = rows[i]
        prior_nav = state["nav"]
        old_shares = dict(state["shares"])
        split_ratios = {symbol: 1.0 for symbol in SYMBOLS}
        accrued = 0.0
        for symbol in SYMBOLS:
            split_events = events[symbol]["splits"].get(row["date"], [])
            if split_events:
                event = split_events[0]
                ratio = float(event["new_rate"]) / float(event["old_rate"])
                split_ratios[symbol] = ratio
                state["shares"][symbol] *= ratio
            for event in events[symbol]["dividends"].get(row["date"], []):
                amount = state["shares"][symbol] * float(event["rate"])
                if amount:
                    state["claims"].append({"symbol": symbol, "amount": amount,
                        "process_date": event["process_date"],
                        "payable_date": event["payable_date"], "paid": False})
                    accrued += amount
        released = _due_events(state, row["date"], i)
        execution = _execute(state, row, signal_day=signal_day, index=i,
            budget=budget, target_usd=target_usd, cash_target=cash_target,
            fee_rate=cost_bps / 10000.0)
        values = {symbol: state["shares"][symbol] * row[symbol.lower() + "_close"]
                  for symbol in SYMBOLS}
        receivable = _receivable(state)
        pending = math.fsum(item["amount"] for item in state["pending"])
        economic_nav = math.fsum((state["cash"], pending, receivable, *values.values()))
        expected = prior_nav + accrued - math.fsum(execution["costs"].values())
        for symbol in SYMBOLS:
            key = symbol.lower()
            expected += old_shares[symbol] * (
                split_ratios[symbol] * row[key + "_close"] - rows[i - 1][key + "_close"])
            expected += execution["trades"][symbol] * (row[key + "_close"] - row[key + "_open"])
        identity_error = abs(economic_nav - expected)
        if identity_error > 1e-6 or economic_nav <= 0 or not math.isfinite(economic_nav):
            raise ValueError("PHASE4_ACCOUNT_IDENTITY_FAILED:" + row["date"])
        state["nav"] = economic_nav
        unrecognized = _unrecognized_claims(state, row["date"])
        if unrecognized > economic_nav + 1e-6:
            raise ValueError("PHASE4_UNRECOGNIZED_CLAIM_EXCEEDS_NAV")
        ledger.append({"date": row["date"], "signal_date": signal_day,
            "candidate_id": policy["candidate_id"] if path_name == "enhanced" else policy["comparator_id"],
            "guard_route": None if signal is None else signal["guard_route"],
            "core_state": None if signal is None else signal["core_state"],
            "decision_equity_usd": decision_equity,
            "unrecognized_claim_at_signal_usd": prior_nav - decision_equity,
            "member_budget_usd": budget, "target_usd": target_usd,
            "cash_target_usd": cash_target, "trade_shares": execution["trades"],
            "shares": dict(state["shares"]),
            "tqqq_value_usd": values["TQQQ"], "qqqm_value_usd": values["QQQM"],
            "boxx_value_usd": values["BOXX"],
            "settled_cash_usd": state["cash"],
            "restricted_paid_cash_usd": _restricted_paid_cash(state, row["date"]),
            "member_reserved_cash_at_open_usd": execution["member_reserved_cash_at_open_usd"],
            "outer_free_settled_cash_usd": max(0.0, state["cash"] -
                _restricted_paid_cash(state, row["date"]) -
                _member_reserved_cash(state, budget, row["tqqq_close"], row["date"])),
            "pending_sale_usd": pending, "receivable_usd": receivable,
            "unrecognized_claim_usd": unrecognized,
            "dividend_accrued_usd": accrued, "sale_proceeds_released_usd": released,
            "trade_cost_usd": execution["costs"],
            "total_cost_usd": math.fsum(execution["costs"].values()),
            "economic_nav_close_usd": economic_nav,
            "actual_security_value_to_nav": math.fsum(values.values()) / economic_nav,
            "nasdaq_lookthrough_to_nav": (values["QQQM"] + 3.0 * values["TQQQ"]) / economic_nav,
            "account_identity_error_usd": identity_error})
    if len(ledger) != 444 or ledger[-1]["date"] != policy["data_window"]["last_session"]:
        raise ValueError("PHASE4_COMMON_WINDOW_CHANGED")
    return _metrics(ledger, initial, policy["data_window"]["first_signal"]), ledger


def analyze(root: Path) -> tuple[dict, dict[str, list[dict]]]:
    policy = _policy()
    old_policy, _, contract, rows, actions = _load(root)
    if (_sha(root / "manifest.json") != policy["source_manifest_sha256"]
            or _sha(root / "tqqq_qqq_guard_cash_contract.v1.json") != policy["core_contract_sha256"]
            or old_policy["core_candidate_id"] != policy["core_candidate_id"]):
        raise ValueError("PHASE4_SOURCE_IDENTITY_MISMATCH")
    events = _events(actions)
    results = {}
    ledgers = {}
    for cost in policy["cost_model"]["trade_cost_bps_scenarios"]:
        for path_name in ("enhanced", "matched_defense_baseline"):
            key = f"{path_name}_{cost}bps"
            results[key], ledgers[key] = _replay(
                rows, events, policy, contract, path_name=path_name, cost_bps=cost)
    pairs = {}
    for cost in policy["cost_model"]["trade_cost_bps_scenarios"]:
        enhanced = results[f"enhanced_{cost}bps"]
        baseline = results[f"matched_defense_baseline_{cost}bps"]
        pairs[f"{cost}bps"] = {
            "enhanced_minus_baseline_log_terminal_wealth": math.log(
                enhanced["end_nav_usd"] / baseline["end_nav_usd"]),
            "enhanced_minus_baseline_cagr": enhanced["cagr_252_sessions"] - baseline["cagr_252_sessions"],
            "enhanced_minus_baseline_max_drawdown": enhanced["max_drawdown"] - baseline["max_drawdown"],
            "enhanced_minus_baseline_total_cost_usd": enhanced["total_cost_usd"] - baseline["total_cost_usd"],
        }
    summary = {"schema": "qsl.research.phase4_qqqm_tqqq_boxx_comparison.v3",
        "research_only": True, "development": True,
        "known_at_status": "provider_process_date_delayed_recognition_proxy_not_verified_announcement",
        "policy_sha256": POLICY_SHA,
        "manifest_sha256": _sha(root / "manifest.json"),
        "core_contract_sha256": _sha(root / "tqqq_qqq_guard_cash_contract.v1.json"),
        "runner_sha256": _sha(Path(__file__)),
        "source_runner_sha256": _sha(HERE / "boxx_outer_cash_compare.py"),
        "results": results, "paired_differences": pairs,
        "limits": "Seen development; process_date is an assumed conservative availability proxy, not verified historic announcement or delivery time. BOXX is not Treasury cash. TQQQ 3x is a nominal daily lookthrough, not a realized multi-day leverage guarantee. No policy adoption, paper, shadow, live or trading authority."}
    return summary, ledgers


def run(root: Path) -> dict:
    summary, ledgers = analyze(root)
    destination = root / "phase4_fixed_pair_v3"
    destination.mkdir(mode=0o700, exist_ok=False)
    hashes = {}
    for key, ledger in sorted(ledgers.items()):
        data = (json.dumps(ledger, indent=2, sort_keys=True) + "\n").encode()
        path = destination / f"private_daily_{key}.json"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        hashes[key] = hashlib.sha256(data).hexdigest()
    summary["private_ledger_sha256"] = hashes
    path = destination / "phase4_comparison_summary.v3.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.private_root)
    print(json.dumps({"schema": report["schema"], "policy_sha256": report["policy_sha256"],
                      "summary_sha256": _sha(args.private_root / "phase4_fixed_pair_v3" /
                                             "phase4_comparison_summary.v3.json"),
                      "path_count": len(report["results"]),
                      "first_date": next(iter(report["results"].values()))["start"],
                      "last_date": next(iter(report["results"].values()))["end"]}, indent=2))
