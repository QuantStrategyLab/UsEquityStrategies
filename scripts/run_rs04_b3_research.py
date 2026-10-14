#!/usr/bin/env python3
"""Explicit synthetic RS-04 B3 entry point. No provider or live registration."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from us_equity_strategies.research.rs04_b3_config import CONFIG, config_identity
from us_equity_strategies.research.rs04_b3_runner import paired_giveback, replay, synthetic_sessions


def synthetic_report() -> dict:
    rows, results = synthetic_sessions(), {}
    for profile in ("SOXL", "TQQQ"):
        def invented_rule(history, book):
            # Fixed invented control for engineering tests, NOT live rules.
            if profile == "SOXL":
                return {"SOXL": .679, "SOXX": .194, "BOXX": .097}
            return {"TQQQ": .45, "QQQM": .45, "BOXX": .08}
        results[profile] = {}
        for cost in CONFIG.cost_multipliers:
            baseline = replay(rows, invented_rule, profile=profile, mode="baseline", kelly_full=4., cost_multiplier=cost)
            sizing_returns = [p["net_return"] for p in baseline.points]
            paths = {"baseline": baseline}
            for mode in ("sizing_control", "b3"):
                paths[mode] = replay(rows, invented_rule, profile=profile, mode=mode, kelly_full=4.,
                                     sizing_returns=sizing_returns, cost_multiplier=cost)
            results[profile][str(cost)] = {mode: path.metrics for mode, path in paths.items()}
            results[profile][str(cost)]["paired_giveback"] = paired_giveback(
                sizing_returns, [p["net_return"] for p in paths["b3"].points])
    return {"status": "SYNTHETIC_ENGINEERING_ONLY", "synthetic": True, "research_only": True,
            "live_ready": False, "promotion_eligible": False, "no_order": True, "budget_effect": "NONE",
            "config_sha256": config_identity(), "registration": asdict(CONFIG), "results": results,
            "acceptance": "NOT_EVALUATED_SYNTHETIC_ONLY", "recommend_shadow": False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--synthetic", action="store_true")
    modes.add_argument("--cloud-preflight", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.synthetic:
        if args.execute:
            parser.error("--execute applies only to cloud preflight")
        result = synthetic_report()
    else:
        from us_equity_strategies.research.rs04_b3_cloud import workflow_cloud_report
        result = workflow_cloud_report(args.execute)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2, allow_nan=False)+"\n")
    print(json.dumps({"status": result["status"], "config_sha256": result["config_sha256"]}))
    return 2 if result["status"] == "PARKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
