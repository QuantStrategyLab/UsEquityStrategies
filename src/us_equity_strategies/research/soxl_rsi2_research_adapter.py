"""Bounded adapter from the existing UES RSI2 study to QPK research recovery."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import argparse
import json
from pathlib import Path
from typing import Any

from us_equity_strategies.research.soxl_core_optimization import (
    persist_rsi2_mean_reversion_result,
    run_soxl_rsi2_mean_reversion,
)
from us_equity_strategies.research.soxl_alpaca_input_adapter import (
    load_soxl_alpaca_input,
)
from us_equity_strategies.research.soxl_soxx_offline_input_contract import (
    OfflineInput,
    load_offline_input,
)
from us_equity_strategies.research.soxl_rsi2_promotion_runner import (
    SoxlRsi2PromotionBinding,
    promotion_binding_digest,
    validate_promotion_timing,
)


PROFILE = "soxl_rsi2_mean_reversion"
DOMAIN = "us_equity"
BASELINE_CANDIDATE = "UNSCALED_SMA200"
_CANDIDATE_COUNT = 4
_PARAM_SPACE_REVISION = "soxl_rsi2_candidates_v1"
_COST_MODEL_REVISION = "soxl_rsi2_C2_5_C5_10_STRESS_v1"
_VALIDATOR_REVISION = "soxl_rsi2_research_contract_v1"


class SoxlRsi2ResearchAdapterError(ValueError):
    """Sanitized invalid-input or unavailable-research failure."""


@dataclass(frozen=True)
class Rsi2OfflineInputPaths:
    manifest: Path
    artifact: Path
    readback: Path


def _manifest_provider(path: Path) -> str:
    try:
        value = json.loads(path.read_bytes().decode())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SoxlRsi2ResearchAdapterError("rsi2_input_manifest_invalid") from exc
    if not isinstance(value, dict) or value.get("schema") != "qsl.research.price_snapshot.v1":
        raise SoxlRsi2ResearchAdapterError("rsi2_input_manifest_invalid")
    provider = value.get("provider")
    if provider not in {"yahoo_chart", "alpaca_sip"}:
        raise SoxlRsi2ResearchAdapterError("rsi2_input_provider_unsupported")
    return provider


def load_rsi2_offline_input(paths: Rsi2OfflineInputPaths) -> OfflineInput:
    """Load one verified Yahoo or Alpaca UES input without translating bars."""
    if not isinstance(paths, Rsi2OfflineInputPaths):
        raise SoxlRsi2ResearchAdapterError("rsi2_input_paths_invalid")
    try:
        provider = _manifest_provider(paths.manifest)
        loader = load_offline_input if provider == "yahoo_chart" else load_soxl_alpaca_input
        return loader(paths.manifest, paths.artifact, paths.readback)
    except Exception as exc:
        if isinstance(exc, SoxlRsi2ResearchAdapterError):
            raise
        raise SoxlRsi2ResearchAdapterError("rsi2_input_invalid") from exc


def _proposal_from_result(result: Mapping[str, Any]) -> Any:
    from quant_platform_kit.strategy_lifecycle.contracts import OptimizationProposal

    if result.get("schema") != "qsl.research.soxl_rsi2_mean_reversion.v1":
        raise SoxlRsi2ResearchAdapterError("rsi2_result_invalid")
    winner = result.get("locked_winner")
    found = result.get("outcome") == "CHARACTERIZATION_CANDIDATE_FOUND"
    candidate = winner if found and isinstance(winner, str) else BASELINE_CANDIDATE
    return OptimizationProposal(
        strategy_profile=PROFILE,
        domain=DOMAIN,
        current_params={"candidate_id": BASELINE_CANDIDATE},
        proposed_params={"candidate_id": candidate},
        recommendation="research_candidate" if found else "reject",
        improvement_score=0.0,
        confidence=0.0,
        winning_dimensions=(),
        regressing_dimensions=(),
        walk_forward_passed=False,
        optimization_method="ues_rsi2_mean_reversion_existing_study",
        search_iterations=len(result.get("candidates", ())),
    )


def _missing_promotion_evidence(_proposal: Any) -> None:
    """RSI2 v1 is not a QPK strict PromotionBacktestRun."""
    return None


def _shadow_must_not_run(_proposal: Any) -> Mapping[str, Any]:
    raise SoxlRsi2ResearchAdapterError("rsi2_shadow_requires_strict_backtest")


def _make_formal_backtest_gate(
    *,
    optimization_source: OfflineInput,
    binding: SoxlRsi2PromotionBinding,
    store: Any,
    shadow_recorder: Any,
):
    if not callable(shadow_recorder):
        raise SoxlRsi2ResearchAdapterError("rsi2_shadow_recorder_invalid")
    try:
        from quant_platform_kit.strategy_lifecycle.backtest_orchestrator import (
            BacktestOrchestrator,
        )
    except Exception as exc:
        raise SoxlRsi2ResearchAdapterError("qpk_backtest_api_unavailable") from exc
    if store is None:
        raise SoxlRsi2ResearchAdapterError("rsi2_promotion_store_required")
    orchestrator = BacktestOrchestrator(store=store)

    def enforce(proposal: Any):
        if (
            getattr(proposal, "strategy_profile", None) != PROFILE
            or getattr(proposal, "domain", None) != DOMAIN
            or dict(getattr(proposal, "proposed_params", {}) or {})
            != {"candidate_id": binding.candidate_id}
        ):
            raise SoxlRsi2ResearchAdapterError("rsi2_promotion_candidate_mismatch")
        return binding.run(orchestrator)

    return enforce


def prepare_soxl_rsi2_promotion(
    *,
    optimization_source: OfflineInput,
    source_commit: str,
    research_identity: Mapping[str, str],
    promotion_binding: SoxlRsi2PromotionBinding | None = None,
    promotion_store: Any | None = None,
    promotion_shadow_recorder: Any | None = None,
):
    """Prepare the shared saved-cycle identity and trusted promotion callbacks."""
    _validate_research_identity(optimization_source, source_commit, research_identity)
    cycle_identity = dict(research_identity)
    if promotion_binding is None:
        return cycle_identity, _missing_promotion_evidence, _shadow_must_not_run
    if type(promotion_binding) is not SoxlRsi2PromotionBinding:
        raise SoxlRsi2ResearchAdapterError("rsi2_promotion_binding_invalid")
    try:
        validate_promotion_timing(optimization_source, promotion_binding)
    except Exception as exc:
        raise SoxlRsi2ResearchAdapterError("rsi2_promotion_timing_invalid") from exc
    cycle_identity["input_revision"] = promotion_binding_digest(
        optimization_source.input_digest, promotion_binding
    )
    enforce = _make_formal_backtest_gate(
        optimization_source=optimization_source,
        binding=promotion_binding,
        store=promotion_store,
        shadow_recorder=promotion_shadow_recorder,
    )
    return cycle_identity, enforce, promotion_shadow_recorder


def make_soxl_rsi2_optimize(
    *,
    input_paths: Rsi2OfflineInputPaths,
    output_root: str | Path,
    source_commit: str,
    source_blobs: Mapping[str, str],
    ues_repo_root: str | Path | None,
    source: OfflineInput | None = None,
    expected_identity: Mapping[str, str] | None = None,
):
    """Return the pure study callback consumed by QPK/AAB research requests."""

    def optimize(request: Any, _budget: Any) -> Any:
        if getattr(_budget, "max_search_iterations", _CANDIDATE_COUNT) < _CANDIDATE_COUNT:
            raise SoxlRsi2ResearchAdapterError("rsi2_budget_insufficient")
        if getattr(_budget, "max_param_keys", 1) < 1:
            raise SoxlRsi2ResearchAdapterError("rsi2_budget_insufficient")
        profile = request.get("strategy_profile") if isinstance(request, Mapping) else getattr(request, "strategy_profile", None)
        domain = request.get("domain") if isinstance(request, Mapping) else getattr(request, "domain", None)
        if profile != PROFILE or domain != DOMAIN:
            raise SoxlRsi2ResearchAdapterError("rsi2_research_target_invalid")
        typed_source = source or load_rsi2_offline_input(input_paths)
        if expected_identity is not None:
            _validate_research_identity(typed_source, source_commit, expected_identity)
        result = run_soxl_rsi2_mean_reversion(typed_source)
        persist_rsi2_mean_reversion_result(
            result,
            output_root,
            source_commit=source_commit,
            source_blobs=dict(source_blobs),
            repo_root=ues_repo_root,
        )
        return _proposal_from_result(result)

    return optimize


def _validate_research_identity(
    source: OfflineInput,
    source_commit: str,
    identity: Mapping[str, str],
) -> None:
    expected = {
        "code_revision": source_commit,
        "input_revision": source.input_digest,
        "param_space_revision": _PARAM_SPACE_REVISION,
        "cost_model_revision": _COST_MODEL_REVISION,
        "validator_revision": _VALIDATOR_REVISION,
    }
    if dict(identity) != expected:
        raise SoxlRsi2ResearchAdapterError("rsi2_research_identity_mismatch")


def run_soxl_rsi2_research_promotion(
    *,
    as_of: str,
    drift_score: float,
    source_revision: str,
    input_paths: Rsi2OfflineInputPaths,
    output_root: str | Path,
    source_commit: str,
    source_blobs: Mapping[str, str],
    ues_repo_root: str | Path | None,
    research_identity: Mapping[str, str],
    ticket_dir: str | Path,
    sync_console: Any | None = None,
    promotion_binding: SoxlRsi2PromotionBinding | None = None,
    promotion_store: Any | None = None,
    promotion_shadow_recorder: Any | None = None,
) -> dict[str, Any]:
    """Run RSI2 once through QPK's saved research lifecycle.

    Without a typed promotion binding this adapter parks before shadow. With
    one, the frozen candidate is evaluated by the strict QPK orchestrator;
    this still never grants live authority or creates an order proposal.
    """
    if not isinstance(as_of, str) or not as_of or not isinstance(source_revision, str) or not source_revision:
        raise SoxlRsi2ResearchAdapterError("rsi2_research_identity_invalid")
    if not isinstance(research_identity, Mapping):
        raise SoxlRsi2ResearchAdapterError("rsi2_research_identity_invalid")
    typed_source = load_rsi2_offline_input(input_paths)
    _validate_research_identity(typed_source, source_commit, research_identity)

    cycle_identity, enforce_backtest_gates, record_shadow = prepare_soxl_rsi2_promotion(
        optimization_source=typed_source,
        source_commit=source_commit,
        research_identity=research_identity,
        promotion_binding=promotion_binding,
        promotion_store=promotion_store,
        promotion_shadow_recorder=promotion_shadow_recorder,
    )

    try:
        from quant_platform_kit.strategy_lifecycle.promotion_actionable_runner import (
            run_actionable_research_promotion,
        )
    except Exception as exc:
        raise SoxlRsi2ResearchAdapterError("qpk_research_promotion_api_unavailable") from exc

    optimize = make_soxl_rsi2_optimize(
        input_paths=input_paths,
        output_root=output_root,
        source_commit=source_commit,
        source_blobs=source_blobs,
        ues_repo_root=ues_repo_root,
        source=typed_source,
        expected_identity=research_identity,
    )

    return run_actionable_research_promotion(
        strategy_profile=PROFILE,
        domain=DOMAIN,
        as_of=as_of,
        drift_score=drift_score,
        source_revision=source_revision,
        optimize=optimize,
        enforce_backtest_gates=enforce_backtest_gates,
        record_shadow=record_shadow,
        sync_console=sync_console or (lambda _ticket: False),
        research_identity=cycle_identity,
        ticket_dir=ticket_dir,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--drift-score", required=True, type=float)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--readback", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--ticket-dir", required=True, type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-blobs", required=True, type=Path)
    parser.add_argument("--ues-repo-root", required=True, type=Path)
    for name in ("code_revision", "input_revision", "param_space_revision", "cost_model_revision", "validator_revision"):
        parser.add_argument(f"--{name.replace('_', '-')}", required=True)
    args = parser.parse_args(argv)
    identity = {
        "code_revision": args.code_revision,
        "input_revision": args.input_revision,
        "param_space_revision": args.param_space_revision,
        "cost_model_revision": args.cost_model_revision,
        "validator_revision": args.validator_revision,
    }
    try:
        source_blobs = json.loads(args.source_blobs.read_text(encoding="utf-8"))
        typed_source = load_rsi2_offline_input(
            Rsi2OfflineInputPaths(args.manifest, args.artifact, args.readback)
        )
        result = run_soxl_rsi2_research_promotion(
            as_of=args.as_of,
            drift_score=args.drift_score,
            source_revision=typed_source.source_revision,
            input_paths=Rsi2OfflineInputPaths(args.manifest, args.artifact, args.readback),
            output_root=args.output_root,
            source_commit=args.source_commit,
            source_blobs=source_blobs,
            ues_repo_root=args.ues_repo_root,
            research_identity=identity,
            ticket_dir=args.ticket_dir,
        )
    except Exception:
        print(json.dumps({"status": "parked", "reason": "rsi2_research_input_invalid"}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


__all__ = [
    "DOMAIN",
    "PROFILE",
    "Rsi2OfflineInputPaths",
    "SoxlRsi2ResearchAdapterError",
    "load_rsi2_offline_input",
    "make_soxl_rsi2_optimize",
    "prepare_soxl_rsi2_promotion",
    "SoxlRsi2PromotionBinding",
    "main",
    "run_soxl_rsi2_research_promotion",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
