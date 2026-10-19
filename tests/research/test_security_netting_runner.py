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
