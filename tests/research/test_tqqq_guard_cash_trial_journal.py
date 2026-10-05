"""Local, network-blocked synthetic acceptance for the actual guard-cash caller."""
from dataclasses import replace
import json
from pathlib import Path
import socket
import sys

import pandas as pd
import pytest

from quant_platform_kit.strategy_lifecycle.contracts import ResearchTrialStatus
from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore
from us_equity_strategies.research import tqqq_qqq_guard_cash_research as candidate


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("SYNTHETIC_CALLER_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(PerformanceStore, "_object_store", forbidden)


def _inputs(tmp_path, *, costs=(0,), split=False):
    days = pd.bdate_range("2023-01-02", periods=262)
    qqq, tqqq = [], []
    for i, day in enumerate(days):
        q = 100.0 + i * 0.1
        t = 25.0 if split and i >= 258 else 50.0
        qqq.append({"t": day.date().isoformat(), "o": q, "h": q, "l": q, "c": q, "v": 1000.0})
        tqqq.append({"t": day.date().isoformat(), "o": t, "h": t, "l": t, "c": t, "v": 1000.0})
    contract = {
        "candidate_id": candidate.CANDIDATE, "core": {"first_signal_min_qqq_bars": 257},
        "qqq_guard": {"lookback_sessions": 20, "soft_drawdown": -0.05,
                      "hard_drawdown": -0.10, "soft_scalar": 0.5,
                      "hard_scalar": 0.0, "max_price_age_days": 3},
        "portfolio": {"research_initial_usd": 10000, "cost_bps": list(costs)},
        "window": {"first_signal_rule": "index 256 after 257 complete common bars",
                   "short_window_sessions": 3}, "limitations": ["synthetic fixture only"],
    }
    actions = {"forward_splits": [], "cash_dividends": []}
    if split:
        actions["forward_splits"] = [{"id": "synthetic-split", "symbol": "TQQQ",
            "ex_date": days[258].date().isoformat(), "old_rate": 1, "new_rate": 2}]
    payload = {"synthetic": True, "contract": contract, "qqq": qqq, "tqqq": tqqq, "actions": actions}
    path = tmp_path / "synthetic-source.json"
    path.write_text(json.dumps(payload))
    store = PerformanceStore(local_root=tmp_path / "journal", cloud_bucket="")
    return path, store, tmp_path / "outputs", payload


def _run(case, **kwargs):
    path, store, out, _ = case
    return candidate.run_private(out, synthetic_fixture=path, store=store,
                                 trial_namespace="synthetic-attempt", **kwargs)


def _records(report, store):
    return [store.load_research_trial("us_equity", candidate.CANDIDATE, trial_id)
            for trial_id in report["journal"]["trial_ids"]]


def test_real_default_refuses_before_private_read_or_simulation(tmp_path, monkeypatch):
    monkeypatch.setattr(candidate, "_verified_bundle", lambda *_: pytest.fail("private read"))
    monkeypatch.setattr(candidate, "_simulate", lambda *_a, **_k: pytest.fail("real simulation"))
    with pytest.raises(ValueError, match="REAL_RESEARCH_DATA_UNQUALIFIED"):
        candidate.run_private(tmp_path / "absent-private-root")
    assert not list(tmp_path.rglob("*.json"))


def test_actual_entrypoint_freezes_params_starts_first_and_reads_complete_triplets(tmp_path, monkeypatch):
    case = _inputs(tmp_path, costs=(0, 20))
    source, store, out, _ = case
    original_simulate = candidate._simulate
    observations = []
    def observed(rows, actions, contract, **kwargs):
        label = "dynamic_guard_cash" if kwargs["use_guard"] else "fixed_core_cash"
        window = "short" if kwargs["sessions"] is not None else "full"
        scenario = f"{label}_{kwargs['cost_bps']}bps_{window}"
        started = store.load_research_trial("us_equity", candidate.CANDIDATE, "synthetic-attempt:" + scenario)
        assert started.status is ResearchTrialStatus.STARTED
        assert started.run_id is None and started.param_version is None
        assert started.actual_params["contract"] == contract
        for key in ("cost_bps", "use_guard", "sessions"):
            assert started.actual_params[key] == kwargs[key]
        assert kwargs["budget_selector"] is None
        output = original_simulate(rows, actions, contract, **kwargs)
        observations.append(output)
        return output
    monkeypatch.setattr(candidate, "_simulate", observed)
    original_adapter = candidate.build_guard_cash_trial_ledger
    adapters = []
    def adapter(**kwargs):
        assert kwargs["metrics"] is observations[-1][0]
        assert kwargs["daily_rows"] is observations[-1][1]
        adapters.append(kwargs["started"].actual_params)
        return original_adapter(**kwargs)
    monkeypatch.setattr(candidate, "build_guard_cash_trial_ledger", adapter)
    report = _run(case)
    assert report["status"] == "succeeded" and report["synthetic"] is True
    assert report["data_qualified"] is False and report["research_only"] is True
    assert report["execution_authorized"] is report["promotion_authorized"] is False
    assert report["no_order"] is True
    assert len(observations) == len(adapters) == 8
    assert len(list(out.glob("private_daily_*.json"))) == 8
    assert (out / "research_summary.v1.json").exists()
    for record in _records(report, store):
        assert record.status is ResearchTrialStatus.SUCCEEDED and record.synthetic is True
        result = store.load_backtest_by_run_id("us_equity", candidate.CANDIDATE, record.run_id,
                                             param_version=record.param_version)
        ledger = store.load_research_ledger("us_equity", candidate.CANDIDATE, record.trial_id,
                                           record.run_id, record.param_version)
        assert result.total_return == ledger.total_return
        assert {k: v for k, v in result.params.items() if k != "research_identity"} == record.actual_params
        assert record.source_revision.startswith("sha256:")
        assert result.validation_identity is None
        assert record.actual_params in adapters
    assert source.read_text() == json.dumps(case[3])


def test_terminal_replay_has_no_simulation_or_writes(tmp_path, monkeypatch):
    case = _inputs(tmp_path)
    first = _run(case)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*.json")}
    monkeypatch.setattr(candidate, "_simulate", lambda *_a, **_k: pytest.fail("terminal rerun"))
    again = _run(case)
    assert again["status"] == "stored"
    assert again["journal"]["trial_ids"] == first["journal"]["trial_ids"]
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*.json")}


def test_existing_started_remains_incomplete(tmp_path, monkeypatch):
    case = _inputs(tmp_path)
    first = _run(case)
    source, store, out, payload = case
    record = _records(first, store)[0]
    other_store = PerformanceStore(local_root=tmp_path / "incomplete")
    started = replace(record, status=ResearchTrialStatus.STARTED, reason_code="", run_id=None, param_version=None)
    other_store.save_research_trial(started)
    monkeypatch.setattr(candidate, "_simulate", lambda *_a, **_k: pytest.fail("incomplete rerun"))
    report = candidate.run_private(out, synthetic_fixture=source, store=other_store,
                                  trial_namespace="synthetic-attempt")
    assert report["status"] == "incomplete"
    assert other_store.load_research_trial("us_equity", candidate.CANDIDATE, started.trial_id) == started
    assert len(list(other_store.local_root.rglob("*.json"))) == 1


@pytest.mark.parametrize("bad", ["missing", "cloud", "implicit_root"])
def test_synthetic_requires_explicit_local_store(tmp_path, bad):
    case = _inputs(tmp_path)
    source, _, out, _ = case
    store = {"missing": None, "cloud": PerformanceStore(cloud_bucket="forbidden"),
             "implicit_root": PerformanceStore()}[bad]
    with pytest.raises(ValueError, match="LOCAL_RESEARCH_STORE_REQUIRED"):
        candidate.run_private(out, synthetic_fixture=source, store=store,
                              trial_namespace="synthetic-attempt")


@pytest.mark.parametrize("change", ["not_synthetic", "result", "qualification", "contract_qualification", "limitations", "duplicate_cost", "clipped_window"])
def test_fixture_cannot_inject_outputs_or_claim_qualification(tmp_path, change):
    case = _inputs(tmp_path)
    source, store, _, payload = case
    if change == "not_synthetic":
        payload["synthetic"] = False
    elif change == "result":
        payload["daily_rows"] = [{"nav": 999999}]
    elif change == "qualification":
        payload["qualified"] = True
    elif change == "contract_qualification":
        payload["contract"]["data_qualified"] = True
    elif change == "limitations":
        payload["contract"]["limitations"] = "synthetic-only"
    elif change == "duplicate_cost":
        payload["contract"]["portfolio"]["cost_bps"] = [0, 0]
    else:
        payload["contract"]["window"]["short_window_sessions"] = 50
    source.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="SYNTHETIC_FIXTURE_INVALID|INSUFFICIENT_REPLAY_WINDOW"):
        _run(case)
    assert not list(store.local_root.rglob("*.json"))


