"""Bounded cloud qualification for RS-04 B3; aggregate artifacts only.

Existing available stores do not provide a qualified live-as-is replay adapter.
This preflight reports that limitation and never substitutes no_plugin_core.
It reads ONLY existing input manifest metadata in a GitHub-hosted cloud runner;
no pricing object, return path, broker, provider credential, or IAM mutation.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
import hashlib
import json
import os
import re
import subprocess
from typing import Callable, Mapping, Sequence

from .rs04_b3_config import CONFIG, config_identity

INPUT_ROOT = "gs://qsl-research-evidence-831478360303/research/v2/input/"
MANIFEST_PATTERN = re.compile(re.escape(INPUT_ROOT)+r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}/manifest\.json")
MAX_MANIFEST_BYTES = 1_048_576


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


def read_cloud_manifest(uri: str) -> tuple[dict, dict]:
    """Existing WIF Cloud SDK; exact generation read, no lists or writes.

    Native diagnostics remain captured and are never included in an artifact.
    No retries after a failed read; each configured manifest is tried once.
    """
    if not MANIFEST_PATTERN.fullmatch(uri):
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
                 reader: Callable | None = read_cloud_manifest, auth_ok: bool = True) -> dict:
    """Attempt qualification, preserving every missing condition, no fake results."""
    blockers, metadata = [], []
    if execute and not cloud:
        blockers.append("CLOUD_ONLY")
    if not auth_ok:
        blockers.append("EXISTING_WIF_AUTHENTICATION_UNAVAILABLE")
    if execute and cloud and auth_ok:
        if not sources:
            blockers.append("APPROVED_INPUT_MANIFEST_NOT_BOUND")
        for uri in sources:
            if not MANIFEST_PATTERN.fullmatch(uri):
                metadata.append({"status": "REJECTED", "reason": "SOURCE_PREFIX_REJECTED"})
                continue
            try:
                manifest, identity = reader(uri)
                metadata.append(inspect_manifest(manifest, identity))
            except Exception:
                metadata.append({"status": "UNAVAILABLE", "reason": "CLOUD_MANIFEST_READ_DENIED_OR_UNAVAILABLE"})
    # These cannot be cleared by untrusted metadata self-attestations.
    blockers.extend(("LIVE_AS_IS_BASELINE_NOT_BOUND", "QUALIFIED_LIVE_RULE_REPLAY_ADAPTER_MISSING",
                     "RAW_OHLC_ACTIONS_LICENSE_PIT_NOT_BOUND", "SELL_REGULATORY_FEES_NOT_BOUND",
                     "CUMULATIVE_TRIAL_UNIVERSE_NOT_ATTESTED"))
    holdout = "NOT_YET_OBSERVABLE" if today <= date.fromisoformat(CONFIG.holdout_end) else "ISOLATION_NOT_ATTESTED"
    blockers.append("HOLDOUT_"+holdout)
    return {"status": "PARKED" if execute else "PLAN_ONLY", "research_only": True, "synthetic": False,
            "promotion_eligible": False, "live_ready": False, "no_order": True, "budget_effect": "NONE",
            "config_sha256": config_identity(), "registration": asdict(CONFIG),
            "source_metadata": metadata, "blockers": blockers,
            "real_bars_read": 0, "real_daily_returns_exported": 0, "real_replay_executed": False,
            "baseline_identity": "CURRENT_LIVE_RULES_AS_IS_REQUIRED_NO_CORE_SUBSTITUTION",
            "baseline_vs_b3": {p: {str(c): {k: _empty_metrics() for k in ("baseline", "sizing_control", "b3")}
                                    for c in CONFIG.cost_multipliers} for p in ("SOXL", "TQQQ")},
            "walk_forward": {"status": "NOT_RUN", "train": CONFIG.train, "validation": CONFIG.validation,
                             "test": CONFIG.test, "purge": CONFIG.purge, "embargo": CONFIG.embargo,
                             "minimum_folds": CONFIG.min_folds, "continuous_ledger": True},
            "holdout": {"status": holdout, "provisional": True, "start": CONFIG.holdout_start,
                        "end": CONFIG.holdout_end, "opened": False},
            "trial_journal": {"attempt": "PARKED_QUALIFICATION", "candidate_identities": 2,
                              "costs_are_paired_scenarios": True, "historical_universe": "NOT_ATTESTED"},
            "acceptance": "INSUFFICIENT_EVIDENCE", "recommend_shadow": False}


def workflow_cloud_report(execute: bool) -> dict:
    # Only this module performs the boundary check; a CLI flag cannot override it.
    cloud = (os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("RUNNER_OS") == "Linux"
             and os.environ.get("GITHUB_REPOSITORY") == "QuantStrategyLab/UsEquityStrategies"
             and os.environ.get("GITHUB_REF") == "refs/heads/main"
             and os.environ.get("GITHUB_RUN_ATTEMPT") == "1")
    sources = [os.environ[name].rstrip("/")+"/manifest.json" for name in
               ("UX1_RAW_GCS_PREFIX", "UX1_R6_GCS_PREFIX") if os.environ.get(name)]
    result = cloud_report(execute=execute, cloud=cloud, today=date.today(), sources=sources,
                          auth_ok=(os.environ.get("RS04_AUTH_OUTCOME") == "success"
                                   and os.environ.get("RS04_SYNTHETIC_OUTCOME") == "success"))
    sha = os.environ.get("GITHUB_SHA", "")
    result["source_revision"] = sha if re.fullmatch(r"[0-9a-f]{40}", sha) else None
    return result
