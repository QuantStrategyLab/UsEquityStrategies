"""Fixed, offline-only SOXL SMA sensitivity evidence."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from typing import Any, NoReturn, Sequence

from .soxl_soxx_offline_input_contract import InputRow, OfflineInput
from .soxl_soxx_typed_baseline_result import run_typed_baseline


SCHEMA = "qsl.research.soxl_core_optimization.v1"
READBACK_SCHEMA = "qsl.research.soxl_core_optimization_readback.v1"
CANDIDATE_WINDOWS = (140, 160, 180, 200)
BASELINE_WINDOW_DAYS = 200
INITIAL_EQUITY = 100_000.0
EXPECTED_INPUT_DIGEST = "78c056c9a4541b7612b4f077ca25df6093aa6eb2f17783097c5b5f83a31dd5c6"
EXPECTED_ARTIFACT_SHA256 = "6eb44951f7b16b7369df2d8d0fcce08b85d44ad3b758381139a027a53dd5c36c"
EXPECTED_MANIFEST_SHA256 = "8fe988353a6bc0f3642e69cc7f58c180df59ebb7ff62d6b986aba314fb9db81b"
EXPECTED_BLOBS = {
    "soxl_soxx_offline_input_contract.py": "b4a16842c33d39851724fa31993001cd27a4c986",
    "soxl_soxx_typed_baseline_result.py": "aa1b43a9e5ab59b34d41932b3b18653451ffe46b",
    "r3_joint_evidence.py": "118553cada8800dde80c30bbca5927da342b1e85",
}
REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_CONTROL = {"state": "ABSENT", "enabled": False, "optimization_eligible": False}
MC_SEED_HEX = "08a73485a70548df5262ad66ac86e02c0c5cc6255469156832aec2e86b501e2b"
MC_TRIALS = 10_000
MC_PATH_LENGTH = 126
MC_BLOCK_LENGTH = 12
WINDOW_SPECS = (
    ("F1_VALIDATION", 370, 411), ("F1_TEST", 413, 454),
    ("F2_VALIDATION", 456, 497), ("F2_TEST", 499, 540),
    ("F3_VALIDATION", 542, 583), ("F3_TEST", 585, 626),
    ("FINAL_HOLDOUT", 627, 752),
)
WFA_TEST_IDS = ("F1_TEST", "F2_TEST", "F3_TEST")


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
    if type(value) is dict and all(type(key) is str for key in value):
        return {key: _wire(item) for key, item in value.items()}
    _fail("WIRE_TYPE_INVALID")


def _typed_rows(source: object) -> tuple[tuple[InputRow, ...], tuple[InputRow, ...]]:
    if type(source) is not OfflineInput or source.input_digest != EXPECTED_INPUT_DIGEST or type(source.rows) is not tuple:
        _fail("INPUT_IDENTITY_INVALID")
    soxx = tuple(row for row in source.rows if type(row) is InputRow and row.symbol == "SOXX")
    soxl = tuple(row for row in source.rows if type(row) is InputRow and row.symbol == "SOXL")
    if (
        len(soxx) != 753 or len(soxl) != 753 or len(source.rows) != 1506
        or tuple(row.as_of for row in soxx) != tuple(row.as_of for row in soxl)
        or tuple(row.as_of for row in soxx) != tuple(sorted(row.as_of for row in soxx))
    ):
        _fail("INPUT_SCHEMA_INVALID")
    for row in (*soxx, *soxl):
        if not all(math.isfinite(value) and value > 0.0 for value in (row.open, row.high, row.low, row.close)):
            _fail("INPUT_VALUES_INVALID")
    return soxx, soxl


def _canonical_input_bytes(rows: tuple[InputRow, ...]) -> bytes:
    lines = ["symbol,as_of,open,high,low,close,volume"]
    for row in rows:
        lines.append(
            ",".join((row.symbol, row.as_of, *(format(value, ".17g") for value in (row.open, row.high, row.low, row.close, row.volume))))
        )
    return ("\n".join(lines) + "\n").encode()


def _verify_immutable_input(source: OfflineInput) -> None:
    if type(source.canonical_bytes) is not bytes or source.canonical_bytes != _canonical_input_bytes(source.rows):
        _fail("INPUT_CANONICAL_BYTES_MISMATCH")
    if hashlib.sha256(source.canonical_bytes).hexdigest() != EXPECTED_ARTIFACT_SHA256:
        _fail("INPUT_ARTIFACT_SHA256_MISMATCH")


def simulate_candidate(source: OfflineInput, window_days: int, scenario: CostScenario) -> tuple[DailyPoint, ...]:
    """Simulate one fixed SOXX inclusive-close/SOXL-next-open candidate."""
    if window_days not in CANDIDATE_WINDOWS or type(scenario) is not CostScenario or scenario not in SCENARIOS:
        _fail("SIMULATION_CONTRACT_INVALID")
    soxx, soxl = _typed_rows(source)
    cash, quantity, previous_equity = INITIAL_EQUITY, 0.0, INITIAL_EQUITY
    commission_rate, slippage_rate = scenario.commission_bps / 10_000.0, scenario.slippage_bps / 10_000.0
    points: list[DailyPoint] = []
    for execution_index in range(BASELINE_WINDOW_DAYS, len(soxx)):
        signal_index = execution_index - 1
        signal_window = soxx[signal_index - window_days + 1: signal_index + 1]
        risk_on = soxx[signal_index].close >= math.fsum(row.close for row in signal_window) / window_days
        execution = soxl[execution_index]
        opening_equity = cash + quantity * execution.open
        if not math.isfinite(opening_equity) or opening_equity <= 0.0:
            _fail("SIMULATION_EQUITY_INVALID")
        transition = risk_on != (quantity > 0.0)
        commission = slippage = 0.0
        if transition and risk_on:
            fill = execution.open * (1.0 + slippage_rate)
            quantity = opening_equity / (fill * (1.0 + commission_rate))
            commission = quantity * fill * commission_rate
            slippage = quantity * (fill - execution.open)
            cash = 0.0
        elif transition:
            fill = execution.open * (1.0 - slippage_rate)
            proceeds = quantity * fill
            commission = proceeds * commission_rate
            slippage = quantity * (execution.open - fill)
            cash, quantity = proceeds - commission, 0.0
        end_equity = cash + quantity * execution.close
        if not math.isfinite(end_equity) or end_equity <= 0.0:
            _fail("SIMULATION_EQUITY_INVALID")
        points.append(DailyPoint(execution.as_of, previous_equity, end_equity, cash, quantity, end_equity / previous_equity - 1.0, transition, commission, slippage))
        previous_equity = end_equity
    return tuple(points)


def _window_metrics(points: Sequence[DailyPoint], raw_start: int, raw_end: int) -> dict[str, float | int]:
    selected = points[raw_start - BASELINE_WINDOW_DAYS:raw_end - BASELINE_WINDOW_DAYS + 1]
    if len(selected) != raw_end - raw_start + 1:
        _fail("WINDOW_BOUNDARY_INVALID")
    returns = tuple(point.daily_return for point in selected)
    mean = math.fsum(returns) / len(returns)
    variance = math.fsum((item - mean) ** 2 for item in returns) / (len(returns) - 1)
    peak, drawdowns = selected[0].start_equity, []
    for point in selected:
        peak = max(peak, point.end_equity)
        drawdowns.append(point.end_equity / peak - 1.0)
    tail_count = math.ceil(len(returns) * 0.05)
    return {
        "cumulative_return": selected[-1].end_equity / selected[0].start_equity - 1.0,
        "max_drawdown": min(drawdowns), "annualized_volatility": math.sqrt(variance) * math.sqrt(252.0),
        "expected_shortfall_95": math.fsum(sorted(returns)[:tail_count]) / tail_count,
        "sharpe": mean / math.sqrt(variance) * math.sqrt(252.0) if variance else 0.0,
        "trade_count": sum(point.transition for point in selected),
        "total_cost": math.fsum(point.commission_paid + point.slippage_impact_vs_open for point in selected),
    }


def _choose_fold_winner(metrics_by_window: dict[int, dict[str, float]]) -> int:
    if set(metrics_by_window) != set(CANDIDATE_WINDOWS) or any(
        not all(math.isfinite(metrics[key]) for key in ("sharpe", "cumulative_return", "max_drawdown"))
        for metrics in metrics_by_window.values()
    ):
        _fail("VALIDATION_SELECTION_INVALID")
    return min(
        CANDIDATE_WINDOWS,
        key=lambda window: (-metrics_by_window[window]["sharpe"], -metrics_by_window[window]["cumulative_return"], -metrics_by_window[window]["max_drawdown"], abs(window - BASELINE_WINDOW_DAYS), window),
    )


def _sample_index(context: str, trial: int, block: int, population: int) -> int:
    seed, limit, counter = bytes.fromhex(MC_SEED_HEX), 2**64 - (2**64 % population), 0
    while True:
        value = int.from_bytes(hmac.new(seed, f"R3-MBB-V1\0{context}\0{trial}\0{block}\0{counter}".encode(), hashlib.sha256).digest()[:8], "big")
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
    return {"schema": SCHEMA, "evidence_valid": False, "failure_codes": [code], "outcome": "NO_IMPROVEMENT", "research_recommendation": None, "research_only": True, "live_adoption_authorized": False, "size_zero_required": True, "plugin_control": dict(PLUGIN_CONTROL)}


def run_soxl_core_optimization(source: object, *, plugin_control: object = PLUGIN_CONTROL) -> dict[str, Any]:
    """Evaluate frozen SOXL candidates without provider, adoption, or live authority."""
    if plugin_control != PLUGIN_CONTROL:
        return _invalid("PLUGIN_CONTROL_NOT_ABSENT")
    try:
        soxx, _ = _typed_rows(source)
        assert type(source) is OfflineInput
        _verify_immutable_input(source)
        simulations = {window: {scenario.scenario_id: simulate_candidate(source, window, scenario) for scenario in SCENARIOS} for window in CANDIDATE_WINDOWS}
        baseline = run_typed_baseline(source)
        zero = simulations[BASELINE_WINDOW_DAYS]["ZERO"]
        if len(zero) != len(baseline.equity_curve) or any(actual.end_equity.hex() != expected.equity.hex() for actual, expected in zip(zero, baseline.equity_curve, strict=True)):
            _fail("SMA200_ZERO_PARITY_FAILED")
        validations: dict[str, dict[int, dict[str, float | int]]] = {}
        locked: list[int] = []
        for fold in ("F1", "F2", "F3"):
            _, start, end = next(spec for spec in WINDOW_SPECS if spec[0] == f"{fold}_VALIDATION")
            entries = {window: _window_metrics(simulations[window]["C2_5"], start, end) for window in CANDIDATE_WINDOWS}
            validations[f"{fold}_VALIDATION"] = entries
            locked.append(_choose_fold_winner({window: {key: float(value) for key, value in values.items() if key in ("sharpe", "cumulative_return", "max_drawdown")} for window, values in entries.items()}))
        metrics: dict[str, dict[int, dict[str, float | int]]] = dict(validations)
        for name, start, end in WINDOW_SPECS:
            if name.endswith("VALIDATION"):
                continue
            metrics[name] = {window: _window_metrics(simulations[window]["C2_5"], start, end) for window in CANDIDATE_WINDOWS}
        selected_tests = tuple(metrics[name][window]["cumulative_return"] for name, window in zip(WFA_TEST_IDS, locked, strict=True))
        final_candidate = locked[-1]
        final = metrics["FINAL_HOLDOUT"][final_candidate]
        final_stress = _window_metrics(simulations[final_candidate]["C5_10_STRESS"], 627, 752)["cumulative_return"]
        returns = tuple(point.daily_return for point in simulations[final_candidate]["C2_5"][627 - BASELINE_WINDOW_DAYS:])
        eligibility, failures = _eligibility(selected_tests, float(final["cumulative_return"]), float(final_stress), _terminal_loss_probability(returns))
        baseline_final = metrics["FINAL_HOLDOUT"][BASELINE_WINDOW_DAYS]
        candidate_found = final_candidate != BASELINE_WINDOW_DAYS and eligibility == "PASS" and final["cumulative_return"] > baseline_final["cumulative_return"] and abs(float(final["max_drawdown"])) <= abs(float(baseline_final["max_drawdown"]))
        return {"schema": SCHEMA, "evidence_valid": True, "failure_codes": list(failures), "outcome": "CHARACTERIZATION_CANDIDATE_FOUND" if candidate_found else "NO_IMPROVEMENT", "research_recommendation": {"sma_window_days": final_candidate} if candidate_found else None, "research_only": True, "live_adoption_authorized": False, "size_zero_required": True, "plugin_control": dict(PLUGIN_CONTROL), "input_digest": source.input_digest, "candidates": list(CANDIDATE_WINDOWS), "baseline_window_days": BASELINE_WINDOW_DAYS, "locked_fold_candidates": locked, "final_candidate": final_candidate, "r3_eligibility_status": eligibility, "metrics": {name: {str(window): values for window, values in entries.items()} for name, entries in metrics.items()}}
    except OptimizationError as exc:
        return _invalid(str(exc))


def _paths(root: Path) -> PersistedPaths:
    return PersistedPaths(root / "soxl_core_optimization_v1.json", root / "soxl_core_optimization_v1.sha256", root / "soxl_core_optimization_v1.readback.json")


def _trusted_anchor_digest(anchors: object, source_commit: str | None = None) -> str:
    if type(anchors) is not dict or set(anchors) != {"caller", "input", "result", "source_commit", "source_blobs"}:
        _fail("TRUSTED_ANCHORS_INVALID")
    if not all(type(anchors[key]) is str and anchors[key] for key in ("caller", "input", "result", "source_commit")) or anchors["source_blobs"] != EXPECTED_BLOBS:
        _fail("TRUSTED_ANCHORS_INVALID")
    if source_commit is not None and anchors["source_commit"] != source_commit:
        _fail("SOURCE_COMMIT_ANCHOR_MISMATCH")
    return _digest(anchors)


def _verify_source_identity(source_commit: str) -> None:
    try:
        if subprocess.run(
            ("git", "-C", str(REPO_ROOT), "cat-file", "-e", f"{source_commit}^{{commit}}"),
            check=False,
            capture_output=True,
            timeout=10,
        ).returncode:
            _fail("SOURCE_COMMIT_UNVERIFIABLE")
        for name, blob in EXPECTED_BLOBS.items():
            actual = subprocess.run(
                ("git", "-C", str(REPO_ROOT), "rev-parse", f"{source_commit}:src/us_equity_strategies/research/{name}"),
                check=False,
                capture_output=True,
                timeout=10,
                text=True,
            )
            if actual.returncode or actual.stdout.strip() != blob:
                _fail("SOURCE_BLOB_MISMATCH")
    except (OSError, subprocess.SubprocessError):
        _fail("SOURCE_COMMIT_UNVERIFIABLE")


def _output_root(value: str | Path) -> Path:
    root = Path(value)
    try:
        root.mkdir(parents=True, exist_ok=True)
        mode = os.lstat(root).st_mode
    except OSError:
        _fail("PERSIST_ROOT_INVALID")
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        _fail("PERSIST_ROOT_UNTRUSTED")
    return root


def _read_regular(path: Path) -> bytes | None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        _fail("PERSIST_PATH_UNTRUSTED")
    if not stat.S_ISREG(info.st_mode):
        _fail("PERSIST_PATH_UNTRUSTED")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            return handle.read()
    except OSError:
        _fail("PERSIST_PATH_UNTRUSTED")


def _write_set_once(contents: dict[Path, bytes]) -> None:
    existing = {path: _read_regular(path) for path in contents}
    if any(value is not None for value in existing.values()):
        if all(existing[path] == content for path, content in contents.items()):
            return
        _fail("EXISTING_DIFFERENT_BYTES")
    temporary_paths: list[tuple[str, Path]] = []
    try:
        for path, content in contents.items():
            descriptor, temporary = tempfile.mkstemp(prefix=".soxl-core-", suffix=".tmp", dir=path.parent)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
                raise
            temporary_paths.append((temporary, path))
        for temporary, path in temporary_paths:
            os.replace(temporary, path)
    except OSError:
        for temporary, _ in temporary_paths:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            except OSError:
                pass
        _fail("PERSIST_WRITE_FAILED")


def persist_result(result: dict[str, Any], output_root: str | Path, *, source_commit: str, trusted_anchors: object) -> PersistedPaths:
    if type(result) is not dict or type(source_commit) is not str or len(source_commit) != 40 or any(char not in "0123456789abcdef" for char in source_commit):
        _fail("PERSIST_INPUT_INVALID")
    anchor_digest = _trusted_anchor_digest(trusted_anchors, source_commit)
    _verify_source_identity(source_commit)
    paths = _paths(_output_root(output_root))
    bundle = _canonical_bytes(_wire(result))
    bundle_sha256 = hashlib.sha256(bundle).hexdigest()
    readback = {"schema": READBACK_SCHEMA, "bundle_sha256": bundle_sha256, "bundle_bytes": len(bundle), "source_commit": source_commit, "source_blobs": EXPECTED_BLOBS, "csv_sha256": EXPECTED_ARTIFACT_SHA256, "manifest_sha256": EXPECTED_MANIFEST_SHA256, "typed_digest": EXPECTED_INPUT_DIGEST, "result_digest": _digest(_wire(result)), "trusted_anchor_digest": anchor_digest}
    _write_set_once({paths.bundle: bundle, paths.sidecar: (bundle_sha256 + "\n").encode(), paths.readback: _canonical_bytes(readback)})
    load_persisted_result(output_root, trusted_anchors=trusted_anchors)
    return paths


def load_persisted_result(output_root: str | Path, *, trusted_anchors: object) -> dict[str, Any]:
    paths = _paths(_output_root(output_root))
    bundle, sidecar, readback_raw = (_read_regular(path) for path in (paths.bundle, paths.sidecar, paths.readback))
    if bundle is None or sidecar is None or readback_raw is None:
        _fail("PERSISTED_FILE_MISSING")
    if sidecar != hashlib.sha256(bundle).hexdigest().encode() + b"\n":
        _fail("SIDECAR_MISMATCH")
    try:
        parsed, readback = json.loads(bundle.decode("utf-8")), json.loads(readback_raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        _fail("PERSISTED_PARSE_INVALID")
    required = {"schema", "bundle_sha256", "bundle_bytes", "source_commit", "source_blobs", "csv_sha256", "manifest_sha256", "typed_digest", "result_digest", "trusted_anchor_digest"}
    if set(readback) != required or readback["schema"] != READBACK_SCHEMA or readback["bundle_sha256"] != hashlib.sha256(bundle).hexdigest() or readback["bundle_bytes"] != len(bundle) or readback["source_blobs"] != EXPECTED_BLOBS or readback["csv_sha256"] != EXPECTED_ARTIFACT_SHA256 or readback["manifest_sha256"] != EXPECTED_MANIFEST_SHA256 or readback["typed_digest"] != EXPECTED_INPUT_DIGEST or readback["result_digest"] != _digest(parsed) or readback["trusted_anchor_digest"] != _trusted_anchor_digest(trusted_anchors, readback["source_commit"]):
        _fail("READBACK_MISMATCH")
    if _canonical_bytes(parsed) != bundle:
        _fail("BUNDLE_NOT_CANONICAL")
    return parsed
