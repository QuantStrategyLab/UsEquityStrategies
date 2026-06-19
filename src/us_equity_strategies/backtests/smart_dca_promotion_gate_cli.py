from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
import sys

from .smart_dca_promotion_gate import (
    DEFAULT_PROFILES,
    audit_smart_dca_promotion_gate,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        audit = audit_smart_dca_promotion_gate(
            review_decision_path=args.review_decision,
            production_profile_decisions_path=args.production_profile_decisions,
            scenario_manifest_path=args.scenario_manifest,
            profiles=args.profile or DEFAULT_PROFILES,
            require_runtime_consumer_coverage=(
                args.require_runtime_consumer_coverage
            ),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(audit, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if audit["passed"] else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit smart-DCA matrix decision artifacts before any runtime "
            "default or smart-mode promotion."
        )
    )
    parser.add_argument("--review-decision", required=True, type=Path)
    parser.add_argument("--production-profile-decisions", required=True, type=Path)
    parser.add_argument(
        "--scenario-manifest",
        type=Path,
        help=(
            "Optional scenario_manifest.json to verify the decision files, "
            "candidate evidence, and input-artifact metadata are pinned together."
        ),
    )
    parser.add_argument(
        "--require-runtime-consumer-coverage",
        action="store_true",
        help=(
            "Require the scenario manifest to prove source catalog or handoff "
            "runtime consumer coverage."
        ),
    )
    parser.add_argument(
        "--profile",
        action="append",
        help=(
            "Production profile to require in the gate. Defaults to both "
            "nasdaq_sp500_smart_dca and ibit_smart_dca."
        ),
    )
    parser.add_argument("--pretty", action="store_true")
    return parser


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
