from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path

import pytest

import us_equity_strategies.research.r3_joint_evidence as r3

from us_equity_strategies.research.r3_joint_evidence import (
    ALIGNED_DATES_SHA256,
    CONTRACT_VERSION,
    PROFILE_CANONICAL_JSON,
    PROFILE_SHA256,
    SCENARIOS,
    THRESHOLD_PROFILE,
    WINDOW_SPECS,
    CostScenario,
    FileIdentity,
    R3EvidenceError,
    _bootstrap_indices,
    _canonical_bytes,
    _dates_sha256,
    _dependency_metrics,
    _digest_value,
    _eligibility,
    _input_identity,
    _invalid_joint_record,
    _invalid_strategy_record,
    _monte_carlo,
    _run_independent,
    _simulate_strategy,
    _strict_json_load,
    _to_wire,
    _verified_call,
    _window_metrics,
    build_r4_handoff,
    load_persisted_bundle,
    persist_bundle,
    validate_bundle,
)
from us_equity_strategies.research.tqqq_offline_input_contract import (
    InputRow,
    OfflineInput,
)
from us_equity_strategies.research.tqqq_typed_baseline_result import (
    run_typed_baseline,
)


TEST_SOURCE_COMMIT = "1" * 40


def _rows(
    signal_closes: list[float],
    *,
    traded_opens: list[float] | None = None,
    traded_closes: list[float] | None = None,
) -> tuple[tuple[InputRow, ...], tuple[InputRow, ...]]:
    count = len(signal_closes)
    opens = traded_opens or [50.0] * count
    closes = traded_closes or opens
    signal: list[InputRow] = []
    traded: list[InputRow] = []
    for index, signal_close in enumerate(signal_closes):
        as_of = (date(2025, 1, 1) + timedelta(days=index)).isoformat()
        traded_open = opens[index]
        traded_close = closes[index]
        signal.append(
            InputRow(
                "QQQ",
                as_of,
                signal_close,
                signal_close,
                signal_close,
                signal_close,
                1.0,
            )
        )
        traded.append(
            InputRow(
                "TQQQ",
                as_of,
                traded_open,
                max(traded_open, traded_close),
                min(traded_open, traded_close),
                traded_close,
                1.0,
            )
        )
    return tuple(signal), tuple(traded)


def _typed_input(
    signal_closes: list[float],
    *,
    traded_opens: list[float] | None = None,
    traded_closes: list[float] | None = None,
) -> OfflineInput:
    signal, traded = _rows(
        signal_closes,
        traded_opens=traded_opens,
        traded_closes=traded_closes,
    )
    rows = tuple(sorted((*signal, *traded), key=lambda row: (row.as_of, row.symbol)))
    return OfflineInput(rows, b"r3-test", "a" * 64, "r3_test_v1")


def _scenario(name: str) -> CostScenario:
    return next(scenario for scenario in SCENARIOS if scenario.scenario_id == name)


def _strategy_record(strategy_id: str, status: str) -> dict[str, object]:
    eligible = status == "PASS"
    record: dict[str, object] = {
        "strategy_id": strategy_id,
        "profile": f"{strategy_id.lower()}_profile",
        "baseline_version": f"{strategy_id.lower()}_baseline_v1",
        "signal_asset": "QQQ" if strategy_id == "TQQQ" else "SOXX",
        "traded_asset": strategy_id,
        "input_digest": ("a" if strategy_id == "TQQQ" else "b") * 64,
        "evidence_valid": status != "NOT_EVALUATED",
        "promotion_status": status,
        "r4a_handoff_eligible": eligible,
        "size_zero_required": not eligible,
        "failure_codes": [] if eligible else ["TEST_INELIGIBLE"],
        "windows": None,
        "monte_carlo": None,
    }
    record["evidence_digest"] = _digest_value(record)
    return record


def _handoff_input(record: dict[str, object]) -> dict[str, object]:
    return {
        "strategy_id": record["strategy_id"],
        "profile": record["profile"],
        "evidence_valid": record["evidence_valid"],
        "research_eligibility_status": record["promotion_status"],
        "eligible": record["r4a_handoff_eligible"],
        "size_zero_required": record["size_zero_required"],
        "failure_codes": record["failure_codes"],
        "evidence_digest": record["evidence_digest"],
        "input_digest": record["input_digest"],
        "sample_count": None,
        "oos_return_distribution_sha256": None,
        "wfa_test_distribution_sha256": None,
        "mean_daily_return": None,
        "final_holdout_sharpe": None,
        "annualized_volatility": None,
        "forecast_volatility": None,
        "forecast_volatility_method": "FINAL_OOS_SAMPLE_VOL_252_V1",
        "historical_max_drawdown_abs": None,
        "expected_shortfall_95": None,
        "stressed_expected_shortfall_95_abs": None,
        "stressed_loss_fraction_at_full_allocation": None,
        "trade_count": None,
        "annualized_turnover": None,
        "zero_to_stress_return_degradation": None,
        "cost_robustness_by_scenario": None,
        "mc_terminal_loss_probability": None,
        "mc_terminal_return_p05": None,
        "mc_max_drawdown_p95_abs": None,
        "daily_return_series_sha256": None,
        "dependency_risk_ref": None,
    }


