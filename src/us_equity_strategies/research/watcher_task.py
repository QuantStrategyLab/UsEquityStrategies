"""SOXL-owned policy over the shared bounded research task contract."""
from __future__ import annotations
import hashlib
from collections.abc import Mapping
from typing import Any
from quant_platform_kit.strategy_lifecycle.research_task import (
    ResearchTaskError, SCHEMA, canonical_json, calculate_task_sha256,
    build_strategy_diagnosis_task as _build, validate_strategy_diagnosis_task as _validate,
)

SOXL_WATCHER_CANDIDATE_ID = "soxl_soxx_core_only_p2_v3"
SOXL_WATCHER_STRATEGY_REPOSITORY = "QuantStrategyLab/UsEquityStrategies"
SOXL_WATCHER_UES_REVISION = "7756fe32585e85cf1d09a163203a02e3eee39fe1"
SOXL_WATCHER_P2_CONFIG_SHA256 = "ff8fa0acf4f175a7c40c3e1e6a3304ea2748b6b81c3797342085a4df3810ab4d"
SOXL_WATCHER_CONSUMER_REVISION = "b03ecbe4e0a7a0de22f298499f867a7039e4b60a"
SOXL_WATCHER_QPK_REVISION = "3acab1923a97b805b077c85c6c19657be0143bac"
SOXL_WATCHER_PARAMETER_BOUNDS = {
    "parameter_key": "blend_gate_mid_soxl_weight",
    "parameter_values": [0.65, 0.60, 0.55],
    "cost_bps": [5.0, 10.0, 15.0],
    "development_cutoff": "2025-07-31",
    "consumer_revision": SOXL_WATCHER_CONSUMER_REVISION,
    "strategy_revision": SOXL_WATCHER_UES_REVISION,
    "quant_platform_kit_revision": SOXL_WATCHER_QPK_REVISION,
}


SOXL_WATCHER_PARAMETER_BOUNDS_SHA256 = hashlib.sha256(
    canonical_json(SOXL_WATCHER_PARAMETER_BOUNDS).encode("utf-8")
).hexdigest()


def _is_exact_soxl_watcher_target(
    *, candidate_id: object, candidate_kind: object, domain: object,
    strategy_repository: object, evidence: Mapping[str, str],
) -> bool:
    return (
        candidate_id == SOXL_WATCHER_CANDIDATE_ID
        and candidate_kind == "individual"
        and domain == "us_equity"
        and strategy_repository == SOXL_WATCHER_STRATEGY_REPOSITORY
        and evidence.get("strategy_revision") == SOXL_WATCHER_UES_REVISION
        and evidence.get("p2_config_digest") == SOXL_WATCHER_P2_CONFIG_SHA256
    )


def build_strategy_diagnosis_task(**kwargs) -> dict[str, Any]:
    return bind_watcher_policy(_build(**kwargs))


def bind_watcher_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    task = _validate(value)
    target = task["target"]
    evidence = {**task["evidence"], "strategy_revision": target["strategy_revision"]}
    if _is_exact_soxl_watcher_target(
        candidate_id=target["candidate_id"], candidate_kind=target["candidate_kind"],
        domain=target["domain"], strategy_repository=target["repository"], evidence=evidence,
    ):
        task["experiment"]["parameter_bounds_sha256"] = SOXL_WATCHER_PARAMETER_BOUNDS_SHA256
        task["task_sha256"] = calculate_task_sha256(task)
    return task


def validate_strategy_diagnosis_task(value: Mapping[str, Any]) -> dict[str, Any]:
    task = _validate(value, allowed_parameter_bounds=frozenset({None, SOXL_WATCHER_PARAMETER_BOUNDS_SHA256}))
    target = task["target"]
    evidence = {**task["evidence"], "strategy_revision": target["strategy_revision"]}
    if task["experiment"]["parameter_bounds_sha256"] is not None and not _is_exact_soxl_watcher_target(
        candidate_id=target["candidate_id"], candidate_kind=target["candidate_kind"],
        domain=target["domain"], strategy_repository=target["repository"], evidence=evidence,
    ):
        raise ResearchTaskError("parameter bounds are not bound to the approved SOXL target")
    return task
