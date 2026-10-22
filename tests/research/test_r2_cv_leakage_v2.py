"""R2 session-window counterexamples; artificial prices only, no real trials."""
import importlib
import math
import os
import socket
from datetime import date, datetime, timedelta, timezone

import pytest

from us_equity_strategies.research import fractional_kelly_walk_forward_v2 as single
from us_equity_strategies.research import portfolio_kelly_walk_forward_v2 as portfolio
from us_equity_strategies.research.fractional_kelly_walk_forward_plan import WalkForwardPlan


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("SYNTHETIC_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


@pytest.fixture
def stats():
    if single._stats_module() is None:
        if os.environ.get("UES_REQUIRE_QPK_RESEARCH_STATS") == "1":
            pytest.fail("required QPK research_stats overlay missing")
        pytest.skip("runs with the research_stats CI overlay")
    return importlib.import_module("quant_platform_kit.research_stats")


@pytest.mark.parametrize("anchored", [False, True])
def test_purge_excludes_actual_three_session_forward_label_overlap(anchored):
    # h=3 is a test fixture, not a new preregistered estimator/horizon.
    horizon = 3
    prices = [100.0 + i + (i % 4) for i in range(100)]
    labels = [prices[i + horizon] / prices[i] - 1 for i in range(len(prices) - horizon)]
    plan = WalkForwardPlan(24, 5, horizon, anchored, horizon, 2)
    for nested in plan.nested_folds(len(labels)):
        for start, stop, val_start, val_stop in (nested.outer, *nested.inner):
            validation = set(range(val_start, val_stop))
            leaked_without_purge = [i for i in range(start, val_start)
                                   if validation.intersection(range(i, i + horizon + 1))]
            assert leaked_without_purge  # naive adjacent windows admit real label leakage
            assert all(i not in range(start, stop) for i in leaked_without_purge)
            before = labels[start:stop]
            altered = prices.copy()
            for i in validation:
                altered[i] *= 2
            after = [altered[i + horizon] / altered[i] - 1 for i in range(start, stop)]
            assert after == before
            assert any(altered[i + horizon] / altered[i] - 1 != labels[i]
                       for i in leaked_without_purge)


@pytest.mark.parametrize("n", [0, 1, 2, 3])
def test_empty_or_single_sample_cannot_form_training_and_validation(n):
    plan = WalkForwardPlan(2, 1, 1)
    assert plan.folds(n) == []
    assert plan.nested_folds(n) == []


def test_single_validation_sample_and_embargo_half_open_boundaries():
    plan = WalkForwardPlan(12, 1, 3, label_horizon_sessions=3, embargo_sessions=2)
    nested = plan.nested_folds(22, inner_splits=3)
    assert nested
    for windows in ([f.outer for f in nested], *(f.inner for f in nested)):
        for previous, current in zip(windows, windows[1:]):
            embargo_start, embargo_stop = previous[3], previous[3] + 2
            selected = set(range(previous[2], previous[3])) | set(range(current[2], current[3]))
            assert embargo_start - 1 in selected
            assert embargo_start not in selected
            assert embargo_stop - 1 not in selected
            assert embargo_stop in selected


def _dates(n):
    # Supplied session labels span the NY DST weekend; weekends and gaps count
    # as zero sessions. This is not a claim to validate an official calendar.
    days = [date(2024, 3, 7), date(2024, 3, 8), date(2024, 3, 11)]
    while len(days) < n:
        day = days[-1] + timedelta(days=1)
        if day.weekday() < 5:
            days.append(day)
        else:
            days.append(day + timedelta(days=7 - day.weekday()))
    return days


def _evaluate(kind, days, plan, *, changed=None, **overrides):
    n = len(days)
    changed = {} if changed is None else changed
    values = [changed.get(i, .002 + .001 * (i % 3)) for i in range(n)]
    rows = list(zip(days, values))
    cash = list(zip(days, [0.0001] * n))
    common = dict(plan=plan, cost_bps_per_side=5., synthetic=True, cash_returns=cash)
    common.update(overrides)
    turnover = [changed.get(i, .01) for i in range(n)]
    if kind == "single":
        return single.evaluate_fractional_kelly_walk_forward(
            strategy_returns=rows, benchmark_returns=rows, r8_returns=rows,
            strategy_turnover=turnover, r8_turnover=turnover, **common)
    return portfolio.evaluate_portfolio_kelly_walk_forward(
        asset_returns=[("A", rows), ("B", rows)],
        benchmark_returns=[("IA", rows), ("IB", rows)],
        asset_turnover={"A": turnover, "B": turnover}, **common)


@pytest.mark.parametrize("kind", ["single", "portfolio"])
@pytest.mark.parametrize("embargo", [0, 2])
def test_evaluators_use_only_selected_sessions_in_every_comparator(stats, kind, embargo):
    plan = WalkForwardPlan(40, 5, 3, label_horizon_sessions=3, embargo_sessions=embargo)
    days = _dates(62)
    folds = plan.folds(len(days))
    selected = [i for _, _, start, stop in folds for i in range(start, stop)]
    result = _evaluate(kind, days, plan)
    assert result["status"] == "COMPUTED"
    assert result["test_window"]["sessions"] == len(selected)
    assert result["coverage"]["tested_sessions"] == len(selected)
    for comp in result["comparators"].values():
        for tier in comp["tiers"].values():
            assert tier["status"] == "COMPUTED"
            assert tier["observations"] == len(selected)
    # QPK charges the entry cost before the session return. The benchmark
    # pays its one entry and uses no embargo rows.
    expected = math.prod((1 + .002 + .001 * (i % 3)) * (1 - (.0005 if j == 0 else 0))
                         for j, i in enumerate(selected)) - 1
    benchmark = "benchmark" if kind == "single" else "benchmark.IA"
    assert result["comparators"][benchmark]["tiers"]["1"]["total_return"] == pytest.approx(expected)


@pytest.mark.parametrize("kind", ["single", "portfolio"])
def test_embargo_values_do_not_enter_evaluation_but_boundary_outside_does(stats, kind):
    # With only two folds, mutate the final embargo session: it cannot be
    # legitimate training in either fold. Later folds may train on old gaps.
    plan = WalkForwardPlan(40, 5, 3, label_horizon_sessions=3, embargo_sessions=2)
    days = _dates(55)
    first, second = plan.folds(len(days))
    assert first[3] + 2 == second[2]
    base = _evaluate(kind, days, plan)
    in_gap = _evaluate(kind, days, plan, changed={first[3]: .4, second[2] - 1: .5})
    assert base["status"] == in_gap["status"] == "COMPUTED"
    assert base["comparators"] == in_gap["comparators"]
    outside = _evaluate(kind, days, plan, changed={second[2]: .5})
    benchmark = "benchmark" if kind == "single" else "benchmark.IA"
    assert base["comparators"][benchmark] != outside["comparators"][benchmark]


@pytest.mark.parametrize("kind", ["single", "portfolio"])
@pytest.mark.parametrize("bad", ["duplicate", "descending", "naive_datetime", "aware_datetime"])
def test_session_labels_reject_duplicates_unsorted_and_datetime(kind, bad):
    days = _dates(55)
    if bad == "duplicate":
        days[2] = days[1]
    elif bad == "descending":
        days[1], days[2] = days[2], days[1]
    else:
        tz = timezone.utc if bad == "aware_datetime" else None
        days = [datetime.combine(day, datetime.min.time(), tz) for day in days]
    with pytest.raises(ValueError, match="(DATES_NOT_STRICTLY_INCREASING|RETURNS.*INVALID)"):
        _evaluate(kind, days, WalkForwardPlan(40, 5, 3))


@pytest.mark.parametrize("kind", ["single", "portfolio"])
@pytest.mark.parametrize("overrides", [{"synthetic": False}, {"real_data": object()}])
def test_v1_real_data_authority_does_not_transfer_to_v2(kind, overrides):
    with pytest.raises(ValueError, match="^NEED_USER_V2_REAL_DATA_PROTOCOL_NOT_REGISTERED$"):
        _evaluate(kind, _dates(55), WalkForwardPlan(40, 5, 3), **overrides)


def test_v2_cannot_start_an_actual_net_candidate():
    with pytest.raises(ValueError, match="^V2_NETTING_NOT_SUPPORTED$"):
        _evaluate("portfolio", _dates(55), WalkForwardPlan(40, 5, 3), netting_candidate={})


@pytest.mark.parametrize("kind", ["single", "portfolio"])
def test_versions_are_explicit_and_zero_embargo_preserves_v1_metrics(stats, kind):
    from us_equity_strategies.research import fractional_kelly_walk_forward as old_single
    from us_equity_strategies.research import portfolio_kelly_walk_forward as old_portfolio

    days = _dates(62)
    plan = WalkForwardPlan(40, 5, 3)
    result = _evaluate(kind, days, plan)
    rows = list(zip(days, [.002 + .001 * (i % 3) for i in range(len(days))]))
    common = dict(plan=plan, cost_bps_per_side=5., cash_returns=list(zip(days, [.0001] * len(days))))
    if kind == "single":
        legacy = old_single.evaluate_fractional_kelly_walk_forward(
            strategy_returns=rows, benchmark_returns=rows, r8_returns=rows,
            strategy_turnover=[.01] * len(days), r8_turnover=[.01] * len(days), **common)
    else:
        legacy = old_portfolio.evaluate_portfolio_kelly_walk_forward(
            asset_returns=[("A", rows), ("B", rows)], benchmark_returns=[("IA", rows), ("IB", rows)],
            asset_turnover={"A": [.01] * len(days), "B": [.01] * len(days)}, **common)
    assert result["evaluator_version"].endswith("_v2")
    assert legacy["evaluator_version"].endswith("_v1")
    assert result["comparators"] == legacy["comparators"]
    assert result["folds"] == legacy["folds"]
    assert result["trial_log_sha256"] != legacy["trial_log_sha256"]


@pytest.mark.parametrize("kind", ["single", "portfolio"])
def test_v2_report_has_version_protocol_and_valid_schema(stats, kind):
    import jsonschema

    context = {"report_id": "v2-synthetic", "candidate_id": "artificial",
               "source_revision": "0" * 40, "created_at": "2026-10-22T00:00:00Z",
               "manifest_sha256": "0" * 64, "source": "synthetic", "license": "synthetic",
               "signal_cutoff": "prior_close", "execution": "next_close",
               "benchmark_name": "artificial", "benchmark_rationale": "synthetic"}
    plan = WalkForwardPlan(40, 5, 3, label_horizon_sessions=3, embargo_sessions=2)
    result = _evaluate(kind, _dates(62), plan, report_context=context, bootstrap_seed=7)
    report = result["backtest_report"]
    jsonschema.validate(report, stats.load_backtest_report_schema())
    assert any(result["evaluator_version"] in note and "embargo_sessions=2" in note
               for note in report["limitations"])
    assert report["trials"]["trial_log_sha256"] == result["trial_log_sha256"]
    assert result["plan"]["label_horizon_sessions"] == 3
    assert result["plan"]["embargo_sessions"] == 2
    assert result["coverage"]["excluded_between_test_sessions"] == 4
    assert result["no_order"] is True and result["live_ready"] is False
    assert result["budget_effect"] == "NONE"
    assert "limitations" not in context


@pytest.mark.parametrize("kind", ["single", "portfolio"])
def test_missing_stats_has_explicit_v2_identity_and_no_numbers(monkeypatch, kind):
    target = single if kind == "single" else portfolio.rs04
    monkeypatch.setattr(target, "_stats_module", lambda: None)
    result = _evaluate(kind, _dates(55), WalkForwardPlan(40, 5, 3, embargo_sessions=2))
    assert result["status"] == "UNCOMPUTABLE"
    assert result["evaluator_version"].endswith("_v2")
    assert result["cv_contract"]["embargo_sessions"] == 2
    assert "comparators" not in result


def test_m2_source_binding_matches_original_frozen_bytes():
    import hashlib
    import json
    from pathlib import Path
    from us_equity_strategies.research.partial_rebalance import CORE_ONLY_SOURCE_SHA256

    root = Path(__file__).resolve().parents[2]
    protocol = json.loads((root / "docs/research/protocols/R3_VS_R0_CHALLENGER_20261022.json").read_text())
    expected = protocol["version_binding"]["core_only_frozen_constants"]["CORE_ONLY_SOURCE_SHA256"]
    assert expected == CORE_ONLY_SOURCE_SHA256
    assert expected["research/fractional_kelly_walk_forward.py"] == (
        "7e5892d59fd9c1243a5433b4c87b616d0805ed14130d24d65850bbfc8734ec74")
    for relative, digest in expected.items():
        assert hashlib.sha256((root / "src/us_equity_strategies" / relative).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("embargo", [0, 2])
def test_non_parked_all_comparators_match_independent_arithmetic(stats, embargo):
    n = 72
    dates = [date(2024, 1, 1) + timedelta(days=i * 2) for i in range(n)]
    assets = {"A": [.04 + .2 * math.sin(i * 1.7) for i in range(n)],
              "B": [.03 + .17 * math.cos(i * 1.31) for i in range(n)]}
    cash = [.0001 * (i % 7) for i in range(n)]
    bench = {"X": [.003 * math.sin(i) for i in range(n)],
             "Y": [.004 * math.cos(i) for i in range(n)]}
    turnover = {"A": [.001 * i for i in range(n)], "B": [.002 * (n - i) for i in range(n)]}
    r8 = [.002 * (i % 9) - .008 for i in range(n)]
    r8_turnover = [.003 * (i % 11) for i in range(n)]
    def rows(values):
        return list(zip(dates, values))


    def oracle(schedule, values, indices, internal=None):
        held, gross, traded = {}, [], []
        for target, i in zip(schedule, indices, strict=True):
            t = sum(abs(target.get(a, 0) - held.get(a, 0)) for a in set(target) | set(held))
            t += sum(w * internal[a][i] for a, w in target.items()) if internal else 0
            r = sum(w * values[a][i] for a, w in target.items()) + (1 - sum(target.values())) * cash[i]
            gross.append(r)
            traded.append(t)
            held = {a: w * (1 + values[a][i]) / (1 + r) for a, w in target.items()}
        return gross, traded


    def check(result, paths, count):
        assert result["test_window"]["sessions"] == result["coverage"]["tested_sessions"] == count
        assert set(paths) == set(result["comparators"])
        for name, (gross, traded) in paths.items():
            for tier in (1, 2, 3):
                metric = result["comparators"][name]["tiers"][str(tier)]
                expected = math.prod((1 + r) * (1 - t * .0005 * tier) for r, t in zip(gross, traded, strict=True)) - 1
                assert math.isclose(metric["total_return"], expected, rel_tol=1e-10, abs_tol=1e-12), (name, tier)
                assert math.isclose(metric["turnover_total"], sum(traded), rel_tol=1e-10, abs_tol=1e-12), name
                assert metric["observations"] == count


    plan = WalkForwardPlan(40, 5, 3, label_horizon_sessions=3, embargo_sessions=embargo)
    folds = plan.folds(n)
    indices = [i for _, _, start, end in folds for i in range(start, end)]
    result = single.evaluate_fractional_kelly_walk_forward(
        strategy_returns=rows(assets["A"]), benchmark_returns=rows(bench["X"]),
        cash_returns=rows(cash), strategy_turnover=turnover["A"], r8_returns=rows(r8),
        r8_turnover=r8_turnover, plan=plan, cost_bps_per_side=5)
    paths = {}
    for c in (.25, .5):
        assert all(f["fits"][format(c, "g")]["status"] == "COMPUTED" for f in result["folds"])
        schedule = [{"A": f["fits"][format(c, "g")]["exposure"]} for f in result["folds"] for _ in range(5)]
        paths[f"kelly_c{format(c, 'g')}"] = oracle(schedule, assets, indices, turnover)
    for name, weight in (("full_exposure", 1), ("fixed_50pct", .5)):
        paths[name] = oracle([{"A": weight}] * len(indices), assets, indices, turnover)
    paths["benchmark"] = ([bench["X"][i] for i in indices], [1] + [0] * (len(indices) - 1))
    paths["r8"] = ([r8[i] for i in indices], [r8_turnover[i] for i in indices])
    check(result, paths, len(indices))
    result = portfolio.evaluate_portfolio_kelly_walk_forward(
        asset_returns=[(a, rows(v)) for a, v in assets.items()], cash_returns=rows(cash),
        benchmark_returns=[(a, rows(v)) for a, v in bench.items()], asset_turnover=turnover,
        plan=plan, cost_bps_per_side=5)
    paths = {}
    for c in (.25, .5):
        key = format(c, "g")
        assert all(f["portfolio_fit"]["status"] == "COMPUTED" for f in result["folds"])
        for method in portfolio.METHODS:
            schedule = [f["portfolio_fit"]["weights"][key][method] for f in result["folds"] for _ in range(5)]
            paths[f"portfolio_kelly.{method}.c{key}"] = oracle(schedule, assets, indices, turnover)
        for a in assets:
            schedule = [{a: f["single_kelly_exposure"][a][key]} for f in result["folds"] for _ in range(5)]
            paths[f"single_kelly.{a}.c{key}"] = oracle(schedule, assets, indices, turnover)
    for name, weights in (("equal_weight_50_50", {"A": .5, "B": .5}),
                          ("equal_weight_25_25", {"A": .25, "B": .25})):
        paths[name] = oracle([weights] * len(indices), assets, indices, turnover)
    for a in assets:
        for prefix, weight in (("fixed_50", .5), ("full", 1)):
            paths[f"{prefix}.{a}"] = oracle([{a: weight}] * len(indices), assets, indices, turnover)
    for a, v in bench.items():
        paths[f"benchmark.{a}"] = ([v[i] for i in indices], [1] + [0] * (len(indices) - 1))
    paths["benchmark.X_Y_50_50"] = oracle([{"X": .5, "Y": .5}] * len(indices), bench, indices)
    check(result, paths, len(indices))
