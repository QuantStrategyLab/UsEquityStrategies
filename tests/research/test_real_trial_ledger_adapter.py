"""Synthetic unit examples only; these are not real research evidence."""
from copy import deepcopy
import hashlib
import json

import pytest

from us_equity_strategies.research import real_trial_ledger_adapter as adapter
from us_equity_strategies.research import trial_overfitting_report as original


def digest(value):
    return hashlib.sha256(value).hexdigest()


def seal(attempt):
    attempt["ledger_sha256"] = digest(json.dumps(
        attempt["ledger"], sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


def receipt(sources, record):
    raw = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    h = digest(raw)
    sources[h] = raw
    return h


def seal_attempt(sources, attempt):
    keys = ("domain", "strategy_profile", "input_id", "trial_id", "run_id", "param_version", "status",
            "raw_attempt_source_sha256")
    attempt["source_sha256"] = receipt(sources, {key: attempt.get(key) for key in keys})


def seal_fee(sources, attempt, fill):
    keys = ("fill_id", "timestamp", "session_date", "currency", "fee", "fee_kind", "raw_fee_source_sha256")
    record = {"trial_id": attempt["trial_id"], "run_id": attempt["run_id"],
              **{key: fill.get(key) for key in keys}}
    fill["fee_source_sha256"] = receipt(sources, record)


def example():
    raw = b"synthetic source bytes"
    h = digest(raw)
    sources = {h: raw}
    attempts = []
    for tid, nav in (("a", "101"), ("b", "102")):
        ledger = {"trial_id": tid, "run_id": "run-" + tid, "currency": "USD",
                  "domain": "us_equity", "strategy_profile": "synthetic", "input_id": "input", "param_version": 1,
                  "calendar_id": "XNYS", "periods_per_year": 252,
                  "return_unit": "SIMPLE_NET", "cost_basis": "NET_NAV", "cost_definition": "synthetic test costs",
                  "initial_nav": "100", "initial_date": "2024-01-01",
                  "days": [{"date": "2024-01-02", "nav": nav,
                            "net_return": "0.01" if tid == "a" else "0.02",
                            "external_flow": "0", "fees": "0.25",
                            "gross_pnl": "1.25" if tid == "a" else "2.25", "other_costs": "0"},
                           {"date": "2024-01-03", "nav": "100",
                            "net_return": "-0.009900990099" if tid == "a" else "-0.019607843137",
                            "external_flow": "0", "fees": "0",
                            "gross_pnl": "-1" if tid == "a" else "-2", "other_costs": "0"}],
                  "fills": [{"fill_id": "fill-" + tid, "timestamp": "2024-01-02T15:00:00+00:00",
                             "session_date": "2024-01-02", "currency": "USD", "fee": "0.25",
                             "fee_kind": "REALIZED", "raw_fee_source_sha256": h}]}
        item = {"trial_id": tid, "run_id": "run-" + tid, "status": "succeeded",
                "domain": "us_equity", "strategy_profile": "synthetic", "input_id": "input", "param_version": 1,
                "raw_attempt_source_sha256": h, "ledger_source_sha256": h, "ledger": ledger}
        seal_fee(sources, item, ledger["fills"][0])
        seal_attempt(sources, item)
        seal(item)
        attempts.append(item)
    return dict(attempts=attempts, sources=sources, sessions=["2024-01-02", "2024-01-03"],
                selected_trial_id="a", selection_candidate_ids=["a", "b"],
                annual_risk_free_rate=0.0252, pbo_splits=2,
                effective_trials={"count": 2, "method": "synthetic test assumption", "source_sha256": h})


class Result:
    status = "COMPUTED"
    value = 0.5
    reason_code = None
    details = {}


def test_conditional_statistics_receive_net_returns_and_explicit_effective_count(monkeypatch):
    calls = {}
    def pbo(values, *, n_splits, metric):
        calls["pbo"] = (values, n_splits, metric)
        return Result()
    def dsr(values, sharpes, *, n_effective_trials):
        calls["dsr"] = (values, sharpes, n_effective_trials)
        return Result()
    monkeypatch.setattr(original, "_qpk_function", lambda name: {
        "probability_of_backtest_overfitting": pbo, "deflated_sharpe_ratio": dsr}[name])
    report = adapter.report_normalized_real_ledgers(**example())
    assert calls["pbo"][0]["a"][0] == pytest.approx(0.01 - 0.0252 / 252)
    assert calls["dsr"][2] == 2
    assert report["conditional_candidate_set_pbo"]["value"] == 0.5
    assert report["conditional_candidate_set_dsr"]["value"] == 0.5
    assert report["metrics"] == {"dsr": None, "pbo": None}
    assert report["family_coverage"] == "NOT_VERIFIED"
    with pytest.raises(ValueError, match="REAL_RESEARCH_DATA_UNQUALIFIED"):
        original.compute_trial_set_overfitting({}, selected_trial_id="a", recorded_trial_count=0,
                                             annual_risk_free_rate=0, synthetic=False)


def test_missing_effective_count_and_failed_attempt_are_not_invented(monkeypatch):
    args = example()
    args["effective_trials"] = None
    failed = {"trial_id": "failed", "status": "failed", "domain": "us_equity",
              "strategy_profile": "synthetic", "input_id": "input", "run_id": "failed-run",
              "raw_attempt_source_sha256": next(iter(args["sources"]))}
    seal_attempt(args["sources"], failed)
    args["attempts"].append(failed)
    monkeypatch.setattr(original, "_qpk_function", lambda name: None)
    result = adapter.report_normalized_real_ledgers(**args)
    assert result["effective_trial_count"] is None
    assert result["trial_statuses"]["failed"] == "failed"
    assert result["conditional_candidate_set_dsr"]["reason_code"] == "EFFECTIVE_TRIALS_NOT_SUPPLIED"
    assert result["metrics"] == {"dsr": None, "pbo": None}


def test_receipts_and_study_spec_persist_and_bind_inputs(monkeypatch):
    args = example()
    monkeypatch.setattr(original, "_qpk_function", lambda name: None)
    result = adapter.report_normalized_real_ledgers(**args)
    a = args["attempts"][0]
    assert result["provenance"]["a"]["attempt"]["run_id"] == "run-a"
    assert result["provenance"]["a"]["ledger_sha256"] == a["ledger_sha256"]
    assert result["provenance"]["a"]["fees"][0]["receipt_sha256"] == a["ledger"]["fills"][0]["fee_source_sha256"]
    assert result["study_spec_sha256"] == digest(json.dumps(
        result["study_spec"], sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
    args["selected_trial_id"] = "b"
    assert adapter.report_normalized_real_ledgers(**args)["study_spec_sha256"] != result["study_spec_sha256"]


def test_report_does_not_alias_input_assumptions(monkeypatch):
    args = example()
    monkeypatch.setattr(original, "_qpk_function", lambda name: None)
    result = adapter.report_normalized_real_ledgers(**args)
    saved = deepcopy(result)
    args["effective_trials"]["count"] = 1
    args["selection_candidate_ids"].append("later")
    args["sessions"].append("2024-01-04")
    assert result == saved
    assert result["effective_trials_assumption"]["count"] == 2
    assert result["selection_candidate_ids"] == ["a", "b"]


def test_normalized_fee_receipt_binds_amount(monkeypatch):
    args = example()
    a = args["attempts"][0]
    a["ledger"]["fills"][0]["fee"] = "0.50"
    seal(a)
    monkeypatch.setattr(original, "_qpk_function", lambda name: pytest.fail("unexpected statistics call"))
    with pytest.raises(ValueError, match="NORMALIZED_RECEIPT_MISMATCH"):
        adapter.report_normalized_real_ledgers(**args)


def test_attempt_receipt_binds_source_identity():
    args = example()
    args["attempts"][0]["status"] = "failed"
    with pytest.raises(ValueError, match="NORMALIZED_RECEIPT_MISMATCH"):
        adapter.report_normalized_real_ledgers(**args)


def test_gross_to_net_reconciliation_rejects_double_fee():
    args = example()
    a = args["attempts"][0]
    a["ledger"]["days"][0]["gross_pnl"] = "1.00"
    seal(a)
    with pytest.raises(ValueError, match="NET_NAV_RECONCILIATION_FAILED"):
        adapter.report_normalized_real_ledgers(**args)


@pytest.mark.parametrize("field", ["initial_date", "input_id", "domain", "cost_definition"])
def test_comparison_study_requires_common_horizon_and_basis(field):
    args = example()
    a = args["attempts"][1]
    if field == "initial_date":
        a["ledger"][field] = "2023-12-29"
    elif field == "cost_definition":
        a["ledger"][field] = "different"
    else:
        a[field] = a["ledger"][field] = "different"
        seal_attempt(args["sources"], a)
    seal(a)
    with pytest.raises(ValueError, match="COMPARISON_STUDY_MISMATCH"):
        adapter.report_normalized_real_ledgers(**args)


def test_failed_attempt_run_id_is_retained_and_deduplicated(monkeypatch):
    args = example()
    failed = {"trial_id": "failed", "status": "failed", "domain": "us_equity",
              "strategy_profile": "synthetic", "input_id": "input", "run_id": "failed-run",
              "raw_attempt_source_sha256": next(iter(args["sources"]))}
    seal_attempt(args["sources"], failed)
    args["attempts"].append(failed)
    monkeypatch.setattr(original, "_qpk_function", lambda name: None)
    result = adapter.report_normalized_real_ledgers(**args)
    assert result["provenance"]["failed"]["attempt"]["run_id"] == "failed-run"
    failed["run_id"] = "run-a"
    seal_attempt(args["sources"], failed)
    with pytest.raises(ValueError, match="DUPLICATE_RUN_ID"):
        adapter.report_normalized_real_ledgers(**args)


def test_fill_timestamp_uses_new_york_date(monkeypatch):
    args = example()
    a = args["attempts"][0]
    fill = a["ledger"]["fills"][0]
    fill["timestamp"] = "2024-01-03T00:30:00+00:00"  # Jan 2 in New York.
    seal_fee(args["sources"], a, fill)
    seal(a)
    monkeypatch.setattr(original, "_qpk_function", lambda name: None)
    adapter.report_normalized_real_ledgers(**args)
    fill["timestamp"] = "2024-01-02T01:00:00+00:00"  # Jan 1 in New York.
    seal_fee(args["sources"], a, fill)
    seal(a)
    with pytest.raises(ValueError, match="FILL_TIME_INVALID"):
        adapter.report_normalized_real_ledgers(**args)


@pytest.mark.parametrize(("change", "error"), [
    ("trial", "DUPLICATE_TRIAL_ID"), ("run", "DUPLICATE_RUN_ID"),
    ("identity", "LEDGER_IDENTITY_MISMATCH"), ("source", "SOURCE_DIGEST_MISMATCH"),
    ("ledger", "LEDGER_DIGEST_MISMATCH"), ("assumed", "FEE_EVIDENCE_INVALID"),
    ("missing_fee", "DECIMAL_STRING_REQUIRED"), ("nan_fee", "DECIMAL_INVALID"),
    ("negative_fee", "DECIMAL_INVALID"), ("currency", "FEE_EVIDENCE_INVALID"),
    ("time", "FILL_TIME_INVALID"), ("calendar", "CALENDAR_INVALID"),
    ("flow", "EXTERNAL_FLOWS_UNSUPPORTED"), ("returns", "NET_RETURN_MISMATCH"),
    ("selection", "SELECTION_CANDIDATES_INVALID"), ("duplicate_fill", "DUPLICATE_FILL_ID"),
])
def test_rejects_contradictions_before_computation(change, error, monkeypatch):
    args = deepcopy(example())
    a = args["attempts"][0]
    ledger = a["ledger"]
    fill = ledger["fills"][0]
    if change == "trial": args["attempts"][1]["trial_id"] = "a"
    elif change == "run": args["attempts"][1]["run_id"] = "run-a"
    elif change == "identity": ledger["trial_id"] = "other"
    elif change == "source": args["sources"][a["source_sha256"]] = b"different"
    elif change == "ledger": a["ledger_sha256"] = "0" * 64
    elif change == "assumed": fill["fee_kind"] = "assumed5bps"
    elif change == "missing_fee": del fill["fee"]
    elif change == "nan_fee": fill["fee"] = "NaN"
    elif change == "negative_fee": fill["fee"] = "-1"
    elif change == "currency": fill["currency"] = "EUR"
    elif change == "time": fill["timestamp"] = "2024-01-03T15:00:00+00:00"
    elif change == "calendar": args["sessions"].reverse()
    elif change == "flow": ledger["days"][0]["external_flow"] = "1"
    elif change == "returns": ledger["days"][0]["net_return"] = "0.02"
    elif change == "selection": args["selection_candidate_ids"] = ["a", "missing"]
    elif change == "duplicate_fill": ledger["fills"].append(deepcopy(fill))
    if change in {"assumed", "missing_fee", "nan_fee", "negative_fee", "currency", "time"}:
        seal_fee(args["sources"], a, fill)
    if change not in {"ledger", "trial", "run", "source", "calendar", "selection"}:
        seal(a)
    monkeypatch.setattr(original, "_qpk_function", lambda name: pytest.fail("unexpected statistics call"))
    with pytest.raises(ValueError, match=f"^{error}$"):
        adapter.report_normalized_real_ledgers(**args)
