from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from us_equity_strategies.research.historical_state_continuity import check_state_continuity


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=True, allow_nan=False).encode()).hexdigest()


DOMAIN = {"platform": "interactive_brokers", "execution_environment": "paper",
          "server": "synthetic-server", "lifecycle_id": "synthetic-lifecycle",
          "account_alias": "acct_" + "0" * 16}
IDENTITY = {"strategy_profile": "tqqq_growth_income", "account_scope": "synthetic",
            "candidate_id": "candidate", "config_sha256": "a" * 64}
RELEASE = {"release_id": "synthetic-release", "manifest_sha256": "d" * 64}


def state(session, *, alpha=True, epoch=2, latch=True, intent=None, reason="risk_latched"):
    return {"schema_version": "tqqq_daily_state.v1", "session": session,
            "alpha_trend_active": alpha, "alpha_epoch": epoch,
            "volatility_latch_active": latch, "pending_intent": intent,
            "intent_invalidation_reason": reason}


def transition(session, previous=None, *, input_digest="a" * 64, **state_values):
    value = {"schema_version": "strategy_risk_state_transition.v1",
             "identity": copy.deepcopy(IDENTITY), "effective_session": session,
             "input_sha256": input_digest,
             "previous_transition_sha256": previous["transition_sha256"] if previous else None,
             "state": state(session, **state_values)}
    return {**value, "transition_sha256": digest(value)}


def reseal(source):
    source["sha256"] = digest({key: value for key, value in source.items() if key != "sha256"})


def rehash_transition(value):
    value["transition_sha256"] = digest({key: item for key, item in value.items()
                                        if key != "transition_sha256"})


def observation(run_id, evaluation_id, role, clock, payload, process="process-a"):
    value = {"record_id": f"{run_id}:{evaluation_id}:{role}", "run_id": run_id,
             "evaluation_id": evaluation_id, "role": role,
             "account_domain": copy.deepcopy(DOMAIN), "profile": "tqqq_growth_income",
             "record_kind": "execution", "release": copy.deepcopy(RELEASE),
             "process_instance_id": process, "available_at": clock, "observed_at": clock,
             "payload": copy.deepcopy(payload)}
    reseal(value)
    return value


def evaluation(run_id, evaluation_id, session, prior, produced, *, offset=0,
               process="process-a", confirmed=False, intent=None):
    start = datetime.fromisoformat(session).replace(hour=21, tzinfo=timezone.utc)
    def at(seconds):
        return (start + timedelta(seconds=offset + seconds)).isoformat()
    request = {"status": "ready", "identity": copy.deepcopy(IDENTITY),
               "previous_transition": copy.deepcopy(prior), "bootstrap": False,
               "input_sha256": produced["input_sha256"],
               "expected_previous_session": prior["effective_session"],
               "fresh_intent": intent, "execution_fence_confirmed": False}
    payload = {"session": session, "state": {"tqqq_daily_state": request},
               "portfolio": {"positions": [{"symbol": "TQQQ", "quantity": 10}]}}
    output = {"diagnostics": {"tqqq_daily_state_status": "confirmed" if confirmed else "proposed",
              "tqqq_daily_state_transition": copy.deepcopy(produced),
              "tqqq_daily_state_risk_increase_allowed": False}}
    return {"evaluation_id": evaluation_id, "decision_at": at(2),
            "head_before": observation(run_id, evaluation_id, "head_before", at(0), prior, process),
            "input": observation(run_id, evaluation_id, "input", at(1), payload, process),
            "decision": observation(run_id, evaluation_id, "decision", at(3), output, process),
            "head_after": observation(run_id, evaluation_id, "head_after", at(4), produced, process)}


