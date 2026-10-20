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


def run_rs04_bayesian_candidate(
    *, core_paths: Mapping[str, CorePath], initial_books: Mapping[str, InitialNettingBook],
    last_close_targets: Mapping[str, CloseTarget], trading_sessions: Sequence[date],
    observation_closes: Mapping[date, Any] | None,
    observation_available_at: Mapping[str, Mapping[date, Any]] | None,
    decision_cutoffs: Mapping[date, Any], model_spec: Any, sigma: Sequence[Sequence[float]],
    training_identity: str | None, scope_caps: Mapping[str, float] | None,
    cap_source: str | None, vetoes: Mapping[date, Mapping[str, bool]] | None,
    control_outer_exposures: Mapping[str, Sequence[OuterExposure]],
    synthetic_fee_specs: Mapping[str, Any], fee_cash_role: str, execution_convention: str,
) -> dict[str, Any]:
    """Opt-in two-core posterior consumer; each account keeps its own net book.

    Reference CorePaths must come from the caller's no-plugin generators. Their
    provenance and the supplied timestamp/cap/control-policy identities are not
    authenticated here. Sigma is supplied, never estimated on evaluation rows.
    Only complete, available vectors after the training cutoff enter the model;
    current-fill returns do not enter the preceding close's decision. State and
    moments are constructed internally through the validated kernel factories,
    never accepted as caller-built DTOs. The fixed first consumer uses c=.25
    once; the original netter receives the resulting outer fraction unchanged.

    A prior-only opening is parked only when the declared opening outer fraction
    is already zero. It produces no model target or implicit liquidation. Missing
    evidence produces no candidate/control ledger, not an invented zero budget.
    Scope caps here are outer TARGET fractions of pre-fee NAV, as in the existing
    research netter; this is not certification of a production post-fee policy.
    """
    from dataclasses import replace
    from datetime import datetime, timezone

    from .bayesian_kelly import (
        B4Observation, B4Spec, b4_fractional_target, b4_predictive_moments,
        initialise_b4, state_to_dict, update_b4,
    )

    cores = ("TQQQ", "SOXL")
    header = {"status": "DEVELOPMENT_ONLY", "no_order": True, "live_ready": False,
        "budget_effect": "NONE", "financial": "NOT_READY", "adoption": "UNKNOWN",
        "comparison_scope": "SEPARATE_NET_BOOKS_NOT_A_JOINT_ALLOCATION",
        "input_provenance": "CALLER_SUPPLIED_CORE_TIMES_AND_ORIGINAL_CAP_NOT_ATTESTED",
        "cap_basis": "OUTER_TARGET_PRE_FEE_NAV", "c": .25}
    decisions: list[dict[str, Any]] = []

    def unavailable(code: str) -> dict[str, Any]:
        return {**header, "status": code, "decisions": decisions,
                "strategies": None, "controls": None, "checkpoint": None}

    if scope_caps is None or cap_source is None:
        return unavailable("UNAVAILABLE_SCOPE_CAP")
    if training_identity is None:
        return unavailable("UNAVAILABLE_TRAINING_IDENTITY")
    if observation_closes is None or observation_available_at is None:
        return unavailable("UNAVAILABLE_OBSERVATION_TIMING")
    if vetoes is None:
        return unavailable("UNAVAILABLE_VETO")

    def pair(value: Any, code: str) -> None:
        if not isinstance(value, Mapping) or set(value) != set(cores):
            raise ValueError(code)

    for value in (core_paths, initial_books, last_close_targets, synthetic_fee_specs,
                  control_outer_exposures):
        pair(value, "B4_TWO_CORE_INPUT_REQUIRED")
    pair(scope_caps, "SCOPE_CAP_CORE_MISMATCH")
    caps = {k: _number(scope_caps[k], "SCOPE_CAP_INVALID") for k in cores}
    if any(not 0. <= x <= 1. for x in caps.values()):
        raise ValueError("SCOPE_CAP_INVALID")
    if not isinstance(cap_source, str) or not cap_source.strip():
        raise ValueError("B4_CAP_SOURCE_REQUIRED")
    if type(model_spec) is not B4Spec or model_spec.core_ids != cores:
        raise ValueError("B4_ORDERED_TWO_CORE_SPEC_REQUIRED")
    state = initialise_b4(model_spec, sigma, training_identity)
    for key, path in core_paths.items():
        if type(path) is not CorePath or path.strategy_key != key or path.variant != "no_plugin_core":
            raise ValueError("B4_NO_PLUGIN_CORE_REQUIRED")
    reference = tuple(core_paths[cores[0]].sessions)
    calendar = tuple(trading_sessions)
    if (not reference or any(type(d) is not date for d in reference)
            or any(a >= b for a, b in zip(reference, reference[1:]))):
        raise ValueError("B4_REFERENCE_ALIGNMENT")
    n = len(reference)
    if any(tuple(p.sessions) != reference or len(p.gross_returns) != n
           or len(p.weights) != n or len(p.turnover) != n
           or not isinstance(p.asset_returns, Mapping)
           or any(len(r) != n for r in p.asset_returns.values()) for p in core_paths.values()):
        raise ValueError("B4_REFERENCE_ALIGNMENT")
    if (len(calendar) < 2 or any(type(d) is not date for d in calendar)
            or any(a >= b for a, b in zip(calendar, calendar[1:]))
            or any(d not in reference for d in calendar)):
        raise ValueError("TRADING_SESSION_ALIGNMENT")
    lo, hi = reference.index(calendar[0]), reference.index(calendar[-1]) + 1
    if reference[lo:hi] != calendar:
        raise ValueError("TRADING_SESSION_ALIGNMENT")
    if any(type(p.first_signal) is not date or p.first_signal > calendar[0] for p in core_paths.values()):
        return unavailable("UNAVAILABLE_CORE_HISTORY")
    if any(type(initial_books[k]) is not InitialNettingBook
           or type(last_close_targets[k]) is not CloseTarget for k in cores):
        raise ValueError("B4_OPENING_AND_TERMINAL_BOOK_REQUIRED")

    def timestamp(value: Any) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("B4_AWARE_TIMESTAMP_REQUIRED")
        return value.astimezone(timezone.utc)

    pair(observation_available_at, "B4_TWO_CORE_AVAILABILITY_REQUIRED")
    if (not isinstance(observation_closes, Mapping) or set(observation_closes) != set(reference)
            or any(not isinstance(observation_available_at[k], Mapping)
                   or set(observation_available_at[k]) != set(reference) for k in cores)):
        raise ValueError("B4_TIME_COVERAGE")
    closes = {d: timestamp(observation_closes[d]) for d in reference}
    available = {k: {d: timestamp(observation_available_at[k][d]) for d in reference} for k in cores}
    if (any(closes[d].date() != d for d in reference)
            or any(closes[a] >= closes[b] for a, b in zip(reference, reference[1:]))
            or any(available[k][d] < closes[d] for k in cores for d in reference)):
        raise ValueError("B4_OBSERVATION_TIMELINE")
    if (not isinstance(decision_cutoffs, Mapping) or set(decision_cutoffs) != set(calendar[:-1])
            or not isinstance(vetoes, Mapping) or set(vetoes) != set(calendar[:-1])):
        raise ValueError("B4_DECISION_COVERAGE")
    cutoffs = {d: timestamp(decision_cutoffs[d]) for d in calendar[:-1]}
    for d, fill in zip(calendar, calendar[1:]):
        if not closes[d] <= cutoffs[d] < closes[fill]:
            raise ValueError("B4_DECISION_TIMELINE")
        pair(vetoes[d], "B4_TWO_CORE_VETO_REQUIRED")
        if any(type(vetoes[d][k]) is not bool for k in cores):
            raise ValueError("B4_BOOLEAN_VETO_REQUIRED")
    if state.spec.training_cutoff > cutoffs[calendar[0]]:
        raise ValueError("B4_TRAINING_AFTER_INITIAL_DECISION")

    # Reconcile reference gross observations with their original unscaled core
    # earning weights. Never learn from the candidate's own fee-depleted return.
    for p in core_paths.values():
        for i, weights in enumerate(p.weights):
            w = _weights(weights)
            if not set(w).issubset(p.asset_returns):
                raise ValueError("SECURITY_RETURN_COVERAGE")
            gross = _number(p.gross_returns[i], "CORE_GROSS_RECONCILIATION")
            returns = {s: _number(r[i], "SECURITY_RETURN_INVALID") for s, r in p.asset_returns.items()}
            if gross <= -1. or any(r <= -1. for r in returns.values()):
                raise ValueError("SECURITY_RETURN_INVALID")
            if not math.isclose(gross, math.fsum(v * returns[s] for s, v in w.items()),
                                rel_tol=1e-10, abs_tol=1e-12):
                raise ValueError("CORE_GROSS_RECONCILIATION")
    observation_indices = [i for i, d in enumerate(reference) if closes[d] > state.spec.training_cutoff]
    cursor = 0
    exposures: dict[str, list[OuterExposure]] = {k: [] for k in cores}
    for d, fill in zip(calendar, calendar[1:]):
        while cursor < len(observation_indices) and reference[observation_indices[cursor]] <= d:
            i = observation_indices[cursor]
            observed_day = reference[i]
            if any(p.first_signal > observed_day for p in core_paths.values()):
                return unavailable("UNAVAILABLE_CORE_HISTORY")
            admission = max(available[k][observed_day] for k in cores)
            if admission > cutoffs[d]:
                return unavailable("UNAVAILABLE_OBSERVATION_AT_CUTOFF")
            obs = B4Observation("rs04-core:" + observed_day.isoformat(), 1,
                closes[observed_day], admission, cores,
                tuple(math.log1p(core_paths[k].gross_returns[i]) for k in cores))
            state = update_b4(state, obs, decision_cutoff=cutoffs[d]).state
            cursor += 1
        moments = b4_predictive_moments(state)
        target = b4_fractional_target(moments, c=.25, scope_cap=caps, cap_source=cap_source)
        if target.targets is None:
            if target.status != "PRIOR_ONLY":
                return unavailable("UNAVAILABLE_" + target.status)
            if any(initial_books[k].f != 0. for k in cores):
                return unavailable("UNAVAILABLE_PRIOR_ONLY_NONZERO_OPENING")
            targets = None
            status = "PRIOR_ONLY_PARKED"
        else:
            targets = {k: 0. if vetoes[d][k] else target.targets[i] for i, k in enumerate(cores)}
            status = "AVAILABLE"
        decisions.append({"decision_session": d.isoformat(), "fill_session": fill.isoformat(),
            "decision_cutoff": cutoffs[d].isoformat(), "n": state.n, "status": status,
            "targets": targets, "c": .25, "fractional_applied_once": target.fractional_applied_once,
            "cap_source": cap_source, "caps": dict(caps), "vetoes": dict(vetoes[d]),
            "source_prefix_sha256": moments.source_prefix_sha256})
        for k in cores:
            exposures[k].append(OuterExposure(d, fill, targets[k] if targets is not None else 0.))

    strategies, controls = {}, {}
    for k, path in core_paths.items():
        start = lo + 1
        window = replace(path, sessions=path.sessions[start:hi], gross_returns=path.gross_returns[start:hi],
            weights=path.weights[start:hi], turnover=path.turnover[start:hi],
            close_fill_turnover=path.close_fill_turnover[start:hi],
            asset_returns={s: r[start:hi] for s, r in path.asset_returns.items()})
        common = dict(core_path=window, initial_book=initial_books[k], last_close_target=last_close_targets[k],
            trading_sessions=calendar, synthetic_fee_spec=synthetic_fee_specs[k], fee_cash_role=fee_cash_role,
            execution_convention=execution_convention)
        strategies[k] = run_rs04_netting_candidate(**common, outer_exposures=tuple(exposures[k]))
        controls[k] = run_rs04_netting_candidate(**common, outer_exposures=control_outer_exposures[k])
    return {**header, "decisions": decisions, "strategies": strategies, "controls": controls,
            "checkpoint": state_to_dict(state)}