def test_same_trial_different_params_conflicts_before_simulation(tmp_path, monkeypatch):
    case = _inputs(tmp_path)
    _run(case)
    case[3]["contract"]["portfolio"]["research_initial_usd"] += 100
    case[0].write_text(json.dumps(case[3]))
    monkeypatch.setattr(candidate, "_simulate", lambda *_a, **_k: pytest.fail("conflicting identity"))
    with pytest.raises(ValueError, match="research_trial_conflict"):
        _run(case)


def test_simulator_cannot_mutate_frozen_params(tmp_path, monkeypatch):
    case = _inputs(tmp_path)
    original = candidate._simulate
    def mutate(rows, actions, contract, **kwargs):
        output = original(rows, actions, contract, **kwargs)
        contract["portfolio"]["research_initial_usd"] += 100
        return output
    monkeypatch.setattr(candidate, "_simulate", mutate)
    with pytest.raises(ValueError, match="SIMULATOR_PARAMS_CHANGED"):
        _run(case)
    record = case[1].load_research_trial("us_equity", candidate.CANDIDATE,
                                       "synthetic-attempt:dynamic_guard_cash_0bps_short")
    assert record.status is ResearchTrialStatus.REJECTED
    assert record.actual_params["contract"]["portfolio"]["research_initial_usd"] == 10000
    assert record.run_id is None and record.param_version is None


