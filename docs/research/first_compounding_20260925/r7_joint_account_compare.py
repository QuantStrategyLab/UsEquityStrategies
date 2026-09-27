"""Bounded R7 development comparison with three owner books and raw-open fills.

The SOXL signal consumes R6's already materialized Twelve-only indicators;
its account execution is a separate, whole-share joint policy. No orders exist.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from boxx_outer_cash_compare import _decision, _load
from phase4_qqqm_tqqq_boxx_compare import _metrics

from us_equity_strategies.entrypoints import default_translator
from us_equity_strategies.manifests import soxl_soxx_trend_income_manifest
from us_equity_strategies.strategies.soxl_soxx_trend_income import build_rebalance_plan
from us_equity_strategies.v7_soxl_profile import (
    V7_CONFIG_SHA256,
    V7_FROZEN_RUNTIME_CONFIG,
)

HERE = Path(__file__).resolve().parent
POLICY_PATH = HERE / "r7_joint_account_policy.v1.json"
POLICY_SHA256 = "9bec154d80b7794f97477cecdd366a7b207947031c1bd76008aebf6f38e5225b"
SETTLEMENT_POLICY_SHA256 = "c135c023ee7329ad6103021ffbb79d4cdfea01e903ac331865c157a6a1246853"
_SETTLEMENT_SOURCE_IDS = frozenset({
    "sec_t1_faq", "sec_t1_final_transition",
    "dtcc_2023_anticipated_holidays", "dtcc_2024_anticipated_holidays",
    "dtcc_2025_anticipated_holidays", "dtcc_2026_anticipated_holidays",
    "carter_2025_01_09_settlement_open", "dtcc_2026_07_03_independence_day_observed",
})
OWNERS = ("outer", "tqqq", "soxl")
OWNER_SYMBOLS = {"outer": ("QQQM", "BOXX"), "tqqq": ("TQQQ",),
                 "soxl": ("SOXL", "SOXX", "BOXX")}
SYMBOLS = ("QQQM", "TQQQ", "SOXL", "SOXX", "BOXX")
UNSUPPORTED_V7_WRAPPER_KEYS = frozenset({
    "managed_symbols", "option_growth_overlay_enabled", "option_income_overlay_enabled",
    "option_income_overlay_nav_risk_ratio", "option_income_overlay_recipe",
    "option_income_overlay_start_usd", "option_overlay_enabled",
})


def _bytes_sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha(path: Path) -> str:
    return _bytes_sha(path.read_bytes())


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode()


def _policy() -> dict:
    if _sha(POLICY_PATH) != POLICY_SHA256:
        raise ValueError("R7_POLICY_CHANGED")
    policy = json.loads(POLICY_PATH.read_bytes())
    if (policy["schema"] != "qsl.research.r7_joint_account_policy.v1"
            or policy["research_only"] is not True or policy["development"] is not True
            or any(policy[k] is not False for k in
                   ("paper_authorized", "shadow_authorized", "live_authorized"))
            or policy["soxl_signal_config_sha256"] != V7_CONFIG_SHA256
            or policy["cost_bps_scenarios"] != [5, 10, 15]
            or set(policy["paths"]) != {"B0", "B1", "B2", "B3"}):
        raise ValueError("R7_POLICY_IDENTITY_INVALID")
    for path in policy["paths"].values():
        if not math.isclose(math.fsum(path.values()), 1.0, abs_tol=1e-12):
            raise ValueError("R7_POLICY_WEIGHTS_INVALID")
    return policy


def _load_settlement_policy(path: Path) -> dict:
    content = Path(path).read_bytes()
    if _bytes_sha(content) != SETTLEMENT_POLICY_SHA256:
        raise ValueError("POST_R9_SETTLEMENT_POLICY_CHANGED")
    policy = json.loads(content)
    closed = set(policy.get("dtc_non_settlement_dates", []))
    carter = [item for item in policy.get("known_settlement_only_dates", [])
              if item.get("date") == "2025-01-09"]
    source_ids = {item.get("id") for item in policy.get("official_sources", [])}
    if (policy.get("schema") != "qsl.research.post_r9_date_effective_settlement_policy.v1"
            or policy.get("research_only") is not True
            or policy.get("policy_id") != "post_r9_us_equity_dtc_standard_settlement_v1"
            or policy.get("calendar_id") !=
               "post_r9_dtc_weekday_excluding_frozen_non_settlement_20230328_20260827_v1"
            or policy.get("coverage_start") != "2023-03-28"
            or policy.get("coverage_end") != "2026-08-27"
            or policy.get("transition_trade_date") != "2024-05-28"
            or policy.get("settlement_lag_before_transition") != 2
            or policy.get("settlement_lag_on_or_after_transition") != 1
            or "2023-11-10" in closed or "2025-01-09" in closed or "2026-07-03" not in closed
            or len(carter) != 1 or carter[0].get("exchange_closed") is not True
            or carter[0].get("settlement_closed") is not False
            or not _SETTLEMENT_SOURCE_IDS <= source_ids):
        raise ValueError("POST_R9_SETTLEMENT_POLICY_INVALID")
    return policy


def _coverage_date(value: str, settlement_policy: dict) -> date:
    current = date.fromisoformat(value)
    if (current < date.fromisoformat(settlement_policy["coverage_start"])
            or current > date.fromisoformat(settlement_policy["coverage_end"])):
        raise ValueError("R7_SETTLEMENT_COVERAGE_MISSING")
    return current


def _is_settlement_day(day: str, settlement_policy: dict) -> bool:
    current = _coverage_date(day, settlement_policy)
    return (current.weekday() < 5
            and day not in set(settlement_policy["dtc_non_settlement_dates"]))


def _settlement_date(trade_date: str, settlement_policy: dict) -> str:
    """T+1/T+2 on weekdays minus frozen DTC closures. Not an XNYS row index."""
    _coverage_date(trade_date, settlement_policy)
    end = date.fromisoformat(settlement_policy["coverage_end"])
    lag = (settlement_policy["settlement_lag_before_transition"]
           if trade_date < settlement_policy["transition_trade_date"]
           else settlement_policy["settlement_lag_on_or_after_transition"])
    closed = set(settlement_policy["dtc_non_settlement_dates"])
    cursor = date.fromisoformat(trade_date)
    found = 0
    while found < lag:
        cursor += timedelta(days=1)
        if cursor > end:
            raise ValueError("R7_SETTLEMENT_COVERAGE_MISSING")
        if cursor.weekday() < 5 and cursor.isoformat() not in closed:
            found += 1
    return cursor.isoformat()


def _require_pending_settlement(item: dict, settlement_policy: dict | None) -> None:
    has_release = "release" in item
    has_settlement = "settlement_date" in item
    if has_release and has_settlement:
        raise ValueError("R7_SETTLEMENT_PENDING_AMBIGUOUS")
    if settlement_policy is None:
        if has_settlement or not has_release:
            raise ValueError("R7_SETTLEMENT_PENDING_INVALID")
        return
    if (has_release or not has_settlement
            or item.get("settlement_policy_id") != settlement_policy["policy_id"]
            or item.get("settlement_calendar_id") != settlement_policy["calendar_id"]):
        raise ValueError("R7_SETTLEMENT_PENDING_INVALID")
    try:
        parsed = date.fromisoformat(item["settlement_date"])
    except (TypeError, ValueError) as exc:
        raise ValueError("R7_SETTLEMENT_PENDING_INVALID") from exc
    if parsed.isoformat() != item["settlement_date"]:
        raise ValueError("R7_SETTLEMENT_PENDING_INVALID")
    _coverage_date(item["settlement_date"], settlement_policy)
    if not _is_settlement_day(item["settlement_date"], settlement_policy):
        raise ValueError("R7_SETTLEMENT_PENDING_INVALID")
    amount = item.get("amount")
    if (isinstance(amount, bool) or not isinstance(amount, (int, float))
            or not math.isfinite(amount) or amount < 0):
        raise ValueError("R7_SETTLEMENT_PENDING_INVALID")


def _load_inputs(raw_root: Path, r6_root: Path, materialized_path: Path, policy: dict) -> tuple[list[dict], dict, dict, dict]:
    if any(p.is_symlink() for p in (raw_root, r6_root, materialized_path)):
        raise ValueError("R7_PRIVATE_INPUT_SYMLINK")
    if _sha(raw_root / "manifest.json") != policy["data"]["raw_manifest_sha256"]:
        raise ValueError("R7_RAW_MANIFEST_MISMATCH")
    if _sha(r6_root / "manifest.json") != policy["data"]["r6_manifest_sha256"]:
        raise ValueError("R7_R6_MANIFEST_MISMATCH")
    if _sha(materialized_path) != policy["data"]["r6_materialized_file_sha256"]:
        raise ValueError("R7_R6_MATERIALIZED_MISMATCH")
    if _sha(raw_root / "tqqq_qqq_guard_cash_contract.v1.json") != policy["data"]["tqqq_contract_sha256"]:
        raise ValueError("R7_TQQQ_CONTRACT_MISMATCH")
    complete = json.loads((r6_root / "complete.json").read_bytes())
    if complete["schema"] != "soxl-v7-r6-twelve-single-completion.v1":
        raise ValueError("R7_R6_COMPLETION_INVALID")
    for name in ("binding", "manifest", "closes", "assurance"):
        content = (r6_root / f"{name}.json").read_bytes()
        if _bytes_sha(content) != complete["p1"][f"{name}.json"]["sha256"]:
            raise ValueError("R7_R6_MEMBER_MISMATCH")
    if (complete["study_id"] != policy["soxl_input_study_id"]
            or complete["source_assurance"] != policy["soxl_input_assurance"]
            or complete["historical_point_in_time_certified"] is not False):
        raise ValueError("R7_R6_IDENTITY_MISMATCH")
    materialized = json.loads(materialized_path.read_bytes())
    if (materialized["p1_identity"]["input_manifest_sha256"] != policy["data"]["r6_manifest_sha256"]
            or materialized["p2_identity"]["config_sha256"] != V7_CONFIG_SHA256
            or len(materialized["sessions"]) != 914):
        raise ValueError("R7_R6_SIGNAL_INPUT_INVALID")
    indicators = {item["as_of"][:10]: item["market_data"]["derived_indicators"]
                  for item in materialized["sessions"]}
    if len(indicators) != len(materialized["sessions"]):
        raise ValueError("R7_R6_SIGNAL_DUPLICATE")
    _old, _s4, tqqq_contract, rows, actions = _load(raw_root)
    manifest = json.loads((raw_root / "manifest.json").read_bytes())
    raw_by_symbol: dict[str, dict[str, dict]] = {}
    for symbol in ("SOXL", "SOXX"):
        for kind in ("bars", "actions"):
            item = next(x for x in manifest["inputs"] if x["symbol"] == symbol and x["kind"] == kind)
            if len(item["pages"]) != 1 or not item["complete_pagination"]:
                raise ValueError("R7_RAW_PAGINATION_INCOMPLETE")
            page = item["pages"][0]
            path = raw_root / kind / symbol / "page-001.json"
            if (path.is_symlink() or path.stat().st_size != page["bytes"]
                    or _sha(path) != page["sha256"]):
                raise ValueError("R7_RAW_PAGE_MISMATCH")
            payload = json.loads(path.read_bytes())
            if payload["next_page_token"] is not None:
                raise ValueError("R7_RAW_PAGINATION_INCOMPLETE")
            if kind == "bars":
                if payload["symbol"] != symbol:
                    raise ValueError("R7_RAW_SYMBOL_MISMATCH")
                dates = [bar["t"][:10] for bar in payload["bars"]]
                if dates != sorted(set(dates)) or len(dates) != item["count"]:
                    raise ValueError("R7_RAW_DATES_INVALID")
                raw_by_symbol[symbol] = dict(zip(dates, payload["bars"], strict=True))
            else:
                actions[symbol] = payload["corporate_actions"]
    for row in rows:
        day = row["date"]
        for symbol in ("SOXL", "SOXX"):
            bar = raw_by_symbol[symbol].get(day)
            if bar is None:
                raise ValueError("R7_RAW_COMMON_DATE_MISSING")
            prices = [float(bar[k]) for k in ("o", "h", "l", "c", "v")]
            if (not all(math.isfinite(v) and v > 0 for v in prices)
                    or prices[1] < max(prices[0], prices[2], prices[3])
                    or prices[2] > min(prices[0], prices[3])):
                raise ValueError("R7_RAW_BAR_INVALID")
            row[symbol.lower() + "_open"] = prices[0]
            row[symbol.lower() + "_close"] = prices[3]
    r6_closes = json.loads((r6_root / "closes.json").read_bytes())["series"]
    r6_close = {symbol: {item["session_date"]: float(item["close"]) for item in r6_closes[symbol]}
                for symbol in ("SOXL", "SOXX")}
    unit_gap = _unit_diagnostic(rows, actions, r6_close, policy)
    first, last = policy["data"]["first_trade"], policy["data"]["last_session"]
    days = [row["date"] for row in rows if first <= row["date"] <= last]
    if (len(days) != policy["data"]["expected_replay_sessions"] or days[0] != first or days[-1] != last
            or any(day not in indicators for day in [policy["data"]["first_signal"], *days])):
        raise ValueError("R7_COMMON_WINDOW_INVALID")
    return rows, actions, indicators, {"unit_gap": unit_gap, "tqqq_contract": tqqq_contract}


def _unit_diagnostic(rows: list[dict], actions: dict, r6_close: dict, policy: dict) -> dict:
    """Compare event-derived units, never infer split factors from price ratios."""
    result = {}
    for symbol in ("SOXL", "SOXX"):
        splits = actions[symbol].get("forward_splits", [])
        if any(item["symbol"] != symbol or float(item["old_rate"]) <= 0
               or float(item["new_rate"]) <= 0 for item in splits):
            raise ValueError("R7_SPLIT_EVENT_INVALID")
        gaps = []
        for row in rows:
            day = row["date"]
            if day not in r6_close[symbol]:
                continue
            factor = math.prod(float(item["old_rate"]) / float(item["new_rate"])
                               for item in splits if item["ex_date"] > day)
            expected_adjusted = row[symbol.lower() + "_close"] * factor
            gap = abs(r6_close[symbol][day] / expected_adjusted - 1.0)
            gaps.append(gap)
        if not gaps or max(gaps) > policy["data"]["cross_source_unit_diagnostic_max_relative_gap"]:
            raise ValueError("R7_SIGNAL_RAW_UNIT_DIAGNOSTIC_FAILED")
        result[symbol] = {"max_relative_close_gap": max(gaps), "compared_sessions": len(gaps),
                          "event_split_count": len(splits)}
    return result


def _events(actions: dict) -> dict:
    result = {}
    for symbol in SYMBOLS:
        source = actions[symbol]
        splits = {}
        dividends = defaultdict(list)
        for item in source.get("forward_splits", []):
            ratio = float(item["new_rate"]) / float(item["old_rate"])
            if (item["symbol"] != symbol or ratio <= 0 or not math.isfinite(ratio)
                    or item["ex_date"] in splits):
                raise ValueError("R7_SPLIT_EVENT_INVALID")
            splits[item["ex_date"]] = ratio
        for item in source.get("cash_dividends", []):
            if (item["symbol"] != symbol or not all(item.get(key) for key in
                    ("ex_date", "payable_date", "process_date"))
                    or item["payable_date"] < item["ex_date"]
                    or not math.isfinite(float(item["rate"])) or float(item["rate"]) < 0):
                raise ValueError("R7_DIVIDEND_EVENT_INVALID")
            dividends[item["ex_date"]].append(item)
        result[symbol] = {"splits": splits, "dividends": dict(dividends)}
    return result


def _book(cash: float, symbols: tuple[str, ...]) -> dict:
    return {"cash": cash, "shares": {symbol: 0 for symbol in symbols}, "pending": [], "claims": []}


def _book_value(book: dict, prices: dict[str, float]) -> float:
    return math.fsum((book["cash"],
                      *(item["amount"] for item in book["pending"]),
                      *(item["amount"] for item in book["claims"] if not item["paid"]),
                      *(qty * prices[symbol] for symbol, qty in book["shares"].items())))


def _unrecognized(book: dict, signal_day: str) -> float:
    return math.fsum(item["amount"] for item in book["claims"]
                     if item["process_date"] >= signal_day)


def _book_decision_equity(book: dict, signal_day: str, prices: dict[str, float]) -> float:
    value = _book_value(book, prices) - _unrecognized(book, signal_day)
    if value < -1e-7 or not math.isfinite(value):
        raise ValueError("R7_OWNER_DECISION_EQUITY_INVALID")
    return max(0.0, value)


def _eligible_cash(book: dict, signal_day: str) -> float:
    restricted = math.fsum(item["amount"] for item in book["claims"]
                           if item["paid"] and item["process_date"] >= signal_day)
    if restricted > book["cash"] + 1e-7:
        raise ValueError("R7_RESTRICTED_CASH_IDENTITY")
    return max(0.0, book["cash"] - restricted)


def _prior_prices(row: dict) -> dict[str, float]:
    return {symbol: float(row[symbol.lower() + "_close"]) for symbol in SYMBOLS}


def _open_prices(row: dict) -> dict[str, float]:
    return {symbol: float(row[symbol.lower() + "_open"]) for symbol in SYMBOLS}


def _soxl_signal(indicators: dict, book: dict, signal_day: str,
                 closes: dict[str, float], budget: float) -> dict:
    """Call the unchanged V7 core on actual owner equity, then apply R7 cap."""
    equity = _book_decision_equity(book, signal_day, closes)
    if equity <= 0 or budget <= 0:
        raise ValueError("R7_SOXL_MEMBER_EQUITY_UNAVAILABLE")
    merged = {**soxl_soxx_trend_income_manifest.default_config, **V7_FROZEN_RUNTIME_CONFIG}
    config = {key: value for key, value in merged.items()
              if key not in UNSUPPORTED_V7_WRAPPER_KEYS}
    if set(merged) - set(config) != UNSUPPORTED_V7_WRAPPER_KEYS:
        raise ValueError("R7_V7_CONFIG_DRIFT")
    market_values = {symbol: book["shares"][symbol] * closes[symbol]
                     for symbol in OWNER_SYMBOLS["soxl"]}
    plan = build_rebalance_plan(
        indicators,
        {"available_cash": _eligible_cash(book, signal_day),
         "market_values": market_values,
         "quantities": dict(book["shares"]),
         "sellable_quantities": dict(book["shares"]),
         "total_strategy_equity": equity,
         "metadata": {}},
        translator=default_translator, **config,
    )
    targets = {symbol: float(plan["targets"][symbol]) for symbol in OWNER_SYMBOLS["soxl"]}
    if (any(not math.isfinite(value) or value < 0 for value in targets.values())
            or math.fsum(targets.values()) > equity + 1e-7):
        raise ValueError("R7_SOXL_CORE_TARGET_INVALID")
    scale = min(1.0, budget / equity)
    return {"targets": {symbol: value * scale for symbol, value in targets.items()},
            "uncapped_targets": targets, "owner_decision_equity": equity,
            "cap_scale": scale, "blend_tier": plan["blend_tier"],
            "reserved_cash": float(plan["reserved_cash"]),
            "current_min_trade": float(plan["current_min_trade"]),
            "threshold_value": float(plan["threshold_value"])}


def _corporate_actions(books: dict, events: dict, day: str) -> tuple[dict, float]:
    ratios = {symbol: 1.0 for symbol in SYMBOLS}
    accrued = 0.0
    for symbol in SYMBOLS:
        ratio = events[symbol]["splits"].get(day, 1.0)
        ratios[symbol] = ratio
        for book in books.values():
            if symbol not in book["shares"]:
                continue
            book["shares"][symbol] *= ratio
            for event in events[symbol]["dividends"].get(day, []):
                amount = book["shares"][symbol] * float(event["rate"])
                if amount:
                    book["claims"].append({"symbol": symbol, "amount": amount,
                        "process_date": event["process_date"], "payable_date": event["payable_date"],
                        "paid": False})
                    accrued += amount
    return ratios, accrued


def _release(books: dict, day: str, index: int, settlement_policy: dict | None = None,
             released_out: list | None = None) -> tuple[float, float]:
    for book in books.values():
        for item in book["pending"]:
            _require_pending_settlement(item, settlement_policy)
    if released_out is not None:
        released_out.clear()
    proceeds = paid = 0.0
    for book in books.values():
        if settlement_policy is None:
            due = [item for item in book["pending"] if item["release"] <= index]
            book["pending"] = [item for item in book["pending"] if item["release"] > index]
        else:
            due = [item for item in book["pending"] if item["settlement_date"] <= day]
            book["pending"] = [item for item in book["pending"] if item["settlement_date"] > day]
            if released_out is not None:
                released_out.extend({"settlement_date": item["settlement_date"],
                                     "observation_date": day, "amount": item["amount"]}
                                    for item in due)
        amount = math.fsum(item["amount"] for item in due)
        book["cash"] += amount
        proceeds += amount
        for claim in book["claims"]:
            if not claim["paid"] and claim["payable_date"] <= day:
                book["cash"] += claim["amount"]
                claim["paid"] = True
                paid += claim["amount"]
    return proceeds, paid


def _sell_to_targets(books: dict, targets: dict, opens: dict[str, float],
                     fee_rate: float, index: int, settlement_policy: dict | None = None,
                     trade_day: str | None = None) -> tuple[dict, dict]:
    trades = {owner: {symbol: 0 for symbol in symbols} for owner, symbols in OWNER_SYMBOLS.items()}
    fees = {owner: {symbol: 0.0 for symbol in symbols} for owner, symbols in OWNER_SYMBOLS.items()}
    for owner in OWNERS:
        book = books[owner]
        for symbol in OWNER_SYMBOLS[owner]:
            target_qty = math.floor(targets[owner][symbol] / opens[symbol])
            quantity = max(0, book["shares"][symbol] - target_qty)
            if not quantity:
                continue
            cost = quantity * opens[symbol] * fee_rate
            book["shares"][symbol] -= quantity
            amount = quantity * opens[symbol] - cost
            if settlement_policy is None:
                book["pending"].append({"symbol": symbol, "amount": amount, "release": index + 2})
            else:
                if trade_day is None:
                    raise ValueError("R7_SETTLEMENT_TRADE_DATE_MISSING")
                book["pending"].append({
                    "symbol": symbol, "amount": amount,
                    "settlement_date": _settlement_date(trade_day, settlement_policy),
                    "settlement_policy_id": settlement_policy["policy_id"],
                    "settlement_calendar_id": settlement_policy["calendar_id"],
                })
            trades[owner][symbol] -= quantity
            fees[owner][symbol] += cost
    return trades, fees


def _transfer(source: dict, destination: dict, amount: float, signal_day: str) -> None:
    if not math.isfinite(amount) or amount < -1e-9 or amount > _eligible_cash(source, signal_day) + 1e-7:
        raise ValueError("R7_OWNER_TRANSFER_INVALID")
    source["cash"] -= amount
    destination["cash"] += amount


def _fund_owners(books: dict, budgets: dict, prior_owner_equity: dict,
                 opens: dict[str, float], signal_day: str, outer_cash_target: float,
                 soxl_signal: dict | None, tqqq_target: float, *,
                 sweep_zero_budget: bool = False,
                 aggregate_member_cap_usd: float | None = None) -> dict:
    transferred = {owner: 0.0 for owner in ("tqqq", "soxl")}
    for owner in ("tqqq", "soxl"):
        budget = budgets[owner]
        if budget <= 0 and not sweep_zero_budget:
            continue
        book = books[owner]
        current_equity = _book_decision_equity(book, signal_day, opens)
        if owner == "soxl":
            reserve = 0.0 if soxl_signal is None else max(0.0, float(soxl_signal["reserved_cash"]))
        else:
            reserve = max(0.0, budget - tqqq_target)
        excess = min(max(0.0, current_equity - budget),
                     max(0.0, _eligible_cash(book, signal_day) - reserve))
        if excess:
            _transfer(book, books["outer"], excess, signal_day)
            transferred[owner] -= excess
    for owner in ("tqqq", "soxl"):
        budget = budgets[owner]
        if budget <= 0:
            continue
        deficit = max(0.0, budget - prior_owner_equity[owner])
        deficit = min(deficit, max(0.0, budget - _book_decision_equity(books[owner], signal_day, opens)))
        available = max(0.0, _eligible_cash(books["outer"], signal_day) - outer_cash_target)
        amount = min(deficit, available)
        if aggregate_member_cap_usd is not None:
            member_equity = math.fsum(_book_decision_equity(books[name], signal_day, opens)
                                      for name in ("tqqq", "soxl"))
            amount = min(amount, max(0.0, aggregate_member_cap_usd - member_equity))
        if amount:
            _transfer(books["outer"], books[owner], amount, signal_day)
            transferred[owner] += amount
    return transferred


def _buy_to_targets(books: dict, targets: dict, opens: dict[str, float], signal_day: str,
                    fee_rate: float, budgets: dict, outer_cash_target: float,
                    soxl_signal: dict | None, trades: dict, fees: dict) -> list[dict]:
    shortages = []
    for owner in OWNERS:
        book = books[owner]
        for symbol in OWNER_SYMBOLS[owner]:
            target_qty = math.floor(targets[owner][symbol] / opens[symbol])
            desired = max(0, target_qty - book["shares"][symbol])
            if not desired:
                continue
            if owner == "outer":
                reserve = outer_cash_target
            elif owner == "soxl":
                reserve = float(soxl_signal["reserved_cash"])
            else:
                reserve = max(0.0, budgets["tqqq"] - targets["tqqq"]["TQQQ"])
            available = max(0.0, _eligible_cash(book, signal_day) - reserve)
            fill = min(desired, math.floor((available + 1e-9) / (opens[symbol] * (1 + fee_rate))))
            if owner == "soxl":
                minimum = max(float(soxl_signal["current_min_trade"]),
                              float(soxl_signal["threshold_value"]))
                if desired * opens[symbol] < minimum - 1e-9 or fill * opens[symbol] < minimum - 1e-9:
                    fill = 0
            if fill:
                cost = fill * opens[symbol] * fee_rate
                book["shares"][symbol] += fill
                book["cash"] -= fill * opens[symbol] + cost
                trades[owner][symbol] += fill
                fees[owner][symbol] += cost
            if fill < desired:
                shortages.append({"owner": owner, "symbol": symbol,
                                  "desired_shares": desired, "filled_shares": fill})
            if book["cash"] < -1e-7:
                raise ValueError("R7_NEGATIVE_SETTLED_CASH")
    return shortages


def _replay(rows: list[dict], actions: dict, indicators: dict, contract: dict,
            policy: dict, *, path_name: str, cost_bps: int,
            short_sessions: int | None = None, action_selector=None,
            candidate_id: str | None = None,
            continuation_last_session: str | None = None,
            continuation_from_session: str | None = None,
            continuation_checkpoint: dict | None = None,
            checkpoint_out: dict | None = None,
            settlement_policy: dict | None = None,
            capital_hook=None) -> tuple[dict, list[dict]]:
    path = policy["paths"][path_name]
    by_date = {row["date"]: index for index, row in enumerate(rows)}
    start = by_date[policy["data"]["first_trade"]]
    if rows[start - 1]["date"] != policy["data"]["first_signal"]:
        raise ValueError("R7_FIRST_SIGNAL_CHANGED")
    loop_start = start
    initial = float(policy["initial_research_nav_usd"])
    if continuation_checkpoint is not None:
        if continuation_from_session is None or continuation_from_session not in by_date:
            raise ValueError("R7_CONTINUATION_CHECKPOINT_BOUNDARY_INVALID")
        boundary_index = by_date[continuation_from_session]
        if (continuation_checkpoint.get("last_date") != continuation_from_session
                or continuation_checkpoint.get("last_global_index") != boundary_index
                or set(continuation_checkpoint.get("books", {})) != set(OWNERS)
                or set(continuation_checkpoint.get("books", {}).keys()) != set(OWNERS)
                or continuation_checkpoint.get("prior_nav_usd", 0) <= 0
                or continuation_checkpoint.get("previous_action") not in policy["paths"]):
            raise ValueError("R7_CONTINUATION_CHECKPOINT_INVALID")
        if settlement_policy is None:
            if ("settlement_policy_id" in continuation_checkpoint
                    or "settlement_calendar_id" in continuation_checkpoint):
                raise ValueError("R7_SETTLEMENT_CHECKPOINT_UNEXPECTED")
        elif (continuation_checkpoint.get("settlement_policy_id") != settlement_policy["policy_id"]
                or continuation_checkpoint.get("settlement_calendar_id") != settlement_policy["calendar_id"]):
            raise ValueError("R7_SETTLEMENT_CHECKPOINT_IDENTITY_MISMATCH")
        else:
            for book in continuation_checkpoint["books"].values():
                for item in book.get("pending", []):
                    _require_pending_settlement(item, settlement_policy)
        if capital_hook is None:
            if any(key in continuation_checkpoint for key in (
                    "capital_policy_id", "capital_w_usd", "capital_seen_event_ids")):
                raise ValueError("POST_R9_CAPITAL_CHECKPOINT_UNEXPECTED")
        else:
            capital_hook.restore(continuation_checkpoint)
        loop_start = boundary_index + 1
    elif continuation_from_session is not None:
        raise ValueError("R7_CONTINUATION_CHECKPOINT_MISSING")
    if short_sessions is not None and (continuation_last_session is not None
                                       or continuation_checkpoint is not None):
        raise ValueError("R7_CONTINUATION_SHORT_WINDOW_CONFLICT")
    if continuation_last_session is None:
        end = len(rows) if short_sessions is None else start + short_sessions
    else:
        end_by_date = {row["date"]: index for index, row in enumerate(rows)}
        if continuation_last_session not in end_by_date:
            raise ValueError("R7_CONTINUATION_END_MISSING")
        if continuation_last_session <= policy["data"]["last_session"]:
            raise ValueError("R7_CONTINUATION_END_NOT_AFTER_PREFIX")
        end = end_by_date[continuation_last_session] + 1
        if end <= loop_start:
            raise ValueError("R7_CONTINUATION_WINDOW_INVALID")
    if end > len(rows) or (short_sessions is not None and not 1 <= short_sessions <= 12):
        raise ValueError("R7_SHORT_WINDOW_INVALID")
    books = (copy.deepcopy(continuation_checkpoint["books"])
             if continuation_checkpoint is not None else {
                 "outer": _book(initial * (1 - path["tqqq_cap"] - path["soxl_cap"]), OWNER_SYMBOLS["outer"]),
                 "tqqq": _book(initial * path["tqqq_cap"], OWNER_SYMBOLS["tqqq"]),
                 "soxl": _book(initial * path["soxl_cap"], OWNER_SYMBOLS["soxl"]),
             })
    events = _events(actions)
    fee_rate = cost_bps / 10_000.0
    ledger = []
    prior_nav = (float(continuation_checkpoint["prior_nav_usd"])
                 if continuation_checkpoint is not None else initial)
    replay_initial_nav = prior_nav
    replay_initial_date = continuation_from_session or policy["data"]["first_signal"]
    previous_action = (continuation_checkpoint["previous_action"]
                       if continuation_checkpoint is not None else path_name)
    for index in range(loop_start, end):
        row = rows[index]
        day = row["date"]
        signal_day = rows[index - 1]["date"]
        prior_closes = _prior_prices(rows[index - 1])
        opens = _open_prices(row)
        closes = _prior_prices(row)
        prior_owner_equity = {owner: _book_decision_equity(book, signal_day, prior_closes)
                              for owner, book in books.items()}
        decision_equity = math.fsum(prior_owner_equity.values())
        if decision_equity <= 0 or not math.isfinite(decision_equity):
            raise ValueError("R7_DECISION_EQUITY_INVALID")
        selection = None
        frozen = None if capital_hook is None else capital_hook.freeze_actual(decision_equity)
        if action_selector is not None:
            if frozen is None:
                selection = action_selector(rows, index, books, decision_equity,
                                            prior_closes, previous_action, cost_bps)
            else:
                selection = action_selector(
                    rows, index, books, decision_equity, prior_closes, previous_action, cost_bps,
                    capital_freeze=frozen)
            selected = selection["selected_action"]
            if selected not in policy["paths"]:
                raise ValueError("R8_ACTION_NOT_IN_FROZEN_SET")
            path = policy["paths"][selected] if frozen is None else frozen.weights(selected)
        else:
            selected = path_name
            if frozen is not None:
                path = frozen.weights(selected)
        budgets = {owner: decision_equity * path[owner + "_cap"] for owner in ("tqqq", "soxl")}
        tqqq_signal = None
        tqqq_target = 0.0
        if budgets["tqqq"]:
            tqqq_signal = _decision(rows, index - 1, shares=books["tqqq"]["shares"]["TQQQ"],
                                    nav=decision_equity, use_guard=True, config=contract,
                                    member_budget_usd=budgets["tqqq"])
            if tqqq_signal["signal_date"] != signal_day:
                raise ValueError("R7_TQQQ_SIGNAL_DATE_INVALID")
            tqqq_target = float(tqqq_signal["target_tqqq_usd"])
        soxl_signal = None
        if budgets["soxl"] and prior_owner_equity["soxl"] > 0:
            soxl_signal = _soxl_signal(indicators[signal_day], books["soxl"], signal_day,
                                       prior_closes, budgets["soxl"])
        targets = {
            "outer": {"QQQM": decision_equity * path["qqqm"],
                      "BOXX": decision_equity * path["outer_boxx"]},
            "tqqq": {"TQQQ": tqqq_target},
            "soxl": ({symbol: 0.0 for symbol in OWNER_SYMBOLS["soxl"]}
                     if soxl_signal is None else dict(soxl_signal["targets"])),
        }
        outer_cash_target = decision_equity * path["outer_cash"]
        old_shares = {owner: dict(book["shares"]) for owner, book in books.items()}
        split_ratios, accrued = _corporate_actions(books, events, day)
        released_records: list = []
        if settlement_policy is None:
            released, paid = _release(books, day, index)
            trades, fees = _sell_to_targets(books, targets, opens, fee_rate, index)
        else:
            released, paid = _release(books, day, index, settlement_policy, released_records)
            trades, fees = _sell_to_targets(books, targets, opens, fee_rate, index,
                                            settlement_policy, trade_day=day)
        if frozen is not None:
            aggregate_cap = frozen.aggregate_member_cap_usd
        elif action_selector is not None:
            aggregate_cap = decision_equity * 0.05
        else:
            aggregate_cap = None
        transfers = _fund_owners(books, budgets, prior_owner_equity, opens, signal_day,
                                 outer_cash_target, soxl_signal, tqqq_target,
                                 sweep_zero_budget=action_selector is not None,
                                 aggregate_member_cap_usd=aggregate_cap)
        shortages = _buy_to_targets(books, targets, opens, signal_day, fee_rate, budgets,
                                    outer_cash_target, soxl_signal, trades, fees)
        owner_nav = {owner: _book_value(book, closes) for owner, book in books.items()}
        nav = math.fsum(owner_nav.values())
        values = {owner: {symbol: qty * closes[symbol] for symbol, qty in book["shares"].items()}
                  for owner, book in books.items()}
        total_cost = math.fsum(cost for item in fees.values() for cost in item.values())
        expected_nav = prior_nav + accrued - total_cost
        for owner in OWNERS:
            for symbol in OWNER_SYMBOLS[owner]:
                expected_nav += old_shares[owner][symbol] * (
                    split_ratios[symbol] * closes[symbol] - prior_closes[symbol])
                expected_nav += trades[owner][symbol] * (closes[symbol] - opens[symbol])
        identity_error = abs(nav - expected_nav)
        if identity_error > 1e-6 or nav <= 0 or not math.isfinite(nav):
            raise ValueError("R7_ACCOUNT_IDENTITY_FAILED:" + day)
        settled_cash = math.fsum(book["cash"] for book in books.values())
        pending = math.fsum(item["amount"] for book in books.values() for item in book["pending"])
        receivable = math.fsum(item["amount"] for book in books.values()
                               for item in book["claims"] if not item["paid"])
        security_value = math.fsum(amount for owner in values.values() for amount in owner.values())
        account_identity = abs(nav - math.fsum((settled_cash, pending, receivable, security_value)))
        if account_identity > 1e-6:
            raise ValueError("R7_OWNER_AGGREGATION_FAILED")
        reference = None
        if capital_hook is not None:
            reference = capital_hook.apply_period_reference(
                previous_equity=prior_nav, ending_equity=nav, events=())
        nominal_leverage = (values["outer"]["QQQM"] + 3 * values["tqqq"]["TQQQ"]
                            + 3 * values["soxl"]["SOXL"] + values["soxl"]["SOXX"])
        traded_notional = math.fsum(abs(quantity) * opens[symbol]
                                    for owner, item in trades.items()
                                    for symbol, quantity in item.items())
        ledger.append({
            "date": day, "signal_date": signal_day, "path": selected, "cost_bps": cost_bps,
            "candidate_id": candidate_id or policy["candidate_id"],
            "decision_equity_usd": decision_equity,
            "unrecognized_claim_at_signal_usd": prior_nav - decision_equity,
            "member_budget_usd": budgets, "owner_decision_equity_usd": prior_owner_equity,
            "target_usd": targets, "cash_target_usd": outer_cash_target,
            "tqqq_guard_route": None if tqqq_signal is None else tqqq_signal["guard_route"],
            "tqqq_core_state": None if tqqq_signal is None else tqqq_signal["core_state"],
            "soxl_blend_tier": None if soxl_signal is None else soxl_signal["blend_tier"],
            "soxl_cap_scale": None if soxl_signal is None else soxl_signal["cap_scale"],
            "soxl_current_min_trade_usd": None if soxl_signal is None else soxl_signal["current_min_trade"],
            "soxl_internal_reserve_target_usd": None if soxl_signal is None else soxl_signal["reserved_cash"],
            "trade_shares": trades, "trade_cost_usd": fees, "total_cost_usd": total_cost,
            "traded_notional_usd": traded_notional, "shortages": shortages,
            "owner_transfers_usd": transfers, "split_ratios": split_ratios,
            "owner_shares": {owner: dict(book["shares"]) for owner, book in books.items()},
            "owner_security_values_usd": values, "owner_nav_usd": owner_nav,
            "owner_settled_cash_usd": {owner: book["cash"] for owner, book in books.items()},
            "owner_pending_sale_usd": {owner: math.fsum(item["amount"] for item in book["pending"])
                                       for owner, book in books.items()},
            "owner_receivable_usd": {owner: math.fsum(item["amount"] for item in book["claims"]
                                                       if not item["paid"]) for owner, book in books.items()},
            "dividend_accrued_usd": accrued, "sale_proceeds_released_usd": released,
            "dividend_paid_usd": paid, "economic_nav_close_usd": nav,
            "settled_cash_usd": settled_cash, "pending_sale_usd": pending, "receivable_usd": receivable,
            "tqqq_value_usd": values["tqqq"]["TQQQ"],
            "qqqm_value_usd": values["outer"]["QQQM"],
            "boxx_value_usd": values["outer"]["BOXX"] + values["soxl"]["BOXX"],
            "outer_boxx_value_usd": values["outer"]["BOXX"],
            "soxl_internal_boxx_value_usd": values["soxl"]["BOXX"],
            "soxl_value_usd": values["soxl"]["SOXL"],
            "soxx_value_usd": values["soxl"]["SOXX"],
            "outer_free_settled_cash_usd": max(0.0, _eligible_cash(books["outer"], signal_day)
                                                 - outer_cash_target),
            "restricted_paid_cash_usd": math.fsum(book["cash"] - _eligible_cash(book, signal_day)
                                                    for book in books.values()),
            "unrecognized_claim_usd": math.fsum(_unrecognized(book, day) for book in books.values()),
            "actual_security_value_to_nav": security_value / nav,
            "nasdaq_lookthrough_to_nav": nominal_leverage / nav,
            "account_identity_error_usd": max(identity_error, account_identity),
        })
        if selection is not None:
            ledger[-1]["action_selection"] = selection
        if frozen is not None:
            member_nav = owner_nav["tqqq"] + owner_nav["soxl"]
            overcap = max(0.0, member_nav - frozen.aggregate_member_cap_usd)
            ledger[-1].update({
                "wealth_reference_usd": frozen.wealth_reference_usd,
                "curve_ratio": frozen.curve_ratio,
                "aggregate_member_cap_usd": frozen.aggregate_member_cap_usd,
                "capital_binding": capital_hook.binding(
                    selected=selected, shortages=shortages, overcap=overcap,
                    ratio=frozen.curve_ratio),
                "overcap_residual_usd": overcap,
                "external_flow_usd": reference["external_flow_usd"],
                "investment_index": reference["investment_index"],
                "investment_high_water": reference["investment_high_water"],
                "capital_policy_id": frozen.policy_id,
                "capital_variant": frozen.variant,
            })
        if settlement_policy is not None:
            ledger[-1]["settlement_cash_releases"] = released_records
        prior_nav = nav
        previous_action = selected
    if short_sessions is None and continuation_last_session is None and continuation_checkpoint is None and (
            len(ledger) != policy["data"]["expected_replay_sessions"]
                                   or ledger[-1]["date"] != policy["data"]["last_session"]):
        raise ValueError("R7_FORMAL_WINDOW_CHANGED")
    if checkpoint_out is not None:
        if not ledger:
            raise ValueError("R7_CHECKPOINT_EMPTY_REPLAY")
        checkpoint = {"last_date": rows[end - 1]["date"],
                      "last_global_index": end - 1,
                      "prior_nav_usd": prior_nav,
                      "previous_action": previous_action,
                      "books": copy.deepcopy(books)}
        if settlement_policy is not None:
            checkpoint["settlement_policy_id"] = settlement_policy["policy_id"]
            checkpoint["settlement_calendar_id"] = settlement_policy["calendar_id"]
        if capital_hook is not None:
            checkpoint.update(capital_hook.export_state())
        checkpoint_out.update(checkpoint)
    metrics = _metrics(ledger, replay_initial_nav, replay_initial_date)
    for row in ledger:
        row["nominal_equity_index_exposure_to_nav"] = row.pop("nasdaq_lookthrough_to_nav")
    for prefix in ("average", "maximum"):
        metrics[f"{prefix}_nominal_equity_index_exposure_to_nav"] = metrics.pop(
            f"{prefix}_nasdaq_lookthrough_to_nav")
    metrics.update({
        "total_traded_notional_usd": math.fsum(x["traded_notional_usd"] for x in ledger),
        "turnover_side_notional_over_initial": math.fsum(x["traded_notional_usd"] for x in ledger) / initial,
        "shortage_event_count": sum(len(x["shortages"]) for x in ledger),
        "max_owner_cap_overage_usd": max((max(0.0, row["owner_nav_usd"][owner]
            - row["member_budget_usd"][owner]) for row in ledger for owner in ("tqqq", "soxl")
            if row["member_budget_usd"][owner] > 0), default=0.0),
        "average_soxl_internal_boxx_to_nav": statistics.mean(
            row["soxl_internal_boxx_value_usd"] / row["economic_nav_close_usd"] for row in ledger),
        "average_outer_boxx_to_nav": statistics.mean(
            row["outer_boxx_value_usd"] / row["economic_nav_close_usd"] for row in ledger),
        "average_soxl_member_settled_cash_to_nav": statistics.mean(
            row["owner_settled_cash_usd"]["soxl"] / row["economic_nav_close_usd"] for row in ledger),
        "average_tqqq_member_settled_cash_to_nav": statistics.mean(
            row["owner_settled_cash_usd"]["tqqq"] / row["economic_nav_close_usd"] for row in ledger),
    })
    return metrics, ledger


def analyze(raw_root: Path, r6_root: Path, materialized_path: Path,
            *, short_sessions: int | None = None) -> tuple[dict, dict[str, list[dict]]]:
    policy = _policy()
    rows, actions, indicators, source = _load_inputs(raw_root, r6_root, materialized_path, policy)
    results = {}
    ledgers = {}
    scenarios = [("B3", 10)] if short_sessions is not None else [
        (name, cost) for cost in policy["cost_bps_scenarios"] for name in policy["paths"]]
    for path_name, cost in scenarios:
        key = f"{path_name}_{cost}bps"
        results[key], ledgers[key] = _replay(rows, actions, indicators, source["tqqq_contract"],
                                            policy, path_name=path_name, cost_bps=cost,
                                            short_sessions=short_sessions)
    return ({"schema": "qsl.research.r7_joint_account_comparison.v1",
             "candidate_id": policy["candidate_id"], "research_only": True,
             "development": True, "source_assurance": policy["soxl_input_assurance"],
             "historical_point_in_time_certified": False,
             "original_v7_or_full_v2_admitted": False,
             "policy_sha256": POLICY_SHA256,
             "raw_manifest_sha256": policy["data"]["raw_manifest_sha256"],
             "r6_manifest_sha256": policy["data"]["r6_manifest_sha256"],
             "r6_materialized_file_sha256": policy["data"]["r6_materialized_file_sha256"],
             "runner_sha256": _sha(Path(__file__)),
             "v7_core_sha256": _sha(Path(build_rebalance_plan.__code__.co_filename)),
             "tqqq_core_sha256": _sha(Path(_decision.__code__.co_filename)),
             "unit_diagnostic": source["unit_gap"],
             "short_sessions": short_sessions,
             "results": results}, ledgers)


def run(raw_root: Path, r6_root: Path, materialized_path: Path,
        output_root: Path, *, short_sessions: int | None = None) -> dict:
    summary, ledgers = analyze(raw_root, r6_root, materialized_path,
                               short_sessions=short_sessions)
    output_root.mkdir(mode=0o700, parents=True, exist_ok=False)
    hashes = {}
    for key, ledger in sorted(ledgers.items()):
        content = _canonical(ledger)
        path = output_root / f"private_daily_{key}.json"
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
            stream.write(content)
        hashes[key] = _bytes_sha(content)
    summary["private_ledger_sha256"] = hashes
    content = _canonical(summary)
    with os.fdopen(os.open(output_root / "summary.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(content)
    return {"status": "COMPLETE", "candidate_id": summary["candidate_id"],
            "path_count": len(ledgers), "first_date": next(iter(summary["results"].values()))["start"],
            "last_date": next(iter(summary["results"].values()))["end"],
            "policy_sha256": POLICY_SHA256, "summary_sha256": _bytes_sha(content)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--r6-root", type=Path, required=True)
    parser.add_argument("--materialized", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--short-sessions", type=int)
    args = parser.parse_args()
    print(json.dumps(run(args.raw_root, args.r6_root, args.materialized,
                         args.output_root, short_sessions=args.short_sessions), sort_keys=True))
