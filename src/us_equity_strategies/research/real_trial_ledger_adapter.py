"""Validate a supplied normalized ledger subset for conditional research diagnostics.

This is an input contract, not a parser for any broker or native store format.
All money and returns are decimal strings. Dates are ISO dates and timestamps
are timezone-aware ISO strings. The caller retains source bytes in ``sources``,
keyed by their lowercase SHA-256. Each attempt has domain, strategy_profile,
input_id, trial_id, run_id, param_version, status,
source_sha256, raw_attempt_source_sha256, and (for successes)
ledger_source_sha256, a ledger and ledger_sha256. SHA-256 identifiers refer to
the supplied source bytes. source_sha256 is canonical JSON containing exactly
the attempt identity, run, parameter version, status and raw-attempt digest. A fill's
fee_source_sha256 is canonical JSON containing trial_id, run_id, fill_id,
timestamp, session_date, currency, fee, fee_kind and raw-fee digest. raw_attempt_source_sha256,
ledger_source_sha256 and raw_fee_source_sha256 identify original supplied bytes;
the conversion from those bytes to normalized records is caller-attested.
Canonical JSON means UTF-8, sorted keys, compact separators, no NaN.

A successful ledger has the same trial_id and run_id, currency USD, calendar_id
XNYS, periods_per_year 252, return_unit SIMPLE_NET, cost_basis NET_NAV,
cost_definition, initial_nav, initial_date, days and fills. Each day has date, nav, net_return,
external_flow, fees, gross_pnl and other_costs. Each fill has fill_id,
timestamp, session_date, currency, fee, fee_kind REALIZED, fee_source_sha256,
and raw_fee_source_sha256. Each daily fee is the
sum of its fills' realized fees; an explicit zero fee is required even for a
zero-fee fill. Days without fills require zero fees. Return is NAV / previous
NAV - 1; external flows are unsupported. The previous NAV plus gross P&L less
fees and separately declared other costs must equal net NAV. Gross P&L and other
costs are normalized caller declarations; this arithmetic does not prove their
source attribution. No fee is subtracted again when calculating returns.

Calendar sessions must exactly equal the supplied dated sequence; this validates
alignment, not whether a calendar is authentic or complete. Hashes bind bytes,
not the identity of their origin or the correctness of their normalization.
The declared comparison candidates must share domain, input, currency, calendar,
periods, return unit, cost basis, cost definition, first marked date, and return sessions. Strategy
profiles and parameter versions may differ. The specified candidates and all
provenance receipts form a returned canonical study-spec digest. This digest
binds the supplied specification, not its completeness or authenticity.
No native schema mapping, broker authentication, account access or source
coverage certification is provided. Results are research-only and cannot grant
execution authority. The primary family metrics always remain uncomputable.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import math
import re
from zoneinfo import ZoneInfo

from . import trial_overfitting_report as stats


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError):
        raise ValueError("NORMALIZED_CONTENT_INVALID") from None


def _id(value):
    if type(value) is not str or not value or value != value.strip():
        raise ValueError("IDENTITY_INVALID")
    return value


def _amount(value, *, nonnegative=False):
    if type(value) is not str:
        raise ValueError("DECIMAL_STRING_REQUIRED")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError("DECIMAL_INVALID") from None
    if (not number.is_finite() or len(number.as_tuple().digits) > 24
            or abs(number.adjusted()) > 15 or (nonnegative and number < 0)):
        raise ValueError("DECIMAL_INVALID")
    return number


def _date(value):
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except (TypeError, ValueError):
        raise ValueError("DATE_INVALID") from None


def _source(sources, digest):
    if (type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or digest not in sources or type(sources[digest]) is not bytes
            or _hash(sources[digest]) != digest):
        raise ValueError("SOURCE_DIGEST_MISMATCH")


def _receipt(sources, digest, expected):
    _source(sources, digest)
    if sources[digest] != _canonical(expected):
        raise ValueError("NORMALIZED_RECEIPT_MISMATCH")


def report_normalized_real_ledgers(*, attempts, sources, sessions, selected_trial_id,
                                   selection_candidate_ids, annual_risk_free_rate,
                                   effective_trials=None, pbo_splits=16):
    """Validate explicit subset and optionally compute a conditional subset PBO.

    ``attempts`` includes observed unsuccessful attempts; no records or family
    completeness are inferred. Duplicate IDs and run IDs fail closed. Only
    selected successful attempts are candidates. The returned subset diagnostic
    never upgrades the primary family PBO or estimates effective trial count.
    """
    if type(sources) is not dict or type(attempts) is not list or not attempts:
        raise ValueError("INPUT_INVALID")
    if type(sessions) is not list or not sessions:
        raise ValueError("CALENDAR_INVALID")
    calendar = [_date(x) for x in sessions]
    if any(b <= a for a, b in zip(calendar, calendar[1:])):
        raise ValueError("CALENDAR_INVALID")
    if (type(annual_risk_free_rate) not in (int, float)
            or not math.isfinite(annual_risk_free_rate) or abs(annual_risk_free_rate) > 1e6):
        raise ValueError("RESEARCH_RATE_INVALID")
    if type(pbo_splits) is not int or pbo_splits < 2:
        raise ValueError("PBO_SPLITS_INVALID")
    ids, runs, dated, statuses = set(), set(), {}, {}
    receipts, comparison = {}, {}
    for attempt in attempts:
        if type(attempt) is not dict:
            raise ValueError("ATTEMPT_INVALID")
        tid = _id(attempt.get("trial_id"))
        if tid in ids:
            raise ValueError("DUPLICATE_TRIAL_ID")
        ids.add(tid)
        status = attempt.get("status")
        if type(status) is not str or status not in {"succeeded", "failed", "rejected", "aborted", "started"}:
            raise ValueError("STATUS_INVALID")
        statuses[tid] = status
        for key in ("domain", "strategy_profile", "input_id"):
            _id(attempt.get(key))
        run = attempt.get("run_id")
        if run is not None:
            _id(run)
            if run in runs:
                raise ValueError("DUPLICATE_RUN_ID")
            runs.add(run)
        attempt_record = {key: attempt.get(key) for key in
                          ("domain", "strategy_profile", "input_id", "trial_id", "run_id",
                           "param_version", "status", "raw_attempt_source_sha256")}
        if attempt_record["param_version"] is not None and (
                type(attempt_record["param_version"]) is not int or attempt_record["param_version"] <= 0):
            raise ValueError("PARAM_VERSION_INVALID")
        _receipt(sources, attempt.get("source_sha256"), attempt_record)
        _source(sources, attempt.get("raw_attempt_source_sha256"))
        receipts[tid] = {"attempt": attempt_record, "attempt_receipt_sha256": attempt["source_sha256"],
                         "raw_attempt_source_sha256": attempt["raw_attempt_source_sha256"],
                         "ledger_source_sha256": None, "ledger_sha256": None, "fees": []}
        if status != "succeeded":
            if any(attempt.get(k) is not None for k in
                   ("ledger", "ledger_sha256", "ledger_source_sha256")):
                raise ValueError("NON_SUCCESS_RESULT_INVALID")
            continue
        _id(run)
        _source(sources, attempt.get("ledger_source_sha256"))
        ledger = attempt.get("ledger")
        if type(ledger) is not dict or _hash(_canonical(ledger)) != attempt.get("ledger_sha256"):
            raise ValueError("LEDGER_DIGEST_MISMATCH")
        if type(attempt.get("param_version")) is not int or attempt["param_version"] <= 0:
            raise ValueError("PARAM_VERSION_INVALID")
        if any(ledger.get(key) != attempt.get(key) for key in
               ("trial_id", "run_id", "domain", "strategy_profile", "input_id", "param_version")):
            raise ValueError("LEDGER_IDENTITY_MISMATCH")
        if (ledger.get("currency") != "USD" or ledger.get("calendar_id") != "XNYS"
                or type(ledger.get("periods_per_year")) is not int or ledger["periods_per_year"] != 252
                or ledger.get("return_unit") != "SIMPLE_NET" or ledger.get("cost_basis") != "NET_NAV"):
            raise ValueError("LEDGER_BASIS_INVALID")
        _id(ledger.get("cost_definition"))
        days, fills = ledger.get("days"), ledger.get("fills")
        if type(days) is not list or type(fills) is not list or len(days) != len(calendar) or len(fills) > 10000:
            raise ValueError("LEDGER_CALENDAR_MISMATCH")
        initial_date = _date(ledger.get("initial_date"))
        if initial_date >= calendar[0]:
            raise ValueError("LEDGER_CHRONOLOGY_INVALID")
        comparison[tid] = (attempt["domain"], attempt["input_id"], ledger["currency"],
                           ledger["calendar_id"], ledger["periods_per_year"],
                           ledger["return_unit"], ledger["cost_basis"], ledger["cost_definition"], initial_date)
        receipts[tid]["ledger_source_sha256"] = attempt["ledger_source_sha256"]
        receipts[tid]["ledger_sha256"] = attempt["ledger_sha256"]
        previous = _amount(ledger.get("initial_nav"))
        if previous <= 0:
            raise ValueError("NAV_INVALID")
        daily_fees = {day: Decimal(0) for day in calendar}
        fill_ids = set()
        for fill in fills:
            if type(fill) is not dict:
                raise ValueError("FILL_INVALID")
            fid = _id(fill.get("fill_id"))
            if fid in fill_ids:
                raise ValueError("DUPLICATE_FILL_ID")
            fill_ids.add(fid)
            day = _date(fill.get("session_date"))
            try:
                stamp = datetime.fromisoformat(fill.get("timestamp", "").replace("Z", "+00:00"))
                if stamp.utcoffset() is None or stamp.astimezone(ZoneInfo("America/New_York")).date() != day:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError("FILL_TIME_INVALID") from None
            if day not in daily_fees or fill.get("currency") != "USD" or fill.get("fee_kind") != "REALIZED":
                raise ValueError("FEE_EVIDENCE_INVALID")
            fee_record = {"trial_id": tid, "run_id": run, **{key: fill.get(key) for key in
                          ("fill_id", "timestamp", "session_date", "currency", "fee", "fee_kind",
                           "raw_fee_source_sha256")}}
            _receipt(sources, fill.get("fee_source_sha256"), fee_record)
            _source(sources, fill.get("raw_fee_source_sha256"))
            receipts[tid]["fees"].append({"fill_id": fid, "receipt_sha256": fill["fee_source_sha256"],
                                          "raw_source_sha256": fill["raw_fee_source_sha256"]})
            with localcontext() as context:
                context.prec = 80
                daily_fees[day] += _amount(fill.get("fee"), nonnegative=True)
        rows = []
        for day, item in zip(calendar, days):
            if type(item) is not dict or _date(item.get("date")) != day:
                raise ValueError("LEDGER_CALENDAR_MISMATCH")
            if _amount(item.get("external_flow")) != 0:
                raise ValueError("EXTERNAL_FLOWS_UNSUPPORTED")
            if _amount(item.get("fees"), nonnegative=True) != daily_fees[day]:
                raise ValueError("FEE_TOTAL_MISMATCH")
            nav = _amount(item.get("nav"))
            if nav <= 0:
                raise ValueError("NAV_INVALID")
            gross_pnl = _amount(item.get("gross_pnl"))
            other_costs = _amount(item.get("other_costs"), nonnegative=True)
            with localcontext() as context:
                context.prec = 80
                if previous + gross_pnl - daily_fees[day] - other_costs != nav:
                    raise ValueError("NET_NAV_RECONCILIATION_FAILED")
                actual = _amount(item.get("net_return"))
                # Decimal returns may be rounded; the declared precision is bounded.
                if abs(actual - (nav / previous - 1)) > Decimal("0.000000000001"):
                    raise ValueError("NET_RETURN_MISMATCH")
            rows.append((day, float(actual)))
            previous = nav
        dated[tid] = rows
    if selected_trial_id not in dated:
        raise ValueError("SELECTED_TRIAL_NOT_SUCCEEDED")
    if (type(selection_candidate_ids) is not list or not selection_candidate_ids
            or any(type(t) is not str or t not in dated for t in selection_candidate_ids)
            or len(set(selection_candidate_ids)) != len(selection_candidate_ids)
            or selected_trial_id not in selection_candidate_ids):
        raise ValueError("SELECTION_CANDIDATES_INVALID")
    if len({comparison[tid] for tid in selection_candidate_ids}) != 1:
        raise ValueError("COMPARISON_STUDY_MISMATCH")
    if effective_trials is not None:
        if (type(effective_trials) is not dict or type(effective_trials.get("count")) is not int
                or not 1 <= effective_trials["count"] <= len(selection_candidate_ids)):
            raise ValueError("EFFECTIVE_TRIALS_INVALID")
        _id(effective_trials.get("method"))
        _source(sources, effective_trials.get("source_sha256"))
    spec = {"sessions": sessions, "selected_trial_id": selected_trial_id,
            "selection_candidate_ids": selection_candidate_ids, "annual_risk_free_rate": annual_risk_free_rate,
            "pbo_splits": pbo_splits, "effective_trials": effective_trials,
            "attempt_receipts": receipts}
    spec_bytes = _canonical(spec)
    spec_digest = _hash(spec_bytes)
    spec = json.loads(spec_bytes)
    rf = annual_risk_free_rate / 252
    values = {tid: [value - rf for _, value in dated[tid]] for tid in selection_candidate_ids}
    fn = stats._qpk_function("probability_of_backtest_overfitting")
    conditional = stats._unavailable()
    if fn is not None:
        conditional = stats._from_stat(fn(values, n_splits=pbo_splits, metric="sharpe"))
    conditional_dsr = stats._metric("UNCOMPUTABLE", None, "EFFECTIVE_TRIALS_NOT_SUPPLIED")
    if effective_trials is not None:
        dsr_fn = stats._qpk_function("deflated_sharpe_ratio")
        sharpes = [stats._per_session_sharpe(rows) for rows in values.values()]
        if dsr_fn is None:
            conditional_dsr = stats._unavailable()
        elif any(sharpe is None for sharpe in sharpes):
            conditional_dsr = stats._metric("UNCOMPUTABLE", None, "TRIAL_SHARPE_UNDEFINED")
        else:
            conditional_dsr = stats._from_stat(dsr_fn(
                values[selected_trial_id], sharpes, n_effective_trials=effective_trials["count"]))
    return {
        "research_only": True, "promotion_eligible": False, "no_order": True,
        "live_ready": False, "size_zero_required": True,
        "source_authentication": "NOT_VERIFIED", "normalization": "CALLER_ATTESTED",
        "calendar_authenticity": "NOT_VERIFIED", "net_cost_inclusion": "NORMALIZED_ARITHMETIC_RECONCILED",
        "study_spec": spec, "study_spec_sha256": spec_digest,
        "provenance": receipts,
        "normalization_provenance": "NORMALIZED_RECEIPTS_VERIFIED_RAW_MAPPING_NOT_VERIFIED",
        "family_coverage": "NOT_VERIFIED", "effective_trial_count": None,
        "trial_statuses": statuses, "selected_trial_id": selected_trial_id,
        "metrics": {"dsr": None, "pbo": None},
        "metric_status": {"dsr": "UNCOMPUTABLE", "pbo": "UNCOMPUTABLE"},
        "metric_reason": {"dsr": "COMPLETE_FAMILY_AND_EFFECTIVE_COUNT_NOT_VERIFIED",
                          "pbo": "COMPLETE_FAMILY_NOT_VERIFIED"},
        "conditional_candidate_set_pbo": conditional,
        "conditional_candidate_set_dsr": conditional_dsr,
        "effective_trials_assumption": deepcopy(spec["effective_trials"]),
        "selection_candidate_ids": list(spec["selection_candidate_ids"]),
        "conditional_scope": "SUPPLIED_SUCCEEDED_SUBSET_ONLY_NOT_FAMILY_PBO",
    }
