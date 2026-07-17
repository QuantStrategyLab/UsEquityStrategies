"""Deterministic offline R3 evidence for independent TQQQ and SOXL baselines."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
from typing import Any, Callable, NoReturn, Sequence

from .soxl_soxx_offline_input_contract import (
    OfflineInput as SoxlOfflineInput,
    load_offline_input as load_soxl_offline_input,
)
from .soxl_soxx_typed_baseline_result import (
    run_typed_baseline as run_soxl_typed_baseline,
)
from .tqqq_offline_input_contract import (
    OfflineInput as TqqqOfflineInput,
    load_offline_input as load_tqqq_offline_input,
)
from .tqqq_typed_baseline_result import (
    run_typed_baseline as run_tqqq_typed_baseline,
)


BUNDLE_SCHEMA = "qsl.research.r3_joint_evidence_bundle.v1"
READBACK_SCHEMA = "qsl.research.r3_joint_evidence_readback.v1"
R4_HANDOFF_SCHEMA = "qsl.research.r4_independent_sizing_evidence_input.v1"
CONTRACT_VERSION = "qsl.r3.joint_evidence.acceptance.v1"
CONTRACT_SHA256 = "31269e3a8654506dccf10766f84911d3d3fb6f7da2eefce3ef33c7f5e7a5dee6"
WORKER_PROMPT_SHA256 = "bb6c56156f28c6318625e22c8979e9420a3c79b79a7d01cfb71e55dd249070d6"
SOURCE_COMMIT = "e04d1561e07ea84e6fb0decfdd714cdcf557cdfa"
PROFILE_SHA256 = "cfc7bcffc4853d1b79ae0575287e76a8e50b679792ccd003858a317b1f42e684"
PROFILE_CANONICAL_JSON = (
    '{"common_strategy_thresholds":{"max_mc_terminal_loss_probability_C2_5":'
    '"0x1.0000000000000p-1","min_final_holdout_cumulative_return_C2_5":"0x0.0p+0",'
    '"min_final_holdout_cumulative_return_C5_10_STRESS":"0x0.0p+0",'
    '"min_wfa_test_cumulative_return":"0x0.0p+0","min_wfa_test_windows_passing":2},'
    '"comparators":{"final_holdout_cumulative_return_C2_5":"STRICT_GREATER_THAN",'
    '"final_holdout_cumulative_return_C5_10_STRESS":"STRICT_GREATER_THAN",'
    '"mc_terminal_loss_probability_C2_5":"STRICT_LESS_THAN",'
    '"wfa_window_cumulative_return":"STRICT_GREATER_THAN",'
    '"wfa_windows_passing":"GREATER_THAN_OR_EQUAL"},'
    '"joint_risk_semantics":"REPORT_ONLY_CONTEXT_NOT_A_GATE_FOR_R4A",'
    '"joint_risk_thresholds":null,"purpose":"R4A_RESEARCH_ELIGIBILITY_ONLY_NOT_LIVE_PROMOTION",'
    '"schema":"qsl.r3.research_eligibility_threshold_profile.v1"}'
)
THRESHOLD_PROFILE: dict[str, Any] = json.loads(PROFILE_CANONICAL_JSON)
MC_SEED_HEX = "08a73485a70548df5262ad66ac86e02c0c5cc6255469156832aec2e86b501e2b"
MC_TRIALS = 10_000
MC_PATH_LENGTH = 126
MC_BLOCK_LENGTH = 12
ALIGNED_DATES_SHA256 = "9ad30cd2ae54d56e58e4ea517f15070b31b4e1d9127ae6031529c191920a7f80"
DEFAULT_OUTPUT_ROOT = Path(
    "/Users/lisiyi/Documents/Codex/2026-07-14/ba-2/work/private_research/r3_joint_evidence_v1"
)
CONTRACT_PATH = Path(
    "/Users/lisiyi/Documents/Codex/2026-07-14/ba-2/outputs/"
    "qsl_r3_joint_evidence_acceptance_contract_v1_2026-07-17.md"
)
WORKER_PROMPT_PATH = Path(
    "/Users/lisiyi/Documents/Codex/2026-07-14/ba-2/outputs/"
    "qsl_r3_joint_evidence_worker_prompt_v1_2026-07-17.md"
)


class R3EvidenceError(ValueError):
    """Sanitized fail-closed R3 evidence error."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> NoReturn:
    raise R3EvidenceError(code) from None


def _canonical_bytes(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError):
        _fail("CANONICAL_JSON_INVALID")


def _digest_value(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)[:-1]).hexdigest()


def _dates_sha256(dates: Sequence[str]) -> str:
    return hashlib.sha256(("\n".join(dates) + "\n").encode("utf-8")).hexdigest()


def _to_wire(value: object) -> object:
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            _fail("NONFINITE_VALUE")
        return value.hex()
    if type(value) is dict:
        return {key: _to_wire(child) for key, child in value.items()}
    if type(value) in (list, tuple):
        return [_to_wire(child) for child in value]
    _fail("WIRE_TYPE_INVALID")


@dataclass(frozen=True, slots=True)
class CostScenario:
    scenario_id: str
    commission_bps: int
    slippage_bps: int


SCENARIOS = (
    CostScenario("ZERO", 0, 0),
    CostScenario("C1_2", 1, 2),
    CostScenario("C2_5", 2, 5),
    CostScenario("C5_10_STRESS", 5, 10),
)


@dataclass(frozen=True, slots=True)
class WindowSpec:
    segment_id: str
    raw_start: int
    raw_end: int
    start_date: str
    end_date: str
    role: str
    metrics_included: bool

    @property
    def observation_count(self) -> int:
        return self.raw_end - self.raw_start + 1


WINDOW_SPECS = (
    WindowSpec("SMA_WARMUP", 0, 199, "2023-07-14", "2024-04-29", "SIGNAL_CONTEXT", False),
    WindowSpec("F1_TRAIN", 200, 368, "2024-04-30", "2024-12-30", "TRAIN_PREFIX", True),
    WindowSpec("F1_EMBARGO_1", 369, 369, "2024-12-31", "2024-12-31", "EMBARGO", False),
    WindowSpec("F1_VALIDATION", 370, 411, "2025-01-02", "2025-03-05", "VALIDATION", True),
    WindowSpec("F1_EMBARGO_2", 412, 412, "2025-03-06", "2025-03-06", "EMBARGO", False),
    WindowSpec("F1_TEST", 413, 454, "2025-03-07", "2025-05-06", "TEST", True),
    WindowSpec("F2_TRAIN", 200, 454, "2024-04-30", "2025-05-06", "TRAIN_PREFIX", True),
    WindowSpec("F2_EMBARGO_1", 455, 455, "2025-05-07", "2025-05-07", "EMBARGO", False),
    WindowSpec("F2_VALIDATION", 456, 497, "2025-05-08", "2025-07-09", "VALIDATION", True),
    WindowSpec("F2_EMBARGO_2", 498, 498, "2025-07-10", "2025-07-10", "EMBARGO", False),
    WindowSpec("F2_TEST", 499, 540, "2025-07-11", "2025-09-09", "TEST", True),
    WindowSpec("F3_TRAIN", 200, 540, "2024-04-30", "2025-09-09", "TRAIN_PREFIX", True),
    WindowSpec("F3_EMBARGO_1", 541, 541, "2025-09-10", "2025-09-10", "EMBARGO", False),
    WindowSpec("F3_VALIDATION", 542, 583, "2025-09-11", "2025-11-07", "VALIDATION", True),
    WindowSpec("F3_EMBARGO_2", 584, 584, "2025-11-10", "2025-11-10", "EMBARGO", False),
    WindowSpec("F3_TEST", 585, 626, "2025-11-11", "2026-01-12", "TEST", True),
    WindowSpec("FINAL_HOLDOUT", 627, 752, "2026-01-13", "2026-07-15", "FINAL_OOS", True),
)
METRIC_WINDOW_IDS = tuple(item.segment_id for item in WINDOW_SPECS if item.metrics_included)
WFA_TEST_IDS = ("F1_TEST", "F2_TEST", "F3_TEST")

METHOD_SPEC = {
    "schema": "qsl.research.r3_joint_evidence_method.v1",
    "contract_sha256": CONTRACT_SHA256,
    "worker_prompt_sha256": WORKER_PROMPT_SHA256,
    "source_commit": SOURCE_COMMIT,
    "threshold_profile_sha256": PROFILE_SHA256,
    "aligned_date_digest_method": "UTF8_LF_ONE_DATE_PER_LINE_WITH_FINAL_LF_V1",
    "aligned_dates_sha256": ALIGNED_DATES_SHA256,
    "baseline": {
        "sma_window": 200,
        "signal_rule": "INCLUSIVE_CLOSE_GREATER_THAN_OR_EQUAL_SMA",
        "execution": "NEXT_OBSERVED_OPEN",
        "initial_equity": "0x1.86a0000000000p+16",
    },
    "cost_scenarios": [
        {
            "scenario_id": item.scenario_id,
            "commission_bps_per_side": item.commission_bps,
            "adverse_slippage_bps_per_side": item.slippage_bps,
        }
        for item in SCENARIOS
    ],
    "windows": [
        {
            "segment_id": item.segment_id,
            "raw_start": item.raw_start,
            "raw_end": item.raw_end,
            "start_date": item.start_date,
            "end_date": item.end_date,
            "role": item.role,
            "metrics_included": item.metrics_included,
        }
        for item in WINDOW_SPECS
    ],
    "monte_carlo": {
        "method": "CIRCULAR_MOVING_BLOCK_BOOTSTRAP_HMAC_SHA256_V1",
        "seed_hex": MC_SEED_HEX,
        "trials": MC_TRIALS,
        "path_length": MC_PATH_LENGTH,
        "block_length": MC_BLOCK_LENGTH,
        "quantile": "NEAREST_RANK",
    },
}
METHOD_DIGEST = _digest_value(METHOD_SPEC)


@dataclass(frozen=True, slots=True)
class FileIdentity:
    path: Path
    sha256: str
    byte_count: int | None = None


@dataclass(frozen=True, slots=True)
class StrategySpec:
    strategy_id: str
    profile: str
    baseline_version: str
    signal_asset: str
    traded_asset: str
    input_digest: str


