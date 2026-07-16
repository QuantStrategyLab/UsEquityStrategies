"""Private-artifact binding for the TQQQ research baseline.

This module validates an explicitly supplied private Yahoo snapshot. It does not
infer sessions, invoke network providers, run production controls, or persist data.
"""
from __future__ import annotations

from dataclasses import dataclass
import csv
from datetime import date
import hashlib
import json
import math
from pathlib import Path
from typing import Any

PROFILE = "tqqq_growth_income_research_baseline_v1"
SCHEMA = "qsl.research.price_snapshot.v1"
COLUMNS = ("symbol", "as_of", "open", "high", "low", "close", "volume")
SYMBOLS = ("QQQ", "TQQQ")


class OfflineBaselineContractError(ValueError):
    """Raised for any malformed or mismatched private snapshot contract."""


@dataclass(frozen=True)
class PriceRow:
    symbol: str
    as_of: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class OfflineBaselineInput:
    profile: str
    source_revision: str
    request_start: str
    request_end_exclusive: str
    rows: tuple[PriceRow, ...]
    canonical_bytes: bytes
    input_digest: str
    controls_disabled: bool = True
    research_only: bool = True
    provider_completeness: str = "unverified"
    calendar_authority: str = "unverified"


def _fail() -> None:
    raise OfflineBaselineContractError("invalid offline baseline snapshot") from None


def _exact_dict(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        _fail()
    return value


def _text(value: Any) -> str:
    if type(value) is not str or not value or any(ord(ch) < 0x20 for ch in value):
        _fail()
    return value


def _date_text(value: Any) -> str:
    text = _text(value)
    try:
        parsed = date.fromisoformat(text)
    except (TypeError, ValueError):
        _fail()
    if parsed.isoformat() != text:
        _fail()
    return text


def _number(value: Any, *, positive: bool = False, nonnegative: bool = False) -> float:
    if type(value) not in (int, float, str) or isinstance(value, bool):
        _fail()
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        _fail()
    if not math.isfinite(number) or (positive and number <= 0) or (nonnegative and number < 0):
        _fail()
    return 0.0 if number == 0.0 else number


def _canonical_csv(rows: tuple[PriceRow, ...]) -> bytes:
    lines = [",".join(COLUMNS)]
    for row in rows:
        lines.append(",".join((row.symbol, row.as_of, *(format(value, ".17g") for value in (row.open, row.high, row.low, row.close, row.volume)))))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail()
    manifest = _exact_dict(value)
    expected = {
        "schema", "research_only", "provider", "price_field", "provider_completeness",
        "calendar_authority", "source_revision", "retrieved_at", "symbols", "request", "sha256", "bytes",
        "counts", "coverage",
    }
    if set(manifest) != expected:
        _fail()
    if manifest["schema"] != SCHEMA or manifest["research_only"] is not True:
        _fail()
    if manifest["provider"] != "yahoo_chart" or manifest["price_field"] != "adjusted_close":
        _fail()
    if manifest["provider_completeness"] != "unverified" or manifest["calendar_authority"] != "unverified":
        _fail()
    if _text(manifest["source_revision"]) != manifest["source_revision"]:
        _fail()
    if _text(manifest["retrieved_at"]) != manifest["retrieved_at"]:
        _fail()
    if manifest["symbols"] != list(SYMBOLS):
        _fail()
    request = _exact_dict(manifest["request"])
    if set(request) != {"start", "end_exclusive"}:
        _fail()
    request_start, request_end = _date_text(request["start"]), _date_text(request["end_exclusive"])
    if request_start >= request_end:
        _fail()
    if type(manifest["sha256"]) is not str or len(manifest["sha256"]) != 64:
        _fail()
    if type(manifest["bytes"]) is not int or isinstance(manifest["bytes"], bool) or manifest["bytes"] < 1:
        _fail()
    for key in ("counts", "coverage"):
        value = _exact_dict(manifest[key])
        if set(value) != set(SYMBOLS):
            _fail()
    return manifest


def load_offline_baseline_input(manifest_path: str | Path, artifact_path: str | Path) -> OfflineBaselineInput:
    """Read and validate one explicit private artifact without network or writes."""
    manifest = _load_manifest(Path(manifest_path))
    try:
        raw = Path(artifact_path).read_bytes()
    except OSError:
        _fail()
    if len(raw) != manifest["bytes"] or hashlib.sha256(raw).hexdigest() != manifest["sha256"]:
        _fail()
    try:
        text = raw.decode("utf-8")
        records = list(csv.DictReader(text.splitlines()))
    except (UnicodeError, csv.Error):
        _fail()
    if not records or tuple(records[0]) != COLUMNS:
        _fail()
    rows_list: list[PriceRow] = []
    for record in records:
        if set(record) != set(COLUMNS):
            _fail()
        rows_list.append(PriceRow(_text(record["symbol"]), _date_text(record["as_of"]), *(_number(record[key], positive=key != "volume", nonnegative=key == "volume") for key in COLUMNS[2:])))
    rows = tuple(rows_list)
    if tuple((row.as_of, row.symbol) for row in rows) != tuple(sorted((row.as_of, row.symbol) for row in rows)):
        _fail()
    if len({(row.symbol, row.as_of) for row in rows}) != len(rows) or {row.symbol for row in rows} != set(SYMBOLS):
        _fail()
    if _canonical_csv(rows) != raw:
        _fail()
    for symbol in SYMBOLS:
        selected = [row for row in rows if row.symbol == symbol]
        if len(selected) != manifest["counts"][symbol] or not selected:
            _fail()
        coverage = _exact_dict(manifest["coverage"][symbol])
        if set(coverage) != {"start", "end"} or (coverage["start"], coverage["end"]) != (selected[0].as_of, selected[-1].as_of):
            _fail()
    digest = hashlib.sha256(raw).hexdigest()
    return OfflineBaselineInput(PROFILE, manifest["source_revision"], manifest["request"]["start"], manifest["request"]["end_exclusive"], rows, raw, digest)