def run_rs04_partial_rebalance_candidate(
    *, core_path: CorePath, initial_book: InitialNettingBook, last_close_target: CloseTarget,
    trading_sessions: Sequence[date], outer_exposures: Sequence[OuterExposure],
    synthetic_fee_spec: Any, fee_cash_role: str, execution_convention: str,
    research_policy: Any, report_periods_per_year: int,
) -> dict[str, Any]:
    """Explicit B5-only consumer of original core/outer targets and actual book.

    The unchanged #639 builder validates the layered inputs; original QPK compose
    flattens all securities, including the tradable/fee-bearing outer BOXX.
    Ownership diagnostics differ; physical trades, costs and NAV have the same
    meaning. The caller supplies frozen target provenance/times and the original
    complete policy. No B4/CVaR cross, budget defaults, fitting or orders.
    """
    from dataclasses import replace
    from .security_netting_runner import RS04ResearchPolicy
    from quant_platform_kit.research_stats.security_netting import compose_security_targets

    if type(research_policy) is not RS04ResearchPolicy:
        raise ValueError("RS04_FIXED_RESEARCH_POLICY_REQUIRED")
    rows = build_rs04_netting_sessions(core_path=core_path, initial_book=initial_book,
        last_close_target=last_close_target, trading_sessions=trading_sessions,
        outer_exposures=outer_exposures, synthetic_fee_spec=synthetic_fee_spec,
        execution_convention=execution_convention)
    universe = research_policy.security_universe
    if not isinstance(universe, tuple) or set(universe) != set(synthetic_fee_spec.bps_per_side):
        raise ValueError("RS04_COMPLETE_SECURITY_UNIVERSE_REQUIRED")
    flat_rows = []
    for row in rows:
        securities, _ = compose_security_targets(f=row.f_target, inner_weights=row.inner_target,
                                                  spec=synthetic_fee_spec)
        if not set(securities) <= set(universe) or set(row.security_returns) != set(universe):
            raise ValueError("RS04_COMPLETE_SECURITY_UNIVERSE_REQUIRED")
        flat_rows.append(replace(row, f_target=1., inner_target={s: securities.get(s, 0.) for s in universe}))
    actual = initial_book.security_values
    if not set(actual) <= set(universe):
        raise ValueError("RS04_COMPLETE_SECURITY_UNIVERSE_REQUIRED")
    opening = research_policy.opening
    if (opening.completed_session != initial_book.session or opening.nav != initial_book.nav
            or opening.cash != initial_book.cash
            or dict(opening.security_values) != {s: actual.get(s, 0.) for s in universe}):
        raise ValueError("RS04_OPENING_ACTUAL_BOOK_MISMATCH")
    flat_spec = replace(synthetic_fee_spec, outer_cash_mode='separate', outer_cash_symbol=None,
                        bps_per_side=dict(synthetic_fee_spec.bps_per_side))
    netting = run_offline_security_netting(tuple(flat_rows), initial_nav=initial_book.nav,
        initial_f=1., initial_inner_weights={s: actual.get(s, 0.) / initial_book.nav for s in universe},
        spec=flat_spec, fee_cash_role=fee_cash_role, execution_timing='next_close',
        report_periods_per_year=report_periods_per_year, rs04_research_policy=research_policy)
    return {'status': 'DEVELOPMENT_ONLY', 'execution_convention': execution_convention,
        'financial': 'NOT_READY', 'adoption': 'UNKNOWN', 'fullchain': 'NOT_CLOSED',
        'research_frozen': False, 'live_ready': False, 'no_order': True, 'budget_effect': 'NONE',
        'candidate': 'RS04_B5_PARTIAL_REBALANCE', 'netting': netting}


