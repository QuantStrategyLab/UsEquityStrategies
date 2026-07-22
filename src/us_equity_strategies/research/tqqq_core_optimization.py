"""Fixed, offline-only TQQQ SMA sensitivity evidence."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import math
from pathlib import Path
from typing import Any, NoReturn, Sequence

from .tqqq_offline_input_contract import InputRow, OfflineInput
from .tqqq_typed_baseline_result import run_typed_baseline


SCHEMA = "qsl.research.tqqq_core_optimization.v1"
READBACK_SCHEMA = "qsl.research.tqqq_core_optimization_readback.v1"
CANDIDATE_WINDOWS = (150, 200, 250)
BASELINE_WINDOW_DAYS = 200
SMA_WINDOW_DAYS = BASELINE_WINDOW_DAYS
INITIAL_EQUITY = 100_000.0
INPUT_COLUMNS = ("symbol", "as_of", "open", "high", "low", "close", "volume")
EXPECTED_INPUT_DIGEST = "8cc682b2d1acc23a8dd93c3bfd67b445d7305844d2c4d254f4f52e0ac817c6cb"
EXPECTED_ARTIFACT_SHA256 = "a40254c7e31d6b49b4a2db5ec57b1b65215a3ab1ee33df879d9e5e2b4dae6551"
EXPECTED_MANIFEST_SHA256 = "8ecbc864f356af94464249ee3003d44fb00cf739c6810dc2de14165e5dc3500d"
EXPECTED_BLOBS = {
    "tqqq_offline_input_contract.py": "d80a3e6b023f26c7c6f4a63c3a46ca459fbac895",
    "tqqq_typed_baseline_result.py": "31fd4b436860c9c0e58803f020e316a8a33f3f2f",
    "r3_joint_evidence.py": "118553cada8800dde80c30bbca5927da342b1e85",
    "run_r3_joint_evidence.py": "12bbed1293878da629093297ffac563cc153033a",
}
PLUGIN_CONTROL = {"state": "ABSENT", "enabled": False, "optimization_eligible": False}
MC_SEED_HEX = "08a73485a70548df5262ad66ac86e02c0c5cc6255469156832aec2e86b501e2b"
MC_TRIALS = 10_000
MC_PATH_LENGTH = 126
MC_BLOCK_LENGTH = 12
WINDOW_SPECS = (
    ("F1_VALIDATION", 370, 411), ("F1_EMBARGO", 412, 412), ("F1_TEST", 413, 454),
    ("F2_VALIDATION", 456, 497), ("F2_EMBARGO", 498, 498), ("F2_TEST", 499, 540),
    ("F3_VALIDATION", 542, 583), ("F3_EMBARGO", 584, 584), ("F3_TEST", 585, 626),
    ("FINAL_HOLDOUT", 627, 752),
)


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
    if type(value) is tuple or type(value) is list:
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
    qqq = tuple(row for row in source.rows if row.symbol == "QQQ")
    tqqq = tuple(row for row in source.rows if row.symbol == "TQQQ")
    if (
        len(qqq) != 753 or len(tqqq) != 753
        or tuple(row.as_of for row in qqq) != tuple(row.as_of for row in tqqq)
        or tuple(row.as_of for row in qqq) != tuple(sorted(row.as_of for row in qqq))
    ):
        _fail("INPUT_SCHEMA_INVALID")
    for row in (*qqq, *tqqq):
        if not all(math.isfinite(value) and value > 0.0 for value in (row.open, row.high, row.low, row.close)):
            _fail("INPUT_VALUES_INVALID")
    return qqq, tqqq


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
    """Simulate one frozen SMA candidate with the R3 next-open adverse fills."""
    if window_days not in CANDIDATE_WINDOWS or type(scenario) is not CostScenario or scenario not in SCENARIOS:
        _fail("SIMULATION_CONTRACT_INVALID")
    qqq, tqqq = _typed_rows(source, EXPECTED_INPUT_DIGEST)
    cash = INITIAL_EQUITY
    quantity = 0.0
    previous_equity = INITIAL_EQUITY
    points: list[DailyPoint] = []
    commission_rate = scenario.commission_bps / 10_000.0
    slippage_rate = scenario.slippage_bps / 10_000.0
    for execution_index in range(window_days, len(qqq)):
        signal_index = execution_index - 1
        signal_window = qqq[execution_index - window_days:execution_index]
        risk_on = qqq[signal_index].close >= math.fsum(row.close for row in signal_window) / window_days
        execution = tqqq[execution_index]
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


def _window_metrics(points: Sequence[DailyPoint], window_days: int, raw_start: int, raw_end: int) -> dict[str, float | int]:
    selected = points[raw_start - window_days:raw_end - window_days + 1]
    if len(selected) != raw_end - raw_start + 1:
        _fail("WINDOW_BOUNDARY_INVALID")
    returns = tuple(point.daily_return for point in selected)
    start_equity, end_equity = selected[0].start_equity, selected[-1].end_equity
    mean = math.fsum(returns) / len(returns)
    variance = math.fsum((item - mean) ** 2 for item in returns) / (len(returns) - 1)
    peak = start_equity
    drawdowns: list[float] = []
    for point in selected:
        peak = max(peak, point.end_equity)
        drawdowns.append(point.end_equity / peak - 1.0)
    tail_count = math.ceil(len(returns) * 0.05)
    return {
        "observation_count": len(selected), "cumulative_return": end_equity / start_equity - 1.0,
        "max_drawdown": min(drawdowns), "annualized_volatility": math.sqrt(variance) * math.sqrt(252.0),
        "expected_shortfall_95": math.fsum(sorted(returns)[:tail_count]) / tail_count,
        "trade_count": sum(point.transition for point in selected),
        "total_cost": math.fsum(point.commission_paid + point.slippage_impact_vs_open for point in selected),
    }


def _pareto_winner(metrics_by_window: dict[int, dict[str, float]]) -> int:
    """Return a unique strict Pareto winner, otherwise retain the baseline."""
    keys = ("cumulative_return", "max_drawdown", "annualized_volatility", "expected_shortfall_95")
    if any(set(keys) - set(metrics) for metrics in metrics_by_window.values()):
        _fail("PARETO_METRICS_INVALID")
    winners: list[int] = []
    for candidate, metrics in metrics_by_window.items():
        dominates_all = True
        for other, other_metrics in metrics_by_window.items():
            if candidate == other:
                continue
            comparisons = [
                metrics["cumulative_return"] >= other_metrics["cumulative_return"],
                metrics["max_drawdown"] >= other_metrics["max_drawdown"],
                metrics["annualized_volatility"] <= other_metrics["annualized_volatility"],
                metrics["expected_shortfall_95"] >= other_metrics["expected_shortfall_95"],
            ]
            strict = [
                metrics["cumulative_return"] > other_metrics["cumulative_return"],
                metrics["max_drawdown"] > other_metrics["max_drawdown"],
                metrics["annualized_volatility"] < other_metrics["annualized_volatility"],
                metrics["expected_shortfall_95"] > other_metrics["expected_shortfall_95"],
            ]
            dominates_all = dominates_all and all(comparisons) and any(strict)
        if dominates_all:
            winners.append(candidate)
    return winners[0] if len(winners) == 1 else BASELINE_WINDOW_DAYS


def _five_metric_winner(metrics_by_window: dict[int, dict[str, float]]) -> int:
    adjusted = {}
    for candidate, metrics in metrics_by_window.items():
        adjusted[candidate] = dict(metrics)
        adjusted[candidate]["cumulative_return"] = metrics["c2_5_cumulative_return"]
    base = _pareto_winner(adjusted)
    if base == BASELINE_WINDOW_DAYS:
        return base
    candidate = metrics_by_window[base]
    for other_window, other in metrics_by_window.items():
        if other_window == base:
            continue
        if not (candidate["stress_cumulative_return"] >= other["stress_cumulative_return"]):
            return BASELINE_WINDOW_DAYS
        if candidate["stress_cumulative_return"] == other["stress_cumulative_return"]:
            continue
    return base


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


def _terminal_loss_probability(returns: Sequence[float]) -> float:
    if len(returns) != MC_PATH_LENGTH:
        _fail("MONTE_CARLO_INPUT_INVALID")
    losses = 0
    for trial in range(MC_TRIALS):
        equity = 1.0
        for block in range(math.ceil(MC_PATH_LENGTH / MC_BLOCK_LENGTH)):
            start = _sample_index("INDEPENDENT:TQQQ", trial, block, MC_PATH_LENGTH)
            for offset in range(MC_BLOCK_LENGTH):
                if block * MC_BLOCK_LENGTH + offset == MC_PATH_LENGTH:
                    break
                equity *= 1.0 + returns[(start + offset) % MC_PATH_LENGTH]
        losses += equity < 1.0
    return losses / MC_TRIALS


def _eligibility(wfa_returns: Sequence[float], final_c2_5: float, final_stress: float, loss_probability: float) -> tuple[str, tuple[str, ...]]:
    if len(wfa_returns) != 3:
        _fail("ELIGIBILITY_INPUT_INVALID")
    failures = []
    if sum(item > 0.0 for item in wfa_returns) < 2:
        failures.append("WFA_C2_5_PASSING_WINDOWS_BELOW_MINIMUM")
    if not final_c2_5 > 0.0:
        failures.append("FINAL_HOLDOUT_C2_5_RETURN_NOT_STRICTLY_POSITIVE")
    if not final_stress > 0.0:
        failures.append("FINAL_HOLDOUT_C5_10_STRESS_RETURN_NOT_STRICTLY_POSITIVE")
    if not loss_probability < 0.5:
        failures.append("MC_C2_5_TERMINAL_LOSS_PROBABILITY_NOT_STRICTLY_BELOW_HALF")
    return ("FAIL", tuple(failures)) if failures else ("PASS", ())


def _invalid(code: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA, "evidence_valid": False, "failure_codes": [code], "outcome": "NO_IMPROVEMENT",
        "research_recommendation": None, "research_only": True, "live_adoption_authorized": False,
        "size_zero_required": True, "plugin_control": dict(PLUGIN_CONTROL),
    }


def run_tqqq_core_optimization(source: object, *, plugin_control: object = PLUGIN_CONTROL, expected_input_digest: str = EXPECTED_INPUT_DIGEST) -> dict[str, Any]:
    """Evaluate the three frozen candidates; this result has no adoption authority."""
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
        if any(actual.end_equity.hex() != expected.equity.hex() for actual, expected in zip(zero, baseline.equity_curve, strict=True)):
            _fail("SMA200_ZERO_PARITY_FAILED")
        metrics: dict[str, dict[int, dict[str, Any]]] = {}
        locked: list[int] = []
        for name, start, end in WINDOW_SPECS:
            if "EMBARGO" in name:
                continue
            metrics[name] = {}
            for window in CANDIDATE_WINDOWS:
                c2 = _window_metrics(simulations[window]["C2_5"], window, start, end)
                stress = _window_metrics(simulations[window]["C5_10_STRESS"], window, start, end)
                metrics[name][window] = {**c2, "c2_5_cumulative_return": c2["cumulative_return"], "stress_cumulative_return": stress["cumulative_return"]}
            if name.endswith("VALIDATION"):
                locked.append(_five_metric_winner(metrics[name]))
        final_candidate = locked[-1]
        selected_tests = [metrics[name][candidate]["c2_5_cumulative_return"] for name, candidate in zip(("F1_TEST", "F2_TEST", "F3_TEST"), locked, strict=True)]
        final = metrics["FINAL_HOLDOUT"][final_candidate]
        final_returns = tuple(point.daily_return for point in simulations[final_candidate]["C2_5"][627 - final_candidate:])
        loss_probability = _terminal_loss_probability(final_returns)
        eligibility, failures = _eligibility(selected_tests, final["c2_5_cumulative_return"], final["stress_cumulative_return"], loss_probability)
        holdout_winner = _five_metric_winner(metrics["FINAL_HOLDOUT"])
        winner = final_candidate != BASELINE_WINDOW_DAYS and eligibility == "PASS" and holdout_winner == final_candidate
        return {
            "schema": SCHEMA, "evidence_valid": True, "failure_codes": list(failures),
            "outcome": "WINNER_RESEARCH_ONLY" if winner else "NO_IMPROVEMENT",
            "research_recommendation": {"sma_window_days": final_candidate} if winner else None,
            "research_only": True, "live_adoption_authorized": False, "size_zero_required": True,
            "plugin_control": dict(PLUGIN_CONTROL), "input_digest": source.input_digest,
            "candidates": list(CANDIDATE_WINDOWS), "baseline_window_days": BASELINE_WINDOW_DAYS,
            "locked_fold_candidates": locked, "final_candidate": final_candidate,
            "r3_eligibility_status": eligibility, "mc_terminal_loss_probability_c2_5": loss_probability,
            "metrics": {name: {str(window): values for window, values in entries.items()} for name, entries in metrics.items()},
        }
    except OptimizationError as exc:
        return _invalid(str(exc))


def _paths(root: str | Path) -> PersistedPaths:
    root = Path(root)
    return PersistedPaths(root / "tqqq_core_optimization_v1.json", root / "tqqq_core_optimization_v1.sha256", root / "tqqq_core_optimization_v1.readback.json")


def _write_set_once(contents: dict[Path, bytes]) -> None:
    """Refuse before writing when a prior persistence set has different bytes."""
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
    sidecar = (bundle_sha256 + "\n").encode()
    readback = {
        "schema": READBACK_SCHEMA, "bundle_sha256": bundle_sha256, "bundle_bytes": len(bundle),
        "source_commit": source_commit, "source_blobs": EXPECTED_BLOBS,
        "csv_sha256": EXPECTED_ARTIFACT_SHA256, "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "typed_digest": EXPECTED_INPUT_DIGEST, "result_digest": _digest(_wire(result)),
    }
    _write_set_once({paths.bundle: bundle, paths.sidecar: sidecar, paths.readback: _canonical_bytes(readback)})
    load_persisted_result(output_root)
    return paths


def load_persisted_result(output_root: str | Path) -> dict[str, Any]:
    paths = _paths(output_root)
    try:
        bundle = paths.bundle.read_bytes()
        sidecar = paths.sidecar.read_text(encoding="ascii")
    except OSError:
        _fail("PERSISTED_FILE_MISSING")
    if sidecar != hashlib.sha256(bundle).hexdigest() + "\n":
        _fail("SIDECAR_MISMATCH")
    try:
        parsed = json.loads(bundle.decode("utf-8"))
        readback = json.loads(paths.readback.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail("PERSISTED_PARSE_INVALID")
    required = {"schema", "bundle_sha256", "bundle_bytes", "source_commit", "source_blobs", "csv_sha256", "manifest_sha256", "typed_digest", "result_digest"}
    if set(readback) != required or readback["schema"] != READBACK_SCHEMA or readback["bundle_sha256"] != hashlib.sha256(bundle).hexdigest() or readback["bundle_bytes"] != len(bundle) or readback["source_blobs"] != EXPECTED_BLOBS or readback["csv_sha256"] != EXPECTED_ARTIFACT_SHA256 or readback["manifest_sha256"] != EXPECTED_MANIFEST_SHA256 or readback["typed_digest"] != EXPECTED_INPUT_DIGEST or readback["result_digest"] != _digest(parsed):
        _fail("READBACK_MISMATCH")
    if _canonical_bytes(parsed) != bundle:
        _fail("BUNDLE_NOT_CANONICAL")
    return parsed
