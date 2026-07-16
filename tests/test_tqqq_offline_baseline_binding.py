import csv
import hashlib
import json
from pathlib import Path

import pytest

from us_equity_strategies.research.tqqq_offline_baseline import (
    OfflineBaselineContractError,
    load_offline_baseline_input,
)


def _artifact(tmp_path: Path):
    rows = [
        ["symbol", "as_of", "open", "high", "low", "close", "volume"],
        ["QQQ", "2026-01-02", "100", "101", "99", "100.5", "1000"],
        ["TQQQ", "2026-01-02", "50", "51", "49", "50.25", "2000"],
        ["QQQ", "2026-01-05", "101", "102", "100", "101.5", "1100"],
        ["TQQQ", "2026-01-05", "51", "52", "50", "51.25", "2100"],
    ]
    path = tmp_path / "prices.csv"
    raw = "\n".join(",".join(row) for row in rows) + "\n"
    path.write_bytes(raw.encode())
    sha = hashlib.sha256(raw.encode()).hexdigest()
    manifest = {
        "schema": "qsl.research.price_snapshot.v1",
        "research_only": True,
        "provider": "yahoo_chart",
        "price_field": "adjusted_close",
        "provider_completeness": "unverified",
        "calendar_authority": "unverified",
        "source_revision": "yahoo_chart_request_v1",
        "retrieved_at": "2026-07-16T08:00:00Z",
        "symbols": ["QQQ", "TQQQ"],
        "request": {"start": "2026-01-02", "end_exclusive": "2026-01-06"},
        "sha256": sha,
        "bytes": len(raw.encode()),
        "counts": {"QQQ": 2, "TQQQ": 2},
        "coverage": {
            "QQQ": {"start": "2026-01-02", "end": "2026-01-05"},
            "TQQQ": {"start": "2026-01-02", "end": "2026-01-05"},
        },
    }
    manifest_path = tmp_path / "prices.manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")
    return manifest_path, path, manifest


def test_private_binding_validates_and_is_deterministic(tmp_path):
    manifest, artifact, _ = _artifact(tmp_path)
    first = load_offline_baseline_input(manifest, artifact)
    second = load_offline_baseline_input(manifest, artifact)
    assert first == second
    assert first.input_digest == hashlib.sha256(first.canonical_bytes).hexdigest()
    assert first.controls_disabled is True
    assert first.rows[0].symbol == "QQQ"


@pytest.mark.parametrize("field", ["sha256", "bytes", "counts", "coverage"])
def test_manifest_tamper_fails_closed(tmp_path, field):
    manifest_path, artifact, manifest = _artifact(tmp_path)
    manifest[field] = {"bad": 1} if field in {"counts", "coverage"} else 1
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(OfflineBaselineContractError):
        load_offline_baseline_input(manifest_path, artifact)


def test_artifact_tamper_and_misaligned_dates_fail_closed(tmp_path):
    manifest, artifact, _ = _artifact(tmp_path)
    artifact.write_bytes(artifact.read_bytes().replace(b"100.5", b"999.5"))
    with pytest.raises(OfflineBaselineContractError):
        load_offline_baseline_input(manifest, artifact)


def test_unknown_controls_and_nonfinite_values_fail_closed(tmp_path):
    manifest, artifact, data = _artifact(tmp_path)
    data["calendar_authority"] = "trusted"
    manifest.write_text(json.dumps(data))
    with pytest.raises(OfflineBaselineContractError):
        load_offline_baseline_input(manifest, artifact)
    manifest, artifact, _ = _artifact(tmp_path / "nested") if False else _artifact(tmp_path)
    lines = artifact.read_text().replace("50.25", "nan")
    artifact.write_text(lines)
    with pytest.raises(OfflineBaselineContractError):
        load_offline_baseline_input(manifest, artifact)
