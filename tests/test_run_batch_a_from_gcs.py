from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.run_batch_a_from_gcs import (
    BATCH_A_DATASET_RELATIVE_PATHS,
    GcsBatchAError,
    materialize_from_staging,
    stage_batch_a_inputs,
)
from tests.test_batch_a_v2_contracts import _dates, _write_dataset


def _fake_runner(calls: list[tuple[str, ...]], *, staging_source: Path):
    def run(command: tuple[str, ...], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[1:3] == ("storage", "cp"):
            destination = Path(command[-1])
            object_path = command[4]
            for relative_path in BATCH_A_DATASET_RELATIVE_PATHS:
                if object_path.endswith("/" + relative_path) or object_path.endswith(relative_path):
                    source = staging_source / relative_path
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(source.read_bytes())
                    break
            else:
                raise AssertionError(f"unexpected object {object_path}")
        return subprocess.CompletedProcess(command, 0, "", "")

    return run


def _prepare_source_tree(tmp_path: Path) -> Path:
    dates = _dates(210)
    source = tmp_path / "gcs_mirror"
    layout = tmp_path / "layout"
    sleeves = (
        ("soxl_soxx", ("SOXX", "SOXL"), "batch-a-demo-soxl-soxx"),
        ("tqqq_qqq", ("QQQ", "TQQQ"), "batch-a-demo-tqqq-qqq"),
    )
    for folder, symbols, dataset_id in sleeves:
        dataset = _write_dataset(source, dataset_id=dataset_id, symbols=symbols, dates=dates)
        target = layout / folder
        target.mkdir(parents=True)
        for filename in ("prices.csv", "prices.csv.manifest.json", "object_identity.json"):
            (target / filename).write_bytes((dataset / filename).read_bytes())
    return layout


def test_stage_copies_fixed_batch_a_set(tmp_path: Path) -> None:
    layout = _prepare_source_tree(tmp_path)
    calls: list[tuple[str, ...]] = []
    staged = stage_batch_a_inputs(
        "gs://qsl-research-evidence-831478360303/research/v2/input/batch-a-demo",
        tmp_path / "stage",
        runner=_fake_runner(calls, staging_source=layout),
    )
    assert len(staged) == len(BATCH_A_DATASET_RELATIVE_PATHS)
    assert all("--no-clobber" in call for call in calls)


def test_stage_refuses_nonempty_root(tmp_path: Path) -> None:
    root = tmp_path / "stage"
    root.mkdir()
    (root / "x").write_text("1", encoding="utf-8")
    with pytest.raises(GcsBatchAError, match="STAGING_ROOT_NOT_EMPTY"):
        stage_batch_a_inputs(
            "gs://bucket/input",
            root,
            runner=_fake_runner([], staging_source=tmp_path),
        )


def test_materialize_from_staging_builds_pack(tmp_path: Path) -> None:
    layout = _prepare_source_tree(tmp_path)
    stage = tmp_path / "stage"
    stage_batch_a_inputs(
        "gs://bucket/research/v2/input/batch-a-demo",
        stage,
        runner=_fake_runner([], staging_source=layout),
    )
    pack = materialize_from_staging(stage)
    assert pack["schema_version"] == "qsl.c3-batch-a-frozen-member-pack.v2"
    assert pack["cash_return_policy"] == "ASSUMED_ZERO_USD_CASH"
    assert isinstance(pack["pack_digest"], str) and len(pack["pack_digest"]) == 64
