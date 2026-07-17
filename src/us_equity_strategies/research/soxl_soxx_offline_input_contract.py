"""Strict private SOXX/SOXL snapshot binding for the offline parity baseline."""
from __future__ import annotations

from dataclasses import dataclass
import csv
from datetime import date
import hashlib
import json
import math
from pathlib import Path
from typing import Any

SCHEMA = "qsl.research.price_snapshot.v1"
READBACK_SCHEMA = "qsl.research.price_snapshot_readback.v1"
SYMBOLS = ("SOXX", "SOXL")
COLUMNS = ("symbol", "as_of", "open", "high", "low", "close", "volume")


class OfflineInputContractError(ValueError):
    """Sanitized malformed private-input error."""


@dataclass(frozen=True)
class InputRow:
    symbol: str
    as_of: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class OfflineInput:
    rows: tuple[InputRow, ...]
    canonical_bytes: bytes
    input_digest: str
    source_revision: str


def _fail() -> None:
    raise OfflineInputContractError("invalid offline input") from None


def _dict(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        _fail()
    return value


def _text(value: Any) -> str:
    if type(value) is not str or not value or any(ord(ch) < 0x20 for ch in value):
        _fail()
    return value


def _date(value: Any) -> str:
    text = _text(value)
    try:
        parsed = date.fromisoformat(text)
    except (TypeError, ValueError):
        _fail()
    if parsed.isoformat() != text:
        _fail()
    return text


def _num(value: Any, *, positive: bool = False, nonnegative: bool = False) -> float:
    if type(value) not in (str, int, float) or isinstance(value, bool):
        _fail()
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        _fail()
    if not math.isfinite(number) or (positive and number <= 0) or (nonnegative and number < 0):
        _fail()
    return 0.0 if number == 0.0 else number


def _canonical(rows: tuple[InputRow, ...]) -> bytes:
    out = [",".join(COLUMNS)]
    for row in rows:
        out.append(
            ",".join(
                (
                    row.symbol,
                    row.as_of,
                    *(format(value, ".17g") for value in (row.open, row.high, row.low, row.close, row.volume)),
                )
            )
        )
    return ("\n".join(out) + "\n").encode()


def _json_object(path: Path) -> dict[str, Any]:
    try:
        return _dict(json.loads(path.read_bytes().decode()))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail()


def _manifest(path: Path) -> dict[str, Any]:
    data = _json_object(path)
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
    if set(data) != expected or data["schema"] != SCHEMA or data["research_only"] is not True:
        _fail()
    if (
        data["provider"],
        data["price_field"],
        data["provider_completeness"],
        data["calendar_authority"],
    ) != ("yahoo_chart", "adjusted_close", "unverified", "unverified"):
        _fail()
    if data["canonicalization"] != "csv.writer_utf8_lf_float17g_v1":
        _fail()
    _text(data["source_revision"])
    _text(data["retrieved_at"])
    if data["symbols"] != list(SYMBOLS):
        _fail()
    request = _dict(data["request"])
    if set(request) != {"start", "end_exclusive"} or _date(request["start"]) >= _date(request["end_exclusive"]):
        _fail()
    if (
        type(data["sha256"]) is not str
        or len(data["sha256"]) != 64
        or type(data["bytes"]) is not int
        or isinstance(data["bytes"], bool)
        or data["bytes"] < 1
    ):
        _fail()
    for key in ("counts", "coverage"):
        if set(_dict(data[key])) != set(SYMBOLS):
            _fail()
    return data


def _readback(path: Path, manifest: dict[str, Any], row_count: int) -> None:
    data = _json_object(path)
    expected = {
        "schema",
        "canonical_csv_sha256",
        "row_count",
        "aligned_observations",
        "counts",
        "date_set_equal",
        "unique_symbol_date_rows",
        "deterministic_order",
        "finite_positive_ohlc",
        "finite_nonnegative_volume",
        "raw_persisted_bytes_equal",
        "raw_sha256_readback_equal",
        "round_trip_canonical_bytes_equal",
    }
    if set(data) != expected or data["schema"] != READBACK_SCHEMA:
        _fail()
    if (
        data["canonical_csv_sha256"] != manifest["sha256"]
        or data["row_count"] != row_count
        or data["aligned_observations"] != manifest["counts"][SYMBOLS[0]]
        or data["counts"] != manifest["counts"]
        or data["deterministic_order"] != "as_of_ascending_then_symbol_ascending"
    ):
        _fail()
    if not all(
        data[key] is True
        for key in (
            "date_set_equal",
            "unique_symbol_date_rows",
            "finite_positive_ohlc",
            "finite_nonnegative_volume",
            "raw_persisted_bytes_equal",
            "raw_sha256_readback_equal",
            "round_trip_canonical_bytes_equal",
        )
    ):
        _fail()


def load_offline_input(
    manifest_path: str | Path,
    artifact_path: str | Path,
    readback_path: str | Path,
) -> OfflineInput:
    """Load one verified private SOXX/SOXL artifact without a fallback source."""
    manifest = _manifest(Path(manifest_path))
    try:
        raw = Path(artifact_path).read_bytes()
        reader = csv.DictReader(raw.decode().splitlines())
        records = list(reader)
    except (OSError, UnicodeError, csv.Error):
        _fail()
    if not records or reader.fieldnames != list(COLUMNS) or any(set(record) != set(COLUMNS) for record in records):
        _fail()
    rows = tuple(
        InputRow(
            _text(record["symbol"]),
            _date(record["as_of"]),
            *(
                _num(record[column], positive=column != "volume", nonnegative=column == "volume")
                for column in COLUMNS[2:]
            ),
        )
        for record in records
    )
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
    canonical = _canonical(rows)
    if canonical != raw or len(raw) != manifest["bytes"] or hashlib.sha256(raw).hexdigest() != manifest["sha256"]:
        _fail()
    for symbol in SYMBOLS:
        selected = [row for row in rows if row.symbol == symbol]
        if len(selected) != manifest["counts"][symbol]:
            _fail()
        coverage = _dict(manifest["coverage"][symbol])
        if set(coverage) != {"start", "end"} or (coverage["start"], coverage["end"]) != (
            selected[0].as_of,
            selected[-1].as_of,
        ):
            _fail()
    _readback(Path(readback_path), manifest, len(rows))
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
    return OfflineInput(rows, canonical, digest, manifest["source_revision"])
