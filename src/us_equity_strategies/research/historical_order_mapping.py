"""Check captured order-mapping evidence without calculating or authorizing orders.

This contract checks one target, parent plan and its slices. It has no production
mapping adapter: the target-to-unrounded-quantity calculation remains unverified.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, DecimalException, localcontext
import re

from .historical_runtime_bundle import canonical_sha256

SCHEMA = "qsl.historical_order_mapping.v1"
_IDENTITY = ("run_id", "release_id", "profile", "platform", "execution_environment",
             "server", "account_alias", "lifecycle_id")
_INPUTS = ("target", "account", "pending", "price", "policy")
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_HASH = re.compile(r"[0-9a-f]{64}")
_AMOUNT = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]{1,12})?")
_MAX_AMOUNT = Decimal("1000000000000000000")


class _EvidenceError(ValueError):
    def __init__(self, code, scope, *, blocked=False):
        super().__init__(code)
        self.code, self.scope, self.blocked = code, scope, blocked


def _require(condition, code, scope, *, blocked=False):
    if not condition:
        raise _EvidenceError(code, scope, blocked=blocked)


def _object(value, scope):
    _require(type(value) is dict, "INVALID_STRUCTURE", scope)
    return value


def _need(value, key, scope):
    item = _object(value, scope).get(key)
    _require(item is not None, "MISSING_EVIDENCE", scope, blocked=True)
    return item


def _identifier(value, scope):
    _require(type(value) is str and _ID.fullmatch(value) is not None,
             "INVALID_IDENTIFIER", scope)
    return value


def _digest(value, scope):
    _require(type(value) is str and _HASH.fullmatch(value) is not None,
             "INVALID_SOURCE_DIGEST", scope)
    return value


def _amount(value, scope, *, positive=False):
    _require(type(value) is str and len(value) <= 40 and _AMOUNT.fullmatch(value) is not None,
             "INVALID_DECIMAL", scope)
    result = Decimal(value)
    _require(result.is_finite() and 0 <= result <= _MAX_AMOUNT and (not positive or result > 0),
             "DECIMAL_OUT_OF_RANGE", scope)
    return result


def _number(row, key, scope, *, positive=False):
    return _amount(_need(row, key, scope), scope, positive=positive)


def _clock(value, scope):
    _require(type(value) is str and len(value) <= 64, "AWARE_TIMESTAMP_REQUIRED", scope)
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise _EvidenceError("AWARE_TIMESTAMP_REQUIRED", scope) from error
    _require(result.tzinfo is not None, "AWARE_TIMESTAMP_REQUIRED", scope)
    return result.astimezone(timezone.utc)


def _rows(value, scope):
    _require(type(value) is list and len(value) <= 1000, "INVALID_STRUCTURE", scope)
    return value


def _age_limit(policy, key):
    value = _need(policy, key, "policy")
    _require(type(value) is int and 0 <= value <= 86400, "INVALID_AGE_LIMIT", "policy")
    return timedelta(seconds=value)


def _identity(value):
    identity = {key: _identifier(_need(value, key, "identity"), "identity") for key in _IDENTITY}
    _require(identity["profile"] in {"tqqq_growth_income", "soxl_soxx_trend_income"},
             "PROFILE_ADAPTER_UNAVAILABLE", "identity", blocked=True)
    _require(identity["execution_environment"] in {"live", "paper", "dry_run"},
             "ACCOUNT_DOMAIN_UNVERIFIED", "identity", blocked=True)
    _require(re.fullmatch(r"acct_[0-9a-f]{16,64}", identity["account_alias"]) is not None,
             "ACCOUNT_ALIAS_REQUIRED", "identity")
    return identity


def _record(value, scope, identity, source_ids):
    value = _object(value, scope)
    source_id = _identifier(_need(value, "source_id", scope), scope)
    _require(source_id not in source_ids, "DUPLICATE_SOURCE_ID", scope)
    source_ids.add(source_id)
    binding = _need(value, "identity", scope)
    _require(all(_need(binding, key, scope) == identity[key] for key in _IDENTITY),
             "IDENTITY_BINDING_MISMATCH", scope)
    producer = _digest(_need(value, "producer_sha256", scope), scope)
    payload = _object(_need(value, "payload", scope), scope)
    digest = _digest(_need(value, "payload_sha256", scope), scope)
    _require(canonical_sha256(payload) == digest, "PAYLOAD_DIGEST_MISMATCH", scope)
    event = _clock(_need(value, "event_at", scope), scope)
    available = _clock(_need(value, "available_at", scope), scope)
    _require(event <= available, "AVAILABILITY_PRECEDES_EVENT", scope)
    return {"id": source_id, "producer": producer, "payload": payload,
            "event": event, "available": available}


def _effective(clock, first, last, scope):
    _require(first <= clock < last, "RELEASE_NOT_EFFECTIVE", scope)


def _fresh(records, at, price_age, account_age):
    _require(at - records["price"]["event"] <= price_age, "PRICE_TOO_OLD", "price")
    for role in ("account", "pending"):
        _require(at - records[role]["event"] <= account_age, "ACCOUNT_TOO_OLD", role)


def _check(document, result):
    document = _object(document, "document")
    _require(_need(document, "schema_version", "document") == SCHEMA,
             "UNSUPPORTED_SCHEMA", "document")
    _require(_need(document, "evidence_kind", "document") in {"synthetic", "historical"},
             "UNSUPPORTED_EVIDENCE_KIND", "document")
    identity = _identity(_need(document, "identity", "identity"))
    release = _object(_need(document, "release", "release"), "release")
    _require(_need(release, "release_id", "release") == identity["release_id"],
             "IDENTITY_BINDING_MISMATCH", "release")
    hashes = {role: _digest(_need(release, role + "_source_sha256", "release"), "release")
              for role in ("strategy", "risk", "mapper")}
    config = _digest(_need(release, "configuration_sha256", "release"), "release")
    first = _clock(_need(release, "effective_from", "release"), "release")
    last = _clock(_need(release, "effective_until", "release"), "release")
    _require(first < last, "INVALID_RELEASE_INTERVAL", "release")
    adapter = _object(_need(document, "adapter", "adapter"), "adapter")
    _identifier(_need(adapter, "adapter_id", "adapter"), "adapter")
    _require(_digest(_need(adapter, "source_sha256", "adapter"), "adapter") == hashes["mapper"],
             "SOURCE_HASH_BINDING_MISMATCH", "adapter")

    source_ids = set()
    records = {role: _record(_need(document, role, role), role, identity, source_ids)
               for role in (*_INPUTS, "plan")}
    at = records["plan"]["event"]
    _effective(at, first, last, "plan")
    _effective(records["target"]["event"], first, last, "target")
    for role in _INPUTS:
        _require(records[role]["available"] <= at, "FUTURE_SOURCE", role)
    for role, producer in (("target", "risk"), ("policy", "mapper"), ("plan", "mapper")):
        _require(records[role]["producer"] == hashes[producer], "SOURCE_HASH_BINDING_MISMATCH", role)
    target, account, pending, price, policy, plan = (records[role]["payload"]
                                                   for role in (*_INPUTS, "plan"))
    refs = _need(plan, "source_refs", "plan")
    for role in _INPUTS:
        _require(_need(refs, role, "plan") == records[role]["id"], "SOURCE_REFERENCE_MISMATCH", "plan")
    _require(_need(target, "stage", "target") == "post_risk"
             and _need(target, "origin", "target") == "risk_result", "TARGET_STAGE_MISMATCH", "target")
    target_id = _identifier(_need(target, "target_id", "target"), "target")
    symbol = _identifier(_need(target, "symbol", "target"), "target")
    target_unit = _need(target, "target_unit", "target")
    _require(target_unit in {"USD", "shares"}, "TARGET_UNIT_ADAPTER_UNAVAILABLE", "target", blocked=True)
    target_amount = _number(target, "target_amount", "target")
    _require(_need(plan, "target_id", "plan") == target_id
             and _need(plan, "target_unit", "plan") == target_unit
             and _number(plan, "target_amount", "plan") == target_amount,
             "TARGET_REFERENCE_MISMATCH", "plan")
    for role, row in (("plan", plan), ("account", account), ("price", price)):
        _require(_need(row, "symbol", role) == symbol, "SYMBOL_BINDING_MISMATCH", role)
    _require(_need(price, "currency", "price") == "USD"
             and _need(price, "unit", "price") == "USD_per_share"
             and _need(account, "currency", "account") == "USD", "UNIT_MISMATCH", "price")
    _require(_need(price, "kind", "price") in {"limit", "reference"},
             "PRICE_ADAPTER_UNAVAILABLE", "price", blocked=True)
    price_amount = _number(price, "amount", "price", positive=True)
    held = _number(account, "position_shares", "account")
    sellable = _number(account, "sellable_shares", "account")
    settled = _number(account, "settled_cash", "account")
    reserved = _number(account, "reserved_cash", "account")
    _require(sellable <= held, "SELLABLE_EXCEEDS_POSITION", "account")
    _require(reserved <= settled, "CASH_OVERCOMMITTED", "account")

    _require(_digest(_need(policy, "configuration_sha256", "policy"), "policy") == config,
             "CONFIGURATION_BINDING_MISMATCH", "policy")
    unit = _need(plan, "quantity_unit", "plan")
    _require(unit in {"shares", "DOLLARS"}, "ORDER_UNIT_ADAPTER_UNAVAILABLE", "plan", blocked=True)
    _require(_need(policy, "quantity_unit", "policy") == unit, "UNIT_MISMATCH", "policy")
    step = _number(policy, "quantity_step", "policy", positive=True)
    minimum_quantity = _number(policy, "minimum_quantity", "policy")
    minimum_notional = _number(policy, "minimum_notional", "policy")
    price_age = _age_limit(policy, "maximum_price_age_seconds")
    account_age = _age_limit(policy, "maximum_account_age_seconds")
    _fresh(records, at, price_age, account_age)
    rounding = _need(policy, "rounding", "policy")
    _require(rounding in {"floor", "exact"}
             and _need(policy, "minimum_scope", "policy") == "per_slice"
             and _need(policy, "cash_basis", "policy") == "settled_cash"
             and _need(policy, "pending_treatment", "policy") == "block",
             "POLICY_ADAPTER_UNAVAILABLE", "policy", blocked=True)
    pending_orders = _rows(_need(pending, "orders", "pending"), "pending")
    pending_ids = set()
    for row in pending_orders:
        order_id = _identifier(_need(row, "order_id", "pending"), "pending")
        _require(order_id not in pending_ids, "DUPLICATE_PENDING_ORDER", "pending")
        pending_ids.add(order_id)
        _identifier(_need(row, "symbol", "pending"), "pending")
        _require(_need(row, "side", "pending") in {"buy", "sell"}, "INVALID_ORDER_SIDE", "pending")
        _require(_need(row, "quantity_unit", "pending") in {"shares", "DOLLARS"},
                 "ORDER_UNIT_ADAPTER_UNAVAILABLE", "pending", blocked=True)
        _number(row, "remaining_quantity", "pending", positive=True)
    plan_id = _identifier(_need(plan, "plan_id", "plan"), "plan")
    side = _need(plan, "side", "plan")
    _require(side in {"buy", "sell"}, "INVALID_ORDER_SIDE", "plan")
    _require(not (side == "sell" and unit == "DOLLARS"),
             "ORDER_UNIT_ADAPTER_UNAVAILABLE", "plan", blocked=True)
    before_rounding = _number(plan, "before_rounding_quantity", "plan")
    planned = _number(plan, "planned_quantity", "plan")
    allocated = _number(plan, "allocated_cash", "plan")
    planned_fees = _number(plan, "estimated_fees", "plan")
    slices = _rows(_need(document, "slices", "slices"), "slices")
    result_kind = _need(plan, "result_kind", "plan")
    _require(result_kind in {"orders", "skip"}, "INVALID_PLAN_RESULT", "plan")
    if result_kind == "skip":
        _identifier(_need(plan, "skip_reason", "plan"), "plan")
        _require(not slices and planned == allocated == planned_fees == 0, "SKIP_HAS_ORDERS_OR_RESERVATION", "plan")
        raise _EvidenceError("SKIP_REASON_ADAPTER_UNAVAILABLE", "plan", blocked=True)
    _require(not pending_orders, "PENDING_ORDER_CONFLICT", "pending")
    _require(planned > 0 and bool(slices), "ORDER_PLAN_HAS_NO_QUANTITY_OR_SLICES", "plan")
    expected = before_rounding - before_rounding % step if rounding == "floor" else before_rounding
    _require(planned == expected and planned % step == 0, "ROUNDING_MISMATCH", "plan")

    total_quantity, total_fees = Decimal(0), Decimal(0)
    slice_ids = set()
    previous_at = records["plan"]["available"]
    for sequence, envelope in enumerate(slices, 1):
        record = _record(envelope, "slice", identity, source_ids)
        _require(record["producer"] == hashes["mapper"], "SOURCE_HASH_BINDING_MISMATCH", "slice")
        _require(record["event"] >= previous_at, "SLICE_PRECEDES_PLAN", "slice")
        previous_at = record["event"]
        _effective(record["event"], first, last, "slice")
        _fresh(records, record["event"], price_age, account_age)
        row = record["payload"]
        slice_id = _identifier(_need(row, "slice_id", "slice"), "slice")
        _require(slice_id not in slice_ids, "DUPLICATE_SLICE_ID", "slice")
        slice_ids.add(slice_id)
        number = _need(row, "sequence", "slice")
        _require(type(number) is int and number == sequence, "SLICE_SEQUENCE_MISMATCH", "slice")
        _require(_need(row, "plan_id", "slice") == plan_id and _need(row, "target_id", "slice") == target_id
                 and _need(row, "symbol", "slice") == symbol and _need(row, "side", "slice") == side,
                 "SLICE_REFERENCE_MISMATCH", "slice")
        _require(_need(row, "plan_source_id", "slice") == records["plan"]["id"]
                 and _need(row, "price_source_id", "slice") == records["price"]["id"],
                 "SOURCE_REFERENCE_MISMATCH", "slice")
        _require(_need(row, "quantity_unit", "slice") == unit, "UNIT_MISMATCH", "slice")
        quantity = _number(row, "quantity", "slice", positive=True)
        fee = _number(row, "estimated_fee", "slice")
        _require(quantity % step == 0, "SLICE_STEP_VIOLATION", "slice")
        notional = quantity if unit == "DOLLARS" else quantity * price_amount
        _require(quantity >= minimum_quantity and notional >= minimum_notional,
                 "MINIMUM_ORDER_VIOLATION", "slice")
        total_quantity += quantity
        total_fees += fee
        result["checked_slice_count"] += 1
    _require(total_quantity == planned, "SLICE_QUANTITY_MISMATCH", "slices")
    _require(total_fees == planned_fees, "SLICE_FEE_MISMATCH", "slices")
    notional = total_quantity if unit == "DOLLARS" else total_quantity * price_amount
    if side == "buy":
        _require(notional + total_fees <= allocated <= settled - reserved, "CASH_OVERCOMMITTED", "plan")
    else:
        _require(allocated == 0, "SELL_HAS_CASH_RESERVATION", "plan")
        _require(total_quantity <= sellable, "SELL_QUANTITY_OVERCOMMITTED", "plan")
        _require(total_fees <= notional + settled - reserved, "CASH_OVERCOMMITTED", "plan")
    result["observed_quantity_unit"] = unit


def validate_order_mapping(document) -> dict:
    """Validate supplied trace relationships; never reproduce a production sizer.

    Local checks stop at the first missing or contradictory requirement. A
    consistent trace still has unverified source identity and mapping semantics.
    The input is never mutated and raw identifiers are not copied to findings.
    """
    result = {
        "schema_version": SCHEMA + ".result", "status": "BLOCKED", "local_consistency": "BLOCKED",
        "adapter_status": "BLOCKED", "target_to_quantity_reproduced": False,
        "source_authenticity_established": False, "historical_parity_established": False,
        "risk_authorization_status": "BLOCKED", "checked_slice_count": 0, "observed_quantity_unit": None,
        "execution_authorized": False, "production_ready": False, "export_authorized": False,
        "upload_authorized": False,
        "findings": [{"code": code, "scope": scope, "status": "BLOCKED"} for code, scope in (
            ("ADAPTER_IDENTITY_UNVERIFIED", "adapter"),
            ("TARGET_TO_QUANTITY_ADAPTER_UNAVAILABLE", "mapping"),
            ("SOURCE_AUTHENTICITY_UNVERIFIED", "provenance"),
            ("HISTORICAL_PARITY_UNVERIFIED", "historical"),
            ("RISK_AUTHORIZATION_UNVERIFIED", "risk"),
        )],
    }
    try:
        with localcontext() as context:
            context.prec = 80
            _check(document, result)
        result["local_consistency"] = "CONSISTENT"
    except _EvidenceError as error:
        status = "BLOCKED" if error.blocked else "FAIL"
        result["status"] = result["local_consistency"] = status
        result["findings"].append({"code": error.code, "scope": error.scope, "status": status})
    except (TypeError, ValueError, OverflowError, RecursionError, DecimalException):
        result["status"] = result["local_consistency"] = "FAIL"
        result["findings"].append({"code": "INVALID_EVIDENCE_STRUCTURE", "scope": "document", "status": "FAIL"})
    return result
