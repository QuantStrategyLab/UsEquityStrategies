"""Offline acceptance for the journaled SMA caller; artificial bars and temporary stores only."""
from datetime import date, timedelta
from dataclasses import replace
from pathlib import Path

import hashlib
import importlib
import json
import math
import socket

import pytest

from quant_platform_kit.strategy_lifecycle.contracts import (
    ResearchDailyLedger, ResearchLedgerDay, ResearchPositionMark, ResearchTrialStatus,
)
from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore
from us_equity_strategies.research import tqqq_core_optimization as core
from us_equity_strategies.research import soxl_core_optimization as soxl
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
    before = {p: p.read_bytes() for p in store.local_root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="RESEARCH_TRIAL_NOT_SUCCEEDED:" + record.status.value):
        module.report_journaled_tqqq_core_metrics(
            store=store, trial_id=record.trial_id, annual_risk_free_rate=0.0,
            annual_minimum_acceptable_return=0.0)
    assert before == {p: p.read_bytes() for p in store.local_root.rglob("*") if p.is_file()}


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
    with pytest.raises(ValueError, match="RESEARCH_TRIAL_READBACK_INVALID"):
        _module().report_journaled_tqqq_core_metrics(
            store=store, trial_id=record.trial_id, annual_risk_free_rate=0.0,
            annual_minimum_acceptable_return=0.0)


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


def _window_points(module, navs):
    return tuple(module.DailyPoint(
        date=f"2024-01-0{i + 2}", start_equity=start, end_equity=end,
        cash=end, quantity=0.0, daily_return=end / start - 1.0,
        transition=False, commission_paid=0.0, slippage_impact_vs_open=0.0,
        gross_traded_notional_at_open=0.0,
    ) for i, (start, end) in enumerate(zip(navs, navs[1:])))


@pytest.mark.parametrize("module", [core, soxl], ids=["tqqq", "soxl"])
def test_common_window_semantics_use_sample_variance_and_initial_nav(module):
    points = _window_points(module, (100.0, 90.0, 90.0, 99.0))
    args = (200, 200, 202) if module is core else (200, 202)
    metrics = module._window_metrics(points, *args)
    assert metrics["observation_count"] == 3
    assert metrics["cumulative_return"] == pytest.approx(-0.01)
    assert metrics["max_drawdown"] == pytest.approx(-0.1)
    assert metrics["expected_shortfall_95"] == pytest.approx(-0.1)
    assert metrics["annualized_volatility"] == pytest.approx(0.1 * math.sqrt(252))
    assert metrics["annualized_volatility"] != pytest.approx(math.sqrt(1.68))
    # Costs are disclosures of already-net NAV, not another deduction here.
    cost_points = (replace(points[0], commission_paid=1.0,
                           slippage_impact_vs_open=2.0), *points[1:])
    cost_metrics = module._window_metrics(cost_points, *args)
    assert cost_metrics["total_cost"] == 3.0
    for key in ("cumulative_return", "max_drawdown", "expected_shortfall_95",
                "annualized_volatility"):
        assert cost_metrics[key] == metrics[key]


def test_zero_undefined_and_uncomputed_metrics_remain_distinct():
    tqqq = core._window_metrics(_window_points(core, (100.0,) * 4), 200, 200, 202)
    other = soxl._window_metrics(_window_points(soxl, (100.0,) * 4), 200, 202)
    for metrics in (tqqq, other):
        for key in ("cumulative_return", "max_drawdown", "annualized_volatility",
                    "expected_shortfall_95"):
            assert metrics[key] == 0.0
    assert other["sharpe"] is None
    assert "sharpe" not in tqqq and "sortino" not in tqqq
    contract = _module()._evaluation_contract()
    assert contract["rf"]["status"] == contract["mar"]["status"] == "NOT_USED"
    assert contract["uncomputed_metrics"] == ["sharpe", "sortino", "dsr", "pbo"]
    assert contract["sample"]["effective_independent_observations"] is None


