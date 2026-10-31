from __future__ import annotations

import copy

import pytest

from us_equity_strategies.research.historical_order_mapping import validate_order_mapping
from us_equity_strategies.research.historical_runtime_bundle import canonical_sha256


def document():
    identity = {
        "run_id": "run_1", "release_id": "release_1", "profile": "tqqq_growth_income",
        "platform": "charles_schwab", "execution_environment": "paper", "server": "server_1",
        "account_alias": "acct_1111111111111111", "lifecycle_id": "lifecycle_1",
    }

    def record(name, payload, at, *, producer="c"):
        return {
            "source_id": "source_" + name, "producer_sha256": producer * 64,
            "identity": dict(identity), "event_at": at, "available_at": at,
            "payload": payload, "payload_sha256": canonical_sha256(payload),
        }

    target = {"target_id": "target_1", "stage": "post_risk", "origin": "risk_result",
              "symbol": "TQQQ", "target_unit": "USD", "target_amount": "100"}
    account = {"symbol": "TQQQ", "position_shares": "5", "sellable_shares": "5",
               "currency": "USD", "settled_cash": "100", "reserved_cash": "10"}
    price = {"symbol": "TQQQ", "currency": "USD", "unit": "USD_per_share",
             "kind": "limit", "amount": "10"}
    policy = {
        "configuration_sha256": "a" * 64, "quantity_unit": "shares", "quantity_step": "1",
        "rounding": "floor", "minimum_scope": "per_slice", "minimum_quantity": "1",
        "minimum_notional": "10", "maximum_price_age_seconds": 60,
        "maximum_account_age_seconds": 120, "cash_basis": "settled_cash",
        "pending_treatment": "block",
    }
    plan = {
        "plan_id": "plan_1", "target_id": "target_1", "symbol": "TQQQ", "side": "buy",
        "target_unit": "USD", "target_amount": "100", "quantity_unit": "shares",
        "before_rounding_quantity": "5.8", "planned_quantity": "5", "allocated_cash": "60",
        "estimated_fees": "0.2", "result_kind": "orders",
        "source_refs": {role: "source_" + role for role in ("target", "account", "pending", "price", "policy")},
    }
    slices = [record("slice_" + str(index), {
        "slice_id": "slice_" + str(index), "sequence": index, "plan_id": "plan_1",
        "target_id": "target_1", "plan_source_id": "source_plan", "price_source_id": "source_price",
        "symbol": "TQQQ", "side": "buy", "quantity_unit": "shares",
        "quantity": quantity, "estimated_fee": "0.1",
    }, f"2026-04-09T10:00:0{index}Z") for index, quantity in enumerate(("2", "3"), 1)]
    return {
        "schema_version": "qsl.historical_order_mapping.v1", "evidence_kind": "synthetic",
        "identity": identity,
        "release": {"release_id": "release_1", "strategy_source_sha256": "d" * 64,
                    "risk_source_sha256": "b" * 64, "mapper_source_sha256": "c" * 64,
                    "configuration_sha256": "a" * 64, "effective_from": "2026-04-09T09:00:00Z",
                    "effective_until": "2026-04-10T09:00:00Z"},
        "adapter": {"adapter_id": "synthetic_captured_trace", "source_sha256": "c" * 64},
        "target": record("target", target, "2026-04-09T09:59:30Z", producer="b"),
        "account": record("account", account, "2026-04-09T09:59:00Z", producer="e"),
        "pending": record("pending", {"orders": []}, "2026-04-09T09:59:00Z", producer="e"),
        "price": record("price", price, "2026-04-09T09:59:40Z", producer="e"),
        "policy": record("policy", policy, "2026-04-09T09:01:00Z"),
        "plan": record("plan", plan, "2026-04-09T10:00:00Z"), "slices": slices,
    }


def payload(data, role):
    return data[role]["payload"]


def refresh(data):
    for role in ("target", "account", "pending", "price", "policy", "plan"):
        if isinstance(data.get(role), dict) and "payload" in data[role]:
            data[role]["payload_sha256"] = canonical_sha256(data[role]["payload"])
    for row in data.get("slices", []):
        row["payload_sha256"] = canonical_sha256(row["payload"])
    return data


def codes(result):
    return {row["code"] for row in result["findings"]}


