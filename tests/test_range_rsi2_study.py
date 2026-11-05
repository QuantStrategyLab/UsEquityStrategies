"""Frozen RSI2 mechanism and prefix-causality regressions."""
import unittest
import numpy as np
import pandas as pd
from us_equity_strategies.research.range_rsi2_study import (OverlayLifecycle, make_pair, synthetic_pair, simulate, trade_to_target)

STRATEGIES = ("trend", "mr_5", "mr_10", "mr_15", "trend_mr_5", "trend_mr_10", "trend_mr_15", "trend_range_hold")

class ContractTests(unittest.TestCase):
    def test_delayed_continuation_cannot_reopen_after_exit(self):
        n=218
        prices=np.full((n,2),100.0)
        pair=make_pair("SYNTHETIC","SYNTHETIC_UNDERLYING",pd.bdate_range("2020-01-01",periods=n),prices,prices,[.45,.45])
        pair.indicators["range"][:]=True
        pair.indicators["trend"][:]=False
        pair.indicators["rsi2"][:]=50
        pair.indicators["rsi2"][200:202]=5
        pair.indicators["rsi2"][202]=75
        pair.indicators["rsi2"][203]=60
        path=simulate(pair,"mr_10",0,0,2,start=pair.dates[202],end=pair.dates[-1])
        self.assertEqual(path.overlay_entry.sum(),1)
        self.assertTrue((path.iloc[2:].overlay_active==0).all())
        self.assertGreater(path.stale_overlay_intent_ignored.sum(),0)

    def test_noop_and_old_episode_exit_cannot_close_new_position(self):
        state=OverlayLifecycle()
        noop=state.make_intent(False)
        entry=state.make_intent(True)
        state.consume(True,*entry)
        self.assertTrue(state.active)
        state.consume(False,*noop)
        self.assertTrue(state.active)
        old_exit=("exit",state.active_generation)
        state.consume(False,*old_exit)
        self.assertFalse(state.active)
        new_entry=state.make_intent(True)
        state.consume(True,*new_entry)
        state.consume(False,*old_exit)
        self.assertTrue(state.active)
        self.assertEqual(state.active_generation,new_entry[1])

    def test_pending_entry_is_not_flattened_by_rsi_above_entry_below_exit(self):
        state=OverlayLifecycle()
        entry=state.make_intent(True)
        self.assertTrue(state.effective_held)
        continuation=state.make_intent(True)
        self.assertEqual(continuation,("continue",entry[1]))
        state.consume(True,*entry)
        state.consume(True,*continuation)
        self.assertTrue(state.active)

    def test_cash_self_financing_and_cost_once(self):
        q,c,fee,slip,_,_,error=trade_to_target(np.zeros(2),100000.,np.array([100.,100.]),np.array([1.,0.]),2,5)
        expected=100000/(100*1.0005*1.0002)
        self.assertAlmostEqual(q[0],expected,places=10)
        self.assertAlmostEqual(c,0,places=8)
        self.assertAlmostEqual(fee,expected*100*1.0005*.0002,places=8)
        self.assertAlmostEqual(100000-q[0]*100,fee+slip,places=8)
        self.assertLess(abs(error),1e-7)
        q2,c2,fee2,slip2,_,_,error2=trade_to_target(q,c,np.array([110.,100.]),np.zeros(2),2,5)
        self.assertEqual(q2[0],0)
        self.assertAlmostEqual(c2,q[0]*110*.9995*.9998,places=7)
        self.assertLess(abs(error2),1e-7)

    def test_synthetic_mutation_truncation_and_delay(self):
        pair=synthetic_pair()
        start=pair.dates[300]
        cut=700
        changed=make_pair(pair.symbol,pair.signal_symbol,pair.dates,
            np.vstack([pair.opens[:cut+1],pair.opens[cut+1:]*3]),
            np.vstack([pair.closes[:cut+1],pair.closes[cut+1:]*3]),pair.base_weights)
        short=make_pair(pair.symbol,pair.signal_symbol,pair.dates[:cut+1],pair.opens[:cut+1],pair.closes[:cut+1],pair.base_weights)
        for strategy in STRATEGIES:
            for lag in [1,2]:
                a=simulate(pair,strategy,2,5,lag,start=start,end=pair.dates[-1])
                b=simulate(changed,strategy,2,5,lag,start=start,end=pair.dates[-1])
                c=simulate(short,strategy,2,5,lag,start=start,end=short.dates[-1])
                pd.testing.assert_frame_equal(a.iloc[:len(c)],b.iloc[:len(c)])
                pd.testing.assert_frame_equal(a.iloc[:len(c)],c)
                self.assertTrue((a.cash>=-1e-8).all())
                self.assertTrue((a.target_levered+a.target_underlying<=1+1e-12).all())
                for session,source in zip(a.index,a.signal_date):
                    self.assertEqual(pair.dates.get_loc(pd.Timestamp(session))-pair.dates.get_loc(pd.Timestamp(source)),lag)
        mr=simulate(pair,"mr_10",2,5,1,start=start,end=pair.dates[-1])
        self.assertGreater(mr.overlay_entry.sum(),0)
        self.assertLessEqual(mr.overlay_held_sessions.max(),5)

    def test_single_asset_vectorized_zero_cost(self):
        pair=synthetic_pair()
        pair.base_weights=np.array([1.,0.])
        start=300
        frame=simulate(pair,"trend",0,0,1,start=pair.dates[start],end=pair.dates[-1])
        signal=pair.indicators["trend"].astype(float)
        daily=[]
        previous_exposure=0.0
        for i in range(start,len(pair.dates)):
            target=signal[i-1]
            overnight=1+previous_exposure*(pair.opens[i,0]/pair.closes[i-1,0]-1)
            intraday=1+target*(pair.closes[i,0]/pair.opens[i,0]-1)
            daily.append(overnight*intraday-1)
            previous_exposure=target
        np.testing.assert_allclose(frame["return"],daily,rtol=0,atol=3e-15)



