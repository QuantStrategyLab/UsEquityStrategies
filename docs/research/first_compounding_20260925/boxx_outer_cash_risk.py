"""Descriptive account risk from the frozen BOXX comparison daily ledgers."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
from datetime import date
from pathlib import Path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _number(value: object, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field}: finite number required")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(f"{field}: finite {'positive ' if positive else ''}number required")
    return result


def _metrics(
    rows: object, initial_nav: float, *, balance_components_complete: bool = True,
) -> tuple[dict, tuple[str, ...], tuple[float, ...]]:
    if not isinstance(rows, list) or len(rows) < 2:
        raise ValueError("at least two daily ledger rows required")
    previous_nav = initial_nav
    previous_date = None
    peak = initial_nav
    max_drawdown = 0.0
    dates: list[str] = []
    returns: list[float] = []
    costs: list[float] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("daily ledger row must be an object")
        day = date.fromisoformat(str(row["date"]))
        if previous_date is not None and day <= previous_date:
            raise ValueError("daily ledger dates must be unique and increasing")
        nav = _number(row["nav_close"], "nav_close", positive=True)
        cost = _number(row["total_cost_usd"], "total_cost_usd")
        if cost < 0 or _number(row.get("external_flow_usd", 0), "external_flow_usd") != 0:
            raise ValueError("negative cost or external flow is outside the frozen policy")
        components = [_number(row[key], key) for key in (
            "tqqq_value_usd", "boxx_value_usd", "settled_cash_usd",
            "pending_sale_usd", "receivable_usd",
        )]
        if any(value < 0 for value in components):
            raise ValueError("negative account component")
        parts = math.fsum(components)
        if ((balance_components_complete and abs(parts - nav) > 1e-6)
                or abs(_number(row["identity_error_usd"], "identity_error_usd")) > 1e-6):
            raise ValueError("daily account identity does not reconcile")
        dates.append(day.isoformat())
        returns.append(nav / previous_nav - 1.0)
        costs.append(cost)
        peak = max(peak, nav)
        max_drawdown = min(max_drawdown, nav / peak - 1.0)
        previous_nav = nav
        previous_date = day
    n = len(returns)
    k = math.ceil(0.05 * n)
    metrics = {
        "sessions": n,
        "first_date": dates[0],
        "last_date": dates[-1],
        "end_nav_usd": previous_nav,
        "annualized_historical_daily_volatility_252": statistics.stdev(returns) * math.sqrt(252),
        "historical_worst_5pct_daily_return_mean": math.fsum(sorted(returns)[:k]) / k,
        "tail_observations": k,
        "actual_tail_fraction": k / n,
        "worst_daily_return": min(returns),
        "max_drawdown": max_drawdown,
        "total_cost_usd": math.fsum(costs),
        "exported_balance_components_reconciled": balance_components_complete,
    }
    return metrics, tuple(dates), tuple(returns)


def analyze(root: Path) -> dict:
    summary_path = root / "boxx_outer_cash_summary.v1.json"
    policy_path = root / "boxx_outer_cash_policy.v1.json"
    summary = json.loads(summary_path.read_text())
    policy = json.loads(policy_path.read_text())
    if (_sha(root / "manifest.json") != summary["source_manifest_sha256"]
            or _sha(policy_path) != summary["policy_sha256"]):
        raise ValueError("frozen manifest or policy changed")
    if summary["development"] is not True or policy["development"] is not True:
        raise ValueError("development identity required")
    initial_nav = _number(policy["initial_nav_usd"], "initial_nav_usd", positive=True)
    outcomes = summary["outcomes"]
    hashes = summary["private_ledger_sha256"]
    if set(outcomes) != set(hashes) or len(outcomes) != 18:
        raise ValueError("frozen 18-path comparison required")
    paths: dict[str, dict] = {}
    all_dates = None
    return_paths: dict[str, tuple[float, ...]] = {}
    for name in sorted(outcomes):
        path = root / f"private_daily_{name}.json"
        if _sha(path) != hashes[name]:
            raise ValueError(f"frozen ledger changed: {name}")
        # The frozen QQQM reference ledger stores NAV but omits the QQQM
        # position value from its exported component fields. Do not claim an
        # independent component reconciliation for that reference path.
        metrics, dates, daily_returns = _metrics(
            json.loads(path.read_text()), initial_nav,
            balance_components_complete=not name.startswith("qqqm_buy_hold_"),
        )
        if all_dates is None:
            all_dates = dates
        elif dates != all_dates:
            raise ValueError("comparison paths have different dates")
        expected = outcomes[name]
        if (metrics["sessions"] != expected["sessions"]
                or metrics["first_date"] != expected["start"]
                or metrics["last_date"] != expected["end"]
                or not math.isclose(metrics["end_nav_usd"], expected["end_nav_usd"], rel_tol=0, abs_tol=1e-6)
                or not math.isclose(metrics["max_drawdown"], expected["max_drawdown"], rel_tol=0, abs_tol=1e-9)
                or not math.isclose(metrics["total_cost_usd"], expected["total_cost_usd"], rel_tol=0, abs_tol=1e-6)):
            raise ValueError(f"risk inputs disagree with frozen outcome: {name}")
        paths[name] = metrics
        return_paths[name] = daily_returns
    pairs = {}
    for cost in policy["cost_bps"]:
        for budget in ("fixed", "automatic"):
            cash_name = f"{budget}_cash_{cost}bps"
            boxx_name = f"{budget}_boxx_{cost}bps"
            cash = paths[cash_name]
            boxx = paths[boxx_name]
            pairs[f"{budget}_{cost}bps"] = {
                "boxx_minus_cash_volatility": boxx["annualized_historical_daily_volatility_252"] - cash["annualized_historical_daily_volatility_252"],
                "boxx_minus_cash_tail_mean": boxx["historical_worst_5pct_daily_return_mean"] - cash["historical_worst_5pct_daily_return_mean"],
                "boxx_minus_cash_max_drawdown": boxx["max_drawdown"] - cash["max_drawdown"],
                "boxx_minus_cash_end_nav_usd": boxx["end_nav_usd"] - cash["end_nav_usd"],
                "boxx_lower_daily_return_days": sum(a < b for a, b in zip(return_paths[boxx_name], return_paths[cash_name])),
            }
    return {
        "schema": "qsl.research.boxx_outer_cash_descriptive_risk.v1",
        "development": True,
        "source_summary_sha256": _sha(summary_path),
        "policy_sha256": _sha(policy_path),
        "sessions": len(all_dates),
        "first_date": all_dates[0],
        "last_date": all_dates[-1],
        "method": "Daily close NAV return includes first session versus initial NAV; sample daily standard deviation times sqrt(252). Tail mean averages the worst ceil(0.05*n) signed daily returns. Drawdown peak includes initial NAV. No external flows in the frozen policy.",
        "limitations": "Descriptive seen-development account risk only. Tail fraction is ceil(0.05*n)/n, not exactly 5%; neither a forecast CVaR nor a future loss limit. The frozen QQQM buy-and-hold export omits QQQM position value, so its reported NAV and producer identity flag are hash-bound but exported components cannot independently reconcile. BOXX-minus-cash differences include member path feedback and are not isolated BOXX causal effects. One strategy member plus outer BOXX does not establish inter-strategy correlation or full-v2 risk.",
        "paths": paths,
        "paired_differences": pairs,
    }


def run(root: Path) -> dict:
    result = analyze(root)
    out = root / "boxx_outer_cash_risk.v1.json"
    descriptor = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.private_root)
    print(json.dumps({"schema": result["schema"], "source_summary_sha256": result["source_summary_sha256"],
                      "sessions": result["sessions"], "output_sha256": _sha(args.private_root / "boxx_outer_cash_risk.v1.json")}, indent=2))