def test_evaluation_contract_is_frozen_read_back_before_simulation(source, tmp_path, monkeypatch):
    store = PerformanceStore(local_root=tmp_path / "journal", cloud_bucket="")
    module = _module()
    expected = module._evaluation_contract()
    original = core.simulate_candidate
    seen = []
    def observed(src, window, scenario):
        trial_id = f"synthetic-attempt:sma{window}:{scenario.scenario_id}"
        record = store.load_research_trial("us_equity", PROFILE, trial_id)
        assert record.status is ResearchTrialStatus.STARTED
        config = record.actual_params["study_config"]
        assert config["evaluation_contract"] == expected
        assert record.candidate_config_id == "sha256:" + module._digest(config)
        seen.append(trial_id)
        return original(src, window, scenario)
    monkeypatch.setattr(core, "simulate_candidate", observed)
    report = _run(source, store)
    assert len(seen) == 12
    assert report["evaluation_contract"] == expected
    monkeypatch.setattr(core, "simulate_candidate", original)
    assert report["optimization"] == core.run_tqqq_core_optimization(source)
    assert "evaluation_contract" not in report["optimization"]
    assert expected["volatility"] == {"ddof": 1, "periods_per_year": 252,
                                      "annualization": "SQRT_PERIODS_PER_YEAR"}
    assert expected["metric_roles"]["total_cost"] == ["REPORT"]
    assert expected["metric_roles"]["annualized_volatility"] == ["REPORT", "PARETO_COMPARISON"]
    assert expected["metric_roles"]["mc_terminal_loss_probability_c2_5"] == ["REPORT", "ELIGIBILITY_VETO"]
    assert expected["uncertainty"]["configured_path_count"] == core.MC_TRIALS
    assert expected["uncertainty"]["path_count_is_independent_observation_count"] is False
    assert expected["untouched_holdout_established"] is False


@pytest.mark.parametrize("section,key,value", [
    ("volatility", "ddof", 0), ("volatility", "periods_per_year", 365),
    ("cost", "net_nav_costs_deducted_again", True),
    ("metric_roles", "total_cost", ["REPORT", "ELIGIBILITY_VETO"]),
])
def test_changed_evaluation_contract_conflicts_without_rewriting_or_simulation(
        source, tmp_path, monkeypatch, section, key, value):
    store = PerformanceStore(local_root=tmp_path / "journal", cloud_bucket="")
    module = _module()
    _run(source, store)
    before = {p: p.read_bytes() for p in store.local_root.rglob("*.json")}
    original = module._evaluation_contract
    def changed():
        contract = original()
        contract[section][key] = value
        return contract
    monkeypatch.setattr(module, "_evaluation_contract", changed)
    monkeypatch.setattr(core, "simulate_candidate", lambda *a, **k: pytest.fail("changed contract simulated"))
    with pytest.raises(ValueError, match="research_trial_conflict"):
        _run(source, store)
    assert before == {p: p.read_bytes() for p in store.local_root.rglob("*.json")}


def test_reported_eligibility_boundaries_match_the_existing_evaluator():
    gates = _module()._evaluation_contract()["eligibility"]
    assert gates == {"positive_folds_minimum": 2, "fold_count": 3,
                     "final_c2_5_return_strictly_above": 0.0,
                     "final_stress_return_strictly_above": 0.0,
                     "terminal_loss_probability_strictly_below": 0.5}
    assert core._eligibility((0.1, 0.1, -0.1), 0.1, 0.1, 0.49)[0] == "PASS"
    assert core._eligibility((0.1, 0.0, -0.1), 0.1, 0.1, 0.49)[0] == "FAIL"
    for final, stress, probability in ((0.0, 0.1, 0.49), (0.1, 0.0, 0.49), (0.1, 0.1, 0.5)):
        assert core._eligibility((0.1, 0.1, -0.1), final, stress, probability)[0] == "FAIL"


def test_legacy_namespace_without_contract_is_not_backfilled(source, tmp_path, monkeypatch):
    full = PerformanceStore(local_root=tmp_path / "full", cloud_bucket="")
    first = _records(_run(source, full), full)[0]
    params = json.loads(json.dumps(first.actual_params))
    del params["study_config"]["evaluation_contract"]
    old = replace(first, status=ResearchTrialStatus.STARTED, reason_code="",
                  run_id=None, param_version=None, actual_params=params,
                  candidate_config_id="sha256:" + _module()._digest(params["study_config"]))
    store = PerformanceStore(local_root=tmp_path / "old", cloud_bucket="")
    store.save_research_trial(old)
    before = {p: p.read_bytes() for p in store.local_root.rglob("*.json")}
    monkeypatch.setattr(core, "simulate_candidate", lambda *a, **k: pytest.fail("old namespace simulated"))
    with pytest.raises(ValueError, match="research_trial_conflict"):
        _run(source, store)
    assert before == {p: p.read_bytes() for p in store.local_root.rglob("*.json")}