def _minimal_bundle(source_commit: str = TEST_SOURCE_COMMIT) -> dict[str, object]:
    tqqq = _strategy_record("TQQQ", "NOT_EVALUATED")
    soxl = _strategy_record("SOXL", "NOT_EVALUATED")
    joint: dict[str, object] = {
        "status": "NOT_RUN_EVIDENCE_INVALID",
        "semantics": "REPORT_ONLY_CONTEXT_NOT_A_GATE_FOR_R4A",
        "preconditions": {"both_evidence_valid": False},
        "aligned_date_count": None,
        "aligned_dates_sha256": None,
        "scenarios": None,
        "gate_status": "NOT_APPLICABLE_REPORT_ONLY",
        "failure_codes": [],
    }
    joint["evidence_digest"] = _digest_value(joint)
    handoff = {
        "schema": "qsl.research.r4_independent_sizing_evidence_input.v1",
        "scope": "RESEARCH_ONLY",
        "source_commit": source_commit,
        "as_of_date": "2026-07-15",
        "reference_cost_scenario": "C2_5",
        "research_eligibility_profile_sha256": PROFILE_SHA256,
        "per_strategy_inputs": [_handoff_input(tqqq), _handoff_input(soxl)],
        "dependency_risk": {
            "status": "NOT_RUN_EVIDENCE_INVALID",
            "semantics": "REPORT_ONLY_CONTEXT_NOT_A_GATE_FOR_R4A",
            "aligned_dates_sha256": None,
            "sample_count": None,
            "covariance_matrix_2x2": None,
            "pearson_correlation": None,
            "common_drawdown_day_rate": None,
            "tail_co_loss_rate": None,
            "cost_stress_by_scenario": None,
        },
        "limitations": {
            "research_only": True,
            "provider_completeness": "unverified",
            "calendar_authority": "unverified",
        },
    }
    return {
        "schema": "qsl.research.r3_joint_evidence_bundle.v1",
        "contract_version": CONTRACT_VERSION,
        "source_commit": source_commit,
        "method_digest": r3._method_digest(source_commit),
        "threshold_profile": {
            "profile": THRESHOLD_PROFILE,
            "canonical_json": PROFILE_CANONICAL_JSON,
            "sha256": PROFILE_SHA256,
        },
        "input_identity": _input_identity(False, False, source_commit),
        "strategies": [tqqq, soxl],
        "joint_dependency": joint,
        "r4_handoff": handoff,
        "terminal": {
            "outcome": "R3_EVIDENCE_READY",
            "failed_stage": "STRATEGY_EVIDENCE",
            "failure_codes": [
                "TQQQ_TEST_INELIGIBLE",
                "SOXL_TEST_INELIGIBLE",
            ],
            "eligible_strategies": [],
            "ineligible_strategies": ["TQQQ", "SOXL"],
        },
    }


def test_profile_method_and_windows_are_frozen_before_performance() -> None:
    assert hashlib.sha256(PROFILE_CANONICAL_JSON.encode()).hexdigest() == PROFILE_SHA256
    assert ALIGNED_DATES_SHA256 == "9ad30cd2ae54d56e58e4ea517f15070b31b4e1d9127ae6031529c191920a7f80"
    assert _dates_sha256(("2026-01-01", "2026-01-02")) == hashlib.sha256(
        b"2026-01-01\n2026-01-02\n"
    ).hexdigest()
    assert json.dumps(
        THRESHOLD_PROFILE,
        sort_keys=True,
        separators=(",", ":"),
    ) == PROFILE_CANONICAL_JSON
    method_spec = r3._method_spec(TEST_SOURCE_COMMIT)
    assert r3._method_digest(TEST_SOURCE_COMMIT) == _digest_value(method_spec)
    assert [scenario.scenario_id for scenario in SCENARIOS] == [
        "ZERO",
        "C1_2",
        "C2_5",
        "C5_10_STRESS",
    ]
    assert [(item.segment_id, item.raw_start, item.raw_end) for item in WINDOW_SPECS] == [
        ("SMA_WARMUP", 0, 199),
        ("F1_TRAIN", 200, 368),
        ("F1_EMBARGO_1", 369, 369),
        ("F1_VALIDATION", 370, 411),
        ("F1_EMBARGO_2", 412, 412),
        ("F1_TEST", 413, 454),
        ("F2_TRAIN", 200, 454),
        ("F2_EMBARGO_1", 455, 455),
        ("F2_VALIDATION", 456, 497),
        ("F2_EMBARGO_2", 498, 498),
        ("F2_TEST", 499, 540),
        ("F3_TRAIN", 200, 540),
        ("F3_EMBARGO_1", 541, 541),
        ("F3_VALIDATION", 542, 583),
        ("F3_EMBARGO_2", 584, 584),
        ("F3_TEST", 585, 626),
        ("FINAL_HOLDOUT", 627, 752),
    ]


def test_zero_cost_simulation_exactly_matches_merged_typed_baseline() -> None:
    closes = [100.0] * 199 + [200.0, 1.0, 300.0, 1.0]
    opens = [50.0] * 203
    traded_closes = [50.0] * 203
    opens[200:203] = [50.0, 60.0, 40.0]
    traded_closes[200:203] = [55.0, 60.0, 44.0]
    source = _typed_input(closes, traded_opens=opens, traded_closes=traded_closes)
    merged = run_typed_baseline(source)
    signal, traded = _rows(closes, traded_opens=opens, traded_closes=traded_closes)

    actual = _simulate_strategy(signal, traded, _scenario("ZERO"))

    assert len(actual) == merged.evaluation_count == 3
    assert [point.date for point in actual] == [point.date for point in merged.equity_curve]
    assert [point.end_equity.hex() for point in actual] == [
        point.equity.hex() for point in merged.equity_curve
    ]
    assert [point.cash.hex() for point in actual] == [
        point.cash.hex() for point in merged.equity_curve
    ]
    assert [point.quantity.hex() for point in actual] == [
        point.tqqq_quantity.hex() for point in merged.equity_curve
    ]
    assert [point.daily_return.hex() for point in actual] == [
        point.daily_return.hex() for point in merged.daily_returns
    ]
    assert sum(point.transition for point in actual) == merged.trade_count


