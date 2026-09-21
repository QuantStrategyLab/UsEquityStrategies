from __future__ import annotations

from us_equity_strategies.portfolio_risk_budget import (
    PortfolioAssetRiskSpec,
    PortfolioRiskBudgetPolicy,
)
from us_equity_strategies.research.c4_shadow_zero_submit_cycle import (
    EVIDENCE_SCOPE,
    SCHEMA_VERSION,
    consume_c4_shadow_zero_submit_cycle,
)


def _account(**overrides: object) -> dict[str, object]:
    payload = {
        "account_id": "acct-paper-1",
        "strategy_id": "us_equity_combo_shadow",
        "as_of": "2026-09-21T14:00:00Z",
        "snapshot_version": "account.v1",
        "account_digest": "a" * 64,
        "freshness": "FRESH",
    }
    payload.update(overrides)
    return payload


def _orders(**overrides: object) -> dict[str, object]:
    payload = {
        "account_id": "acct-paper-1",
        "strategy_id": "us_equity_combo_shadow",
        "as_of": "2026-09-21T14:00:00Z",
        "snapshot_version": "orders.v1",
        "orders_digest": "b" * 64,
        "account_digest": "a" * 64,
        "freshness": "FRESH",
        "orders": [],
    }
    payload.update(overrides)
    return payload


def _risk(**overrides: object) -> dict[str, object]:
    payload = {
        "account_id": "acct-paper-1",
        "strategy_id": "us_equity_combo_shadow",
        "as_of": "2026-09-21T14:00:00Z",
        "snapshot_version": "risk.v1",
        "freshness": "FRESH",
        "status": "APPROVE",
        "execution_authorized": False,
        "account_digest": "a" * 64,
        "risk_policy_digest": "c" * 64,
        "assessment_digest": "d" * 64,
    }
    payload.update(overrides)
    return payload


SPECS = {
    "BOXX": PortfolioAssetRiskSpec("BOXX", 1.0, "CASH", is_cash=True),
    "SOXL": PortfolioAssetRiskSpec("SOXL", 3.0, "SEMICONDUCTOR"),
    "TQQQ": PortfolioAssetRiskSpec("TQQQ", 3.0, "NASDAQ100"),
}
POLICY = PortfolioRiskBudgetPolicy(
    cash_symbol="BOXX",
    max_effective_risk_exposure=1.5,
    max_symbol_weights={"SOXL": 0.40, "TQQQ": 0.40},
    max_underlying_effective_exposure={"SEMICONDUCTOR": 1.2, "NASDAQ100": 1.2},
)


def test_consumes_materialized_inputs_as_verifiable_zero_submit() -> None:
    result = consume_c4_shadow_zero_submit_cycle(
        account_snapshot=_account(),
        open_orders_snapshot=_orders(),
        risk_engine_result=_risk(),
        member_refs=(
            {
                "member_id": "soxl_core",
                "evidence_digest": "e" * 64,
                "input_digest": "f" * 64,
            },
            {
                "member_id": "tqqq_core",
                "evidence_digest": "1" * 64,
                "input_digest": "2" * 64,
            },
        ),
        research_target_weights={"SOXL": 0.20, "TQQQ": 0.20, "BOXX": 0.60},
        asset_risk_specs=SPECS,
        risk_policy=POLICY,
    )

    assert result["schema_version"] == SCHEMA_VERSION
    assert result["evidence_scope"] == EVIDENCE_SCOPE
    assert result["status"] == "READY_SHADOW_ZERO_SUBMIT"
    assert result["research_only"] is True
    assert result["shadow_only"] is True
    assert result["no_order"] is True
    assert result["execution_authorized"] is False
    assert result["promotion_authorized"] is False
    assert result["proposed_orders"] == []
    assert result["submission_attempted"] is False
    assert result["execution_permitted"] is False
    assert result["account_ref"]["account_digest"] == "a" * 64
    assert result["orders_ref"]["orders_digest"] == "b" * 64
    assert result["risk_ref"]["assessment_digest"] == "d" * 64
    assert result["policy_digest"] == "c" * 64
    assert result["risk_contribution"]["status"] == "APPROVE"
    assert "effective_risk_exposure" in result["risk_contribution"]["metrics"]
    assert result["boundaries"]["integer_share_sizing"] == "DIAGNOSTIC_ONLY_NOT_COMPUTED"
    assert result["boundaries"]["fill_ledger_reconstruction"] == (
        "NOT_COMPUTED_REQUIRES_PLATFORM_FACTS"
    )
    assert len(result["evidence_digest"]) == 64
    # Research recommendation must not become an execution intent.
    assert "SOXL" in result["risk_contribution"]["recommended_target_weights"]
    assert result["proposed_orders"] == []


