"""Read-only conformance and accounting checks for sanitized runtime exports.

This module has no broker, deployment or network integration. Evidence supplied
in a bundle is not independently authenticated by this checker.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal, Inexact, InvalidOperation, localcontext
import hashlib
import json
from pathlib import Path
import re
from typing import Any

SCHEMA = "qsl.historical_runtime_bundle.v1"
PROFILES = ("tqqq_growth_income", "soxl_soxx_trend_income")
FILES = {
    "releases": "release-manifests.json",
    "sessions": "sessions.json",
    "runs": "runs.jsonl",
    "order_events": "order-events.jsonl",
    "account_events": "account-events.jsonl",
    "account_snapshots": "account-snapshots.jsonl",
    "source_evidence": "source-evidence.json",
}
COMPONENTS = ("integrity", "coverage", "provenance", "daily_replay",
              "order_lifecycle", "accounting", "shared_account")
_HASH = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_ACCOUNT = re.compile(r"acct_[0-9a-f]{16,64}")
_AMOUNT = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")


class BundleError(ValueError):
    def __init__(self, code: str, *, blocked: bool = False):
        super().__init__(code)
        self.code = code
        self.blocked = blocked


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BundleError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _bad_constant(_value):
    raise BundleError("NONFINITE_JSON_NUMBER")


def _json(data: bytes | str):
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return json.loads(data, object_pairs_hook=_unique_object, parse_constant=_bad_constant)


def _read_rows(path: Path) -> list[dict[str, Any]]:
    raw = path.read_bytes()
    if path.suffix == ".jsonl":
        rows = [_json(line) for line in raw.splitlines() if line.strip()]
    else:
        rows = _json(raw)
    if type(rows) is not list or any(type(row) is not dict for row in rows):
        raise BundleError("RECORD_ARRAY_REQUIRED")
    return rows


def _clock(value) -> datetime:
    if type(value) is not str:
        raise BundleError("AWARE_TIMESTAMP_REQUIRED")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise BundleError("AWARE_TIMESTAMP_REQUIRED")
    return result.astimezone(timezone.utc)


def _day(value) -> str:
    if type(value) is not str or date.fromisoformat(value).isoformat() != value:
        raise BundleError("ISO_SESSION_REQUIRED")
    return value


def _id(value) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise BundleError("STABLE_IDENTIFIER_REQUIRED")
    return value


def _digest(value) -> str:
    if type(value) is not str or _HASH.fullmatch(value) is None:
        raise BundleError("SHA256_REQUIRED")
    return value


def _amount(value, *, nonnegative: bool = False, positive: bool = False) -> Decimal:
    if type(value) is not str or _AMOUNT.fullmatch(value) is None or len(value) > 80:
        raise BundleError("EXACT_DECIMAL_STRING_REQUIRED")
    result = Decimal(value)
    if not result.is_finite() or (nonnegative and result < 0) or (positive and result <= 0):
        raise BundleError("AMOUNT_OUT_OF_RANGE")
    return result


def _amounts(value) -> dict[str, Decimal]:
    if type(value) is not dict:
        raise BundleError("QUANTITY_MAP_REQUIRED")
    return {_id(key): _amount(amount, nonnegative=True) for key, amount in value.items()}


def _index(rows, key):
    result = {}
    for row in rows:
        identifier = _id(row[key])
        if identifier in result:
            raise BundleError("DUPLICATE_RECORD_IDENTITY")
        result[identifier] = row
    return result


def _nonzero(value):
    return {key: amount for key, amount in value.items() if amount}


def _report():
    return {"schema_version": SCHEMA + ".result", "status": "BLOCKED",
            "conformance_status": "NOT_EXECUTED", "evidence_kind": None,
            "bundle_manifest_sha256": None, "components": {
                name: {"status": "NOT_EXECUTED"} for name in COMPONENTS},
            "findings": [], "daily_results": [], "order_trace": [],
            "scenario_coverage": {}, "historical_parity_established": False,
            "execution_authorized": False, "production_ready": False}


def _finding(report, component, code, status="BLOCKED", record=None):
    item = {"component": component, "code": code, "status": status}
    if record is not None:
        item["record"] = record
    if item not in report["findings"]:
        report["findings"].append(item)
    previous = report["components"][component]["status"]
    if previous != "FAIL":
        report["components"][component]["status"] = status


def _finish(report):
    statuses = [row["status"] for row in report["components"].values()]
    report["status"] = "FAIL" if "FAIL" in statuses else "BLOCKED" if any(
        status != "PASS" for status in statuses) else "PASS"
    conformance = [report["components"][key]["status"] for key in
                   ("integrity", "coverage", "order_lifecycle", "accounting", "shared_account")]
    report["conformance_status"] = "FAIL" if "FAIL" in conformance else (
        "PASS" if all(status == "PASS" for status in conformance) else "BLOCKED")
    return report


def freeze_bundle(directory: Path | str) -> dict[str, Any]:
    """Finalize a new local export; an existing SHA256SUMS is never overwritten."""
    root = Path(directory)
    if any(path.is_symlink() for path in (root, *root.parents)) or (root / "SHA256SUMS").is_symlink():
        raise BundleError("UNSAFE_BUNDLE_PATH")
    if (root / "SHA256SUMS").exists():
        raise BundleError("BUNDLE_ALREADY_FROZEN")
    if (root / "manifest.json").is_symlink():
        raise BundleError("UNSAFE_BUNDLE_PATH")
    if (root / "manifest.json").stat().st_nlink != 1:
        raise BundleError("SHARED_MANIFEST_INODE")
    manifest = _json((root / "manifest.json").read_bytes())
    files = []
    for role, name in FILES.items():
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise BundleError("MISSING_OR_UNSAFE_BUNDLE_FILE")
        data = path.read_bytes()
        files.append({"role": role, "path": name, "sha256": _sha(data),
                      "size_bytes": len(data), "rows": len(_read_rows(path))})
    manifest["files"] = files
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    sums = [(_sha((root / "manifest.json").read_bytes()), "manifest.json")]
    sums.extend((row["sha256"], row["path"]) for row in files)
    (root / "SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for digest, name in sums))
    return manifest


def _load(root, report):
    expected = {"manifest.json", "SHA256SUMS", *FILES.values()}
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise BundleError("UNSAFE_BUNDLE_PATH")
    if not root.is_dir():
        raise BundleError("BUNDLE_DIRECTORY_MISSING", blocked=True)
    present = {path.name for path in root.iterdir()}
    if expected - present:
        raise BundleError("REQUIRED_BUNDLE_FILE_MISSING", blocked=True)
    if present - expected:
        raise BundleError("UNLISTED_BUNDLE_FILES")
    if any((root / name).is_symlink() or not (root / name).is_file() for name in expected):
        raise BundleError("UNSAFE_BUNDLE_PATH")
    raw_manifest = (root / "manifest.json").read_bytes()
    manifest = _json(raw_manifest)
    report["bundle_manifest_sha256"] = _sha(raw_manifest)
    if any(manifest.get(key) is None for key in ("schema_version", "evidence_kind", "bundle_id", "created_at",
                                                "account_alias", "currency", "profiles", "first_session", "last_session")):
        raise BundleError("REQUIRED_MANIFEST_METADATA_MISSING", blocked=True)
    if manifest["schema_version"] != SCHEMA:
        raise BundleError("UNSUPPORTED_BUNDLE_SCHEMA", blocked=True)
    if manifest["evidence_kind"] not in {"historical", "synthetic_fixture"}:
        raise BundleError("INVALID_EVIDENCE_KIND")
    report["evidence_kind"] = manifest["evidence_kind"]
    _id(manifest["bundle_id"])
    _clock(manifest["created_at"])
    if type(manifest["account_alias"]) is not str or not _ACCOUNT.fullmatch(manifest["account_alias"]):
        raise BundleError("PSEUDONYMOUS_ACCOUNT_REQUIRED")
    if manifest["currency"] != "USD":
        raise BundleError("UNSUPPORTED_ACCOUNT_CURRENCY", blocked=True)
    if (type(manifest["profiles"]) is not list or set(manifest["profiles"]) != set(PROFILES)
            or len(manifest["profiles"]) != 2):
        raise BundleError("BOTH_STRATEGY_PROFILES_REQUIRED", blocked=True)
    if _day(manifest["first_session"]) > _day(manifest["last_session"]):
        raise BundleError("INVALID_SESSION_WINDOW")
    checksums = {}
    for line in (root / "SHA256SUMS").read_text().splitlines():
        if len(line) < 67 or line[64:66] != "  ":
            raise BundleError("INVALID_CHECKSUM_FILE")
        digest, name = _digest(line[:64]), line[66:]
        if name in checksums or name not in expected - {"SHA256SUMS"}:
            raise BundleError("INVALID_CHECKSUM_FILE")
        checksums[name] = digest
    if set(checksums) != expected - {"SHA256SUMS"}:
        raise BundleError("INCOMPLETE_CHECKSUM_FILE")
    for name, digest in checksums.items():
        if _sha((root / name).read_bytes()) != digest:
            raise BundleError("FILE_DIGEST_MISMATCH")
    entries = _index(manifest["files"], "role")
    if set(entries) != set(FILES):
        raise BundleError("MANIFEST_ROLES_INCOMPLETE", blocked=True)
    data = {}
    for role, name in FILES.items():
        entry = entries[role]
        if entry["path"] != name:
            raise BundleError("UNSAFE_BUNDLE_PATH")
        raw = (root / name).read_bytes()
        rows = _read_rows(root / name)
        if (entry["sha256"] != checksums[name] or type(entry["size_bytes"]) is not int
                or entry["size_bytes"] != len(raw) or type(entry["rows"]) is not int
                or entry["rows"] != len(rows)):
            raise BundleError("MANIFEST_FILE_METADATA_MISMATCH")
        data[role] = rows
    report["components"]["integrity"]["status"] = "PASS"
    return manifest, data


def _coverage(manifest, data, report):
    sessions = data["sessions"]
    days = [_day(row["session"]) for row in sessions]
    if (not days or sorted(set(days)) != days or days[0] != manifest["first_session"]
            or days[-1] != manifest["last_session"]):
        raise BundleError("DECLARED_SESSION_COVERAGE_MISMATCH")
    releases = _index(data["releases"], "release_id")
    runs = _index(data["runs"], "run_id")
    source = _index(data["source_evidence"], "evidence_id")
    for row in source.values():
        if canonical_sha256(row["payload"]) != _digest(row["payload_sha256"]):
            raise BundleError("SOURCE_PAYLOAD_DIGEST_MISMATCH")
        if _clock(row["available_at"]) > _clock(row["observed_at"]):
            raise BundleError("SOURCE_AVAILABILITY_AFTER_OBSERVATION")
        _id(row["kind"])
    for role, rows in data.items():
        if role == "source_evidence":
            continue
        for index, row in enumerate(rows):
            if row.get("source_evidence_id") not in source:
                _finding(report, "coverage", "SOURCE_RECORD_MISSING", record=f"{role}:{index}")
    for release in releases.values():
        if (not re.fullmatch(r"[0-9a-f]{40}", release["source_revision"])
                or not release["source_hashes"]):
            raise BundleError("RELEASE_SOURCE_IDENTITY_MISSING", blocked=True)
        for digest in release["source_hashes"].values():
            _digest(digest)
        for field in ("dependency_lock_sha256", "image_sha256", "effective_config_sha256"):
            _digest(release[field])
        if canonical_sha256(release["config"]) != _digest(release["config_sha256"]):
            raise BundleError("RELEASE_CONFIG_DIGEST_MISMATCH")
        start, end = _clock(release["effective_from"]), _clock(release["effective_until"])
        if start >= end:
            raise BundleError("INVALID_RELEASE_INTERVAL")
    listed = []
    for row in sessions:
        opening, closing = _clock(row["open_at"]), _clock(row["close_at"])
        if opening >= closing or opening.date().isoformat() != row["session"]:
            raise BundleError("INVALID_SESSION_CLOCK")
        _id(row["calendar_id"])
        if set(row["runs_by_profile"]) != set(PROFILES):
            raise BundleError("SESSION_PROFILE_COVERAGE_MISSING", blocked=True)
        for profile, ids in row["runs_by_profile"].items():
            if ids is None:
                _finding(report, "coverage", "SESSION_RUNS_UNOBSERVED", record=f"{row['session']}:{profile}")
                continue
            if type(ids) is not list:
                raise BundleError("RUN_LIST_REQUIRED")
            if not ids and not row["no_run_reasons"].get(profile):
                raise BundleError("OBSERVED_NO_RUN_REASON_REQUIRED", blocked=True)
            for run_id in ids:
                run = runs[run_id]
                if run["profile"] != profile or run["session"] != row["session"]:
                    raise BundleError("SESSION_RUN_BINDING_MISMATCH")
                listed.append(run_id)
    if Counter(listed) != Counter({run_id: 1 for run_id in runs}):
        raise BundleError("RUN_COVERAGE_MISMATCH")
    for run in runs.values():
        clock = _clock(run["decision_at"])
        release = releases[run["release_id"]]
        if (run["account_alias"] != manifest["account_alias"] or run["profile"] not in PROFILES
                or not _clock(release["effective_from"]) <= clock < _clock(release["effective_until"])):
            raise BundleError("RUN_RELEASE_OR_ACCOUNT_BINDING_MISMATCH")
        if (canonical_sha256(run["input"]) != _digest(run["input_sha256"])
                or canonical_sha256(run["output"]) != _digest(run["output_sha256"])):
            raise BundleError("RUN_INPUT_OR_OUTPUT_DIGEST_MISMATCH")
        if run["order_observation"] != "observed":
            _finding(report, "coverage", "RUN_ORDERS_UNOBSERVED", record=run["run_id"])
        if type(run["intent_ids"]) is not list or len(set(run["intent_ids"])) != len(run["intent_ids"]):
            raise BundleError("INVALID_INTENT_INDEX")
        if not run["availability"]:
            _finding(report, "coverage", "INPUT_AVAILABILITY_MISSING", record=run["run_id"])
        if not any(item.get("name") == "frozen_input" and item.get("sha256") == run["input_sha256"]
                   for item in run["availability"]):
            _finding(report, "coverage", "FROZEN_INPUT_AVAILABILITY_BINDING_MISSING", record=run["run_id"])
        for item in run["availability"]:
            _id(item["name"])
            _digest(item["sha256"])
            if item["source_evidence_id"] not in source:
                raise BundleError("AVAILABILITY_SOURCE_MISSING", blocked=True)
            if _clock(item["available_at"]) > clock:
                raise BundleError("INPUT_AVAILABLE_AFTER_DECISION")
    if report["components"]["coverage"]["status"] == "NOT_EXECUTED":
        report["components"]["coverage"]["status"] = "PASS"
    _finding(report, "provenance", "SOURCE_AUTHENTICATION_NOT_ESTABLISHED")
    _finding(report, "provenance", "CALENDAR_COMPLETENESS_NOT_INDEPENDENTLY_ATTESTED")
    _finding(report, "provenance", "DECISION_TO_ORDER_SIZING_AND_RISK_LINK_UNVERIFIED")
    if manifest["evidence_kind"] == "synthetic_fixture":
        _finding(report, "provenance", "SYNTHETIC_FIXTURE_IS_NOT_HISTORICAL_EVIDENCE")
    return runs, releases


def _decisions(manifest, runs, releases, report, enabled):
    if not enabled:
        _finding(report, "daily_replay", "DECISION_REPLAY_NOT_REQUESTED")
        return
    try:
        from .production_decision_replay import (
            ProductionDecisionReplayRequest, production_source_hashes, replay_production_decision,
        )
    except ImportError:
        _finding(report, "daily_replay", "CURRENT_SOURCE_ADAPTER_UNAVAILABLE")
        return
    for run in sorted(runs.values(), key=lambda row: (_clock(row["decision_at"]), row["run_id"])):
        release = releases[run["release_id"]]
        entry = {"run_id": run["run_id"], "status": "BLOCKED", "computed": False,
                 "calculation_status": "NOT_EXECUTED"}
        if release["source_hashes"] != production_source_hashes():
            entry["reason"] = "HISTORICAL_RELEASE_ADAPTER_UNAVAILABLE"
            report["daily_results"].append(entry)
            _finding(report, "daily_replay", entry["reason"], record=run["run_id"])
            continue
        result = replay_production_decision(ProductionDecisionReplayRequest(
            profile=run["profile"], source_revision=release["source_revision"],
            source_hashes=release["source_hashes"], config=release["config"],
            config_sha256=release["config_sha256"], effective_config_sha256=release["effective_config_sha256"],
            frozen_input=run["input"], input_sha256=run["input_sha256"],
            decision_at=run["decision_at"], evidence_kind=manifest["evidence_kind"],
            publication_evidence=None, execution_evidence=None))
        if result.trace is not None and "decision_targets" in result.trace:
            entry["computed"] = True
            entry["calculation_status"] = "PASS"
            entry["computed_output_sha256"] = canonical_sha256(result.trace)
            entry["golden_comparison"] = "PASS" if entry["computed_output_sha256"] == run["output_sha256"] else "FAIL"
            if entry["golden_comparison"] == "FAIL":
                _finding(report, "daily_replay", "GOLDEN_DECISION_MISMATCH", "FAIL", run["run_id"])
        entry["gaps"] = list(result.gaps)
        report["daily_results"].append(entry)
    _finding(report, "daily_replay", "COMPLETE_DEPENDENCY_AND_PUBLICATION_REPLAY_UNVERIFIED")


def _snapshot_state(snapshot, account):
    if snapshot["account_alias"] != account:
        raise BundleError("SNAPSHOT_ACCOUNT_BINDING_MISMATCH")
    if type(snapshot["sequence"]) is not int or snapshot["sequence"] < 0:
        raise BundleError("INVALID_SNAPSHOT_SEQUENCE")
    _clock(snapshot["at"])
    if _clock(snapshot["valid_until"]) < _clock(snapshot["at"]):
        raise BundleError("INVALID_SNAPSHOT_VALIDITY")
    positions = _amounts(snapshot["positions"])
    allocations = {profile: _amounts(values) for profile, values in snapshot["allocations"].items()}
    if set(allocations) != set(PROFILES):
        raise BundleError("BOTH_STRATEGY_ALLOCATIONS_REQUIRED", blocked=True)
    symbols = set(positions) | {symbol for values in allocations.values() for symbol in values}
    if any(sum((values.get(symbol, Decimal(0)) for values in allocations.values()), Decimal(0))
           != positions.get(symbol, Decimal(0)) for symbol in symbols):
        raise BundleError("POSITION_OWNERSHIP_MISMATCH")
    cash = _amount(snapshot["cash"], nonnegative=True)
    settled = _amount(snapshot["settled_cash"], nonnegative=True)
    reserved = _amounts(snapshot["reserved_cash"])
    if settled > cash or sum(reserved.values(), Decimal(0)) > settled:
        raise BundleError("SHARED_CASH_OVERCOMMITTED")
    return {"cash": cash, "settled_cash": settled, "positions": positions,
            "allocations": allocations, "reserved_cash": reserved,
            "receivables": _amounts(snapshot["receivables"])}


def _compare_snapshot(state, snapshot, account):
    expected = _snapshot_state(snapshot, account)
    for key in ("cash", "settled_cash"):
        if state[key] != expected[key]:
            raise BundleError("ACCOUNT_CASH_RECONCILIATION_MISMATCH")
    for key in ("positions", "reserved_cash", "receivables"):
        if _nonzero(state[key]) != _nonzero(expected[key]):
            raise BundleError("ACCOUNT_POSITION_OR_RESERVATION_MISMATCH")
    if any(_nonzero(state["allocations"][profile]) != _nonzero(expected["allocations"][profile])
           for profile in PROFILES):
        raise BundleError("STRATEGY_OWNERSHIP_RECONCILIATION_MISMATCH")


def _order_event(event, state, intents, attempts, executions, runs, snapshots, scenarios):
    kind, intent_id = event["kind"], _id(event["intent_id"])
    if kind in {"reserve", "submit"} and any(item["status"] == "unknown" for item in attempts.values()):
        raise BundleError("ACCOUNT_UNKNOWN_OUTCOME_BLOCKS_NEW_EXECUTION")
    semantic = None
    if kind == "fill":
        execution_id = _id(event["execution_id"])
        semantic = {key: event[key] for key in ("intent_id", "attempt_id", "broker_order_id",
                    "execution_id", "event_at", "quantity", "price", "fee", "cumulative_quantity", "remaining_quantity")}
        if execution_id in executions:
            if executions[execution_id] != semantic:
                raise BundleError("EXECUTION_IDENTITY_CONFLICT")
            scenarios["duplicate_execution_idempotence"] = True
            return
    if kind == "reserve":
        if intent_id in intents:
            raise BundleError("DUPLICATE_INTENT")
        run = runs[event["run_id"]]
        if event["profile"] != run["profile"] or intent_id not in run["intent_ids"]:
            raise BundleError("INTENT_RUN_BINDING_MISMATCH")
        if _clock(event["event_at"]) < _clock(run["decision_at"]):
            raise BundleError("INTENT_PRECEDES_DECISION")
        observation = snapshots[event["observation_id"]]
        if (observation["sequence"] >= event["sequence"]
                or not _clock(observation["at"]) <= _clock(event["event_at"]) <= _clock(observation["valid_until"])):
            raise BundleError("STALE_OR_FUTURE_ACCOUNT_OBSERVATION")
        side = event["side"]
        if side not in {"buy", "sell"}:
            raise BundleError("UNSUPPORTED_ORDER_SIDE")
        quantity = _amount(event["quantity"], positive=True)
        reserved = _amount(event["reserved_cash"], nonnegative=True)
        if (side == "sell" and reserved != 0) or (side == "buy" and reserved <= 0):
            raise BundleError("INVALID_ORDER_RESERVATION")
        profile, symbol = event["profile"], _id(event["symbol"])
        if side == "sell":
            pending = sum((item["quantity"] - item["filled"] for item in intents.values()
                           if item["side"] == "sell" and item["profile"] == profile
                           and item["symbol"] == symbol and not item["released"]), Decimal(0))
            if pending + quantity > state["allocations"][profile].get(symbol, Decimal(0)):
                raise BundleError("STRATEGY_SELL_QUANTITY_OVERCOMMITTED")
        state["reserved_cash"][intent_id] = reserved
        if sum(state["reserved_cash"].values(), Decimal(0)) > state["settled_cash"]:
            raise BundleError("SHARED_CASH_OVERCOMMITTED")
        intents[intent_id] = {"quantity": quantity, "filled": Decimal(0), "side": side,
                              "profile": profile, "symbol": symbol, "released": False, "attempts": [],
                              "reserved_at": _clock(event["event_at"])}
        active_profiles = {item["profile"] for item in intents.values()
                           if item["side"] == "buy" and not item["released"]}
        scenarios["concurrent_strategy_buy_reservations"] |= len(active_profiles) == 2
        return
    intent = intents[intent_id]
    if _clock(event["event_at"]) < intent["reserved_at"]:
        raise BundleError("ORDER_EVENT_PRECEDES_RESERVATION")
    if intent["released"]:
        raise BundleError("EVENT_AFTER_RESERVATION_RELEASE")
    if kind == "release":
        if not intent["attempts"] or any(attempts[key]["status"] not in {"filled", "cancelled", "rejected"}
                                          for key in intent["attempts"]):
            raise BundleError("UNRESOLVED_RESERVATION_RELEASE")
        if any(_clock(event["event_at"]) < attempts[key]["terminal_at"] for key in intent["attempts"]):
            raise BundleError("RESERVATION_RELEASE_PRECEDES_TERMINAL_OUTCOME")
        state["reserved_cash"].pop(intent_id)
        intent["released"] = True
        return
    attempt_id = _id(event["attempt_id"])
    if kind == "submit":
        if attempt_id in attempts:
            raise BundleError("DUPLICATE_SUBMISSION_ATTEMPT")
        if any(attempts[key]["status"] not in {"filled", "cancelled", "rejected"} for key in intent["attempts"]):
            raise BundleError("RETRY_WITH_UNRESOLVED_ATTEMPT")
        if any(_clock(event["event_at"]) < attempts[key]["terminal_at"] for key in intent["attempts"]):
            raise BundleError("RETRY_PRECEDES_TERMINAL_OUTCOME")
        quantity = _amount(event["quantity"], positive=True)
        if quantity != intent["quantity"] - intent["filled"]:
            raise BundleError("RETRY_REMAINING_QUANTITY_MISMATCH")
        attempts[attempt_id] = {"intent_id": intent_id, "quantity": quantity, "filled": Decimal(0),
                                "status": "submitted", "broker_order_id": None,
                                "submitted_at": _clock(event["event_at"]), "terminal_at": None,
                                "latest_fill_at": None}
        scenarios["retry_after_terminal"] |= bool(intent["attempts"])
        intent["attempts"].append(attempt_id)
        return
    attempt = attempts[attempt_id]
    if attempt["intent_id"] != intent_id:
        raise BundleError("ATTEMPT_INTENT_BINDING_MISMATCH")
    if _clock(event["event_at"]) < attempt["submitted_at"]:
        raise BundleError("BROKER_EVENT_PRECEDES_SUBMISSION")
    if kind == "fill":
        if attempt["status"] not in {"acknowledged", "partial", "unknown"}:
            raise BundleError("FILL_WITHOUT_OPEN_BROKER_ORDER")
        if attempt["broker_order_id"] is None and attempt["status"] == "unknown":
            broker_id = _id(event["broker_order_id"])
            if any(item is not attempt and item["broker_order_id"] == broker_id for item in attempts.values()):
                raise BundleError("BROKER_ORDER_REUSED_ACROSS_ATTEMPTS")
            attempt["broker_order_id"] = broker_id
        if _id(event["broker_order_id"]) != attempt["broker_order_id"]:
            raise BundleError("FILL_ORDER_BINDING_MISMATCH")
        quantity = _amount(event["quantity"], positive=True)
        cumulative = _amount(event["cumulative_quantity"], nonnegative=True)
        remaining = _amount(event["remaining_quantity"], nonnegative=True)
        if cumulative != attempt["filled"] + quantity or cumulative + remaining != attempt["quantity"]:
            raise BundleError("CUMULATIVE_FILL_MISMATCH")
        price, fee = _amount(event["price"], positive=True), _amount(event["fee"], nonnegative=True)
        cash_delta = quantity * price
        sign = Decimal(1) if intent["side"] == "buy" else Decimal(-1)
        if sign > 0:
            cost = cash_delta + fee
            if state["reserved_cash"][intent_id] < cost:
                raise BundleError("FILL_EXCEEDS_CASH_RESERVATION")
            state["reserved_cash"][intent_id] -= cost
            state["cash"] -= cost
            state["settled_cash"] -= cost
        else:
            state["cash"] += cash_delta - fee
        symbol, profile = intent["symbol"], intent["profile"]
        state["positions"][symbol] = state["positions"].get(symbol, Decimal(0)) + sign * quantity
        owned = state["allocations"][profile]
        owned[symbol] = owned.get(symbol, Decimal(0)) + sign * quantity
        if owned[symbol] < 0:
            raise BundleError("NEGATIVE_STRATEGY_OWNERSHIP")
        attempt["filled"], intent["filled"] = cumulative, intent["filled"] + quantity
        fill_clock = _clock(event["event_at"])
        attempt["latest_fill_at"] = max(attempt["latest_fill_at"] or fill_clock, fill_clock)
        if remaining == 0:
            scenarios["resolved_unknown"] |= attempt["status"] == "unknown"
            attempt["status"] = "filled"
            attempt["terminal_at"] = attempt["latest_fill_at"]
        elif attempt["status"] != "unknown":
            attempt["status"] = "partial"
        scenarios["partial_fill"] |= remaining > 0
        executions[execution_id] = semantic
        return
    if attempt["status"] in {"filled", "cancelled", "rejected"}:
        raise BundleError("CONTRADICTORY_TERMINAL_ORDER_EVENT")
    if kind == "acknowledged":
        broker_id = _id(event["broker_order_id"])
        if attempt["broker_order_id"] not in {None, broker_id}:
            raise BundleError("BROKER_ORDER_IDENTITY_CONFLICT")
        if any(item is not attempt and item["broker_order_id"] == broker_id for item in attempts.values()):
            raise BundleError("BROKER_ORDER_REUSED_ACROSS_ATTEMPTS")
        attempt["broker_order_id"] = broker_id
        scenarios["resolved_unknown"] |= attempt["status"] == "unknown"
        attempt["status"] = "partial" if attempt["filled"] else "acknowledged"
    elif kind == "unknown":
        attempt["status"] = "unknown"
    elif kind in {"rejected", "cancelled"}:
        if event["reason"] != "broker_" + kind:
            raise BundleError("UNCONFIRMED_TERMINAL_OUTCOME")
        if kind == "rejected" and attempt["filled"]:
            raise BundleError("REJECTION_AFTER_FILL")
        if attempt["latest_fill_at"] is not None and _clock(event["event_at"]) < attempt["latest_fill_at"]:
            raise BundleError("BROKER_TERMINAL_PRECEDES_INCLUDED_FILL")
        scenarios["resolved_unknown"] |= attempt["status"] == "unknown"
        attempt["status"] = kind
        attempt["terminal_at"] = _clock(event["event_at"])
    else:
        raise BundleError("UNSUPPORTED_ORDER_EVENT", blocked=True)


def _account_event(event, state, intents, scenarios):
    kind = event["kind"]
    if kind == "external_cashflow":
        amount = _amount(event["amount"])
        state["cash"] += amount
        if event["settled"] is not True:
            raise BundleError("UNSUPPORTED_UNSETTLED_EXTERNAL_FLOW", blocked=True)
        state["settled_cash"] += amount
    elif kind == "settlement":
        amount = _amount(event["amount"], positive=True)
        state["settled_cash"] += amount
        scenarios["settlement"] = True
    elif kind == "split":
        symbol, ratio = _id(event["symbol"]), _amount(event["ratio"], positive=True)
        if any(item["symbol"] == symbol and not item["released"] for item in intents.values()):
            raise BundleError("SPLIT_WITH_PENDING_ORDER_REQUIRES_ADAPTER", blocked=True)
        state["positions"][symbol] = state["positions"].get(symbol, Decimal(0)) * ratio
        for allocation in state["allocations"].values():
            allocation[symbol] = allocation.get(symbol, Decimal(0)) * ratio
    elif kind == "dividend_ex":
        identifier, symbol = _id(event["entitlement_id"]), _id(event["symbol"])
        if identifier in state["_entitlement_ids"]:
            raise BundleError("DUPLICATE_DIVIDEND_ENTITLEMENT")
        state["_entitlement_ids"].add(identifier)
        state["_entitlement_at"][identifier] = _clock(event["event_at"])
        state["receivables"][identifier] = state["positions"].get(symbol, Decimal(0)) * _amount(event["per_share"], nonnegative=True)
    elif kind == "dividend_pay":
        identifier = _id(event["entitlement_id"])
        if identifier not in state["receivables"]:
            raise BundleError("DIVIDEND_ENTITLEMENT_MISSING")
        if _clock(event["event_at"]) < state["_entitlement_at"][identifier]:
            raise BundleError("DIVIDEND_PAYMENT_PRECEDES_ENTITLEMENT")
        amount = state["receivables"].pop(identifier)
        if amount != _amount(event["amount"], nonnegative=True):
            raise BundleError("DIVIDEND_PAYMENT_MISMATCH")
        state["cash"] += amount
        state["settled_cash"] += amount
    elif kind == "fee":
        amount = _amount(event["amount"], nonnegative=True)
        state["cash"] -= amount
        state["settled_cash"] -= amount
    elif kind == "restart":
        if event["reservation_sha256"] != canonical_sha256({key: str(value) for key, value in sorted(state["reserved_cash"].items())}):
            raise BundleError("RESTART_RESERVATION_STATE_MISMATCH")
        scenarios["restart_with_reservation"] |= any(state["reserved_cash"].values())
    else:
        raise BundleError("UNSUPPORTED_ACCOUNT_EVENT", blocked=True)


def _ledger(manifest, data, runs, report):
    snapshots = _index(data["account_snapshots"], "snapshot_id")
    if not snapshots:
        raise BundleError("ACCOUNT_SNAPSHOTS_MISSING", blocked=True)
    for session in data["sessions"]:
        boundaries = {}
        for boundary in ("opening", "closing"):
            matches = [row for row in snapshots.values() if row["session"] == session["session"] and row["boundary"] == boundary]
            if len(matches) != 1:
                raise BundleError("DAILY_ACCOUNT_BOUNDARY_MISSING", blocked=True)
            boundaries[boundary] = matches[0]
        opening, closing = boundaries["opening"], boundaries["closing"]
        if (opening["sequence"] > closing["sequence"] or _clock(opening["at"]) >= _clock(closing["at"])
                or _clock(opening["at"]) > _clock(session["open_at"])
                or _clock(closing["at"]) < _clock(session["close_at"])):
            raise BundleError("DAILY_ACCOUNT_BOUNDARY_CLOCK_MISMATCH")
    ordered_snapshots = sorted(snapshots.values(), key=lambda row: (row["sequence"], _clock(row["at"])))
    initial = ordered_snapshots[0]
    if initial["sequence"] != 0 or initial["boundary"] != "opening":
        raise BundleError("OPENING_BASELINE_REQUIRED", blocked=True)
    state = _snapshot_state(initial, manifest["account_alias"])
    state["_entitlement_ids"] = set(state["receivables"])
    state["_entitlement_at"] = {key: _clock(initial["at"]) for key in state["receivables"]}
    if state["reserved_cash"]:
        raise BundleError("OPENING_PENDING_ORDERS_REQUIRE_PREWINDOW_HISTORY", blocked=True)
    events = [(event, role) for role in ("order_events", "account_events") for event in data[role]]
    for event, _ in events:
        if type(event["sequence"]) is not int or event["sequence"] <= 0:
            raise BundleError("INVALID_EVENT_SEQUENCE")
    events.sort(key=lambda item: item[0]["sequence"])
    if [row[0]["sequence"] for row in events] != list(range(1, len(events) + 1)):
        raise BundleError("GLOBAL_EVENT_SEQUENCE_GAP_OR_DUPLICATE")
    if ordered_snapshots[-1]["sequence"] != len(events):
        raise BundleError("CLOSING_SNAPSHOT_DOES_NOT_COVER_EVENTS")
    _index([row[0] for row in events], "event_id")
    intents, attempts, executions = {}, {}, {}
    scenarios = dict.fromkeys(("concurrent_strategy_buy_reservations", "partial_fill", "duplicate_execution_idempotence",
                              "resolved_unknown", "retry_after_terminal", "restart_with_reservation", "settlement"), False)
    previous_observation = _clock(initial["at"])
    at_sequence = {}
    for snapshot in snapshots.values():
        if snapshot["sequence"] > len(events):
            raise BundleError("SNAPSHOT_SEQUENCE_OUTSIDE_LEDGER")
        if (snapshot["sequence"] < len(events)
                and _clock(snapshot["at"]) > _clock(events[snapshot["sequence"]][0]["observed_at"])):
            raise BundleError("SNAPSHOT_OMITS_ALREADY_OBSERVED_EVENT")
        at_sequence.setdefault(snapshot["sequence"], []).append(snapshot)
    for snapshot in at_sequence.get(0, []):
        _compare_snapshot(state, snapshot, manifest["account_alias"])
    for event, role in events:
        when, observed = _clock(event["event_at"]), _clock(event["observed_at"])
        if event["account_alias"] != manifest["account_alias"]:
            raise BundleError("EVENT_ACCOUNT_BINDING_MISMATCH")
        if when > observed or observed < previous_observation or when < _clock(initial["at"]):
            raise BundleError("NONCAUSAL_EVENT_OBSERVATION")
        previous_observation = observed
        if role == "order_events":
            _order_event(event, state, intents, attempts, executions, runs, snapshots, scenarios)
        else:
            _account_event(event, state, intents, scenarios)
        if (state["cash"] < 0 or state["settled_cash"] < 0 or state["settled_cash"] > state["cash"]
                or sum(state["reserved_cash"].values(), Decimal(0)) > state["settled_cash"]):
            raise BundleError("SHARED_CASH_OVERCOMMITTED")
        for snapshot in at_sequence.get(event["sequence"], []):
            if _clock(snapshot["at"]) < observed:
                raise BundleError("SNAPSHOT_PRECEDES_INCLUDED_EVENT")
            _compare_snapshot(state, snapshot, manifest["account_alias"])
        report["order_trace"].append({"sequence": event["sequence"], "event_id": event["event_id"],
                                      "kind": event["kind"], "cash": str(state["cash"]),
                                      "settled_cash": str(state["settled_cash"]),
                                      "reserved_cash": str(sum(state["reserved_cash"].values(), Decimal(0)))})
    expected_intents = Counter(identifier for run in runs.values() for identifier in run["intent_ids"])
    if expected_intents != Counter({identifier: 1 for identifier in intents}):
        raise BundleError("RUN_INTENT_COVERAGE_MISMATCH")
    unresolved = [key for key, value in intents.items() if not value["released"]]
    report["unresolved_intents"] = unresolved
    report["scenario_coverage"] = scenarios
    report["shared_account_scenario_status"] = "PASS" if all(scenarios.values()) else "BLOCKED"
    report["shared_account_acceptance"] = "BLOCKED"
    for component in ("order_lifecycle", "accounting", "shared_account"):
        report["components"][component]["status"] = "PASS"
    if unresolved:
        _finding(report, "order_lifecycle", "UNRESOLVED_ORDER_OR_RESERVATION")
    _finding(report, "provenance", "HISTORICAL_SHARED_RISK_POLICY_AND_ENROLLMENT_UNVERIFIED")


def _check_bundle(directory: Path | str, *, decision_replay: bool = True) -> dict[str, Any]:
    """Validate supplied evidence and replay its observed ledger without mutation."""
    report = _report()
    component = "integrity"
    try:
        manifest, data = _load(Path(directory), report)
        component = "coverage"
        runs, releases = _coverage(manifest, data, report)
        component = "daily_replay"
        _decisions(manifest, runs, releases, report, decision_replay)
        component = "accounting"
        _ledger(manifest, data, runs, report)
    except BundleError as exc:
        _finding(report, component, exc.code, "BLOCKED" if exc.blocked else "FAIL")
    except KeyError:
        _finding(report, component, "REQUIRED_FIELD_OR_LINK_MISSING")
    except Inexact:
        _finding(report, component, "DECIMAL_PRECISION_EXCEEDS_SUPPORTED_BOUND")
    except (TypeError, ValueError, OSError, OverflowError, InvalidOperation):
        _finding(report, component, "MALFORMED_OR_UNREADABLE_INPUT", "FAIL")
    return _finish(report)


def check_bundle(directory: Path | str, *, decision_replay: bool = True) -> dict[str, Any]:
    """Check without rounding the supported finite decimal strings during arithmetic."""
    with localcontext() as context:
        context.prec = 256
        context.traps[Inexact] = True
        return _check_bundle(directory, decision_replay=decision_replay)


def write_template(directory: Path | str) -> None:
    """Write a deterministic two-profile synthetic example into an empty directory."""
    from .historical_runtime_template import template_records

    root = Path(directory)
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise BundleError("UNSAFE_BUNDLE_PATH")
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise BundleError("TEMPLATE_DIRECTORY_NOT_EMPTY")
    manifest, records = template_records()
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for role, name in FILES.items():
        rows = records[role]
        text = ("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
                if name.endswith(".jsonl") else json.dumps(rows, indent=2, sort_keys=True) + "\n")
        (root / name).write_text(text)
    freeze_bundle(root)


def write_intake_skeleton(directory: Path | str) -> None:
    """Create an unfrozen historical intake with no invented account observations."""
    root = Path(directory)
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise BundleError("UNSAFE_BUNDLE_PATH")
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise BundleError("TEMPLATE_DIRECTORY_NOT_EMPTY")
    manifest = {"schema_version": SCHEMA, "evidence_kind": "historical", "bundle_id": None,
                "created_at": None, "account_alias": None, "currency": "USD", "profiles": list(PROFILES),
                "first_session": None, "last_session": None, "files": []}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for name in FILES.values():
        (root / name).write_text("" if name.endswith(".jsonl") else "[]\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "template", "freeze", "check"):
        command = commands.add_parser(name)
        command.add_argument("directory", type=Path)
        if name == "check":
            command.add_argument("--output", type=Path)
            command.add_argument("--skip-decision-replay", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "init":
        write_intake_skeleton(args.directory)
        return 0
    if args.command == "template":
        write_template(args.directory)
        return 0
    if args.command == "freeze":
        freeze_bundle(args.directory)
        return 0
    report = check_bundle(args.directory, decision_replay=not args.skip_decision_replay)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        if args.directory.resolve() in args.output.resolve().parents:
            parser.error("write check results outside the frozen input directory")
        try:
            with args.output.open("x", encoding="utf-8") as handle:
                handle.write(text)
        except FileExistsError:
            parser.error("output path already exists; select a new report path")
    else:
        print(text, end="")
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
