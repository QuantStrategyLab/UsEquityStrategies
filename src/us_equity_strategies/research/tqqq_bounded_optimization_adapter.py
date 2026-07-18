"""Fail-closed singleton TQQQ identity package; it never evaluates performance."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, NoReturn
from uuid import uuid4

from . import r3_joint_evidence as r3


ADAPTER_ID = "us_equity_strategies.research.tqqq_bounded_optimization_adapter.run_verified_current_r3_adapter"
CALLER_ID = "scripts/run_tqqq_bounded_optimization_adapter.py:main"
EVIDENCE_SCHEMA = "qsl.tqqq.bounded_optimization_adapter.evidence.v2"
SEARCH_SPACE_SCHEMA = "qsl.tqqq.bounded_optimization_adapter.search_space.v2"
CANDIDATE_SCHEMA = "qsl.tqqq.bounded_optimization_adapter.candidate.v2"
MANIFEST_SCHEMA = "qsl.tqqq.bounded_optimization_adapter.package_manifest.v2"
CONTRACT_SHA256 = "78fc528f2b7fb40ebacdedc484ca7d62c67c898f698f18fcd4e59a12d047880d"
WORKER_PROMPT_SHA256 = "4bbb83e4c12c367ee98c1715e9e9875c1298470cb6fd51ee5bf2716dc6367685"
PROFILE_SHA256 = "cfc7bcffc4853d1b79ae0575287e76a8e50b679792ccd003858a317b1f42e684"
LOCKED_ARTIFACT_SHA256 = "a40254c7e31d6b49b4a2db5ec57b1b65215a3ab1ee33df879d9e5e2b4dae6551"
LOCKED_ARTIFACT_BYTES = 150661
LOCKED_MANIFEST_SHA256 = "8ecbc864f356af94464249ee3003d44fb00cf739c6810dc2de14165e5dc3500d"
LOCKED_MANIFEST_BYTES = 593
LOCKED_INPUT_DIGEST = "8cc682b2d1acc23a8dd93c3bfd67b445d7305844d2c4d254f4f52e0ac817c6cb"
CANDIDATE_SHA256 = "e340068901e3ce702bf829ad3a75890ff09d33879a5c463c148def6837d15af2"
SEARCH_SPACE_SHA256 = "3a48e637ec6604f8dc4b90f35d8797b24592c2f8565abc0c70881de576cbee7f"
LEDGER_SHA256 = "1c65d909af54ce91593e126020fe38c36ec0ab7c6f10c63fd74cdd90b54a74f4"
METRICS_DIGEST = "ba7bbb687f69dbfda21c7a129b4854e95c7ce5d6ef0a1e07515a1ff96eecea99"

COMMITTED_RUNTIME_PATHS = (
    "src/us_equity_strategies/research/r3_joint_evidence.py",
    "src/us_equity_strategies/research/tqqq_offline_input_contract.py",
    "src/us_equity_strategies/research/tqqq_typed_baseline_result.py",
    "src/us_equity_strategies/research/tqqq_bounded_optimization_adapter.py",
    "scripts/run_tqqq_bounded_optimization_adapter.py",
)
CANDIDATE_BYTES = (
    b'{"candidate_id":"baseline_v1","parameters":{},"parent_baseline_artifact_sha256":"'
    b"a40254c7e31d6b49b4a2db5ec57b1b65215a3ab1ee33df879d9e5e2b4dae6551"
    b'","schema":"qsl.tqqq.bounded_optimization_adapter.candidate.v2"}\n'
)
SEARCH_SPACE = {
    "schema": SEARCH_SPACE_SCHEMA,
    "max_trials": 1,
    "trial_count": 1,
    "candidate_order": ["baseline_v1"],
    "candidate_sha256": CANDIDATE_SHA256,
}
LEDGER = {
    "candidate_id": "baseline_v1",
    "candidate_sha256": CANDIDATE_SHA256,
    "deterministic_order": 0,
    "eligible": False,
    "end_status": "COMPLETED",
    "failure_code": "SEALED_UNSEEN_DATA_UNAVAILABLE",
    "metrics_digest": METRICS_DIGEST,
    "selection_reason": "REJECT_ALL_BASELINE_PRESERVED_NO_APPROVED_ALTERNATIVES",
    "size_zero_required": True,
    "start_status": "STARTED",
}


class BoundedOptimizationAdapterError(ValueError):
    """Sanitized fail-closed adapter error."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> NoReturn:
    raise BoundedOptimizationAdapterError(code) from None


def _canonical(value: object) -> bytes:
    try:
        return (
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
            .encode("ascii")
            + b"\n"
        )
    except (TypeError, ValueError, UnicodeError):
        _fail("CANONICAL_JSON_INVALID")


