"""Synthetic acceptance of the opt-in, cash-funded close-fill net ledger."""
import importlib.util
import inspect
import json
import math
import os
import random
from datetime import date, timedelta

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


def spec(**kwargs):
    from quant_platform_kit.research_stats.security_netting import SecurityNettingSpec
    return SecurityNettingSpec(bps_per_side={"A": 100., "BOXX": 20.}, **kwargs)


def session(i=1, *, f=.5, weights=None, returns=None, cash_return=0.):
    day = date(2026, 1, 1) + timedelta(days=i)
    return NettingSession(day, day - timedelta(days=1), f,
                          {"A": .8} if weights is None else weights,
                          {"A": 0., "BOXX": 0.} if returns is None else returns, cash_return)


def run(rows, **kwargs):
    args = dict(initial_nav=100., initial_f=0., initial_inner_weights={},
                spec=spec(), fee_cash_role="inner_residual_cash")
    return run_offline_security_netting(rows, **(args | kwargs))


def reconcile(result):
    nav = result["initial_nav"]
    for row in result["ledger"]:
        assert row["nav_start"] == pytest.approx(nav)
        pnl = sum(row["security_pnl"].values()) + row["outer_cash_pnl"]
        assert row["nav_pre_trade"] == pytest.approx(nav + pnl)
        assert row["fees"] == pytest.approx(sum(row["fee_by_security"].values()))
        assert row["nav_end"] == pytest.approx(nav + pnl - row["fees"])
        assert row["nav_end"] == pytest.approx(
            sum(row["security_end"].values()) + sum(row["virtual_cash_end"].values()))
        for symbol in set(row["security_pre"]) | set(row["security_end"]):
            assert row["security_end"].get(symbol, 0.) == pytest.approx(
                row["security_pre"].get(symbol, 0.) + row["net_trades"].get(symbol, 0.))
        pre_cash = row["nav_pre_trade"] - sum(row["security_pre"].values())
        assert sum(row["net_trades"].values()) + sum(row["virtual_cash_end"].values()) - pre_cash + row["fees"] == pytest.approx(0., abs=1e-10)
        assert row["net_return"] == pytest.approx(row["nav_end"] / nav - 1)
        assert row["gross_return"] - row["net_return"] == pytest.approx(row["fees"] / nav)
        assert max(abs(v) for v in row["reconciliation_errors"].values()) < 1e-10
        assert min(row["virtual_cash_end"].values(), default=0.) >= 0.
        assert row["turnover_columns_summable"] is False
        nav = row["nav_end"]
    assert result["final_nav"] == pytest.approx(nav)
    assert result["initial_nav"] * math.prod(1 + r for r in result["net_returns"]) == pytest.approx(nav)
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('returns,durations,reason', [
    ((-.5, 0., 1.), (3, 2), None),
    ((-.5,), (None, None), 'right_censored'),
    ((0., .5), (None, None), 'no_drawdown'),
])
def test_max_drawdown_recovery_native_report(returns, durations, reason):
    rows = [session(i, f=1., weights={'A': 1.}, returns={'A': r, 'BOXX': 0.})
            for i, r in enumerate(returns, 1)]
    from quant_platform_kit.research_stats.security_netting import SecurityNettingSpec
    options = dict(initial_f=1., initial_inner_weights={'A': 1.},
                   spec=SecurityNettingSpec(bps_per_side={'A': 0., 'BOXX': 0.}),
                   execution_timing='next_close', report_periods_per_year=252)
    baseline = run(rows, **options)
    explicit_false = run(rows, include_max_drawdown_recovery=False, **options)
    assert json.dumps(baseline, sort_keys=True) == json.dumps(explicit_false, sort_keys=True)
    result = run(rows, include_max_drawdown_recovery=True, **options)
    metrics = {m['name']: m for m in result['unified_net_report']['metrics']}
    for suffix, duration in zip(('peak', 'trough'), durations, strict=True):
        m = metrics[f'max_drawdown_{suffix}_to_recovery_observations']
        assert m['value'] == duration
        assert m['reason_code'] == reason
        assert m['details']['unit'] == 'return_observations'
        assert m['details']['right_censored'] is (reason == 'right_censored')
        if reason == 'right_censored':
            assert m['details']['recovery_index'] is None
            assert m['details']['observed_trough_to_end_lower_bound'] == 0
    assert result['unified_net_report_basis']['include_max_drawdown_recovery'] is True
    assert {k: v for k, v in result.items() if not k.startswith('unified_net_report')} == {
        k: v for k, v in baseline.items() if not k.startswith('unified_net_report')}
    assert result['unified_net_report']['metrics'][:-2] == baseline['unified_net_report']['metrics']
    restored = json.loads(json.dumps(result))
    assert json.dumps(restored, sort_keys=True) == json.dumps(result, sort_keys=True)
    restored_metrics = {m['name']: m for m in restored['unified_net_report']['metrics']}
    for suffix in ('peak', 'trough'):
        name = f'max_drawdown_{suffix}_to_recovery_observations'
        assert restored_metrics[name] == metrics[name]


@pytest.mark.parametrize('flag', [None, 0, 1, 'yes'])
def test_max_drawdown_recovery_flag_is_exact_bool(flag):
    with pytest.raises(ValueError, match='RECOVERY_OPT_IN_INVALID'):
        run([session()], include_max_drawdown_recovery=flag)


def test_explicit_rf_changes_statistics_never_book_or_fees():
    rows = [session(i) for i in range(1, 36)]
    legacy = run(rows, execution_timing="next_close", report_periods_per_year=252)
    options = dict(execution_timing="next_close", report_periods_per_year=252,
                   report_sharpe_basis="net_wealth_excess_sharpe_v2",
                   report_risk_free_returns=[.001] * 35,
                   report_risk_free_source="synthetic-period-rf", report_calendar_id="synthetic")
    if not _explicit_report_basis_supported():
        with pytest.raises(ValueError, match="QPK_EXPLICIT_SHARPE_BASIS_REQUIRED"):
            run(rows, **options)
        return  # only the legacy-QPK rejection path was covered
    explicit = run(rows, **options)
    reconcile(explicit)
    for name in ("ledger", "net_returns", "gross_returns", "final_nav"):
        assert explicit[name] == legacy[name]
    lm = {m["name"]: m for m in legacy["unified_net_report"]["metrics"]}
    em = {m["name"]: m for m in explicit["unified_net_report"]["metrics"]}
    assert em["sharpe_per_period"]["value"] != lm["sharpe_per_period"]["value"]
    for name in ("net_cagr", "max_drawdown", "cost_log_drag_per_period"):
        assert em[name]["value"] == lm[name]["value"]
    assert explicit["unified_net_report_basis"]["costs_deducted_again"] is False


def test_missing_rf_preserves_book_and_marks_inference_uncomputable():
    rows = [session(i) for i in range(1, 36)]
    options = dict(execution_timing="next_close", report_periods_per_year=252,
                   report_sharpe_basis="net_wealth_excess_sharpe_v2", report_calendar_id="synthetic")
    if not _explicit_report_basis_supported():
        with pytest.raises(ValueError, match="QPK_EXPLICIT_SHARPE_BASIS_REQUIRED"):
            run(rows, **options)
        return  # not a successful v2 report on an incapable dependency
    result = run(rows, **options)
    reconcile(result)
    metrics = {m["name"]: m for m in result["unified_net_report"]["metrics"]}
    assert metrics["sharpe_per_period"]["value"] is None
    assert metrics["sharpe_per_period"]["reason_code"] == "missing_risk_free_returns"
    assert metrics["net_cagr"]["status"] == "COMPUTED"


def _explicit_report_basis_supported():
    from quant_platform_kit.research_stats.unified_report import build_unified_net_report
    supported = "sharpe_basis" in inspect.signature(build_unified_net_report).parameters
    if os.environ.get("UES_REQUIRE_SECURITY_NETTING") == "1" and not supported:
        pytest.fail("required netting overlay lacks explicit Sharpe basis; do not skip v2 acceptance")
    return supported


def test_cash_funded_hand_math_and_fee_feedback():
    result = run([session(), session(2, returns={"A": .1, "BOXX": 0.})])
    first, second = result["ledger"]
    assert first["net_trades"] == {"A": 40.}
    assert first["fees"] == pytest.approx(.4)
    assert first["virtual_cash_end"] == pytest.approx({"inner_residual_cash": 9.6, "outer_cash_leg": 50.})
    assert first["nav_end"] == pytest.approx(99.6)
    assert second["nav_pre_trade"] == pytest.approx(103.6)
    assert second["net_trades"]["A"] == pytest.approx(-2.56)
    assert second["fees"] == pytest.approx(.0256)
    assert second["nav_end"] == pytest.approx(103.5744)
    assert result["no_order"] is True and result["live_ready"] is False
    reconcile(result)


def test_opposite_inner_outer_changes_net_before_charging():
    result = run([session(f=.4, weights={"A": .75})], initial_f=.8,
                 initial_inner_weights={"A": .25})
    row = result["ledger"][0]
    assert row["inner_unnetted_turnover"] == pytest.approx(.4)
    assert row["outer_unnetted_turnover"] == pytest.approx(.3)
    assert row["net_security_turnover"] == pytest.approx(.1)
    assert row["fees"] == pytest.approx(.1)
    reconcile(result)


@pytest.mark.parametrize("role", ["inner_residual_cash", "a2_zero_return_cash"])
def test_f_one_matches_inner_turnover_and_cost_model(role):
    from quant_platform_kit.research_stats.execution_models import CostModel
    result = run([session(f=1., weights={"A": .6})], initial_f=1.,
                 initial_inner_weights={"A": .2}, spec=spec(inner_residual_role=role), fee_cash_role=role)
    row = result["ledger"][0]
    assert row["net_security_turnover"] == pytest.approx(.4)
    assert row["inner_unnetted_turnover"] == pytest.approx(.4)
    assert row["outer_unnetted_turnover"] == 0.
    assert row["fees"] / 100 == pytest.approx(CostModel(commission_bps=100.).fraction(.4))
    assert set(row["virtual_cash_end"]) == {role, "outer_cash_leg"}
    reconcile(result)


@pytest.mark.parametrize("mode", ["separate", "merge_into_security"])
def test_boxx_security_and_outer_cash_roles(mode):
    model = spec(outer_cash_mode=mode, outer_cash_symbol="BOXX" if mode == "merge_into_security" else None,
                 cash_like_symbols=("BOXX",))
    result = run([session(weights={"A": .4, "BOXX": .2})], spec=model)
    row = result["ledger"][0]
    if mode == "separate":
        assert row["net_trades"] == pytest.approx({"A": 20., "BOXX": 10.})
        assert row["fees"] == pytest.approx(.22)
        assert row["virtual_cash_end"]["outer_cash_leg"] == 50.
    else:
        # The existing outer BOXX 100 is netted against the combined BOXX target 60.
        assert row["net_trades"] == pytest.approx({"A": 20., "BOXX": -40.})
        assert row["fees"] == pytest.approx(.28)
        assert "outer_cash_leg" not in row["virtual_cash_end"]
    assert row["role_weights"]["inner_cash_like_security"] == pytest.approx(.1)
    reconcile(result)


def test_separate_outer_cash_earns_and_can_fund_fees():
    result = run([session(f=.5, weights={"A": 1.}, cash_return=.01)], fee_cash_role="outer_cash_leg")
    row = result["ledger"][0]
    assert row["outer_cash_pnl"] == pytest.approx(1.)
    assert row["security_end"]["A"] == pytest.approx(50.5)
    assert row["virtual_cash_end"]["outer_cash_leg"] == pytest.approx(49.995)
    reconcile(result)


