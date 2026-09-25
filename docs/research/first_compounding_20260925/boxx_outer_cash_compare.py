"""Private, offline BOXX outer-cash comparison for the frozen TQQQ core."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from tqqq_cash_budget_compare import _policy as s4_policy, _recovery
from us_equity_strategies.research.c3_capital_path import smooth_bounded_capital_risk_ratio
from us_equity_strategies.research.tqqq_qqq_guard_cash_research import _decision, _validated_bars, _verified_bundle

POLICY_NAME = "boxx_outer_cash_policy.v1.json"
POLICY_SHA = "cfed32767cb367ccce7ef880c3c15f82d50b99fe805d542e85839fc67270c905"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(root: Path):
    if root.is_symlink() or _sha(root / POLICY_NAME) != POLICY_SHA:
        raise ValueError("BOXX_FROZEN_POLICY_CHANGED")
    policy = json.loads((root / POLICY_NAME).read_text())
    if _sha(root / "manifest.json") != policy["source_manifest_sha256"]:
        raise ValueError("BOXX_MANIFEST_CHANGED")
    contract, qqq, tqqq, t_actions = _verified_bundle(root)
    rows = _validated_bars(qqq, tqqq)
    for name, spec in policy["source_objects"].items():
        path = root / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != spec["bytes"] or _sha(path) != spec["sha256"]:
            raise ValueError("BOXX_PRIVATE_INPUT_CHANGED:" + name)
    actions = {"TQQQ": t_actions}
    supplemental = {}
    for symbol in ("BOXX", "QQQM"):
        page = json.loads((root / f"bars/{symbol}/page-001.json").read_text())
        if page.get("symbol") != symbol or page.get("next_page_token") is not None:
            raise ValueError("BAR_IDENTITY_OR_PAGINATION")
        supplemental[symbol] = {str(x["t"])[:10]: x for x in page["bars"]}
        if len(supplemental[symbol]) != len(page["bars"]):
            raise ValueError("DUPLICATE_PRICE_DATE")
        action_page = json.loads((root / f"actions/{symbol}/page-001.json").read_text())
        if action_page.get("next_page_token") is not None:
            raise ValueError("ACTION_PAGINATION")
        actions[symbol] = action_page["corporate_actions"]
        if actions[symbol].get("forward_splits"):
            raise ValueError("SUPPLEMENTAL_SPLIT_NEEDS_REVIEW")
    common = []
    for row in rows:
        day = row["date"]
        if day < min(supplemental["BOXX"]):
            continue
        if day not in supplemental["BOXX"] or day not in supplemental["QQQM"]:
            raise ValueError("MISSING_COMMON_PRICE_DATE:" + day)
        for symbol in ("BOXX", "QQQM"):
            bar = supplemental[symbol][day]
            values = [bar[x] for x in ("o", "h", "l", "c", "v")]
            if any(not isinstance(x, (int, float)) or not math.isfinite(x) or x <= 0 for x in values):
                raise ValueError("INVALID_OHLCV")
            if bar["h"] < max(bar["o"], bar["l"], bar["c"]) or bar["l"] > min(bar["o"], bar["c"]):
                raise ValueError("INVALID_OHLC")
            row[f"{symbol.lower()}_open"] = float(bar["o"])
            row[f"{symbol.lower()}_close"] = float(bar["c"])
        common.append(row)
    if len(common) != 505 or common[60]["date"] != policy["first_signal"] or common[61]["date"] != policy["first_trade"] or common[-1]["date"] != policy["end_date"]:
        raise ValueError("FROZEN_COMMON_WINDOW_CHANGED")
    return policy, s4_policy(root), contract, rows, actions


def _event_schedule(actions: dict) -> dict:
    schedule = {}
    for symbol, source in actions.items():
        splits = {}
        dividends = defaultdict(list)
        for event in source.get("forward_splits", []):
            ratio = float(event["new_rate"]) / float(event["old_rate"])
            if ratio <= 0 or event["ex_date"] in splits:
                raise ValueError("INVALID_SPLIT")
            splits[event["ex_date"]] = ratio
        for event in source.get("cash_dividends", []):
            if event["symbol"] != symbol or event["payable_date"] < event["ex_date"] or float(event["rate"]) < 0:
                raise ValueError("INVALID_DIVIDEND")
            dividends[event["ex_date"]].append(event)
        schedule[symbol] = {"splits": splits, "dividends": dividends}
    return schedule


def _scenario_pairs(rows: list[dict], i: int, actions: dict, boxx: bool) -> list[dict]:
    if i < 60 or any("boxx_close" not in rows[j] for j in range(i - 60, i + 1)):
        raise ValueError("PAIRED_SCENARIO_WARMUP_INCOMPLETE")
    asof = rows[i]["date"]
    dividends = {}
    for symbol in ("TQQQ", "BOXX"):
        dividends[symbol] = defaultdict(float)
        for event in actions[symbol].get("cash_dividends", []):
            if event["ex_date"] <= asof and event.get("process_date", "9999") <= asof:
                dividends[symbol][event["ex_date"]] += float(event["rate"])
    out = []
    for j in range(i - 59, i + 1):
        row, prior = rows[j], rows[j - 1]
        item = {}
        for symbol in ("TQQQ", "BOXX"):
            if symbol == "BOXX" and not boxx:
                continue
            key = symbol.lower()
            previous = prior[key + "_close"]
            item[symbol] = (row[key + "_open"] / previous,
                            row[key + "_close"] / row[key + "_open"],
                            dividends[symbol].get(row["date"], 0.0) / previous)
        out.append(item)
    return out


def _execute(state: dict, *, t_open: float, b_open: float, t_close: float, b_close: float,
             budget: float, t_target: float, b_target: float, fee: float, release: int) -> dict:
    """Sale proceeds settle after two sessions; member cash is reserved first."""
    cash = state["cash"]
    shares = dict(state["shares"])
    pending = [dict(x) for x in state["pending"]]
    prices = {"TQQQ": t_open, "BOXX": b_open}
    targets = {"TQQQ": math.floor(t_target / t_open), "BOXX": math.floor(b_target / b_open)}
    trades = {"TQQQ": 0.0, "BOXX": 0.0}
    costs = {"TQQQ": 0.0, "BOXX": 0.0}
    for symbol in ("TQQQ", "BOXX"):
        quantity = max(0.0, shares[symbol] - targets[symbol])
        if quantity:
            shares[symbol] -= quantity
            trades[symbol] -= quantity
            cost = quantity * prices[symbol] * fee
            costs[symbol] += cost
            pending.append({"release": release, "amount": quantity * prices[symbol] - cost, "symbol": symbol})
    for symbol in ("TQQQ", "BOXX"):
        quantity = max(0.0, targets[symbol] - shares[symbol])
        reserve = max(0.0, budget - shares["TQQQ"] * t_open) if symbol == "BOXX" else 0.0
        available = max(0.0, cash - reserve)
        fill = min(quantity, math.floor((available + 1e-9) / (prices[symbol] * (1 + fee))))
        if fill:
            shares[symbol] += fill
            trades[symbol] += fill
            cost = fill * prices[symbol] * fee
            costs[symbol] += cost
            cash -= fill * prices[symbol] + cost
    if cash < -1e-7:
        raise ValueError("NEGATIVE_SETTLED_CASH")
    nav = cash + sum(x["amount"] for x in pending) + state["receivable"] + shares["TQQQ"] * t_close + shares["BOXX"] * b_close
    return {"cash": cash, "shares": shares, "pending": pending,
            "receivable": state["receivable"], "nav": nav, "trades": trades, "costs": costs}


def _choose_budget(rows: list[dict], i: int, state: dict, policy: dict, s4: dict,
                   contract: dict, actions: dict, *, automatic: bool, boxx: bool,
                   high_water: float, cost_bps: int) -> tuple[float, int]:
    nav = state["nav"]
    if not automatic:
        return nav * policy["member_budget_fixed_ratio"], 0
    full = _decision(rows, i, shares=state["shares"]["TQQQ"], nav=nav,
                     use_guard=True, config=contract, member_budget_usd=nav)
    exposure = full["target_tqqq_usd"] / nav
    auto = s4["automatic"]
    curve = auto["curve"]
    curve_ratio = float(smooth_bounded_capital_risk_ratio(
        capital=high_water, a0=curve["a0_usd"], lower=curve["lower"],
        upper=curve["upper"], curvature=curve["curvature"])["risk_ratio"])
    cap = min(nav * curve_ratio, nav - state["receivable"])
    step = float(auto["member_budget_grid_usd"])
    grid = [j * step for j in range(int(cap / step) + 1)]
    if abs(grid[-1] - cap) > 1e-8:
        grid.append(cap)
    paired = _scenario_pairs(rows, i, actions, boxx)
    next_index = i + 1
    next_day = rows[next_index]["date"]
    due_sales = sum(x["amount"] for x in state["pending"] if x["release"] <= next_index)
    due_dividends = sum(x["amount"] for x in state["dividend_pending"] if x["pay"] <= next_day)
    base = {"cash": state["cash"] + due_sales + due_dividends,
            "receivable": state["receivable"] - due_dividends,
            "pending": [x for x in state["pending"] if x["release"] > next_index],
            "shares": dict(state["shares"])}
    best = None
    feasible = 0
    t_prior, b_prior = rows[i]["tqqq_close"], rows[i]["boxx_close"]
    for budget in grid:
        outcomes = []
        costs = []
        b_target = max(0.0, nav - state["receivable"] - budget) if boxx else 0.0
        for item in paired:
            t_overnight, t_intraday, t_div = item["TQQQ"]
            b_overnight, b_intraday, b_div = item.get("BOXX", (1.0, 1.0, 0.0))
            t_open, b_open = t_prior * t_overnight, b_prior * b_overnight
            scenario = dict(base)
            scenario["receivable"] += state["shares"]["TQQQ"] * t_prior * t_div
            scenario["receivable"] += state["shares"]["BOXX"] * b_prior * b_div
            trial = _execute(scenario, t_open=t_open, b_open=b_open,
                             t_close=t_open * t_intraday, b_close=b_open * b_intraday,
                             budget=budget, t_target=exposure * budget, b_target=b_target,
                             fee=cost_bps / 10000.0, release=next_index + 2)
            outcomes.append(trial["nav"])
            costs.append(sum(trial["costs"].values()))
        if min(outcomes) < nav * (1 - auto["max_worst_scenario_loss_ratio_of_current_nav"]) - 1e-8:
            continue
        feasible += 1
        score = (math.fsum(math.log(value / nav) for value in outcomes) / len(outcomes),
                 -math.fsum(costs) / len(costs), -budget)
        if best is None or score > best[0]:
            best = score, budget
    if best is None:
        raise ValueError("NO_FEASIBLE_BUDGET_AT_" + rows[i]["date"])
    budget = best[1]
    actual = _decision(rows, i, shares=state["shares"]["TQQQ"], nav=nav,
                       use_guard=True, config=contract, member_budget_usd=budget)
    if abs(actual["target_tqqq_usd"] - exposure * budget) > 1e-6:
        raise ValueError("CORE_NONLINEAR_BUDGET_ESTIMATE")
    return budget, feasible


def _metrics(ledger: list[dict], initial: float) -> dict:
    peak = initial
    worst = 0.0
    for row in ledger:
        peak = max(peak, row["nav_close"])
        worst = min(worst, row["nav_close"] / peak - 1)
    result = {"start": ledger[0]["date"], "end": ledger[-1]["date"],
              "sessions": len(ledger), "start_nav_usd": initial,
              "end_nav_usd": ledger[-1]["nav_close"],
              "total_return": ledger[-1]["nav_close"] / initial - 1,
              "annualized_return_252_sessions": (ledger[-1]["nav_close"] / initial) ** (252 / len(ledger)) - 1,
              "max_drawdown": worst,
              "total_cost_usd": sum(x["total_cost_usd"] for x in ledger),
              "tqqq_cost_usd": sum(x["tqqq_cost_usd"] for x in ledger),
              "boxx_cost_usd": sum(x["boxx_cost_usd"] for x in ledger),
              "total_dividend_accrued_usd": sum(x["dividend_accrued_usd"] for x in ledger),
              "average_tqqq_ratio": sum(x["tqqq_value_usd"] / x["nav_close"] for x in ledger) / len(ledger),
              "average_boxx_ratio": sum(x["boxx_value_usd"] / x["nav_close"] for x in ledger) / len(ledger),
              "average_cash_ratio": sum(x["settled_cash_usd"] / x["nav_close"] for x in ledger) / len(ledger),
              "average_pending_sale_ratio": sum(x["pending_sale_usd"] / x["nav_close"] for x in ledger) / len(ledger),
              "average_receivable_ratio": sum(x["receivable_usd"] / x["nav_close"] for x in ledger) / len(ledger),
              "max_identity_error_usd": max(x["identity_error_usd"] for x in ledger)}
    result.update(_recovery(ledger, initial))
    return result


def _replay(rows: list[dict], actions: dict, policy: dict, s4: dict, contract: dict,
            *, cost_bps: int, automatic: bool, boxx: bool) -> tuple[dict, list[dict]]:
    by_date = {x["date"]: i for i, x in enumerate(rows)}
    start = by_date[policy["first_trade"]]
    events = _event_schedule(actions)
    initial = float(policy["initial_nav_usd"])
    state = {"cash": initial, "shares": {"TQQQ": 0.0, "BOXX": 0.0},
             "pending": [], "dividend_pending": [], "receivable": 0.0, "nav": initial}
    high_water = initial
    ledger = []
    for i in range(start, len(rows)):
        signal_index = i - 1
        budget, feasible = _choose_budget(rows, signal_index, state, policy, s4, contract, actions,
                                          automatic=automatic, boxx=boxx, high_water=high_water,
                                          cost_bps=cost_bps)
        signal = _decision(rows, signal_index, shares=state["shares"]["TQQQ"], nav=state["nav"],
                           use_guard=True, config=contract, member_budget_usd=budget)
        row = rows[i]
        old_shares = dict(state["shares"])
        prior_nav = state["nav"]
        signal_receivable = state["receivable"]
        accrued = 0.0
        for symbol in ("TQQQ", "BOXX"):
            state["shares"][symbol] *= events[symbol]["splits"].get(row["date"], 1.0)
            for event in events[symbol]["dividends"].get(row["date"], []):
                amount = state["shares"][symbol] * float(event["rate"])
                state["receivable"] += amount
                accrued += amount
                state["dividend_pending"].append({"pay": event["payable_date"], "amount": amount})
        state["cash"] += sum(x["amount"] for x in state["pending"] if x["release"] <= i)
        state["pending"] = [x for x in state["pending"] if x["release"] > i]
        paid = sum(x["amount"] for x in state["dividend_pending"] if x["pay"] <= row["date"])
        state["cash"] += paid
        state["receivable"] -= paid
        state["dividend_pending"] = [x for x in state["dividend_pending"] if x["pay"] > row["date"]]
        boxx_target = max(0.0, prior_nav - budget - signal_receivable) if boxx else 0.0
        execution = _execute(state, t_open=row["tqqq_open"], b_open=row["boxx_open"],
                             t_close=row["tqqq_close"], b_close=row["boxx_close"],
                             budget=budget, t_target=signal["target_tqqq_usd"],
                             b_target=boxx_target, fee=cost_bps / 10000.0, release=i + 2)
        state.update({k: execution[k] for k in ("cash", "shares", "pending", "receivable", "nav")})
        cost = sum(execution["costs"].values())
        expected = prior_nav + accrued - cost
        for symbol in ("TQQQ", "BOXX"):
            key = symbol.lower()
            expected += old_shares[symbol] * (
                events[symbol]["splits"].get(row["date"], 1.0) * row[key + "_close"] - rows[i - 1][key + "_close"])
            expected += execution["trades"][symbol] * (row[key + "_close"] - row[key + "_open"])
        error = abs(state["nav"] - expected)
        if error > 1e-6 or state["cash"] < -1e-7 or state["receivable"] < -1e-7:
            raise ValueError("DAILY_ACCOUNT_IDENTITY_FAILED:" + row["date"])
        ledger.append({"date": row["date"], "signal_date": signal["signal_date"],
                       "scenario_observed_through": rows[signal_index]["date"],
                       "guard_route": signal["guard_route"], "core_state": signal["core_state"],
                       "feasible_budget_grid_points": feasible,
                       "member_budget_usd": budget, "target_tqqq_usd": signal["target_tqqq_usd"],
                       "target_boxx_usd": boxx_target,
                       "tqqq_trade_shares": execution["trades"]["TQQQ"],
                       "boxx_trade_shares": execution["trades"]["BOXX"],
                       "tqqq_shares": state["shares"]["TQQQ"],
                       "boxx_shares": state["shares"]["BOXX"],
                       "tqqq_value_usd": state["shares"]["TQQQ"] * row["tqqq_close"],
                       "boxx_value_usd": state["shares"]["BOXX"] * row["boxx_close"],
                       "settled_cash_usd": state["cash"],
                       "pending_sale_usd": sum(x["amount"] for x in state["pending"]),
                       "receivable_usd": state["receivable"], "dividend_accrued_usd": accrued,
                       "tqqq_cost_usd": execution["costs"]["TQQQ"],
                       "boxx_cost_usd": execution["costs"]["BOXX"],
                       "total_cost_usd": cost, "nav_close": state["nav"],
                       "identity_error_usd": error})
        high_water = max(high_water, state["nav"])
    return _metrics(ledger, initial), ledger


def _buy_hold(rows: list[dict], actions: dict, policy: dict,
              *, symbol: str, cost_bps: int) -> tuple[dict, list[dict]]:
    start = next(i for i, row in enumerate(rows) if row["date"] == policy["first_trade"])
    key = symbol.lower()
    initial = float(policy["initial_nav_usd"])
    cash, shares, receivable = initial, 0.0, 0.0
    pending = []
    events = _event_schedule(actions)[symbol]
    ledger = []
    for i in range(start, len(rows)):
        row = rows[i]
        shares *= events["splits"].get(row["date"], 1.0)
        accrued = 0.0
        for event in events["dividends"].get(row["date"], []):
            amount = shares * float(event["rate"])
            receivable += amount
            accrued += amount
            pending.append({"pay": event["payable_date"], "amount": amount})
        paid = sum(x["amount"] for x in pending if x["pay"] <= row["date"])
        cash += paid
        receivable -= paid
        pending = [x for x in pending if x["pay"] > row["date"]]
        cost = 0.0
        if i == start:
            shares = math.floor(cash / (row[key + "_open"] * (1 + cost_bps / 10000.0)))
            cost = shares * row[key + "_open"] * cost_bps / 10000.0
            cash -= shares * row[key + "_open"] + cost
        nav = cash + receivable + shares * row[key + "_close"]
        ledger.append({"date": row["date"], "nav_close": nav, "total_cost_usd": cost,
                       "tqqq_cost_usd": 0.0, "boxx_cost_usd": cost if symbol == "BOXX" else 0.0,
                       "dividend_accrued_usd": accrued,
                       "tqqq_value_usd": 0.0,
                       "boxx_value_usd": shares * row[key + "_close"] if symbol == "BOXX" else 0.0,
                       "settled_cash_usd": cash, "pending_sale_usd": 0.0,
                       "receivable_usd": receivable, "identity_error_usd": 0.0})
    result = _metrics(ledger, initial)
    result["symbol"] = symbol
    return result, ledger


def run(root: Path) -> dict:
    policy, s4, contract, rows, actions = _load(root)
    outcomes = {}
    hashes = {}
    for cost in policy["cost_bps"]:
        for automatic in (False, True):
            for boxx in (False, True):
                name = ("automatic" if automatic else "fixed") + ("_boxx" if boxx else "_cash") + f"_{cost}bps"
                metrics, ledger = _replay(rows, actions, policy, s4, contract,
                                          cost_bps=cost, automatic=automatic, boxx=boxx)
                path = root / f"private_daily_{name}.json"
                path.write_text(json.dumps(ledger, separators=(",", ":")) + "\n")
                path.chmod(0o600)
                outcomes[name] = metrics
                hashes[name] = _sha(path)
        for symbol in policy["buy_hold_benchmarks"]:
            name = symbol.lower() + f"_buy_hold_{cost}bps"
            metrics, ledger = _buy_hold(rows, actions, policy, symbol=symbol, cost_bps=cost)
            path = root / f"private_daily_{name}.json"
            path.write_text(json.dumps(ledger, separators=(",", ":")) + "\n")
            path.chmod(0o600)
            outcomes[name] = metrics
            hashes[name] = _sha(path)
    report = {"policy_id": policy["policy_id"], "research_only": True, "development": True,
              "policy_sha256": POLICY_SHA, "runner_sha256": _sha(Path(__file__)),
              "core_candidate_contract_sha256": _sha(root / "tqqq_qqq_guard_cash_contract.v1.json"),
              "s4_budget_policy_sha256": _sha(root / "s4_budget_policy.v1.json"),
              "source_manifest_sha256": policy["source_manifest_sha256"],
              "input_sha256": {name: spec["sha256"] for name, spec in policy["source_objects"].items()},
              "coverage": {"first_signal": policy["first_signal"], "first_trade": policy["first_trade"],
                           "last_mark": policy["end_date"], "sessions": next(iter(outcomes.values()))["sessions"]},
              "outcomes": outcomes, "private_ledger_sha256": hashes,
              "computed_at": datetime.now(timezone.utc).isoformat(),
              "limitations": ["2023-2024 is development, not unseen validation.",
                              "T+2 sale proceeds are a research assumption, not a broker execution claim.",
                              "Provider action process_date is not announcement time; accounting is retrospective.",
                              "Automatic BOXX estimates include BOXX price risk and form a separate outer policy.",
                              "No personal allocation, original complete v2, paper, shadow, live or trading authority."]}
    path = root / "boxx_outer_cash_summary.v1.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    path.chmod(0o600)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.private_root)
    print(json.dumps({"policy_id": report["policy_id"], "coverage": report["coverage"],
                      "summary_sha256": _sha(args.private_root / "boxx_outer_cash_summary.v1.json")}, indent=2))


if __name__ == "__main__":
    main()
