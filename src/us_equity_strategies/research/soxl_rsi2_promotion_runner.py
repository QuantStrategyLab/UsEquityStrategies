"""Fixed-candidate promotion runner for the bounded SOXL RSI2 study."""

from __future__ import annotations

from datetime import date
from dataclasses import dataclass
import hashlib
import json
import math
from time import perf_counter
from typing import Any, Mapping

from quant_platform_kit.strategy_lifecycle.contracts import (
    BacktestResult,
    PromotionCostModel,
    PurgedWalkForwardFold,
)

from .soxl_core_optimization import (
    INITIAL_EQUITY,
    RSI2_MEAN_REVERSION_CANDIDATES,
    SCENARIOS,
    DailyPoint,
    simulate_rsi2_mean_reversion_candidate,
)
from .soxl_soxx_offline_input_contract import OfflineInput


PROFILE = "soxl_rsi2_mean_reversion"
DOMAIN = "us_equity"


class SoxlRsi2PromotionRunnerError(ValueError):
    """Sanitized invalid fixed-candidate promotion-runner input."""


@dataclass(frozen=True)
class SoxlRsi2PromotionBinding:
    """Immutable, typed inputs for one formal promotion backtest."""

    source: OfflineInput
    candidate_id: str
    folds: tuple[PurgedWalkForwardFold, ...]
    locked_oos_start: date
    locked_oos_end: date
    purge_days: int
    embargo_days: int
    source_revision: str
    cost_model: PromotionCostModel

    def __post_init__(self) -> None:
        if type(self.source) is not OfflineInput:
            raise SoxlRsi2PromotionRunnerError("promotion_input_invalid")
        runner = SoxlRsi2PromotionRunner(self.source, candidate_id=self.candidate_id)
        if len(self.folds) < 3:
            raise SoxlRsi2PromotionRunnerError("promotion_folds_insufficient")
        if type(self.locked_oos_start) is not date or type(self.locked_oos_end) is not date:
            raise SoxlRsi2PromotionRunnerError("promotion_window_invalid")
        if self.locked_oos_start > self.locked_oos_end:
            raise SoxlRsi2PromotionRunnerError("promotion_window_invalid")
        if not isinstance(self.source_revision, str) or len(self.source_revision) != 40:
            raise SoxlRsi2PromotionRunnerError("promotion_source_revision_invalid")
        if any(character not in "0123456789abcdef" for character in self.source_revision):
            raise SoxlRsi2PromotionRunnerError("promotion_source_revision_invalid")
        _scenario(self.cost_model)
        del runner

    def run(self, orchestrator: Any):
        runner = SoxlRsi2PromotionRunner(self.source, candidate_id=self.candidate_id)
        orchestrator.register_runner(DOMAIN, runner)
        return orchestrator.run_promotion(
            PROFILE,
            domain=DOMAIN,
            params={"candidate_id": self.candidate_id},
            folds=self.folds,
            locked_oos_start=self.locked_oos_start,
            locked_oos_end=self.locked_oos_end,
            purge_days=self.purge_days,
            embargo_days=self.embargo_days,
            source_revision=self.source_revision,
            cost_model=self.cost_model,
            param_set_id=self.candidate_id,
        )


