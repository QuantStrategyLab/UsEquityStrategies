"""Explicit runtime-selectable profiles for the US strategy package.

This is a selection boundary only.  It does not grant live authority; platform
policy, evidence and broker gates remain separate.
"""

RUNTIME_SELECTABLE_ALLOWLIST_V1 = frozenset(
    {
        "global_etf_rotation",
        "tqqq_growth_income",
        "soxl_soxx_trend_income",
        "russell_top50_leader_rotation",
        "nasdaq_sp500_smart_dca",
        "ibit_smart_dca",
    }
)


def get_runtime_selectable_profiles() -> frozenset[str]:
    return RUNTIME_SELECTABLE_ALLOWLIST_V1