def test_missing_account_snapshot_parks_fail_closed() -> None:
    result = consume_c4_shadow_zero_submit_cycle(
        account_snapshot={},  # type: ignore[arg-type]
        open_orders_snapshot=_orders(),
        risk_engine_result=_risk(),
    )
    assert result["status"] == "PARKED"
    assert result["execution_authorized"] is False
    assert result["proposed_orders"] == []
    assert result["submission_attempted"] is False
    assert result["execution_permitted"] is False


def test_stale_orders_snapshot_parks() -> None:
    result = consume_c4_shadow_zero_submit_cycle(
        account_snapshot=_account(),
        open_orders_snapshot=_orders(freshness="STALE"),
        risk_engine_result=_risk(),
    )
    assert result["status"] == "PARKED"
    assert result["reason_codes"] == ("ORDERS_FRESHNESS_STALE_OR_MISSING",)


def test_identity_or_digest_mismatch_parks() -> None:
    mismatched = consume_c4_shadow_zero_submit_cycle(
        account_snapshot=_account(),
        open_orders_snapshot=_orders(strategy_id="other_strategy"),
        risk_engine_result=_risk(),
    )
    assert mismatched["status"] == "PARKED"
    assert mismatched["reason_codes"] == ("ACCOUNT_ORDERS_STRATEGY_ID_MISMATCH",)

    digest_mismatch = consume_c4_shadow_zero_submit_cycle(
        account_snapshot=_account(),
        open_orders_snapshot=_orders(),
        risk_engine_result=_risk(account_digest="9" * 64),
    )
    assert digest_mismatch["status"] == "PARKED"
    assert digest_mismatch["reason_codes"] == ("ACCOUNT_DIGEST_MISMATCH",)

    unbound_orders = dict(_orders())
    del unbound_orders["account_digest"]
    missing_binding = consume_c4_shadow_zero_submit_cycle(
        account_snapshot=_account(),
        open_orders_snapshot=unbound_orders,
        risk_engine_result=_risk(),
    )
    assert missing_binding["status"] == "PARKED"
    assert missing_binding["reason_codes"] == ("ORDERS_ACCOUNT_DIGEST_INVALID",)


def test_unknown_order_outcome_parks() -> None:
    result = consume_c4_shadow_zero_submit_cycle(
        account_snapshot=_account(),
        open_orders_snapshot=_orders(
            orders=[{"order_id": "oid-1", "outcome": "UNKNOWN"}]
        ),
        risk_engine_result=_risk(),
    )
    assert result["status"] == "PARKED"
    assert result["reason_codes"] == ("OPEN_ORDER_OUTCOME_UNKNOWN",)
    assert result["no_order"] is True


def test_risk_engine_reject_or_execution_authorized_true_parks() -> None:
    rejected = consume_c4_shadow_zero_submit_cycle(
        account_snapshot=_account(),
        open_orders_snapshot=_orders(),
        risk_engine_result=_risk(status="REJECT"),
    )
    assert rejected["status"] == "PARKED"
    assert rejected["reason_codes"] == ("RISK_ENGINE_NOT_APPROVE",)

    authorized = consume_c4_shadow_zero_submit_cycle(
        account_snapshot=_account(),
        open_orders_snapshot=_orders(),
        risk_engine_result=_risk(execution_authorized=True),
    )
    assert authorized["status"] == "PARKED"
    assert authorized["reason_codes"] == (
        "RISK_ENGINE_EXECUTION_AUTHORIZED_SEMANTICS_UNSATISFIED",
    )