def test_consistent_trace_is_never_production_or_mapping_parity():
    data = document()
    before = copy.deepcopy(data)
    result = validate_order_mapping(data)
    assert result["status"] == "BLOCKED"
    assert result["local_consistency"] == "CONSISTENT"
    assert result["target_to_quantity_reproduced"] is False
    assert result["adapter_status"] == "BLOCKED"
    assert result["historical_parity_established"] is False
    assert result["risk_authorization_status"] == "BLOCKED"
    assert result["checked_slice_count"] == 2
    assert all(result[key] is False for key in ("execution_authorized", "production_ready", "export_authorized", "upload_authorized"))
    assert data == before


@pytest.mark.parametrize("role", ["release", "adapter", "target", "account", "pending", "price", "policy", "plan", "slices"])
def test_missing_evidence_stays_blocked(role):
    data = document()
    del data[role]
    result = validate_order_mapping(data)
    assert result["status"] == "BLOCKED"
    assert result["local_consistency"] == "BLOCKED"
    assert "MISSING_EVIDENCE" in codes(result)


def test_self_asserted_completeness_validation_and_historical_labels_do_not_authorize():
    data = document()
    data.update(evidence_kind="historical", execution_validated=True, verified=True, complete=True,
                production_ready=True, execution_authorized=True)
    data["adapter"]["verified"] = True
    result = validate_order_mapping(data)
    assert result["status"] == "BLOCKED"
    assert result["adapter_status"] == "BLOCKED"
    assert not result["target_to_quantity_reproduced"]
    assert not result["execution_authorized"] and not result["production_ready"]


@pytest.mark.parametrize("changes", [{"stage": "pre_risk"}, {"origin": "decision_targets"}])
def test_original_decision_targets_cannot_be_relabeled_as_risk_output(changes):
    data = document()
    payload(data, "target").update(changes)
    result = validate_order_mapping(refresh(data))
    assert result["status"] == "FAIL"
    assert "TARGET_STAGE_MISMATCH" in codes(result)


@pytest.mark.parametrize("value", [True, 1.0, "NaN", "Infinity", "1e2", "-1", "1000000000000000001", "0.1234567890123"])
def test_amounts_require_bounded_exact_decimal_strings(value):
    data = document()
    payload(data, "plan")["planned_quantity"] = value
    result = validate_order_mapping(refresh(data))
    assert result["status"] == "FAIL"
    assert {"INVALID_DECIMAL", "DECIMAL_OUT_OF_RANGE"} & codes(result)


@pytest.mark.parametrize("field", ["release_id", "profile", "platform", "execution_environment", "server", "account_alias", "lifecycle_id"])
def test_record_bindings_cannot_cross_release_or_account_domains(field):
    data = document()
    data["account"]["identity"][field] = "different_identity"
    result = validate_order_mapping(data)
    assert result["status"] == "FAIL"
    assert "IDENTITY_BINDING_MISMATCH" in codes(result)


def test_payload_tampering_and_source_hash_mismatch_are_failures():
    data = document()
    payload(data, "target")["target_amount"] = "999"
    assert "PAYLOAD_DIGEST_MISMATCH" in codes(validate_order_mapping(data))
    data = document()
    data["target"]["producer_sha256"] = "f" * 64
    assert "SOURCE_HASH_BINDING_MISMATCH" in codes(validate_order_mapping(data))


@pytest.mark.parametrize("where,key,value,code", [
    ("plan", "target_id", "wrong_target", "TARGET_REFERENCE_MISMATCH"),
    ("plan", "target_amount", "999", "TARGET_REFERENCE_MISMATCH"),
    ("plan", "target_unit", "shares", "TARGET_REFERENCE_MISMATCH"),
    ("plan", "planned_quantity", "6", "ROUNDING_MISMATCH"),
    ("policy", "quantity_unit", "DOLLARS", "UNIT_MISMATCH"),
    ("policy", "minimum_quantity", "3", "MINIMUM_ORDER_VIOLATION"),
    ("policy", "minimum_notional", "25", "MINIMUM_ORDER_VIOLATION"),
    ("account", "settled_cash", "65", "CASH_OVERCOMMITTED"),
    ("plan", "allocated_cash", "50", "CASH_OVERCOMMITTED"),
    ("plan", "estimated_fees", "0.3", "SLICE_FEE_MISMATCH"),
])
def test_explicit_contradictions_fail(where, key, value, code):
    data = document()
    payload(data, where)[key] = value
    result = validate_order_mapping(refresh(data))
    assert result["status"] == "FAIL"
    assert code in codes(result)


