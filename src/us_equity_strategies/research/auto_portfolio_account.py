"""Offline adapter for existing ibkr_account_snapshot.v1 facts.

The caller supplies a frozen, source-bound cash/completeness contract. Cash
balance is never inferred to mean settled or spendable cash. No broker I/O.
The original adapter remains artificial-only. The separate read-only assessment
accepts real-classified packets with caller-bound source hashes and receipt
times; it does not authenticate the caller's source or admit model/execution use.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re

CASH_RULE = "SETTLED_MINUS_DISJOINT_RESERVATIONS_AND_OBLIGATIONS_NO_SELL_BRIDGE"


def utc(value):
    if not isinstance(value, str):
        raise ValueError("aware timestamp required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("aware timestamp required")
    return parsed.astimezone(timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def adapt_ibkr_account_input(facts: Mapping, contract: Mapping) -> dict:
    return _adapt_ibkr_account_input(facts, contract, synthetic=True)


def _adapt_ibkr_account_input(facts, contract, *, synthetic):
    if not isinstance(facts,Mapping) or not isinstance(contract,Mapping):
        return dict(status="ACCOUNT_INPUT_GAPS",gaps=["ACCOUNT_WIRE_INVALID"],nav=None,
            cash_balance=None,spendable_cash=None,current_weights=None,protected_capital_basis=None,
            scope_ref=None,currency=None,cutoff_utc=None,execution_authorized=False,
            account_policy_bound=False,synthetic=False)
    result = dict(status="ACCOUNT_INPUT_GAPS", gaps=[], nav=None, cash_balance=None,
        spendable_cash=None, current_weights=None, protected_capital_basis=None,
        scope_ref=contract.get("scope_ref"), currency=contract.get("currency"),
        cutoff_utc=contract.get("cutoff_utc"), execution_authorized=False,
        account_policy_bound=False, synthetic=contract.get("synthetic") is True)
    gaps = result["gaps"]

    def amount(value, code, *, positive=False):
        try:
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise ValueError(code)
            number = Decimal(str(value))
            if not number.is_finite() or not math.isfinite(float(number)) or number < 0 or (positive and number == 0):
                raise ValueError(code)
            return number
        except (InvalidOperation, ValueError, OverflowError):
            gaps.append(code)
            return None

    if facts.get("schema_version") != "ibkr_account_snapshot.v1":
        gaps.append("ACCOUNT_SCHEMA_UNSUPPORTED")
    account_id = contract.get("account_id")
    if (not isinstance(account_id,str) or not account_id.strip()
            or facts.get("account_ids") != [account_id]):
        gaps.append("ACCOUNT_SCOPE_MISMATCH")
    for name in ("scope_ref", "source", "source_version", "cash_rule_source", "capital_state_source"):
        if not isinstance(contract.get(name), str) or not contract[name].strip():
            gaps.append("MISSING_" + name.upper())
    if contract.get("synthetic") is not synthetic:
        gaps.append("SYNTHETIC_ACCOUNT_REQUIRED" if synthetic else "REAL_ACCOUNT_INPUT_REQUIRED")
    currency = contract.get("currency")
    if facts.get("currency") != currency or currency != "USD":
        gaps.append("CURRENCY_MISMATCH")
    try:
        age = (utc(contract.get("cutoff_utc")) - utc(facts.get("observed_at"))).total_seconds()
        limit = contract.get("max_age_seconds")
        if isinstance(limit, bool) or not isinstance(limit, (int, float)) or not math.isfinite(limit) or not 0 <= age <= limit:
            raise ValueError("snapshot time")
    except (TypeError, ValueError, OverflowError):
        gaps.append("SNAPSHOT_TIME_INVALID")
    if contract.get("nav_basis") != "WHOLE_ACCOUNT_NET_LIQUIDATION":
        gaps.append("NAV_BASIS_UNSUPPORTED")
    if contract.get("asset_scope") != "STOCKS_AND_CASH_ONLY":
        gaps.append("ASSET_SCOPE_UNSUPPORTED")
    if contract.get("positions_complete") is not True:
        gaps.append("POSITIONS_INCOMPLETE")
    if contract.get("obligations_complete") is not True:
        gaps.append("OBLIGATIONS_INCOMPLETE")
    if contract.get("cash_rule") != CASH_RULE:
        gaps.append("CASH_RULE_UNBOUND")

    nav = amount(facts.get("net_assets"), "INVALID_NET_ASSETS", positive=True)
    result["nav"] = float(nav) if nav is not None else None
    cash_rows = facts.get("cash")
    balance = None
    if (not isinstance(cash_rows, list) or len(cash_rows) != 1
            or not isinstance(cash_rows[0],Mapping) or cash_rows[0].get("currency") != currency):
        gaps.append("CASH_CURRENCY_SCOPE_UNSUPPORTED")
    else:
        if not isinstance(cash_rows[0].get("source_tag"),str) or not cash_rows[0]["source_tag"].strip():
            gaps.append("CASH_SOURCE_UNBOUND")
        balance = amount(cash_rows[0].get("cash_balance"), "INVALID_CASH_BALANCE")
        result["cash_balance"] = float(balance) if balance is not None else None
    cash = {}
    for key in ("settled_cash", "reserved_cash", "other_cash_obligations"):
        if key not in contract or contract[key] is None:
            gaps.append("MISSING_" + key.upper()); cash[key] = None
        else:
            cash[key] = amount(contract[key], "INVALID_" + key.upper())
    if all(x is not None for x in cash.values()):
        available = cash["settled_cash"] - cash["reserved_cash"] - cash["other_cash_obligations"]
        if balance is None or cash["settled_cash"] > balance or available < 0:
            gaps.append("CASH_CONTRACT_INCONSISTENT")
        else:
            result["spendable_cash"] = float(available)

    positions = facts.get("broker_reported_positions")
    marked = {}
    if facts.get("broker_reported_positions_scope") != "stocks_only":
        gaps.append("POSITION_SCOPE_INVALID")
    if not isinstance(positions, list):
        gaps.append("MISSING_POSITION_ROWS")
    else:
        for row in positions:
            if not isinstance(row,Mapping):
                gaps.append("POSITION_SCOPE_INVALID");continue
            symbol = row.get("symbol")
            if not isinstance(symbol, str) or not symbol or symbol in marked or row.get("currency") != currency:
                gaps.append("POSITION_SCOPE_INVALID"); continue
            quantity = amount(row.get("quantity"), "INVALID_POSITION_QUANTITY", positive=True)
            value = amount(row.get("market_value"), "INVALID_POSITION_MARK")
            if quantity is not None and value is not None:
                marked[symbol] = value
    tolerance = amount(contract.get("valuation_tolerance"), "VALUATION_CONTRACT_UNBOUND")
    if nav is not None and balance is not None and isinstance(positions, list) and tolerance is not None:
        residual = nav - balance - sum(marked.values())
        result["valuation_residual"] = str(residual)
        if abs(residual) > tolerance:
            gaps.append("NAV_RECONCILIATION_FAILED")
        result["current_weights"] = {s: float(v/nav) for s,v in marked.items()}
    if contract.get("protected_capital_basis") is None:
        gaps.append("MISSING_PROTECTED_CAPITAL_BASIS")
    else:
        protected = amount(contract["protected_capital_basis"], "INVALID_PROTECTED_CAPITAL_BASIS", positive=True)
        if protected is not None:
            if nav is not None and protected < nav:
                gaps.append("PROTECTED_CAPITAL_BELOW_NAV")
            result["protected_capital_basis"] = float(protected)
    try:
        result["facts_sha256"] = digest(facts)
        result["cash_contract_sha256"] = digest(contract)
    except (TypeError, ValueError):
        gaps.append("ACCOUNT_WIRE_INVALID")
    result["gaps"] = sorted(set(gaps))
    if not gaps:
        result["status"] = "READY_ARTIFICIAL_INPUT" if synthetic else "READY_READONLY_INPUT"
    return result


def assess_ibkr_readonly_account_input(facts: Mapping, contract: Mapping, *, source: Mapping) -> dict:
    """Assess supplied real inputs without changing the artificial preview API.

    Hashes bind supplied facts/contract and identify their retained source bytes.
    Source reference and producer revision are caller declarations, not proof of
    authenticity, obligation completeness, policy approval or publication time.
    A trusted private caller retains original bytes and the actual read receipt.
    published_at stays None when unavailable; received_at is never backdated to
    a historical cutoff. No model, order, state store or settings writer is used.
    """
    result = _adapt_ibkr_account_input(facts, contract, synthetic=False)
    result.update(settings_applied=False, financial_risk_certified=False,
                  historical_pit_attested=False, source_received_at=None,
                  source_published_at=None, source_object_sha256=None,
                  source_identity="CALLER_BOUND_HASHES_NOT_AUTHENTICATED")
    gaps = result["gaps"]
    if isinstance(contract, Mapping) and contract.get("max_age_seconds") is None:
        gaps.append("READONLY_FRESHNESS_LIMIT_UNBOUND")
    if not isinstance(source, Mapping):
        gaps.append("READONLY_SOURCE_UNBOUND")
    else:
        if any(not isinstance(source.get(k), str) or not source[k].strip()
               for k in ("source_ref", "source_revision")):
            gaps.append("READONLY_SOURCE_UNBOUND")
        hashes = ("source_object_sha256", "facts_sha256", "contract_sha256")
        if any(not isinstance(source.get(k), str) or re.fullmatch(r"[0-9a-f]{64}", source[k]) is None
               for k in hashes):
            gaps.append("READONLY_SOURCE_UNBOUND")
        try:
            if source.get("facts_sha256") != digest(facts) or source.get("contract_sha256") != digest(contract):
                gaps.append("READONLY_SOURCE_IDENTITY_MISMATCH")
        except (TypeError, ValueError):
            gaps.append("READONLY_SOURCE_IDENTITY_MISMATCH")
        try:
            observed, received, cutoff = utc(facts.get("observed_at")), utc(source.get("received_at")), utc(contract.get("cutoff_utc"))
            if not observed <= received <= cutoff:
                raise ValueError("source receipt outside cutoff")
            published = source.get("published_at")
            if published is not None and not observed <= utc(published) <= received:
                raise ValueError("source publication outside receipt")
            result.update(source_received_at=source["received_at"], source_published_at=published)
        except (AttributeError, TypeError, ValueError, OverflowError):
            gaps.append("READONLY_SOURCE_TIME_INVALID")
        if "READONLY_SOURCE_UNBOUND" not in gaps and "READONLY_SOURCE_IDENTITY_MISMATCH" not in gaps:
            result["source_object_sha256"] = source["source_object_sha256"]
    result["gaps"] = sorted(set(gaps))
    if gaps:
        result.update(status="ACCOUNT_INPUT_GAPS", spendable_cash=None, current_weights=None)
    return result