def test_merged_outer_boxx_return_is_counted_once():
    model = spec(outer_cash_mode="merge_into_security", outer_cash_symbol="BOXX", cash_like_symbols=("BOXX",))
    result = run([session(weights={"A": .4, "BOXX": .2}),
                  session(2, weights={"A": .4, "BOXX": .2}, returns={"A": 0., "BOXX": .1})], spec=model)
    row = result["ledger"][1]
    assert row["security_pnl"]["BOXX"] == pytest.approx(6.)
    assert row["outer_cash_pnl"] == 0.
    reconcile(result)


def test_terminal_exit_cost_and_no_phantom_final_liquidation():
    result = run([session(), session(2, f=0., weights={})], fee_cash_role="outer_cash_leg")
    assert result["ledger"][-1]["fees"] == pytest.approx(.4)
    assert result["ledger"][-1]["security_end"] == {}
    assert len(result["ledger"]) == 2
    reconcile(result)


def test_prefix_is_unchanged_by_future_returns_or_targets():
    prefix = [session(), session(2)]
    assert run(prefix)["ledger"] == run(prefix + [session(3, f=.7, returns={"A": 3., "BOXX": -.2})])["ledger"][:2]


def test_seeded_sessions_reconcile_with_fee_feedback():
    rng = random.Random(20261016)
    rows = [session(i, f=rng.uniform(.2, .8), weights={"A": rng.uniform(.1, .5), "BOXX": .1},
                    returns={"A": rng.uniform(-.2, .2), "BOXX": rng.uniform(-.01, .01)}) for i in range(1, 101)]
    for mode in ("separate", "merge_into_security"):
        model = spec(outer_cash_mode=mode, outer_cash_symbol="BOXX" if mode == "merge_into_security" else None,
                     cash_like_symbols=("BOXX",), multiplier=2.)
        reconcile(run(rows, spec=model))


@pytest.mark.parametrize("kwargs,code", [
    ({"fee_cash_role": "inner_cash_like_security"}, "FEE_CASH_ROLE_INVALID"),
    ({"fee_cash_role": "outer_cash_leg", "merged_spec": True}, "FEE_CASH_ROLE_INVALID"),
    ({"initial_nav": 0.}, "INITIAL_NAV_INVALID"),
    ({"initial_f": float("nan")}, "OUTER_FRACTION_INVALID"),
    ({"initial_inner_weights": {"A": 2.}}, "INNER_WEIGHTS_INVALID"),
    ({"initial_inner_weights": {"A": True}}, "INNER_WEIGHTS_INVALID"),
])
def test_invalid_contracts(kwargs, code):
    kwargs = dict(kwargs)
    if kwargs.pop("merged_spec", False):
        kwargs["spec"] = spec(outer_cash_mode="merge_into_security", outer_cash_symbol="BOXX")
    with pytest.raises(ValueError, match=code):
        run([session()], **kwargs)


@pytest.mark.parametrize("rows,code", [
    ([], "SESSIONS_REQUIRED"),
    ([session(), session()], "SESSION_ORDER_INVALID"),
    ([NettingSession(date(2026, 1, 2), date(2026, 1, 2), .5, {"A": .8}, {}, 0.)], "DECISION_NOT_PRIOR"),
    ([session(f=1., weights={"A": 1.})], "FEE_CASH_INSUFFICIENT"),
    ([session(returns={"A": float("nan")})], "RETURN_INVALID"),
    ([session(cash_return=-1.)], "RETURN_INVALID"),
])
def test_invalid_session_inputs(rows, code):
    with pytest.raises(ValueError, match=code):
        run(rows)


def test_missing_held_return_and_missing_rate_are_rejected():
    with pytest.raises(ValueError, match="RETURN_MISSING:A"):
        run([session(returns={})], initial_f=1., initial_inner_weights={"A": .5})
    with pytest.raises(ValueError, match="BPS_MISSING:UNKNOWN"):
        run([session(weights={"UNKNOWN": .5})])


def test_merged_outer_rejects_independent_cash_return():
    with pytest.raises(ValueError, match="MERGED_OUTER_RETURN_MUST_USE_SECURITY"):
        run([session(cash_return=.01)], spec=spec(outer_cash_mode="merge_into_security", outer_cash_symbol="BOXX"))


def test_no_trade_is_not_charged_and_empty_inner_book_can_drift():
    result = run([session(f=0., weights={}), session(2, f=0., weights={}, cash_return=.1)],
                 fee_cash_role="outer_cash_leg")
    assert result["final_nav"] == pytest.approx(110.)
    assert all(row["fees"] == 0. for row in result["ledger"])
    reconcile(result)


@pytest.mark.parametrize("symbols", [("BOXX", "BOXX"), ("",), (True,)])
def test_invalid_cash_like_labels(symbols):
    with pytest.raises(ValueError, match="CASH_LIKE_SYMBOLS_INVALID"):
        run([session()], spec=spec(cash_like_symbols=symbols))


def test_mutated_fee_mapping_cannot_credit_cash():
    model = spec()
    # QPK freezes attribute assignment, not the caller-owned rate dictionary.
    model.bps_per_side["A"] = -100.
    with pytest.raises(ValueError, match="BPS_INVALID"):
        run([session()], spec=model)


COST_POLICY = "RESEARCH_LONG_ONLY_WEIGHTED_AVERAGE_BUY_COST_CAPITALIZED_V1"


def cost_inputs(rows):
    return dict(policy_id=COST_POLICY, synthetic_book_id="synthetic-cost-book",
        opening_book={"session": "2026-01-01", "currency": "USD", "cash": 800.,
                      "positions": {"A": {"quantity": 20., "total_basis": 160., "mark": 8.}}},
        fill_marks={row.session.isoformat(): {"A": p} for row, p in zip(rows, [10., 12., 11., 9., 10.])})


def cost_path():
    # No second engine: explicit economic targets, original netter determines all fills/fees.
    return [session(1, f=1., weights={"A": .3}, returns={"A": .25}),
            session(2, f=1., weights={"A": .2}, returns={"A": .2}),
            session(3, f=1., weights={"A": .1}, returns={"A": 11./12.-1}),
            session(4, f=1., weights={}, returns={"A": 9./11.-1}),
            session(5, f=1., weights={"A": .1}, returns={"A": 10./9.-1})]


def cost_run(rows, inputs):
    return run(rows, initial_nav=960., initial_f=1., initial_inner_weights={"A": 1./6.},
               research_cost_inputs=inputs)


def test_cost_facts_native_buy_partial_full_close_reopen_and_default_compatibility(tmp_path):
    rows = cost_path(); inputs = cost_inputs(rows)
    result = cost_run(rows, inputs)
    receipt = result.pop("research_cost_receipt")
    legacy = run(rows, initial_nav=960., initial_f=1., initial_inner_weights={"A": 1./6.})
    assert result == legacy
    assert receipt["status"] == "PASS_SCOPED_SYNTHETIC"
    assert receipt["source_label"] == "SYNTHETIC_NATIVE_NETTER_V1"
    assert receipt["policy_id"] == COST_POLICY
    basis, quantity, realized = 160., 20., 0.
    for day, native, price in zip(receipt["days"], result["ledger"], [10., 12., 11., 9., 10.]):
        trade = native["net_trades"].get("A", 0.); fee = native["fee_by_security"].get("A", 0.)
        if trade > 0:
            basis += trade + fee
        elif trade < 0:
            sold = -trade/price; relief = basis*sold/quantity
            realized += -trade-relief-fee; basis -= relief
        quantity = native["security_end"].get("A", 0.)/price
        assert day["positions"]["A"]["quantity"] == pytest.approx(quantity)
        assert day["positions"]["A"]["total_basis"] == pytest.approx(basis, abs=1e-9)
        assert day["realized_net_cumulative"] == pytest.approx(realized)
        assert realized + day["unrealized"] == pytest.approx(native["nav_end"]-960.)
    assert receipt["days"][3]["positions"]["A"]["total_basis"] == pytest.approx(0.)
    assert cost_run(rows, inputs)["research_cost_receipt"] == receipt
    saved = tmp_path / "research-cost-receipt.json"
    saved.write_text(json.dumps(receipt, sort_keys=True))
    assert json.loads(saved.read_text()) == receipt
    reconcile(result)


def test_cost_facts_native_prefix_future_marks_do_not_change_past_receipt():
    rows = cost_path()
    full = cost_run(rows, cost_inputs(rows))["research_cost_receipt"]
    prefix = cost_run(rows[:2], cost_inputs(rows[:2]))["research_cost_receipt"]
    assert full["days"][:2] == prefix["days"]
    changed = rows[:2] + [session(3, f=1., weights={}, returns={"A": -.5})]
    inputs = cost_inputs(changed); inputs["fill_marks"][changed[2].session.isoformat()]["A"] = 6.
    assert cost_run(changed, inputs)["research_cost_receipt"]["days"][:2] == prefix["days"]


@pytest.mark.parametrize("mutation,code", [
    ("missing_opening", "COST_OPENING_REQUIRED"), ("missing_basis", "COST_OPENING_REQUIRED"),
    ("missing_marks", "COST_MARKS_REQUIRED"), ("wrong_mark", "COST_MARK_BOOK_MISMATCH"),
    ("wrong_policy", "COST_POLICY_REQUIRED"), ("corrections", "COST_INPUT_KEYS"),
    ("late_fees", "COST_INPUT_KEYS"), ("wrong_currency", "COST_CURRENCY_REQUIRED"),
])
def test_cost_facts_native_missing_and_unsupported_inputs_fail_closed(mutation, code):
    rows = cost_path(); inputs = cost_inputs(rows)
    if mutation == "missing_opening": inputs.pop("opening_book")
    elif mutation == "missing_basis": inputs["opening_book"]["positions"]["A"].pop("total_basis")
    elif mutation == "missing_marks": inputs["fill_marks"].pop(rows[0].session.isoformat())
    elif mutation == "wrong_mark": inputs["fill_marks"][rows[0].session.isoformat()]["A"] = 999.
    elif mutation == "wrong_policy": inputs["policy_id"] = "BROKER_TAX_COST"
    elif mutation == "wrong_currency": inputs["opening_book"]["currency"] = ""
    else: inputs[mutation] = [{"id": "unsupported"}]
    with pytest.raises(ValueError, match=code): cost_run(rows, inputs)


def test_cost_facts_explicit_zero_fees_zero_opening_and_multiple_fill_identities():
    rows = [session(1, f=1., weights={"A": .2, "BOXX": .2}), session(2, f=1., weights={})]
    inputs = dict(policy_id=COST_POLICY, synthetic_book_id="zero-book",
        opening_book={"session":"2026-01-01", "currency":"USD", "cash":100., "positions":{}},
        fill_marks={row.session.isoformat(): {"A": 10., "BOXX": 20.} for row in rows})
    from quant_platform_kit.research_stats.security_netting import SecurityNettingSpec
    result = run(rows, spec=SecurityNettingSpec(bps_per_side={"A":0., "BOXX":0.}),
                 research_cost_inputs=inputs)
    receipt = result["research_cost_receipt"]
    assert receipt["status"] == "PASS_SCOPED_SYNTHETIC"
    assert len(receipt["days"][0]["accepted_execution_ids"]) == 2
    assert sum(row["fees"] for row in result["ledger"]) == 0.
    assert receipt["days"][-1]["realized_net_cumulative"] == pytest.approx(0.)


