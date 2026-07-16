import hashlib
import json
from pathlib import Path

import pytest

from us_equity_strategies.research.tqqq_offline_input_contract import (
    OfflineInputContractError,
    load_offline_input,
)


def fixture(tmp_path: Path):
    rows = "\n".join([
        "symbol,as_of,open,high,low,close,volume",
        "QQQ,2026-01-02,100,101,99,100.5,1000",
        "TQQQ,2026-01-02,50,51,49,50.25,2000",
        "QQQ,2026-01-05,101,102,100,101.5,1100",
        "TQQQ,2026-01-05,51,52,50,51.25,2100",
    ]) + "\n"
    artifact = tmp_path / "prices.csv"
    raw = rows.encode()
    artifact.write_bytes(raw)
    manifest = {
        "schema": "qsl.research.price_snapshot.v1", "research_only": True,
        "provider": "yahoo_chart", "price_field": "adjusted_close",
        "provider_completeness": "unverified", "calendar_authority": "unverified",
        "source_revision": "yahoo_chart_request_v1", "retrieved_at": "2026-07-16T08:00:00Z",
        "symbols": ["QQQ", "TQQQ"], "request": {"start": "2026-01-02", "end_exclusive": "2026-01-06"},
        "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
        "counts": {"QQQ": 2, "TQQQ": 2},
        "coverage": {"QQQ": {"start": "2026-01-02", "end": "2026-01-05"}, "TQQQ": {"start": "2026-01-02", "end": "2026-01-05"}},
    }
    mp = tmp_path / "prices.manifest.json"
    mp.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")
    return mp, artifact, manifest


def test_exact_contract_and_digest_are_stable(tmp_path):
    mp, artifact, _ = fixture(tmp_path)
    a = load_offline_input(mp, artifact)
    b = load_offline_input(mp, artifact)
    assert a == b
    assert a.input_digest == b.input_digest
    assert a.rows[0].symbol == "QQQ"


def test_ohlc_range_and_date_set_fail_closed(tmp_path):
    mp, artifact, manifest = fixture(tmp_path)
    artifact.write_text(artifact.read_text().replace("100,101,99,100.5", "100,99,99,100.5"))
    with pytest.raises(OfflineInputContractError):
        load_offline_input(mp, artifact)
    mp, artifact, manifest = fixture(tmp_path)
    artifact.write_text(artifact.read_text().replace("TQQQ,2026-01-05", "TQQQ,2026-01-04"))
    with pytest.raises(OfflineInputContractError):
        load_offline_input(mp, artifact)


def test_manifest_window_and_digest_binding_fail_closed(tmp_path):
    mp, artifact, manifest = fixture(tmp_path)
    manifest["request"]["start"] = "2026-01-03"
    mp.write_text(json.dumps(manifest))
    with pytest.raises(OfflineInputContractError):
        load_offline_input(mp, artifact)
    mp, artifact, manifest = fixture(tmp_path)
    manifest["source_revision"] = "changed"
    mp.write_text(json.dumps(manifest))
    changed = load_offline_input(mp, artifact)
    assert changed.input_digest != load_offline_input(*fixture(tmp_path)[:2]).input_digest

def test_retrieved_at_is_bound_to_input_digest(tmp_path):
    mp, artifact, manifest = fixture(tmp_path)
    first = load_offline_input(mp, artifact)
    manifest["retrieved_at"] = "2026-07-16T09:00:00Z"
    mp.write_text(json.dumps(manifest))
    second = load_offline_input(mp, artifact)
    assert second.input_digest != first.input_digest