def test_signal_and_execution_use_only_prior_close_and_next_open() -> None:
    closes = [100.0] * 199 + [200.0, 1.0, 300.0, 1.0]
    opens = [50.0] * 203
    traded_closes = [50.0] * 203
    signal, traded = _rows(closes, traded_opens=opens, traded_closes=traded_closes)
    original = _simulate_strategy(signal, traded, _scenario("ZERO"))

    changed_signal = list(signal)
    changed_traded = list(traded)
    changed_signal[202] = replace(changed_signal[202], close=9_999.0, high=9_999.0, open=9_999.0, low=9_999.0)
    changed_traded[202] = replace(changed_traded[202], open=5_000.0, high=5_000.0, low=5_000.0, close=5_000.0)
    changed = _simulate_strategy(tuple(changed_signal), tuple(changed_traded), _scenario("ZERO"))

    assert original[:2] == changed[:2]
    assert original[2] != changed[2]


def test_costs_apply_only_on_next_open_transitions() -> None:
    closes = [100.0] * 199 + [200.0, 200.0, 200.0]
    signal, traded = _rows(closes)
    scenario = _scenario("C1_2")

    points = _simulate_strategy(signal, traded, scenario)

    first = points[0]
    expected_fill = 50.0 * (1.0 + 2.0 / 10_000.0)
    expected_quantity = 100_000.0 / (expected_fill * (1.0 + 1.0 / 10_000.0))
    assert first.quantity == expected_quantity
    assert first.commission_paid == expected_quantity * expected_fill / 10_000.0
    assert first.slippage_impact_vs_open == expected_quantity * (expected_fill - 50.0)
    assert first.gross_traded_notional_at_open == expected_quantity * 50.0
    assert first.buy is True and first.sell is False
    assert points[1].transition is False
    assert points[1].commission_paid == points[1].slippage_impact_vs_open == 0.0


def test_window_metrics_use_continuous_state_and_exclude_other_dates() -> None:
    closes = [100.0] * 199 + [200.0, 1.0, 300.0, 1.0]
    opens = [50.0] * 203
    traded_closes = [50.0] * 203
    opens[200:203] = [50.0, 60.0, 40.0]
    traded_closes[200:203] = [55.0, 60.0, 44.0]
    signal, traded = _rows(closes, traded_opens=opens, traded_closes=traded_closes)
    points = _simulate_strategy(signal, traded, _scenario("ZERO"))

    metrics = _window_metrics(points, 201, 202)

    assert metrics["observation_count"] == 2
    assert metrics["start_equity"] == points[1].start_equity
    assert metrics["end_equity"] == points[2].end_equity
    assert metrics["trade_count"] == 2
    assert metrics["terminal_exposure_open"] is True
    assert math.isclose(
        metrics["cumulative_return"],
        math.prod(1.0 + point.daily_return for point in points[1:]) - 1.0,
        rel_tol=1e-12,
    )


def test_hmac_bootstrap_and_monte_carlo_are_deterministic_and_scenario_paired() -> None:
    first = _bootstrap_indices("INDEPENDENT:TQQQ", 0, path_length=12, block_length=4)
    second = _bootstrap_indices("INDEPENDENT:TQQQ", 0, path_length=12, block_length=4)
    other = _bootstrap_indices("INDEPENDENT:SOXL", 0, path_length=12, block_length=4)
    assert first == second
    assert first != other
    assert len(first) == 12

    vector = tuple(0.01 if index % 2 == 0 else -0.005 for index in range(12))
    result = _monte_carlo(
        {"ZERO": vector, "C2_5": vector},
        "INDEPENDENT:TQQQ",
        trials=32,
        path_length=12,
        block_length=4,
    )
    assert result["ZERO"] == result["C2_5"]
    assert result == _monte_carlo(
        {"ZERO": vector, "C2_5": vector},
        "INDEPENDENT:TQQQ",
        trials=32,
        path_length=12,
        block_length=4,
    )


def test_eligibility_uses_only_five_locked_fields_and_strict_boundaries() -> None:
    passed = _eligibility([0.1, 0.0, 0.2], 0.01, 0.01, 0.49)
    assert passed == ("PASS", ())

    status, codes = _eligibility([0.1, 0.0, -0.1], 0.0, 0.0, 0.5)
    assert status == "FAIL"
    assert codes == (
        "WFA_C2_5_PASSING_WINDOWS_BELOW_MINIMUM",
        "FINAL_HOLDOUT_C2_5_RETURN_NOT_STRICTLY_POSITIVE",
        "FINAL_HOLDOUT_C5_10_STRESS_RETURN_NOT_STRICTLY_POSITIVE",
        "MC_C2_5_TERMINAL_LOSS_PROBABILITY_NOT_STRICTLY_BELOW_HALF",
    )


