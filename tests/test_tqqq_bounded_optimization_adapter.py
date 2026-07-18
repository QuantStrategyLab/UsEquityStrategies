from __future__ import annotations

import ast
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest


MODULE_NAME = "us_equity_strategies.research.tqqq_bounded_optimization_adapter"
ADAPTER_PATH = "src/us_equity_strategies/research/tqqq_bounded_optimization_adapter.py"
R3_PATH = "src/us_equity_strategies/research/r3_joint_evidence.py"
ARTIFACT_SHA256 = "a40254c7e31d6b49b4a2db5ec57b1b65215a3ab1ee33df879d9e5e2b4dae6551"
MANIFEST_SHA256 = "8ecbc864f356af94464249ee3003d44fb00cf739c6810dc2de14165e5dc3500d"
SEMANTIC_DIGEST = "8cc682b2d1acc23a8dd93c3bfd67b445d7305844d2c4d254f4f52e0ac817c6cb"


def _adapter():
    return importlib.import_module(MODULE_NAME)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def _commit_all(repo: Path, message: str) -> None:
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", message)


def _identity(path: Path, expected_sha256: str, expected_bytes: int) -> SimpleNamespace:
    return SimpleNamespace(path=path, sha256=expected_sha256, byte_count=expected_bytes)


@pytest.fixture
def synthetic_repo(tmp_path: Path) -> tuple[Path, SimpleNamespace, Path, Path]:
    repo = tmp_path / "repo"
    (repo / "src/us_equity_strategies/research").mkdir(parents=True)
    source_adapter = Path(__file__).parents[1] / ADAPTER_PATH
    shutil.copyfile(source_adapter, repo / ADAPTER_PATH)
    (repo / R3_PATH).write_text("R3_IDENTITY = 'synthetic'\n", encoding="ascii")
    artifact = tmp_path / "artifact.csv"
    manifest = tmp_path / "artifact.csv.manifest.json"
    artifact.write_bytes(b"artifact")
    manifest.write_bytes(b"manifest")
    r3 = SimpleNamespace(
        TQQQ_IDENTITIES=(
            _identity(artifact, hashlib.sha256(b"artifact").hexdigest(), len(b"artifact")),
            _identity(manifest, hashlib.sha256(b"manifest").hexdigest(), len(b"manifest")),
        ),
        TQQQ_SPEC=SimpleNamespace(input_digest=SEMANTIC_DIGEST),
    )
    _git(repo, "init")
    _commit_all(repo, "initial")
    return repo, r3, artifact, manifest


def _build(
    repo: Path,
    r3: SimpleNamespace,
    *,
    after_first_check=None,
) -> tuple[bytes, str]:
    module = _adapter()
    return module._build_verified_identity_bundle_for_testing(
        repo_root=repo,
        r3_module=r3,
        adapter_path=repo / ADAPTER_PATH,
        after_first_check=after_first_check,
    )


def _assert_code(exc: pytest.ExceptionInfo[BaseException], code: str) -> None:
    assert str(exc.value) == code
    assert getattr(exc.value, "code") == code


def test_public_api_has_no_parameters_and_returns_exact_bytes_and_digest() -> None:
    module = _adapter()
    assert tuple(inspect.signature(module.build_verified_current_r3_identity_bundle).parameters) == ()
    assert inspect.signature(module.build_verified_current_r3_identity_bundle).return_annotation == "tuple[bytes, str]"