LEDGER_BYTES = _canonical(LEDGER)
if (
    len(CANDIDATE_BYTES) != 210
    or hashlib.sha256(CANDIDATE_BYTES).hexdigest() != CANDIDATE_SHA256
    or len(_canonical(SEARCH_SPACE)) != 218
    or hashlib.sha256(_canonical(SEARCH_SPACE)).hexdigest() != SEARCH_SPACE_SHA256
    or len(LEDGER_BYTES) != 442
    or hashlib.sha256(LEDGER_BYTES).hexdigest() != LEDGER_SHA256
    or hashlib.sha256(b"NO_PERFORMANCE_READ_SINGLETON_BASELINE_V2\n").hexdigest()
    != METRICS_DIGEST
):
    raise RuntimeError("frozen adapter constants drifted")


@dataclass(frozen=True, slots=True)
class SourceContext:
    source_commit: str
    source_modules: dict[str, str]


@dataclass(frozen=True, slots=True)
class FileState:
    device: int
    inode: int
    byte_count: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ExpectedPackage:
    evidence: bytes
    ledger: bytes
    manifest: bytes
    manifest_sha256: str


def _strict_json(raw: bytes) -> dict[str, Any]:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail("DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def reject_constant(_: str) -> NoReturn:
        _fail("NONFINITE_JSON_VALUE")

    if not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        _fail("STRICT_JSON_INVALID")
    try:
        text = raw.decode("ascii", errors="strict")
        value = json.loads(text, object_pairs_hook=object_pairs, parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError):
        _fail("STRICT_JSON_INVALID")
    if type(value) is not dict or _canonical(value) != raw:
        _fail("CANONICAL_JSON_MISMATCH")
    return value


def _lower_hex(value: object, length: int) -> bool:
    return type(value) is str and len(value) == length and all(char in "0123456789abcdef" for char in value)


def _read_identity(identity: r3.FileIdentity) -> FileState:
    try:
        metadata = identity.path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            _fail("FILE_IDENTITY_MISMATCH")
        descriptor = os.open(identity.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_dev != metadata.st_dev
                or opened.st_ino != metadata.st_ino
            ):
                _fail("FILE_IDENTITY_MISMATCH")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            os.close(descriptor)
    except (OSError, ValueError):
        _fail("FILE_IDENTITY_MISMATCH")
    raw = b"".join(chunks)
    state = FileState(metadata.st_dev, metadata.st_ino, len(raw), hashlib.sha256(raw).hexdigest())
    if identity.byte_count is None or state.byte_count != identity.byte_count or state.sha256 != identity.sha256:
        _fail("FILE_IDENTITY_MISMATCH")
    return state


def _locked_identity_states() -> tuple[FileState, FileState]:
    identities = r3.TQQQ_IDENTITIES
    if type(identities) is not tuple or len(identities) != 2:
        _fail("LOCKED_IDENTITY_MISMATCH")
    artifact, manifest = identities
    if (
        artifact.sha256 != LOCKED_ARTIFACT_SHA256
        or artifact.byte_count != LOCKED_ARTIFACT_BYTES
        or manifest.sha256 != LOCKED_MANIFEST_SHA256
        or manifest.byte_count != LOCKED_MANIFEST_BYTES
        or r3.TQQQ_SPEC.input_digest != LOCKED_INPUT_DIGEST
    ):
        _fail("LOCKED_IDENTITY_MISMATCH")
    return _read_identity(artifact), _read_identity(manifest)


def _operational_relative_paths(paths: tuple[Path, ...]) -> set[str]:
    allowed: set[str] = set()
    for path in paths:
        try:
            relative = path.resolve().relative_to(r3.REPO_ROOT.resolve()).as_posix()
        except ValueError:
            continue
        allowed.add(relative)
    return allowed


def _resolve_source_commit_with_operational_paths(paths: tuple[Path, ...]) -> str:
    allowed = _operational_relative_paths(paths)
    if not allowed:
        return r3._resolve_source_commit()
    try:
        actual_root = r3._git_output(r3.REPO_ROOT, ("rev-parse", "--show-toplevel")).decode(
            "utf-8", errors="strict"
        ).strip()
        source_commit = r3._git_output(
            r3.REPO_ROOT, ("rev-parse", "--verify", "HEAD^{commit}")
        ).decode("ascii", errors="strict").strip()
        dirty = r3._git_output(
            r3.REPO_ROOT,
            ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
        )
    except (OSError, UnicodeError):
        _fail("SOURCE_REVISION_UNVERIFIABLE")
    if Path(actual_root).resolve() != r3.REPO_ROOT.resolve() or not _lower_hex(source_commit, 40):
        _fail("SOURCE_REVISION_UNVERIFIABLE")
    for entry in dirty.split(b"\0"):
        if not entry:
            continue
        try:
            status = entry[:2].decode("ascii", errors="strict")
            relative = entry[3:].decode("utf-8", errors="strict").rstrip("/")
        except UnicodeError:
            _fail("SOURCE_CHECKOUT_DIRTY")
        if status != "??" or relative not in allowed:
            _fail("SOURCE_CHECKOUT_DIRTY")
    for path in COMMITTED_RUNTIME_PATHS:
        try:
            r3._git_output(r3.REPO_ROOT, ("cat-file", "-e", f"{source_commit}:{path}"))
        except OSError:
            _fail("SOURCE_RUNNER_NOT_COMMITTED")
    return source_commit


def _trusted_source_context(*operational_paths: Path) -> SourceContext:
    try:
        source_commit = _resolve_source_commit_with_operational_paths(operational_paths)
        modules = {
            path: hashlib.sha256(
                r3._git_output(r3.REPO_ROOT, ("show", f"{source_commit}:{path}"))
            ).hexdigest()
            for path in COMMITTED_RUNTIME_PATHS
        }
    except (OSError, r3.R3EvidenceError):
        _fail("SOURCE_REVISION_UNVERIFIABLE")
    if not _lower_hex(source_commit, 40) or any(
        not _lower_hex(digest, 64) for digest in modules.values()
    ) or set(modules) != set(COMMITTED_RUNTIME_PATHS):
        _fail("SOURCE_REVISION_UNVERIFIABLE")
    return SourceContext(source_commit, modules)


def _evidence(context: SourceContext) -> dict[str, object]:
    return {
        "schema": EVIDENCE_SCHEMA,
        "contract_sha256": CONTRACT_SHA256,
        "worker_prompt_sha256": WORKER_PROMPT_SHA256,
        "adapter_id": ADAPTER_ID,
        "caller_id": CALLER_ID,
        "source_commit": context.source_commit,
        "source_modules": context.source_modules,
        "profile_sha256": PROFILE_SHA256,
        "input_identity": {
            "artifact_sha256": LOCKED_ARTIFACT_SHA256,
            "artifact_bytes": LOCKED_ARTIFACT_BYTES,
            "manifest_sha256": LOCKED_MANIFEST_SHA256,
            "manifest_bytes": LOCKED_MANIFEST_BYTES,
            "input_digest": LOCKED_INPUT_DIGEST,
        },
        "baseline_artifact": {
            "relation_to_locked_input": "SAME_PHYSICAL_FILE_AND_BYTES",
            "sha256_before": LOCKED_ARTIFACT_SHA256,
            "sha256_after": LOCKED_ARTIFACT_SHA256,
            "bytes_before": LOCKED_ARTIFACT_BYTES,
            "bytes_after": LOCKED_ARTIFACT_BYTES,
            "same_file_identity_preserved": True,
        },
        "search_space": SEARCH_SPACE,
        "search_space_sha256": SEARCH_SPACE_SHA256,
        "trial_count": 1,
        "trial_ledger_bytes": len(LEDGER_BYTES),
        "trial_ledger_sha256": LEDGER_SHA256,
        "metrics_digest": METRICS_DIGEST,
        "performance_read": False,
        "sealed_holdout_opened": False,
        "unseen_confirmation_available": False,
        "research_limitation": "SEALED_UNSEEN_DATA_UNAVAILABLE",
        "eligible": False,
        "size_zero_required": True,
        "reject_all": True,
        "reported_candidate_id": "baseline_v1",
        "promoted_candidate_id": None,
        "rollback_to_baseline": True,
        "terminal_status": "REJECT_ALL_SIZE_ZERO",
    }


def _manifest_for(evidence: bytes, ledger: bytes, source_commit: str) -> dict[str, object]:
    return {
        "schema": MANIFEST_SCHEMA,
        "source_commit": source_commit,
        "transaction": "WHOLE_DIRECTORY_ATOMIC_RENAME_V1",
        "file_count": 2,
        "files": [
            {"name": "evidence.json", "bytes": len(evidence), "sha256": hashlib.sha256(evidence).hexdigest()},
            {"name": "trial_ledger.jsonl", "bytes": len(ledger), "sha256": hashlib.sha256(ledger).hexdigest()},
        ],
    }


def _expected_package(context: SourceContext) -> ExpectedPackage:
    evidence = _canonical(_evidence(context))
    manifest = _canonical(_manifest_for(evidence, LEDGER_BYTES, context.source_commit))
    return ExpectedPackage(evidence, LEDGER_BYTES, manifest, hashlib.sha256(manifest).hexdigest())


def _read_regular(path: Path) -> bytes:
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            _fail("PACKAGE_INVALID")
        return path.read_bytes()
    except OSError:
        _fail("PACKAGE_INVALID")


def _read_package(directory: Path, expected: ExpectedPackage) -> None:
    try:
        metadata = directory.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            _fail("PACKAGE_INVALID")
        entries = {entry.name for entry in directory.iterdir()}
    except OSError:
        _fail("PACKAGE_INVALID")
    if entries != {"evidence.json", "trial_ledger.jsonl", "package_manifest.json"}:
        _fail("PACKAGE_INVALID")
    evidence = _read_regular(directory / "evidence.json")
    ledger = _read_regular(directory / "trial_ledger.jsonl")
    manifest = _read_regular(directory / "package_manifest.json")
    _strict_json(evidence)
    _strict_json(ledger)
    _strict_json(manifest)
    if evidence != expected.evidence or ledger != expected.ledger or manifest != expected.manifest:
        _fail("PACKAGE_CONTENT_MISMATCH")
    if directory.name != f"package-{expected.manifest_sha256}":
        _fail("PACKAGE_NAME_MISMATCH")


def _write_exclusive(path: Path, raw: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_staging(staging: Path, expected: ExpectedPackage) -> None:
    try:
        staging.mkdir(mode=0o700)
        _write_exclusive(staging / "evidence.json", expected.evidence)
        _write_exclusive(staging / "trial_ledger.jsonl", expected.ledger)
        _write_exclusive(staging / "package_manifest.json", expected.manifest)
        _fsync_directory(staging)
    except OSError:
        _fail("PACKAGE_STAGING_FAILED")


def _demote_published_final(final: Path) -> None:
    demoted = final.with_name(f".tqqq-bounded-optimization-adapter-{uuid4().hex}.demoted")
    try:
        os.rename(final, demoted)
        _fsync_directory(final.parent)
    except OSError:
        _fail("PACKAGE_DEMOTION_FAILED")


def run_verified_current_r3_adapter(output_root: str | Path) -> str:
    """Publish one verified, terminal, no-performance singleton package."""
    context = _trusted_source_context()
    identities_before = _locked_identity_states()
    expected = _expected_package(context)
    root = Path(output_root)
    try:
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink() or not root.is_dir():
            _fail("OUTPUT_ROOT_INVALID")
    except OSError:
        _fail("OUTPUT_ROOT_INVALID")
    final = root / f"package-{expected.manifest_sha256}"
    if final.exists() or final.is_symlink():
        _read_package(final, expected)
        if _trusted_source_context() != context or _locked_identity_states() != identities_before:
            _fail("SOURCE_OR_IDENTITY_CHANGED")
        return expected.manifest_sha256
    staging = root / f".tqqq-bounded-optimization-adapter-{uuid4().hex}.staging"
    _write_staging(staging, expected)
    # Staging is validated with the same strict bytes, without treating its operational name as final.
    _read_staged_package(staging, expected)
    if _trusted_source_context(staging) != context or _locked_identity_states() != identities_before:
        _fail("SOURCE_OR_IDENTITY_CHANGED")
    published_by_this_invocation = False
    try:
        os.rename(staging, final)
    except OSError:
        if final.exists() and not final.is_symlink():
            _read_package(final, expected)
        else:
            _fail("PACKAGE_PUBLISH_FAILED")
    else:
        published_by_this_invocation = True
    try:
        _fsync_directory(root)
    except OSError:
        _fail("PACKAGE_PARENT_FSYNC_FAILED")
    _read_package(final, expected)
    try:
        if (
            (
                _trusted_source_context(final)
                if published_by_this_invocation
                else _trusted_source_context()
            )
            != context
            or _locked_identity_states() != identities_before
        ):
            _fail("SOURCE_OR_IDENTITY_CHANGED")
    except BoundedOptimizationAdapterError:
        if published_by_this_invocation:
            _demote_published_final(final)
        raise
    return expected.manifest_sha256


def _read_staged_package(directory: Path, expected: ExpectedPackage) -> None:
    try:
        metadata = directory.lstat()
        entries = {entry.name for entry in directory.iterdir()}
    except OSError:
        _fail("PACKAGE_INVALID")
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or entries != {
        "evidence.json",
        "trial_ledger.jsonl",
        "package_manifest.json",
    }:
        _fail("PACKAGE_INVALID")
    if (
        _read_regular(directory / "evidence.json") != expected.evidence
        or _read_regular(directory / "trial_ledger.jsonl") != expected.ledger
        or _read_regular(directory / "package_manifest.json") != expected.manifest
    ):
        _fail("PACKAGE_CONTENT_MISMATCH")
    _strict_json(expected.evidence)
    _strict_json(expected.ledger)
    _strict_json(expected.manifest)
