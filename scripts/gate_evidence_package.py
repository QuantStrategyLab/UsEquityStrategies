#!/usr/bin/env python3
"""PR gate: require a valid evidence package when catalog status is promoted."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

GATE_STAGES = frozenset(
    {
        "ai_monitored_candidate",
        "shadow_candidate",
        "live_candidate",
        "runtime_enabled",
    }
)
STATUS_ADDED_RE = re.compile(r'^\+.*status="([^"]+)"')
EVIDENCE_SUFFIXES = {".json", ".toml"}


def _git_diff(base_ref: str) -> str:
    result = subprocess.run(
        ["git", "diff", f"origin/{base_ref}...HEAD", "--", "src"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        result = subprocess.run(
            ["git", "diff", f"{base_ref}...HEAD", "--", "src"],
            capture_output=True,
            text=True,
            check=True,
        )
    return result.stdout


def _promotion_detected(diff: str) -> bool:
    if "status=" not in diff:
        return False
    return any(match.group(1) in GATE_STAGES for line in diff.splitlines() if (match := STATUS_ADDED_RE.match(line)))


def _evidence_paths_from_diff(diff: str) -> list[Path]:
    paths: list[Path] = []
    for line in diff.splitlines():
        if not line.startswith("+++ b/"):
            continue
        candidate = Path(line[6:])
        if candidate.suffix.lower() not in EVIDENCE_SUFFIXES:
            continue
        if "evidence" in candidate.parts or candidate.parent.name == "evidence":
            paths.append(candidate)
    return paths


def _discover_evidence_files(diff: str) -> list[Path]:
    discovered = _evidence_paths_from_diff(diff)
    for folder in (Path("docs/evidence"), Path("evidence")):
        if folder.is_dir():
            discovered.extend(path for path in folder.iterdir() if path.suffix.lower() in EVIDENCE_SUFFIXES)
    explicit = os.environ.get("EVIDENCE_PACKAGE_PATH", "").strip()
    if explicit:
        discovered.append(Path(explicit))
    return sorted({path for path in discovered if path.exists()})


def _validate_with_lifecycle(path: Path) -> tuple[bool, list[str]]:
    from quant_platform_kit.strategy_lifecycle.evidence_gate import validate_evidence_package_file

    result = validate_evidence_package_file(path)
    issues = list(result.issues)
    return result.valid, issues


def _validate_with_promotion_standard(path: Path) -> tuple[bool, list[str]]:
    script = Path("external/QuantPlatformKit/scripts/validate_strategy_evidence_package.py")
    if not script.exists():
        return True, []
    result = subprocess.run(
        [sys.executable, str(script), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return True, []
    issues = [line for line in result.stderr.splitlines() if line.strip()]
    issues.extend(line for line in result.stdout.splitlines() if line.strip())
    return False, issues or ["promotion evidence package validation failed"]


def main() -> int:
    base_ref = os.environ.get("GITHUB_BASE_REF", "main").strip() or "main"
    diff = _git_diff(base_ref)

    if not _promotion_detected(diff):
        print("[evidence-gate] No lifecycle status promotion detected; skipping validation")
        return 0

    evidence_files = _discover_evidence_files(diff)
    if not evidence_files:
        print(
            "::error::Catalog status promotion detected but no evidence package file was found. "
            "Add docs/evidence/<profile>.json with the 11 required artifacts.",
            file=sys.stderr,
        )
        return 1

    failed = False
    for path in evidence_files:
        lifecycle_ok, lifecycle_issues = _validate_with_lifecycle(path)
        standard_ok, standard_issues = _validate_with_promotion_standard(path)
        if lifecycle_ok and standard_ok:
            print(f"[evidence-gate] PASS {path}")
            continue
        failed = True
        print(f"[evidence-gate] FAIL {path}", file=sys.stderr)
        for issue in lifecycle_issues + standard_issues:
            print(f"  - {issue}", file=sys.stderr)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
