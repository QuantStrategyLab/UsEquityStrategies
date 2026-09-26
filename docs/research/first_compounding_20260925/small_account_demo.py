"""Reproduce one synthetic small-account run through both real v2 builders.

The existing test fixtures supply only artificial, source-timed market inputs.
No provider, account, order or external storage is used.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import tempfile
from collections import defaultdict
from copy import deepcopy
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from quant_platform_kit.strategy_lifecycle.contracts import (
    ResearchDailyLedger, ResearchLedgerDay, ResearchPositionMark,
)
from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore
from us_equity_strategies.research.local_member_replay_input import load_local_member_fixture
from us_equity_strategies.research.optimized_strategy_replay import (
    persist_optimized_strategy_trial,
    replay_optimized_strategy,
)

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _account_days(replays, budgets):
    sessions = replays["SOXL"].points
    if tuple(p.session for p in sessions) != tuple(p.session for p in replays["TQQQ"].points):
        raise RuntimeError("member calendars differ")
    days = []
    for soxl, tqqq in zip(sessions, replays["TQQQ"].points, strict=True):
        holdings = defaultdict(float)
        for point in (soxl, tqqq):
            for symbol, quantity in point.holdings:
                holdings[symbol] += quantity
            member_assets = point.cash + sum(value for _, value in point.market_values)
            member_assets += sum(mark.valuation for mark in point.option_positions)
            member_assets += point.dividend_receivable
            if not math.isclose(member_assets, point.nav, abs_tol=1e-7):
                raise RuntimeError("member cash and positions do not match NAV")
        account_cash = budgets["CASH"] + soxl.cash + tqqq.cash
        account_nav = budgets["CASH"] + soxl.nav + tqqq.nav
        if account_cash < -1e-8 or not math.isfinite(account_nav):
            raise RuntimeError("common account is unfunded")
        days.append({"session": soxl.session.isoformat(), "account_cash_usd": round(account_cash, 4),
            "account_nav_usd": round(account_nav, 4),
            "trading_fees_usd": round(soxl.fees + tqqq.fees, 4),
            "member_nav_usd": {"SOXL": round(soxl.nav, 4), "TQQQ": round(tqqq.nav, 4)},
            "holdings_shares": {symbol: round(qty, 8) for symbol, qty in sorted(holdings.items())
                if abs(qty) > 1e-9}})
    return days


def _cash_only_daily_path(allocator, allocation_input, initial_outer_cash, base_sources, position_sources,
                          initial_points, fixtures, store, temp):
    """Rebuild each next-day decision from saved synthetic holdings after funded cash transfers."""
    revision = json.loads((HERE / "small_account_scenario_revision.json").read_text())
    members = ("SOXL", "TQQQ")
    sessions = base_sources["SOXL"]["calendar"]
    if sessions != base_sources["TQQQ"]["calendar"]:
        raise RuntimeError("member calendars differ")
    if any(source.get(key) for source in base_sources.values()
           for key in ("income_cashflow", "external_cashflow", "ledger_events")):
        raise RuntimeError("cash-only segment path cannot reconstruct external events")
    states = dict(initial_points)
    outer_cash = initial_outer_cash
    high_water = allocation_input["high_water_policy"]["high_water_usd"]
    source_fingerprint = hashlib.sha256(json.dumps({"initial_policy": allocation_input,
        "member_inputs": base_sources, "scenario_revision": revision},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    account_days = []
    transfers = []
    readbacks = []
    checkpoint_restorations = []
    final_checkpoint = None
    for index, session in enumerate(sessions):
        nav = outer_cash + sum(states[member].nav for member in members)
        high_water = max(high_water, nav)
        if any(states[member].option_positions or states[member].restricted_cash
               or states[member].dividend_receivable for member in members):
            raise RuntimeError("cash-only transfer cannot move option or dividend obligations")
        holdings = defaultdict(float)
        for member in members:
            point = states[member]
            marked_assets = point.cash + sum(value for _, value in point.market_values)
            if not math.isclose(marked_assets, point.nav, abs_tol=1e-7):
                raise RuntimeError("dynamic member holdings and cash do not match NAV")
            for symbol, quantity in states[member].holdings:
                holdings[symbol] += quantity
        account_cash = outer_cash + sum(states[member].cash for member in members)
        if account_cash < -1e-8 or not math.isfinite(nav):
            raise RuntimeError("dynamic account cash or NAV is invalid")
        account_days.append({"session": session, "account_nav_usd": round(nav, 6),
            "account_cash_usd": round(account_cash, 6),
            "member_nav_usd": {member: round(states[member].nav, 6) for member in members},
            "holdings_shares": {symbol: quantity for symbol, quantity in sorted(holdings.items())
                if abs(quantity) > 1e-9}})
        if index == len(sessions) - 1:
            break
        current = {member: states[member].nav for member in members}
        current["CASH"] = outer_cash
        preserved = {member: sum(value for _, value in states[member].market_values)
                     for member in members}
        preserved["CASH"] = 0.0
        candidate_input = deepcopy(allocation_input)
        candidate_input.update(decision_date=session, nav_usd=nav,
                               current_usd=current, locked_usd=preserved)
        candidate_input["high_water_policy"]["high_water_usd"] = high_water
        if session == revision["decision_date"]:
            candidate_input["evidence"]["observed_through"] = revision["observed_through"]
            candidate_input["evidence"]["scenarios"] = revision["scenarios"]
        decision = allocator.allocate(candidate_input)
        if decision["status"] != "MECHANISM_APPROXIMATION" or decision["fee_usd"] != 0:
            raise RuntimeError(f"daily capital decision is not funded: {decision['status']}")
        desired = decision["budget_usd"]
        cash_before = outer_cash + sum(states[member].cash for member in members)
        deltas = {member: desired[member] - current[member] for member in members}
        outer_delta = desired["CASH"] - outer_cash
        if not math.isclose(sum(deltas.values()) + outer_delta, 0.0, abs_tol=1e-6):
            raise RuntimeError("member cash transfer does not sum to zero")
        if any(states[member].cash + deltas[member] < -1e-8 for member in members) or desired["CASH"] < -1e-8:
            raise RuntimeError("member transfer spends held securities without a sale")
        outer_cash = desired["CASH"]
        if not math.isclose(cash_before, outer_cash + sum(states[member].cash + deltas[member]
                                                         for member in members), abs_tol=1e-6):
            raise RuntimeError("internal transfer changed account cash")
        transfers.append({"decision_date": session, "source": "manual_scenario_revision"
            if session == revision["decision_date"] else "initial_manual_scenarios",
            "high_water_usd": round(high_water, 6),
            "wealth_floor_usd": round(decision["wealth_floor_usd"], 6),
            "preserved_securities_usd": {member: round(preserved[member], 6) for member in members},
            "target_budget_usd": desired,
            "member_cash_delta_usd": {member: round(deltas[member], 6) for member in members},
            "outer_cash_delta_usd": round(outer_delta, 6),
            "account_cash_before_and_after_usd": round(cash_before, 6)})
        next_states = {}
        for member in members:
            source = deepcopy(base_sources[member])
            window = set(sessions[index:index + 2])
            source["calendar"] = sessions[index:index + 2]
            source["initial_cash"] = states[member].cash + deltas[member]
            source["initial_quantities"] = dict(states[member].holdings)
            source["prices"] = [row for row in source["prices"] if row["session"] in window]
            if source["derived_indicators"] is not None:
                source["derived_indicators"] = {day: row for day, row in source["derived_indicators"].items()
                                                if day == session}
            if source["indicator_sources"] is not None:
                source["indicator_sources"] = {day: row for day, row in source["indicator_sources"].items()
                                               if day == session}
            source["benchmark_bars"] = [row for row in source["benchmark_bars"]
                                        if row["session"] <= sessions[index + 1]]
            if "state_inputs" in source:
                source["state_inputs"] = [row for row in source["state_inputs"]
                                          if row["signal_date"] == session]
            source["option_market_inputs"] = [row for row in source["option_market_inputs"]
                                               if row["session"] in window]
            first_option_row = source["option_market_inputs"][0]
            first_option_row["positions"] = []
            first_option_row["positions_source"] = position_sources[member][session]
            fixture = {"input": source}
            name = member.lower()
            identity_args = {"param_set_id": f"small-{name}-cash-transfer-{session}",
                "share_quantity_contract": "synthetic whole equity shares and integer option lots"}
            fixture["identity"] = (fixtures._build_soxl_option_v2_identity(source, **identity_args)
                if member == "SOXL" else fixtures._build_tqqq_v2_identity(source, **identity_args))
            path = temp / f"{name}-segment-{session}.json"
            path.write_text(json.dumps(fixture), encoding="utf-8")
            _, request = load_local_member_fixture(path)
            replay = replay_optimized_strategy(request)
            if not math.isclose(replay.points[0].nav, desired[member], abs_tol=1e-6):
                raise RuntimeError("transferred member NAV differs from next builder input")
            record = persist_optimized_strategy_trial(request, store,
                trial_id=f"small-{name}-cash-transfer-{session}")
            ledger = store.load_research_ledger("us_equity", request.identity.strategy_profile,
                record.trial_id, record.run_id, record.param_version)
            if ledger is None or len(ledger.days) != 1:
                raise RuntimeError("post-transfer member ledger readback incomplete")
            day, point = ledger.days[0], replay.points[1]
            expected_marks = {symbol: (quantity, dict(point.market_values)[symbol])
                              for symbol, quantity in point.holdings if quantity != 0.0}
            actual_marks = {mark.symbol: (mark.quantity, mark.valuation) for mark in day.positions}
            if (not math.isclose(day.nav, point.nav, abs_tol=1e-8)
                    or not math.isclose(day.cash, point.cash, abs_tol=1e-8)
                    or actual_marks != expected_marks):
                raise RuntimeError("post-transfer member ledger differs from replay")
            readbacks.append({"member": member, "signal_date": session,
                              "next_day": sessions[index + 1]})
            next_states[member] = replay.points[1]
        states = next_states
        next_session = sessions[index + 1]
        high_water = max(high_water, outer_cash + sum(states[member].nav for member in members))
        checkpoint = {"version": "synthetic_cash_only_v1", "session": next_session,
            "source_fingerprint": source_fingerprint, "outer_cash_usd": outer_cash,
            "high_water_usd": high_water,
            "members": {member: {"cash_usd": states[member].cash,
                "nav_usd": states[member].nav,
                "holdings": list(states[member].holdings),
                "market_values": list(states[member].market_values)} for member in members}}
        checkpoint_path = temp / f"cash-only-checkpoint-{next_session}.json"
        encoded = json.dumps(checkpoint, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False)
        checkpoint_path.write_text(encoded, encoding="utf-8")
        restored = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if (restored["version"] != "synthetic_cash_only_v1"
                or restored["source_fingerprint"] != source_fingerprint
                or restored["session"] != next_session):
            raise RuntimeError("restored checkpoint identity differs from fixed input")
        for member in members:
            saved = restored["members"][member]
            holdings = dict(saved["holdings"])
            market_values = dict(saved["market_values"])
            if (saved["cash_usd"] < -1e-8 or set(holdings) != set(market_values)
                    or any(abs(quantity - round(quantity)) > 1e-9 for quantity in holdings.values())
                    or not math.isclose(saved["cash_usd"] + sum(market_values.values()),
                                        saved["nav_usd"], abs_tol=1e-8)):
                raise RuntimeError("restored member cash and whole-share positions do not close")
        if not math.isclose(restored["outer_cash_usd"] + sum(
            restored["members"][member]["nav_usd"] for member in members),
            outer_cash + sum(states[member].nav for member in members), abs_tol=1e-8):
            raise RuntimeError("restored checkpoint does not conserve account NAV")
        if (restored["outer_cash_usd"] < -1e-8
                or restored["high_water_usd"] + 1e-8 < max(
                    allocation_input["high_water_policy"]["initial_principal_usd"],
                    restored["outer_cash_usd"] + sum(
                        restored["members"][member]["nav_usd"] for member in members))):
            raise RuntimeError("restored checkpoint wealth reference is invalid")
        states = {member: SimpleNamespace(
            cash=restored["members"][member]["cash_usd"],
            nav=restored["members"][member]["nav_usd"],
            holdings=tuple(tuple(row) for row in restored["members"][member]["holdings"]),
            market_values=tuple(tuple(row) for row in restored["members"][member]["market_values"]),
            option_positions=(), restricted_cash=0.0, dividend_receivable=0.0,
        ) for member in members}
        outer_cash = restored["outer_cash_usd"]
        high_water = restored["high_water_usd"]
        checkpoint_restorations.append({"session": next_session,
            "sha256": hashlib.sha256(encoded.encode()).hexdigest()})
        final_checkpoint = restored
    return {"status": "SYNTHETIC_CASH_ONLY_CONTINUATION", "account_days": account_days,
            "transfers": transfers, "segment_qpk_readbacks": readbacks,
            "checkpoint_restorations": checkpoint_restorations,
            "last_checkpoint": final_checkpoint,
            "scenario_revision": revision,
            "scope": "Each next-day member replay restarts from a saved and reloaded synthetic checkpoint of prior whole-share holdings, funded cash, outer cash and high-water state. No securities are sold to fund a transfer; no option, dividend or external-event obligation is present. This is case-specific checkpoint restoration, not a general runtime restore or historical performance."}


def _sale_funding_path(allocator, allocation_input, base_sources, position_sources, execution_by_member,
                       initial_outer_cash, prior_points, fixtures, store, temp):
    """Exercise one explicit synthetic sell-first policy without anticipating proceeds."""
    policy = json.loads((HERE / "small_account_sale_policy.json").read_text())
    revision = json.loads((HERE / "small_account_scenario_revision.json").read_text())
    members = ("SOXL", "TQQQ")
    if (isinstance(initial_outer_cash, bool) or not isinstance(initial_outer_cash, (int, float))
            or not math.isfinite(initial_outer_cash) or initial_outer_cash < 0):
        raise RuntimeError("sale funding requires finite nonnegative outer cash")
    locked_outer_cash = allocation_input["locked_usd"]["CASH"]
    if locked_outer_cash > initial_outer_cash:
        raise RuntimeError("protected outer cash exceeds available outer cash")
    if policy["version"] != "synthetic_single_core_sale_v1" or policy["decision_date"] != revision["decision_date"]:
        raise RuntimeError("sale policy and scenario revision are not aligned")
    if any(source.get(key) for source in base_sources.values()
           for key in ("income_cashflow", "external_cashflow", "ledger_events")):
        raise RuntimeError("sale funding path cannot reconstruct external events")
    if any(execution_by_member[member][key] != 0 for member in members
           for key in ("slippage_bps", "market_impact_bps")):
        raise RuntimeError("sale policy requires explicit adverse-fill handling")
    if any(point.option_positions or point.restricted_cash or point.dividend_receivable
           for point in prior_points.values()):
        raise RuntimeError("sale policy cannot carry option, collateral or dividend obligations")

    def shift_day(value, old, new):
        if isinstance(value, str):
            return value.replace(old, new)
        if isinstance(value, list):
            return [shift_day(item, old, new) for item in value]
        if isinstance(value, dict):
            return {key: shift_day(item, old, new) for key, item in value.items()}
        return value

    current = {member: prior_points[member].nav for member in members}
    current["CASH"] = initial_outer_cash
    preserved = {}
    for member in members:
        quantities = dict(prior_points[member].holdings)
        if policy["sellable_symbol"][member] in policy["preserved_symbols"][member]:
            raise RuntimeError("sale symbol is also marked preserved")
        nonzero = {symbol for symbol, quantity in quantities.items() if quantity > 0}
        allowed = set(policy["preserved_symbols"][member]) | {policy["sellable_symbol"][member]}
        if not nonzero <= allowed:
            raise RuntimeError("sale policy lacks an explicit disposition for a held asset")
        values = dict(prior_points[member].market_values)
        preserved[member] = sum(values[symbol] for symbol in policy["preserved_symbols"][member])
    preserved["CASH"] = locked_outer_cash
    candidate_input = deepcopy(allocation_input)
    candidate_input.update(decision_date=policy["decision_date"],
                           nav_usd=sum(current.values()), current_usd=current,
                           locked_usd=preserved)
    candidate_input["high_water_policy"]["high_water_usd"] = max(
        allocation_input["high_water_policy"]["high_water_usd"], sum(current.values()))
    candidate_input["evidence"]["observed_through"] = revision["observed_through"]
    candidate_input["evidence"]["scenarios"] = revision["scenarios"]
    candidate = allocator.allocate(candidate_input)
    if candidate["status"] != "MECHANISM_APPROXIMATION" or candidate["fee_usd"] != 0:
        raise RuntimeError("sale funding candidate is not feasible")
    target = candidate["budget_usd"]
    planned_sales = {}
    for member in members:
        symbol = policy["sellable_symbol"][member]
        held = dict(prior_points[member].holdings)[symbol]
        marked = dict(prior_points[member].market_values)[symbol]
        reference_price = marked / held if held else 0.0
        available = prior_points[member].cash
        needed = max(0.0, current[member] - target[member] - available)
        commission_rate = float(execution_by_member[member]["commission_bps"]) / 10_000
        count = (min(int(held), math.ceil(needed / (reference_price * (1 - commission_rate)) - 1e-9))
                 if needed and reference_price > 0 and commission_rate < 1 else 0)
        planned_sales[member] = {"symbol": symbol, "shares": count,
            "needed_beyond_cash_usd": round(needed, 6),
            "decision_reference_price_usd": round(reference_price, 6)}

    marked_states = {}
    sale_events = {}
    for member in members:
        source = base_sources[member]
        quantities = dict(prior_points[member].holdings)
        prices = {row["symbol"]: row for row in source["prices"]
                  if row["session"] == policy["sale_date"]}
        symbol = planned_sales[member]["symbol"]
        count = planned_sales[member]["shares"]
        if symbol not in prices or count > quantities[symbol]:
            raise RuntimeError("sale quantity or dated fill is invalid")
        field = execution_by_member[member]["fill_price_field"]
        fill = prices[symbol][field]
        commission_rate = float(execution_by_member[member]["commission_bps"]) / 10_000
        gross = count * fill
        fee = gross * commission_rate
        cash = prior_points[member].cash + gross - fee
        quantities[symbol] -= count
        if any(quantity and held_symbol not in prices
               for held_symbol, quantity in quantities.items()):
            raise RuntimeError("held asset has no dated sale-day price")
        values = {held_symbol: quantity * prices[held_symbol]["close"] if quantity else 0.0
                  for held_symbol, quantity in quantities.items()}
        nav = cash + sum(values.values())
        if cash < -1e-8 or nav <= 0:
            raise RuntimeError("realized sale did not leave a funded member")
        marked_states[member] = SimpleNamespace(cash=cash, nav=nav,
            holdings=tuple(sorted(quantities.items())),
            market_values=tuple(sorted(values.items())),
            option_positions=(), restricted_cash=0.0, dividend_receivable=0.0)
        sale_events[member] = {"symbol": symbol, "shares": count,
            "actual_fill_usd": round(fill, 6), "gross_proceeds_usd": round(gross, 6),
            "fee_usd": round(fee, 6), "net_cash_released_usd": round(gross - fee, 6)}
    sale_nav = initial_outer_cash + sum(marked_states[member].nav for member in members)
    high_water = max(candidate_input["high_water_policy"]["high_water_usd"], sale_nav)
    after_sale = deepcopy(candidate_input)
    after_sale.update(decision_date=policy["sale_date"], nav_usd=sale_nav,
                      current_usd={**{member: marked_states[member].nav for member in members}, "CASH": initial_outer_cash},
                      locked_usd={**{member: sum(dict(marked_states[member].market_values)[symbol]
                            for symbol in policy["preserved_symbols"][member]) for member in members}, "CASH": locked_outer_cash})
    after_sale["high_water_policy"]["high_water_usd"] = high_water
    reallocation = allocator.allocate(after_sale)
    if reallocation["status"] != "MECHANISM_APPROXIMATION" or reallocation["fee_usd"] != 0:
        raise RuntimeError("post-sale capital reallocation is not feasible")
    actual_target = reallocation["budget_usd"]
    deltas = {member: actual_target[member] - marked_states[member].nav for member in members}
    outer_cash = actual_target["CASH"]
    outer_delta = outer_cash - initial_outer_cash
    if (not math.isclose(sum(deltas.values()) + outer_delta, 0.0, abs_tol=1e-6)
            or any(marked_states[member].cash + deltas[member] < -1e-8 for member in members)
            or outer_cash < locked_outer_cash - 1e-8):
        raise RuntimeError("post-sale transfer spends uncollected or protected assets")
    cash_before = initial_outer_cash + sum(marked_states[member].cash for member in members)
    if not math.isclose(cash_before, outer_cash + sum(marked_states[member].cash + deltas[member]
                                                      for member in members), abs_tol=1e-6):
        raise RuntimeError("post-sale internal transfer changed account cash")
    funding_events = [{"event_id": f"synthetic-sale-{member}-{policy['sale_date']}",
        "session": policy["sale_date"], "phase": "sale", "member": member,
        "symbol": sale_events[member]["symbol"], "shares_delta": -sale_events[member]["shares"],
        "cash_delta_usd": sale_events[member]["net_cash_released_usd"],
        "fee_usd": sale_events[member]["fee_usd"]}
        for member in members if sale_events[member]["shares"]]
    funding_events.append({"event_id": f"synthetic-internal-transfer-{policy['sale_date']}",
        "session": policy["sale_date"], "phase": "transfer",
        "cash_delta_usd": {**{member: round(deltas[member], 6) for member in members},
                           "CASH": round(outer_delta, 6)}})
    journal_path = temp / "small-account-sale-funding-events.json"
    journal_text = json.dumps(funding_events, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False, allow_nan=False)
    journal_path.write_text(journal_text, encoding="utf-8")
    journal_readback = json.loads(journal_path.read_text(encoding="utf-8"))
    if journal_readback != funding_events:
        raise RuntimeError("sale and transfer events did not read back")
    reconstructed_cash = {member: prior_points[member].cash for member in members}
    reconstructed_outer_cash = initial_outer_cash
    reconstructed_holdings = {member: dict(prior_points[member].holdings) for member in members}
    for event in journal_readback:
        if event["phase"] == "sale":
            member = event["member"]
            reconstructed_cash[member] += event["cash_delta_usd"]
            reconstructed_holdings[member][event["symbol"]] += event["shares_delta"]
        else:
            for member in members:
                reconstructed_cash[member] += event["cash_delta_usd"][member]
            reconstructed_outer_cash += event["cash_delta_usd"]["CASH"]
    if (not math.isclose(sum(journal_readback[-1]["cash_delta_usd"].values()), 0.0, abs_tol=1e-8)
            or not math.isclose(reconstructed_outer_cash, outer_cash, abs_tol=1e-6)
            or any(not math.isclose(reconstructed_cash[member], marked_states[member].cash + deltas[member],
                                        abs_tol=1e-8) or reconstructed_holdings[member]
                    != dict(marked_states[member].holdings) for member in members)):
        raise RuntimeError("read-back funding events do not reconstruct member cash and holdings")

    next_points = {}
    segment_replays = {}
    segment_input_digests = {}
    readbacks = []
    for member in members:
        source = deepcopy(base_sources[member])
        signal, effective = policy["sale_date"], policy["next_execution_date"]
        next_bars = [shift_day(row, signal, effective) for row in source["prices"]
                     if row["session"] == signal]
        source["calendar"] = [signal, effective]
        source["prices"] = [row for row in source["prices"] if row["session"] == signal] + next_bars
        source["initial_cash"] = marked_states[member].cash + deltas[member]
        source["initial_quantities"] = dict(marked_states[member].holdings)
        if source["derived_indicators"] is not None:
            source["derived_indicators"] = {signal: shift_day(
                source["derived_indicators"][policy["decision_date"]],
                policy["decision_date"], signal)}
        if source["indicator_sources"] is not None:
            source["indicator_sources"] = {signal: shift_day(
                source["indicator_sources"][policy["decision_date"]],
                policy["decision_date"], signal)}
        if "state_inputs" in source:
            source["state_inputs"] = [shift_day(row, policy["decision_date"], signal)
                for row in source["state_inputs"] if row["signal_date"] == policy["decision_date"]]
        source["benchmark_bars"] = [row for row in source["benchmark_bars"]
                                    if row["session"] <= signal]
        if source["benchmark_bars"]:
            source["benchmark_bars"].append(shift_day(source["benchmark_bars"][-1], signal, effective))
        initial_option_row = next(row for row in source["option_market_inputs"]
                                  if row["session"] == signal)
        initial_option_row["positions"] = []
        initial_option_row["positions_source"] = position_sources[member][signal]
        source["option_market_inputs"] = [initial_option_row,
            shift_day(initial_option_row, signal, effective)]
        source["option_market_inputs"][1].pop("positions")
        source["option_market_inputs"][1].pop("positions_source")
        identity_args = {"param_set_id": f"small-{member.lower()}-sale-policy-{signal}",
            "share_quantity_contract": "synthetic whole equity shares and integer option lots"}
        fixture = {"input": source, "identity": (
            fixtures._build_soxl_option_v2_identity(source, **identity_args) if member == "SOXL"
            else fixtures._build_tqqq_v2_identity(source, **identity_args))}
        path = temp / f"{member.lower()}-sale-segment.json"
        path.write_text(json.dumps(fixture), encoding="utf-8")
        segment_input_digests[member] = hashlib.sha256(path.read_bytes()).hexdigest()
        _, request = load_local_member_fixture(path)
        replay = replay_optimized_strategy(request)
        if not math.isclose(replay.points[0].nav, actual_target[member], abs_tol=1e-6):
            raise RuntimeError("post-sale builder started with the wrong funded member NAV")
        if replay.points[1].option_positions or replay.points[1].restricted_cash:
            raise RuntimeError("sale policy cannot resume activated option obligations")
        record = persist_optimized_strategy_trial(request, store,
            trial_id=f"small-{member.lower()}-sale-policy")
        ledger = store.load_research_ledger("us_equity", request.identity.strategy_profile,
            record.trial_id, record.run_id, record.param_version)
        if ledger is None or len(ledger.days) != 1 or not math.isclose(
            ledger.days[0].nav, replay.points[1].nav, abs_tol=1e-8):
            raise RuntimeError("post-sale builder ledger did not read back")
        next_points[member] = replay.points[1]
        segment_replays[member] = replay
        readbacks.append(member)

    def marks(point):
        values = dict(point.market_values)
        return tuple(ResearchPositionMark(symbol=symbol, quantity=quantity,
            valuation=values[symbol]) for symbol, quantity in point.holdings if quantity)

    continuous_readbacks = {}
    for member in members:
        prior = prior_points[member]
        after_sale_mark = marked_states[member]
        following = next_points[member]
        backtest = segment_replays[member].backtest
        sale = sale_events[member]
        ledger = ResearchDailyLedger(
            trial_id=f"small-{member.lower()}-sale-continuous",
            domain="us_equity",
            strategy_profile=backtest.strategy_profile,
            run_id=f"small-{member.lower()}-sale-continuous",
            param_version=backtest.param_version,
            input_id=hashlib.sha256((journal_text + segment_input_digests[member]).encode()).hexdigest(),
            calendar_id=backtest.calendar_id,
            periods_per_year=backtest.periods_per_year,
            cost_source=backtest.cost_model,
            cost_inputs=dict(backtest.cost_inputs),
            initial_session_date=prior.session,
            initial_nav=prior.nav,
            initial_cash=prior.cash,
            initial_positions=marks(prior),
            days=(
                ResearchLedgerDay(
                    session_date=date.fromisoformat(policy["sale_date"]),
                    cash=after_sale_mark.cash,
                    positions=marks(after_sale_mark),
                    trade_net_cashflow=sale["gross_proceeds_usd"],
                    fees=sale["fee_usd"],
                    nav=after_sale_mark.nav,
                    daily_return=after_sale_mark.nav / prior.nav - 1.0,
                ),
                ResearchLedgerDay(
                    session_date=date.fromisoformat(policy["next_execution_date"]),
                    cash=following.cash,
                    positions=marks(following),
                    trade_net_cashflow=following.trade_net_cashflow,
                    fees=following.fees,
                    nav=following.nav,
                    daily_return=following.nav / (after_sale_mark.nav + deltas[member]) - 1.0,
                    external_cashflow=deltas[member],
                ),
            ),
            synthetic=True,
        )
        store.save_research_ledger(ledger)
        loaded = store.load_research_ledger(ledger.domain, ledger.strategy_profile,
            ledger.trial_id, ledger.run_id, ledger.param_version)
        if loaded is None or loaded.to_dict() != ledger.to_dict():
            raise RuntimeError("continuous sale and builder ledger did not read back")
        continuous_readbacks[member] = {"initial_session": prior.session.isoformat(),
            "sessions": [day.session_date.isoformat() for day in loaded.days],
            "nav_usd": [round(day.nav, 6) for day in loaded.days],
            "sale_trade_cashflow_usd": round(loaded.days[0].trade_net_cashflow, 6),
            "next_interval_member_flow_usd": round(loaded.days[1].external_cashflow, 6),
            "total_fees_usd": round(loaded.total_fees, 6)}
    next_nav = outer_cash + sum(next_points[member].nav for member in members)
    next_cash = outer_cash + sum(next_points[member].cash for member in members)
    next_fees = sum(next_points[member].fees for member in members)
    if not math.isclose(next_nav, sale_nav - next_fees, abs_tol=1e-6):
        raise RuntimeError("constant synthetic next-day marks do not reconcile trading fees")
    def account_marks(states):
        totals = {}
        for member in members:
            point = states[member]
            values = dict(point.market_values)
            for symbol, quantity in point.holdings:
                if quantity:
                    old_quantity, old_value = totals.get(symbol, (0.0, 0.0))
                    totals[symbol] = (old_quantity + quantity, old_value + values[symbol])
        return tuple(ResearchPositionMark(symbol=symbol, quantity=quantity, valuation=value)
            for symbol, (quantity, value) in sorted(totals.items()) if quantity)

    initial_account_cash = initial_outer_cash + sum(prior_points[member].cash for member in members)
    initial_account_nav = initial_outer_cash + sum(prior_points[member].nav for member in members)
    sale_account_cash = initial_outer_cash + sum(marked_states[member].cash for member in members)
    sale_gross = sum(sale_events[member]["gross_proceeds_usd"] for member in members)
    sale_fees = sum(sale_events[member]["fee_usd"] for member in members)
    next_trade_cashflow = sum(next_points[member].trade_net_cashflow for member in members)
    if (not math.isclose(initial_account_cash + sale_gross - sale_fees, sale_account_cash, abs_tol=1e-9)
            or not math.isclose(sale_account_cash + next_trade_cashflow - next_fees, next_cash,
                                abs_tol=1e-9)):
        raise RuntimeError("account cash does not reconcile to member trade events and fees")
    account_ledger = ResearchDailyLedger(
        trial_id="small-soxl-tqqq-sale-account-continuous", domain="us_equity",
        strategy_profile="soxl_tqqq_joint_synthetic_account",
        run_id="small-soxl-tqqq-sale-account-continuous", param_version=1,
        input_id=hashlib.sha256((journal_text + json.dumps(segment_input_digests,
            sort_keys=True)).encode()).hexdigest(),
        calendar_id="synthetic_xnys_2024_01_03_05", periods_per_year=252.0,
        cost_source="synthetic_aggregated_member_costs_v1",
        cost_inputs={f"{member.lower()}_commission_bps": execution_by_member[member]["commission_bps"]
            for member in members},
        initial_session_date=prior_points[members[0]].session,
        initial_nav=initial_account_nav, initial_cash=initial_account_cash,
        initial_positions=account_marks(prior_points),
        days=(ResearchLedgerDay(session_date=date.fromisoformat(policy["sale_date"]),
                cash=sale_account_cash, positions=account_marks(marked_states),
                trade_net_cashflow=sale_gross, fees=sale_fees, nav=sale_nav,
                daily_return=sale_nav / initial_account_nav - 1.0),
            ResearchLedgerDay(session_date=date.fromisoformat(policy["next_execution_date"]),
                cash=next_cash, positions=account_marks(next_points),
                trade_net_cashflow=next_trade_cashflow, fees=next_fees, nav=next_nav,
                daily_return=next_nav / sale_nav - 1.0)),
        synthetic=True,
    )
    store.save_research_ledger(account_ledger)
    loaded_account = store.load_research_ledger(account_ledger.domain, account_ledger.strategy_profile,
        account_ledger.trial_id, account_ledger.run_id, account_ledger.param_version)
    if loaded_account is None or loaded_account.to_dict() != account_ledger.to_dict():
        raise RuntimeError("account continuous ledger did not read back")
    if (not math.isclose(loaded_account.days[0].nav, initial_outer_cash + sum(
                marked_states[member].nav for member in members), abs_tol=1e-8)
            or not math.isclose(loaded_account.days[1].nav, outer_cash + sum(
                next_points[member].nav for member in members), abs_tol=1e-8)
            or not math.isclose(loaded_account.total_fees, sum(
                continuous_readbacks[member]["total_fees_usd"] for member in members), abs_tol=1e-8)):
        raise RuntimeError("account ledger does not reconcile to member ledgers")
    account_readback = {"initial_session": loaded_account.initial_session_date.isoformat(),
        "initial_nav_usd": round(loaded_account.initial_nav, 6),
        "initial_cash_usd": round(loaded_account.initial_cash, 6),
        "initial_outer_cash_usd": round(initial_outer_cash, 6),
        "sessions": [day.session_date.isoformat() for day in loaded_account.days],
        "nav_usd": [round(day.nav, 6) for day in loaded_account.days],
        "cash_usd": [round(day.cash, 6) for day in loaded_account.days],
        "holdings_shares": [{mark.symbol: mark.quantity for mark in day.positions}
            for day in loaded_account.days],
        "trade_net_cashflow_usd": [round(day.trade_net_cashflow, 6) for day in loaded_account.days],
        "external_cashflow_usd": [round(day.external_cashflow, 6) for day in loaded_account.days],
        "total_fees_usd": round(loaded_account.total_fees, 6),
        "member_nav_reconciled": True}
    next_holdings = {symbol: sum(dict(next_points[member].holdings).get(symbol, 0.0)
        for member in members) for symbol in sorted(set().union(*(
            dict(next_points[member].holdings) for member in members)))}
    reference_prices = {row["symbol"]: row["close"] for source in base_sources.values()
        for row in source["prices"] if row["session"] == policy["sale_date"]}
    return {"status": "SYNTHETIC_SELL_FIRST_CONTINUATION",
        "policy": policy, "candidate_budget_usd": target,
        "planned_sales": planned_sales, "realized_sales": sale_events,
        "post_sale_account_nav_usd": round(sale_nav, 6),
        "post_sale_reallocation_usd": actual_target,
        "post_sale_member_cash_delta_usd": {member: round(deltas[member], 6) for member in members},
        "outer_cash_before_usd": round(initial_outer_cash, 6),
        "post_sale_outer_cash_usd": round(outer_cash, 6),
        "post_sale_account_cash_before_and_after_usd": round(cash_before, 6),
        "post_sale_funded_member_states": {member: {
            "cash_usd": round(marked_states[member].cash + deltas[member], 6),
            "holdings_shares": {symbol: quantity for symbol, quantity in
                marked_states[member].holdings if quantity}}
            for member in members},
        "reference_prices_usd": reference_prices,
        "funding_events": funding_events,
        "funding_event_readback_sha256": hashlib.sha256(journal_text.encode()).hexdigest(),
        "next_day": {"session": policy["next_execution_date"], "account_nav_usd": round(next_nav, 6),
            "account_cash_usd": round(next_cash, 6),
            "trading_fees_usd": round(next_fees, 6),
            "member_nav_usd": {member: round(next_points[member].nav, 6) for member in members},
            "member_holdings_shares": {member: {symbol: quantity for symbol, quantity in
                next_points[member].holdings if quantity} for member in members},
            "builder_decision_target_value_usd": {member: dict(
                segment_replays[member].decision_targets[0][2]) for member in members},
            "holdings_shares": {symbol: round(quantity, 8) for symbol, quantity in next_holdings.items()
                if abs(quantity) > 1e-9}},
        "segment_qpk_readbacks": readbacks,
        "continuous_qpk_readbacks": continuous_readbacks,
        "account_qpk_readback": account_readback,
        "scope": "Artificial single-core-ETF sale policy: Jan-03 target is only a proposal; Jan-04 core ETF shares sell at synthetic dated fills before re-solving and moving realized free cash. The prior Jan-03 builder trade is intentionally suspended during fundraising. Actual post-close transfer is dated Jan-04 in the UES event journal; QPK member external_cashflow appears at the start of the Jan-04 to Jan-05 return interval. The separate QPK account daily ledger has zero external cashflow and aggregates member gross trades, marks, fees and outer cash. Jan-04 builders then plan Jan-05 holdings. No account-level event attribution, old option, dividend, settlement, broker or history claim."}


def run(*, terminal_price_shock: bool = True,
        omit_inactive_income_prices: bool = False,
        capital_curve_policy: dict | None = None) -> dict[str, object]:
    allocator = _load_module("small_account_allocator", HERE / "auto_allocate.py")
    fixtures = _load_module("small_account_fixtures", ROOT / "tests/test_optimized_strategy_replay.py")
    allocation_input = json.loads((HERE / "small_account_input.json").read_text())
    if capital_curve_policy is not None:
        allocation_input["capital_curve_policy"] = deepcopy(capital_curve_policy)
    allocation = allocator.allocate(allocation_input)
    if allocation["status"] != "MECHANISM_APPROXIMATION" or allocation["fee_usd"] != 0:
        raise RuntimeError("small-account allocation is not funded without internal transfer fees")
    budgets = allocation["budget_usd"]
    if not math.isclose(sum(budgets.values()), allocation_input["nav_usd"], abs_tol=1e-8):
        raise RuntimeError("member budgets and outer cash do not close")

    replays = {}
    readbacks = {}
    identities = {}
    whole_share_preview = {}
    whole_replays = {}
    whole_readbacks = {}
    base_sources = {}
    position_sources = {}
    execution_by_member = {}
    with tempfile.TemporaryDirectory(prefix="qsl-small-account-synthetic-") as tmp:
        temp = Path(tmp)
        store = PerformanceStore(local_root=temp / "qpk-ledger", cloud_bucket="")
        for member in ("SOXL", "TQQQ"):
            name = member.lower()
            path = temp / f"{name}.json"
            fixture = (fixtures._soxx_credit_fixture(path) if member == "SOXL"
                       else fixtures._tqqq_option_fixture_file(path))
            source = fixture["input"]
            candidate = json.loads((ROOT / "docs/research" /
                f"independent_{name}_full_manifest_v2_20260925.json").read_text())
            config = candidate["runtime_config"]
            if config["option_overlay_enabled"] is not True or config[
                "option_income_overlay_enabled" if member == "SOXL" else "option_growth_overlay_enabled"
            ] is not True:
                raise RuntimeError(f"{member} full candidate option controls were disabled")
            config_sha = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False).encode()).hexdigest()
            if config_sha != candidate["config_sha256"]:
                raise RuntimeError(f"{member} candidate configuration changed")
            source["runtime_config"] = config
            source["initial_cash"] = budgets[member]
            if omit_inactive_income_prices:
                inactive_income = {"SCHD", "DGRO", "SGOV", "SPYI", "QQQI"}
                if (budgets[member] >= config["income_layer_start_usd"]
                        or any(source["initial_quantities"][symbol] != 0 for symbol in inactive_income)):
                    raise RuntimeError("income assets are not proven inactive")
                source["prices"] = [bar for bar in source["prices"]
                                    if bar["symbol"] not in inactive_income]
            if member == "SOXL":
                source["state_inputs"] = list(fixtures._soxl_market_regime_request(
                    ("risk_on", "risk_on")).state_inputs)
            position_sources[member] = {row["session"]: deepcopy(row["positions_source"])
                                        for row in source["option_market_inputs"]}
            for index, row in enumerate(source["option_market_inputs"]):
                row["quotes"] = []
                if index == 0:
                    row["positions"] = []
                else:
                    row.pop("positions")
                    row.pop("positions_source")
            if terminal_price_shock:
                multipliers = {"SOXL": 1.10, "SOXX": 1.02, "TQQQ": 0.90, "QQQM": 1.01}
                for bar in source["prices"]:
                    if bar["session"] == "2024-01-04" and bar["symbol"] in multipliers:
                        marked = bar["close"] * multipliers[bar["symbol"]]
                        bar.update(open=marked, high=marked, low=marked, close=marked)
            synthetic_id = f"small-account-{candidate['candidate_id']}-synthetic-v1"
            fixture["identity"] = (fixtures._build_soxl_option_v2_identity(
                source, param_set_id=synthetic_id) if member == "SOXL"
                else fixtures._build_tqqq_v2_identity(source, param_set_id=synthetic_id))
            path.write_text(json.dumps(fixture), encoding="utf-8")
            _, request = load_local_member_fixture(path)
            replay = replay_optimized_strategy(request)
            if not math.isclose(replay.points[0].nav, budgets[member], abs_tol=1e-8):
                raise RuntimeError(f"{member} initial member capital differs from allocation")
            if any(point.option_positions for point in replay.points):
                raise RuntimeError(f"{member} opened an option below its candidate threshold")
            record = persist_optimized_strategy_trial(request, store, trial_id=f"small-{name}")
            ledger = store.load_research_ledger("us_equity", request.identity.strategy_profile,
                record.trial_id, record.run_id, record.param_version)
            if ledger is None or len(ledger.days) != len(replay.points) - 1:
                raise RuntimeError(f"{member} QPK ledger readback incomplete")
            for point, day in zip(replay.points[1:], ledger.days, strict=True):
                if not (math.isclose(point.cash, day.cash, abs_tol=1e-8)
                        and math.isclose(point.nav, day.nav, abs_tol=1e-8)):
                    raise RuntimeError(f"{member} QPK ledger differs from replay")
            replays[member] = replay
            readbacks[member] = len(ledger.days)
            first_trade = replay.points[1]
            fills = {bar.symbol: bar.close for bar in request.prices
                     if bar.session == first_trade.session}
            whole_shares = {symbol: math.floor(value / fills[symbol] + 1e-9)
                            for symbol, value in first_trade.market_values if value > 0}
            gross = sum(quantity * fills[symbol] for symbol, quantity in whole_shares.items())
            commission = round(gross * float(request.cost_model.commission_bps) / 10_000, 4)
            cash_left = budgets[member] - gross - commission
            if cash_left < -1e-8:
                raise RuntimeError(f"{member} whole-share preview exceeds its own budget")
            whole_share_preview[member] = {
                "holdings_shares": {key: value for key, value in sorted(whole_shares.items()) if value},
                "stock_value_usd": round(gross, 4), "trading_fees_usd": commission,
                "cash_usd": round(cash_left, 4),
            }
            identities[member] = {"candidate_id": candidate["candidate_id"],
                "config_sha256": config_sha, "synthetic_param_set_id": synthetic_id,
                "option_start_usd": config["option_income_overlay_start_usd" if member == "SOXL"
                    else "option_growth_overlay_start_usd"]}

        for member in ("SOXL", "TQQQ"):
            name = member.lower()
            path = temp / f"{name}.json"
            fixture = json.loads(path.read_text(encoding="utf-8"))
            source = fixture["input"]
            source["whole_share_execution"] = True
            base_sources[member] = deepcopy(source)
            whole_id = f"small-{name}-full-v2-whole-shares"
            kwargs = {"param_set_id": whole_id,
                "share_quantity_contract": "synthetic whole equity shares and integer option lots"}
            fixture["identity"] = (fixtures._build_soxl_option_v2_identity(source, **kwargs)
                if member == "SOXL" else fixtures._build_tqqq_v2_identity(source, **kwargs))
            path.write_text(json.dumps(fixture), encoding="utf-8")
            _, request = load_local_member_fixture(path)
            execution_by_member[member] = {"commission_bps": float(request.cost_model.commission_bps),
                "slippage_bps": float(request.cost_model.slippage_bps),
                "market_impact_bps": float(request.cost_model.market_impact_bps),
                "fill_price_field": request.execution.fill_price_field}
            replay = replay_optimized_strategy(request)
            if any(abs(quantity - round(quantity)) > 1e-8 for point in replay.points
                   for _, quantity in point.holdings):
                raise RuntimeError(f"{member} whole-share replay produced fractional stock")
            record = persist_optimized_strategy_trial(request, store, trial_id=f"small-{name}-whole")
            ledger = store.load_research_ledger("us_equity", request.identity.strategy_profile,
                record.trial_id, record.run_id, record.param_version)
            if ledger is None or len(ledger.days) != len(replay.points) - 1:
                raise RuntimeError(f"{member} whole-share QPK readback incomplete")
            for point, day in zip(replay.points[1:], ledger.days, strict=True):
                if not (math.isclose(point.cash, day.cash, abs_tol=1e-8)
                        and math.isclose(point.nav, day.nav, abs_tol=1e-8)):
                    raise RuntimeError(f"{member} whole-share QPK ledger differs from replay")
            whole_replays[member] = replay
            whole_readbacks[member] = len(ledger.days)

        dynamic_cash_path = _cash_only_daily_path(
            allocator, allocation_input, budgets["CASH"], base_sources, position_sources,
            {member: whole_replays[member].points[0] for member in ("SOXL", "TQQQ")},
            fixtures, store, temp)
        sale_funding_path = _sale_funding_path(
            allocator, allocation_input, base_sources, position_sources, execution_by_member,
            budgets["CASH"],
            {member: whole_replays[member].points[1] for member in ("SOXL", "TQQQ")},
            fixtures, store, temp)

    days = _account_days(replays, budgets)
    whole_days = _account_days(whole_replays, budgets)
    capital_proposals = []
    high_water = allocation_input["high_water_policy"]["high_water_usd"]
    for soxl, tqqq in zip(whole_replays["SOXL"].points,
                          whole_replays["TQQQ"].points, strict=True):
        nav = budgets["CASH"] + soxl.nav + tqqq.nav
        high_water = max(high_water, nav)
        member_points = {"SOXL": soxl, "TQQQ": tqqq}
        current = {member: point.nav for member, point in member_points.items()}
        current["CASH"] = budgets["CASH"]
        locked = {member: sum(value for _, value in point.market_values)
                  + point.dividend_receivable + sum(mark.valuation for mark in point.option_positions)
                  for member, point in member_points.items()}
        locked["CASH"] = 0
        if any(point.option_positions or point.restricted_cash for point in member_points.values()):
            raise RuntimeError("capital proposal needs option liability and collateral accounting")
        proposal_input = deepcopy(allocation_input)
        proposal_input.update(decision_date=soxl.session.isoformat(), nav_usd=nav,
                              current_usd=current, locked_usd=locked)
        proposal_input["high_water_policy"]["high_water_usd"] = high_water
        proposal = allocator.allocate(proposal_input)
        capital_proposals.append({"session": soxl.session.isoformat(),
            "current_nav_usd": round(nav, 4), "high_water_usd": round(high_water, 4),
            "wealth_floor_usd": round(high_water - proposal_input["high_water_policy"]["cumulative_loss_limit_usd"], 4),
            "preserved_securities_for_proposal_usd": {member: round(locked[member], 4)
                for member in member_points},
            "status": proposal["status"],
            "proposed_budget_usd": proposal.get("budget_usd"),
            "proposal_only": True})
    return {"status": "SYNTHETIC_BUILDER_REPLAY", "allocation": allocation,
        "candidate_identities": identities, "account_days": days,
        "qpk_readback_days": readbacks,
        "whole_share_account_days": whole_days,
        "whole_share_qpk_readback_days": whole_readbacks,
        "dynamic_cash_only_path": dynamic_cash_path,
        "sale_funding_path": sale_funding_path,
        "daily_capital_proposals": capital_proposals,
        "first_trade_whole_share_preview": {"members": whole_share_preview,
            "account_cash_usd": round(budgets["CASH"] + sum(p["cash_usd"]
                for p in whole_share_preview.values()), 4),
            "scope": "First trade only, floor each real-builder target to whole shares at artificial close; separate funded preview, not a multi-day Schwab execution replay."},
        "scope": "Artificial scenarios and market inputs, including an optional final-day price shock; full v2 runtime configurations through real builders. Fixture identity uses synthetic placeholder code revisions; candidate config hashes are verified. Fractional and whole-share research executions are separate. Daily capital proposals conservatively preserve all marked securities without modelling sales; that is a proposal bound, not a claim that every security is legally unsellable. Proposals are not executed intermember transfers. No platform execution or historical performance claim.",
        "option_status": "Both original option switches remain enabled; member NAV stayed below the stated new-entry thresholds with no initial option positions. Threshold crossing and old-position paths require separate evidence."}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
