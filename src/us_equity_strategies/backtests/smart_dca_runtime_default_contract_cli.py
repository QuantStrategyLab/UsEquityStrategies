from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
import sys

from us_equity_strategies.catalog import (
    SMART_DCA_RUNTIME_DEFAULT_CONTRACT_PROFILES,
    audit_smart_dca_runtime_default_contract,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        audit = audit_smart_dca_runtime_default_contract(
            profiles=args.profile or SMART_DCA_RUNTIME_DEFAULT_CONTRACT_PROFILES,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(audit, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if audit["passed"] else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit production smart-DCA runtime defaults against the fixed-DCA "
            "default contract."
        )
    )
    parser.add_argument(
        "--profile",
        action="append",
        help=(
            "Smart-DCA profile to audit. Defaults to both "
            "nasdaq_sp500_smart_dca and ibit_smart_dca."
        ),
    )
    parser.add_argument("--pretty", action="store_true")
    return parser


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
