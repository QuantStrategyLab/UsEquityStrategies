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


R0_R3_ROWS = ('R0_ORIGINAL_FULL', 'R1_B4_ONLY', 'R2_B5_HALF_ONLY', 'R3_B5_BAND_ONLY')
_R0_R3_CORES = ('TQQQ', 'SOXL')
_R0_R3_PURPOSE = 'RETROSPECTIVE_DESCRIPTIVE_DEVELOPMENT_ABLATION'
_R0_R3_COST = 'ASSUMED_FLAT_BPS5_SINGLE_CASE_NOT_BROKER_FEE_FACT'


def _r0_r3_json(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _r0_r3_sha(value: Any) -> str:
    import hashlib

    return hashlib.sha256(_r0_r3_json(value).encode()).hexdigest()


def _r0_r3_declared_source_times(projection):
    """Canonical caller declarations for identity, never authenticated availability.

    This metadata API has no decision cutoff or producer attestation. Missing
    times stay missing; NOT_ATTESTED is unchanged even when all times are given.
    """
    from .security_netting_runner import _rs04_time

    times = {name: _rs04_time(projection[name]).isoformat()
             for name in ('observed_at', 'declared_available_at', 'first_seen')
             if name in projection and projection[name] is not None}
    if times:
        times['source_time_basis'] = 'CALLER_DECLARED_NOT_AUTHENTICATED'
    return times


def admit_r0_r3_source_metadata(projection: Mapping[str, Any], *,
                                identity_packet_sha256: str, dataset_class: str) -> dict[str, Any]:
    """Validate metadata only; no path, manifest or market-page I/O.

    Admission is descriptive research identity, never PIT, live policy or
    execution authorization. Callers enforce their review and data-access boundary.
    Fixture counts describe fixture bodies, never the real 13-object packet.
    """
    def require(condition: bool, code: str) -> None:
        if not condition:
            raise ValueError('UNAVAILABLE_SOURCE_' + code)

    require(dataset_class in ('ARTIFICIAL_FIXTURE', 'REAL_ARCHIVED_RAW_ACTIONS'), 'CLASS')
    require(isinstance(identity_packet_sha256, str) and len(identity_packet_sha256) == 64
            and all(c in '0123456789abcdef' for c in identity_packet_sha256), 'IDENTITY')
    require(isinstance(projection, Mapping), 'METADATA')
    require(projection.get('source_packet_sha256') == identity_packet_sha256, 'PACKET_BINDING')
    require(projection.get('source_verified_objects') == 13
            and type(projection.get('source_verified_bytes')) is int
            and projection['source_verified_bytes'] > 0, 'OBJECT_INVENTORY')
    require(projection.get('actual_raw_bodies_not_included') is True, 'METADATA_ONLY')
    metadata = projection.get('manifest_metadata')
    expected = {'schema_version': 'qsl.research.raw_sip_input.v1',
        'source': 'alpaca.stocks.bars.v2_and_corporate_actions.v1', 'feed': 'sip',
        'price_adjustment': 'raw', 'calendar': 'XNYS', 'timezone': 'America/New_York',
        'currency': 'USD', 'license': 'user_attested_non_commercial_private_research',
        'historical_pit_attested': False, 'historical_context': 'NOT_BOUND',
        'live_config_snapshot': 'LIVE_CONFIG_URI_UNAVAILABLE', 'research_only': True, 'no_order': True}
    require(isinstance(metadata, Mapping) and set(metadata) == set(expected) | {'window_start', 'window_end'},
            'SCHEMA')
    require(all(type(metadata[k]) is type(v) and metadata[k] == v for k, v in expected.items()),
            'RAW_ACTION_CALENDAR_CONTRACT')
    start, end = date.fromisoformat(metadata['window_start']), date.fromisoformat(metadata['window_end'])
    require(start < end, 'WINDOW')
    groups = projection.get('groups')
    require(isinstance(groups, (list, tuple)) and len(groups) == 12, 'GROUPS')
    counts = {}
    symbols = {'TQQQ', 'QQQ', 'QQQM', 'SOXL', 'SOXX', 'BOXX'}
    for group in groups:
        require(isinstance(group, Mapping) and set(group) == {
            'symbol', 'kind', 'count', 'complete_pagination', 'pages'}, 'GROUP_SCHEMA')
        key = (group['symbol'], group['kind'])
        require(key[0] in symbols and key[1] in ('bars', 'actions') and key not in counts, 'GROUP_ID')
        require(group['complete_pagination'] is True and type(group['pages']) is int
                and group['pages'] > 0 and type(group['count']) is int
                and group['count'] >= (1 if key[1] == 'bars' else 0), 'PAGINATION_COUNTS')
        counts[key] = group['count']
    if dataset_class == 'REAL_ARCHIVED_RAW_ACTIONS':
        require(identity_packet_sha256 == '150f6d6b5318f7dbbcf171b9c76fde567a349ee1eed6b49c184d1f33c9e35124'
                and projection['source_verified_bytes'] == 780174
                and (start, end) == (date(2021, 12, 1), date(2026, 8, 25)), 'FROZEN_PACKET')
        require(all(counts[s, 'bars'] == (917 if s == 'BOXX' else 1187) for s in symbols)
                and all(counts[s, 'actions'] == n for s, n in
                        {'SOXL': 16, 'SOXX': 21, 'BOXX': 1, 'QQQ': 21, 'TQQQ': 18, 'QQQM': 19}.items()),
                'FROZEN_COUNTS')
    # Deliberate whitelist: projection/packet paths never enter model or journal.
    return {'dataset_class': dataset_class, 'identity_packet_sha256': identity_packet_sha256,
        'source_verified_objects': 13, 'source_verified_bytes': projection['source_verified_bytes'],
        'manifest_metadata': dict(metadata),
        'groups': sorted((dict(g) for g in groups), key=lambda g: (g['symbol'], g['kind'])),
        'PIT': 'NOT_ATTESTED', 'OOS': 'SEEN_DEVELOPMENT_NOT_INDEPENDENT',
        **_r0_r3_declared_source_times(projection)}


def _root_a_core_contexts(binding, implementation, identity, dataset_class):
    from pathlib import Path
    import hashlib
    from .partial_rebalance import (CORE_ONLY_MAPPING_SHA256, CORE_ONLY_CONFIG_SHA256,
        CORE_ONLY_SOURCE_SHA256, NoPluginCorePolicyContext)

    expected = {'mapping_sha256': CORE_ONLY_MAPPING_SHA256,
                'source_sha256': CORE_ONLY_SOURCE_SHA256, 'config_sha256': CORE_ONLY_CONFIG_SHA256}
    if binding != expected:
        raise ValueError('UNAVAILABLE_CORE_ONLY_SOURCE_CONFIG_BINDING')
    package = Path(__file__).resolve().parents[1]
    for rel, digest in CORE_ONLY_SOURCE_SHA256.items():
        if hashlib.sha256((package/rel).read_bytes()).hexdigest() != digest:
            raise ValueError('UNAVAILABLE_CORE_ONLY_ENABLED_OR_CHANGED_SOURCE')
    for name in ('partial_rebalance', 'rs04_netting_candidate', 'security_netting_runner'):
        rel = 'research/' + name + '.py'
        if implementation.get(rel) != hashlib.sha256((package/rel).read_bytes()).hexdigest():
            raise ValueError('UNAVAILABLE_CORE_ONLY_ADAPTER_IDENTITY')
    return {k: NoPluginCorePolicyContext(k, identity, dataset_class, _r0_r3_sha(implementation),
        CORE_ONLY_MAPPING_SHA256, CORE_ONLY_CONFIG_SHA256,
        tuple(sorted(CORE_ONLY_SOURCE_SHA256.items()))) for k in _R0_R3_CORES}


def r0_r3_parser_identity():
    """Source identity of this canonical adapter and the existing archive parser."""
    import hashlib
    from pathlib import Path
    here = Path(__file__).resolve()
    return _r0_r3_sha({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in (here, here.with_name('kelly_private_runner.py'))})


def _r0_r3_source_registration(value, identity, dataset_class):
    symbols = ('TQQQ', 'QQQ', 'QQQM', 'SOXL', 'SOXX', 'BOXX')
    keys = {'manifest.json'} | {f'{kind}/{symbol}/page-001.json'
        for kind in ('bars', 'actions') for symbol in symbols}
    if (not isinstance(value, Mapping) or set(value) != {
            'source_packet_sha256', 'manifest_sha256', 'object_sha256', 'parser_sha256'}
            or value['source_packet_sha256'] != identity
            or value['parser_sha256'] != r0_r3_parser_identity()
            or not isinstance(value['object_sha256'], Mapping)
            or set(value['object_sha256']) != keys
            or value['manifest_sha256'] != value['object_sha256']['manifest.json']
            or any(not isinstance(v, str) or len(v) != 64
                   or any(c not in '0123456789abcdef' for c in v)
                   for v in (*value['object_sha256'].values(), value['parser_sha256']))):
        raise ValueError('UNAVAILABLE_SOURCE_OR_PARSER_REGISTRATION')
    if dataset_class == 'REAL_ARCHIVED_RAW_ACTIONS' and (
            identity != '150f6d6b5318f7dbbcf171b9c76fde567a349ee1eed6b49c184d1f33c9e35124'
            or value['manifest_sha256'] != 'd5d40dfc54f55fe59c0eba57302ef9b018d006a0835f33f969053e52d05cc9e7'):
        raise ValueError('UNAVAILABLE_FROZEN_D5_REGISTRATION')


def parse_registered_r0_r3_archive(raw_root, registration):
    """Call only after STARTED. Reuse the existing hash-checking raw parser.

    Registration binds source objects and parser source before body access. This
    function returns a canonical body plus the verified source receipt, never a
    pretend preknown body hash or historical available_at.
    """
    import hashlib
    import json
    from pathlib import Path
    from .kelly_private_runner import load_archive, _sessions

    root = Path(raw_root)
    if root.is_symlink() or (root/'manifest.json').is_symlink():
        raise ValueError('UNAVAILABLE_ARCHIVE_OR_PARSER_IDENTITY')
    raw = (root/'manifest.json').read_bytes()
    if (hashlib.sha256(raw).hexdigest() != registration['manifest_sha256']
            or registration['parser_sha256'] != r0_r3_parser_identity()):
        raise ValueError('UNAVAILABLE_ARCHIVE_OR_PARSER_IDENTITY')
    manifest = json.loads(raw)
    declared = {'manifest.json': hashlib.sha256(raw).hexdigest()}
    for item in manifest.get('inputs', []):
        kind, symbol = item.get('kind'), item.get('symbol')
        if (kind not in ('bars', 'actions') or symbol not in ('TQQQ','QQQ','QQQM','SOXL','SOXX','BOXX')
                or item.get('complete_pagination') is not True or len(item.get('pages', [])) != 1):
            raise ValueError('UNAVAILABLE_ARCHIVE_OBJECT_COVERAGE')
        key = f'{kind}/{symbol}/page-001.json'
        if key in declared:
            raise ValueError('UNAVAILABLE_ARCHIVE_OBJECT_COVERAGE')
        declared[key] = item['pages'][0]['sha256']
    if declared != registration['object_sha256']:
        raise ValueError('UNAVAILABLE_ARCHIVE_OBJECT_BINDING')
    symbols = ('TQQQ','QQQ','QQQM','SOXL','SOXX','BOXX')
    bars, actions = load_archive(root, symbols, registration['manifest_sha256'])
    end = max(b.session for b in bars['QQQ'])
    sessions = _sessions(bars, ('TQQQ','QQQ','QQQM','SOXL','SOXX'), end)
    body = {'sessions': [d.isoformat() for d in sessions],
        'bars': {k: [[b.session.isoformat(), b.open, b.close] for b in v] for k,v in bars.items()},
        'actions': {k: {'splits': [[d.isoformat(), v] for d,v in a.splits],
                        'dividends': [[d.isoformat(), v] for d,v in a.dividends]} for k,a in actions.items()}}
    return {'body': body, 'source_registration': dict(registration)}


def _r0_r3_generate(body: Mapping[str, Any], source: Mapping[str, Any], *,
                     trade_start: date) -> tuple[dict[str, CorePath], tuple[date, ...]]:
    """Use the original generators unchanged, with their original warmup rules."""
    from .kelly_core_return_generator import (
        CorporateActions, RawBar, generate_soxl_core_path, generate_tqqq_core_path,
    )

    if not isinstance(body, dict) or set(body) != {'sessions', 'bars', 'actions'}:
        raise ValueError('UNAVAILABLE_INPUT_SCHEMA')
    sessions = tuple(date.fromisoformat(d) for d in body['sessions'])
    if (len(sessions) < 2 or any(a >= b for a, b in zip(sessions, sessions[1:]))
            or sessions[0].isoformat() != source['manifest_metadata']['window_start']
            or sessions[-1].isoformat() != source['manifest_metadata']['window_end']):
        raise ValueError('UNAVAILABLE_INPUT_SESSION_COVERAGE')
    symbols = {g['symbol'] for g in source['groups']}
    if set(body['bars']) != symbols or set(body['actions']) != symbols:
        raise ValueError('UNAVAILABLE_INPUT_SYMBOL_COVERAGE')
    counts = {(g['symbol'], g['kind']): g['count'] for g in source['groups']}
    bars, actions = {}, {}
    for s in sorted(symbols):
        raw = body['bars'][s]
        action = body['actions'][s]
        if (not isinstance(raw, list) or len(raw) != counts[s, 'bars']
                or not isinstance(action, dict) or set(action) != {'splits', 'dividends'}
                or len(action['splits']) + len(action['dividends']) != counts[s, 'actions']):
            raise ValueError('UNAVAILABLE_INPUT_COUNTS_OR_ACTION_SCHEMA')
        bars[s] = tuple(RawBar(date.fromisoformat(d), _number(o, 'INVALID_RAW_PRICE'),
                               _number(c, 'INVALID_RAW_PRICE')) for d, o, c in raw)
        if (any(b.open <= 0 or b.close <= 0 or b.session not in sessions for b in bars[s])
                or any(a.session >= b.session for a, b in zip(bars[s], bars[s][1:]))):
            raise ValueError('UNAVAILABLE_RAW_BAR_ORDER_OR_PRICE')
        pairs = {}
        for kind in ('splits', 'dividends'):
            pairs[kind] = tuple((date.fromisoformat(d), _number(v, 'INVALID_RAW_ACTION'))
                                for d, v in action[kind])
            if (any(d not in sessions or v < 0 or (kind == 'splits' and v == 0)
                    for d, v in pairs[kind])
                    # The original core sums same-day dividends; splits remain unique.
                    or any(a[0] > b[0] or (kind == 'splits' and a[0] == b[0])
                           for a, b in zip(pairs[kind], pairs[kind][1:]))):
                raise ValueError('UNAVAILABLE_RAW_ACTION_ORDER_OR_VALUE')
        actions[s] = CorporateActions(**pairs)
    # Retain every prewindow price for the original indicator warmup. The
    # existing trade_start API starts replay near the frozen requested window,
    # rather than creating pre-inception BOXX holdings in unused source history.
    return {'TQQQ': generate_tqqq_core_path(bars, actions, sessions, trade_start=trade_start),
            'SOXL': generate_soxl_core_path(bars, actions, sessions, trade_start=trade_start)}, sessions


def _r0_r3_prepare_signal_inputs(body, params):
    """Original full-history generation and common active-prefix split."""
    start, end = map(date.fromisoformat, params['requested_window'])
    paths, sessions = _r0_r3_generate(body, params['source'], trade_start=start)
    if start not in sessions or end not in sessions:
        raise ValueError('UNAVAILABLE_REQUESTED_WINDOW')
    indices = [i for i, d in enumerate(sessions) if start <= d <= end]
    eligible = []
    for i in indices:
        if all(type(p.first_signal) is date and p.first_signal <= sessions[i]
               and bool(p.weights[i]) for p in paths.values()):
            for p in paths.values():
                value = _number(p.gross_returns[i], 'UNAVAILABLE_COMPLETE_CORE_GROSS')
                if value <= -1 or not math.isclose(value, math.fsum(
                        w * p.asset_returns[s][i] for s, w in p.weights[i].items()),
                        rel_tol=1e-10, abs_tol=1e-12):
                    raise ValueError('UNAVAILABLE_COMPLETE_CORE_GROSS')
            eligible.append(i)
    if len(eligible) < 64:
        raise ValueError('UNAVAILABLE_TRAINING_PREFIX_OR_EVALUATION')
    train = eligible[:63]
    # Missing/inactive sessions after training cannot be silently dropped.
    evaluate = list(range(train[-1] + 1, indices[-1] + 1))
    if not evaluate or any(i not in eligible for i in evaluate):
        raise ValueError('UNAVAILABLE_COMPLETE_ACTIVE_EVALUATION')
    return {'paths': paths, 'sessions': sessions, 'indices': indices,
        'eligible': eligible, 'train': train, 'evaluate': evaluate,
        'body_sha256': _r0_r3_sha(body), 'params_sha256': _r0_r3_sha(params)}


def _r0_r3_compute(body: Mapping[str, Any], params: Mapping[str, Any],
                    policies: Mapping[str, Any], terminal_targets: Mapping[str, CloseTarget] | None, *,
                    _prepared=None, _profiles=None) -> dict[str, Any]:
    from datetime import datetime, timezone
    from .bayesian_kelly import (B4Observation, B4Spec, b4_fractional_target,
        b4_predictive_moments, initialise_b4, state_to_dict, update_b4)
    from .portfolio_kelly_walk_forward import ledoit_wolf
    from .security_netting_runner import (DecisionSnapshot, RS04DecisionInput,
        RS04RetrospectiveResearchPolicy)
    from quant_platform_kit.research_stats.security_netting import SecurityNettingSpec, compose_security_targets

    prepared = _r0_r3_prepare_signal_inputs(body, params) if _prepared is None else _prepared
    if (prepared['body_sha256'] != _r0_r3_sha(body)
            or prepared['params_sha256'] != _r0_r3_sha(params)):
        raise ValueError('UNAVAILABLE_PREPARED_SIGNAL_BINDING')
    paths, sessions = prepared['paths'], prepared['sessions']
    indices, train, evaluate = prepared['indices'], prepared['train'], prepared['evaluate']
    if _profiles is not None and (not params.get('core_only_source_binding')
            or not _profiles or set(_profiles) - set(_R0_R3_CORES)):
        raise ValueError('UNAVAILABLE_SENSITIVITY_PROFILE_SELECTION')
    def close(d: date) -> str:
        # An explicitly assumed model clock; never source historical available_at.
        return d.isoformat() + 'T23:00:00+00:00'

    core_only = params.get('core_only_source_binding') is not None
    if core_only:
        sigma = shrinkage = None
        training_id = _r0_r3_sha({'reserved_alignment_prefix': [sessions[i].isoformat() for i in train]})
        calendar = (sessions[train[-1]], *(sessions[i] for i in evaluate))
        model_decisions = []
        b4_targets = []
        state = None
    else:
        training_columns = tuple(tuple(math.log1p(paths[k].gross_returns[i]) for i in train)
                                 for k in _R0_R3_CORES)
        sigma, shrinkage = ledoit_wolf(training_columns)  # exactly once per batch/fold
        training_id = _r0_r3_sha({'sessions': [sessions[i].isoformat() for i in train],
                                'loggross_columns': training_columns})

        cutoff = datetime.fromisoformat(close(sessions[train[-1]]))
        state = initialise_b4(B4Spec(_R0_R3_CORES, cutoff), sigma, training_id)
        calendar = (sessions[train[-1]], *(sessions[i] for i in evaluate))
        b4_targets, model_decisions = [], []
        for j, d in enumerate(calendar[:-1]):
            if j:
                i = evaluate[j - 1]
                stamp = datetime.fromisoformat(close(d)).astimezone(timezone.utc)
                obs = B4Observation('r0-r3:' + d.isoformat(), 1, stamp, stamp, _R0_R3_CORES,
                    tuple(math.log1p(paths[k].gross_returns[i]) for k in _R0_R3_CORES))
                state = update_b4(state, obs, decision_cutoff=stamp).state
            moments = b4_predictive_moments(state)
            target = b4_fractional_target(moments, c=.25,
                scope_cap=params['original_scope_caps'], cap_source=params['original_scope_cap_source'])
            if target.targets is None and target.status != 'PRIOR_ONLY':
                raise ValueError('UNAVAILABLE_B4_' + target.status)
            b4_targets.append(target.targets)
            model_decisions.append({'session': d.isoformat(), 'n': state.n, 'status': target.status,
                'targets': target.targets, 'source_prefix_sha256': moments.source_prefix_sha256})
    cells, openings = {}, {}
    for k in _R0_R3_CORES:
        if _profiles is not None and k not in _profiles:
            continue
        path = paths[k]
        universe = tuple(sorted(path.asset_returns))
        if set(universe) != (set(('TQQQ','QQQM','BOXX') if k == 'TQQQ' else ('SOXL','SOXX','BOXX'))
                if core_only else {s.symbol for s in policies[k].securities}):
            raise ValueError('UNAVAILABLE_POLICY_UNIVERSE')
        last = (CloseTarget(calendar[-2], calendar[-1], {s: 0. for s in universe})
                if core_only and terminal_targets is None else terminal_targets[k])
        if (type(last) is not CloseTarget or last.decision_session != calendar[-2]
                or last.fill_session != calendar[-1] or set(last.weights) != set(universe)
                or any(_number(v, 'UNAVAILABLE_LAST_TARGET') != 0 for v in last.weights.values())):
            raise ValueError('UNAVAILABLE_EXPLICIT_ZERO_EXPOSURE_LAST_TARGET')
        inner_identity = dict(path.weights[evaluate[0]])
        book = InitialNettingBook(calendar[0], 1., 0., inner_identity,
                                  {s: 0. for s in universe}, 1.)
        openings[k] = {'nav': book.nav, 'cash': book.cash, 'f': book.f,
            'security_values': dict(book.security_values), 'inner_weights': dict(book.inner_weights),
            'inner_identity_sha256': _r0_r3_sha(inner_identity),
            'last_close_target': {'decision_session': last.decision_session.isoformat(),
                'fill_session': last.fill_session.isoformat(), 'weights': dict(last.weights)}}
        for row_id in params.get('active_rows', R0_R3_ROWS):
            mode = {'R2_B5_HALF_ONLY': 'PROPORTIONAL', 'R3_B5_BAND_ONLY': 'BAND_HOLD'}.get(row_id, 'FULL')
            rows, decisions = [], []
            if not core_only:
                rules = params['original_policy_configs'][k]['decision_rules']
                if set(rules) != {sessions[i].isoformat() for i in indices[:-1]}:
                    raise ValueError('UNAVAILABLE_ORIGINAL_MANDATORY_VETO_RULE_COVERAGE')
            for j, i in enumerate(evaluate):
                # Storage reversal only: weights[i+1] was decided at i-1 and fills i.
                inner = dict(path.weights[i + 1]) if j + 1 < len(evaluate) else dict(last.weights)
                f = params['original_outer_f'][k]
                if row_id == 'R1_B4_ONLY':
                    f = 0. if b4_targets[j] is None else b4_targets[j][_R0_R3_CORES.index(k)]
                merged = SecurityNettingSpec(bps_per_side={s: 5. for s in universe},
                    outer_cash_mode='merge_into_security', outer_cash_symbol='BOXX', cash_like_symbols=('BOXX',))
                target, _ = compose_security_targets(f=f, inner_weights=inner, spec=merged)
                # Prior-only means no model target and unchanged cash-only book,
                # rather than an invented, fully invested BOXX parking trade.
                parked = row_id == 'R1_B4_ONLY' and b4_targets[j] is None
                target = {s: 0. if parked else target.get(s, 0.) for s in universe}
                d, fill = calendar[j:j + 2]
                if core_only:
                    info = RS04DecisionInput(d, fill, close(d), close(fill), close(d),
                        policies[k].exact_source, tuple(target.items()), policies[k], False)
                else:
                    from dataclasses import replace
                    rule = rules[d.isoformat()]
                    if (not isinstance(rule, dict) or set(rule) != {
                            'mandatory_reduce', 'forced_target', 'forced_source', 'forced_reason', 'vetoed_symbols'}
                            or type(rule['mandatory_reduce']) is not bool
                            or not isinstance(rule['vetoed_symbols'], list)):
                        raise ValueError('UNAVAILABLE_ORIGINAL_MANDATORY_VETO_RULE')
                    context = replace(policies[k], vetoed_symbols=tuple(rule['vetoed_symbols']))
                    info = RS04DecisionInput(d, fill, close(d), close(fill), close(d),
                        policies[k].exact_source, tuple(target.items()), context, rule['mandatory_reduce'],
                        None if rule['forced_target'] is None else tuple(sorted(rule['forced_target'].items())),
                        rule['forced_source'], rule['forced_reason'])
                rows.append(NettingSession(fill, d, 1., target,
                    {s: path.asset_returns[s][i] for s in universe}, 0.))
                decisions.append(info)
            opening = DecisionSnapshot(calendar[0], close(calendar[0]), 1.,
                                        tuple((s, 0.) for s in universe), 1., True)
            from .security_netting_runner import RS04NoPluginCoreResearchPolicy
            policy_type = RS04NoPluginCoreResearchPolicy if core_only else RS04RetrospectiveResearchPolicy
            policy = policy_type(mode, universe, opening, tuple(decisions),
                profile=k, source_identity=policies[k].source_identity)
            flat_spec = SecurityNettingSpec(bps_per_side={s: 5. for s in universe},
                                            cash_like_symbols=('BOXX',))
            cells[row_id + ':' + k] = run_offline_security_netting(tuple(rows), initial_nav=1., initial_f=1.,
                initial_inner_weights={s: 0. for s in universe}, spec=flat_spec,
                fee_cash_role=flat_spec.inner_residual_role, execution_timing='next_close',
                report_periods_per_year=252, rs04_research_policy=policy)
    return {'cells': cells, 'opening_books': openings, 'training': {
        'sessions': [sessions[i].isoformat() for i in train], 'count': 63,
        'sigma': sigma, 'shrinkage': shrinkage, 'identity': training_id,
        'covariance_fits': 0 if core_only else 1, 'posterior_training_observations': 0},
        'evaluation_sessions': [sessions[i].isoformat() for i in evaluate],
        'model_decisions': model_decisions, 'checkpoint': None if core_only else state_to_dict(state),
        'requested_sessions': [sessions[i].isoformat() for i in indices]}


def _r0_r3_persist_cells(started: Any, params: dict[str, Any], output: dict[str, Any], store: Any):
    """Eight physical books; one additive audit receipt for the existing journal.

    The additive receipt does not mix targets, trade accounts, or allocate capital.
    It is labeled as accounting only. Eight cells remain dependent subresults of
    one trial. Their individual ledgers use cell profiles and have no trial rows.
    """
    from quant_platform_kit.strategy_lifecycle.contracts import (
        BacktestResult, ResearchDailyLedger, ResearchLedgerDay, ResearchPositionMark,
    )

    run_id = output.get('derived_run_id', started.trial_id)
    dates = tuple(date.fromisoformat(d) for d in output['requested_sessions'])
    cell_ledgers = []
    for cell, packet in output['cells'].items():
        daily = {r['session']: r for r in packet['ledger']}
        days = []
        levels = {s: 1. for s in packet['spec']['bps_per_side']}
        quantities = {s: 0. for s in levels}
        for d in dates[1:]:
            row = daily.get(d.isoformat())
            if row is None:
                days.append(ResearchLedgerDay(d, 1., (), 0., 0., 1., 0.))
                continue
            for s in levels:
                # Return-index units are an accounting numeraire, not raw shares.
                old_value = row['security_start'].get(s, 0.)
                if old_value:
                    levels[s] *= row['security_pre'][s] / old_value
                quantities[s] = row['security_end'].get(s, 0.) / levels[s]
            positions = tuple(ResearchPositionMark(s, quantities[s], row['security_end'].get(s, 0.))
                              for s in levels if quantities[s] != 0.)
            days.append(ResearchLedgerDay(d, math.fsum(row['virtual_cash_end'].values()), positions,
                -math.fsum(row['net_trades'].values()), row['fees'], row['nav_end'], row['net_return']))
        profile = started.strategy_profile + '__' + cell.replace(':', '__')
        ledger = ResearchDailyLedger(started.trial_id, started.domain, profile,
            run_id, 1, started.input_id, started.calendar_id, 252,
            started.cost_source, started.cost_inputs, dates[0], 1., 1., (), tuple(days), started.synthetic)
        store.save_research_ledger(ledger)
        if store.load_research_ledger(started.domain, profile, started.trial_id, run_id, 1) != ledger:
            raise ValueError('BATCH_CELL_LEDGER_READBACK_FAILED')
        cell_ledgers.append((cell, ledger))
    aggregate_days, previous_nav = [], float(len(cell_ledgers))
    for i, d in enumerate(dates[1:]):
        days = [(cell, ledger.days[i]) for cell, ledger in cell_ledgers]
        nav = math.fsum(day.nav for _, day in days)
        positions = tuple(ResearchPositionMark(cell.replace(':', '_') + '_' + p.symbol,
                                               p.quantity, p.valuation)
                          for cell, day in days for p in day.positions)
        aggregate_days.append(ResearchLedgerDay(d, math.fsum(day.cash for _, day in days), positions,
            math.fsum(day.trade_net_cashflow for _, day in days), math.fsum(day.fees for _, day in days),
            nav, nav / previous_nav - 1.))
        previous_nav = nav
    ledger = ResearchDailyLedger(started.trial_id, started.domain, started.strategy_profile,
        run_id, 1, started.input_id, started.calendar_id, 252,
        started.cost_source, started.cost_inputs, dates[0], float(len(cell_ledgers)), float(len(cell_ledgers)), (), tuple(aggregate_days), started.synthetic)
    result = BacktestResult(strategy_profile=started.strategy_profile, domain=started.domain,
        param_set_id=started.param_set_id, params={**params, 'research_identity': dict(started.research_identity)},
        total_return=ledger.total_return, start_date=started.window_start, end_date=started.window_end,
        observation_count=ledger.observation_count, run_id=run_id, source_script=__name__,
        source_revision=started.source_revision, cost_model=started.cost_source,
        cost_inputs=dict(started.cost_inputs), calendar_id=started.calendar_id, periods_per_year=252)
    return result, ledger


def run_registered_r0_r3_batch(*, projection: Mapping[str, Any], identity_packet_sha256: str,
    dataset_class: str, input_body_sha256: str | None, implementation_sha256: Mapping[str, str],
    policies: Mapping[str, Any] | None, original_scope_caps: Mapping[str, float] | None,
    original_scope_cap_source: str | None, original_outer_f: Mapping[str, float] | None,
    original_policy_configs: Mapping[str, Any] | None,
    last_close_targets: Mapping[str, CloseTarget] | None, requested_start: date, requested_end: date,
    source_revision: str, trial_id: str, store: Any, load_input_body: Any,
    core_only_source_binding: Mapping[str, Any] | None = None,
    input_source_registration: Mapping[str, Any] | None = None,
    load_source_root: Any | None = None) -> dict[str, Any]:
    """Register one frozen R0-R3 batch before loading inputs or generating models.

    Explicit real retrospective and artificial admissions share the same native
    computation after source/policy binding. The caller must separately authorize
    real data access and review; metadata admission grants neither. No historical
    available_at or PIT qualification is invented from the assumed model clock.
    The single-writer CLI owns exclusion. Unknown STARTED/terminal writes are never
    recomputed. Existing helper owns failure/interrupt terminals and resume.
    For unknown canonical digest, register13 source objects/parser first, pass
    load_input_body=None and load_source_root returning a private directory.
    The internal fixed parser runs after STARTED; source paths never enter params.
    """
    import json
    from copy import deepcopy
    from dataclasses import asdict, replace
    from .partial_rebalance import RetrospectivePolicyContext, _context
    from . import tqqq_trial_journal
    from quant_platform_kit.strategy_lifecycle.contracts import ResearchTrialRecord, ResearchTrialStatus
    from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore

    if (type(store) is not PerformanceStore or store.cloud_bucket or store.client_factory is not None
            or store.local_root is None):
        raise ValueError('LOCAL_EXPLICIT_PERFORMANCE_STORE_REQUIRED')
    source = admit_r0_r3_source_metadata(projection, identity_packet_sha256=identity_packet_sha256,
                                          dataset_class=dataset_class)
    if (not implementation_sha256 or any(not isinstance(v, str) or len(v) != 64
            or any(c not in '0123456789abcdef' for c in v) for v in implementation_sha256.values())
            or (input_body_sha256 is not None and (not isinstance(input_body_sha256, str)
                or len(input_body_sha256) != 64
                or any(c not in '0123456789abcdef' for c in input_body_sha256)))):
        raise ValueError('FROZEN_CODE_DEPENDENCY_INPUT_DIGESTS_REQUIRED')
    if ((last_close_targets is None and core_only_source_binding is None)
            or (last_close_targets is not None and set(last_close_targets) != set(_R0_R3_CORES))):
        raise ValueError('EXPLICIT_ORIGINAL_TYPE_LAST_TARGETS_REQUIRED')
    policies = None if policies is None else deepcopy(dict(policies))
    last_close_targets = (None if last_close_targets is None else
        {k: replace(v, weights=dict(v.weights)) for k, v in last_close_targets.items()})
    params = {'source': source, 'input_body_sha256': input_body_sha256,
        'implementation_sha256': dict(implementation_sha256),
        'policies': None if policies is None else {k: asdict(v) for k, v in policies.items()},
        'original_scope_caps': None if original_scope_caps is None else dict(original_scope_caps),
        'original_scope_cap_source': original_scope_cap_source,
        'original_policy_configs': original_policy_configs,
        'original_outer_f': None if original_outer_f is None else dict(original_outer_f),
        'last_close_targets': None if last_close_targets is None else {k: {'decision_session': v.decision_session.isoformat(),
            'fill_session': v.fill_session.isoformat(), 'weights': dict(v.weights)}
            for k, v in last_close_targets.items()},
        'requested_window': [requested_start.isoformat(), requested_end.isoformat()],
        'date_rule': 'FIRST63_COMPLETE_ACTIVE_ALIGNED_THEN_STRICTLY_LATER_NO_TUNING',
        'rows': list(R0_R3_ROWS), 'profiles': list(_R0_R3_CORES), 'folds': 1, 'cost_cases': 1,
        'c': .25, 'lambda': .5, 'band': .01, 'kappa0': 1, 'train_complete_sessions': 63,
        'initial': {'per_cell_nav': 1., 'cash': 1., 'security_values': 0., 'outer_f': 0., 'flows': 0.},
        'purpose': _R0_R3_PURPOSE, 'PIT': 'NOT_ATTESTED', 'OOS': 'SEEN_DEVELOPMENT_NOT_INDEPENDENT',
        'promotion': 'NONE', 'R4': 'OUT_OF_SCOPE', 'cost_identity': _R0_R3_COST,
        'ledger_role': 'ADDITIVE_AUDIT_OF_EIGHT_SEPARATE_BOOKS_NOT_JOINT_ALLOCATION',
        'cell_unit': 'DEPENDENT_SUBRESULT_NOT_TRIAL', 'no_order': True}
    if core_only_source_binding is not None:
        params.update(core_only_source_binding=json.loads(_r0_r3_json(core_only_source_binding)),
            terminal_target_role='EXPLICIT_ZERO_MODEL_BOUNDARY_NOT_ACTUAL_STRATEGY_TERMINAL',
            active_rows=['R0_ORIGINAL_FULL','R2_B5_HALF_ONLY','R3_B5_BAND_ONLY'],
            unavailable_cells={f'R1_B4_ONLY:{k}': 'PAUSED_UNAVAILABLE_SAME_SCOPE_B4_CAP_UNDEFINED'
                               for k in _R0_R3_CORES},
            ledger_role='ADDITIVE_AUDIT_OF_SIX_EXECUTED_BOOKS_NOT_COMPARISON_COMPLETION')
    if last_close_targets is None:
        params['last_close_target_rule'] = 'SOURCE_LAST_TWO_EVALUATION_SESSIONS_ZERO_SECURITY_TARGET'
    if input_body_sha256 is None:
        if core_only_source_binding is None or input_source_registration is None:
            raise ValueError('SOURCE_REGISTRATION_REQUIRED_BEFORE_BODY')
        params['input_source_registration'] = json.loads(_r0_r3_json(input_source_registration))
    elif input_source_registration is not None:
        raise ValueError('AMBIGUOUS_CANONICAL_BODY_AND_SOURCE_REGISTRATION')
    frozen = _r0_r3_json(params)
    fingerprint = _r0_r3_sha(params)
    started = ResearchTrialRecord(trial_id=trial_id, domain='us_equity',
        strategy_profile='r0_r3_batch_additive_audit_v1', status=ResearchTrialStatus.STARTED,
        candidate_config_id='r0-r3:' + fingerprint, actual_params=json.loads(frozen),
        param_set_id='sha256:' + fingerprint, source_revision=source_revision,
        input_id='sha256:' + (input_body_sha256 if input_body_sha256 is not None
            else _r0_r3_sha(params['input_source_registration'])), window_start=requested_start, window_end=requested_end,
        calendar_id='XNYS', periods_per_year=252, cost_source=_R0_R3_COST,
        cost_inputs={'per_side_bps': 5.}, reason_code='', synthetic=dataset_class == 'ARTIFICIAL_FIXTURE',
        run_id=None, param_version=None, research_identity={'batch_fingerprint': fingerprint,
            'purpose': _R0_R3_PURPOSE, 'trial_count': 1, 'dependent_cells': 8})

    def simulate(registered: dict[str, Any]) -> dict[str, Any]:
        contexts = policies
        if core_only_source_binding is not None:
            if (registered['policies'] is not None or registered['original_scope_caps'] is not None
                    or registered['original_scope_cap_source'] is not None
                    or registered['original_policy_configs'] is not None
                    or registered['original_outer_f'] != {'TQQQ': 1., 'SOXL': 1.}
                    or any(isinstance(v, bool) for v in registered['original_outer_f'].values())):
                raise ValueError('UNAVAILABLE_CORE_ONLY_PROTOCOL_CONFLICT')
            contexts = _root_a_core_contexts(registered['core_only_source_binding'],
                registered['implementation_sha256'], identity_packet_sha256, dataset_class)
        else:
            if (policies is None or set(policies) != set(_R0_R3_CORES)
                    or original_scope_caps is None or set(original_scope_caps) != set(_R0_R3_CORES)
                    or not original_scope_cap_source or original_outer_f is None
                    or set(original_outer_f) != set(_R0_R3_CORES)
                    or original_policy_configs is None or set(original_policy_configs) != set(_R0_R3_CORES)):
                raise ValueError('UNAVAILABLE_ORIGINAL_POLICY_SOURCE_SCOPE_CODE_CONFIG_CAP_VETO_MANDATORY')
            for k in _R0_R3_CORES:
                ctx = policies[k]
                if (type(ctx) is not RetrospectivePolicyContext or ctx.scope != 'RS04:' + k
                        or ctx.source_identity != identity_packet_sha256 or ctx.dataset_class != dataset_class
                        or ctx.cap_basis != 'DECISION_TIME_TARGET' or ctx.turnover_basis != 'RISK_BUY_WEIGHT'
                        or ctx.max_one_way_risk_turnover is not None):
                    raise ValueError('UNAVAILABLE_ORIGINAL_POLICY_BINDING_OR_BASIS')
                _context(ctx, tuple(s.symbol for s in ctx.securities))
                config = registered['original_policy_configs'][k]
                expected = {'scope': ctx.scope, 'exact_source': ctx.exact_source,
                    'outer_f': registered['original_outer_f'][k],
                    'scope_cap': registered['original_scope_caps'][k],
                    'scope_cap_source': registered['original_scope_cap_source'],
                    'hard_policy': {key: value for key, value in asdict(ctx).items()
                        if key not in RetrospectivePolicyContext.__annotations__},
                    'decision_rules': config.get('decision_rules') if isinstance(config, dict) else None}
                if (config != json.loads(_r0_r3_json(expected)) or ctx.config_sha256 != _r0_r3_sha(config)
                        or ctx.code_sha256 != _r0_r3_sha(registered['implementation_sha256'])
                        or original_scope_cap_source != ctx.exact_source
                        or not isinstance(config['decision_rules'], dict)):
                    raise ValueError('UNAVAILABLE_EXACT_ORIGINAL_POLICY_CODE_CONFIG_DIGEST')
                if not 0 <= _number(original_scope_caps[k], 'UNAVAILABLE_SCOPE_CAP') <= 1:
                    raise ValueError('UNAVAILABLE_SCOPE_CAP')
                if not 0 <= _number(original_outer_f[k], 'UNAVAILABLE_ORIGINAL_OUTER_F') <= 1:
                    raise ValueError('UNAVAILABLE_ORIGINAL_OUTER_F')
        registration = registered.get('input_source_registration')
        if registration is not None:
            _r0_r3_source_registration(registration, identity_packet_sha256, dataset_class)
        if registration is not None:
            if load_input_body is not None or not callable(load_source_root):
                raise ValueError('REGISTERED_ARCHIVE_ROOT_CALLBACK_REQUIRED')
            from pathlib import Path
            from os import PathLike
            raw_root = load_source_root()  # Only after STARTED and source/parser validation.
            if not isinstance(raw_root, (str, Path, PathLike)):
                raise ValueError('REGISTERED_ARCHIVE_ROOT_REFERENCE_REQUIRED')
            loaded = parse_registered_r0_r3_archive(raw_root, registration)
        else:
            if load_source_root is not None:
                raise ValueError('AMBIGUOUS_BODY_AND_ARCHIVE_SOURCE_CALLBACK')
            loaded = load_input_body()
        if registration is not None:
            if (not isinstance(loaded, dict) or set(loaded) != {'body','source_registration'}
                    or loaded['source_registration'] != registration):
                raise ValueError('UNAVAILABLE_PARSED_SOURCE_RECEIPT_MISMATCH')
            body = loaded['body']
        else:
            body = loaded
        body_digest = _r0_r3_sha(body)
        if registration is None and body_digest != registered['input_body_sha256']:
            raise ValueError('UNAVAILABLE_FROZEN_INPUT_BODY_DIGEST_MISMATCH')
        computed = _r0_r3_compute(body, registered, contexts, last_close_targets)
        if registration is not None:
            computed['derived_input_body_sha256'] = body_digest
            computed['derived_run_id'] = 'parsed-' + body_digest + '-' + fingerprint
        return computed

    terminal, output = tqqq_trial_journal._journal_trial(started, store, frozen, simulate,
        lambda record, registered, computed: _r0_r3_persist_cells(record, registered, computed, store))
    cells = {}
    if terminal.status is ResearchTrialStatus.SUCCEEDED:
        for row in params.get('active_rows', R0_R3_ROWS):
            for k in _R0_R3_CORES:
                cell = row + ':' + k
                profile = started.strategy_profile + '__' + cell.replace(':', '__')
                ledger = store.load_research_ledger(started.domain, profile, trial_id, terminal.run_id, 1)
                if ledger is None:
                    raise ValueError('BATCH_IMMUTABLE_CELL_LEDGER_MISSING_NO_RECOMPUTE')
                cells[cell] = ledger.to_dict()
    extra = {}
    if core_only_source_binding is not None:
        extra = {'unavailable_cells': params['unavailable_cells'], 'whole_comparison_complete': False,
                 'terminal_target_role': params['terminal_target_role']}
    if input_body_sha256 is None and terminal.status is ResearchTrialStatus.SUCCEEDED:
        prefix, digest, batch_digest = terminal.run_id.split('-')
        if prefix != 'parsed' or batch_digest != fingerprint or len(digest) != 64:
            raise ValueError('UNAVAILABLE_DERIVED_INPUT_RUN_ID')
        extra['derived_input_body_sha256'] = digest
    status = ('PARTIAL_COMPLETED_R1_UNAVAILABLE' if core_only_source_binding is not None
              and terminal.status is ResearchTrialStatus.SUCCEEDED else terminal.status.value)
    return {'status': status, **extra, 'trial': terminal.to_dict(), 'fingerprint': fingerprint,
        'resumed_without_compute': output is None, 'subresults': cells, 'engineering': output,
        'financial': 'NOT_READY', 'adoption': 'UNKNOWN', 'productionbuild': 'NOT_READY',
        'fullchain': 'NOT_CLOSED', 'live_ready': False, 'no_order': True,
        'PIT': 'NOT_ATTESTED', 'OOS': 'SEEN_DEVELOPMENT_NOT_INDEPENDENT', 'promotion': 'NONE'}



def _qqq_pair_projection(body, scenario):
    """Only the frozen QQQ canonical pair's calculation multiplicity changes."""
    from copy import deepcopy

    if scenario not in ('A_TWO_RECORDS', 'B_ONE_EVENT_ASSUMPTION'):
        raise ValueError('SENSITIVITY_SCENARIO')
    rows = body['actions']['QQQ']['dividends']
    if len(rows) < 5 or rows[3] != rows[4]:
        raise ValueError('SENSITIVITY_PROJECTED_PAIR_MISMATCH')
    day, value = rows[3]
    date.fromisoformat(day)
    if _number(value, 'SENSITIVITY_PAIR_VALUE') < 0:
        raise ValueError('SENSITIVITY_PAIR_VALUE')
    raw_digest = _r0_r3_sha(body)
    derived = deepcopy(body)
    if scenario == 'B_ONE_EVENT_ASSUMPTION':
        # Equal ex_date/rate entries have no event ID here. This selects no correct raw record.
        del derived['actions']['QQQ']['dividends'][4]
    def counts(packet):
        return {f'{s}:{kind}': len(packet['bars'][s]) if kind == 'bars' else
            sum(len(v) for v in packet['actions'][s].values())
            for s in sorted(packet['bars']) for kind in ('bars', 'actions')}
    provenance = {'input_semantics': 'HYPOTHETICAL_QQQ_PAIR_SENSITIVITY',
        'scenario': scenario, 'raw_body_sha256': raw_digest,
        'derived_body_sha256': _r0_r3_sha(derived),
        'raw_counts': counts(body), 'derived_counts': counts(derived),
        'transform_sha256': qqq_pair_transform_identity(),
        'economic_relation': 'UNKNOWN_NOT_ATTESTED',
        'raw_record_correctness_selection': 'NONE_CALCULATION_MULTIPLICITY_ONLY'}
    if _r0_r3_sha(body) != raw_digest:
        raise ValueError('SENSITIVITY_PARENT_BODY_MUTATED')
    return derived, provenance


def qqq_pair_transform_identity():
    """Pin the narrow transform source, independently of the archive parser."""
    import ast
    import hashlib
    from pathlib import Path

    source = Path(__file__).read_text()
    node = next(n for n in ast.parse(source).body
                if isinstance(n, ast.FunctionDef) and n.name == '_qqq_pair_projection')
    return hashlib.sha256(ast.get_source_segment(source, node).encode()).hexdigest()


def _qqq_pair_evaluation_inputs(prepared, profile):
    path = prepared['paths'][profile]
    sessions, train, evaluate = (prepared[k] for k in ('sessions', 'train', 'evaluate'))
    universe = tuple(sorted(path.asset_returns))
    calendar = (sessions[train[-1]], *(sessions[i] for i in evaluate))
    return {'calendar': [d.isoformat() for d in calendar],
        'opening_inner_identity': dict(path.weights[evaluate[0]]),
        'targets': [dict(path.weights[i + 1]) if j + 1 < len(evaluate)
                    else {s: 0. for s in universe} for j, i in enumerate(evaluate)],
        'asset_returns': {s: [path.asset_returns[s][i] for i in evaluate] for s in universe},
        'terminal': {'decision': calendar[-2].isoformat(), 'fill': calendar[-1].isoformat(),
                     'weights': {s: 0. for s in universe}}}



def _qqq_pair_compare_signal_inputs(a, b):
    calendar_equal = all([a['sessions'][i] for i in a[key]] ==
                         [b['sessions'][i] for i in b[key]] for key in ('eligible', 'train', 'evaluate'))
    profiles, changed = {}, []
    for key in _R0_R3_CORES:
        x, y = (_qqq_pair_evaluation_inputs(state, key) for state in (a, b))
        equal = _r0_r3_sha(x) == _r0_r3_sha(y)
        differences = [max(abs(row.get(s, 0.) - other.get(s, 0.))
                           for s in set(row) | set(other))
                       for row, other in zip(x['targets'], y['targets'])] if calendar_equal else []
        opening_difference = max(abs(x['opening_inner_identity'].get(s, 0.) -
                                     y['opening_inner_identity'].get(s, 0.))
                                 for s in set(x['opening_inner_identity']) | set(y['opening_inner_identity']))
        profiles[key] = {'exact_evaluation_inputs_equal': equal,
            'input_hash_A': _r0_r3_sha(x), 'input_hash_B': _r0_r3_sha(y),
            'opening_identity_equal': _r0_r3_sha(x['opening_inner_identity']) == _r0_r3_sha(y['opening_inner_identity']),
            'max_opening_identity_absolute_difference': opening_difference,
            'evaluation_target_inputs_equal': _r0_r3_sha(x['targets']) == _r0_r3_sha(y['targets']),
            'held_asset_return_inputs_equal': _r0_r3_sha(x['asset_returns']) == _r0_r3_sha(y['asset_returns']),
            'terminal_equal': x['terminal'] == y['terminal'],
            'nonempty_weights_differing_sessions': sum(bool(p) != bool(q)
                for p, q in zip(a['paths'][key].weights, b['paths'][key].weights)),
            'exact_evaluation_target_differing_dates': (sum(_r0_r3_sha(p) != _r0_r3_sha(q)
                for p, q in zip(x['targets'], y['targets'])) if calendar_equal else None),
            'evaluation_target_dates_above_visibility_tolerance': sum(d > 1e-12 for d in differences),
            'max_target_absolute_difference': max(differences, default=0.),
            'first_signal_A': a['paths'][key].first_signal.isoformat(),
            'first_signal_B': b['paths'][key].first_signal.isoformat()}
        if not equal:
            changed.append(key)
    return {'calendar_equal': calendar_equal, 'profiles': profiles,
            'changed_profiles': changed, 'tolerance_is_visibility_only': True}

def run_registered_qqq_pair_sensitivity(*, projection, identity_packet_sha256,
        dataset_class, implementation_sha256, core_only_source_binding,
        input_source_registration, requested_start, requested_end, source_revision,
        batch_id, store, load_source_root, transform_sha256):
    """Two dependent hypotheses, one verified parse, exact signal gate, conditional books.

    Source admission and policy contexts describe the unchanged raw origin. The
    explicitly hypothetical input semantics/counts/identities are separate. No
    externally supplied body, fake raw counts, generic transform or cap is accepted.
    The caller owns the existing exclusive writer lock. An existing attempt stops.
    """
    from copy import deepcopy
    from dataclasses import replace
    from .tqqq_trial_journal import _record_trial_failure
    from quant_platform_kit.strategy_lifecycle.contracts import ResearchTrialRecord, ResearchTrialStatus
    from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore

    # Bind all later registration uses to the same snapshot, not callback-owned state.
    input_source_registration = deepcopy(input_source_registration)
    if (type(store) is not PerformanceStore or store.cloud_bucket or store.client_factory is not None
            or store.local_root is None):
        raise ValueError('LOCAL_EXPLICIT_PERFORMANCE_STORE_REQUIRED')
    if transform_sha256 != qqq_pair_transform_identity():
        raise ValueError('SENSITIVITY_TRANSFORM_IDENTITY')
    if (not isinstance(batch_id, str) or not batch_id or batch_id != batch_id.strip()
            or batch_id == 'r0-r2-r3-core-only-d5-20261020-01' or not callable(load_source_root)):
        raise ValueError('SENSITIVITY_BATCH_ID_OR_SOURCE_CALLBACK')
    source = admit_r0_r3_source_metadata(projection,
        identity_packet_sha256=identity_packet_sha256, dataset_class=dataset_class)
    _r0_r3_source_registration(input_source_registration, identity_packet_sha256, dataset_class)
    if not implementation_sha256 or any(not isinstance(v, str) or len(v) != 64
            or any(c not in '0123456789abcdef' for c in v) for v in implementation_sha256.values()):
        raise ValueError('FROZEN_CODE_DEPENDENCY_INPUT_DIGESTS_REQUIRED')
    contexts = _root_a_core_contexts(core_only_source_binding, implementation_sha256,
                                   identity_packet_sha256, dataset_class)
    labels = ('A_TWO_RECORDS', 'B_ONE_EVENT_ASSUMPTION')
    suffixes = ('-A-double', '-B-single')
    profile = 'r0_r3_qqq_pair_sensitivity_v1'
    raw_counts = {g['symbol'] + ':' + g['kind']: g['count'] for g in source['groups']}
    records, params = {}, {}
    for label, suffix in zip(labels, suffixes):
        if store.load_research_trial('us_equity', profile, batch_id + suffix) is not None:
            raise ValueError('SENSITIVITY_TRIAL_ALREADY_EXISTS')
        expected_counts = dict(raw_counts)
        if label == labels[1]:
            expected_counts['QQQ:actions'] -= 1
        frozen = {'source': source, 'input_semantics': 'HYPOTHETICAL_QQQ_PAIR_SENSITIVITY',
            'scenario': label, 'input_source_registration': dict(input_source_registration),
            'transform_sha256': transform_sha256, 'raw_counts': raw_counts,
            'expected_derived_counts': expected_counts, 'canonical_body_sha256': None,
            'implementation_sha256': dict(implementation_sha256),
            'core_only_source_binding': deepcopy(core_only_source_binding),
            'original_outer_f': {'TQQQ': 1., 'SOXL': 1.},
            'requested_window': [requested_start.isoformat(), requested_end.isoformat()],
            'active_rows': ['R0_ORIGINAL_FULL', 'R2_B5_HALF_ONLY', 'R3_B5_BAND_ONLY'],
            'initial': {'nav': 1., 'cash': 1., 'securities': 0.},
            'train_complete_sessions': 63, 'lambda': .5, 'band': .01,
            'terminal_rule': 'SOURCE_LAST_TWO_EVALUATION_SESSIONS_ZERO_SECURITY_TARGET',
            'ledger_rule': 'ONLY_EXACT_RELEVANT_EVALUATION_INPUT_DIFFERENCES',
            'no_ledger_terminal': 'ABORTED_LEDGER_NOT_REQUIRED_COMPARISON_COMPLETE',
            'visibility_absolute_tolerance': 1e-12, 'visibility_relative_tolerance': 0.,
            'PIT': 'NOT_ATTESTED', 'OOS': 'DEPENDENT_HYPOTHESES_NOT_INDEPENDENT',
            'economic_relation': 'UNKNOWN_NOT_ATTESTED', 'no_order': True}
        fingerprint = _r0_r3_sha(frozen)
        params[label] = frozen
        records[label] = ResearchTrialRecord(trial_id=batch_id + suffix, domain='us_equity',
            strategy_profile=profile, status=ResearchTrialStatus.STARTED,
            candidate_config_id='qqq-pair:' + fingerprint, actual_params=deepcopy(frozen),
            param_set_id='sha256:' + fingerprint, source_revision=source_revision,
            input_id='sha256:' + _r0_r3_sha({'parent': input_source_registration,
                'scenario': label, 'transform': transform_sha256}),
            window_start=requested_start, window_end=requested_end, calendar_id='XNYS',
            periods_per_year=252, cost_source=_R0_R3_COST, cost_inputs={'per_side_bps': 5.},
            reason_code='', synthetic=dataset_class == 'ARTIFICIAL_FIXTURE',
            run_id=None, param_version=None, research_identity={'batch_fingerprint': fingerprint,
                'purpose': 'QQQ_PAIR_DEPENDENT_HYPOTHESES', 'trial_count': 1, 'dependent_cells': 6})
    # Unknown STARTED writes/readbacks stop here, before any source access.
    for label in labels:
        store.save_research_trial(records[label])
        if store.load_research_trial('us_equity', profile, records[label].trial_id) != records[label]:
            raise ValueError('RESEARCH_TRIAL_READBACK_INVALID')
    terminal_attempted = {label: False for label in labels}
    try:
        loaded = parse_registered_r0_r3_archive(load_source_root(), input_source_registration)
        if loaded['source_registration'] != input_source_registration:
            raise ValueError('UNAVAILABLE_PARSED_SOURCE_RECEIPT_MISMATCH')
        bodies, provenance, effective, prepared = {}, {}, {}, {}
        for label in labels:
            bodies[label], provenance[label] = _qqq_pair_projection(loaded['body'], label)
            proof = provenance[label]
            if (proof['raw_counts'] != raw_counts
                    or proof['derived_counts'] != params[label]['expected_derived_counts']
                    or proof['transform_sha256'] != transform_sha256):
                raise ValueError('SENSITIVITY_DERIVED_PROVENANCE_MISMATCH')
            effective[label] = deepcopy(params[label])
            # Internal calculation metadata is labeled derived; raw source metadata above stays immutable.
            effective[label]['source'] = deepcopy(source)
            effective[label]['source']['dataset_class'] = 'HYPOTHETICAL_QQQ_PAIR_SENSITIVITY'
            for group in effective[label]['source']['groups']:
                group['count'] = proof['derived_counts'][group['symbol'] + ':' + group['kind']]
            prepared[label] = _r0_r3_prepare_signal_inputs(bodies[label], effective[label])
        a, b = (prepared[label] for label in labels)
        comparison = _qqq_pair_compare_signal_inputs(a, b)
        calendar_equal, changed = comparison['calendar_equal'], comparison['changed_profiles']
        engineering, executed = {}, 0
        if not calendar_equal or not changed:
            reason = 'qqq_pair_calendar_mismatch_no_ledger' if not calendar_equal else 'qqq_pair_equal_inputs_ledger_not_required'
            for label in labels:
                terminal_attempted[label] = True
                terminal = replace(records[label], status=ResearchTrialStatus.ABORTED, reason_code=reason)
                store.save_research_trial(terminal)
                if store.load_research_trial('us_equity', profile, terminal.trial_id) != terminal:
                    raise ValueError('RESEARCH_TRIAL_READBACK_INVALID')
                records[label] = terminal
            status = ('STOPPED_CALENDAR_MISMATCH_NO_INTERSECTION' if not calendar_equal
                      else 'COMPLETED_NO_RELEVANT_DIFFERENCE_NO_LEDGER')
        else:
            for label in labels:
                output = _r0_r3_compute(bodies[label], effective[label], contexts, None,
                    _prepared=prepared[label], _profiles=tuple(changed))
                output['derived_run_id'] = ('qqq-pair-' + provenance[label]['derived_body_sha256']
                    + '-' + _r0_r3_sha(params[label]))
                result, ledger = _r0_r3_persist_cells(records[label], params[label], output, store)
                store.save_backtest_result(result)
                store.save_research_ledger(ledger)
                terminal_attempted[label] = True
                terminal = replace(records[label], status=ResearchTrialStatus.SUCCEEDED,
                    run_id=result.run_id, param_version=result.param_version)
                store.save_research_trial(terminal)
                if store.load_research_trial('us_equity', profile, terminal.trial_id) != terminal:
                    raise ValueError('RESEARCH_TRIAL_READBACK_INVALID')
                records[label] = terminal
                engineering[label] = output
                executed += len(output['cells'])
            status = 'COMPLETED_HYPOTHETICAL_SENSITIVITY_LEDGERS'
        for label in labels:
            if records[label].actual_params != params[label]:
                raise ValueError('SENSITIVITY_FROZEN_PARAMS_MUTATED')
        def span(state, key):
            indices = state[key]
            return {'count': len(indices), 'first': state['sessions'][indices[0]].isoformat(),
                    'last': state['sessions'][indices[-1]].isoformat()}
        return {'status': status, 'comparison': comparison, 'derivation': provenance,
            'source_registration': dict(input_source_registration),
            'signals': {label: {key: span(prepared[label], key)
                               for key in ('eligible', 'train', 'evaluate')} for label in labels},
            'trials': {label: records[label].to_dict() for label in labels},
            'engineering': engineering, 'ledger_cells_executed': executed,
            'parse_calls': 1, 'scenario_count': 2, 'hypothetical_only': True,
            'financial': 'NOT_READY', 'adoption': 'UNKNOWN', 'fullchain': 'NOT_CLOSED',
            'R1': 'PAUSED_UNAVAILABLE', 'no_order': True}
    except BaseException as exc:
        for label in labels:
            state = (ResearchTrialStatus.ABORTED if isinstance(exc, (KeyboardInterrupt, SystemExit))
                     else ResearchTrialStatus.REJECTED if isinstance(exc, ValueError)
                     else ResearchTrialStatus.FAILED)
            _record_trial_failure(store, records[label], state, 'qqq_pair_sensitivity_stopped',
                                  exc, terminal_attempted=terminal_attempted[label])
        raise
