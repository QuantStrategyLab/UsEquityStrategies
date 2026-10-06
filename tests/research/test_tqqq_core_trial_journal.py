"""Offline acceptance for the journaled SMA caller; artificial bars and temporary stores only."""
from datetime import date, timedelta
from dataclasses import replace
from pathlib import Path

import hashlib
import importlib
import json
import socket

import pytest

from quant_platform_kit.strategy_lifecycle.contracts import ResearchTrialStatus
from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore
from us_equity_strategies.research import tqqq_core_optimization as core
from us_equity_strategies.research.tqqq_offline_input_contract import InputRow, OfflineInput

PROFILE = "tqqq_core_optimization_sma_journal_v1"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("SYNTHETIC_JOURNAL_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(PerformanceStore, "_object_store", denied)


@pytest.fixture
def source(monkeypatch):
    rows = []
    for i in range(753):
        day = (date(2023, 1, 2) + timedelta(days=i)).isoformat()
        q, opening = 100.0 + i % 31, 50.0 + i % 7
        closing = opening * (1.0 + (i % 5 - 2) / 100.0)
        rows.extend((
            InputRow("QQQ", day, q, q, q, q, 1.0),
            InputRow("TQQQ", day, opening, max(opening, closing),
                     min(opening, closing), closing, 1.0),
        ))
    lines = [",".join(core.INPUT_COLUMNS)]
    for row in rows:
        lines.append(",".join((row.symbol, row.as_of, *(
            format(v, ".17g") for v in (
                row.open, row.high, row.low, row.close, row.volume)))))
    raw = ("\n".join(lines) + "\n").encode()
    # Existing baseline-test convention: only synthetic fixture's hash is patched.
    monkeypatch.setattr(core, "EXPECTED_ARTIFACT_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(core, "_terminal_loss_probability", lambda returns: 0.0)
    return OfflineInput(tuple(rows), raw, core.EXPECTED_INPUT_DIGEST, "c" * 40)


def _run(source, store, namespace="synthetic-attempt"):
    try:
        module = importlib.import_module(
            "us_equity_strategies.research.tqqq_core_trial_journal")
    except ModuleNotFoundError as exc:
        if exc.name != "us_equity_strategies.research.tqqq_core_trial_journal":
            raise
        pytest.fail("RED: journaled SMA entrypoint does not exist at audited main")
    return module.run_journaled_tqqq_core_optimization(
        source, store=store, trial_namespace=namespace, synthetic=True)


def _stored_statuses(store):
    return [json.loads(p.read_text())["status"] for p in
            store.local_root.rglob("terminal.json")]


def test_started_readback_precedes_every_simulation(source, tmp_path, monkeypatch):
    store = PerformanceStore(local_root=tmp_path / "journal", cloud_bucket="")
    original = core.simulate_candidate
    seen = []
    def observed(src, window, scenario):
        found = []
        for path in store.local_root.rglob("started.json"):
            payload = json.loads(path.read_text())
            record = store.load_research_trial(
                payload["domain"], payload["strategy_profile"], payload["trial_id"])
            if record is not None and record.status is ResearchTrialStatus.STARTED:
                found.append(record)
        assert len(found) == 1
        record = found[0]
        assert record.actual_params["window_days"] == window
        assert record.actual_params["scenario_id"] == scenario.scenario_id
        assert record.actual_params["commission_bps"] == scenario.commission_bps
        assert record.actual_params["slippage_bps"] == scenario.slippage_bps
        assert record.synthetic is True and record.run_id is None
        seen.append((window, scenario.scenario_id))
        return original(src, window, scenario)
    monkeypatch.setattr(core, "simulate_candidate", observed)
    report = _run(source, store)
    assert report["status"] == "succeeded"
    assert len(seen) == 12
    assert _stored_statuses(store) == ["succeeded"] * 12


@pytest.mark.parametrize("boundary", ["write", "readback"])
def test_unverified_start_prevents_simulation(source, tmp_path, monkeypatch, boundary):
    store = PerformanceStore(local_root=tmp_path / "journal", cloud_bucket="")
    monkeypatch.setattr(core, "simulate_candidate",
                        lambda *a, **k: pytest.fail("unverified STARTED evaluated"))
    if boundary == "write":
        def fail(*args, **kwargs):
            raise OSError("synthetic start unavailable")
        monkeypatch.setattr(PerformanceStore, "save_research_trial", fail)
    else:
        original = PerformanceStore.load_research_trial
        def missing(self, *args, **kwargs):
            record = original(self, *args, **kwargs)
            return None if record is not None and record.status is ResearchTrialStatus.STARTED else record
        monkeypatch.setattr(PerformanceStore, "load_research_trial", missing)
    with pytest.raises((OSError, ValueError)):
        _run(source, store)
    assert "succeeded" not in _stored_statuses(store)


def test_verified_success_is_not_rerun(source, tmp_path, monkeypatch):
    store = PerformanceStore(local_root=tmp_path / "journal", cloud_bucket="")
    assert _run(source, store)["status"] == "succeeded"
    before = {str(p): p.read_bytes() for p in store.local_root.rglob("*.json")}
    monkeypatch.setattr(core, "simulate_candidate",
                        lambda *a, **k: pytest.fail("successful trial rerun"))
    assert _run(source, store)["status"] == "stored"
    assert before == {str(p): p.read_bytes() for p in store.local_root.rglob("*.json")}


def test_changed_parameters_conflict_before_evaluation(source, tmp_path, monkeypatch):
    store = PerformanceStore(local_root=tmp_path / "journal", cloud_bucket="")
    assert _run(source, store)["status"] == "succeeded"
    monkeypatch.setattr(core, "INITIAL_EQUITY", core.INITIAL_EQUITY + 100.0)
    monkeypatch.setattr(core, "simulate_candidate",
                        lambda *a, **k: pytest.fail("conflicting trial evaluated"))
    with pytest.raises(ValueError, match="conflict"):
        _run(source, store)


def test_commit_then_raise_keeps_started_incomplete(source, tmp_path, monkeypatch):
    store = PerformanceStore(local_root=tmp_path / "journal", cloud_bucket="")
    original = PerformanceStore.save_research_trial
    error = OSError("synthetic uncertain start")
    def uncertain(self, record):
        original(self, record)
        if record.status is ResearchTrialStatus.STARTED:
            raise error
    monkeypatch.setattr(PerformanceStore, "save_research_trial", uncertain)
    monkeypatch.setattr(core, "simulate_candidate",
                        lambda *a, **k: pytest.fail("uncertain start evaluated"))
    with pytest.raises(OSError) as raised:
        _run(source, store)
    assert raised.value is error
    assert len(list(store.local_root.rglob("started.json"))) == 1
    assert not _stored_statuses(store)
    monkeypatch.setattr(PerformanceStore, "save_research_trial", original)
    assert _run(source, store)["status"] == "incomplete"


def test_cloud_store_is_rejected_before_simulation(source, tmp_path, monkeypatch):
    store = PerformanceStore(local_root=tmp_path / "journal", cloud_bucket="forbidden-bucket")
    monkeypatch.setattr(core, "simulate_candidate",
                        lambda *a, **k: pytest.fail("cloud-backed evaluation"))
    with pytest.raises(ValueError, match="LOCAL_RESEARCH_STORE_REQUIRED"):
        _run(source, store)

def _module():
    return importlib.import_module("us_equity_strategies.research.tqqq_core_trial_journal")


def _records(report, store):
    return [store.load_research_trial("us_equity", PROFILE, trial_id)
            for trial_id in report["journal"]["trial_ids"]]


def test_triplets_preserve_legacy_result_and_exact_cost_units(source, tmp_path, monkeypatch):
    store = PerformanceStore(local_root=tmp_path / "journal", cloud_bucket="")
    actual = {}
    original = core.simulate_candidate
    def observed(src, window, scenario):
        points = original(src, window, scenario)
        actual[(window, scenario.scenario_id)] = points
        return points
    monkeypatch.setattr(core, "simulate_candidate", observed)
    report = _run(source, store)
    assert report["optimization"] == core.run_tqqq_core_optimization(source)
    assert report["data_qualified"] is report["execution_authorized"] is report["promotion_authorized"] is False
    assert report["research_only"] is report["synthetic"] is report["no_order"] is True
    for record in _records(report, store):
        assert record.status is ResearchTrialStatus.SUCCEEDED
        ledger = store.load_research_ledger("us_equity", PROFILE, record.trial_id,
                                           record.run_id, record.param_version)
        result = store.load_backtest_by_run_id("us_equity", PROFILE, record.run_id,
                                               param_version=record.param_version)
        params = record.actual_params
        points = actual[(params["window_days"], params["scenario_id"])]
        assert ledger.initial_nav == ledger.initial_cash == core.INITIAL_EQUITY
        assert ledger.initial_positions == ()
        assert ledger.observation_count == 753 - params["window_days"]
        assert ledger.total_fees == sum(p.commission_paid for p in points)
        assert ledger.events_complete is False  # Frozen SMA is not action-complete.
        assert ledger.total_return == result.total_return == points[-1].end_equity / core.INITIAL_EQUITY - 1
        assert result.params["research_identity"] == record.research_identity
        assert {k: v for k, v in result.params.items() if k != "research_identity"} == params
        assert result.source_revision == record.source_revision
        assert record.cost_inputs == {"commission_bps": float(params["commission_bps"]),
                                      "slippage_bps": float(params["slippage_bps"])}
        previous_cash = core.INITIAL_EQUITY
        for day, point in zip(ledger.days, points, strict=True):
            assert day.nav == point.end_equity and day.daily_return == point.daily_return
            assert day.fees == point.commission_paid
            assert day.cash == pytest.approx(previous_cash + day.trade_net_cashflow - day.fees, abs=1e-9)
            assert (day.positions[0].quantity if day.positions else 0.0) == point.quantity
            previous_cash = day.cash


def test_implementation_identity_hashes_actual_shared_helper(source, tmp_path, monkeypatch):
    store = PerformanceStore(local_root=tmp_path / "journal", cloud_bucket="")
    module = _module()
    report = _run(source, store)
    helper_hash = hashlib.sha256(Path(module.journal.__file__).read_bytes()).hexdigest()
    assert report["implementation_sha256"]["trial_journal"] == helper_hash
    record = _records(report, store)[0]
    assert record.research_identity["implementation_sha256"] == report["implementation_sha256"]
    assert record.source_revision == "sha256:" + module._digest(report["implementation_sha256"])
    original = module._implementation
    monkeypatch.setattr(module, "_implementation", lambda: {**original(), "trial_journal": "f" * 64})
    monkeypatch.setattr(core, "simulate_candidate", lambda *a, **k: pytest.fail("changed source rerun"))
    with pytest.raises(ValueError, match="conflict"):
        _run(source, store)


@pytest.mark.parametrize("status", [ResearchTrialStatus.STARTED, ResearchTrialStatus.FAILED,
                                    ResearchTrialStatus.REJECTED, ResearchTrialStatus.ABORTED])
def test_partial_attempt_never_fills_missing_slots(source, tmp_path, monkeypatch, status):
    full = PerformanceStore(local_root=tmp_path / "full", cloud_bucket="")
    first = _records(_run(source, full), full)[0]
    store = PerformanceStore(local_root=tmp_path / "partial", cloud_bucket="")
    started = replace(first, status=ResearchTrialStatus.STARTED, reason_code="", run_id=None, param_version=None)
    store.save_research_trial(started)
    if status is not ResearchTrialStatus.STARTED:
        store.save_research_trial(replace(started, status=status, reason_code="synthetic_prior_terminal"))
    before = {str(p): p.read_bytes() for p in store.local_root.rglob("*.json")}
    monkeypatch.setattr(core, "simulate_candidate", lambda *a, **k: pytest.fail("partial resumed"))
    report = _run(source, store)
    assert report["status"] == ("incomplete" if status is ResearchTrialStatus.STARTED else status.value)
    assert list(report["journal"]["statuses"].values()).count("not_started") == 11
    assert before == {str(p): p.read_bytes() for p in store.local_root.rglob("*.json")}


def test_new_namespace_records_new_attempt_without_overwrite(source, tmp_path):
    store = PerformanceStore(local_root=tmp_path / "journal", cloud_bucket="")
    first = _run(source, store)
    before = {str(p): p.read_bytes() for p in store.local_root.rglob("*.json")}
    second = _run(source, store, namespace="synthetic-attempt-two")
    assert second["status"] == "succeeded"
    assert set(first["journal"]["trial_ids"]).isdisjoint(second["journal"]["trial_ids"])
    assert len(list(store.local_root.rglob("started.json"))) == 24
    for path, data in before.items():
        assert Path(path).read_bytes() == data


@pytest.mark.parametrize("boundary", ["simulation", "ledger", "result_write", "ledger_write", "terminal_write"])
@pytest.mark.parametrize("exception_type", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_failure_boundaries_preserve_original_and_known_params(
        source, tmp_path, monkeypatch, boundary, exception_type):
    store = PerformanceStore(local_root=tmp_path / "journal", cloud_bucket="")
    module = _module()
    error = exception_type("private synthetic detail")
    def fail(*args, **kwargs):
        raise error
    if boundary == "simulation":
        monkeypatch.setattr(core, "simulate_candidate", fail)
    elif boundary == "ledger":
        monkeypatch.setattr(module, "_build_ledger", fail)
    elif boundary == "terminal_write":
        original = PerformanceStore.save_research_trial
        def terminal(self, record):
            if record.status is ResearchTrialStatus.SUCCEEDED:
                raise error
            return original(self, record)
        monkeypatch.setattr(PerformanceStore, "save_research_trial", terminal)
    else:
        name = "save_backtest_result" if boundary == "result_write" else "save_research_ledger"
        monkeypatch.setattr(PerformanceStore, name, fail)
    with pytest.raises(exception_type) as raised:
        _run(source, store)
    assert raised.value is error
    payload = json.loads(next(store.local_root.rglob("started.json")).read_text())
    record = store.load_research_trial(payload["domain"], payload["strategy_profile"], payload["trial_id"])
    assert record.status is (ResearchTrialStatus.FAILED if exception_type is RuntimeError else ResearchTrialStatus.ABORTED)
    assert record.actual_params["window_days"] == 150
    assert record.run_id is record.param_version is None
    assert "private synthetic detail" not in "".join(p.read_text() for p in store.local_root.rglob("*.json"))


def test_accounting_contradiction_is_rejected(source, tmp_path, monkeypatch):
    store = PerformanceStore(local_root=tmp_path / "journal", cloud_bucket="")
    original = core.simulate_candidate
    def corrupt(*args, **kwargs):
        points = original(*args, **kwargs)
        return (replace(points[0], commission_paid=100.0), *points[1:])
    monkeypatch.setattr(core, "simulate_candidate", corrupt)
    with pytest.raises(ValueError, match="SMA_LEDGER_ACCOUNTING_MISMATCH"):
        _run(source, store)
    terminal = json.loads(next(store.local_root.rglob("terminal.json")).read_text())
    assert terminal["status"] == "rejected" and terminal["reason_code"] == "ledger_rejected"


@pytest.mark.parametrize("boundary", ["omitted_terminal", "hidden_success_readback", "committed_then_raised"])
def test_uncertain_terminal_never_overwrites_success(source, tmp_path, monkeypatch, boundary):
    store = PerformanceStore(local_root=tmp_path / "journal", cloud_bucket="")
    original_save = PerformanceStore.save_research_trial
    original_load = PerformanceStore.load_research_trial
    error = OSError("synthetic uncertain terminal")
    writes = []
    def save(self, record):
        writes.append(record.status)
        if boundary == "omitted_terminal" and record.status is not ResearchTrialStatus.STARTED:
            return
        original_save(self, record)
        if boundary == "committed_then_raised" and record.status is ResearchTrialStatus.SUCCEEDED:
            raise error
    def load(self, *args, **kwargs):
        record = original_load(self, *args, **kwargs)
        if boundary == "hidden_success_readback" and record is not None and record.status is ResearchTrialStatus.SUCCEEDED:
            return None
        return record
    monkeypatch.setattr(PerformanceStore, "save_research_trial", save)
    monkeypatch.setattr(PerformanceStore, "load_research_trial", load)
    with pytest.raises((OSError, ValueError)):
        _run(source, store)
    first = json.loads(next(store.local_root.rglob("started.json")).read_text())
    actual = original_load(store, first["domain"], first["strategy_profile"], first["trial_id"])
    if boundary == "omitted_terminal":
        assert actual.status is ResearchTrialStatus.STARTED
    else:
        assert actual.status is ResearchTrialStatus.SUCCEEDED
        assert writes == [ResearchTrialStatus.STARTED, ResearchTrialStatus.SUCCEEDED]


@pytest.mark.parametrize("target", ["ledger", "result", "terminal"])
def test_corrupt_success_fails_readback_without_rerun(source, tmp_path, monkeypatch, target):
    store = PerformanceStore(local_root=tmp_path / "journal", cloud_bucket="")
    report = _run(source, store)
    record = _records(report, store)[0]
    if target == "result":
        result = store.load_backtest_by_run_id("us_equity", PROFILE, record.run_id, param_version=1)
        path = store._local_path(store._backtest_key(result))
    else:
        path = store._local_path(store._research_key("us_equity", PROFILE, record.trial_id, target))
    path.write_text("{}")  # Test-owned synthetic temporary file only.
    monkeypatch.setattr(core, "simulate_candidate", lambda *a, **k: pytest.fail("corrupt evidence rerun"))
    with pytest.raises(ValueError, match="RESEARCH_TRIAL_READBACK_INVALID"):
        _run(source, store)


def test_aggregate_failure_does_not_rewrite_verified_slot_success(source, tmp_path, monkeypatch):
    store = PerformanceStore(local_root=tmp_path / "journal", cloud_bucket="")
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic aggregate failure")
    monkeypatch.setattr(core, "_window_metrics", fail)
    report = _run(source, store)
    assert report["status"] == "rejected"
    assert report["optimization"]["evidence_valid"] is False
    assert _stored_statuses(store) == ["succeeded"] * 12
    monkeypatch.setattr(core, "simulate_candidate", lambda *a, **k: pytest.fail("stored slots rerun"))
    again = _run(source, store)
    assert again["status"] == "stored" and again["optimization"] is None


def test_ambient_cloud_and_false_qualification_cannot_change_backend(source, tmp_path, monkeypatch):
    store = PerformanceStore(local_root=tmp_path / "journal", cloud_bucket="")
    monkeypatch.setenv("LIFECYCLE_PERFORMANCE_BUCKET", "forbidden")
    monkeypatch.setattr(PerformanceStore, "from_env", lambda *a, **k: pytest.fail("ambient cloud selection"))
    with pytest.raises(ValueError, match="REAL_RESEARCH_DATA_UNQUALIFIED"):
        _module().run_journaled_tqqq_core_optimization(source, store=store,
            trial_namespace="real-not-authorized", synthetic=False)
    assert not list(tmp_path.rglob("*.json"))
    assert _run(source, store)["status"] == "succeeded"


def test_guard_provenance_uses_actual_caller_and_shared_helper(tmp_path, monkeypatch):
    # Reuse the existing guard acceptance fixture, not its strategy-specific ledger.
    import test_tqqq_guard_cash_trial_journal as guard_tests
    guard = guard_tests.candidate
    case = guard_tests._inputs(tmp_path)
    report = guard_tests._run(case)
    caller_hash = hashlib.sha256(Path(guard.__file__).read_bytes()).hexdigest()
    helper_hash = hashlib.sha256(Path(guard.tqqq_trial_journal.__file__).read_bytes()).hexdigest()
    expected = {"research_adapter": caller_hash, "trial_journal": helper_hash}
    assert {key: report["implementation_sha256"][key] for key in expected} == expected
    assert all(record.source_revision == "sha256:" + guard._canonical_digest(expected)
               for record in guard_tests._records(report, case[1]))
    original_hash = guard._sha256
    monkeypatch.setattr(guard, "_sha256", lambda path:
        "f" * 64 if path == Path(guard.tqqq_trial_journal.__file__) else original_hash(path))
    monkeypatch.setattr(guard, "_simulate", lambda *a, **k: pytest.fail("changed guard provenance rerun"))
    with pytest.raises(ValueError, match="research_trial_conflict"):
        guard_tests._run(case)
