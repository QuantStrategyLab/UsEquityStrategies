"""Synthetic-only B5 decision-time weight rules, without I/O or orders.

Inputs are explicit synthetic snapshots, not authenticated policy/PIT evidence.
FULL uses d; PROPORTIONAL uses w + lambda*(d-w); BAND_HOLD compares every
security, including BOXX. BOXX remains a security; residual cash is 1-sum(w).
HOLD_POSITIONS means unchanged quantities and carries no next-fill target.

The small supported policy basis is DECISION_TIME_TARGET: explicit symbol,
effective/underlying exposure caps and zero-position vetoes. Risk classification
is supplied, never inferred from BOXX. Exposure formulas and RISK_BUY_WEIGHT
turnover match portfolio_risk_budget.py: sum(max(target-current, 0)) over risk
securities. This module neither calls its allocator nor creates exit exemptions.
Hard comparisons use exact rational values of the supplied floats, without an
epsilon allowance. Only band subtraction has a two-ulp comparison tolerance.

No future returns enter selection. Before any subsequent execution, the consumer
must recheck actual drift, net security fees, cash sufficiency and the original
hard policy. Post-fee/fill-time caps cannot be certified here. No ledger, fee
model, consumer integration, financial qualification or execution right is added.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from fractions import Fraction


@dataclass(frozen=True)
class SecurityPolicy:
    symbol: str
    is_risk: bool
    effective_exposure_factor: float
    underlying: str
    max_weight: float


@dataclass(frozen=True)
class HardPolicyContext:
    """Explicit synthetic policy; source labels are assertions, not credentials.

    All securities need a cap/classification and all risk underlyings need a
    cap. A zero cap is valid. None explicitly disables risk-buy turnover only.
    vetoed_symbols means those securities must have zero target weight.
    """

    securities: tuple[SecurityPolicy, ...]
    max_effective_risk_exposure: float
    max_underlying_exposure: tuple[tuple[str, float], ...]
    vetoed_symbols: tuple[str, ...]
    source: str
    classification_source: str
    synthetic_only: bool
    cap_basis: str
    max_one_way_risk_turnover: float | None
    turnover_basis: str
    weight_unit: str


@dataclass(frozen=True)
class InputSummary:
    sha256: str
    security_universe: tuple[str, ...]
    current_residual_cash: float
    full_target_residual_cash: float


@dataclass(frozen=True)
class RebalanceDecision:
    status: str
    reason: str
    kind: str | None
    target_weights: tuple[tuple[str, float], ...] | None
    residual_cash: float | None
    decision_session: str | None
    cutoff: str | None
    mode: str | None
    lambda_value: float | None
    band: float | None
    input_summary: InputSummary | None
    forced_source: str | None = None
    forced_reason: str | None = None
    parameter_units: tuple[tuple[str, str], ...] = (
        ('weights', 'NAV_FRACTION'),
        ('lambda_value', 'DECISION_GAP_FRACTION'),
        ('band', 'ABSOLUTE_NAV_FRACTION'),
        ('turnover', 'ONE_WAY_RISK_BUY_WEIGHT'),
    )
    scope: str = 'SYNTHETIC_ONLY'
    live_ready: bool = False
    order_authorized: bool = False
    policy_authenticated: bool = False
    pit_authenticated: bool = False
    requires_downstream_verification: tuple[str, ...] = (
        'ACTUAL_DRIFT_FEES_NET_CASH_AND_HARD_POLICY',
    )


class _Refusal(ValueError):
    def __init__(self, reason: str, status: str = 'REJECTED'):
        self.reason = reason
        self.status = status


def _number(value: object, reason: str, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _Refusal(reason)
    try:
        number = float(value)
    except OverflowError:
        raise _Refusal(reason) from None
    if not math.isfinite(number) or number < 0 or (maximum is not None and number > maximum):
        raise _Refusal(reason)
    return number


def _label(value: object) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip()


def _symbol(value: object) -> bool:
    return _label(value) and value == value.upper()


def _cash(weights: Mapping[str, float]) -> float:
    # Exact sums prevent rounding a tiny cash deficit into apparent feasibility.
    return float(1 - sum(map(Fraction, weights.values()), Fraction()))


def _weights(value: object, universe: tuple[str, ...]) -> dict[str, float]:
    # Mapping-only API rejects sequences, including duplicate-symbol pair lists.
    if not isinstance(value, Mapping) or set(value) != set(universe):
        raise _Refusal('INVALID_SECURITY_WEIGHTS')
    result = {s: _number(value[s], 'INVALID_SECURITY_WEIGHTS', 1) for s in universe}
    if _cash(result) < 0:
        raise _Refusal('UNFUNDED_SECURITY_WEIGHTS')
    return result


def _context(value: object, universe: tuple[str, ...]) -> HardPolicyContext:
    if not isinstance(value, HardPolicyContext):
        raise _Refusal('MISSING_OR_INVALID_POLICY')
    if (value.synthetic_only is not True or not _label(value.source)
            or not _label(value.classification_source) or value.weight_unit != 'NAV_FRACTION'):
        raise _Refusal('INVALID_POLICY_SOURCE_SCOPE_OR_UNITS')
    if not isinstance(value.securities, tuple):
        raise _Refusal('INVALID_RISK_CLASSIFICATION')
    securities = {}
    for spec in value.securities:
        if (not isinstance(spec, SecurityPolicy) or not _symbol(spec.symbol)
                or type(spec.is_risk) is not bool or not _label(spec.underlying)
                or spec.symbol in securities):
            raise _Refusal('INVALID_RISK_CLASSIFICATION')
        factor = _number(spec.effective_exposure_factor, 'INVALID_EXPOSURE_FACTOR')
        if factor == 0:
            raise _Refusal('INVALID_EXPOSURE_FACTOR')
        cap = _number(spec.max_weight, 'INVALID_SYMBOL_CAP', 1)
        securities[spec.symbol] = SecurityPolicy(spec.symbol, spec.is_risk, factor,
                                                 spec.underlying, cap)
    if set(securities) != set(universe):
        raise _Refusal('INCOMPLETE_RISK_CLASSIFICATION')
    effective = _number(value.max_effective_risk_exposure, 'INVALID_EFFECTIVE_CAP')
    if not isinstance(value.max_underlying_exposure, tuple):
        raise _Refusal('INVALID_UNDERLYING_CAPS')
    limits = {}
    for pair in value.max_underlying_exposure:
        if (not isinstance(pair, tuple) or len(pair) != 2 or not _label(pair[0])
                or pair[0] in limits):
            raise _Refusal('INVALID_UNDERLYING_CAPS')
        limits[pair[0]] = _number(pair[1], 'INVALID_UNDERLYING_CAPS')
    if set(limits) != {s.underlying for s in securities.values() if s.is_risk}:
        raise _Refusal('INCOMPLETE_UNDERLYING_CAPS')
    vetoes = value.vetoed_symbols
    if (not isinstance(vetoes, tuple) or any(not _symbol(s) for s in vetoes)
            or len(set(vetoes)) != len(vetoes) or not set(vetoes) <= set(universe)):
        raise _Refusal('INVALID_VETO')
    turnover = value.max_one_way_risk_turnover
    if turnover is not None:
        turnover = _number(turnover, 'INVALID_TURNOVER_LIMIT', 1)
    # Return a canonical immutable snapshot, retaining even unsupported bases.
    return HardPolicyContext(tuple(securities[s] for s in universe), effective,
                             tuple(sorted(limits.items())), tuple(sorted(vetoes)),
                             value.source, value.classification_source, True,
                             value.cap_basis, turnover, value.turnover_basis, value.weight_unit)


def _hard_check(target: Mapping[str, float], current: Mapping[str, float],
                context: HardPolicyContext, *, turnover: bool = True) -> None:
    """Check, never rescale; no epsilon can grant extra hard-risk permission."""
    if any(target[s] > 0 for s in context.vetoed_symbols):
        raise _Refusal('HARD_VETO')
    if any(target[s.symbol] > s.max_weight for s in context.securities):
        raise _Refusal('HARD_CAP_SYMBOL_WEIGHT')
    risk = tuple(s for s in context.securities if s.is_risk)
    exposures = {s.symbol: Fraction(target[s.symbol]) * Fraction(s.effective_exposure_factor)
                 for s in risk}
    if sum(exposures.values(), Fraction()) > Fraction(context.max_effective_risk_exposure):
        raise _Refusal('HARD_CAP_EFFECTIVE_EXPOSURE')
    for underlying, cap in context.max_underlying_exposure:
        exposure = sum((exposures[s.symbol] for s in risk if s.underlying == underlying),
                       Fraction())
        if exposure > Fraction(cap):
            raise _Refusal('HARD_CAP_UNDERLYING_EXPOSURE')
    if turnover and context.max_one_way_risk_turnover is not None:
        buys = sum((max(Fraction(target[s.symbol]) - Fraction(current[s.symbol]), 0)
                    for s in risk), Fraction())
        if buys > Fraction(context.max_one_way_risk_turnover):
            raise _Refusal('HARD_CAP_RISK_BUY_TURNOVER')


def _inside_band(current: Mapping[str, float], target: Mapping[str, float], band: float) -> bool:
    for symbol, weight in current.items():
        gap = abs(target[symbol] - weight)
        # Covers binary subtraction at an intended decimal equality only. Zero
        # band requires exact equality. This tolerance never reaches hard caps.
        tolerance = 2 * max(math.ulp(weight), math.ulp(target[symbol]), math.ulp(band))
        if gap > band and (band == 0 or gap - band > tolerance):
            return False
    return True


def decide_partial_rebalance(
    *,
    security_universe: tuple[str, ...],
    current_weights: Mapping[str, float],
    full_target: Mapping[str, float],
    mode: str,
    decision_session: str,
    cutoff: str,
    hard_policy_context: HardPolicyContext,
    lambda_value: float = 0.5,
    band: float = 0.01,
    mandatory_reduce: bool = False,
    forced_target: Mapping[str, float] | None = None,
    forced_source: str | None = None,
    forced_reason: str | None = None,
) -> RebalanceDecision:
    """Choose an action from complete, long-only synthetic decision snapshots.

    ``full_target`` asserts an upstream gated target, so its caps/veto are
    rechecked on the ordinary path, before the resulting action is checked.
    Mandatory reduction takes priority and checks the forced target instead.
    Its source must match the context's asserted upstream source; string equality
    does not authenticate that source. Risk securities cannot increase even one
    ulp. Unknown/exit-throttling turnover bases grant no implicit exemption.

    ``cutoff`` must be an explicit UTC ISO timestamp on/after the session date.
    That syntactic check does not certify a trading calendar, close time or PIT.
    Invalid input/policy yields no executable action. Valid HOLD carries neither
    target weights nor target cash. Input summary cash is only a past snapshot.
    """
    summary = None
    lam = delta = None
    session_label = decision_session if isinstance(decision_session, str) else None
    cutoff_label = cutoff if isinstance(cutoff, str) else None
    mode_label = mode if isinstance(mode, str) else None
    try:
        if (not isinstance(security_universe, tuple) or not security_universe
                or any(not _symbol(s) for s in security_universe)
                or len(set(security_universe)) != len(security_universe)
                or 'BOXX' not in security_universe):
            raise _Refusal('INVALID_SECURITY_UNIVERSE')
        universe = tuple(sorted(security_universe))
        if mode not in ('FULL', 'PROPORTIONAL', 'BAND_HOLD'):
            raise _Refusal('INVALID_MODE')
        lam = _number(lambda_value, 'INVALID_LAMBDA', 1)
        delta = _number(band, 'INVALID_BAND', 1)
        if type(mandatory_reduce) is not bool:
            raise _Refusal('INVALID_MANDATORY_FLAG')
        try:
            session = date.fromisoformat(decision_session)
            timestamp = datetime.fromisoformat(cutoff)
            if (session.isoformat() != decision_session or 'T' not in cutoff
                    or timestamp.utcoffset() != timedelta(0) or timestamp.date() < session):
                raise ValueError
        except (TypeError, ValueError):
            raise _Refusal('INVALID_SESSION_OR_UTC_CUTOFF') from None
        current = _weights(current_weights, universe)
        desired = _weights(full_target, universe)
        forced = None if forced_target is None else _weights(forced_target, universe)
        context = _context(hard_policy_context, universe)
        if any(v is not None and not _label(v) for v in (forced_source, forced_reason)):
            raise _Refusal('INVALID_FORCED_SOURCE_OR_REASON')
        payload = dict(current=current, full_target=desired, forced_target=forced,
                       universe=universe, policy=asdict(context), mode=mode, lambda_value=lam,
                       band=delta, mandatory_reduce=mandatory_reduce, forced_source=forced_source,
                       forced_reason=forced_reason, decision_session=decision_session, cutoff=cutoff)
        # Unsupported bases may be non-string invalid values; do not serialize
        # arbitrary objects or let them escape as an unhandled JSON exception.
        if not isinstance(context.cap_basis, str):
            raise _Refusal('UNSUPPORTED_CAP_BASIS_REQUIRES_DOWNSTREAM_VERIFICATION', 'UNSUPPORTED')
        if not isinstance(context.turnover_basis, str):
            code = ('UNSUPPORTED_RISK_THROTTLE_CONFLICT' if mandatory_reduce
                    and context.max_one_way_risk_turnover is not None
                    else 'UNSUPPORTED_TURNOVER_BASIS')
            raise _Refusal(code, 'UNSUPPORTED')
        summary = InputSummary(hashlib.sha256(json.dumps(payload, sort_keys=True,
                               separators=(',', ':'), allow_nan=False).encode()).hexdigest(),
                               universe, _cash(current), _cash(desired))
        if context.cap_basis != 'DECISION_TIME_TARGET':
            raise _Refusal('UNSUPPORTED_CAP_BASIS_REQUIRES_DOWNSTREAM_VERIFICATION', 'UNSUPPORTED')
        if context.turnover_basis != 'RISK_BUY_WEIGHT':
            code = ('UNSUPPORTED_RISK_THROTTLE_CONFLICT' if mandatory_reduce
                    and context.max_one_way_risk_turnover is not None
                    else 'UNSUPPORTED_TURNOVER_BASIS')
            raise _Refusal(code, 'UNSUPPORTED')
        if mandatory_reduce:
            if (forced is None or forced_source != context.source or not _label(forced_reason)):
                raise _Refusal('MISSING_OR_UNTRUSTED_FORCED_TARGET')
            if any(forced[s.symbol] > current[s.symbol] for s in context.securities if s.is_risk):
                raise _Refusal('FORCED_RISK_INCREASE')
            candidate, reason = forced, 'MANDATORY_REDUCTION'
        else:
            # Do not silently repair a purportedly gated d. Turnover is checked
            # on the actual action, not on unused FULL trades in a partial mode.
            _hard_check(desired, current, context, turnover=False)
            if mode == 'PROPORTIONAL' and lam == 0:
                candidate, reason = None, 'ZERO_LAMBDA_HOLD'
            elif mode == 'BAND_HOLD' and _inside_band(current, desired, delta):
                candidate, reason = None, 'WITHIN_BAND_HOLD'
            elif mode == 'PROPORTIONAL' and lam != 1:
                candidate = {s: current[s] + lam * (desired[s] - current[s]) for s in universe}
                candidate, reason = _weights(candidate, universe), 'PROPORTIONAL_DECISION_GAP'
            else:
                candidate, reason = desired, 'FULL_TARGET'
        _hard_check(current if candidate is None else candidate, current, context)
        return RebalanceDecision(
            status='ACCEPTED', reason=reason,
            kind='HOLD_POSITIONS' if candidate is None else 'TARGET_WEIGHTS',
            target_weights=None if candidate is None else tuple(candidate.items()),
            residual_cash=None if candidate is None else _cash(candidate),
            decision_session=session_label, cutoff=cutoff_label, mode=mode_label,
            lambda_value=lam, band=delta, input_summary=summary,
            forced_source=forced_source if mandatory_reduce else None,
            forced_reason=forced_reason if mandatory_reduce else None,
        )
    except _Refusal as exc:
        return RebalanceDecision(status=exc.status, reason=exc.reason, kind=None,
                                 target_weights=None, residual_cash=None,
                                 decision_session=session_label, cutoff=cutoff_label,
                                 mode=mode_label, lambda_value=lam, band=delta,
                                 input_summary=summary)
