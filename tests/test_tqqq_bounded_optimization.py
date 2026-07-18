from copy import deepcopy
import hashlib
import json

import pytest

from us_equity_strategies.research.tqqq_bounded_optimization import (
    CONTRACT_SHA256,
    METHOD_SHA256,
    PROFILE_SHA256,
    WORKER_PROMPT_SHA256,
    CandidateSpaceError,
    OptimizationIdentity,
    TrialLedgerError,
    apply_r3_gates,
    canonicalize_candidate_space,
    deterministic_trial_order,
    read_evidence,
    run_noop_characterization,
    write_evidence,
)


def test_candidate_space_rejects_unknown_nonfinite_and_duplicates() -> None:
    with pytest.raises(CandidateSpaceError):
        canonicalize_candidate_space(({"params": {"unknown": 1.0}},))
    with pytest.raises(CandidateSpaceError):
        canonicalize_candidate_space(({"params": {"sma_window": float("nan")}},))
    with pytest.raises(CandidateSpaceError):
        canonicalize_candidate_space(
            ({"params": {}}, {"params": {}}, {"params": {"sma_window": 210}})
        )
    with pytest.raises(CandidateSpaceError):
        canonicalize_candidate_space(
            ({"params": {}}, {"params": {"sma_window": 210}})
        )


def test_baseline_first_and_at_most_five_trials() -> None:
    space = canonicalize_candidate_space(({"params": {}},))
    ordered = deterministic_trial_order(space)
    assert ordered[0].params == {}
    assert len(ordered) <= 5
    assert [candidate.candidate_id for candidate in ordered] == [
        candidate.candidate_id for candidate in deterministic_trial_order(space)
    ]


def _identity() -> OptimizationIdentity:
    return OptimizationIdentity(
        source_commit="a" * 40,
        input_digest="b" * 64,
        input_artifact_sha256="c" * 64,
        input_manifest_sha256="d" * 64,
        module_digests=(
            "95b7846be52a706cf55bdcf318bd22e47fcbd8bcbde481b1607bfe431db2efbb",
            "b03768587adc8810faa399e78f21a276f443fd673a120b3f3a6829b0ad6fe2bf",
        ),
    )


def test_exact_r3_gates_and_sealed_unseen_limitation_fail_closed() -> None:
    decision = apply_r3_gates((True, True, True, True, True), unseen_confirmation_available=False)
    assert decision.eligible is False
    assert decision.size_zero_required is True
    assert decision.research_limitation == "SEALED_UNSEEN_DATA_UNAVAILABLE"
    with pytest.raises(TrialLedgerError):
        apply_r3_gates((True,) * 4, unseen_confirmation_available=True)


def test_noop_batch_has_immutable_identity_full_ledger_and_baseline_rollback() -> None:
    baseline = b"immutable-baseline-evidence\n"
    result = run_noop_characterization(_identity(), baseline)

    assert result.payload["contract_sha256"] == CONTRACT_SHA256
    assert result.payload["worker_prompt_sha256"] == WORKER_PROMPT_SHA256
    assert result.payload["method_sha256"] == METHOD_SHA256
    assert result.payload["profile_sha256"] == PROFILE_SHA256
    assert result.payload["trial_count"] == 1
    assert result.payload["reject_all"] is True
    assert result.payload["selected_candidate_id"] == "baseline_v1"
    assert result.payload["baseline_preserved"] is True
    assert result.payload["rollback_to_baseline"] is True
    assert result.payload["unseen_confirmation_available"] is False
    assert result.payload["eligible"] is False
    assert result.payload["size_zero_required"] is True
    assert baseline == b"immutable-baseline-evidence\n"
    assert len(result.ledger) == 1
    assert '"start_status":"STARTED"' in result.ledger[0]
    assert '"end_status":"COMPLETED"' in result.ledger[0]


def test_evidence_readback_rejects_rewritten_sidecar(tmp_path) -> None:
    result = run_noop_characterization(_identity(), b"immutable-baseline-evidence\n")
    paths = write_evidence(tmp_path, result.payload, result.ledger)
    assert read_evidence(paths).payload == result.payload
    paths.sidecar.write_text("0" * 64 + "\n")
    with pytest.raises(TrialLedgerError):
        read_evidence(paths)


