"""Inert NEW research package: offline engineering, never data execution.

Supplied declarations are not authenticated rights, PIT, calendar or producer
receipts. No function in this revision downloads, compares market inputs,
registers a future clock, or enables a strategy. See the package contract.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import math
import os
import re
import sys
from copy import deepcopy
from datetime import date, datetime, timezone, timedelta
from pathlib import Path


PACKAGE_ID = "ues-new-evidence-v1"
SYMBOLS = ("QQQ", "TQQQ", "QQQM", "BOXX", "SOXL", "SOXX")
RESOURCE_CEILINGS = {
    "initial_http_requests": 2000, "session_http_requests": 128,
    "total_http_requests": 40000, "transport_attempts_per_object": 3,
    "download_bytes": 2 * 1024**3, "retained_bytes": 10 * 1024**3,
    "runner_cpu_seconds": 24 * 3600, "incremental_spend_usd": 0,
    "collection_expiry_days": 400,
}
UNKNOWN_REASONS = (
    "PROVIDER_ENTITLEMENT_UNKNOWN", "SOURCE_IDENTITY_UNKNOWN", "ZERO_COST_UNPROVEN",
    "RUNNER_IDENTITY_UNKNOWN", "STORE_IDENTITY_UNKNOWN", "RETENTION_UNPROVEN",
    "PRODUCER_COMPATIBILITY_UNKNOWN", "RUNTIME_IDENTITY_UNKNOWN",
    "EFFECTIVE_DEFAULTS_UNRESOLVED", "CALENDAR_FINALITY_UNKNOWN",
    "FORWARD_ACCESS_CONTROLS_UNKNOWN", "LATER_EXECUTION_GRANT_ABSENT",
)
_T_DELTA = ("market_regime_control_enabled", "dual_drive_macro_risk_governor_enabled",
            "dual_drive_crisis_defense_enabled")
_S_DELTA = ("market_regime_control_enabled", "blend_gate_volatility_delever_retention_mode",
            "blend_gate_volatility_delever_retention_ratio",
            "blend_gate_volatility_delever_retention_context_required",
            "blend_gate_volatility_delever_max_retention_ratio")


class NewEvidenceError(ValueError):
    """Fixed public reason code only; never echo supplied identifiers or data."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str):
    if not condition:
        raise NewEvidenceError(code)


def canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, OverflowError):
        raise NewEvidenceError("CANONICALIZATION_INVALID") from None


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _number(v: object) -> bool:
    return type(v) in (int, float) and math.isfinite(v)


def _day(v: object, code: str) -> date:
    try:
        _require(type(v) is str and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) is not None, code)
        return date.fromisoformat(v)
    except (ValueError, TypeError):
        raise NewEvidenceError(code) from None