def fixture():
    anchor = transition("2026-10-28")
    first = transition("2026-10-29", anchor, input_digest="b" * 64)
    second = transition("2026-10-30", first, input_digest="c" * 64,
                        alpha=False, reason="alpha_exit")
    first_evaluations = [evaluation("run-1", "proposal", "2026-10-29", anchor, first),
                        evaluation("run-1", "confirmation", "2026-10-29", first, first,
                                   offset=5, confirmed=True)]
    second_evaluations = [evaluation("run-2", "proposal", "2026-10-30", first, second,
                                    process="process-b")]
    runs = [{"run_id": run_id, "account_domain": copy.deepcopy(DOMAIN),
             "profile": "tqqq_growth_income", "record_kind": "execution",
             "release": copy.deepcopy(RELEASE), "process_instance_id": process,
             "evaluations": evaluations}
            for run_id, process, evaluations in (("run-1", "process-a", first_evaluations),
                                                ("run-2", "process-b", second_evaluations))]
    return {"schema_version": "qsl.historical_state_continuity.v1",
            "evidence_kind": "synthetic_fixture", "account_domain": copy.deepcopy(DOMAIN),
            "profile": "tqqq_growth_income", "record_kind": "execution",
            "release": copy.deepcopy(RELEASE), "identity": copy.deepcopy(IDENTITY),
            "sessions": ["2026-10-29", "2026-10-30"],
            "boundary": {"run_id": "run-before-window", "session": "2026-10-28",
                "process_instance_id": "process-a", "head": observation(
                    "run-before-window", "boundary", "head_after",
                    "2026-10-28T21:00:00+00:00", anchor)},
            "run_index": [{"run_id": "run-1", "predecessor_run_id": "run-before-window",
                "session": "2026-10-29", "decision_at": first_evaluations[-1]["decision_at"],
                "evaluation_ids": ["proposal", "confirmation"]},
                {"run_id": "run-2", "predecessor_run_id": "run-1", "session": "2026-10-30",
                 "decision_at": second_evaluations[-1]["decision_at"], "evaluation_ids": ["proposal"]}],
            "runs": runs}


def codes(result):
    return {row["code"] for row in result["findings"]}


def first_evaluation(value):
    return value["runs"][0]["evaluations"][0]


def request(value):
    return first_evaluation(value)["input"]["payload"]["state"]["tqqq_daily_state"]


def assert_denied(result):
    assert result["status"] != "PASS"
    assert result["historical_parity_established"] is False
    assert result["production_ready"] is False
    assert result["execution_authorized"] is False
    assert result["executable"] is False


def test_observed_proposal_confirmation_restart_is_locally_consistent_only():
    value = fixture()
    original = copy.deepcopy(value)
    result = check_state_continuity(value)
    assert result["conformance_status"] == "PASS", result
    assert result["status"] == "BLOCKED"
    assert result["evaluations_checked"] == 3
    assert result["runs_checked"] == 2
    assert result["restart_boundaries_checked"] == 1
    assert "SOURCE_AUTHENTICATION_UNVERIFIED" in codes(result)
    assert "DECLARED_RUN_INDEX_COMPLETENESS_UNVERIFIED" in codes(result)
    assert "DAILY_INPUT_PROVENANCE_UNVERIFIED" in codes(result)
    assert value == original
    assert_denied(result)


@pytest.mark.parametrize("evidence_kind", ["synthetic_fixture", "historical"])
def test_success_declarations_cannot_grant_authority(evidence_kind):
    value = fixture()
    value.update(evidence_kind=evidence_kind, verified=True, complete=True,
                 historical_parity_established=True, production_ready=True,
                 execution_authorized=True, executable=True)
    result = check_state_continuity(value)
    assert result["conformance_status"] == "PASS", result
    assert_denied(result)
    del value["boundary"]
    blocked = check_state_continuity(value)
    assert blocked["conformance_status"] == "BLOCKED"
    assert_denied(blocked)


@pytest.mark.parametrize("field", ["input", "decision", "head_before", "head_after"])
def test_each_original_observation_is_required(field):
    value = fixture()
    del first_evaluation(value)[field]
    result = check_state_continuity(value)
    assert result["conformance_status"] == "BLOCKED", result
    assert_denied(result)


def test_last_commit_and_restart_read_cannot_be_inferred_from_diagnostics():
    value = fixture()
    value["runs"][1]["evaluations"][0].pop("head_before")
    result = check_state_continuity(value)
    assert result["conformance_status"] == "BLOCKED"
    assert "RESTART_HEAD_UNOBSERVED" in codes(result)
    value = fixture()
    value["runs"][-1]["evaluations"][-1].pop("head_after")
    result = check_state_continuity(value)
    assert result["conformance_status"] == "BLOCKED"
    assert "HEAD_AFTER_UNOBSERVED" in codes(result)


def test_final_confirmation_does_not_replace_unobserved_proposal():
    value = fixture()
    value["run_index"][0]["evaluation_ids"] = ["confirmation"]
    value["runs"][0]["evaluations"].pop(0)
    result = check_state_continuity(value)
    assert result["conformance_status"] == "BLOCKED", result
    assert "WITHIN_RUN_STATE_STEP_UNOBSERVED" in codes(result)
    assert result["evaluations_checked"] == 0
    assert_denied(result)


