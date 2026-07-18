from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
from pathlib import Path

import pytest

import us_equity_strategies.research.tqqq_bounded_optimization_adapter as adapter
from us_equity_strategies.research.r3_joint_evidence import FileIdentity


SOURCE_COMMIT = "a" * 40


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii") + b"\n"


def _identity(path: Path) -> FileIdentity:
    raw = b"synthetic-identity-boundary\n"
    path.write_bytes(raw)
    return FileIdentity(path, hashlib.sha256(raw).hexdigest(), len(raw))


def _context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    artifact = _identity(tmp_path / "artifact")
    manifest = _identity(tmp_path / "manifest")
    monkeypatch.setattr(adapter.r3, "TQQQ_IDENTITIES", (artifact, manifest))
    monkeypatch.setattr(adapter.r3, "TQQQ_SPEC", type("Spec", (), {"input_digest": "b" * 64})())
    monkeypatch.setattr(adapter, "LOCKED_ARTIFACT_SHA256", artifact.sha256)
    monkeypatch.setattr(adapter, "LOCKED_ARTIFACT_BYTES", artifact.byte_count)
    monkeypatch.setattr(adapter, "LOCKED_MANIFEST_SHA256", manifest.sha256)
    monkeypatch.setattr(adapter, "LOCKED_MANIFEST_BYTES", manifest.byte_count)
    monkeypatch.setattr(adapter, "LOCKED_INPUT_DIGEST", "b" * 64)
    monkeypatch.setattr(
        adapter,
        "_trusted_source_context",
        lambda *_operational_paths: adapter.SourceContext(
            SOURCE_COMMIT,
            {path: "c" * 64 for path in adapter.COMMITTED_RUNTIME_PATHS},
        ),
    )


def test_public_api_only_accepts_output_root_and_runner_has_one_production_call() -> None:
    assert tuple(inspect.signature(adapter.run_verified_current_r3_adapter).parameters) == (
        "output_root",
    )
    runner = Path("scripts/run_tqqq_bounded_optimization_adapter.py")
    tree = ast.parse(runner.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "run_verified_current_r3_adapter"
    ]
    assert len(calls) == 1
    assert not calls[0].keywords
    assert len(calls[0].args) == 1
    production_calls = []
    for source in (*Path("src").rglob("*.py"), *Path("scripts").glob("*.py")):
        if source == Path(adapter.__file__) or source == runner:
            continue
        production_calls.extend(
            node
            for node in ast.walk(ast.parse(source.read_text(encoding="utf-8")))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "run_verified_current_r3_adapter"
        )
    assert production_calls == []
    assert "run_private_r3" not in Path(adapter.__file__).read_text(encoding="utf-8")


def test_singleton_bytes_and_terminal_flags_are_exact() -> None:
    assert adapter.CANDIDATE_BYTES == (
        b'{"candidate_id":"baseline_v1","parameters":{},"parent_baseline_artifact_sha256":"'
        b'a40254c7e31d6b49b4a2db5ec57b1b65215a3ab1ee33df879d9e5e2b4dae6551",'
        b'"schema":"qsl.tqqq.bounded_optimization_adapter.candidate.v2"}\n'
    )
    assert len(adapter.CANDIDATE_BYTES) == 210
    assert hashlib.sha256(adapter.CANDIDATE_BYTES).hexdigest() == adapter.CANDIDATE_SHA256
    assert adapter.LEDGER_BYTES.endswith(b"\n") and len(adapter.LEDGER_BYTES) == 442
    assert hashlib.sha256(adapter.LEDGER_BYTES).hexdigest() == adapter.LEDGER_SHA256
    assert adapter._strict_json(adapter.LEDGER_BYTES)["eligible"] is False
    assert adapter._strict_json(adapter.LEDGER_BYTES)["size_zero_required"] is True


def test_source_commit_and_module_digests_require_exact_lowercase_lengths() -> None:
    assert adapter._lower_hex("a" * 40, 40)
    assert adapter._lower_hex("b" * 64, 64)
    assert not adapter._lower_hex("a" * 40, 64)
    assert not adapter._lower_hex(True, 40)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"a":1}\n',
        b'{"a":NaN}\n',
        b'{"a":1}',
        b'{"a":1}\n\n',
        b'{"a":1}\xff',
        b'{"a": 1}\n',
    ],
)
def test_strict_json_rejects_duplicate_noncanonical_or_non_ascii(raw: bytes) -> None:
    with pytest.raises(adapter.BoundedOptimizationAdapterError):
        adapter._strict_json(raw)