def test_actual_simulator_qpk_gap_is_rejected_without_fake_success(tmp_path):
    case = _inputs(tmp_path, costs=(20,), split=True)
    with pytest.raises(ValueError, match="QPK_SPLIT_TRADE_UNSUPPORTED"):
        _run(case)
    record = case[1].load_research_trial("us_equity", candidate.CANDIDATE,
                                       "synthetic-attempt:dynamic_guard_cash_20bps_short")
    assert record.status is ResearchTrialStatus.REJECTED and record.reason_code == "ledger_rejected"
    assert record.actual_params is not None and record.run_id is None
    assert not list(case[1].local_root.rglob("ledger.json"))
    assert not list(case[2].glob("*.json"))


@pytest.mark.parametrize("exception", [RuntimeError("synthetic private detail"), KeyboardInterrupt("cancel"), SystemExit(7)])
def test_simulation_errors_keep_original_and_known_params(tmp_path, monkeypatch, exception):
    case = _inputs(tmp_path)
    def fail(*_args, **_kwargs):
        raise exception
    monkeypatch.setattr(candidate, "_simulate", fail)
    with pytest.raises(type(exception)) as raised:
        _run(case)
    assert raised.value is exception
    record = case[1].load_research_trial("us_equity", candidate.CANDIDATE,
                                       "synthetic-attempt:dynamic_guard_cash_0bps_short")
    expected = ResearchTrialStatus.FAILED if isinstance(exception, RuntimeError) else ResearchTrialStatus.ABORTED
    assert record.status is expected and record.actual_params is not None
    assert record.run_id is None and record.param_version is None
    assert all("synthetic private detail" not in p.read_text() for p in case[1].local_root.rglob("*.json"))


