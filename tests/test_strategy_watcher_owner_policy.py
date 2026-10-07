from unittest.mock import patch
from types import SimpleNamespace
from quant_platform_kit.strategy_lifecycle.research_task import build_strategy_diagnosis_task
from quant_platform_kit.strategy_lifecycle.watch.strategy_watch import research_task_source_snapshot
from us_equity_strategies.research.strategy_watcher import build_task
from us_equity_strategies.research.watcher_task import (
    SOXL_WATCHER_PARAMETER_BOUNDS_SHA256, SOXL_WATCHER_P2_CONFIG_SHA256,
    SOXL_WATCHER_UES_REVISION, validate_strategy_diagnosis_task,
)


def generic_task(candidate):
    return build_strategy_diagnosis_task(event_key="123456789abc", created_at="2026-10-08T00:00:00Z",
        candidate_id=candidate, candidate_kind="individual", domain="us_equity",
        strategy_repository="QuantStrategyLab/UsEquityStrategies",
        evidence={"p1_input_digest":"0"*64, "p2_config_digest":SOXL_WATCHER_P2_CONFIG_SHA256,
                  "p3_evidence_id":"c"*64, "strategy_revision":SOXL_WATCHER_UES_REVISION,
                  "producer_revision":"e"*40})


def test_source_projection_only_binds_bounds_to_the_frozen_soxl_target():
    finding = SimpleNamespace(snapshot=SimpleNamespace(generated_at="2026-10-08T00:00:00Z"))
    for candidate, expected in [("soxl_soxx_core_only_p2_v3", SOXL_WATCHER_PARAMETER_BOUNDS_SHA256), ("another-strategy", None)]:
        with patch("us_equity_strategies.research.strategy_watcher.finding_to_research_task", return_value=generic_task(candidate)):
            result = research_task_source_snapshot([finding], context_available=True,
                computed_at="2026-10-08T00:00:00Z", task_builder=build_task)
        task = validate_strategy_diagnosis_task(result["tasks"][0])
        assert task["experiment"]["parameter_bounds_sha256"] == expected
        assert task["authority"]["no_order"] is True
