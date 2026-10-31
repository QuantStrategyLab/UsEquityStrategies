from datetime import date
import math
import hashlib
import json

import pytest

from us_equity_strategies.research.prelisting_proxy_research import (
    SyntheticAssumptions, classify_session, floating_rate_three_times_path, proxy_signal_diagnostics,
    synthetic_three_times_path,
)
from us_equity_strategies.research.paired_bayesian_update import evaluate_pair
from us_equity_strategies.research.provider_history import read_provider_history


def test_prelisting_and_synthetic_path_are_explicitly_distinct():
    assert classify_session(date(2010, 1, 4), date(2010, 2, 11)) == "PRELISTING_PROXY_ONLY"
    assert classify_session(date(2010, 2, 11), date(2010, 2, 11)) == "POSTLISTING_DATA_REQUIRED"
    bars = [(date(2010, 1, 4), 100.0), (date(2010, 1, 5), 101.0)]
    result = synthetic_three_times_path(bars, SyntheticAssumptions(.01, .02, .03))
    assert result["executable"] is False and result["promotion_eligible"] is False
    assert result["rows"][1]["synthetic_return"] == pytest.approx(.03 - .06 / 252)
    signals = proxy_signal_diagnostics(bars, first_tradable_session=date(2010, 2, 11))
    assert signals["rows"][1]["range"] is None
    assert signals["rows"][1]["tradability"] == "PRELISTING_PROXY_ONLY"
    assert signals["executable"] is False
    with pytest.raises(ValueError):
        synthetic_three_times_path(list(reversed(bars)), SyntheticAssumptions(.01, .02, .03))
    with pytest.raises(ValueError):
        synthetic_three_times_path(bars, SyntheticAssumptions(float("nan"), .02, .03))
    with pytest.raises(ValueError, match="INSOLVENCY"):
        synthetic_three_times_path([(date(2010, 1, 4), 100.0), (date(2010, 1, 5), 60.0)],
                                   SyntheticAssumptions(.01, .02, .03))


def test_pair_requires_matched_sessions_and_updates_only_completed_months(tmp_path):
    baseline = tmp_path / "baseline.csv"
    candidate = tmp_path / "candidate.csv"
    sessions = [date(2024, month, 15) for month in range(1, 13)] + [
        date(2025, month, 15) for month in range(1, 13)] + [date(2026, 1, 15), date(2026, 2, 15)]
    baseline.write_text("date,return\n" + "".join(f"{d},0\n" for d in sessions))
    candidate.write_text("date,return\n" + "".join(f"{d},{.001 * ((i % 3) - 1)}\n" for i, d in enumerate(sessions)))
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    def evaluate():
        return evaluate_pair(baseline, candidate, through=date(2026, 2, 20),
                             expected_baseline_sha256=digest(baseline), expected_candidate_sha256=digest(candidate))
    result = evaluate()
    with pytest.raises(ValueError, match="DIGEST_MISMATCH"):
        evaluate_pair(baseline, candidate, through=date(2026, 2, 20),
                      expected_baseline_sha256="0" * 64, expected_candidate_sha256=digest(candidate))
    assert result["sessions"] == 2 and len(result["posterior"]) == 1
    assert result["availability_status"] == "ASSUMED_NOT_HISTORICALLY_VERIFIED"
    assert result["month_completeness"] == "UNVERIFIED_CALENDAR_COVERAGE"
    v = result["training_variance"]
    assert result["posterior"][0]["posterior_mean"] == pytest.approx(math.log1p(-.001) / 2)
    assert result["posterior"][0]["posterior_std"] == pytest.approx(math.sqrt(v / 2))
    for strength in (.25, 1., 4.):
        case = result["posterior"][0]["prior_sensitivity"][str(strength)]
        assert case["posterior_mean"] == pytest.approx(math.log1p(-.001) / (1 + strength))
        assert case["posterior_std"] == pytest.approx(math.sqrt(v / (1 + strength)))
    original = result["posterior"][0]
    candidate.write_text(candidate.read_text().replace("2026-02-15,0.0", "2026-02-15,0.2"))
    assert evaluate()["posterior"][0] == original
    baseline.write_text(baseline.read_text().replace("2025-07-15,0\n", ""))
    candidate.write_text("\n".join(line for line in candidate.read_text().splitlines()
                                   if not line.startswith("2025-07-15,")) + "\n")
    assert evaluate()["month_completeness"] == "UNVERIFIED_CALENDAR_COVERAGE"
    candidate.write_text(candidate.read_text().replace("2026-02-15", "2026-02-16"))
    with pytest.raises(ValueError, match="MATCHED_SESSION"):
        evaluate()