def _metric_ledger(navs):
    """A closed synthetic account: USD 40 cash plus a marked fractional-share leg."""
    return ResearchDailyLedger(
        trial_id="synthetic-metrics", domain="us_equity", strategy_profile=PROFILE,
        run_id="synthetic-metrics", param_version=1, input_id="synthetic-nav-path",
        calendar_id="XNYS", periods_per_year=252, cost_source="synthetic-net-nav",
        cost_inputs={"commission_bps": 0.0, "slippage_bps": 0.0},
        initial_session_date=date(2024, 1, 1), initial_nav=navs[0], initial_cash=40.0,
        initial_positions=(ResearchPositionMark("TQQQ", 0.5, navs[0] - 40.0),),
        days=tuple(ResearchLedgerDay(
            session_date=date(2024, 1, i + 2), cash=40.0,
            positions=(ResearchPositionMark("TQQQ", 0.5, end - 40.0),),
            trade_net_cashflow=0.0, fees=0.0, nav=end, daily_return=end / start - 1.0,
        ) for i, (start, end) in enumerate(zip(navs, navs[1:]))), synthetic=True,
    )


def _research_metrics(ledger, **kwargs):
    return _module().compute_synthetic_research_metrics(
        ledger, annual_risk_free_rate=kwargs.pop("annual_risk_free_rate", 0.0),
        annual_minimum_acceptable_return=kwargs.pop("annual_minimum_acceptable_return", 0.0),
        **kwargs)


def test_journal_metric_report_consumes_verified_trial_without_writes(source, tmp_path, monkeypatch):
    store = PerformanceStore(local_root=tmp_path / "journal", cloud_bucket="")
    original = _run(source, store)
    record = next(r for r in _records(original, store)
                  if r.actual_params["window_days"] == 200 and r.actual_params["scenario_id"] == "C2_5")
    ledger = store.load_research_ledger("us_equity", PROFILE, record.trial_id, record.run_id, 1)
    before = {p: p.read_bytes() for p in store.local_root.rglob("*") if p.is_file()}
    monkeypatch.setattr(core, "simulate_candidate", lambda *a: pytest.fail("report resimulated"))
    report = _module().report_journaled_tqqq_core_metrics(
        store=store, trial_id=record.trial_id, annual_risk_free_rate=0.03,
        annual_minimum_acceptable_return=0.06)
    assert report["metrics"] == _research_metrics(
        ledger, annual_risk_free_rate=0.03, annual_minimum_acceptable_return=0.06)["metrics"]
    assert report["report_profile"] == "tqqq_core_trial_metric_report_v1"
    assert report["source_trial_status"] == "succeeded"
    assert ledger.total_fees > 0 and any(day.cash > 0 for day in ledger.days)
    assert any(day.daily_return == 0.0 for day in ledger.days)
    assert before == {p: p.read_bytes() for p in store.local_root.rglob("*") if p.is_file()}
    assert original["evaluation_contract"]["uncomputed_metrics"] == ["sharpe", "sortino", "dsr", "pbo"]
    # Both actual simulators receive the same generated bars and cost inputs.
    soxl_rows = tuple(soxl.InputRow(
        "SOXX" if row.symbol == "QQQ" else "SOXL", row.as_of,
        row.open, row.high, row.low, row.close, row.volume) for row in source.rows)
    lines = [",".join(core.INPUT_COLUMNS)] + [",".join((row.symbol, row.as_of, *(
        format(v, ".17g") for v in (row.open, row.high, row.low, row.close, row.volume))))
        for row in soxl_rows]
    soxl_source = soxl.OfflineInput(soxl_rows, ("\n".join(lines) + "\n").encode(), "a" * 64, "synthetic")
    soxl_points = soxl.simulate_candidate(soxl_source, 200, soxl.SCENARIOS[2])
    paired = soxl.report_soxl_core_candidate_metrics(
        soxl_source, soxl_points, window_days=200, scenario=soxl.SCENARIOS[2],
        synthetic=True, calendar_id="XNYS", periods_per_year=252,
        annual_risk_free_rate=0.03, annual_minimum_acceptable_return=0.06)
    assert paired["metrics"] == report["metrics"]
    assert paired["metric_status"] == report["metric_status"]
    assert paired["evaluation_contract"]["rf"] == report["evaluation_contract"]["rf"]
    first, third = ledger.days[0].session_date, ledger.days[2].session_date
    windowed = _module().report_journaled_tqqq_core_metrics(
        store=store, trial_id=record.trial_id, annual_risk_free_rate=0.03,
        annual_minimum_acceptable_return=0.06, start_session=first, end_session=third)
    assert windowed["observation_count"] == 3
    assert windowed["metrics"] == _research_metrics(
        ledger, annual_risk_free_rate=0.03, annual_minimum_acceptable_return=0.06,
        start_session=first, end_session=third)["metrics"]
    for field, value in (("annual_risk_free_rate", None), ("annual_risk_free_rate", float("nan")),
                         ("annual_minimum_acceptable_return", float("inf"))):
        rates = {"annual_risk_free_rate": 0.03, "annual_minimum_acceptable_return": 0.06, field: value}
        with pytest.raises(ValueError, match="RESEARCH_RATE_INVALID"):
            _module().report_journaled_tqqq_core_metrics(store=store, trial_id=record.trial_id, **rates)
    assert before == {p: p.read_bytes() for p in store.local_root.rglob("*") if p.is_file()}


