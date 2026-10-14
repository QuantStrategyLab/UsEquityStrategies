#!/usr/bin/env python3
"""Export registration or cloud-only qualification, never real data to Mac."""
import argparse
import json
from pathlib import Path

from us_equity_strategies.research.soxl_v7_derisk_study import registration, workflow_report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cloud-qualify", action="store_true")
    p.add_argument("--archive-prefix", default="")
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    report = workflow_report(args.archive_prefix) if args.cloud_qualify else registration()
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False)+"\n")
    return 2 if args.cloud_qualify and report["status"] == "PARKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