def test_cost_facts_unsupported_cash_income_and_currency_mix_cannot_qualify():
    rows = cost_path(); inputs = cost_inputs(rows)
    income = [session(1, f=1., weights={"A": .3}, returns={"A": .25}, cash_return=.01)]
    with pytest.raises(ValueError, match="COST_EVENT_SCOPE_UNSUPPORTED"):
        cost_run(income, cost_inputs(income))
    inputs["opening_book"]["positions"]["A"]["currency"] = "EUR"
    with pytest.raises(ValueError, match="COST_OPENING_REQUIRED"):
        cost_run(rows, inputs)


def test_b5_missing_consumer_entry():
    # An actual requested action with valid flat-book inputs must be accepted.
    # This fails on the baseline at the missing opt-in parameter, not at a fake assertion.
    result = run([session(f=1., weights={'A': .4, 'BOXX': .4})],
                 initial_f=1., initial_inner_weights={'A': .2, 'BOXX': .4},
                 execution_timing='next_close', report_periods_per_year=252,
                 spec=spec(cash_like_symbols=('BOXX',)), rs04_research_policy=b5_policy())
    assert result['ledger'][0]['inner_target'] == pytest.approx({'A': .3, 'BOXX': .4})


def b5_policy():
    from us_equity_strategies.research.security_netting_runner import (
        DecisionSnapshot, RS04DecisionInput, RS04ResearchPolicy,
    )
    from us_equity_strategies.research.partial_rebalance import HardPolicyContext, SecurityPolicy
    context = HardPolicyContext(
        (SecurityPolicy('A', True, 1., 'A', .9), SecurityPolicy('BOXX', False, 1., 'BOXX', .9)),
        .9, (('A', .9),), (), 'synthetic-original-policy', 'synthetic-classification', True,
        'DECISION_TIME_TARGET', None, 'RISK_BUY_WEIGHT', 'NAV_FRACTION')
    return RS04ResearchPolicy(
        mode='PROPORTIONAL', security_universe=('A', 'BOXX'),
        opening=DecisionSnapshot(date(2026, 1, 1), '2026-01-01T21:00:00+00:00',
                                 100., (('A', 20.), ('BOXX', 40.)), 40., True),
        decisions=(RS04DecisionInput(date(2026, 1, 1), date(2026, 1, 2),
            '2026-01-01T22:00:00+00:00', '2026-01-02T21:00:00+00:00',
            '2026-01-01T21:00:00+00:00', 'synthetic-frozen-full-target',
            (('A', .4), ('BOXX', .4)), context, False),))


def b5_case(*, targets=None, returns=None, mode='PROPORTIONAL', lam=.5, band=.01,
            weights=None, fees=10., context=None, mandatory=False, forced=None):
    from dataclasses import replace
    from us_equity_strategies.research.security_netting_runner import DecisionSnapshot
    base = b5_policy()
    weights = {'A': .4, 'BOXX': .58} if weights is None else weights
    targets = [{'A': .6, 'BOXX': .38}] if targets is None else targets
    returns = [{'A': 0., 'BOXX': 0.} for _ in targets] if returns is None else returns
    rows, inputs = [], []
    for i, (target, ret) in enumerate(zip(targets, returns, strict=True), 1):
        row = session(i, f=1., weights=target, returns=ret)
        rows.append(row)
        inputs.append(replace(base.decisions[0], decision_session=row.decision_session,
            fill_session=row.session, cutoff=row.decision_session.isoformat()+'T22:00:00+00:00',
            fill_completed_at=row.session.isoformat()+'T21:00:00+00:00',
            full_target_available_at=row.decision_session.isoformat()+'T21:00:00+00:00',
            full_target=tuple(target.items()), hard_policy_context=context or base.decisions[0].hard_policy_context,
            mandatory_reduce=mandatory, forced_target=None if forced is None else tuple(forced.items()),
            forced_source='synthetic-original-policy' if mandatory else None,
            forced_reason='original-breaker' if mandatory else None))
    cash = 100. * (1. - sum(weights.values()))
    opening = DecisionSnapshot(date(2026, 1, 1), '2026-01-01T21:00:00+00:00', 100.,
                               tuple((s, 100.*w) for s, w in weights.items()), cash, True)
    policy = replace(base, mode=mode, lambda_value=lam, band=band, opening=opening, decisions=tuple(inputs))
    from quant_platform_kit.research_stats.security_netting import SecurityNettingSpec
    options = dict(initial_nav=100., initial_f=1., initial_inner_weights=weights,
        spec=SecurityNettingSpec(bps_per_side={'A': fees, 'BOXX': fees}, cash_like_symbols=('BOXX',)),
        fee_cash_role='inner_residual_cash', execution_timing='next_close', report_periods_per_year=252,
        rs04_research_policy=policy)
    return rows, options


def test_b5_half_hand_math_future_fill_is_not_half_execution_and_actual_book_recurses():
    from decimal import Decimal as D, localcontext
    rows, options = b5_case(targets=[{'A': .6, 'BOXX': .38}]*2,
                            returns=[{'A': .1, 'BOXX': 0.}, {'A': -.2, 'BOXX': .03}])
    result = run_offline_security_netting(rows, **options)
    first, second = result['ledger']
    # Independent dollar arithmetic: pre=(44,58,2), NAV104; x=(.5,.48).
    assert first['security_end'] == pytest.approx({'A': 52., 'BOXX': 49.92})
    assert first['net_trades'] == pytest.approx({'A': 8., 'BOXX': -8.08})
    assert first['fees'] == pytest.approx(.01608)
    assert first['nav_end'] == pytest.approx(103.98392)
    assert first['virtual_cash_end']['inner_residual_cash'] == pytest.approx(2.06392)
    assert first['net_trades']['A'] != pytest.approx(.5 * (.6*104.-44.))
    with localcontext() as ctx:
        ctx.prec = 60
        nav = D('103.98392')
        x_a = (D(52)/nav + D('.6'))/2
        x_b = (D('49.92')/nav + D('.38'))/2
        pre = D('41.6') + D('51.4176') + D('2.06392')
        trade_a = pre*x_a - D('41.6')
        trade_b = pre*x_b - D('51.4176')
        fee = (abs(trade_a)+abs(trade_b))*D('.001')
        assert second['inner_target'] == pytest.approx({'A': float(x_a), 'BOXX': float(x_b)})
        assert second['net_trades'] == pytest.approx({'A': float(trade_a), 'BOXX': float(trade_b)})
        assert second['nav_end'] == pytest.approx(float(pre-fee))
    snapshot = result['rs04_research']['decisions'][1]['snapshot']
    assert snapshot['security_values'] == first['security_end']
    assert snapshot['nav'] == first['nav_end']
    assert snapshot['cash'] == first['virtual_cash_end']['inner_residual_cash']
    assert second['inner_target']['A'] != pytest.approx(.55, abs=1e-7)
    reconcile(result)


@pytest.mark.parametrize('mode,lam', [('PROPORTIONAL', 0.), ('BAND_HOLD', .5)])
def test_b5_hold_actual_values_no_phantom_fees_quantities(mode, lam):
    targets = [{'A': .405, 'BOXX': .575}, {'A': .4, 'BOXX': .58}]
    rows, options = b5_case(targets=targets,
        returns=[{'A': .02, 'BOXX': .01}, {'A': -.015, 'BOXX': .005}],
        mode=mode, lam=lam, fees=100.)
    result = run_offline_security_netting(rows, **options)
    for row, receipt in zip(result['ledger'], result['rs04_research']['decisions'], strict=True):
        assert receipt['decision']['kind'] == 'HOLD_POSITIONS'
        assert receipt['decision']['target_weights'] is None
        assert row['security_end'] == row['security_pre']
        assert all(t == 0. for t in row['net_trades'].values())
        assert row['fees'] == 0. and row['net_security_turnover'] == 0.
        assert row['virtual_cash_end']['inner_residual_cash'] == options['rs04_research_policy'].opening.cash
    # Fixed initial quantities at price100: .4 and .58, no reset to old weights.
    assert result['ledger'][0]['security_end']['A'] == 40.8
    assert result['ledger'][1]['security_end']['A']/100.47 == pytest.approx(.4)
    assert result['ledger'][1]['security_end']['BOXX']/101.505 == pytest.approx(.58)
    assert result['ledger'][0]['inner_target']['A'] != .4
    reconcile(result)


def test_b5_full_lambda_one_and_none_compatibility():
    from dataclasses import replace
    rows, opts = b5_case(targets=[{'A': .6, 'BOXX': .38}]*2,
                         returns=[{'A': .02, 'BOXX': .01}, {'A': .01, 'BOXX': -.01}])
    full = run_offline_security_netting(rows, **(opts | {'rs04_research_policy': replace(opts['rs04_research_policy'], mode='FULL')}))
    one = run_offline_security_netting(rows, **(opts | {'rs04_research_policy': replace(opts['rs04_research_policy'], lambda_value=1.)}))
    assert full['ledger'] == one['ledger']
    legacy = run_offline_security_netting(rows, **(opts | {'rs04_research_policy': None}))
    omitted = dict(opts); omitted.pop('rs04_research_policy')
    assert json.dumps(legacy, sort_keys=True) == json.dumps(run_offline_security_netting(rows, **omitted), sort_keys=True)
    assert full['ledger'][0]['inner_target'] == rows[0].inner_target


def test_b5_boxx_participates_in_band_even_if_risk_is_unchanged():
    rows, opts = b5_case(mode='BAND_HOLD', targets=[{'A': .4, 'BOXX': .55}])
    result = run_offline_security_netting(rows, **opts)
    assert result['rs04_research']['decisions'][0]['decision']['kind'] == 'TARGET_WEIGHTS'
    assert result['ledger'][0]['net_trades']['BOXX'] == pytest.approx(-3.)
    assert result['ledger'][0]['fees'] == pytest.approx(.003)


@pytest.mark.parametrize('mode,lam,band', [('FULL', .5, .01), ('PROPORTIONAL', 0., .01), ('BAND_HOLD', .5, 1.)])
def test_b5_mandatory_bypasses_throttle_but_not_cap_veto_or_cash(mode, lam, band):
    from dataclasses import replace
    context = b5_policy().decisions[0].hard_policy_context
    context = replace(context, vetoed_symbols=('A',), max_one_way_risk_turnover=0.)
    rows, opts = b5_case(mode=mode, lam=lam, band=band, context=context,
        mandatory=True, forced={'A': 0., 'BOXX': .58})
    result = run_offline_security_netting(rows, **opts)
    assert result['ledger'][0]['inner_target']['A'] == 0.
    assert result['rs04_research']['decisions'][0]['decision']['reason'] == 'MANDATORY_REDUCTION'
    assert result['ledger'][0]['fees'] == pytest.approx(.04)
    # Same mandatory flag cannot grant a veto exemption or a higher symbol cap.
    for forced in ({'A': .1, 'BOXX': .58}, {'A': 0., 'BOXX': .95}):
        bad = replace(opts['rs04_research_policy'].decisions[0], forced_target=tuple(forced.items()))
        with pytest.raises(ValueError, match='HARD_VETO|HARD_CAP_SYMBOL_WEIGHT'):
            run_offline_security_netting(rows, **(opts | {'rs04_research_policy': replace(opts['rs04_research_policy'], decisions=(bad,))}))
    cash_context = replace(context, securities=tuple(replace(s, max_weight=1.) for s in context.securities))
    rows, opts = b5_case(mode=mode, lam=lam, band=band, context=cash_context,
                         mandatory=True, forced={'A': 0., 'BOXX': 1.})
    with pytest.raises(ValueError, match='FEE_CASH_INSUFFICIENT'):
        run_offline_security_netting(rows, **opts)


