"""Synthetic B1/B2 comparison tests. Fixture values are engineering inputs, not attested parameters."""
import dataclasses

import pytest

from us_equity_strategies.research import b1_b2_synthetic_comparison as cmp
from us_equity_strategies.research.b1_b2_rs04_preparation import PreparationStop

SEED = 20261014
TOL = 1e-12
LIMITS = cmp.ScopeLimits(total_gross_cap=0.97, cash_reserve=0.03, other_gross_exposure=0.0)
REGIMES = ((0.0008, 0.01), (-0.0015, 0.035), (0.0004, 0.018))
B1 = cmp.B1Fixture(existing_cap=0.8, f_star=1.6, target_vol=0.012, ewma_decay=0.94,
                   ewma_initial_variance=1e-4, ewma_warmup=20, c_for_vol_target_row=0.5)
B2 = cmp.B2Fixture(base_exposure=0.6, d_max=0.25, ladder_full_until=0.10, ladder_half_until=0.20)


def _returns(n=756, seed=SEED):
    return cmp.synthetic_returns(seed=seed, sessions=n, regimes=REGIMES, regime_length=63)


def test_row_sets_match_freeze_contract_and_no_cartesian():
    out = cmp.run_comparison(_returns(), b1=B1, b2=B2, limits=LIMITS)
    assert tuple(out["b1"]) == cmp.B1_ROWS
    assert tuple(out["b2"]) == cmp.B2_ROWS
    assert out["b1_x_b2_cartesian"] is False
    assert out["trial_count"] == 6
    for flag in ("live_ready", "research_frozen", "fixture_values_attested", "fees_modelled", "winner_declared"):
        assert out[flag] is False
    assert out["no_order"] is True and out["data"] == "SYNTHETIC"


def test_deterministic():
    assert cmp.run_comparison(_returns(), b1=B1, b2=B2, limits=LIMITS) == cmp.run_comparison(_returns(), b1=B1, b2=B2, limits=LIMITS)


def test_exposures_never_exceed_cap_and_c_ordering():
    rows = cmp.b1_exposures(_returns(), B1, limits=LIMITS)
    for name, path in rows.items():
        assert all(x is None or 0.0 <= x <= B1.existing_cap + TOL for x in path), name
    for a, b in zip(rows["kelly_c_0.25_once"], rows["kelly_c_0.5_once"]):
        assert a <= b + TOL
    assert all(x == pytest.approx(0.4) for x in rows["kelly_c_0.25_once"])
    assert all(x == pytest.approx(0.8) for x in rows["kelly_c_0.5_once"])  # 0.5*1.6 clipped by cap
    assert all(x == pytest.approx(0.5) for x in rows["fixed_50_subject_to_original_cap"])


def test_fixed_50_is_clipped_by_original_cap():
    low = dataclasses.replace(B1, existing_cap=0.3)
    rows = cmp.b1_exposures(_returns(100), low, limits=LIMITS)
    assert all(x == pytest.approx(0.3) for x in rows["fixed_50_subject_to_original_cap"])


def test_vol_target_row_never_exceeds_kelly_leg_and_parks_during_warmup():
    rows = cmp.b1_exposures(_returns(), B1, limits=LIMITS)
    vt = rows["ewma_vol_target_and_kelly_cap"]
    assert all(x == 0.0 for x in vt[: B1.ewma_warmup])
    kelly = min(B1.existing_cap, 0.5 * B1.f_star)
    assert all(x <= kelly + TOL for x in vt)
    assert any(x < kelly - 1e-6 for x in vt[B1.ewma_warmup:])  # high-vol regime de-risks


def test_causal_prefix_future_perturbation_does_not_change_past():
    base = list(_returns(300))
    alt = base[:200] + [r * -3 for r in base[200:]]
    for fn, fx in ((cmp.b1_exposures, B1), (cmp.b2_exposures, B2)):
        kw = {"limits": LIMITS}
        if fn is cmp.b2_exposures:
            kw["existing_cap"] = B1.existing_cap
        a, b = fn(base, fx, **kw), fn(alt, fx, **kw)
        for name in a:
            assert a[name][:201] == b[name][:201], name


def test_c_outside_allowed_set_rejected():
    for bad in (0.3, 0.75, 1.0):
        with pytest.raises(PreparationStop, match="C_NOT_IN_ALLOWED_SET"):
            cmp.b1_exposures(_returns(10), dataclasses.replace(B1, c_for_vol_target_row=bad), limits=LIMITS)


def test_missing_cap_is_uncomputable_not_one():
    out = cmp.run_comparison(_returns(50), b1=dataclasses.replace(B1, existing_cap=None), b2=B2, limits=LIMITS)
    assert all(row["status"] == "UNCOMPUTABLE" for row in out["b1"].values())