def promotion_binding_digest(
    optimization_input_revision: str,
    binding: SoxlRsi2PromotionBinding,
) -> str:
    """Return the saved-cycle identity for exactly this promotion material."""
    payload = {
        "optimization_input_revision": optimization_input_revision,
        "promotion_input_revision": binding.source.input_digest,
        "candidate_id": binding.candidate_id,
        "folds": [fold.to_dict() for fold in binding.folds],
        "locked_oos_start": binding.locked_oos_start.isoformat(),
        "locked_oos_end": binding.locked_oos_end.isoformat(),
        "purge_days": binding.purge_days,
        "embargo_days": binding.embargo_days,
        "source_revision": binding.source_revision,
        "cost_model": binding.cost_model.to_dict(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_promotion_timing(
    optimization_source: OfflineInput,
    binding: SoxlRsi2PromotionBinding,
) -> None:
    """Reject folds/OOS that overlap the actually loaded optimization input."""
    if type(optimization_source) is not OfflineInput:
        raise SoxlRsi2PromotionRunnerError("optimization_input_invalid")
    latest_seen = max(date.fromisoformat(row.as_of) for row in optimization_source.rows)
    if binding.locked_oos_start <= latest_seen:
        raise SoxlRsi2PromotionRunnerError("promotion_oos_overlaps_optimization")
    if any(fold.test_start <= latest_seen for fold in binding.folds):
        raise SoxlRsi2PromotionRunnerError("promotion_fold_overlaps_optimization")


def _scenario(cost_model: PromotionCostModel):
    if not isinstance(cost_model, PromotionCostModel):
        raise SoxlRsi2PromotionRunnerError("cost_model_invalid")
    if cost_model.market_impact_bps != 0.0:
        raise SoxlRsi2PromotionRunnerError("cost_model_unsupported")
    for scenario in SCENARIOS:
        if (
            cost_model.model_id == scenario.scenario_id
            and cost_model.commission_bps == scenario.commission_bps
            and cost_model.slippage_bps == scenario.slippage_bps
        ):
            return scenario
    raise SoxlRsi2PromotionRunnerError("cost_model_unsupported")


class SoxlRsi2PromotionRunner:
    """Run one frozen RSI2 candidate over explicit dated windows."""

    runner_kind = "real"

    def __init__(self, source: OfflineInput, *, candidate_id: str) -> None:
        if type(source) is not OfflineInput:
            raise SoxlRsi2PromotionRunnerError("offline_input_invalid")
        if candidate_id not in RSI2_MEAN_REVERSION_CANDIDATES:
            raise SoxlRsi2PromotionRunnerError("candidate_invalid")
        self._source = source
        self._candidate_id = candidate_id

    @property
    def candidate_id(self) -> str:
        return self._candidate_id

    def _check_params(self, strategy_profile: str, params: Mapping[str, Any]) -> None:
        if strategy_profile != PROFILE or type(params) is not dict or dict(params) != {"candidate_id": self._candidate_id}:
            raise SoxlRsi2PromotionRunnerError("candidate_params_mismatch")

    def _points(self, cost_model: PromotionCostModel) -> tuple[DailyPoint, ...]:
        scenario = _scenario(cost_model)
        return simulate_rsi2_mean_reversion_candidate(self._source, self._candidate_id, scenario)

    def _result(self, points: tuple[DailyPoint, ...], *, start_date: date, end_date: date, cost_model: PromotionCostModel, elapsed_seconds: float) -> BacktestResult:
        if type(start_date) is not date or type(end_date) is not date or start_date > end_date:
            raise SoxlRsi2PromotionRunnerError("window_invalid")
        selected = tuple(point for point in points if start_date.isoformat() <= point.date <= end_date.isoformat())
        if not selected or selected[0].date != start_date.isoformat() or selected[-1].date != end_date.isoformat():
            raise SoxlRsi2PromotionRunnerError("window_dates_missing")
        if selected[0].start_equity <= 0.0 or not math.isfinite(selected[0].start_equity):
            raise SoxlRsi2PromotionRunnerError("window_equity_invalid")
        returns = tuple(point.daily_return for point in selected)
        count = len(returns)
        mean = math.fsum(returns) / count
        variance = math.fsum((value - mean) ** 2 for value in returns) / (count - 1) if count > 1 else 0.0
        volatility = math.sqrt(variance) * math.sqrt(252.0)
        sharpe = mean / math.sqrt(variance) * math.sqrt(252.0) if variance > 0.0 else None
        start_equity = selected[0].start_equity
        total_return = selected[-1].end_equity / start_equity - 1.0
        if 1.0 + total_return <= 0.0:
            raise SoxlRsi2PromotionRunnerError("window_equity_invalid")
        peak = start_equity
        drawdowns: list[float] = []
        for point in selected:
            peak = max(peak, point.end_equity)
            drawdowns.append(point.end_equity / peak - 1.0)
        return BacktestResult(
            strategy_profile=PROFILE,
            domain=DOMAIN,
            param_set_id=self._candidate_id,
            params={"candidate_id": self._candidate_id},
            sharpe_ratio=sharpe,
            max_drawdown=min(drawdowns),
            cagr=(1.0 + total_return) ** (252.0 / count) - 1.0,
            volatility=volatility,
            win_rate=None,
            total_return=total_return,
            start_date=start_date,
            end_date=end_date,
            observation_count=count,
            run_id=f"{self._candidate_id}:{start_date.isoformat()}:{end_date.isoformat()}:{cost_model.model_id}",
            run_duration_seconds=elapsed_seconds,
            source_script=__file__,
            cost_model=cost_model.model_id,
            cost_inputs={
                "commission_bps": float(cost_model.commission_bps),
                "slippage_bps": float(cost_model.slippage_bps),
                "market_impact_bps": float(cost_model.market_impact_bps),
            },
        )

    def run_purged_fold(
        self,
        strategy_profile: str,
        params: Mapping[str, Any],
        *,
        fold: PurgedWalkForwardFold,
        purge_days: int,
        embargo_days: int,
        cost_model: PromotionCostModel,
    ) -> BacktestResult:
        self._check_params(strategy_profile, params)
        if not isinstance(fold, PurgedWalkForwardFold) or purge_days <= 0 or embargo_days <= 0:
            raise SoxlRsi2PromotionRunnerError("fold_invalid")
        started = perf_counter()
        points = self._points(cost_model)
        return self._result(points, start_date=fold.test_start, end_date=fold.test_end, cost_model=cost_model, elapsed_seconds=perf_counter() - started)

    def run_locked_oos(
        self,
        strategy_profile: str,
        params: Mapping[str, Any],
        *,
        start_date: date,
        end_date: date,
        cost_model: PromotionCostModel,
    ) -> BacktestResult:
        self._check_params(strategy_profile, params)
        started = perf_counter()
        points = self._points(cost_model)
        return self._result(points, start_date=start_date, end_date=end_date, cost_model=cost_model, elapsed_seconds=perf_counter() - started)


__all__ = [
    "DOMAIN",
    "PROFILE",
    "SoxlRsi2PromotionBinding",
    "SoxlRsi2PromotionRunner",
    "SoxlRsi2PromotionRunnerError",
    "promotion_binding_digest",
    "validate_promotion_timing",
]