TQQQ_SPEC = StrategySpec(
    "TQQQ",
    "tqqq_growth_income_research_baseline_v1",
    "qsl.research.tqqq_typed_baseline_result.v1",
    "QQQ",
    "TQQQ",
    "8cc682b2d1acc23a8dd93c3bfd67b445d7305844d2c4d254f4f52e0ac817c6cb",
)
SOXL_SPEC = StrategySpec(
    "SOXL",
    "soxl_soxx_trend_income_parity_baseline_v1",
    "qsl.research.soxl_soxx_typed_baseline_result.v1",
    "SOXX",
    "SOXL",
    "78c056c9a4541b7612b4f077ca25df6093aa6eb2f17783097c5b5f83a31dd5c6",
)

PRIVATE_ROOT = Path("/Users/lisiyi/Documents/Codex/2026-07-14/ba-2/work/private_research")
TQQQ_ARTIFACT = PRIVATE_ROOT / "tqqq_baseline_v1/full_20230714_20260716.csv"
SOXL_ARTIFACT = PRIVATE_ROOT / "soxx_soxl_adjusted_daily_v1/full_20230714_20260716.csv"
TQQQ_IDENTITIES = (
    FileIdentity(TQQQ_ARTIFACT, "a40254c7e31d6b49b4a2db5ec57b1b65215a3ab1ee33df879d9e5e2b4dae6551", 150661),
    FileIdentity(
        Path(str(TQQQ_ARTIFACT) + ".manifest.json"),
        "8ecbc864f356af94464249ee3003d44fb00cf739c6810dc2de14165e5dc3500d",
        593,
    ),
)
SOXL_IDENTITIES = (
    FileIdentity(SOXL_ARTIFACT, "6eb44951f7b16b7369df2d8d0fcce08b85d44ad3b758381139a027a53dd5f36c", 149968),
    FileIdentity(
        Path(str(SOXL_ARTIFACT) + ".manifest.json"),
        "8fe988353a6bc0f3642e69cc7f58c180df59ebb7ff62d6b986aba314fb9db81b",
        648,
    ),
    FileIdentity(
        Path(str(SOXL_ARTIFACT) + ".readback.json"),
        "94bef6a1d27a4487d13500242fa24a183ec388318bcab59dace21d32235b3dd2",
        502,
    ),
)
SOURCE_MODULE_SHA256 = {
    "tqqq_offline_input_contract.py": "95b7846be52a706cf55bdcf318bd22e47fcbd8bcbde481b1607bfe431db2efbb",
    "tqqq_typed_baseline_result.py": "b03768587adc8810faa399e78f21a276f443fd673a120b3f3a6829b0ad6fe2bf",
    "soxl_soxx_offline_input_contract.py": "883ba2c72fd0fe661f70faa4871f8f710147506ba54e41ffa81618dccc6a67d2",
    "soxl_soxx_typed_baseline_result.py": "fa489c77c16699d9b1a56315fd7f95c220939651e80e13b87ce1b37794c2724c",
}


def _read_identity(identity: FileIdentity) -> bytes:
    try:
        raw = identity.path.read_bytes()
    except OSError:
        _fail("FILE_IDENTITY_MISMATCH")
    if (
        (identity.byte_count is not None and len(raw) != identity.byte_count)
        or hashlib.sha256(raw).hexdigest() != identity.sha256
    ):
        _fail("FILE_IDENTITY_MISMATCH")
    return raw


def _verified_call(identities: Sequence[FileIdentity], action: Callable[[], Any]) -> Any:
    before = tuple(_read_identity(identity) for identity in identities)
    result = action()
    after = tuple(_read_identity(identity) for identity in identities)
    if before != after:
        _fail("FILE_IDENTITY_MISMATCH")
    return result


def _verify_source_modules() -> None:
    root = Path(__file__).resolve().parent
    for name, expected in SOURCE_MODULE_SHA256.items():
        try:
            actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
        except OSError:
            _fail("SOURCE_MODULE_IDENTITY_MISMATCH")
        if actual != expected:
            _fail("SOURCE_MODULE_IDENTITY_MISMATCH")


@dataclass(frozen=True, slots=True)
class DailyEvidence:
    date: str
    start_equity: float
    end_equity: float
    cash: float
    quantity: float
    daily_return: float
    transition: bool
    buy: bool
    sell: bool
    gross_traded_notional_at_open: float
    commission_paid: float
    slippage_impact_vs_open: float


def _finite_positive(value: float, code: str) -> float:
    if type(value) is not float or not math.isfinite(value) or value <= 0.0:
        _fail(code)
    return value


def _simulate_strategy(
    signal_rows: Sequence[Any],
    traded_rows: Sequence[Any],
    scenario: CostScenario,
) -> tuple[DailyEvidence, ...]:
    if (
        type(scenario) is not CostScenario
        or len(signal_rows) != len(traded_rows)
        or len(signal_rows) <= 200
        or tuple(row.as_of for row in signal_rows) != tuple(row.as_of for row in traded_rows)
    ):
        _fail("SIMULATION_INPUT_INVALID")
    cash = 100_000.0
    quantity = 0.0
    previous_equity = 100_000.0
    points: list[DailyEvidence] = []
    commission_rate = scenario.commission_bps / 10_000.0
    slippage_rate = scenario.slippage_bps / 10_000.0

    for execution_index in range(200, len(signal_rows)):
        signal_index = execution_index - 1
        window = signal_rows[execution_index - 200 : execution_index]
        signal_close = float(signal_rows[signal_index].close)
        if any(not math.isfinite(float(row.close)) or float(row.close) <= 0.0 for row in window):
            _fail("SIMULATION_INPUT_INVALID")
        risk_on = signal_close >= math.fsum(float(row.close) for row in window) / 200.0
        execution = traded_rows[execution_index]
        traded_open = _finite_positive(float(execution.open), "SIMULATION_INPUT_INVALID")
        traded_close = _finite_positive(float(execution.close), "SIMULATION_INPUT_INVALID")
        opening_equity = _finite_positive(cash + quantity * traded_open, "SIMULATION_EQUITY_INVALID")
        transition = risk_on != (quantity > 0.0)
        buy = transition and risk_on
        sell = transition and not risk_on
        gross_notional = 0.0
        commission = 0.0
        slippage_impact = 0.0

        if buy:
            if scenario.scenario_id == "ZERO":
                quantity = opening_equity / traded_open
            else:
                fill = traded_open * (1.0 + slippage_rate)
                quantity = opening_equity / (fill * (1.0 + commission_rate))
                commission = quantity * fill * commission_rate
                slippage_impact = quantity * (fill - traded_open)
            gross_notional = quantity * traded_open
            cash = 0.0
        elif sell:
            sold_quantity = quantity
            if scenario.scenario_id == "ZERO":
                cash = opening_equity
            else:
                fill = traded_open * (1.0 - slippage_rate)
                gross_proceeds = sold_quantity * fill
                commission = gross_proceeds * commission_rate
                slippage_impact = sold_quantity * (traded_open - fill)
                cash = gross_proceeds - commission
            gross_notional = sold_quantity * traded_open
            quantity = 0.0

        end_equity = _finite_positive(cash + quantity * traded_close, "SIMULATION_EQUITY_INVALID")
        if not math.isfinite(cash) or cash < 0.0 or not math.isfinite(quantity) or quantity < 0.0:
            _fail("SIMULATION_EQUITY_INVALID")
        daily_return = end_equity / previous_equity - 1.0
        if not math.isfinite(daily_return):
            _fail("SIMULATION_EQUITY_INVALID")
        points.append(
            DailyEvidence(
                date=execution.as_of,
                start_equity=previous_equity,
                end_equity=end_equity,
                cash=float(cash),
                quantity=float(quantity),
                daily_return=float(daily_return),
                transition=transition,
                buy=buy,
                sell=sell,
                gross_traded_notional_at_open=float(gross_notional),
                commission_paid=float(commission),
                slippage_impact_vs_open=float(slippage_impact),
            )
        )
        previous_equity = end_equity
    return tuple(points)


def _window_metrics(
    points: Sequence[DailyEvidence], raw_start: int, raw_end: int
) -> dict[str, Any]:
    start = raw_start - 200
    end = raw_end - 200 + 1
    if start < 0 or end > len(points) or start >= end:
        _fail("WINDOW_BOUNDARY_INVALID")
    selected = tuple(points[start:end])
    returns = tuple(point.daily_return for point in selected)
    start_equity = selected[0].start_equity
    end_equity = selected[-1].end_equity
    ratio = end_equity / start_equity
    cumulative_return = ratio - 1.0
    product_return = math.prod(1.0 + value for value in returns) - 1.0
    if not math.isclose(cumulative_return, product_return, rel_tol=1e-12, abs_tol=1e-14):
        _fail("WINDOW_RETURN_INVARIANT_FAILED")
    count = len(selected)
    mean_return = math.fsum(returns) / count
    if count > 1:
        variance = math.fsum((value - mean_return) ** 2 for value in returns) / (count - 1)
        daily_std = math.sqrt(variance)
        annualized_volatility = daily_std * math.sqrt(252.0)
        sharpe = None if daily_std == 0.0 else mean_return / daily_std * math.sqrt(252.0)
    else:
        annualized_volatility = None
        sharpe = None
    peak = start_equity
    drawdowns: list[float] = []
    for point in selected:
        peak = max(peak, point.end_equity)
        drawdowns.append(point.end_equity / peak - 1.0)
    tail_count = math.ceil(0.05 * count)
    expected_shortfall = math.fsum(sorted(returns)[:tail_count]) / tail_count
    gross_notional = math.fsum(point.gross_traded_notional_at_open for point in selected)
    mean_close_equity = math.fsum(point.end_equity for point in selected) / count
    turnover = gross_notional / mean_close_equity
    commission = math.fsum(point.commission_paid for point in selected)
    slippage = math.fsum(point.slippage_impact_vs_open for point in selected)
    return {
        "observation_count": count,
        "start_date": selected[0].date,
        "end_date": selected[-1].date,
        "start_equity": start_equity,
        "end_equity": end_equity,
        "cumulative_return": cumulative_return,
        "return_product_cumulative_return": product_return,
        "annualized_return": ratio ** (252.0 / count) - 1.0,
        "mean_daily_return": mean_return,
        "annualized_volatility": annualized_volatility,
        "sharpe": sharpe,
        "max_drawdown": min(drawdowns),
        "worst_daily_return": min(returns),
        "expected_shortfall_95": expected_shortfall,
        "positive_day_rate": sum(value > 0.0 for value in returns) / count,
        "trade_count": sum(point.transition for point in selected),
        "buy_count": sum(point.buy for point in selected),
        "sell_count": sum(point.sell for point in selected),
        "gross_traded_notional_at_open": gross_notional,
        "turnover_ratio": turnover,
        "annualized_turnover": turnover * 252.0 / count,
        "commission_paid": commission,
        "slippage_impact_vs_open": slippage,
        "total_cost": commission + slippage,
        "terminal_exposure_open": selected[-1].quantity > 0.0,
    }


