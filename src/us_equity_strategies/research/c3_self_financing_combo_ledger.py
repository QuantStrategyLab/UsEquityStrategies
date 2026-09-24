"""Synthetic SOXL/TQQQ/cash self-financing ledgers for one acceptance case.

Member net returns are already net of member-level costs and are not charged
again. Combo-layer fees, when requested, are a synthetic assumption on the
absolute traded dollars of the declared risk legs after same-day returns.
The post-fee NAV is solved so those target dollars and the cash residual are
funded by available NAV. This is not the pre-trade weight approximation in
``simulate_fixed_budget_capital_path``.

Cash earns a fixed zero return. That is a synthetic assumption: this module
does not supply a real cash rate. The initial book is already at the ledger's
target weights, so there is no initial establishment fee. Nothing here
authorizes an order.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import date
from itertools import pairwise

from quant_platform_kit.strategy_lifecycle.contracts import (
    ResearchDailyLedger,
    ResearchLedgerDay,
    ResearchPositionMark,
)

from us_equity_strategies.research.c3_capital_path import scale_member_budgets_to_cash

SOXL = "SOXL"
TQQQ = "TQQQ"
CASH = "CASH"
RAW_FIXED_BUDGET = "raw_fixed_budget"
RISK_SCALED = "risk_scaled"
RISK_SCALED_WITH_SYNTHETIC_COMBO_FEE = "risk_scaled_with_synthetic_combo_fee"
COST_SOURCE = "SYNTHETIC_ASSUMPTION_NOT_A_LIVE_CHARGE"

_MEMBERS = (SOXL, TQQQ, CASH)
_RISK_LEGS = frozenset((SOXL, TQQQ))
_EPSILON = 1e-12
_LEDGER_SPECS = (
    (RAW_FIXED_BUDGET, "synthetic-soxl-tqqq-raw-fixed-budget", False, False),
    (RISK_SCALED, "synthetic-soxl-tqqq-risk-scaled", True, False),
    (
        RISK_SCALED_WITH_SYNTHETIC_COMBO_FEE,
        "synthetic-soxl-tqqq-risk-scaled-combo-fee",
        True,
        True,
    ),
)


def _number(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(code)
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(code)
    return number


def _target_weights(value: Mapping[str, float]) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != set(_MEMBERS):
        raise ValueError("SELF_FINANCING_WEIGHT_INVALID")
    weights = {
        member: _number(value[member], "SELF_FINANCING_WEIGHT_INVALID") for member in _MEMBERS
    }
    if any(weight < 0.0 for weight in weights.values()):
        raise ValueError("SELF_FINANCING_WEIGHT_INVALID")
    if not math.isclose(math.fsum(weights.values()), 1.0, rel_tol=0.0, abs_tol=_EPSILON):
        raise ValueError("SELF_FINANCING_WEIGHT_INVALID")
    return weights


def _return_series(value: Sequence[float], *, other_length: int | None) -> tuple[float, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) < 1:
        raise ValueError("SELF_FINANCING_RETURN_INVALID")
    if other_length is not None and len(value) != other_length:
        raise ValueError("SELF_FINANCING_RETURN_LENGTH_MISMATCH")
    series: list[float] = []
    for item in value:
        number = _number(item, "SELF_FINANCING_RETURN_INVALID")
        if number <= -1.0:
            raise ValueError("SELF_FINANCING_RETURN_INVALID")
        series.append(number)
    return tuple(series)


def _session_dates(initial: date, sessions: Sequence[date], count: int) -> tuple[date, ...]:
    if type(initial) is not date:
        raise ValueError("SELF_FINANCING_DATE_MISALIGNED")
    if isinstance(sessions, (str, bytes)) or not isinstance(sessions, Sequence):
        raise TypeError("SELF_FINANCING_DATE_MISALIGNED")
    if len(sessions) != count:
        raise ValueError("SELF_FINANCING_DATE_MISALIGNED")
    parsed: list[date] = []
    previous = initial
    for item in sessions:
        if type(item) is not date or item <= previous:
            raise ValueError("SELF_FINANCING_DATE_MISALIGNED")
        parsed.append(item)
        previous = item
    return tuple(parsed)


def _rebalance_indices(value: Sequence[int], count: int) -> frozenset[int]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError("SELF_FINANCING_REBALANCE_INDEX_INVALID")
    indices: set[int] = set()
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise TypeError("SELF_FINANCING_REBALANCE_INDEX_INVALID")
        if item < 0 or item >= count:
            raise ValueError("SELF_FINANCING_REBALANCE_INDEX_INVALID")
        indices.add(item)
    return frozenset(indices)


def _fee_bearing(value: Sequence[str], fee_rate: float) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError("SELF_FINANCING_FEE_BEARING_INVALID")
    if len(value) == 0:
        if fee_rate > 0.0:
            raise ValueError("SELF_FINANCING_FEE_BEARING_INVALID")
        return ()
    seen: list[str] = []
    for item in value:
        if not isinstance(item, str) or item not in _RISK_LEGS or item in seen:
            raise ValueError("SELF_FINANCING_FEE_BEARING_INVALID")
        seen.append(item)
    return tuple(sorted(seen))


def _absolute_fee(
    grown: Mapping[str, float],
    weights: Mapping[str, float],
    nav: float,
    fee_rate: float,
    fee_bearing: Sequence[str],
) -> float:
    traded = math.fsum(abs(weights[member] * nav - grown[member]) for member in fee_bearing)
    return fee_rate * traded


def _affine_trade(
    grown: Mapping[str, float],
    weights: Mapping[str, float],
    fee_bearing: Sequence[str],
    probe: float,
) -> tuple[float, float]:
    """Return slope and intercept of fee-bearing |target - grown| at ``probe``."""

    slope = 0.0
    intercept = 0.0
    for member in fee_bearing:
        weight = weights[member]
        pre = grown[member]
        if weight <= _EPSILON:
            intercept += abs(pre)
            continue
        if weight * probe >= pre:
            slope += weight
            intercept -= pre
        else:
            slope -= weight
            intercept += pre
    return slope, intercept


def _solve_post_fee_nav(
    grown: Mapping[str, float],
    weights: Mapping[str, float],
    fee_rate: float,
    fee_bearing: Sequence[str],
) -> float:
    """NAV after a same-day rebalance whose fee is funded by that NAV."""

    v_pre = grown[SOXL] + grown[TQQQ] + grown[CASH]
    if fee_rate == 0.0 or not fee_bearing:
        return v_pre
    cuts = [0.0, v_pre]
    for member in fee_bearing:
        weight = weights[member]
        if weight > _EPSILON:
            cuts.append(grown[member] / weight)
    ordered = sorted(point for point in cuts if math.isfinite(point))
    tolerance = 1e-8 * max(1.0, abs(v_pre))
    found: list[float] = []
    for left, right in pairwise(ordered):
        seg_left = max(left, 0.0)
        seg_right = min(right, v_pre)
        if seg_right <= seg_left:
            continue
        slope, intercept = _affine_trade(
            grown, weights, fee_bearing, (seg_left + seg_right) / 2.0
        )
        denominator = 1.0 + fee_rate * slope
        numerator = v_pre - fee_rate * intercept
        if abs(denominator) <= _EPSILON:
            if abs(numerator) <= tolerance and seg_right - seg_left > 1e-6:
                found.extend((seg_left, seg_right))
            continue
        nav = numerator / denominator
        if nav < seg_left - 1e-8 or nav > seg_right + 1e-8 or nav <= 0.0:
            continue
        fee = _absolute_fee(grown, weights, nav, fee_rate, fee_bearing)
        if fee < 0.0 or abs((nav + fee) - v_pre) > tolerance:
            continue
        if any(math.isclose(nav, prior, rel_tol=0.0, abs_tol=1e-8) for prior in found):
            continue
        found.append(nav)
    if len(found) != 1:
        raise ValueError("SELF_FINANCING_NAV_UNSOLVED")
    nav = found[0]
    if nav > v_pre:
        if nav - v_pre > tolerance:
            raise ValueError("SELF_FINANCING_NAV_UNSOLVED")
        return v_pre
    return nav


def _marks(
    soxl_units: float, soxl_value: float, tqqq_units: float, tqqq_value: float
) -> tuple[ResearchPositionMark, ...]:
    marks: list[ResearchPositionMark] = []
    for symbol, units, value in (
        (SOXL, soxl_units, soxl_value),
        (TQQQ, tqqq_units, tqqq_value),
    ):
        if units == 0.0:
            continue
        if units < 0.0 or value <= 0.0:
            raise ValueError("SELF_FINANCING_NAV_UNSOLVED")
        marks.append(ResearchPositionMark(symbol=symbol, quantity=units, valuation=value))
    return tuple(marks)


def _book(
    *,
    weights: Mapping[str, float],
    soxl_returns: Sequence[float],
    tqqq_returns: Sequence[float],
    sessions: Sequence[date],
    initial_capital: float,
    rebalance_on: frozenset[int],
    fee_rate: float,
    fee_bearing: Sequence[str],
) -> tuple[tuple[ResearchPositionMark, ...], float, tuple[ResearchLedgerDay, ...]]:
    soxl = weights[SOXL] * initial_capital
    tqqq = weights[TQQQ] * initial_capital
    if weights[SOXL] == 0.0:
        soxl = 0.0
    if weights[TQQQ] == 0.0:
        tqqq = 0.0
    cash = initial_capital - soxl - tqqq
    if cash < 0.0:
        if cash < -1e-10:
            raise ValueError("SELF_FINANCING_CASH_NEGATIVE")
        cash = 0.0
    soxl_units = soxl
    tqqq_units = tqqq
    soxl_price = 1.0
    tqqq_price = 1.0
    opening_marks = _marks(soxl_units, soxl, tqqq_units, tqqq)
    opening_cash = cash
    previous_nav = cash + soxl + tqqq
    if previous_nav <= 0.0:
        raise ValueError("SELF_FINANCING_NAV_UNSOLVED")
    days: list[ResearchLedgerDay] = []
    for index, session in enumerate(sessions):
        soxl_price *= 1.0 + soxl_returns[index]
        tqqq_price *= 1.0 + tqqq_returns[index]
        if not all(math.isfinite(price) and price > 0.0 for price in (soxl_price, tqqq_price)):
            raise ValueError("SELF_FINANCING_NAV_UNSOLVED")
        grown = {
            SOXL: soxl_units * soxl_price,
            TQQQ: tqqq_units * tqqq_price,
            CASH: cash,
        }
        if index in rebalance_on:
            target_nav = _solve_post_fee_nav(grown, weights, fee_rate, fee_bearing)
            soxl = 0.0 if weights[SOXL] == 0.0 else weights[SOXL] * target_nav
            tqqq = 0.0 if weights[TQQQ] == 0.0 else weights[TQQQ] * target_nav
            soxl_units = soxl / soxl_price
            tqqq_units = tqqq / tqqq_price
            fee = _absolute_fee(grown, weights, target_nav, fee_rate, fee_bearing)
            trade_net = -((soxl - grown[SOXL]) + (tqqq - grown[TQQQ]))
            cash = grown[CASH] + trade_net - fee
        else:
            soxl = grown[SOXL]
            tqqq = grown[TQQQ]
            cash = grown[CASH]
            fee = 0.0
            trade_net = 0.0
        if fee < 0.0:
            raise ValueError("SELF_FINANCING_NAV_UNSOLVED")
        if cash < 0.0:
            if cash < -1e-10:
                raise ValueError("SELF_FINANCING_CASH_NEGATIVE")
            cash = 0.0
        nav = cash + soxl + tqqq
        if not math.isfinite(nav) or nav <= 0.0:
            raise ValueError("SELF_FINANCING_NAV_UNSOLVED")
        days.append(
            ResearchLedgerDay(
                session_date=session,
                cash=cash,
                positions=_marks(soxl_units, soxl, tqqq_units, tqqq),
                trade_net_cashflow=trade_net,
                fees=fee,
                nav=nav,
                daily_return=nav / previous_nav - 1.0,
            )
        )
        previous_nav = nav
    return opening_marks, opening_cash, tuple(days)


def _research_ledger(
    *,
    identity: str,
    input_digest: str,
    initial_session: date,
    opening_marks: tuple[ResearchPositionMark, ...],
    opening_cash: float,
    days: tuple[ResearchLedgerDay, ...],
    combo_fee_bps: float,
) -> ResearchDailyLedger:
    nav = opening_cash + sum(mark.valuation for mark in opening_marks)
    return ResearchDailyLedger(
        trial_id=f"{identity}-{input_digest[:16]}",
        domain="us_equity",
        strategy_profile=identity,
        run_id=f"{identity}-run-{input_digest[:16]}",
        param_version=1,
        input_id=f"synthetic-c3-self-financing-{input_digest}",
        calendar_id="synthetic_declared_sessions",
        periods_per_year=252.0,
        cost_source=COST_SOURCE,
        cost_inputs={"cash_daily_return": 0.0, "combo_fee_bps": combo_fee_bps},
        initial_session_date=initial_session,
        initial_nav=nav,
        initial_cash=opening_cash,
        initial_positions=opening_marks,
        days=days,
        synthetic=True,
    )


def build_soxl_tqqq_self_financing_ledgers(
    *,
    soxl_net_returns: Sequence[float],
    tqqq_net_returns: Sequence[float],
    initial_session_date: date,
    session_dates: Sequence[date],
    initial_capital: float,
    target_weights: Mapping[str, float],
    risk_scalar: float,
    rebalance_indices: Sequence[int],
    combo_fee_bps: float,
    fee_bearing_members: Sequence[str],
) -> dict[str, ResearchDailyLedger]:
    """Build the raw, risk-scaled, and fee-charged synthetic cash ledgers.

    ``session_dates`` are the return observations after ``initial_session_date``.
    Index 0 is the first of those dates, not the initial date. Rebalance
    indices select those observations. Each selected day first applies that
    day's SOXL and TQQQ net returns, leaves cash unchanged, then trades to the
    ledger's fixed target. Later returns are not an input to that trade.
    """

    weights = _target_weights(target_weights)
    capital = _number(initial_capital, "SELF_FINANCING_CAPITAL_INVALID")
    if capital <= 0.0:
        raise ValueError("SELF_FINANCING_CAPITAL_INVALID")
    soxl_returns = _return_series(soxl_net_returns, other_length=None)
    tqqq_returns = _return_series(tqqq_net_returns, other_length=len(soxl_returns))
    sessions = _session_dates(initial_session_date, session_dates, len(soxl_returns))
    fee_bps = _number(combo_fee_bps, "SELF_FINANCING_FEE_BPS_INVALID")
    if fee_bps < 0.0 or fee_bps >= 10_000.0:
        raise ValueError("SELF_FINANCING_FEE_BPS_INVALID")
    fee_rate = fee_bps / 10_000.0
    rebalance_on = _rebalance_indices(rebalance_indices, len(soxl_returns))
    if fee_rate > 0.0 and not rebalance_on:
        raise ValueError("SELF_FINANCING_FEE_SCHEDULE_REQUIRED")
    bearing = _fee_bearing(fee_bearing_members, fee_rate)
    if isinstance(risk_scalar, bool) or not isinstance(risk_scalar, (int, float)):
        raise TypeError("RISK_SCALAR_INVALID")
    scaled = scale_member_budgets_to_cash(
        budgets=weights,
        risk_scalar=risk_scalar,
        cash_member_id=CASH,
    )
    input_digest = hashlib.sha256(
        json.dumps(
            {
                "soxl_net_returns": soxl_returns,
                "tqqq_net_returns": tqqq_returns,
                "initial_session_date": initial_session_date.isoformat(),
                "session_dates": [session.isoformat() for session in sessions],
                "initial_capital": capital,
                "target_weights": weights,
                "risk_scalar": float(risk_scalar),
                "rebalance_indices": sorted(rebalance_on),
                "combo_fee_bps": fee_bps,
                "fee_bearing_members": bearing,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    ledgers: dict[str, ResearchDailyLedger] = {}
    for key, identity, use_scaled, charge_fee in _LEDGER_SPECS:
        opening_marks, opening_cash, days = _book(
            weights=scaled if use_scaled else weights,
            soxl_returns=soxl_returns,
            tqqq_returns=tqqq_returns,
            sessions=sessions,
            initial_capital=capital,
            rebalance_on=rebalance_on,
            fee_rate=fee_rate if charge_fee else 0.0,
            fee_bearing=bearing if charge_fee else (),
        )
        ledgers[key] = _research_ledger(
            identity=identity,
            input_digest=input_digest,
            initial_session=initial_session_date,
            opening_marks=opening_marks,
            opening_cash=opening_cash,
            days=days,
            combo_fee_bps=fee_bps if charge_fee else 0.0,
        )
    return ledgers


__all__ = [
    "CASH",
    "COST_SOURCE",
    "RAW_FIXED_BUDGET",
    "RISK_SCALED",
    "RISK_SCALED_WITH_SYNTHETIC_COMBO_FEE",
    "SOXL",
    "TQQQ",
    "build_soxl_tqqq_self_financing_ledgers",
]
