"""Opt-in offline close-fill ledger consuming QPK security_netting.

Inputs are in-memory, caller-supplied decisions eligible before each fill
session, total security returns, initial book and explicit cash/fee roles.
Existing Kelly evaluators and their virtual-asset cost model are unchanged.
This module fetches nothing and submits no orders. Input provenance, calendar,
signal availability and real-run authorization must be checked upstream.

The held book earns the session return before the close fill. QPK nets inner
and outer targets once, in units of pre-fill NAV. Fees are paid from the named
virtual cash role after filling; insufficient cash is rejected, never borrowed
or silently funded by uncharged security sales. Targets are pre-fee weights;
the next session drifts the actual post-fee book, including the depleted cash.
"""
from __future__ import annotations

import inspect
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

RS06_TURNOVER_CURRENT_BOOK_BASIS = "RS06_SYNTHETIC_FILL_SESSION_PRE_TRADE_PRE_FEE_NET_NAV_V1"

# Opt-in artificial snapshots only. These are total-return index units, never
# broker shares, account holdings, or an admission of prospective market data.
SYNTHETIC_R0_R3_UNIVERSES = {"TQQQ": ("BOXX", "QQQM", "TQQQ"),
                            "SOXL": ("BOXX", "SOXL", "SOXX")}


def new_synthetic_virtual_book(profile: str, candidate: str, anchor_session: str) -> dict[str, Any]:
    if type(profile) is not str or profile not in SYNTHETIC_R0_R3_UNIVERSES or candidate not in ("R0", "R3"):
        raise ValueError("SYNTHETIC_BOOK_IDENTITY")
    date.fromisoformat(anchor_session)
    symbols = SYNTHETIC_R0_R3_UNIVERSES[profile]
    return dict(profile=profile, candidate=candidate, completed_session=anchor_session,
                nav=1., cash=1., security_values={s: 0. for s in symbols},
                units={s: 0. for s in symbols}, marks={s: 1. for s in symbols},
                fees=0., trade_events=0, pending=None)


def _validate_synthetic_pending(pending: Any, *, profile: str, candidate: str,
                                decision_session: str, fill_session: str | None = None) -> None:
    """A pending belongs to one cell and one adjacent decision/fill interval."""
    if type(pending) is not dict or set(pending) != {
            "profile", "candidate", "decision_session", "fill_session", "kind", "weights"}:
        raise ValueError("SYNTHETIC_PENDING_FIELDS")
    if pending["profile"] != profile or pending["candidate"] != candidate:
        raise ValueError("SYNTHETIC_PENDING_OWNER")
    fill = pending["fill_session"]
    if (pending["decision_session"] != decision_session or type(fill) is not str
            or date.fromisoformat(fill).isoformat() != fill
            or date.fromisoformat(fill) <= date.fromisoformat(decision_session)
            or (fill_session is not None and fill != fill_session)):
        raise ValueError("SYNTHETIC_PENDING_CLOCK")
    if pending["kind"] == "HOLD_POSITIONS":
        if candidate != "R3":
            raise ValueError("SYNTHETIC_R0_FULL_PENDING_REQUIRED")
        if pending["weights"] is not None:
            raise ValueError("SYNTHETIC_HOLD_TARGET")
    elif pending["kind"] == "TARGET_WEIGHTS":
        weights = pending["weights"]
        if (type(weights) is not dict or set(weights) != set(SYNTHETIC_R0_R3_UNIVERSES[profile])
                or any(_finite(w, "SYNTHETIC_PENDING_WEIGHT") < 0 for w in weights.values())
                or math.fsum(weights.values()) > 1.):
            raise ValueError("SYNTHETIC_PENDING_WEIGHT")
    else:
        raise ValueError("SYNTHETIC_PENDING_KIND")


def validate_synthetic_virtual_book(book: Any) -> None:
    """Strict in-memory wire boundary; does not read any external state."""
    keys = {"profile", "candidate", "completed_session", "nav", "cash", "security_values",
            "units", "marks", "fees", "trade_events", "pending"}
    if type(book) is not dict or set(book) != keys:
        raise ValueError("SYNTHETIC_BOOK_FIELDS")
    profile = book["profile"]
    if type(profile) is not str or profile not in SYNTHETIC_R0_R3_UNIVERSES or book["candidate"] not in ("R0", "R3"):
        raise ValueError("SYNTHETIC_BOOK_IDENTITY")
    if date.fromisoformat(book["completed_session"]).isoformat() != book["completed_session"]:
        raise ValueError("SYNTHETIC_BOOK_SESSION")
    symbols = set(SYNTHETIC_R0_R3_UNIVERSES[profile])
    for name in ("security_values", "units", "marks"):
        if type(book[name]) is not dict or set(book[name]) != symbols:
            raise ValueError("SYNTHETIC_BOOK_UNIVERSE")
        for value in book[name].values():
            if _finite(value, "SYNTHETIC_BOOK_NUMBER") < 0 or (name == "marks" and value == 0):
                raise ValueError("SYNTHETIC_BOOK_NUMBER")
    for name in ("nav", "cash", "fees"):
        if _finite(book[name], "SYNTHETIC_BOOK_NUMBER") < 0 or (name == "nav" and book[name] == 0):
            raise ValueError("SYNTHETIC_BOOK_NUMBER")
    if type(book["trade_events"]) is not int or book["trade_events"] < 0:
        raise ValueError("SYNTHETIC_TRADE_COUNT")
    tolerance = 1e-11 * max(1., book["nav"])
    if abs(book["cash"] + math.fsum(book["security_values"].values()) - book["nav"]) > tolerance:
        raise ValueError("SYNTHETIC_BOOK_CONSERVATION")
    if any(abs(book["units"][s]*book["marks"][s] - book["security_values"][s]) > tolerance for s in symbols):
        raise ValueError("SYNTHETIC_UNIT_CONSERVATION")
    pending = book["pending"]
    if pending is not None:
        _validate_synthetic_pending(pending, profile=profile, candidate=book["candidate"],
                                    decision_session=book["completed_session"])


