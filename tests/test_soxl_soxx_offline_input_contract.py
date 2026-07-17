import hashlib
import json
from pathlib import Path

import pytest

from us_equity_strategies.research.soxl_soxx_offline_input_contract import (
    OfflineInputContractError,
    load_offline_input,
)


def fixture(tmp_path: Path):
    rows = "\n".join(
        [
            "symbol,as_of,open,high,low,close,volume",
            "SOXL,2026-01-02,50,51,49,50.25,2000",
            "SOXX,2026-01-02,100,101,99,100.5,1000",
            "SOXL,2026-01-05,51,52,50,51.25,2100",
            "SOXX,2026-01-05,101,102,100,101.5,1100",
        ]
    ) + "\n"
    artifact = tmp_path / "prices.csv"
    raw = rows.encode()
    artifact.write_bytes(raw)
    manifest = {
        "schema": "qsl.research.price_snapshot.v1",
        "research_only": True,
        "provider": "yahoo_chart",
        "price_field": "adjusted_close",
        "provider_completeness": "unverified",
        "calendar_authority": "unverified",
        "canonicalization": "csv.writer_utf8_lf_float17g_v1",
        "source_revision": "yahoo_chart_request_v1",
        "retrieved_at": "2026-07-16T08:00:00Z",
        "symbols": ["SOXX", "SOXL"],
        "request": {"start": "2026-01-02", "end_exclusive": "2026-01-06"},
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "counts": {"SOXX": 2, "SOXL": 2},
        "coverage": {
            "SOXX": {"start": "2026-01-02", "end": "2026-01-05"},
            "SOXL": {"start": "2026-01-02", "end": "2026-01-05"},
        },
    }
    readback = {
        "schema": "qsl.research.price_snapshot_readback.v1",
        "canonical_csv_sha256": manifest["sha256"],
        "row_count": 4,
        "aligned_observations": 2,
        "counts": manifest["counts"],
        "date_set_equal": True,
        "unique_symbol_date_rows": True,
        "deterministic_order": "as_of_ascending_then_symbol_ascending",
        "finite_positive_ohlc": True,
        "finite_nonnegative_volume": True,
        "raw_persisted_bytes_equal": True,
        "raw_sha256_readback_equal": True,
        "round_trip_canonical_bytes_equal": True,
    }
    manifest_path = tmp_path / "prices.csv.manifest.json"
    readback_path = tmp_path / "prices.csv.readback.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")
    readback_path.write_text(json.dumps(readback, sort_keys=True, separators=(",", ":")) + "\n")
    return manifest_path, artifact, readback_path, manifest


def _write_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")


def test_exact_private_contract_and_digest_are_stable(tmp_path: Path) -> None:
    manifest_path, artifact, readback_path, _ = fixture(tmp_path)

    first = load_offline_input(manifest_path, artifact, readback_path)
    second = load_offline_input(manifest_path, artifact, readback_path)

    assert first == second
    assert first.input_digest == second.input_digest
    assert [row.symbol for row in first.rows] == ["SOXL", "SOXX", "SOXL", "SOXX"]


def test_malformed_alignment_canonical_bytes_and_digest_fail_closed(tmp_path: Path) -> None:
    manifest_path, artifact, readback_path, manifest = fixture(tmp_path)
    changed = artifact.read_bytes().replace(b"SOXL,2026-01-05", b"SOXL,2026-01-04")
    artifact.write_bytes(changed)
    manifest["sha256"] = hashlib.sha256(changed).hexdigest()
    manifest["bytes"] = len(changed)
    manifest["coverage"]["SOXL"]["end"] = "2026-01-04"
    _write_manifest(manifest_path, manifest)
    with pytest.raises(OfflineInputContractError):
        load_offline_input(manifest_path, artifact, readback_path)

    manifest_path, artifact, readback_path, manifest = fixture(tmp_path)
    changed = artifact.read_bytes().replace(b"100,101,99,100.5", b"100.0,101,99,100.5")
    artifact.write_bytes(changed)
    manifest["sha256"] = hashlib.sha256(changed).hexdigest()
    manifest["bytes"] = len(changed)
    _write_manifest(manifest_path, manifest)
    with pytest.raises(OfflineInputContractError):
        load_offline_input(manifest_path, artifact, readback_path)

    manifest_path, artifact, readback_path, manifest = fixture(tmp_path)
    manifest["sha256"] = "0" * 64
    _write_manifest(manifest_path, manifest)
    with pytest.raises(OfflineInputContractError):
        load_offline_input(manifest_path, artifact, readback_path)
