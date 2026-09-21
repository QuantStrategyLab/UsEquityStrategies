#!/usr/bin/env python3
"""Stage locked R3 inputs from GCS and optionally run the offline evidence job.

The default action only stages the five fixed R3 input artifacts and runs the
existing read-only preflight.  ``--run`` is an explicit research-only opt-in;
it never touches brokers, credentials, workflows, or production settings.
Objects are addressed explicitly and copied with ``--no-clobber``.  The
script never lists, overwrites, or deletes bucket objects.
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


R3_INPUT_RELATIVE_PATHS = (
    "tqqq_baseline_v1/full_20230714_20260716.csv",
    "tqqq_baseline_v1/full_20230714_20260716.csv.manifest.json",
    "soxx_soxl_adjusted_daily_v1/full_20230714_20260716.csv",
    "soxx_soxl_adjusted_daily_v1/full_20230714_20260716.csv.manifest.json",
    "soxx_soxl_adjusted_daily_v1/full_20230714_20260716.csv.readback.json",
)
_GS_PREFIX = re.compile(r"^gs://[a-z0-9][a-z0-9._-]*(?:/[A-Za-z0-9._=-]+)*$")
_Runner = Callable[..., subprocess.CompletedProcess[str]]


class GcsR3Error(ValueError):
    """Sanitized staging failure; raw provider output is never surfaced."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require_gs_prefix(value: str, label: str) -> str:
    normalized = value.rstrip("/")
    if _GS_PREFIX.fullmatch(normalized) is None:
        raise GcsR3Error(f"{label}_INVALID")
    return normalized


def _join_object(prefix: str, relative_path: str) -> str:
    return f"{prefix.rstrip('/')}/{relative_path}"


def _require_empty_staging_root(root: Path) -> None:
    if root.exists():
        if not root.is_dir() or any(root.iterdir()):
            raise GcsR3Error("STAGING_ROOT_NOT_EMPTY")
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
        raise GcsR3Error("GCLOUD_UNAVAILABLE") from exc
    if result.returncode != 0:
        raise GcsR3Error("GCS_OPERATION_FAILED")


def stage_r3_inputs(
    gcs_prefix: str,
    staging_root: str | Path,
    *,
    runner: _Runner = subprocess.run,
) -> tuple[Path, ...]:
    """Copy the fixed R3 input set into a new private staging directory."""

    prefix = _require_gs_prefix(gcs_prefix, "GCS_INPUT_PREFIX")
    root = Path(staging_root)
    _require_empty_staging_root(root)
    staged: list[Path] = []
    for relative_path in R3_INPUT_RELATIVE_PATHS:
        destination = root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise GcsR3Error("STAGING_DESTINATION_EXISTS")
        _run_gcloud(
            ("cp", "--no-clobber", _join_object(prefix, relative_path), str(destination)),
            runner=runner,
        )
        if not destination.is_file():
            raise GcsR3Error("STAGED_FILE_MISSING")
        staged.append(destination)
    return tuple(staged)


def stage_support_file(
    object_uri: str,
    destination: str | Path,
    *,
    runner: _Runner = subprocess.run,
) -> Path:
    """Copy one exact contract/prompt object without replacing a local file."""

    uri = _require_gs_prefix(object_uri, "GCS_SUPPORT_OBJECT")
    target = Path(destination)
    if target.exists():
        raise GcsR3Error("STAGING_DESTINATION_EXISTS")
    target.parent.mkdir(parents=True, exist_ok=True)
    _run_gcloud(("cp", "--no-clobber", uri, str(target)), runner=runner)
    if not target.is_file():
        raise GcsR3Error("STAGED_SUPPORT_FILE_MISSING")
    return target


def _python_script(repo_root: Path, name: str) -> str:
    script = repo_root / "scripts" / name
    if not script.is_file():
        raise GcsR3Error("REPOSITORY_SCRIPT_MISSING")
    return str(script)


