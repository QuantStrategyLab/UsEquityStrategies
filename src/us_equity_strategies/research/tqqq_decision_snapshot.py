"""Private, opt-in TQQQ decision snapshots for local research."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from types import MappingProxyType
from typing import Any, Mapping, NoReturn


SCHEMA = "qsl.research.tqqq_decision_snapshot.v1"
_ROOT_KEYS = frozenset(
    {
        "schema",
        "version",
        "session",
        "timestamp",
        "source",
        "input",
        "control",
        "plugin",
        "risk_gate",
        "final_decision",
        "digest",
    }
)
_CAPTURE_KEYS = frozenset(
    {"path", "session", "timestamp", "source", "input", "control", "plugin"}
)
_FORBIDDEN_KEY_PARTS = (
    "account",
    "credential",
    "token",
    "secret",
    "password",
    "cookie",
    "jwt",
    "order_id",
    "broker_order",
    "api_key",
    "authorization",
)
_UTC_MICROS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_FACT_NESTING = 32


class DecisionSnapshotError(ValueError):
    """Sanitized local snapshot error."""


@dataclass(frozen=True, slots=True)
class VerifiedTqqqDecisionSnapshot:
    path: Path
    snapshot: Mapping[str, object]


def _invalid() -> NoReturn:
    raise DecisionSnapshotError("INVALID_DECISION_SNAPSHOT") from None


def _write_failed() -> NoReturn:
    raise DecisionSnapshotError("DECISION_SNAPSHOT_WRITE_FAILED") from None


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _text(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or any(ord(character) < 0x20 or 0xD800 <= ord(character) <= 0xDFFF for character in value)
    ):
        _invalid()
    return value


def _utc_micros(value: object) -> str:
    value = _text(value)
    if not _UTC_MICROS.fullmatch(value):
        _invalid()
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        _invalid()
    return value


def _exact_keys(value: object, keys: frozenset[str]) -> Mapping[str, object]:
    if type(value) is not dict or set(value) != keys:
        _invalid()
    return value


def _facts(value: object, depth: int = 0) -> Mapping[str, object]:
    if depth > _MAX_FACT_NESTING or type(value) is not dict:
        _invalid()
    try:
        for key, item in value.items():
            if type(key) is not str or not key or any(part in key.lower() for part in _FORBIDDEN_KEY_PARTS):
                _invalid()
            _json_value(item, depth + 1)
    except RecursionError:
        _invalid()
    return value


def _json_value(value: object, depth: int = 0) -> None:
    if depth > _MAX_FACT_NESTING:
        _invalid()
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if not 0 <= value <= 2**53 - 1:
            _invalid()
        return
    if type(value) is str:
        _text(value)
        return
    if type(value) is list:
        for item in value:
            _json_value(item, depth + 1)
        return
    if type(value) is dict:
        _facts(value, depth)
        return
    _invalid()


def _finite_number_text(value: object) -> str:
    value = _text(value)
    try:
        numeric = float(value)
    except ValueError:
        _invalid()
    if not math.isfinite(numeric):
        _invalid()
    canonical = "0" if numeric == 0.0 else format(numeric, ".17g")
    if value != canonical:
        _invalid()
    return value


def _float_text(value: object) -> str:
    if type(value) is not float or not math.isfinite(value):
        _invalid()
    return "0" if value == 0.0 else format(value, ".17g")


def _decision(value: object) -> Mapping[str, object]:
    decision = _exact_keys(value, frozenset({"positions", "budgets", "risk_flags", "identity"}))
    positions = decision["positions"]
    budgets = decision["budgets"]
    risk_flags = decision["risk_flags"]
    identity = decision["identity"]
    if type(positions) is not list or type(budgets) is not list or type(risk_flags) is not list:
        _invalid()
    if type(identity) is not str or not _DIGEST.fullmatch(identity):
        _invalid()
    for position in positions:
        position = _exact_keys(position, frozenset({"symbol", "target_weight", "target_value"}))
        _text(position["symbol"])
        target_weight = position["target_weight"]
        target_value = position["target_value"]
        if target_weight is not None:
            _finite_number_text(target_weight)
        if target_value is not None:
            _finite_number_text(target_value)
        if (target_weight is None) == (target_value is None):
            _invalid()
    for budget in budgets:
        budget = _exact_keys(budget, frozenset({"name", "amount"}))
        _text(budget["name"])
        _finite_number_text(budget["amount"])
    for flag in risk_flags:
        _text(flag)
    facts = {"positions": positions, "budgets": budgets, "risk_flags": risk_flags}
    if sha256(_canonical_bytes(facts)).hexdigest() != identity:
        _invalid()
    return decision


def _identity_group(value: object) -> Mapping[str, object]:
    group = _exact_keys(value, frozenset({"identity", "raw_provenance", "resolved"}))
    _text(group["identity"])
    _facts(group["raw_provenance"])
    _facts(group["resolved"])
    return group


def _capture(value: object) -> Mapping[str, object]:
    capture = _exact_keys(value, _CAPTURE_KEYS)
    _text(capture["path"])
    session = _exact_keys(capture["session"], frozenset({"id", "sequence"}))
    session_id = _text(session["id"])
    if (
        not session_id.startswith("tqqq_growth_income:")
        or type(session["sequence"]) is not int
        or not 0 <= session["sequence"] <= 2**53 - 1
    ):
        _invalid()
    _utc_micros(capture["timestamp"])
    for name in ("source", "input", "control", "plugin"):
        _identity_group(capture[name])
    return capture


def _snapshot(value: object) -> Mapping[str, object]:
    snapshot = _exact_keys(value, _ROOT_KEYS)
    if snapshot["schema"] != SCHEMA or type(snapshot["version"]) is not int or snapshot["version"] != 1:
        _invalid()
    session = _exact_keys(snapshot["session"], frozenset({"id", "sequence"}))
    session_id = _text(session["id"])
    if (
        not session_id.startswith("tqqq_growth_income:")
        or type(session["sequence"]) is not int
        or not 0 <= session["sequence"] <= 2**53 - 1
    ):
        _invalid()
    _utc_micros(snapshot["timestamp"])
    for name in ("source", "input", "control", "plugin"):
        _identity_group(snapshot[name])
    risk_gate = _exact_keys(snapshot["risk_gate"], frozenset({"pre_risk_decision", "final_decision"}))
    _decision(risk_gate["pre_risk_decision"])
    _decision(risk_gate["final_decision"])
    _decision(snapshot["final_decision"])
    if snapshot["risk_gate"]["final_decision"] != snapshot["final_decision"]:
        _invalid()
    digest = snapshot["digest"]
    if type(digest) is not str or not _DIGEST.fullmatch(digest):
        _invalid()
    unsigned = dict(snapshot)
    unsigned.pop("digest")
    if sha256(_canonical_bytes(unsigned)).hexdigest() != digest:
        _invalid()
    return snapshot


def _load(path: Path) -> Mapping[str, object]:
    try:
        raw = path.read_bytes()
        if not raw.endswith(b"\n") or raw.startswith(b"\xef\xbb\xbf"):
            _invalid()

        def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
            value: dict[str, object] = {}
            for key, item in pairs:
                if key in value:
                    _invalid()
                value[key] = item
            return value

        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda _value: _invalid(),
            parse_float=lambda _value: _invalid(),
        )
        snapshot = _snapshot(value)
        if _canonical_bytes(snapshot) != raw:
            _invalid()
        return snapshot
    except DecisionSnapshotError:
        raise
    except (OSError, RecursionError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        _invalid()


def _freeze(value: object) -> object:
    if type(value) is dict:
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze(item) for item in value)
    return value


def read_tqqq_decision_snapshot_package(path: Path) -> VerifiedTqqqDecisionSnapshot:
    """Read one canonical snapshot and fail closed for invalid local bytes."""
    path = Path(path)
    return VerifiedTqqqDecisionSnapshot(path=path, snapshot=_freeze(_load(path)))


def _build_snapshot(
    capture: Mapping[str, object], *, pre_risk_decision: Mapping[str, object], final_decision: Mapping[str, object]
) -> dict[str, object]:
    capture = _capture(capture)
    _decision(pre_risk_decision)
    _decision(final_decision)
    snapshot: dict[str, object] = {
        "schema": SCHEMA,
        "version": 1,
        "session": capture["session"],
        "timestamp": capture["timestamp"],
        "source": capture["source"],
        "input": capture["input"],
        "control": capture["control"],
        "plugin": capture["plugin"],
        "risk_gate": {
            "pre_risk_decision": pre_risk_decision,
            "final_decision": final_decision,
        },
        "final_decision": final_decision,
    }
    unsigned = dict(snapshot)
    snapshot["digest"] = sha256(_canonical_bytes(unsigned)).hexdigest()
    _snapshot(snapshot)
    return snapshot


def write_tqqq_decision_snapshot(
    path: Path,
    capture: Mapping[str, object],
    *,
    pre_risk_decision: Mapping[str, object],
    final_decision: Mapping[str, object],
) -> VerifiedTqqqDecisionSnapshot:
    """Publish one snapshot with same-directory temp, fsync, replace, and readback."""
    snapshot = _build_snapshot(capture, pre_risk_decision=pre_risk_decision, final_decision=final_decision)
    path = Path(path)
    data = _canonical_bytes(snapshot)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="xb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        return read_tqqq_decision_snapshot_package(path)
    except DecisionSnapshotError:
        raise
    except OSError:
        _write_failed()
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def capture_tqqq_decision_snapshot_if_enabled(
    capture: object,
    *,
    pre_risk_decision: Mapping[str, object],
    final_decision: Mapping[str, object],
) -> VerifiedTqqqDecisionSnapshot | None:
    """Capture only when the caller supplies the complete explicit opt-in mapping."""
    if capture is None or capture == {}:
        return None
    if type(capture) is not dict:
        _invalid()
    path = capture.get("path")
    if type(path) is not str or not path:
        _invalid()
    return write_tqqq_decision_snapshot(
        Path(path), capture, pre_risk_decision=pre_risk_decision, final_decision=final_decision
    )


def decision_facts(decision: Any) -> dict[str, object]:
    """Project the actual decision into the deliberately small snapshot schema."""
    try:
        positions = [
            {
                "symbol": _text(position.symbol),
                "target_weight": (
                    _float_text(position.target_weight)
                    if position.target_weight is not None
                    else None
                ),
                "target_value": (
                    _float_text(position.target_value)
                    if position.target_value is not None
                    else None
                ),
            }
            for position in decision.positions
        ]
        budgets = [
            {"name": _text(budget.name), "amount": _float_text(budget.amount)}
            for budget in decision.budgets
        ]
        risk_flags = [_text(flag) for flag in decision.risk_flags]
    except (AttributeError, TypeError, ValueError):
        _invalid()
    safe_facts = {"positions": positions, "budgets": budgets, "risk_flags": risk_flags}
    return {
        **safe_facts,
        "identity": sha256(_canonical_bytes(safe_facts)).hexdigest(),
    }
