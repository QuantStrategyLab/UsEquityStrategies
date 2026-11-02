"""Check frozen local evidence without fetching data or executing strategies.

The two optional sibling checkers have fixed package names and reviewed source
digests. A local conformance result never establishes historical acceptance,
source authenticity, point-in-time availability or trading authority.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from decimal import Decimal, DecimalException
import hashlib
import importlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat

SCHEMA = "qsl.historical_validation_harness.v1"
SYMBOLS = ("QQQM", "SCHD", "DGRO", "SGOV", "SPYI", "QQQI")
MARKET_WINDOW = {"after_session": "2026-08-25", "through_session": "2026-10-30"}
_FIRST = "2026-08-26"
_LAST = MARKET_WINDOW["through_session"]
_PACKAGE_DIR = Path(__file__).absolute().parent
_MAX_FILE_BYTES = 16 * 1024 * 1024
_MAX_TOTAL_BYTES = 128 * 1024 * 1024
_HASH = re.compile(r"[0-9a-f]{64}")
_ALIAS = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,12})?")
_ROLES = {"checklist", "calendar", "state_continuity", "order_mapping"} | {
    symbol + "." + role for symbol in SYMBOLS for role in ("raw", "adjusted", "corporate_actions")}
_COMMON_SHA256 = "d3f93f9e4fe4a2083679f3b7a6d03510716e26f84873e46727b6d7b707b79641"
_ADAPTERS = {
    "state_continuity": ("historical_state_continuity", "check_state_continuity",
        "047013a1e6a5dcb89b97188efe47c3f6708021e39138b6b3ea1e377ea0ba12c8",
        "conformance_status", "PASS"),
    "order_mapping": ("historical_order_mapping", "validate_order_mapping",
        "057cda6ebc3741a898add13ae0d0e7221aba60aaec75715494a52c867a61ed64",
        "local_consistency", "CONSISTENT"),
}


class _Problem(ValueError):
    def __init__(self, code, status="FAIL"):
        super().__init__(code)
        self.code, self.status = code, status


def _require(condition, code, status="FAIL"):
    if not condition:
        raise _Problem(code, status)


def _object(value):
    _require(type(value) is dict, "JSON_OBJECT_REQUIRED")
    return value


def _need(value, key):
    _object(value)
    _require(key in value and value[key] is not None, "REQUIRED_FIELD_MISSING", "BLOCKED")
    return value[key]


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _digest(value):
    _require(type(value) is str and _HASH.fullmatch(value) is not None, "SHA256_REQUIRED")
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _constant(_value):
    raise _Problem("NONFINITE_JSON_NUMBER")


def _float(value):
    result = float(value)
    _require(math.isfinite(result), "NONFINITE_JSON_NUMBER")
    return result


def _json(raw):
    try:
        return _object(json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                                  parse_constant=_constant, parse_float=_float))
    except (UnicodeError, json.JSONDecodeError, RecursionError, OverflowError, ValueError) as error:
        if isinstance(error, _Problem):
            raise
        raise _Problem("MALFORMED_JSON") from error


def _relative(name):
    _require(type(name) is str and 0 < len(name) <= 256 and "\\" not in name,
             "UNSAFE_LOCAL_PATH")
    path = PurePosixPath(name)
    _require(bool(path.parts) and not path.is_absolute() and path.as_posix() == name
             and all(part not in {".", ".."} and re.fullmatch(r"[A-Za-z0-9_.-]+", part)
                     for part in path.parts), "UNSAFE_LOCAL_PATH")
    return path.parts


def _directory(path):
    path = Path(path)
    _require(".." not in path.parts, "UNSAFE_LOCAL_PATH")
    path = path.absolute()
    _require(not any(parent.is_symlink() for parent in (path, *path.parents)), "UNSAFE_LOCAL_PATH")
    return path


def _open_directory(path):
    path = _directory(path)
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read(root, name, limit=_MAX_FILE_BYTES):
    """Use directory descriptors so an intermediate symlink is never followed."""
    parts = _relative(name)
    descriptors = []
    try:
        directory = _open_directory(root)
        descriptors.append(directory)
        for part in parts[:-1]:
            directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            descriptors.append(directory)
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        descriptors.append(descriptor)
        before = os.fstat(descriptor)
        _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, "UNSAFE_LOCAL_FILE")
        _require(before.st_size <= limit, "INPUT_SIZE_LIMIT_EXCEEDED", "BLOCKED")
        with os.fdopen(os.dup(descriptor), "rb") as handle:
            raw = handle.read(limit + 1)
        after = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        _require(len(raw) <= limit and all(getattr(before, key) == getattr(after, key) for key in fields),
                 "INPUT_CHANGED_DURING_READ")
        return raw
    except FileNotFoundError as error:
        raise _Problem("INPUT_FILE_MISSING", "BLOCKED") from error
    except PermissionError as error:
        raise _Problem("INPUT_FILE_UNREADABLE", "BLOCKED") from error
    except OSError as error:
        raise _Problem("UNSAFE_OR_UNREADABLE_LOCAL_PATH") from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _finding(problem):
    return {"code": problem.code, "status": problem.status}


def _component():
    return {"status": "BLOCKED", "local_conformance": "BLOCKED", "findings": []}


def _fail(component, problem):
    if component["local_conformance"] != "FAIL":
        component["status"] = component["local_conformance"] = problem.status
    component["findings"].append(_finding(problem))


def _document(loaded, errors, role):
    if role in errors:
        raise errors[role]
    if role not in loaded:
        raise _Problem("FROZEN_INPUT_MISSING", "BLOCKED")
    return loaded[role]


def _load(root, expected, result):
    raw = _read(root, "manifest.json", 256 * 1024)
    result["manifest_sha256"] = _sha(raw)
    _require(result["manifest_sha256"] == _digest(expected), "MANIFEST_DIGEST_MISMATCH")
    manifest = _json(raw)
    _require(_need(manifest, "schema_version") == SCHEMA, "UNSUPPORTED_MANIFEST_SCHEMA", "BLOCKED")
    _require(set(manifest) == {"schema_version", "evidence_kind", "market_window", "files"},
             "UNEXPECTED_MANIFEST_FIELDS")
    _require(manifest["evidence_kind"] in {"synthetic_fixture", "historical"}, "UNSUPPORTED_EVIDENCE_KIND")
    _require(manifest["market_window"] == MARKET_WINDOW, "MARKET_WINDOW_MISMATCH")
    files = _object(manifest["files"])
    _require(set(files) <= _ROLES, "UNSUPPORTED_INPUT_ROLE")
    result["evidence_kind"] = manifest["evidence_kind"]
    loaded, errors, seen_paths, total = {}, {}, set(), 0
    for role, entry in files.items():
        info = result["input_files"][role] = {"status": "BLOCKED"}
        try:
            _require(set(_object(entry)) == {"path", "sha256", "size_bytes"}, "INVALID_FILE_ENTRY")
            _relative(entry["path"])
            _require(entry["path"] != "manifest.json" and entry["path"] not in seen_paths,
                     "DUPLICATE_INPUT_PATH")
            seen_paths.add(entry["path"])
            _digest(entry["sha256"])
            size = entry["size_bytes"]
            _require(type(size) is int and 0 <= size <= _MAX_FILE_BYTES, "INVALID_FILE_SIZE")
            total += size
            _require(total <= _MAX_TOTAL_BYTES, "INPUT_BUDGET_EXCEEDED", "BLOCKED")
            data = _read(root, entry["path"])
            info.update(sha256=_sha(data), size_bytes=len(data))
            _require(info["sha256"] == entry["sha256"] and len(data) == size, "INPUT_DIGEST_OR_SIZE_MISMATCH")
            loaded[role] = _json(data)
            info["status"] = "PASS"
        except _Problem as error:
            errors[role] = error
            info.update(status=error.status, code=error.code)
    statuses = [item["status"] for item in result["input_files"].values()]
    result["integrity"]["status"] = "FAIL" if "FAIL" in statuses else "BLOCKED" if "BLOCKED" in statuses else "PASS"
    result["integrity"]["local_conformance"] = result["integrity"]["status"]
    return loaded, errors


def _check_adapter(role, loaded, errors):
    result = _component()
    result["checker_sources"] = {}
    try:
        document = _document(loaded, errors, role)
        module_name, entrypoint, expected, local_field, success = _ADAPTERS[role]
        for name, digest in ((module_name, expected), ("historical_runtime_bundle", _COMMON_SHA256)):
            try:
                raw = _read(_PACKAGE_DIR, name + ".py")
            except _Problem as error:
                if error.code == "INPUT_FILE_MISSING":
                    raise _Problem("ADAPTER_UNAVAILABLE", "BLOCKED") from error
                raise
            result["checker_sources"][name] = _sha(raw)
            _require(_sha(raw) == digest, "ADAPTER_SOURCE_REVISION_UNSUPPORTED", "BLOCKED")
        module = importlib.import_module("." + module_name, __package__)
        _require(Path(module.__file__).absolute() == _PACKAGE_DIR / (module_name + ".py"),
                 "ADAPTER_IMPORT_ORIGIN_MISMATCH")
        checker = getattr(module, entrypoint)
        _require(callable(checker) and checker.__module__ == module.__name__, "ADAPTER_ENTRYPOINT_MISMATCH")
        checked = checker(document)
        _require(type(checked) is dict and checked.get("status") in {"FAIL", "BLOCKED"}
                 and checked.get("schema_version") == "qsl.historical_" + role + ".v1.result"
                 and all(checked.get(key) is False for key in (
                     "historical_parity_established", "production_ready", "execution_authorized")),
                 "INVALID_CHECKER_RESULT")
        local = checked.get(local_field)
        _require(local in {success, "BLOCKED", "FAIL"}, "INVALID_CHECKER_RESULT")
        # Source identifiers and record bodies are intentionally not copied.
        selected = ("schema_version", "status", "conformance_status", "local_consistency",
                    "runs_checked", "evaluations_checked", "restart_boundaries_checked", "checked_slice_count",
                    "observed_quantity_unit", "target_to_quantity_reproduced", "adapter_status",
                    "source_authenticity_established", "risk_authorization_status",
                    "historical_parity_established", "production_ready", "execution_authorized")
        result["checker_result"] = {key: checked[key] for key in selected if key in checked}
        result["checker_result"]["findings"] = [
            {"code": item["code"], "status": item["status"]} for item in checked["findings"]]
        result["local_conformance"] = "PASS" if local == success else local
        result["status"] = "FAIL" if "FAIL" in {local, checked["status"]} else "BLOCKED"
    except _Problem as error:
        _fail(result, error)
    except ImportError:
        _fail(result, _Problem("ADAPTER_UNAVAILABLE", "BLOCKED"))
    except (AttributeError, KeyError, TypeError, ValueError, OSError, RecursionError):
        _fail(result, _Problem("ADAPTER_EXECUTION_FAILED"))
    return result


def _check_prefix(row):
    result = _component()
    result.update(listing_complete_verified=False, historical_versions_coverage_verified=False)
    try:
        count, limit = row.get("listed_object_count"), row.get("output_limit")
        if limit is not None:
            _require(type(limit) is int and limit == 20, "LISTING_LIMIT_CONTRADICTION")
        if count is not None:
            _require(type(count) is int and 0 <= count <= 20, "LISTING_COUNT_EXCEEDS_LIMIT")
        for key in ("listing_complete_verified", "historical_versions_coverage_verified"):
            if row.get(key) is not None:
                _require(row[key] is False, "CHECKLIST_COMPLETENESS_CLAIM_UNSUPPORTED")
        _need(row, "listed_object_count")
        _need(row, "output_limit")
        for key in ("listing_complete_verified", "historical_versions_coverage_verified"):
            _need(row, key)
        _require(_need(row, "status") == "LIST_COMMAND_SUCCEEDED", "LISTING_NOT_OBSERVED", "BLOCKED")
        aliases = _need(row, "sample_object_aliases")
        _require(type(aliases) is list and all(type(item) is str for item in aliases)
                 and len(aliases) == len(set(aliases)) and len(aliases) <= count,
                 "SAMPLE_LISTING_COUNT_CONTRADICTION")
        pagination = _object(_need(row, "pagination"))
        for key in ("next_page_token_present", "output_cap_reached"):
            _require(pagination.get(key) is None or type(pagination[key]) is bool,
                     "INVALID_PAGINATION_OBSERVATION")
        missing = _need(row, "missing_or_blocked")
        _require(type(missing) is list, "INVALID_MISSING_OBSERVATION_LIST")
        _require(not missing, "LISTING_REPORTED_UNRESOLVED", "BLOCKED")
        result.update(status="PASS", local_conformance="PASS")
    except _Problem as error:
        _fail(result, error)
    return result


def _check_sample(sample):
    result = _component()
    result.update(object_authenticity_established=False, metadata_header_binding_verified=False)
    try:
        count = _need(sample, "body_bytes_read")
        _require(type(count) is int and 0 <= count <= 4096, "CHECKLIST_OBSERVATION_BUDGET_EXCEEDED")
        if count:
            if sample.get("http_status") is not None:
                _require(type(sample["http_status"]) is int and sample["http_status"] == 206,
                         "BODY_READ_WITHOUT_206")
            if sample.get("requested_range") is not None:
                _require(sample["requested_range"] == "bytes=0-4095", "NON_PREFIX_RANGE_REQUESTED")
            if sample.get("confirmed_range") is not None:
                value = sample["confirmed_range"]
                match = re.fullmatch(r"bytes 0-4095/([1-9][0-9]{0,29})", value) if type(value) is str else None
                _require(match is not None and int(match[1]) > 4096, "RANGE_OR_SMALL_OBJECT_CONTRADICTION")
        _require(count > 0, "SAMPLE_BODY_NOT_OBSERVED", "BLOCKED")
        for key in ("http_status", "requested_range", "confirmed_range"):
            _need(sample, key)
        for key in ("generation", "metageneration"):
            value = _need(sample, key)
            _require(type(value) is str and re.fullmatch(r"[1-9][0-9]{0,29}", value) is not None,
                     "INVALID_OBJECT_VERSION_OBSERVATION")
        _require(count == 4096, "SAMPLE_PREFIX_INCOMPLETE", "BLOCKED")
        _require(_need(sample, "metadata_status") == "OBSERVED", "SAMPLE_METADATA_UNVERIFIED", "BLOCKED")
        _require(_need(sample, "bounded_read_status") == "BOUNDED_PREFIX_SAVED",
                 "SAMPLE_READ_UNVERIFIED", "BLOCKED")
        _require(_need(sample, "schema_status") == "FIELDS_OBSERVED", "SAMPLE_SCHEMA_UNVERIFIED", "BLOCKED")
        fields = _need(sample, "fields")
        _require(type(fields) is list and bool(fields), "SAMPLE_FIELDS_UNOBSERVED", "BLOCKED")
        for field in fields:
            path = _need(field, "sanitized_path")
            basis = _need(field, "complete_record_basis")
            types = _need(field, "observed_local_types")
            _require(type(path) is str and 0 < len(path) <= 256 and type(basis) is str and bool(basis)
                     and type(_need(field, "dynamic_key_replaced")) is bool,
                     "INVALID_FIELD_OBSERVATION")
            _require(type(types) is list and bool(types) and all(type(item) is str for item in types)
                     and set(types) <= {"null", "boolean", "integer", "number", "string", "array", "object"},
                     "INVALID_FIELD_TYPE_OBSERVATION")
        dates = _need(sample, "date_observations")
        _require(type(dates) is list, "INVALID_DATE_OBSERVATION_LIST")
        for observation in dates:
            _require(_need(observation, "scope") == "SAMPLE_ONLY", "SAMPLE_DATE_COVERAGE_CLAIM_UNSUPPORTED")
        missing = _need(sample, "stop_or_missing_codes")
        _require(type(missing) is list, "INVALID_MISSING_OBSERVATION_LIST")
        _require(not missing, "SAMPLE_REPORTED_UNRESOLVED", "BLOCKED")
        result.update(status="PASS", local_conformance="PASS")
    except _Problem as error:
        _fail(result, error)
    except (TypeError, ValueError, OverflowError):
        _fail(result, _Problem("MALFORMED_SAMPLE_OBSERVATION"))
    return result


def _check_checklist(loaded, errors):
    result = _component()
    try:
        value = _document(loaded, errors, "checklist")
        _require(_need(value, "schema_version") == "qsl.local_readonly_feedback.v1",
                 "UNSUPPORTED_CHECKLIST_SCHEMA", "BLOCKED")
        _require(value.get("template_state") != "NOT_EXECUTED_TEMPLATE"
                 and value.get("performed_on_user_machine") is True, "CHECKLIST_NOT_OBSERVED", "BLOCKED")
        at = _need(value, "observed_at_utc")
        _require(type(at) is str and datetime.fromisoformat(at.replace("Z", "+00:00")).tzinfo is not None,
                 "AWARE_TIMESTAMP_REQUIRED")
        scope = _object(_need(value, "scope"))
        required = {"listing_and_bounded_structure_only": True, "body_bytes_per_object_limit": 4096,
                    "maximum_body_requests": 24, "full_object_reads_allowed": False,
                    "tail_reads_allowed": False, "gcs_exports_or_uploads_allowed": False}
        _require(all(type(scope.get(key)) is type(item) and scope[key] == item for key, item in required.items()),
                 "CHECKLIST_SCOPE_MISMATCH")
        summary = _object(_need(value, "execution_summary"))
        _require(summary.get("redaction_review_completed") is True, "REDACTION_REVIEW_MISSING", "BLOCKED")
        for key, maximum in (("listing_commands_executed", 12), ("metadata_describes_executed", 240),
                             ("body_requests_attempted", 24), ("body_bytes_read", 24 * 4096)):
            amount = _need(summary, key)
            _require(type(amount) is int and 0 <= amount <= maximum, "CHECKLIST_OBSERVATION_BUDGET_EXCEEDED")
        _require(summary["body_bytes_read"] <= summary["body_requests_attempted"] * 4096,
                 "CHECKLIST_OBSERVATION_BUDGET_EXCEEDED")
        prefixes = _need(value, "prefix_observations")
        expected_prefixes = {"market_" + symbol for symbol in SYMBOLS} | {
            "runtime_" + broker + "_" + profile
            for broker in ("schwab", "ibkr", "longbridge") for profile in ("tqqq", "soxl")}
        _require(type(prefixes) is list and len(prefixes) == 12, "CHECKLIST_PREFIX_INDEX_REQUIRED")
        aliases = [_need(row, "prefix_alias") for row in prefixes]
        _require(all(type(alias) is str for alias in aliases) and set(aliases) == expected_prefixes,
                 "CHECKLIST_PREFIX_INDEX_MISMATCH")
        samples = _need(value, "object_structure_observations")
        _require(type(samples) is list and len(samples) <= 24, "INVALID_CHECKLIST_SAMPLE_LIST")
        sample_bytes, sample_ids = 0, set()
        for sample in samples:
            _require(_object(sample).get("reviewed_for_sharing") is True,
                     "REDACTION_REVIEW_MISSING", "BLOCKED")
            alias = _need(sample, "object_alias")
            _require(type(alias) is str and _ALIAS.fullmatch(alias) is not None
                     and alias not in sample_ids, "INVALID_OR_DUPLICATE_SAMPLE_ALIAS")
            sample_ids.add(alias)
            _require(_need(sample, "prefix_alias") in expected_prefixes, "CHECKLIST_PREFIX_INDEX_MISMATCH")
            count = sample.get("body_bytes_read")
            if count is not None:
                _require(type(count) is int and 0 <= count <= 4096, "CHECKLIST_OBSERVATION_BUDGET_EXCEEDED")
                sample_bytes += count
        _require(sample_bytes <= summary["body_bytes_read"], "CHECKLIST_BYTE_COUNT_CONFLICT")
        coverage = _object(_need(value, "market_coverage"))
        _require(_need(coverage, "required_last_session") == _LAST
                 and _need(coverage, "post_cutoff_calendar_interval") == [_FIRST, _LAST], "MARKET_WINDOW_MISMATCH")
        _require(_need(coverage, "post_cutoff_status") in {"UNVERIFIED", "USER_REPORTED_GAP", "user_reported_gap"},
                 "CHECKLIST_COVERAGE_CLAIM_UNSUPPORTED")
        _require(not any(coverage.get(key) is True for key in (
            "coverage_start_verified", "coverage_end_verified", "row_completeness_verified",
            "raw_adjusted_consistency_verified", "corporate_actions_complete_verified",
            "point_in_time_availability_verified")), "CHECKLIST_COVERAGE_CLAIM_UNSUPPORTED")
        runtime = value.get("runtime_acceptance", {})
        _require(not any(_object(runtime).get(key) is True for key in (
            "actual_modes_verified", "physical_account_binding_verified",
            "two_profile_shared_account_verified", "full_order_fill_chain_verified")),
            "CHECKLIST_RUNTIME_CLAIM_UNSUPPORTED")
        result["prefix_results"] = {row["prefix_alias"]: _check_prefix(row) for row in prefixes}
        result["sample_results"] = [_check_sample(sample) for sample in samples]
        for sample, checked in zip(samples, result["sample_results"]):
            prefix = next(row for row in prefixes if row["prefix_alias"] == sample["prefix_alias"])
            listed = prefix.get("sample_object_aliases")
            if listed is None:
                _fail(checked, _Problem("LISTED_SAMPLE_ALIAS_UNOBSERVED", "BLOCKED"))
            elif type(listed) is not list or sample["object_alias"] not in listed:
                _fail(checked, _Problem("SAMPLE_NOT_BOUND_TO_LISTED_ALIAS"))
        statuses = [entry["local_conformance"] for entry in (
            *result["prefix_results"].values(), *result["sample_results"])]
        result["local_conformance"] = "FAIL" if "FAIL" in statuses else "BLOCKED" if "BLOCKED" in statuses else "PASS"
        result["status"] = "FAIL" if result["local_conformance"] == "FAIL" else "BLOCKED"
        if sample_bytes < summary["body_bytes_read"]:
            result["findings"].append({"code": "BODY_OBSERVATIONS_INCOMPLETE", "status": "BLOCKED"})
            if result["local_conformance"] != "FAIL":
                result["local_conformance"] = "BLOCKED"
        result["findings"].append({"code": "BOUNDED_STRUCTURE_IS_NOT_ROW_COVERAGE", "status": "BLOCKED"})
    except _Problem as error:
        _fail(result, error)
    except (TypeError, ValueError, OverflowError):
        _fail(result, _Problem("MALFORMED_CHECKLIST"))
    return result


def _day(value):
    _require(type(value) is str and len(value) == 10, "ISO_SESSION_REQUIRED")
    try:
        _require(date.fromisoformat(value).isoformat() == value, "ISO_SESSION_REQUIRED")
    except ValueError as error:
        if isinstance(error, _Problem):
            raise
        raise _Problem("ISO_SESSION_REQUIRED") from error
    return value


def _ordered_days(value):
    _require(type(value) is list and len(value) <= 100000, "SESSION_LIST_REQUIRED")
    days = [_day(item) for item in value]
    _require(days == sorted(set(days)), "SESSION_LIST_DUPLICATE_OR_UNSORTED")
    return days


def _calendar(loaded, errors):
    result = _component()
    sessions = None
    try:
        value = _document(loaded, errors, "calendar")
        _require(_need(value, "schema_version") == "qsl.market_sessions.v1", "UNSUPPORTED_CALENDAR_SCHEMA", "BLOCKED")
        _require(_need(value, "market_window") == MARKET_WINDOW, "MARKET_WINDOW_MISMATCH")
        identifier = _need(value, "calendar_id")
        _require(type(identifier) is str and _ALIAS.fullmatch(identifier) is not None,
                 "CALENDAR_IDENTIFIER_REQUIRED")
        _require(_need(value, "timezone") == "America/New_York", "CALENDAR_TIMEZONE_UNSUPPORTED", "BLOCKED")
        declared = _ordered_days(_need(value, "sessions"))
        closures = _ordered_days(_need(value, "non_sessions"))
        first, last = date.fromisoformat(_FIRST), date.fromisoformat(_LAST)
        every_day = {(first + timedelta(days=offset)).isoformat() for offset in range((last - first).days + 1)}
        _require(set(declared).isdisjoint(closures), "CALENDAR_PARTITION_OVERLAP")
        _require(set(declared) | set(closures) == every_day, "CALENDAR_PARTITION_INCOMPLETE")
        _require(bool(declared), "DECLARED_SESSIONS_EMPTY", "BLOCKED")
        sessions = set(declared)
        result.update(status="PASS", local_conformance="PASS", declared_session_count=len(sessions),
                      declared_non_session_count=len(closures), calendar_authenticity_established=False)
    except _Problem as error:
        _fail(result, error)
    return sessions, result


def _rows(value):
    rows = _need(value, "rows")
    _require(type(rows) is list and len(rows) <= 100000, "INVALID_ROW_ARRAY")
    return rows


def _amount(value, *, positive=False):
    _require(type(value) is str and len(value) <= 31 and _DECIMAL.fullmatch(value) is not None,
             "BOUNDED_DECIMAL_STRING_REQUIRED")
    number = Decimal(value)
    _require(not positive or number > 0, "PRICE_OR_ACTION_AMOUNT_NOT_POSITIVE")
    return number


def _prices(value, symbol, basis, sessions, result):
    _require(_need(value, "schema_version") == "qsl.market_prices.v1", "UNSUPPORTED_PRICE_SCHEMA", "BLOCKED")
    _require(_need(value, "symbol") == symbol and _need(value, "basis") == basis, "PRICE_ROLE_BINDING_MISMATCH")
    _require(_need(value, "currency") == "USD", "UNSUPPORTED_PRICE_CURRENCY", "BLOCKED")
    volume_basis = _need(value, "volume_basis")
    _require(volume_basis in {"raw_shares", "split_adjusted_shares"}, "VOLUME_BASIS_UNSPECIFIED", "BLOCKED")
    convention = _need(value, "adjustment_convention")
    allowed = {"none"} if basis == "raw" else {"split_only", "split_and_cash_dividend"}
    _require(convention in allowed and (basis != "raw" or volume_basis == "raw_shares"),
             "ADJUSTMENT_CONVENTION_UNSUPPORTED", "BLOCKED")
    days = []
    for row in _rows(value):
        day = _day(_need(row, "session"))
        values = {field: _amount(_need(row, field), positive=field != "volume")
                  for field in ("open", "high", "low", "close", "volume")}
        _require(values["low"] <= min(values["open"], values["close"])
                 <= max(values["open"], values["close"]) <= values["high"], "OHLC_BOUNDS_CONTRADICTION")
        if volume_basis == "raw_shares":
            _require(values["volume"] == values["volume"].to_integral_value(), "NONINTEGER_RAW_VOLUME")
        days.append(day)
    _require(days == sorted(set(days)), "DUPLICATE_OR_UNSORTED_PRICE_SESSION")
    window_days = {day for day in days if _FIRST <= day <= _LAST}
    result.update(rows_checked=len(days), rows_in_window=len(window_days),
                  rows_outside_window=len(days) - len(window_days),
                  observed_first_session_in_window=min(window_days) if window_days else None,
                  observed_last_session_in_window=max(window_days) if window_days else None,
                  adjustment_convention=convention, volume_basis=volume_basis)
    _require(sessions is not None, "DECLARED_CALENDAR_REQUIRED", "BLOCKED")
    _require(not window_days - sessions, "PRICE_ON_DECLARED_NON_SESSION")
    result["missing_sessions"] = sorted(sessions - window_days)
    _require(not result["missing_sessions"], "PRICE_SESSIONS_MISSING", "BLOCKED")
    result.update(status="PASS", local_conformance="PASS", coverage_for_declared_sessions=True)


def _actions(value, symbol, sessions, result):
    _require(_need(value, "schema_version") == "qsl.market_actions.v1", "UNSUPPORTED_ACTION_SCHEMA", "BLOCKED")
    _require(_need(value, "symbol") == symbol, "ACTION_SYMBOL_MISMATCH")
    _require(_need(value, "currency") == "USD", "UNSUPPORTED_ACTION_CURRENCY", "BLOCKED")
    seen = set()
    for row in _rows(value):
        identifier = _need(row, "event_id")
        _require(type(identifier) is str and _ALIAS.fullmatch(identifier) is not None, "ACTION_IDENTIFIER_REQUIRED")
        _require(identifier not in seen, "DUPLICATE_ACTION_ID")
        seen.add(identifier)
        day = _day(_need(row, "effective_session"))
        kind = _need(row, "type")
        _require(kind in {"split", "cash_dividend"}, "ACTION_ADAPTER_UNAVAILABLE", "BLOCKED")
        for field in (("numerator", "denominator") if kind == "split" else ("amount",)):
            _amount(_need(row, field), positive=True)
        if sessions is not None and _FIRST <= day <= _LAST:
            _require(day in sessions, "ACTION_ON_DECLARED_NON_SESSION")
    result.update(rows_checked=len(seen), corporate_actions_complete_verified=False,
                  declared_calendar_membership_checked=sessions is not None,
                  status="PASS", local_conformance="PASS")


def _check_market(loaded, errors):
    result = _component()
    result.update(coverage_for_declared_sessions=False, actual_market_coverage_established=False,
                  corporate_actions_complete_verified=False, raw_adjusted_consistency_verified=False,
                  point_in_time_availability_verified=False, source_authenticity_established=False,
                  market_window=dict(MARKET_WINDOW), symbols={})
    sessions, calendar = _calendar(loaded, errors)
    result["calendar"] = calendar
    statuses = [calendar["local_conformance"]]
    prices = []
    for symbol in SYMBOLS:
        result["symbols"][symbol] = {}
        for role in ("raw", "adjusted", "corporate_actions"):
            entry = result["symbols"][symbol][role] = _component()
            try:
                value = _document(loaded, errors, symbol + "." + role)
                if role == "corporate_actions":
                    _actions(value, symbol, sessions, entry)
                else:
                    _prices(value, symbol, role, sessions, entry)
            except _Problem as error:
                _fail(entry, error)
            except (TypeError, ValueError, OverflowError, RecursionError, DecimalException):
                _fail(entry, _Problem("MALFORMED_MARKET_RECORD"))
            statuses.append(entry["local_conformance"])
            if role != "corporate_actions":
                prices.append(entry["local_conformance"])
    result["coverage_for_declared_sessions"] = sessions is not None and all(value == "PASS" for value in prices)
    result["local_conformance"] = "FAIL" if "FAIL" in statuses else "PASS" if all(
        value == "PASS" for value in statuses) else "BLOCKED"
    result["status"] = "FAIL" if result["local_conformance"] == "FAIL" else "BLOCKED"
    result["findings"] = [{"code": code, "status": "BLOCKED"} for code in (
        "CALENDAR_SOURCE_AUTHENTICITY_UNVERIFIED", "MARKET_SOURCE_AUTHENTICITY_UNVERIFIED",
        "RAW_ADJUSTED_CONSISTENCY_UNVERIFIED", "CORPORATE_ACTION_COMPLETENESS_UNVERIFIED",
        "POINT_IN_TIME_AVAILABILITY_UNVERIFIED")]
    return result


def check_harness(directory: Path | str, *, manifest_sha256: str) -> dict:
    """Read only pinned local bytes; preserve independent missing-evidence results."""
    result = {"schema_version": SCHEMA + ".result", "status": "BLOCKED", "manifest_sha256": None,
              "evidence_kind": None, "input_files": {}, "integrity": _component(),
              "checklist": _component(), "checks": {name: _component() for name in (
                  "state_continuity", "order_mapping", "market_coverage")}, "findings": [],
              "historical_parity_established": False, "production_ready": False,
              "execution_authorized": False, "source_authenticity_established": False,
              "point_in_time_availability_verified": False, "export_authorized": False,
              "upload_authorized": False, "cross_component_identity_link_established": False}
    try:
        loaded, errors = _load(directory, manifest_sha256, result)
        result["checklist"] = _check_checklist(loaded, errors)
        for name in _ADAPTERS:
            result["checks"][name] = _check_adapter(name, loaded, errors)
        result["checks"]["market_coverage"] = _check_market(loaded, errors)
    except _Problem as error:
        _fail(result["integrity"], error)
        result["findings"].append(_finding(error))
    except (TypeError, ValueError, OverflowError, RecursionError):
        error = _Problem("MALFORMED_HARNESS_INPUT")
        _fail(result["integrity"], error)
        result["findings"].append(_finding(error))
    statuses = [result["integrity"]["status"], result["checklist"]["status"],
                *(value["status"] for value in result["checks"].values())]
    result["status"] = "FAIL" if "FAIL" in statuses else "BLOCKED"
    return result


def _write_new(path, directory, text):
    path, root = Path(path).absolute(), _directory(directory)
    _require(".." not in path.parts, "UNSAFE_LOCAL_PATH")
    _require(path != root and root not in path.parents, "OUTPUT_INSIDE_FROZEN_INPUT")
    parent = None
    try:
        parent = _open_directory(path.parent)
        descriptor = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
    except FileExistsError as error:
        raise _Problem("OUTPUT_ALREADY_EXISTS") from error
    except OSError as error:
        raise _Problem("OUTPUT_UNWRITABLE") from error
    finally:
        if parent is not None:
            os.close(parent)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = check_harness(args.directory, manifest_sha256=args.manifest_sha256)
    text = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        try:
            _write_new(args.output, args.directory, text)
        except _Problem as error:
            result["status"] = "FAIL"
            result["findings"].append(_finding(error))
            print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    else:
        print(text, end="")
    return 1 if result["status"] == "FAIL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