def test_reader_rejects_bool_counts_and_rewritten_rows_after_recomputed_hashes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _context(monkeypatch, tmp_path)
    package_id = adapter.run_verified_current_r3_adapter(tmp_path / "out")
    package = tmp_path / "out" / f"package-{package_id}"
    evidence = adapter._strict_json((package / "evidence.json").read_bytes())
    evidence["trial_count"] = True
    (package / "evidence.json").write_bytes(_canonical(evidence))
    manifest = adapter._manifest_for(
        (package / "evidence.json").read_bytes(), (package / "trial_ledger.jsonl").read_bytes(), SOURCE_COMMIT
    )
    (package / "package_manifest.json").write_bytes(_canonical(manifest))
    with pytest.raises(adapter.BoundedOptimizationAdapterError):
        adapter._read_package(package, adapter._expected_package(adapter._trusted_source_context()))


def test_rewritten_ledger_field_is_rejected_even_when_manifest_is_recomputed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _context(monkeypatch, tmp_path)
    package_id = adapter.run_verified_current_r3_adapter(tmp_path / "out")
    package = tmp_path / "out" / f"package-{package_id}"
    ledger = adapter._strict_json((package / "trial_ledger.jsonl").read_bytes())
    ledger["end_status"] = "FAILED"
    (package / "trial_ledger.jsonl").write_bytes(_canonical(ledger))
    manifest = adapter._manifest_for(
        (package / "evidence.json").read_bytes(), (package / "trial_ledger.jsonl").read_bytes(), SOURCE_COMMIT
    )
    (package / "package_manifest.json").write_bytes(_canonical(manifest))
    with pytest.raises(adapter.BoundedOptimizationAdapterError):
        adapter._read_package(package, adapter._expected_package(adapter._trusted_source_context()))


def test_identity_requires_same_regular_file_and_preserves_inode_and_bytes(tmp_path: Path) -> None:
    artifact = _identity(tmp_path / "artifact")
    before = adapter._read_identity(artifact)
    after = adapter._read_identity(artifact)
    assert before == after
    replacement = tmp_path / "replacement"
    replacement.write_bytes(artifact.path.read_bytes())
    original_open = adapter.os.open

    def replaced_open(path: Path, flags: int) -> int:
        if path == artifact.path:
            os.replace(replacement, artifact.path)
        return original_open(path, flags)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(adapter.os, "open", replaced_open)
        with pytest.raises(adapter.BoundedOptimizationAdapterError):
            adapter._read_identity(artifact)
    link = tmp_path / "link"
    link.symlink_to(artifact.path)
    with pytest.raises(adapter.BoundedOptimizationAdapterError):
        adapter._read_identity(FileIdentity(link, artifact.sha256, artifact.byte_count))