@pytest.mark.parametrize("boundary", ["result", "ledger", "terminal"])
def test_persistence_failure_never_claims_success(tmp_path, monkeypatch, boundary):
    case = _inputs(tmp_path)
    error = OSError("synthetic persistence failure")
    if boundary == "terminal":
        original = PerformanceStore.save_research_trial
        def fail(self, record):
            if record.status is ResearchTrialStatus.SUCCEEDED:
                raise error
            return original(self, record)
        monkeypatch.setattr(PerformanceStore, "save_research_trial", fail)
    else:
        def fail(*_args, **_kwargs):
            raise error
        monkeypatch.setattr(PerformanceStore, "save_backtest_result" if boundary == "result" else "save_research_ledger", fail)
    with pytest.raises(OSError) as raised:
        _run(case)
    assert raised.value is error
    record = case[1].load_research_trial("us_equity", candidate.CANDIDATE,
                                       "synthetic-attempt:dynamic_guard_cash_0bps_short")
    assert record.status is ResearchTrialStatus.FAILED and record.reason_code == "persistence_failed"
    assert record.actual_params is not None and record.run_id is None


@pytest.mark.parametrize("boundary", ["start_write", "start_readback"])
def test_start_failure_prevents_simulation(tmp_path, monkeypatch, boundary):
    case = _inputs(tmp_path)
    monkeypatch.setattr(candidate, "_simulate", lambda *_a, **_k: pytest.fail("start not verified"))
    if boundary == "start_write":
        error = OSError("synthetic start unavailable")
        def fail(*_args, **_kwargs):
            raise error
        monkeypatch.setattr(PerformanceStore, "save_research_trial", fail)
        with pytest.raises(OSError) as raised:
            _run(case)
        assert raised.value is error
    else:
        original = PerformanceStore.load_research_trial
        def missing(self, *args, **kwargs):
            record = original(self, *args, **kwargs)
            return None if record is not None and record.status is ResearchTrialStatus.STARTED else record
        monkeypatch.setattr(PerformanceStore, "load_research_trial", missing)
        with pytest.raises(ValueError, match="RESEARCH_TRIAL_READBACK_INVALID"):
            _run(case)


def test_silent_success_terminal_write_is_not_success(tmp_path, monkeypatch):
    case = _inputs(tmp_path)
    original = PerformanceStore.save_research_trial
    def omit(self, record):
        if record.status is ResearchTrialStatus.STARTED:
            return original(self, record)
    monkeypatch.setattr(PerformanceStore, "save_research_trial", omit)
    with pytest.raises(ValueError, match="RESEARCH_TRIAL_READBACK_INVALID") as raised:
        _run(case)
    assert "research_terminal_write_failed" in raised.value.__notes__
    loaded = case[1].load_research_trial("us_equity", candidate.CANDIDATE,
                                       "synthetic-attempt:dynamic_guard_cash_0bps_short")
    assert loaded.status is ResearchTrialStatus.STARTED


@pytest.mark.parametrize("secondary", [OSError("synthetic recording failure"), KeyboardInterrupt(), SystemExit(3)])
def test_secondary_terminal_failure_never_replaces_original(tmp_path, monkeypatch, secondary):
    case = _inputs(tmp_path)
    original_error = RuntimeError("synthetic original")
    original = PerformanceStore.save_research_trial
    def record(self, trial):
        if trial.status is not ResearchTrialStatus.STARTED:
            raise secondary
        return original(self, trial)
    def simulation(*_args, **_kwargs):
        raise original_error
    monkeypatch.setattr(PerformanceStore, "save_research_trial", record)
    monkeypatch.setattr(candidate, "_simulate", simulation)
    with pytest.raises(RuntimeError) as raised:
        _run(case)
    assert raised.value is original_error
    assert "research_terminal_write_failed" in original_error.__notes__
    assert case[1].load_research_trial("us_equity", candidate.CANDIDATE,
        "synthetic-attempt:dynamic_guard_cash_0bps_short").status is ResearchTrialStatus.STARTED