def _sample_index(context: str, trial: int, block: int, population: int) -> int:
    seed = bytes.fromhex(MC_SEED_HEX)
    limit = 2**64 - (2**64 % population)
    counter = 0
    while True:
        message = f"R3-MBB-V1\0{context}\0{trial}\0{block}\0{counter}".encode()
        value = int.from_bytes(hmac.new(seed, message, hashlib.sha256).digest()[:8], "big")
        if value < limit:
            return value % population
        counter += 1


def _bootstrap_indices(
    context: str,
    trial: int,
    *,
    path_length: int = MC_PATH_LENGTH,
    block_length: int = MC_BLOCK_LENGTH,
) -> tuple[int, ...]:
    if not context or path_length < 1 or block_length < 1 or trial < 0:
        _fail("MONTE_CARLO_METHOD_INVALID")
    indices: list[int] = []
    blocks = math.ceil(path_length / block_length)
    for block in range(blocks):
        start = _sample_index(context, trial, block, path_length)
        indices.extend((start + offset) % path_length for offset in range(block_length))
    return tuple(indices[:path_length])


def _path_statistics(returns: Sequence[float], indices: Sequence[int]) -> tuple[float, float]:
    equity = 1.0
    peak = 1.0
    max_drawdown_abs = 0.0
    for index in indices:
        equity *= 1.0 + returns[index]
        if not math.isfinite(equity) or equity <= 0.0:
            _fail("MONTE_CARLO_PATH_INVALID")
        peak = max(peak, equity)
        max_drawdown_abs = max(max_drawdown_abs, 1.0 - equity / peak)
    return equity - 1.0, max_drawdown_abs


