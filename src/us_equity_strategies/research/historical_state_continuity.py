"""Offline conformance of explicitly observed TQQQ state transitions.

This candidate sidecar is separate from the historical runtime bundle. It does
not capture production state, replay market calculations, authenticate sources,
or grant execution authority. SOXL has no supported state adapter here.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import re
from typing import Any

from .historical_runtime_bundle import canonical_sha256

SCHEMA = "qsl.historical_state_continuity.v1"
_PROFILE = "tqqq_growth_income"
_HASH = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_SCOPED = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*")
_DOMAIN_FIELDS = {"platform", "execution_environment", "server", "lifecycle_id", "account_alias"}
_IDENTITY_FIELDS = {"strategy_profile", "account_scope", "candidate_id", "config_sha256"}
_STATE_FIELDS = {"schema_version", "session", "alpha_trend_active", "alpha_epoch",
                 "volatility_latch_active", "pending_intent", "intent_invalidation_reason"}
_TRANSITION_FIELDS = {"schema_version", "identity", "effective_session", "input_sha256",
                      "previous_transition_sha256", "state", "transition_sha256"}
_UNVERIFIED = (
    "SOURCE_AUTHENTICATION_UNVERIFIED", "DECLARED_RUN_INDEX_COMPLETENESS_UNVERIFIED",
    "ACCOUNT_DOMAIN_BINDING_UNVERIFIED", "DAILY_INPUT_PROVENANCE_UNVERIFIED",
    "STATE_STORAGE_AUTHENTICITY_UNVERIFIED", "STATE_RELEASE_BINDING_UNVERIFIED",
    "MARKET_STATE_RULE_REPLAY_NOT_PERFORMED",
)


class _Invalid(ValueError):
    def __init__(self, code: str, status: str = "FAIL"):
        super().__init__(code)
        self.code, self.status = code, status


def _need(value, key, code="REQUIRED_OBSERVATION_MISSING"):
    if type(value) is not dict or key not in value or value[key] is None:
        raise _Invalid(code, "BLOCKED")
    return value[key]


def _object(value):
    if type(value) is not dict:
        raise _Invalid("OBJECT_REQUIRED")
    return value


def _identifier(value):
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise _Invalid("STABLE_IDENTIFIER_REQUIRED")
    return value


def _digest(value):
    if type(value) is not str or _HASH.fullmatch(value) is None:
        raise _Invalid("SHA256_REQUIRED")
    return value


def _day(value):
    if type(value) is not str or date.fromisoformat(value).isoformat() != value:
        raise _Invalid("ISO_SESSION_REQUIRED")
    return value


def _clock(value):
    if type(value) is not str:
        raise _Invalid("AWARE_TIMESTAMP_REQUIRED")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise _Invalid("AWARE_TIMESTAMP_REQUIRED")
    return parsed.astimezone(timezone.utc)


def _domain(value):
    if type(value) is not dict or set(value) != _DOMAIN_FIELDS:
        raise _Invalid("ACCOUNT_DOMAIN_UNSPECIFIED", "BLOCKED")
    if value["execution_environment"] not in {"live", "paper", "dry_run"}:
        raise _Invalid("EXECUTION_ENVIRONMENT_UNSPECIFIED", "BLOCKED")
    for item in value.values():
        _identifier(item)
    if re.fullmatch(r"acct_[0-9a-f]{16,64}", value["account_alias"]) is None:
        raise _Invalid("PSEUDONYMOUS_ACCOUNT_REQUIRED")
    return value


def _release(value, expected=None):
    if type(value) is not dict or set(value) != {"release_id", "manifest_sha256"}:
        raise _Invalid("RELEASE_BINDING_UNOBSERVED", "BLOCKED")
    _identifier(value["release_id"])
    _digest(value["manifest_sha256"])
    if expected is not None:
        if value["release_id"] != expected["release_id"]:
            raise _Invalid("RELEASE_MIGRATION_UNSUPPORTED", "BLOCKED")
        if value != expected:
            raise _Invalid("RELEASE_DIGEST_MISMATCH")
    return value


def _identity(value):
    if type(value) is not dict or set(value) != _IDENTITY_FIELDS:
        raise _Invalid("STATE_IDENTITY_UNOBSERVED", "BLOCKED")
    for key in _IDENTITY_FIELDS - {"config_sha256"}:
        if type(value[key]) is not str or _SCOPED.fullmatch(value[key]) is None:
            raise _Invalid("INVALID_STATE_IDENTITY")
    _digest(value["config_sha256"])
    if value["strategy_profile"] != _PROFILE:
        raise _Invalid("STATE_IDENTITY_PROFILE_MISMATCH")
    return value


def _valid_intent(value, *, session, alpha, latch, epoch):
    return (type(value) is dict and set(value) == {"id", "alpha_epoch", "signal_session"}
            and type(value["id"]) is str and 0 < len(value["id"]) <= 128
            and type(value["alpha_epoch"]) is int and value["alpha_epoch"] == epoch
            and value["signal_session"] == session and alpha and not latch)


def _transition(value, identity):
    value = _object(value)
    if value.get("schema_version") != "strategy_risk_state_transition.v1":
        raise _Invalid("TRANSITION_SCHEMA_ADAPTER_UNAVAILABLE", "BLOCKED")
    if set(value) != _TRANSITION_FIELDS:
        raise _Invalid("INVALID_TRANSITION_FIELDS")
    if canonical_sha256({key: item for key, item in value.items()
                         if key != "transition_sha256"}) != _digest(value["transition_sha256"]):
        raise _Invalid("TRANSITION_DIGEST_MISMATCH")
    if _identity(value["identity"]) != identity:
        raise _Invalid("STATE_IDENTITY_MISMATCH")
    session = _day(value["effective_session"])
    _digest(value["input_sha256"])
    if value["previous_transition_sha256"] is not None:
        _digest(value["previous_transition_sha256"])
    state = _object(value["state"])
    if state.get("schema_version") != "tqqq_daily_state.v1":
        raise _Invalid("STATE_SCHEMA_ADAPTER_UNAVAILABLE", "BLOCKED")
    if set(state) != _STATE_FIELDS:
        raise _Invalid("INVALID_STATE_FIELDS")
    if (state["session"] != session or type(state["alpha_trend_active"]) is not bool
            or type(state["volatility_latch_active"]) is not bool
            or type(state["alpha_epoch"]) is not int or state["alpha_epoch"] < 0
            or (state["alpha_trend_active"] and state["alpha_epoch"] == 0)):
        raise _Invalid("INVALID_STATE_VALUES")
    if state["pending_intent"] is not None and not _valid_intent(
        state["pending_intent"], session=session, alpha=state["alpha_trend_active"],
        latch=state["volatility_latch_active"], epoch=state["alpha_epoch"],
    ):
        raise _Invalid("INVALID_PENDING_INTENT")
    if state["intent_invalidation_reason"] not in (None, "alpha_exit", "new_session", "risk_latched"):
        raise _Invalid("INVALID_INTENT_INVALIDATION")
    return value


def _binding(value, context, process):
    if _domain(_need(value, "account_domain")) != context["account_domain"]:
        raise _Invalid("ACCOUNT_DOMAIN_MISMATCH")
    for field in ("profile", "record_kind"):
        if _need(value, field) != context[field]:
            raise _Invalid("SOURCE_" + field.upper() + "_MISMATCH")
    _release(_need(value, "release"), context["release"])
    if _need(value, "process_instance_id", "PROCESS_INSTANCE_UNOBSERVED") != process:
        raise _Invalid("PROCESS_INSTANCE_MISMATCH")


def _source(value, *, context, process, run_id, evaluation_id, role, seen):
    value = _object(value)
    if canonical_sha256({key: item for key, item in value.items()
                         if key != "sha256"}) != _digest(_need(value, "sha256")):
        raise _Invalid("SOURCE_DIGEST_MISMATCH")
    record_id = _identifier(_need(value, "record_id"))
    if record_id in seen:
        raise _Invalid("DUPLICATE_SOURCE_RECORD")
    seen.add(record_id)
    _binding(value, context, process)
    for key, expected in (("run_id", run_id), ("evaluation_id", evaluation_id), ("role", role)):
        if _need(value, key) != expected:
            raise _Invalid("SOURCE_" + key.upper() + "_MISMATCH")
    available, observed = _clock(_need(value, "available_at")), _clock(_need(value, "observed_at"))
    if available > observed:
        raise _Invalid("OBSERVATION_PRECEDES_AVAILABILITY")
    return _object(_need(value, "payload")), available, observed


def _indexed(records, declared, *, key, noun):
    if type(records) is not list or type(declared) is not list or not declared:
        raise _Invalid(noun + "_INDEX_UNOBSERVED", "BLOCKED")
    identifiers = [_identifier(_need(row, key)) for row in records]
    if len(set(identifiers)) != len(identifiers) or len(set(declared)) != len(declared):
        raise _Invalid("DUPLICATE_" + noun + "_IDENTITY")
    if set(declared) - set(identifiers):
        raise _Invalid("INDEXED_" + noun + "_MISSING", "BLOCKED")
    if set(identifiers) - set(declared):
        raise _Invalid("UNLISTED_" + noun)
    if identifiers != declared:
        raise _Invalid("OUT_OF_ORDER_" + noun)


def _state_step(request, diagnostics, prior, *, session, identity):
    if request.get("status") != "ready":
        raise _Invalid("STATE_INPUT_UNAVAILABLE", "BLOCKED")
    if type(_need(request, "bootstrap")) is not bool:
        raise _Invalid("EXPLICIT_BOOTSTRAP_FLAG_REQUIRED")
    if request["bootstrap"]:
        raise _Invalid("STATE_REBOOTSTRAP_UNSUPPORTED", "BLOCKED")
    if _identity(_need(request, "identity")) != identity:
        raise _Invalid("STATE_IDENTITY_MISMATCH")
    consumed = _transition(_need(request, "previous_transition", "CONSUMED_STATE_UNOBSERVED"), identity)
    if consumed != prior:
        if (consumed["effective_session"] == session
                and consumed["previous_transition_sha256"] == prior["transition_sha256"]
                and diagnostics.get("tqqq_daily_state_status") == "confirmed"):
            raise _Invalid("WITHIN_RUN_STATE_STEP_UNOBSERVED", "BLOCKED")
        raise _Invalid("CONSUMED_HEAD_MISMATCH")
    produced = _transition(_need(diagnostics, "tqqq_daily_state_transition", "STATE_OUTPUT_UNOBSERVED"), identity)
    if produced["effective_session"] != session:
        raise _Invalid("OUTPUT_SESSION_MISMATCH")
    if produced["input_sha256"] != _digest(_need(request, "input_sha256")):
        raise _Invalid("DAILY_INPUT_DIGEST_MISMATCH")
    if prior["effective_session"] > session:
        raise _Invalid("STATE_SESSION_REGRESSION")
    same_session = prior["effective_session"] == session
    expected_status = "confirmed" if same_session else "proposed"
    observed_status = _need(diagnostics, "tqqq_daily_state_status", "STATE_STATUS_UNOBSERVED")
    if observed_status not in {"proposed", "confirmed"}:
        raise _Invalid("STATE_STATUS_UNAVAILABLE", "BLOCKED")
    if observed_status != expected_status:
        raise _Invalid("STATE_STATUS_CONFLICT")
    if same_session:
        if produced != prior:
            raise _Invalid("SAME_SESSION_STATE_CONFLICT")
        return produced
    if _need(request, "expected_previous_session", "PREVIOUS_SESSION_UNOBSERVED") != prior["effective_session"]:
        raise _Invalid("EXPECTED_PREVIOUS_SESSION_MISMATCH")
    if produced["previous_transition_sha256"] != prior["transition_sha256"]:
        raise _Invalid("TRANSITION_PREDECESSOR_MISMATCH")
    old, new = prior["state"], produced["state"]
    epoch = old["alpha_epoch"] + int(new["alpha_trend_active"] and not old["alpha_trend_active"])
    if new["alpha_epoch"] != epoch:
        raise _Invalid("ALPHA_EPOCH_CONFLICT")
    reason = ("alpha_exit" if old["alpha_trend_active"] and not new["alpha_trend_active"]
              else "risk_latched" if new["volatility_latch_active"]
              else "new_session" if old["pending_intent"] else None)
    if new["intent_invalidation_reason"] != reason:
        raise _Invalid("INTENT_INVALIDATION_CONFLICT")
    fresh = request.get("fresh_intent")
    expected_intent = fresh if _valid_intent(fresh, session=session, alpha=new["alpha_trend_active"],
        latch=new["volatility_latch_active"], epoch=epoch) else None
    if new["pending_intent"] != expected_intent:
        raise _Invalid("PENDING_INTENT_INPUT_MISMATCH")
    return produced


def _evaluate(row, *, context, process, run_id, session, prior, prior_time, previous_clock,
              restarted, seen):
    evaluation_id = _identifier(_need(row, "evaluation_id"))
    clock = _clock(_need(row, "decision_at"))
    if previous_clock is not None and clock <= previous_clock:
        raise _Invalid("OUT_OF_ORDER_EVALUATION_CLOCK")
    if _day(session) > clock.date().isoformat():
        raise _Invalid("FUTURE_SESSION")
    sources = {}
    for role in ("head_before", "input", "decision", "head_after"):
        missing = "RESTART_HEAD_UNOBSERVED" if role == "head_before" and restarted else role.upper() + "_UNOBSERVED"
        sources[role] = _source(_need(row, role, missing), context=context, process=process,
            run_id=run_id, evaluation_id=evaluation_id, role=role, seen=seen)
    before, before_available, before_observed = sources["head_before"]
    frozen_input, input_available, input_observed = sources["input"]
    output, output_available, _ = sources["decision"]
    after, after_available, after_observed = sources["head_after"]
    if (prior_time > before_available or before_observed > input_available
            or input_available > clock or input_observed > clock
            or output_available < clock or after_available < output_available):
        raise _Invalid("STATE_OBSERVATION_CLOCK_CONFLICT")
    before = _transition(before, context["identity"])
    if before != prior:
        if (before["effective_session"] == session
                and before["previous_transition_sha256"] == prior["transition_sha256"]):
            # A final head read cannot substitute for its unobserved proposal.
            raise _Invalid("WITHIN_RUN_STATE_STEP_UNOBSERVED", "BLOCKED")
        raise _Invalid("OBSERVED_HEAD_MISMATCH")
    if _need(frozen_input, "session", "INPUT_SESSION_UNOBSERVED") != session:
        raise _Invalid("INPUT_SESSION_MISMATCH")
    state = _need(frozen_input, "state", "STATE_INPUT_UNOBSERVED")
    request = _object(_need(state, "tqqq_daily_state", "STATE_INPUT_UNOBSERVED"))
    diagnostics = _object(_need(output, "diagnostics", "DECISION_DIAGNOSTICS_UNOBSERVED"))
    produced = _state_step(request, diagnostics, prior, session=session, identity=context["identity"])
    if _transition(after, context["identity"]) != produced:
        raise _Invalid("COMMITTED_HEAD_MISMATCH")
    return produced, after_observed, clock


def _check(value, result):
    value = _object(value)
    if value.get("schema_version") != SCHEMA:
        raise _Invalid("SIDECAR_SCHEMA_ADAPTER_UNAVAILABLE", "BLOCKED")
    if value.get("profile") != _PROFILE:
        raise _Invalid("STATE_ADAPTER_UNAVAILABLE", "BLOCKED")
    if value.get("evidence_kind") not in {"synthetic_fixture", "historical"}:
        raise _Invalid("EVIDENCE_KIND_UNSPECIFIED", "BLOCKED")
    context = {"account_domain": _domain(_need(value, "account_domain")), "profile": _PROFILE,
               "record_kind": _need(value, "record_kind"), "release": _release(_need(value, "release")),
               "identity": _identity(_need(value, "identity"))}
    if context["record_kind"] not in {"execution", "reconciliation", "diagnostic"}:
        raise _Invalid("RECORD_KIND_UNSUPPORTED", "BLOCKED")
    sessions = _need(value, "sessions", "SESSION_INDEX_UNOBSERVED")
    if type(sessions) is not list or not sessions:
        raise _Invalid("SESSION_INDEX_UNOBSERVED", "BLOCKED")
    if [_day(day) for day in sessions] != sorted(set(sessions)):
        raise _Invalid("INVALID_SESSION_INDEX")
    index = _need(value, "run_index", "RUN_INDEX_UNOBSERVED")
    if type(index) is not list or not index:
        raise _Invalid("RUN_INDEX_UNOBSERVED", "BLOCKED")
    run_ids = [_identifier(_need(row, "run_id")) for row in index]
    runs = _need(value, "runs", "RUNS_UNOBSERVED")
    _indexed(runs, run_ids, key="run_id", noun="RUN")
    indexed_sessions = [_day(_need(row, "session")) for row in index]
    if set(sessions) - set(indexed_sessions):
        raise _Invalid("SESSION_RUN_UNOBSERVED", "BLOCKED")
    if set(indexed_sessions) - set(sessions) or indexed_sessions != sorted(indexed_sessions):
        raise _Invalid("RUN_SESSION_INDEX_MISMATCH")
    boundary = _need(value, "boundary", "BOUNDARY_HEAD_UNOBSERVED")
    previous_run = _identifier(_need(boundary, "run_id"))
    previous_process = _identifier(_need(boundary, "process_instance_id", "PROCESS_INSTANCE_UNOBSERVED"))
    if previous_run in run_ids:
        raise _Invalid("BOUNDARY_RUN_REUSED")
    seen = set()
    head, _, head_time = _source(_need(boundary, "head", "BOUNDARY_HEAD_UNOBSERVED"),
        context=context, process=previous_process, run_id=previous_run,
        evaluation_id="boundary", role="head_after", seen=seen)
    _transition(head, context["identity"])
    if head["effective_session"] != _day(_need(boundary, "session")) or head["effective_session"] > sessions[0]:
        raise _Invalid("BOUNDARY_SESSION_MISMATCH")
    last_clock = None
    for declared, run in zip(index, runs):
        run_id = declared["run_id"]
        result["current_record"] = run_id
        if _need(declared, "predecessor_run_id", "PREDECESSOR_RUN_UNOBSERVED") != previous_run:
            raise _Invalid("RUN_PREDECESSOR_MISMATCH")
        process = _identifier(_need(run, "process_instance_id", "PROCESS_INSTANCE_UNOBSERVED"))
        _binding(run, context, process)
        evaluation_ids = _need(declared, "evaluation_ids", "EVALUATION_INDEX_UNOBSERVED")
        if type(evaluation_ids) is not list:
            raise _Invalid("EVALUATION_INDEX_UNOBSERVED", "BLOCKED")
        evaluation_ids = [_identifier(item) for item in evaluation_ids]
        evaluations = _need(run, "evaluations", "EVALUATIONS_UNOBSERVED")
        _indexed(evaluations, evaluation_ids, key="evaluation_id", noun="EVALUATION")
        restarted = process != previous_process
        for position, row in enumerate(evaluations):
            result["current_record"] = run_id + ":" + row["evaluation_id"]
            head, head_time, last_clock = _evaluate(row, context=context, process=process,
                run_id=run_id, session=declared["session"], prior=head, prior_time=head_time,
                previous_clock=last_clock, restarted=restarted and position == 0, seen=seen)
            result["evaluations_checked"] += 1
        if last_clock != _clock(_need(declared, "decision_at")):
            raise _Invalid("RUN_DECISION_CLOCK_MISMATCH")
        result["runs_checked"] += 1
        result["restart_boundaries_checked"] += int(restarted)
        previous_run, previous_process = run_id, process


def check_state_continuity(value: Any) -> dict[str, Any]:
    """Compare supplied state records; a local pass is never historical acceptance.

    Missing evidence and unsupported migrations are BLOCKED. Contradictory
    observed digests, bindings or state fields FAIL. The sidecar is not a
    replacement for a frozen bundle or an authenticated production-state export.
    """
    result = {"schema_version": SCHEMA + ".result", "status": "BLOCKED",
              "conformance_status": "NOT_EXECUTED", "runs_checked": 0,
              "evaluations_checked": 0, "restart_boundaries_checked": 0,
              "findings": [{"code": code, "status": "BLOCKED"} for code in _UNVERIFIED],
              "historical_parity_established": False, "production_ready": False,
              "execution_authorized": False, "executable": False}
    try:
        _check(value, result)
        result["conformance_status"] = "PASS"
    except _Invalid as exc:
        result["conformance_status"] = exc.status
        result["status"] = "FAIL" if exc.status == "FAIL" else "BLOCKED"
        result["findings"].append({"code": exc.code, "status": exc.status,
                                    "record": result.get("current_record")})
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
        result["status"] = result["conformance_status"] = "FAIL"
        result["findings"].append({"code": "MALFORMED_STATE_RECORD", "status": "FAIL",
                                    "record": result.get("current_record")})
    result.pop("current_record", None)
    return result
