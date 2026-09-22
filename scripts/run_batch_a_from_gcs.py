#!/usr/bin/env python3
"""Stage Batch A v2 inputs from GCS and materialize a frozen member pack.

This is the active Batch A research entry.  Old R3 staging
(``run_r3_from_gcs`` / ``--private-root``) has exited the active path and is
not invoked here.  Objects are copied with ``--no-clobber`` into an empty
staging root; the script never lists, overwrites, or deletes bucket objects.
Default action stops after local pack materialization and does not touch
brokers, credentials, workflows, or production settings.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from us_equity_strategies.research.batch_a_dataset import (
    BatchADatasetError,
    load_price_snapshot_v2,
)
from us_equity_strategies.research.batch_a_member_pack import (
    BatchAMemberPackError,
    materialize_batch_a_member_pack,
)

BATCH_A_DATASET_RELATIVE_PATHS = (
    "soxl_soxx/prices.csv",
    "soxl_soxx/prices.csv.manifest.json",
    "soxl_soxx/object_identity.json",
    "tqqq_qqq/prices.csv",
    "tqqq_qqq/prices.csv.manifest.json",
    "tqqq_qqq/object_identity.json",
)
_GS_PREFIX = re.compile(r"^gs://[a-z0-9][a-z0-9._-]*(?:/[A-Za-z0-9._=-]+)*$")
_Runner = Callable[..., subprocess.CompletedProcess[str]]


class GcsBatchAError(ValueError):
    """Sanitized staging failure; raw provider output is never surfaced."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require_gs_prefix(value: str, label: str) -> str:
    normalized = value.rstrip("/")
    if _GS_PREFIX.fullmatch(normalized) is None:
        raise GcsBatchAError(f"{label}_INVALID")
    return normalized


def _join_object(prefix: str, relative_path: str) -> str:
    return f"{prefix.rstrip('/')}/{relative_path}"


def _require_empty_staging_root(root: Path) -> None:
    if root.exists():
        if not root.is_dir() or any(root.iterdir()):
            raise GcsBatchAError("STAGING_ROOT_NOT_EMPTY")
    else:
        root.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)


def _run_gcloud(args: Sequence[str], *, runner: _Runner) -> None:
    try:
        result = runner(
            ("gcloud", "storage", *args),
            check=False,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError) as exc:
        raise GcsBatchAError("GCLOUD_UNAVAILABLE") from exc
    if result.returncode != 0:
        raise GcsBatchAError("GCS_OPERATION_FAILED")


def stage_batch_a_inputs(
    gcs_prefix: str,
    staging_root: str | Path,
    *,
    runner: _Runner = subprocess.run,
) -> tuple[Path, ...]:
    """Copy the fixed Batch A v2 input set into a new private staging directory."""

    prefix = _require_gs_prefix(gcs_prefix, "GCS_INPUT_PREFIX")
    root = Path(staging_root)
    _require_empty_staging_root(root)
    staged: list[Path] = []
    for relative_path in BATCH_A_DATASET_RELATIVE_PATHS:
        destination = root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise GcsBatchAError("STAGING_DESTINATION_EXISTS")
        _run_gcloud(
            ("cp", "--no-clobber", _join_object(prefix, relative_path), str(destination)),
            runner=runner,
        )
        if not destination.is_file():
            raise GcsBatchAError("STAGED_FILE_MISSING")
        staged.append(destination)
    return tuple(staged)


def materialize_from_staging(staging_root: str | Path) -> dict[str, object]:
    """Load staged v2 snapshots and materialize the frozen member pack."""

    root = Path(staging_root)
    try:
        soxl = load_price_snapshot_v2(root / "soxl_soxx")
        tqqq = load_price_snapshot_v2(root / "tqqq_qqq")
        pack = materialize_batch_a_member_pack(soxl_snapshot=soxl, tqqq_snapshot=tqqq)
    except BatchADatasetError as exc:
        raise GcsBatchAError(exc.code) from exc
    except BatchAMemberPackError as exc:
        raise GcsBatchAError(exc.code) from exc
    return pack


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gcs-input-prefix", required=True)
    parser.add_argument("--staging-root", type=Path, default=None)
    parser.add_argument(
        "--pack-output",
        type=Path,
        default=None,
        help="Optional local path to write the materialized v2 member pack JSON.",
    )
    args = parser.parse_args(argv)

    try:
        with tempfile.TemporaryDirectory(prefix="qsl-batch-a-v2-") as temporary_root:
            staging_root = args.staging_root or Path(temporary_root) / "batch_a_v2"
            stage_batch_a_inputs(args.gcs_input_prefix, staging_root)
            pack = materialize_from_staging(staging_root)
            if args.pack_output is not None:
                if args.pack_output.exists():
                    raise GcsBatchAError("PACK_OUTPUT_EXISTS")
                args.pack_output.parent.mkdir(parents=True, exist_ok=True)
                args.pack_output.write_text(
                    json.dumps(pack, sort_keys=True, separators=(",", ":"), allow_nan=False),
                    encoding="utf-8",
                )
            payload = {
                "status": "BATCH_A_V2_PACK_READY",
                "schema_version": pack["schema_version"],
                "pack_digest": pack["pack_digest"],
                "cash_return_policy": pack["cash_return_policy"],
                "execution_authorized": False,
                "no_order": True,
            }
            print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
            return 0
    except GcsBatchAError as exc:
        print(
            json.dumps(
                {
                    "status": "PARKED",
                    "reason_code": exc.code,
                    "execution_authorized": False,
                    "no_order": True,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
