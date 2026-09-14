"""Convert one verified UESP Alpaca P1 root into the existing UES input shape.

This is an offline, source-preserving bridge.  It checks the small set of
binding, manifest, member, and source-hash relationships needed to avoid
silently changing the provider or adjustment basis, then emits the existing
CSV/manifest/readback contract for ``load_offline_input``.  It does not claim
that a digest proves an external provider's truth and never contacts Alpaca.
"""

from __future__ import annotations

from dataclasses import dataclass
import csv
from datetime import date
import hashlib
import json
import math
from collections.abc import Mapping
from pathlib import Path
import shutil
from typing import Any

from us_equity_strategies.research import soxl_soxx_offline_input_contract as _offline_contract


SOURCE_BINDING_SCHEMA = "qsl.soxl_soxx_core_only_p1_data_binding.v2"
SOURCE_MANIFEST_SCHEMA = "research_input_manifest.v1"
BARS_SCHEMA = "qsl.soxl-soxx-core-only-adjusted-ohlcv.v2"
SOURCE_SERIES_SCHEMA = "qsl.soxl-soxx-core-only-adjusted-ohlcv-source.v1"
SYMBOLS = ("SOXX", "SOXL")
SOURCE_SYMBOLS = ("SOXL", "SOXX", "BOXX")
CSV_COLUMNS = ("symbol", "as_of", "open", "high", "low", "close", "volume")


class SoxlAlpacaInputAdapterError(ValueError):
    """Sanitized malformed or unsupported source-package error."""


@dataclass(frozen=True)
class SoxlAlpacaInputPaths:
    """Paths for one create-only materialized UES input pack."""

    manifest: Path
    artifact: Path
    readback: Path


def _fail() -> None:
    raise SoxlAlpacaInputAdapterError("invalid Alpaca SOXL input source package") from None


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        _fail()
    return dict(value)


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError):
        _fail()