def test_evidence_readback_rejects_partial_or_rewritten_ledger(tmp_path) -> None:
    result = run_noop_characterization(_identity(), b"immutable-baseline-evidence\n")
    paths = write_evidence(tmp_path, result.payload, result.ledger)
    paths.ledger.write_text("")
    with pytest.raises(TrialLedgerError):
        read_evidence(paths)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode() + b"\n"


def _rewrite_package(paths, payload: dict[str, object], ledger: tuple[str, ...]) -> None:
    ledger_raw = ("\n".join(ledger) + "\n").encode()
    payload["trial_ledger_sha256"] = hashlib.sha256(ledger_raw).hexdigest()
    evidence_raw = _canonical_bytes(payload)
    paths.ledger.write_bytes(ledger_raw)
    paths.evidence.write_bytes(evidence_raw)
    paths.sidecar.write_text(hashlib.sha256(evidence_raw).hexdigest() + "\n")


def test_evidence_rejects_ledger_length_mismatched_with_trial_count(tmp_path) -> None:
    result = run_noop_characterization(_identity(), b"immutable-baseline-evidence\n")
    payload = deepcopy(result.payload)
    extra = json.loads(result.ledger[0])
    extra.update(
        candidate_id="extra",
        candidate_sha256="0" * 64,
        deterministic_order=1,
    )
    ledger = result.ledger + (
        json.dumps(extra, sort_keys=True, separators=(",", ":")),
    )
    payload["trial_ledger_sha256"] = hashlib.sha256(
        ("\n".join(ledger) + "\n").encode()
    ).hexdigest()

    with pytest.raises(TrialLedgerError):
        write_evidence(tmp_path, payload, ledger)


def test_readback_rejects_rewritten_module_digests(tmp_path) -> None:
    result = run_noop_characterization(_identity(), b"immutable-baseline-evidence\n")
    paths = write_evidence(tmp_path, result.payload, result.ledger)
    payload = deepcopy(result.payload)
    payload["module_digests"] = ["0" * 64, "1" * 64]
    _rewrite_package(paths, payload, result.ledger)

    with pytest.raises(TrialLedgerError):
        read_evidence(paths)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("metrics_digest", "0" * 64),
        ("failure_code", "REWRITTEN"),
        ("candidate_id", "rewritten"),
        ("candidate_sha256", "1" * 64),
    ),
)
def test_readback_binds_ledger_row_to_payload(tmp_path, field, value) -> None:
    result = run_noop_characterization(_identity(), b"immutable-baseline-evidence\n")
    paths = write_evidence(tmp_path, result.payload, result.ledger)
    row = json.loads(result.ledger[0])
    row[field] = value
    ledger = (json.dumps(row, sort_keys=True, separators=(",", ":")),)
    payload = deepcopy(result.payload)
    _rewrite_package(paths, payload, ledger)

    with pytest.raises(TrialLedgerError):
        read_evidence(paths)


def test_readback_enforces_locked_search_space(tmp_path) -> None:
    result = run_noop_characterization(_identity(), b"immutable-baseline-evidence\n")
    paths = write_evidence(tmp_path, result.payload, result.ledger)
    payload = deepcopy(result.payload)
    payload["search_space"]["max_trials"] = 6
    payload["search_space_id"] = hashlib.sha256(
        _canonical_bytes(payload["search_space"])[:-1]
    ).hexdigest()
    _rewrite_package(paths, payload, result.ledger)

    with pytest.raises(TrialLedgerError):
        read_evidence(paths)


def test_readback_enforces_validation_sequence(tmp_path) -> None:
    result = run_noop_characterization(_identity(), b"immutable-baseline-evidence\n")
    paths = write_evidence(tmp_path, result.payload, result.ledger)
    payload = deepcopy(result.payload)
    payload["validation_sequence"] = ["SEALED_UNSEEN_HOLDOUT"]
    _rewrite_package(paths, payload, result.ledger)

    with pytest.raises(TrialLedgerError):
        read_evidence(paths)


def test_package_conflict_fails_before_writing_any_other_artifact(tmp_path) -> None:
    result = run_noop_characterization(_identity(), b"immutable-baseline-evidence\n")
    stale_sidecar = tmp_path / "evidence.sha256"
    stale_sidecar.write_text("0" * 64 + "\n")

    with pytest.raises(TrialLedgerError):
        write_evidence(tmp_path, result.payload, result.ledger)

    assert stale_sidecar.read_text() == "0" * 64 + "\n"
    assert not (tmp_path / "evidence.json").exists()
    assert not (tmp_path / "trial_ledger.jsonl").exists()