def test_dependency_metrics_are_report_only_and_use_same_dates() -> None:
    dates = tuple(f"2026-01-{day:02d}" for day in range(1, 9))
    t_returns = (0.04, -0.08, 0.03, -0.02, 0.01, -0.05, 0.02, -0.01)
    s_returns = (0.02, -0.09, 0.01, -0.03, 0.03, -0.04, 0.01, -0.02)
    t_equity = tuple(math.prod(1.0 + value for value in t_returns[: index + 1]) for index in range(8))
    s_equity = tuple(math.prod(1.0 + value for value in s_returns[: index + 1]) for index in range(8))

    result = _dependency_metrics(dates, t_returns, t_equity, s_returns, s_equity)

    assert result["pearson_correlation"] > 0.0
    assert result["both_below_own_peak_count"] > 0
    assert result["tail_co_loss_count"] == 1
    assert result["gate_status"] == "NOT_APPLICABLE_REPORT_ONLY"
    assert "combined_return" not in json.dumps(result, sort_keys=True)
    assert "weight" not in json.dumps(result, sort_keys=True)


def test_strategy_specific_failure_does_not_suppress_the_other_strategy() -> None:
    calls: list[str] = []

    def fail_tqqq() -> dict[str, object]:
        calls.append("TQQQ")
        raise R3EvidenceError("TQQQ_INPUT_INVALID")

    def pass_soxl() -> dict[str, object]:
        calls.append("SOXL")
        return _strategy_record("SOXL", "PASS")

    tqqq, soxl = _run_independent(fail_tqqq, pass_soxl)

    assert calls == ["TQQQ", "SOXL"]
    assert tqqq == _invalid_strategy_record("TQQQ", "TQQQ_INPUT_INVALID")
    assert soxl["promotion_status"] == "PASS"
    assert soxl["r4a_handoff_eligible"] is True


def test_file_identity_is_checked_before_and_after_use(tmp_path: Path) -> None:
    path = tmp_path / "input.csv"
    path.write_bytes(b"stable")
    identity = FileIdentity(path, hashlib.sha256(b"stable").hexdigest(), 6)

    assert _verified_call((identity,), lambda: "ok") == "ok"

    def mutate() -> str:
        path.write_bytes(b"changed")
        return "unsafe"

    with pytest.raises(R3EvidenceError, match="FILE_IDENTITY_MISMATCH"):
        _verified_call((identity,), mutate)


def test_wire_encoding_rejects_nonfinite_and_encodes_binary64() -> None:
    assert _to_wire({"value": 0.5, "count": 1, "enabled": True}) == {
        "value": "0x1.0000000000000p-1",
        "count": 1,
        "enabled": True,
    }
    with pytest.raises(R3EvidenceError, match="NONFINITE_VALUE"):
        _to_wire({"value": float("nan")})


def test_r4_handoff_has_two_independent_records_and_no_in_band_bundle_sha() -> None:
    tqqq = _strategy_record("TQQQ", "PASS")
    soxl = _strategy_record("SOXL", "FAIL")
    handoff = build_r4_handoff(
        [_handoff_input(tqqq), _handoff_input(soxl)],
        {
            "status": "NOT_RUN_EVIDENCE_INVALID",
            "semantics": "REPORT_ONLY_CONTEXT_NOT_A_GATE_FOR_R4A",
            "aligned_dates_sha256": None,
            "sample_count": None,
            "covariance_matrix_2x2": None,
            "pearson_correlation": None,
            "common_drawdown_day_rate": None,
            "tail_co_loss_rate": None,
            "cost_stress_by_scenario": None,
        },
        TEST_SOURCE_COMMIT,
    )

    assert "bundle_sha256" not in handoff
    assert [item["strategy_id"] for item in handoff["per_strategy_inputs"]] == [
        "TQQQ",
        "SOXL",
    ]
    assert handoff["per_strategy_inputs"][0]["eligible"] is True
    assert handoff["per_strategy_inputs"][1]["eligible"] is False
    assert handoff["per_strategy_inputs"][1]["size_zero_required"] is True


def test_bundle_sidecar_is_verified_before_strict_parse_and_readback(tmp_path: Path) -> None:
    bundle = _minimal_bundle()

    paths = persist_bundle(bundle, tmp_path)
    loaded = load_persisted_bundle(tmp_path)

    expected_bytes = _canonical_bytes(bundle)
    expected_sha = hashlib.sha256(expected_bytes).hexdigest()
    assert loaded == bundle
    assert paths.bundle.read_bytes() == expected_bytes
    assert paths.sidecar.read_text() == expected_sha + "\n"
    readback = json.loads(paths.readback.read_text())
    assert readback == {
        "bundle_bytes": len(expected_bytes),
        "bundle_sha256": expected_sha,
        "canonical_bytes_equal": True,
        "nested_digests_valid": True,
        "persisted_bytes_sha256_equal": True,
        "schema": "qsl.research.r3_joint_evidence_readback.v1",
        "sidecar_verified_before_parse": True,
        "strict_schema_valid": True,
    }

    paths.bundle.write_text("{not-json")
    paths.sidecar.write_text("0" * 64 + "\n")
    with pytest.raises(R3EvidenceError, match="SIDECAR_SHA_MISMATCH"):
        load_persisted_bundle(tmp_path)


