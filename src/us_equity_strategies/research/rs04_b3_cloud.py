"""Bounded cloud qualification for RS-04 B3; aggregate artifacts only.

The full-rule callback exists; effective configuration, historical external
context and recorded-live parity remain unbound. Never substitutes core-only.
It reads ONLY existing input manifest metadata in a GitHub-hosted cloud runner;
no pricing object, return path, broker, provider credential, or IAM mutation.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
import hashlib
import json
import os
import platform
import re
import subprocess
from typing import Callable, Mapping, Sequence

from .rs04_b3_config import CONFIG, config_identity

DEFAULT_INPUT_ROOT = "gs://research-inputs/research/v2/input/"
MAX_MANIFEST_BYTES = 1_048_576
RS04_ARCHIVE_SHA256 = "b5a3dd40cb0d4260fb15492108bde7ab544ac0d191807943637b8216a6808482"


def input_root() -> str:
    """One configured research root; no bucket discovery or fallback dataset."""
    root = os.environ.get("RS04_INPUT_ROOT") or DEFAULT_INPUT_ROOT
    if not re.fullmatch(r"gs://[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]/research/v2/input/", root):
        raise ValueError("INPUT_ROOT_REJECTED")
    return root


def _manifest_allowed(uri: str) -> bool:
    return re.fullmatch(re.escape(input_root())+r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}/manifest\.json", uri) is not None


def walk_forward_plan(observations: int, *, feature_warmup: int) -> dict:
    """Half-open indices; all complete folds retained, no holdout observations."""
    folds, train_start = [], feature_warmup
    while True:
        train_end = train_start+CONFIG.train
        validation_start = train_end+CONFIG.purge
        validation_end = validation_start+CONFIG.validation
        test_start = validation_end+CONFIG.embargo
        test_end = test_start+CONFIG.test
        if test_end > observations:
            break
        folds.append(dict(train_start=train_start, train_end=train_end,
                          validation_start=validation_start, validation_end=validation_end,
                          test_start=test_start, test_end=test_end))
        train_start += CONFIG.test
    return {"status": "PLANNED" if len(folds) >= CONFIG.min_folds else "INSUFFICIENT_COVERAGE",
            "folds": folds, "minimum_folds": CONFIG.min_folds, "indices": "HALF_OPEN",
            "selection": "FIXED_B3_NO_PARAMETER_SELECTION",
            "state": "CONTINUOUS_BOOK_PEAK_EPISODES_AND_COSTS_REQUIRED",
            "uncovered_tail": observations-folds[-1]["test_end"] if folds else observations,
            "executed": False}


def inspect_manifest(manifest: Mapping, identity: Mapping) -> dict:
    """Presence/shape inspection is not permission, PIT proof or full validation."""
    schema = manifest.get("schema")
    recognised = schema == "qsl.research.price_snapshot.v2"
    symbols = manifest.get("symbols", [])
    return {"status": "METADATA_INSPECTED", "generation": identity.get("generation"),
            "sha256": identity.get("sha256"), "recognised_price_snapshot_v2": recognised,
            "declares_alpaca_sip": manifest.get("provider") == "alpaca" and manifest.get("feed") == "sip",
            "declares_raw": manifest.get("adjustment") == "raw",
            "required_symbol_coverage_present": isinstance(symbols, list) and
                set(("SOXL", "SOXX", "QQQ", "TQQQ", "QQQM", "BOXX")).issubset(symbols),
            "raw_plus_actions_qualified": False,
            "live_baseline_qualified": False,
            "reason": "PRICE_MANIFEST_DOES_NOT_ATTEST_CORPORATE_ACTIONS_PIT_OR_LIVE_BASELINE"}


def _cloud_environment() -> bool:
    return (platform.system() == "Linux" and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_OS") == "Linux"
            and os.environ.get("GITHUB_REPOSITORY") == "QuantStrategyLab/UsEquityStrategies"
            and os.environ.get("GITHUB_REF") == "refs/heads/main"
            and os.environ.get("GITHUB_RUN_ATTEMPT") == "1")


def read_cloud_manifest(uri: str) -> tuple[dict, dict]:
    """Existing WIF Cloud SDK; exact generation read, no lists or writes.

    Native diagnostics remain captured and are never included in an artifact.
    No retries after a failed read; each configured manifest is tried once.
    """
    if not _cloud_environment():
        raise ValueError("CLOUD_ONLY")
    if not _manifest_allowed(uri):
        raise ValueError("SOURCE_PREFIX_REJECTED")
    proc = subprocess.run(["gcloud", "storage", "objects", "describe", uri, "--format=json", "--quiet"],
                          capture_output=True, timeout=45, check=True)
    meta = json.loads(proc.stdout)
    generation, size = str(meta.get("generation", "")), int(meta.get("size", 0))
    if not re.fullmatch(r"[1-9][0-9]*", generation) or not 0 < size <= MAX_MANIFEST_BYTES:
        raise ValueError("MANIFEST_METADATA_REJECTED")
    body = subprocess.run(["gcloud", "storage", "cat", uri+"#"+generation, "--quiet"],
                          capture_output=True, timeout=45, check=True).stdout
    if len(body) != size:
        raise ValueError("MANIFEST_SIZE_MISMATCH")
    manifest = json.loads(body)
    if not isinstance(manifest, dict):
        raise ValueError("MANIFEST_INVALID")
    return manifest, {"generation": generation, "sha256": hashlib.sha256(body).hexdigest()}


def _empty_metrics() -> dict:
    return {"status": "NOT_COMPUTED", "cagr": None, "max_drawdown": None, "sharpe": None,
            "sortino": None, "calmar": None, "giveback": None, "turnover_one_side": None,
            "regime_stats": None, "combined_exposure": None,
            "deflated_sharpe": None, "deflated_sharpe_status": "UNCOMPUTABLE",
            "reason": "LIVE_AS_IS_BASELINE_AND_QUALIFIED_RAW_ACTION_INPUTS_UNAVAILABLE"}


def cloud_report(*, execute: bool, cloud: bool, today: date, sources: Sequence[str],
                 reader: Callable | None = read_cloud_manifest, auth_ok: bool = True,
                 synthetic_ok: bool = True) -> dict:
    """Attempt qualification, preserving every missing condition, no fake results."""
    blockers, metadata = [], []
    if execute and not cloud:
        blockers.append("CLOUD_ONLY")
    if execute and not synthetic_ok:
        blockers.append("SYNTHETIC_ACCEPTANCE_NOT_PASSED")
    if execute and synthetic_ok and not auth_ok:
        blockers.append("EXISTING_WIF_AUTHENTICATION_UNAVAILABLE")
    if execute and cloud and auth_ok and synthetic_ok:
        if not sources:
            blockers.append("APPROVED_INPUT_MANIFEST_NOT_BOUND")
        for uri in sources:
            if not _manifest_allowed(uri):
                metadata.append({"status": "REJECTED", "reason": "SOURCE_PREFIX_REJECTED"})
                continue
            try:
                manifest, identity = reader(uri)
                metadata.append(inspect_manifest(manifest, identity))
            except Exception:
                metadata.append({"status": "UNAVAILABLE", "reason": "CLOUD_MANIFEST_READ_DENIED_OR_UNAVAILABLE"})
    # These cannot be cleared by untrusted metadata self-attestations.
    blockers.extend(("LIVE_AS_IS_BASELINE_NOT_BOUND", "EFFECTIVE_MERGED_LIVE_CONFIG_NOT_BOUND",
                     "HISTORICAL_EXTERNAL_CONTEXT_NOT_BOUND",
                     "RAW_OHLC_ACTIONS_LICENSE_PIT_NOT_BOUND", "SELL_REGULATORY_FEES_NOT_BOUND",
                     "CUMULATIVE_TRIAL_UNIVERSE_NOT_ATTESTED"))
    holdout = "NOT_YET_OBSERVABLE" if today <= date.fromisoformat(CONFIG.holdout_end) else "ISOLATION_NOT_ATTESTED"
    blockers.append("HOLDOUT_"+holdout)
    from .rs04_b3_live import binding_report
    return {"status": "PARKED" if execute else "PLAN_ONLY", "research_only": True, "synthetic": False,
            "promotion_eligible": False, "live_ready": False, "no_order": True, "budget_effect": "NONE",
            "config_sha256": config_identity(), "registration": asdict(CONFIG),
            "source_metadata": metadata, "blockers": blockers,
            "real_bars_read": 0, "real_daily_returns_exported": 0, "real_replay_executed": False,
            "baseline_identity": "CURRENT_LIVE_RULES_AS_IS_REQUIRED_NO_CORE_SUBSTITUTION",
            "live_rule_adapter": {"implementation": "FULL_EXISTING_STRATEGY_FUNCTIONS",
                "qualified": False, "bindings": {p: binding_report(p) for p in ("SOXL", "TQQQ")},
                "parity": {"status": "UNAVAILABLE", "recorded_live_decisions": 0}},
            "approved_archive": {"expected_manifest_sha256": RS04_ARCHIVE_SHA256,
                "matching_metadata_found": any(x.get("sha256") == RS04_ARCHIVE_SHA256 for x in metadata)},
            "baseline_vs_b3": {p: {str(c): {k: _empty_metrics() for k in ("baseline", "sizing_control", "b3", "de_risk_only")}
                                    for c in CONFIG.cost_multipliers} for p in ("SOXL", "TQQQ")},
            "walk_forward": {"status": "NOT_RUN", "train": CONFIG.train, "validation": CONFIG.validation,
                             "test": CONFIG.test, "purge": CONFIG.purge, "embargo": CONFIG.embargo,
                             "minimum_folds": CONFIG.min_folds, "continuous_ledger": True},
            "holdout": {"status": holdout, "provisional": True, "start": CONFIG.holdout_start,
                        "end": CONFIG.holdout_end, "opened": False},
            "pseudo_holdout": {"status": "NOT_COMPUTED_INPUT_NOT_BOUND", "start": "2024-08-26",
                "end": CONFIG.development_end, "months": 24,
                "anchor": "DOCUMENTED_ARCHIVE_END_NOT_CURRENT_DATE",
                "weaker_evidence": True, "previously_seen_development": True, "untouched": False,
                "parameters_frozen_before_read": True, "excluded_from_wfa": True,
                "current_trailing_24_month_coverage": "NOT_AVAILABLE_ARCHIVE_ENDS_2026_08_25"},
            "trial_journal": {"attempt": "PARKED_QUALIFICATION", "candidate_identities": 4,
                              "costs_are_paired_scenarios": True, "historical_universe": "NOT_ATTESTED"},
            "acceptance": "INSUFFICIENT_EVIDENCE", "recommend_shadow": False}


def workflow_cloud_report(execute: bool) -> dict:
    # Only this module performs the boundary check; a CLI flag cannot override it.
    cloud = _cloud_environment()
    sources = [os.environ[name].rstrip("/")+"/manifest.json" for name in
               ("UX1_RAW_GCS_PREFIX", "UX1_R6_GCS_PREFIX") if os.environ.get(name)]
    archive = os.environ.get("RS04_ARCHIVE_PREFIX", "").rstrip("/")
    if archive:
        sources = [archive+"/manifest.json"]
    result = cloud_report(execute=execute, cloud=cloud, today=date.today(), sources=sources,
                          auth_ok=os.environ.get("RS04_AUTH_OUTCOME") == "success",
                          synthetic_ok=os.environ.get("RS04_SYNTHETIC_OUTCOME") == "success")
    sha = os.environ.get("GITHUB_SHA", "")
    result["source_revision"] = sha if re.fullmatch(r"[0-9a-f]{40}", sha) else None
    return result
