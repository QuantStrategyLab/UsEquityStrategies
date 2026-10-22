"""Cost-aware fractional-Kelly target (R5, research-only).

The existing RS-04 evaluator charges costs only *after* the fact, through
``cost_stress_recompute``.  This module puts a proportional one-side cost into
the sizing decision itself:

* no-cost target: ``center = clip(c * mu / sigma2, 0, cap)`` where ``mu`` is
  the already-shrunk per-session excess mean and ``sigma2`` the per-session
  sample variance;
* quadratic utility ``U(w) = mu * w - sigma2 * w^2 / (2 * c)``, whose
  maximum is exactly ``center`` (effective risk aversion ``1 / c``);
* one-side cost ``k = cost_bps_per_side / 10_000`` amortised over
  ``holding_sessions``.

Maximising ``U(w) - (k / H) * |w - w0|`` gives the no-trade band
``[center - delta, center + delta]`` with ``delta = c * k / (H * sigma2)``.
If the current weight is below the band the
target is the lower edge; above the band it is the upper edge; inside the band
it is the current weight.  At zero cost ``delta = 0`` and the target is exactly
the fractional-Kelly target.

Boundaries
----------
- Research only: ``no_order=True``, ``live_ready=False``,
  ``budget_effect="NONE"``.  It never authorises an order or raises a budget.
- No leverage and no shorting: ``0 <= current_weight, target_weight <= 1``.
- ``position_cap`` is a hard ceiling.  If the current weight drifted above it,
  the target is brought back to at most the cap even when the cost band would
  otherwise hold.
- ``mu`` must be the **shrunk** mean (``fit["mu_shrunk"]`` from RS-04).  Passing
  the raw mean would double-count the shrinkage decision.
- Costs are a caller-supplied modelling assumption, not a broker fact.  The
  band follows from a quadratic utility, not an exact optimal-control solution.
- Reported growth fields use the second-order proxy
  ``mu * w - 0.5 * sigma2 * w^2``; they are not a compounded path.
- Lower turnover is not the same as higher net compounding.  The band can
  under-invest relative to the fixed fractional target; net growth depends on
  edge, cost, ``c`` and ``holding_sessions`` and must be measured on a path.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import math
from typing import Any

MODEL_ID = "r5_cost_aware_fractional_kelly_v1"
BAND_MODEL = "LOCAL_QUADRATIC_AROUND_FRACTIONAL_TARGET_V1"
UNIT = "SIMPLE_STRATEGY_RETURN_VARIANCE_AND_EXCESS_MEAN_PER_SESSION_DECIMAL"
COST_UNIT = "BPS_PER_SIDE_ON_TRADED_CAPITAL_FRACTION"

STATUS_COMPUTED = "COMPUTED"
STATUS_HOLD = "HOLD"
STATUS_PARKED = "PARKED"


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{label}_INVALID")
    return float(value)


def _fraction(value: object, label: str, *, upper: float = 1.0) -> float:
    number = _finite(value, label)
    if not 0.0 <= number <= upper:
        raise ValueError(f"{label}_OUT_OF_RANGE")
    return number


@dataclass(frozen=True)
class CostAwareKellyPolicy:
    """Caller policy.  All fields are validated at construction time."""

    c: float = 0.25
    position_cap: float = 1.0
    cost_bps_per_side: float = 0.0
    holding_sessions: int = 1
    min_trade_weight: float = 0.0

    def __post_init__(self) -> None:
        c = _finite(self.c, "C")
        if not 0.0 < c < 1.0:
            raise ValueError("FULL_KELLY_NOT_ALLOWED")
        object.__setattr__(self, "c", c)
        object.__setattr__(self, "position_cap", _fraction(self.position_cap, "POSITION_CAP"))
        cost = _finite(self.cost_bps_per_side, "COST_BPS_PER_SIDE")
        if cost < 0.0:
            raise ValueError("COST_BPS_PER_SIDE_INVALID")
        object.__setattr__(self, "cost_bps_per_side", cost)
        if isinstance(self.holding_sessions, bool) or not isinstance(self.holding_sessions, int):
            raise ValueError("HOLDING_SESSIONS_INVALID")
        if self.holding_sessions < 1:
            raise ValueError("HOLDING_SESSIONS_INVALID")
        object.__setattr__(self, "min_trade_weight", _fraction(self.min_trade_weight, "MIN_TRADE_WEIGHT"))


@dataclass(frozen=True)
class CostAwareKellyTarget:
    status: str
    reason_code: str | None
    current_weight: float | None
    target_weight: float | None
    trade_weight: float | None
    raw_fractional_target: float | None
    no_trade_lower: float | None
    no_trade_upper: float | None
    cost_half_band: float | None
    band_saturated: bool
    estimated_cost_fraction: float | None
    gross_log_growth_per_session_at_current: float | None
    gross_log_growth_per_session_at_target: float | None
    net_log_growth_per_session_at_target: float | None
    net_growth_improvement_vs_hold: float | None
    holding_sessions: int
    cost_bps_per_side: float
    c: float
    position_cap: float
    model_id: str = MODEL_ID
    band_model: str = BAND_MODEL
    unit: str = UNIT
    cost_unit: str = COST_UNIT
    no_order: bool = True
    live_ready: bool = False
    budget_effect: str = "NONE"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parked(policy: CostAwareKellyPolicy, current_weight: float, reason_code: str) -> CostAwareKellyTarget:
    return CostAwareKellyTarget(
        status=STATUS_PARKED,
        reason_code=reason_code,
        current_weight=current_weight,
        target_weight=None,
        trade_weight=None,
        raw_fractional_target=None,
        no_trade_lower=None,
        no_trade_upper=None,
        cost_half_band=None,
        band_saturated=False,
        estimated_cost_fraction=None,
        gross_log_growth_per_session_at_current=None,
        gross_log_growth_per_session_at_target=None,
        net_log_growth_per_session_at_target=None,
        net_growth_improvement_vs_hold=None,
        holding_sessions=policy.holding_sessions,
        cost_bps_per_side=policy.cost_bps_per_side,
        c=policy.c,
        position_cap=policy.position_cap,
    )


def cost_aware_fractional_kelly(
    *,
    mu_excess_shrunk: float,
    sigma2: float,
    current_weight: float,
    policy: CostAwareKellyPolicy,
) -> CostAwareKellyTarget:
    """Return a research target; never an order, budget increase or live signal.

    ``mu_excess_shrunk`` and ``sigma2`` are per-session decimal quantities.
    ``current_weight`` is the current capital fraction in ``[0, 1]``.
    """
    if not isinstance(policy, CostAwareKellyPolicy):
        raise TypeError("POLICY_INVALID")
    mu = _finite(mu_excess_shrunk, "MU_EXCESS_SHRUNK")
    variance = _finite(sigma2, "SIGMA2")
    if variance < 0.0:
        raise ValueError("SIGMA2_INVALID")
    current = _fraction(current_weight, "CURRENT_WEIGHT")
    if variance == 0.0:
        return _parked(policy, current, "ZERO_VARIANCE")

    raw = policy.c * mu / variance
    if not math.isfinite(raw):
        raise ValueError("TARGET_UNCOMPUTABLE")
    center = min(policy.position_cap, max(0.0, raw))

    cost_fraction = policy.cost_bps_per_side / 10_000.0
    half_band = policy.c * cost_fraction / (policy.holding_sessions * variance)
    half_band = 1.0 if not math.isfinite(half_band) else min(half_band, 1.0)
    lower = max(0.0, center - half_band)
    upper = min(policy.position_cap, center + half_band)
    saturated = lower == 0.0 and upper == policy.position_cap

    if current < lower:
        target = lower
        status = STATUS_COMPUTED
        reason = None
    elif current > upper:
        target = upper
        status = STATUS_COMPUTED
        reason = None
    else:
        target = current
        status = STATUS_HOLD
        reason = "WITHIN_NO_TRADE_BAND"

    if current > policy.position_cap:
        status = STATUS_COMPUTED
        reason = "CURRENT_ABOVE_POSITION_CAP"
    elif policy.min_trade_weight > 0.0 and 0.0 < abs(target - current) < policy.min_trade_weight:
        target = current
        status = STATUS_HOLD
        reason = "BELOW_MIN_TRADE_WEIGHT"
    if target == 0.0 and center == 0.0 and current == 0.0:
        status = STATUS_HOLD
        reason = "NO_POSITIVE_EDGE"

    trade = target - current
    if trade == 0.0:
        trade = 0.0
    cost_paid = cost_fraction * abs(trade)
    amortised_cost = cost_paid / policy.holding_sessions
    gross_current = mu * current - 0.5 * variance * current * current
    gross_target = mu * target - 0.5 * variance * target * target
    net_target = gross_target - amortised_cost
    improvement = net_target - gross_current
    if not all(math.isfinite(x) for x in (gross_current, gross_target, net_target, improvement, cost_paid)):
        raise ValueError("TARGET_UNCOMPUTABLE")

    return CostAwareKellyTarget(
        status=status,
        reason_code=reason,
        current_weight=current,
        target_weight=target,
        trade_weight=trade,
        raw_fractional_target=center,
        no_trade_lower=lower,
        no_trade_upper=upper,
        cost_half_band=half_band,
        band_saturated=saturated,
        estimated_cost_fraction=cost_paid,
        gross_log_growth_per_session_at_current=gross_current,
        gross_log_growth_per_session_at_target=gross_target,
        net_log_growth_per_session_at_target=net_target,
        net_growth_improvement_vs_hold=improvement,
        holding_sessions=policy.holding_sessions,
        cost_bps_per_side=policy.cost_bps_per_side,
        c=policy.c,
        position_cap=policy.position_cap,
    )


def from_fractional_kelly_fit(
    fit: Mapping[str, Any],
    *,
    current_weight: float,
    policy: CostAwareKellyPolicy,
) -> CostAwareKellyTarget:
    """Consume an RS-04 ``fit_fractional_kelly`` result without re-shrinking."""
    if not isinstance(policy, CostAwareKellyPolicy):
        raise TypeError("POLICY_INVALID")
    if not isinstance(fit, Mapping):
        raise TypeError("FIT_INVALID")
    if fit.get("status") != STATUS_COMPUTED:
        current = _fraction(current_weight, "CURRENT_WEIGHT")
        return _parked(policy, current, str(fit.get("reason_code") or "FIT_NOT_COMPUTED"))
    if fit.get("c") != policy.c:
        raise ValueError("FIT_POLICY_C_MISMATCH")
    return cost_aware_fractional_kelly(
        mu_excess_shrunk=fit.get("mu_shrunk"),
        sigma2=fit.get("sigma2"),
        current_weight=current_weight,
        policy=policy,
    )
