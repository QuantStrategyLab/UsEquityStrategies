from __future__ import annotations

import json
import copy
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from us_equity_strategies.research.historical_runtime_bundle import (
    BundleError, FILES, PROFILES, canonical_sha256, check_bundle, freeze_bundle, main, write_template,
)
from us_equity_strategies.research.historical_runtime_template import template_records


def test_template_exercises_accounting_without_claiming_historical_acceptance(tmp_path):
    write_template(tmp_path)
    result = check_bundle(tmp_path, decision_replay=False)
    assert result["components"]["accounting"]["status"] == "PASS", result
    assert result["components"]["shared_account"]["status"] == "PASS", result
    assert result["conformance_status"] == "PASS", result
    assert result["status"] == "BLOCKED"
    assert result["historical_parity_established"] is False
    assert result["production_ready"] is False
    assert "DECISION_TO_ORDER_SIZING_AND_RISK_LINK_UNVERIFIED" in _codes(result)


def _edit(root: Path, role: str, change):
    manifest = json.loads((root / "manifest.json").read_text())
    item = next(item for item in manifest["files"] if item["role"] == role)
    path = root / item["path"]
    rows = ([json.loads(line) for line in path.read_text().splitlines()]
            if path.suffix == ".jsonl" else json.loads(path.read_text()))
    change(rows)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows)
                    if path.suffix == ".jsonl" else json.dumps(rows))
    (root / "SHA256SUMS").unlink()
    freeze_bundle(root)


def test_tampered_bytes_fail_before_accounting(tmp_path):
    write_template(tmp_path)
    with (tmp_path / "order-events.jsonl").open("a") as handle:
        handle.write("\n")
    result = check_bundle(tmp_path)
    assert result["components"]["integrity"]["status"] == "FAIL"
    assert result["components"]["accounting"]["status"] == "NOT_EXECUTED"


def test_second_strategy_cannot_spend_reserved_cash(tmp_path):
    write_template(tmp_path)
    _edit(tmp_path, "order_events", lambda rows: rows[1].update(reserved_cash="950"))
    result = check_bundle(tmp_path, decision_replay=False)
    assert "SHARED_CASH_OVERCOMMITTED" in {item["code"] for item in result["findings"]}
    assert result["status"] == "FAIL"


def _write(root, data, manifest=None):
    root.mkdir(parents=True, exist_ok=True)
    if manifest is None:
        manifest = template_records()[0]
    (root / "manifest.json").write_text(json.dumps(manifest))
    for role, name in FILES.items():
        path = root / name
        path.write_text("".join(json.dumps(row) + "\n" for row in data[role])
                        if path.suffix == ".jsonl" else json.dumps(data[role]))
    if (root / "SHA256SUMS").exists():
        (root / "SHA256SUMS").unlink()
    freeze_bundle(root)


def _codes(result):
    return {item["code"] for item in result["findings"]}


def _resequence(data, ordered):
    data["order_events"], data["account_events"] = [], []
    for sequence, (event, role) in enumerate(ordered, 1):
        event["event_id"], event["sequence"] = f"event_{sequence:03d}", sequence
        event["observed_at"] = f"2026-10-30T13:32:{sequence:02d}Z"
        if not event.pop("preserve_event_at", False):
            event["event_at"] = event["observed_at"]
        data[role].append(event)
    data["account_snapshots"][-1]["sequence"] = len(ordered)


def test_missing_file_is_blocked_not_observed_empty(tmp_path):
    write_template(tmp_path)
    (tmp_path / "account-events.jsonl").unlink()
    result = check_bundle(tmp_path)
    assert result["status"] == "BLOCKED"
    assert "REQUIRED_BUNDLE_FILE_MISSING" in _codes(result)


def test_freeze_never_overwrites_frozen_manifest(tmp_path):
    write_template(tmp_path)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    with pytest.raises(BundleError, match="BUNDLE_ALREADY_FROZEN"):
        freeze_bundle(tmp_path)
    assert before == {path.name: path.read_bytes() for path in tmp_path.iterdir()}


