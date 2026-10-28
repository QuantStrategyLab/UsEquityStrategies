"""Artificial packets exercise the real-source interface without account I/O."""
from copy import deepcopy

import pytest

from us_equity_strategies.research.auto_portfolio_account import (
    adapt_ibkr_account_input, assess_ibkr_readonly_account_input, digest,
)
from test_auto_portfolio_preview import account_fixture


def packet():
    facts, contract = account_fixture()
    contract["synthetic"] = False
    source = dict(source_ref="PRIVATE_TEST_SOURCE", source_revision="fixture-v1",
                  source_object_sha256="a" * 64, facts_sha256=digest(facts),
                  contract_sha256=digest(contract), received_at="2000-01-04T12:00:30Z",
                  published_at=None)
    return facts, contract, source


def assess(value):
    facts, contract, source = value
    return assess_ibkr_readonly_account_input(facts, contract, source=source)


def test_readonly_source_is_normalized_without_relabelling_or_execution():
    value = packet()
    original = deepcopy(value)
    result = assess(value)
    assert result["status"] == "READY_READONLY_INPUT"
    assert result["spendable_cash"] == 100
    assert result["synthetic"] is False
    assert result["execution_authorized"] is False
    assert result["settings_applied"] is False
    assert result["account_policy_bound"] is False
    assert result["financial_risk_certified"] is False
    assert result["historical_pit_attested"] is False
    assert result["source_identity"] == "CALLER_BOUND_HASHES_NOT_AUTHENTICATED"
    assert result["source_received_at"] == value[2]["received_at"]
    assert result["source_published_at"] is None
    assert value == original


def test_original_artificial_entry_still_rejects_real_input():
    facts, contract, _ = packet()
    assert "SYNTHETIC_ACCOUNT_REQUIRED" in adapt_ibkr_account_input(facts, contract)["gaps"]


@pytest.mark.parametrize("value", [True, None, "false", 0])
def test_readonly_entry_requires_explicit_real_classification(value):
    facts, contract, source = packet()
    contract["synthetic"] = value
    source["contract_sha256"] = digest(contract)
    result = assess((facts, contract, source))
    assert "REAL_ACCOUNT_INPUT_REQUIRED" in result["gaps"]
    assert result["spendable_cash"] is None


@pytest.mark.parametrize("field", ["facts_sha256", "contract_sha256", "source_object_sha256",
                                   "source_ref", "source_revision", "received_at"])
def test_missing_source_binding_cannot_make_a_usable_input(field):
    value = packet()
    value[2].pop(field)
    result = assess(value)
    assert result["status"] == "ACCOUNT_INPUT_GAPS"
    assert result["spendable_cash"] is None and result["current_weights"] is None


@pytest.mark.parametrize("field", ["facts_sha256", "contract_sha256"])
def test_content_changes_are_not_accepted_with_old_source_hashes(field):
    value = packet()
    value[2][field] = "b" * 64
    assert "READONLY_SOURCE_IDENTITY_MISMATCH" in assess(value)["gaps"]


@pytest.mark.parametrize("received,published", [
    ("2000-01-04T12:02:00Z", None),
    ("2000-01-04T11:59:00Z", None),
    ("2000-01-04T12:00:30Z", "2000-01-04T12:00:40Z"),
    ("2000-01-04T12:00:30", None),
])
def test_future_or_naive_receipt_and_later_publication_are_rejected(received, published):
    value = packet()
    value[2].update(received_at=received, published_at=published)
    assert "READONLY_SOURCE_TIME_INVALID" in assess(value)["gaps"]


def test_missing_settlement_and_obligations_do_not_become_zero_or_cash_balance():
    value = packet()
    value[1].pop("settled_cash")
    value[1].pop("other_cash_obligations")
    value[2]["contract_sha256"] = digest(value[1])
    result = assess(value)
    assert {"MISSING_SETTLED_CASH", "MISSING_OTHER_CASH_OBLIGATIONS"} <= set(result["gaps"])
    assert result["cash_balance"] == 100 and result["spendable_cash"] is None


def test_wrong_account_and_unknown_complete_scope_stay_rejected():
    value = packet()
    value[0]["account_ids"] = ["OTHER"]
    value[1]["obligations_complete"] = False
    value[2].update(facts_sha256=digest(value[0]), contract_sha256=digest(value[1]))
    result = assess(value)
    assert {"ACCOUNT_SCOPE_MISMATCH", "OBLIGATIONS_INCOMPLETE"} <= set(result["gaps"])
    assert result["spendable_cash"] is None


def test_missing_freshness_policy_is_reported_separately_from_staleness():
    value = packet()
    value[1]["max_age_seconds"] = None
    value[2]["contract_sha256"] = digest(value[1])
    assert "READONLY_FRESHNESS_LIMIT_UNBOUND" in assess(value)["gaps"]


@pytest.mark.parametrize("source", [None, [], "invalid"])
def test_malformed_source_is_a_fixed_gap_not_an_exception(source):
    facts, contract, _ = packet()
    result = assess_ibkr_readonly_account_input(facts, contract, source=source)
    assert "READONLY_SOURCE_UNBOUND" in result["gaps"]


def test_malformed_facts_and_contract_remain_diagnostic():
    result = assess_ibkr_readonly_account_input(None, None, source=None)
    assert result["status"] == "ACCOUNT_INPUT_GAPS"
    assert "ACCOUNT_WIRE_INVALID" in result["gaps"]
    assert result["spendable_cash"] is None