def test_written_success_with_failed_readback_cannot_be_rewritten(tmp_path, monkeypatch):
    case = _inputs(tmp_path)
    original_load = PerformanceStore.load_research_trial
    original_save = PerformanceStore.save_research_trial
    writes = []
    def load(self, *args, **kwargs):
        record = original_load(self, *args, **kwargs)
        if record is not None and record.status is ResearchTrialStatus.SUCCEEDED:
            return None
        return record
    def save(self, record):
        writes.append(record.status)
        return original_save(self, record)
    monkeypatch.setattr(PerformanceStore, "load_research_trial", load)
    monkeypatch.setattr(PerformanceStore, "save_research_trial", save)
    with pytest.raises(ValueError, match="RESEARCH_TRIAL_READBACK_INVALID"):
        _run(case)
    assert writes == [ResearchTrialStatus.STARTED, ResearchTrialStatus.SUCCEEDED]
    assert original_load(case[1], "us_equity", candidate.CANDIDATE,
        "synthetic-attempt:dynamic_guard_cash_0bps_short").status is ResearchTrialStatus.SUCCEEDED


def test_export_failure_does_not_rewrite_succeeded_trials(tmp_path, monkeypatch):
    case = _inputs(tmp_path)
    error = OSError("synthetic export failure")
    original_write = Path.write_text
    original_save = PerformanceStore.save_research_trial
    writes = []
    def export(path, *args, **kwargs):
        if path.name.startswith("private_daily_"):
            raise error
        return original_write(path, *args, **kwargs)
    def save(self, record):
        writes.append(record.status)
        return original_save(self, record)
    monkeypatch.setattr(Path, "write_text", export)
    monkeypatch.setattr(PerformanceStore, "save_research_trial", save)
    with pytest.raises(OSError) as raised:
        _run(case)
    assert raised.value is error
    assert writes.count(ResearchTrialStatus.SUCCEEDED) == 4
    assert ResearchTrialStatus.FAILED not in writes and ResearchTrialStatus.ABORTED not in writes
    assert not (case[2] / "research_summary.v1.json").exists()


def test_cli_routes_explicit_synthetic_to_same_entrypoint(tmp_path, monkeypatch, capsys):
    case = _inputs(tmp_path)
    monkeypatch.setattr(sys, "argv", ["research", "--private-root", str(case[2]),
        "--synthetic-fixture", str(case[0]), "--journal-root", str(case[1].local_root),
        "--trial-namespace", "synthetic-attempt"])
    candidate.main()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "succeeded" and result["synthetic"] is True


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("boundary", ["ledger", "result_write", "ledger_write", "terminal_write"])
def test_cancel_at_each_post_simulation_boundary_preserves_known_params(tmp_path, monkeypatch, exception_type, boundary):
    case = _inputs(tmp_path)
    error = exception_type("synthetic cancellation")
    def fail(*_args, **_kwargs):
        raise error
    if boundary == "ledger":
        monkeypatch.setattr(candidate, "build_guard_cash_trial_ledger", fail)
    elif boundary == "terminal_write":
        original = PerformanceStore.save_research_trial
        def terminal(self, record):
            if record.status is ResearchTrialStatus.SUCCEEDED:
                raise error
            return original(self, record)
        monkeypatch.setattr(PerformanceStore, "save_research_trial", terminal)
    else:
        monkeypatch.setattr(PerformanceStore, "save_backtest_result" if boundary == "result_write" else "save_research_ledger", fail)
    with pytest.raises(exception_type) as raised:
        _run(case)
    assert raised.value is error
    record = case[1].load_research_trial("us_equity", candidate.CANDIDATE,
                                       "synthetic-attempt:dynamic_guard_cash_0bps_short")
    assert record.status is ResearchTrialStatus.ABORTED and record.reason_code == "trial_interrupted"
    assert record.actual_params is not None and record.run_id is None and record.param_version is None