def test_research_weights_never_become_orders_even_when_reduced() -> None:
    result = consume_c4_shadow_zero_submit_cycle(
        account_snapshot=_account(),
        open_orders_snapshot=_orders(),
        risk_engine_result=_risk(),
        research_target_weights={"SOXL": 0.60, "TQQQ": 0.40},
        asset_risk_specs=SPECS,
        risk_policy=POLICY,
    )
    assert result["status"] == "READY_SHADOW_ZERO_SUBMIT"
    assert result["risk_contribution"]["status"] == "REDUCE"
    assert result["proposed_orders"] == []
    assert result["submission_attempted"] is False
    assert result["execution_permitted"] is False
    assert result["execution_authorized"] is False


def test_fresh_snapshot_rejects_illegal_naive_or_future_as_of() -> None:
    naive = consume_c4_shadow_zero_submit_cycle(
        account_snapshot=_account(as_of="2026-09-21T14:00:00"),
        open_orders_snapshot=_orders(as_of="2026-09-21T14:00:00"),
        risk_engine_result=_risk(as_of="2026-09-21T14:00:00"),
    )
    assert naive["status"] == "PARKED"
    assert naive["reason_codes"] == ("ACCOUNT_AS_OF_TIMEZONE_REQUIRED",)
    assert naive["proposed_orders"] == []
    assert naive["submission_attempted"] is False
    assert naive["execution_permitted"] is False
    assert naive["execution_authorized"] is False
    assert naive["promotion_authorized"] is False
    assert naive["no_order"] is True

    unparseable = consume_c4_shadow_zero_submit_cycle(
        account_snapshot=_account(as_of="not-a-timestamp"),
        open_orders_snapshot=_orders(as_of="not-a-timestamp"),
        risk_engine_result=_risk(as_of="not-a-timestamp"),
    )
    assert unparseable["status"] == "PARKED"
    assert unparseable["reason_codes"] == ("ACCOUNT_AS_OF_UNPARSEABLE",)

    future = consume_c4_shadow_zero_submit_cycle(
        account_snapshot=_account(as_of="2099-01-01T00:00:00Z"),
        open_orders_snapshot=_orders(as_of="2099-01-01T00:00:00Z"),
        risk_engine_result=_risk(as_of="2099-01-01T00:00:00Z"),
    )
    assert future["status"] == "PARKED"
    assert future["reason_codes"] == ("ACCOUNT_AS_OF_FUTURE",)


def test_account_and_orders_as_of_must_share_explainable_instant() -> None:
    mismatched = consume_c4_shadow_zero_submit_cycle(
        account_snapshot=_account(as_of="2026-09-21T14:00:00Z"),
        open_orders_snapshot=_orders(as_of="2026-09-21T14:05:00Z"),
        risk_engine_result=_risk(as_of="2026-09-21T14:00:00Z"),
    )
    assert mismatched["status"] == "PARKED"
    assert mismatched["reason_codes"] == ("ACCOUNT_ORDERS_AS_OF_MISMATCH",)
    assert mismatched["proposed_orders"] == []
    assert mismatched["no_order"] is True

    equivalent = consume_c4_shadow_zero_submit_cycle(
        account_snapshot=_account(as_of="2026-09-21T14:00:00+00:00"),
        open_orders_snapshot=_orders(as_of="2026-09-21T14:00:00Z"),
        risk_engine_result=_risk(as_of="2026-09-21T14:00:00Z"),
    )
    assert equivalent["status"] == "READY_SHADOW_ZERO_SUBMIT"
    assert equivalent["account_ref"]["as_of"] == "2026-09-21T14:00:00Z"
    assert equivalent["orders_ref"]["as_of"] == "2026-09-21T14:00:00Z"
    assert equivalent["as_of_relation"] == "ACCOUNT_ORDERS_RISK_IDENTICAL_UTC"
    assert equivalent["execution_authorized"] is False
    assert equivalent["proposed_orders"] == []
