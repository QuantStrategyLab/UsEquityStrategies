from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
import sys

from .signal_bundle_contract import (
    CANONICAL_INPUT_DERIVED_INDICATORS,
    SignalBundleContractError,
    signal_bundle_consumer_audit_summary_from_index,
    signal_bundle_consumer_audit_summary_from_manifest,
    signal_bundle_audit_summary_from_index,
    signal_bundle_audit_summary_from_manifest,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.index is not None and args.manifest is not None:
            raise SignalBundleContractError("provide either manifest or --index, not both")
        if args.index is not None:
            if args.consumer:
                summary = signal_bundle_consumer_audit_summary_from_index(
                    args.index,
                    consumer=args.consumer,
                    expected_canonical_input=args.canonical_input,
                    as_of=args.as_of,
                    bundle_id=args.bundle_id,
                )
            else:
                summary = signal_bundle_audit_summary_from_index(
                    args.index,
                    expected_canonical_input=args.canonical_input,
                    as_of=args.as_of,
                    bundle_id=args.bundle_id,
                )
        elif args.manifest is not None:
            if args.consumer:
                summary = signal_bundle_consumer_audit_summary_from_manifest(
                    args.manifest,
                    consumer=args.consumer,
                )
            else:
                summary = signal_bundle_audit_summary_from_manifest(args.manifest)
        else:
            raise SignalBundleContractError("provide a manifest path or --index")
    except (OSError, SignalBundleContractError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(summary, indent=2 if args.pretty else None, sort_keys=True))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a local signal bundle manifest and print non-sensitive "
            "consumer audit metadata."
        )
    )
    parser.add_argument("manifest", nargs="?", type=Path)
    parser.add_argument("--index", type=Path, help="Resolve a manifest from a local bundle index.")
    parser.add_argument("--as-of", help="Select the latest index entry at or before this as_of date.")
    parser.add_argument("--bundle-id", help="Require a specific bundle_id from the index.")
    parser.add_argument("--canonical-input", default=CANONICAL_INPUT_DERIVED_INDICATORS)
    parser.add_argument(
        "--consumer",
        help="Validate required indicator fields for a known strategy or research consumer.",
    )
    parser.add_argument("--pretty", action="store_true")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