def test_bayesian_insufficient_or_degenerate_training_fails_closed(tmp_path):
    baseline = tmp_path / "baseline.csv"
    candidate = tmp_path / "candidate.csv"
    rows = "date,return\n" + "".join(f"2025-{month:02d}-15,0\n" for month in range(1, 13))
    rows += "2026-01-15,0\n2026-02-15,0\n"
    baseline.write_text(rows)
    candidate.write_text(rows)
    sha = hashlib.sha256(baseline.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="POSITIVE_TRAINING_VARIANCE"):
        evaluate_pair(baseline, candidate, through=date(2026, 2, 20),
                      expected_baseline_sha256=sha, expected_candidate_sha256=sha)


def test_calendar_matched_month_end_and_missing_pair_session(tmp_path):
    baseline = tmp_path / "baseline.csv"; candidate = tmp_path / "candidate.csv"
    sessions = [date(2025, m, 15) for m in range(1, 13)] + [date(2026, 1, 15), date(2026, 2, 15)]
    def paths(days):
        baseline.write_text("date,return\n" + "".join(f"{d},0\n" for d in days))
        candidate.write_text("date,return\n" + "".join(f"{d},{.001 * (i % 3 - 1)}\n" for i, d in enumerate(days)))
    paths(sessions)
    calendar = {"coverage_start": "2025-01-01", "coverage_end": "2026-12-31", "source": "test_calendar",
                "source_sha256": "a" * 64, "sessions": [d.isoformat() for d in sessions] + ["2026-12-15"]}
    def evaluate():
        return evaluate_pair(baseline, candidate, through=date(2026, 2, 20), calendar=calendar,
            expected_baseline_sha256=hashlib.sha256(baseline.read_bytes()).hexdigest(),
            expected_candidate_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest())
    result = evaluate()
    assert len(result["posterior"]) == 2
    assert result["month_completeness"] == "MATCHED_SELECTED_CALENDAR_SOURCE_NOT_EXCHANGE_ATTESTED"
    paths(sessions[:5] + sessions[6:])
    with pytest.raises(ValueError, match="CALENDAR_SESSION_MISMATCH"):
        evaluate()
    paths(sessions[1:])
    with pytest.raises(ValueError, match="CALENDAR_SESSION_MISMATCH"):
        evaluate()


def test_floating_rate_is_prior_session_and_accrues_calendar_days():
    bars = [(date(2025, 1, 3), 100.), (date(2025, 1, 6), 101.)]
    rates = [(date(2025, 1, 3), .04), (date(2025, 1, 6), .90)]
    out = floating_rate_three_times_path(bars, rates, spread=.005, tracking_drag=.005)
    assert out["rows"][1]["assumed_rate_observation"] == "2025-01-03"
    assert out["rows"][1]["synthetic_return"] == pytest.approx(.03 - (.01 + 2 * (.04 + .005) + .005) * 3 / 365)
    future = floating_rate_three_times_path(bars + [(date(2025, 1, 7), 120.)], rates,
                                           spread=.005, tracking_drag=.005)
    assert future["rows"][:2] == out["rows"]
    with pytest.raises(ValueError, match="LAGGED_RATE"):
        floating_rate_three_times_path(bars, [(date(2024, 12, 1), .04)], spread=.005, tracking_drag=.005)
    with pytest.raises(ValueError, match="UNREGISTERED"):
        floating_rate_three_times_path(bars, rates, spread=.004, tracking_drag=.005)


def test_provider_history_validates_identity_adjustment_and_duplicates(tmp_path):
    result = {"meta": {"symbol": "QQQ", "exchangeTimezoneName": "America/New_York"},
              "timestamp": [1577975400, 1578061800],
              "indicators": {"quote": [{"open": [10, 11], "high": [12, 12], "low": [9, 10],
                                          "close": [11, 11], "volume": [100, 100]}],
                             "adjclose": [{"adjclose": [5.5, 5.5]}]}}
    path = tmp_path / "chart.json"
    def write():
        path.write_text(json.dumps({"chart": {"error": None, "result": [result]}}))
        return hashlib.sha256(path.read_bytes()).hexdigest()
    digest = write()
    parsed = read_provider_history(path, expected_sha256=digest, expected_symbol="QQQ")
    assert parsed["rows"][0]["adjusted_open"] == 5.0
    assert parsed["actions_reconciled"] is False
    with pytest.raises(ValueError, match="BYTES_MISMATCH"):
        read_provider_history(path, expected_sha256="0" * 64, expected_symbol="QQQ")
    result["timestamp"][1] = result["timestamp"][0]
    with pytest.raises(ValueError, match="DUPLICATE"):
        read_provider_history(path, expected_sha256=write(), expected_symbol="QQQ")
