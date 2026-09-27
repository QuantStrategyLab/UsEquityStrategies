"""Installed stdin/stdout entry for the frozen UX1 research preview.

The executable reads one JSON request and writes one JSON result. Operator
input roots stay in the process environment. This module only stages the
research files that entry already imports; it does not copy strategy
implementations or the rest of the dated research directory.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

BUNDLE_FILES = (
    "auto_allocate.py",
    "boxx_outer_cash_compare.py",
    "phase4_qqqm_tqqq_boxx_compare.py",
    "post_r9_capital_policy.v1.json",
    "post_r9_capital_study.py",
    "post_r9_date_effective_settlement_policy.v1.json",
    "post_r9_settlement_study.py",
    "r7_joint_account_compare.py",
    "r7_joint_account_policy.v1.json",
    "r8_joint_allocation_compare.py",
    "r8_joint_allocation_policy.v1.json",
    "r8_joint_allocation_policy.v2.json",
    "r9_frozen_policy_validation.py",
    "tqqq_cash_budget_compare.py",
    "ux1_preview_adapter.py",
)
_RESEARCH_RELATIVE = Path("docs/research/first_compounding_20260925")


def _checked_name(name: str) -> str:
    if name != Path(name).name or name in {".", ".."}:
        raise ValueError("UX1_BUNDLE_NAME_INVALID")
    return name


def stage_bundle(source: Path, destination: Path) -> None:
    """Copy the fixed research file set into an installed bundle directory."""
    destination.mkdir(parents=True, exist_ok=True)
    for name in BUNDLE_FILES:
        filename = _checked_name(name)
        src = source / filename
        if src.is_symlink() or not src.is_file():
            raise ValueError("UX1_BUNDLE_SOURCE_MISSING:" + filename)
        target = destination / filename
        if target.is_symlink():
            raise ValueError("UX1_BUNDLE_TARGET_SYMLINK:" + filename)
        shutil.copyfile(src, target)
        target.chmod(0o644)


def _complete(directory: Path) -> bool:
    return all(
        (directory / _checked_name(name)).is_file()
        and not (directory / name).is_symlink()
        for name in BUNDLE_FILES
    )


def _packaged_bundle() -> Path | None:
    candidate = Path(__file__).resolve().parent / "ux1_bundle"
    if _complete(candidate):
        return candidate
    return None


def _source_research_dir() -> Path | None:
    candidate = Path(__file__).resolve().parents[2] / _RESEARCH_RELATIVE
    if (candidate / "ux1_preview_adapter.py").is_file():
        return candidate
    return None


def _cached_source_bundle(source: Path) -> Path:
    digest = hashlib.sha256()
    for name in BUNDLE_FILES:
        filename = _checked_name(name)
        src = source / filename
        if src.is_symlink() or not src.is_file():
            raise ValueError("UX1_BUNDLE_SOURCE_MISSING:" + filename)
        digest.update(filename.encode())
        digest.update(src.read_bytes())
    destination = Path(tempfile.gettempdir()) / ("ues-ux1-bundle-" + digest.hexdigest()[:24])
    if _complete(destination):
        return destination
    staging = Path(tempfile.mkdtemp(prefix="ues-ux1-bundle-"))
    stage_bundle(source, staging)
    try:
        staging.rename(destination)
    except OSError:
        shutil.rmtree(staging, ignore_errors=True)
        if not _complete(destination):
            raise
    return destination


def resolve_bundle() -> Path:
    packaged = _packaged_bundle()
    if packaged is not None:
        return packaged
    source = _source_research_dir()
    if source is None:
        raise RuntimeError("UX1_BUNDLE_MISSING")
    return _cached_source_bundle(source)


def _prefer_bundle(bundle: Path) -> None:
    bundle_text = str(bundle)
    sys.path[:] = [item for item in sys.path if item != bundle_text]
    sys.path.insert(0, bundle_text)
    for name in BUNDLE_FILES:
        if name.endswith(".py"):
            sys.modules.pop(name[:-3], None)


def main(stdin=None) -> int:
    try:
        bundle = resolve_bundle()
    except (RuntimeError, ValueError) as exc:
        token = str(exc).split(":", 1)[0]
        sys.stderr.write(token + "\n")
        return 1
    _prefer_bundle(bundle)
    import ux1_preview_adapter

    return ux1_preview_adapter.main(stdin)


if __name__ == "__main__":
    raise SystemExit(main())