def _nearest_rank(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    return ordered[math.ceil(probability * len(ordered)) - 1]


def _monte_carlo(
    scenario_returns: dict[str, Sequence[float]],
    context: str,
    *,
    trials: int = MC_TRIALS,
    path_length: int = MC_PATH_LENGTH,
    block_length: int = MC_BLOCK_LENGTH,
) -> dict[str, dict[str, Any]]:
    if (
        trials < 1
        or set(scenario_returns) == set()
        or any(len(values) != path_length for values in scenario_returns.values())
        or any(
            not math.isfinite(value) or value <= -1.0
            for values in scenario_returns.values()
            for value in values
        )
    ):
        _fail("MONTE_CARLO_INPUT_INVALID")
    terminal: dict[str, list[float]] = {name: [] for name in scenario_returns}
    drawdown: dict[str, list[float]] = {name: [] for name in scenario_returns}
    for trial in range(trials):
        indices = _bootstrap_indices(
            context,
            trial,
            path_length=path_length,
            block_length=block_length,
        )
        for name, returns in scenario_returns.items():
            terminal_value, drawdown_value = _path_statistics(returns, indices)
            terminal[name].append(terminal_value)
            drawdown[name].append(drawdown_value)
    return {
        name: {
            "trials": trials,
            "path_length": path_length,
            "block_length": block_length,
            "terminal_cumulative_return_p05": _nearest_rank(terminal[name], 0.05),
            "terminal_cumulative_return_p50": _nearest_rank(terminal[name], 0.50),
            "terminal_cumulative_return_p95": _nearest_rank(terminal[name], 0.95),
            "max_drawdown_abs_p50": _nearest_rank(drawdown[name], 0.50),
            "max_drawdown_abs_p95": _nearest_rank(drawdown[name], 0.95),
            "terminal_loss_probability": sum(value < 0.0 for value in terminal[name]) / trials,
        }
        for name in scenario_returns
    }


def _eligibility(
    wfa_c2_5_returns: Sequence[float],
    final_c2_5_return: float,
    final_stress_return: float,
    mc_c2_5_loss_probability: float,
) -> tuple[str, tuple[str, ...]]:
    if len(wfa_c2_5_returns) != 3:
        _fail("ELIGIBILITY_INPUT_INVALID")
    failures: list[str] = []
    if sum(value > 0.0 for value in wfa_c2_5_returns) < 2:
        failures.append("WFA_C2_5_PASSING_WINDOWS_BELOW_MINIMUM")
    if not final_c2_5_return > 0.0:
        failures.append("FINAL_HOLDOUT_C2_5_RETURN_NOT_STRICTLY_POSITIVE")
    if not final_stress_return > 0.0:
        failures.append("FINAL_HOLDOUT_C5_10_STRESS_RETURN_NOT_STRICTLY_POSITIVE")
    if not mc_c2_5_loss_probability < 0.5:
        failures.append("MC_C2_5_TERMINAL_LOSS_PROBABILITY_NOT_STRICTLY_BELOW_HALF")
    return ("FAIL", tuple(failures)) if failures else ("PASS", ())


def _sample_covariance(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        _fail("DEPENDENCY_INPUT_INVALID")
    left_mean = math.fsum(left) / len(left)
    right_mean = math.fsum(right) / len(right)
    return math.fsum(
        (l_value - left_mean) * (r_value - right_mean)
        for l_value, r_value in zip(left, right, strict=True)
    ) / (len(left) - 1)


def _drawdowns(returns: Sequence[float], equities: Sequence[float]) -> tuple[float, ...]:
    if len(returns) != len(equities) or not returns:
        _fail("DEPENDENCY_INPUT_INVALID")
    start_equity = equities[0] / (1.0 + returns[0])
    peak = start_equity
    values: list[float] = []
    for equity in equities:
        peak = max(peak, equity)
        values.append(equity / peak - 1.0)
    return tuple(values)


def _dependency_metrics(
    dates: Sequence[str],
    tqqq_returns: Sequence[float],
    tqqq_equities: Sequence[float],
    soxl_returns: Sequence[float],
    soxl_equities: Sequence[float],
) -> dict[str, Any]:
    count = len(dates)
    if count < 2 or any(len(values) != count for values in (tqqq_returns, tqqq_equities, soxl_returns, soxl_equities)):
        _fail("DEPENDENCY_INPUT_INVALID")
    covariance = _sample_covariance(tqqq_returns, soxl_returns)
    t_variance = _sample_covariance(tqqq_returns, tqqq_returns)
    s_variance = _sample_covariance(soxl_returns, soxl_returns)
    correlation = None
    failure_codes: list[str] = []
    if t_variance > 0.0 and s_variance > 0.0:
        correlation = covariance / math.sqrt(t_variance * s_variance)
    else:
        failure_codes.append("CORRELATION_UNDEFINED_ZERO_VARIANCE_REPORT_ONLY")
    t_drawdowns = _drawdowns(tqqq_returns, tqqq_equities)
    s_drawdowns = _drawdowns(soxl_returns, soxl_equities)
    common_indices = [
        index
        for index, (t_value, s_value) in enumerate(zip(t_drawdowns, s_drawdowns, strict=True))
        if t_value < 0.0 and s_value < 0.0
    ]
    tail_count = math.ceil(0.05 * count)
    t_tail = {index for index, _ in sorted(enumerate(tqqq_returns), key=lambda item: (item[1], dates[item[0]]))[:tail_count]}
    s_tail = {index for index, _ in sorted(enumerate(soxl_returns), key=lambda item: (item[1], dates[item[0]]))[:tail_count]}
    tail_indices = sorted(t_tail & s_tail)
    tail_dates = [dates[index] for index in tail_indices]
    return {
        "sample_count": count,
        "sample_covariance": covariance,
        "tqqq_sample_variance": t_variance,
        "soxl_sample_variance": s_variance,
        "pearson_correlation": correlation,
        "both_below_own_peak_count": len(common_indices),
        "both_below_own_peak_rate": len(common_indices) / count,
        "tqqq_worst_drawdown_on_common_days": min((t_drawdowns[index] for index in common_indices), default=None),
        "soxl_worst_drawdown_on_common_days": min((s_drawdowns[index] for index in common_indices), default=None),
        "tail_co_loss_count": len(tail_indices),
        "tail_co_loss_rate": len(tail_indices) / count,
        "tqqq_tail_co_loss_conditional_mean": (
            math.fsum(tqqq_returns[index] for index in tail_indices) / len(tail_indices)
            if tail_indices
            else None
        ),
        "soxl_tail_co_loss_conditional_mean": (
            math.fsum(soxl_returns[index] for index in tail_indices) / len(tail_indices)
            if tail_indices
            else None
        ),
        "tail_co_loss_dates_sha256": hashlib.sha256(("\n".join(tail_dates) + ("\n" if tail_dates else "")).encode()).hexdigest(),
        "gate_status": "NOT_APPLICABLE_REPORT_ONLY",
        "failure_codes": failure_codes,
    }


def _paired_monte_carlo(
    tqqq_returns: dict[str, Sequence[float]],
    soxl_returns: dict[str, Sequence[float]],
    *,
    trials: int = MC_TRIALS,
    path_length: int = MC_PATH_LENGTH,
    block_length: int = MC_BLOCK_LENGTH,
) -> dict[str, dict[str, Any]]:
    if set(tqqq_returns) != set(soxl_returns) or any(
        len(values) != path_length
        for values in (*tqqq_returns.values(), *soxl_returns.values())
    ):
        _fail("PAIRED_MONTE_CARLO_INPUT_INVALID")
    collected: dict[str, dict[str, list[float]]] = {
        scenario: {
            "t_terminal": [],
            "s_terminal": [],
            "t_drawdown": [],
            "s_drawdown": [],
            "both_loss": [],
        }
        for scenario in tqqq_returns
    }
    for trial in range(trials):
        indices = _bootstrap_indices(
            "JOINT:TQQQ:SOXL",
            trial,
            path_length=path_length,
            block_length=block_length,
        )
        for scenario in tqqq_returns:
            t_terminal, t_drawdown = _path_statistics(tqqq_returns[scenario], indices)
            s_terminal, s_drawdown = _path_statistics(soxl_returns[scenario], indices)
            target = collected[scenario]
            target["t_terminal"].append(t_terminal)
            target["s_terminal"].append(s_terminal)
            target["t_drawdown"].append(t_drawdown)
            target["s_drawdown"].append(s_drawdown)
            target["both_loss"].append(float(t_terminal < 0.0 and s_terminal < 0.0))
    result: dict[str, dict[str, Any]] = {}
    for scenario, values in collected.items():
        result[scenario] = {
            "trials": trials,
            "path_length": path_length,
            "block_length": block_length,
            "tqqq_terminal_cumulative_return_p05": _nearest_rank(values["t_terminal"], 0.05),
            "tqqq_terminal_cumulative_return_p50": _nearest_rank(values["t_terminal"], 0.50),
            "tqqq_terminal_cumulative_return_p95": _nearest_rank(values["t_terminal"], 0.95),
            "soxl_terminal_cumulative_return_p05": _nearest_rank(values["s_terminal"], 0.05),
            "soxl_terminal_cumulative_return_p50": _nearest_rank(values["s_terminal"], 0.50),
            "soxl_terminal_cumulative_return_p95": _nearest_rank(values["s_terminal"], 0.95),
            "tqqq_max_drawdown_abs_p95": _nearest_rank(values["t_drawdown"], 0.95),
            "soxl_max_drawdown_abs_p95": _nearest_rank(values["s_drawdown"], 0.95),
            "both_terminal_loss_probability": math.fsum(values["both_loss"]) / trials,
        }
    return result


@dataclass(frozen=True, slots=True)
class StrategyEvaluation:
    record: dict[str, Any]
    daily_by_scenario: dict[str, tuple[DailyEvidence, ...]] | None
    window_metrics: dict[str, dict[str, dict[str, Any]]] | None
    monte_carlo: dict[str, dict[str, Any]] | None


def _validate_private_dates(signal_rows: Sequence[Any], traded_rows: Sequence[Any]) -> tuple[str, ...]:
    dates = tuple(row.as_of for row in signal_rows)
    if (
        len(dates) != 753
        or dates != tuple(row.as_of for row in traded_rows)
        or dates[0] != "2023-07-14"
        or dates[-1] != "2026-07-15"
        or len(set(dates)) != len(dates)
        or dates != tuple(sorted(dates))
        or _dates_sha256(dates) != ALIGNED_DATES_SHA256
    ):
        _fail("OBSERVED_DATE_IDENTITY_MISMATCH")
    for item in WINDOW_SPECS:
        if dates[item.raw_start] != item.start_date or dates[item.raw_end] != item.end_date:
            _fail("WINDOW_DATE_IDENTITY_MISMATCH")
    return dates


def _zero_invariant(
    spec: StrategySpec,
    source: TqqqOfflineInput | SoxlOfflineInput,
    points: Sequence[DailyEvidence],
) -> None:
    baseline = (
        run_tqqq_typed_baseline(source)
        if spec.strategy_id == "TQQQ"
        else run_soxl_typed_baseline(source)
    )
    quantity_name = "tqqq_quantity" if spec.strategy_id == "TQQQ" else "soxl_quantity"
    if len(points) != baseline.evaluation_count or baseline.evaluation_count != 553:
        _fail("ZERO_COST_BASELINE_MISMATCH")
    for actual, expected, expected_return in zip(
        points,
        baseline.equity_curve,
        baseline.daily_returns,
        strict=True,
    ):
        if (
            actual.date != expected.date
            or actual.end_equity.hex() != expected.equity.hex()
            or actual.cash.hex() != expected.cash.hex()
            or actual.quantity.hex() != getattr(expected, quantity_name).hex()
            or actual.daily_return.hex() != expected_return.daily_return.hex()
        ):
            _fail("ZERO_COST_BASELINE_MISMATCH")
    if sum(point.transition for point in points) != baseline.trade_count:
        _fail("ZERO_COST_BASELINE_MISMATCH")


def _evaluate_loaded(
    spec: StrategySpec,
    source: TqqqOfflineInput | SoxlOfflineInput,
) -> StrategyEvaluation:
    if source.input_digest != spec.input_digest:
        _fail(f"{spec.strategy_id}_INPUT_DIGEST_MISMATCH")
    signal_rows = tuple(row for row in source.rows if row.symbol == spec.signal_asset)
    traded_rows = tuple(row for row in source.rows if row.symbol == spec.traded_asset)
    dates = _validate_private_dates(signal_rows, traded_rows)
    daily_by_scenario = {
        scenario.scenario_id: _simulate_strategy(signal_rows, traded_rows, scenario)
        for scenario in SCENARIOS
    }
    _zero_invariant(spec, source, daily_by_scenario["ZERO"])
    metrics: dict[str, dict[str, dict[str, Any]]] = {}
    for window in WINDOW_SPECS:
        if not window.metrics_included:
            continue
        metrics[window.segment_id] = {
            scenario.scenario_id: _window_metrics(
                daily_by_scenario[scenario.scenario_id],
                window.raw_start,
                window.raw_end,
            )
            for scenario in SCENARIOS
        }
    final_start = 627 - 200
    final_returns = {
        scenario.scenario_id: tuple(
            point.daily_return for point in daily_by_scenario[scenario.scenario_id][final_start:]
        )
        for scenario in SCENARIOS
    }
    monte_carlo = _monte_carlo(final_returns, f"INDEPENDENT:{spec.strategy_id}")
    status, failures = _eligibility(
        [metrics[window_id]["C2_5"]["cumulative_return"] for window_id in WFA_TEST_IDS],
        metrics["FINAL_HOLDOUT"]["C2_5"]["cumulative_return"],
        metrics["FINAL_HOLDOUT"]["C5_10_STRESS"]["cumulative_return"],
        monte_carlo["C2_5"]["terminal_loss_probability"],
    )
    eligible = status == "PASS"
    windows = {
        "evaluation_count": 553,
        "observed_date_count": len(dates),
        "observed_dates_sha256": ALIGNED_DATES_SHA256,
        "segments": [
            {
                "segment_id": item.segment_id,
                "raw_start": item.raw_start,
                "raw_end": item.raw_end,
                "start_date": item.start_date,
                "end_date": item.end_date,
                "observation_count": item.observation_count,
                "role": item.role,
                "metrics_included": item.metrics_included,
            }
            for item in WINDOW_SPECS
        ],
        "metrics": metrics,
    }
    mc_wire_source = {
        "method": "CIRCULAR_MOVING_BLOCK_BOOTSTRAP_HMAC_SHA256_V1",
        "context": f"INDEPENDENT:{spec.strategy_id}",
        "seed_hex": MC_SEED_HEX,
        "trials": MC_TRIALS,
        "path_length": MC_PATH_LENGTH,
        "block_length": MC_BLOCK_LENGTH,
        "scenarios": monte_carlo,
    }
    record: dict[str, Any] = {
        "strategy_id": spec.strategy_id,
        "profile": spec.profile,
        "baseline_version": spec.baseline_version,
        "signal_asset": spec.signal_asset,
        "traded_asset": spec.traded_asset,
        "input_digest": spec.input_digest,
        "evidence_valid": True,
        "promotion_status": status,
        "r4a_handoff_eligible": eligible,
        "size_zero_required": not eligible,
        "failure_codes": list(failures),
        "windows": _to_wire(windows),
        "monte_carlo": _to_wire(mc_wire_source),
    }
    record["evidence_digest"] = _digest_value(record)
    return StrategyEvaluation(record, daily_by_scenario, metrics, monte_carlo)


def _spec_for(strategy_id: str) -> StrategySpec:
    if strategy_id == "TQQQ":
        return TQQQ_SPEC
    if strategy_id == "SOXL":
        return SOXL_SPEC
    _fail("STRATEGY_ID_INVALID")


def _invalid_strategy_record(strategy_id: str, failure_code: str) -> dict[str, Any]:
    spec = _spec_for(strategy_id)
    record: dict[str, Any] = {
        "strategy_id": spec.strategy_id,
        "profile": spec.profile,
        "baseline_version": spec.baseline_version,
        "signal_asset": spec.signal_asset,
        "traded_asset": spec.traded_asset,
        "input_digest": spec.input_digest,
        "evidence_valid": False,
        "promotion_status": "NOT_EVALUATED",
        "r4a_handoff_eligible": False,
        "size_zero_required": True,
        "failure_codes": [failure_code],
        "windows": None,
        "monte_carlo": None,
    }
    record["evidence_digest"] = _digest_value(record)
    return record


def _run_independent(
    tqqq_action: Callable[[], dict[str, Any]],
    soxl_action: Callable[[], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for strategy_id, action in (("TQQQ", tqqq_action), ("SOXL", soxl_action)):
        try:
            records.append(action())
        except R3EvidenceError as exc:
            records.append(_invalid_strategy_record(strategy_id, exc.code))
    return records[0], records[1]


def _load_tqqq() -> TqqqOfflineInput:
    try:
        source = load_tqqq_offline_input(TQQQ_IDENTITIES[1].path, TQQQ_IDENTITIES[0].path)
    except (OSError, ValueError, TypeError):
        _fail("TQQQ_INPUT_INVALID")
    if source.input_digest != TQQQ_SPEC.input_digest:
        _fail("TQQQ_INPUT_DIGEST_MISMATCH")
    return source


def _load_soxl() -> SoxlOfflineInput:
    try:
        source = load_soxl_offline_input(
            SOXL_IDENTITIES[1].path,
            SOXL_IDENTITIES[0].path,
            SOXL_IDENTITIES[2].path,
        )
    except (OSError, ValueError, TypeError):
        _fail("SOXL_INPUT_INVALID")
    if source.input_digest != SOXL_SPEC.input_digest:
        _fail("SOXL_INPUT_DIGEST_MISMATCH")
    return source


def _attempt_private_strategy(spec: StrategySpec) -> StrategyEvaluation:
    identities = TQQQ_IDENTITIES if spec.strategy_id == "TQQQ" else SOXL_IDENTITIES

    def action() -> StrategyEvaluation:
        source = _load_tqqq() if spec.strategy_id == "TQQQ" else _load_soxl()
        return _evaluate_loaded(spec, source)

    try:
        return _verified_call(identities, action)
    except R3EvidenceError as exc:
        return StrategyEvaluation(
            _invalid_strategy_record(spec.strategy_id, exc.code),
            None,
            None,
            None,
        )


def _final_points(evaluation: StrategyEvaluation, scenario: str) -> tuple[DailyEvidence, ...]:
    if evaluation.daily_by_scenario is None:
        _fail("STRATEGY_EVIDENCE_UNAVAILABLE")
    return evaluation.daily_by_scenario[scenario][627 - 200 :]


def _invalid_joint_record(failure_code: str, *, both_evidence_valid: bool) -> dict[str, Any]:
    record: dict[str, Any] = {
        "status": "INVALID_REPORT_ONLY" if both_evidence_valid else "NOT_RUN_EVIDENCE_INVALID",
        "semantics": "REPORT_ONLY_CONTEXT_NOT_A_GATE_FOR_R4A",
        "preconditions": {"both_evidence_valid": both_evidence_valid},
        "aligned_date_count": None,
        "aligned_dates_sha256": None,
        "scenarios": None,
        "gate_status": "NOT_APPLICABLE_REPORT_ONLY",
        "failure_codes": [failure_code],
    }
    record["evidence_digest"] = _digest_value(record)
    return record


def _build_joint_dependency(
    tqqq: StrategyEvaluation,
    soxl: StrategyEvaluation,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]] | None]:
    both_valid = bool(tqqq.record["evidence_valid"] and soxl.record["evidence_valid"])
    if not both_valid:
        record: dict[str, Any] = {
            "status": "NOT_RUN_EVIDENCE_INVALID",
            "semantics": "REPORT_ONLY_CONTEXT_NOT_A_GATE_FOR_R4A",
            "preconditions": {"both_evidence_valid": False},
            "aligned_date_count": None,
            "aligned_dates_sha256": None,
            "scenarios": None,
            "gate_status": "NOT_APPLICABLE_REPORT_ONLY",
            "failure_codes": [],
        }
        record["evidence_digest"] = _digest_value(record)
        return record, None

    t_returns = {
        scenario.scenario_id: tuple(point.daily_return for point in _final_points(tqqq, scenario.scenario_id))
        for scenario in SCENARIOS
    }
    s_returns = {
        scenario.scenario_id: tuple(point.daily_return for point in _final_points(soxl, scenario.scenario_id))
        for scenario in SCENARIOS
    }
    paired = _paired_monte_carlo(t_returns, s_returns)
    raw_scenarios: dict[str, dict[str, Any]] = {}
    zero_t = tqqq.window_metrics["FINAL_HOLDOUT"]["ZERO"]["cumulative_return"]  # type: ignore[index]
    zero_s = soxl.window_metrics["FINAL_HOLDOUT"]["ZERO"]["cumulative_return"]  # type: ignore[index]
    dates: tuple[str, ...] | None = None
    joint_failures: list[str] = []
    for scenario in SCENARIOS:
        t_points = _final_points(tqqq, scenario.scenario_id)
        s_points = _final_points(soxl, scenario.scenario_id)
        scenario_dates = tuple(point.date for point in t_points)
        if scenario_dates != tuple(point.date for point in s_points) or len(scenario_dates) != 126:
            _fail("JOINT_DATE_ALIGNMENT_INVALID")
        if dates is None:
            dates = scenario_dates
        metrics = _dependency_metrics(
            scenario_dates,
            tuple(point.daily_return for point in t_points),
            tuple(point.end_equity for point in t_points),
            tuple(point.daily_return for point in s_points),
            tuple(point.end_equity for point in s_points),
        )
        joint_failures.extend(metrics["failure_codes"])
        metrics["tqqq_return_degradation_vs_zero"] = (
            zero_t - tqqq.window_metrics["FINAL_HOLDOUT"][scenario.scenario_id]["cumulative_return"]  # type: ignore[index]
        )
        metrics["soxl_return_degradation_vs_zero"] = (
            zero_s - soxl.window_metrics["FINAL_HOLDOUT"][scenario.scenario_id]["cumulative_return"]  # type: ignore[index]
        )
        metrics["paired_monte_carlo"] = paired[scenario.scenario_id]
        raw_scenarios[scenario.scenario_id] = metrics
    assert dates is not None
    aligned_sha = _dates_sha256(dates)
    record = {
        "status": "READY_REPORT_ONLY",
        "semantics": "REPORT_ONLY_CONTEXT_NOT_A_GATE_FOR_R4A",
        "preconditions": {"both_evidence_valid": True},
        "aligned_date_count": 126,
        "aligned_dates_sha256": aligned_sha,
        "scenarios": _to_wire(raw_scenarios),
        "gate_status": "NOT_APPLICABLE_REPORT_ONLY",
        "failure_codes": sorted(set(joint_failures)),
    }
    record["evidence_digest"] = _digest_value(record)
    return record, raw_scenarios


def _distribution_digest(points: Sequence[DailyEvidence]) -> str:
    return _digest_value(
        [{"date": point.date, "daily_return": point.daily_return.hex()} for point in points]
    )


def _handoff_input(
    evaluation: StrategyEvaluation,
    dependency_ref: str | None,
) -> dict[str, Any]:
    record = evaluation.record
    base: dict[str, Any] = {
        "strategy_id": record["strategy_id"],
        "profile": record["profile"],
        "evidence_valid": record["evidence_valid"],
        "research_eligibility_status": record["promotion_status"],
        "eligible": record["r4a_handoff_eligible"],
        "size_zero_required": record["size_zero_required"],
        "failure_codes": record["failure_codes"],
        "evidence_digest": record["evidence_digest"],
        "input_digest": record["input_digest"],
        "sample_count": None,
        "oos_return_distribution_sha256": None,
        "wfa_test_distribution_sha256": None,
        "mean_daily_return": None,
        "final_holdout_sharpe": None,
        "annualized_volatility": None,
        "forecast_volatility": None,
        "forecast_volatility_method": "FINAL_OOS_SAMPLE_VOL_252_V1",
        "historical_max_drawdown_abs": None,
        "expected_shortfall_95": None,
        "stressed_expected_shortfall_95_abs": None,
        "stressed_loss_fraction_at_full_allocation": None,
        "trade_count": None,
        "annualized_turnover": None,
        "zero_to_stress_return_degradation": None,
        "cost_robustness_by_scenario": None,
        "mc_terminal_loss_probability": None,
        "mc_terminal_return_p05": None,
        "mc_max_drawdown_p95_abs": None,
        "daily_return_series_sha256": None,
        "dependency_risk_ref": dependency_ref,
    }
    if not record["evidence_valid"]:
        return base
    assert evaluation.window_metrics is not None
    assert evaluation.monte_carlo is not None
    c2 = evaluation.window_metrics["FINAL_HOLDOUT"]["C2_5"]
    stress = evaluation.window_metrics["FINAL_HOLDOUT"]["C5_10_STRESS"]
    zero = evaluation.window_metrics["FINAL_HOLDOUT"]["ZERO"]
    c2_mc = evaluation.monte_carlo["C2_5"]
    stress_mc = evaluation.monte_carlo["C5_10_STRESS"]
    final_c2 = _final_points(evaluation, "C2_5")
    wfa_points = tuple(
        point
        for window_id in WFA_TEST_IDS
        for point in evaluation.daily_by_scenario["C2_5"][  # type: ignore[index]
            next(item.raw_start for item in WINDOW_SPECS if item.segment_id == window_id) - 200 :
            next(item.raw_end for item in WINDOW_SPECS if item.segment_id == window_id) - 199
        ]
    )
    cost_robustness = {
        scenario.scenario_id: {
            "cumulative_return": evaluation.window_metrics["FINAL_HOLDOUT"][scenario.scenario_id]["cumulative_return"],
            "annualized_turnover": evaluation.window_metrics["FINAL_HOLDOUT"][scenario.scenario_id]["annualized_turnover"],
            "total_cost": evaluation.window_metrics["FINAL_HOLDOUT"][scenario.scenario_id]["total_cost"],
            "terminal_loss_probability": evaluation.monte_carlo[scenario.scenario_id]["terminal_loss_probability"],
        }
        for scenario in SCENARIOS
    }
    stressed_loss = max(
        abs(stress["expected_shortfall_95"]),
        abs(stress["max_drawdown"]),
        stress_mc["max_drawdown_abs_p95"],
    )
    base.update(
        {
            "sample_count": 126,
            "oos_return_distribution_sha256": _distribution_digest(final_c2),
            "wfa_test_distribution_sha256": _distribution_digest(wfa_points),
            "mean_daily_return": c2["mean_daily_return"],
            "final_holdout_sharpe": c2["sharpe"],
            "annualized_volatility": c2["annualized_volatility"],
            "forecast_volatility": c2["annualized_volatility"],
            "historical_max_drawdown_abs": abs(c2["max_drawdown"]),
            "expected_shortfall_95": c2["expected_shortfall_95"],
            "stressed_expected_shortfall_95_abs": abs(stress["expected_shortfall_95"]),
            "stressed_loss_fraction_at_full_allocation": stressed_loss,
            "trade_count": c2["trade_count"],
            "annualized_turnover": c2["annualized_turnover"],
            "zero_to_stress_return_degradation": zero["cumulative_return"] - stress["cumulative_return"],
            "cost_robustness_by_scenario": cost_robustness,
            "mc_terminal_loss_probability": c2_mc["terminal_loss_probability"],
            "mc_terminal_return_p05": c2_mc["terminal_cumulative_return_p05"],
            "mc_max_drawdown_p95_abs": c2_mc["max_drawdown_abs_p95"],
            "daily_return_series_sha256": _distribution_digest(final_c2),
        }
    )
    return _to_wire(base)  # type: ignore[return-value]


def build_r4_handoff(
    per_strategy_inputs: Sequence[dict[str, Any]],
    dependency_risk: dict[str, Any],
) -> dict[str, Any]:
    if (
        len(per_strategy_inputs) != 2
        or [item.get("strategy_id") for item in per_strategy_inputs] != ["TQQQ", "SOXL"]
        or "bundle_sha256" in dependency_risk
    ):
        _fail("R4_HANDOFF_INPUT_INVALID")
    return {
        "schema": R4_HANDOFF_SCHEMA,
        "scope": "RESEARCH_ONLY",
        "source_commit": SOURCE_COMMIT,
        "as_of_date": "2026-07-15",
        "reference_cost_scenario": "C2_5",
        "research_eligibility_profile_sha256": PROFILE_SHA256,
        "per_strategy_inputs": list(per_strategy_inputs),
        "dependency_risk": dependency_risk,
        "limitations": {
            "research_only": True,
            "provider_completeness": "unverified",
            "calendar_authority": "unverified",
        },
    }


def _r4_dependency(
    joint_record: dict[str, Any],
    raw_scenarios: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    if raw_scenarios is None:
        return {
            "status": joint_record["status"],
            "semantics": joint_record["semantics"],
            "aligned_dates_sha256": None,
            "sample_count": None,
            "covariance_matrix_2x2": None,
            "pearson_correlation": None,
            "common_drawdown_day_rate": None,
            "tail_co_loss_rate": None,
            "cost_stress_by_scenario": None,
        }
    reference = raw_scenarios["C2_5"]
    cost_stress = {
        name: {
            "sample_covariance": value["sample_covariance"],
            "pearson_correlation": value["pearson_correlation"],
            "common_drawdown_day_rate": value["both_below_own_peak_rate"],
            "tail_co_loss_rate": value["tail_co_loss_rate"],
            "tqqq_return_degradation_vs_zero": value["tqqq_return_degradation_vs_zero"],
            "soxl_return_degradation_vs_zero": value["soxl_return_degradation_vs_zero"],
        }
        for name, value in raw_scenarios.items()
    }
    return _to_wire(
        {
            "status": joint_record["status"],
            "semantics": joint_record["semantics"],
            "aligned_dates_sha256": joint_record["aligned_dates_sha256"],
            "sample_count": 126,
            "covariance_matrix_2x2": [
                [reference["tqqq_sample_variance"], reference["sample_covariance"]],
                [reference["sample_covariance"], reference["soxl_sample_variance"]],
            ],
            "pearson_correlation": reference["pearson_correlation"],
            "common_drawdown_day_rate": reference["both_below_own_peak_rate"],
            "tail_co_loss_rate": reference["tail_co_loss_rate"],
            "cost_stress_by_scenario": cost_stress,
        }
    )  # type: ignore[return-value]


def _input_identity(tqqq_valid: bool, soxl_valid: bool) -> dict[str, Any]:
    return {
        "contract_sha256": CONTRACT_SHA256,
        "worker_prompt_sha256": WORKER_PROMPT_SHA256,
        "source_commit": SOURCE_COMMIT,
        "source_module_sha256": SOURCE_MODULE_SHA256,
        "aligned_date_count": 753,
        "aligned_dates_sha256": ALIGNED_DATES_SHA256,
        "coverage": {"start": "2023-07-14", "end": "2026-07-15"},
        "tqqq": {
            "status": "VERIFIED" if tqqq_valid else "INVALID",
            "artifact_path": str(TQQQ_IDENTITIES[0].path),
            "artifact_sha256": TQQQ_IDENTITIES[0].sha256,
            "artifact_bytes": TQQQ_IDENTITIES[0].byte_count,
            "manifest_path": str(TQQQ_IDENTITIES[1].path),
            "manifest_sha256": TQQQ_IDENTITIES[1].sha256,
            "readback_path": None,
            "input_digest": TQQQ_SPEC.input_digest,
            "symbols": ["QQQ", "TQQQ"],
            "counts": {"QQQ": 753, "TQQQ": 753},
        },
        "soxl": {
            "status": "VERIFIED" if soxl_valid else "INVALID",
            "artifact_path": str(SOXL_IDENTITIES[0].path),
            "artifact_sha256": SOXL_IDENTITIES[0].sha256,
            "artifact_bytes": SOXL_IDENTITIES[0].byte_count,
            "manifest_path": str(SOXL_IDENTITIES[1].path),
            "manifest_sha256": SOXL_IDENTITIES[1].sha256,
            "readback_path": str(SOXL_IDENTITIES[2].path),
            "readback_sha256": SOXL_IDENTITIES[2].sha256,
            "input_digest": SOXL_SPEC.input_digest,
            "symbols": ["SOXX", "SOXL"],
            "counts": {"SOXX": 753, "SOXL": 753},
        },
        "limitations": {
            "research_only": True,
            "provider": "yahoo_chart",
            "price_field": "adjusted_close",
            "provider_completeness": "unverified",
            "calendar_authority": "unverified",
        },
    }

TOP_LEVEL_KEYS = {
    "schema",
    "contract_version",
    "source_commit",
    "method_digest",
    "threshold_profile",
    "input_identity",
    "strategies",
    "joint_dependency",
    "r4_handoff",
    "terminal",
}
STRATEGY_KEYS = {
    "strategy_id",
    "profile",
    "baseline_version",
    "signal_asset",
    "traded_asset",
    "input_digest",
    "evidence_valid",
    "promotion_status",
    "r4a_handoff_eligible",
    "size_zero_required",
    "failure_codes",
    "windows",
    "monte_carlo",
    "evidence_digest",
}
JOINT_KEYS = {
    "status",
    "semantics",
    "preconditions",
    "aligned_date_count",
    "aligned_dates_sha256",
    "scenarios",
    "gate_status",
    "failure_codes",
    "evidence_digest",
}
TERMINAL_KEYS = {
    "outcome",
    "failed_stage",
    "failure_codes",
    "eligible_strategies",
    "ineligible_strategies",
}
R4_HANDOFF_KEYS = {
    "schema",
    "scope",
    "source_commit",
    "as_of_date",
    "reference_cost_scenario",
    "research_eligibility_profile_sha256",
    "per_strategy_inputs",
    "dependency_risk",
    "limitations",
}
R4_INPUT_KEYS = {
    "strategy_id",
    "profile",
    "evidence_valid",
    "research_eligibility_status",
    "eligible",
    "size_zero_required",
    "failure_codes",
    "evidence_digest",
    "input_digest",
    "sample_count",
    "oos_return_distribution_sha256",
    "wfa_test_distribution_sha256",
    "mean_daily_return",
    "final_holdout_sharpe",
    "annualized_volatility",
    "forecast_volatility",
    "forecast_volatility_method",
    "historical_max_drawdown_abs",
    "expected_shortfall_95",
    "stressed_expected_shortfall_95_abs",
    "stressed_loss_fraction_at_full_allocation",
    "trade_count",
    "annualized_turnover",
    "zero_to_stress_return_degradation",
    "cost_robustness_by_scenario",
    "mc_terminal_loss_probability",
    "mc_terminal_return_p05",
    "mc_max_drawdown_p95_abs",
    "daily_return_series_sha256",
    "dependency_risk_ref",
}
R4_DEPENDENCY_KEYS = {
    "status",
    "semantics",
    "aligned_dates_sha256",
    "sample_count",
    "covariance_matrix_2x2",
    "pearson_correlation",
    "common_drawdown_day_rate",
    "tail_co_loss_rate",
    "cost_stress_by_scenario",
}
LIMITATION_KEYS = {"research_only", "provider_completeness", "calendar_authority"}
WINDOWS_KEYS = {"evaluation_count", "observed_date_count", "observed_dates_sha256", "segments", "metrics"}
SEGMENT_KEYS = {
    "segment_id",
    "raw_start",
    "raw_end",
    "start_date",
    "end_date",
    "observation_count",
    "role",
    "metrics_included",
}
WINDOW_METRIC_KEYS = {
    "observation_count",
    "start_date",
    "end_date",
    "start_equity",
    "end_equity",
    "cumulative_return",
    "return_product_cumulative_return",
    "annualized_return",
    "mean_daily_return",
    "annualized_volatility",
    "sharpe",
    "max_drawdown",
    "worst_daily_return",
    "expected_shortfall_95",
    "positive_day_rate",
    "trade_count",
    "buy_count",
    "sell_count",
    "gross_traded_notional_at_open",
    "turnover_ratio",
    "annualized_turnover",
    "commission_paid",
    "slippage_impact_vs_open",
    "total_cost",
    "terminal_exposure_open",
}
MC_KEYS = {"method", "context", "seed_hex", "trials", "path_length", "block_length", "scenarios"}
MC_SCENARIO_KEYS = {
    "trials",
    "path_length",
    "block_length",
    "terminal_cumulative_return_p05",
    "terminal_cumulative_return_p50",
    "terminal_cumulative_return_p95",
    "max_drawdown_abs_p50",
    "max_drawdown_abs_p95",
    "terminal_loss_probability",
}
INPUT_IDENTITY_KEYS = {
    "contract_sha256",
    "worker_prompt_sha256",
    "source_commit",
    "source_module_sha256",
    "aligned_date_count",
    "aligned_dates_sha256",
    "coverage",
    "tqqq",
    "soxl",
    "limitations",
}
TQQQ_IDENTITY_KEYS = {
    "status",
    "artifact_path",
    "artifact_sha256",
    "artifact_bytes",
    "manifest_path",
    "manifest_sha256",
    "readback_path",
    "input_digest",
    "symbols",
    "counts",
}
SOXL_IDENTITY_KEYS = TQQQ_IDENTITY_KEYS | {"readback_sha256"}
INPUT_LIMITATION_KEYS = {
    "research_only",
    "provider",
    "price_field",
    "provider_completeness",
    "calendar_authority",
}
JOINT_SCENARIO_KEYS = {
    "sample_count",
    "sample_covariance",
    "tqqq_sample_variance",
    "soxl_sample_variance",
    "pearson_correlation",
    "both_below_own_peak_count",
    "both_below_own_peak_rate",
    "tqqq_worst_drawdown_on_common_days",
    "soxl_worst_drawdown_on_common_days",
    "tail_co_loss_count",
    "tail_co_loss_rate",
    "tqqq_tail_co_loss_conditional_mean",
    "soxl_tail_co_loss_conditional_mean",
    "tail_co_loss_dates_sha256",
    "gate_status",
    "failure_codes",
    "tqqq_return_degradation_vs_zero",
    "soxl_return_degradation_vs_zero",
    "paired_monte_carlo",
}
PAIRED_MC_KEYS = {
    "trials",
    "path_length",
    "block_length",
    "tqqq_terminal_cumulative_return_p05",
    "tqqq_terminal_cumulative_return_p50",
    "tqqq_terminal_cumulative_return_p95",
    "soxl_terminal_cumulative_return_p05",
    "soxl_terminal_cumulative_return_p50",
    "soxl_terminal_cumulative_return_p95",
    "tqqq_max_drawdown_abs_p95",
    "soxl_max_drawdown_abs_p95",
    "both_terminal_loss_probability",
}
COST_ROBUSTNESS_KEYS = {
    "cumulative_return",
    "annualized_turnover",
    "total_cost",
    "terminal_loss_probability",
}
FORBIDDEN_KEYS = {
    "weight",
    "combined_signal",
    "combined_return",
    "joint_gate",
    "live_promotion",
    "fixed_currency_amount",
    "share_quantity",
    "order",
    "broker",
    "live",
    "position",
    "leverage",
    "risk_budget_increase",
    "bundle_sha256",
}


def _walk_keys(value: object) -> None:
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str or key in FORBIDDEN_KEYS:
                _fail("FORBIDDEN_BUNDLE_KEY")
            _walk_keys(child)
    elif type(value) is list:
        for child in value:
            _walk_keys(child)
    elif type(value) is float:
        _fail("RAW_FLOAT_IN_WIRE")


def _check_digest(record: dict[str, Any], code: str) -> None:
    candidate = dict(record)
    digest = candidate.pop("evidence_digest", None)
    if (
        type(digest) is not str
        or len(digest) != 64
        or digest != _digest_value(candidate)
    ):
        _fail(code)


def _validate_input_identity(value: object) -> None:
    if type(value) is not dict or set(value) != INPUT_IDENTITY_KEYS:
        _fail("INPUT_IDENTITY_SCHEMA_INVALID")
    if (
        value["contract_sha256"] != CONTRACT_SHA256
        or value["worker_prompt_sha256"] != WORKER_PROMPT_SHA256
        or value["source_commit"] != SOURCE_COMMIT
        or value["source_module_sha256"] != SOURCE_MODULE_SHA256
        or value["aligned_date_count"] != 753
        or value["aligned_dates_sha256"] != ALIGNED_DATES_SHA256
        or value["coverage"] != {"start": "2023-07-14", "end": "2026-07-15"}
        or type(value["tqqq"]) is not dict
        or set(value["tqqq"]) != TQQQ_IDENTITY_KEYS
        or type(value["soxl"]) is not dict
        or set(value["soxl"]) != SOXL_IDENTITY_KEYS
        or type(value["limitations"]) is not dict
        or set(value["limitations"]) != INPUT_LIMITATION_KEYS
    ):
        _fail("INPUT_IDENTITY_SCHEMA_INVALID")
    if value["tqqq"]["status"] not in {"VERIFIED", "INVALID"} or value["soxl"]["status"] not in {"VERIFIED", "INVALID"}:
        _fail("INPUT_IDENTITY_SCHEMA_INVALID")


def _validate_windows(value: object) -> None:
    if type(value) is not dict or set(value) != WINDOWS_KEYS:
        _fail("WINDOW_SCHEMA_INVALID")
    if (
        value["evaluation_count"] != 553
        or value["observed_date_count"] != 753
        or value["observed_dates_sha256"] != ALIGNED_DATES_SHA256
        or type(value["segments"]) is not list
        or len(value["segments"]) != len(WINDOW_SPECS)
        or type(value["metrics"]) is not dict
        or set(value["metrics"]) != set(METRIC_WINDOW_IDS)
    ):
        _fail("WINDOW_SCHEMA_INVALID")
    for expected, actual in zip(WINDOW_SPECS, value["segments"], strict=True):
        if type(actual) is not dict or set(actual) != SEGMENT_KEYS or actual != {
            "segment_id": expected.segment_id,
            "raw_start": expected.raw_start,
            "raw_end": expected.raw_end,
            "start_date": expected.start_date,
            "end_date": expected.end_date,
            "observation_count": expected.observation_count,
            "role": expected.role,
            "metrics_included": expected.metrics_included,
        }:
            _fail("WINDOW_SCHEMA_INVALID")
    for segment_id, scenarios in value["metrics"].items():
        spec = next(item for item in WINDOW_SPECS if item.segment_id == segment_id)
        if type(scenarios) is not dict or set(scenarios) != {item.scenario_id for item in SCENARIOS}:
            _fail("WINDOW_SCHEMA_INVALID")
        for metrics in scenarios.values():
            if (
                type(metrics) is not dict
                or set(metrics) != WINDOW_METRIC_KEYS
                or metrics["observation_count"] != spec.observation_count
                or metrics["start_date"] != spec.start_date
                or metrics["end_date"] != spec.end_date
            ):
                _fail("WINDOW_SCHEMA_INVALID")


def _validate_monte_carlo(value: object, strategy_id: str) -> None:
    if type(value) is not dict or set(value) != MC_KEYS:
        _fail("MONTE_CARLO_SCHEMA_INVALID")
    if (
        value["method"] != "CIRCULAR_MOVING_BLOCK_BOOTSTRAP_HMAC_SHA256_V1"
        or value["context"] != f"INDEPENDENT:{strategy_id}"
        or value["seed_hex"] != MC_SEED_HEX
        or value["trials"] != MC_TRIALS
        or value["path_length"] != MC_PATH_LENGTH
        or value["block_length"] != MC_BLOCK_LENGTH
        or type(value["scenarios"]) is not dict
        or set(value["scenarios"]) != {item.scenario_id for item in SCENARIOS}
    ):
        _fail("MONTE_CARLO_SCHEMA_INVALID")
    for scenario in value["scenarios"].values():
        if type(scenario) is not dict or set(scenario) != MC_SCENARIO_KEYS:
            _fail("MONTE_CARLO_SCHEMA_INVALID")


def _validate_joint_details(value: dict[str, Any]) -> None:
    if value["status"] == "READY_REPORT_ONLY":
        if (
            value["aligned_date_count"] != 126
            or type(value["aligned_dates_sha256"]) is not str
            or type(value["scenarios"]) is not dict
            or set(value["scenarios"]) != {item.scenario_id for item in SCENARIOS}
        ):
            _fail("JOINT_SCHEMA_INVALID")
        for scenario in value["scenarios"].values():
            if (
                type(scenario) is not dict
                or set(scenario) != JOINT_SCENARIO_KEYS
                or type(scenario["paired_monte_carlo"]) is not dict
                or set(scenario["paired_monte_carlo"]) != PAIRED_MC_KEYS
                or scenario["gate_status"] != "NOT_APPLICABLE_REPORT_ONLY"
            ):
                _fail("JOINT_SCHEMA_INVALID")
    elif value["scenarios"] is not None:
        _fail("JOINT_SCHEMA_INVALID")


def validate_bundle(bundle: object) -> None:
    if type(bundle) is not dict or set(bundle) != TOP_LEVEL_KEYS:
        _fail("BUNDLE_SCHEMA_INVALID")
    if (
        bundle["schema"] != BUNDLE_SCHEMA
        or bundle["contract_version"] != CONTRACT_VERSION
        or bundle["source_commit"] != SOURCE_COMMIT
        or bundle["method_digest"] != METHOD_DIGEST
        or bundle["threshold_profile"]
        != {
            "profile": THRESHOLD_PROFILE,
            "canonical_json": PROFILE_CANONICAL_JSON,
            "sha256": PROFILE_SHA256,
        }
        or type(bundle["input_identity"]) is not dict
    ):
        _fail("BUNDLE_SCHEMA_INVALID")
    _validate_input_identity(bundle["input_identity"])
    strategies = bundle["strategies"]
    if (
        type(strategies) is not list
        or len(strategies) != 2
        or [item.get("strategy_id") for item in strategies if type(item) is dict]
        != ["TQQQ", "SOXL"]
    ):
        _fail("STRATEGY_SCHEMA_INVALID")
    for strategy in strategies:
        if type(strategy) is not dict or set(strategy) != STRATEGY_KEYS:
            _fail("STRATEGY_SCHEMA_INVALID")
        _check_digest(strategy, "STRATEGY_DIGEST_INVALID")
        evidence_valid = strategy["evidence_valid"]
        status = strategy["promotion_status"]
        eligible = strategy["r4a_handoff_eligible"]
        if (
            type(evidence_valid) is not bool
            or status not in {"PASS", "FAIL", "NOT_EVALUATED"}
            or type(eligible) is not bool
            or type(strategy["size_zero_required"]) is not bool
            or eligible != (evidence_valid and status == "PASS")
            or strategy["size_zero_required"] == eligible
            or (not evidence_valid and status != "NOT_EVALUATED")
            or (evidence_valid and status == "NOT_EVALUATED")
            or type(strategy["failure_codes"]) is not list
        ):
            _fail("STRATEGY_STATE_INVALID")
        if evidence_valid and (strategy["windows"] is None or strategy["monte_carlo"] is None):
            _fail("STRATEGY_STATE_INVALID")
        if not evidence_valid and (strategy["windows"] is not None or strategy["monte_carlo"] is not None):
            _fail("STRATEGY_STATE_INVALID")
        if evidence_valid:
            _validate_windows(strategy["windows"])
            _validate_monte_carlo(strategy["monte_carlo"], strategy["strategy_id"])

    joint = bundle["joint_dependency"]
    if type(joint) is not dict or set(joint) != JOINT_KEYS:
        _fail("JOINT_SCHEMA_INVALID")
    _check_digest(joint, "JOINT_DIGEST_INVALID")
    if (
        joint["semantics"] != "REPORT_ONLY_CONTEXT_NOT_A_GATE_FOR_R4A"
        or joint["gate_status"] != "NOT_APPLICABLE_REPORT_ONLY"
        or joint["status"]
        not in {"READY_REPORT_ONLY", "NOT_RUN_EVIDENCE_INVALID", "INVALID_REPORT_ONLY"}
    ):
        _fail("JOINT_STATE_INVALID")
    both_valid = all(item["evidence_valid"] for item in strategies)
    if both_valid and joint["status"] == "NOT_RUN_EVIDENCE_INVALID":
        _fail("JOINT_STATE_INVALID")
    if not both_valid and joint["status"] != "NOT_RUN_EVIDENCE_INVALID":
        _fail("JOINT_STATE_INVALID")
    _validate_joint_details(joint)

    handoff = bundle["r4_handoff"]
    if type(handoff) is not dict or set(handoff) != R4_HANDOFF_KEYS:
        _fail("R4_HANDOFF_SCHEMA_INVALID")
    if (
        handoff["schema"] != R4_HANDOFF_SCHEMA
        or handoff["scope"] != "RESEARCH_ONLY"
        or handoff["source_commit"] != SOURCE_COMMIT
        or handoff["as_of_date"] != "2026-07-15"
        or handoff["reference_cost_scenario"] != "C2_5"
        or handoff["research_eligibility_profile_sha256"] != PROFILE_SHA256
        or type(handoff["per_strategy_inputs"]) is not list
        or len(handoff["per_strategy_inputs"]) != 2
        or type(handoff["dependency_risk"]) is not dict
        or set(handoff["dependency_risk"]) != R4_DEPENDENCY_KEYS
        or type(handoff["limitations"]) is not dict
        or set(handoff["limitations"]) != LIMITATION_KEYS
    ):
        _fail("R4_HANDOFF_SCHEMA_INVALID")
    for strategy, handoff_input in zip(strategies, handoff["per_strategy_inputs"], strict=True):
        if type(handoff_input) is not dict or set(handoff_input) != R4_INPUT_KEYS:
            _fail("R4_HANDOFF_SCHEMA_INVALID")
        if (
            handoff_input["strategy_id"] != strategy["strategy_id"]
            or handoff_input["profile"] != strategy["profile"]
            or handoff_input["evidence_valid"] != strategy["evidence_valid"]
            or handoff_input["research_eligibility_status"] != strategy["promotion_status"]
            or handoff_input["eligible"] != strategy["r4a_handoff_eligible"]
            or handoff_input["size_zero_required"] != strategy["size_zero_required"]
            or handoff_input["evidence_digest"] != strategy["evidence_digest"]
            or handoff_input["input_digest"] != strategy["input_digest"]
            or handoff_input["forecast_volatility_method"] != "FINAL_OOS_SAMPLE_VOL_252_V1"
        ):
            _fail("R4_HANDOFF_STATE_INVALID")
    if handoff["dependency_risk"]["semantics"] != "REPORT_ONLY_CONTEXT_NOT_A_GATE_FOR_R4A":
        _fail("R4_HANDOFF_STATE_INVALID")

    terminal = bundle["terminal"]
    if type(terminal) is not dict or set(terminal) != TERMINAL_KEYS:
        _fail("TERMINAL_SCHEMA_INVALID")
    eligible_ids = [item["strategy_id"] for item in strategies if item["r4a_handoff_eligible"]]
    ineligible_ids = [item["strategy_id"] for item in strategies if not item["r4a_handoff_eligible"]]
    if (
        terminal["outcome"]
        not in {"R3_EVIDENCE_READY", "R3_SHARED_EVIDENCE_INVALID", "DESIGN_BLOCKED"}
        or terminal["eligible_strategies"] != eligible_ids
        or terminal["ineligible_strategies"] != ineligible_ids
    ):
        _fail("TERMINAL_STATE_INVALID")
    _walk_keys(bundle)


def _strict_json_load(raw: bytes) -> dict[str, Any]:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail("DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def reject_constant(_: str) -> NoReturn:
        _fail("NONFINITE_JSON_VALUE")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=object_pairs, parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError):
        _fail("STRICT_JSON_INVALID")
    if type(value) is not dict:
        _fail("STRICT_JSON_INVALID")
    return value


@dataclass(frozen=True, slots=True)
class PersistedPaths:
    bundle: Path
    sidecar: Path
    readback: Path


def _paths(output_root: Path) -> PersistedPaths:
    return PersistedPaths(
        output_root / "r3_joint_evidence_bundle.json",
        output_root / "r3_joint_evidence_bundle.sha256",
        output_root / "r3_joint_evidence_bundle.readback.json",
    )


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, raw: bytes) -> bool:
    if path.exists():
        try:
            existing = path.read_bytes()
        except OSError:
            _fail("EXISTING_ARTIFACT_DIFFERS")
        if existing != raw:
            _fail("EXISTING_ARTIFACT_DIFFERS")
        return False
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        _fail("ATOMIC_WRITE_FAILED")
    return True


def load_persisted_bundle(output_root: str | Path = DEFAULT_OUTPUT_ROOT) -> dict[str, Any]:
    paths = _paths(Path(output_root))
    try:
        sidecar_raw = paths.sidecar.read_bytes()
    except OSError:
        _fail("SIDECAR_INVALID")
    if (
        len(sidecar_raw) != 65
        or sidecar_raw[-1:] != b"\n"
        or any(byte not in b"0123456789abcdef" for byte in sidecar_raw[:-1])
    ):
        _fail("SIDECAR_INVALID")
    expected_sha = sidecar_raw[:-1].decode("ascii")
    try:
        persisted = paths.bundle.read_bytes()
    except OSError:
        _fail("BUNDLE_READ_FAILED")
    if hashlib.sha256(persisted).hexdigest() != expected_sha:
        _fail("SIDECAR_SHA_MISMATCH")
    payload = _strict_json_load(persisted)
    validate_bundle(payload)
    if _canonical_bytes(payload) != persisted:
        _fail("CANONICAL_READBACK_MISMATCH")
    return payload


def persist_bundle(
    bundle: dict[str, Any], output_root: str | Path = DEFAULT_OUTPUT_ROOT
) -> PersistedPaths:
    validate_bundle(bundle)
    root = Path(output_root)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        _fail("OUTPUT_ROOT_INVALID")
    paths = _paths(root)
    persisted = _canonical_bytes(bundle)
    bundle_sha = hashlib.sha256(persisted).hexdigest()
    sidecar = (bundle_sha + "\n").encode("ascii")
    readback = {
        "schema": READBACK_SCHEMA,
        "bundle_sha256": bundle_sha,
        "bundle_bytes": len(persisted),
        "persisted_bytes_sha256_equal": True,
        "sidecar_verified_before_parse": True,
        "strict_schema_valid": True,
        "canonical_bytes_equal": True,
        "nested_digests_valid": True,
    }
    readback_bytes = _canonical_bytes(readback)
    bundle_created = sidecar_created = False
    readback_preexisting = paths.readback.exists()
    try:
        bundle_created = _atomic_write(paths.bundle, persisted)
        sidecar_created = _atomic_write(paths.sidecar, sidecar)
        if load_persisted_bundle(root) != bundle:
            _fail("READBACK_PAYLOAD_MISMATCH")
        _atomic_write(paths.readback, readback_bytes)
    except R3EvidenceError:
        if sidecar_created:
            paths.sidecar.unlink(missing_ok=True)
        if not readback_preexisting:
            paths.readback.unlink(missing_ok=True)
        if bundle_created and not paths.sidecar.exists():
            # An orphan bundle is not an authoritative artifact without its sidecar.
            pass
        raise
    return paths


def _build_bundle() -> dict[str, Any]:
    tqqq = _attempt_private_strategy(TQQQ_SPEC)
    soxl = _attempt_private_strategy(SOXL_SPEC)
    try:
        joint, raw_joint = _build_joint_dependency(tqqq, soxl)
    except R3EvidenceError as exc:
        joint = _invalid_joint_record(
            exc.code,
            both_evidence_valid=bool(
                tqqq.record["evidence_valid"] and soxl.record["evidence_valid"]
            ),
        )
        raw_joint = None
    dependency = _r4_dependency(joint, raw_joint)
    dependency_ref = joint["evidence_digest"] if joint["status"] == "READY_REPORT_ONLY" else None
    handoff_inputs = [
        _handoff_input(tqqq, dependency_ref),
        _handoff_input(soxl, dependency_ref),
    ]
    handoff = build_r4_handoff(handoff_inputs, dependency)
    strategies = [tqqq.record, soxl.record]
    eligible = [item["strategy_id"] for item in strategies if item["r4a_handoff_eligible"]]
    ineligible = [item["strategy_id"] for item in strategies if not item["r4a_handoff_eligible"]]
    bundle = {
        "schema": BUNDLE_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "source_commit": SOURCE_COMMIT,
        "method_digest": METHOD_DIGEST,
        "threshold_profile": {
            "profile": THRESHOLD_PROFILE,
            "canonical_json": PROFILE_CANONICAL_JSON,
            "sha256": PROFILE_SHA256,
        },
        "input_identity": _input_identity(
            bool(tqqq.record["evidence_valid"]),
            bool(soxl.record["evidence_valid"]),
        ),
        "strategies": strategies,
        "joint_dependency": joint,
        "r4_handoff": handoff,
        "terminal": {
            "outcome": "R3_EVIDENCE_READY",
            "failed_stage": None,
            "failure_codes": [],
            "eligible_strategies": eligible,
            "ineligible_strategies": ineligible,
        },
    }
    validate_bundle(bundle)
    return bundle


def run_private_r3(
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> tuple[dict[str, Any], PersistedPaths]:
    if (
        hashlib.sha256(PROFILE_CANONICAL_JSON.encode()).hexdigest() != PROFILE_SHA256
        or json.dumps(THRESHOLD_PROFILE, sort_keys=True, separators=(",", ":"))
        != PROFILE_CANONICAL_JSON
    ):
        _fail("PROFILE_IDENTITY_MISMATCH")
    research_root = Path(__file__).resolve().parent
    shared_identities = (
        FileIdentity(CONTRACT_PATH, CONTRACT_SHA256),
        FileIdentity(WORKER_PROMPT_PATH, WORKER_PROMPT_SHA256),
        *(
            FileIdentity(research_root / name, digest)
            for name, digest in SOURCE_MODULE_SHA256.items()
        ),
    )
    bundle = _verified_call(shared_identities, _build_bundle)
    paths = persist_bundle(bundle, output_root)
    return bundle, paths
