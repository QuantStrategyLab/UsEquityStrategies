"""Opt-in next-close/net-ledger compounding acceptance with a capable QPK overlay."""
import importlib.util
import json
import math
import os
from dataclasses import replace
from datetime import date

import pytest

from us_equity_strategies.research.security_netting_runner import NettingSession, run_offline_security_netting

try:
    AVAILABLE = importlib.util.find_spec("quant_platform_kit.research_stats.security_netting") is not None
except ModuleNotFoundError:
    AVAILABLE = False
pytestmark = pytest.mark.skipif(
    not AVAILABLE and os.environ.get("UES_REQUIRE_SECURITY_NETTING") != "1",
    reason="production QPK pin lacks research-only security netting",
)


def _report_rows():
    return [
        NettingSession(date(2026, 2, 2), date(2026, 1, 30), .5, {"A": .8}, {"A": 0.}, 0.),
        NettingSession(date(2026, 2, 3), date(2026, 2, 2), .5, {"A": .8}, {"A": .1}, 0.),
    ]


def _report_run(sessions=None, **kwargs):
    from quant_platform_kit.research_stats.security_netting import SecurityNettingSpec

    options = {"initial_nav": 100., "initial_f": 0., "initial_inner_weights": {},
               "spec": SecurityNettingSpec(bps_per_side={"A": 100.}),
               "fee_cash_role": "inner_residual_cash", "execution_timing": "next_close",
               "report_periods_per_year": 2}
    options.update(kwargs)
    if options["execution_timing"] is None and options["report_periods_per_year"] is None:
        options.pop("execution_timing")
        options.pop("report_periods_per_year")
    return run_offline_security_netting(_report_rows() if sessions is None else sessions, **options)


def _report_metrics(result):
    return {m["name"]: m for m in result["unified_net_report"]["metrics"]}


def test_actual_runner_report_hand_math_preserves_the_original_net_book():
    legacy = _report_run(execution_timing=None, report_periods_per_year=None)
    result = _report_run()
    report = result.pop("unified_net_report")
    basis = result.pop("unified_net_report_basis")
    assert result.pop("timing") == {"execution": "next_close"}
    assert result == legacy
    first, final = result["ledger"]
    assert first["net_trades"] == {"A": 40.}
    assert first["fees"] == pytest.approx(.4)
    assert final["net_trades"]["A"] == pytest.approx(-2.56)
    assert final["fees"] == pytest.approx(.0256)
    assert final["nav_end"] == pytest.approx(103.5744)
    measured = {m["name"]: m for m in report["metrics"]}
    assert measured["net_cagr"]["value"] == pytest.approx(.035744)
    assert measured["max_drawdown"]["value"] == pytest.approx(-.004)
    assert measured["net_calmar_ratio"]["value"] == pytest.approx(.035744 / .004)
    assert measured["net_log_growth_per_period"]["value"] == pytest.approx(math.log(1.035744) / 2)
    assert measured["deflated_sharpe_ratio"]["reason_code"] == "missing_trial_evidence"
    assert report["observations"] == 2
    assert report["no_order"] is True and report["live_ready"] is False
    assert report["status"] == "DEVELOPMENT_ONLY"
    assert basis["sessions"] == ["2026-02-02", "2026-02-03"]
    assert basis["calendar_and_signal_availability_verified"] is False
    assert basis["costs_deducted_again"] is False
    assert basis["gross_counterfactual"] == "NOT_SUPPLIED"
    json.dumps(report, allow_nan=False)


def test_true_zero_session_is_retained_without_initial_nav_placeholder():
    sessions = _report_rows()
    sessions.insert(0, NettingSession(date(2026, 1, 30), date(2026, 1, 29), 0., {}, {}, 0.))
    result = _report_run(sessions, report_periods_per_year=3)
    assert result["net_returns"][0] == 0.
    assert result["unified_net_report"]["observations"] == len(result["ledger"]) == 3
    cagr = _report_metrics(result)["net_cagr"]
    assert cagr["value"] == pytest.approx(result["final_nav"] / result["initial_nav"] - 1)
    assert cagr["details"]["initial_nav_is_observation"] is False
    assert cagr["details"]["definition_version"] == "qpk.net_compounding.v1"


def test_zero_drawdown_calmar_stays_uncomputable():
    result = _report_run([replace(row, f_target=0., inner_target={}) for row in _report_rows()])
    calmar = _report_metrics(result)["net_calmar_ratio"]
    assert calmar["status"] == "UNCOMPUTABLE" and calmar["value"] is None
    assert calmar["reason_code"] == "zero_drawdown"