def test_whole_directory_publish_is_idempotent_and_staging_is_not_authoritative(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _context(monkeypatch, tmp_path)
    root = tmp_path / "out"
    stale = root / ".tqqq-bounded-optimization-adapter-stale.staging"
    stale.mkdir(parents=True)
    (stale / "junk").write_text("not authoritative", encoding="ascii")
    package_id = adapter.run_verified_current_r3_adapter(root)
    assert adapter.run_verified_current_r3_adapter(root) == package_id
    package = root / f"package-{package_id}"
    assert {entry.name for entry in package.iterdir()} == {
        "evidence.json",
        "trial_ledger.jsonl",
        "package_manifest.json",
    }
    assert stale.exists()


def test_partial_existing_package_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _context(monkeypatch, tmp_path)
    expected = adapter._expected_package(adapter._trusted_source_context())
    broken = tmp_path / "out" / f"package-{expected.manifest_sha256}"
    broken.mkdir(parents=True)
    (broken / "evidence.json").write_bytes(expected.evidence)
    with pytest.raises(adapter.BoundedOptimizationAdapterError):
        adapter.run_verified_current_r3_adapter(tmp_path / "out")


def test_staging_failure_leaves_no_final_package(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _context(monkeypatch, tmp_path)
    expected = adapter._expected_package(adapter._trusted_source_context())
    monkeypatch.setattr(adapter, "_write_exclusive", lambda *_args: (_ for _ in ()).throw(OSError()))
    with pytest.raises(adapter.BoundedOptimizationAdapterError):
        adapter.run_verified_current_r3_adapter(tmp_path / "out")
    assert not (tmp_path / "out" / f"package-{expected.manifest_sha256}").exists()


def test_no_provider_or_performance_or_baseline_evaluator_is_called(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _context(monkeypatch, tmp_path)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("forbidden execution")

    monkeypatch.setattr(adapter.r3, "run_private_r3", forbidden)
    monkeypatch.setattr(adapter.r3, "_build_bundle", forbidden)
    monkeypatch.setattr(adapter.r3, "_evaluate_loaded", forbidden)
    monkeypatch.setattr(adapter.r3, "_zero_invariant", forbidden)
    package_id = adapter.run_verified_current_r3_adapter(tmp_path / "out")
    assert len(package_id) == 64


def test_in_repo_output_allows_only_its_staging_directory_during_reverification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _context(monkeypatch, tmp_path)
    observed: list[tuple[Path, ...]] = []

    def source_context(*operational_paths: Path) -> adapter.SourceContext:
        observed.append(operational_paths)
        return adapter.SourceContext(
            SOURCE_COMMIT,
            {path: "c" * 64 for path in adapter.COMMITTED_RUNTIME_PATHS},
        )

    monkeypatch.setattr(adapter, "_trusted_source_context", source_context)
    adapter.run_verified_current_r3_adapter(tmp_path / "repo-output")
    assert any(
        len(paths) == 1 and paths[0].name.endswith(".staging") for paths in observed
    )
    assert all(len(paths) <= 1 for paths in observed)


def test_parent_fsync_failure_after_rename_never_reports_publish_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _context(monkeypatch, tmp_path)
    root = tmp_path / "out"
    original_fsync = adapter._fsync_directory

    def fail_final_parent(directory: Path) -> None:
        if directory == root:
            raise OSError("parent fsync failed")
        original_fsync(directory)

    monkeypatch.setattr(adapter, "_fsync_directory", fail_final_parent)
    with pytest.raises(adapter.BoundedOptimizationAdapterError, match="PACKAGE_PARENT_FSYNC_FAILED"):
        adapter.run_verified_current_r3_adapter(root)


def test_late_source_failure_demotes_only_this_invocations_final_package(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _context(monkeypatch, tmp_path)
    calls = 0

    def source_context(*_operational_paths: Path) -> adapter.SourceContext:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise adapter.BoundedOptimizationAdapterError("SOURCE_OR_IDENTITY_CHANGED")
        return adapter.SourceContext(
            SOURCE_COMMIT,
            {path: "c" * 64 for path in adapter.COMMITTED_RUNTIME_PATHS},
        )

    monkeypatch.setattr(adapter, "_trusted_source_context", source_context)
    expected = adapter._expected_package(source_context())
    calls = 0
    root = tmp_path / "out"
    with pytest.raises(adapter.BoundedOptimizationAdapterError, match="SOURCE_OR_IDENTITY_CHANGED"):
        adapter.run_verified_current_r3_adapter(root)
    assert not (root / f"package-{expected.manifest_sha256}").exists()
    assert any(path.name.endswith(".demoted") for path in root.iterdir())


def test_late_failure_never_demotes_an_exact_race_winner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _context(monkeypatch, tmp_path)
    expected = adapter._expected_package(adapter._trusted_source_context())
    root = tmp_path / "out"
    final = root / f"package-{expected.manifest_sha256}"
    original_rename = adapter.os.rename

    def race_winner(source: Path, destination: Path) -> None:
        if destination == final:
            final.mkdir(parents=True)
            (final / "evidence.json").write_bytes(expected.evidence)
            (final / "trial_ledger.jsonl").write_bytes(expected.ledger)
            (final / "package_manifest.json").write_bytes(expected.manifest)
            raise OSError("race winner")
        original_rename(source, destination)

    monkeypatch.setattr(adapter.os, "rename", race_winner)
    assert adapter.run_verified_current_r3_adapter(root) == expected.manifest_sha256
    assert final.exists()
    assert not any(path.name.endswith(".demoted") for path in root.iterdir())