from collections.abc import Mapping as B5Mapping


class B5UnreadReturns(B5Mapping):
    """Every read/iteration fails until the genuine decision selector finishes."""
    def __init__(self, data):
        self._data = dict(data)
        self.selected = False
        self.reads = 0

    def _check(self):
        if not self.selected:
            raise AssertionError('fill returns read before B5 decision')
        self.reads += 1

    def __iter__(self):
        self._check()
        return iter(self._data)

    def __getitem__(self, key):
        self._check()
        return self._data[key]

    def __len__(self):
        self._check()
        return len(self._data)


def test_b5_causal_order_real_selector_netter_and_report(monkeypatch):
    from dataclasses import replace
    import us_equity_strategies.research.partial_rebalance as kernel
    import quant_platform_kit.research_stats.security_netting as netter
    import quant_platform_kit.research_stats.unified_report as reporting
    original_select, original_net, original_report = kernel.decide_partial_rebalance, netter.net_security_session, reporting.build_unified_net_report
    calls = {'select': 0, 'net': 0, 'report': 0}
    guards = []
    def select(**kwargs):
        guard = guards[calls['select'] % 2]
        assert not guard.selected and guard.reads == 0
        assert not any('return' in k for k in kwargs)
        result = original_select(**kwargs)
        assert result.status == 'ACCEPTED'
        guard.selected = True
        calls['select'] += 1
        return result
    def net(**kwargs):
        calls['net'] += 1
        assert guards[(calls['net']-1) % 2].reads > 0
        return original_net(**kwargs)
    # Preserve the inspected native signature while counting true native reports.
    import functools
    @functools.wraps(original_report)
    def report(*args, **kwargs):
        calls['report'] += 1
        return original_report(*args, **kwargs)
    monkeypatch.setattr(kernel, 'decide_partial_rebalance', select)
    monkeypatch.setattr(netter, 'net_security_session', net)
    monkeypatch.setattr(reporting, 'build_unified_net_report', report)
    results = []
    for future in (-.2, .8):
        rows, opts = b5_case(targets=[{'A': .6, 'BOXX': .38}]*2,
            returns=[{'A': future, 'BOXX': .01}, {'A': .02, 'BOXX': -.01}])
        guards[:] = [B5UnreadReturns(r.security_returns) for r in rows]
        rows = [replace(r, security_returns=g) for r, g in zip(rows, guards, strict=True)]
        results.append(run_offline_security_netting(rows, **opts))
    assert calls == {'select': 4, 'net': 4, 'report': 2}
    assert results[0]['rs04_research']['decisions'][0]['decision'] == results[1]['rs04_research']['decisions'][0]['decision']
    assert results[0]['ledger'][0]['inner_target'] == results[1]['ledger'][0]['inner_target']
    assert results[0]['ledger'][0]['net_trades'] != results[1]['ledger'][0]['net_trades']


@pytest.mark.parametrize('bad', ['source', 'time', 'anchor', 'unknown_current', 'values', 'policy', 'units', 'cap_basis', 'universe', 'full_target', 'turnover_conflict'])
def test_b5_incomplete_or_unknown_contract_stops_before_returns(bad):
    from dataclasses import replace
    rows, opts = b5_case()
    policy = opts['rs04_research_policy']
    info = policy.decisions[0]
    context = info.hard_policy_context
    if bad == 'source': info = replace(info, full_target_source='')
    if bad == 'time': info = replace(info, cutoff=None)
    if bad == 'anchor': policy = replace(policy, opening=replace(policy.opening, completed_session=date(2025, 12, 31)))
    if bad == 'unknown_current': policy = replace(policy, opening=replace(policy.opening, current_known=False))
    if bad == 'values': policy = replace(policy, opening=replace(policy.opening, cash=1.))
    if bad == 'policy': info = replace(info, hard_policy_context=None)
    if bad == 'units': info = replace(info, hard_policy_context=replace(context, weight_unit='PERCENT'))
    if bad == 'cap_basis': info = replace(info, hard_policy_context=replace(context, cap_basis='ACTUAL_POST_FEE_NAV'))
    if bad == 'universe': policy = replace(policy, security_universe=('A',))
    if bad == 'full_target': info = replace(info, full_target=(('A', .6),))
    if bad == 'turnover_conflict':
        info = replace(info, hard_policy_context=replace(context, turnover_basis='EXIT_THROTTLE', max_one_way_risk_turnover=.1),
                       mandatory_reduce=True, forced_target=(('A', .2), ('BOXX', .58)),
                       forced_source=context.source, forced_reason='exit')
    policy = replace(policy, decisions=(info,))
    guard = B5UnreadReturns(rows[0].security_returns)
    with pytest.raises(ValueError):
        run_offline_security_netting([replace(rows[0], security_returns=guard)], **(opts | {'rs04_research_policy': policy}))
    assert guard.reads == 0


def test_b5_fill_drift_risk_buy_limit_and_hold_cap_are_rechecked():
    from dataclasses import replace
    context = replace(b5_policy().decisions[0].hard_policy_context, max_one_way_risk_turnover=.11)
    rows, opts = b5_case(returns=[{'A': -.8, 'BOXX': 0.}], context=context)
    # Decision buys .1; the frozen .5 target after the crash buys much more.
    with pytest.raises(ValueError, match='RS04_EXECUTION_REJECTED:HARD_CAP_RISK_BUY_TURNOVER'):
        run_offline_security_netting(rows, **opts)
    context = replace(context, max_one_way_risk_turnover=None,
                      securities=tuple(replace(s, max_weight=.5) if s.symbol == 'A' else s for s in context.securities))
    rows, opts = b5_case(targets=[{'A': .405, 'BOXX': .575}], mode='BAND_HOLD',
                         returns=[{'A': 1., 'BOXX': 0.}], context=context)
    with pytest.raises(ValueError, match='RS04_EXECUTION_REJECTED:HARD_CAP_SYMBOL_WEIGHT'):
        run_offline_security_netting(rows, **opts)


def test_b5_policy_conflict_and_fee_cash_no_free_boxx_sale():
    rows, opts = b5_case()
    with pytest.raises(ValueError, match='RS04_RS06_POLICY_CONFLICT'):
        run_offline_security_netting(rows, **(opts | {'rs06_risk_budget': {}}))
    rows, opts = b5_case(mode='FULL', targets=[{'A': .6, 'BOXX': .4}])
    with pytest.raises(ValueError, match='FEE_CASH_INSUFFICIENT'):
        run_offline_security_netting(rows, **opts)


def test_b5_post_fee_basis_is_explicitly_unsupported_never_relabelled():
    from dataclasses import replace
    context = b5_policy().decisions[0].hard_policy_context
    context = replace(context, securities=tuple(replace(s, max_weight=.5) if s.symbol == 'A' else s for s in context.securities))
    rows, opts = b5_case(mode='FULL', weights={'A': .5, 'BOXX': .45},
                         targets=[{'A': .5, 'BOXX': .4}], context=context)
    scoped = run_offline_security_netting(rows, **opts)
    receipt = scoped['rs04_research']['decisions'][0]['execution']
    assert receipt['actual_fees'] == pytest.approx(.005)
    assert receipt['post_fee_weights']['A'] == pytest.approx(50./99.995)
    assert receipt['post_fee_weights']['A'] > .5
    assert receipt['cap_basis'] == 'DECISION_TIME_TARGET'
    assert receipt['post_fee_cap_certified'] is False
    policy = opts['rs04_research_policy']
    original_post_fee = replace(context, cap_basis='ACTUAL_POST_FEE_NAV')
    policy = replace(policy, decisions=(replace(policy.decisions[0], hard_policy_context=original_post_fee),))
    guard = B5UnreadReturns(rows[0].security_returns)
    with pytest.raises(ValueError, match='RS04_UNSUPPORTED_ORIGINAL_CAP_BASIS'):
        run_offline_security_netting([replace(rows[0], security_returns=guard)], **(opts | {'rs04_research_policy': policy}))
    assert guard.reads == 0


