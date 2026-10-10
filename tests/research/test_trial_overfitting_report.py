"""RS-02: DSR/PBO trial-set report. Artificial bars and temporary stores only."""
from datetime import date, timedelta
import hashlib
import importlib
import math
import os
import random
import socket

import pytest

from quant_platform_kit.strategy_lifecycle.contracts import ResearchTrialStatus
from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore
from us_equity_strategies.research import tqqq_core_optimization as core
from us_equity_strategies.research import trial_overfitting_report as rs02
from us_equity_strategies.research.tqqq_offline_input_contract import InputRow, OfflineInput

PROFILE = "tqqq_core_optimization_sma_journal_v1"


def _qpk_stats():
    try:
        module = importlib.import_module("quant_platform_kit.research_stats")
    except ImportError:
        return None
    if not hasattr(module, "probability_of_backtest_overfitting"):
        return None
    return module


REQUIRE_QPK_STATS = os.environ.get("UES_REQUIRE_QPK_RESEARCH_STATS") == "1"
requires_qpk_stats = pytest.mark.skipif(
    _qpk_stats() is None and not REQUIRE_QPK_STATS,
    reason="installed QPK lacks research_stats PBO (runs in research-stats-integration CI job)")


def test_integration_job_really_has_qpk_research_stats():
    if REQUIRE_QPK_STATS:
        assert _qpk_stats() is not None, "integration job must install QPK with research_stats PBO"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("SYNTHETIC_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(PerformanceStore, "_object_store", denied)


def _dated(n_trials=4, rows=96, seed=3, start=date(2024, 1, 2)):
    rng = random.Random(seed)
    days = [start + timedelta(days=i) for i in range(rows)]
    return {f"t{k}": [(d, 0.0002 * k + rng.gauss(0.0, 0.01)) for d in days] for k in range(n_trials)}


class _Stat:
    def __init__(self, name, value=0.25):
        self.name, self.status, self.value, self.reason_code = name, "COMPUTED", value, None
        self.details = {"fake": True}


class _FakeStats:
    def __init__(self):
        self.calls = {}

    def deflated_sharpe_ratio(self, returns, trial_sharpes, *, n_effective_trials):
        self.calls["dsr"] = (list(returns), list(trial_sharpes), n_effective_trials)
        return _Stat("dsr", 0.4)

    def probability_of_backtest_overfitting(self, returns_by_trial, *, n_splits, metric):
        self.calls["pbo"] = ({k: list(v) for k, v in returns_by_trial.items()}, n_splits, metric)
        return _Stat("pbo", 0.6)


def _use(monkeypatch, provider):
    monkeypatch.setattr(rs02, "_qpk_function", lambda name: None if provider is None else getattr(provider, name, None))


def test_missing_qpk_research_stats_is_uncomputable_not_zero(monkeypatch):
    _use(monkeypatch, None)
    report = rs02.compute_trial_set_overfitting(
        _dated(), selected_trial_id="t1", recorded_trial_count=6,
        annual_risk_free_rate=0.0, synthetic=True)
    assert report["metrics"] == {"dsr": None, "pbo": None}
    assert report["metric_status"] == {"dsr": "UNCOMPUTABLE", "pbo": "UNCOMPUTABLE"}
    assert report["metric_reason"] == {"dsr": rs02.UNAVAILABLE, "pbo": rs02.UNAVAILABLE}
    assert report["not_succeeded_trial_count"] == 2
    assert report["live_ready"] is False and report["no_order"] is True
    assert report["promotion_eligible"] is False and report["size_zero_required"] is True


def test_plumbing_passes_excess_returns_and_succeeded_trial_count(monkeypatch):
    fake = _FakeStats()
    _use(monkeypatch, fake)
    data = _dated()
    report = rs02.compute_trial_set_overfitting(
        data, selected_trial_id="t2", recorded_trial_count=5,
        annual_risk_free_rate=0.0252, pbo_splits=8, synthetic=True)
    rf = 0.0252 / 252
    returns, sharpes, n_eff = fake.calls["dsr"]
    assert returns == pytest.approx([r - rf for _, r in data["t2"]])
    assert len(sharpes) == 4 and n_eff == 4
    pbo_input, splits, metric = fake.calls["pbo"]
    assert set(pbo_input) == set(data) and splits == 8 and metric == "sharpe"
    assert report["metrics"] == {"dsr": 0.4, "pbo": 0.6}
    assert report["evaluation_contract"]["qpk_research_stats_available"] == {"dsr": True, "pbo": True}


def test_misaligned_sessions_are_uncomputable(monkeypatch):
    _use(monkeypatch, _FakeStats())
    data = _dated()
    data["t3"] = data["t3"][1:]
    report = rs02.compute_trial_set_overfitting(
        data, selected_trial_id="t0", recorded_trial_count=4, annual_risk_free_rate=0.0, synthetic=True)
    assert report["metric_reason"] == {"dsr": "TRIAL_SESSIONS_NOT_ALIGNED", "pbo": "TRIAL_SESSIONS_NOT_ALIGNED"}
    assert report["metrics"] == {"dsr": None, "pbo": None}


def test_constant_trial_sharpe_blocks_dsr(monkeypatch):
    _use(monkeypatch, _FakeStats())
    data = _dated()
    data["t0"] = [(d, 0.001) for d, _ in data["t0"]]
    report = rs02.compute_trial_set_overfitting(
        data, selected_trial_id="t1", recorded_trial_count=4, annual_risk_free_rate=0.0, synthetic=True)
    assert report["metric_status"]["dsr"] == "UNCOMPUTABLE"
    assert report["metric_reason"]["dsr"] == "TRIAL_SHARPE_UNDEFINED"


@pytest.mark.parametrize(("kwargs", "error"), [
    ({"synthetic": False}, "REAL_RESEARCH_DATA_UNQUALIFIED"),
    ({"recorded_trial_count": 3}, "RECORDED_TRIAL_COUNT_INVALID"),
    ({"selected_trial_id": "missing"}, "SELECTED_TRIAL_NOT_SUCCEEDED"),
    ({"annual_risk_free_rate": math.nan}, "RESEARCH_RATE_INVALID"),
])
def test_input_contract_errors(kwargs, error):
    params = dict(selected_trial_id="t0", recorded_trial_count=4, annual_risk_free_rate=0.0, synthetic=True)
    params.update(kwargs)
    with pytest.raises(ValueError, match=error):
        rs02.compute_trial_set_overfitting(_dated(), **params)


@requires_qpk_stats
def test_real_qpk_research_stats_values_match_direct_calls():
    stats = _qpk_stats()
    data = _dated(n_trials=5, rows=128)
    report = rs02.compute_trial_set_overfitting(
        data, selected_trial_id="t4", recorded_trial_count=5,
        annual_risk_free_rate=0.0, pbo_splits=8, synthetic=True)
    excess = {k: [r for _, r in v] for k, v in data.items()}
    sharpes = [statistics_sharpe(v) for v in excess.values()]
    dsr = stats.deflated_sharpe_ratio(excess["t4"], sharpes, n_effective_trials=5)
    pbo = stats.probability_of_backtest_overfitting(excess, n_splits=8, metric="sharpe")
    assert report["metric_status"] == {"dsr": dsr.status, "pbo": pbo.status}
    assert report["metrics"]["dsr"] == pytest.approx(dsr.value)
    assert report["metrics"]["pbo"] == pytest.approx(pbo.value)


def statistics_sharpe(xs):
    import statistics
    return statistics.mean(xs) / statistics.stdev(xs)


@requires_qpk_stats
def test_real_qpk_single_trial_dsr_is_uncomputable():
    data = _dated(n_trials=1)
    report = rs02.compute_trial_set_overfitting(
        data, selected_trial_id="t0", recorded_trial_count=1, annual_risk_free_rate=0.0, synthetic=True)
    assert report["metric_status"] == {"dsr": "UNCOMPUTABLE", "pbo": "UNCOMPUTABLE"}
    assert report["metric_reason"] == {"dsr": "insufficient_trials", "pbo": "insufficient_trials"}
    assert report["metrics"] == {"dsr": None, "pbo": None}


# --- store adapter over a real synthetic journal run -------------------------

@pytest.fixture
def source(monkeypatch):
    rows = []
    for i in range(753):
        day = (date(2023, 1, 2) + timedelta(days=i)).isoformat()
        q, opening = 100.0 + i % 31, 50.0 + i % 7
        closing = opening * (1.0 + (i % 5 - 2) / 100.0)
        rows.extend((
            InputRow("QQQ", day, q, q, q, q, 1.0),
            InputRow("TQQQ", day, opening, max(opening, closing), min(opening, closing), closing, 1.0),
        ))
    lines = [",".join(core.INPUT_COLUMNS)]
    for row in rows:
        lines.append(",".join((row.symbol, row.as_of, *(
            format(v, ".17g") for v in (row.open, row.high, row.low, row.close, row.volume)))))
    raw = ("\n".join(lines) + "\n").encode()
    monkeypatch.setattr(core, "EXPECTED_ARTIFACT_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(core, "_terminal_loss_probability", lambda returns: 0.0)
    return OfflineInput(tuple(rows), raw, core.EXPECTED_INPUT_DIGEST, "c" * 40)


def _journal(source, tmp_path):
    from us_equity_strategies.research import tqqq_core_trial_journal as journal
    store = PerformanceStore(local_root=tmp_path / "journal", cloud_bucket="")
    report = journal.run_journaled_tqqq_core_optimization(
        source, store=store, trial_namespace="rs02-synthetic", synthetic=True)
    ids = list(report["journal"]["trial_ids"])
    records = [store.load_research_trial("us_equity", PROFILE, t) for t in ids]
    assert all(r.status is ResearchTrialStatus.SUCCEEDED for r in records)
    ledgers = [store.load_research_ledger("us_equity", PROFILE, r.trial_id, r.run_id, r.param_version)
               for r in records]
    common_start = max(ledger.days[0].session_date for ledger in ledgers)
    return store, ids, common_start


def test_adapter_reads_journal_without_writes_and_requires_explicit_window(source, tmp_path, monkeypatch):
    fake = _FakeStats()
    _use(monkeypatch, fake)
    store, ids, common_start = _journal(source, tmp_path)
    before = {p: p.read_bytes() for p in store.local_root.rglob("*") if p.is_file()}
    unaligned = rs02.report_journaled_tqqq_trial_set_overfitting(
        store=store, trial_ids=ids, selected_trial_id=ids[0], annual_risk_free_rate=0.0)
    assert unaligned["metric_reason"]["pbo"] == "TRIAL_SESSIONS_NOT_ALIGNED"
    aligned = rs02.report_journaled_tqqq_trial_set_overfitting(
        store=store, trial_ids=ids, selected_trial_id=ids[0], annual_risk_free_rate=0.0,
        start_session=common_start, pbo_splits=8)
    assert aligned["sessions_aligned"] is True
    assert aligned["recorded_trial_count"] == aligned["succeeded_trial_count"] == len(ids)
    assert set(aligned["trial_statuses"].values()) == {"succeeded"}
    assert aligned["metric_status"] == {"dsr": "COMPUTED", "pbo": "COMPUTED"}
    assert aligned["scope"] == "JOURNALED_SYNTHETIC_TRIAL_SET_NOT_SELECTION_OR_QUALIFICATION"
    assert before == {p: p.read_bytes() for p in store.local_root.rglob("*") if p.is_file()}


def test_adapter_guards(source, tmp_path, monkeypatch):
    _use(monkeypatch, None)
    store, ids, common_start = _journal(source, tmp_path)
    with pytest.raises(ValueError, match="SELECTED_TRIAL_NOT_IN_STUDY"):
        rs02.report_journaled_tqqq_trial_set_overfitting(
            store=store, trial_ids=ids[1:], selected_trial_id=ids[0], annual_risk_free_rate=0.0)
    with pytest.raises(ValueError, match="TRIAL_IDS_INVALID"):
        rs02.report_journaled_tqqq_trial_set_overfitting(
            store=store, trial_ids=[ids[0], ids[0]], selected_trial_id=ids[0], annual_risk_free_rate=0.0)
    with pytest.raises(ValueError, match="RESEARCH_TRIAL_READBACK_INVALID"):
        rs02.report_journaled_tqqq_trial_set_overfitting(
            store=store, trial_ids=[*ids, "missing-trial"], selected_trial_id=ids[0], annual_risk_free_rate=0.0)
    with pytest.raises(ValueError, match="RESEARCH_WINDOW_INVALID"):
        rs02.report_journaled_tqqq_trial_set_overfitting(
            store=store, trial_ids=ids, selected_trial_id=ids[0], annual_risk_free_rate=0.0,
            start_session=date(2000, 1, 3))
    remote = PerformanceStore(local_root=tmp_path / "journal", cloud_bucket="forbidden-bucket")
    with pytest.raises(ValueError, match="LOCAL_RESEARCH_STORE_REQUIRED"):
        rs02.report_journaled_tqqq_trial_set_overfitting(
            store=remote, trial_ids=ids, selected_trial_id=ids[0], annual_risk_free_rate=0.0)


@requires_qpk_stats
def test_adapter_with_real_qpk_research_stats(source, tmp_path):
    store, ids, common_start = _journal(source, tmp_path)
    report = rs02.report_journaled_tqqq_trial_set_overfitting(
        store=store, trial_ids=ids, selected_trial_id=ids[0], annual_risk_free_rate=0.0,
        start_session=common_start, pbo_splits=8)
    for metric in ("dsr", "pbo"):
        status = report["metric_status"][metric]
        assert status in {"COMPUTED", "UNCOMPUTABLE"}
        if status == "COMPUTED":
            assert 0.0 <= report["metrics"][metric] <= 1.0
        else:
            assert report["metrics"][metric] is None and report["metric_reason"][metric]