def test_target_to_quantity_formula_is_not_invented_from_consistent_references():
    data = document()
    for role in ("target", "plan"):
        payload(data, role).update(target_unit="shares", target_amount="1000")
    result = validate_order_mapping(refresh(data))
    assert result["local_consistency"] == "CONSISTENT"
    assert result["target_to_quantity_reproduced"] is False
    assert "TARGET_TO_QUANTITY_ADAPTER_UNAVAILABLE" in codes(result)


def test_dollar_quantity_consumes_dollars_and_is_not_converted_to_shares():
    data = document()
    payload(data, "policy").update(quantity_unit="DOLLARS", quantity_step="0.01", minimum_quantity="0.01")
    payload(data, "plan").update(quantity_unit="DOLLARS", before_rounding_quantity="50.009", planned_quantity="50")
    for row, amount in zip(data["slices"], ("20", "30")):
        row["payload"].update(quantity_unit="DOLLARS", quantity=amount)
    result = validate_order_mapping(refresh(data))
    assert result["local_consistency"] == "CONSISTENT"
    assert result["observed_quantity_unit"] == "DOLLARS"
    data["slices"][0]["payload"]["quantity_unit"] = "shares"
    assert "UNIT_MISMATCH" in codes(validate_order_mapping(refresh(data)))


@pytest.mark.parametrize("field,value,code", [
    ("quantity", "1", "SLICE_QUANTITY_MISMATCH"),
    ("quantity", "2.5", "SLICE_STEP_VIOLATION"),
    ("plan_id", "other_plan", "SLICE_REFERENCE_MISMATCH"),
    ("target_id", "other_target", "SLICE_REFERENCE_MISMATCH"),
    ("plan_source_id", "other_source", "SOURCE_REFERENCE_MISMATCH"),
    ("price_source_id", "other_price", "SOURCE_REFERENCE_MISMATCH"),
    ("sequence", 2, "SLICE_SEQUENCE_MISMATCH"),
])
def test_slice_identity_quantity_and_sequence_are_checked(field, value, code):
    data = document()
    data["slices"][0]["payload"][field] = value
    assert code in codes(validate_order_mapping(refresh(data)))


def test_repeated_slice_identity_and_wrong_plan_input_reference_fail():
    data = document()
    data["slices"][1]["payload"]["slice_id"] = "slice_1"
    assert "DUPLICATE_SLICE_ID" in codes(validate_order_mapping(refresh(data)))
    data = document()
    payload(data, "plan")["source_refs"]["account"] = "unrelated_account_source"
    assert "SOURCE_REFERENCE_MISMATCH" in codes(validate_order_mapping(refresh(data)))


@pytest.mark.parametrize("role,field,value,code", [
    ("price", "available_at", "2026-04-09T10:00:01Z", "FUTURE_SOURCE"),
    ("price", "event_at", "2026-04-09T09:00:00Z", "PRICE_TOO_OLD"),
    ("account", "event_at", "2026-04-09T09:00:00Z", "ACCOUNT_TOO_OLD"),
    ("pending", "available_at", "2026-04-09T10:00:01Z", "FUTURE_SOURCE"),
    ("target", "available_at", "2026-04-09T09:58:00Z", "AVAILABILITY_PRECEDES_EVENT"),
    ("price", "event_at", "2026-04-09T09:59:40", "AWARE_TIMESTAMP_REQUIRED"),
])
def test_source_clocks_cannot_be_future_stale_or_naive(role, field, value, code):
    data = document()
    data[role][field] = value
    assert code in codes(validate_order_mapping(data))


def test_slice_clock_is_after_plan_availability_and_price_is_fresh_for_each_slice():
    data = document()
    data["slices"][0].update(event_at="2026-04-09T09:59:59Z", available_at="2026-04-09T09:59:59Z")
    assert "SLICE_PRECEDES_PLAN" in codes(validate_order_mapping(data))
    data = document()
    data["slices"][1].update(event_at="2026-04-09T10:01:00Z", available_at="2026-04-09T10:01:00Z")
    assert "PRICE_TOO_OLD" in codes(validate_order_mapping(data))


def test_release_effective_interval_is_required_for_the_plan_and_slices():
    data = document()
    data["release"]["effective_until"] = "2026-04-09T10:00:01Z"
    assert "RELEASE_NOT_EFFECTIVE" in codes(validate_order_mapping(data))