def test_check_does_not_mutate_any_bundle_byte(tmp_path):
    write_template(tmp_path)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    check_bundle(tmp_path, decision_replay=False)
    assert before == {path.name: path.read_bytes() for path in tmp_path.iterdir()}


@pytest.mark.parametrize("mutation,code", [
    (lambda d: d["order_events"][1].update(sequence=1), "GLOBAL_EVENT_SEQUENCE_GAP_OR_DUPLICATE"),
    (lambda d: d["order_events"][0].update(account_alias="acct_1111111111111111"), "EVENT_ACCOUNT_BINDING_MISMATCH"),
    (lambda d: d["order_events"][0].update(reserved_cash=True), "EXACT_DECIMAL_STRING_REQUIRED"),
    (lambda d: d["account_snapshots"][-1].update(cash="799"), "ACCOUNT_CASH_RECONCILIATION_MISMATCH"),
    (lambda d: d["runs"][0]["availability"][0].update(available_at="2026-10-30T14:00:00Z"), "INPUT_AVAILABLE_AFTER_DECISION"),
    (lambda d: d["runs"][0].update(output={"invented": True}), "RUN_INPUT_OR_OUTPUT_DIGEST_MISMATCH"),
    (lambda d: d["releases"][0].update(config={}), "RELEASE_CONFIG_DIGEST_MISMATCH"),
    (lambda d: d["order_events"][0].update(event_at="2026-10-30T14:00:00Z"), "NONCAUSAL_EVENT_OBSERVATION"),
    (lambda d: d["order_events"][4].update(cumulative_quantity="1"), "CUMULATIVE_FILL_MISMATCH"),
    (lambda d: d["order_events"][4].update(fee="11"), "FILL_EXCEEDS_CASH_RESERVATION"),
    (lambda d: d["order_events"][4].update(broker_order_id="order_wrong"), "FILL_ORDER_BINDING_MISMATCH"),
    (lambda d: d["account_snapshots"][0].update(valid_until="2026-10-30T13:30:00Z"), "STALE_OR_FUTURE_ACCOUNT_OBSERVATION"),
])
def test_invalid_evidence_fails_closed(tmp_path, mutation, code):
    _, data = template_records()
    mutation(data)
    _write(tmp_path, data)
    result = check_bundle(tmp_path, decision_replay=False)
    assert result["status"] == "FAIL", result
    assert code in _codes(result)


def test_nullable_orders_are_not_observed_no_orders(tmp_path):
    _, data = template_records()
    data["runs"][0]["order_observation"] = None
    _write(tmp_path, data)
    result = check_bundle(tmp_path, decision_replay=False)
    assert result["components"]["coverage"]["status"] == "BLOCKED"
    assert "RUN_ORDERS_UNOBSERVED" in _codes(result)


def test_observed_no_run_and_no_orders_can_be_checked(tmp_path):
    _, data = template_records()
    data["sessions"][0]["runs_by_profile"] = {profile: [] for profile in PROFILES}
    data["sessions"][0]["no_run_reasons"] = {profile: "scheduled_observation_no_run" for profile in PROFILES}
    data["runs"], data["order_events"] = [], []
    closing = data["account_snapshots"][-1]
    closing.update(sequence=0, cash="1000", settled_cash="1000", positions={},
                   allocations={profile: {} for profile in PROFILES})
    _write(tmp_path, data)
    result = check_bundle(tmp_path, decision_replay=False)
    assert result["conformance_status"] == "PASS", result
    assert not any(result["scenario_coverage"].values())
    assert result["shared_account_scenario_status"] == "BLOCKED"


def test_unobserved_session_is_not_an_observed_no_run(tmp_path):
    _, data = template_records()
    data["sessions"][0]["runs_by_profile"][PROFILES[0]] = None
    data["runs"] = data["runs"][1:]
    data["order_events"] = []
    _write(tmp_path, data)
    result = check_bundle(tmp_path, decision_replay=False)
    assert "SESSION_RUNS_UNOBSERVED" in _codes(result)
    assert result["components"]["coverage"]["status"] == "BLOCKED"