def test_bundle_rejects_extra_keys_in_band_sha_and_different_existing_bytes(tmp_path: Path) -> None:
    bundle = _minimal_bundle()
    validate_bundle(bundle)

    extra = dict(bundle)
    extra["unexpected"] = True
    with pytest.raises(R3EvidenceError, match="BUNDLE_SCHEMA_INVALID"):
        validate_bundle(extra)

    in_band = json.loads(json.dumps(bundle))
    in_band["r4_handoff"]["bundle_sha256"] = "0" * 64
    with pytest.raises(R3EvidenceError, match="R4_HANDOFF_SCHEMA_INVALID"):
        validate_bundle(in_band)

    paths = persist_bundle(bundle, tmp_path)
    paths.bundle.write_bytes(b"different")
    with pytest.raises(R3EvidenceError, match="EXISTING_ARTIFACT_DIFFERS"):
        persist_bundle(bundle, tmp_path)


def test_sell_costs_and_terminal_exposure_do_not_invent_liquidation() -> None:
    closes = [100.0] * 199 + [200.0, 1.0, 1.0]
    opens = [50.0] * 202
    opens[201] = 60.0
    signal, traded = _rows(closes, traded_opens=opens)

    points = _simulate_strategy(signal, traded, _scenario("C1_2"))

    sold = points[1]
    sell_fill = 60.0 * (1.0 - 2.0 / 10_000.0)
    gross = points[0].quantity * sell_fill
    assert sold.sell is True and sold.buy is False
    assert sold.commission_paid == gross * (1.0 / 10_000.0)
    assert sold.cash == gross - sold.commission_paid
    assert sold.quantity == 0.0

    always_on = [200.0] * 202
    signal, traded = _rows(always_on)
    open_points = _simulate_strategy(signal, traded, _scenario("C2_5"))
    assert sum(point.transition for point in open_points) == 1
    assert open_points[-1].quantity > 0.0


def test_paired_monte_carlo_uses_one_joint_path_for_both_strategies() -> None:
    vector = tuple(0.01 if index % 3 else -0.02 for index in range(12))
    from us_equity_strategies.research.r3_joint_evidence import _paired_monte_carlo

    result = _paired_monte_carlo(
        {"C2_5": vector},
        {"C2_5": vector},
        trials=32,
        path_length=12,
        block_length=4,
    )["C2_5"]

    assert result["tqqq_terminal_cumulative_return_p05"] == result["soxl_terminal_cumulative_return_p05"]
    assert result["tqqq_terminal_cumulative_return_p50"] == result["soxl_terminal_cumulative_return_p50"]
    assert result["tqqq_max_drawdown_abs_p95"] == result["soxl_max_drawdown_abs_p95"]


def test_joint_calculation_failure_is_report_only_not_a_strategy_veto() -> None:
    joint = _invalid_joint_record("JOINT_CALCULATION_INVALID", both_evidence_valid=True)

    assert joint["status"] == "INVALID_REPORT_ONLY"
    assert joint["gate_status"] == "NOT_APPLICABLE_REPORT_ONLY"
    assert joint["failure_codes"] == ["JOINT_CALCULATION_INVALID"]
    assert joint["evidence_digest"] == _digest_value(
        {key: value for key, value in joint.items() if key != "evidence_digest"}
    )


def test_strict_json_rejects_duplicate_keys() -> None:
    with pytest.raises(R3EvidenceError, match="DUPLICATE_JSON_KEY"):
        _strict_json_load(b'{"schema":"one","schema":"two"}')


def test_atomic_replace_failure_leaves_no_authoritative_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import us_equity_strategies.research.r3_joint_evidence as r3

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("injected")

    monkeypatch.setattr(r3.os, "replace", fail_replace)
    with pytest.raises(R3EvidenceError, match="ATOMIC_WRITE_FAILED"):
        persist_bundle(_minimal_bundle(), tmp_path)

    assert not (tmp_path / "r3_joint_evidence_bundle.sha256").exists()
    assert not (tmp_path / "r3_joint_evidence_bundle.readback.json").exists()


def test_existing_different_readback_is_preserved_on_refusal(tmp_path: Path) -> None:
    bundle = _minimal_bundle()
    paths = persist_bundle(bundle, tmp_path)
    paths.readback.write_bytes(b"different-readback")

    with pytest.raises(R3EvidenceError, match="EXISTING_ARTIFACT_DIFFERS"):
        persist_bundle(bundle, tmp_path)

    assert paths.readback.read_bytes() == b"different-readback"


def test_thin_cli_emits_only_terminal_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import scripts.run_r3_joint_evidence as cli
    from us_equity_strategies.research.r3_joint_evidence import PersistedPaths

    bundle = _minimal_bundle()
    paths = PersistedPaths(tmp_path / "bundle", tmp_path / "sidecar", tmp_path / "readback")
    monkeypatch.setattr(cli, "run_private_r3", lambda **_kwargs: (bundle, paths))

    assert cli.main([]) == 2
    output = json.loads(capsys.readouterr().out)
    assert set(output) == {
        "outcome",
        "failed_stage",
        "failure_codes",
        "eligible_strategies",
        "ineligible_strategies",
        "joint_status",
        "bundle_path",
        "sidecar_path",
        "readback_path",
    }
    assert "return" not in output and "position" not in output