@pytest.mark.parametrize("exception", [OSError("synthetic uncertain commit"), KeyboardInterrupt(), SystemExit(2)])
def test_terminal_write_committed_then_raised_is_never_compensated(tmp_path, monkeypatch, exception):
    case = _inputs(tmp_path)
    original = PerformanceStore.save_research_trial
    writes = []
    def committed(self, record):
        writes.append(record.status)
        original(self, record)
        if record.status is ResearchTrialStatus.SUCCEEDED:
            raise exception
    monkeypatch.setattr(PerformanceStore, "save_research_trial", committed)
    with pytest.raises(type(exception)) as raised:
        _run(case)
    assert raised.value is exception
    assert writes == [ResearchTrialStatus.STARTED, ResearchTrialStatus.SUCCEEDED]
    record = case[1].load_research_trial("us_equity", candidate.CANDIDATE,
                                       "synthetic-attempt:dynamic_guard_cash_0bps_short")
    assert record.status is ResearchTrialStatus.SUCCEEDED
    assert "research_terminal_already_recorded" in exception.__notes__
    assert not list(case[2].glob("*.json"))


@pytest.mark.parametrize("status", [ResearchTrialStatus.REJECTED, ResearchTrialStatus.FAILED, ResearchTrialStatus.ABORTED])
def test_known_non_success_terminal_is_returned_without_evaluation(tmp_path, monkeypatch, status):
    case = _inputs(tmp_path)
    first = _run(case)
    original_record = _records(first, case[1])[0]
    store = PerformanceStore(local_root=tmp_path / "non-success")
    started = replace(original_record, status=ResearchTrialStatus.STARTED, reason_code="", run_id=None, param_version=None)
    terminal = replace(started, status=status, reason_code="synthetic_prior_terminal")
    store.save_research_trial(started)
    store.save_research_trial(terminal)
    before = {p: p.read_bytes() for p in store.local_root.rglob("*.json")}
    monkeypatch.setattr(candidate, "_simulate", lambda *_a, **_k: pytest.fail("non-success terminal rerun"))
    report = candidate.run_private(case[2], synthetic_fixture=case[0], store=store,
                                  trial_namespace="synthetic-attempt")
    assert report["status"] == status.value
    assert before == {p: p.read_bytes() for p in store.local_root.rglob("*.json")}


def test_missing_persisted_success_ledger_does_not_trigger_rerun(tmp_path, monkeypatch):
    case = _inputs(tmp_path)
    report = _run(case)
    record = _records(report, case[1])[0]
    key = case[1]._research_key(record.domain, record.strategy_profile, record.trial_id, "ledger")
    case[1]._local_path(key).unlink()  # Test-owned temporary synthetic data only.
    monkeypatch.setattr(candidate, "_simulate", lambda *_a, **_k: pytest.fail("unverified success rerun"))
    with pytest.raises(ValueError, match="RESEARCH_TRIAL_READBACK_INVALID"):
        _run(case)
    assert case[1].load_research_trial(record.domain, record.strategy_profile, record.trial_id) is None


def test_ambient_cloud_configuration_cannot_change_explicit_local_backend(tmp_path, monkeypatch):
    case = _inputs(tmp_path)
    monkeypatch.setenv("LIFECYCLE_PERFORMANCE_BUCKET", "forbidden-bucket")
    monkeypatch.setattr(PerformanceStore, "from_env", lambda *_a, **_k: pytest.fail("ambient backend"))
    assert _run(case)["status"] == "succeeded"