def test_negative_growth_and_calmar_keep_their_sign():
    sessions = _report_rows()
    sessions[-1] = replace(sessions[-1], security_returns={"A": -.1})
    measured = _report_metrics(_report_run(sessions))
    assert measured["net_cagr"]["value"] < 0
    assert measured["net_calmar_ratio"]["value"] < 0


@pytest.mark.parametrize("timing", ["next_open", "same_close_research_only", "same_close"])
def test_report_cannot_relabel_another_execution_timing(timing):
    with pytest.raises(ValueError, match="EXPLICIT_NEXT_CLOSE_REPORT_REQUIRED"):
        _report_run(execution_timing=timing)


@pytest.mark.parametrize("options", [
    {"execution_timing": None}, {"report_periods_per_year": None},
])
def test_report_requires_both_explicit_timing_and_annualization(options):
    with pytest.raises(ValueError, match="EXPLICIT_NEXT_CLOSE_REPORT_REQUIRED"):
        _report_run(**options)


@pytest.mark.parametrize("year_basis", [True, 0, 252.])
def test_invalid_annualization_is_rejected_by_the_actual_report(year_basis):
    with pytest.raises(ValueError, match="periods_per_year"):
        _report_run(report_periods_per_year=year_basis)


def test_report_rejects_stale_decision_instead_of_claiming_next_close():
    sessions = _report_rows()
    sessions[-1] = replace(sessions[-1], decision_session=date(2026, 1, 30))
    # The original primitive accepts any prior decision; only this explicit
    # next-close report contract requires the immediately preceding input session.
    _report_run(sessions, execution_timing=None, report_periods_per_year=None)
    with pytest.raises(ValueError, match="NEXT_CLOSE_DECISION_SESSION_MISMATCH"):
        _report_run(sessions)


@pytest.mark.parametrize("kind,code", [
    ("fee_cash", "FEE_CASH_INSUFFICIENT"), ("missing_return", "RETURN_MISSING:A"),
])
def test_report_path_preserves_existing_cash_and_return_refusals(kind, code):
    if kind == "fee_cash":
        sessions = [replace(_report_rows()[0], f_target=1., inner_target={"A": 1.})]
        options = {}
    else:
        sessions = [replace(_report_rows()[0], security_returns={})]
        options = {"initial_f": .5, "initial_inner_weights": {"A": .8}}
    with pytest.raises(ValueError, match=code):
        _report_run(sessions, **options)


def test_report_does_not_relax_the_existing_future_decision_guard():
    sessions = [replace(_report_rows()[0], decision_session=_report_rows()[0].session)]
    with pytest.raises(ValueError, match="DECISION_NOT_PRIOR"):
        _report_run(sessions)


def test_missing_compounding_capability_never_falls_back(monkeypatch):
    from quant_platform_kit.research_stats import unified_report

    def old_builder(net_returns):
        pytest.fail("old report capability must not be called")

    monkeypatch.setattr(unified_report, "build_unified_net_report", old_builder)
    with pytest.raises(ValueError, match="QPK_NET_COMPOUNDING_REQUIRED"):
        _report_run()
    assert "unified_net_report" not in _report_run(execution_timing=None, report_periods_per_year=None)


def test_next_close_matches_existing_etf_timing_with_zero_costs():
    import pandas as pd
    from quant_platform_kit.research_stats.security_netting import SecurityNettingSpec
    from us_equity_strategies.backtest.etf_rotation_simulator import (
        UsRotationBacktestConfig,
        run_etf_rotation_backtest,
    )

    history = pd.DataFrame({"date": ["2026-01-30", "2026-02-02", "2026-02-03"],
                            "symbol": "A", "close": [100., 132., 150.]})
    etf = run_etf_rotation_backtest(
        history, lambda h: ({"A": .4}, {}),
        config=UsRotationBacktestConfig(min_history_days=1, cost_bps=0., execution_timing="next_close"))
    sessions = _report_rows()
    sessions[-1] = replace(sessions[-1], security_returns={"A": 150. / 132. - 1})
    result = _report_run(sessions, spec=SecurityNettingSpec(bps_per_side={"A": 0.}))
    assert result["timing"]["execution"] == etf.timing_execution == "next_close"
    assert result["net_returns"] == pytest.approx(etf.daily_returns.iloc[1:].tolist())
    assert result["net_returns"][0] == 0.  # The signal-close -> fill-close rally is not earned.
    assert _report_metrics(result)["net_cagr"]["value"] == pytest.approx(
        math.prod(1 + r for r in etf.daily_returns.iloc[1:]) - 1)