def test_opt_in_metrics_hand_calculation_distinguishes_rf_mar_and_ddof():
    report = _research_metrics(_metric_ledger((100.0, 90.0, 90.0, 99.0)),
                               annual_risk_free_rate=0.252,
                               annual_minimum_acceptable_return=0.504)
    metrics = report["metrics"]
    assert metrics["cumulative_return"] == pytest.approx(-0.01)
    assert metrics["max_drawdown"] == pytest.approx(-0.1)
    assert metrics["annualized_volatility"] == pytest.approx(0.1 * math.sqrt(252))
    assert metrics["sharpe"] == pytest.approx(-0.001 / 0.1 * math.sqrt(252))
    downside = math.sqrt((0.102 ** 2 + 0.002 ** 2) / 3)
    assert metrics["sortino"] == pytest.approx(-0.002 / downside * math.sqrt(252))
    assert metrics["sharpe"] < 0 and metrics["sortino"] < 0
    assert report["observation_count"] == 3
    assert report["evaluation_contract"]["rf"]["per_session"] == 0.001
    assert report["evaluation_contract"]["mar"]["per_session"] == 0.002
    assert report["metric_status"]["sharpe"] == "COMPUTED"
    assert report["metric_status"]["sortino"] == "COMPUTED"
    assert metrics["dsr"] is metrics["pbo"] is None
    assert report["effective_independent_observations"] is None
    assert report["metric_status"]["dsr"] == report["metric_status"]["pbo"] == "NOT_COMPUTED"
    assert report["synthetic"] is report["research_only"] is report["no_order"] is True
    assert report["promotion_eligible"] is report["live_ready"] is False


@pytest.mark.parametrize("navs,status", [
    ((100.0, 100.0), "INSUFFICIENT_OBSERVATIONS"),
    ((100.0, 100.0, 100.0, 100.0), "CONSTANT_RETURN_SAMPLE"),
    ((100.0, 50.0, 25.0), "CONSTANT_RETURN_SAMPLE"),
    ((100.0, 200.0, 400.0), "CONSTANT_RETURN_SAMPLE"),
])
def test_opt_in_ratios_do_not_zero_fill_constant_or_short_samples(navs, status):
    # Use a fully invested leg for the declining constant path.
    ledger = _metric_ledger((100.0,) * len(navs))
    ledger = replace(ledger, initial_cash=0.0,
                     initial_positions=(ResearchPositionMark("TQQQ", 0.5, navs[0]),),
                     days=tuple(replace(day, cash=0.0, nav=end,
                         positions=(ResearchPositionMark("TQQQ", 0.5, end),),
                         daily_return=end / start - 1.0)
                         for day, start, end in zip(ledger.days, navs, navs[1:])))
    report = _research_metrics(ledger)
    assert report["metrics"]["sharpe"] is report["metrics"]["sortino"] is None
    assert report["metric_status"]["sharpe"] == report["metric_status"]["sortino"] == status
    assert report["metrics"]["annualized_volatility"] == (None if len(navs) == 2 else 0.0)
    json.dumps(report, allow_nan=False)