def _json_file(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = _mapping(json.loads(raw.decode()))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail()
    if raw != _canonical(value):
        _fail()
    return value, raw


def _text(value: object) -> str:
    if not isinstance(value, str) or not value or any(ord(char) < 0x20 for char in value):
        _fail()
    return value


def _session(value: object) -> str:
    value = _text(value)
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        _fail()
    if parsed.isoformat() != value:
        _fail()
    return value


def _number(value: object, *, positive: bool = False, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail()
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0.0) or (nonnegative and number < 0.0):
        _fail()
    return 0.0 if number == 0.0 else number


def _bar(value: object) -> dict[str, float]:
    value = _mapping(value)
    if set(value) != {"open", "high", "low", "close", "volume"}:
        _fail()
    bar = {
        "open": _number(value["open"], positive=True),
        "high": _number(value["high"], positive=True),
        "low": _number(value["low"], positive=True),
        "close": _number(value["close"], positive=True),
        "volume": _number(value["volume"], nonnegative=True),
    }
    if bar["low"] > min(bar["open"], bar["close"]) or bar["high"] < max(bar["open"], bar["close"]) or bar["high"] < bar["low"]:
        _fail()
    return bar


def _source_series_bytes(symbol: str, sessions: list[dict[str, object]]) -> bytes:
    return _canonical({"schema_version": SOURCE_SERIES_SCHEMA, "symbol": symbol, "sessions": sessions})


def _read_source_root(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, str]:
    binding, binding_bytes = _json_file(root / "binding.json")
    manifest, manifest_bytes = _json_file(root / "manifest.json")
    try:
        bars_bytes = (root / "bars.json").read_bytes()
        bars = _mapping(json.loads(bars_bytes.decode()))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail()
    if (
        binding.get("schema_version") != SOURCE_BINDING_SCHEMA
        or manifest.get("schema_version") != SOURCE_MANIFEST_SCHEMA
        or bars.get("schema_version") != BARS_SCHEMA
        or bars_bytes != _canonical(bars)
    ):
        _fail()
    identity = _mapping(binding.get("data_identity"))
    calendar = _mapping(identity.get("calendar"))
    adjustment = _mapping(identity.get("adjustment"))
    if (
        identity.get("provider") != "ALPACA_MARKET_DATA"
        or identity.get("feed") != "SIP"
        or calendar.get("calendar_id") != "XNYS"
        or calendar.get("timezone") != "America/New_York"
        or adjustment.get("policy") != "total_return_adjusted"
        or "adjustment=all" not in str(adjustment.get("source"))
        or identity.get("universe") != list(SOURCE_SYMBOLS)
    ):
        _fail()
    binding_digest = hashlib.sha256(binding_bytes).hexdigest()
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    manifest_calendar = _mapping(manifest.get("calendar"))
    manifest_adjustment = _mapping(manifest.get("adjustment"))
    if (
        manifest.get("profile") != "soxl_soxx_core_only_p2_v3"
        or manifest.get("artifact_type") != "immutable_adjusted_ohlcv_etf_only"
        or manifest_calendar.get("calendar_id") != "XNYS"
        or manifest_calendar.get("timezone") != "America/New_York"
        or manifest_calendar.get("session_date") != identity.get("date_cutoff")
        or manifest_calendar.get("source_revision") != binding_digest
        or manifest_adjustment.get("policy") != "total_return_adjusted"
        or "adjustment=all" not in str(manifest_adjustment.get("source"))
        or manifest_adjustment.get("source_revision") != binding_digest
        or _text(manifest.get("observed_at")) == ""
    ):
        _fail()
    members = manifest.get("members")
    if members != [{"path": "bars.json", "media_type": "application/json", "size_bytes": len(bars_bytes), "sha256": hashlib.sha256(bars_bytes).hexdigest()}]:
        _fail()
    return binding, manifest, bars, binding_digest, manifest_digest


def _series(bars: Mapping[str, Any]) -> dict[str, list[dict[str, object]]]:
    raw_series = _mapping(bars.get("series"))
    if set(raw_series) != set(SOURCE_SYMBOLS):
        _fail()
    result: dict[str, list[dict[str, object]]] = {}
    for symbol in SOURCE_SYMBOLS:
        raw_rows = raw_series[symbol]
        if not isinstance(raw_rows, list) or not raw_rows:
            _fail()
        rows: list[dict[str, object]] = []
        previous: str | None = None
        for raw_row in raw_rows:
            row = _mapping(raw_row)
            if set(row) != {"session_date", "bar"}:
                _fail()
            session = _session(row["session_date"])
            if previous is not None and session <= previous:
                _fail()
            previous = session
            rows.append({"session_date": session, "bar": _bar(row["bar"])})
        result[symbol] = rows
    if [row["session_date"] for row in result["SOXL"]] != [row["session_date"] for row in result["SOXX"]]:
        _fail()
    return result


def _verify_source_hashes(manifest: Mapping[str, Any], series: Mapping[str, list[dict[str, object]]]) -> None:
    sources = manifest.get("sources")
    if not isinstance(sources, list):
        _fail()
    declared = {_mapping(source).get("source_id"): _mapping(source).get("content_sha256") for source in sources}
    if set(declared) != {f"alpaca_sip_1day_adjustment_all:{symbol}" for symbol in SOURCE_SYMBOLS}:
        _fail()
    for symbol in SOURCE_SYMBOLS:
        expected = hashlib.sha256(_source_series_bytes(symbol, series[symbol])).hexdigest()
        if declared.get(f"alpaca_sip_1day_adjustment_all:{symbol}") != expected:
            _fail()


def _manifest(path: Path) -> dict[str, Any]:
    data, _ = _json_file(path)
    expected = {
        "schema",
        "research_only",
        "provider",
        "price_field",
        "provider_completeness",
        "calendar_authority",
        "canonicalization",
        "source_revision",
        "retrieved_at",
        "symbols",
        "request",
        "sha256",
        "bytes",
        "counts",
        "coverage",
    }
    if set(data) != expected or data["schema"] != _offline_contract.SCHEMA or data["research_only"] is not True:
        _fail()
    if (
        data["provider"],
        data["price_field"],
        data["provider_completeness"],
        data["calendar_authority"],
    ) != ("alpaca_sip", "adjusted_close", "unverified", "unverified"):
        _fail()
    if data["canonicalization"] != "csv.writer_utf8_lf_float17g_v1":
        _fail()
    source_revision = _text(data["source_revision"])
    if not source_revision.startswith("alpaca_sip:total_return_adjusted:adjustment=all:"):
        _fail()
    revision_parts = source_revision.split(":")
    if (
        len(revision_parts) != 5
        or not revision_parts[3].startswith("binding_sha256=")
        or not revision_parts[4].startswith("manifest_sha256=")
        or len(revision_parts[3].removeprefix("binding_sha256=")) != 64
        or len(revision_parts[4].removeprefix("manifest_sha256=")) != 64
        or any(char not in "0123456789abcdef" for char in revision_parts[3].removeprefix("binding_sha256="))
        or any(char not in "0123456789abcdef" for char in revision_parts[4].removeprefix("manifest_sha256="))
    ):
        _fail()
    _text(data["retrieved_at"])
    if data["symbols"] != list(SYMBOLS):
        _fail()
    request = _mapping(data["request"])
    if set(request) != {"start", "end_exclusive"} or _session(request["start"]) >= _session(request["end_exclusive"]):
        _fail()
    if (
        type(data["sha256"]) is not str
        or len(data["sha256"]) != 64
        or any(char not in "0123456789abcdef" for char in data["sha256"])
        or type(data["bytes"]) is not int
        or isinstance(data["bytes"], bool)
        or data["bytes"] < 1
    ):
        _fail()
    for key in ("counts", "coverage"):
        if set(_mapping(data[key])) != set(SYMBOLS):
            _fail()
    return data


def load_soxl_alpaca_input(
    manifest_path: str | Path,
    artifact_path: str | Path,
    readback_path: str | Path,
) -> _offline_contract.OfflineInput:
    """Load a verified Alpaca source-specific UES input pack."""
    manifest = _manifest(Path(manifest_path))
    try:
        raw = Path(artifact_path).read_bytes()
        reader = csv.DictReader(raw.decode().splitlines())
        records = list(reader)
    except (OSError, UnicodeError, csv.Error):
        _fail()
    if not records or reader.fieldnames != list(_offline_contract.COLUMNS) or any(
        set(record) != set(_offline_contract.COLUMNS) for record in records
    ):
        _fail()
    try:
        rows = tuple(
            _offline_contract.InputRow(
                _offline_contract._text(record["symbol"]),
                _offline_contract._date(record["as_of"]),
                *(
                    _offline_contract._num(
                        record[column],
                        positive=column != "volume",
                        nonnegative=column == "volume",
                    )
                    for column in _offline_contract.COLUMNS[2:]
                ),
            )
            for record in records
        )
    except (KeyError, TypeError, ValueError):
        _fail()
    if (
        tuple((row.as_of, row.symbol) for row in rows) != tuple(sorted((row.as_of, row.symbol) for row in rows))
        or len({(row.symbol, row.as_of) for row in rows}) != len(rows)
        or {row.symbol for row in rows} != set(SYMBOLS)
    ):
        _fail()
    request = manifest["request"]
    if any(not (request["start"] <= row.as_of < request["end_exclusive"]) for row in rows):
        _fail()
    dates = [{row.as_of for row in rows if row.symbol == symbol} for symbol in SYMBOLS]
    if dates[0] != dates[1]:
        _fail()
    for row in rows:
        if not (row.low <= min(row.open, row.close) <= max(row.open, row.close) <= row.high):
            _fail()
    canonical = _offline_contract._canonical(rows)
    if canonical != raw or len(raw) != manifest["bytes"] or hashlib.sha256(raw).hexdigest() != manifest["sha256"]:
        _fail()
    for symbol in SYMBOLS:
        selected = [row for row in rows if row.symbol == symbol]
        if len(selected) != manifest["counts"][symbol]:
            _fail()
        coverage = _mapping(manifest["coverage"][symbol])
        if set(coverage) != {"start", "end"} or (coverage["start"], coverage["end"]) != (
            selected[0].as_of,
            selected[-1].as_of,
        ):
            _fail()
    try:
        _offline_contract._readback(Path(readback_path), manifest, len(rows))
    except (OSError, UnicodeError, ValueError, TypeError):
        _fail()
    identity = {
        key: manifest[key]
        for key in (
            "schema",
            "research_only",
            "provider",
            "price_field",
            "provider_completeness",
            "calendar_authority",
            "canonicalization",
            "source_revision",
            "retrieved_at",
            "symbols",
            "request",
            "counts",
            "coverage",
        )
    }
    identity.update({"artifact_sha256": manifest["sha256"], "artifact_bytes": manifest["bytes"]})
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return _offline_contract.OfflineInput(rows, canonical, digest, manifest["source_revision"])


def _csv_bytes(rows: list[tuple[str, str, dict[str, float]]]) -> bytes:
    lines = [",".join(("symbol", "as_of", "open", "high", "low", "close", "volume"))]
    for symbol, session, bar in rows:
        lines.append(",".join((symbol, session, *(format(bar[field], ".17g") for field in ("open", "high", "low", "close", "volume")))))
    return ("\n".join(lines) + "\n").encode()


def _build_payload(
    source_root: str | Path,
    *,
    start: str,
    end_exclusive: str,
    expected_sessions: int = 753,
) -> tuple[dict[str, Any], bytes]:
    """Build one exact two-asset 753-session UES payload in memory."""
    start = _session(start)
    end_exclusive = _session(end_exclusive)
    if start >= end_exclusive or type(expected_sessions) is not int or expected_sessions <= 0:
        _fail()
    binding, manifest, bars, binding_digest, manifest_digest = _read_source_root(Path(source_root))
    series = _series(bars)
    _verify_source_hashes(manifest, series)
    identity = _mapping(binding["data_identity"])
    cutoff = _session(identity["date_cutoff"])
    if any(rows[-1]["session_date"] != cutoff for rows in series.values()):
        _fail()
    selected: list[tuple[str, str, dict[str, float]]] = []
    for session in (row["session_date"] for row in series["SOXL"]):
        if start <= session < end_exclusive:
            soxl = next(row["bar"] for row in series["SOXL"] if row["session_date"] == session)
            soxx = next(row["bar"] for row in series["SOXX"] if row["session_date"] == session)
            selected.extend((("SOXL", session, soxl), ("SOXX", session, soxx)))
    selected.sort(key=lambda row: (row[1], row[0]))
    if len(selected) != expected_sessions * len(SYMBOLS):
        _fail()
    artifact = _csv_bytes(selected)
    dates = {symbol: [session for symbol_, session, _ in selected if symbol_ == symbol] for symbol in SYMBOLS}
    source_revision = (
        "alpaca_sip:total_return_adjusted:adjustment=all:"
        f"binding_sha256={binding_digest}:manifest_sha256={manifest_digest}"
    )
    output_manifest = {
        "schema": "qsl.research.price_snapshot.v1",
        "research_only": True,
        "provider": "alpaca_sip",
        "price_field": "adjusted_close",
        "provider_completeness": "unverified",
        "calendar_authority": "unverified",
        "canonicalization": "csv.writer_utf8_lf_float17g_v1",
        "source_revision": source_revision,
        "retrieved_at": _text(manifest["observed_at"]),
        "symbols": list(SYMBOLS),
        "request": {"start": start, "end_exclusive": end_exclusive},
        "sha256": hashlib.sha256(artifact).hexdigest(),
        "bytes": len(artifact),
        "counts": {symbol: len(dates[symbol]) for symbol in SYMBOLS},
        "coverage": {symbol: {"start": dates[symbol][0], "end": dates[symbol][-1]} for symbol in SYMBOLS},
    }
    return output_manifest, artifact


def materialize_soxl_alpaca_input(
    source_root: str | Path,
    output_root: str | Path,
    *,
    start: str,
    end_exclusive: str,
    expected_sessions: int = 753,
) -> SoxlAlpacaInputPaths:
    """Persist one payload, then generate readback from the persisted bytes."""
    destination = Path(output_root)
    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
        _fail()
    manifest, artifact = _build_payload(
        source_root,
        start=start,
        end_exclusive=end_exclusive,
        expected_sessions=expected_sessions,
    )
    manifest_bytes = _canonical(manifest)
    artifact_path = destination / "prices.csv"
    manifest_path = destination / "prices.csv.manifest.json"
    readback_path = destination / "prices.csv.readback.json"
    try:
        destination.mkdir(mode=0o700)
    except OSError:
        _fail()
    try:
        artifact_path.write_bytes(artifact)
        manifest_path.write_bytes(manifest_bytes)
        persisted_artifact = artifact_path.read_bytes()
        persisted_manifest = manifest_path.read_bytes()
        if persisted_artifact != artifact or persisted_manifest != manifest_bytes:
            raise OSError
        readback = {
            "schema": "qsl.research.price_snapshot_readback.v1",
            "canonical_csv_sha256": manifest["sha256"],
            "row_count": manifest["counts"]["SOXX"] + manifest["counts"]["SOXL"],
            "aligned_observations": manifest["counts"]["SOXX"],
            "counts": manifest["counts"],
            "date_set_equal": True,
            "unique_symbol_date_rows": True,
            "deterministic_order": "as_of_ascending_then_symbol_ascending",
            "finite_positive_ohlc": True,
            "finite_nonnegative_volume": True,
            "raw_persisted_bytes_equal": persisted_artifact == artifact,
            "raw_sha256_readback_equal": hashlib.sha256(persisted_artifact).hexdigest() == manifest["sha256"],
            "round_trip_canonical_bytes_equal": persisted_artifact == artifact,
        }
        readback_path.write_bytes(_canonical(readback))
    except (OSError, ValueError):
        shutil.rmtree(destination, ignore_errors=True)
        _fail()
    return SoxlAlpacaInputPaths(manifest_path, artifact_path, readback_path)


__all__ = [
    "SoxlAlpacaInputAdapterError",
    "SoxlAlpacaInputPaths",
    "load_soxl_alpaca_input",
    "materialize_soxl_alpaca_input",
]