@pytest.mark.parametrize("field,value", [("rounding", "producer_specific"), ("cash_basis", "buying_power"),
                                         ("pending_treatment", "net"), ("minimum_scope", "pre_round_budget")])
def test_unsupported_producer_policy_is_blocked_not_replaced(field, value):
    data = document()
    payload(data, "policy")[field] = value
    result = validate_order_mapping(refresh(data))
    assert result["status"] == "BLOCKED"
    assert result["local_consistency"] == "BLOCKED"
    assert "POLICY_ADAPTER_UNAVAILABLE" in codes(result)


def test_pending_orders_are_observed_rows_not_an_assumed_zero():
    data = document()
    del payload(data, "pending")["orders"]
    assert validate_order_mapping(refresh(data))["local_consistency"] == "BLOCKED"
    payload(data, "pending")["orders"] = [{"order_id": "pending_1", "symbol": "TQQQ", "side": "buy",
                                            "quantity_unit": "shares", "remaining_quantity": "2"}]
    result = validate_order_mapping(refresh(data))
    assert result["status"] == "FAIL"
    assert "PENDING_ORDER_CONFLICT" in codes(result)


def test_sell_cannot_exceed_observed_sellable_shares():
    data = document()
    payload(data, "plan").update(side="sell", allocated_cash="0")
    for row in data["slices"]:
        row["payload"]["side"] = "sell"
    assert validate_order_mapping(refresh(data))["local_consistency"] == "CONSISTENT"
    payload(data, "account")["sellable_shares"] = "4"
    assert "SELL_QUANTITY_OVERCOMMITTED" in codes(validate_order_mapping(refresh(data)))


def test_skip_keeps_its_reason_unverified_without_creating_orders():
    data = document()
    payload(data, "plan").update(result_kind="skip", skip_reason="producer_threshold", planned_quantity="0",
                                 allocated_cash="0", estimated_fees="0")
    data["slices"] = []
    result = validate_order_mapping(refresh(data))
    assert result["status"] == "BLOCKED"
    assert result["checked_slice_count"] == 0
    assert "SKIP_REASON_ADAPTER_UNAVAILABLE" in codes(result)


def test_invalid_structures_fail_without_echoing_supplied_content():
    data = document()
    data["target"] = "private-account-object-sentinel"
    result = validate_order_mapping(data)
    assert result["status"] == "FAIL"
    assert "private-account-object-sentinel" not in str(result)


@pytest.mark.parametrize("before,expected", [("5", "CONSISTENT"), ("5.8", "FAIL")])
def test_exact_rounding_does_not_silently_floor_a_producer_quantity(before, expected):
    data = document()
    payload(data, "policy")["rounding"] = "exact"
    payload(data, "plan")["before_rounding_quantity"] = before
    result = validate_order_mapping(refresh(data))
    assert result["local_consistency"] == expected


def test_unknown_cash_and_weight_target_require_evidence_and_an_adapter():
    data = document()
    payload(data, "account")["settled_cash"] = None
    result = validate_order_mapping(refresh(data))
    assert result["status"] == "BLOCKED" and "MISSING_EVIDENCE" in codes(result)
    data = document()
    payload(data, "target")["target_unit"] = "weight"
    result = validate_order_mapping(refresh(data))
    assert result["status"] == "BLOCKED" and "TARGET_UNIT_ADAPTER_UNAVAILABLE" in codes(result)


@pytest.mark.parametrize("each_fee,total_fee,settled,reserved,expected", [
    ("30", "60", "20", "10", "CONSISTENT"),
    ("30.005", "60.01", "20", "10", "FAIL"),
    ("500000000000000000", "1000000000000000000", "0", "0", "FAIL"),
])
def test_sell_fees_cannot_exceed_proceeds_and_unreserved_cash(each_fee, total_fee, settled, reserved, expected):
    data = document()
    payload(data, "account").update(settled_cash=settled, reserved_cash=reserved)
    payload(data, "plan").update(side="sell", allocated_cash="0", estimated_fees=total_fee)
    for row in data["slices"]:
        row["payload"].update(side="sell", estimated_fee=each_fee)
    result = validate_order_mapping(refresh(data))
    assert result["local_consistency"] == expected
    if expected == "FAIL":
        assert "CASH_OVERCOMMITTED" in codes(result)
    assert result["execution_authorized"] is False and result["production_ready"] is False

