"""Fail-closed, offline TQQQ bounded-optimization characterization."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, NoReturn


SCHEMA = "qsl.tqqq.bounded_optimization.evidence.v1"
VERSION = "qsl.research.tqqq_bounded_optimization.v1"
CONTRACT_SHA256 = "5acf940775b0a69a78b7960b9dab78997d73873f9558c34c03199a495b8b70a4"
WORKER_PROMPT_SHA256 = "20547cb0514c412fb020f7e034fcbc935e1300a60c298565974d726fb603ee65"
PROFILE_SHA256 = "cfc7bcffc4853d1b79ae0575287e76a8e50b679792ccd003858a317b1f42e684"
MAX_TRIALS = 5
BASELINE_CANDIDATE_ID = "baseline_v1"
RESEARCH_LIMITATION = "SEALED_UNSEEN_DATA_UNAVAILABLE"
MODULE_DIGESTS = (
    "95b7846be52a706cf55bdcf318bd22e47fcbd8bcbde481b1607bfe431db2efbb",
    "b03768587adc8810faa399e78f21a276f443fd673a120b3f3a6829b0ad6fe2bf",
)
VALIDATION_SEQUENCE = (
    "R3_WFA_VALIDATION_TEST",
    "CANDIDATE_SELECTION",
    "SEALED_UNSEEN_HOLDOUT",
)


class CandidateSpaceError(ValueError):
    """Sanitized candidate-space contract error."""


class TrialLedgerError(ValueError):
    """Sanitized evidence/ledger contract error."""


def _fail_candidate(message: str) -> NoReturn:
    raise CandidateSpaceError(message) from None


def _fail_evidence(message: str) -> NoReturn:
    raise TrialLedgerError(message) from None


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        _fail_evidence("canonical JSON invalid")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _digest(value: object) -> str:
    return _sha(_canonical(value))


def _require_digest(value: object, length: int = 64) -> str:
    if (
        type(value) is not str
        or len(value) != length
        or value != value.lower()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        _fail_evidence("identity digest invalid")
    return value


_BASELINE_SPEC = {
    "profile": "tqqq_growth_income_research_baseline_v1",
    "version": "qsl.research.tqqq_typed_baseline_result.v1",
    "signal_timing": "SMA200_INCLUSIVE_CLOSE_V1",
    "sma_window": 200,
}
PARENT_BASELINE_SHA256 = _digest(_BASELINE_SPEC)
_METHOD_SPEC = {
    "schema": "qsl.tqqq.bounded_optimization.method.v1",
    "max_trials": MAX_TRIALS,
    "baseline_first": True,
    "candidate_order": "BASELINE_THEN_CANDIDATE_SHA256_ASCENDING",
    "selection": "ELIGIBILITY_PRIMARY_SCORE_CANDIDATE_SHA256",
    "adaptive_expansion": False,
    "early_stopping": False,
    "retry": False,
    "parallel": False,
    "r3_profile_sha256": PROFILE_SHA256,
    "sealed_holdout_order": "AFTER_VALIDATION_SELECTION_AND_IDENTITY_FREEZE",
    "missing_approved_alternatives": "SINGLETON_BASELINE_CHARACTERIZATION",
}
METHOD_SHA256 = _digest(_METHOD_SPEC)


@dataclass(frozen=True, slots=True)
class EligibilityDecision:
    eligible: bool
    size_zero_required: bool
    unseen_confirmation_available: bool
    research_limitation: str | None


def apply_r3_gates(
    gates: tuple[bool, ...], *, unseen_confirmation_available: bool
) -> EligibilityDecision:
    """Map the frozen five-field R3 gate result without changing thresholds."""
    if type(gates) is not tuple or len(gates) != 5 or any(type(value) is not bool for value in gates):
        _fail_evidence("invalid R3 gate evidence")
    if type(unseen_confirmation_available) is not bool:
        _fail_evidence("invalid unseen confirmation flag")
    passed = all(gates) and unseen_confirmation_available
    return EligibilityDecision(
        eligible=passed,
        size_zero_required=not passed,
        unseen_confirmation_available=unseen_confirmation_available,
        research_limitation=None if unseen_confirmation_available else RESEARCH_LIMITATION,
    )


@dataclass(frozen=True, slots=True)
class Candidate:
    candidate_id: str
    parameters: Mapping[str, int | float]
    canonical_parameter_bytes: bytes
    candidate_sha256: str
    parent_baseline_sha256: str

    @property
    def params(self) -> dict[str, int | float]:
        return dict(self.parameters)


def canonicalize_candidate_space(raw_candidates: Any) -> tuple[Candidate, ...]:
    """Return the singleton baseline because no finite alternatives are approved."""
    if type(raw_candidates) not in (tuple, list) or not raw_candidates:
        _fail_candidate("candidate space must be non-empty")
    if len(raw_candidates) > MAX_TRIALS:
        _fail_candidate("trial budget exceeded")
    seen: set[bytes] = set()
    for raw in raw_candidates:
        if type(raw) is not dict or set(raw) != {"params"} or type(raw["params"]) is not dict:
            _fail_candidate("invalid candidate")
        params = raw["params"]
        for value in params.values():
            if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(float(value)):
                _fail_candidate("non-finite parameter")
        try:
            canonical = _canonical({"params": params})
        except TrialLedgerError:
            _fail_candidate("non-canonical candidate")
        if canonical in seen:
            _fail_candidate("duplicate candidate")
        seen.add(canonical)
    if len(raw_candidates) != 1 or raw_candidates[0]["params"]:
        _fail_candidate("no preregistered finite alternatives")
    parameter_bytes = _canonical({"params": {}})
    identity = {
        "candidate_id": BASELINE_CANDIDATE_ID,
        "canonical_parameters_sha256": _sha(parameter_bytes),
        "parent_baseline_sha256": PARENT_BASELINE_SHA256,
        "version": VERSION,
    }
    return (
        Candidate(
            candidate_id=BASELINE_CANDIDATE_ID,
            parameters=MappingProxyType({}),
            canonical_parameter_bytes=parameter_bytes,
            candidate_sha256=_digest(identity),
            parent_baseline_sha256=PARENT_BASELINE_SHA256,
        ),
    )


def deterministic_trial_order(space: tuple[Candidate, ...]) -> tuple[Candidate, ...]:
    if (
        type(space) is not tuple
        or not space
        or len(space) > MAX_TRIALS
        or sum(candidate.candidate_id == BASELINE_CANDIDATE_ID for candidate in space) != 1
    ):
        _fail_candidate("invalid candidate space")
    baseline = tuple(candidate for candidate in space if candidate.candidate_id == BASELINE_CANDIDATE_ID)
    candidates = tuple(
        sorted(
            (candidate for candidate in space if candidate.candidate_id != BASELINE_CANDIDATE_ID),
            key=lambda candidate: candidate.candidate_sha256,
        )
    )
    return baseline + candidates


@dataclass(frozen=True, slots=True)
class OptimizationIdentity:
    source_commit: str
    input_digest: str
    input_artifact_sha256: str
    input_manifest_sha256: str
    module_digests: tuple[str, str]

    def __post_init__(self) -> None:
        _require_digest(self.source_commit, 40)
        _require_digest(self.input_digest)
        _require_digest(self.input_artifact_sha256)
        _require_digest(self.input_manifest_sha256)
        if type(self.module_digests) is not tuple or self.module_digests != MODULE_DIGESTS:
            _fail_evidence("module identity invalid")
        for digest in self.module_digests:
            _require_digest(digest)


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    payload: dict[str, Any]
    ledger: tuple[str, ...]


def _locked_search_space(candidate: Candidate) -> dict[str, Any]:
    return {
        "candidate_order": [candidate.candidate_id],
        "candidates": [
            {
                "candidate_id": candidate.candidate_id,
                "candidate_sha256": candidate.candidate_sha256,
                "canonical_parameters_sha256": _sha(candidate.canonical_parameter_bytes),
                "parent_baseline_sha256": candidate.parent_baseline_sha256,
            }
        ],
        "max_trials": MAX_TRIALS,
        "schema": "qsl.tqqq.bounded_optimization.search_space.v1",
    }


def run_noop_characterization(
    identity: OptimizationIdentity, baseline_evidence_bytes: bytes
) -> OptimizationResult:
    """Characterize the preserved baseline without reading performance or holdout."""
    if type(identity) is not OptimizationIdentity:
        _fail_evidence("optimization identity invalid")
    if type(baseline_evidence_bytes) is not bytes or not baseline_evidence_bytes:
        _fail_evidence("baseline evidence invalid")
    before = _sha(baseline_evidence_bytes)
    space = canonicalize_candidate_space(({"params": {}},))
    ordered = deterministic_trial_order(space)
    candidate = ordered[0]
    decision = apply_r3_gates((False,) * 5, unseen_confirmation_available=False)
    metrics_digest = _sha(b"NO_PERFORMANCE_READ_SINGLETON_BASELINE_V1")
    ledger_row = {
        "candidate_id": candidate.candidate_id,
        "candidate_sha256": candidate.candidate_sha256,
        "deterministic_order": 0,
        "end_status": "COMPLETED",
        "failure_code": RESEARCH_LIMITATION,
        "metrics_digest": metrics_digest,
        "selection_reason": "BASELINE_ROLLBACK_NO_APPROVED_ALTERNATIVES",
        "start_status": "STARTED",
    }
    ledger = (_canonical(ledger_row).decode("utf-8"),)
    ledger_raw = (ledger[0] + "\n").encode("utf-8")
    search_space = _locked_search_space(candidate)
    after = _sha(baseline_evidence_bytes)
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "version": VERSION,
        "contract_sha256": CONTRACT_SHA256,
        "worker_prompt_sha256": WORKER_PROMPT_SHA256,
        "source_commit": identity.source_commit,
        "input_digest": identity.input_digest,
        "input_artifact_sha256": identity.input_artifact_sha256,
        "input_manifest_sha256": identity.input_manifest_sha256,
        "module_digests": list(identity.module_digests),
        "method_sha256": METHOD_SHA256,
        "profile_sha256": PROFILE_SHA256,
        "search_space_id": _digest(search_space),
        "search_space": search_space,
        "trial_count": 1,
        "trial_ledger_sha256": _sha(ledger_raw),
        "metrics_digest": metrics_digest,
        "validation_sequence": list(VALIDATION_SEQUENCE),
        "sealed_holdout_opened": False,
        "unseen_confirmation_available": decision.unseen_confirmation_available,
        "research_limitation": decision.research_limitation,
        "eligible": decision.eligible,
        "size_zero_required": decision.size_zero_required,
        "reject_all": True,
        "selected_candidate_id": BASELINE_CANDIDATE_ID,
        "baseline_artifact_sha256_before": before,
        "baseline_artifact_sha256_after": after,
        "baseline_preserved": before == after,
        "rollback_to_baseline": True,
    }
    return OptimizationResult(payload=payload, ledger=ledger)


@dataclass(frozen=True, slots=True)
class EvidencePaths:
    evidence: Path
    sidecar: Path
    ledger: Path


@dataclass(frozen=True, slots=True)
class EvidenceReadback:
    payload: dict[str, Any]
    ledger: tuple[str, ...]


_REQUIRED_PAYLOAD_KEYS = {
    "schema",
    "version",
    "contract_sha256",
    "worker_prompt_sha256",
    "source_commit",
    "input_digest",
    "input_artifact_sha256",
    "input_manifest_sha256",
    "module_digests",
    "method_sha256",
    "profile_sha256",
    "search_space_id",
    "search_space",
    "trial_count",
    "trial_ledger_sha256",
    "metrics_digest",
    "validation_sequence",
    "sealed_holdout_opened",
    "unseen_confirmation_available",
    "research_limitation",
    "eligible",
    "size_zero_required",
    "reject_all",
    "selected_candidate_id",
    "baseline_artifact_sha256_before",
    "baseline_artifact_sha256_after",
    "baseline_preserved",
    "rollback_to_baseline",
}
_LEDGER_KEYS = {
    "candidate_id",
    "candidate_sha256",
    "deterministic_order",
    "end_status",
    "failure_code",
    "metrics_digest",
    "selection_reason",
    "start_status",
}


def _ledger_bytes(ledger: tuple[str, ...]) -> bytes:
    if type(ledger) is not tuple or not ledger:
        _fail_evidence("trial ledger invalid")
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(ledger):
        if type(line) is not str or not line:
            _fail_evidence("trial ledger invalid")
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            _fail_evidence("trial ledger invalid")
        if type(row) is not dict or set(row) != _LEDGER_KEYS or _canonical(row).decode("utf-8") != line:
            _fail_evidence("trial ledger non-canonical")
        if (
            row["deterministic_order"] != index
            or row["start_status"] != "STARTED"
            or row["end_status"] not in ("COMPLETED", "FAILED")
        ):
            _fail_evidence("trial ledger sequence invalid")
        rows.append(row)
    if len({row["candidate_id"] for row in rows}) != len(rows):
        _fail_evidence("trial ledger duplicate")
    return ("\n".join(ledger) + "\n").encode("utf-8")


def _validate_payload(payload: dict[str, Any], ledger_raw: bytes) -> None:
    if type(payload) is not dict or set(payload) != _REQUIRED_PAYLOAD_KEYS:
        _fail_evidence("evidence schema invalid")
    for key in (
        "contract_sha256",
        "worker_prompt_sha256",
        "source_commit",
        "input_digest",
        "input_artifact_sha256",
        "input_manifest_sha256",
        "method_sha256",
        "profile_sha256",
        "search_space_id",
        "trial_ledger_sha256",
        "metrics_digest",
        "baseline_artifact_sha256_before",
        "baseline_artifact_sha256_after",
    ):
        _require_digest(payload[key], 40 if key == "source_commit" else 64)
    try:
        ledger = tuple(ledger_raw.decode("utf-8").splitlines())
        rows = tuple(json.loads(line) for line in ledger)
    except (UnicodeError, json.JSONDecodeError):
        _fail_evidence("trial ledger invalid")
    if ledger_raw != _ledger_bytes(ledger):
        _fail_evidence("trial ledger non-canonical")
    candidate = canonicalize_candidate_space(({"params": {}},))[0]
    search_space = _locked_search_space(candidate)
    row = rows[0] if len(rows) == 1 else None
    if (
        payload["schema"] != SCHEMA
        or payload["version"] != VERSION
        or payload["contract_sha256"] != CONTRACT_SHA256
        or payload["worker_prompt_sha256"] != WORKER_PROMPT_SHA256
        or payload["method_sha256"] != METHOD_SHA256
        or payload["profile_sha256"] != PROFILE_SHA256
        or type(payload["module_digests"]) is not list
        or tuple(payload["module_digests"]) != MODULE_DIGESTS
        or payload["trial_count"] != len(rows)
        or payload["trial_count"] != 1
        or payload["trial_ledger_sha256"] != _sha(ledger_raw)
        or payload["selected_candidate_id"] != BASELINE_CANDIDATE_ID
        or payload["search_space"] != search_space
        or payload["sealed_holdout_opened"] is not False
        or payload["unseen_confirmation_available"] is not False
        or payload["research_limitation"] != RESEARCH_LIMITATION
        or payload["eligible"] is not False
        or payload["size_zero_required"] is not True
        or payload["reject_all"] is not True
        or payload["baseline_preserved"] is not True
        or payload["rollback_to_baseline"] is not True
        or payload["baseline_artifact_sha256_before"] != payload["baseline_artifact_sha256_after"]
        or payload["search_space_id"] != _digest(payload["search_space"])
        or payload["validation_sequence"] != list(VALIDATION_SEQUENCE)
        or row is None
        or row["candidate_id"] != payload["selected_candidate_id"]
        or row["candidate_sha256"] != search_space["candidates"][0]["candidate_sha256"]
        or row["metrics_digest"] != payload["metrics_digest"]
        or row["failure_code"] != payload["research_limitation"]
    ):
        _fail_evidence("evidence invariant invalid")


def _atomic_write(path: Path, data: bytes) -> None:
    if path.exists():
        try:
            if path.read_bytes() == data:
                return
        except OSError:
            _fail_evidence("existing artifact unreadable")
        _fail_evidence("existing artifact differs")
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        _fail_evidence("atomic evidence write failed")


def _preflight_package(files: tuple[tuple[Path, bytes], ...]) -> None:
    for path, expected in files:
        if not path.exists():
            continue
        try:
            actual = path.read_bytes()
        except OSError:
            _fail_evidence("existing artifact unreadable")
        if actual != expected:
            _fail_evidence("existing artifact differs")


def write_evidence(
    directory: Path, payload: dict[str, Any], ledger: tuple[str, ...]
) -> EvidencePaths:
    if not isinstance(directory, Path):
        _fail_evidence("evidence directory invalid")
    ledger_raw = _ledger_bytes(ledger)
    _validate_payload(payload, ledger_raw)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        _fail_evidence("evidence directory invalid")
    evidence = directory / "evidence.json"
    sidecar = directory / "evidence.sha256"
    ledger_path = directory / "trial_ledger.jsonl"
    evidence_raw = _canonical(payload) + b"\n"
    package = (
        (ledger_path, ledger_raw),
        (evidence, evidence_raw),
        (sidecar, (_sha(evidence_raw) + "\n").encode("ascii")),
    )
    _preflight_package(package)
    for path, data in package:
        _atomic_write(path, data)
    return EvidencePaths(evidence=evidence, sidecar=sidecar, ledger=ledger_path)


def read_evidence(paths: EvidencePaths) -> EvidenceReadback:
    if type(paths) is not EvidencePaths:
        _fail_evidence("evidence paths invalid")
    try:
        evidence_raw = paths.evidence.read_bytes()
        sidecar_raw = paths.sidecar.read_bytes()
        ledger_raw = paths.ledger.read_bytes()
    except OSError:
        _fail_evidence("evidence readback failed")
    if sidecar_raw != (_sha(evidence_raw) + "\n").encode("ascii"):
        _fail_evidence("evidence digest mismatch")
    try:
        payload = json.loads(evidence_raw)
        ledger = tuple(ledger_raw.decode("utf-8").splitlines())
    except (UnicodeError, json.JSONDecodeError):
        _fail_evidence("evidence readback failed")
    if evidence_raw != _canonical(payload) + b"\n" or ledger_raw != _ledger_bytes(ledger):
        _fail_evidence("evidence non-canonical")
    _validate_payload(payload, ledger_raw)
    return EvidenceReadback(payload=payload, ledger=ledger)
