"""Bounded UBPS study registration/qualification; no fabricated financial run.

The data/consumer binding is unavailable. This module exports gaps, never
converts fixtures, a catalog, or an input path into historical evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Sequence

from .rs04_b3_cloud import INPUT_ROOT, RS04_ARCHIVE_SHA256, _cloud_environment
from .rs04_b3_live import binding_report, config_sha256, default_config
from .soxl_v7_derisk import CANDIDATE_ID, variant_config
from .soxl_v7_derisk_history import STRESS_PERIODS

UBPS_SHA256 = "2bf66aa8f0f7c4c9a63824f2428cd10c16a6d436f32b2da035e1c0fe7a8d16ba"
QSP_CATALOG_IDS = ("crisis_response_shadow", "macro_risk_governor", "market_regime_control",
                   "panic_reversal_shadow", "taco_rebound_shadow")
DIMENSIONS = {"D": 2, "C": 2, "S": 2, "M": 1, "O": 1, "L": 1, "V": 1, "R": 1, "H": 1, "G": 2}
METRICS = ("gross_and_net_cagr", "net_log_growth", "sharpe_excess_rf", "sortino", "calmar",
           "mdd", "drawdown_duration_sessions_and_days", "giveback", "cvar95_99_daily_weekly",
           "worst_day_week_month", "one_side_turnover", "nominal_effective_and_dollar_exposure",
           "cash", "transaction_financing_and_service_costs", "confidence_intervals",
           "dsr_pbo", "capacity", "regime_stats")


def plugin_inventory(extra_ids: Sequence[str] = ()) -> dict:
    from quant_platform_kit.common.strategy_plugins import DEFAULT_STRATEGY_PLUGIN_DEFINITIONS
    from ..ai_extensions import AI_EXTENSION_DEFAULT_CONFIG
    from ..option_overlay import OPTION_OVERLAY_RESEARCH_CANDIDATES
    default_ids = set(DEFAULT_STRATEGY_PLUGIN_DEFINITIONS)
    catalog_ids = set(QSP_CATALOG_IDS)
    catalog_status = "FROZEN_DOCUMENT_CATALOG_ONLY"
    try:
        from quant_strategy_plugins.plugin_catalog import build_plugin_catalog
    except ImportError:
        catalog_status = "INSTALLED_QSP_CATALOG_UNAVAILABLE"
    else:
        catalog_ids.update(e["plugin_id"] for e in build_plugin_catalog()["entries"])
        catalog_status = "INSTALLED_QSP_CATALOG_ENUMERATED"
    known = (default_ids | catalog_ids | set(AI_EXTENSION_DEFAULT_CONFIG["modules"])
             | set(OPTION_OVERLAY_RESEARCH_CANDIDATES) | {"income_layer", "qqq_price_regime_observer"})
    unknown = set(extra_ids)-known
    ids = sorted(known | set(extra_ids))
    rows = []
    for name in ids:
        scores = {d: {"status": "missing", "reason": "NO_BOUND_HISTORICAL_JOINT_PATH_EVIDENCE"}
                  for d in DIMENSIONS}
        scores["R"] = {"status": "partial", "reason": "STATIC_ID_ONLY_EFFECTIVE_VERSION_AND_ORDER_UNBOUND"}
        rows.append({"plugin_id": name, "in_default_registry": name in default_ids,
            "in_qsp_catalog": name in catalog_ids, "unresolved": name in unknown,
            "host_compatibility": "NOT_ATTESTED_BY_EFFECTIVE_BINDING",
            "scores": scores, "gap_severity": 13.5/14})
    return {"status": "INCOMPLETE", "reason": "CUSTOM_REGISTRY_PLATFORM_EXTENSIONS_AND_EFFECTIVE_BINDING_UNAVAILABLE",
            "catalog_status": catalog_status, "expected": sorted(known), "discovered": ids,
            "unresolved": sorted(unknown), "coverage_difference": sorted(unknown),
            "custom_registry_completeness": "UNVERIFIED", "rows": rows,
            "score_denominator": 14, "notification_and_noop_included": True}


def ablation_plan(ids: Sequence[str]) -> list[dict]:
    """Names are requirements, not assertions that arbitrary combinations work.

    BASE disables only optional mounts, preserving native mandatory protection.
    Compatibility/order and effective consumer binding must be resolved before
    executing SINGLE/FULL_MINUS. No new position-changing consumer is invented.
    """
    labels = [("BASE", ()), ("FULL", tuple(ids))]
    for name in ids:
        labels.extend((("SINGLE_"+name, (name,)),
                       ("FULL_MINUS_"+name, tuple(i for i in ids if i != name))))
    return [{"arm": label, "requested_plugin_ids": list(selected),
        "status": "NOT_COMPARABLE", "reason": "EFFECTIVE_CONSUMER_COMPATIBILITY_ORDER_AND_PIT_INPUTS_NOT_BOUND",
        "baseline": "FROZEN_FULL_LIVE_BASELINE_CEILING_ALWAYS_RETAINED",
        "mandatory_protection_preserved": True,
        "joint_scores": {d: {"status": "missing", "reason": "JOINT_REPLAY_NOT_EXECUTED"} for d in DIMENSIONS},
        "cost_multipliers": [1., 2.], "full_synthetic_extended": "NOT_COMPARABLE",
        "real_only_independent_cash_start": "NOT_COMPARABLE",
        "noop_requirement": "TARGET_INTENT_HOLDING_CASH_FEES_EQUAL_EXCEPT_BOUND_SERVICE_FEES"}
        for label, selected in labels]


def registration() -> dict:
    base = default_config("SOXL")
    return {"candidate_id": CANDIDATE_ID, "variant": "STRICT_DERISK_ONLY", "market": "US", "frequency": "daily",
        "scope": ["core", "income", "all_configurable_plugins", "options_if_effectively_configured", "execution"],
        "ubps_v2_sha256": UBPS_SHA256, "parent_candidate": "soxl_soxx_core_only_p2_v7_longterm_compounding_cash_reserve",
        "repo_reference_baseline_config_sha256": config_sha256(base),
        "repo_reference_variant_config_sha256": config_sha256(variant_config(base)),
        "config_reference_is_effective_live": False, "cost_multipliers": [1., 2.],
        "decision_fill": "COMPLETED_T_MINUS_1_CLOSE_NEXT_CLOSE_SIMULATION",
        "live_clock_parity": "UNVERIFIED", "seed": 20261015, "candidate_trials": 1,
        "wfa": {"train_start": "2010-01-01", "initial_train_end": "2018-12-31",
            "test_years": list(range(2019, 2025)), "minimum_folds": 3, "purge_sessions": 5, "embargo_sessions": 5,
            "purge_basis": "FIXED_RULES_NO_FORWARD_LABELS_ONE_SESSION_INPUT_LAG_FIVE_SESSION_SEPARATION",
            "selection": "FIXED_PARAMETERS_NO_TEST_REFIT", "pseudo_holdout_overlap": "EXPLICIT_BLOCK_NOT_SILENT_DELETE"},
        "pseudo_holdout": {"months": 24, "anchor": "LAST_QUALIFIED_ARCHIVED_SESSION",
            "parameters_frozen_before_view": True, "excluded_from_training": True,
            "untouched": False, "previously_seen_development": True, "weaker_evidence": True},
        "synthetic_history": {"train_sessions": 504, "validation_min_sessions": 252,
            "validation": "ALL_REMAINING_PLUS_EVERY_COMPLETE_252_BLOCK_AND_CRISIS_INDEX_RATE_REGIMES",
            "calibration_thresholds": "UBPS_V2_SECTION_8_2_2_UNCHANGED",
            "underlying": "PER_PERIOD_SOX_OFFICIAL_INDEX_OR_CALIBRATED_SOXX_NOT_ONE_MAPPING_ALL_YEARS",
            "financing_spread_stress_bps": [0, 100, 300], "internal_drag_multipliers": [1, 2],
            "splice": "PRE_ANCHOR_SYNTHETIC_ANCHOR_BRIDGE_POST_ANCHOR_REAL_ONLY",
            "real_only": "INDEPENDENT_CASH_START_NOT_EXTENDED_CURVE_TAIL",
            "execution_limitation": "CLOSE_RETURN_EXTENSION_CANNOT_SUPPLY_OHLC_VOLUME_OPTIONS_OR_PLUGIN_PIT"},
        "required_metrics": list(METRICS), "financial_acceptance": "LOSS_AND_RETURN_SACRIFICE_BUDGET_NOT_BOUND",
        "shadow": {"draft_only_after_research_pass": True, "execution_parity_target_sessions": 20,
            "does_not_replace_original_v7_252_sessions_or_human_review": True},
        "research_only": True, "no_order": True, "live_ready": False}


def qualification_report(*, cloud: bool, archive_prefix: str = "", extra_ids: Sequence[str] = ()) -> dict:
    if archive_prefix and not re.fullmatch(re.escape(INPUT_ROOT)+r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}", archive_prefix):
        raise ValueError("ARCHIVE_PREFIX_REJECTED")
    inventory, spec = plugin_inventory(extra_ids), registration()
    blockers = ["RS04_CLOUD_ARCHIVE_LOCATION_REQUIRED" if not archive_prefix else "RS04_ARCHIVE_GENERATION_AND_DIGEST_UNVERIFIED",
        "EFFECTIVE_PLATFORM_MERGED_LIVE_CONFIG_AND_HASH_METHOD_NOT_ARCHIVED",
        "RECORDED_LIVE_DECISION_PARITY_UNAVAILABLE", "HISTORICAL_PLUGIN_CONTEXT_BINDINGS_UNAVAILABLE",
        "LICENSED_RAW_ACTIONS_INCOME_SECURITIES_AND_EXECUTION_INPUTS_NOT_BOUND",
        "SOX_SOXX_HISTORY_RATES_FEES_INDEX_MAPPING_AND_NAV_BASIS_NOT_BOUND",
        "ALL_CONFIGURABLE_PLUGIN_COVERAGE_INCOMPLETE", "QUALIFIED_CLOUD_JOINT_REPLAY_NOT_BOUND",
        "LOSS_AND_RETURN_SACRIFICE_BUDGET_NOT_BOUND", "CUMULATIVE_TRIAL_UNIVERSE_NOT_ATTESTED"]
    if not cloud:
        blockers.insert(0, "CLOUD_EXECUTION_REQUIRED")
    return {"schema": "soxl_v7_derisk_ubps.v1", "status": "PARKED", "research_only": True,
        "no_order": True, "live_ready": False, "promotion_eligible": False, "shadow_draft_created": False,
        "real_replay_executed": False, "real_bars_read": 0, "raw_data_exported": False,
        "registration": spec, "registration_sha256": hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest(),
        "blockers": blockers, "baseline_binding": binding_report("SOXL"),
        "approved_archive": {"expected_manifest_sha256": RS04_ARCHIVE_SHA256,
            "locator_provided": bool(archive_prefix), "gcs_copy_verified": False,
            "documented_symbol_count": 6, "documented_start": "2021-12-01", "documented_end": "2026-08-25",
            "source": "docs/research/fractional_kelly_preregistration.zh-CN.md section 4.0",
            "cannot_cover": ["2000_2002", "2008", "2020", "income_universe", "historical_plugins"]},
        "plugin_inventory": inventory,
        "plugin_binding_manifest": {"status": "UNAVAILABLE", "custom_registry": "UNRESOLVED"},
        "ablation_report": ablation_plan(inventory["discovered"]),
        "calibration_report": {"status": "INCONCLUSIVE", "reason": "QUALIFIED_OVERLAP_AND_HISTORY_INPUTS_NOT_BOUND"},
        "stress_coverage_report": {year: {"start": start, "end": end, "status": "COVERAGE_BLOCKED",
            "underlying_exists": "NOT_VERIFIED", "input_available": False, "real_or_synthetic": "NOT_COMPUTED",
            "plugin_PIT_coverage": "UNAVAILABLE", "missing_reason": "QUALIFIED_INPUTS_AND_JOINT_LEDGER_UNAVAILABLE"}
            for year, (start, end) in STRESS_PERIODS.items()},
        "full_synthetic_extended_report": {"status": "NOT_COMPARABLE", "metrics": None},
        "real_only_report": {"status": "NOT_COMPARABLE", "metrics": None},
        "walk_forward": {"status": "NOT_RUN"}, "pseudo_holdout": {"status": "NOT_RUN", "untouched": False},
        "cost_stress_2x": {"status": "NOT_RUN"}, "acceptance": "INCONCLUSIVE_MISSING_EVIDENCE_NOT_FINANCIAL_FAIL"}


def workflow_report(archive_prefix: str = "") -> dict:
    if not _cloud_environment():
        raise ValueError("CLOUD_ONLY_MAIN_FIRST_ATTEMPT")
    # Qualification precedes authentication: there is no qualified path to
    # download prices yet. No secret/IAM/server/data client is imported here.
    return qualification_report(cloud=True, archive_prefix=archive_prefix)
