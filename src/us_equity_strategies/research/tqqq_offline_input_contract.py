"""Strict private Yahoo snapshot binding for the offline TQQQ baseline."""
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
SYMBOLS = ("QQQ", "TQQQ")
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
        out.append(",".join((row.symbol, row.as_of, *(format(v, ".17g") for v in (row.open, row.high, row.low, row.close, row.volume)))))
    return ("\n".join(out) + "\n").encode()

def _manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_bytes().decode())
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail()
    data = _dict(data)
    expected = {"schema", "research_only", "provider", "price_field", "provider_completeness", "calendar_authority", "source_revision", "retrieved_at", "symbols", "request", "sha256", "bytes", "counts", "coverage"}
    if set(data) != expected or data["schema"] != SCHEMA or data["research_only"] is not True:
        _fail()
    if (data["provider"], data["price_field"], data["provider_completeness"], data["calendar_authority"]) != ("yahoo_chart", "adjusted_close", "unverified", "unverified"):
        _fail()
    _text(data["source_revision"]); _text(data["retrieved_at"])
    if data["symbols"] != list(SYMBOLS):
        _fail()
    request = _dict(data["request"])
    if set(request) != {"start", "end_exclusive"} or _date(request["start"]) >= _date(request["end_exclusive"]):
        _fail()
    if type(data["sha256"]) is not str or len(data["sha256"]) != 64 or type(data["bytes"]) is not int or isinstance(data["bytes"], bool) or data["bytes"] < 1:
        _fail()
    for key in ("counts", "coverage"):
        if set(_dict(data[key])) != set(SYMBOLS):
            _fail()
    return data

def load_offline_input(manifest_path: str | Path, artifact_path: str | Path) -> OfflineInput:
    manifest = _manifest(Path(manifest_path))
    try:
        raw = Path(artifact_path).read_bytes()
        records = list(csv.DictReader(raw.decode().splitlines()))
    except (OSError, UnicodeError, csv.Error):
        _fail()
    if not records or tuple(records[0]) != COLUMNS:
        _fail()
    rows = tuple(InputRow(_text(r["symbol"]), _date(r["as_of"]), *(_num(r[k], positive=k != "volume", nonnegative=k == "volume") for k in COLUMNS[2:])) for r in records)
    if tuple((r.as_of, r.symbol) for r in rows) != tuple(sorted((r.as_of, r.symbol) for r in rows)) or len({(r.symbol, r.as_of) for r in rows}) != len(rows):
        _fail()
    if {r.symbol for r in rows} != set(SYMBOLS):
        _fail()
    request = manifest["request"]
    if any(not (request["start"] <= r.as_of < request["end_exclusive"]) for r in rows):
        _fail()
    dates = [{r.as_of for r in rows if r.symbol == s} for s in SYMBOLS]
    if dates[0] != dates[1]:
        _fail()
    for r in rows:
        if not (r.low <= min(r.open, r.close) <= max(r.open, r.close) <= r.high):
            _fail()
    canonical = _canonical(rows)
    if canonical != raw or len(raw) != manifest["bytes"] or hashlib.sha256(raw).hexdigest() != manifest["sha256"]:
        _fail()
    for symbol in SYMBOLS:
        selected = [r for r in rows if r.symbol == symbol]
        if len(selected) != manifest["counts"][symbol]:
            _fail()
        coverage = _dict(manifest["coverage"][symbol])
        if set(coverage) != {"start", "end"} or (coverage["start"], coverage["end"]) != (selected[0].as_of, selected[-1].as_of):
            _fail()
    identity = {k: manifest[k] for k in ("schema", "research_only", "provider", "price_field", "provider_completeness", "calendar_authority", "source_revision", "symbols", "request", "counts", "coverage")}
    identity.update({"artifact_sha256": manifest["sha256"], "artifact_bytes": manifest["bytes"]})
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return OfflineInput(rows, canonical, digest, manifest["source_revision"])
