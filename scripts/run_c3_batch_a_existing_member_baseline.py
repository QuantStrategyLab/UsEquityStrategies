#!/usr/bin/env python3
"""Run Batch A existing-member C3 baseline gate (research/shadow only).

Default behavior: return PARKED when a frozen v2 C3-comparable SOXL/TQQQ/cash
member pack is absent.  Never invents returns, never touches
broker/credentials/workflows, never authorizes execution, and does not use
legacy R3 ``--private-root`` as an active Batch A entry.

Optional capital-path flags only activate when at least one is supplied; fee
bps and rebalance indices must both be explicit (no silent zero-fee / empty
schedule defaults).  Does not run real-portfolio comparisons.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from us_equity_strategies.research.c3_batch_a_existing_member_baseline import (
    evaluate_batch_a_existing_member_baselines,
    report_historical_batch_a_baselines,
)


def _parse_rebalance_indices(raw: str | None) -> tuple[int, ...] | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return ()
    indices: list[int] = []
    for part in text.split(","):
        item = part.strip()
        if not item:
            raise argparse.ArgumentTypeError("REBALANCE_INDEX_INVALID")
        try:
            value = int(item)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("REBALANCE_INDEX_INVALID") from exc
        indices.append(value)
    return tuple(indices)


def _capital_path_options_from_args(args: argparse.Namespace) -> dict[str, object] | None:
    requested = any(
        (
            args.apply_risk_scaling,
            args.cash_member_id is not None,
            args.rebalance_fee_bps is not None,
            args.rebalance_indices is not None,
        )
    )
    if not requested:
        return None
    options: dict[str, object] = {
        "apply_risk_scaling": bool(args.apply_risk_scaling),
        "member_costs_already_embedded": True,
    }
    if args.cash_member_id is not None:
        options["cash_member_id"] = args.cash_member_id
    if args.rebalance_fee_bps is not None:
        options["rebalance_fee_bps"] = float(args.rebalance_fee_bps)
    if args.rebalance_indices is not None:
        options["rebalance_indices"] = args.rebalance_indices
    return options


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
        "--apply-risk-scaling",
        action="store_true",
        default=False,
        help=(
            "Explicitly request capital-path risk scaling into cash_sleeve "
            "(requires --cash-member-id)."
        ),
    )
    parser.add_argument(
        "--cash-member-id",
        type=str,
        default=None,
        help="Cash member id for risk-scaling residual (e.g. cash_sleeve).",
    )
    parser.add_argument(
        "--rebalance-fee-bps",
        type=float,
        default=None,
        help=(
            "Explicit combo-layer rebalance fee in bps. Required together with "
            "--rebalance-indices when any capital-path flag is set; omitted "
            "values are reported as gaps (never defaulted to zero)."
        ),
    )
    parser.add_argument(
        "--rebalance-indices",
        type=_parse_rebalance_indices,
        default=None,
        help=(
            "Comma-separated 0-based session indices for combo rebalances "
            "(e.g. 0,2). Required together with --rebalance-fee-bps when any "
            "capital-path flag is set."
        ),
    )
    parser.add_argument(
        "--historical-account-report", action="store_true",
        help="Opt-in source-bound seen-development control with 0/5/10/15bps combo assumptions, not qualification.",
    )
    parser.add_argument("--snapshot-input-root", type=Path, default=None,
                        help="Original cloud staging root; historical report revalidates snapshots and the pack.")
    parser.add_argument("--annual-risk-free-rate", type=float, default=None)
    parser.add_argument("--annual-minimum-acceptable-return", type=float, default=None)
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
            "promotion_authorized": False,
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
    capital_path_options = _capital_path_options_from_args(args)
    if capital_path_options is not None:
        kwargs["capital_path_options"] = capital_path_options
    if args.historical_account_report:
        from us_equity_strategies.research.batch_a_dataset import load_price_snapshot_v2
        try:
            if (pack is None or args.snapshot_input_root is None
                    or args.annual_risk_free_rate is None or args.annual_minimum_acceptable_return is None
                    or capital_path_options is not None):
                raise ValueError("HISTORICAL_REPORT_INPUTS_REQUIRED")
            result = report_historical_batch_a_baselines(
                frozen_member_pack=pack,
                soxl_snapshot=load_price_snapshot_v2(
                    args.snapshot_input_root / "soxl_soxx",
                    identity_path=args.snapshot_input_root / "soxl_soxx" / "object_identity.json"),
                tqqq_snapshot=load_price_snapshot_v2(
                    args.snapshot_input_root / "tqqq_qqq",
                    identity_path=args.snapshot_input_root / "tqqq_qqq" / "object_identity.json"),
                annual_risk_free_rate=args.annual_risk_free_rate,
                annual_minimum_acceptable_return=args.annual_minimum_acceptable_return)
        except (ValueError, TypeError, ArithmeticError):
            # Never echo private paths, provider bodies or an arbitrary exception.
            result = {"status": "PARKED", "reason_codes": ["HISTORICAL_ACCOUNT_REPORT_REFUSED"],
                      "research_only": True, "execution_authorized": False,
                      "promotion_authorized": False, "no_order": True,
                      "data_qualified": False, "oos_qualified": False, "batch_a_accepted": False}
    elif (args.snapshot_input_root is not None or args.annual_risk_free_rate is not None
          or args.annual_minimum_acceptable_return is not None):
        result = {"status": "PARKED", "reason_codes": ["HISTORICAL_REPORT_OPT_IN_REQUIRED"],
                  "batch_a_accepted": False, "execution_authorized": False, "no_order": True}
    else:
        result = evaluate_batch_a_existing_member_baselines(**kwargs)
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    if result.get("status") == "READY_RESEARCH_ONLY" and result.get("batch_a_accepted") is True:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
