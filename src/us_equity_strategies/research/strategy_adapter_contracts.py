"""Small, research-only registry for strategies awaiting real data evidence.

This registry is deliberately metadata-only.  It verifies that a strategy has a
stable public adapter name without invoking a broker, loading market data, or
granting promotion authority.  Daily runners can use the report to keep
unverified strategies explicitly ``DEFERRED`` instead of treating an adapter
scaffold as completed P0--P6 evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ResearchAdapterContract:
    profile: str
    lineage: str
    public_adapter: str
    evidence_status: str = "DEFERRED"
    research_only: bool = True
    no_order: bool = True


_CONTRACTS: tuple[ResearchAdapterContract, ...] = (
    ResearchAdapterContract(
        profile="global_etf_rotation",
        lineage="global_etf_rotation",
        public_adapter="global_etf_rotation_entrypoint",
    ),
    ResearchAdapterContract(
        profile="russell_top50_leader_rotation",
        lineage="russell_top50_leader_rotation",
        public_adapter="russell_top50_leader_rotation_entrypoint",
    ),
    ResearchAdapterContract(
        profile="tecl_xlk_trend_income",
        lineage="tecl_xlk_trend_income",
        public_adapter="tecl_xlk_trend_income_entrypoint",
    ),
    ResearchAdapterContract(
        profile="nasdaq_sp500_smart_dca",
        lineage="smart_dca",
        public_adapter="nasdaq_sp500_smart_dca_entrypoint",
    ),
)

ADAPTER_CONTRACTS = {contract.profile: contract for contract in _CONTRACTS}


def build_research_adapter_contract_report(
    adapter_namespace: object,
) -> dict[str, object]:
    """Return a deterministic, side-effect-free readiness report.

    The caller supplies the imported entrypoint module.  We only check that
    the named public callable exists; invocation belongs to a separate
    synthetic test runner and must provide an explicitly synthetic context.
    """

    rows: list[dict[str, object]] = []
    for contract in _CONTRACTS:
        adapter = getattr(adapter_namespace, contract.public_adapter, None)
        rows.append(
            {
                "profile": contract.profile,
                "lineage": contract.lineage,
                "public_adapter": contract.public_adapter,
                "adapter_available": callable(adapter),
                "evidence_status": contract.evidence_status,
                "research_only": contract.research_only,
                "no_order": contract.no_order,
                "promotion_authorized": False,
            }
        )
    return {
        "schema_version": "research_adapter_contracts.v1",
        "synthetic_only": True,
        "real_market_data_read": False,
        "orders_allowed": False,
        "contracts": rows,
    }


def get_research_adapter_contract(profile: str) -> ResearchAdapterContract:
    """Return one contract or fail closed for an unknown profile."""

    try:
        return ADAPTER_CONTRACTS[profile]
    except KeyError as exc:
        raise ValueError(f"unknown research adapter profile: {profile}") from exc


__all__ = [
    "ADAPTER_CONTRACTS",
    "ResearchAdapterContract",
    "build_research_adapter_contract_report",
    "get_research_adapter_contract",
]