def test_opt_in_negative_ratios_and_zero_downside_are_distinct():
    losing = _research_metrics(_metric_ledger((100.0, 90.0, 72.0)))
    assert losing["metrics"]["sharpe"] == pytest.approx(-0.15 / math.sqrt(0.005) * math.sqrt(252))
    assert losing["metrics"]["sortino"] == pytest.approx(-0.15 / math.sqrt(0.025) * math.sqrt(252))
    gaining = _research_metrics(_metric_ledger((100.0, 110.0, 132.0)))
    assert gaining["metrics"]["sharpe"] > 0
    assert gaining["metrics"]["sortino"] is None
    assert gaining["metric_status"]["sortino"] == "ZERO_DOWNSIDE_DEVIATION"


def test_opt_in_computed_zero_ratios_and_negative_rates_are_preserved():
    balanced = _research_metrics(_metric_ledger((100.0, 50.0, 75.0)))
    assert balanced["metrics"]["sharpe"] == balanced["metrics"]["sortino"] == 0.0
    assert balanced["metric_status"]["sharpe"] == balanced["metric_status"]["sortino"] == "COMPUTED"
    negative = _research_metrics(_metric_ledger((100.0, 90.0, 99.0)),
                                annual_risk_free_rate=-0.252,
                                annual_minimum_acceptable_return=-0.504)
    assert negative["metrics"]["sharpe"] > 0 and negative["metrics"]["sortino"] > 0
    with pytest.raises(TypeError):
        _module().compute_synthetic_research_metrics(_metric_ledger((100.0, 90.0, 99.0)))


def test_opt_in_window_uses_preceding_nav_and_ignores_later_returns():
    ledger = _metric_ledger((100.0, 110.0, 99.0, 99.0, 108.9))
    kwargs = {"start_session": date(2024, 1, 3), "end_session": date(2024, 1, 4)}
    report = _research_metrics(ledger, **kwargs)
    changed = _metric_ledger((100.0, 110.0, 99.0, 99.0, 50.0))
    assert report == _research_metrics(changed, **kwargs)
    assert report["observation_count"] == 2
    assert report["initial_session"] == "2024-01-02"
    assert report["metrics"]["cumulative_return"] == pytest.approx(-0.1)
    assert report["metrics"]["max_drawdown"] == pytest.approx(-0.1)
    for invalid in ({"start_session": date(2024, 1, 1)},
                    {"end_session": date(2024, 1, 6)},
                    {"start_session": date(2024, 1, 4), "end_session": date(2024, 1, 3)}):
        with pytest.raises(ValueError, match="RESEARCH_WINDOW_INVALID"):
            _research_metrics(ledger, **invalid)


def test_opt_in_metrics_include_cash_and_do_not_rededuct_costs_or_mutate_store(source, tmp_path):
    store = PerformanceStore(local_root=tmp_path / "journal", cloud_bucket="")
    report = _run(source, store)
    record = next(r for r in _records(report, store) if r.actual_params["scenario_id"] == "C2_5")
    ledger = store.load_research_ledger("us_equity", PROFILE, record.trial_id, record.run_id, 1)
    before = {p: p.read_bytes() for p in store.local_root.rglob("*.json")}
    metrics = _research_metrics(ledger)["metrics"]
    returns = [day.daily_return for day in ledger.days]
    mean = math.fsum(returns) / len(returns)
    sigma = math.sqrt(math.fsum((r - mean) ** 2 for r in returns) / (len(returns) - 1))
    assert ledger.total_fees > 0 and any(day.trade_net_cashflow != 0 for day in ledger.days)
    assert metrics["cumulative_return"] == pytest.approx(ledger.total_return)
    assert metrics["sharpe"] == pytest.approx(mean / sigma * math.sqrt(252))
    assert before == {p: p.read_bytes() for p in store.local_root.rglob("*.json")}
    assert report["evaluation_contract"]["uncomputed_metrics"] == ["sharpe", "sortino", "dsr", "pbo"]


