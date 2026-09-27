"""Account for BOXX comparison P/L using private ledgers and raw closes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(root: Path) -> dict:
    source = root / "boxx_outer_cash_summary.v1.json"
    summary = json.loads(source.read_text())
    policy = json.loads((root / "boxx_outer_cash_policy.v1.json").read_text())
    if _sha(root / "manifest.json") != summary["source_manifest_sha256"] or _sha(root / "boxx_outer_cash_policy.v1.json") != summary["policy_sha256"]:
        raise ValueError("FROZEN_SOURCE_CHANGED")
    prices = {}
    dividends = {}
    for symbol in ("TQQQ", "BOXX"):
        bar_path = root / f"bars/{symbol}/page-001.json"
        action_path = root / f"actions/{symbol}/page-001.json"
        if _sha(bar_path) != summary["input_sha256"][f"bars/{symbol}/page-001.json"] or _sha(action_path) != summary["input_sha256"][f"actions/{symbol}/page-001.json"]:
            raise ValueError("ATTRIBUTION_SOURCE_CHANGED")
        prices[symbol] = {x["t"][:10]: x for x in json.loads(bar_path.read_text())["bars"]}
        dividends[symbol] = {}
        for event in json.loads(action_path.read_text())["corporate_actions"].get("cash_dividends", []):
            day = event["ex_date"]
            dividends[symbol][day] = dividends[symbol].get(day, 0.0) + float(event["rate"])
    days = sorted(set(prices["TQQQ"]) & set(prices["BOXX"]))
    previous_day = {days[i]: days[i - 1] for i in range(1, len(days))}
    detail = {}
    for cost in policy["cost_bps"]:
        for group in ("fixed", "automatic"):
            for parking in ("cash", "boxx"):
                name = f"{group}_{parking}_{cost}bps"
                path = root / f"private_daily_{name}.json"
                if _sha(path) != summary["private_ledger_sha256"][name]:
                    raise ValueError("ATTRIBUTION_LEDGER_CHANGED")
                ledger = json.loads(path.read_text())
                totals = {"tqqq_price_and_dividend_pnl_usd": 0.0,
                          "boxx_price_and_dividend_pnl_usd": 0.0,
                          "total_cost_usd": 0.0}
                previous_shares = {"TQQQ": 0.0, "BOXX": 0.0}
                for row in ledger:
                    day = row["date"]
                    prior = previous_day[day]
                    for symbol in ("TQQQ", "BOXX"):
                        key = symbol.lower()
                        bar = prices[symbol][day]
                        pnl = previous_shares[symbol] * (bar["c"] - prices[symbol][prior]["c"])
                        pnl += row[f"{key}_trade_shares"] * (bar["c"] - bar["o"])
                        pnl += previous_shares[symbol] * dividends[symbol].get(day, 0.0)
                        totals[f"{key}_price_and_dividend_pnl_usd"] += pnl
                        previous_shares[symbol] = row[f"{key}_shares"]
                    totals["total_cost_usd"] += row["total_cost_usd"]
                predicted = policy["initial_nav_usd"] + totals["tqqq_price_and_dividend_pnl_usd"] + totals["boxx_price_and_dividend_pnl_usd"] - totals["total_cost_usd"]
                if abs(predicted - ledger[-1]["nav_close"]) > 1e-6:
                    raise ValueError("ATTRIBUTION_DOES_NOT_RECONCILE:" + name)
                detail[name] = totals
    paired = {}
    for cost in policy["cost_bps"]:
        for group in ("fixed", "automatic"):
            cash = detail[f"{group}_cash_{cost}bps"]
            boxx = detail[f"{group}_boxx_{cost}bps"]
            b = boxx["boxx_price_and_dividend_pnl_usd"] - cash["boxx_price_and_dividend_pnl_usd"]
            t = boxx["tqqq_price_and_dividend_pnl_usd"] - cash["tqqq_price_and_dividend_pnl_usd"]
            fees = boxx["total_cost_usd"] - cash["total_cost_usd"]
            difference = summary["outcomes"][f"{group}_boxx_{cost}bps"]["end_nav_usd"] - summary["outcomes"][f"{group}_cash_{cost}bps"]["end_nav_usd"]
            if abs(b + t - fees - difference) > 1e-6:
                raise ValueError("PAIR_DIFFERENCE_DOES_NOT_RECONCILE")
            paired[f"{group}_{cost}bps"] = {"boxx_gross_pnl_usd": b,
                                            "tqqq_path_pnl_difference_usd": t,
                                            "incremental_trade_cost_usd": fees,
                                            "net_end_nav_difference_usd": difference}
    result = {"source_summary_sha256": _sha(source), "policy_sha256": summary["policy_sha256"],
              "method": "Old shares times raw close change plus opening trades times intraday change plus ex-date dividend accrual, less both ETF trade costs.",
              "paths": detail, "paired_differences": paired,
              "limit": "Accounting decomposition; TQQQ path includes NAV and budget feedback. It does not isolate a counterfactual pure cash-yield effect for automatic BOXX."}
    out = root / "boxx_outer_cash_attribution.v1.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    out.chmod(0o600)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.private_root)
    print(json.dumps({"paired_differences": result["paired_differences"],
                      "attribution_sha256": _sha(args.private_root / "boxx_outer_cash_attribution.v1.json")}, indent=2))
