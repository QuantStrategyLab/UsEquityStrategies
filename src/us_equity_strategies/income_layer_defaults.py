from __future__ import annotations

from copy import deepcopy

INCOME_LAYER_RATIO_MODE = "log_total_drawdown_budget"

GLOBAL_ETF_ROTATION_PROFILE = "global_etf_rotation"
TQQQ_GROWTH_INCOME_PROFILE = "tqqq_growth_income"
SOXL_SOXX_TREND_INCOME_PROFILE = "soxl_soxx_trend_income"
TECL_XLK_TREND_INCOME_PROFILE = "tecl_xlk_trend_income"
RUSSELL_TOP50_LEADER_ROTATION_PROFILE = "russell_top50_leader_rotation"

INCOME_LAYER_LIVE_VALIDATION_EVIDENCE: dict[str, dict[str, object]] = {
    GLOBAL_ETF_ROTATION_PROFILE: {
        "status": "live",
        "evidence_status": "validated",
        "research_doc": "docs/research/income_layer_design.zh-CN.md",
        "summary": "Defensive ETF rotation income sleeve calibrated with drawdown-budget defaults.",
    },
    TQQQ_GROWTH_INCOME_PROFILE: {
        "status": "live",
        "evidence_status": "validated",
        "research_doc": "docs/research/income_layer_design.zh-CN.md",
        "artifact": "UsEquitySnapshotPipelines/data/output/levered_income_layer_candidate_compare_2026-05-26/",
        "summary": "TQQQ income sleeve selected from backtested drawdown-budget candidates.",
    },
    SOXL_SOXX_TREND_INCOME_PROFILE: {
        "status": "live",
        "evidence_status": "validated",
        "research_doc": "docs/research/income_layer_design.zh-CN.md",
        "artifact": "UsEquitySnapshotPipelines/data/output/soxl_soxx_trend_income_archive_2026-05-04/summary.csv",
        "summary": "SOXL/SOXX income sleeve validated by archived replay and later drawdown-budget calibration.",
    },
    RUSSELL_TOP50_LEADER_ROTATION_PROFILE: {
        "status": "live",
        "evidence_status": "validated",
        "research_doc": "docs/research/income_layer_design.zh-CN.md",
        "summary": "Leader-rotation income sleeve uses the same validated drawdown-budget curve.",
    },
}

INCOME_LAYER_RESEARCH_VALIDATION_EVIDENCE: dict[str, dict[str, object]] = {
    TECL_XLK_TREND_INCOME_PROFILE: {
        "status": "research",
        "evidence_status": "rejected_vs_live_leveraged",
        "research_doc": "UsEquitySnapshotPipelines/docs/tecl-xlk-optimization-research.md",
        "research_doc_zh": "UsEquitySnapshotPipelines/docs/tecl-xlk-optimization-research.zh-CN.md",
        "artifact": "UsEquitySnapshotPipelines/data/output/tecl_xlk_trend_income_research_20260628/",
        "summary": (
            "TECL/XLK research sleeve archived after failing promotion gate versus live TQQQ and SOXL; "
            "not wired into runtime defaults."
        ),
    },
}