def build_preflight_command(
    repo_root: str | Path,
    staging_root: str | Path,
    contract_path: str | Path,
    worker_prompt_path: str | Path,
) -> tuple[str, ...]:
    return (
        sys.executable,
        _python_script(Path(repo_root), "run_r3_joint_evidence.py"),
        "--preflight",
        "--private-root",
        str(staging_root),
        "--contract-path",
        str(contract_path),
        "--worker-prompt-path",
        str(worker_prompt_path),
    )


def _run_repo_command(command: Sequence[str], *, runner: _Runner) -> tuple[int, str]:
    try:
        result = runner(command, check=False, capture_output=True, text=True)
    except (FileNotFoundError, OSError) as exc:
        raise GcsR3Error("REPOSITORY_COMMAND_FAILED") from exc
    output = (result.stdout or "").strip().splitlines()
    return result.returncode, output[-1] if output else ""


def _json_line(value: str, code: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise GcsR3Error(code) from exc
    if not isinstance(parsed, dict):
        raise GcsR3Error(code)
    return parsed


def run_preflight(
    repo_root: str | Path,
    staging_root: str | Path,
    contract_path: str | Path,
    worker_prompt_path: str | Path,
    *,
    runner: _Runner = subprocess.run,
) -> dict[str, object]:
    return_code, output = _run_repo_command(
        build_preflight_command(repo_root, staging_root, contract_path, worker_prompt_path),
        runner=runner,
    )
    payload = _json_line(output, "R3_PREFLIGHT_OUTPUT_INVALID")
    if return_code != 0 or payload.get("ready") is not True:
        raise GcsR3Error("R3_PREFLIGHT_NOT_READY")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gcs-input-prefix", required=True)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--staging-root", type=Path, default=None)
    support = parser.add_argument_group("contract and worker prompt")
    support.add_argument("--contract-uri")
    support.add_argument("--contract-path", type=Path)
    support.add_argument("--worker-prompt-uri")
    support.add_argument("--worker-prompt-path", type=Path)
    parser.add_argument(
        "--run",
        action="store_true",
        help="After a successful preflight, run the offline R3 evidence CLI.",
    )
    parser.add_argument(
        "--gcs-output-prefix",
        help="Required with --run; a new, non-overwriting prefix for successful output.",
    )
    args = parser.parse_args(argv)

    if (args.contract_uri is None) == (args.contract_path is None):
        parser.error("provide exactly one of --contract-uri and --contract-path")
    if (args.worker_prompt_uri is None) == (args.worker_prompt_path is None):
        parser.error("provide exactly one of --worker-prompt-uri and --worker-prompt-path")
    if args.run and args.gcs_output_prefix is None:
        parser.error("--gcs-output-prefix is required with --run")

    try:
        with tempfile.TemporaryDirectory(prefix="qsl-r3-gcs-") as temporary_root:
            staging_root = args.staging_root or Path(temporary_root) / "private_research"
            stage_r3_inputs(args.gcs_input_prefix, staging_root)
            contract_path = (
                stage_support_file(args.contract_uri, staging_root / "acceptance-contract.md")
                if args.contract_uri
                else args.contract_path
            )
            worker_prompt_path = (
                stage_support_file(args.worker_prompt_uri, staging_root / "worker-prompt.md")
                if args.worker_prompt_uri
                else args.worker_prompt_path
            )
            readiness = run_preflight(
                args.repo_root,
                staging_root,
                contract_path,
                worker_prompt_path,
            )
            payload: dict[str, object] = {
                "status": "R3_PREFLIGHT_READY",
                "ready": True,
                "source_commit": readiness.get("source_commit"),
                "execution_authorized": False,
                "no_order": True,
            }
            if args.run:
                raise GcsR3Error("R3_EXECUTION_NOT_IMPLEMENTED")
            print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
            return 0
    except GcsR3Error as exc:
        print(
            json.dumps(
                {
                    "status": "PARKED",
                    "ready": False,
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