def step_synthetic_virtual_book(book: Mapping[str, Any], *, session: str, next_session: str,
                                security_returns: Mapping[str, float], full_target: Mapping[str, float],
                                cutoff: str, policy_context: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Consume OLD pending, mark/fill/fee, then choose NEXT pending on own book.

    Explicit artificial timestamps are caller-validated labels, not real PIT.
    No file, provider, generator, trial, order, or persistence API is called.
    """
    from fractions import Fraction
    from .partial_rebalance import NoPluginCorePolicyContext, _inside_band, decide_partial_rebalance
    from quant_platform_kit.research_stats.security_netting import SecurityNettingSpec, net_security_session

    validate_synthetic_virtual_book(book)
    profile, candidate = book["profile"], book["candidate"]
    symbols = SYNTHETIC_R0_R3_UNIVERSES[profile]
    if (type(policy_context) is not NoPluginCorePolicyContext or policy_context.dataset_class != "ARTIFICIAL_FIXTURE"
            or policy_context.profile != profile):
        raise ValueError("SYNTHETIC_ONLY_POLICY_REQUIRED")
    if not date.fromisoformat(book["completed_session"]) < date.fromisoformat(session) < date.fromisoformat(next_session):
        raise ValueError("SYNTHETIC_SESSION_ORDER")
    if (type(security_returns) is not dict or type(full_target) is not dict
            or set(security_returns) != set(symbols) or set(full_target) != set(symbols)):
        raise ValueError("SYNTHETIC_COMPLETE_INPUT_REQUIRED")
    returns = {s: _return(security_returns[s]) for s in symbols}
    target = {s: _finite(full_target[s], "SYNTHETIC_TARGET") for s in symbols}
    if min(target.values()) < 0 or math.fsum(target.values()) > 1.:
        raise ValueError("SYNTHETIC_TARGET")
    pending = book["pending"]
    if pending is not None and pending["fill_session"] != session:
        raise ValueError("SYNTHETIC_PENDING_FILL_SESSION")
    marks = {s: book["marks"][s]*(1+returns[s]) for s in symbols}
    pre = {s: book["security_values"][s]*(1+returns[s]) for s in symbols}
    pre_nav = book["cash"] + math.fsum(pre.values())
    if not math.isfinite(pre_nav) or pre_nav <= 0:
        raise ValueError("SYNTHETIC_NAV")
    current = {s: pre[s]/pre_nav for s in symbols}
    hold = pending is None or pending["kind"] == "HOLD_POSITIONS"
    effective = current if hold else pending["weights"]
    spec = SecurityNettingSpec(bps_per_side={s: 5. for s in symbols}, cash_like_symbols=("BOXX",))
    net = net_security_session(f_pre=1., inner_pre=current, f_target=1., inner_target=effective, spec=spec)
    fees = net.cost_fraction*pre_nav
    trades = {s: net.net_trades[s]*pre_nav for s in symbols}
    if hold:
        if fees != 0 or any(trades.values()):
            raise ValueError("SYNTHETIC_HOLD_PHANTOM_TRADE")
        values, cash, units = pre, book["cash"], dict(book["units"])
    else:
        values = {s: pre_nav*effective[s] for s in symbols}
        cash_before_fee = pre_nav*net.role_weights[spec.inner_residual_role]
        if fees > cash_before_fee:
            raise ValueError("FEE_CASH_INSUFFICIENT")
        cash = cash_before_fee-fees
        units = {s: values[s]/marks[s] for s in symbols}
    nav = pre_nav-fees
    if abs(book["cash"]-math.fsum(trades.values())-fees-cash) > 1e-11*max(1., pre_nav):
        raise ValueError("SYNTHETIC_CASH_CONSERVATION")
    completed = DecisionSnapshot(date.fromisoformat(session), cutoff, nav,
                                 tuple(values.items()), cash, True)
    encoded = _rs04_current_weights(completed)
    mode, lam = ("FULL", .5) if candidate == "R0" else ("BAND_HOLD", .5)
    inside = _inside_band({s: Fraction(values[s])/Fraction(nav) for s in symbols},
                          {s: Fraction(target[s]) for s in symbols}, .01)
    # Preserve the existing actual-money band boundary rule without allowing
    # the conservative funded float encoding to change a HOLD decision.
    selected_mode = mode
    if mode == "BAND_HOLD" and inside != _inside_band(encoded, target, .01):
        selected_mode, lam = ("PROPORTIONAL", 0.) if inside else ("FULL", .5)
    decision = decide_partial_rebalance(security_universe=symbols, current_weights=encoded,
        full_target=target, mode=selected_mode, decision_session=session, cutoff=cutoff,
        hard_policy_context=policy_context, lambda_value=lam, band=.01)
    if decision.status != "ACCEPTED":
        raise ValueError("SYNTHETIC_DECISION:"+decision.reason)
    following = dict(profile=profile, candidate=candidate, decision_session=session,
                     fill_session=next_session, kind=decision.kind,
                     weights=None if decision.target_weights is None else dict(decision.target_weights))
    count = sum(v != 0 for v in trades.values())
    result = dict(profile=profile, candidate=candidate, completed_session=session, nav=nav, cash=cash,
                  security_values=values, units=units, marks=marks, fees=book["fees"]+fees,
                  trade_events=book["trade_events"]+count, pending=following)
    validate_synthetic_virtual_book(result)
    return result, dict(executed_fees=fees, net_trades=trades, turnover=net.net_security_turnover,
                        previous_pending=pending, next_pending=following, decision_mode=mode,
                        band=.01, decision_weights={s: values[s]/nav for s in symbols},
                        nav=nav, cash=cash, scope="SYNTHETIC_ONLY", no_order=True)


@dataclass(frozen=True)
class NettingSession:
    session: date
    decision_session: date
    f_target: float
    inner_target: Mapping[str, float]
    security_returns: Mapping[str, float]
    outer_cash_return: float


def _finite(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(code)
    return float(value)


def _return(value: object) -> float:
    out = _finite(value, "RETURN_INVALID")
    if out <= -1:
        raise ValueError("RETURN_INVALID")
    return out


def _securities(inner: Mapping[str, float], outer: float, spec: Any) -> dict[str, float]:
    values = dict(inner)
    if spec.outer_cash_mode == "merge_into_security":
        symbol = spec.outer_cash_symbol
        values[symbol] = values.get(symbol, 0.) + outer
    return values


def _cash(inner_cash: float, outer: float, spec: Any) -> dict[str, float]:
    values = {spec.inner_residual_role: inner_cash}
    if spec.outer_cash_mode == "separate":
        values["outer_cash_leg"] = outer
    return values



def _research_cost_begin(inputs: Mapping[str, Any], sessions: Sequence[NettingSession],
                         securities: Mapping[str, float], cash: float) -> dict[str, Any]:
    """Open attribution alongside the existing book; never construct targets/cash."""
    from quant_platform_kit.strategy_lifecycle.contracts import RESEARCH_AVERAGE_COST_POLICY

    if not isinstance(inputs, Mapping) or "opening_book" not in inputs:
        raise ValueError("COST_OPENING_REQUIRED")
    if set(inputs) != {"policy_id", "synthetic_book_id", "opening_book", "fill_marks"}:
        raise ValueError("COST_INPUT_KEYS")
    if inputs["policy_id"] != RESEARCH_AVERAGE_COST_POLICY:
        raise ValueError("COST_POLICY_REQUIRED")
    if not isinstance(inputs["synthetic_book_id"], str) or not inputs["synthetic_book_id"]:
        raise ValueError("COST_BOOK_ID_REQUIRED")
    # Freeze caller-owned maps. All prices remain per-session explicit inputs.
    data = json.loads(json.dumps(dict(inputs), allow_nan=False))
    opening = data["opening_book"]
    if (not isinstance(opening, dict) or set(opening) != {"session", "currency", "cash", "positions"}
            or not isinstance(opening["positions"], dict)):
        raise ValueError("COST_OPENING_REQUIRED")
    currency = opening["currency"]
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isascii() or not currency.isupper() or not currency.isalpha():
        raise ValueError("COST_CURRENCY_REQUIRED")
    if (not isinstance(data["fill_marks"], dict)
            or set(data["fill_marks"]) != {row.session.isoformat() for row in sessions}):
        raise ValueError("COST_MARKS_REQUIRED")
    if opening["session"] != sessions[0].decision_session.isoformat():
        raise ValueError("COST_OPENING_REQUIRED")
    if not math.isclose(_finite(opening["cash"], "COST_OPENING_REQUIRED"), cash, rel_tol=1e-10, abs_tol=1e-9):
        raise ValueError("COST_OPENING_BOOK_MISMATCH")
    quantity, basis = {}, {}
    for symbol, position in opening["positions"].items():
        if not isinstance(position, dict) or set(position) != {"quantity", "total_basis", "mark"}:
            raise ValueError("COST_OPENING_REQUIRED")
        q = _finite(position["quantity"], "COST_OPENING_REQUIRED")
        b = _finite(position["total_basis"], "COST_OPENING_REQUIRED")
        mark = _finite(position["mark"], "COST_OPENING_REQUIRED")
        if q < 0 or b < 0 or mark <= 0 or (q == 0 and b != 0):
            raise ValueError("COST_OPENING_REQUIRED")
        quantity[symbol], basis[symbol] = q, b
    for symbol in set(securities) | set(quantity):
        value = quantity.get(symbol, 0.) * opening["positions"].get(symbol, {}).get("mark", 0.)
        if not math.isclose(value, securities.get(symbol, 0.), rel_tol=1e-10, abs_tol=1e-9):
            raise ValueError("COST_OPENING_BOOK_MISMATCH")
    return {"inputs": data, "quantity": quantity, "basis": basis, "realized": 0., "days": []}


def _research_cost_day(cost: dict[str, Any], row: Mapping[str, Any]) -> None:
    """Attribute existing net fills using one declared research average policy."""
    marks = cost["inputs"]["fill_marks"][row["session"]]
    if not isinstance(marks, dict):
        raise ValueError("COST_MARKS_REQUIRED")
    universe = set(cost["quantity"]) | set(row["security_pre"]) | set(row["security_end"]) | set(row["net_trades"])
    positions, fills = {}, []
    for symbol in sorted(universe):
        if symbol not in marks:
            raise ValueError("COST_MARKS_REQUIRED")
        price = _finite(marks[symbol], "COST_MARKS_REQUIRED")
        if price <= 0:
            raise ValueError("COST_MARKS_REQUIRED")
        old_q, old_b = cost["quantity"].get(symbol, 0.), cost["basis"].get(symbol, 0.)
        if not math.isclose(old_q*price, row["security_pre"].get(symbol, 0.), rel_tol=1e-10, abs_tol=1e-9):
            raise ValueError("COST_MARK_BOOK_MISMATCH")
        trade = row["net_trades"].get(symbol, 0.)
        fee = row["fee_by_security"].get(symbol, 0.)
        added = relieved = realized = 0.
        q = row["security_end"].get(symbol, 0.) / price
        if trade > 0:
            added = trade+fee
        elif trade < 0:
            if old_q <= 0 or -trade/price > old_q+1e-9:
                raise ValueError("COST_LONG_ONLY_FILL")
            relieved = old_b if q == 0 else old_b*(-trade/price)/old_q
            realized = -trade-relieved-fee
        if trade != 0:
            identity = f'{cost["inputs"]["synthetic_book_id"]}/{row["session"]}/{symbol}'
            fills.append(dict(execution_id=identity, revision=1, fee_id=identity+"/fee",
                symbol=symbol, side="BUY" if trade > 0 else "SELL", quantity=abs(trade)/price,
                price=price, fee=fee, basis_added=added, basis_relieved=relieved,
                realized_net=realized, event_time=row["session"], known_at=row["session"]))
        b = old_b+added-relieved
        cost["quantity"][symbol], cost["basis"][symbol] = q, b
        cost["realized"] += realized
        positions[symbol] = dict(quantity=q, total_basis=b, mark=price)
    cost["days"].append(dict(session=row["session"], fills=fills, positions=positions,
                             realized_net_cumulative=cost["realized"]))


def _research_cost_packet(cost: dict[str, Any], ledger: Sequence[Mapping[str, Any]],
                          initial_nav: float, fee_multiplier: float) -> tuple[Any, Any]:
    """Original independent cost book/facts constructor, also used at recovery."""
    from quant_platform_kit.strategy_lifecycle.contracts import (
        RESEARCH_AVERAGE_COST_POLICY, RESEARCH_NETTER_COST_SOURCE, ResearchCostFacts,
        ResearchDailyLedger, ResearchLedgerDay, ResearchPositionMark,
    )

    inputs = cost["inputs"]; opening = inputs["opening_book"]
    # Synthetic constructor identity only: no market calendar or annualized report is produced.
    opening_hash = hashlib.sha256(json.dumps(opening, sort_keys=True, allow_nan=False).encode()).hexdigest()
    run_id = "cost-"+hashlib.sha256(inputs["synthetic_book_id"].encode()).hexdigest()
    days = []
    previous_nav = initial_nav
    for facts, row in zip(cost["days"], ledger):
        positions = tuple(ResearchPositionMark(symbol, p["quantity"], p["quantity"]*p["mark"])
                          for symbol, p in facts["positions"].items() if p["quantity"] != 0)
        days.append(ResearchLedgerDay(session_date=date.fromisoformat(row["session"]),
            cash=sum(row["virtual_cash_end"].values()), positions=positions,
            trade_net_cashflow=-sum(row["net_trades"].values()), fees=row["fees"],
            nav=row["nav_end"], daily_return=row["nav_end"]/previous_nav-1.))
        previous_nav = row["nav_end"]
    book = ResearchDailyLedger(trial_id=run_id, domain="us_equity", strategy_profile="synthetic_netting",
        run_id=run_id, param_version=1, input_id=opening_hash, calendar_id="SYNTHETIC_COST_INVARIANTS_ONLY",
        periods_per_year=252., cost_source=RESEARCH_AVERAGE_COST_POLICY,
        cost_inputs={"native_fee_multiplier": fee_multiplier}, initial_session_date=date.fromisoformat(opening["session"]),
        initial_nav=initial_nav, initial_cash=opening["cash"],
        initial_positions=tuple(ResearchPositionMark(symbol, p["quantity"], p["quantity"]*p["mark"])
                                for symbol, p in opening["positions"].items() if p["quantity"] != 0),
        days=tuple(days), synthetic=True)
    facts = ResearchCostFacts.from_dict(dict(policy_id=RESEARCH_AVERAGE_COST_POLICY,
        source_label=RESEARCH_NETTER_COST_SOURCE, synthetic=True, synthetic_book_id=inputs["synthetic_book_id"],
        currency=opening["currency"], ledger_identity={key:getattr(book, key) for key in ("trial_id", "run_id", "input_id")},
        opening_book={key:opening[key] for key in ("session", "cash", "positions")},
        coverage={"opening":"complete", "fills":"complete", "fees":"complete"}, corrections=[], late_fees=[],
        cutoff=ledger[-1]["session"], days=cost["days"]))
    return book, facts


def _research_cost_finish(cost: dict[str, Any], ledger: Sequence[Mapping[str, Any]],
                          initial_nav: float, fee_multiplier: float) -> dict[str, Any]:
    from quant_platform_kit.strategy_lifecycle.contracts import validate_research_cost_facts

    book, facts = _research_cost_packet(cost, ledger, initial_nav, fee_multiplier)
    return validate_research_cost_facts(book, facts).to_dict()


def _verify_research_cost_receipt(inputs: Mapping[str, Any], ledger: Sequence[Mapping[str, Any]],
                                 initial_nav: float, fee_multiplier: float,
                                 receipt: Mapping[str, Any]) -> None:
    """Validate recorded facts against native fills; no target, fit or fill replay.

    The original cost book keeps its own synthetic identity and calendar. The
    caller separately binds that companion to its candidate ledger/context.
    """
    from quant_platform_kit.strategy_lifecycle.contracts import validate_research_cost_facts

    if not isinstance(receipt, Mapping) or receipt.get("status") != "PASS_SCOPED_SYNTHETIC":
        raise ValueError("COST_COMPANION_UNAVAILABLE")
    try:
        recorded = receipt["days"]
        if not isinstance(recorded, list) or len(recorded) != len(ledger):
            raise ValueError("COST_LEDGER_BINDING")
        days = []
        universe = set(inputs["opening_book"]["positions"])
        for declared, row in zip(recorded, ledger, strict=True):
            universe |= set(row["security_pre"]) | set(row["security_end"]) | set(row["net_trades"])
            if set(declared["positions"]) != universe or declared["session"] != row["session"]:
                raise ValueError("COST_LEDGER_BINDING")
            marks = inputs["fill_marks"][row["session"]]
            for symbol, position in declared["positions"].items():
                price = _finite(position["mark"], "COST_MARKS_REQUIRED")
                quantity = _finite(position["quantity"], "COST_FACT_INVALID")
                if (price != marks[symbol] or not math.isclose(quantity * price,
                        row["security_end"].get(symbol, 0.), rel_tol=1e-10, abs_tol=1e-9)):
                    raise ValueError("COST_MARK_BOOK_MISMATCH")
            fills = declared["fills"]
            trades = {s: v for s, v in row["net_trades"].items() if v != 0.}
            if (not isinstance(fills, list) or len(fills) != len(trades)
                    or {f["symbol"] for f in fills} != set(trades)):
                raise ValueError("COST_LEDGER_BINDING")
            for fill in fills:
                symbol = fill["symbol"]; trade = trades[symbol]
                identity = f'{inputs["synthetic_book_id"]}/{row["session"]}/{symbol}'
                if (fill["execution_id"] != identity or fill["fee_id"] != identity + "/fee"
                        or fill["side"] != ("BUY" if trade > 0 else "SELL")
                        or fill["price"] != marks[symbol]
                        or not math.isclose(_finite(fill["quantity"], "COST_FACT_INVALID") * marks[symbol],
                                            abs(trade), rel_tol=1e-10, abs_tol=1e-9)
                        or fill["fee"] != row["fee_by_security"][symbol]):
                    raise ValueError("COST_LEDGER_BINDING")
            # Preserve declared basis/realized facts for the existing validator;
            # do not derive them again from a second attribution implementation.
            days.append({key: declared[key] for key in (
                "session", "fills", "positions", "realized_net_cumulative")})
        book, facts = _research_cost_packet({"inputs": inputs, "days": days}, ledger, initial_nav, fee_multiplier)
        expected = validate_research_cost_facts(book, facts).to_dict()
        # Canonical JSON equality also preserves numeric/boolean shape; raw
        # dict equality would silently equate False with a forged integer 0.
        if (expected["status"] != "PASS_SCOPED_SYNTHETIC"
                or json.dumps(expected, sort_keys=True, allow_nan=False)
                != json.dumps(dict(receipt), sort_keys=True, allow_nan=False)):
            raise ValueError("COST_COMPANION_MISMATCH")
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("COST_COMPANION_INCOMPLETE") from exc


def run_offline_security_netting(
    sessions: Sequence[NettingSession], *, initial_nav: float, initial_f: float,
    initial_inner_weights: Mapping[str, float], spec: Any, fee_cash_role: str,
    execution_timing: str | None = None, report_periods_per_year: int | None = None,
    rs06_risk_budget: Mapping[str, Any] | None = None,
    report_sharpe_basis: str = "legacy_net_rf0_v1",
    report_risk_free_returns: Sequence[float] | None = None,
    report_risk_free_source: str | None = None, report_calendar_id: str | None = None,
    research_cost_inputs: Mapping[str, Any] | None = None,
    include_max_drawdown_recovery: bool = False,
    rs04_research_policy: Any | None = None,
) -> dict[str, Any]:
    """Replay one composed sleeve with explicit cash funding; no data or order I/O.

    ``gross_returns`` measure pre-fee returns on the same evolving net book,
    not an independently rerun cost-free counterfactual. Role weights overlap
    securities for inner cash-like assets and merged outer cash: never sum the
    role columns into NAV. Only ``security_end`` + ``virtual_cash_end`` sum to NAV.

    Explicit ``execution_timing="next_close"`` and ``report_periods_per_year``
    opt into QPK's compounding report on the actual post-fee ledger returns.
    Decisions must then use the immediately preceding supplied session; the
    first decision is the caller's pre-window anchor. Calendar completeness and
    real signal availability remain unverified. Omitting both keeps the original
    result and dependency requirements; no annualization policy is inferred.

    Legacy reports retain their original implicit rf=0 interpretation and bytes.
    Explicit v2 reports take caller-supplied same-session rf and its source;
    missing rf makes Sharpe inference unavailable, not net wealth unavailable.
    ``include_max_drawdown_recovery=True`` adds window-only observation
    intervals and censor details to that same report; defaults stay unchanged.

    ``rs06_risk_budget`` is only the explicit synthetic single-unit RS06
    f=1/zero-cash-return context. Its gate uses fill-session marks, not prior
    decision information; it establishes no real currency/FX qualification.
    """
    # Explicitly opt-in research attribution; legacy requests import no new contract.
    if type(include_max_drawdown_recovery) is not bool:
        raise ValueError("RECOVERY_OPT_IN_INVALID")
    if include_max_drawdown_recovery and (execution_timing != "next_close" or report_periods_per_year is None):
        raise ValueError("EXPLICIT_NEXT_CLOSE_REPORT_REQUIRED")
    report_builder = None
    report_options = {}
    if report_sharpe_basis != "legacy_net_rf0_v1":
        if execution_timing is None or report_periods_per_year is None:
            raise ValueError("EXPLICIT_NEXT_CLOSE_REPORT_REQUIRED")
        report_options = {"sharpe_basis": report_sharpe_basis,
                          "risk_free_returns": (None if report_risk_free_returns is None
                                                else list(report_risk_free_returns)),
                          "risk_free_source": report_risk_free_source, "calendar_id": report_calendar_id}
    elif any(v is not None for v in (report_risk_free_returns, report_risk_free_source, report_calendar_id)):
        raise ValueError("RF_INPUTS_REQUIRE_EXPLICIT_BASIS")
    if include_max_drawdown_recovery:
        report_options["include_max_drawdown_recovery"] = True
    if execution_timing is not None or report_periods_per_year is not None:
        if execution_timing != "next_close" or report_periods_per_year is None:
            raise ValueError("EXPLICIT_NEXT_CLOSE_REPORT_REQUIRED")
        try:
            from quant_platform_kit.research_stats.unified_report import (
                build_unified_net_report,
            )
        except ImportError as exc:
            raise ValueError("QPK_NET_COMPOUNDING_REQUIRED") from exc
        if "periods_per_year" not in inspect.signature(build_unified_net_report).parameters:
            raise ValueError("QPK_NET_COMPOUNDING_REQUIRED")
        if include_max_drawdown_recovery and "include_max_drawdown_recovery" not in inspect.signature(
                build_unified_net_report).parameters:
            raise ValueError("QPK_MAX_DRAWDOWN_RECOVERY_REQUIRED")
        if report_options and not set(report_options).issubset(inspect.signature(build_unified_net_report).parameters):
            raise ValueError("QPK_EXPLICIT_SHARPE_BASIS_REQUIRED")
        report_builder = build_unified_net_report
    try:
        from quant_platform_kit.research_stats.security_netting import (
            SECURITY_NETTING_VERSION,
            SecurityNettingSpec,
            compose_security_targets,
            drift_inner_weights,
            net_security_session,
        )
    except ImportError as exc:
        raise ValueError("QPK_SECURITY_NETTING_REQUIRED") from exc
    if type(spec) is not SecurityNettingSpec:
        raise ValueError("NETTING_SPEC_INVALID")
    # Frozen dataclass fields can still contain a caller-owned mutable dict.
    # Snapshot rates and rerun QPK validation before any fee enters the ledger.
    spec = replace(spec, bps_per_side=dict(spec.bps_per_side))
    if (any(not isinstance(s, str) or not s for s in spec.cash_like_symbols)
            or len(set(spec.cash_like_symbols)) != len(spec.cash_like_symbols)):
        raise ValueError("CASH_LIKE_SYMBOLS_INVALID")
    allowed_roles = {spec.inner_residual_role}
    if spec.outer_cash_mode == "separate":
        allowed_roles.add("outer_cash_leg")
    if fee_cash_role not in allowed_roles:
        raise ValueError("FEE_CASH_ROLE_INVALID")
    nav = _finite(initial_nav, "INITIAL_NAV_INVALID")
    if nav <= 0:
        raise ValueError("INITIAL_NAV_INVALID")
    compose_security_targets(f=initial_f, inner_weights=initial_inner_weights, spec=spec)
    if not sessions:
        raise ValueError("SESSIONS_REQUIRED")
    budget_policy = None
    # B5_BEGIN
    research = None
    if rs04_research_policy is not None:
        if rs06_risk_budget is not None:
            raise ValueError("RS04_RS06_POLICY_CONFLICT")
        research = _rs04_prepare(rs04_research_policy, sessions, spec,
                                 initial_f, fee_cash_role, execution_timing)
    # B5_END
    if rs06_risk_budget is not None:
        from us_equity_strategies.portfolio_risk_budget import (
            PortfolioAssetRiskSpec, PortfolioRiskBudgetPolicy, assess_portfolio_risk_budget,
        )

        if (not isinstance(rs06_risk_budget, Mapping)
                or set(rs06_risk_budget) != {"policy", "asset_risk_specs", "synthetic", "valuation_basis"}
                or rs06_risk_budget["synthetic"] is not True
                or rs06_risk_budget["valuation_basis"] != RS06_TURNOVER_CURRENT_BOOK_BASIS
                or initial_f != 1. or spec.outer_cash_mode != "separate" or spec.cash_like_symbols
                or fee_cash_role != spec.inner_residual_role or execution_timing != "next_close"):
            raise ValueError("NETTING_RISK_BUDGET_SYNTHETIC_SCOPE_REQUIRED")
        budget_policy = rs06_risk_budget["policy"]
        risk_specs = rs06_risk_budget["asset_risk_specs"]
        try:
            if type(budget_policy) is not PortfolioRiskBudgetPolicy or not isinstance(risk_specs, Mapping):
                raise ValueError("policy/spec")
            budget_policy = replace(budget_policy, max_symbol_weights=dict(budget_policy.max_symbol_weights),
                                    max_underlying_effective_exposure=dict(budget_policy.max_underlying_effective_exposure))
            risk_specs = dict(risk_specs)
            cash_symbol = budget_policy.cash_symbol
            if (budget_policy.max_one_way_risk_turnover is None or cash_symbol in spec.bps_per_side
                    or set(risk_specs) != set(spec.bps_per_side) | {cash_symbol}
                    or any(type(v) is not PortfolioAssetRiskSpec for v in risk_specs.values())
                    or risk_specs[cash_symbol].is_cash is not True
                    or any(risk_specs[s].is_cash is not False for s in spec.bps_per_side)):
                raise ValueError("cash/security mapping")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("NETTING_RISK_BUDGET_INVALID") from exc
    inner = {s: nav * initial_f * w for s, w in initial_inner_weights.items()}
    inner_cash = nav * initial_f * max(0., 1 - sum(initial_inner_weights.values()))
    outer = nav * (1 - initial_f)
    # B5_BEGIN
    if research is not None:
        opening = research.opening
        _rs04_reconcile(opening, inner, inner_cash, nav)
        # The explicitly completed actual book is authoritative after reconciliation.
        inner, inner_cash, nav = dict(opening.security_values), opening.cash, opening.nav
        completed = opening
        research_decisions = []
    # B5_END
    cost = None
    if research_cost_inputs is not None:
        if spec.outer_cash_mode != "separate":
            raise ValueError("COST_EVENT_SCOPE_UNSUPPORTED")
        cost = _research_cost_begin(research_cost_inputs, sessions, _securities(inner, outer, spec), inner_cash+outer)
    ledger = []
    previous = None
    for row in sessions:
        if type(row) is not NettingSession or type(row.session) is not date:
            raise ValueError("SESSION_INVALID")
        if previous is not None and row.session <= previous:
            raise ValueError("SESSION_ORDER_INVALID")
        if type(row.decision_session) is not date or row.decision_session >= row.session:
            raise ValueError("DECISION_NOT_PRIOR")
        if report_builder is not None and previous is not None and row.decision_session != previous:
            raise ValueError("NEXT_CLOSE_DECISION_SESSION_MISMATCH")
        # B5_BEGIN
        decision = None
        if research is not None:
            information = research.decisions[len(ledger)]
            _rs04_reconcile(completed, inner, inner_cash, nav)
            if completed.completed_session != row.decision_session:
                raise ValueError("RS04_COMPLETED_ANCHOR_MISMATCH")
            decision = _rs04_select(research, information, completed)
            receipt = _rs04_receipt(decision, information, completed)
            tail_mandatory = False
            if information.cvar_request is not None:
                decision, tail = _rs04_constrain_tail(research, information, completed, decision, spec)
                receipt["cvar"] = tail
                tail_mandatory = tail["constraint"]["status"] == "MANDATORY_TAIL_REDUCTION"
            research_decisions.append(receipt)
        # B5_END
        previous = row.session
        if not isinstance(row.security_returns, Mapping):
            raise ValueError("RETURN_INVALID")
        returns = {s: _return(r) for s, r in row.security_returns.items()}
        # B5_BEGIN
        if research is not None and set(returns) != set(research.security_universe):
            raise ValueError("RS04_COMPLETE_SECURITY_RETURN_UNIVERSE_REQUIRED")
        # B5_END
        cash_return = _return(row.outer_cash_return)
        if cost is not None and cash_return != 0.:
            raise ValueError("COST_EVENT_SCOPE_UNSUPPORTED")
        if spec.outer_cash_mode == "merge_into_security" and cash_return != 0.:
            raise ValueError("MERGED_OUTER_RETURN_MUST_USE_SECURITY")
        nav_start = nav
        security_start = _securities(inner, outer, spec)
        missing = sorted(set(security_start) - set(returns))
        if missing:
            raise ValueError("RETURN_MISSING:" + ",".join(missing))
        security_pnl = {s: value * returns[s] for s, value in security_start.items()}
        security_pre = {s: value + security_pnl[s] for s, value in security_start.items()}
        outer_pnl = outer * cash_return if spec.outer_cash_mode == "separate" else 0.
        # Use QPK's inner drift; retain layer ownership when BOXX appears in both.
        inner_total = sum(inner.values()) + inner_cash
        if inner_total > 0:
            inner_pre, inner_return = drift_inner_weights(
                {s: value / inner_total for s, value in inner.items()}, returns)
            inner_total *= 1 + inner_return
        else:
            inner_pre = {}
        outer_pre = (outer * (1 + returns[spec.outer_cash_symbol])
                     if spec.outer_cash_mode == "merge_into_security" else outer + outer_pnl)
        pre_nav = inner_total + outer_pre
        # B5_BEGIN
        if research is not None:
            if cash_return != 0. or outer_pre != 0.:
                raise ValueError("RS04_ZERO_RETURN_RESIDUAL_REQUIRED")
            # Actual values, rather than a second weight-based drift, define this book.
            pre_nav = sum(security_pre.values()) + inner_cash
            inner_total = pre_nav
            inner_pre = {s: security_pre[s] / pre_nav for s in research.security_universe}
        # B5_END
        if not math.isfinite(pre_nav) or pre_nav <= 0:
            raise ValueError("NAV_INVALID")
        effective_target = row.inner_target
        # B5_BEGIN
        if research is not None:
            effective_target = (inner_pre if decision.kind == "HOLD_POSITIONS"
                                else dict(decision.target_weights))
            from fractions import Fraction
            actual_pre = {s: Fraction(v) / Fraction(pre_nav) for s, v in security_pre.items()}
            _rs04_actual_check(information, actual_pre,
                actual_pre if decision.kind == "HOLD_POSITIONS" else effective_target,
                stage='EXECUTION', mandatory=information.mandatory_reduce or tail_mandatory)
        # B5_END
        if budget_policy is not None:
            if row.f_target != 1. or cash_return != 0. or outer_pre != 0.:
                raise ValueError("NETTING_RISK_BUDGET_SYNTHETIC_SCOPE_REQUIRED")
            # Same loop/book after held returns and all previous fees, before
            # this fill's fee. Never a previous target or a cost-free replay.
            if (not set(security_pre).issubset(spec.bps_per_side)
                    or any(not math.isfinite(v) or v < 0. for v in security_pre.values())
                    or not math.isfinite(inner_cash) or inner_cash < 0.
                    or abs(sum(security_pre.values()) + inner_cash - pre_nav) > 1e-10 * max(1., pre_nav)):
                raise ValueError("NETTING_RISK_BUDGET_CURRENT_BOOK_INVALID")
            current = {s: value / pre_nav for s, value in security_pre.items()}
            current[cash_symbol] = inner_cash / pre_nav
            if (not isinstance(effective_target, Mapping)
                    or not set(effective_target).issubset(spec.bps_per_side)):
                raise ValueError("NETTING_RISK_BUDGET_TARGET_INVALID")
            proposal = {s: _finite(w, "NETTING_RISK_BUDGET_TARGET_INVALID") for s, w in effective_target.items()}
            proposal[cash_symbol] = max(0., 1. - sum(proposal.values()))
            assessed = assess_portfolio_risk_budget(
                target_weights=proposal, current_weights=current,
                asset_risk_specs=risk_specs, policy=budget_policy)
            if assessed["status"] not in ("APPROVE", "REDUCE"):
                raise ValueError("NETTING_RISK_BUDGET_PARKED:" + ";".join(assessed["reason_codes"]))
            effective_target = {s: w for s, w in assessed["recommended_target_weights"].items() if s != cash_symbol}
        result = net_security_session(f_pre=inner_total / pre_nav, inner_pre=inner_pre,
                                      f_target=row.f_target, inner_target=effective_target, spec=spec)
        trades = {s: w * pre_nav for s, w in result.net_trades.items()}
        fee_by_security = {s: abs(value) * spec.bps_per_side[s] * spec.multiplier / 10_000
                           for s, value in trades.items()}
        fees = result.cost_fraction * pre_nav
        inner = {s: pre_nav * row.f_target * w for s, w in effective_target.items()}
        inner_cash = pre_nav * result.role_weights[spec.inner_residual_role]
        outer = pre_nav * (1 - row.f_target)
        # B5_BEGIN
        if research is not None and decision.kind == "HOLD_POSITIONS":
            # Both netter inputs are the very same drifted mapping, f=1 exactly.
            # Keep actual values/cash, avoiding division/multiplication round trips.
            if (any(v != 0. for v in result.net_trades.values()) or fees != 0.
                    or result.net_security_turnover != 0.):
                raise ValueError("RS04_HOLD_PHANTOM_TRADE")
            inner = dict(security_pre)
            inner_cash = completed.cash
        # B5_END
        cash_before_fee = _cash(inner_cash, outer, spec)
        if fees > cash_before_fee[fee_cash_role]:
            raise ValueError("FEE_CASH_INSUFFICIENT")
        if fee_cash_role == spec.inner_residual_role:
            inner_cash -= fees
        else:
            outer -= fees
        security_end = _securities(inner, outer, spec)
        cash_end = _cash(inner_cash, outer, spec)
        nav = pre_nav - fees
        if not math.isfinite(nav) or nav <= 0:
            raise ValueError("NAV_INVALID")
        errors = {
            "return_pnl": pre_nav - nav_start - sum(security_pnl.values()) - outer_pnl,
            "target_budget": sum(security_end.values()) + sum(cash_before_fee.values()) - pre_nav,
            "net_book": sum(security_end.values()) + sum(cash_end.values()) - nav,
            "fees": sum(fee_by_security.values()) - fees,
            "cash_flow": sum(trades.values()) + sum(cash_end.values())
                         - (pre_nav - sum(security_pre.values())) + fees,
            "security_trades": max((abs(security_pre.get(s, 0.) + trades.get(s, 0.)
                                         - security_end.get(s, 0.))
                                     for s in set(security_pre) | set(security_end)), default=0.),
        }
        if any(not math.isfinite(v) or abs(v) > 1e-10 * max(1., pre_nav) for v in errors.values()):
            raise ValueError("LEDGER_RECONCILIATION_FAILED")
        ledger.append({
            "session": row.session.isoformat(), "decision_session": row.decision_session.isoformat(),
            "nav_start": nav_start, "security_start": security_start,
            "security_pnl": security_pnl, "outer_cash_pnl": outer_pnl,
            "nav_pre_trade": pre_nav, "security_pre": security_pre,
            "net_trades": trades, "security_end": security_end,
            "virtual_cash_end": cash_end, "role_weights": dict(result.role_weights),
            "f_pre": inner_total / pre_nav, "inner_pre": inner_pre,
            "f_target": row.f_target, "inner_target": dict(effective_target),
            "fee_cash_role": fee_cash_role, "fee_by_security": fee_by_security, "fees": fees,
            "nav_end": nav, "gross_return": pre_nav / nav_start - 1,
            "net_return": nav / nav_start - 1,
            "inner_unnetted_turnover": result.inner_unnetted_turnover,
            "outer_unnetted_turnover": result.outer_unnetted_turnover,
            "net_security_turnover": result.net_security_turnover,
            "turnover_columns_summable": result.turnover_columns_summable,
            "reconciliation_errors": errors,
        })
        if cost is not None:
            _research_cost_day(cost, ledger[-1])
        # B5_BEGIN
        if research is not None:
            completed = DecisionSnapshot(row.session, information.fill_completed_at, nav,
                tuple(inner.items()), inner_cash, True)
            _rs04_reconcile(completed, inner, inner_cash, nav)
            research_decisions[-1]["execution"] = {
                "actual_fees": fees, "cash_end": inner_cash, "nav_end": nav,
                "post_fee_weights": {s: v / nav for s, v in security_end.items()},
                "cap_basis": information.hard_policy_context.cap_basis,
                "post_fee_cap_certified": False,
                "hold_no_trade_exact": decision.kind == "HOLD_POSITIONS"}
        # B5_END
    output = {
        "scope": "OFFLINE_RESEARCH_ONLY", "no_order": True, "live_ready": False,
        "input_provenance": "CALLER_SUPPLIED_NOT_VERIFIED", "version": SECURITY_NETTING_VERSION,
        "fill_convention": "prior_decision_close_fill_after_held_book_return",
        "target_basis": "pre_fee_nav_cash_funded", "fee_cash_role": fee_cash_role,
        "gross_return_basis": "same_net_book_before_close_fee",
        "spec": {"bps_per_side": dict(spec.bps_per_side), "multiplier": spec.multiplier,
                 "outer_cash_mode": spec.outer_cash_mode, "outer_cash_symbol": spec.outer_cash_symbol,
                 "inner_residual_role": spec.inner_residual_role, "cash_like_symbols": list(spec.cash_like_symbols)},
        "initial_nav": initial_nav, "final_nav": nav,
        "gross_returns": [r["gross_return"] for r in ledger],
        "net_returns": [r["net_return"] for r in ledger], "ledger": ledger,
    }
    if report_builder is not None:
        output["timing"] = {"execution": execution_timing}
        output["unified_net_report"] = report_builder(
            output["net_returns"], gross_returns=output["gross_returns"],
            periods_per_year=report_periods_per_year,
            **report_options,
        ).to_dict()
        output["unified_net_report_basis"] = {
            "sessions": [r["session"] for r in ledger],
            "observation_unit": "SESSION_SIMPLE_NET_RETURN", "return_unit": "SIGNED_DECIMAL_FRACTION",
            "periods_per_year": report_periods_per_year, "initial_nav_is_return_observation": False,
            "zero_return_sessions": "INCLUDED", "costs_deducted_again": False,
            "calendar_and_signal_availability_verified": False,
            "gross_counterfactual": "NOT_SUPPLIED", "asset_benchmark": None,
            "benchmark_alignment_count": None, "benchmark_dropped_count": None,
        }
        if report_options:
            output["unified_net_report_basis"].update(report_options)
    if cost is not None:
        output["research_cost_receipt"] = _research_cost_finish(cost, ledger, initial_nav, spec.multiplier)
    # B5_BEGIN
    if research is not None:
        output["rs04_research"] = {"mode": research.mode, "decisions": research_decisions,
            "scope": ("RETROSPECTIVE_RESEARCH_ONLY" if type(research) in (RS04RetrospectiveResearchPolicy, RS04NoPluginCoreResearchPolicy)
                      else "SYNTHETIC_ONLY"), "financial": "NOT_READY", "adoption": "UNKNOWN",
            "fullchain": "NOT_CLOSED", "residual_cash_return": 0.,
            "supported_cap_basis": "DECISION_TIME_TARGET",
            "execution_check": "FILL_PRE_FEE_TARGET_AND_RISK_BUYS_ACTUAL_NET_FEES_CASH",
            "post_fee_cap_certified": False}
    if type(research) in (RS04RetrospectiveResearchPolicy, RS04NoPluginCoreResearchPolicy):
        output["rs04_research"].update(
            clock="ASSUMED_COMPLETED_CLOSE_NOT_HISTORICAL_AVAILABLE_AT",
            historical_available_at=None, PIT="NOT_ATTESTED",
            OOS="SEEN_DEVELOPMENT_NOT_INDEPENDENT", promotion="NONE")
    if type(research) is RS04NoPluginCoreResearchPolicy:
        output['rs04_research'].update(
            scope='RETROSPECTIVE_CORE_ONLY_SOURCE_RULES',
            independent_external_policy='NOT_CALLED_IN_FROZEN_SOURCE_PATH',
            numeric_external_risk_caps_applied=False)
    # B5_END
    return output


@dataclass(frozen=True)
class DecisionSnapshot:
    """Completed actual book only. Contains no returns or future fill objects."""

    completed_session: date
    completed_at: str
    nav: float
    security_values: tuple[tuple[str, float], ...]
    cash: float
    current_known: bool


@dataclass(frozen=True)
class RS04DecisionInput:
    decision_session: date
    fill_session: date
    cutoff: str
    fill_completed_at: str
    full_target_available_at: str
    full_target_source: str
    full_target: tuple[tuple[str, float], ...]
    hard_policy_context: Any
    mandatory_reduce: bool
    forced_target: tuple[tuple[str, float], ...] | None = None
    forced_source: str | None = None
    forced_reason: str | None = None
    cvar_request: RS04CVaRRequest | None = None


@dataclass(frozen=True)
class RS04CVaRRequest:
    """Fixed joint scenarios and authority, never a callback or live data source.

    known_inputs contains (component, source, as_of, available_at) for scenarios,
    tail_policy, cost, original_policy and, when supplied, anchor. All UTC times
    must precede this decision cutoff. Sources/approvals are synthetic assertions.
    The entire original policy is bound by value; the adapter derives its caps.
    """

    scenarios: Any
    tail_policy: Any
    cost_spec: Any
    original_policy: Any
    anchor: Any
    known_inputs: tuple[tuple[str, str, str, str], ...]


@dataclass(frozen=True)
class RS04ResearchPolicy:
    """Fixed synthetic B5 inputs; no callback or access to the runner's returns.

    Only DECISION_TIME_TARGET/RISK_BUY_WEIGHT is supported by the accepted
    kernel. A post-fee/fill cap basis is rejected before any execution, never
    relabeled. Fill drift, original target/veto/risk-buy limits, actual net fees
    and cash are checked separately. This is not post-fee cap certification.
    """

    mode: str
    security_universe: tuple[str, ...]
    opening: DecisionSnapshot
    decisions: tuple[RS04DecisionInput, ...]
    lambda_value: float = .5
    band: float = .01
    cvar_descriptor: Any | None = None


@dataclass(frozen=True)
class RS04RetrospectiveResearchPolicy(RS04ResearchPolicy):
    """Explicit R0-R3 research admission. R4/tail requests are prohibited."""

    profile: str = ''
    source_identity: str = ''


@dataclass(frozen=True)
class RS04NoPluginCoreResearchPolicy(RS04RetrospectiveResearchPolicy):
    """Exact rootA core route; no independent external hard modules."""


def _rs04_time(value: Any):
    from datetime import datetime, timedelta

    try:
        parsed = datetime.fromisoformat(value)
        if not isinstance(value, str) or 'T' not in value or parsed.utcoffset() != timedelta(0):
            raise ValueError
        return parsed
    except (TypeError, ValueError):
        raise ValueError("RS04_EXPLICIT_UTC_TIME_REQUIRED") from None


def _rs04_pairs(value: Any, universe: tuple[str, ...]) -> dict[str, float]:
    if (not isinstance(value, tuple) or any(not isinstance(p, tuple) or len(p) != 2 for p in value)
            or len(value) != len(universe) or {p[0] for p in value} != set(universe)):
        raise ValueError("RS04_COMPLETE_SECURITY_VALUES_REQUIRED")
    result = {s: _finite(v, "RS04_VALUES_INVALID") for s, v in value}
    if any(v < 0. for v in result.values()):
        raise ValueError("RS04_VALUES_INVALID")
    return result


def _rs04_reconcile(snapshot: DecisionSnapshot, values: Mapping[str, float], cash: float, nav: float) -> None:
    if (type(snapshot) is not DecisionSnapshot or snapshot.current_known is not True
            or type(snapshot.completed_session) is not date):
        raise ValueError("RS04_KNOWN_COMPLETED_BOOK_REQUIRED")
    if _rs04_time(snapshot.completed_at).date() != snapshot.completed_session:
        raise ValueError("RS04_COMPLETED_TIME_MISMATCH")
    actual = _rs04_pairs(snapshot.security_values, tuple(values))
    n = _finite(snapshot.nav, "RS04_BOOK_INVALID")
    c = _finite(snapshot.cash, "RS04_BOOK_INVALID")
    if (n <= 0. or c < 0. or not math.isclose(sum(actual.values()) + c, n, rel_tol=1e-10, abs_tol=1e-10)
            or not math.isclose(n, nav, rel_tol=1e-10, abs_tol=1e-10)
            or not math.isclose(c, cash, rel_tol=1e-10, abs_tol=1e-10)
            or any(not math.isclose(actual[s], values[s], rel_tol=1e-10, abs_tol=1e-10) for s in actual)):
        raise ValueError("RS04_ACTUAL_BOOK_RECONCILIATION")


def _rs04_prepare(policy: Any, rows: Sequence[NettingSession], spec: Any,
                  initial_f: float, fee_cash_role: str, timing: str | None) -> RS04ResearchPolicy:
    from .partial_rebalance import HardPolicyContext, RetrospectivePolicyContext, NoPluginCorePolicyContext, _context

    if type(policy) not in (RS04ResearchPolicy, RS04RetrospectiveResearchPolicy, RS04NoPluginCoreResearchPolicy):
        raise ValueError("RS04_FIXED_RESEARCH_POLICY_REQUIRED")
    admitted = type(policy) in (RS04RetrospectiveResearchPolicy, RS04NoPluginCoreResearchPolicy)
    core_only = type(policy) is RS04NoPluginCoreResearchPolicy
    if admitted and (policy.profile not in ('TQQQ', 'SOXL') or not policy.source_identity
                     or policy.cvar_descriptor is not None):
        raise ValueError('RS04_RETROSPECTIVE_R0_R3_ONLY')
    universe = policy.security_universe
    if (not isinstance(universe, tuple) or not universe or 'BOXX' not in universe
            or any(not isinstance(s, str) or not s or s != s.upper() for s in universe)
            or len(set(universe)) != len(universe) or set(universe) != set(spec.bps_per_side)):
        raise ValueError("RS04_COMPLETE_SECURITY_UNIVERSE_REQUIRED")
    if (initial_f != 1. or spec.outer_cash_mode != 'separate'
            or spec.cash_like_symbols != ('BOXX',) or timing != 'next_close'
            or fee_cash_role != spec.inner_residual_role):
        raise ValueError("RS04_FLAT_F1_SEPARATE_NEXT_CLOSE_REQUIRED")
    if (type(policy.opening) is not DecisionSnapshot or policy.opening.current_known is not True):
        raise ValueError("RS04_KNOWN_COMPLETED_BOOK_REQUIRED")
    _rs04_pairs(policy.opening.security_values, universe)
    completed_at = _rs04_time(policy.opening.completed_at)
    completed_session = policy.opening.completed_session
    if not isinstance(policy.decisions, tuple) or len(policy.decisions) != len(rows):
        raise ValueError("RS04_DECISION_COVERAGE_REQUIRED")
    for row, info in zip(rows, policy.decisions, strict=True):
        if (type(info) is not RS04DecisionInput or type(row) is not NettingSession
                or row.f_target != 1. or row.decision_session != completed_session
                or info.decision_session != row.decision_session or info.fill_session != row.session):
            raise ValueError("RS04_COMPLETED_ANCHOR_MISMATCH")
        cutoff, fill, available = map(_rs04_time, (info.cutoff, info.fill_completed_at,
                                                  info.full_target_available_at))
        if (cutoff.date() < row.decision_session or fill.date() != row.session
                or not completed_at <= cutoff < fill or not available <= cutoff):
            raise ValueError("RS04_DECISION_TIME_MISMATCH")
        if not isinstance(info.full_target_source, str) or not info.full_target_source.strip():
            raise ValueError("RS04_FULL_TARGET_SOURCE_REQUIRED")
        target = _rs04_pairs(info.full_target, universe)
        if not isinstance(row.inner_target, Mapping) or set(row.inner_target) != set(universe):
            raise ValueError("RS04_FULL_TARGET_INCOMPLETE")
        if any(_finite(row.inner_target[s], "RS04_TARGET_INVALID") != target[s] for s in universe):
            raise ValueError("RS04_FROZEN_TARGET_MISMATCH")
        context = info.hard_policy_context
        expected_type = (NoPluginCorePolicyContext if core_only else
                         RetrospectivePolicyContext if admitted else HardPolicyContext)
        if type(context) is not expected_type:
            raise ValueError('RS04_ORIGINAL_POLICY_REQUIRED')
        if core_only and (info.mandatory_reduce or info.forced_target is not None
                          or info.forced_source is not None or info.forced_reason is not None):
            raise ValueError('RS04_CORE_ONLY_EXTERNAL_MODULE_NOT_ADMITTED')
        if admitted:
            _context(context, universe)
            if (context.scope != 'RS04:' + policy.profile
                    or context.source_identity != policy.source_identity
                    or info.full_target_source != context.exact_source
                    or info.cvar_request is not None
                    or context.max_one_way_risk_turnover is not None):
                raise ValueError('RS04_RETROSPECTIVE_POLICY_BINDING_UNAVAILABLE')
        if context.cap_basis != 'DECISION_TIME_TARGET':
            raise ValueError("RS04_UNSUPPORTED_ORIGINAL_CAP_BASIS")
        if type(info.mandatory_reduce) is not bool:
            raise ValueError("RS04_MANDATORY_FLAG_REQUIRED")
        if info.forced_target is not None:
            _rs04_pairs(info.forced_target, universe)
        completed_at, completed_session = fill, row.session
    return policy


def _rs04_current_weights(snapshot: DecisionSnapshot) -> dict[str, float]:
    """Encode a reconciled book, never cash available for execution.

    Keep ordinary funded division unchanged. If it cannot express the book's
    cash status, apportion 2**53 binary units across securities AND actual cash
    by largest remainder (stable symbol order). Each component differs from
    its exact share of total book value by <2**-53; the exact float sum is one,
    including cash. Zero cash receives zero units, never phantom funding.
    Relative to v/NAV the additional bound is |v/total - v/NAV|. Reconciliation
    remains unchanged; all hard checks use v/NAV itself, not this encoding.
    """
    from fractions import Fraction

    values = dict(snapshot.security_values)
    weights = {s: v / snapshot.nav for s, v in values.items()}
    residual = 1 - sum(map(Fraction, weights.values()), Fraction())
    if residual >= 0 and (snapshot.cash > 0 or residual == 0):
        return weights
    amounts = [Fraction(values[s]) for s in sorted(values)] + [Fraction(snapshot.cash)]
    total = sum(amounts, Fraction())
    scaled = [v * 2**53 / total for v in amounts]
    units = [v.numerator // v.denominator for v in scaled]
    remainder_order = sorted(range(len(units)), key=lambda i: scaled[i] - units[i], reverse=True)
    for i in remainder_order[:2**53 - sum(units)]:
        units[i] += 1
    return {s: units[i] / 2**53 for i, s in enumerate(sorted(values))}


def _rs04_select(policy: RS04ResearchPolicy, info: RS04DecisionInput,
                 snapshot: DecisionSnapshot):
    from fractions import Fraction
    from .partial_rebalance import _Refusal, _inside_band, _number, decide_partial_rebalance

    current = {s: Fraction(v) / Fraction(snapshot.nav) for s, v in snapshot.security_values}
    encoded = _rs04_current_weights(snapshot)
    mode, lam = policy.mode, policy.lambda_value
    # Apportionment must not change the band action. Reuse the kernel's band
    # rule on actual ratios, then ask the real kernel to validate that action
    # if its float encoding falls on the other side. No rejected call is retried.
    try:
        _number(lam, 'INVALID_LAMBDA', 1)
        band = _number(policy.band, 'INVALID_BAND', 1)
    except _Refusal as exc:
        raise ValueError('RS04_DECISION_' + exc.status + ':' + exc.reason) from None
    band_hold = None
    if mode == 'BAND_HOLD' and not info.mandatory_reduce:
        desired = dict(info.full_target)
        band_hold = _inside_band(current, {s: Fraction(v) for s, v in desired.items()}, band)
        if band_hold != _inside_band(encoded, desired, band):
            mode, lam = ('PROPORTIONAL', 0.) if band_hold else ('FULL', lam)

    decision = decide_partial_rebalance(
        security_universe=policy.security_universe,
        current_weights=encoded,
        full_target=dict(info.full_target), mode=mode,
        lambda_value=lam, band=policy.band,
        decision_session=info.decision_session.isoformat(), cutoff=info.cutoff,
        hard_policy_context=info.hard_policy_context, mandatory_reduce=info.mandatory_reduce,
        forced_target=None if info.forced_target is None else dict(info.forced_target),
        forced_source=info.forced_source, forced_reason=info.forced_reason)
    if decision.status != 'ACCEPTED':
        raise ValueError('RS04_DECISION_' + decision.status + ':' + decision.reason)
    _rs04_actual_check(info, current,
        current if decision.kind == 'HOLD_POSITIONS' else dict(decision.target_weights),
        stage='DECISION', mandatory=info.mandatory_reduce)
    if mode != policy.mode:
        # input_summary hashes the effective, reproducible kernel call above;
        # public mode/reason describe the requested band action on the real book.
        decision = replace(decision, mode=policy.mode, lambda_value=policy.lambda_value,
                           reason='WITHIN_BAND_HOLD' if band_hold else 'FULL_TARGET')
    return decision


def _rs04_actual_check(info: RS04DecisionInput, current: Mapping[str, Any],
                      target: Mapping[str, Any], *, stage: str, mandatory: bool) -> None:
    from .partial_rebalance import _Refusal, _context, _hard_check

    # Validate the frozen action; never call the decision selector a second time.
    try:
        context = _context(info.hard_policy_context, tuple(current))
        if mandatory and any(target[s.symbol] > current[s.symbol]
                             for s in context.securities if s.is_risk):
            raise _Refusal('FORCED_RISK_INCREASE')
        _hard_check(target, current, context)
    except _Refusal as exc:
        raise ValueError('RS04_' + stage + '_' + exc.status + ':' + exc.reason) from None


def _rs04_receipt(decision: Any, info: RS04DecisionInput, snapshot: DecisionSnapshot) -> dict[str, Any]:
    from dataclasses import asdict

    return {'decision': asdict(decision), 'snapshot': {
        'completed_session': snapshot.completed_session.isoformat(),
        'completed_at': snapshot.completed_at, 'nav': snapshot.nav,
        'security_values': dict(snapshot.security_values), 'cash': snapshot.cash,
        'current_known': snapshot.current_known},
        'full_target_source': info.full_target_source,
        'full_target_available_at': info.full_target_available_at,
        'full_target': dict(info.full_target), 'mandatory_reduce': info.mandatory_reduce,
        'original_policy': asdict(info.hard_policy_context)}


def _rs04_constrain_tail(policy: RS04ResearchPolicy, info: RS04DecisionInput,
                         snapshot: DecisionSnapshot, proposal: Any, spec: Any):
    """Consume the accepted pure kernel after B5, before any actual return read.

    Only the complete original target-cap policy is representable here. A bounded
    turnover permission is deliberately unsupported: omitting it would certify a
    different feasible set. Original exact-rational checks remain authoritative.
    """
    from dataclasses import asdict
    from fractions import Fraction
    from .partial_rebalance import _context, _Refusal, _weights
    from .cvar_exposure_constraint import (
        Action, ApprovedAnchor, CostSpec, DecisionBook, ExposureCap, HardPolicy,
        ScenarioSet, TailDescriptor, TailPolicy, constrain_tail_target,
    )

    def refuse(status: str, reason: str):
        raise ValueError('RS04_CVAR_' + status + ':' + reason)

    request = info.cvar_request
    if type(request) is not RS04CVaRRequest:
        refuse('REJECTED_INPUT', 'fixed request required')
    descriptor = policy.cvar_descriptor
    if (type(descriptor) is not TailDescriptor or not isinstance(descriptor.scope, str)
            or not descriptor.scope.strip()
            or descriptor.benchmark != 'ZERO_RETURN_RESIDUAL_CASH'):
        refuse('UNAVAILABLE', 'explicit scope/currency/zero-cash descriptor required')
    scenarios, tail_policy, costs = request.scenarios, request.tail_policy, request.cost_spec
    if type(scenarios) is not ScenarioSet or type(tail_policy) is not TailPolicy:
        refuse('UNAVAILABLE', 'missing scenarios or tail policy')
    if scenarios.descriptor != descriptor or tail_policy.descriptor != descriptor:
        refuse('UNAVAILABLE', 'scope/currency/horizon/descriptor mismatch')
    if scenarios.symbols != policy.security_universe:
        refuse('UNAVAILABLE', 'complete ordered security universe required')
    if scenarios.synthetic is not True or tail_policy.synthetic_policy is not True:
        refuse('UNAVAILABLE', 'consumer is synthetic only')
    if scenarios.quality_attested is not True:
        refuse('UNAVAILABLE', 'scenario quality declaration required')
    # Labels alone never bind a policy: compare its complete canonical content,
    # including classification, units, caps, vetoes, turnover and cap basis.
    try:
        context = _context(info.hard_policy_context, policy.security_universe)
        bound = _context(request.original_policy, policy.security_universe)
    except _Refusal as exc:
        refuse('UNAVAILABLE', exc.reason)
    if asdict(bound) != asdict(context):
        refuse('UNAVAILABLE', 'original policy content drift')
    if context.cap_basis != 'DECISION_TIME_TARGET':
        refuse('UNSUPPORTED_HARD_POLICY', 'original fill/post-fee basis unsupported')
    if context.turnover_basis != 'RISK_BUY_WEIGHT' or context.weight_unit != 'NAV_FRACTION':
        refuse('UNSUPPORTED_HARD_POLICY', 'original policy units or basis unsupported')
    if context.max_one_way_risk_turnover is not None:
        refuse('UNSUPPORTED_HARD_POLICY', 'bounded original turnover is not represented by kernel')
    if (type(costs) is not CostSpec or costs.model != 'FIXED_NONNEGATIVE_BPS'
            or costs.unit != 'BPS_PER_SIDE' or costs.multiplier_unit != 'DIMENSIONLESS'):
        refuse('UNSUPPORTED_COST_MODEL', 'fixed ledger per-side bps required')
    if (not isinstance(costs.bps_by_symbol, Mapping)
            or set(costs.bps_by_symbol) != set(spec.bps_per_side)
            or any(_finite(costs.bps_by_symbol[s], 'RS04_CVAR_REJECTED_COST') != spec.bps_per_side[s]
                   for s in spec.bps_per_side)
            or _finite(costs.frozen_multiplier, 'RS04_CVAR_REJECTED_COST') != spec.multiplier):
        refuse('UNAVAILABLE', 'forecast cost differs from actual ledger cost')
    sources = {'scenarios': scenarios.source, 'tail_policy': tail_policy.source,
               'cost': None, 'original_policy': context.source}
    if request.anchor is not None:
        if type(request.anchor) is not ApprovedAnchor:
            refuse('REJECTED_INPUT', 'invalid approved anchor')
        sources['anchor'] = request.anchor.source
    known = request.known_inputs
    if (not isinstance(known, tuple) or len(known) != len(sources)
            or any(not isinstance(row, tuple) or len(row) != 4 for row in known)
            or {row[0] for row in known} != set(sources)):
        refuse('UNAVAILABLE', 'complete source/as_of/available_at required')
    for component, source, as_of, available_at in known:
        if (not isinstance(source, str) or not source.strip()
                or (sources[component] is not None and source != sources[component])):
            refuse('UNAVAILABLE', 'source binding mismatch')
        if not _rs04_time(as_of) <= _rs04_time(available_at) <= _rs04_time(info.cutoff):
            refuse('UNAVAILABLE', 'input unavailable at decision cutoff')
    # Caps are generated from the original gate, never a caller-supplied wider
    # HardPolicy. All coefficients are nonnegative linear target exposures.
    universe = policy.security_universe
    def cap(coefficients, limit):
        return ExposureCap({s: coefficients.get(s, 0.) for s in universe}, limit, 'TARGET_ONLY')
    caps = [cap({item.symbol: 1.}, item.max_weight) for item in context.securities]
    risks = tuple(item for item in context.securities if item.is_risk)
    caps.append(cap({item.symbol: item.effective_exposure_factor for item in risks},
                    context.max_effective_risk_exposure))
    caps.extend(cap({item.symbol: item.effective_exposure_factor for item in risks
                     if item.underlying == underlying}, limit)
                for underlying, limit in context.max_underlying_exposure)
    caps.extend(cap({symbol: 1.}, 0.) for symbol in context.vetoed_symbols)
    hard = HardPolicy(tuple(caps), tuple(item.symbol for item in risks), context.source,
                      True, True, True)
    # B5's funded weight encoding is a kernel preview, not the actual capital.
    # Its normalized residual cannot certify the native money/NAV tail budget.
    book = DecisionBook(_rs04_current_weights(snapshot), snapshot.nav, descriptor.currency, 0.)
    action = Action(proposal.kind, None if proposal.target_weights is None else dict(proposal.target_weights))
    forced = None if info.forced_target is None else dict(info.forced_target)
    current = {s: Fraction(v) / Fraction(snapshot.nav) for s, v in snapshot.security_values}
    # The float scenario kernel cannot certify a one-ulp original-gate breach.
    # Invalid anchors remain unusable even when their float preview looks safe.
    anchor = request.anchor
    anchor_refusal = None
    if anchor is not None and isinstance(anchor.action, Action) and anchor.action.kind == 'TARGET_WEIGHTS':
        try:
            anchor_weights = _weights(anchor.action.target_weights, universe)
            _rs04_actual_check(info, current, anchor_weights, stage='CVAR_ANCHOR',
                mandatory=info.mandatory_reduce or action.kind == 'HOLD_POSITIONS')
        except (_Refusal, ValueError) as exc:
            anchor_refusal = str(exc)
            anchor = replace(anchor, approved=False)
    constrained = constrain_tail_target(action, anchor, tail_policy, scenarios,
        decision_book=book, bps_spec=costs, hard_policy=hard, forced_target=forced)
    if constrained.action is None or constrained.evaluation is None or not constrained.evaluation.feasible:
        refuse(constrained.status, constrained.reason)
    selected = constrained.action
    try:
        target = current if selected.kind == 'HOLD_POSITIONS' else _weights(selected.target_weights, universe)
    except _Refusal as exc:
        refuse('REJECTED_INPUT', 'final funding: ' + exc.reason)
    _rs04_actual_check(info, current, target, stage='CVAR_FINAL',
        mandatory=info.mandatory_reduce or constrained.status == 'MANDATORY_TAIL_REDUCTION')
    # Certify only this selected action, using the same known joint forecasts.
    # Exact arithmetic preserves actual cash and any reconciled total/NAV gap;
    # neither a synthetic residual nor a budget tolerance is risk permission.
    # This is a local certificate, not another selector or an execution ledger.
    values = {s: Fraction(v) for s, v in snapshot.security_values}
    cash, nav = Fraction(snapshot.cash), Fraction(snapshot.nav)
    rates = {s: Fraction(costs.bps_by_symbol[s]) / 10000 * Fraction(costs.frozen_multiplier)
             for s in universe}
    native_losses = []
    for scenario in scenarios.scenarios:
        pre = {s: values[s] * (1 + Fraction(scenario.fill_returns[s])) for s in universe}
        pre_nav = sum(pre.values(), cash)
        if pre_nav <= 0:
            refuse('UNCOMPUTABLE', 'native book nonpositive forecast fill NAV')
        if selected.kind == 'HOLD_POSITIONS':
            end, remaining, fee = pre, cash, Fraction()
        else:
            end = {s: pre_nav * Fraction(target[s]) for s in universe}
            fee = sum((abs(end[s] - pre[s]) * rates[s] for s in universe), Fraction())
            remaining = pre_nav - sum(end.values(), Fraction()) - fee
        endpoint = sum((end[s] * (1 + Fraction(scenario.earn_returns[s]))
                        for s in universe), remaining)
        if remaining < 0 or endpoint <= 0 or fee >= pre_nav:
            refuse('UNCOMPUTABLE', 'native book selected action fails forecast funding/positive NAV')
        native_losses.append((1 - endpoint / nav, Fraction(scenario.probability)))
    mass = 1 - Fraction(descriptor.alpha_confidence)
    remaining_mass, tail_loss = mass, Fraction()
    for loss, probability in sorted(native_losses, key=lambda pair: pair[0], reverse=True):
        take = min(remaining_mass, probability)
        tail_loss += take * loss
        remaining_mass -= take
        if not remaining_mass:
            break
    if remaining_mass or tail_loss / mass > Fraction(tail_policy.budget):
        refuse('UNCOMPUTABLE', 'native book selected action fails exact tail budget certificate')
    native_certificate = {'basis': 'ACTUAL_VALUES_CASH_UNCHANGED_DECISION_NAV',
        'security_values': dict(snapshot.security_values), 'cash': snapshot.cash, 'nav': snapshot.nav,
        'es_fraction': str(tail_loss / mass), 'budget_fraction': str(Fraction(tail_policy.budget))}
    if constrained.status != 'PROPOSAL_FEASIBLE':
        proposal = replace(proposal, kind=selected.kind,
            target_weights=tuple(selected.target_weights.items()),
            residual_cash=float(1 - sum(map(Fraction, target.values()), Fraction())),
            reason=constrained.status)
    return proposal, {'constraint': asdict(constrained), 'decision_book': asdict(book),
        'native_book_certificate': native_certificate,
        'descriptor': asdict(descriptor), 'cost': asdict(costs), 'hard_policy': asdict(hard),
        'known_inputs': known, 'policy_source': tail_policy.source, 'budget': tail_policy.budget,
        'anchor': None if request.anchor is None else asdict(request.anchor),
        'anchor_original_gate_refusal': anchor_refusal,
        'execution_action': asdict(selected), 'policy_authenticated': False,
        'pit_authenticated': False, 'endpoint_is_before_next_fill': True}