def test_missing_daily_account_boundary_is_blocked(tmp_path):
    _, data = template_records()
    data["account_snapshots"].pop()
    _write(tmp_path, data)
    result = check_bundle(tmp_path, decision_replay=False)
    assert "DAILY_ACCOUNT_BOUNDARY_MISSING" in _codes(result)
    assert result["status"] == "BLOCKED"


def test_duplicate_fill_after_release_does_not_change_cash(tmp_path):
    _, data = template_records()
    duplicate = copy.deepcopy(data["order_events"][4])
    duplicate.update(event_id="event_duplicate", sequence=11, observed_at="2026-10-30T13:32:00Z")
    data["order_events"].append(duplicate)
    data["account_snapshots"][-1]["sequence"] = 11
    _write(tmp_path, data)
    result = check_bundle(tmp_path, decision_replay=False)
    assert result["conformance_status"] == "PASS", result
    assert result["scenario_coverage"]["duplicate_execution_idempotence"] is True
    assert result["order_trace"][-1]["cash"] == "798"


def test_conflicting_duplicate_fill_is_never_ignored(tmp_path):
    _, data = template_records()
    duplicate = copy.deepcopy(data["order_events"][4])
    duplicate.update(event_id="event_duplicate", sequence=11, observed_at="2026-10-30T13:32:00Z", fee="0")
    data["order_events"].append(duplicate)
    data["account_snapshots"][-1]["sequence"] = 11
    _write(tmp_path, data)
    result = check_bundle(tmp_path, decision_replay=False)
    assert "EXECUTION_IDENTITY_CONFLICT" in _codes(result)


@pytest.mark.parametrize("replacement,code", [
    ({"kind": "release"}, "UNRESOLVED_RESERVATION_RELEASE"),
    ({"kind": "submit", "attempt_id": "attempt_retry", "quantity": "2"}, "ACCOUNT_UNKNOWN_OUTCOME_BLOCKS_NEW_EXECUTION"),
    ({"kind": "rejected", "reason": "http_500"}, "UNCONFIRMED_TERMINAL_OUTCOME"),
])
def test_unknown_attempt_cannot_be_released_retried_or_inferred_rejected(tmp_path, replacement, code):
    _, data = template_records()
    data["order_events"][3]["kind"] = "unknown"
    data["order_events"][4].update(replacement)
    _write(tmp_path, data)
    result = check_bundle(tmp_path, decision_replay=False)
    assert code in _codes(result)
    assert result["status"] == "FAIL"


def test_unresolved_unknown_keeps_the_cash_reservation(tmp_path):
    _, data = template_records()
    data["order_events"][3]["kind"] = "unknown"
    events = data["order_events"][:2] + data["order_events"][6:] + data["order_events"][2:4]
    _resequence(data, [(event, "order_events") for event in events])
    data["account_snapshots"][-1].update(cash="899", settled_cash="899", positions={"SOXL": "2"},
        allocations={PROFILES[0]: {}, PROFILES[1]: {"SOXL": "2"}}, reserved_cash={"intent_TQQQ": "110"})
    _write(tmp_path, data)
    result = check_bundle(tmp_path, decision_replay=False)
    assert result["components"]["accounting"]["status"] == "PASS", result
    assert result["components"]["order_lifecycle"]["status"] == "BLOCKED"
    assert result["unresolved_intents"] == ["intent_TQQQ"]


@pytest.mark.parametrize("cancel_early", [False, True])
def test_partial_fill_cancel_and_retry_only_the_remaining_quantity(tmp_path, cancel_early):
    _, data = template_records()
    partial = copy.deepcopy(data["order_events"][4])
    partial.update(quantity="1", fee="0.5", cumulative_quantity="1", remaining_quantity="1")
    cancelled = {**partial, "kind": "cancelled", "reason": "broker_cancelled"}
    retry = {**partial, "kind": "submit", "attempt_id": "attempt_TQQQ_retry", "quantity": "1"}
    ack = {**retry, "kind": "acknowledged", "broker_order_id": "order_TQQQ_retry"}
    remaining = {**ack, "kind": "fill", "execution_id": "execution_TQQQ_retry", "remaining_quantity": "0"}
    events = data["order_events"][:4] + [partial, cancelled, retry, ack, remaining] + data["order_events"][5:]
    _resequence(data, [(event, "order_events") for event in events])
    if cancel_early:
        next(event for event in data["order_events"] if event["kind"] == "cancelled")["event_at"] = "2026-10-30T13:32:04Z"
    _write(tmp_path, data)
    result = check_bundle(tmp_path, decision_replay=False)
    if cancel_early:
        assert result["status"] == "FAIL"
        assert "BROKER_TERMINAL_PRECEDES_INCLUDED_FILL" in _codes(result)
        return
    assert result["conformance_status"] == "PASS", result
    assert result["scenario_coverage"]["partial_fill"] is True
    assert result["scenario_coverage"]["retry_after_terminal"] is True


