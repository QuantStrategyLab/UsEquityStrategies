"""Finite joint-scenario CVaR for a two-session research exposure constraint.

The endpoint is PRE_NEXT_FILL_AT_T_PLUS_2: one fill at t+1, then valuation
before the next fill. Costs here are forecasts, never cash postings. All data,
original hard caps and authority come from the caller; no source/PIT certification
is inferred. Even a feasible result is no_order, not live ready, and changes no
budget. This module has no IO, netter, broker or general optimization interface.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math

ENDPOINT = "PRE_NEXT_FILL_AT_T_PLUS_2"


@dataclass(frozen=True)
class TailDescriptor:
    scope: str
    horizon_sessions: int
    endpoint: str
    currency: str
    benchmark: str
    alpha_confidence: float
    loss_unit: str


@dataclass(frozen=True)
class DecisionBook:
    weights: Mapping[str, float]
    nav: float
    currency: str
    external_flow: float


@dataclass(frozen=True)
class Action:
    kind: str
    target_weights: Mapping[str, float] | None = None


@dataclass(frozen=True)
class JointScenario:
    scenario_id: str
    probability: float
    fill_returns: Mapping[str, float]
    earn_returns: Mapping[str, float]


@dataclass(frozen=True)
class ScenarioSet:
    symbols: tuple[str, ...]
    scenarios: tuple[JointScenario, ...]
    descriptor: TailDescriptor
    set_id: str
    source: str
    coverage_available: bool
    synthetic: bool
    quality_attested: bool


@dataclass(frozen=True)
class CostSpec:
    model: str
    bps_by_symbol: Mapping[str, float]
    unit: str
    frozen_multiplier: float
    multiplier_unit: str


@dataclass(frozen=True)
class ExposureCap:
    """An original linear nonnegative exposure cap; coefficients cover all symbols.

    TARGET_ONLY bounds the specified pre-fee target. POST_FEE bounds actual
    fill weights x/(1-k), using the identical cap in its convex transformation.
    An all-ones cap also expresses an explicit minimum residual-cash policy.
    """

    coefficients: Mapping[str, float]
    cap: float
    basis: str


@dataclass(frozen=True)
class HardPolicy:
    """Only these caps and fully-funded long-only allocations are supported.

    ``convex`` asserts that this schema describes the entire applicable original
    policy. Discrete, callback or unrepresented constraints cannot be certified.
    Caps apply at the fill, not as a guarantee against later market drift. HOLD
    additionally checks the decision book, and its scenario fill weights.
    """

    caps: tuple[ExposureCap, ...]
    risk_symbols: tuple[str, ...]
    source: str
    approved: bool
    synthetic_policy: bool
    convex: bool


@dataclass(frozen=True)
class TailPolicy:
    descriptor: TailDescriptor
    budget: float | None
    source: str
    approved: bool
    synthetic_policy: bool


@dataclass(frozen=True)
class ApprovedAnchor:
    action: Action
    source: str
    approved: bool
    synthetic_policy: bool


@dataclass(frozen=True)
class ScenarioLoss:
    scenario_id: str
    probability: float
    G: float
    pre_fill_weights: Mapping[str, float]
    k: float
    cash_after_fee: float
    nav_ratio: float
    loss: float
    feasible: bool


@dataclass(frozen=True)
class TailEvaluation:
    es: float
    alpha_confidence: float
    tail_probability: float
    scenario_set_id: str
    synthetic_scenarios: bool
    scenarios: tuple[ScenarioLoss, ...]
    feasible: bool
    failures: tuple[str, ...]


@dataclass(frozen=True)
class ConstraintDecision:
    status: str
    reason: str
    action: Action | None = None
    evaluation: TailEvaluation | None = None
    proposal_evaluation: TailEvaluation | None = None
    lambda_value: float | None = None
    iterations: int = 0
    bracket_width: float | None = None
    mandatory_reduce: bool = False
    bypass_b5_throttle: bool = False
    no_order: bool = True
    live_ready: bool = False
    budget_effect: str = "NONE"

    @property
    def target_weights(self) -> Mapping[str, float] | None:
        return None if self.action is None else self.action.target_weights


class _Unavailable(ValueError):
    pass


class _UnsupportedCost(ValueError):
    pass


class _UnsupportedHardPolicy(ValueError):
    pass


def _number(value: object, label: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid {label}")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"nonfinite {label}") from exc
    if not math.isfinite(result) or (nonnegative and result < 0.):
        raise ValueError(f"invalid {label}")
    return result


def _computed(value: float) -> float:
    if not math.isfinite(value):
        raise ArithmeticError("nonfinite scenario arithmetic")
    return value


def _sum(values: Sequence[float]) -> float:
    return _computed(math.fsum(values))


def _sequence(value: object, label: str) -> Sequence:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"invalid {label}")
    return value


def _source(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise _Unavailable(f"missing {label}")


def _flag(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"invalid {label}")
    return value


def _symbols(value: object, *, allow_empty: bool = False) -> tuple[str, ...]:
    if value is None:
        raise _Unavailable("missing securities/risk symbols")
    symbols = tuple(_sequence(value, "securities"))
    if not symbols and not allow_empty:
        raise _Unavailable("missing securities")
    for symbol in symbols:
        if not isinstance(symbol, str) or not symbol or symbol.strip() != symbol:
            raise ValueError("invalid security symbol")
    if len(set(symbols)) != len(symbols):
        raise ValueError("duplicate security symbol")
    return symbols


def _complete_mapping(value: object, symbols: tuple[str, ...], label: str) -> Mapping:
    if value is None:
        raise _Unavailable(f"missing {label}")
    if not isinstance(value, Mapping):
        raise ValueError(f"invalid {label}")
    if set(value) - set(symbols):
        raise ValueError(f"unknown security in {label}")
    if set(symbols) - set(value):
        raise _Unavailable(f"incomplete securities in {label}")
    return value


def _weights(value: object, symbols: tuple[str, ...]) -> dict[str, float]:
    values = _complete_mapping(value, symbols, "weights")
    result = {s: _number(values[s], "weight", nonnegative=True) for s in symbols}
    if _sum(tuple(result.values())) > 1.:
        raise ValueError("weights exceed fully-funded long-only NAV")
    return result


def _probabilities(values: Sequence[float]) -> tuple[float, ...]:
    if not values:
        raise _Unavailable("missing probabilities")
    if any(p is None for p in values):
        raise _Unavailable("missing probability")
    ps = tuple(_number(p, "probability", nonnegative=True) for p in values)
    # Deliberately no normalization or tolerance that could admit extra tail mass.
    if math.fsum(ps) != 1.:
        raise ValueError("probabilities must sum to one")
    return ps


def weighted_cvar(losses: Sequence[float], probabilities: Sequence[float], alpha: float) -> float:
    """Exact finite weighted upper-tail ES, splitting the boundary atom.

    Alpha is confidence (e.g. .95), not tail mass. Sort is stable, zero-mass
    atoms contribute nothing, and negative ES is preserved. Invalid mathematical
    inputs raise ValueError. Probability inputs are never filled or normalized.
    """
    values = tuple(_number(v, "loss") for v in _sequence(losses, "losses"))
    ps = _probabilities(tuple(_sequence(probabilities, "probabilities")))
    if not values or len(values) != len(ps):
        raise ValueError("nonempty losses/probabilities of equal length required")
    confidence = _number(alpha, "alpha")
    if not 0. < confidence < 1.:
        raise ValueError("alpha must lie strictly between zero and one")
    q = 1. - confidence
    remaining = q
    terms = []
    for loss, probability in sorted(zip(values, ps), key=lambda pair: pair[0], reverse=True):
        mass = min(probability, remaining)
        terms.append(loss * mass)
        remaining -= mass
        if remaining <= 0.:
            break
    if remaining > 0.:
        raise ArithmeticError("tail mass could not be filled")
    return _computed(math.fsum(terms) / q)


def _descriptor(value: TailDescriptor) -> None:
    if value is None:
        raise _Unavailable("missing tail descriptor")
    if not isinstance(value, TailDescriptor):
        raise ValueError("invalid tail descriptor")
    for field in ("scope", "currency", "benchmark", "loss_unit", "endpoint"):
        _source(getattr(value, field), field)
    alpha = _number(value.alpha_confidence, "alpha confidence")
    if isinstance(value.horizon_sessions, bool) or not isinstance(value.horizon_sessions, int):
        raise ValueError("invalid horizon sessions")
    if (value.horizon_sessions != 2 or value.endpoint != ENDPOINT or alpha != .95
            or value.loss_unit != "DECISION_NAV_FRACTION"):
        raise _Unavailable("unsupported tail horizon/endpoint/alpha/loss unit")
    if len(value.currency) != 3 or not value.currency.isalpha() or not value.currency.isupper():
        raise ValueError("invalid currency")


def _scenario_inputs(scenario_set: ScenarioSet) -> tuple[tuple[str, ...], tuple[JointScenario, ...]]:
    if scenario_set is None:
        raise _Unavailable("missing scenario set")
    if not isinstance(scenario_set, ScenarioSet):
        raise ValueError("invalid scenario set")
    symbols = _symbols(scenario_set.symbols)
    _descriptor(scenario_set.descriptor)
    _source(scenario_set.set_id, "scenario set identity")
    _source(scenario_set.source, "scenario source")
    if not _flag(scenario_set.coverage_available, "coverage availability"):
        raise _Unavailable("scenario coverage unavailable")
    _flag(scenario_set.synthetic, "synthetic scenario identity")
    _flag(scenario_set.quality_attested, "external quality attestation")
    scenarios = tuple(_sequence(scenario_set.scenarios, "joint scenarios"))
    if not scenarios:
        raise _Unavailable("missing joint scenarios")
    seen = set()
    for scenario in scenarios:
        if not isinstance(scenario, JointScenario):
            raise ValueError("invalid joint scenario")
        _source(scenario.scenario_id, "joint scenario identity")
        if scenario.scenario_id in seen:
            raise ValueError("duplicate joint scenario identity")
        seen.add(scenario.scenario_id)
        for returns in (scenario.fill_returns, scenario.earn_returns):
            values = _complete_mapping(returns, symbols, "joint returns")
            for value in values.values():
                if _number(value, "simple return") <= -1.:
                    raise ValueError("security gross return must be positive")
    _probabilities(tuple(s.probability for s in scenarios))
    return symbols, scenarios


def _book(book: DecisionBook, symbols: tuple[str, ...], currency: str) -> dict[str, float]:
    if book is None:
        raise _Unavailable("missing decision book")
    if not isinstance(book, DecisionBook):
        raise ValueError("invalid decision book")
    if _number(book.nav, "decision NAV") <= 0.:
        raise ValueError("decision NAV must be positive")
    if book.currency != currency:
        raise _Unavailable("decision/scenario currency mismatch")
    if _number(book.external_flow, "external flow") != 0.:
        raise ValueError("external flows unsupported")
    return _weights(book.weights, symbols)


def _action(action: Action, symbols: tuple[str, ...]) -> dict[str, float] | None:
    if action is None:
        raise _Unavailable("missing action")
    if not isinstance(action, Action):
        raise ValueError("invalid action")
    if action.kind == "HOLD_POSITIONS" and action.target_weights is None:
        return None
    if action.kind != "TARGET_WEIGHTS":
        raise ValueError("unknown action or HOLD with fixed weights")
    return _weights(action.target_weights, symbols)


def _costs(spec: CostSpec, symbols: tuple[str, ...]) -> dict[str, float]:
    if spec is None:
        raise _Unavailable("missing cost specification")
    if not isinstance(spec, CostSpec):
        raise ValueError("invalid cost specification")
    if (spec.model != "FIXED_NONNEGATIVE_BPS" or spec.unit != "BPS_PER_SIDE"
            or spec.multiplier_unit != "DIMENSIONLESS"):
        raise _UnsupportedCost("only fixed nonnegative per-side bps and frozen multiplier supported")
    multiplier = _number(spec.frozen_multiplier, "frozen multiplier", nonnegative=True)
    bps = _complete_mapping(spec.bps_by_symbol, symbols, "bps")
    return {s: _computed(_number(bps[s], "bps", nonnegative=True) / 10000. * multiplier)
            for s in symbols}


def scenario_losses(decision_book: DecisionBook, action: Action, scenario_set: ScenarioSet,
                    bps_spec: CostSpec) -> tuple[ScenarioLoss, ...]:
    """Project a fixed decision-time target or true HOLD along joint paths.

    Every security must occur in book, target, each return row and cost mapping.
    Residual cash earns zero. BOXX is an ordinary priced, charged security. Each
    result exposes fill NAV G, drifted weights, fee fraction k, fill cash fraction,
    endpoint NAV ratio and signed loss. Finite but unfunded/nonpositive-end-NAV
    targets return infeasible diagnostics; malformed inputs raise ValueError.
    """
    symbols, scenarios = _scenario_inputs(scenario_set)
    w = _book(decision_book, symbols, scenario_set.descriptor.currency)
    x = _action(action, symbols)
    rates = _costs(bps_spec, symbols)
    residual_cash = 1. - _sum(tuple(w.values()))
    result = []
    for scenario in scenarios:
        fill_values = {s: _computed(w[s] * (1. + scenario.fill_returns[s])) for s in symbols}
        G = _sum((residual_cash, *fill_values.values()))
        if G <= 0.:
            raise ArithmeticError("nonpositive fill NAV from valid fully-funded book")
        pre = {s: _computed(fill_values[s] / G) for s in symbols}
        if x is None:
            k = 0.
            cash = _computed(residual_cash / G)
            ratio = _sum((residual_cash, *(_computed(fill_values[s] * (1. + scenario.earn_returns[s]))
                                          for s in symbols)))
        else:
            k = _sum(tuple(_computed(rates[s] * abs(x[s] - pre[s])) for s in symbols))
            cash = _sum((1., -_sum(tuple(x.values())), -k))
            earn = _sum(tuple(_computed(x[s] * scenario.earn_returns[s]) for s in symbols))
            ratio = _computed(G * _sum((1., earn, -k)))
        result.append(ScenarioLoss(scenario.scenario_id, scenario.probability, G, pre, k, cash,
                                   ratio, _computed(1. - ratio), cash >= 0. and ratio > 0. and k < 1.))
    return tuple(result)


def _policies(policy: TailPolicy, hard_policy: HardPolicy, scenario_set: ScenarioSet,
              symbols: tuple[str, ...]) -> float:
    if policy is None or hard_policy is None:
        raise _Unavailable("missing original tail/hard policy")
    if not isinstance(policy, TailPolicy) or not isinstance(hard_policy, HardPolicy):
        raise ValueError("invalid policy")
    _descriptor(policy.descriptor)
    if policy.descriptor != scenario_set.descriptor:
        raise _Unavailable("tail policy/scenario descriptor mismatch")
    if policy.budget is None:
        raise _Unavailable("missing matching tail budget")
    budget = _number(policy.budget, "tail budget")
    for p in (policy, hard_policy):
        _source(p.source, "approved policy source")
        if not _flag(p.approved, "policy approval"):
            raise _Unavailable("policy not approved")
        if _flag(p.synthetic_policy, "synthetic policy identity") != scenario_set.synthetic:
            raise ValueError("synthetic/real policy and scenario identities mixed")
    if not scenario_set.synthetic and not scenario_set.quality_attested:
        raise _Unavailable("real scenario quality not externally attested")
    if not _flag(hard_policy.convex, "convex original policy"):
        raise _UnsupportedHardPolicy("original hard policy is not certified convex")
    risks = _symbols(hard_policy.risk_symbols, allow_empty=True)
    if not set(risks) <= set(symbols):
        raise ValueError("unknown risk symbol")
    if hard_policy.caps is None:
        raise _Unavailable("missing original hard caps")
    for cap in _sequence(hard_policy.caps, "original caps"):
        if not isinstance(cap, ExposureCap):
            raise _UnsupportedHardPolicy("unrepresented original hard constraint")
        if cap.basis not in ("TARGET_ONLY", "POST_FEE"):
            raise _UnsupportedHardPolicy("unknown hard-cap basis")
        _number(cap.cap, "exposure cap", nonnegative=True)
        coefficients = _complete_mapping(cap.coefficients, symbols, "cap coefficients")
        for value in coefficients.values():
            _number(value, "exposure coefficient", nonnegative=True)
    return budget


def _exposure(weights: Mapping[str, float], cap: ExposureCap) -> float:
    return _sum(tuple(_computed(cap.coefficients[s] * weights[s]) for s in weights))


def _evaluate(book: DecisionBook, action: Action, scenario_set: ScenarioSet, costs: CostSpec,
              hard: HardPolicy, budget: float, forced: dict[str, float] | None) -> TailEvaluation:
    rows = scenario_losses(book, action, scenario_set, costs)
    es = weighted_cvar(tuple(row.loss for row in rows), tuple(row.probability for row in rows),
                       scenario_set.descriptor.alpha_confidence)
    failures = []
    if not all(row.feasible for row in rows):
        failures.append("SCENARIO_CASH_OR_NAV")
    if es > budget:
        failures.append("TAIL_BUDGET")
    weights = book.weights if action.kind == "HOLD_POSITIONS" else action.target_weights
    if forced is not None and any(weights[s] > forced[s] for s in hard.risk_symbols):
        failures.append("FORCED_TARGET_CAP")
    for cap in hard.caps:
        if action.kind == "HOLD_POSITIONS":
            # No fee or trade. Reject both an already-breached decision book and
            # a fill scenario whose actual drifting weights breach the same cap.
            passed = (_exposure(weights, cap) <= cap.cap
                      and all(_exposure(row.pre_fill_weights, cap) <= cap.cap for row in rows))
        elif cap.basis == "TARGET_ONLY":
            passed = _exposure(weights, cap) <= cap.cap
        else:
            exposure = _exposure(weights, cap)
            passed = True
            for row in rows:
                if row.k >= 1.:
                    passed = False
                    break
                transformed = _sum((exposure, _computed(cap.cap * row.k)))
                actual_weights = {s: _computed(weights[s] / (1. - row.k)) for s in weights}
                if transformed > cap.cap or _exposure(actual_weights, cap) > cap.cap:
                    passed = False
                    break
        if not passed:
            failures.append("ORIGINAL_HARD_CAP")
    return TailEvaluation(es=es, alpha_confidence=scenario_set.descriptor.alpha_confidence,
                          tail_probability=1. - scenario_set.descriptor.alpha_confidence,
                          scenario_set_id=scenario_set.set_id, synthetic_scenarios=scenario_set.synthetic,
                          scenarios=rows, feasible=not failures, failures=tuple(failures))


def _anchor_action(anchor: ApprovedAnchor | None, scenario_set: ScenarioSet,
                   symbols: tuple[str, ...], hard: HardPolicy, upper: Mapping[str, float],
                   forced: dict[str, float] | None) -> Action | None:
    # Invalid authority cannot supply a fallback, irrespective of mathematical ES.
    if not isinstance(anchor, ApprovedAnchor):
        return None
    try:
        _source(anchor.source, "anchor authority source")
        if not _flag(anchor.approved, "anchor approval"):
            return None
        if _flag(anchor.synthetic_policy, "synthetic anchor identity") != scenario_set.synthetic:
            return None
        weights = _action(anchor.action, symbols)
        if weights is None:
            return None
        for s in hard.risk_symbols:
            if weights[s] > upper[s] or (forced is not None and weights[s] > forced[s]):
                return None
    except ValueError:
        return None
    return anchor.action


def constrain_tail_target(proposal: Action, approved_anchor: ApprovedAnchor | None,
                          policy: TailPolicy, scenario_set: ScenarioSet, *,
                          decision_book: DecisionBook, bps_spec: CostSpec, hard_policy: HardPolicy,
                          forced_target: Mapping[str, float] | None = None) -> ConstraintDecision:
    """Evaluate first; tighten only on an authorized, certified convex segment.

    No anchor is needed for a feasible proposal. A failed fixed target can use a
    fixed approved anchor that does not increase any risk-symbol weight relative
    to the proposal or forced cap. Along their segment convex sublevel sets admit
    feasible-lo/infeasible-hi bisection even when ES is not globally monotone.
    HOLD failure selects a feasible approved fixed anchor directly, never blends
    HOLD with a target. Risk tightening is mandatory and must bypass B5 throttling.

    Missing/mismatched inputs, unsupported policy/cost or arithmetic failure return
    explicit status and no action/target. No fallback is a claim of global
    infeasibility. A caller must separately certify real source/PIT/authority.
    """
    try:
        symbols, _ = _scenario_inputs(scenario_set)
        budget = _policies(policy, hard_policy, scenario_set, symbols)
        w = _book(decision_book, symbols, scenario_set.descriptor.currency)
        proposed_weights = _action(proposal, symbols)
        _costs(bps_spec, symbols)
        forced = None if forced_target is None else _weights(forced_target, symbols)
        original = _evaluate(decision_book, proposal, scenario_set, bps_spec, hard_policy, budget, forced)
        if original.feasible:
            return ConstraintDecision("PROPOSAL_FEASIBLE", "complete proposal evaluation passed", proposal,
                                      original, original)
        hold = proposed_weights is None
        fallback_status = ("TAIL_REDUCTION_REQUIRED_NO_CERTIFIED_TARGET" if hold
                           else "NO_CERTIFIED_FEASIBLE_FALLBACK")
        upper = w if hold else proposed_weights
        anchor_action = _anchor_action(approved_anchor, scenario_set, symbols, hard_policy, upper, forced)
        if anchor_action is None:
            return ConstraintDecision(fallback_status, "no authorized reduce-only fixed anchor",
                                      proposal_evaluation=original, mandatory_reduce=hold,
                                      bypass_b5_throttle=hold)
        anchor_eval = _evaluate(decision_book, anchor_action, scenario_set, bps_spec, hard_policy, budget, forced)
        if not anchor_eval.feasible:
            return ConstraintDecision(fallback_status, "anchor failed full evaluation; no global claim",
                                      proposal_evaluation=original, mandatory_reduce=hold,
                                      bypass_b5_throttle=hold)
        if hold:
            return ConstraintDecision("MANDATORY_TAIL_REDUCTION", "HOLD failed; approved fixed anchor passed",
                                      anchor_action, anchor_eval, original, mandatory_reduce=True,
                                      bypass_b5_throttle=True)
        a = anchor_action.target_weights
        d = proposed_weights
        lo, hi = 0., 1.
        iterations = 0
        while iterations < 60 and hi - lo > 1e-10:
            mid = (lo + hi) / 2.
            candidate = Action("TARGET_WEIGHTS", {s: a[s] + mid * (d[s] - a[s]) for s in symbols})
            evaluation = _evaluate(decision_book, candidate, scenario_set, bps_spec, hard_policy, budget, forced)
            if evaluation.feasible:
                lo = mid
            else:
                hi = mid
            iterations += 1
        chosen = Action("TARGET_WEIGHTS", {s: a[s] + lo * (d[s] - a[s]) for s in symbols})
        final = _evaluate(decision_book, chosen, scenario_set, bps_spec, hard_policy, budget, forced)
        if not final.feasible:
            return ConstraintDecision("UNCOMPUTABLE", "feasible lower endpoint failed final original-formula check")
        return ConstraintDecision("TAIL_TARGET_TIGHTENED", "conservative feasible segment endpoint rechecked",
                                  chosen, final, original, lo, iterations, hi - lo, True, True)
    except _Unavailable as exc:
        return ConstraintDecision("UNAVAILABLE", str(exc))
    except _UnsupportedCost as exc:
        return ConstraintDecision("UNSUPPORTED_COST_MODEL", str(exc))
    except _UnsupportedHardPolicy as exc:
        return ConstraintDecision("UNSUPPORTED_HARD_POLICY", str(exc))
    except (ArithmeticError, OverflowError) as exc:
        return ConstraintDecision("UNCOMPUTABLE", str(exc))
    except (ValueError, TypeError) as exc:
        return ConstraintDecision("REJECTED_INPUT", str(exc))
