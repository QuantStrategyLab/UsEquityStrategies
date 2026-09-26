"""Finite-action R8 development replay on the R7 executable owner account."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import statistics
from collections import Counter
from pathlib import Path

from auto_allocate import select_finite_executable_action
from r7_joint_account_compare import (
    OWNER_SYMBOLS,
    OWNERS,
    SYMBOLS,
    _book_decision_equity,
    _book_value,
    _buy_to_targets,
    _canonical,
    _decision,
    _eligible_cash,
    _events,
    _fund_owners,
    _load_inputs,
    _policy as r7_policy,
    _prior_prices,
    _release,
    _replay,
    _sell_to_targets,
    _sha,
    _soxl_signal,
)

HERE = Path(__file__).resolve().parent
POLICY_PATH = HERE / "r8_joint_allocation_policy.v2.json"
POLICY_SHA256 = "f7f4c062e952ca46a5087dc7da0f67cca3cdd7208f3d84357376deb225375dc4"


def _policy() -> dict:
    if _sha(POLICY_PATH) != POLICY_SHA256:
        raise ValueError("R8_POLICY_CHANGED")
    policy = json.loads(POLICY_PATH.read_bytes())
    if (policy["schema"] != "qsl.research.r8_joint_allocation_policy.v2"
            or policy["research_only"] is not True or policy["development"] is not True
            or any(policy[key] is not False for key in
                   ("paper_authorized", "shadow_authorized", "live_authorized"))
            or policy["action_ids"] != ["B0", "B1", "B2", "B3"]
            or policy["initial_action"] != "B0"
            or policy["cost_bps_scenarios"] != [5, 10, 15]
            or policy["estimator"]["training_observations"] != 60
            or policy["estimator"]["horizon_sessions"] != 1
            or policy["policy_revision_parent_sha256"] !=
               _sha(HERE / "r8_joint_allocation_policy.v1.json")
            or policy["startup"]["decision_dates"] != ["2023-03-27", "2023-03-28"]
            or policy["startup"]["required_action"] != "B0"
            or policy["startup"]["dynamic_first_decision_date"] != "2023-03-29"
            or policy["startup"]["first_dynamic_trade_date"] != "2023-03-30"):
        raise ValueError("R8_POLICY_IDENTITY_INVALID")
    base = r7_policy()
    if (policy["r7_policy_sha256"] != _sha(HERE / "r7_joint_account_policy.v1.json")
            or policy["initial_research_nav_usd"] != base["initial_research_nav_usd"]
            or policy["first_signal"] != base["data"]["first_signal"]
            or policy["first_trade"] != base["data"]["first_trade"]
            or policy["last_session"] != base["data"]["last_session"]
            or policy["expected_replay_sessions"] != base["data"]["expected_replay_sessions"]
            or any(base["paths"][name]["tqqq_cap"] + base["paths"][name]["soxl_cap"] > 0.05 + 1e-12
                   for name in policy["action_ids"])):
        raise ValueError("R8_R7_POLICY_MISMATCH")
    return policy


def _known_books(books: dict, signal_day: str) -> dict:
    """Remove only claims that the R7 decision equity already excludes."""
    known = copy.deepcopy(books)
    for book in known.values():
        retained = []
        for claim in book["claims"]:
            if claim["process_date"] >= signal_day:
                if claim["paid"]:
                    book["cash"] -= claim["amount"]
            else:
                retained.append(claim)
        book["claims"] = retained
        if book["cash"] < -1e-7:
            raise ValueError("R8_UNKNOWN_PAID_CASH_EXCEEDS_BALANCE")
    return known


def _historical_scenarios(rows: list[dict], actions: dict, decision_index: int,
                          *, count: int = 60) -> list[dict]:
    """Keep paired sessions and event-normalize old raw units without future events."""
    signal_day = rows[decision_index]["date"]
    events = _events(actions)
    eligible = []
    for index in range(1, decision_index + 1):
        day = rows[index]["date"]
        if (all(symbol.lower() + "_open" in rows[index]
                and symbol.lower() + "_close" in rows[index]
                and symbol.lower() + "_close" in rows[index - 1] for symbol in SYMBOLS)
                and all(item["process_date"] <= signal_day
                        for symbol in SYMBOLS
                        for item in events[symbol]["dividends"].get(day, []))):
            eligible.append(index)
    if len(eligible) < count:
        raise ValueError("R8_INSUFFICIENT_KNOWN_PAIRED_SESSIONS")
    scenarios = []
    for index in eligible[-count:]:
        previous, row = rows[index - 1], rows[index]
        day = row["date"]
        overnight = {}
        intraday = {}
        dividend_yield = {}
        for symbol in SYMBOLS:
            previous_close = float(previous[symbol.lower() + "_close"])
            open_price = float(row[symbol.lower() + "_open"])
            close_price = float(row[symbol.lower() + "_close"])
            ratio = float(events[symbol]["splits"].get(day, 1.0))
            overnight[symbol] = ratio * open_price / previous_close
            intraday[symbol] = close_price / open_price
            dividend_yield[symbol] = math.fsum(
                ratio * float(event["rate"]) / previous_close
                for event in events[symbol]["dividends"].get(day, []))
            if (not math.isfinite(overnight[symbol]) or overnight[symbol] <= 0
                    or not math.isfinite(intraday[symbol]) or intraday[symbol] <= 0
                    or not math.isfinite(dividend_yield[symbol])
                    or dividend_yield[symbol] < 0):
                raise ValueError("R8_SCENARIO_UNIT_INVALID")
        scenarios.append({"date": day, "overnight": overnight,
                          "intraday": intraday, "dividend_yield": dividend_yield})
    return scenarios


def _targets_for_action(rows: list[dict], index: int, books: dict,
                        decision_equity: float, prior_closes: dict[str, float],
                        action: str, indicators: dict, contract: dict, r7: dict) -> dict:
    path = r7["paths"][action]
    signal_day = rows[index - 1]["date"]
    owner_equity = {owner: _book_decision_equity(book, signal_day, prior_closes)
                    for owner, book in books.items()}
    budgets = {owner: decision_equity * path[owner + "_cap"] for owner in ("tqqq", "soxl")}
    tqqq_target = 0.0
    if budgets["tqqq"]:
        signal = _decision(rows, index - 1, shares=books["tqqq"]["shares"]["TQQQ"],
                           nav=decision_equity, use_guard=True, config=contract,
                           member_budget_usd=budgets["tqqq"])
        if signal["signal_date"] != signal_day:
            raise ValueError("R8_TQQQ_SIGNAL_DATE_INVALID")
        tqqq_target = float(signal["target_tqqq_usd"])
    soxl_signal = None
    if budgets["soxl"] and owner_equity["soxl"] > 0:
        soxl_signal = _soxl_signal(indicators[signal_day], books["soxl"], signal_day,
                                   prior_closes, budgets["soxl"])
    targets = {
        "outer": {"QQQM": decision_equity * path["qqqm"],
                  "BOXX": decision_equity * path["outer_boxx"]},
        "tqqq": {"TQQQ": tqqq_target},
        "soxl": ({symbol: 0.0 for symbol in OWNER_SYMBOLS["soxl"]}
                 if soxl_signal is None else dict(soxl_signal["targets"])),
    }
    return {"budgets": budgets, "owner_equity": owner_equity, "targets": targets,
            "outer_cash_target": decision_equity * path["outer_cash"],
            "tqqq_target": tqqq_target, "soxl_signal": soxl_signal}


def _scenario_wealth(books: dict, prior_closes: dict[str, float],
                     scenario: dict, plan: dict, *, signal_day: str,
                     trade_day: str, trade_index: int, fee_rate: float,
                     decision_equity: float) -> tuple[float, dict]:
    projected = _known_books(books, signal_day)
    opening = {symbol: prior_closes[symbol] * scenario["overnight"][symbol]
               for symbol in SYMBOLS}
    closing = {symbol: opening[symbol] * scenario["intraday"][symbol]
               for symbol in SYMBOLS}
    prior_shares = {owner: dict(book["shares"]) for owner, book in projected.items()}
    dividend = math.fsum(prior_shares[owner][symbol] * prior_closes[symbol]
                         * scenario["dividend_yield"][symbol]
                         for owner in OWNERS for symbol in OWNER_SYMBOLS[owner])
    _release(projected, trade_day, trade_index)
    trades, fees = _sell_to_targets(projected, plan["targets"], opening, fee_rate, trade_index)
    transfers = _fund_owners(projected, plan["budgets"], plan["owner_equity"], opening,
                             signal_day, plan["outer_cash_target"], plan["soxl_signal"],
                             plan["tqqq_target"], sweep_zero_budget=True,
                             aggregate_member_cap_usd=decision_equity * 0.05)
    shortages = _buy_to_targets(projected, plan["targets"], opening, signal_day,
                                 fee_rate, plan["budgets"], plan["outer_cash_target"],
                                 plan["soxl_signal"], trades, fees)
    wealth = math.fsum(_book_value(book, closing) for book in projected.values()) + dividend
    if wealth <= 0 or not math.isfinite(wealth):
        raise ValueError("R8_SCENARIO_TERMINAL_WEALTH_INVALID")
    return wealth, {"trades": trades, "transfers": transfers,
                    "shortages": shortages, "hypothetical_dividend_usd": dividend}


def _selector(indicators: dict, contract: dict, actions: dict,
              r7: dict, r8: dict):
    scenario_cache = {}

    def choose(rows: list[dict], index: int, books: dict,
               decision_equity: float, prior_closes: dict[str, float],
               previous_action: str, cost_bps: int) -> dict:
        signal_day = rows[index - 1]["date"]
        if signal_day in r8["startup"]["decision_dates"]:
            return {"selected_action": r8["startup"]["required_action"],
                    "previous_action": previous_action, "startup_fixed": True,
                    "observed_through": signal_day, "scenario_count": 0,
                    "estimated_mean_log_growth": None, "estimated_scores": None,
                    "estimated_mean_trade_shares": None,
                    "previous_action_retained": previous_action == "B0"}
        if signal_day < r8["startup"]["dynamic_first_decision_date"]:
            raise ValueError("R8_UNDECLARED_STARTUP_DATE")
        if index not in scenario_cache:
            scenario_cache[index] = _historical_scenarios(
                rows, actions, index - 1,
                count=r8["estimator"]["training_observations"])
        scenarios = scenario_cache[index]
        known = _known_books(books, signal_day)
        known_nav = math.fsum(_book_value(book, prior_closes) for book in known.values())
        if abs(known_nav - decision_equity) > 1e-6:
            raise ValueError("R8_DECISION_DENOMINATOR_MISMATCH")
        outcomes = {}
        executable = {}
        for action in r8["action_ids"]:
            plan = _targets_for_action(rows, index, books, decision_equity,
                                       prior_closes, action, indicators, contract, r7)
            values = []
            trades = 0
            for scenario in scenarios:
                wealth, detail = _scenario_wealth(
                    books, prior_closes, scenario, plan, signal_day=signal_day,
                    trade_day=rows[index]["date"], trade_index=index,
                    fee_rate=cost_bps / 10_000.0, decision_equity=decision_equity)
                values.append(wealth)
                trades += sum(abs(quantity) for item in detail["trades"].values()
                              for quantity in item.values())
            outcomes[action] = values
            executable[action] = trades / len(scenarios)
        selected = select_finite_executable_action(
            scenario_wealth_usd=outcomes, nav_usd=decision_equity,
            previous_action=previous_action,
            tie_log_tolerance=r8["estimator"]["tie_log_tolerance"])
        return {"selected_action": selected["selected_action"],
                "previous_action": previous_action,
                "startup_fixed": False,
                "observed_through": scenarios[-1]["date"],
                "scenario_count": selected["scenario_count"],
                "estimated_mean_log_growth": selected["estimated_mean_log_growth"],
                "estimated_scores": selected["scores"],
                "estimated_mean_trade_shares": executable,
                "previous_action_retained": selected["previous_action_retained"]}

    return choose


def _read_r7_reference(root: Path, r8: dict) -> dict:
    path = root / "summary.json"
    if path.is_symlink() or _sha(path) != r8["r7_formal_summary_sha256"]:
        raise ValueError("R8_R7_REFERENCE_MISMATCH")
    reference = json.loads(path.read_bytes())
    if (set(reference["results"]) != {f"{name}_{cost}bps"
                                      for name in r8["action_ids"] for cost in r8["cost_bps_scenarios"]}
            or any(item["sessions"] != r8["expected_replay_sessions"]
                   for item in reference["results"].values())):
        raise ValueError("R8_R7_REFERENCE_INCOMPLETE")
    return reference


def analyze(raw_root: Path, r6_root: Path, materialized_path: Path,
            r7_reference_root: Path, *, short_sessions: int | None = None,
            continuation_last_session: str | None = None,
            continuation_from_session: str | None = None,
            continuation_checkpoints: dict[str, dict] | None = None,
            checkpoint_out: dict[str, dict] | None = None,
            replay_inputs: tuple[list[dict], dict] | None = None) -> tuple[dict, dict]:
    r8 = _policy()
    r7 = r7_policy()
    rows, actions, indicators, source = _load_inputs(raw_root, r6_root, materialized_path, r7)
    if replay_inputs is not None:
        rows, actions = replay_inputs
    reference = _read_r7_reference(r7_reference_root, r8)
    if short_sessions is not None and not 1 <= short_sessions <= 12:
        raise ValueError("R8_SHORT_WINDOW_INVALID")
    costs = [10] if short_sessions is not None else r8["cost_bps_scenarios"]
    results = {}
    ledgers = {}
    selector = _selector(indicators, source["tqqq_contract"], actions, r7, r8)
    for cost in costs:
        key = f"dynamic_{cost}bps"
        checkpoint = (None if continuation_checkpoints is None
                      else continuation_checkpoints.get(key))
        checkpoint_result = (None if checkpoint_out is None
                             else checkpoint_out.setdefault(key, {}))
        metrics, ledger = _replay(rows, actions, indicators, source["tqqq_contract"], r7,
                                  path_name="B0", cost_bps=cost, short_sessions=short_sessions,
                                  action_selector=selector, candidate_id=r8["candidate_id"],
                                  continuation_last_session=continuation_last_session,
                                  continuation_from_session=continuation_from_session,
                                  continuation_checkpoint=checkpoint,
                                  checkpoint_out=checkpoint_result)
        counts = Counter(item["path"] for item in ledger)
        metrics["action_counts"] = {name: counts[name] for name in r8["action_ids"]}
        metrics["startup_fixed_sessions"] = sum(item["action_selection"]["startup_fixed"]
                                                for item in ledger)
        metrics["dynamic_selected_sessions"] = len(ledger) - metrics["startup_fixed_sessions"]
        metrics["action_transitions"] = sum(ledger[i]["path"] != ledger[i - 1]["path"]
                                         for i in range(1, len(ledger)))
        metrics["average_nasdaq_nominal_to_nav"] = statistics.mean(
            (item["qqqm_value_usd"] + 3 * item["tqqq_value_usd"])
            / item["economic_nav_close_usd"] for item in ledger)
        metrics["average_semiconductor_nominal_to_nav"] = statistics.mean(
            (3 * item["soxl_value_usd"] + item["soxx_value_usd"])
            / item["economic_nav_close_usd"] for item in ledger)
        metrics["soxl_or_soxx_holding_days"] = sum(item["soxl_value_usd"] +
                                                  item["soxx_value_usd"] > 0 for item in ledger)
        results[key] = metrics
        ledgers[key] = ledger
    return ({"schema": "qsl.research.r8_joint_allocation_result.v1",
             "candidate_id": r8["candidate_id"], "development": True,
             "research_only": True, "strict_point_in_time_certified": False,
             "source_assurance": r7["soxl_input_assurance"],
             "r8_policy_sha256": POLICY_SHA256,
             "r7_policy_sha256": r8["r7_policy_sha256"],
             "r7_formal_summary_sha256": r8["r7_formal_summary_sha256"],
             "raw_manifest_sha256": r7["data"]["raw_manifest_sha256"],
             "r6_manifest_sha256": r7["data"]["r6_manifest_sha256"],
             "r6_materialized_file_sha256": r7["data"]["r6_materialized_file_sha256"],
             "runner_sha256": _sha(Path(__file__)),
             "r7_engine_sha256": _sha(HERE / "r7_joint_account_compare.py"),
             "auto_allocator_sha256": _sha(HERE / "auto_allocate.py"),
             "short_sessions": short_sessions,
             "fixed_reference_metrics": (reference["results"] if short_sessions is None else {}),
             "results": results}, ledgers)


def run(raw_root: Path, r6_root: Path, materialized_path: Path,
        r7_reference_root: Path, output_root: Path, *, short_sessions: int | None = None) -> dict:
    summary, ledgers = analyze(raw_root, r6_root, materialized_path,
                               r7_reference_root, short_sessions=short_sessions)
    output_root.mkdir(mode=0o700, parents=True, exist_ok=False)
    hashes = {}
    for key, ledger in ledgers.items():
        content = _canonical(ledger)
        with os.fdopen(os.open(output_root / f"private_daily_{key}.json",
                              os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
            stream.write(content)
        hashes[key] = hashlib.sha256(content).hexdigest()
    summary["private_ledger_sha256"] = hashes
    content = _canonical(summary)
    with os.fdopen(os.open(output_root / "summary.json",
                          os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(content)
    return {"status": "COMPLETE", "candidate_id": summary["candidate_id"],
            "run_count": len(ledgers), "policy_sha256": POLICY_SHA256,
            "summary_sha256": hashlib.sha256(content).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--r6-root", type=Path, required=True)
    parser.add_argument("--materialized", type=Path, required=True)
    parser.add_argument("--r7-reference-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--short-sessions", type=int)
    args = parser.parse_args()
    print(json.dumps(run(args.raw_root, args.r6_root, args.materialized,
                         args.r7_reference_root, args.output_root,
                         short_sessions=args.short_sessions), sort_keys=True))
