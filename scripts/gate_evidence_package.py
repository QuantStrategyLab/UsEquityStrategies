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
STRATEGY_SPEC_FILENAMES = frozenset({"research-spec.json", "optimization-spec.json"})
RESEARCH_ARTIFACT_FILENAMES = STRATEGY_SPEC_FILENAMES | {
    "benchmark-registry.json",
    "config-snapshot.json",
    "cost-model.json",
    "data-manifest.json",
    "trial-ledger.json",
}


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
        if candidate.name in RESEARCH_ARTIFACT_FILENAMES:
            continue
        if candidate.suffix.lower() not in EVIDENCE_SUFFIXES:
            continue
        if "evidence" in candidate.parts or candidate.parent.name == "evidence":
            paths.append(candidate)
    return paths


def _discover_evidence_files(diff: str) -> list[Path]:
    discovered = _evidence_paths_from_diff(diff)
    for folder in (Path("docs/evidence"), Path("evidence")):
        if folder.is_dir():
            discovered.extend(
                path
                for path in folder.iterdir()
                if path.suffix.lower() in EVIDENCE_SUFFIXES and path.name not in RESEARCH_ARTIFACT_FILENAMES
            )
    explicit = os.environ.get("EVIDENCE_PACKAGE_PATH", "").strip()
    if explicit:
        discovered.append(Path(explicit))
    return sorted({path for path in discovered if path.exists()})


def _discover_strategy_specs(diff: str) -> list[Path]:
    paths: list[Path] = []
    for line in diff.splitlines():
        if not line.startswith("+++ b/"):
            continue
        candidate = Path(line[6:])
        if candidate.name not in STRATEGY_SPEC_FILENAMES or "evidence" not in candidate.parts:
            continue
        if candidate.exists():
            paths.append(candidate)
    return sorted(set(paths))


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


def _validate_strategy_specs(
    paths: list[Path],
    *,
    validator=None,
) -> tuple[bool, list[str]]:
    if validator is None:
        try:
            from quant_platform_kit.strategy_lifecycle import validate_strategy_spec_file
        except ImportError:
            return False, ["QuantPlatformKit does not provide strategy spec validation; update the QPK pin first"]
        validator = validate_strategy_spec_file

    issues: list[str] = []
    for path in paths:
        for issue in validator(path):
            issues.append(f"{path}: {issue}")
    return not issues, issues


def _run_promotion_dual_review(evidence_files: list[Path]) -> int:
    root = Path(os.environ.get("AIAUDIT_BRIDGE_ROOT", "external/AIAuditBridge"))
    script = root / "scripts" / "run_dual_review_pipeline.py"
    if not script.is_file():
        print("[evidence-gate] dual-review skipped: AIAuditBridge not found")
        return 0
    if str(os.environ.get("DUAL_REVIEW_GATE_SKIP", "")).strip().lower() in {"1", "true", "yes"}:
        print("[evidence-gate] dual-review skipped by DUAL_REVIEW_GATE_SKIP")
        return 0

    worst = 0
    for path in evidence_files:
        proc = subprocess.run(
            [
                sys.executable,
                str(script),
                "--from-evidence",
                str(path),
                "--dispatch",
            ],
            cwd=str(root),
            env={**os.environ, "PYTHONPATH": str(root)},
            check=False,
        )
        print(f"[evidence-gate] dual-review {path.name} exit={proc.returncode}")
        if proc.stdout:
            print(proc.stdout.strip())
        if proc.stderr:
            print(proc.stderr.strip(), file=sys.stderr)
        worst = max(worst, proc.returncode)
    if worst >= 2:
        print("::error::Dual-review blocked promotion (disagreement or reject)", file=sys.stderr)
        return 1
    return 0


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

    strategy_specs = _discover_strategy_specs(diff)
    spec_names = {path.name for path in strategy_specs}
    if spec_names != STRATEGY_SPEC_FILENAMES:
        missing = sorted(STRATEGY_SPEC_FILENAMES - spec_names)
        print(
            "::error::Catalog status promotion requires changed evidence/research-spec.json and "
            f"evidence/optimization-spec.json files; missing: {', '.join(missing)}",
            file=sys.stderr,
        )
        return 1
    specs_ok, spec_issues = _validate_strategy_specs(strategy_specs)
    if not specs_ok:
        print("::error::Strategy specification validation failed", file=sys.stderr)
        for issue in spec_issues:
            print(f"  - {issue}", file=sys.stderr)
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

    if failed:
        return 1
    return _run_promotion_dual_review(evidence_files)


if __name__ == "__main__":
    raise SystemExit(main())