@pytest.mark.parametrize("field,value", [
    ("annual_risk_free_rate", None), ("annual_risk_free_rate", True),
    ("annual_risk_free_rate", float("nan")),
    ("annual_minimum_acceptable_return", float("inf")),
])
def test_opt_in_requires_explicit_finite_rates(field, value):
    with pytest.raises(ValueError, match="RESEARCH_RATE_INVALID"):
        _research_metrics(_metric_ledger((100.0, 90.0, 99.0)), **{field: value})


def test_opt_in_rejects_real_or_different_frequency_ledgers():
    ledger = _metric_ledger((100.0, 90.0, 99.0))
    for changed, reason in ((replace(ledger, synthetic=False), "REAL_RESEARCH_DATA_UNQUALIFIED"),
                            (replace(ledger, periods_per_year=365.25), "RESEARCH_BASIS_INVALID"),
                            (replace(ledger, calendar_id="CRYPTO"), "RESEARCH_BASIS_INVALID")):
        with pytest.raises(ValueError, match=reason):
            _research_metrics(changed)


def test_opt_in_rejects_external_flow_scope_and_ledger_missing_values():
    ledger = _metric_ledger((100.0, 90.0, 99.0))
    first, second = ledger.days
    flowed = replace(ledger, days=(replace(first, cash=50.0, nav=100.0,
                         external_cashflow=10.0, daily_return=100.0 / 110.0 - 1.0),
                         replace(second, cash=50.0, nav=109.0, daily_return=109.0 / 100.0 - 1.0)))
    with pytest.raises(ValueError, match="RESEARCH_EXTERNAL_FLOWS_UNSUPPORTED"):
        _research_metrics(flowed)
    for invalid in (None, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            replace(first, daily_return=invalid)
    with pytest.raises(ValueError):
        replace(ledger, days=(second, first))  # A late/out-of-order row is not silently sorted.


def test_declared_session_contract_keeps_calendar_and_shares_only_arithmetic(monkeypatch):
    module = _module()
    xnys = _metric_ledger((100.0, 90.0, 90.0, 99.0))
    declared = replace(xnys, calendar_id="synthetic_declared_sessions")
    calendars = []
    original = module.qpk_metrics.compute_window_metrics
    def observed(*args, **kwargs):
        calendars.append(kwargs["calendar_id"])
        return original(*args, **kwargs)
    monkeypatch.setattr(module.qpk_metrics, "compute_window_metrics", observed)
    rates = {"annual_risk_free_rate": 0.252, "annual_minimum_acceptable_return": 0.504}
    old = module.compute_synthetic_research_metrics(xnys, **rates)
    new = module.compute_declared_session_research_metrics(declared, **rates)
    assert calendars == ["XNYS", "synthetic_declared_sessions"]
    assert declared.calendar_id == "synthetic_declared_sessions"
    assert new["evaluation_contract"]["version"] == "synthetic_declared_session_account_metrics_v1"
    assert new["evaluation_contract"]["annualization_basis"] == "DECLARED_SYNTHETIC_252_STEPS_PER_YEAR"
    assert new["evaluation_contract"]["real_calendar_verified"] is False
    assert new["metrics"] == old["metrics"]
    normalized = json.loads(json.dumps(new))
    contract = normalized["evaluation_contract"]
    contract.pop("annualization_basis")
    contract.pop("session_scope")
    contract["calendar_id"] = "XNYS"
    contract["version"] = "synthetic_account_metrics_v1"
    assert normalized == old
    with pytest.raises(ValueError, match="RESEARCH_BASIS_INVALID"):
        module.compute_synthetic_research_metrics(declared, **rates)
    for wrong, reason in ((xnys, "RESEARCH_BASIS_INVALID"),
                          (replace(declared, synthetic=False), "REAL_RESEARCH_DATA_UNQUALIFIED"),
                          (replace(declared, periods_per_year=365.25), "RESEARCH_BASIS_INVALID")):
        with pytest.raises(ValueError, match=reason):
            module.compute_declared_session_research_metrics(wrong, **rates)


@pytest.mark.parametrize("rate", [None, float("nan"), float("inf")])
def test_declared_session_contract_refuses_nonfinite_or_missing_rates(rate):
    declared = replace(_metric_ledger((100.0, 90.0, 99.0)), calendar_id="synthetic_declared_sessions")
    with pytest.raises(ValueError, match="RESEARCH_RATE_INVALID"):
        _module().compute_declared_session_research_metrics(
            declared, annual_risk_free_rate=rate, annual_minimum_acceptable_return=0.0)