def test_b2_continuous_is_monotone_and_never_above_base():
    r = [0.0, -0.05, -0.05, -0.05, -0.05, 0.02]
    rows = cmp.b2_exposures(r, B2, existing_cap=B1.existing_cap, limits=LIMITS)
    cont = rows["continuous_drawdown"]
    assert cont[0] == pytest.approx(B2.base_exposure)
    assert all(cont[i + 1] <= cont[i] + TOL for i in range(4))
    assert all(x <= B2.base_exposure + TOL for path in rows.values() for x in path)
    assert set(rows["original_ladder"]) <= {B2.base_exposure, B2.base_exposure * 0.5, 0.0}


def test_b2_step_vs_continuous_differ_on_synthetic_drawdown():
    out = cmp.run_comparison(_returns(), b1=B1, b2=B2, limits=LIMITS)
    ladder, cont = out["b2"]["original_ladder"], out["b2"]["continuous_drawdown"]
    assert ladder["gross_turnover"] != pytest.approx(cont["gross_turnover"])
    assert ladder["turnover_and_growth_summable"] is False


def test_har_parked_b4_b5_rejected_unknown_row_stops():
    out = cmp.run_comparison(_returns(50), b1=B1, b2=B2, limits=LIMITS, requested_rows=(
        "har_vol_target", "bayesian_kelly", "partial_rebalance", "kelly_c_0.25_once", "continuous_drawdown"))
    assert out["parked"] == {"har_vol_target": "HAR_PARKED_DAILY_SQUARE_IS_NOT_RV"}
    assert set(out["rejected"]) == {"bayesian_kelly", "partial_rebalance"}
    assert tuple(out["b1"]) == ("kelly_c_0.25_once",) and tuple(out["b2"]) == ("continuous_drawdown",)
    with pytest.raises(PreparationStop, match="ROW_NOT_REGISTERED"):
        cmp.run_comparison(_returns(10), b1=B1, b2=B2, limits=LIMITS, requested_rows=("kelly_c_1.0",))


def test_fixture_dataclasses_have_no_defaults():
    for cls in (cmp.B1Fixture, cmp.B2Fixture, cmp.ScopeLimits):
        assert all(f.default is dataclasses.MISSING for f in dataclasses.fields(cls))


def test_gross_drawdown_has_the_same_signed_definition_and_initial_nav_as_net_report():
    row = cmp._path_stats([-.2, .25, 0.], [1., 1., 1.])
    assert row["max_drawdown"] == pytest.approx(-.2)
    assert row["final_gross_wealth"] == pytest.approx(1.)
    assert row["mean_log_growth"] == pytest.approx(0., abs=TOL)
    assert row["return_basis"] == "gross_before_costs"
    assert row["drawdown_sign"] == "signed_nonpositive_includes_initial_nav"


@pytest.mark.parametrize("cap,total,cash,other,expected", [
    (.679, .97, .03, 0., .679),
    (1., .97, .03, 0., .97),
    (1., 1., .03, 0., .97),
    (.679, .97, .03, .5, .47),
    (.679, .97, .4, .1, .5),
])
def test_all_rows_share_product_gross_and_cash_caps(cap, total, cash, other, expected):
    limits = cmp.ScopeLimits(total, cash, other)
    out = cmp.run_comparison([0., -.05, .02], b1=dataclasses.replace(B1, existing_cap=cap),
                             b2=dataclasses.replace(B2, base_exposure=1.), limits=limits)
    for table in (out["b1"], out["b2"]):
        for row in table.values():
            assert row["max_exposure"] <= expected + TOL
            assert row["max_exposure"] + other <= total + TOL
            assert 1 - row["max_exposure"] - other >= cash - TOL
    assert out["b2"]["original_ladder"]["max_exposure"] == pytest.approx(expected)


@pytest.mark.parametrize("field", ["total_gross_cap", "cash_reserve", "other_gross_exposure"])
def test_missing_scope_constraint_makes_both_tables_uncomputable(field):
    out = cmp.run_comparison([0., .1], b1=B1, b2=B2,
                             limits=dataclasses.replace(LIMITS, **{field: None}))
    assert all(row["status"] == "UNCOMPUTABLE" for key in ("b1", "b2") for row in out[key].values())


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_limits_and_base_reject(bad):
    with pytest.raises(PreparationStop):
        cmp.run_comparison([0.], b1=B1, b2=B2, limits=dataclasses.replace(LIMITS, cash_reserve=bad))
    with pytest.raises(PreparationStop):
        cmp.run_comparison([0.], b1=B1, b2=dataclasses.replace(B2, base_exposure=bad), limits=LIMITS)


def test_existing_other_exposure_cannot_be_repaired_by_negative_new_exposure():
    with pytest.raises(PreparationStop, match="OTHER_EXPOSURE_EXCEEDS_SCOPE_BUDGET"):
        cmp.run_comparison([0.], b1=B1, b2=B2,
                           limits=cmp.ScopeLimits(.97, .03, .98))
