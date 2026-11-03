"""Artificial-only capital/preset adapter to the existing joint-tail kernel.

No account access, fitting, return-path backtest, new solver, settings writes or
orders. The budget below is two-session modelled CVaR, never a worst-loss or
maximum-drawdown guarantee. Preset names and capital alone are not authority.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import math

from .cvar_exposure_constraint import (
    Action, ApprovedAnchor, CostSpec, DecisionBook, HardPolicy, ScenarioSet,
    TailPolicy, constrain_tail_target,
)

LEVELS = ("conservative", "balanced", "growth")


@dataclass(frozen=True)
class CapitalProtectionCurve:
    reference_capital: float
    exponent: float
    protected_capital: float
    currency: str
    source: str


def _number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid {name}")
    value = float(value)
    if not math.isfinite(value) or value < 0.0 or (positive and value == 0.0):
        raise ValueError(f"invalid {name}")
    return value


def capital_protection_factor(curve: CapitalProtectionCurve) -> float:
    """Smooth decreasing (1 + protected_capital/reference_capital)**(-exponent).

    0 < exponent <= 1 also makes C*factor(C) nondecreasing for a fresh capital
    basis. Reference, exponent and basis are explicit policy inputs, not chosen
    from a backtest. Protected basis must come from the caller's retained state.
    """
    if not isinstance(curve, CapitalProtectionCurve):
        raise ValueError("missing capital protection curve")
    reference = _number(curve.reference_capital, "reference capital", positive=True)
    capital = _number(curve.protected_capital, "protected capital", positive=True)
    exponent = _number(curve.exponent, "curve exponent", positive=True)
    if exponent > 1.0:
        raise ValueError("curve exponent must be at most one")
    if (not isinstance(curve.currency, str) or len(curve.currency) != 3
            or not curve.currency.isalpha() or not curve.currency.isupper()
            or not isinstance(curve.source, str) or not curve.source.strip()):
        raise ValueError("curve currency/source required")
    # Stable log(1 + C/C0), without overflowing C/C0.
    ratio_log = math.log(capital) - math.log(reference)
    log_base = max(ratio_log, 0.0) + math.log1p(math.exp(-abs(ratio_log)))
    return math.exp(-exponent * log_base)


def preview_capital_adaptive_target(
    *, level: str, presets: Mapping[str, TailPolicy], account_policy: TailPolicy | None,
    curve: CapitalProtectionCurve, proposal: Action, scenario_set: ScenarioSet,
    decision_book: DecisionBook, bps_spec: CostSpec, hard_policy: HardPolicy,
    approved_anchor: ApprovedAnchor | None = None,
    absolute_tail_loss_limit: float | None = None,
    previous_tail_budget_ratio: float | None = None,
    forced_target: Mapping[str, float] | None = None,
) -> dict:
    """Tighten one existing proposal; never infer an account policy from a level.

    Protected capital is >= current decision NAV. Retaining it across losses,
    plus an optional previous effective ratio, prevents automatic risk loosening
    after drawdown. Any withdrawal/reset/upgrade requires the caller's separate
    policy decision; this function does not reset or persist state.

    Input amounts share the decision currency. Absolute amount is a CVaR budget
    at the identical horizon/endpoint, not a different loss metric. Final fees,
    hard caps and joint scenarios are evaluated by the unchanged tail kernel.
    """
    header = dict(
        execution_authorized=False, settings_applied=False, no_order=True,
        financial_risk_certified=False, account_policy_bound=False,
        scope="ARTIFICIAL_JOINT_SCENARIOS_ONLY", decision=None,
    )

    def stop(status, reason):
        return {**header, "status": status, "reason": reason}

    try:
        if not isinstance(scenario_set, ScenarioSet):
            raise ValueError("scenario set required")
        if scenario_set.synthetic is not True:
            return stop("SYNTHETIC_ONLY", "real data and account operation are outside this adapter")
        if account_policy is None or not isinstance(account_policy, TailPolicy):
            return stop("UNBOUND_ACCOUNT_POLICY", "a preset cannot create an account policy")
        if account_policy.approved is not True or account_policy.synthetic_policy is not True:
            return stop("UNBOUND_ACCOUNT_POLICY", "original artificial account policy not approved/bound")
        if level not in LEVELS or not isinstance(presets, Mapping) or set(presets) != set(LEVELS):
            raise ValueError("three explicit preset policies and a known level required")
        ratios = []
        for preset_level in LEVELS:
            preset = presets[preset_level]
            if not isinstance(preset, TailPolicy):
                raise ValueError("invalid preset policy")
            if preset.approved is not True or preset.synthetic_policy is not True or not preset.source.strip():
                return stop("UNBOUND_PRESET_POLICY", "preset approval/source is missing")
            if preset.descriptor != account_policy.descriptor or preset.descriptor != scenario_set.descriptor:
                raise ValueError("preset/account/scenario loss descriptor mismatch")
            ratio = _number(preset.budget, "preset budget", positive=True)
            if ratio > 1.0:
                raise ValueError("preset budget exceeds NAV fraction")
            ratios.append(ratio)
        if any(a > b for a, b in zip(ratios, ratios[1:])):
            raise ValueError("preset budgets are not ordered")
        account_cap = _number(account_policy.budget, "original account budget")
        nav = _number(decision_book.nav, "decision NAV", positive=True)
        factor = capital_protection_factor(curve)
        if curve.currency != decision_book.currency or curve.currency != account_policy.descriptor.currency:
            raise ValueError("capital currency mismatch")
        if curve.protected_capital < nav:
            raise ValueError("protected capital basis cannot be below current NAV")
        budget = min(ratios[LEVELS.index(level)] * factor, account_cap)
        if absolute_tail_loss_limit is not None:
            budget = min(budget, _number(absolute_tail_loss_limit, "absolute tail loss budget") / nav)
        if previous_tail_budget_ratio is not None:
            budget = min(budget, _number(previous_tail_budget_ratio, "previous tail budget ratio"))
        effective = replace(account_policy, budget=budget)
        decision = constrain_tail_target(
            proposal, approved_anchor, effective, scenario_set,
            decision_book=decision_book, bps_spec=bps_spec, hard_policy=hard_policy,
            forced_target=forced_target,
        )
        return {
            **header, "status": decision.status, "reason": decision.reason,
            "requested_level": level, "capital_factor": factor,
            "protected_capital_basis": curve.protected_capital,
            "effective_tail_budget_ratio": budget, "effective_tail_budget_amount": nav * budget,
            "loss_measure": "CVAR95_TWO_SESSION_DECISION_NAV_FRACTION",
            "policy_sources": {"preset": presets[level].source, "account": account_policy.source, "curve": curve.source},
            "decision": decision,
        }
    except (ValueError, TypeError, AttributeError, OverflowError) as exc:
        return stop("REJECTED_INPUT", str(exc))