def test_unknown_submission_can_be_resolved_by_a_bound_broker_fill(tmp_path):
    _, data = template_records()
    data["order_events"][3]["kind"] = "unknown"
    _write(tmp_path, data)
    result = check_bundle(tmp_path, decision_replay=False)
    assert result["conformance_status"] == "PASS", result


def test_split_dividend_external_cashflow_and_fee_reconcile(tmp_path):
    _, data = template_records()
    base = {key: data["order_events"][-1][key] for key in
            ("event_id", "sequence", "event_at", "observed_at", "account_alias", "source_evidence_id")}
    account_events = [
        {**base, "kind": "split", "symbol": "TQQQ", "ratio": "2"},
        {**base, "kind": "dividend_ex", "symbol": "TQQQ", "per_share": "1", "entitlement_id": "entitlement_1"},
        {**base, "kind": "dividend_pay", "entitlement_id": "entitlement_1", "amount": "4"},
        {**base, "kind": "external_cashflow", "amount": "100", "settled": True},
        {**base, "kind": "fee", "amount": "2"},
    ]
    _resequence(data, [(event, "order_events") for event in data["order_events"]]
                + [(event, "account_events") for event in account_events])
    data["account_snapshots"][-1].update(cash="900", settled_cash="900", positions={"TQQQ": "4", "SOXL": "2"},
        allocations={PROFILES[0]: {"TQQQ": "4"}, PROFILES[1]: {"SOXL": "2"}})
    _write(tmp_path, data)
    result = check_bundle(tmp_path, decision_replay=False)
    assert result["conformance_status"] == "PASS", result


def test_restart_must_preserve_the_joint_reservation_state(tmp_path):
    _, data = template_records()
    restart = {**data["order_events"][0], "kind": "restart",
               "reservation_sha256": canonical_sha256({"intent_TQQQ": "110", "intent_SOXL": "110"})}
    ordered = [(event, "order_events") for event in data["order_events"]]
    ordered.insert(2, (restart, "account_events"))
    _resequence(data, ordered)
    _write(tmp_path, data)
    result = check_bundle(tmp_path, decision_replay=False)
    assert result["conformance_status"] == "PASS", result
    assert result["scenario_coverage"]["restart_with_reservation"] is True
    data["account_events"][0]["reservation_sha256"] = canonical_sha256({})
    _write(tmp_path, data)
    assert "RESTART_RESERVATION_STATE_MISMATCH" in _codes(check_bundle(tmp_path, decision_replay=False))


def test_foreign_strategy_ownership_cannot_be_sold(tmp_path):
    _, data = template_records()
    data["order_events"][0].update(side="sell", reserved_cash="0")
    _write(tmp_path, data)
    result = check_bundle(tmp_path, decision_replay=False)
    assert "STRATEGY_SELL_QUANTITY_OVERCOMMITTED" in _codes(result)


def test_unknown_source_revision_blocks_daily_replay(tmp_path):
    write_template(tmp_path)
    result = check_bundle(tmp_path)
    assert result["components"]["daily_replay"]["status"] == "BLOCKED"
    assert {"HISTORICAL_RELEASE_ADAPTER_UNAVAILABLE", "CURRENT_SOURCE_ADAPTER_UNAVAILABLE"} & _codes(result)
    assert not any(row["computed"] for row in result["daily_results"])


