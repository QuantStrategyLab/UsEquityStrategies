from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
import sys

from .smart_dca_decision_summary import (
    smart_dca_decision_summary_markdown,
    summarize_smart_dca_decision_matrices,
    write_smart_dca_decision_summary_json,
    write_smart_dca_decision_summary_markdown,
)
from .smart_dca_promotion_gate import DEFAULT_PROFILES


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        summary = summarize_smart_dca_decision_matrices(
            args.matrix_dir,
            profiles=args.profile or DEFAULT_PROFILES,
            require_scenario_manifest=args.require_scenario_manifest,
            require_runtime_consumer_coverage=(
                args.require_runtime_consumer_coverage
            ),
        )
        if args.output_json is not None:
            write_smart_dca_decision_summary_json(args.output_json, summary)
        if args.output_md is not None:
            write_smart_dca_decision_summary_markdown(args.output_md, summary)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.markdown:
        print(smart_dca_decision_summary_markdown(summary), end="")
    else:
        print(json.dumps(summary, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if summary["passed"] else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Summarize existing smart-DCA matrix decision artifacts without "
            "rerunning backtests."
        )
    )
    parser.add_argument(
        "--matrix-dir",
        required=True,
        action="append",
        type=Path,
        help=(
            "Matrix artifact directory containing review_decision.json and "
            "production_profile_decisions.csv. Can be provided multiple times."
        ),
    )
    parser.add_argument(
        "--profile",
        action="append",
        help=(
            "Production profile to require in every matrix. Defaults to both "
            "nasdaq_sp500_smart_dca and ibit_smart_dca."
        ),
    )
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    parser.add_argument(
        "--require-scenario-manifest",
        action="store_true",
        help=(
            "Fail a matrix directory that does not include scenario_manifest.json."
        ),
    )
    parser.add_argument(
        "--require-runtime-consumer-coverage",
        action="store_true",
        help=(
            "Require each matrix scenario manifest to prove runtime consumer "
            "coverage for external signal sources."
        ),
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Print Markdown instead of JSON.",
    )
    parser.add_argument("--pretty", action="store_true")
    return parser


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
