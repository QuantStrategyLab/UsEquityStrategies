"""Research-only V7 reductions over the full RS-04 baseline rule adapter.

No entrypoint/production registration. Exposure here means long security risk
weights and 3x SOXL look-through, not a guarantee about losses or BOXX risk.
The final guard also bounds dollar risk when two books have different NAVs.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Mapping

from .rs04_b3_live import LiveRuleAdapter
from .rs04_b3_runner import Ledger
from ..v7_soxl_profile import V7_FROZEN_RUNTIME_CONFIG

CANDIDATE_ID = "soxl_v7_strict_derisk_only_v1"
RISK_FACTORS = {"SOXL": 3., "SOXX": 1., "SCHD": 1., "DGRO": 1., "SPYI": 1., "QQQI": 1.,
                "SGOV": 0., "BOXX": 0.}
TOLERANCE = 1e-10
# Preserve SOXX allocations, entry/exit rules, managed symbols, income, regime,
# options, AI and trade filters exactly. Only reviewed V7 reductions are copied.
OVERRIDES = {k: deepcopy(v) for k, v in V7_FROZEN_RUNTIME_CONFIG.items()
             if k.startswith("blend_gate_") and k not in {
                 "blend_gate_active_soxx_weight", "blend_gate_defensive_soxx_weight",
                 "blend_gate_trend_source"}}


def variant_config(baseline: Mapping) -> dict:
    if (baseline.get("income_layer_enabled") is not True
            or baseline.get("market_regime_control_enabled") is not True):
        raise ValueError("BASELINE_PROTECTION_REQUIRED")
    out = deepcopy(dict(baseline))
    out.update(deepcopy(OVERRIDES))
    for key in ("blend_gate_soxl_weight", "blend_gate_mid_soxl_weight"):
        value = float(baseline[key])
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("BASELINE_WEIGHT_INVALID")
        out[key] = min(value, float(out[key]))
    reserve = float(baseline.get("cash_reserve_ratio", 0.))
    if not math.isfinite(reserve) or not 0 <= reserve <= 1:
        raise ValueError("BASELINE_RESERVE_INVALID")
    out["cash_reserve_ratio"] = max(.03, reserve)
    # A pre-existing stricter dollar floor is never removed.
    return out


def _weights(value: Mapping) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise ValueError("BASELINE_OR_TARGET_MISSING")
    out = {}
    for symbol, weight in value.items():
        if symbol not in RISK_FACTORS:
            raise ValueError("UNMODELLED_SECURITY_EXPOSURE")
        if isinstance(weight, bool) or not isinstance(weight, (float, int)):
            raise ValueError("TARGET_INVALID")
        if not math.isfinite(weight) or weight < 0:
            raise ValueError("TARGET_INVALID")
        out[symbol] = float(weight)
    if math.fsum(out.values()) > 1+TOLERANCE:
        raise ValueError("UNFUNDED_TARGET")
    return out


def exposures(weights: Mapping) -> dict:
    w = _weights(weights)
    return {"risk_nominal": math.fsum(v for s, v in w.items() if RISK_FACTORS[s]),
            "effective": math.fsum(v*RISK_FACTORS[s] for s, v in w.items()),
            "security_nominal_including_cash_etfs": math.fsum(w.values())}


def assert_exposure(candidate: Mapping, baseline: Mapping, *,
                    baseline_nav: float = 1., candidate_nav: float = 1.) -> None:
    c, b = _weights(candidate), _weights(baseline)
    if any(not math.isfinite(v) or v <= 0 for v in (baseline_nav, candidate_nav)):
        raise ValueError("NAV_INVALID")
    # Component limits are stricter than a single aggregate cap: no SOXX or
    # income-security substitution can disguise an increase behind less SOXL.
    for symbol in set(c) | set(b):
        if RISK_FACTORS[symbol] and (
                c.get(symbol, 0.) > b.get(symbol, 0.)+TOLERANCE
                or c.get(symbol, 0.)*candidate_nav > b.get(symbol, 0.)*baseline_nav+1e-7):
            raise ValueError("EXPOSURE_INVARIANT_COMPONENT")
    ce, be = exposures(c), exposures(b)
    for key in ("risk_nominal", "effective", "security_nominal_including_cash_etfs"):
        if (ce[key] > be[key]+TOLERANCE
                or ce[key]*candidate_nav > be[key]*baseline_nav+1e-7):
            raise ValueError("EXPOSURE_INVARIANT_AGGREGATE")


def clamp_targets(proposed: Mapping, baseline: Mapping, *, baseline_nav: float = 1.,
                  candidate_nav: float = 1., reserve: float = .03) -> dict[str, float]:
    proposed, baseline = _weights(proposed), _weights(baseline)
    if (any(not math.isfinite(v) or v <= 0 for v in (baseline_nav, candidate_nav))
            or not math.isfinite(reserve) or not .03 <= reserve <= 1):
        raise ValueError("CAP_INPUT_INVALID")
    out, removed = dict(proposed), 0.
    for symbol, weight in proposed.items():
        if RISK_FACTORS[symbol]:
            limit = baseline.get(symbol, 0.)*min(1., baseline_nav/candidate_nav)
            out[symbol] = min(weight, limit)
            removed += weight-out[symbol]
    out["BOXX"] = out.get("BOXX", 0.)+removed
    # Never increase a risky target to spend spare cash. Preserve a stronger
    # baseline reserve, if any, in the caller's variant config.
    total = math.fsum(out.values())
    total_cap = min(1-reserve, math.fsum(baseline.values())*min(1., baseline_nav/candidate_nav))
    if total > total_cap:
        factor = total_cap/total
        out = {s: w*factor for s, w in out.items()}
    assert_exposure(out, baseline, baseline_nav=baseline_nav, candidate_nav=candidate_nav)
    return out


class VariantAdapter:
    def __init__(self, baseline_config: Mapping):
        self.config = variant_config(baseline_config)
        self.rule = LiveRuleAdapter("SOXL", self.config)

    def __call__(self, history, book, baseline_targets: Mapping) -> dict:
        # Caller supplies same-input baseline outputs. Replaying the baseline on
        # the candidate's wealth would conceal income-threshold path differences.
        return clamp_targets(self.rule(history, book), baseline_targets,
                             reserve=self.config["cash_reserve_ratio"])


def book_weights(book: Ledger, prices: Mapping) -> dict:
    nav = book.nav(prices)
    return {s: q*prices[s]/nav for s, q in book.quantity.items() if q}


def strict_rebalance(candidate: Ledger, proposed: Mapping, baseline: Ledger,
                     prices: Mapping, *, cost_multiplier: float,
                     reserve: float = .03) -> dict:
    """Fill-time guard in the synthetic ledger, then assert actual post-fee risk.

    Uses only contemporaneous fill prices and the paired baseline book, never a
    future return or high/low. This next-close notional sizing is a research
    approximation; intraday/broker execution parity is NOT established. A partial
    fill or gap leaving excess actual exposure raises and invalidates the path.
    """
    baseline_nav, candidate_nav = baseline.nav(prices), candidate.nav(prices)
    reference = book_weights(baseline, prices)
    targets = clamp_targets(proposed, reference, baseline_nav=baseline_nav,
                            candidate_nav=candidate_nav, reserve=reserve)
    fees = candidate.rebalance(targets, prices, profile="SOXL", cost_multiplier=cost_multiplier)
    actual_nav = candidate.nav(prices)
    assert_exposure(book_weights(candidate, prices), reference,
                    baseline_nav=baseline_nav, candidate_nav=actual_nav)
    if candidate.cash < reserve*actual_nav-1e-7:
        raise ValueError("CASH_RESERVE_INVARIANT")
    return fees


def paired_synthetic_replay(rows, baseline_config: Mapping, *, initial_nav: float = 100000.,
                            cost_multiplier: float = 1., synthetic: bool = True) -> tuple[dict, ...]:
    """Thin fixture driver over the shared RS-04 ledger, never a real-data runner.

    Each book consumes its own previous-close wealth/holdings, with t-1 signals
    and next-close fills. Every completed bar checks targets and actual holdings.
    No B1/B2 sizing is introduced. Raw point tapes must remain private in any
    future qualified cloud adapter. This function grants no real-data authority.
    """
    from .rs04_b3 import cap_targets
    if (synthetic is not True or len(rows) < 2 or not math.isfinite(initial_nav)
            or initial_nav <= 0 or [r.session for r in rows] != sorted({r.session for r in rows})):
        raise ValueError("SYNTHETIC_PAIRED_INPUT_INVALID")
    base_rule, variant = LiveRuleAdapter("SOXL", baseline_config), VariantAdapter(baseline_config)
    base, candidate = Ledger(initial_nav, {}, {}), Ledger(initial_nav, {}, {})
    prior = {"baseline": initial_nav, "candidate": initial_nav}
    points = []
    for i, row in enumerate(rows):
        bt = ct = {}
        if i:
            def snapshot(book, name):
                return {"cash": book.cash, "nav": prior[name], "quantity": dict(book.quantity),
                        "receivables": math.fsum(book.receivables.values())}
            bt = base_rule(rows[:i], snapshot(base, "baseline"))
            capped = cap_targets(bt, "SOXL")
            if any(bt[s] > capped.get(s, 0.)+TOLERANCE for s in bt):
                raise ValueError("BASELINE_CAP_BREACH")
            ct = variant(rows[:i], snapshot(candidate, "candidate"), bt)
            assert_exposure(ct, bt)
        for book in (base, candidate):
            for symbol, ratio in row.splits.items():
                book.split(symbol, ratio)
            for symbol, amount, pay_day in row.dividends:
                if pay_day < row.session:
                    raise ValueError("DIVIDEND_PAY_BEFORE_EX_DATE")
                book.accrue(symbol, amount, pay_day)
            book.pay(row.session)
        bf = base.rebalance(bt, row.prices, profile="SOXL", cost_multiplier=cost_multiplier)
        cf = strict_rebalance(candidate, ct, base, row.prices, cost_multiplier=cost_multiplier,
                              reserve=variant.config["cash_reserve_ratio"])
        point = {"session": row.session, "synthetic": True, "invariant": "PASS_POST_FILL_BAR"}
        for name, book, fees in (("baseline", base, bf), ("candidate", candidate, cf)):
            nav = book.nav(row.prices)
            point[name] = {"nav": nav, "cash": book.cash, "weights": book_weights(book, row.prices),
                           "net_return": nav/prior[name]-1, "fees": fees}
            prior[name] = nav
        points.append(point)
    return tuple(points)