INCOME_LAYER_DEFAULT_CONFIGS: dict[str, dict[str, object]] = {
    GLOBAL_ETF_ROTATION_PROFILE: {
        "income_layer_enabled": True,
        "income_layer_start_usd": 500000.0,
        "income_layer_max_ratio": 0.15,
        "income_layer_activation_band_ratio": 0.10,
        "income_layer_ratio_mode": INCOME_LAYER_RATIO_MODE,
        "income_layer_core_stress_drawdown_ratio": 0.30,
        "income_layer_income_stress_drawdown_ratio": 0.08,
        "income_layer_base_drawdown_budget_ratio": 0.30,
        "income_layer_min_drawdown_budget_ratio": 0.267,
        "income_layer_drawdown_budget_decay_per_double": 0.015,
        "income_layer_allocations": {
            "SCHD": 0.40,
            "DGRO": 0.25,
            "SGOV": 0.30,
            "SPYI": 0.05,
        },
    },
    TQQQ_GROWTH_INCOME_PROFILE: {
        "income_layer_enabled": True,
        "income_layer_start_usd": 250000.0,
        "income_layer_max_ratio": 0.55,
        "income_layer_activation_band_ratio": 0.20,
        "income_layer_ratio_mode": INCOME_LAYER_RATIO_MODE,
        "income_layer_core_stress_drawdown_ratio": 0.45,
        "income_layer_income_stress_drawdown_ratio": 0.08,
        "income_layer_base_drawdown_budget_ratio": 0.45,
        "income_layer_min_drawdown_budget_ratio": 0.25,
        "income_layer_drawdown_budget_decay_per_double": 0.05,
        "income_layer_allocations": {
            "SCHD": 0.30,
            "DGRO": 0.20,
            "SGOV": 0.40,
            "SPYI": 0.08,
            "QQQI": 0.02,
        },
    },
    SOXL_SOXX_TREND_INCOME_PROFILE: {
        "income_layer_enabled": True,
        "income_layer_start_usd": 150000.0,
        "income_layer_max_ratio": 0.95,
        "income_layer_activation_band_ratio": 0.20,
        "income_layer_ratio_mode": INCOME_LAYER_RATIO_MODE,
        "income_layer_core_stress_drawdown_ratio": 0.45,
        "income_layer_income_stress_drawdown_ratio": 0.06,
        "income_layer_base_drawdown_budget_ratio": 0.45,
        "income_layer_min_drawdown_budget_ratio": 0.25,
        "income_layer_drawdown_budget_decay_per_double": 0.05,
        "income_layer_allocations": {
            "SCHD": 0.15,
            "DGRO": 0.10,
            "SGOV": 0.70,
            "SPYI": 0.04,
            "QQQI": 0.01,
        },
    },
    TECL_XLK_TREND_INCOME_PROFILE: {
        "income_layer_enabled": True,
        "income_layer_start_usd": 150000.0,
        "income_layer_max_ratio": 0.95,
        "income_layer_activation_band_ratio": 0.20,
        "income_layer_ratio_mode": INCOME_LAYER_RATIO_MODE,
        "income_layer_core_stress_drawdown_ratio": 0.45,
        "income_layer_income_stress_drawdown_ratio": 0.06,
        "income_layer_base_drawdown_budget_ratio": 0.45,
        "income_layer_min_drawdown_budget_ratio": 0.25,
        "income_layer_drawdown_budget_decay_per_double": 0.05,
        "income_layer_allocations": {
            "SCHD": 0.15,
            "DGRO": 0.10,
            "SGOV": 0.70,
            "SPYI": 0.04,
            "QQQI": 0.01,
        },
    },
    RUSSELL_TOP50_LEADER_ROTATION_PROFILE: {
        "income_layer_enabled": True,
        "income_layer_start_usd": 300000.0,
        "income_layer_max_ratio": 0.25,
        "income_layer_activation_band_ratio": 0.15,
        "income_layer_ratio_mode": INCOME_LAYER_RATIO_MODE,
        "income_layer_core_stress_drawdown_ratio": 0.35,
        "income_layer_income_stress_drawdown_ratio": 0.08,
        "income_layer_base_drawdown_budget_ratio": 0.35,
        "income_layer_min_drawdown_budget_ratio": 0.2825,
        "income_layer_drawdown_budget_decay_per_double": 0.020,
        "income_layer_allocations": {
            "SCHD": 0.45,
            "DGRO": 0.30,
            "SGOV": 0.25,
        },
    },
}


def income_layer_default_config(profile: str) -> dict[str, object]:
    return deepcopy(INCOME_LAYER_DEFAULT_CONFIGS[profile])


__all__ = [
    "INCOME_LAYER_DEFAULT_CONFIGS",
    "INCOME_LAYER_LIVE_VALIDATION_EVIDENCE",
    "INCOME_LAYER_RESEARCH_VALIDATION_EVIDENCE",
    "INCOME_LAYER_RATIO_MODE",
    "income_layer_default_config",
]
