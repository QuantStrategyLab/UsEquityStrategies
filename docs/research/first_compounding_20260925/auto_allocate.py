"""Offline bounded budget searches over explicitly supplied joint scenarios.

This is a sleeve-level, linear-return mechanism check. It cannot validate the
full threshold/option-dependent SOXL or TQQQ builders.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import date
from pathlib import Path


def _number(value: object, name: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}: number required")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ValueError(f"{name}: finite value >= {minimum} required")
    return result


def allocate_single_member_budget(data: dict[str, object]) -> dict[str, object]:
    """One member plus cash, with old shares overnight and new shares intraday.

    This is a budget-selection approximation. The actual research replay must
    still call its frozen builder with the selected budget and execute whole
    shares against the next observed open; no return curve is rescaled here.
    """
    try:
        decision_date = date.fromisoformat(str(data["decision_date"]))
        nav = _number(data["nav_usd"], "nav_usd", minimum=0.01)
        cash = _number(data["cash_usd"], "cash_usd")
        receivable = _number(data["receivable_usd"], "receivable_usd")
        shares = _number(data["current_shares"], "current_shares")
        close = _number(data["current_close"], "current_close", minimum=0.01)
        exposure_ratio = _number(data["member_tqqq_exposure_ratio"], "member_tqqq_exposure_ratio")
        if exposure_ratio > 1 or abs(cash + receivable + shares * close - nav) > 1e-5:
            raise ValueError("account identity or exposure invalid")
        high_water = _number(data["observed_high_water_usd"], "observed_high_water_usd", minimum=nav)
        initial = _number(data["initial_principal_usd"], "initial_principal_usd", minimum=0.01)
        if high_water < initial:
            raise ValueError("high water below initial principal")
        reference = _number(data["wealth_reference_usd"], "wealth_reference_usd", minimum=0.01)
        if abs(reference - high_water) > 1e-6:
            raise ValueError("wealth reference must be observed high water")
        fee_bps = _number(data["security_trade_cost_bps"], "security_trade_cost_bps")
        stress_limit = _number(data["max_worst_scenario_loss_ratio"], "max_worst_scenario_loss_ratio")
        if stress_limit >= 1:
            raise ValueError("stress ratio must be below one")
        step = _number(data["search_step_usd"], "search_step_usd", minimum=0.01)
        curve = data["capital_curve"]
        if not isinstance(curve, dict) or set(curve) != {"a0_usd", "lower", "upper", "curvature"}:
            raise ValueError("capital curve fields invalid")
        from us_equity_strategies.research.c3_capital_path import smooth_bounded_capital_risk_ratio

        curve_result = smooth_bounded_capital_risk_ratio(
            capital=reference, a0=curve["a0_usd"], lower=curve["lower"],
            upper=curve["upper"], curvature=curve["curvature"],
        )
        cap = min(nav * float(curve_result["risk_ratio"]), cash + shares * close)
        if cap / step > 1000:
            raise ValueError("search exceeds bounded grid")
        raw_scenarios = data["scenarios"]
        if not isinstance(raw_scenarios, list) or len(raw_scenarios) != 60:
            raise ValueError("exactly 60 prior session scenarios required")
        scenarios = []
        previous_date = None
        for item in raw_scenarios:
            day = date.fromisoformat(str(item["date"]))
            if day > decision_date or (previous_date is not None and day <= previous_date):
                raise ValueError("scenario is future, duplicated or unordered")
            overnight = _number(item["overnight_open_to_prior_close"], "scenario.overnight", minimum=0.01)
            intraday = _number(item["intraday_close_to_open"], "scenario.intraday", minimum=0.01)
            dividend = _number(item["dividend_per_prior_close"], "scenario.dividend")
            scenarios.append((overnight, intraday, dividend))
            previous_date = day
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        return {"status": "DATA_INSUFFICIENT", "reason": str(exc)}

    best: tuple[float, float, float, float, float] | None = None
    tested = 0
    candidates = [i * step for i in range(int(cap / step) + 1)]
    if abs(candidates[-1] - cap) > 1e-8:
        candidates.append(cap)
    fee_rate = fee_bps / 10000.0
    for budget in candidates:
        outcomes = []
        fees = []
        for overnight, intraday, dividend in scenarios:
            simulated_open = close * overnight
            target_shares = math.floor(exposure_ratio * budget / simulated_open)
            fee = abs(target_shares - shares) * simulated_open * fee_rate
            post_trade_cash = cash + (shares - target_shares) * simulated_open - fee
            if post_trade_cash < -1e-8:
                break
            terminal_wealth = (
                post_trade_cash + target_shares * simulated_open * intraday
                + receivable + shares * close * dividend
            )
            if terminal_wealth <= 0 or not math.isfinite(terminal_wealth):
                break
            outcomes.append(terminal_wealth)
            fees.append(fee)
        if len(outcomes) != len(scenarios) or min(outcomes) < nav * (1 - stress_limit) - 1e-8:
            continue
        tested += 1
        objective = math.fsum(math.log(wealth / nav) for wealth in outcomes) / len(outcomes)
        expected_fee = math.fsum(fees) / len(fees)
        candidate = (objective, -expected_fee, -budget, budget, min(outcomes))
        if best is None or candidate[:3] > best[:3]:
            best = candidate
    if best is None:
        return {"status": "INFEASIBLE", "decision_date": decision_date.isoformat(),
                "reason": "no self-financing budget satisfies frozen scenario loss bound",
                "scenario_count": len(scenarios), "curve_budget_cap_usd": cap}
    objective, neg_fee, _, budget, worst = best
    return {"status": "MECHANISM_APPROXIMATION", "decision_date": decision_date.isoformat(),
            "observed_through": previous_date.isoformat(), "member_budget_usd": budget,
            "outer_cash_budget_usd": nav - receivable - budget,
            "unpaid_dividend_receivable_usd": receivable,
            "member_budget_ratio": budget / nav, "curve_budget_cap_usd": cap,
            "observed_high_water_usd": high_water, "wealth_reference_usd": reference,
            "scenario_count": len(scenarios), "feasible_grid_points": tested,
            "estimated_mean_log_growth": objective, "estimated_expected_trade_cost_usd": -neg_fee,
            "estimated_worst_scenario_loss_ratio": max(0.0, 1 - worst / nav),
            "method_limit": "Past paired daily scenarios choose one budget; next observed open, actual builder, shares and fees determine replay. Not an optimal future position."}


def allocate(data: dict[str, object]) -> dict[str, object]:
    """Return a bounded-grid solution or an explicit non-success status."""
    try:
        names = data["members"]
        if not isinstance(names, list) or len(names) != 2 or len(set(names)) != 2 or not all(isinstance(x, str) and x for x in names):
            raise ValueError("members: two distinct names required")
        decision = date.fromisoformat(str(data["decision_date"]))
        evidence = data["evidence"]
        if not isinstance(evidence, dict):
            raise ValueError("evidence: object required")
        source = evidence["source"]
        if source not in ("manual_scenarios", "historical_estimate"):
            raise ValueError("evidence.source: unsupported")
        observed = date.fromisoformat(str(evidence["observed_through"]))
        if observed > decision:
            raise ValueError("evidence observed after decision date")
        horizon = int(_number(evidence["horizon_days"], "horizon_days", minimum=1))
        if horizon != evidence["horizon_days"]:
            raise ValueError("horizon_days: integer required")
        nav = _number(data["nav_usd"], "nav_usd", minimum=0.01)
        current = data["current_usd"]
        locked = data["locked_usd"]
        if not isinstance(current, dict) or not isinstance(locked, dict):
            raise ValueError("current_usd and locked_usd: objects required")
        ids = [*names, "CASH"]
        if set(current) != set(ids) or set(locked) != set(ids):
            raise ValueError("current_usd and locked_usd: both members and CASH required")
        cur = {key: _number(current[key], f"current_usd.{key}") for key in ids}
        floor = {key: _number(locked[key], f"locked_usd.{key}") for key in ids}
        if abs(sum(cur.values()) - nav) > 0.01:
            raise ValueError("current holdings do not sum to NAV")
        if any(floor[key] > cur[key] + 0.01 for key in ids):
            raise ValueError("locked amount exceeds current holding")
        funding = data.get("settled_transfer_funding")
        free_cash = None
        if funding is not None:
            if (not isinstance(funding, dict) or set(funding) != {"version", "free_cash_usd"}
                    or funding["version"] != "settled_transfer_v1"
                    or not isinstance(funding["free_cash_usd"], dict)
                    or set(funding["free_cash_usd"]) != set(ids)):
                raise ValueError("settled_transfer_funding: complete settled_transfer_v1 required")
            free_cash = {key: _number(funding["free_cash_usd"][key], f"free_cash_usd.{key}")
                         for key in ids}
            if any(free_cash[key] > cur[key] - floor[key] + 0.01 for key in ids):
                raise ValueError("settled free cash exceeds unlocked current holding")
        fee_bps = _number(data["transfer_fee_bps"], "transfer_fee_bps")
        loss_limit = _number(data["stress_loss_limit_usd"], "stress_loss_limit_usd")
        policy = data.get("high_water_policy")
        wealth_floor = None
        if policy is not None:
            if not isinstance(policy, dict) or policy.get("version") != "synthetic_no_external_flow_v1":
                raise ValueError("high_water_policy: supported policy version required")
            principal = _number(policy["initial_principal_usd"], "initial_principal_usd", minimum=0.01)
            high_water = _number(policy["high_water_usd"], "high_water_usd", minimum=0.01)
            cumulative_limit = _number(policy["cumulative_loss_limit_usd"], "cumulative_loss_limit_usd")
            if high_water + 1e-9 < max(principal, nav) or cumulative_limit > high_water:
                raise ValueError("high_water_policy: inconsistent principal, NAV, high water or loss limit")
            wealth_floor = high_water - cumulative_limit
        curve_policy = data.get("capital_curve_policy")
        curve_result = None
        member_budget_cap = None
        if curve_policy is not None:
            required_curve_keys = {
                "version", "wealth_reference_usd", "a0_usd", "lower", "upper", "curvature"
            }
            if (not isinstance(curve_policy, dict)
                    or set(curve_policy) != required_curve_keys
                    or curve_policy["version"] != "explicit_reference_v1"):
                raise ValueError("capital_curve_policy: complete explicit_reference_v1 required")
            from us_equity_strategies.research.c3_capital_path import smooth_bounded_capital_risk_ratio

            curve_result = smooth_bounded_capital_risk_ratio(
                capital=_number(curve_policy["wealth_reference_usd"], "wealth_reference_usd", minimum=0.01),
                a0=_number(curve_policy["a0_usd"], "a0_usd", minimum=0.01),
                lower=_number(curve_policy["lower"], "curve.lower"),
                upper=_number(curve_policy["upper"], "curve.upper"),
                curvature=_number(curve_policy["curvature"], "curve.curvature", minimum=0.01),
            )
            member_budget_cap = nav * float(curve_result["risk_ratio"])
        fixed_weights = data.get("fixed_member_weights")
        if fixed_weights is not None:
            if (not isinstance(fixed_weights, dict) or set(fixed_weights) != set(names)):
                raise ValueError("fixed_member_weights: both members required")
            fixed_weights = {name: _number(fixed_weights[name], f"fixed_member_weights.{name}")
                             for name in names}
            if any(value > 1 for value in fixed_weights.values()) or sum(fixed_weights.values()) > 1 + 1e-9:
                raise ValueError("fixed_member_weights: total member weight must be <= 1")
            step = None
        else:
            step = _number(data["search_step_usd"], "search_step_usd", minimum=0.01)
            if nav / step > 1000:
                raise ValueError("search_step_usd: at most 1000 steps required")
        scenarios = evidence["scenarios"]
        if not isinstance(scenarios, list) or not scenarios:
            raise ValueError("evidence.scenarios: nonempty list required")
        parsed = []
        for item in scenarios:
            if not isinstance(item, dict) or set(item["returns"]) != set(names):
                raise ValueError("scenario returns: both members required")
            probability = _number(item["weight"], "scenario.weight")
            returns = [_number(item["returns"][name], f"return.{name}", minimum=-1.0) for name in names]
            parsed.append((probability, returns))
        if abs(sum(weight for weight, _ in parsed) - 1.0) > 1e-9:
            raise ValueError("scenario weights must sum to one")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        return {"status": "DATA_INSUFFICIENT", "reason": str(exc)}

    if sum(floor.values()) > nav + 0.01:
        return {"status": "INFEASIBLE", "reason": "actual locked holdings exceed current NAV", "locked_usd": floor}
    if wealth_floor is not None and nav < wealth_floor - 1e-9:
        return {"status": "INFEASIBLE", "reason": "current NAV has already breached the wealth floor", "wealth_floor_usd": wealth_floor}
    if member_budget_cap is not None and sum(floor[name] for name in names) > member_budget_cap + 1e-9:
        return {"status": "INFEASIBLE", "reason": "locked member holdings exceed curve member budget cap",
                "locked_usd": floor, "member_budget_cap_usd": round(member_budget_cap, 6)}

    # Candidate member budgets are end-of-transfer dollar holdings. Fees are
    # paid once from outer cash; neither locked holdings nor cash are sold.
    best = None
    tested = 0
    def consider(a: float, b: float) -> None:
        nonlocal best, tested
        if a < floor[names[0]] - 1e-9 or b < floor[names[1]] - 1e-9:
            return
        if member_budget_cap is not None and a + b > member_budget_cap + 1e-9:
            return
        fee = round(fee_bps / 10000 * (abs(a - cur[names[0]]) + abs(b - cur[names[1]])), 2)
        if free_cash is not None:
            releases = [max(0.0, cur[name] - target) for name, target in zip(names, (a, b))]
            if any(release > free_cash[name] + 1e-9 for name, release in zip(names, releases)):
                return
            inward = sum(max(0.0, target - cur[name]) for name, target in zip(names, (a, b)))
            if inward + fee > free_cash["CASH"] + sum(releases) + 1e-9:
                return
        cash = nav - a - b - fee
        if cash < floor["CASH"] - 1e-9:
            return
        wealth = [cash + a * (1 + r[0]) + b * (1 + r[1]) for _, r in parsed]
        if min(wealth) <= 0:
            return
        if wealth_floor is not None and min(wealth) < wealth_floor - 1e-9:
            return
        stress_loss = max(0.0, nav - min(wealth))
        if stress_loss > loss_limit + 1e-9:
            return
        objective = sum(weight * math.log(value / nav) for (weight, _), value in zip(parsed, wealth))
        tested += 1
        candidate = (objective, -fee, -a, -b, a, b, cash, fee, stress_loss, wealth)
        if best is None or candidate[:4] > best[:4]:
            best = candidate

    # An actual holding need not coincide with the search grid. Keeping it is
    # always a candidate unless the supplied stress policy itself rules it out.
    if fixed_weights is not None:
        consider(nav * fixed_weights[names[0]], nav * fixed_weights[names[1]])
    else:
        if any(abs(cur[name] / step - round(cur[name] / step)) > 1e-9 for name in names):
            consider(cur[names[0]], cur[names[1]])
        for i in range(int(nav / step) + 1):
            a = i * step
            if a < floor[names[0]] - 1e-9:
                continue
            for j in range(int((nav - a) / step) + 1):
                consider(a, j * step)
    if best is None:
        reason = ("fixed member weights violate funding or risk constraints" if fixed_weights is not None
                  else "no grid point satisfies actual locked holdings, cash, fees and risk floors")
        if free_cash is not None:
            reason += " and settled transfer funding"
        return {"status": "INFEASIBLE", "reason": reason, "locked_usd": floor, "stress_loss_limit_usd": loss_limit, "wealth_floor_usd": wealth_floor}
    objective, _, _, _, a, b, cash, fee, loss, wealth = best
    binding = []
    tolerance = max(0.01, (step or 0.0) * 0.02)
    if abs(loss - loss_limit) <= tolerance:
        binding.append("stress_loss_limit_usd")
    if wealth_floor is not None and abs(min(wealth) - wealth_floor) <= tolerance:
        binding.append("wealth_floor_usd")
    if member_budget_cap is not None and abs(a + b - member_budget_cap) <= tolerance:
        binding.append("member_budget_cap_usd")
    for key, value in zip(ids, (a, b, cash)):
        if abs(value - floor[key]) <= tolerance:
            binding.append(f"locked_usd.{key}")
    method_limit = (
        "Linear sleeve returns and explicitly fixed member weights; objective is reported, not optimized. Thresholds, income targets, integer option contracts, internal stock/option funding and state transitions are not replayed. No full-candidate or real-optimal claim."
        if fixed_weights is not None else
        "Linear sleeve returns and grid search; thresholds, income targets, integer option contracts, internal stock/option funding and state transitions are not replayed. No full-candidate or real-optimal claim."
    )
    result = {
        "status": "MECHANISM_APPROXIMATION",
        # Preserve marked NAV precision in research proposals; cent rounding
        # belongs to a later executable transfer contract.
        "budget_usd": {names[0]: round(a, 6), names[1]: round(b, 6), "CASH": round(cash, 6)},
        "fee_usd": round(fee, 2),
        "funding_check_usd": round(a + b + cash + fee, 6),
        "estimated_weighted_log_growth": objective,
        "stress_loss_usd": round(loss, 2),
        "stress_loss_ratio": loss / nav,
        "stress_loss_limit_usd": loss_limit,
        "wealth_floor_usd": wealth_floor,
        "high_water_policy": policy,
        "scenario_terminal_wealth_usd": [round(x, 2) for x in wealth],
        "binding_or_near_binding": binding,
        "feasible_grid_points": tested,
        "search_step_usd": step,
        "evidence_source": source,
        "horizon_days": horizon,
        "decision_date": decision.isoformat(),
        "method_limit": method_limit,
        "weight_meaning": "Manual scenario weights are objective weights, not estimated future probabilities." if source == "manual_scenarios" else "Historical estimate; validity requires separate time-ordered replay.",
    }
    if curve_result is not None:
        result.update(
            capital_curve_policy=curve_policy,
            member_budget_cap_usd=round(member_budget_cap, 6),
            member_budget_cap_role="max_current_member_budgets_including_internal_cash",
            reference_curve_risk_capital_usd=round(float(curve_result["risk_capital"]), 6),
            reference_curve_risk_capital_role="not_current_available_funds",
        )
    if free_cash is not None:
        result.update(
            settled_transfer_funding_version="settled_transfer_v1",
            free_cash_usd=free_cash,
            funding_scope="settled outer and member cash transfers only; builder trades and future settlements require replay",
        )
    if fixed_weights is not None:
        result.update(allocation_mode="fixed_member_weights_v1",
                      fixed_member_weights=fixed_weights,
                      feasible_grid_points=None, search_step_usd=None)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = allocate(json.loads(args.input.read_text(encoding="utf-8")))
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
