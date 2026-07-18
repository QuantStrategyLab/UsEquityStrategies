#!/usr/bin/env python3
"""Run the one locked private offline R3 evidence profile."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from us_equity_strategies.research.r3_joint_evidence import (
    CONTRACT_PATH,
    R3EvidenceError,
    WORKER_PROMPT_PATH,
    run_private_r3,
    run_private_tqqq_sma_bounded_optimization,
    persist_tqqq_sma_bounded_optimization,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract-path", type=Path, default=CONTRACT_PATH)
    parser.add_argument("--worker-prompt-path", type=Path, default=WORKER_PROMPT_PATH)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--tqqq-sma-bounded-optimization", action="store_true")
    args = parser.parse_args(argv)
    try:
        run_kwargs: dict[str, object] = {
            "contract_path": args.contract_path,
            "worker_prompt_path": args.worker_prompt_path,
        }
        if args.output_root is not None:
            run_kwargs["output_root"] = args.output_root
        bundle, paths = run_private_r3(**run_kwargs)
    except R3EvidenceError as exc:
        print(f"R3 evidence failed: {exc.code}", file=sys.stderr)
        return 2
    summary = {
        "outcome": bundle["terminal"]["outcome"],
        "failed_stage": bundle["terminal"]["failed_stage"],
        "failure_codes": bundle["terminal"]["failure_codes"],
        "eligible_strategies": bundle["terminal"]["eligible_strategies"],
        "ineligible_strategies": bundle["terminal"]["ineligible_strategies"],
        "joint_status": bundle["joint_dependency"]["status"],
        "bundle_path": str(paths.bundle),
        "sidecar_path": str(paths.sidecar),
        "readback_path": str(paths.readback),
    }
    if args.tqqq_sma_bounded_optimization:
        try:
            report = run_private_tqqq_sma_bounded_optimization(
                bundle,
                _source_commit_reader=lambda: bundle["source_commit"],
            )
            persist_tqqq_sma_bounded_optimization(report, paths.bundle.parent)
        except R3EvidenceError as exc:
            print(f"TQQQ SMA optimization failed: {exc.code}", file=sys.stderr)
            return 2
        summary["tqqq_sma_bounded_optimization"] = {
            "outcome": report["outcome"],
            "winner_sma_window": report["winner_sma_window"],
            "gate_reasons": report["gate_reasons"],
            "evidence_digest": report["evidence_digest"],
            "source_commit": report["source_commit"],
            "checkout_verification": report["runner_envelope"]["checkout_verification"],
            "ci_provenance": report["runner_envelope"]["ci_provenance"],
        }
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    if bundle["terminal"]["failure_codes"]:
        return 2
    return 1 if bundle["terminal"]["ineligible_strategies"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
