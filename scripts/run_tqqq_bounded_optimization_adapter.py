#!/usr/bin/env python3
"""Publish the TQQQ bounded-optimization identity package."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from us_equity_strategies.research.tqqq_bounded_optimization_adapter import (
    BoundedOptimizationAdapterError,
    run_verified_current_r3_adapter,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_root", type=Path)
    args = parser.parse_args(argv)
    try:
        package_id = run_verified_current_r3_adapter(args.output_root)
    except BoundedOptimizationAdapterError as exc:
        print(f"TQQQ bounded optimization adapter failed: {exc.code}", file=sys.stderr)
        return 2
    print(json.dumps({"package_id": package_id, "terminal_status": "REJECT_ALL_SIZE_ZERO"}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
