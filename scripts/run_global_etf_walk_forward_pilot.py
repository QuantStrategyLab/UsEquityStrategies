#!/usr/bin/env python3
"""Pilot wrapper — delegates to run_walk_forward_backtest.py (task 3c)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_walk_forward_backtest import run_walk_forward  # noqa: E402
from us_equity_strategies.strategies.global_etf_rotation import PROFILE_NAME  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="US global ETF walk-forward pilot (compat wrapper).")
    parser.add_argument("--output", type=Path, default=Path("us_global_etf_walk_forward_pilot.json"))
    parser.add_argument("--synthetic-days", type=int, default=900)
    args = parser.parse_args()
    payload = run_walk_forward(profile=PROFILE_NAME, synthetic_days=args.synthetic_days)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
