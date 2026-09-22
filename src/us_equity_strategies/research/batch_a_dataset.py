"""Batch A v2 price snapshot loader (qsl.research.price_snapshot.v2).

Rejects v1 schemas and any automatic conversion.  Validates GCS identity
fields (generation / bytes / sha256), staging-root object path binding, and
aligned date coverage.  Does not fetch market data or call GCS.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA = "qsl.research.price_snapshot.v2"
COLUMNS = ("symbol", "as_of", "open", "high", "low", "close", "volume")
_MANIFEST_KEYS = frozenset(
    {
        "schema",
        "research_only",
        "dataset_id",
        "provider",
        "feed",
        "price_field",
        "adjustment",
        "calendar",
        "timezone",
        "license_retention",
        "code_version",
        "source_revision",
        "retrieved_at",
        "symbols",
        "request",
        "gcs",
        "counts",
        "coverage",
    }
)
_GCS_KEYS = frozenset({"bucket", "object", "generation", "bytes", "sha256"})


class BatchADatasetError(ValueError):
    """Sanitized v2 dataset validation failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PriceRow:
    symbol: str
    as_of: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class PriceSnapshotV2:
    dataset_id: str
    rows: tuple[PriceRow, ...]
    manifest: dict[str, Any]
    artifact_bytes: bytes
    input_digest: str


def _fail(code: str) -> None:
    raise BatchADatasetError(code)


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or any(ord(ch) < 0x20 for ch in value):
        _fail(code)
    return value


def _date(value: object, code: str) -> str:
    text = _text(value, code)
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        _fail(code)
    if parsed.isoformat() != text:
        _fail(code)
    return text