def test_lifecycle_recomputes_and_rejects_self_consistent_path_mutation(tmp_path):
    from us_equity_strategies.research.range_rsi2_lifecycle import validate_frozen_path
    from us_equity_strategies.research.range_rsi2_study import metrics
    pair = synthetic_pair()
    frame = simulate(pair, "trend", 2, 5, 1)
    path = tmp_path / "path.csv"
    frame.to_csv(path, float_format="%.17g")
    expected = {"symbol": "SYNTHETIC", "strategy": "trend", "cost": "C2_5", "lag": 1, **metrics(frame)}
    cost = {"commission_bps": 2, "slippage_bps": 5}
    checked, _ = validate_frozen_path(path, pair, strategy="trend", cost=cost, lag=1, expected_metrics=expected)
    assert len(checked) == len(frame)
    altered = frame.copy()
    altered["cash"] += 100.0
    altered["nav"] += 100.0
    altered.to_csv(path, float_format="%.17g")
    import pytest
    with pytest.raises(ValueError, match="frozen daily path mismatch"):
        validate_frozen_path(path, pair, strategy="trend", cost=cost, lag=1, expected_metrics=expected)


def test_registry_grid_rejects_rewritten_or_duplicate_trial():
    import hashlib
    import json
    import pytest
    from us_equity_strategies.research.range_rsi2_lifecycle import validate_trial_registry
    from us_equity_strategies.research.range_rsi2_study import FROZEN_PREREGISTRATION_SHA256
    config = {"input_files": {"SOXL": {"sha256": "a"*64}, "TQQQ": {"sha256": "b"*64}},
        "costs": [{"id": "C2_5"}, {"id": "C5_10"}], "execution": {"lag_sessions": [1, 2]},
        "strategies": list(STRATEGIES)}
    source = "c"*64
    trials = []
    for symbol in config["input_files"]:
        for cost in config["costs"]:
            for lag in (1, 2):
                for strategy in STRATEGIES:
                    spec = {"symbol": symbol, "strategy": strategy, "cost": cost["id"], "lag": lag,
                        "input_sha256": config["input_files"][symbol]["sha256"], "source": "sha256-"+source,
                        "preregistration_sha256": FROZEN_PREREGISTRATION_SHA256}
                    trial_id = hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                    trials.append({**spec, "trial_id": trial_id})
    registry = {"schema_version": "qsl.range_rsi2_trial_registry.v1", "preregistration_sha256": FROZEN_PREREGISTRATION_SHA256,
                "classification": "retrospective_development_only", "promotion_eligible": False, "trials": trials}
    validate_trial_registry(registry, config, source)
    altered = [dict(item) for item in trials]
    altered[0]["lag"] = 2
    with pytest.raises(ValueError):
        validate_trial_registry({**registry, "trials": altered}, config, source)
    with pytest.raises(ValueError):
        validate_trial_registry({**registry, "trials": [*trials[:-1], trials[0]]}, config, source)