def test_clean_committed_synthetic_source_returns_canonical_identity_bundle(
    synthetic_repo: tuple[Path, SimpleNamespace, Path, Path],
) -> None:
    repo, r3, _, _ = synthetic_repo
    bundle, digest = _build(repo, r3)
    decoded = json.loads(bundle)
    assert bundle.endswith(b"\n")
    assert digest == hashlib.sha256(bundle).hexdigest()
    assert decoded == {
        "adapter_id": "us_equity_strategies.research.tqqq_bounded_optimization_adapter.build_verified_current_r3_identity_bundle",
        "eligible": False,
        "input_identity": {
            "artifact_bytes": 8,
            "artifact_sha256": hashlib.sha256(b"artifact").hexdigest(),
            "manifest_bytes": 8,
            "manifest_sha256": hashlib.sha256(b"manifest").hexdigest(),
            "semantic_input_digest": SEMANTIC_DIGEST,
        },
        "operation": "IDENTITY_ONLY",
        "parameter_search_performed": False,
        "performance_read": False,
        "provider_access": False,
        "schema": "qsl.tqqq.bounded_optimization_adapter.bundle.v3",
        "sealed_holdout_opened": False,
        "size_zero_required": True,
        "source_commit": _git(repo, "rev-parse", "HEAD"),
        "source_modules": {
            ADAPTER_PATH: hashlib.sha256((repo / ADAPTER_PATH).read_bytes()).hexdigest(),
            R3_PATH: hashlib.sha256((repo / R3_PATH).read_bytes()).hexdigest(),
        },
        "strategy_id": "TQQQ",
        "terminal_status": "IDENTITY_ONLY_SIZE_ZERO",
    }
    assert bundle == json.dumps(decoded, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii") + b"\n"
    assert _build(repo, r3) == (bundle, digest)


@pytest.mark.parametrize("mutation", ["dirty", "staged", "untracked", "missing", "blob_mismatch"])
def test_source_identity_failures_are_closed(
    synthetic_repo: tuple[Path, SimpleNamespace, Path, Path], mutation: str
) -> None:
    repo, r3, _, _ = synthetic_repo
    target = repo / (R3_PATH if mutation != "missing" else ADAPTER_PATH)
    if mutation == "missing":
        target.unlink()
    else:
        target.write_text("mutated\n", encoding="ascii")
        if mutation == "staged":
            _git(repo, "add", str(target.relative_to(repo)))
        elif mutation == "blob_mismatch":
            _git(repo, "add", str(target.relative_to(repo)))
            _commit_all(repo, "changed")
            target.write_text("working mismatch\n", encoding="ascii")
        elif mutation == "untracked":
            (repo / "untracked.txt").write_text("x", encoding="ascii")
    with pytest.raises(_adapter().IdentityBundleError) as exc:
        _build(repo, r3)
    _assert_code(exc, "SOURCE_IDENTITY_INVALID")


def test_wrong_root_and_uncommitted_source_fail_closed(
    synthetic_repo: tuple[Path, SimpleNamespace, Path, Path],
) -> None:
    repo, r3, _, _ = synthetic_repo
    with pytest.raises(_adapter().IdentityBundleError) as exc:
        _build(repo / "not-root", r3)
    _assert_code(exc, "SOURCE_IDENTITY_INVALID")
    (repo / R3_PATH).write_text("uncommitted\n", encoding="ascii")
    with pytest.raises(_adapter().IdentityBundleError) as exc:
        _build(repo, r3)
    _assert_code(exc, "SOURCE_IDENTITY_INVALID")


@pytest.mark.parametrize("index, replacement", [(0, b"wrong"), (1, b"wrong")])
def test_fixed_input_digest_and_byte_count_are_enforced(
    synthetic_repo: tuple[Path, SimpleNamespace, Path, Path], index: int, replacement: bytes
) -> None:
    repo, r3, artifact, manifest = synthetic_repo
    (artifact, manifest)[index].write_bytes(replacement)
    with pytest.raises(_adapter().IdentityBundleError) as exc:
        _build(repo, r3)
    _assert_code(exc, "INPUT_IDENTITY_INVALID")


def test_input_identity_requires_regular_non_symlink_files(
    synthetic_repo: tuple[Path, SimpleNamespace, Path, Path],
) -> None:
    repo, r3, artifact, _ = synthetic_repo
    linked = artifact.with_name("artifact-link")
    linked.symlink_to(artifact)
    r3.TQQQ_IDENTITIES = (_identity(linked, r3.TQQQ_IDENTITIES[0].sha256, 8), r3.TQQQ_IDENTITIES[1])
    with pytest.raises(_adapter().IdentityBundleError) as exc:
        _build(repo, r3)
    _assert_code(exc, "INPUT_IDENTITY_INVALID")


def test_source_and_input_changes_between_validation_points_prevent_return(
    synthetic_repo: tuple[Path, SimpleNamespace, Path, Path],
) -> None:
    repo, r3, artifact, _ = synthetic_repo
    with pytest.raises(_adapter().IdentityBundleError) as exc:
        _build(repo, r3, after_first_check=lambda: artifact.write_bytes(b"changed"))
    _assert_code(exc, "INPUT_IDENTITY_INVALID")

    artifact.write_bytes(b"artifact")
    repo, r3, _, _ = synthetic_repo
    with pytest.raises(_adapter().IdentityBundleError) as exc:
        _build(repo, r3, after_first_check=lambda: (repo / R3_PATH).write_text("changed\n", encoding="ascii"))
    _assert_code(exc, "SOURCE_IDENTITY_INVALID")


def test_identity_constants_and_integer_type_are_strict(
    synthetic_repo: tuple[Path, SimpleNamespace, Path, Path],
) -> None:
    repo, r3, _, _ = synthetic_repo
    r3.TQQQ_IDENTITIES = (
        _identity(r3.TQQQ_IDENTITIES[0].path, ARTIFACT_SHA256, True),
        r3.TQQQ_IDENTITIES[1],
    )
    with pytest.raises(_adapter().IdentityBundleError) as exc:
        _build(repo, r3)
    _assert_code(exc, "INPUT_IDENTITY_INVALID")


def test_errors_and_bundle_do_not_expose_private_paths_or_raw_input_values(
    synthetic_repo: tuple[Path, SimpleNamespace, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, r3, artifact, _ = synthetic_repo
    bundle, _ = _build(repo, r3)
    assert str(artifact).encode() not in bundle
    artifact.write_bytes(b"private raw value")
    with pytest.raises(_adapter().IdentityBundleError) as exc:
        _build(repo, r3)
    assert str(artifact) not in str(exc.value)
    assert b"private raw value" not in capsys.readouterr().out.encode()


def test_static_boundary_excludes_persistence_callers_and_forbidden_work(
    synthetic_repo: tuple[Path, SimpleNamespace, Path, Path],
) -> None:
    repo, _, _, _ = synthetic_repo
    source = (repo / ADAPTER_PATH).read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Attribute, ast.Name))
        and (isinstance(node.func, ast.Name) or isinstance(node.func.attr, str))
    }
    forbidden = {
        "mkdir", "write_bytes", "write_text", "rename", "replace", "unlink", "fsync",
        "run_private_r3", "_build_bundle", "_attempt_private_strategy", "_load_tqqq",
        "_evaluate_loaded", "run_typed_baseline", "persist_bundle", "load_persisted_bundle",
    }
    assert not calls & forbidden
    assert not any(token in source for token in ("output_root", "sidecar", "ledger", "staging", "candidate"))
    assert "build_verified_current_r3_identity_bundle(" not in (repo / R3_PATH).read_text(encoding="ascii")
