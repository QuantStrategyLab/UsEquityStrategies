from __future__ import annotations

import pytest

import us_equity_strategies.entrypoints as entrypoints
from us_equity_strategies.research.strategy_adapter_contracts import (
    ADAPTER_CONTRACTS,
    build_research_adapter_contract_report,
    get_research_adapter_contract,
)


def test_batch_contracts_bind_public_adapters_and_remain_deferred() -> None:
    report = build_research_adapter_contract_report(entrypoints)

    assert report["schema_version"] == "research_adapter_contracts.v1"
    assert report["synthetic_only"] is True
    assert report["real_market_data_read"] is False
    assert report["orders_allowed"] is False
    assert {row["profile"] for row in report["contracts"]} == set(ADAPTER_CONTRACTS)
    for row in report["contracts"]:
        assert row["adapter_available"] is True
        assert row["evidence_status"] == "DEFERRED"
        assert row["promotion_authorized"] is False


@pytest.mark.parametrize(
    "profile,lineage",
    [
        ("global_etf_rotation", "global_etf_rotation"),
        ("russell_top50_leader_rotation", "russell_top50_leader_rotation"),
        ("tecl_xlk_trend_income", "tecl_xlk_trend_income"),
        ("nasdaq_sp500_smart_dca", "smart_dca"),
    ],
)
def test_contract_preserves_lineage(profile: str, lineage: str) -> None:
    contract = get_research_adapter_contract(profile)
    assert contract.lineage == lineage
    assert contract.research_only is True
    assert contract.no_order is True


def test_unknown_profile_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown research adapter profile"):
        get_research_adapter_contract("not_registered")