def test_stored_trial_cannot_relabel_frozen_cost_or_window():
    from types import SimpleNamespace
    from datetime import date
    import pytest
    from us_equity_strategies.research.range_rsi2_lifecycle import validate_stored_trial
    item = {"strategy": "trend", "source": "sha256-source", "input_sha256": "a"*64, "cost": "C2_5"}
    config = {"costs": [{"id": "C2_5", "commission_bps": 2, "slippage_bps": 5}],
              "evaluation_start": "2011-01-03", "evaluation_end": "2026-10-30"}
    values = dict(candidate_config_id="trend", param_set_id="trend", source_revision="sha256-source",
        input_id="a"*64, cost_source="C2_5", cost_inputs={"commission_bps": 2.0, "slippage_bps": 5.0,
        "market_impact_bps": 0.0}, synthetic=False, window_start=date(2011,1,3), window_end=date(2026,10,30),
        calendar_id="paired_adjusted_price_sessions", periods_per_year=252.0)
    validate_stored_trial(SimpleNamespace(**values), item, config)
    for changed in ({"cost_inputs": {"commission_bps": 0, "slippage_bps": 5, "market_impact_bps": 0}},
                    {"window_start": date(2012,1,3)}, {"calendar_id": "wrong"}, {"periods_per_year": 365.0}):
        with pytest.raises(ValueError):
            validate_stored_trial(SimpleNamespace(**{**values, **changed}), item, config)


def test_runner_records_failed_and_aborted_trials_on_injected_error(tmp_path, monkeypatch):
    import json
    from datetime import date
    import pytest
    from quant_platform_kit.strategy_lifecycle.contracts import ResearchTrialRecord, ResearchTrialStatus
    from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore
    from us_equity_strategies.research import range_rsi2_study
    items = [{"symbol": "SOXL", "strategy": strategy, "cost": "C2_5", "lag": 1, "trial_id": str(i)*64}
             for i, strategy in ((1, "trend"), (2, "mr_10"))]
    store = PerformanceStore(local_root=tmp_path / "performance_store")
    for item in items:
        trial = ResearchTrialRecord(trial_id=item["trial_id"], domain="us_equity", strategy_profile="range_rsi2_soxl",
            status=ResearchTrialStatus.STARTED, candidate_config_id=item["strategy"], actual_params=None,
            param_set_id=item["strategy"], source_revision="sha256-test", input_id="input", window_start=date(2011,1,3),
            window_end=date(2026,10,30), calendar_id="paired_adjusted_price_sessions", periods_per_year=252,
            cost_source="C2_5", cost_inputs={"commission_bps": 2.0, "slippage_bps": 5.0, "market_impact_bps": 0.0},
            reason_code="", synthetic=False, run_id=None, param_version=None)
        store.save_research_trial(trial)
    (tmp_path / "trial_registry.json").write_text(json.dumps({"trials": items}))
    def fail(config, inputs, output, tracker):
        tracker["registry_owned"] = True
        tracker["active"] = ("SOXL", "mr_10", "C2_5", 1)
        raise RuntimeError("injected failure")
    monkeypatch.setattr(range_rsi2_study, "_run_study_impl", fail)
    with pytest.raises(RuntimeError, match="injected failure"):
        range_rsi2_study.run_study({}, {}, tmp_path)
    assert store.load_research_trial("us_equity", "range_rsi2_soxl", items[0]["trial_id"]).status is ResearchTrialStatus.ABORTED
    assert store.load_research_trial("us_equity", "range_rsi2_soxl", items[1]["trial_id"]).status is ResearchTrialStatus.FAILED
    assert not (tmp_path / "results.json").exists()


