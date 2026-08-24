#!/usr/bin/env python3
"""Assess whether immutable artifacts are eligible for a strategy release.

This command is intentionally diagnostic-only: it never creates a manifest,
changes a strategy, deploys a service, or authorizes orders. A nonzero result
means the supplied package must not be reloaded into any runtime target.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from quant_platform_kit.strategy_lifecycle import assess_strategy_release_readiness


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", required=True, help="Candidate immutable release identifier.")
    parser.add_argument("--strategy-profile", required=True)
    parser.add_argument("--strategy-revision", required=True, help="Committed source revision under review.")
    parser.add_argument("--effective-session", required=True, help="ISO-8601 trading session date.")
    parser.add_argument("--target-set-id", required=True, help="Name of the reviewed runtime target set.")
    parser.add_argument(
        "--target",
        action="append",
        required=True,
        help="One reviewed target, such as longbridge:SG. Repeat for every target.",
    )
    parser.add_argument("--config-path", required=True, type=Path)
    parser.add_argument("--risk-policy-path", required=True, type=Path)
    parser.add_argument("--evidence-path", required=True, type=Path)
    parser.add_argument(
        "--plugin-bundle-path",
        action="append",
        required=True,
        type=Path,
        help="One approved plugin artifact. Repeat for every artifact in the bundle.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    readiness = assess_strategy_release_readiness(
        release_id=args.release_id,
        strategy_profile=args.strategy_profile,
        strategy_revision=args.strategy_revision,
        effective_session=args.effective_session,
        target_set_id=args.target_set_id,
        targets=args.target,
        config_path=args.config_path,
        risk_policy_path=args.risk_policy_path,
        evidence_path=args.evidence_path,
        plugin_bundle_paths=args.plugin_bundle_path,
    )
    print(json.dumps(readiness.to_diagnostic(), ensure_ascii=False, sort_keys=True))
    return 0 if readiness.is_ready else 2


if __name__ == "__main__":  # pragma: no cover - exercised through main
    raise SystemExit(main())
