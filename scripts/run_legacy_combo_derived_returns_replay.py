"""Run the pinned, non-promotable legacy combo return replay."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from us_equity_strategies.research.legacy_combo_derived_returns_replay import (
    persist_report,
    run_pinned_legacy_combo_replay,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, default=Path("docs/research/us_equity_combo_backtest_20260628.json"))
    parser.add_argument("--output", type=Path, help="Optional create-only JSON report path")
    args = parser.parse_args()
    report = run_pinned_legacy_combo_replay(args.artifact.read_bytes())
    if args.output is not None:
        persist_report(report, args.output)
    else:
        print(json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