def test_started_write_committed_then_raised_preserves_incomplete_uncertainty(tmp_path, monkeypatch):
    case = _inputs(tmp_path)
    error = OSError("synthetic uncertain start commit")
    original = PerformanceStore.save_research_trial
    def uncertain(self, record):
        original(self, record)
        if record.status is ResearchTrialStatus.STARTED:
            raise error
    monkeypatch.setattr(PerformanceStore, "save_research_trial", uncertain)
    monkeypatch.setattr(candidate, "_simulate", lambda *_a, **_k: pytest.fail("uncertain start must not evaluate"))
    with pytest.raises(OSError) as raised:
        _run(case)
    assert raised.value is error
    record = case[1].load_research_trial("us_equity", candidate.CANDIDATE,
                                       "synthetic-attempt:dynamic_guard_cash_0bps_short")
    assert record.status is ResearchTrialStatus.STARTED and record.actual_params is not None
    assert record.reason_code == "" and record.run_id is None and record.param_version is None
    assert len(list(case[1].local_root.rglob("*.json"))) == 1
    monkeypatch.setattr(PerformanceStore, "save_research_trial", original)
    assert _run(case)["status"] == "incomplete"


def test_unexpected_ledger_exception_is_failed_not_rejected(tmp_path, monkeypatch):
    case = _inputs(tmp_path)
    error = RuntimeError("synthetic private ledger detail")
    def fail(**_kwargs):
        raise error
    monkeypatch.setattr(candidate, "build_guard_cash_trial_ledger", fail)
    with pytest.raises(RuntimeError) as raised:
        _run(case)
    assert raised.value is error
    record = case[1].load_research_trial("us_equity", candidate.CANDIDATE,
                                       "synthetic-attempt:dynamic_guard_cash_0bps_short")
    assert record.status is ResearchTrialStatus.FAILED and record.reason_code == "ledger_failed"
    assert record.actual_params is not None and record.run_id is None


@pytest.mark.parametrize("status", ["succeeded", "stored", "incomplete", "rejected", "failed", "aborted"])
def test_main_prints_status_and_exits_nonzero_for_non_success(tmp_path, monkeypatch, capsys, status):
    calls = []
    report = {"candidate_id": candidate.CANDIDATE, "status": status, "synthetic": True,
              "contract_sha256": "synthetic-digest", "data_coverage": {},
              "journal": {"statuses": {"synthetic-scenario": "succeeded" if status in {"succeeded", "stored"} else
                                       "started" if status == "incomplete" else status}}}
    def once(*args, **kwargs):
        calls.append((args, kwargs))
        return report
    monkeypatch.setattr(candidate, "run_private", once)
    monkeypatch.setattr(candidate, "_simulate", lambda *_a, **_k: pytest.fail("main must not re-evaluate"))
    monkeypatch.setattr(sys, "argv", ["research", "--private-root", str(tmp_path)])
    if status in {"succeeded", "stored"}:
        candidate.main()
    else:
        with pytest.raises(SystemExit) as raised:
            candidate.main()
        assert raised.value.code == 1
    assert len(calls) == 1
    assert json.loads(capsys.readouterr().out)["status"] == status


@pytest.mark.parametrize("statuses", [{}, {"synthetic-scenario": "failed"}])
def test_main_stored_requires_all_trials_verified_success(tmp_path, monkeypatch, capsys, statuses):
    report = {"candidate_id": candidate.CANDIDATE, "status": "stored", "synthetic": True,
              "contract_sha256": "synthetic-digest", "data_coverage": {}, "journal": {"statuses": statuses}}
    monkeypatch.setattr(candidate, "run_private", lambda *_a, **_k: report)
    monkeypatch.setattr(sys, "argv", ["research", "--private-root", str(tmp_path)])
    with pytest.raises(SystemExit) as raised:
        candidate.main()
    assert raised.value.code == 1
    assert json.loads(capsys.readouterr().out)["status"] == "stored"


def test_main_real_default_exception_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["research", "--private-root", str(tmp_path)])
    monkeypatch.setattr(candidate, "_verified_bundle", lambda *_a: pytest.fail("private read"))
    with pytest.raises(ValueError, match="REAL_RESEARCH_DATA_UNQUALIFIED"):
        candidate.main()