def _utc(v: object, code: str) -> datetime:
    try:
        _require(type(v) is str and re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", v) is not None, code)
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise NewEvidenceError(code) from None


def _hash(v: object) -> bool:
    return type(v) is str and re.fullmatch(r"[0-9a-f]{64}", v) is not None


def package_definition() -> dict:
    """Fresh declared configs, never an executable freeze or old parameters.

    Manifest defaults are expanded here; strategy/helper defaults and genuine
    runtime/producer compatibility are still explicitly unresolved. Do not
    treat a digest of these declared recipes as a fully materialized freeze.
    """
    from us_equity_strategies.manifests import (
        soxl_soxx_trend_income_manifest, tqqq_growth_income_manifest,
    )

    common = {"income_layer_enabled": False, "option_overlay_enabled": False,
              "option_growth_overlay_enabled": False, "option_income_overlay_enabled": False,
              "ai_extensions": {"enabled": False}, "signal_effective_after_trading_days": 1}
    t = {**deepcopy(tqqq_growth_income_manifest.default_config), **deepcopy(common),
         "managed_symbols": ["TQQQ", "QQQM", "BOXX"],
         "dual_drive_volatility_delever_retention_mode": "none",
         "dual_drive_volatility_delever_retention_ratio": 0.,
         "dual_drive_volatility_delever_taco_veto_enabled": False,
         **{key: True for key in _T_DELTA}}
    s = {**deepcopy(soxl_soxx_trend_income_manifest.default_config), **deepcopy(common),
         "managed_symbols": ["SOXL", "SOXX", "BOXX"]}
    # SOXL has no AI-extension keyword; its ETF-only caller accepts no overlay.
    s.pop("ai_extensions", None)
    tc = {**deepcopy(t), **{key: False for key in _T_DELTA}}
    sc = {**deepcopy(s), "market_regime_control_enabled": False,
          "blend_gate_volatility_delever_retention_mode": "none",
          "blend_gate_volatility_delever_retention_ratio": 0.,
          "blend_gate_volatility_delever_retention_context_required": False,
          "blend_gate_volatility_delever_max_retention_ratio": 0.}
    # Ratio is zero in both arms because environment context supplies treatment
    # retention. The allowlist contains only actual changed fields.
    sdelta = [key for key in _S_DELTA if s[key] != sc[key]]
    candidates = [
        {"candidate_id": "tqqq-current-guard-new-v1", "treatment": t, "control": tc,
         "allowed_differences": list(_T_DELTA),
         "caller": "build_tqqq_core_only_p2_benchmark_guard_research_decision",
         "control_caller": "build_tqqq_core_only_p2_v2_research_decision",
         "builder": "_build_tqqq_growth_income_decision",
         "guard_policy": {"window": 20, "soft_drawdown": -.05, "hard_drawdown": -.10,
                          "soft_scalar": .5, "hard_scalar": 0., "max_age_days": 3}},
        {"candidate_id": "soxl-current-regime-retention-new-v1", "treatment": s, "control": sc,
         "allowed_differences": sdelta,
         "caller": "_build_soxl_soxx_trend_income_decision",
         "control_caller": "_build_soxl_soxx_trend_income_decision",
         "builder": "build_rebalance_plan", "producer": None},
    ]
    return {
        "source_binding": {
            "baseline_commit": "a93fea4ae37de22938f4ecc398e54460f276f85a",
            "baseline_tree": "5f709193fd629fda4b2c4b9297c8e35dceaee5bd",
            "declared_qpk_pin": "52adab68d6109cf93941ae21a39914cfa37e00e3",
            "declared_qsp_pin": "88483c8f786d3fc3bed865fe7a420996fb487c79",
            "reviewed_implementation": None, "runtime_attestation": None,
        },
        "schema_version": "ues-new-package.v1", "package_id": PACKAGE_ID,
        "evidence_origin": "NEW", "symbols": list(SYMBOLS), "candidates": candidates,
        "logical_paths": [{"candidate_id": c["candidate_id"], "arm": arm, "cost_bps": cost}
                          for c in candidates for arm in ("treatment", "control") for cost in (5, 10, 15)],
        "materialization_status": "BLOCKED", "unresolved": list(UNKNOWN_REASONS),
        "execution_authorized": False, "promotion_eligible": False,
        "historical_receipts_redeemed": False,
        "legacy": {"items": {key: "INSUFFICIENT_EVIDENCE" for key in
                              ("T1", "T2", "T3", "T4", "S1", "S2", "S3", "S4")},
                   "admissible_fills": "0/8", "actual_params": None,
                   "unknown_params_reason": "ACTUAL_PARAMS_UNKNOWN"},
        "resource_ceilings": dict(RESOURCE_CEILINGS),
        "protocol": {
            "historical_start": "2022-12-28", "warmup_start": "2021-01-01", "cutoff": None,
            "warmup_symbols": ["QQQ", "SOXX", "SOXL"], "warmup_lookbacks_verified": False,
            "reference_folds": [
                ["2022-12-28", "2023-06-30", "2023-07-03", "2023-12-29"],
                ["2024-01-02", "2024-06-28", "2024-07-01", "2024-12-31"],
                ["2025-01-02", "2025-02-28", "2025-03-03", "2025-07-31"]],
            "purge_sessions": 1, "embargo_days": 1, "trailing_sessions": [252, 756],
            "calendar": "XNYS", "initial_cash_per_book_usd": 100000,
            "initial_positions": {}, "external_flows": 0, "cash_yield": 0, "borrowing": False,
            "fractional_quantities": True, "signal": "completed_session_after_finality",
            "fill": "next_verified_completed_session_close", "basis": "raw_plus_actions_once",
            "cost": "all_in_bps_once_per_one_way_traded_notional_including_initial_buy",
            "forced_terminal_liquidation": False, "price_tolerance_usd": .01,
            "price_tolerance_relative": .0001, "qqq_signal_basis": "split_price_only",
            "forward_sessions": 252, "soxl_forward_calendar_months": 12,
            "forward_started_at": None, "historical_forward_credit": 0,
            "retention_months_from_registration": 18, "retention_days_after_evaluation": 90,
            "financial_criteria_status": "PROPOSED_FROZEN_BEFORE_FUTURE_EXECUTION",
            "financial_criteria": {"paired_calmar_improvement": "positive_at_all_costs",
                "mdd_magnitude": "no_larger_at_all_costs", "growth_difference": "nonnegative_at_all_costs",
                "paired_growth_lower_bound": .975, "bootstrap": "paired_stationary_block",
                "expected_block_sessions": 20, "resamples": 10000,
                "seed": "derived_once_from_pre_outcome_protocol_hash",
                "resample": "whole_same_session_six_path_vectors",
                "zero_mdd_or_undefined": "INSUFFICIENT_EVIDENCE",
                "no_observed_treatment_effect": "INSUFFICIENT_EVIDENCE"},
            "dsr": None, "pbo": None,
        },
    }


def preflight() -> dict:
    """No I/O, credentials, runtime claims, paid calls, or wait-clock mutation."""
    return {"schema_version": "ues-new-preflight.v1", "package_id": PACKAGE_ID,
            "status": "PREFLIGHT_BLOCKED", "reason_codes": list(UNKNOWN_REASONS),
            "execution_authorized": False, "financial_status": "NOT_RUN",
            "forward_started_at": None, "owner_needed": []}


def validate_resources(planned: object) -> None:
    _require(type(planned) is dict and set(planned) == set(RESOURCE_CEILINGS), "RESOURCE_CEILING")
    for key, limit in RESOURCE_CEILINGS.items():
        v = planned[key]
        _require(_number(v) and 0 <= v <= limit, "RESOURCE_CEILING")
        if key != "incremental_spend_usd":
            _require(type(v) is int, "RESOURCE_CEILING")
    # Bounds are requested maxima, never evidence of entitlement/capacity.


def validate_assurance(bundle: object, *, use: str = "economic_book") -> dict:
    """Offline schema/consistency only; supplied coverage is not provider truth.

    In this revision corporate-action events cannot be replayed; every nonempty
    event set remains blocked rather than silently ignored/double-counted.
    """
    _require(type(bundle) is dict, "PROVENANCE_INVALID")
    b = bundle
    _require(b.get("schema_version") == "ues-new-assurance.v1"
             and b.get("evidence_origin") == "NEW", "PROVENANCE_INVALID")
    _require(b.get("evidence_class") == "retrospective", "PIT_UNPROVEN")
    _require(b.get("calendar_id") == "XNYS", "CALENDAR_UNKNOWN")
    _require(type(b.get("expected_sessions")) is list and len(b["expected_sessions"]) >= 2,
             "SESSION_GAP")
    days = [_day(x, "SESSION_GAP") for x in b["expected_sessions"]]
    _require(days == sorted(set(days)) and b.get("sessions") == b["expected_sessions"], "SESSION_GAP")
    _require(b.get("symbols") == list(SYMBOLS), "SYMBOL_SCOPE")
    _require(b.get("basis") == "raw_plus_actions_once", "BASIS_MISMATCH")
    _require(use in ("economic_book", "qqq_split_signal", "qqq_total_return"), "USE_INVALID")
    if use == "qqq_total_return":
        _require(b.get("qqq_distributions_qualified") is True, "QQQ_DISTRIBUTIONS_UNPROVEN")
    rows = b.get("rows")
    _require(type(rows) is list, "SESSION_SYMBOL_GAP")
    seen = set()
    for row in rows:
        _require(type(row) is dict, "BAR_INVALID")
        key = (row.get("symbol"), row.get("session"))
        _require(type(key[0]) is str and type(key[1]) is str, "BAR_INVALID")
        _require(key not in seen, "SESSION_SYMBOL_GAP"); seen.add(key)
        vals = [row.get(k) for k in ("open", "high", "low", "close", "volume", "verifier_close")]
        _require(all(_number(x) for x in vals), "BAR_INVALID")
        op, hi, lo, cl, vol, verifier = vals
        _require(0 < lo <= min(op, cl) <= max(op, cl) <= hi and vol >= 0 and verifier > 0, "BAR_INVALID")
        for field in ("open", "high", "low", "close"):
            other = row.get("verifier_" + field)
            _require(_number(other) and other > 0, "SOURCE_DISAGREEMENT")
            _require(abs(other - row[field]) <= max(.01, .0001 * row[field]) + 1e-12,
                     "SOURCE_DISAGREEMENT")
        available = _utc(row.get("available_at"), "INPUT_LATE")
        decision = _utc(row.get("decision_at"), "INPUT_LATE")
        _require(available <= decision and available.date() >= _day(key[1], "BAR_INVALID"), "INPUT_LATE")
    _require(seen == {(s, d) for s in SYMBOLS for d in b["expected_sessions"]}, "SESSION_SYMBOL_GAP")
    _require(b.get("actions") == [], "ACTION_UNVERIFIED")
    coverage = b.get("coverage")
    _require(type(coverage) is list, "ACTION_COVERAGE_UNKNOWN")
    covered = set()
    for row in coverage:
        _require(type(row) is dict, "ACTION_COVERAGE_UNKNOWN")
        s, k = row.get("symbol"), row.get("kind")
        _require(type(s) is str and type(k) is str and (s, k) not in covered, "ACTION_COVERAGE_UNKNOWN")
        covered.add((s, k))
        # A disputed QQQ distribution is unrelated to a price-only signal.
        if s == "QQQ" and k == "distributions" and use == "qqq_split_signal":
            continue
        _require(row.get("start") == b["expected_sessions"][0]
                 and row.get("end") == b["expected_sessions"][-1]
                 and all(row.get(key) is True for key in ("canonical_complete", "verifier_complete",
                         "independent", "pagination_exhausted", "negative_coverage_proven")),
                 "ACTION_COVERAGE_UNKNOWN")
    _require(covered == {(s, k) for s in SYMBOLS for k in ("splits", "distributions")},
             "ACTION_COVERAGE_UNKNOWN")
    return {"status": "OFFLINE_CONSISTENCY_ONLY", "financial_status": "NOT_RUN",
            "provider_truth": "UNKNOWN", "calendar_authenticity": "UNKNOWN",
            "execution_authorized": False}


def validate_forward_receipt(receipt: object) -> dict:
    """Pure fake-receipt checks; does not register, observe, or attest a calendar."""
    _require(type(receipt) is dict, "FORWARD_RECEIPT_INVALID")
    r = receipt
    _require(r.get("historical_sessions_counted") == 0, "FORWARD_HISTORY_FORBIDDEN")
    expected, observed = r.get("expected_sessions"), r.get("observed_sessions")
    _require(type(expected) is list and type(observed) is list, "FORWARD_SESSION_GAP")
    days = [_day(d, "FORWARD_SESSION_GAP") for d in expected]
    _require(len(days) >= 252 and days == sorted(set(days)) and observed == expected, "FORWARD_SESSION_GAP")
    first = _day(r.get("first_session"), "FORWARD_MONTHS")
    last = _day(r.get("last_session"), "FORWARD_MONTHS")
    month_end = first.replace(year=first.year + 1, day=min(first.day, calendar.monthrange(first.year + 1, first.month)[1]))
    _require(last >= month_end, "FORWARD_MONTHS")
    _require(first == days[0] and last == days[-1], "FORWARD_SESSION_GAP")
    first_open = _utc(r.get("first_open_at"), "FORWARD_REGISTRATION_LATE")
    _require(first_open.date() == first, "FORWARD_REGISTRATION_LATE")
    _require(_utc(r.get("registered_at"), "FORWARD_REGISTRATION_LATE") <
             first_open, "FORWARD_REGISTRATION_LATE")
    _require(_hash(r.get("p3_sha256")) and _hash(r.get("frozen_sha256"))
             and r.get("frozen_sha256") == r.get("observed_sha256"), "FORWARD_IDENTITY_DRIFT")
    _require(r.get("outcome_access_recorded") is True and r.get("unsealed_early") is False,
             "FORWARD_ACCESS_UNPROVEN")
    _require(r.get("pending_fill") is False, "FORWARD_PENDING_FILL")
    expiry = _day(r.get("hard_expiry"), "FORWARD_EXPIRY")
    _require(last <= expiry <= first + timedelta(days=400), "FORWARD_EXPIRY")
    return {"status": "OFFLINE_CONSISTENCY_ONLY", "calendar_authenticity": "UNKNOWN",
            "forward_status": "NOT_RUN", "execution_authorized": False}


def source_identity() -> dict:
    """Non-private local code hashes, not an installed-runtime attestation."""
    root = Path(__file__).resolve().parents[1]
    return {"caller_sha256": hashlib.sha256((root / "entrypoints/__init__.py").read_bytes()).hexdigest(),
            "manifest_sha256": hashlib.sha256((root / "manifests/__init__.py").read_bytes()).hexdigest(),
            "tqqq_builder_sha256": hashlib.sha256((root / "strategies/tqqq_growth_income.py").read_bytes()).hexdigest(),
            "soxl_builder_sha256": hashlib.sha256((root / "strategies/soxl_soxx_trend_income.py").read_bytes()).hexdigest()}


def validate_observed_admission(manifest: object) -> None:
    """Validate supplied declarations; unresolved real producer ALWAYS blocks.

    There is intentionally no execution token, fixture override, receipt bool,
    or callable injection that can enable data execution in this revision.
    """
    _require(type(manifest) is dict and type(manifest.get("identity")) is dict, "IDENTITY_UNKNOWN")
    m = manifest
    _require(m.get("evidence_origin") == "NEW" and m.get("package_id") == PACKAGE_ID,
             "PROVENANCE_INVALID")
    _require(m.get("execution_authorized") is False and m.get("promotion_eligible") is False
             and m.get("historical_receipts_redeemed") is False, "AUTHORITY_INVALID")
    for key, digest in source_identity().items():
        _require(m["identity"].get(key) == digest, "CALLER_IDENTITY_MISMATCH")
    _require(m.get("logical_paths") == package_definition()["logical_paths"], "PATH_IDENTITY_MISMATCH")
    _require(m.get("definition_sha256") == _digest(package_definition()), "PARAMETER_IDENTITY_MISMATCH")
    validate_resources(m.get("resources"))
    p = m.get("provenance")
    _require(type(p) is dict and all(p.get(k) is True for k in
             ("source_bound", "rights_bound", "zero_incremental_cost_proven", "runner_bound", "store_bound")),
             "PROVENANCE_UNKNOWN")
    retention = m.get("retention")
    _require(type(retention) is dict and retention.get("evaluator_readback") is True
             and retention.get("immutable") is True and type(retention.get("months_from_registration")) is int
             and retention["months_from_registration"] >= 18
             and type(retention.get("days_after_evaluation")) is int
             and retention["days_after_evaluation"] >= 90, "RETENTION_UNPROVEN")
    validate_assurance(m.get("assurance"))
    # A syntactically valid producer digest supplied by a caller is not a
    # qualified pinned causal QSP producer. No producer has been bound here.
    raise NewEvidenceError("PRODUCER_COMPATIBILITY_UNKNOWN")


def persist_fake_attempt(root: Path, attempt_id: str) -> Path:
    """Fake-only local mechanism record; atomic exclusive claim, never resume.

    The created directory survives crashes as an occupied incomplete attempt.
    No caller payload, market input, private resource reference or outcome is
    accepted or written. This is not a production research archive adapter.
    """
    _require(type(attempt_id) is str and re.fullmatch(r"fake-[a-z0-9-]{1,64}", attempt_id) is not None,
             "FAKE_ATTEMPT_REQUIRED")
    folder = root / attempt_id
    try:
        folder.mkdir(mode=0o700)
    except FileExistsError:
        raise NewEvidenceError("ATTEMPT_EXISTS") from None
    except OSError:
        raise NewEvidenceError("ATTEMPT_WRITE_FAILED") from None
    terminal = {"schema_version": "ues-new-fake-terminal.v1", "attempt_id": attempt_id,
                "status": "OFFLINE_MECHANISM_ONLY", "financial_status": "NOT_RUN",
                "execution_authorized": False}
    content = canonical_bytes(terminal)
    try:
        for name, data in (("terminal.json", content), ("index.json", canonical_bytes({
                "terminal_sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}))):
            with (folder / name).open("xb") as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
        directory_fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory_fd)
        finally: os.close(directory_fd)
        read_fake_attempt(folder)
    except OSError:
        raise NewEvidenceError("ATTEMPT_WRITE_FAILED") from None
    return folder


def read_fake_attempt(folder: Path) -> dict:
    try:
        _require(not folder.is_symlink() and all(not (folder / n).is_symlink()
                 for n in ("terminal.json", "index.json")), "RETENTION_UNPROVEN")
        raw = (folder / "terminal.json").read_bytes()
        index = json.loads((folder / "index.json").read_bytes())
        _require(type(index) is dict and index.get("terminal_sha256") == hashlib.sha256(raw).hexdigest()
                 and index.get("size") == len(raw), "RETENTION_UNPROVEN")
        result = json.loads(raw)
        _require(result == {"schema_version": "ues-new-fake-terminal.v1", "attempt_id": folder.name,
                 "status": "OFFLINE_MECHANISM_ONLY", "financial_status": "NOT_RUN",
                 "execution_authorized": False}, "RETENTION_UNPROVEN")
        return result
    except (OSError, ValueError, TypeError):
        raise NewEvidenceError("RETENTION_UNPROVEN") from None


def main(argv: list[str] | None = None) -> int:
    # Do not use argparse's raw-argument error echo for potentially private text.
    args = sys.argv[1:] if argv is None else argv
    if args == ["preflight"]:
        result = preflight()
    else:
        stages = {"freeze", "collect", "compare", "verify-retention", "register-forward", "observe-session", "finalize"}
        code = "DATA_EXECUTION_NOT_AUTHORIZED" if len(args) == 1 and args[0] in stages else "COMMAND_INVALID"
        result = {"status": "BLOCKED", "reason_codes": [code], "execution_authorized": False}
    print(canonical_bytes(result).decode())
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
