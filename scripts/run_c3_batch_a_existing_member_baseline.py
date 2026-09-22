#!/usr/bin/env python3
"""Run Batch A existing-member C3 baseline gate (research/shadow only).

Default behavior: return PARKED when a frozen v2 C3-comparable SOXL/TQQQ/cash
member pack is absent.  Never invents returns, never touches
broker/credentials/workflows, never authorizes execution, and does not use
legacy R3 ``--private-root`` as an active Batch A entry.
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
        "--frozen-member-pack",
        type=Path,
        default=None,
        help=(
            "Optional JSON pack (qsl.c3-batch-a-frozen-member-pack.v2) with "
            "aligned SOXL/TQQQ/cash strategy returns. Omitted → honest PARKED."
        ),
    )
    parser.add_argument(
        "--private-root",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,  # legacy flag retained only to emit a clear park reason
    )
    args = parser.parse_args(argv)
    if args.private_root is not None and args.frozen_member_pack is None:
        result = {
            "schema_version": "qsl.c3-batch-a-existing-member-baseline-research.v1",
            "status": "PARKED",
            "batch_a_accepted": False,
            "reason_codes": [
                "BATCH_A_LEGACY_R3_PRIVATE_ROOT_REJECTED",
                "BATCH_A_C3_MEMBER_PACK_NOT_PROVIDED",
            ],
            "execution_authorized": False,
            "no_order": True,
        }
        print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
        return 2
    pack = None
    if args.frozen_member_pack is not None:
        pack = json.loads(args.frozen_member_pack.read_text(encoding="utf-8"))
    kwargs: dict[str, object] = {}
    if pack is not None:
        kwargs["frozen_member_pack"] = pack
    result = evaluate_batch_a_existing_member_baselines(**kwargs)
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    if result.get("status") == "READY_RESEARCH_ONLY" and result.get("batch_a_accepted") is True:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