@pytest.mark.parametrize(
    ("failure_codes", "eligible", "ineligible", "expected_exit"),
    [
        (["TQQQ_INPUT_INVALID"], ["SOXL"], ["TQQQ"], 2),
        ([], ["SOXL"], ["TQQQ"], 1),
        ([], ["TQQQ", "SOXL"], [], 0),
    ],
)
def test_cli_exit_code_distinguishes_failure_negative_evidence_and_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_codes: list[str],
    eligible: list[str],
    ineligible: list[str],
    expected_exit: int,
) -> None:
    import scripts.run_r3_joint_evidence as cli
    from us_equity_strategies.research.r3_joint_evidence import PersistedPaths

    bundle = _minimal_bundle()
    bundle["terminal"] = {
        "outcome": "R3_EVIDENCE_READY",
        "failed_stage": "STRATEGY_EVIDENCE" if failure_codes else None,
        "failure_codes": failure_codes,
        "eligible_strategies": eligible,
        "ineligible_strategies": ineligible,
    }
    paths = PersistedPaths(tmp_path / "bundle", tmp_path / "sidecar", tmp_path / "readback")
    monkeypatch.setattr(cli, "run_private_r3", lambda **_kwargs: (bundle, paths))

    assert cli.main([]) == expected_exit


def test_source_commit_uses_injected_clean_exact_revision_and_fails_closed() -> None:
    repo_root = Path("/deterministic/repo")

    def clean_git(root: Path, arguments: tuple[str, ...]) -> bytes:
        assert root == repo_root
        outputs = {
            ("rev-parse", "--show-toplevel"): b"/deterministic/repo\n",
            ("rev-parse", "--verify", "HEAD^{commit}"): (TEST_SOURCE_COMMIT + "\n").encode(),
            ("status", "--porcelain=v1", "--untracked-files=all"): b"",
            (
                "cat-file",
                "-e",
                f"{TEST_SOURCE_COMMIT}:src/us_equity_strategies/research/r3_joint_evidence.py",
            ): b"",
            (
                "cat-file",
                "-e",
                f"{TEST_SOURCE_COMMIT}:scripts/run_r3_joint_evidence.py",
            ): b"",
        }
        return outputs[arguments]

    assert r3._resolve_source_commit(repo_root, git_output=clean_git) == TEST_SOURCE_COMMIT
    assert r3._method_spec(TEST_SOURCE_COMMIT)["source_commit"] == TEST_SOURCE_COMMIT
    assert r3._method_digest(TEST_SOURCE_COMMIT) == r3._method_digest(TEST_SOURCE_COMMIT)
    assert r3._method_digest(TEST_SOURCE_COMMIT) != r3._method_digest("2" * 40)

    def dirty_git(root: Path, arguments: tuple[str, ...]) -> bytes:
        if arguments == ("status", "--porcelain=v1", "--untracked-files=all"):
            return b" M src/us_equity_strategies/research/r3_joint_evidence.py\0"
        return clean_git(root, arguments)

    with pytest.raises(R3EvidenceError, match="SOURCE_CHECKOUT_DIRTY"):
        r3._resolve_source_commit(repo_root, git_output=dirty_git)

    def missing_runner(root: Path, arguments: tuple[str, ...]) -> bytes:
        if arguments[0] == "cat-file":
            raise OSError("not committed")
        return clean_git(root, arguments)

    with pytest.raises(R3EvidenceError, match="SOURCE_RUNNER_NOT_COMMITTED"):
        r3._resolve_source_commit(repo_root, git_output=missing_runner)

    with pytest.raises(R3EvidenceError, match="SOURCE_REVISION_UNVERIFIABLE"):
        r3._resolve_source_commit(repo_root, git_output=lambda _root, _arguments: b"")


def test_terminal_derives_only_strategy_evidence_failures_and_preserves_valid_handoff() -> None:
    invalid_tqqq = _invalid_strategy_record("TQQQ", "FILE_IDENTITY_MISMATCH")
    valid_soxl = _strategy_record("SOXL", "PASS")

    terminal = r3._terminal_record([invalid_tqqq, valid_soxl])

    assert terminal == {
        "outcome": "R3_EVIDENCE_READY",
        "failed_stage": "STRATEGY_EVIDENCE",
        "failure_codes": ["TQQQ_FILE_IDENTITY_MISMATCH"],
        "eligible_strategies": ["SOXL"],
        "ineligible_strategies": ["TQQQ"],
    }
    assert invalid_tqqq["promotion_status"] == "NOT_EVALUATED"
    assert invalid_tqqq["r4a_handoff_eligible"] is False
    assert invalid_tqqq["size_zero_required"] is True

    handoff = build_r4_handoff(
        [_handoff_input(invalid_tqqq), _handoff_input(valid_soxl)],
        {
            "status": "NOT_RUN_EVIDENCE_INVALID",
            "semantics": "REPORT_ONLY_CONTEXT_NOT_A_GATE_FOR_R4A",
            "aligned_dates_sha256": None,
            "sample_count": None,
            "covariance_matrix_2x2": None,
            "pearson_correlation": None,
            "common_drawdown_day_rate": None,
            "tail_co_loss_rate": None,
            "cost_stress_by_scenario": None,
        },
        TEST_SOURCE_COMMIT,
    )
    assert handoff["per_strategy_inputs"][0]["eligible"] is False
    assert handoff["per_strategy_inputs"][1]["eligible"] is True

    valid_fail = _strategy_record("TQQQ", "FAIL")
    assert r3._terminal_record([valid_fail, valid_soxl])["failure_codes"] == []