@pytest.mark.parametrize("position,contradictory_status", [(0, "confirmed"), (1, "proposed")])
def test_observed_state_status_contradiction_fails(position, contradictory_status):
    value = fixture()
    source = value["runs"][0]["evaluations"][position]["decision"]
    source["payload"]["diagnostics"]["tqqq_daily_state_status"] = contradictory_status
    reseal(source)
    result = check_state_continuity(value)
    assert result["conformance_status"] == "FAIL", result
    assert "STATE_STATUS_CONFLICT" in codes(result)
    assert_denied(result)


@pytest.mark.parametrize("missing", ["run", "evaluation", "session", "boundary"])
def test_missing_indexed_history_is_blocked(missing):
    value = fixture()
    if missing == "run":
        value["runs"].pop()
    elif missing == "evaluation":
        value["runs"][0]["evaluations"].pop(0)
    elif missing == "session":
        value["sessions"] = ["2026-10-28", "2026-10-29", "2026-10-30"]
    else:
        value.pop("boundary")
    result = check_state_continuity(value)
    assert result["conformance_status"] == "BLOCKED", result
    assert_denied(result)


@pytest.mark.parametrize("mutation", ["run_duplicate", "run_order", "run_unlisted",
                                      "evaluation_duplicate", "evaluation_order", "predecessor"])
def test_known_index_contradictions_fail(mutation):
    value = fixture()
    if mutation == "run_duplicate":
        value["run_index"].append(copy.deepcopy(value["run_index"][0]))
    elif mutation == "run_order":
        value["runs"].reverse()
    elif mutation == "run_unlisted":
        extra = copy.deepcopy(value["runs"][0])
        extra["run_id"] = "unlisted-run"
        value["runs"].append(extra)
    elif mutation == "evaluation_duplicate":
        value["runs"][0]["evaluations"].append(copy.deepcopy(first_evaluation(value)))
    elif mutation == "evaluation_order":
        value["runs"][0]["evaluations"].reverse()
    else:
        value["run_index"][1]["predecessor_run_id"] = "unrelated-run"
    result = check_state_continuity(value)
    assert result["conformance_status"] == "FAIL", result
    assert result["status"] == "FAIL"
    assert_denied(result)


@pytest.mark.parametrize("key,replacement", [("platform", "charles_schwab"),
    ("execution_environment", "live"), ("server", "another-server"),
    ("lifecycle_id", "another-lifecycle"), ("account_alias", "acct_" + "1" * 16)])
def test_physical_domains_cannot_be_merged(key, replacement):
    value = fixture()
    source = first_evaluation(value)["input"]
    source["account_domain"][key] = replacement
    reseal(source)
    result = check_state_continuity(value)
    assert result["conformance_status"] == "FAIL", result
    assert "ACCOUNT_DOMAIN_MISMATCH" in codes(result)


@pytest.mark.parametrize("field,replacement", [("profile", "soxl_soxx_trend_income"),
    ("record_kind", "diagnostic"), ("run_id", "other-run"),
    ("evaluation_id", "other-evaluation"), ("process_instance_id", "old-process")])
def test_original_sources_are_bound_independently(field, replacement):
    value = fixture()
    source = first_evaluation(value)["decision"]
    source[field] = replacement
    reseal(source)
    result = check_state_continuity(value)
    assert result["conformance_status"] == "FAIL", result
    assert_denied(result)


def test_source_digest_and_transition_digest_are_both_checked():
    value = fixture()
    first_evaluation(value)["input"]["payload"]["portfolio"]["positions"] = []
    result = check_state_continuity(value)
    assert "SOURCE_DIGEST_MISMATCH" in codes(result)
    value = fixture()
    source = first_evaluation(value)["decision"]
    source["payload"]["diagnostics"]["tqqq_daily_state_transition"]["state"]["volatility_latch_active"] = False
    reseal(source)
    result = check_state_continuity(value)
    assert "TRANSITION_DIGEST_MISMATCH" in codes(result)


def test_lost_latch_cannot_be_reconstructed_from_holdings():
    value = fixture()
    consumed = request(value)["previous_transition"]
    consumed["state"]["volatility_latch_active"] = False
    rehash_transition(consumed)
    source = first_evaluation(value)["input"]
    source["payload"]["portfolio"]["positions"] = [{"symbol": "TQQQ", "quantity": 999}]
    reseal(source)
    result = check_state_continuity(value)
    assert result["conformance_status"] == "FAIL", result
    assert "CONSUMED_HEAD_MISMATCH" in codes(result)


@pytest.mark.parametrize("field,replacement", [("alpha_epoch", 1),
    ("intent_invalidation_reason", None),
    ("pending_intent", {"id": "old", "alpha_epoch": 2, "signal_session": "2026-10-28"})])