@pytest.mark.parametrize('mode', ['PROPORTIONAL', 'BAND_HOLD'])
def test_a1_zero_cash_two_period_true_hold(mode, monkeypatch):
    from fractions import Fraction
    from us_equity_strategies.research import partial_rebalance as kernel
    original = kernel.decide_partial_rebalance
    calls = []

    def observed(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(kernel, 'decide_partial_rebalance', observed)
    rows, options = b5_case(mode=mode, lam=0., weights={'A': .25, 'BOXX': .75},
        targets=[{'A': .25, 'BOXX': .75}]*2,
        returns=[{'A': .004, 'BOXX': .01}, {'A': 0., 'BOXX': 0.}])
    result = run_offline_security_netting(rows, **options)
    assert result['final_nav'] == 100.85
    assert len(result['ledger']) == 2
    for row, receipt in zip(result['ledger'], result['rs04_research']['decisions'], strict=True):
        assert receipt['decision']['kind'] == 'HOLD_POSITIONS'
        assert receipt['decision']['target_weights'] is None
        assert receipt['decision']['residual_cash'] is None
        assert receipt['decision']['input_summary']['current_residual_cash'] == 0.
        assert row['security_pre'] == row['security_end'] == {'A': 25.1, 'BOXX': 75.75}
        assert row['virtual_cash_end']['inner_residual_cash'] == 0.
        assert all(value == 0. for value in row['net_trades'].values())
        assert row['net_security_turnover'] == row['fees'] == 0.
        assert all(value == 0. for value in row['fee_by_security'].values())
    assert len(calls) == 2
    assert all(sum(map(Fraction, call['current_weights'].values()), Fraction()) == 1
               for call in calls)


@pytest.mark.parametrize('mode', ['PROPORTIONAL', 'BAND_HOLD'])
def test_a1_three_securities_zero_cash_and_band_rounding_boundary(mode, monkeypatch):
    from dataclasses import replace
    from fractions import Fraction
    from quant_platform_kit.research_stats.security_netting import SecurityNettingSpec
    from us_equity_strategies.research import partial_rebalance as kernel
    from us_equity_strategies.research.security_netting_runner import DecisionSnapshot
    weights = {'A': .000002, 'B': .4, 'BOXX': .599998}
    target = {'A': .001002, 'B': .399, 'BOXX': .5999979}
    rows, options = b5_case(mode=mode, lam=0., band=.001, weights=weights, targets=[target],
                           returns=[dict.fromkeys(weights, 0.)])
    policy = options['rs04_research_policy']
    context = policy.decisions[0].hard_policy_context
    context = replace(context, securities=(*context.securities,
        kernel.SecurityPolicy('B', True, 1., 'B', .9)),
        max_underlying_exposure=(*context.max_underlying_exposure, ('B', .9)))
    policy = replace(policy, security_universe=tuple(weights),
        opening=DecisionSnapshot(date(2026, 1, 1), '2026-01-01T21:00:00+00:00',
            100., tuple((s, 100.*w) for s, w in weights.items()), 0., True),
        decisions=(replace(policy.decisions[0], hard_policy_context=context),))
    original = kernel.decide_partial_rebalance
    calls = []

    def observed(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(kernel, 'decide_partial_rebalance', observed)
    result = run_offline_security_netting(rows, **(options | {'rs04_research_policy': policy,
        'spec': SecurityNettingSpec(bps_per_side=dict.fromkeys(weights, 10.), cash_like_symbols=('BOXX',))}))
    row = result['ledger'][0]
    decision = result['rs04_research']['decisions'][0]['decision']
    assert decision['kind'] == 'HOLD_POSITIONS' and decision['mode'] == mode
    assert decision['input_summary']['current_residual_cash'] == 0.
    assert row['security_end'] == dict(policy.opening.security_values)
    assert row['virtual_cash_end']['inner_residual_cash'] == row['fees'] == 0.
    assert all(v == 0. for v in row['net_trades'].values())
    assert len(calls) == 1
    assert sum(map(Fraction, calls[0]['current_weights'].values()), Fraction()) == 1
    if mode == 'BAND_HOLD':
        # Encoding A downward would put it outside this very small band margin.
        # The actual book is inside; a FULL trade would need nonexistent fee cash.
        assert calls[0]['mode'] == 'PROPORTIONAL' and calls[0]['lambda_value'] == 0.
        assert decision['reason'] == 'WITHIN_BAND_HOLD'


@pytest.mark.parametrize('cap', ['symbol', 'effective', 'underlying'])
def test_a1_rounding_cannot_hide_real_hold_overcap(cap):
    from dataclasses import replace
    from fractions import Fraction
    rows, options = b5_case(lam=0., weights={'A': .25, 'BOXX': .75},
        targets=[{'A': .25, 'BOXX': .75}, {'A': .125, 'BOXX': .75}],
        returns=[{'A': .004, 'BOXX': .01}, {'A': 0., 'BOXX': 0.}])
    policy = options['rs04_research_policy']
    limit = 25.1 / 100.85
    assert Fraction(25.1) / Fraction(100.85) > Fraction(limit)
    context = policy.decisions[1].hard_policy_context
    if cap == 'symbol':
        context = replace(context, securities=tuple(
            replace(s, max_weight=limit) if s.symbol == 'A' else s for s in context.securities))
    elif cap == 'effective':
        context = replace(context, max_effective_risk_exposure=limit)
    else:
        context = replace(context, max_underlying_exposure=(('A', limit),))
    policy = replace(policy, decisions=(policy.decisions[0],
        replace(policy.decisions[1], hard_policy_context=context)))
    reason = {'symbol': 'SYMBOL_WEIGHT', 'effective': 'EFFECTIVE_EXPOSURE',
              'underlying': 'UNDERLYING_EXPOSURE'}[cap]
    with pytest.raises(ValueError, match='RS04_DECISION_REJECTED:HARD_CAP_' + reason):
        run_offline_security_netting(rows, **(options | {'rs04_research_policy': policy}))


@pytest.mark.parametrize('mandatory', [False, True])
def test_a1_actual_ratios_protect_turnover_and_forced_risk(mandatory):
    from dataclasses import replace
    from fractions import Fraction
    weights = {'A': .401, 'BOXX': .599}
    context = replace(b5_policy().decisions[0].hard_policy_context, max_one_way_risk_turnover=0.)
    rows, options = b5_case(mode='FULL', weights=weights, targets=[weights], context=context,
                           mandatory=mandatory, forced=weights if mandatory else None)
    assert Fraction(.401) > Fraction(40.1) / 100
    guard = B5UnreadReturns(rows[0].security_returns)
    reason = 'FORCED_RISK_INCREASE' if mandatory else 'HARD_CAP_RISK_BUY_TURNOVER'
    with pytest.raises(ValueError, match='RS04_DECISION_REJECTED:' + reason):
        run_offline_security_netting([replace(rows[0], security_returns=guard)], **options)
    assert guard.reads == 0


def test_a1_sub_grid_position_cannot_hide_veto():
    from dataclasses import replace
    context = b5_policy().decisions[0].hard_policy_context
    context = replace(context, vetoed_symbols=('A',), securities=tuple(
        replace(s, max_weight=1.) if s.symbol == 'BOXX' else s for s in context.securities))
    rows, options = b5_case(lam=0., weights={'A': 2.**-60, 'BOXX': 1.},
                           targets=[{'A': 0., 'BOXX': 1.}], context=context)
    with pytest.raises(ValueError, match='RS04_DECISION_REJECTED:HARD_VETO'):
        run_offline_security_netting(rows, **options)


@pytest.mark.parametrize('bad', ['negative_cash', 'unfunded_book', 'unfunded_full', 'fee_cash'])
def test_a1_original_funding_refusals_remain(bad):
    from dataclasses import replace
    target = {'A': .25, 'BOXX': .75}
    if bad == 'unfunded_full':
        target = {'A': .25, 'BOXX': math.nextafter(.75, 1.)}
    if bad == 'fee_cash':
        target = {'A': .5, 'BOXX': .5}
    rows, options = b5_case(mode='FULL', weights={'A': .25, 'BOXX': .75}, targets=[target])
    policy = options['rs04_research_policy']
    if bad == 'negative_cash':
        policy = replace(policy, opening=replace(policy.opening, cash=-1e-12))
    if bad == 'unfunded_book':
        policy = replace(policy, opening=replace(policy.opening, security_values=(('A', 26.), ('BOXX', 75.))))
    options['rs04_research_policy'] = policy
    reason = {'negative_cash': 'RS04_ACTUAL_BOOK_RECONCILIATION',
              'unfunded_book': 'RS04_ACTUAL_BOOK_RECONCILIATION',
              'unfunded_full': 'RS04_DECISION_REJECTED:UNFUNDED_SECURITY_WEIGHTS',
              'fee_cash': 'FEE_CASH_INSUFFICIENT'}[bad]
    with pytest.raises(ValueError, match=reason):
        run_offline_security_netting(rows, **options)


def cvar_case(risk='TQQQ', sleeve='QQQM', *, probability=.07, budget=.03,
              mode='FULL', lam=.5, band=.01, target=None, anchor=True):
    """Independent synthetic oracle; no real prices, policy or account budget."""
    from dataclasses import replace
    from us_equity_strategies.research.security_netting_runner import (
        DecisionSnapshot, RS04DecisionInput, RS04ResearchPolicy, RS04CVaRRequest,
    )
    from us_equity_strategies.research.partial_rebalance import HardPolicyContext, SecurityPolicy
    from us_equity_strategies.research.cvar_exposure_constraint import (
        TailDescriptor, JointScenario, ScenarioSet, CostSpec, TailPolicy, ApprovedAnchor, Action,
    )
    from quant_platform_kit.research_stats.security_netting import SecurityNettingSpec
    symbols = (risk, sleeve, 'BOXX')
    weights = {risk: .4, sleeve: 0., 'BOXX': .58}
    target = {risk: .2, sleeve: 0., 'BOXX': .75} if target is None else target
    fill = {risk: .2, sleeve: 0., 'BOXX': 0.}
    context = HardPolicyContext(tuple(SecurityPolicy(s, s != 'BOXX', 3. if s == risk else 1.,
        'SYNTHETIC_INDEX', 1.) for s in symbols), 3., (('SYNTHETIC_INDEX', 3.),), (),
        'synthetic-original-policy-v1', 'synthetic-classification-v1', True,
        'DECISION_TIME_TARGET', None, 'RISK_BUY_WEIGHT', 'NAV_FRACTION')
    descriptor = TailDescriptor('RS04:'+risk, 2, 'PRE_NEXT_FILL_AT_T_PLUS_2', 'USD',
        'ZERO_RETURN_RESIDUAL_CASH', .95, 'DECISION_NAV_FRACTION')
    scenarios = ScenarioSet(symbols, tuple(JointScenario(name, p, fill,
        {risk: r, sleeve: 0., 'BOXX': .01}) for name, p, r in
        (('crash', probability, -.5), ('less_bad', 1.-probability, -.1))), descriptor,
        'synthetic-joint-two-step', 'synthetic-scenario-source', True, True, True)
    rates = {risk: 10., sleeve: 10., 'BOXX': 2.}
    tail = TailPolicy(descriptor, budget, 'synthetic-tail-approval', True, True)
    fallback = ApprovedAnchor(Action('TARGET_WEIGHTS', {risk: 0., sleeve: 0., 'BOXX': .75}),
        'synthetic-anchor-approval', True, True) if anchor else None
    sources = {'scenarios': scenarios.source, 'tail_policy': tail.source,
        'cost': 'synthetic-fixed-cost', 'original_policy': context.source}
    if fallback is not None:
        sources['anchor'] = fallback.source
    known = tuple((key, source, '2026-01-01T20:00:00+00:00',
        '2026-01-01T21:00:00+00:00') for key, source in sources.items())
    request = RS04CVaRRequest(scenarios, tail,
        CostSpec('FIXED_NONNEGATIVE_BPS', rates, 'BPS_PER_SIDE', 1., 'DIMENSIONLESS'),
        context, fallback, known)
    opening = DecisionSnapshot(date(2026, 1, 1), '2026-01-01T21:00:00+00:00', 1.,
        tuple(weights.items()), .02, True)
    info = RS04DecisionInput(date(2026, 1, 1), date(2026, 1, 2),
        '2026-01-01T22:00:00+00:00', '2026-01-02T21:00:00+00:00',
        '2026-01-01T21:00:00+00:00', 'synthetic-full-target', tuple(target.items()), context,
        False, cvar_request=request)
    policy = RS04ResearchPolicy(mode, symbols, opening, (info,), lam, band,
        cvar_descriptor=descriptor)
    options = dict(initial_nav=1., initial_f=1., initial_inner_weights=weights,
        spec=SecurityNettingSpec(bps_per_side=rates, cash_like_symbols=('BOXX',)),
        fee_cash_role='inner_residual_cash', execution_timing='next_close', report_periods_per_year=252,
        rs04_research_policy=policy)
    rows = [NettingSession(info.fill_session, info.decision_session, 1., target, fill, 0.)]
    return rows, options


@pytest.mark.parametrize('risk,sleeve', [('TQQQ', 'QQQM'), ('SOXL', 'SOXX')])
@pytest.mark.parametrize('probability,expected_es', [(.07, .02021), (.03, -.01435)])
def test_cvar_full_two_step_oracle(risk, sleeve, probability, expected_es):
    from dataclasses import replace
    from us_equity_strategies.research.security_netting_runner import RS04ResearchPolicy
    rows, options = cvar_case(risk, sleeve, probability=probability)
    result = run_offline_security_netting(rows, **options)
    receipt = result['rs04_research']['decisions'][0]['cvar']
    assert receipt['constraint']['status'] == 'PROPOSAL_FEASIBLE'
    assert receipt['constraint']['evaluation']['es'] == pytest.approx(expected_es, abs=1e-12)
    assert result['ledger'][0]['inner_target'] == rows[0].inner_target
    row = result['ledger'][0]
    assert row['nav_pre_trade'] == pytest.approx(1.08, abs=1e-12)
    assert row['net_trades'] == pytest.approx({risk: -.264, sleeve: 0., 'BOXX': .23}, abs=1e-12)
    assert row['fees'] == pytest.approx(.00031, abs=1e-12)
    assert row['virtual_cash_end']['inner_residual_cash'] == pytest.approx(.05369, abs=1e-12)
    assert row['nav_end'] == pytest.approx(1.07969, abs=1e-12)
    # A second explicit lambda=0 HOLD, with NO new tail request, implements the
    # pre-next-fill endpoint. Neither target callback nor another fee is allowed.
    policy = options['rs04_research_policy']
    first = policy.decisions[0]
    second = replace(first, decision_session=date(2026, 1, 2), fill_session=date(2026, 1, 3),
        cutoff='2026-01-02T22:00:00+00:00', fill_completed_at='2026-01-03T21:00:00+00:00',
        full_target_available_at='2026-01-02T21:00:00+00:00', cvar_request=None)
    for name, r2, expected_nav in [('crash', -.5, .97979), ('less_bad', -.1, 1.06619)]:
        # One global lambda=0 plus a mandatory first target makes the second
        # action explicit HOLD without adding a new per-row action mechanism.
        forced = replace(first, mandatory_reduce=True, forced_target=first.full_target,
                         forced_source=first.hard_policy_context.source, forced_reason='synthetic-oracle')
        two_policy = replace(policy, mode='PROPORTIONAL', lambda_value=0., decisions=(forced, second))
        second_row = NettingSession(second.fill_session, second.decision_session, 1.,
            rows[0].inner_target, {risk: r2, sleeve: 0., 'BOXX': .01}, 0.)
        two = run_offline_security_netting([rows[0], second_row], **(options | {'rs04_research_policy': two_policy}))
        assert two['ledger'][1]['fees'] == 0.
        assert two['ledger'][1]['security_end'] == two['ledger'][1]['security_pre']
        assert two['final_nav'] == pytest.approx(expected_nav, abs=1e-12)
        predicted = next(s for s in receipt['constraint']['evaluation']['scenarios'] if s['scenario_id'] == name)
        assert predicted['nav_ratio'] == pytest.approx(two['final_nav'], abs=1e-12)
    reconcile(result)


def cvar_replace(options, transform, **info_changes):
    from dataclasses import replace
    policy = options['rs04_research_policy']
    first = policy.decisions[0]
    info = replace(first, cvar_request=transform(first.cvar_request), **info_changes)
    return options | {'rs04_research_policy': replace(policy, decisions=(info,))}


@pytest.mark.parametrize('mode,lam,band', [('PROPORTIONAL', 0., .01), ('BAND_HOLD', .5, 1.)])
def test_cvar_hold_is_real_positions_or_direct_mandatory_anchor(mode, lam, band):
    from dataclasses import replace
    for budget, status in ((.2, 'PROPOSAL_FEASIBLE'), (.03, 'MANDATORY_TAIL_REDUCTION')):
        rows, options = cvar_case(mode=mode, lam=lam, band=band, budget=budget)
        result = run_offline_security_netting(rows, **options)
        receipt = result['rs04_research']['decisions'][0]
        tail = receipt['cvar']['constraint']
        assert receipt['decision']['kind'] == 'HOLD_POSITIONS'  # actual B5 selection
        assert tail['status'] == status
        if budget == .2:
            assert tail['evaluation']['es'] == pytest.approx(.1542, abs=1e-12)
            assert tail['evaluation']['scenarios'][0]['nav_ratio'] == pytest.approx(.8458, abs=1e-12)
            assert result['ledger'][0]['security_end'] == result['ledger'][0]['security_pre']
            assert result['ledger'][0]['fees'] == 0.
            assert tail['action']['target_weights'] is None
        else:
            anchor = options['rs04_research_policy'].decisions[0].cvar_request.anchor
            assert result['ledger'][0]['inner_target'] == anchor.action.target_weights
            assert tail['mandatory_reduce'] and tail['bypass_b5_throttle']
            assert tail['lambda_value'] is None
            assert result['ledger'][0]['fees'] > 0.


def test_cvar_tightens_only_certified_segment_and_rechecks_original_formula():
    from decimal import Decimal as D, localcontext
    from us_equity_strategies.research.cvar_exposure_constraint import (
        Action, DecisionBook, scenario_losses, weighted_cvar,
    )
    rows, options = cvar_case(budget=.005)
    result = run_offline_security_netting(rows, **options)
    detail = result['rs04_research']['decisions'][0]['cvar']
    tail = detail['constraint']
    request = options['rs04_research_policy'].decisions[0].cvar_request
    target = result['ledger'][0]['inner_target']
    assert tail['status'] == 'TAIL_TARGET_TIGHTENED'
    assert 0. < tail['lambda_value'] < 1.
    assert target['TQQQ'] < .2 and target['QQQM'] == 0. and target['BOXX'] == .75
    # Independent cash/BOXX/fee oracle: L(x)=-.087574+.53892*x.
    with localcontext() as context:
        context.prec = 60
        boundary = (D('.005')+D('.087574'))/D('.53892')
        assert D(target['TQQQ']) <= boundary
        assert abs(float(boundary)-target['TQQQ']) < 2e-11
    book = DecisionBook(detail['decision_book']['weights'], 1., 'USD', 0.)
    projected = scenario_losses(book, Action('TARGET_WEIGHTS', target), request.scenarios, request.cost_spec)
    es = weighted_cvar([row.loss for row in projected], [row.probability for row in projected], .95)
    assert es <= .005 and all(row.feasible for row in projected)
    assert tail['evaluation']['es'] == es
    assert result['ledger'][0]['fees'] == pytest.approx(.000526-.00108*target['TQQQ'], abs=1e-12)
    assert target['TQQQ'] != pytest.approx(.2*.005/.02021)


@pytest.mark.parametrize('mode', ['FULL', 'PROPORTIONAL'])
@pytest.mark.parametrize('anchor_bad', ['absent', 'unapproved', 'risk_increase', 'cash_failure', 'tail_failure'])
def test_cvar_no_certified_anchor_never_executes_or_reads_actual(mode, anchor_bad):
    from dataclasses import replace
    from us_equity_strategies.research.cvar_exposure_constraint import Action
    rows, options = cvar_case(mode=mode, lam=0., budget=.005)
    def bad(request):
        anchor = request.anchor
        if anchor_bad == 'absent':
            return replace(request, anchor=None, known_inputs=tuple(v for v in request.known_inputs if v[0] != 'anchor'))
        if anchor_bad == 'unapproved':
            anchor = replace(anchor, approved=False)
        else:
            target = {'TQQQ': .4 if anchor_bad == 'risk_increase' else .25 if anchor_bad == 'cash_failure' else .2,
                      'QQQM': 0., 'BOXX': .75}
            if anchor_bad == 'risk_increase':
                target['BOXX'] = .5
            anchor = replace(anchor, action=Action('TARGET_WEIGHTS', target))
        return replace(request, anchor=anchor)
    opts = cvar_replace(options, bad)
    guard = B5UnreadReturns(rows[0].security_returns)
    status = 'NO_CERTIFIED_FEASIBLE_FALLBACK' if mode == 'FULL' else 'TAIL_REDUCTION_REQUIRED_NO_CERTIFIED_TARGET'
    with pytest.raises(ValueError, match=status):
        run_offline_security_netting([replace(rows[0], security_returns=guard)], **opts)
    assert guard.reads == 0


@pytest.mark.parametrize('field,value', [('scope', 'WRONG_SCOPE'), ('currency', 'EUR'),
    ('horizon_sessions', 1), ('endpoint', 'POST_SECOND_FILL'), ('alpha_confidence', .99),
    ('loss_unit', 'PERCENT'), ('benchmark', 'BOXX')])
def test_cvar_descriptor_drift_refused_before_future_read(field, value):
    from dataclasses import replace
    rows, options = cvar_case()
    opts = cvar_replace(options, lambda req: replace(req,
        scenarios=replace(req.scenarios, descriptor=replace(req.scenarios.descriptor, **{field: value}))))
    guard = B5UnreadReturns(rows[0].security_returns)
    with pytest.raises(ValueError, match='RS04_CVAR_UNAVAILABLE'):
        run_offline_security_netting([replace(rows[0], security_returns=guard)], **opts)
    assert guard.reads == 0


@pytest.mark.parametrize('bad', ['budget', 'policy', 'coverage', 'universe', 'quality', 'probability',
    'source', 'known_inputs', 'late', 'as_of', 'real_scenarios', 'returns_coverage'])
def test_cvar_missing_or_unknown_inputs_never_use_old_success(bad):
    from dataclasses import replace
    rows, options = cvar_case()
    def change(req):
        if bad == 'budget': return replace(req, tail_policy=replace(req.tail_policy, budget=None))
        if bad == 'policy': return replace(req, tail_policy=None)
        if bad == 'coverage': return replace(req, scenarios=replace(req.scenarios, coverage_available=False))
        if bad == 'universe': return replace(req, scenarios=replace(req.scenarios, symbols=('TQQQ', 'BOXX')))
        if bad == 'quality': return replace(req, scenarios=replace(req.scenarios, quality_attested=False))
        if bad == 'source': return replace(req, scenarios=replace(req.scenarios, source='different-source'))
        if bad == 'known_inputs': return replace(req, known_inputs=())
        if bad in ('late', 'as_of'):
            known = list(req.known_inputs)
            name, source, as_of, available = known[0]
            known[0] = (name, source, '2026-01-02T20:00:00+00:00' if bad == 'as_of' else as_of,
                        '2026-01-03T21:00:00+00:00' if bad == 'late' else available)
            return replace(req, known_inputs=tuple(known))
        if bad == 'real_scenarios': return replace(req, scenarios=replace(req.scenarios, synthetic=False))
        scenarios = list(req.scenarios.scenarios)
        scenarios[0] = replace(scenarios[0], **({'probability': .08} if bad == 'probability' else
            {'earn_returns': {'TQQQ': -.5, 'BOXX': .01}}))
        return replace(req, scenarios=replace(req.scenarios, scenarios=tuple(scenarios)))
    opts = cvar_replace(options, change)
    guard = B5UnreadReturns(rows[0].security_returns)
    with pytest.raises(ValueError, match='RS04_CVAR_(UNAVAILABLE|REJECTED_INPUT)'):
        run_offline_security_netting([replace(rows[0], security_returns=guard)], **opts)
    assert guard.reads == 0


@pytest.mark.parametrize('bad', ['bps', 'multiplier', 'model', 'unit', 'multiplier_unit', 'universe'])
def test_cvar_forecast_cost_must_equal_actual_ledger_contract(bad):
    from dataclasses import replace
    rows, options = cvar_case()
    def change(req):
        cost = req.cost_spec
        values = {'bps': {'bps_by_symbol': dict(cost.bps_by_symbol) | {'BOXX': 0.}},
            'multiplier': {'frozen_multiplier': 0.}, 'model': {'model': 'FREE'},
            'unit': {'unit': 'DECIMAL'}, 'multiplier_unit': {'multiplier_unit': 'BPS'},
            'universe': {'bps_by_symbol': {'TQQQ': 10., 'BOXX': 2.}}}
        return replace(req, cost_spec=replace(cost, **values[bad]))
    guard = B5UnreadReturns(rows[0].security_returns)
    with pytest.raises(ValueError, match='RS04_CVAR_(UNAVAILABLE|UNSUPPORTED_COST_MODEL)'):
        run_offline_security_netting([replace(rows[0], security_returns=guard)], **cvar_replace(options, change))
    assert guard.reads == 0


@pytest.mark.parametrize('bad', ['symbol', 'effective', 'underlying', 'veto', 'classification', 'units', 'turnover'])
def test_cvar_same_source_cannot_bind_a_different_original_policy(bad):
    from dataclasses import replace
    rows, options = cvar_case()
    def change(req):
        policy = req.original_policy
        changes = {'symbol': {'securities': tuple(replace(s, max_weight=.9) for s in policy.securities)},
            'effective': {'max_effective_risk_exposure': 4.},
            'underlying': {'max_underlying_exposure': (('SYNTHETIC_INDEX', 4.),)},
            'veto': {'vetoed_symbols': ('TQQQ',)},
            'classification': {'classification_source': 'synthetic-other-classification'},
            'units': {'weight_unit': 'PERCENT'}, 'turnover': {'max_one_way_risk_turnover': .5}}
        return replace(req, original_policy=replace(policy, **changes[bad]))
    with pytest.raises(ValueError, match='RS04_CVAR_UNAVAILABLE'):
        run_offline_security_netting(rows, **cvar_replace(options, change))


@pytest.mark.parametrize('bad', ['turnover', 'actual_post_fee', 'fill_basis'])
def test_cvar_preserves_explicitly_unsupported_original_contract(bad):
    from dataclasses import replace
    rows, options = cvar_case()
    context = options['rs04_research_policy'].decisions[0].hard_policy_context
    context = replace(context, **({'max_one_way_risk_turnover': .1} if bad == 'turnover' else
        {'cap_basis': 'ACTUAL_POST_FEE' if bad == 'actual_post_fee' else 'FILL_PRE_FEE'}))
    opts = cvar_replace(options, lambda req: replace(req, original_policy=context), hard_policy_context=context)
    with pytest.raises(ValueError, match='UNSUPPORTED_HARD_POLICY|UNSUPPORTED_ORIGINAL_CAP_BASIS'):
        run_offline_security_netting(rows, **opts)


@pytest.mark.parametrize('target', [{'TQQQ': .2, 'QQQM': 0., 'BOXX': .8},
                                    {'TQQQ': .25, 'QQQM': 0., 'BOXX': .75}])
def test_cvar_zero_residual_target_never_borrows_or_sells_boxx_for_free(target):
    rows, options = cvar_case(target=target, anchor=False, budget=1.)
    with pytest.raises(ValueError, match='UNFUNDED_SECURITY_WEIGHTS|NO_CERTIFIED_FEASIBLE_FALLBACK'):
        run_offline_security_netting(rows, **options)


def test_cvar_selection_finishes_before_actual_return_and_real_fee_book_recurses(monkeypatch):
    from dataclasses import replace
    import us_equity_strategies.research.security_netting_runner as runner
    import us_equity_strategies.research.cvar_exposure_constraint as kernel
    import quant_platform_kit.research_stats.security_netting as qpk
    original_select, original_tail, original_net = runner._rs04_select, runner._rs04_constrain_tail, qpk.net_security_session
    original_kernel = kernel.constrain_tail_target
    counts = {'b5': 0, 'tail': 0, 'kernel': 0, 'net': 0}
    guards, seen_books = [], []
    def select(*args):
        guard = guards[counts['b5'] % 2]
        assert guard.reads == 0
        counts['b5'] += 1
        return original_select(*args)
    def pure(*args, **kwargs):
        assert counts['b5'] == counts['kernel'] + 1
        assert counts['tail'] == counts['kernel']
        assert guards[counts['kernel'] % 2].reads == 0
        counts['kernel'] += 1
        seen_books.append(kwargs['decision_book'])
        return original_kernel(*args, **kwargs)
    def tail(*args):
        index = counts['tail'] % 2
        assert guards[index].reads == 0
        result = original_tail(*args)
        assert guards[index].reads == 0
        guards[index].selected = True
        counts['tail'] += 1
        return result
    def net(**kwargs):
        assert guards[counts['net'] % 2].reads > 0
        counts['net'] += 1
        return original_net(**kwargs)
    monkeypatch.setattr(runner, '_rs04_select', select)
    monkeypatch.setattr(runner, '_rs04_constrain_tail', tail)
    monkeypatch.setattr(kernel, 'constrain_tail_target', pure)
    monkeypatch.setattr(qpk, 'net_security_session', net)
    outputs = []
    for future in (.2, .8):
        rows, options = cvar_case()
        first = options['rs04_research_policy'].decisions[0]
        second = replace(first, decision_session=date(2026, 1, 2), fill_session=date(2026, 1, 3),
            cutoff='2026-01-02T22:00:00+00:00', fill_completed_at='2026-01-03T21:00:00+00:00',
            full_target_available_at='2026-01-02T21:00:00+00:00')
        guards = [B5UnreadReturns({'TQQQ': future, 'QQQM': 0., 'BOXX': 0.}),
                  B5UnreadReturns({'TQQQ': -.5, 'QQQM': 0., 'BOXX': .01})]
        rows = [replace(rows[0], security_returns=guards[0]), NettingSession(second.fill_session,
            second.decision_session, 1., dict(first.full_target), guards[1], 0.)]
        policy = replace(options['rs04_research_policy'], decisions=(first, second))
        output = run_offline_security_netting(rows, **(options | {'rs04_research_policy': policy}))
        outputs.append(output)
        first_row = output['ledger'][0]
        next_receipt = output['rs04_research']['decisions'][1]
        assert next_receipt['snapshot']['security_values'] == first_row['security_end']
        assert next_receipt['snapshot']['nav'] == first_row['nav_end']
        assert next_receipt['snapshot']['cash'] == first_row['virtual_cash_end']['inner_residual_cash']
        assert seen_books[-1].nav == first_row['nav_end']
        assert seen_books[-1].weights['TQQQ'] == first_row['security_end']['TQQQ']/first_row['nav_end']
        assert seen_books[-1].weights['TQQQ'] != dict(first.full_target)['TQQQ']
    first_decisions = [output['rs04_research']['decisions'][0] for output in outputs]
    assert {k: v for k, v in first_decisions[0].items() if k != 'execution'} == {k: v for k, v in first_decisions[1].items() if k != 'execution'}
    assert outputs[0]['rs04_research']['decisions'][1]['snapshot'] != outputs[1]['rs04_research']['decisions'][1]['snapshot']
    assert counts == {'b5': 4, 'tail': 4, 'kernel': 4, 'net': 4}


def test_cvar_original_forced_target_and_veto_cannot_be_relaxed():
    from dataclasses import replace
    rows, options = cvar_case(mode='BAND_HOLD', band=1., budget=.005)
    info = options['rs04_research_policy'].decisions[0]
    forced = (('TQQQ', .1), ('QQQM', 0.), ('BOXX', .75))
    opts = cvar_replace(options, lambda req: req, mandatory_reduce=True, forced_target=forced,
        forced_source=info.hard_policy_context.source, forced_reason='synthetic-original-breaker')
    result = run_offline_security_netting(rows, **opts)
    receipt = result['rs04_research']['decisions'][0]
    assert receipt['decision']['reason'] == 'MANDATORY_REDUCTION'
    assert result['ledger'][0]['inner_target']['TQQQ'] <= .1
    context = replace(info.hard_policy_context, vetoed_symbols=('TQQQ',))
    bad = cvar_replace(opts, lambda req: replace(req, original_policy=context), hard_policy_context=context)
    with pytest.raises(ValueError, match='HARD_VETO'):
        run_offline_security_netting(rows, **bad)


def test_cvar_forecast_feasible_does_not_exempt_actual_fee_cash_failure():
    from dataclasses import replace
    rows, options = cvar_case(budget=1.)
    # Forecast no turnover, but actual fill return produces a much larger trade.
    # Both costs bind the actual 50% per-side model. Prediction is never posted.
    def change(req):
        flat = {'TQQQ': 0., 'QQQM': 0., 'BOXX': 0.}
        scenarios = tuple(replace(s, fill_returns=flat, earn_returns=flat) for s in req.scenarios.scenarios)
        return replace(req, scenarios=replace(req.scenarios, scenarios=scenarios),
            cost_spec=replace(req.cost_spec, bps_by_symbol={s: 5000. for s in flat}))
    target = {'TQQQ': .4, 'QQQM': 0., 'BOXX': .58}
    options = cvar_replace(options, change, full_target=tuple(target.items()))
    options['spec'] = replace(options['spec'], bps_per_side={s: 5000. for s in target})
    rows = [replace(rows[0], inner_target=target, security_returns={'TQQQ': 10., 'QQQM': 0., 'BOXX': 0.})]
    with pytest.raises(ValueError, match='FEE_CASH_INSUFFICIENT'):
        run_offline_security_netting(rows, **options)


def test_cvar_absent_request_has_complete_b5_payload_and_call_compatibility(monkeypatch):
    from dataclasses import replace
    import us_equity_strategies.research.cvar_exposure_constraint as kernel
    def forbidden(*args, **kwargs):
        raise AssertionError('absent request must never call CVaR')
    monkeypatch.setattr(kernel, 'constrain_tail_target', forbidden)
    for mode, lam, band in [('FULL', .5, .01), ('PROPORTIONAL', 0., .01), ('BAND_HOLD', .5, 1.)]:
        rows, options = cvar_case(mode=mode, lam=lam, band=band)
        policy = options['rs04_research_policy']
        info = replace(policy.decisions[0], cvar_request=None)
        opts = options | {'rs04_research_policy': replace(policy, decisions=(info,), cvar_descriptor=None)}
        result = run_offline_security_netting(rows, **opts)
        assert all('cvar' not in receipt for receipt in result['rs04_research']['decisions'])
        explicit = replace(opts['rs04_research_policy'], cvar_descriptor=policy.cvar_descriptor)
        assert json.dumps(result, sort_keys=True) == json.dumps(run_offline_security_netting(rows,
            **(opts | {'rs04_research_policy': explicit})), sort_keys=True)


@pytest.mark.parametrize('mode,lam,band', [('PROPORTIONAL', 0., .01), ('BAND_HOLD', .5, 1.)])
def test_cvar_zero_cash_two_period_true_hold_uses_a1_conserving_book(mode, lam, band, monkeypatch):
    from dataclasses import replace
    from fractions import Fraction
    import us_equity_strategies.research.cvar_exposure_constraint as kernel
    original = kernel.constrain_tail_target
    books = []
    def select(*args, **kwargs):
        books.append(kwargs['decision_book'])
        return original(*args, **kwargs)
    monkeypatch.setattr(kernel, 'constrain_tail_target', select)
    weights = {'TQQQ': .25, 'QQQM': 0., 'BOXX': .75}
    rows, options = cvar_case(mode=mode, lam=lam, band=band, target=weights, budget=1.)
    policy = options['rs04_research_policy']
    first = policy.decisions[0]
    second = replace(first, decision_session=date(2026, 1, 2), fill_session=date(2026, 1, 3),
        cutoff='2026-01-02T22:00:00+00:00', fill_completed_at='2026-01-03T21:00:00+00:00',
        full_target_available_at='2026-01-02T21:00:00+00:00')
    policy = replace(policy, opening=replace(policy.opening, security_values=tuple(weights.items()), cash=0.),
                     decisions=(first, second))
    rows = [replace(rows[0], security_returns={'TQQQ': .004, 'QQQM': 0., 'BOXX': .01}),
        NettingSession(second.fill_session, second.decision_session, 1., weights,
            {'TQQQ': -.01, 'QQQM': 0., 'BOXX': .003}, 0.)]
    output = run_offline_security_netting(rows, **(options | {'initial_inner_weights': weights,
        'rs04_research_policy': policy}))
    assert len(books) == 2
    for book, row, receipt in zip(books, output['ledger'], output['rs04_research']['decisions'], strict=True):
        assert sum(map(Fraction, book.weights.values()), Fraction()) == 1
        assert row['fees'] == row['virtual_cash_end']['inner_residual_cash'] == 0.
        assert row['security_end'] == row['security_pre']
        assert receipt['cvar']['execution_action']['kind'] == 'HOLD_POSITIONS'
        assert all(value == 0. for value in row['net_trades'].values())


def test_cvar_actual_hold_control_and_wrong_second_fill_are_different_endpoints():
    from dataclasses import replace
    rows, options = cvar_case()
    policy = options['rs04_research_policy']
    first = policy.decisions[0]
    second = replace(first, decision_session=date(2026, 1, 2), fill_session=date(2026, 1, 3),
        cutoff='2026-01-02T22:00:00+00:00', fill_completed_at='2026-01-03T21:00:00+00:00',
        full_target_available_at='2026-01-02T21:00:00+00:00', cvar_request=None)
    second_row = NettingSession(second.fill_session, second.decision_session, 1., dict(first.full_target),
        {'TQQQ': -.5, 'QQQM': 0., 'BOXX': .01}, 0.)
    # This FULL second close deliberately represents the different endpoint.
    full = run_offline_security_netting([rows[0], second_row], **(options | {
        'rs04_research_policy': replace(policy, decisions=(first, second))}))
    assert full['ledger'][1]['fees'] == pytest.approx(.0001046095, abs=1e-12)
    assert full['final_nav'] == pytest.approx(.9796853905, abs=1e-12)
    assert full['final_nav'] != pytest.approx(.97979, abs=1e-8)
    request = replace(first.cvar_request, tail_policy=replace(first.cvar_request.tail_policy, budget=.2))
    hold_first = replace(first, cvar_request=request)
    hold = run_offline_security_netting([rows[0], second_row], **(options | {'rs04_research_policy':
        replace(policy, mode='PROPORTIONAL', lambda_value=0., decisions=(hold_first, second))}))
    assert hold['final_nav'] == pytest.approx(.8458, abs=1e-12)
    assert sum(row['fees'] for row in hold['ledger']) == 0.


def test_cvar_anchor_original_boxx_cap_is_preserved():
    from dataclasses import replace
    from us_equity_strategies.research.cvar_exposure_constraint import Action
    rows, options = cvar_case(budget=.005)
    first = options['rs04_research_policy'].decisions[0]
    context = replace(first.hard_policy_context, securities=tuple(
        replace(s, max_weight=.8) if s.symbol == 'BOXX' else s for s in first.hard_policy_context.securities))
    opts = cvar_replace(options, lambda req: replace(req, original_policy=context,
        anchor=replace(req.anchor, action=Action('TARGET_WEIGHTS', {'TQQQ': 0., 'QQQM': 0., 'BOXX': .9}))),
        hard_policy_context=context)
    with pytest.raises(ValueError, match='NO_CERTIFIED_FEASIBLE_FALLBACK'):
        run_offline_security_netting(rows, **opts)


def cvar_a1_book_case(*, nav=100.000000009, cash=0., mode='PROPORTIONAL', budget=.125,
                      fixed=False):
    """Known dollars and forecasts, including a second close with true HOLD."""
    from dataclasses import replace
    from us_equity_strategies.research.cvar_exposure_constraint import JointScenario
    weights = {'TQQQ': .25, 'QQQM': 0., 'BOXX': .75}
    target = weights | {'TQQQ': .2} if fixed else weights
    rows, options = cvar_case(mode=mode, lam=0., target=target, budget=budget, anchor=False)
    policy = options['rs04_research_policy']
    first = policy.decisions[0]
    flat = dict.fromkeys(weights, 0.)
    stress = flat | {'TQQQ': -.5}
    request = first.cvar_request
    request = replace(request, scenarios=replace(request.scenarios,
        scenarios=(JointScenario('actual-book-boundary', 1., flat, stress),)))
    first = replace(first, cvar_request=request)
    if fixed:
        first = replace(first, mandatory_reduce=True, forced_target=tuple(target.items()),
            forced_source=first.hard_policy_context.source, forced_reason='synthetic-native-book-oracle')
    second = replace(first, decision_session=date(2026, 1, 2), fill_session=date(2026, 1, 3),
        cutoff='2026-01-02T22:00:00+00:00', fill_completed_at='2026-01-03T21:00:00+00:00',
        full_target_available_at='2026-01-02T21:00:00+00:00', cvar_request=None,
        mandatory_reduce=False, forced_target=None, forced_source=None, forced_reason=None)
    values = {'TQQQ': 25., 'QQQM': 0., 'BOXX': 75. - cash}
    policy = replace(policy, opening=replace(policy.opening, nav=nav,
        security_values=tuple(values.items()), cash=cash), decisions=(first, second))
    rows = [replace(rows[0], security_returns=flat),
        NettingSession(second.fill_session, second.decision_session, 1., target, stress, 0.)]
    return rows, options | {'initial_nav': nav,
        'initial_inner_weights': {s: v / 100. for s, v in values.items()}, 'rs04_research_policy': policy}


def test_cvar_a1_actual_book_budget_boundary():
    from fractions import Fraction as F
    rows, options = cvar_a1_book_case()
    nav = options['rs04_research_policy'].opening.nav
    # Independent dollar oracle: both closes HOLD; no trades, fees or cash.
    oracle = 1 - F(175, 2) / F(nav)
    assert oracle > F(.125)
    try:
        result = run_offline_security_netting(rows, **options)
    except ValueError as exc:
        assert str(exc).startswith('RS04_CVAR_UNCOMPUTABLE:native book')
        return
    tail = result['rs04_research']['decisions'][0]['cvar']['constraint']
    assert tail['status'] == 'PROPOSAL_FEASIBLE'
    assert result['final_nav'] == 87.5
    assert all(row['fees'] == 0. for row in result['ledger'])
    actual_loss = 1 - F(result['final_nav']) / F(nav)
    assert actual_loss == oracle
    assert actual_loss <= F(.125), 'normalized certificate exceeds unchanged native-book tail budget'


@pytest.mark.parametrize('gap', [0., 9e-9, -9e-9])
@pytest.mark.parametrize('cash', [0., 2.])
@pytest.mark.parametrize('fixed', [False, True])
def test_cvar_a1_native_money_certificate_boundaries(gap, cash, fixed, monkeypatch):
    from dataclasses import replace
    from fractions import Fraction as F
    import us_equity_strategies.research.security_netting_runner as runner
    # The fixed first target pays nonzero 10/2 bps fees. Second close is HOLD.
    budget = .10005 + cash * .000002 + 4e-11 if fixed else .125
    rows, options = cvar_a1_book_case(nav=100. + gap, cash=cash, budget=budget, fixed=fixed)
    policy = options['rs04_research_policy']
    if fixed:
        x = dict(policy.decisions[0].full_target)
        risk, boxx = F(100) * F(x['TQQQ']), F(100) * F(x['BOXX'])
        fee = abs(risk - 25) / 1000 + abs(boxx - F(75 - cash)) / 5000
        endpoint = F(100) - risk / 2 - fee
        assert fee > 0
    else:
        fee, endpoint = F(), F(175, 2)
    oracle = 1 - endpoint / F(policy.opening.nav)
    guard = B5UnreadReturns(rows[0].security_returns)
    original = runner._rs04_constrain_tail
    def certify(*args):
        result = original(*args)
        assert guard.reads == 0
        guard.selected = True
        return result
    monkeypatch.setattr(runner, '_rs04_constrain_tail', certify)
    rows[0] = replace(rows[0], security_returns=guard)
    if oracle > F(budget):
        with pytest.raises(ValueError, match='RS04_CVAR_UNCOMPUTABLE:native book'):
            run_offline_security_netting(rows, **options)
        assert guard.reads == 0
        return
    if not fixed and cash == 2. and gap < 0:
        # The unchanged kernel preview itself rejects this tight HOLD budget.
        # This local certificate does not add a selector to recover that case.
        with pytest.raises(ValueError, match='RS04_CVAR_TAIL_REDUCTION_REQUIRED_NO_CERTIFIED_TARGET'):
            run_offline_security_netting(rows, **options)
        assert guard.reads == 0
        return
    result = run_offline_security_netting(rows, **options)
    assert guard.reads > 0
    receipt = result['rs04_research']['decisions'][0]['cvar']
    certificate = receipt['native_book_certificate']
    assert certificate['security_values'] == dict(policy.opening.security_values)
    assert certificate['cash'] == cash and certificate['nav'] == policy.opening.nav
    assert F(certificate['es_fraction']) == oracle <= F(certificate['budget_fraction']) == F(budget)
    assert receipt['execution_action']['kind'] == ('TARGET_WEIGHTS' if fixed else 'HOLD_POSITIONS')
    assert result['ledger'][0]['fees'] == pytest.approx(float(fee), abs=1e-14)
    assert result['ledger'][1]['fees'] == 0.
    assert result['ledger'][1]['security_end'] == result['ledger'][1]['security_pre']
    assert result['final_nav'] == pytest.approx(float(endpoint), abs=2e-14)
    assert 1 - F(result['final_nav']) / F(policy.opening.nav) <= F(budget)


def test_cvar_a1_wide_budget_does_not_reject_reconciled_native_gap():
    from fractions import Fraction as F
    rows, options = cvar_a1_book_case(budget=.2)
    result = run_offline_security_netting(rows, **options)
    receipt = result['rs04_research']['decisions'][0]['cvar']
    oracle = 1 - F(175, 2) / F(options['initial_nav'])
    assert receipt['constraint']['status'] == 'PROPOSAL_FEASIBLE'
    assert F(receipt['native_book_certificate']['es_fraction']) == oracle > F(.125)
    assert result['final_nav'] == 87.5 and all(row['fees'] == 0. for row in result['ledger'])


def test_cvar_a1_normalized_mandatory_anchor_is_not_native_certification():
    from dataclasses import replace
    from fractions import Fraction as F
    from us_equity_strategies.research.cvar_exposure_constraint import ApprovedAnchor, Action
    budget = .10005000004
    rows, options = cvar_a1_book_case(budget=budget)
    policy = options['rs04_research_policy']
    first = policy.decisions[0]
    anchor = ApprovedAnchor(Action('TARGET_WEIGHTS', {'TQQQ': .2, 'QQQM': 0., 'BOXX': .75}),
        'synthetic-a1-anchor', True, True)
    request = replace(first.cvar_request, anchor=anchor, known_inputs=first.cvar_request.known_inputs +
        (('anchor', anchor.source, '2026-01-01T20:00:00+00:00', '2026-01-01T21:00:00+00:00'),))
    policy = replace(policy, decisions=(replace(first, cvar_request=request), policy.decisions[1]))
    # Full fees, actual dollars and unchanged NAV put this anchor over budget.
    risk = F(100) * F(.2)
    endpoint = F(100) - risk / 2 - abs(risk - 25) / 1000
    assert 1 - endpoint / F(policy.opening.nav) > F(budget)
    guard = B5UnreadReturns(rows[0].security_returns)
    with pytest.raises(ValueError, match='RS04_CVAR_UNCOMPUTABLE:native book'):
        run_offline_security_netting([replace(rows[0], security_returns=guard), rows[1]],
            **(options | {'rs04_research_policy': policy}))
    assert guard.reads == 0
