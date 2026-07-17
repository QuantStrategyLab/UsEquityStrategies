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
        module_digests=("e" * 64, "f" * 64),
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
