from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.run_r3_from_gcs import (
    GcsR3Error,
    R3_INPUT_RELATIVE_PATHS,
    build_preflight_command,
    stage_r3_inputs,
    stage_support_file,
)


def _fake_runner(calls: list[tuple[str, ...]]):
    def run(command: tuple[str, ...], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[1:3] == ("storage", "cp"):
            destination = Path(command[-1])
            destination.write_bytes(b"fixture")
        return subprocess.CompletedProcess(command, 0, "", "")

    return run


def test_stage_copies_only_the_fixed_r3_input_set(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []
    staged = stage_r3_inputs(
        "gs://qsl-research-evidence-831478360303/research/v1/input/r3",
        tmp_path / "stage",
        runner=_fake_runner(calls),
    )

    assert tuple(path.relative_to(tmp_path / "stage").as_posix() for path in staged) == (
        *R3_INPUT_RELATIVE_PATHS,
    )
    assert len(calls) == len(R3_INPUT_RELATIVE_PATHS)
    assert all("--no-clobber" in call for call in calls)
    assert all(call[4].startswith("gs://qsl-research-evidence-831478360303/") for call in calls)


def test_stage_refuses_nonempty_root(tmp_path: Path) -> None:
    root = tmp_path / "stage"
    root.mkdir()
    (root / "unexpected").write_text("x", encoding="utf-8")

    with pytest.raises(GcsR3Error, match="STAGING_ROOT_NOT_EMPTY"):
        stage_r3_inputs("gs://bucket/input", root, runner=_fake_runner([]))


def test_support_file_refuses_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "contract.md"
    target.write_text("existing", encoding="utf-8")

    with pytest.raises(GcsR3Error, match="STAGING_DESTINATION_EXISTS"):
        stage_support_file("gs://bucket/contract.md", target, runner=_fake_runner([]))


def test_preflight_command_uses_explicit_paths(tmp_path: Path) -> None:
    script = tmp_path / "repo" / "scripts" / "run_r3_joint_evidence.py"
    script.parent.mkdir(parents=True)
    script.write_text("# fixture\n", encoding="utf-8")
    command = build_preflight_command(
        tmp_path / "repo",
        tmp_path / "stage",
        tmp_path / "contract.md",
        tmp_path / "worker-prompt.md",
    )

    assert command[2:] == (
        "--preflight",
        "--private-root",
        str(tmp_path / "stage"),
        "--contract-path",
        str(tmp_path / "contract.md"),
        "--worker-prompt-path",
        str(tmp_path / "worker-prompt.md"),
    )