def test_relabeling_a_fixture_historical_does_not_authenticate_it(tmp_path):
    manifest, data = template_records()
    manifest["evidence_kind"] = "historical"
    _write(tmp_path, data, manifest)
    result = check_bundle(tmp_path, decision_replay=False)
    assert result["conformance_status"] == "PASS", result
    assert result["components"]["provenance"]["status"] == "BLOCKED"
    assert result["historical_parity_established"] is False


def test_symlink_input_is_rejected_before_opening_it(tmp_path):
    write_template(tmp_path)
    path = tmp_path / "account-events.jsonl"
    path.unlink()
    path.symlink_to(tmp_path / "order-events.jsonl")
    result = check_bundle(tmp_path)
    assert "UNSAFE_BUNDLE_PATH" in _codes(result)


def test_unlisted_files_are_not_silently_included_in_a_bundle(tmp_path):
    write_template(tmp_path)
    (tmp_path / "unrelated.txt").write_text("not a bundle input")
    result = check_bundle(tmp_path)
    assert "UNLISTED_BUNDLE_FILES" in _codes(result)


def test_duplicate_json_keys_are_rejected(tmp_path):
    write_template(tmp_path)
    path = tmp_path / "manifest.json"
    raw = path.read_text().replace('"currency": "USD"', '"currency": "USD", "currency": "USD"')
    path.write_text(raw)
    result = check_bundle(tmp_path)
    assert "DUPLICATE_JSON_KEY" in _codes(result)


def test_unknown_in_one_profile_blocks_submission_by_the_other(tmp_path):
    _, data = template_records()
    data["order_events"][3]["kind"] = "unknown"
    events = data["order_events"][:4] + data["order_events"][6:]
    _resequence(data, [(event, "order_events") for event in events])
    _write(tmp_path, data)
    result = check_bundle(tmp_path, decision_replay=False)
    assert "ACCOUNT_UNKNOWN_OUTCOME_BLOCKS_NEW_EXECUTION" in _codes(result)
    assert result["order_trace"][-1]["reserved_cash"] == "220"


def test_decimal_multiplication_and_cash_subtraction_preserve_more_than_28_digits(tmp_path):
    _, data = template_records()
    for event in data["order_events"]:
        if event["kind"] == "fill":
            event["price"] = "50.123456789012345678901234567890"
    closing = "797.506172843950617284395061728440"
    data["account_snapshots"][-1].update(cash=closing, settled_cash=closing)
    _write(tmp_path, data)
    result = check_bundle(tmp_path, decision_replay=False)
    assert result["conformance_status"] == "PASS", result
    assert result["order_trace"][-1]["cash"] == closing


def test_large_exact_balance_does_not_erase_fractional_cash(tmp_path):
    _, data = template_records()
    opening = "1000000000000000000000000001000.123456789012345678901234567890"
    closing = "1000000000000000000000000000798.123456789012345678901234567890"
    data["account_snapshots"][0].update(cash=opening, settled_cash=opening)
    data["account_snapshots"][-1].update(cash=closing, settled_cash=closing)
    _write(tmp_path, data)
    result = check_bundle(tmp_path, decision_replay=False)
    assert result["conformance_status"] == "PASS", result
    assert result["order_trace"][-1]["cash"] == closing


def test_precision_overflow_is_blocked_instead_of_silently_rounded(tmp_path):
    _, data = template_records()
    base = data["order_events"][-1]
    events = [{**base, "kind": "split", "symbol": "TQQQ", "ratio": "1." + "7" * 78} for _ in range(4)]
    _resequence(data, [(event, "order_events") for event in data["order_events"]]
                + [(event, "account_events") for event in events])
    _write(tmp_path, data)
    result = check_bundle(tmp_path, decision_replay=False)
    assert result["status"] == "BLOCKED", result
    assert "DECIMAL_PRECISION_EXCEEDS_SUPPORTED_BOUND" in _codes(result)


