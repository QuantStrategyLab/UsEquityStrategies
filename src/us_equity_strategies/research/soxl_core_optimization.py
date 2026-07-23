"""Fixed, offline-only SOXL SMA sensitivity evidence."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import math
from pathlib import Path
from typing import Any, NoReturn, Sequence

from .soxl_soxx_offline_input_contract import InputRow, OfflineInput
from .soxl_soxx_typed_baseline_result import run_typed_baseline


SCHEMA = "qsl.research.soxl_core_optimization.v1"
READBACK_SCHEMA = "qsl.research.soxl_core_optimization_readback.v1"
CANDIDATE_WINDOWS = (140, 160, 180, 200)
BASELINE_WINDOW_DAYS = 200
INITIAL_EQUITY = 100_000.0
INPUT_COLUMNS = ("symbol", "as_of", "open", "high", "low", "close", "volume")
EXPECTED_INPUT_DIGEST = "78c056c9a4541b7612b4f077ca25df6093aa6eb2f17783097c5b5f83a31dd5c6"
EXPECTED_ARTIFACT_SHA256 = "6eb44951f7b16b7369df2d8d0fcce08b85d44ad3b758381139a027a53dd5f36c"
EXPECTED_MANIFEST_SHA256 = "8fe988353a6bc0f3642e69cc7f58c180df59ebb7ff62d6b986aba314fb9db81b"
EXPECTED_READBACK_SHA256 = "94bef6a1d27a4487d13500242fa24a183ec388318bcab59dace21d32235b3dd2"
EXPECTED_SOURCE_BLOBS = {
    "soxl_soxx_offline_input_contract.py": "b4a16842c33d39851724fa31993001cd27a4c986",
    "soxl_soxx_typed_baseline_result.py": "aa1b43a9e5ab59b34d41932b3b18653451ffe46b",
    "r3_joint_evidence.py": "118553cada8800dde80c30bbca5927da342b1e85",
}
PLUGIN_CONTROL = {"state": "ABSENT", "enabled": False, "optimization_eligible": False}
MC_SEED_HEX = "08a73485a70548df5262ad66ac86e02c0c5cc6255469156832aec2e86b501e2b"
MC_TRIALS = 10_000
MC_PATH_LENGTH = 126
MC_BLOCK_LENGTH = 12
WINDOW_SPECS = (
    ("SMA_WARMUP", 0, 199), ("F1_TRAIN", 200, 368), ("F1_EMBARGO_1", 369, 369),
    ("F1_VALIDATION", 370, 411), ("F1_EMBARGO_2", 412, 412), ("F1_TEST", 413, 454),
    ("F2_TRAIN", 200, 454), ("F2_EMBARGO_1", 455, 455), ("F2_VALIDATION", 456, 497),
    ("F2_EMBARGO_2", 498, 498), ("F2_TEST", 499, 540), ("F3_TRAIN", 200, 540),
    ("F3_EMBARGO_1", 541, 541), ("F3_VALIDATION", 542, 583), ("F3_EMBARGO_2", 584, 584),
    ("F3_TEST", 585, 626), ("FINAL_HOLDOUT", 627, 752),
)
VALIDATION_SEGMENTS = ("F1_VALIDATION", "F2_VALIDATION", "F3_VALIDATION")
TEST_SEGMENTS = ("F1_TEST", "F2_TEST", "F3_TEST")


class OptimizationError(ValueError):
    """Sanitized optimization boundary error."""


def _fail(code: str) -> NoReturn:
    raise OptimizationError(code) from None


@dataclass(frozen=True, slots=True)
class CostScenario:
    scenario_id: str
    commission_bps: int
    slippage_bps: int


SCENARIOS = (
    CostScenario("ZERO", 0, 0), CostScenario("C1_2", 1, 2),
    CostScenario("C2_5", 2, 5), CostScenario("C5_10_STRESS", 5, 10),
)


@dataclass(frozen=True, slots=True)
class DailyPoint:
    date: str
    start_equity: float
    end_equity: float
    cash: float
    quantity: float
    daily_return: float
    transition: bool
    commission_paid: float
    slippage_impact_vs_open: float
    gross_traded_notional_at_open: float


@dataclass(frozen=True, slots=True)
class PersistedPaths:
    bundle: Path
    sidecar: Path
    readback: Path


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode() + b"\n"
    except (TypeError, ValueError):
        _fail("CANONICAL_JSON_INVALID")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)[:-1]).hexdigest()


def _wire(value: Any) -> Any:
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            _fail("NONFINITE_VALUE")
        return value.hex()
    if type(value) in (tuple, list):
        return [_wire(item) for item in value]
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            _fail("WIRE_KEY_INVALID")
        return {key: _wire(item) for key, item in value.items()}
    _fail("WIRE_TYPE_INVALID")


def _typed_rows(source: object, expected_input_digest: str) -> tuple[tuple[InputRow, ...], tuple[InputRow, ...]]:
    if (
        type(source) is not OfflineInput
        or source.input_digest != expected_input_digest
        or type(source.rows) is not tuple
        or len(source.rows) != 1506
        or any(type(row) is not InputRow for row in source.rows)
    ):
        _fail("INPUT_IDENTITY_INVALID")
    soxx = tuple(row for row in source.rows if row.symbol == "SOXX")
    soxl = tuple(row for row in source.rows if row.symbol == "SOXL")
    if (
        len(soxx) != 753 or len(soxl) != 753
        or tuple(row.as_of for row in soxx) != tuple(row.as_of for row in soxl)
        or tuple(row.as_of for row in soxx) != tuple(sorted(row.as_of for row in soxx))
    ):
        _fail("INPUT_SCHEMA_INVALID")
    for row in (*soxx, *soxl):
        if not all(math.isfinite(value) and value > 0.0 for value in (row.open, row.high, row.low, row.close)):
            _fail("INPUT_VALUES_INVALID")
    return soxx, soxl


def _canonical_input_bytes(rows: tuple[InputRow, ...]) -> bytes:
    lines = [",".join(INPUT_COLUMNS)]
    for row in rows:
        lines.append(
            ",".join(
                (row.symbol, row.as_of, *(format(value, ".17g") for value in (row.open, row.high, row.low, row.close, row.volume)))
            )
        )
    return ("\n".join(lines) + "\n").encode()


def _verify_immutable_input(source: OfflineInput) -> None:
    if type(source.canonical_bytes) is not bytes or source.canonical_bytes != _canonical_input_bytes(source.rows):
        _fail("INPUT_CANONICAL_BYTES_MISMATCH")
    if hashlib.sha256(source.canonical_bytes).hexdigest() != EXPECTED_ARTIFACT_SHA256:
        _fail("INPUT_ARTIFACT_SHA256_MISMATCH")


def simulate_candidate(source: OfflineInput, window_days: int, scenario: CostScenario) -> tuple[DailyPoint, ...]:
    """Simulate one frozen SMA candidate with common start and R3 adverse fills."""
    if window_days not in CANDIDATE_WINDOWS or type(scenario) is not CostScenario or scenario not in SCENARIOS:
        _fail("SIMULATION_CONTRACT_INVALID")
    soxx, soxl = _typed_rows(source, EXPECTED_INPUT_DIGEST)
    cash = INITIAL_EQUITY
    quantity = 0.0
    previous_equity = INITIAL_EQUITY
    points: list[DailyPoint] = []
    commission_rate = scenario.commission_bps / 10_000.0
    slippage_rate = scenario.slippage_bps / 10_000.0
    for execution_index in range(BASELINE_WINDOW_DAYS, len(soxx)):
        signal_window = soxx[execution_index - window_days:execution_index]
        risk_on = soxx[execution_index - 1].close >= math.fsum(row.close for row in signal_window) / window_days
        execution = soxl[execution_index]
        opening_equity = cash + quantity * execution.open
        if not math.isfinite(opening_equity) or opening_equity <= 0.0:
            _fail("SIMULATION_EQUITY_INVALID")
        transition = risk_on != (quantity > 0.0)
        commission = 0.0
        slippage = 0.0
        gross_notional = 0.0
        if transition and risk_on:
            if scenario.scenario_id == "ZERO":
                quantity = opening_equity / execution.open
            else:
                fill = execution.open * (1.0 + slippage_rate)
                quantity = opening_equity / (fill * (1.0 + commission_rate))
                commission = quantity * fill * commission_rate
                slippage = quantity * (fill - execution.open)
            gross_notional = quantity * execution.open
            cash = 0.0
        elif transition:
            sold_quantity = quantity
            if scenario.scenario_id == "ZERO":
                cash = opening_equity
            else:
                fill = execution.open * (1.0 - slippage_rate)
                proceeds = sold_quantity * fill
                commission = proceeds * commission_rate
                slippage = sold_quantity * (execution.open - fill)
                cash = proceeds - commission
            gross_notional = sold_quantity * execution.open
            quantity = 0.0
        end_equity = cash + quantity * execution.close
        if not math.isfinite(end_equity) or end_equity <= 0.0:
            _fail("SIMULATION_EQUITY_INVALID")
        points.append(DailyPoint(execution.as_of, previous_equity, end_equity, cash, quantity, end_equity / previous_equity - 1.0, transition, commission, slippage, gross_notional))
        previous_equity = end_equity
    return tuple(points)


def _window_metrics(points: Sequence[DailyPoint], raw_start: int, raw_end: int) -> dict[str, float | int]:
    selected = points[raw_start - BASELINE_WINDOW_DAYS:raw_end - BASELINE_WINDOW_DAYS + 1]
    if len(selected) != raw_end - raw_start + 1:
        _fail("WINDOW_BOUNDARY_INVALID")
    returns = tuple(point.daily_return for point in selected)
    mean = math.fsum(returns) / len(returns)
    variance = math.fsum((item - mean) ** 2 for item in returns) / (len(returns) - 1)
    if variance < 0.0:
        _fail("RETURN_VARIANCE_INVALID")
    start_equity, end_equity = selected[0].start_equity, selected[-1].end_equity
    peak = start_equity
    drawdowns: list[float] = []
    for point in selected:
        peak = max(peak, point.end_equity)
        drawdowns.append(point.end_equity / peak - 1.0)
    tail_count = math.ceil(len(returns) * 0.05)
    annualized_volatility = math.sqrt(variance) * math.sqrt(252.0)
    return {
        "observation_count": len(selected), "cumulative_return": end_equity / start_equity - 1.0,
        "max_drawdown": min(drawdowns), "annualized_volatility": annualized_volatility,
        "expected_shortfall_95": math.fsum(sorted(returns)[:tail_count]) / tail_count,
        "sharpe": 0.0 if annualized_volatility == 0.0 else mean / math.sqrt(variance) * math.sqrt(252.0),
        "trade_count": sum(point.transition for point in selected),
        "annualized_turnover": math.fsum(point.gross_traded_notional_at_open for point in selected) / start_equity * 252.0 / len(selected),
        "total_cost": math.fsum(point.commission_paid + point.slippage_impact_vs_open for point in selected),
    }


def _median(values: Sequence[float]) -> float:
    if len(values) != 3 or any(not math.isfinite(value) for value in values):
        _fail("VALIDATION_METRICS_INVALID")
    return sorted(values)[1]


def _select_validation_winner(metrics_by_window: dict[int, Sequence[dict[str, float]]]) -> int:
    """Use only the three validation folds and the preregistered tie order."""
    if set(metrics_by_window) != set(CANDIDATE_WINDOWS):
        _fail("VALIDATION_CANDIDATES_INVALID")
    ranked: list[tuple[tuple[float, float, float, int, int], int]] = []
    for window in CANDIDATE_WINDOWS:
        metrics = metrics_by_window[window]
        if len(metrics) != 3 or any(set(("sharpe", "cumulative_return", "max_drawdown")) - set(item) for item in metrics):
            _fail("VALIDATION_METRICS_INVALID")
        median_sharpe = _median([float(item["sharpe"]) for item in metrics])
        median_return = _median([float(item["cumulative_return"]) for item in metrics])
        median_abs_drawdown = _median([abs(float(item["max_drawdown"])) for item in metrics])
        ranked.append(((-median_sharpe, -median_return, median_abs_drawdown, abs(window - BASELINE_WINDOW_DAYS), 0 if window == BASELINE_WINDOW_DAYS else 1), window))
    return min(ranked)[1]


def _sample_index(trial: int, block: int, population: int) -> int:
    seed = bytes.fromhex(MC_SEED_HEX)
    limit = 2**64 - (2**64 % population)
    counter = 0
    while True:
        message = f"R3-MBB-V1\\0INDEPENDENT:SOXL\\0{trial}\\0{block}\\0{counter}".encode()
        value = int.from_bytes(hmac.new(seed, message, hashlib.sha256).digest()[:8], "big")
        if value < limit:
            return value % population
        counter += 1


def _terminal_loss_probability(returns: Sequence[float]) -> float:
    if len(returns) != MC_PATH_LENGTH:
        _fail("MONTE_CARLO_INPUT_INVALID")
    losses = 0
    for trial in range(MC_TRIALS):
        equity = 1.0
        for block in range(math.ceil(MC_PATH_LENGTH / MC_BLOCK_LENGTH)):
            start = _sample_index(trial, block, MC_PATH_LENGTH)
            for offset in range(MC_BLOCK_LENGTH):
                if block * MC_BLOCK_LENGTH + offset == MC_PATH_LENGTH:
                    break
                equity *= 1.0 + returns[(start + offset) % MC_PATH_LENGTH]
        losses += equity < 1.0
    return losses / MC_TRIALS


def _later_predicates(
    *,
    winner: int,
    baseline: int,
    winner_validation_sharpe: float,
    baseline_validation_sharpe: float,
    selected_wfa_c2_5_returns: Sequence[float],
    final_c2_5_return: float,
    baseline_final_c2_5_return: float,
    final_c2_5_drawdown: float,
    baseline_final_c2_5_drawdown: float,
    final_stress_return: float,
    terminal_loss_probability: float,
) -> tuple[bool, tuple[str, ...]]:
    if len(selected_wfa_c2_5_returns) != 3:
        _fail("ELIGIBILITY_INPUT_INVALID")
    failures: list[str] = []
    if winner == baseline:
        failures.append("WINNER_IS_BASELINE")
    if not winner_validation_sharpe > baseline_validation_sharpe:
        failures.append("VALIDATION_SHARPE_NOT_STRICTLY_ABOVE_BASELINE")
    if sum(item > 0.0 for item in selected_wfa_c2_5_returns) < 2:
        failures.append("WFA_C2_5_PASSING_WINDOWS_BELOW_MINIMUM")
    if not final_c2_5_return > 0.0:
        failures.append("FINAL_HOLDOUT_C2_5_RETURN_NOT_STRICTLY_POSITIVE")
    if not final_stress_return > 0.0:
        failures.append("FINAL_HOLDOUT_C5_10_STRESS_RETURN_NOT_STRICTLY_POSITIVE")
    if not terminal_loss_probability < 0.5:
        failures.append("MC_C2_5_TERMINAL_LOSS_PROBABILITY_NOT_STRICTLY_BELOW_HALF")
    if not final_c2_5_return > baseline_final_c2_5_return:
        failures.append("FINAL_HOLDOUT_C2_5_NOT_STRICTLY_ABOVE_BASELINE")
    if abs(final_c2_5_drawdown) > abs(baseline_final_c2_5_drawdown):
        failures.append("FINAL_HOLDOUT_C2_5_DRAWDOWN_WORSE_THAN_BASELINE")
    return (not failures, tuple(failures))


def _invalid(code: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA, "evidence_valid": False, "failure_codes": [code], "outcome": "NO_IMPROVEMENT",
        "research_recommendation": None, "research_only": True, "live_adoption_authorized": False,
        "size_zero_required": True, "plugin_control": dict(PLUGIN_CONTROL),
    }


def _segments() -> dict[str, tuple[int, int]]:
    return {name: (start, end) for name, start, end in WINDOW_SPECS}


def run_soxl_core_optimization(
    source: object,
    *,
    plugin_control: object = PLUGIN_CONTROL,
    expected_input_digest: str = EXPECTED_INPUT_DIGEST,
) -> dict[str, Any]:
    """Evaluate fixed SOXL candidates; the result has no adoption authority."""
    if plugin_control != PLUGIN_CONTROL:
        return _invalid("PLUGIN_CONTROL_NOT_ABSENT")
    if expected_input_digest != EXPECTED_INPUT_DIGEST:
        return _invalid("INPUT_IDENTITY_MISMATCH")
    try:
        _typed_rows(source, expected_input_digest)
        assert type(source) is OfflineInput
        _verify_immutable_input(source)
        simulations = {
            window: {scenario.scenario_id: simulate_candidate(source, window, scenario) for scenario in SCENARIOS}
            for window in CANDIDATE_WINDOWS
        }
        baseline = run_typed_baseline(source)
        zero = simulations[BASELINE_WINDOW_DAYS]["ZERO"]
        if len(zero) != len(baseline.equity_curve) or any(
            actual.end_equity.hex() != expected.equity.hex()
            or actual.cash.hex() != expected.cash.hex()
            or actual.quantity.hex() != expected.soxl_quantity.hex()
            for actual, expected in zip(zero, baseline.equity_curve, strict=True)
        ):
            _fail("SMA200_ZERO_PARITY_FAILED")

        segments = _segments()
        validation = {
            window: tuple(_window_metrics(simulations[window]["C2_5"], *segments[name]) for name in VALIDATION_SEGMENTS)
            for window in CANDIDATE_WINDOWS
        }
        winner = _select_validation_winner(validation)
        baseline_validation_sharpe = _median([float(item["sharpe"]) for item in validation[BASELINE_WINDOW_DAYS]])
        winner_validation_sharpe = _median([float(item["sharpe"]) for item in validation[winner]])

        validation_records = {
            str(window): {
                "sma_window_days": window,
                "c2_5_validation": list(validation[window]),
                "scenario_cost_robustness": {
                    scenario.scenario_id: [
                        _window_metrics(simulations[window][scenario.scenario_id], *segments[name])
                        for name in VALIDATION_SEGMENTS
                    ]
                    for scenario in SCENARIOS
                },
            }
            for window in CANDIDATE_WINDOWS
        }

        selected_tests = tuple(
            float(_window_metrics(simulations[winner]["C2_5"], *segments[name])["cumulative_return"])
            for name in TEST_SEGMENTS
        )
        final_winner_c2_5 = _window_metrics(simulations[winner]["C2_5"], *segments["FINAL_HOLDOUT"])
        final_baseline_c2_5 = _window_metrics(simulations[BASELINE_WINDOW_DAYS]["C2_5"], *segments["FINAL_HOLDOUT"])
        final_winner_stress = _window_metrics(simulations[winner]["C5_10_STRESS"], *segments["FINAL_HOLDOUT"])
        final_returns = tuple(point.daily_return for point in simulations[winner]["C2_5"][627 - BASELINE_WINDOW_DAYS:])
        loss_probability = _terminal_loss_probability(final_returns)
        candidate_found, failures = _later_predicates(
            winner=winner,
            baseline=BASELINE_WINDOW_DAYS,
            winner_validation_sharpe=winner_validation_sharpe,
            baseline_validation_sharpe=baseline_validation_sharpe,
            selected_wfa_c2_5_returns=selected_tests,
            final_c2_5_return=float(final_winner_c2_5["cumulative_return"]),
            baseline_final_c2_5_return=float(final_baseline_c2_5["cumulative_return"]),
            final_c2_5_drawdown=float(final_winner_c2_5["max_drawdown"]),
            baseline_final_c2_5_drawdown=float(final_baseline_c2_5["max_drawdown"]),
            final_stress_return=float(final_winner_stress["cumulative_return"]),
            terminal_loss_probability=loss_probability,
        )
        return {
            "schema": SCHEMA, "evidence_valid": True, "failure_codes": list(failures),
            "outcome": "CHARACTERIZATION_CANDIDATE_FOUND" if candidate_found else "NO_IMPROVEMENT",
            "research_recommendation": {"sma_window_days": winner} if candidate_found else None,
            "research_only": True, "live_adoption_authorized": False, "size_zero_required": True,
            "plugin_control": dict(PLUGIN_CONTROL), "input_digest": source.input_digest,
            "candidates": list(CANDIDATE_WINDOWS), "baseline_window_days": BASELINE_WINDOW_DAYS,
            "validation_candidate_records": validation_records, "selected_window_days": winner,
            "post_lock": {
                "selected_wfa_c2_5_returns": selected_tests,
                "selected_final_c2_5": final_winner_c2_5,
                "baseline_final_c2_5": final_baseline_c2_5,
                "selected_final_c5_10_stress": final_winner_stress,
                "mc_terminal_loss_probability_c2_5": loss_probability,
            },
        }
    except OptimizationError as exc:
        return _invalid(str(exc))


def _paths(root: str | Path) -> PersistedPaths:
    root = Path(root)
    return PersistedPaths(
        root / "soxl_core_optimization_v1.json",
        root / "soxl_core_optimization_v1.sha256",
        root / "soxl_core_optimization_v1.readback.json",
    )


def _write_set_once(contents: dict[Path, bytes]) -> None:
    for path, content in contents.items():
        if path.exists() and path.read_bytes() != content:
            _fail("EXISTING_DIFFERENT_BYTES")
    temporary_paths: list[tuple[Path, Path]] = []
    try:
        for path, content in contents.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_bytes(content)
            temporary_paths.append((temporary, path))
        for temporary, path in temporary_paths:
            temporary.replace(path)
    except OSError:
        for temporary, _ in temporary_paths:
            temporary.unlink(missing_ok=True)
        _fail("PERSIST_WRITE_FAILED")


def persist_result(result: dict[str, Any], output_root: str | Path, *, source_commit: str) -> PersistedPaths:
    if type(result) is not dict or len(source_commit) != 40 or any(char not in "0123456789abcdef" for char in source_commit):
        _fail("PERSIST_INPUT_INVALID")
    paths = _paths(output_root)
    bundle = _canonical_bytes(_wire(result))
    bundle_sha256 = hashlib.sha256(bundle).hexdigest()
    readback = {
        "schema": READBACK_SCHEMA, "bundle_sha256": bundle_sha256, "bundle_bytes": len(bundle),
        "source_commit": source_commit, "source_blobs": EXPECTED_SOURCE_BLOBS,
        "csv_sha256": EXPECTED_ARTIFACT_SHA256, "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "readback_sha256": EXPECTED_READBACK_SHA256, "typed_digest": EXPECTED_INPUT_DIGEST,
        "result_digest": _digest(_wire(result)),
    }
    _write_set_once({paths.bundle: bundle, paths.sidecar: (bundle_sha256 + "\n").encode(), paths.readback: _canonical_bytes(readback)})
    load_persisted_result(output_root)
    return paths


def load_persisted_result(output_root: str | Path) -> dict[str, Any]:
    paths = _paths(output_root)
    try:
        bundle = paths.bundle.read_bytes()
        sidecar = paths.sidecar.read_text(encoding="ascii")
        parsed = json.loads(bundle.decode("utf-8"))
        readback = json.loads(paths.readback.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail("PERSISTED_PARSE_INVALID")
    required = {
        "schema", "bundle_sha256", "bundle_bytes", "source_commit", "source_blobs", "csv_sha256",
        "manifest_sha256", "readback_sha256", "typed_digest", "result_digest",
    }
    if (
        sidecar != hashlib.sha256(bundle).hexdigest() + "\n"
        or set(readback) != required
        or readback["schema"] != READBACK_SCHEMA
        or readback["bundle_sha256"] != hashlib.sha256(bundle).hexdigest()
        or readback["bundle_bytes"] != len(bundle)
        or readback["source_blobs"] != EXPECTED_SOURCE_BLOBS
        or readback["csv_sha256"] != EXPECTED_ARTIFACT_SHA256
        or readback["manifest_sha256"] != EXPECTED_MANIFEST_SHA256
        or readback["readback_sha256"] != EXPECTED_READBACK_SHA256
        or readback["typed_digest"] != EXPECTED_INPUT_DIGEST
        or readback["result_digest"] != _digest(parsed)
        or _canonical_bytes(parsed) != bundle
    ):
        _fail("READBACK_MISMATCH")
    return parsed