def test_rerun_refusal_does_not_mutate_preexisting_trials(tmp_path, monkeypatch):
    import json
    from datetime import date
    import pytest
    from quant_platform_kit.strategy_lifecycle.contracts import ResearchTrialRecord, ResearchTrialStatus
    from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore
    from us_equity_strategies.research import range_rsi2_study
    item = {"symbol": "SOXL", "strategy": "trend", "cost": "C2_5", "lag": 1, "trial_id": "1"*64}
    registry = tmp_path / "trial_registry.json"
    registry.write_text(json.dumps({"trials": [item]}))
    before = registry.read_bytes()
    store = PerformanceStore(local_root=tmp_path / "performance_store")
    trial = ResearchTrialRecord(trial_id=item["trial_id"], domain="us_equity", strategy_profile="range_rsi2_soxl",
        status=ResearchTrialStatus.STARTED, candidate_config_id="trend", actual_params=None, param_set_id="trend",
        source_revision="sha256-test", input_id="input", window_start=date(2011,1,3), window_end=date(2026,10,30),
        calendar_id="paired_adjusted_price_sessions", periods_per_year=252, cost_source="C2_5",
        cost_inputs={"commission_bps": 2.0, "slippage_bps": 5.0, "market_impact_bps": 0.0},
        reason_code="", synthetic=False, run_id=None, param_version=None)
    store.save_research_trial(trial)
    def refuse(config, inputs, output, tracker):
        raise FileExistsError("registry exists")
    monkeypatch.setattr(range_rsi2_study, "_run_study_impl", refuse)
    with pytest.raises(FileExistsError):
        range_rsi2_study.run_study({}, {}, tmp_path)
    assert registry.read_bytes() == before
    assert store.load_research_trial("us_equity", "range_rsi2_soxl", item["trial_id"]) == trial
    assert not (tmp_path / "trial_failure_report.json").exists()


def test_terminal_failure_reports_uncertainty_without_rewriting_success():
    from dataclasses import dataclass
    from quant_platform_kit.strategy_lifecycle.contracts import ResearchTrialStatus
    from us_equity_strategies.research.range_rsi2_lifecycle import record_linkage_failure
    @dataclass(frozen=True)
    class Trial:
        status: object = ResearchTrialStatus.STARTED
        reason_code: str = ""
        domain: str = "us_equity"
        strategy_profile: str = "range_rsi2_soxl"
        trial_id: str = "trial"
    class Store:
        def __init__(self, fail_save=False, fail_read=False):
            self.saved = None
            self.fail_save, self.fail_read = fail_save, fail_read
        def save_research_trial(self, value):
            if self.fail_save:
                raise OSError("write unavailable")
            self.saved = value
        def load_research_trial(self, *args):
            if self.fail_read:
                raise OSError("read unavailable")
            return self.saved
    store = Store()
    assert record_linkage_failure(store, Trial(), terminal_write_attempted=True) == "PERSISTENCE_UNCERTAIN"
    assert store.saved is None
    assert record_linkage_failure(Store(fail_save=True), Trial(), terminal_write_attempted=False) == "PERSISTENCE_UNCERTAIN"
    assert record_linkage_failure(Store(fail_read=True), Trial(), terminal_write_attempted=False) == "PERSISTENCE_UNCERTAIN"
    assert record_linkage_failure(Store(), Trial(), terminal_write_attempted=False) == "FAILED"