def test_explicit_contract_and_prompt_paths_keep_exact_sha_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract = tmp_path / "contract.md"
    prompt = tmp_path / "prompt.md"
    contract.write_bytes(b"locked-contract")
    prompt.write_bytes(b"locked-prompt")
    monkeypatch.setattr(r3, "CONTRACT_SHA256", hashlib.sha256(contract.read_bytes()).hexdigest())
    monkeypatch.setattr(r3, "WORKER_PROMPT_SHA256", hashlib.sha256(prompt.read_bytes()).hexdigest())
    monkeypatch.setattr(r3, "SOURCE_MODULE_SHA256", {})
    captured: dict[str, object] = {}
    persist_calls: list[Path] = []

    def build(source_commit: str) -> dict[str, object]:
        captured["source_commit"] = source_commit
        return {"source_commit": source_commit}

    def persist(bundle: dict[str, object], output_root: str | Path) -> object:
        captured["bundle"] = bundle
        captured["output_root"] = Path(output_root)
        persist_calls.append(Path(output_root))
        return "persisted"

    monkeypatch.setattr(r3, "_build_bundle", build)
    monkeypatch.setattr(r3, "persist_bundle", persist)

    bundle, paths = r3.run_private_r3(
        tmp_path / "evidence",
        contract_path=contract,
        worker_prompt_path=prompt,
        _source_commit_reader=lambda: TEST_SOURCE_COMMIT,
    )

    assert bundle == {"source_commit": TEST_SOURCE_COMMIT}
    assert paths == "persisted"
    assert captured["source_commit"] == TEST_SOURCE_COMMIT
    assert persist_calls == [tmp_path / "evidence"]

    prompt.write_bytes(b"tampered")
    with pytest.raises(R3EvidenceError, match="FILE_IDENTITY_MISMATCH"):
        r3.run_private_r3(
            tmp_path / "not-published",
            contract_path=contract,
            worker_prompt_path=prompt,
            _source_commit_reader=lambda: TEST_SOURCE_COMMIT,
        )
    assert not (tmp_path / "not-published").exists()
    assert persist_calls == [tmp_path / "evidence"]

    prompt.write_bytes(b"locked-prompt")
    revisions = iter((TEST_SOURCE_COMMIT, "2" * 40))
    with pytest.raises(R3EvidenceError, match="SOURCE_REVISION_CHANGED"):
        r3.run_private_r3(
            tmp_path / "revision-changed",
            contract_path=contract,
            worker_prompt_path=prompt,
            _source_commit_reader=lambda: next(revisions),
        )
    assert not (tmp_path / "revision-changed").exists()
    assert persist_calls == [tmp_path / "evidence"]


def test_cli_passes_only_explicit_identity_paths_and_fails_on_terminal_strategy_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import scripts.run_r3_joint_evidence as cli
    from us_equity_strategies.research.r3_joint_evidence import PersistedPaths

    contract = tmp_path / "contract.md"
    prompt = tmp_path / "prompt.md"
    captured: dict[str, object] = {}
    bundle = _minimal_bundle()
    paths = PersistedPaths(tmp_path / "bundle", tmp_path / "sidecar", tmp_path / "readback")

    def run_private_r3(**kwargs: object) -> tuple[dict[str, object], PersistedPaths]:
        captured.update(kwargs)
        return bundle, paths

    monkeypatch.setattr(cli, "run_private_r3", run_private_r3)

    assert cli.main(
        [
            "--contract-path",
            str(contract),
            "--worker-prompt-path",
            str(prompt),
        ]
    ) == 2
    assert captured == {
        "contract_path": contract,
        "worker_prompt_path": prompt,
    }
    output = json.loads(capsys.readouterr().out)
    assert output["failed_stage"] == "STRATEGY_EVIDENCE"
    assert output["failure_codes"] == [
        "TQQQ_TEST_INELIGIBLE",
        "SOXL_TEST_INELIGIBLE",
    ]


def test_bounded_sma_candidate_keeps_the_sma200_comparison_start() -> None:
    closes = [100.0] * 199 + [200.0, 1.0, 300.0, 1.0]
    signal, traded = _rows(closes)

    points = r3._simulate_tqqq_sma_candidate(
        signal,
        traded,
        _scenario("ZERO"),
        sma_window=160,
    )

    assert len(points) == 3
    assert [point.date for point in points] == [row.as_of for row in traded[200:]]


def test_bounded_sma_selection_is_fixed_to_three_candidates_and_locked_tie_breaks() -> None:
    candidates = [
        {"sma_window": 160, "selection": {"status": "VALID", "primary_score": 1.0, "cumulative_return": 0.2, "abs_max_drawdown": 0.3}},
        {"sma_window": 180, "selection": {"status": "VALID", "primary_score": 1.0, "cumulative_return": 0.2, "abs_max_drawdown": 0.3}},
        {"sma_window": 200, "selection": {"status": "VALID", "primary_score": 1.0, "cumulative_return": 0.2, "abs_max_drawdown": 0.3}},
    ]

    assert r3._select_tqqq_sma_winner(candidates) == 200

    candidates[1]["selection"]["primary_score"] = 1.1
    assert r3._select_tqqq_sma_winner(candidates) == 180


def test_bounded_candidate_marks_nonfinite_validation_sharpe_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _typed_input([200.0] * 753)
    original_simulate = r3._simulate_tqqq_sma_candidate
    execution_ends: list[int | None] = []
    monkeypatch.setattr(
        r3,
        "_validate_private_dates",
        lambda signal_rows, _traded_rows: tuple(row.as_of for row in signal_rows),
    )
    monkeypatch.setattr(
        r3,
        "_simulate_tqqq_sma_candidate",
        lambda *args, **kwargs: execution_ends.append(kwargs["execution_end_exclusive"])
        or original_simulate(*args, **kwargs),
    )

    candidate = r3.evaluate_tqqq_sma_candidate(source, sma_window=160)

    assert candidate["sma_window"] == 160
    assert candidate["selection"] == {
        "status": "INVALID",
        "reason": "VALIDATION_SHARPE_NONFINITE",
        "primary_score": None,
        "cumulative_return": None,
        "abs_max_drawdown": None,
    }
    assert execution_ends == [584]