def _num(
    value: object,
    code: str,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        _fail(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        _fail(code)
    if not math.isfinite(number):
        _fail(code)
    if positive and number <= 0.0:
        _fail(code)
    if nonnegative and number < 0.0:
        _fail(code)
    return 0.0 if number == 0.0 else number


def _digest(value: object, code: str) -> str:
    text = _text(value, code)
    if len(text) != 64 or text != text.lower() or any(ch not in "0123456789abcdef" for ch in text):
        _fail(code)
    return text


def _canonical_csv(rows: Sequence[PriceRow]) -> bytes:
    lines = [",".join(COLUMNS)]
    for row in rows:
        lines.append(
            ",".join(
                (
                    row.symbol,
                    row.as_of,
                    *(
                        format(item, ".17g")
                        for item in (row.open, row.high, row.low, row.close, row.volume)
                    ),
                )
            )
        )
    return ("\n".join(lines) + "\n").encode()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_bytes().decode())
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail("MANIFEST_UNREADABLE")
    if not isinstance(payload, dict):
        _fail("MANIFEST_INVALID")
    return payload


def _parse_manifest(raw: Mapping[str, Any]) -> dict[str, Any]:
    if set(raw) != _MANIFEST_KEYS:
        _fail("MANIFEST_KEYS_INVALID")
    if raw.get("schema") != SCHEMA:
        if raw.get("schema") == "qsl.research.price_snapshot.v1":
            _fail("PRICE_SNAPSHOT_V1_REJECTED")
        _fail("PRICE_SNAPSHOT_SCHEMA_INVALID")
    if raw.get("research_only") is not True:
        _fail("PRICE_SNAPSHOT_NOT_RESEARCH_ONLY")
    dataset_id = _text(raw.get("dataset_id"), "DATASET_ID_INVALID")
    for key in (
        "provider",
        "feed",
        "price_field",
        "adjustment",
        "calendar",
        "timezone",
        "license_retention",
        "code_version",
        "source_revision",
        "retrieved_at",
    ):
        _text(raw.get(key), f"{key.upper()}_INVALID")
    symbols_raw = raw.get("symbols")
    if not isinstance(symbols_raw, list) or not symbols_raw:
        _fail("SYMBOLS_INVALID")
    symbols = tuple(_text(item, "SYMBOLS_INVALID") for item in symbols_raw)
    if len(symbols) != len(set(symbols)):
        _fail("SYMBOLS_DUPLICATE")
    request = raw.get("request")
    if not isinstance(request, dict) or set(request) != {"start", "end_exclusive"}:
        _fail("REQUEST_INVALID")
    start = _date(request.get("start"), "REQUEST_INVALID")
    end_exclusive = _date(request.get("end_exclusive"), "REQUEST_INVALID")
    if start >= end_exclusive:
        _fail("REQUEST_INVALID")
    gcs = raw.get("gcs")
    if not isinstance(gcs, dict) or set(gcs) != _GCS_KEYS:
        _fail("GCS_IDENTITY_INVALID")
    _text(gcs.get("bucket"), "GCS_IDENTITY_INVALID")
    object_path = _text(gcs.get("object"), "GCS_IDENTITY_INVALID")
    generation = _text(gcs.get("generation"), "GCS_GENERATION_INVALID")
    if not generation.isdigit():
        _fail("GCS_GENERATION_INVALID")
    byte_count = gcs.get("bytes")
    if (
        isinstance(byte_count, bool)
        or not isinstance(byte_count, int)
        or byte_count < 1
    ):
        _fail("GCS_BYTES_INVALID")
    sha256 = _digest(gcs.get("sha256"), "GCS_SHA256_INVALID")
    counts = raw.get("counts")
    coverage = raw.get("coverage")
    if not isinstance(counts, dict) or set(counts) != set(symbols):
        _fail("COUNTS_INVALID")
    if not isinstance(coverage, dict) or set(coverage) != set(symbols):
        _fail("COVERAGE_INVALID")
    for symbol in symbols:
        count = counts[symbol]
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            _fail("COUNTS_INVALID")
        item = coverage[symbol]
        if not isinstance(item, dict) or set(item) != {"start", "end"}:
            _fail("COVERAGE_INVALID")
        _date(item.get("start"), "COVERAGE_INVALID")
        _date(item.get("end"), "COVERAGE_INVALID")
    return {
        "dataset_id": dataset_id,
        "symbols": symbols,
        "start": start,
        "end_exclusive": end_exclusive,
        "object_path": object_path,
        "generation": generation,
        "bytes": byte_count,
        "sha256": sha256,
        "counts": {symbol: int(counts[symbol]) for symbol in symbols},
        "coverage": {
            symbol: {
                "start": str(coverage[symbol]["start"]),
                "end": str(coverage[symbol]["end"]),
            }
            for symbol in symbols
        },
        "raw": dict(raw),
    }


def _parse_rows(raw: bytes, *, symbols: Sequence[str], start: str, end_exclusive: str) -> tuple[PriceRow, ...]:
    try:
        records = list(csv.DictReader(raw.decode().splitlines()))
    except (UnicodeError, csv.Error):
        _fail("ARTIFACT_UNREADABLE")
    if not records or tuple(records[0]) != COLUMNS:
        _fail("ARTIFACT_COLUMNS_INVALID")
    rows: list[PriceRow] = []
    for record in records:
        symbol = _text(record.get("symbol"), "ROW_INVALID")
        as_of = _date(record.get("as_of"), "ROW_DATE_INVALID")
        if not (start <= as_of < end_exclusive):
            _fail("ROW_OUTSIDE_REQUEST_WINDOW")
        open_ = _num(record.get("open"), "ROW_OHLC_INVALID", positive=True)
        high = _num(record.get("high"), "ROW_OHLC_INVALID", positive=True)
        low = _num(record.get("low"), "ROW_OHLC_INVALID", positive=True)
        close = _num(record.get("close"), "ROW_OHLC_INVALID", positive=True)
        volume = _num(record.get("volume"), "ROW_VOLUME_INVALID", nonnegative=True)
        if not (low <= min(open_, close) <= max(open_, close) <= high):
            _fail("ROW_OHLC_ORDER_INVALID")
        rows.append(
            PriceRow(
                symbol=symbol,
                as_of=as_of,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
            )
        )
    if not rows:
        _fail("DATES_INCOMPLETE")
    keys = [(row.as_of, row.symbol) for row in rows]
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        _fail("ROW_ORDER_OR_DUPLICATE_INVALID")
    present = {row.symbol for row in rows}
    if present != set(symbols):
        _fail("SYMBOL_SET_MISMATCH")
    dates_by_symbol = {
        symbol: [row.as_of for row in rows if row.symbol == symbol] for symbol in symbols
    }
    baseline = dates_by_symbol[symbols[0]]
    if len(baseline) < 2:
        _fail("DATES_INCOMPLETE")
    for symbol in symbols[1:]:
        if dates_by_symbol[symbol] != baseline:
            _fail("DATES_INCOMPLETE")
    return tuple(rows)


def _verify_object_path(*, object_path: str, dataset_id: str, relative_artifact: str) -> None:
    expected = f"research/v2/input/{dataset_id}/{relative_artifact}"
    if object_path != expected:
        _fail("GCS_OBJECT_ROOT_MISMATCH")


def _verify_generation(identity_path: Path | None, *, generation: str) -> None:
    if identity_path is None:
        return
    payload = _load_json(identity_path)
    actual = payload.get("generation")
    if not isinstance(actual, str) or actual != generation:
        _fail("GCS_GENERATION_MISMATCH")


def load_price_snapshot_v2(
    dataset_dir: str | Path,
    *,
    relative_artifact: str = "prices.csv",
    identity_path: str | Path | None = None,
) -> PriceSnapshotV2:
    """Load one v2 snapshot from a staged dataset directory."""

    root = Path(dataset_dir)
    artifact_path = root / relative_artifact
    manifest_path = root / f"{relative_artifact}.manifest.json"
    if not artifact_path.is_file() or not manifest_path.is_file():
        _fail("DATASET_FILES_MISSING")
    parsed = _parse_manifest(_load_json(manifest_path))
    try:
        artifact = artifact_path.read_bytes()
    except OSError:
        _fail("ARTIFACT_UNREADABLE")
    if len(artifact) != parsed["bytes"] or hashlib.sha256(artifact).hexdigest() != parsed["sha256"]:
        _fail("GCS_CONTENT_MISMATCH")
    _verify_object_path(
        object_path=parsed["object_path"],
        dataset_id=parsed["dataset_id"],
        relative_artifact=relative_artifact,
    )
    resolved_identity: Path | None
    if identity_path is not None:
        resolved_identity = Path(identity_path)
    else:
        candidate = root / "object_identity.json"
        resolved_identity = candidate if candidate.is_file() else None
    _verify_generation(resolved_identity, generation=parsed["generation"])
    rows = _parse_rows(
        artifact,
        symbols=parsed["symbols"],
        start=parsed["start"],
        end_exclusive=parsed["end_exclusive"],
    )
    for symbol in parsed["symbols"]:
        selected = [row for row in rows if row.symbol == symbol]
        if len(selected) != parsed["counts"][symbol]:
            _fail("COUNTS_MISMATCH")
        coverage = parsed["coverage"][symbol]
        if (selected[0].as_of, selected[-1].as_of) != (coverage["start"], coverage["end"]):
            _fail("COVERAGE_MISMATCH")
    if _canonical_csv(rows) != artifact:
        _fail("ARTIFACT_CANONICAL_MISMATCH")
    identity = {
        key: parsed["raw"][key]
        for key in (
            "schema",
            "research_only",
            "dataset_id",
            "provider",
            "feed",
            "price_field",
            "adjustment",
            "calendar",
            "timezone",
            "license_retention",
            "code_version",
            "source_revision",
            "retrieved_at",
            "symbols",
            "request",
            "gcs",
            "counts",
            "coverage",
        )
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return PriceSnapshotV2(
        dataset_id=parsed["dataset_id"],
        rows=rows,
        manifest=dict(parsed["raw"]),
        artifact_bytes=artifact,
        input_digest=digest,
    )


__all__ = [
    "SCHEMA",
    "BatchADatasetError",
    "COLUMNS",
    "PriceRow",
    "PriceSnapshotV2",
    "load_price_snapshot_v2",
]
