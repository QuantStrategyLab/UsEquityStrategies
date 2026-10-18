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
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

RS06_TURNOVER_CURRENT_BOOK_BASIS = "RS06_SYNTHETIC_FILL_SESSION_PRE_TRADE_PRE_FEE_NET_NAV_V1"


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
    execution_timing: str | None = None, report_periods_per_year: int | None = None,
    rs06_risk_budget: Mapping[str, Any] | None = None,
    report_sharpe_basis: str = "legacy_net_rf0_v1",
    report_risk_free_returns: Sequence[float] | None = None,
    report_risk_free_source: str | None = None, report_calendar_id: str | None = None,
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

    ``rs06_risk_budget`` is only the explicit synthetic single-unit RS06
    f=1/zero-cash-return context. Its gate uses fill-session marks, not prior
    decision information; it establishes no real currency/FX qualification.
    """
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
        effective_target = row.inner_target
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
    return output
