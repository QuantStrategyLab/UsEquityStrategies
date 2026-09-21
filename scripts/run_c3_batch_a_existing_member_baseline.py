#!/usr/bin/env python3
"""Run Batch A existing-member C3 baseline gate (research/shadow only).

Default behavior: assess private R3 readiness and return PARKED when a frozen
C3-comparable SOXL/TQQQ/cash member pack is absent.  Never invents returns,
never touches broker/credentials/workflows, and never authorizes execution.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from us_equity_strategies.research.c3_batch_a_existing_member_baseline import (
    evaluate_batch_a_existing_member_baselines,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--private-root",
        type=Path,
        default=None,
        help="Mounted root for locked private R3 inputs (optional).",
    )
    parser.add_argument(
        "--frozen-member-pack",
        type=Path,
        default=None,
        help=(
            "Optional JSON pack with aligned SOXL/TQQQ/cash returns and C2 "
            "comparability fields. Omitted → honest PARKED."
        ),
    )
    args = parser.parse_args(argv)
    pack = None
    if args.frozen_member_pack is not None:
        pack = json.loads(args.frozen_member_pack.read_text(encoding="utf-8"))
    kwargs: dict[str, object] = {}
    if args.private_root is not None:
        kwargs["private_root"] = args.private_root
    if pack is not None:
        kwargs["frozen_member_pack"] = pack
    result = evaluate_batch_a_existing_member_baselines(**kwargs)
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    if result.get("status") == "READY_RESEARCH_ONLY" and result.get("batch_a_accepted") is True:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
