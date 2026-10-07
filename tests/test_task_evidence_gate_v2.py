"""Promotion keeps a three-role advisory quorum and blocks missing results."""
import json
from unittest.mock import patch
import pytest
from scripts.gate_evidence_package import _run_promotion_dual_review


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.delenv("DUAL_REVIEW_GATE_SKIP", raising=False)
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({"synthetic": True}))
    return path


@pytest.mark.parametrize("status", ["pending", "unavailable", "failed", "outcome_unknown"])
def test_noncompleted_quorum_blocks_promotion(evidence, status):
    with patch("quant_platform_kit.strategy_lifecycle.task_review.review_material", return_value={"status": status}):
        assert _run_promotion_dual_review([evidence]) != 0


@pytest.mark.parametrize("outcome", ["agree_reject", "requires_human", "unknown"])
def test_rejection_or_disagreement_blocks(evidence, outcome):
    with patch("quant_platform_kit.strategy_lifecycle.task_review.review_material", return_value={
        "status": "completed", "outcome": outcome, "advisory_only": True,
    }):
        assert _run_promotion_dual_review([evidence]) != 0


def test_three_roles_and_caller_bound_material(evidence):
    with patch("quant_platform_kit.strategy_lifecycle.task_review.review_material", return_value={
        "status": "completed", "outcome": "agree_approve", "advisory_only": True,
    }) as review:
        assert _run_promotion_dual_review([evidence]) == 0
    assert review.call_args.args[0] == {"synthetic": True}
    assert len(review.call_args.kwargs["required_roles"]) == 3
    assert "a" * 40 in review.call_args.kwargs["operation_id"]


def test_failed_file_cannot_be_overwritten_by_later_success(evidence):
    with patch("quant_platform_kit.strategy_lifecycle.task_review.review_material", side_effect=[
        {"status": "completed", "outcome": "agree_reject", "advisory_only": True},
        {"status": "completed", "outcome": "agree_approve", "advisory_only": True},
    ]) as review:
        assert _run_promotion_dual_review([evidence, evidence]) != 0
    assert review.call_count == 1


def test_explicit_existing_skip_makes_no_task(evidence, monkeypatch):
    monkeypatch.setenv("DUAL_REVIEW_GATE_SKIP", "true")
    with patch("quant_platform_kit.strategy_lifecycle.task_review.review_material") as review:
        assert _run_promotion_dual_review([evidence]) == 0
        review.assert_not_called()


def test_missing_evidence_and_private_error_fail_without_leaking(evidence, capsys):
    with patch("quant_platform_kit.strategy_lifecycle.task_review.review_material", side_effect=RuntimeError("private credential")):
        assert _run_promotion_dual_review([evidence]) != 0
    evidence.unlink()
    assert _run_promotion_dual_review([evidence]) != 0
    assert "private credential" not in capsys.readouterr().err


def test_toml_evidence_is_supported(evidence):
    path = evidence.with_suffix(".toml")
    path.write_text("synthetic = true\n")
    with patch("quant_platform_kit.strategy_lifecycle.task_review.review_material", return_value={
        "status": "completed", "outcome": "agree_approve", "advisory_only": True,
    }) as review:
        assert _run_promotion_dual_review([path]) == 0
    assert review.call_args.args[0] == {"synthetic": True}
