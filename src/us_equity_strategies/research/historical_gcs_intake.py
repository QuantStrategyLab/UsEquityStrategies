"""Plan a bounded historical intake from an operator-reported GCS inventory.

This module reads local JSON and writes a new local plan. It has no GCS client,
network access, download, export, sanitization, upload or execution integration.
Directory selections and date boundaries remain unverified operator reports.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import re

DISCOVERY_SCHEMA = "qsl.gcs_discovery_summary.v1"
PLAN_SCHEMA = "qsl.historical_gcs_intake_plan.v1"
PLATFORMS = ("charles_schwab", "interactive_brokers", "longbridge")
PROFILES = ("tqqq_growth_income", "soxl_soxx_trend_income")
TICKERS = ("QQQM", "SCHD", "DGRO", "SGOV", "SPYI", "QQQI")
COMPONENT_ROLES = ("raw", "adjusted", "corporate_actions")
_PREFIX_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_BUCKET_URI = re.compile(r"gs://[a-z0-9][a-z0-9._-]*[a-z0-9]/?")


class _PlanInputError(ValueError):
    def __init__(self, code: str, field: str):
        super().__init__(code)
        self.code = code
        self.field = field


def _base_result():
    return {
        "schema_version": PLAN_SCHEMA,
        "status": "BLOCKED",
        "plan_state": "NOT_CREATED",
        "discovery_evidence_kind": "OPERATOR_REPORTED_NOT_INSPECTED",
        "discovery_sha256": None,
        "findings": [],
        "object_inspection_performed": False,
        "source_authentication_established": False,
        "point_in_time_availability_established": False,
        "export_authorized": False,
        "upload_authorized": False,
        "execution_authorized": False,
        "production_ready": False,
    }


def _finding(result, code, scope, status="BLOCKED"):
    result["findings"].append({"code": code, "scope": scope, "status": status})


def _failure(code, field="discovery"):
    result = _base_result()
    result["status"] = "FAIL"
    _finding(result, code, field, "FAIL")
    return result


def _object(value, field):
    if type(value) is not dict:
        raise _PlanInputError("OBJECT_REQUIRED", field)
    return value


def _date(value, field):
    if type(value) is not str:
        raise _PlanInputError("INVALID_DATE", field)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise _PlanInputError("INVALID_DATE", field) from error
    if parsed.isoformat() != value:
        raise _PlanInputError("INVALID_DATE", field)
    return parsed


def _bucket(value, field):
    if type(value) is not str or _BUCKET_URI.fullmatch(value) is None:
        raise _PlanInputError("INVALID_BUCKET_URI", field)
    return value.removesuffix("/")


def _prefix(value, field):
    if type(value) is not str or not value:
        raise _PlanInputError("INVALID_DIRECTORY_PREFIX", field)
    # Do not normalize traversal, repeated separators, escapes, URLs or queries.
    parts = value.removesuffix("/").split("/")
    if any(_PREFIX_SEGMENT.fullmatch(part) is None for part in parts):
        raise _PlanInputError("INVALID_DIRECTORY_PREFIX", field)
    return "/".join(parts) + "/"


def _exact_values(value, expected, field):
    if (type(value) is not list or any(type(item) is not str for item in value)
            or len(value) != len(expected) or set(value) != set(expected)):
        raise _PlanInputError("UNSUPPORTED_OR_DUPLICATE_SELECTION", field)


def _optional_boolean(value, field):
    if value is not None and type(value) is not bool:
        raise _PlanInputError("BOOLEAN_OR_UNKNOWN_REQUIRED", field)
    return value


def _reported_inception_count(value, field):
    if value is None:
        return None
    if value not in ("1-2", "1–2"):
        raise _PlanInputError("UNSUPPORTED_REPORTED_INCEPTION_COUNT", field)
    return {"minimum": 1, "maximum": 2}


def _parse_discovery(discovery):
    discovery = _object(discovery, "discovery")
    if discovery["schema_version"] != DISCOVERY_SCHEMA:
        raise _PlanInputError("UNSUPPORTED_DISCOVERY_SCHEMA", "schema_version")
    if discovery["evidence_kind"] != "operator_reported_discovery":
        raise _PlanInputError("UNSUPPORTED_DISCOVERY_EVIDENCE_KIND", "evidence_kind")
    runtime = _object(discovery["runtime"], "runtime")
    market = _object(discovery["market"], "market")
    _exact_values(runtime["platforms"], PLATFORMS, "runtime.platforms")
    _exact_values(runtime["profiles"], PROFILES, "runtime.profiles")
    _exact_values(market["required_components"], COMPONENT_ROLES, "market.required_components")
    runtime_bucket = _bucket(runtime["bucket_uri"], "runtime.bucket_uri")
    market_bucket = _bucket(market["bucket_uri"], "market.bucket_uri")
    execution_prefix = _prefix(runtime["execution_prefix"], "runtime.execution_prefix")
    market_prefix = _prefix(market["input_prefix"], "market.input_prefix")
    if type(runtime["auxiliary_prefixes"]) is not list:
        raise _PlanInputError("PREFIX_LIST_REQUIRED", "runtime.auxiliary_prefixes")
    auxiliary = [_prefix(value, "runtime.auxiliary_prefixes") for value in runtime["auxiliary_prefixes"]]
    if len(set(auxiliary)) != len(auxiliary):
        raise _PlanInputError("DUPLICATE_DIRECTORY_PREFIX", "runtime.auxiliary_prefixes")
    reported_last = _date(market["reported_last_session"], "market.reported_last_session")
    tickers = _object(market["tickers"], "market.tickers")
    if set(tickers) != set(TICKERS):
        raise _PlanInputError("REQUIRED_TICKERS_MISMATCH", "market.tickers")
    reported = {}
    for ticker in TICKERS:
        field = "market.tickers." + ticker
        row = _object(tickers[ticker], field)
        first_value = row.get("reported_first_session")
        first = None if first_value is None else _date(first_value, field + ".reported_first_session")
        if first is not None and first > reported_last:
            raise _PlanInputError("INCONSISTENT_REPORTED_DATE_BOUNDS", field)
        reported[ticker] = {
            "first": first,
            "no_internal_gaps": _optional_boolean(row.get("reported_no_internal_gaps"), field + ".reported_no_internal_gaps"),
            "earlier_history_missing": _optional_boolean(row.get("earlier_history_missing"), field + ".earlier_history_missing"),
            "inception_gap": _optional_boolean(row.get("inception_gap_reported"), field + ".inception_gap_reported"),
            "inception_count": _reported_inception_count(row.get("reported_missing_inception_sessions"), field + ".reported_missing_inception_sessions"),
        }
    return runtime_bucket, market_bucket, execution_prefix, market_prefix, auxiliary, reported_last, reported


def _gap(first, last, reason, evaluation_first):
    return {
        "first_date": first.isoformat(),
        "last_date": last.isoformat(),
        "reason": reason,
        "basis": "INCLUSIVE_CALENDAR_DATES_NOT_TRADING_SESSION_COUNTS",
        "evidence_status": "OUTSIDE_OPERATOR_REPORTED_BOUNDARY_NOT_INDEPENDENTLY_VERIFIED",
        "affects_warmup": first < evaluation_first,
        "affects_requested_window": last >= evaluation_first,
    }


def _coverage(reported, reported_last, first, last, effective_first):
    reported_first = reported["first"]
    gaps = []
    if reported_first is not None and effective_first < reported_first:
        gaps.append(_gap(effective_first, min(last, reported_first - timedelta(days=1)),
                         "BEFORE_REPORTED_FIRST_SESSION", first))
    if last > reported_last:
        gaps.append(_gap(max(effective_first, reported_last + timedelta(days=1)), last,
                         "AFTER_REPORTED_LAST_SESSION", first))
    return {
        "reported_first_session": reported_first.isoformat() if reported_first is not None else None,
        "reported_last_session": reported_last.isoformat(),
        "reported_no_internal_gaps": reported["no_internal_gaps"],
        "reported_earlier_history_missing": reported["earlier_history_missing"],
        "first_boundary_status": "OPERATOR_REPORTED_UNVERIFIED" if reported_first is not None else "UNVERIFIED",
        "coverage_verified": False,
        "session_calendar_verified": False,
        "gap_intervals": gaps,
        "inception_gap": {
            "operator_reported": reported["inception_gap"],
            "reported_session_count_range": reported["inception_count"],
            "missing_session_dates": None,
            "status": "UNVERIFIED_OPERATOR_REPORT",
        },
    }


def _selection_rules():
    return {
        "execution_environments": ["live", "paper", "dry_run", "unknown"],
        "record_kinds": ["execution", "reconciliation", "diagnostic", "not_run", "heartbeat", "unknown"],
        "physical_account_domain_keys": ["platform", "execution_environment", "server", "lifecycle_id", "account_alias"],
        "account_alias_namespace_keys": ["platform", "execution_environment", "server", "physical_account_identity", "lifecycle_id"],
        "separate_record_strata": ["profile", "record_kind"],
        "account_alias_rule": "Use the same stable alias for both profiles only within the same independently verified physical account domain.",
        "account_subdirectories_enumerated": False,
        "account_aliases_known": False,
        "cross_broker_account_merge_allowed": False,
        "logical_account_scope_is_physical_account": False,
        "dry_run_false_establishes_live_or_broker_paper": False,
        "legacy_paper_label_establishes_broker_paper": False,
        "bucket_project_is_runtime_source_project": False,
        "projection_empty_or_missing_fills_establish_zero_fills": False,
        "file_counts_establish_order_or_fill_counts": False,
        "filename_dates_establish_session_coverage": False,
        "unresolved_classification": "Preserve conflicting or missing evidence as unknown; retain source labels without promoting them to live or broker paper.",
        "scope_note": "Account scopes and prefixes are candidates; no physical account, source project or historical producer schema was inspected.",
    }


def build_intake_plan(discovery, first_session, last_session, *, warmup_first_session=None) -> dict:
    """Build a non-authorizing selection plan; malformed inputs return FAIL.

    All dates remain requested or operator-reported boundaries. No session count,
    missing inception date, account child path or component filename is inferred.
    """
    try:
        first = _date(first_session, "first_session")
        last = _date(last_session, "last_session")
        if first > last:
            raise _PlanInputError("REVERSED_REQUEST_WINDOW", "requested_window")
        warmup = None if warmup_first_session is None else _date(warmup_first_session, "warmup_first_session")
        if warmup is not None and warmup > first:
            raise _PlanInputError("WARMUP_AFTER_REQUEST_START", "warmup_first_session")
        effective_first = warmup if warmup is not None else first
        runtime_bucket, market_bucket, execution_prefix, market_prefix, auxiliary, reported_last, reported = _parse_discovery(discovery)
        digest = hashlib.sha256(json.dumps(discovery, sort_keys=True, separators=(",", ":"),
                                           ensure_ascii=True, allow_nan=False).encode()).hexdigest()
    except _PlanInputError as error:
        return _failure(error.code, error.field)
    except RecursionError:
        return _failure("DISCOVERY_NESTING_LIMIT_EXCEEDED")
    except KeyError:
        return _failure("REQUIRED_DISCOVERY_FIELD_MISSING")
    except (TypeError, ValueError, OverflowError):
        return _failure("MALFORMED_DISCOVERY")

    result = _base_result()
    result["plan_state"] = "PLAN_CREATED"
    result["discovery_sha256"] = digest
    result["requested_window"] = {
        "first_session": first.isoformat(), "last_session": last.isoformat(),
        "warmup_first_session": warmup.isoformat() if warmup is not None else None,
        "effective_first_session": effective_first.isoformat(),
        "date_basis": "INCLUSIVE_REQUESTED_DATES_WITHOUT_VERIFIED_SESSION_CALENDAR",
        "window_shortened": False,
    }
    result["runtime"] = {
        "bucket_uri": runtime_bucket,
        "execution_prefix": execution_prefix,
        "execution_selections": [],
        "auxiliary_selections": [{
            "directory_prefix": prefix, "gs_prefix": runtime_bucket + "/" + prefix,
            "selection_method": "STABLE_REFERENCE_OR_REVIEWED_ASSOCIATION_ONLY",
            "recursive_export_allowed": False,
        } for prefix in auxiliary],
        "selection_rules": _selection_rules(),
    }
    for platform in PLATFORMS:
        for profile in PROFILES:
            prefix = execution_prefix + platform + "/" + profile + "/"
            result["runtime"]["execution_selections"].append({
                "platform": platform, "profile": profile, "directory_prefix": prefix,
                "gs_prefix": runtime_bucket + "/" + prefix, "account_scope": "CANDIDATE_PREFIX_ONLY",
                "account_alias": None, "server": None, "lifecycle_id": None,
                "execution_environment": "unknown", "record_kind": "unknown",
                "producer_schema_status": "UNVERIFIED", "recursive_export_allowed": False,
            })
    result["market"] = {
        "bucket_uri": market_bucket, "input_prefix": market_prefix,
        "reported_last_session": reported_last.isoformat(),
        "object_schema_status": "UNVERIFIED", "normalization_adapter_status": "UNVERIFIED",
        "existing_importer_compatibility_assumed": False,
        "future_data_fill_allowed": False, "symbol_selections": [],
    }
    for ticker in TICKERS:
        coverage = _coverage(reported[ticker], reported_last, first, last, effective_first)
        prefix = market_prefix + ticker + "/"
        result["market"]["symbol_selections"].append({
            "ticker": ticker, "directory_prefix": prefix, "gs_prefix": market_bucket + "/" + prefix,
            "required_component_roles": list(COMPONENT_ROLES), "component_object_names": None,
            "component_roles_are_requirements_not_observed_schema": True, "coverage": coverage,
        })
        if reported[ticker]["first"] is None:
            _finding(result, "REPORTED_FIRST_SESSION_UNKNOWN", "market." + ticker)
        if reported[ticker]["inception_gap"] is True:
            _finding(result, "REPORTED_INCEPTION_GAP_UNVERIFIED", "market." + ticker)
        if any(gap["reason"] == "BEFORE_REPORTED_FIRST_SESSION" for gap in coverage["gap_intervals"]):
            _finding(result, "REQUEST_PRECEDES_REPORTED_MARKET_START", "market." + ticker)
    if last > reported_last:
        _finding(result, "REQUEST_EXCEEDS_REPORTED_MARKET_END", "market")
    result["evidence_layers"] = [
        {"layer": "ledger_reconciliation", "status": "BLOCKED", "required_evidence_roles": [
            "source_and_release_identity", "frozen_inputs_and_strategy_state", "decision_receipts",
            "order_attempt_lifecycle", "broker_fills_and_fees", "physical_account_bindings",
            "cash_settlement_and_positions", "global_sequence_and_reservations"],
         "roles_are_requirements_not_observed_schema": True,
         "price_presence_establishes_ledger_acceptance": False},
        {"layer": "price_recalculation", "status": "BLOCKED", "required_evidence_roles": [
            "raw", "adjusted", "corporate_actions", "session_calendar", "point_in_time_source_provenance"],
         "roles_are_requirements_not_observed_schema": True,
         "ledger_balance_establishes_price_acceptance": False},
    ]
    for code, scope in (
        ("SOURCE_AUTHENTICATION_UNVERIFIED", "all"),
        ("POINT_IN_TIME_AVAILABILITY_UNVERIFIED", "all"),
        ("OBJECT_AND_SESSION_COMPLETENESS_UNVERIFIED", "all"),
        ("PHYSICAL_ACCOUNT_DOMAIN_AND_MODE_UNVERIFIED", "runtime"),
        ("LONGBRIDGE_PRODUCER_SCHEMA_UNVERIFIED", "runtime.longbridge"),
        ("SCHWAB_SETTLED_CASH_SOURCE_REQUIRED", "runtime.charles_schwab"),
        ("MARKET_SCHEMA_AND_NORMALIZATION_ADAPTER_UNVERIFIED", "market"),
        ("CROSS_RUN_STRATEGY_STATE_CONTINUITY_UNVERIFIED", "runtime"),
        ("DECISION_TO_ORDER_SIZING_AND_RISK_LINK_UNVERIFIED", "runtime"),
        ("EXPORT_AND_UPLOAD_REQUIRE_SEPARATE_APPROVAL", "all"),
    ):
        _finding(result, code, scope)
    return result


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _PlanInputError("DUPLICATE_JSON_KEY", "discovery")
        result[key] = value
    return result


def _nonfinite_json(_value):
    raise _PlanInputError("NONFINITE_JSON_NUMBER", "discovery")


def _stdout_metadata(result):
    return {
        "schema_version": PLAN_SCHEMA, "status": result["status"], "plan_state": result["plan_state"],
        "execution_prefix_count": len(result.get("runtime", {}).get("execution_selections", [])),
        "market_prefix_count": len(result.get("market", {}).get("symbol_selections", [])),
        "finding_codes": sorted({row["code"] for row in result["findings"]}),
        "export_authorized": False, "upload_authorized": False,
        "execution_authorized": False, "production_ready": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("discovery", type=Path)
    parser.add_argument("--first-session", required=True)
    parser.add_argument("--last-session", required=True)
    parser.add_argument("--warmup-first-session")
    parser.add_argument("--output", required=True, type=Path, help="New local plan file; never overwritten")
    args = parser.parse_args(argv)
    try:
        discovery = json.loads(args.discovery.read_text(encoding="utf-8"),
                               object_pairs_hook=_json_object, parse_constant=_nonfinite_json)
        result = build_intake_plan(discovery, args.first_session, args.last_session,
                                   warmup_first_session=args.warmup_first_session)
    except _PlanInputError as error:
        result = _failure(error.code, error.field)
    except RecursionError:
        result = _failure("DISCOVERY_NESTING_LIMIT_EXCEEDED")
    except OSError:
        result = _failure("DISCOVERY_FILE_UNREADABLE")
    except (ValueError, TypeError, UnicodeError):
        result = _failure("MALFORMED_DISCOVERY_JSON")
    try:
        if any(parent.is_symlink() for parent in args.output.parents):
            raise _PlanInputError("OUTPUT_PARENT_SYMLINK", "output")
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    except _PlanInputError as error:
        result = _failure(error.code, error.field)
    except FileExistsError:
        result = _failure("OUTPUT_PATH_EXISTS", "output")
    except OSError:
        result = _failure("OUTPUT_FILE_UNWRITABLE", "output")
    print(json.dumps(_stdout_metadata(result), sort_keys=True))
    return 1 if result["status"] == "FAIL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
