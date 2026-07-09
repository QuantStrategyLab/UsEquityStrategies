#!/usr/bin/env python3
"""Generic US orchestrator research entrypoint (task 3c)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_walk_forward_backtest import run_walk_forward  # noqa: E402
from us_equity_strategies.backtest.orchestrator_runner import SUPPORTED_PROFILES, build_backtest_runner  # noqa: E402
from us_equity_strategies.strategies.global_etf_rotation import DEFAULT_MIN_HISTORY_DAYS, PROFILE_NAME  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="US orchestrator research backtest.")
    parser.add_argument("--profile", default=PROFILE_NAME)
    parser.add_argument("--list-profiles", action="store_true")
    parser.add_argument("--mode", choices=("single", "walk_forward"), default="walk_forward")
    parser.add_argument("--synthetic-days", type=int, default=900)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()

    if args.list_profiles:
        print(json.dumps({"profiles": sorted(SUPPORTED_PROFILES)}, indent=2))
        return 0

    if args.mode == "walk_forward":
        payload = run_walk_forward(profile=args.profile, synthetic_days=args.synthetic_days)
    else:
        runner = build_backtest_runner(args.profile, synthetic_days=args.synthetic_days)
        params = {"min_history_days": DEFAULT_MIN_HISTORY_DAYS}
        if args.profile == "us_equity_combo":
            params["combo_mode"] = "dynamic"
        result = runner.run(args.profile, params)
        payload = {
            "profile": args.profile,
            "metrics": {
                "sharpe_ratio": result.sharpe_ratio,
                "max_drawdown": result.max_drawdown,
                "cagr": result.cagr,
            },
            "source": type(runner).__name__,
        }

    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    if args.json_output:
        args.json_output.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
