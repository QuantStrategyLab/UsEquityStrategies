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
            profiles=args.profile or DEFAULT_PROFILES,
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
