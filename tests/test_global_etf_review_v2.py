import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from us_equity_strategies.research import global_etf_review as review
from us_equity_strategies.research import soxl_new_research


def source():
    body = ('<h1>Alan Moreira</h1><h2>Volatility Managed Portfolios</h2>'
            '<a href="https://amoreira2.github.io/alan-moreira.github.io/VolPortfolios_published.pdf">paper</a>'
            '<p>Managed portfolios that take less risk when volatility is high. '
            'This synthetic abstract supplies enough text to test precise section boundaries only.</p>'
            '<h2>Should Long-Term Investors Time Volatility?</h2><p>Other paper.</p>')
    title, abstract = review._source_fields(body.encode())
    return {"url": review.GLOBAL_ETF_RESEARCH_SOURCE_URL, "retrieved_at": "2026-09-17T00:00:00+00:00",
            "title": title, "abstract": abstract, "body": body,
            "body_sha256": hashlib.sha256(body.encode()).hexdigest()}


def response(*, status="completed", task_id="task-synthetic"):
    value = {"method_assessment": "Synthetic advisory assessment.", "implementation_assessment": "Fixed candidate only.",
             "limitations": "Not out of sample or financial validation.",
             "source_url": review.GLOBAL_ETF_RESEARCH_SOURCE_URL,
             "candidate_commit": review.GLOBAL_ETF_RESEARCH_CODEGEN_UES_COMMIT}
    return SimpleNamespace(success=status == "completed", provider="dot", output=json.dumps(value),
                           raw={"status": status, "id": task_id, "result_kind": "advisory",
                                "model_requested": "configured-dot", "model_verification": "unavailable"})


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "_docker_preflight", lambda: "synthetic-docker")
    monkeypatch.setattr(review, "_read_global_base", lambda root: (review.GLOBAL_ETF_RESEARCH_CODEGEN_UES_COMMIT, {"candidate.py": "synthetic"}))
    monkeypatch.setattr(soxl_new_research, "_archive_codegen_base", lambda *args, **kwargs: None)
    tests = Mock(return_value={"status": "passed", "isolation": "synthetic"})
    fetch = Mock(side_effect=source)
    return {"ues_repo_root": tmp_path, "run_root": tmp_path / "state", "source_ref": "a" * 40,
            "fetch_source": fetch, "candidate_test_runner": tests}


def test_completed_review_replays_without_another_task_or_tests(prepared):
    execute = Mock(return_value=response())
    first = review.run_global_etf_review(**prepared, execute=execute)
    second = review.run_global_etf_review(**prepared, execute=execute)
    assert first["status"] == "review_completed" and second["replay"] is True
    assert first["live_authority_granted"] is False and first["changed_paths"] == []
    assert first["model_verification"] == "unavailable"
    assert execute.call_count == prepared["fetch_source"].call_count == prepared["candidate_test_runner"].call_count == 1


def test_pending_only_reads_original_task_with_identical_operation(prepared):
    execute = Mock(side_effect=[response(status="outcome_unknown"), response()])
    first = review.run_global_etf_review(**prepared, execute=execute)
    second = review.run_global_etf_review(**prepared, execute=execute)
    assert first["status"] == "pending" and second["status"] == "review_completed"
    calls = execute.call_args_list
    assert calls[0].kwargs["resume_task_id"] is None and calls[1].kwargs["resume_task_id"] == "task-synthetic"
    assert calls[0].kwargs["operation_id"] == calls[1].kwargs["operation_id"]
    assert calls[0].args == calls[1].args and prepared["fetch_source"].call_count == 1


def test_lost_submit_response_never_retriggers_native_execution(prepared):
    execute = Mock(side_effect=RuntimeError("private upstream detail"))
    with pytest.raises(review.GlobalResearchCodegenError, match="global_review_response_unknown"):
        review.run_global_etf_review(**prepared, execute=execute)
    result = review.run_global_etf_review(**prepared, execute=execute)
    assert result["status"] == "outcome_unknown" and execute.call_count == 1
    assert "private" not in json.dumps(result)


def test_concurrent_same_operation_makes_one_task(prepared):
    execute = Mock(return_value=response())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: review.run_global_etf_review(**prepared, execute=execute), range(2)))
    assert all(r["status"] == "review_completed" for r in results) and execute.call_count == 1


def test_bad_tests_and_source_stop_before_task(prepared):
    execute = Mock(return_value=response())
    prepared["candidate_test_runner"].return_value = {"status": "failed"}
    with pytest.raises(review.GlobalResearchCodegenError):
        review.run_global_etf_review(**prepared, execute=execute)
    execute.assert_not_called()
    bad = source(); bad["abstract"] = "Invented source text"
    prepared["fetch_source"].side_effect = None; prepared["fetch_source"].return_value = bad
    with pytest.raises(review.GlobalResearchCodegenError):
        review.run_global_etf_review(**prepared, execute=execute)
    execute.assert_not_called()


def test_wrong_pending_task_and_corrupt_cached_authority_are_rejected(prepared):
    execute = Mock(side_effect=[response(status="running"), response(task_id="other-task")])
    review.run_global_etf_review(**prepared, execute=execute)
    with pytest.raises(review.GlobalResearchCodegenError, match="task_binding"):
        review.run_global_etf_review(**prepared, execute=execute)
    state_path = prepared["run_root"] / "run-v2.json"
    state = json.loads(state_path.read_text())
    state.update(phase="terminal", result={"status": "review_completed", "live_authority_granted": True})
    state_path.write_text(json.dumps(state))
    with pytest.raises(review.GlobalResearchCodegenError, match="state_invalid"):
        review.run_global_etf_review(**prepared, execute=execute)


def test_frozen_caller_identity_cannot_reuse_another_callers_result(prepared):
    execute = Mock(return_value=response())
    review.run_global_etf_review(**prepared, execute=execute)
    prepared["source_ref"] = "b" * 40
    with pytest.raises(review.GlobalResearchCodegenError, match="binding_mismatch"):
        review.run_global_etf_review(**prepared, execute=execute)
    assert execute.call_count == 1


def test_plan_does_not_require_model_auth_or_start_research(monkeypatch, capsys):
    monkeypatch.delenv("AI_SERVICE_MODEL", raising=False)
    assert review.main([]) == 0
    assert json.loads(capsys.readouterr().out)["live_authority_granted"] is False
    assert review.main(["--execute"]) == 2