def test_bounded_optimization_locks_the_winner_before_opening_holdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _typed_input([200.0] * 753)
    calls: list[object] = []
    candidates = {
        160: {"sma_window": 160, "selection": {"status": "VALID", "primary_score": 1.1, "cumulative_return": 0.2, "abs_max_drawdown": 0.3}},
        180: {"sma_window": 180, "selection": {"status": "VALID", "primary_score": 1.0, "cumulative_return": 0.2, "abs_max_drawdown": 0.3}},
        200: {"sma_window": 200, "selection": {"status": "VALID", "primary_score": 1.0, "cumulative_return": 0.2, "abs_max_drawdown": 0.3}},
    }
    baseline = {
        "wfa_c2_5_returns": (0.1, 0.1, 0.1),
        "final_c2_5_return": 0.1,
        "final_c2_5_abs_max_drawdown": 0.2,
        "final_c5_10_stress_return": 0.1,
        "mc_c2_5_loss_probability": 0.1,
    }
    post_lock = {
        "sma_window": 160,
        "r3_eligibility": "PASS",
        "r3_eligibility_reasons": [],
        "wfa_c2_5_returns": [0.1, 0.1, 0.1],
        "final_c2_5_return": 0.2,
        "final_c2_5_abs_max_drawdown": 0.2,
        "final_c5_10_stress_return": 0.1,
        "mc_c2_5_loss_probability": 0.1,
    }
    monkeypatch.setattr(r3, "validate_bundle", lambda _bundle: None)
    monkeypatch.setattr(r3, "_tqqq_baseline_gate_metrics", lambda _bundle: baseline)
    monkeypatch.setattr(r3, "_load_tqqq", lambda: source)
    monkeypatch.setattr(
        r3,
        "evaluate_tqqq_sma_candidate",
        lambda _source, *, sma_window: calls.append(("candidate", sma_window)) or candidates[sma_window],
    )
    monkeypatch.setattr(
        r3,
        "_evaluate_tqqq_sma_post_lock",
        lambda _source, *, sma_window: calls.append(("holdout", sma_window)) or post_lock,
    )
    monkeypatch.setattr(r3, "_verified_call", lambda _identities, action: action())

    report = r3.run_private_tqqq_sma_bounded_optimization(
        {"source_commit": TEST_SOURCE_COMMIT},
        _source_commit_reader=lambda: TEST_SOURCE_COMMIT,
    )

    assert calls == [("candidate", 160), ("candidate", 180), ("candidate", 200), ("holdout", 160)]
    assert report["winner_sma_window"] == 160
    assert report["outcome"] == "RESEARCH_IMPROVEMENT"
    assert report["post_lock"]["final_c2_5_return"] == float(0.2).hex()


def test_cli_runs_the_bounded_optimization_only_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import scripts.run_r3_joint_evidence as cli
    from us_equity_strategies.research.r3_joint_evidence import PersistedPaths

    bundle = _minimal_bundle()
    paths = PersistedPaths(tmp_path / "bundle", tmp_path / "sidecar", tmp_path / "readback")
    report = {
        "outcome": "NO_IMPROVEMENT",
        "winner_sma_window": 200,
        "gate_reasons": ["WINNER_IS_BASELINE_200"],
        "evidence_digest": "b" * 64,
        "source_commit": TEST_SOURCE_COMMIT,
        "runner_envelope": {
            "checkout_verification": "GIT_CLEAN_COMMITTED",
            "ci_provenance": "LOCAL_FOREGROUND",
        },
    }
    persisted: list[tuple[dict[str, object], Path]] = []
    monkeypatch.setattr(cli, "run_private_r3", lambda **_kwargs: (bundle, paths))
    monkeypatch.setattr(
        cli,
        "run_private_tqqq_sma_bounded_optimization",
        lambda actual_bundle: report if actual_bundle is bundle else None,
    )
    monkeypatch.setattr(
        cli,
        "persist_tqqq_sma_bounded_optimization",
        lambda actual_report, root: persisted.append((actual_report, Path(root))),
    )

    assert cli.main(["--tqqq-sma-bounded-optimization"]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["tqqq_sma_bounded_optimization"] == {
        "outcome": "NO_IMPROVEMENT",
        "winner_sma_window": 200,
        "gate_reasons": ["WINNER_IS_BASELINE_200"],
        "evidence_digest": "b" * 64,
        "source_commit": TEST_SOURCE_COMMIT,
        "checkout_verification": "GIT_CLEAN_COMMITTED",
        "ci_provenance": "LOCAL_FOREGROUND",
    }
    assert persisted == [(report, tmp_path)]


def test_bounded_optimization_report_persists_with_a_sidecar(tmp_path: Path) -> None:
    report: dict[str, object] = {
        "schema": r3.TQQQ_SMA_OPTIMIZATION_SCHEMA,
        "outcome": "NO_IMPROVEMENT",
    }
    report["evidence_digest"] = _digest_value(report)

    paths = r3.persist_tqqq_sma_bounded_optimization(report, tmp_path)

    persisted = _canonical_bytes(report)
    assert paths.report.read_bytes() == persisted
    assert paths.sidecar.read_text() == hashlib.sha256(persisted).hexdigest() + "\n"
