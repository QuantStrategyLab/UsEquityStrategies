"""Partial, pure USD adapter for already-computed TQQQ guard-cash research.

No simulation, provider access, journal writes, or qualification occurs here.
The caller supplies a STARTED trial with declared params and input identity.
This pure helper checks accounting and explicit contradictions, not which params
actually produced an output. For admission, a trusted caller must freeze one
actual_params snapshot, construct STARTED, call _simulate from that same snapshot,
and immediately adapt that call's output. Arbitrary external outputs cannot be
admitted through this helper alone. Identical routes cannot prove guard identity.
Its research_identity must declare guard_cash_input_sha256 (canonical JSON of
rows/actions/contract), actions_source_id, and external_cashflow_scope equal to
closed_research_no_external_flows. A content-bound source event ID is derived
from the original action record and its source array index, never a native ID.

Pinned QPK cannot represent split-plus-trade sessions, zero-quantity actions,
or zero-rate dividends. Those valid simulator paths fail explicitly, preserving
source values. Strict QPK tolerance can also reject simulator-valid arithmetic;
this adapter never rounds or repairs it. Calendar/PIT/license remain unqualified.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
import math

from quant_platform_kit.strategy_lifecycle.contracts import (
    BacktestResult, ResearchDailyLedger, ResearchLedgerDay, ResearchLedgerEvent,
    ResearchPositionMark, ResearchTrialRecord, ResearchTrialStatus,
)

_CANDIDATE = "tqqq_qqq_guard_cash_research_v1"


def _digest(value: object) -> str:
    try:
        data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError("INPUT_NOT_CANONICAL_JSON") from exc
    return hashlib.sha256(data).hexdigest()


def _number(value: object, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("SIMULATOR_NUMBER_INVALID")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError("SIMULATOR_NUMBER_INVALID") from exc
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        raise ValueError("SIMULATOR_NUMBER_INVALID")
    return number


def _day(value: object) -> date:
    if not isinstance(value, str):
        raise ValueError("SIMULATOR_DATE_INVALID")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("SIMULATOR_DATE_INVALID")
    return parsed


def _same(left: float, right: float, code: str) -> None:
    if not math.isclose(left, right, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError(code)


def _actions(actions: dict) -> tuple[dict, dict]:
    """Validate complete supplied arrays, retaining original records and indexes."""
    if (type(actions) is not dict or not {"forward_splits", "cash_dividends"} <= set(actions)
            or any(type(value) is not list or value for key, value in actions.items()
                   if key not in {"forward_splits", "cash_dividends"})):
        raise ValueError("ACTION_SET_INCOMPLETE_OR_UNSUPPORTED")
    schedules = ({}, {})
    native_ids: set[str] = set()
    for key, schedule in zip(("forward_splits", "cash_dividends"), schedules, strict=True):
        if type(actions[key]) is not list:
            raise ValueError("ACTION_SET_INCOMPLETE_OR_UNSUPPORTED")
        for index, original in enumerate(actions[key]):
            if type(original) is not dict or original.get("symbol", "TQQQ") != "TQQQ":
                raise ValueError("ACTION_IDENTITY_INVALID")
            day = _day(original["ex_date"]).isoformat()
            native = original.get("id")
            if native is not None:
                if type(native) is not str or not native or native != native.strip():
                    raise ValueError("ACTION_IDENTITY_INVALID")
                if native in native_ids:
                    raise ValueError("ACTION_DUPLICATE_OR_CONFLICT")
                native_ids.add(native)
            # This single-candidate adapter has one split/distribution per ex-date.
            if day in schedule:
                raise ValueError("ACTION_DUPLICATE_OR_CONFLICT")
            if key == "forward_splits":
                new, old = _number(original["new_rate"]), _number(original["old_rate"])
                if new <= 0 or old <= 0:
                    raise ValueError("ACTION_RATIO_INVALID")
            else:
                rate = _number(original["rate"], minimum=0)
                if _day(original["payable_date"]) < _day(original["ex_date"]):
                    raise ValueError("ACTION_PAYMENT_DATE_INVALID")
            schedule[day] = (key, index, original)
    return schedules


def build_guard_cash_trial_ledger(
    *, rows: list[dict], actions: dict, contract: dict, metrics: dict,
    daily_rows: list[dict], started: ResearchTrialRecord, run_id: str,
    param_version: int, computed_at: str,
) -> tuple[BacktestResult, ResearchDailyLedger]:
    """Map declared identity and accounting; reject explicit contradictions/gaps.

    Input digest uses UTF-8 json.dumps({rows, actions, contract}, sort_keys=True,
    separators=(",", ":"), allow_nan=False). The caller retains the exact originals
    and source manifest; this is input binding, not provider authentication or
    proof of executed strategy params. A trusted caller must bind STARTED, the
    simulator invocation and its returned output using one frozen param snapshot.
    No terminal trial is constructed or persisted.
    """
    splits, dividends = _actions(actions)
    if type(rows) is not list or type(daily_rows) is not list or not daily_rows:
        raise ValueError("SIMULATOR_WINDOW_INVALID")
    start = contract["core"]["first_signal_min_qqq_bars"]
    if (type(start) is not int or start != 257 or contract.get("candidate_id") != _CANDIDATE
            or contract["window"]["first_signal_rule"] != "index 256 after 257 complete common bars"
            or len(rows) < start + len(daily_rows)):
        raise ValueError("SIMULATOR_WINDOW_INVALID")
    dates = [_day(row["date"]) for row in rows]
    if any(current <= previous for previous, current in zip(dates, dates[1:])):
        raise ValueError("SIMULATOR_WINDOW_INVALID")
    initial = _number(contract["portfolio"]["research_initial_usd"])
    if initial <= 0:
        raise ValueError("SIMULATOR_NUMBER_INVALID")
    params = None if not isinstance(started, ResearchTrialRecord) else started.actual_params
    identity = None if not isinstance(started, ResearchTrialRecord) else started.research_identity
    if (not isinstance(started, ResearchTrialRecord) or started.status is not ResearchTrialStatus.STARTED
            or started.domain != "us_equity" or started.strategy_profile != _CANDIDATE
            or started.window_start != dates[start - 1]
            or started.window_end != dates[start + len(daily_rows) - 1]
            or started.calendar_id != "XNYS" or started.periods_per_year != 252.0
            or not started.source_revision or not started.param_set_id or not started.cost_source
            or not isinstance(params, Mapping) or not isinstance(identity, Mapping)
            or "research_identity" in params
            or not isinstance(params.get("scenario_id"), str) or not params["scenario_id"]
            or any(c.isspace() for c in params["scenario_id"])
            or type(params.get("use_guard")) is not bool
            or (params.get("sessions") is not None and (type(params["sessions"]) is not int
                or params["sessions"] != len(daily_rows)))
            or (params.get("sessions") is None and len(daily_rows) != len(rows) - start)
            or type(params.get("cost_bps")) is not int or params["cost_bps"] < 0
            or dict(started.cost_inputs) != {"cost_bps": float(params["cost_bps"])}
            or not isinstance(identity.get("actions_source_id"), str)
            or not identity["actions_source_id"] or any(c.isspace() for c in identity["actions_source_id"])
            or identity.get("external_cashflow_scope") != "closed_research_no_external_flows"):
        raise ValueError("TRIAL_IDENTITY_MISMATCH")
    if identity.get("guard_cash_input_sha256") != _digest({"rows": rows, "actions": actions, "contract": contract}):
        raise ValueError("INPUT_IDENTITY_MISMATCH")
    if not isinstance(computed_at, str):
        raise ValueError("COMPUTED_AT_INVALID")
    stamp = datetime.fromisoformat(computed_at.replace("Z", "+00:00"))
    if stamp.utcoffset() is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError("COMPUTED_AT_INVALID")

    def event_id(source: tuple, phase: str) -> str:
        return "tqqq-source-event-sha256:" + _digest({
            "source_id": identity["actions_source_id"], "input_sha256": identity["guard_cash_input_sha256"],
            "trial_id": started.trial_id, "scenario_id": params["scenario_id"],
            "source_array": source[0], "source_index": source[1], "original_action": source[2],
            "accounting_phase": phase,
        })

    days: list[ResearchLedgerDay] = []
    replay_dates = {day.isoformat() for day in dates[start:start + len(daily_rows)]}
    for effective in set(splits) | set(dividends):
        if dates[start - 1] < _day(effective) <= started.window_end and effective not in replay_dates:
            raise ValueError("ACTION_EFFECTIVE_SESSION_MISSING")
    prior_shares, prior_nav = 0.0, initial
    pending: dict[str, list[tuple[tuple, str, float]]] = {}
    pending_cash: dict[str, float] = {}
    for offset, row in enumerate(daily_rows):
        original = rows[start + offset]
        day = original["date"]
        if row["date"] != day or row["signal_date"] != rows[start + offset - 1]["date"]:
            raise ValueError("SIMULATOR_WINDOW_INVALID")
        applied_route = row.get("signal_applied_route")
        if not isinstance(applied_route, str) or applied_route not in {"no_action", "risk_reduced", "risk_off"}:
            raise ValueError("SIMULATOR_ROUTE_INVALID")
        if not params["use_guard"] and applied_route != "no_action":
            raise ValueError("GUARD_SCENARIO_CONTRADICTION")
        opening = _number(row["tqqq_open"])
        closing = _number(row["tqqq_close"])
        if opening <= 0 or closing <= 0 or opening != original["tqqq_open"] or closing != original["tqqq_close"]:
            raise ValueError("SIMULATOR_PRICE_MISMATCH")
        shares = _number(row["shares"], minimum=0)
        trade = _number(row["trade_shares"])
        fees = _number(row["cost_usd"], minimum=0)
        if not shares.is_integer():
            raise ValueError("SIMULATOR_INTEGER_POSITION_REQUIRED")
        events: list[ResearchLedgerEvent] = []
        split = splits.get(day)
        ratio = 1.0 if split is None else float(split[2]["new_rate"]) / float(split[2]["old_rate"])
        if row["split_ratio"] != ratio:
            raise ValueError("SPLIT_EVENT_MISMATCH")
        entitlement = prior_shares * ratio
        if split is not None:
            if trade != 0 or fees != 0:
                raise ValueError("QPK_SPLIT_TRADE_UNSUPPORTED")
            if prior_shares <= 0:
                raise ValueError("QPK_ZERO_ENTITLEMENT_UNSUPPORTED")
            events.append(ResearchLedgerEvent(event_id(split, "split"), "split", "TQQQ", ratio=ratio))
        _same(shares, entitlement + trade, "SIMULATOR_TRADE_QUANTITY_MISMATCH")
        _same(fees, abs(trade) * opening * (params["cost_bps"] / 10000.0), "SIMULATOR_COST_MISMATCH")
        accrued = 0.0
        dividend = dividends.get(day)
        if dividend is not None:
            if dividend[2]["rate"] == 0:
                raise ValueError("QPK_ZERO_DIVIDEND_UNSUPPORTED")
            if entitlement <= 0:
                raise ValueError("QPK_ZERO_ENTITLEMENT_UNSUPPORTED")
            event = dividend[2]
            accrued = entitlement * float(event["rate"])
            accrual_id = event_id(dividend, "dividend_accrual")
            events.append(ResearchLedgerEvent(accrual_id, "dividend_accrual", "TQQQ", per_share=event["rate"]))
            pay = event["payable_date"]
            pending.setdefault(pay, []).append((dividend, accrual_id, accrued))
            pending_cash[pay] = pending_cash.get(pay, 0.0) + accrued
        _same(_number(row["dividend_accrued_usd"], minimum=0), accrued, "DIVIDEND_ACCRUAL_MISMATCH")
        # Exactly the simulator's pending-pay-date grouping and next-row settlement.
        income = sum(amount for pay, amount in pending_cash.items() if pay <= day)
        for pay in [pay for pay in pending if pay <= day]:
            for source, reference, amount in pending.pop(pay):
                events.append(ResearchLedgerEvent(event_id(source, "dividend_payment"), "dividend_payment",
                                                 "TQQQ", amount=amount, reference_event_id=reference))
            del pending_cash[pay]
        if _number(row.get("external_cashflow", 0.0)) != 0.0:
            raise ValueError("EXTERNAL_FLOW_SCOPE_MISMATCH")
        if _number(row["identity_error_usd"], minimum=0) > 1e-6:
            raise ValueError("SIMULATOR_IDENTITY_ERROR")
        nav = _number(row["nav_close"])
        positions = () if shares == 0 else (ResearchPositionMark("TQQQ", shares, shares * closing),)
        days.append(ResearchLedgerDay(
            session_date=dates[start + offset], cash=_number(row["cash_usd"], minimum=0),
            positions=positions, trade_net_cashflow=-trade * opening, fees=fees, nav=nav,
            daily_return=nav / (prior_nav + 0.0) - 1.0, income_cashflow=income,
            external_cashflow=0.0, dividend_receivable=_number(row["receivable_usd"], minimum=0),
            declared_event_ids=tuple(event.event_id for event in events), events=tuple(events)))
        prior_shares, prior_nav = shares, nav
    ledger = ResearchDailyLedger(
        trial_id=started.trial_id, domain=started.domain, strategy_profile=started.strategy_profile,
        run_id=run_id, param_version=param_version, input_id=started.input_id,
        calendar_id=started.calendar_id, periods_per_year=started.periods_per_year,
        cost_source=started.cost_source, cost_inputs=dict(started.cost_inputs),
        initial_session_date=dates[start - 1], initial_nav=initial, initial_cash=initial,
        initial_positions=(), days=tuple(days), synthetic=started.synthetic)
    peak, max_drawdown = initial, 0.0
    for day in days:
        peak = max(peak, day.nav)
        max_drawdown = min(max_drawdown, day.nav / peak - 1.0)
    expected_metrics = {
        "start": daily_rows[0]["date"], "end": daily_rows[-1]["date"], "sessions": len(days),
        "start_nav_usd": initial, "end_nav_usd": days[-1].nav, "total_return": ledger.total_return,
        "annualized_return_252_sessions": (days[-1].nav / initial) ** (252 / len(days)) - 1.0,
        "max_drawdown": max_drawdown, "trade_count": sum(row["trade_shares"] != 0 for row in daily_rows),
        "total_cost_usd": sum(row["cost_usd"] for row in daily_rows),
        "turnover_usd": sum(abs(row["trade_shares"] * row["tqqq_open"]) for row in daily_rows),
        "min_cash_usd": min(row["cash_usd"] for row in daily_rows),
        "max_ledger_identity_error_usd": max(row["identity_error_usd"] for row in daily_rows),
    }
    if type(metrics) is not dict:
        raise ValueError("METRICS_MISMATCH")
    for key, value in metrics.items():
        if key in {"sessions", "trade_count"}:
            if type(value) is not int:
                raise ValueError("METRICS_MISMATCH")
        elif key not in {"start", "end"}:
            _number(value)
    if metrics != expected_metrics:
        raise ValueError("METRICS_MISMATCH")
    result_params = deepcopy(dict(params))
    result_params["research_identity"] = deepcopy(dict(identity))
    result = BacktestResult(
        strategy_profile=started.strategy_profile, domain=started.domain,
        param_set_id=started.param_set_id, params=result_params, param_version=param_version,
        max_drawdown=metrics["max_drawdown"], cagr=metrics["annualized_return_252_sessions"],
        total_return=metrics["total_return"], start_date=ledger.window_start,
        end_date=ledger.window_end, observation_count=ledger.observation_count,
        run_id=ledger.run_id, source_script=__name__, computed_at=computed_at,
        source_revision=started.source_revision, cost_model=ledger.cost_source,
        cost_inputs=dict(ledger.cost_inputs), calendar_id=ledger.calendar_id,
        periods_per_year=ledger.periods_per_year)
    return result, ledger
