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

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any


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


def run_offline_security_netting(
    sessions: Sequence[NettingSession], *, initial_nav: float, initial_f: float,
    initial_inner_weights: Mapping[str, float], spec: Any, fee_cash_role: str,
) -> dict[str, Any]:
    """Replay one composed sleeve with explicit cash funding; no data or order I/O.

    ``gross_returns`` measure pre-fee returns on the same evolving net book,
    not an independently rerun cost-free counterfactual. Role weights overlap
    securities for inner cash-like assets and merged outer cash: never sum the
    role columns into NAV. Only ``security_end`` + ``virtual_cash_end`` sum to NAV.
    """
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
    inner = {s: nav * initial_f * w for s, w in initial_inner_weights.items()}
    inner_cash = nav * initial_f * max(0., 1 - sum(initial_inner_weights.values()))
    outer = nav * (1 - initial_f)
    ledger = []
    previous = None
    for row in sessions:
        if type(row) is not NettingSession or type(row.session) is not date:
            raise ValueError("SESSION_INVALID")
        if previous is not None and row.session <= previous:
            raise ValueError("SESSION_ORDER_INVALID")
        if type(row.decision_session) is not date or row.decision_session >= row.session:
            raise ValueError("DECISION_NOT_PRIOR")
        previous = row.session
        if not isinstance(row.security_returns, Mapping):
            raise ValueError("RETURN_INVALID")
        returns = {s: _return(r) for s, r in row.security_returns.items()}
        cash_return = _return(row.outer_cash_return)
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
        if not math.isfinite(pre_nav) or pre_nav <= 0:
            raise ValueError("NAV_INVALID")
        result = net_security_session(f_pre=inner_total / pre_nav, inner_pre=inner_pre,
                                      f_target=row.f_target, inner_target=row.inner_target, spec=spec)
        trades = {s: w * pre_nav for s, w in result.net_trades.items()}
        fee_by_security = {s: abs(value) * spec.bps_per_side[s] * spec.multiplier / 10_000
                           for s, value in trades.items()}
        fees = result.cost_fraction * pre_nav
        inner = {s: pre_nav * row.f_target * w for s, w in row.inner_target.items()}
        inner_cash = pre_nav * result.role_weights[spec.inner_residual_role]
        outer = pre_nav * (1 - row.f_target)
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
            "f_target": row.f_target, "inner_target": dict(row.inner_target),
            "fee_cash_role": fee_cash_role, "fee_by_security": fee_by_security, "fees": fees,
            "nav_end": nav, "gross_return": pre_nav / nav_start - 1,
            "net_return": nav / nav_start - 1,
            "inner_unnetted_turnover": result.inner_unnetted_turnover,
            "outer_unnetted_turnover": result.outer_unnetted_turnover,
            "net_security_turnover": result.net_security_turnover,
            "turnover_columns_summable": result.turnover_columns_summable,
            "reconciliation_errors": errors,
        })
    return {
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