def run_rs04_cvar_candidate(
    *, core_path: CorePath, initial_book: InitialNettingBook, last_close_target: CloseTarget,
    trading_sessions: Sequence[date], outer_exposures: Sequence[OuterExposure],
    synthetic_fee_spec: Any, fee_cash_role: str, execution_convention: str,
    research_policy: Any, report_periods_per_year: int,
) -> dict[str, Any]:
    """Explicit synthetic two-step tail consumer through the original B5 path.

    Each generated strategy runs its own physical book. Scenario returns are
    fixed ex ante inputs, distinct from the core's actual future asset returns.
    No real budget, policy attestation or cross-account allocation is inferred.
    """
    from .security_netting_runner import RS04ResearchPolicy
    from .cvar_exposure_constraint import TailDescriptor

    if (type(research_policy) is not RS04ResearchPolicy
            or type(research_policy.cvar_descriptor) is not TailDescriptor
            or research_policy.cvar_descriptor.scope != 'RS04:' + core_path.strategy_key
            or not any(info.cvar_request is not None for info in research_policy.decisions)):
        raise ValueError('RS04_CVAR_EXPLICIT_SCOPED_REQUEST_REQUIRED')
    result = run_rs04_partial_rebalance_candidate(core_path=core_path, initial_book=initial_book,
        last_close_target=last_close_target, trading_sessions=trading_sessions,
        outer_exposures=outer_exposures, synthetic_fee_spec=synthetic_fee_spec,
        fee_cash_role=fee_cash_role, execution_convention=execution_convention,
        research_policy=research_policy, report_periods_per_year=report_periods_per_year)
    result['candidate'] = 'RS04_CVAR_TWO_SESSION_CONSTRAINT'
    return result
