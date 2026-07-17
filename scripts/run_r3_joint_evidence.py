#!/usr/bin/env python3
"""Run the one locked private offline R3 evidence profile."""
from __future__ import annotations

import json
import sys

from us_equity_strategies.research.r3_joint_evidence import (
    R3EvidenceError,
    run_private_r3,
)


def main() -> int:
    try:
        bundle, paths = run_private_r3()
    except R3EvidenceError as exc:
        print(f"R3 evidence failed: {exc.code}", file=sys.stderr)
        return 2
    summary = {
        "outcome": bundle["terminal"]["outcome"],
        "eligible_strategies": bundle["terminal"]["eligible_strategies"],
        "ineligible_strategies": bundle["terminal"]["ineligible_strategies"],
        "joint_status": bundle["joint_dependency"]["status"],
        "bundle_path": str(paths.bundle),
        "sidecar_path": str(paths.sidecar),
        "readback_path": str(paths.readback),
    }
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
