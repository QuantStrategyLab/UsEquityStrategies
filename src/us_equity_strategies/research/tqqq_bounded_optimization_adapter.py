from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from typing import Any, Callable

from .r3_joint_evidence import TQQQ_IDENTITIES, TQQQ_SPEC


_ADAPTER_ID = (
    "us_equity_strategies.research.tqqq_bounded_optimization_adapter."
    "build_verified_current_r3_identity_bundle"
)
_SCHEMA = "qsl.tqqq.bounded_optimization_adapter.bundle.v3"
_RUNTIME_PATHS = (
    "src/us_equity_strategies/research/r3_joint_evidence.py",
    "src/us_equity_strategies/research/tqqq_bounded_optimization_adapter.py",
)
_EXPECTED_INPUT_IDENTITY = (
    ("a40254c7e31d6b49b4a2db5ec57b1b65215a3ab1ee33df879d9e5e2b4dae6551", 150661),
    ("8ecbc864f356af94464249ee3003d44fb00cf739c6810dc2de14165e5dc3500d", 593),
)
_SEMANTIC_INPUT_DIGEST = "8cc682b2d1acc23a8dd93c3bfd67b445d7305844d2c4d254f4f52e0ac817c6cb"


class IdentityBundleError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise IdentityBundleError(code)


def _git_bytes(repo_root: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        _fail("SOURCE_IDENTITY_INVALID")
    if result.returncode != 0:
        _fail("SOURCE_IDENTITY_INVALID")
    return result.stdout


def _git_text(repo_root: Path, *args: str) -> str:
    try:
        return _git_bytes(repo_root, *args).decode("ascii").strip()
    except UnicodeDecodeError:
        _fail("SOURCE_IDENTITY_INVALID")


def _resolve_source_identity(repo_root: Path, adapter_path: Path) -> tuple[str, dict[str, str]]:
    try:
        expected_root = repo_root.resolve()
        if adapter_path.resolve() != expected_root / _RUNTIME_PATHS[1]:
            _fail("SOURCE_IDENTITY_INVALID")
        if Path(_git_text(repo_root, "rev-parse", "--show-toplevel")).resolve() != expected_root:
            _fail("SOURCE_IDENTITY_INVALID")
        source_commit = _git_text(repo_root, "rev-parse", "--verify", "HEAD^{commit}")
        if len(source_commit) != 40 or any(char not in "0123456789abcdef" for char in source_commit):
            _fail("SOURCE_IDENTITY_INVALID")
        if _git_bytes(repo_root, "status", "--porcelain=v1", "--untracked-files=all"):
            _fail("SOURCE_IDENTITY_INVALID")
        source_modules: dict[str, str] = {}
        for runtime_path in _RUNTIME_PATHS:
            working_path = expected_root / runtime_path
            if not working_path.is_file():
                _fail("SOURCE_IDENTITY_INVALID")
            working_bytes = working_path.read_bytes()
            committed_bytes = _git_bytes(repo_root, "show", f"{source_commit}:{runtime_path}")
            if working_bytes != committed_bytes:
                _fail("SOURCE_IDENTITY_INVALID")
            source_modules[runtime_path] = hashlib.sha256(committed_bytes).hexdigest()
    except (OSError, ValueError):
        _fail("SOURCE_IDENTITY_INVALID")
    return source_commit, source_modules


def _read_input_state(path: Path, expected_sha256: str, expected_bytes: int) -> tuple[int, int, int, int, int, str]:
    try:
        metadata = os.lstat(path)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            _fail("INPUT_IDENTITY_INVALID")
        value = path.read_bytes()
    except OSError:
        _fail("INPUT_IDENTITY_INVALID")
    digest = hashlib.sha256(value).hexdigest()
    if len(value) != expected_bytes or digest != expected_sha256:
        _fail("INPUT_IDENTITY_INVALID")
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        digest,
    )


def _trusted_input_identity(
    r3_module: Any,
    expected_identity: tuple[tuple[str, int], tuple[str, int]],
    semantic_digest: str,
) -> tuple[dict[str, Any], tuple[tuple[int, int, int, int, int, str], ...]]:
    try:
        identities = r3_module.TQQQ_IDENTITIES
        if len(identities) != 2 or r3_module.TQQQ_SPEC.input_digest != semantic_digest:
            _fail("INPUT_IDENTITY_INVALID")
        states = []
        for identity, (expected_sha256, expected_bytes) in zip(identities, expected_identity, strict=True):
            if (
                not isinstance(identity.sha256, str)
                or identity.sha256 != expected_sha256
                or type(identity.byte_count) is not int
                or identity.byte_count != expected_bytes
            ):
                _fail("INPUT_IDENTITY_INVALID")
            states.append(_read_input_state(Path(identity.path), expected_sha256, expected_bytes))
    except (AttributeError, OSError, TypeError, ValueError):
        _fail("INPUT_IDENTITY_INVALID")
    return (
        {
            "artifact_sha256": expected_identity[0][0],
            "artifact_bytes": expected_identity[0][1],
            "manifest_sha256": expected_identity[1][0],
            "manifest_bytes": expected_identity[1][1],
            "semantic_input_digest": semantic_digest,
        },
        tuple(states),
    )


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii") + b"\n"


def _build_verified_identity_bundle(
    repo_root: Path,
    r3_module: Any,
    adapter_path: Path,
    expected_identity: tuple[tuple[str, int], tuple[str, int]],
    semantic_digest: str,
    after_first_check: Callable[[], None] | None = None,
) -> tuple[bytes, str]:
    source_before = _resolve_source_identity(repo_root, adapter_path)
    input_identity, input_before = _trusted_input_identity(r3_module, expected_identity, semantic_digest)
    bundle = _canonical_bytes(
        {
            "schema": _SCHEMA,
            "adapter_id": _ADAPTER_ID,
            "strategy_id": "TQQQ",
            "operation": "IDENTITY_ONLY",
            "source_commit": source_before[0],
            "source_modules": source_before[1],
            "input_identity": input_identity,
            "parameter_search_performed": False,
            "performance_read": False,
            "provider_access": False,
            "sealed_holdout_opened": False,
            "eligible": False,
            "size_zero_required": True,
            "terminal_status": "IDENTITY_ONLY_SIZE_ZERO",
        }
    )
    if after_first_check is not None:
        after_first_check()
    source_after = _resolve_source_identity(repo_root, adapter_path)
    _, input_after = _trusted_input_identity(r3_module, expected_identity, semantic_digest)
    if source_after != source_before or input_after != input_before:
        _fail("IDENTITY_CHANGED")
    return bundle, hashlib.sha256(bundle).hexdigest()


def _build_verified_identity_bundle_for_testing(
    *,
    repo_root: Path,
    r3_module: Any,
    adapter_path: Path,
    after_first_check: Callable[[], None] | None = None,
) -> tuple[bytes, str]:
    identities = r3_module.TQQQ_IDENTITIES
    expected_identity = tuple((item.sha256, item.byte_count) for item in identities)
    return _build_verified_identity_bundle(
        repo_root,
        r3_module,
        adapter_path,
        expected_identity,
        r3_module.TQQQ_SPEC.input_digest,
        after_first_check,
    )


def build_verified_current_r3_identity_bundle() -> tuple[bytes, str]:
    return _build_verified_identity_bundle(
        Path(__file__).resolve().parents[3],
        __import__("us_equity_strategies.research.r3_joint_evidence", fromlist=["TQQQ_IDENTITIES"]),
        Path(__file__),
        _EXPECTED_INPUT_IDENTITY,
        _SEMANTIC_INPUT_DIGEST,
    )
