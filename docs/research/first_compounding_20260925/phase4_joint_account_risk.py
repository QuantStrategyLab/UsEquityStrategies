"""Descriptive account risk from the six frozen Phase 4 v3 ledgers."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
from datetime import date
from pathlib import Path

SUMMARY_SHA = "32e812ee0f38461f74f10975d32003db496b81e9232be7bd5f6be6b8459c9793"
PATHS = ("enhanced", "matched_defense_baseline")
COSTS = (5, 10, 15)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _path_metrics(rows: list[dict], initial: float) -> tuple[dict, tuple[str, ...], tuple[float, ...]]:
    if len(rows) != 444:
        raise ValueError("PHASE4_RISK_WINDOW_CHANGED")
    previous_nav = initial
    previous_day = None
    peak = initial
    drawdown = 0.0
    dates = []
    returns = []
    total_cost = 0.0
    for row in rows:
        day = date.fromisoformat(row["date"])
        if previous_day is not None and day <= previous_day:
            raise ValueError("PHASE4_RISK_DATE_ORDER_INVALID")
        nav = float(row["economic_nav_close_usd"])
        values = [float(row[key]) for key in (
            "qqqm_value_usd", "tqqq_value_usd", "boxx_value_usd",
            "settled_cash_usd", "pending_sale_usd", "receivable_usd")]
        cost = float(row["total_cost_usd"])
        trade_cost_parts = [float(value) for value in row["trade_cost_usd"].values()]
        trade_cost = math.fsum(trade_cost_parts)
        if (not math.isfinite(nav) or nav <= 0
                or any(not math.isfinite(value) or value < 0 for value in values)
                or not math.isfinite(cost) or cost < 0
                or any(not math.isfinite(value) or value < 0 for value in trade_cost_parts)
                or abs(trade_cost - cost) > 1e-6
                or abs(math.fsum(values) - nav) > 1e-6
                or abs(float(row["account_identity_error_usd"])) > 1e-6
                or float(row.get("external_flow_usd", 0)) != 0):
            raise ValueError("PHASE4_RISK_ACCOUNT_INVALID")
        dates.append(day.isoformat())
        returns.append(nav / previous_nav - 1)
        total_cost += cost
        peak = max(peak, nav)
        drawdown = min(drawdown, nav / peak - 1)
        previous_nav = nav
        previous_day = day
    if dates[0] != "2023-03-28" or dates[-1] != "2024-12-31":
        raise ValueError("PHASE4_RISK_WINDOW_CHANGED")
    tail_count = math.ceil(0.05 * len(returns))
    five_day = [(math.prod(1 + r for r in returns[i:i + 5]) - 1, i)
                for i in range(len(returns) - 4)]
    worst_five_return, worst_start = min(five_day)
    return ({"annualized_historical_daily_volatility_252": statistics.stdev(returns) * math.sqrt(252),
             "historical_worst_5pct_daily_return_mean": math.fsum(sorted(returns)[:tail_count]) / tail_count,
             "tail_observations": tail_count,
             "worst_daily_return": min(returns),
             "worst_observed_five_session_compounded_return": worst_five_return,
             "worst_five_session_start": dates[worst_start],
             "worst_five_session_end": dates[worst_start + 4],
             "max_drawdown": drawdown,
             "end_nav_usd": previous_nav,
             "total_cost_usd": total_cost}, tuple(dates), tuple(returns))


def analyze(root: Path) -> dict:
    folder = root / "phase4_fixed_pair_v3"
    source = folder / "phase4_comparison_summary.v3.json"
    if _sha(source) != SUMMARY_SHA:
        raise ValueError("PHASE4_RISK_SUMMARY_CHANGED")
    summary = json.loads(source.read_text())
    if summary["development"] is not True or summary["research_only"] is not True:
        raise ValueError("PHASE4_RISK_IDENTITY_CHANGED")
    if set(summary["private_ledger_sha256"]) != {f"{path}_{cost}bps" for path in PATHS for cost in COSTS}:
        raise ValueError("PHASE4_RISK_PATH_SET_CHANGED")
    results = {}
    dates_by_path = {}
    returns_by_path = {}
    for cost in COSTS:
        for path in PATHS:
            name = f"{path}_{cost}bps"
            ledger = folder / f"private_daily_{name}.json"
            if _sha(ledger) != summary["private_ledger_sha256"][name]:
                raise ValueError("PHASE4_RISK_LEDGER_CHANGED")
            expected = summary["results"][name]
            metrics, dates, returns = _path_metrics(json.loads(ledger.read_text()), expected["initial_nav_usd"])
            if (not math.isclose(metrics["end_nav_usd"], expected["end_nav_usd"], abs_tol=1e-6, rel_tol=0)
                    or not math.isclose(metrics["max_drawdown"], expected["max_drawdown"], abs_tol=1e-9, rel_tol=0)
                    or not math.isclose(metrics["total_cost_usd"], expected["total_cost_usd"], abs_tol=1e-6, rel_tol=0)):
                raise ValueError("PHASE4_RISK_RESULT_MISMATCH")
            results[name] = metrics
            dates_by_path[name] = dates
            returns_by_path[name] = returns
    if len(set(dates_by_path.values())) != 1:
        raise ValueError("PHASE4_RISK_DATES_MISMATCH")
    paired = {}
    for cost in COSTS:
        enhanced = f"enhanced_{cost}bps"
        baseline = f"matched_defense_baseline_{cost}bps"
        paired[f"{cost}bps"] = {
            "account_daily_return_correlation": statistics.correlation(
                returns_by_path[enhanced], returns_by_path[baseline]),
            "enhanced_minus_baseline_volatility": results[enhanced]["annualized_historical_daily_volatility_252"]
            - results[baseline]["annualized_historical_daily_volatility_252"],
            "enhanced_minus_baseline_tail_mean": results[enhanced]["historical_worst_5pct_daily_return_mean"]
            - results[baseline]["historical_worst_5pct_daily_return_mean"],
        }
    return {"schema": "qsl.research.phase4_joint_account_descriptive_risk.v1",
            "development": True, "research_only": True,
            "source_phase4_summary_sha256": SUMMARY_SHA,
            "sessions": 444, "first_date": "2023-03-28", "last_date": "2024-12-31",
            "method": "Close-to-close economic account NAV returns, including first close versus initial NAV; sample volatility x sqrt(252); signed mean of worst ceil(5% x 444)=23 daily returns; worst observed compounded five-session span. No missing sessions or external flows.",
            "limits": "Ex-post seen-development account statistics. The 23-day mean is neither exact-quantile nor forecast CVaR. Five-session span is observed inside this window, not an unseen-crisis stress test. Pair correlation is between two policies with shared assets, not between TQQQ and another strategy member. No risk limit, budget choice or new replay.",
            "paths": results, "paired": paired}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.parent != args.private_root / "phase4_fixed_pair_v3":
        raise ValueError("PHASE4_RISK_OUTPUT_OUTSIDE_PRIVATE_ROOT")
    result = analyze(args.private_root)
    data = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
    print(json.dumps({"paths": len(result["paths"]), "summary_sha256": hashlib.sha256(data).hexdigest()}, sort_keys=True))