def test_sell_proceeds_are_unsettled_until_a_settlement_event(tmp_path):
    _, data = template_records()
    base = {**data["order_events"][0], "intent_id": "intent_sell"}
    data["runs"][0]["intent_ids"].append("intent_sell")
    sell_events = [
        {**base, "kind": "reserve", "side": "sell", "quantity": "1", "reserved_cash": "0"},
        {**base, "kind": "submit", "attempt_id": "attempt_sell", "quantity": "1"},
        {**base, "kind": "acknowledged", "attempt_id": "attempt_sell", "broker_order_id": "order_sell"},
        {**base, "kind": "fill", "attempt_id": "attempt_sell", "broker_order_id": "order_sell",
         "execution_id": "execution_sell", "quantity": "1", "price": "50", "fee": "1",
         "cumulative_quantity": "1", "remaining_quantity": "0"},
        {**base, "kind": "release"},
    ]
    settlement = {**base, "kind": "settlement", "amount": "49"}
    _resequence(data, [(event, "order_events") for event in data["order_events"] + sell_events]
                + [(settlement, "account_events")])
    data["account_snapshots"][-1].update(cash="847", settled_cash="847", positions={"TQQQ": "1", "SOXL": "2"},
        allocations={PROFILES[0]: {"TQQQ": "1"}, PROFILES[1]: {"SOXL": "2"}})
    _write(tmp_path, data)
    result = check_bundle(tmp_path, decision_replay=False)
    assert result["conformance_status"] == "PASS", result
    assert result["order_trace"][-2]["settled_cash"] == "798"
    assert result["order_trace"][-1]["settled_cash"] == "847"


@pytest.mark.parametrize("name", ["manifest.json", "SHA256SUMS"])
def test_freeze_rejects_symlinks_without_writing_outside_bundle(tmp_path, name):
    root = tmp_path / "bundle"
    write_template(root)
    target = tmp_path / "untouched.json"
    target.write_bytes((root / "manifest.json").read_bytes())
    original = target.read_bytes()
    (root / "SHA256SUMS").unlink()
    if name == "manifest.json":
        (root / name).unlink()
    (root / name).symlink_to(target if name == "manifest.json" else tmp_path / "nonexistent")
    with pytest.raises(BundleError, match="UNSAFE_BUNDLE_PATH"):
        freeze_bundle(root)
    assert target.read_bytes() == original
    assert not (tmp_path / "nonexistent").exists()


def test_symlink_bundle_root_is_rejected(tmp_path):
    real = tmp_path / "real"
    write_template(real)
    pointer = tmp_path / "pointer"
    pointer.symlink_to(real, target_is_directory=True)
    assert "UNSAFE_BUNDLE_PATH" in _codes(check_bundle(pointer))


@pytest.mark.parametrize("trace_target,golden_target,match", [(0, 0, True), (1, 0, False), (0, False, False), (0, 0.0, False)])
def test_golden_comparison_cannot_override_adapter_blocked_result(tmp_path, monkeypatch, trace_target, golden_target, match):
    _, data = template_records()
    expected = {"decision_targets": {"TQQQ": golden_target}, "execution_authorized": False}
    for run in data["runs"]:
        run["output"], run["output_sha256"] = expected, canonical_sha256(expected)
    _write(tmp_path, data)
    calls = []

    def replay(request):
        calls.append(request)
        trace = {**expected, "decision_targets": {"TQQQ": trace_target}}
        return SimpleNamespace(trace=trace, status="BLOCKED", executable=False,
                               gaps=("COMPLETE_SOURCE_DEPENDENCY_CLOSURE_UNVERIFIED",))

    module = SimpleNamespace(ProductionDecisionReplayRequest=SimpleNamespace,
                             production_source_hashes=lambda: data["releases"][0]["source_hashes"],
                             replay_production_decision=replay)
    monkeypatch.setitem(sys.modules, "us_equity_strategies.research.production_decision_replay", module)
    result = check_bundle(tmp_path)
    assert len(calls) == 2
    assert all(row["computed"] and row["golden_comparison"] == ("PASS" if match else "FAIL") for row in result["daily_results"])
    assert all(row["status"] == "BLOCKED" for row in result["daily_results"])
    assert result["components"]["daily_replay"]["status"] == ("BLOCKED" if match else "FAIL")
    assert result["historical_parity_established"] is False


