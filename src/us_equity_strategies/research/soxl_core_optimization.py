"""Fixed, offline-only SOXL SMA sensitivity evidence with checkout-bound provenance."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import stat
from typing import Any, NoReturn, Sequence

from .soxl_soxx_offline_input_contract import InputRow, OfflineInput
from .soxl_soxx_typed_baseline_result import run_typed_baseline


SCHEMA = "qsl.research.soxl_core_optimization.v1"
READBACK_SCHEMA = "qsl.research.soxl_core_optimization_readback.v1"
CANDIDATE_WINDOWS = (140, 160, 180, 200)
BASELINE_WINDOW_DAYS = 200
INITIAL_EQUITY = 100_000.0
PLUGIN_CONTROL = {"state": "ABSENT", "enabled": False, "optimization_eligible": False}
MC_SEED_HEX = "08a73485a70548df5262ad66ac86e02c0c5cc6255469156832aec2e86b501e2b"
MC_TRIALS = 10_000
MC_PATH_LENGTH = 126
MC_BLOCK_LENGTH = 12
_REQUIRED_SOURCE_PATHS = (
    "src/us_equity_strategies/research/soxl_core_optimization.py",
    "src/us_equity_strategies/research/soxl_soxx_offline_input_contract.py",
    "src/us_equity_strategies/research/soxl_soxx_typed_baseline_result.py",
)
WINDOWS = {
    "F1_VALIDATION": (370, 411),
    "F1_TEST": (413, 454),
    "F2_VALIDATION": (456, 497),
    "F2_TEST": (499, 540),
    "F3_VALIDATION": (542, 583),
    "F3_TEST": (585, 626),
    "FINAL_HOLDOUT": (627, 752),
}


class OptimizationError(ValueError):
    """Sanitized SOXL optimization or persistence boundary error."""


def _fail(code: str) -> NoReturn:
    raise OptimizationError(code) from None


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


def _wire(value: Any) -> Any:
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            _fail("NONFINITE_VALUE")
        return value.hex()
    if type(value) in (list, tuple):
        return [_wire(item) for item in value]
    if type(value) is dict and all(type(key) is str for key in value):
        return {key: _wire(item) for key, item in value.items()}
    _fail("WIRE_TYPE_INVALID")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)[:-1]).hexdigest()


def _typed_rows(source: object) -> tuple[tuple[InputRow, ...], tuple[InputRow, ...]]:
    if type(source) is not OfflineInput or type(source.rows) is not tuple or len(source.rows) != 1506:
        _fail("INPUT_IDENTITY_INVALID")
    if type(source.input_digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", source.input_digest):
        _fail("INPUT_IDENTITY_INVALID")
    if type(source.canonical_bytes) is not bytes or not source.canonical_bytes:
        _fail("INPUT_CANONICAL_BYTES_MISMATCH")
    if any(type(row) is not InputRow for row in source.rows):
        _fail("INPUT_SCHEMA_INVALID")
    soxx = tuple(row for row in source.rows if row.symbol == "SOXX")
    soxl = tuple(row for row in source.rows if row.symbol == "SOXL")
    if (
        len(soxx) != 753
        or len(soxl) != 753
        or tuple(row.as_of for row in soxx) != tuple(row.as_of for row in soxl)
        or tuple(row.as_of for row in soxx) != tuple(sorted(row.as_of for row in soxx))
    ):
        _fail("INPUT_SCHEMA_INVALID")
    for row in (*soxx, *soxl):
        if not all(math.isfinite(value) and value > 0.0 for value in (row.open, row.high, row.low, row.close)):
            _fail("INPUT_VALUES_INVALID")
    lines = ["symbol,as_of,open,high,low,close,volume"]
    for row in source.rows:
        lines.append(",".join((row.symbol, row.as_of, *(format(value, ".17g") for value in (row.open, row.high, row.low, row.close, row.volume)))))
    if source.canonical_bytes != ("\n".join(lines) + "\n").encode():
        _fail("INPUT_CANONICAL_BYTES_MISMATCH")
    return soxx, soxl


def simulate_candidate(source: OfflineInput, window_days: int, scenario: CostScenario) -> tuple[DailyPoint, ...]:
    """Simulate one preregistered SOXX-SMA/SOXL-next-open candidate."""
    if window_days not in CANDIDATE_WINDOWS or type(scenario) is not CostScenario or scenario not in SCENARIOS:
        _fail("SIMULATION_CONTRACT_INVALID")
    soxx, soxl = _typed_rows(source)
    cash = INITIAL_EQUITY
    quantity = 0.0
    previous_equity = INITIAL_EQUITY
    commission_rate = scenario.commission_bps / 10_000.0
    slippage_rate = scenario.slippage_bps / 10_000.0
    points: list[DailyPoint] = []
    for execution_index in range(BASELINE_WINDOW_DAYS, len(soxx)):
        signal_window = soxx[execution_index - window_days:execution_index]
        risk_on = soxx[execution_index - 1].close >= math.fsum(row.close for row in signal_window) / window_days
        execution = soxl[execution_index]
        opening_equity = cash + quantity * execution.open
        if not math.isfinite(opening_equity) or opening_equity <= 0.0:
            _fail("SIMULATION_EQUITY_INVALID")
        transition = risk_on != (quantity > 0.0)
        commission = slippage = gross_notional = 0.0
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


def _window_metrics(points: Sequence[DailyPoint], raw_start: int, raw_end: int) -> dict[str, float | int | None | str]:
    selected = points[raw_start - BASELINE_WINDOW_DAYS:raw_end - BASELINE_WINDOW_DAYS + 1]
    if len(selected) != raw_end - raw_start + 1:
        _fail("WINDOW_BOUNDARY_INVALID")
    returns = tuple(point.daily_return for point in selected)
    count = len(returns)
    start_equity, end_equity = selected[0].start_equity, selected[-1].end_equity
    mean = math.fsum(returns) / count
    variance = math.fsum((item - mean) ** 2 for item in returns) / (count - 1)
    daily_std = math.sqrt(variance)
    peak = start_equity
    drawdowns: list[float] = []
    for point in selected:
        peak = max(peak, point.end_equity)
        drawdowns.append(point.end_equity / peak - 1.0)
    tail_count = math.ceil(count * 0.05)
    gross_notional = math.fsum(point.gross_traded_notional_at_open for point in selected)
    mean_equity = math.fsum(point.end_equity for point in selected) / count
    return {
        "observation_count": count,
        "start_date": selected[0].date,
        "end_date": selected[-1].date,
        "cumulative_return": end_equity / start_equity - 1.0,
        "max_drawdown": min(drawdowns),
        "annualized_volatility": daily_std * math.sqrt(252.0),
        "sharpe": None if daily_std == 0.0 else mean / daily_std * math.sqrt(252.0),
        "expected_shortfall_95": math.fsum(sorted(returns)[:tail_count]) / tail_count,
        "turnover": gross_notional / mean_equity,
        "trade_count": sum(point.transition for point in selected),
        "total_cost": math.fsum(point.commission_paid + point.slippage_impact_vs_open for point in selected),
        "commission_paid": math.fsum(point.commission_paid for point in selected),
        "slippage_impact_vs_open": math.fsum(point.slippage_impact_vs_open for point in selected),
    }


def _median(values: Sequence[float]) -> float:
    if len(values) != 3 or any(type(value) is not float or not math.isfinite(value) for value in values):
        _fail("VALIDATION_METRICS_INVALID")
    return sorted(values)[1]


def _metric_values(metrics: Sequence[dict[str, float | int | None | str]], key: str) -> list[float]:
    values = [item.get(key) for item in metrics]
    if any(type(value) is not float or not math.isfinite(value) for value in values):
        _fail("VALIDATION_METRICS_INVALID")
    return values  # type: ignore[return-value]


def _select_winner(validation: dict[int, Sequence[dict[str, float | int | None | str]]]) -> int:
    """Select exactly once using the preregistered validation-only ordering."""
    if set(validation) != set(CANDIDATE_WINDOWS):
        _fail("VALIDATION_METRICS_INVALID")
    ranked: list[tuple[tuple[float, float, float, int, int], int]] = []
    for window in CANDIDATE_WINDOWS:
        metrics = validation[window]
        if len(metrics) != 3:
            _fail("VALIDATION_METRICS_INVALID")
        sharpe = _median(_metric_values(metrics, "sharpe"))
        cumulative_return = _median(_metric_values(metrics, "cumulative_return"))
        max_drawdown = _median([abs(value) for value in _metric_values(metrics, "max_drawdown")])
        ranked.append(((sharpe, cumulative_return, -max_drawdown, -abs(window - BASELINE_WINDOW_DAYS), int(window == BASELINE_WINDOW_DAYS)), window))
    return max(ranked)[1]


def _sample_index(context: str, trial: int, block: int, population: int) -> int:
    limit = 2**64 - (2**64 % population)
    counter = 0
    while True:
        message = f"R3-MBB-V1\0{context}\0{trial}\0{block}\0{counter}".encode()
        value = int.from_bytes(hmac.new(bytes.fromhex(MC_SEED_HEX), message, hashlib.sha256).digest()[:8], "big")
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
            start = _sample_index("INDEPENDENT:SOXL", trial, block, MC_PATH_LENGTH)
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


def run_soxl_core_optimization(source: object, *, plugin_control: object = PLUGIN_CONTROL) -> dict[str, Any]:
    """Evaluate four frozen candidates; no outcome authorizes live adoption."""
    if plugin_control != PLUGIN_CONTROL:
        return _invalid("PLUGIN_CONTROL_NOT_ABSENT")
    try:
        _typed_rows(source)
        simulations = {window: {scenario.scenario_id: simulate_candidate(source, window, scenario) for scenario in SCENARIOS} for window in CANDIDATE_WINDOWS}
        baseline = run_typed_baseline(source)
        zero = simulations[BASELINE_WINDOW_DAYS]["ZERO"]
        if [(item.date, item.end_equity.hex(), item.cash.hex(), item.quantity.hex()) for item in zero] != [(item.date, item.equity.hex(), item.cash.hex(), item.soxl_quantity.hex()) for item in baseline.equity_curve]:
            _fail("SMA200_ZERO_PARITY_FAILED")
        validation = {window: [_window_metrics(simulations[window]["C2_5"], *WINDOWS[name]) for name in ("F1_VALIDATION", "F2_VALIDATION", "F3_VALIDATION")] for window in CANDIDATE_WINDOWS}
        selected = _select_winner(validation)
        compared = tuple(dict.fromkeys((selected, BASELINE_WINDOW_DAYS)))
        post_lock = {
            str(window): {
                name: {scenario.scenario_id: _window_metrics(simulations[window][scenario.scenario_id], *WINDOWS[name]) for scenario in SCENARIOS}
                for name in ("F1_TEST", "F2_TEST", "F3_TEST", "FINAL_HOLDOUT")
            }
            for window in compared
        }
        selected_post = post_lock[str(selected)]
        baseline_post = post_lock[str(BASELINE_WINDOW_DAYS)]
        wfa_returns = tuple(float(selected_post[name]["C2_5"]["cumulative_return"]) for name in ("F1_TEST", "F2_TEST", "F3_TEST"))
        final_c2 = selected_post["FINAL_HOLDOUT"]["C2_5"]
        baseline_final_c2 = baseline_post["FINAL_HOLDOUT"]["C2_5"]
        final_stress = selected_post["FINAL_HOLDOUT"]["C5_10_STRESS"]
        final_returns = tuple(point.daily_return for point in simulations[selected]["C2_5"][WINDOWS["FINAL_HOLDOUT"][0] - BASELINE_WINDOW_DAYS:])
        loss_probability = _terminal_loss_probability(final_returns)
        eligibility, failures = _eligibility(wfa_returns, float(final_c2["cumulative_return"]), float(final_stress["cumulative_return"]), loss_probability)
        selected_validation = validation[selected]
        baseline_validation = validation[BASELINE_WINDOW_DAYS]
        better_validation = _median([item["sharpe"] for item in selected_validation]) > _median([item["sharpe"] for item in baseline_validation])
        found = (
            selected != BASELINE_WINDOW_DAYS and better_validation and eligibility == "PASS"
            and float(final_c2["cumulative_return"]) > float(baseline_final_c2["cumulative_return"])
            and abs(float(final_c2["max_drawdown"])) <= abs(float(baseline_final_c2["max_drawdown"]))
            and float(final_stress["cumulative_return"]) > 0.0
        )
        return {
            "schema": SCHEMA, "evidence_valid": True, "failure_codes": list(failures),
            "outcome": "CHARACTERIZATION_CANDIDATE_FOUND" if found else "NO_IMPROVEMENT",
            "research_recommendation": {"sma_window_days": selected} if found else None,
            "research_only": True, "live_adoption_authorized": False, "size_zero_required": True,
            "plugin_control": dict(PLUGIN_CONTROL), "input_digest": source.input_digest,
            "candidates": list(CANDIDATE_WINDOWS), "baseline_window_days": BASELINE_WINDOW_DAYS,
            "validation_metrics_c2_5": {str(window): metrics for window, metrics in validation.items()},
            "locked_winner": selected, "post_lock_metrics": post_lock,
            "r3_eligibility_status": eligibility, "mc_terminal_loss_probability_c2_5": loss_probability,
        }
    except OptimizationError as exc:
        return _invalid(str(exc))


def _paths(root: Path) -> PersistedPaths:
    return PersistedPaths(root / "soxl_core_optimization_v1.json", root / "soxl_core_optimization_v1.sha256", root / "soxl_core_optimization_v1.readback.json")


def _git(repo_root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(("git", "-C", str(repo_root), *arguments), check=False, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        _fail("SOURCE_CHECKOUT_UNVERIFIABLE")
    if completed.returncode != 0:
        _fail("SOURCE_CHECKOUT_UNVERIFIABLE")
    return completed.stdout.strip()


def _verify_provenance(repo_root: Path, source_commit: object, source_blobs: object) -> dict[str, str]:
    if type(source_commit) is not str or re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        _fail("PERSIST_INPUT_INVALID")
    if type(source_blobs) is not dict or set(source_blobs) != set(_REQUIRED_SOURCE_PATHS) or any(type(path) is not str or type(blob) is not str or re.fullmatch(r"[0-9a-f]{40}", blob) is None for path, blob in source_blobs.items()):
        _fail("SOURCE_BLOB_MAP_INVALID")
    if Path(_git(repo_root, "rev-parse", "--show-toplevel")).resolve() != repo_root.resolve():
        _fail("SOURCE_CHECKOUT_UNVERIFIABLE")
    if _git(repo_root, "rev-parse", "--verify", "HEAD^{commit}") != source_commit:
        _fail("SOURCE_COMMIT_MISMATCH")
    if _git(repo_root, "status", "--porcelain=v1", "--untracked-files=all", "--", *_REQUIRED_SOURCE_PATHS):
        _fail("SOURCE_CHECKOUT_DIRTY")
    for path in _REQUIRED_SOURCE_PATHS:
        blob = source_blobs[path]
        ls_tree = _git(repo_root, "ls-tree", "-r", "--full-tree", "HEAD", "--", path)
        if not ls_tree or _git(repo_root, "rev-parse", f"HEAD:{path}") != blob or f" {blob}\t{path}" not in ls_tree:
            _fail("SOURCE_BLOB_MISMATCH")
    return dict(source_blobs)


def _read_regular(path: Path) -> bytes | None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return None
    except OSError:
        _fail("OUTPUT_PATH_INVALID")
    if os.path.islink(path) or not stat.S_ISREG(mode):
        _fail("OUTPUT_PATH_INVALID")
    try:
        return path.read_bytes()
    except OSError:
        _fail("OUTPUT_PATH_INVALID")


def _ensure_output_root(root: Path) -> Path:
    if not root.is_absolute():
        root = Path.cwd() / root
    if ".." in root.parts:
        _fail("OUTPUT_ROOT_INVALID")
    current = Path(root.anchor)
    for component in root.parts[1:]:
        current /= component
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            try:
                current.mkdir()
                mode = current.lstat().st_mode
            except OSError:
                _fail("OUTPUT_ROOT_INVALID")
        except OSError:
            _fail("OUTPUT_ROOT_INVALID")
        if not stat.S_ISDIR(mode):
            _fail("OUTPUT_ROOT_INVALID")
    return current


def _write_set_once(root: Path, contents: dict[Path, bytes]) -> None:
    existing = {path: _read_regular(path) for path in contents}
    if any(value is not None and value != contents[path] for path, value in existing.items()):
        _fail("EXISTING_DIFFERENT_BYTES")
    if all(value is not None for value in existing.values()):
        return
    if any(value is not None for value in existing.values()):
        _fail("PERSIST_SET_INCOMPLETE")
    temporary: list[tuple[Path, Path, bytes]] = []
    published: list[tuple[Path, bytes]] = []
    try:
        for path, raw in contents.items():
            descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=root)
            temp_path = Path(name)
            temporary.append((temp_path, path, raw))
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
        for temp_path, path, raw in temporary:
            os.replace(temp_path, path)
            published.append((path, raw))
        directory = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        for temp_path, _, _ in temporary:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        for path, raw in published:
            try:
                if _read_regular(path) == raw:
                    path.unlink()
            except OptimizationError:
                pass
        _fail("PERSIST_WRITE_FAILED")


def persist_result(result: dict[str, Any], output_root: str | Path, *, source_commit: str, source_blobs: dict[str, str], repo_root: str | Path | None = None) -> PersistedPaths:
    """Persist one idempotent result only after current-checkout provenance verification."""
    if type(result) is not dict:
        _fail("PERSIST_INPUT_INVALID")
    root = Path(output_root)
    repo = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[3]
    blobs = _verify_provenance(repo, source_commit, source_blobs)
    root = _ensure_output_root(root)
    paths = _paths(root)
    bundle = _canonical_bytes(_wire(result))
    bundle_sha = hashlib.sha256(bundle).hexdigest()
    readback = {
        "schema": READBACK_SCHEMA, "bundle_sha256": bundle_sha, "bundle_bytes": len(bundle),
        "source_commit": source_commit, "source_blobs": blobs, "result_digest": _digest(_wire(result)),
    }
    _write_set_once(paths.bundle.parent, {paths.bundle: bundle, paths.sidecar: (bundle_sha + "\n").encode("ascii"), paths.readback: _canonical_bytes(readback)})
    if load_persisted_result(root) != _wire(result):
        _fail("READBACK_MISMATCH")
    return paths


def _strict_json(raw: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        if len({key for key, _ in items}) != len(items):
            _fail("PERSISTED_PARSE_INVALID")
        return dict(items)
    try:
        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("PERSISTED_PARSE_INVALID")
    if type(parsed) is not dict:
        _fail("PERSISTED_PARSE_INVALID")
    return parsed


def load_persisted_result(output_root: str | Path) -> dict[str, Any]:
    root = Path(output_root)
    paths = _paths(root)
    bundle = _read_regular(paths.bundle)
    sidecar = _read_regular(paths.sidecar)
    readback_raw = _read_regular(paths.readback)
    if bundle is None or sidecar is None or readback_raw is None:
        _fail("PERSISTED_FILE_MISSING")
    digest = hashlib.sha256(bundle).hexdigest()
    if sidecar != (digest + "\n").encode("ascii"):
        _fail("SIDECAR_MISMATCH")
    parsed = _strict_json(bundle)
    readback = _strict_json(readback_raw)
    required = {"schema", "bundle_sha256", "bundle_bytes", "source_commit", "source_blobs", "result_digest"}
    if set(readback) != required or readback["schema"] != READBACK_SCHEMA or readback["bundle_sha256"] != digest or readback["bundle_bytes"] != len(bundle) or readback["result_digest"] != _digest(parsed) or type(readback["source_commit"]) is not str or type(readback["source_blobs"]) is not dict or set(readback["source_blobs"]) != set(_REQUIRED_SOURCE_PATHS):
        _fail("READBACK_MISMATCH")
    if _canonical_bytes(parsed) != bundle:
        _fail("BUNDLE_NOT_CANONICAL")
    return parsed
