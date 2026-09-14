from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
from pathlib import Path

import pytest

from us_equity_strategies.research import soxl_alpaca_input_adapter as adapter
from us_equity_strategies.research.soxl_alpaca_input_adapter import (
    SoxlAlpacaInputAdapterError,
    load_soxl_alpaca_input,
    materialize_soxl_alpaca_input,
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _source_root(tmp_path: Path, count: int = 3, calendar_source: str = "exchange_calendars:4.13.2:XNYS") -> Path:
    root = tmp_path / "p1"
    root.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir()
    binding = {
        "schema_version": "qsl.soxl_soxx_core_only_p1_data_binding.v2",
        "data_identity": {
            "provider": "ALPACA_MARKET_DATA",
            "feed": "SIP",
            "calendar": {"calendar_id": "XNYS", "timezone": "America/New_York", "source": calendar_source},
            "adjustment": {"policy": "total_return_adjusted", "source": "ALPACA_MARKET_DATA adjustment=all(split,dividend,spin-off)"},
            "universe": ["SOXL", "SOXX", "BOXX"],
            "date_cutoff": "2026-01-04",
        },
    }
    binding_bytes = _canonical(binding)
    binding_digest = hashlib.sha256(binding_bytes).hexdigest()
    series = {}
    start = date(2026, 1, 2)
    for symbol, base in (("SOXL", 50.0), ("SOXX", 100.0), ("BOXX", 10.0)):
        rows = []
        for index in range(count):
            session = (start + timedelta(days=index)).isoformat()
            close = base + index
            rows.append({"session_date": session, "bar": {"open": close, "high": close + 1.0, "low": close - 1.0, "close": close, "volume": 1000.0}})
        series[symbol] = rows
    bars = {"schema_version": "qsl.soxl-soxx-core-only-adjusted-ohlcv.v2", "series": series}
    bars_bytes = _canonical(bars)
    manifest = {
        "schema_version": "research_input_manifest.v1",
        "profile": "soxl_soxx_core_only_p2_v3",
        "artifact_type": "immutable_adjusted_ohlcv_etf_only",
        "observed_at": "2026-01-08T00:00:00Z",
        "calendar": {"calendar_id": "XNYS", "timezone": "America/New_York", "session_date": "2026-01-04", "source_revision": binding_digest},
        "adjustment": {"policy": "total_return_adjusted", "source": "ALPACA_MARKET_DATA adjustment=all(split,dividend,spin-off)", "source_revision": binding_digest},
        "members": [{"path": "bars.json", "media_type": "application/json", "size_bytes": len(bars_bytes), "sha256": hashlib.sha256(bars_bytes).hexdigest()}],
        "sources": [
            {
                "source_id": f"alpaca_sip_1day_adjustment_all:{symbol}",
                "content_sha256": hashlib.sha256(_canonical({"schema_version": "qsl.soxl-soxx-core-only-adjusted-ohlcv-source.v1", "symbol": symbol, "sessions": series[symbol]})).hexdigest(),
            }
            for symbol in ("SOXL", "SOXX", "BOXX")
        ],
    }
    (root / "binding.json").write_bytes(binding_bytes)
    (root / "bars.json").write_bytes(bars_bytes)
    (root / "manifest.json").write_bytes(_canonical(manifest))
    return root


def test_materialize_alpaca_root_persists_readback_and_loader_accepts(tmp_path: Path) -> None:
    source_root = _source_root(tmp_path)
    paths = materialize_soxl_alpaca_input(
        source_root,
        tmp_path / "pack",
        start="2026-01-02",
        end_exclusive="2026-01-05",
        expected_sessions=3,
    )
    loaded = load_soxl_alpaca_input(paths.manifest, paths.artifact, paths.readback)
    assert loaded.input_digest
    manifest = json.loads(paths.manifest.read_bytes())
    assert manifest["provider"] == "alpaca_sip"
    assert manifest["price_field"] == "adjusted_close"
    assert manifest["provider_completeness"] == "unverified"
    assert manifest["calendar_authority"] == "unverified"
    assert "total_return_adjusted" in manifest["source_revision"]
    assert "adjustment=all" in manifest["source_revision"]
    assert "manifest_sha256=" in manifest["source_revision"]
    readback = json.loads(paths.readback.read_bytes())
    assert readback["raw_persisted_bytes_equal"] is True
    assert readback["raw_sha256_readback_equal"] is True


def test_alpaca_loader_rejects_non_alpaca_manifest(tmp_path: Path) -> None:
    root = _source_root(tmp_path)
    paths = materialize_soxl_alpaca_input(
        root,
        tmp_path / "pack",
        start="2026-01-02",
        end_exclusive="2026-01-05",
        expected_sessions=3,
    )
    manifest = json.loads(paths.manifest.read_bytes())
    manifest["provider"] = "yahoo_chart"
    paths.manifest.write_bytes(_canonical(manifest))
    with pytest.raises(SoxlAlpacaInputAdapterError):
        load_soxl_alpaca_input(paths.manifest, paths.artifact, paths.readback)


def test_alpaca_input_digest_binds_source_identity(tmp_path: Path) -> None:
    first_paths = materialize_soxl_alpaca_input(
        _source_root(tmp_path / "first"),
        tmp_path / "first-pack",
        start="2026-01-02",
        end_exclusive="2026-01-05",
        expected_sessions=3,
    )
    second_paths = materialize_soxl_alpaca_input(
        _source_root(tmp_path / "second", calendar_source="exchange_calendars:4.13.3:XNYS"),
        tmp_path / "second-pack",
        start="2026-01-02",
        end_exclusive="2026-01-05",
        expected_sessions=3,
    )
    first = load_soxl_alpaca_input(first_paths.manifest, first_paths.artifact, first_paths.readback)
    second = load_soxl_alpaca_input(second_paths.manifest, second_paths.artifact, second_paths.readback)
    assert first.source_revision != second.source_revision
    assert first.input_digest != second.input_digest


@pytest.mark.parametrize(
    "mutation",
    (
        lambda binding: binding["data_identity"].update(provider="YAHOO"),
        lambda binding: binding["data_identity"]["adjustment"].update(policy="split_adjusted"),
    ),
)
def test_unsupported_source_identity_fails_closed(tmp_path: Path, mutation) -> None:
    root = _source_root(tmp_path)
    binding = json.loads((root / "binding.json").read_bytes())
    mutation(binding)
    (root / "binding.json").write_bytes(_canonical(binding))
    with pytest.raises(SoxlAlpacaInputAdapterError):
        materialize_soxl_alpaca_input(root, tmp_path / "pack", start="2026-01-02", end_exclusive="2026-01-05", expected_sessions=3)


def test_wrong_slice_size_and_existing_output_are_rejected(tmp_path: Path) -> None:
    root = _source_root(tmp_path)
    with pytest.raises(SoxlAlpacaInputAdapterError):
        materialize_soxl_alpaca_input(root, tmp_path / "pack", start="2026-01-02", end_exclusive="2026-01-05", expected_sessions=2)
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with pytest.raises(SoxlAlpacaInputAdapterError):
        materialize_soxl_alpaca_input(root, output, start="2026-01-02", end_exclusive="2026-01-05", expected_sessions=3)
    assert sentinel.read_text(encoding="utf-8") == "keep"

    output = tmp_path / "pack"
    materialize_soxl_alpaca_input(root, output, start="2026-01-02", end_exclusive="2026-01-05", expected_sessions=3)
    with pytest.raises(SoxlAlpacaInputAdapterError):
        materialize_soxl_alpaca_input(root, output, start="2026-01-02", end_exclusive="2026-01-05", expected_sessions=3)


def test_mkdir_competition_is_not_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _source_root(tmp_path)
    output = tmp_path / "raced"
    original_mkdir = Path.mkdir

    def competing_mkdir(path: Path, *args, **kwargs):
        if path == output:
            original_mkdir(path, *args, **kwargs)
            (path / "keep.txt").write_text("keep", encoding="utf-8")
            raise FileExistsError("competing creator")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(adapter.Path, "mkdir", competing_mkdir)
    with pytest.raises(SoxlAlpacaInputAdapterError):
        materialize_soxl_alpaca_input(root, output, start="2026-01-02", end_exclusive="2026-01-05", expected_sessions=3)
    assert (output / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_member_hash_binding_hash_and_alignment_drift_fail_closed(tmp_path: Path) -> None:
    root = _source_root(tmp_path / "member")
    bars_path = root / "bars.json"
    bars = json.loads(bars_path.read_bytes())
    bars["series"]["SOXL"][0]["bar"]["close"] = 99.0
    bars_path.write_bytes(_canonical(bars))
    with pytest.raises(SoxlAlpacaInputAdapterError):
        materialize_soxl_alpaca_input(root, tmp_path / "member-pack", start="2026-01-02", end_exclusive="2026-01-05", expected_sessions=3)

    root = _source_root(tmp_path / "source")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["sources"][0]["content_sha256"] = "0" * 64
    manifest_path.write_bytes(_canonical(manifest))
    with pytest.raises(SoxlAlpacaInputAdapterError):
        materialize_soxl_alpaca_input(root, tmp_path / "source-pack", start="2026-01-02", end_exclusive="2026-01-05", expected_sessions=3)

    root = _source_root(tmp_path / "binding")
    binding_path = root / "binding.json"
    binding = json.loads(binding_path.read_bytes())
    binding["data_identity"]["date_cutoff"] = "2026-01-08"
    binding_path.write_bytes(_canonical(binding))
    with pytest.raises(SoxlAlpacaInputAdapterError):
        materialize_soxl_alpaca_input(root, tmp_path / "binding-pack", start="2026-01-02", end_exclusive="2026-01-05", expected_sessions=3)

    root = _source_root(tmp_path / "alignment")
    bars_path = root / "bars.json"
    bars = json.loads(bars_path.read_bytes())
    bars["series"]["SOXX"][1]["session_date"] = "2026-01-06"
    bars_bytes = _canonical(bars)
    bars_path.write_bytes(bars_bytes)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["members"][0]["size_bytes"] = len(bars_bytes)
    manifest["members"][0]["sha256"] = hashlib.sha256(bars_bytes).hexdigest()
    source = {"schema_version": "qsl.soxl-soxx-core-only-adjusted-ohlcv-source.v1", "symbol": "SOXX", "sessions": bars["series"]["SOXX"]}
    manifest["sources"][1]["content_sha256"] = hashlib.sha256(_canonical(source)).hexdigest()
    manifest_path.write_bytes(_canonical(manifest))
    with pytest.raises(SoxlAlpacaInputAdapterError):
        materialize_soxl_alpaca_input(root, tmp_path / "alignment-pack", start="2026-01-02", end_exclusive="2026-01-05", expected_sessions=3)
