from datetime import date, timedelta
import random

import pytest

from us_equity_strategies.research.soxl_v7_derisk_history import (
    calibrate, synthetic_return, splice_history,
)


def data():
    rng = random.Random(20261015)
    dates = [date(2018, 1, 1)+timedelta(days=i) for i in range(1900)]
    real = [rng.gauss(0., .03) for _ in dates]
    evidence = {k: "synthetic-evidence-reference" for k in ("license", "return_definition",
        "nav_and_market_basis", "historical_rates", "historical_fees", "calendar",
        "corporate_actions", "index_regimes", "pit_limits")}
    evidence.update(expected_validation_sessions=len(real)-504,
        validation_regime_windows=[("synthetic_high_vol", date(2020, 1, 1), date(2020, 12, 31)),
                                   ("synthetic_rate_regime", date(2022, 1, 1), date(2022, 12, 31))])
    return real, [r+.0001 for r in real], dates, evidence


def test_daily_reset_act365_act360_and_sensitivity_no_double_vol_drag():
    base = synthetic_return(.01, rate=.04, expense=.0095, spread=.005, drag=.00001, days=3)
    assert base == pytest.approx(.03-.0095*3/365-2*.045*3/360-.00001)
    assert synthetic_return(.01, rate=.04, expense=.0095, spread=.005, drag=.00001,
        days=3, spread_stress=.01) == pytest.approx(base-2*.01*3/360)
    assert synthetic_return(.01, rate=.04, expense=.0095, spread=.005, drag=.00001,
        days=3, drag_multiplier=2) == pytest.approx(base-.00001)
    assert synthetic_return(.01, rate=.04, expense=.0095, spread=.005, drag=-.00001,
        days=3, drag_multiplier=2) == synthetic_return(.01, rate=.04, expense=.0095,
        spread=.005, drag=-.00001, days=3)
    with pytest.raises(ValueError, match="TERMINATED_NO_REVIVAL"):
        synthetic_return(-.4, rate=.04, expense=.01, spread=.005, drag=0., days=1)


def test_independent_calibration_and_each_252_block_and_crisis_pass():
    r, m, d, e = data()
    report = calibrate(r, m, d, input_evidence=e)
    assert report["status"] == "PASS"
    assert report["frozen_daily_drag"] == pytest.approx(.0001)
    assert len(report["blocks_252"]) == (len(r)-504)//252
    assert set(report["crisis_validation"]) == {"2020", "2022"}
    assert report["strategy_pass"] is False


def test_validation_bias_or_bad_day_fails_without_refitting_or_dropping():
    r, m, d, e = data()
    bad = [v if i < 504 else v+.0002 for i, v in enumerate(m)]
    report = calibrate(r, bad, d, input_evidence=e)
    assert report["frozen_daily_drag"] == pytest.approx(.0001)
    assert report["status"] == "FAIL"
    m[-1] += .4
    assert calibrate(r, m, d, input_evidence=e)["status"] == "FAIL"


def test_short_missing_rates_missing_crisis_and_unknown_coverage_are_inconclusive():
    r, m, d, e = data()
    assert calibrate(r[:755], m[:755], d[:755], input_evidence=e)["status"] == "INCONCLUSIVE"
    assert calibrate(r, m, d, input_evidence={})["status"] == "INCONCLUSIVE"
    e["expected_validation_sessions"] = len(r)*2
    assert calibrate(r, m, d, input_evidence=e)["status"] == "INCONCLUSIVE"


def test_labels_and_gate_never_replace_real_with_model():
    start, anchor, end = date(2000, 1, 3), date(2010, 3, 11), date(2010, 3, 12)
    manifest = {k: "synthetic-identity" for k in
                ("underlying", "target_index_regime", "rate_source", "expense_source", "calendar_ref", "calibration_sha256")}
    manifest.update(leverage=3., expense_ratio=.0095, financing_spread=.005, drag=.0001)
    rows = splice_history({start: -.1, anchor: .03, end: .9}, {end: -.07}, anchor=anchor,
                          model_manifest=manifest, calibration_report={"status": "PASS"},
                          expected_sessions=[start, anchor, end])
    assert [r["source_type"] for r in rows] == ["synthetic_history", "synthetic_bridge", "real"]
    assert rows[-1]["return"] == -.07
    with pytest.raises(ValueError, match="GATE_NOT_PASSED"):
        splice_history({anchor: .01}, {end: -.01}, anchor=anchor,
                       model_manifest=manifest, calibration_report={"status": "FAIL"}, expected_sessions=[anchor, end])
    with pytest.raises(ValueError, match="REAL_HISTORY_GAP"):
        splice_history({anchor: .01, end+timedelta(days=1): .02}, {end: -.01}, anchor=anchor,
                       model_manifest=manifest, calibration_report={"status": "PASS"}, expected_sessions=[anchor, end])
    with pytest.raises(ValueError, match="CALENDAR_COVERAGE_INCOMPLETE"):
        splice_history({anchor: .01}, {end: -.01}, anchor=anchor, model_manifest=manifest,
                       calibration_report={"status": "PASS"}, expected_sessions=[anchor])
