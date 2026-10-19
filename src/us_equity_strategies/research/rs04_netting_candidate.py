"""Thin CorePath -> close targets -> existing security net ledger, synthetic only.

This convention is an explicit research candidate while D09 remains unfrozen.
It does not reproduce the legacy evaluator's day-start virtual-asset rebalance.
CorePath.weights[i] earns session i; weights[i+1] therefore recovers the target
filled at close i, decided at the previous supplied trading session. Reading
that next earning row is reversing CorePath's storage convention, not waiting
one more session to fill. The final close target cannot be recovered this way
and must be supplied separately, with its prior decision session.

The opening book is an explicit already-held book, including its inner/outer
ownership (needed by the existing runner) and actual securities/cash. No initial
position, terminal liquidation, borrowing or extra BOXX return is invented.
Calendar completeness and the truth of caller-supplied final decisions remain
upstream obligations. No market, account, file or order I/O occurs here.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from .kelly_core_return_generator import CorePath, SOXL_ASSETS, TQQQ_ASSETS
from .security_netting_runner import NettingSession, run_offline_security_netting

NEXT_CLOSE_RESEARCH_CANDIDATE = "NEXT_CLOSE_RESEARCH_CANDIDATE"


@dataclass(frozen=True)
class InitialNettingBook:
    session: date  # preceding completed close; no free pre-window trades
    nav: float
    f: float
    inner_weights: Mapping[str, float]
    security_values: Mapping[str, float]
    cash: float  # actual zero-return residual, not BOXX


@dataclass(frozen=True)
class CloseTarget:
    decision_session: date
    fill_session: date
    weights: Mapping[str, float]


@dataclass(frozen=True)
class OuterExposure:
    decision_session: date
    fill_session: date
    f_target: float


def _number(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(code)
    return float(value)


def _weights(value: Mapping[str, float]) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise ValueError("INNER_WEIGHTS_INVALID")
    out = {}
    for s, w in value.items():
        if not isinstance(s, str) or not s or _number(w, "INNER_WEIGHTS_INVALID") < 0.:
            raise ValueError("INNER_WEIGHTS_INVALID")
        out[s] = float(w)
    if math.fsum(out.values()) > 1. + 1e-12:
        raise ValueError("INNER_WEIGHTS_INVALID")
    return out


def _equal_maps(left: Mapping[str, float], right: Mapping[str, float]) -> bool:
    return all(math.isclose(left.get(s, 0.), right.get(s, 0.), rel_tol=1e-10, abs_tol=1e-10)
               for s in set(left) | set(right))


def build_rs04_netting_sessions(
    *, core_path: CorePath, initial_book: InitialNettingBook, last_close_target: CloseTarget,
    trading_sessions: Sequence[date], outer_exposures: Sequence[OuterExposure],
    synthetic_fee_spec: Any, execution_convention: str,
) -> tuple[NettingSession, ...]:
    """Recover close targets on exactly [opening anchor, *CorePath.sessions].

    Require an already active no_plugin_core path: first_signal must be no later
    than the opening anchor. Empty weights then mean explicit zero-return cash,
    rather than an unknown warmup state. The first earning book must match the
    declared opening inner allocation. Core gross returns are checked against
    earning weights/asset returns; neither legacy nor close-fill turnover is
    used as a target or charged as an additional fee.
    """
    if type(execution_convention) is not str or execution_convention != NEXT_CLOSE_RESEARCH_CANDIDATE:
        raise ValueError("EXPLICIT_RESEARCH_CONVENTION_REQUIRED")
    if type(initial_book) is not InitialNettingBook:
        raise ValueError("INITIAL_BOOK_REQUIRED")
    if type(last_close_target) is not CloseTarget:
        raise ValueError("LAST_CLOSE_TARGET_REQUIRED")
    if (type(core_path) is not CorePath or core_path.variant != "no_plugin_core"
            or core_path.strategy_key not in ("SOXL", "TQQQ")):
        raise ValueError("RS04_CORE_PATH_REQUIRED")
    calendar = tuple(trading_sessions)
    if any(type(d) is not date for d in calendar) or any(a >= b for a, b in zip(calendar, calendar[1:])):
        raise ValueError("TRADING_SESSION_ORDER")
    if (not core_path.sessions or len(calendar) != len(core_path.sessions) + 1
            or type(initial_book.session) is not date or calendar[0] != initial_book.session
            or calendar[1:] != tuple(core_path.sessions)):
        raise ValueError("TRADING_SESSION_ALIGNMENT")
    if type(core_path.first_signal) is not date or core_path.first_signal > calendar[0]:
        raise ValueError("CORE_DECISION_HISTORY_REQUIRED")
    n = len(core_path.sessions)
    if len(core_path.weights) != n or len(core_path.gross_returns) != n:
        raise ValueError("CORE_PATH_ALIGNMENT")
    if (last_close_target.decision_session != calendar[-2]
            or last_close_target.fill_session != calendar[-1]):
        raise ValueError("LAST_CLOSE_TARGET_TIMELINE")
    exposures = tuple(outer_exposures)
    if len(exposures) != n:
        raise ValueError("OUTER_EXPOSURES_REQUIRED")
    for i, exposure in enumerate(exposures):
        if (type(exposure) is not OuterExposure or exposure.decision_session != calendar[i]
                or exposure.fill_session != calendar[i + 1]):
            raise ValueError("OUTER_EXPOSURE_TIMELINE")
        if not 0. <= _number(exposure.f_target, "OUTER_FRACTION_INVALID") <= 1.:
            raise ValueError("OUTER_FRACTION_INVALID")

    from quant_platform_kit.research_stats.security_netting import (
        SecurityNettingSpec, compose_security_targets,
    )

    spec = synthetic_fee_spec
    if (type(spec) is not SecurityNettingSpec or spec.outer_cash_mode != "merge_into_security"
            or spec.outer_cash_symbol != "BOXX" or spec.cash_like_symbols != ("BOXX",)):
        raise ValueError("EXPLICIT_MERGED_BOXX_SPEC_REQUIRED")
    earning = tuple(_weights(w) for w in core_path.weights)
    terminal = _weights(last_close_target.weights)
    opening = _weights(initial_book.inner_weights)
    if not _equal_maps(opening, earning[0]):
        raise ValueError("INITIAL_BOOK_EARNING_MISMATCH")
    nav = _number(initial_book.nav, "INITIAL_BOOK_INVALID")
    cash = _number(initial_book.cash, "INITIAL_BOOK_INVALID")
    if nav <= 0. or cash < 0. or not isinstance(initial_book.security_values, Mapping):
        raise ValueError("INITIAL_BOOK_INVALID")
    actual = {s: _number(v, "INITIAL_BOOK_INVALID") for s, v in initial_book.security_values.items()}
    if any(not isinstance(s, str) or not s or v < 0. for s, v in actual.items()):
        raise ValueError("INITIAL_BOOK_INVALID")
    securities, roles = compose_security_targets(f=initial_book.f, inner_weights=opening, spec=spec)
    expected = {s: w * nav for s, w in securities.items()}
    if (not _equal_maps(actual, expected)
            or not math.isclose(cash, nav * roles[spec.inner_residual_role], rel_tol=1e-10, abs_tol=1e-10)
            or not math.isclose(nav, math.fsum(actual.values()) + cash, rel_tol=1e-10, abs_tol=1e-10)):
        raise ValueError("INITIAL_BOOK_RECONCILIATION")
    symbols = {"BOXX"} | set(actual) | set(opening) | set(terminal)
    for weights in earning:
        symbols.update(weights)
    assets = SOXL_ASSETS if core_path.strategy_key == "SOXL" else TQQQ_ASSETS
    if not symbols.issubset(assets):
        raise ValueError("RS04_SECURITY_UNIVERSE")
    if not isinstance(core_path.asset_returns, Mapping) or not symbols.issubset(core_path.asset_returns):
        raise ValueError("SECURITY_RETURN_COVERAGE")
    if not symbols.issubset(spec.bps_per_side):
        raise ValueError("BPS_MISSING")
    returns = {}
    for s in symbols:
        series = core_path.asset_returns[s]
        if len(series) != n:
            raise ValueError("SECURITY_RETURN_COVERAGE")
        returns[s] = tuple(_number(r, "SECURITY_RETURN_INVALID") for r in series)
        if any(r <= -1. for r in returns[s]):
            raise ValueError("SECURITY_RETURN_INVALID")
    for i, weights in enumerate(earning):
        gross = _number(core_path.gross_returns[i], "CORE_GROSS_RECONCILIATION")
        if not math.isclose(gross, math.fsum(w * returns[s][i] for s, w in weights.items()),
                            rel_tol=1e-10, abs_tol=1e-12):
            raise ValueError("CORE_GROSS_RECONCILIATION")
    targets = (*earning[1:], terminal)
    return tuple(NettingSession(calendar[i + 1], calendar[i], exposures[i].f_target,
        dict(targets[i]), {s: returns[s][i] for s in sorted(symbols)}, 0.) for i in range(n))


def run_rs04_netting_candidate(
    *, core_path: CorePath, initial_book: InitialNettingBook, last_close_target: CloseTarget,
    trading_sessions: Sequence[date], outer_exposures: Sequence[OuterExposure],
    synthetic_fee_spec: Any, fee_cash_role: str, execution_convention: str,
) -> dict[str, Any]:
    """Run the existing cash-funded netter; no fitting, journal or live defaults.

    Targets are pre-fee NAV fractions. Fees feed back through the existing
    actual net book and require cash at each fill. The separate-only research
    cost attribution companion is deliberately outside this merged candidate.
    """
    rows = build_rs04_netting_sessions(core_path=core_path, initial_book=initial_book,
        last_close_target=last_close_target, trading_sessions=trading_sessions,
        outer_exposures=outer_exposures, synthetic_fee_spec=synthetic_fee_spec,
        execution_convention=execution_convention)
    netting = run_offline_security_netting(rows, initial_nav=initial_book.nav,
        initial_f=initial_book.f, initial_inner_weights=dict(initial_book.inner_weights),
        spec=synthetic_fee_spec, fee_cash_role=fee_cash_role)
    return {
        "status": "DEVELOPMENT_ONLY", "execution_convention": execution_convention,
        "research_frozen": False, "live_ready": False, "no_order": True, "budget_effect": "NONE",
        "d09": "NOT_FROZEN", "fee_role": "SYNTHETIC_SCENARIO_NOT_D10_FREEZE",
        "input_provenance": "CALLER_SUPPLIED_CALENDAR_AND_TERMINAL_DECISION_NOT_ATTESTED",
        "target_source": "NEXT_EARNING_WEIGHT_EXCEPT_EXPLICIT_LAST_CLOSE",
        "initial_book": {"session": initial_book.session.isoformat(), "nav": initial_book.nav,
            "f": initial_book.f, "inner_weights": dict(initial_book.inner_weights),
            "security_values": dict(initial_book.security_values), "cash": initial_book.cash},
        "timeline": [{"decision_session": row.decision_session.isoformat(),
            "fill_session": row.session.isoformat(),
            "subsequent_earning_session": rows[i + 1].session.isoformat() if i + 1 < len(rows) else None,
            "target_source": "explicit_last_close" if i + 1 == len(rows) else "core_next_earning_weight"}
            for i, row in enumerate(rows)],
        "netting": netting,
    }