def test_cli_exit_codes_distinguish_blocked_and_failure(tmp_path):
    root, output = tmp_path / "input", tmp_path / "report.json"
    assert main(["template", str(root)]) == 0
    assert main(["check", str(root), "--skip-decision-replay", "--output", str(output)]) == 2
    assert json.loads(output.read_text())["conformance_status"] == "PASS"
    with (root / "order-events.jsonl").open("a") as handle:
        handle.write("\n")
    output = tmp_path / "failure-report.json"
    assert main(["check", str(root), "--skip-decision-replay", "--output", str(output)]) == 1
    assert json.loads(output.read_text())["components"]["integrity"]["status"] == "FAIL"


def test_init_provides_an_unfrozen_historical_intake_without_invented_data(tmp_path):
    root = tmp_path / "new-export"
    assert main(["init", str(root)]) == 0
    assert not (root / "SHA256SUMS").exists()
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["evidence_kind"] == "historical"
    assert manifest["account_alias"] is None
    assert (root / "runs.jsonl").read_text() == ""
    assert check_bundle(root)["status"] == "BLOCKED"
    freeze_bundle(root)
    result = check_bundle(root)
    assert result["status"] == "BLOCKED"
    assert "REQUIRED_MANIFEST_METADATA_MISSING" in _codes(result)


@pytest.mark.parametrize("event_index,code", [
    (2, "ORDER_EVENT_PRECEDES_RESERVATION"),
    (3, "BROKER_EVENT_PRECEDES_SUBMISSION"),
    (4, "BROKER_EVENT_PRECEDES_SUBMISSION"),
])
def test_order_event_clocks_cannot_precede_their_causal_parent(tmp_path, event_index, code):
    _, data = template_records()
    data["order_events"][event_index]["event_at"] = (
        "2026-10-30T13:30:01Z" if event_index == 2 else "2026-10-30T13:31:02Z")
    _write(tmp_path, data)
    result = check_bundle(tmp_path, decision_replay=False)
    assert result["status"] == "FAIL"
    assert code in _codes(result)


def test_cli_does_not_overwrite_a_hardlink_to_frozen_input(tmp_path):
    root = tmp_path / "input"
    write_template(root)
    original = (root / "order-events.jsonl").read_bytes()
    output = tmp_path / "report.json"
    os.link(root / "order-events.jsonl", output)
    with pytest.raises(SystemExit) as error:
        main(["check", str(root), "--skip-decision-replay", "--output", str(output)])
    assert error.value.code == 2
    assert (root / "order-events.jsonl").read_bytes() == original
    assert output.read_bytes() == original


def test_freeze_refuses_a_manifest_shared_with_an_external_path(tmp_path):
    root = tmp_path / "input"
    assert main(["init", str(root)]) == 0
    external = tmp_path / "external.json"
    os.link(root / "manifest.json", external)
    original = external.read_bytes()
    with pytest.raises(BundleError, match="SHARED_MANIFEST_INODE"):
        freeze_bundle(root)
    assert external.read_bytes() == original
    assert not (root / "SHA256SUMS").exists()


@pytest.mark.parametrize("duplicate", [True, False])
def test_dividend_entitlement_cannot_be_reused_or_paid_before_it_existed(tmp_path, duplicate):
    _, data = template_records()
    base = data["order_events"][-1]
    ex = {**base, "kind": "dividend_ex", "symbol": "TQQQ", "per_share": "1", "entitlement_id": "entitlement_1"}
    pay = {**base, "kind": "dividend_pay", "entitlement_id": "entitlement_1", "amount": "2"}
    account_events = [ex, pay, copy.deepcopy(ex)] if duplicate else [ex, pay]
    _resequence(data, [(event, "order_events") for event in data["order_events"]]
                + [(event, "account_events") for event in account_events])
    if not duplicate:
        data["account_events"][1]["event_at"] = "2026-10-30T13:31:50Z"
    _write(tmp_path, data)
    result = check_bundle(tmp_path, decision_replay=False)
    code = "DUPLICATE_DIVIDEND_ENTITLEMENT" if duplicate else "DIVIDEND_PAYMENT_PRECEDES_ENTITLEMENT"
    assert result["status"] == "FAIL", result
    assert code in _codes(result)