def test_valid_hash_does_not_hide_invalid_epoch_or_intent(field, replacement):
    value = fixture()
    source = first_evaluation(value)["decision"]
    produced = source["payload"]["diagnostics"]["tqqq_daily_state_transition"]
    produced["state"][field] = replacement
    rehash_transition(produced)
    reseal(source)
    result = check_state_continuity(value)
    assert result["conformance_status"] == "FAIL", result


def test_same_session_confirmation_cannot_change_input_or_state():
    value = fixture()
    source = value["runs"][0]["evaluations"][1]["input"]
    source["payload"]["state"]["tqqq_daily_state"]["input_sha256"] = "f" * 64
    reseal(source)
    result = check_state_continuity(value)
    assert "DAILY_INPUT_DIGEST_MISMATCH" in codes(result)
    value = fixture()
    source = value["runs"][0]["evaluations"][1]["decision"]
    produced = source["payload"]["diagnostics"]["tqqq_daily_state_transition"]
    produced["state"]["volatility_latch_active"] = False
    rehash_transition(produced)
    reseal(source)
    result = check_state_continuity(value)
    assert "SAME_SESSION_STATE_CONFLICT" in codes(result)


@pytest.mark.parametrize("change", ["release", "transition_schema", "state_schema", "bootstrap"])
def test_migration_and_rebootstrap_require_an_adapter(change):
    value = fixture()
    if change == "release":
        value["runs"][1]["release"]["release_id"] = "next-release"
    elif change == "bootstrap":
        request(value)["bootstrap"] = True
        reseal(first_evaluation(value)["input"])
    else:
        source = first_evaluation(value)["decision"]
        produced = source["payload"]["diagnostics"]["tqqq_daily_state_transition"]
        if change == "transition_schema":
            produced["schema_version"] = "strategy_risk_state_transition.v2"
        else:
            produced["state"]["schema_version"] = "tqqq_daily_state.v2"
        rehash_transition(produced)
        reseal(source)
    result = check_state_continuity(value)
    assert result["conformance_status"] == "BLOCKED", result
    assert_denied(result)


def test_same_release_with_changed_digest_is_a_contradiction():
    value = fixture()
    value["runs"][1]["release"]["manifest_sha256"] = "f" * 64
    result = check_state_continuity(value)
    assert result["conformance_status"] == "FAIL"
    assert "RELEASE_DIGEST_MISMATCH" in codes(result)


@pytest.mark.parametrize("role", ["input", "head_before"])
def test_future_observations_cannot_be_consumed(role):
    value = fixture()
    source = first_evaluation(value)[role]
    source["available_at"] = source["observed_at"] = "2026-10-29T22:00:00+00:00"
    reseal(source)
    result = check_state_continuity(value)
    assert result["conformance_status"] == "FAIL", result


@pytest.mark.parametrize("mutation", ["missing_request", "missing_diagnostics", "legacy_trace",
                                     "unknown_environment", "missing_process"])
def test_unknown_input_cannot_be_promoted(mutation):
    value = fixture()
    if mutation == "missing_request":
        source = first_evaluation(value)["input"]
        source["payload"].pop("state")
        reseal(source)
    elif mutation == "missing_diagnostics":
        source = first_evaluation(value)["decision"]
        source["payload"]["diagnostics"].pop("tqqq_daily_state_transition")
        reseal(source)
    elif mutation == "legacy_trace":
        source = first_evaluation(value)["decision"]
        source["payload"] = {"decision_targets": {"TQQQ": 1000}, "verified": True}
        reseal(source)
    elif mutation == "unknown_environment":
        value["account_domain"]["execution_environment"] = "unknown"
    else:
        value["runs"][0].pop("process_instance_id")
    result = check_state_continuity(value)
    assert result["conformance_status"] == "BLOCKED", result


def test_soxl_has_no_fabricated_empty_state_adapter():
    value = fixture()
    value["profile"] = "soxl_soxx_trend_income"
    value.update(state={}, verified=True, complete=True)
    result = check_state_continuity(value)
    assert "STATE_ADAPTER_UNAVAILABLE" in codes(result)
    assert result["conformance_status"] == "BLOCKED"
    assert result["evaluations_checked"] == 0
    assert_denied(result)


@pytest.mark.parametrize("value", [None, [], {}, {"schema_version": "unknown"}])
def test_malformed_top_level_has_no_success_path(value):
    result = check_state_continuity(value)
    assert result["conformance_status"] in {"FAIL", "BLOCKED"}
    assert_denied(result)
