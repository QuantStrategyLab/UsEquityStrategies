"""Synthetic CVaR contract tests; run only through the task's guarded harness."""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal, localcontext
from fractions import Fraction
import math
import unittest

from us_equity_strategies.research.cvar_exposure_constraint import (
    ENDPOINT,
    Action,
    ApprovedAnchor,
    CostSpec,
    DecisionBook,
    ExposureCap,
    HardPolicy,
    JointScenario,
    ScenarioSet,
    TailDescriptor,
    TailPolicy,
    constrain_tail_target,
    scenario_losses,
    weighted_cvar,
)


def descriptor():
    return TailDescriptor("synthetic-sleeve", 2, ENDPOINT, "USD", "ZERO_RETURN_CASH",
                          .95, "DECISION_NAV_FRACTION")


def ledger_oracle(weights, target, fill, earn, bps, multiplier=1, second_target=None):
    """Independent quantity/cash ledger with exact rationals, prices initially one.

    Targets are values divided by pre-trade NAV. The second close holds quantities
    unless an explicit second target is supplied. No projection formula is used.
    """
    def f(x):
        return Fraction(str(x))
    quantities = {s: f(w) for s, w in weights.items()}
    cash = 1 - sum(quantities.values())
    prices = {s: Fraction(1) for s in weights}
    fees = []
    for returns, instruction in ((fill, target), (earn, second_target)):
        prices = {s: prices[s] * (1 + f(returns[s])) for s in prices}
        nav = cash + sum(quantities[s] * prices[s] for s in prices)
        fee = Fraction(0)
        if instruction is not None:
            next_quantities = {s: f(instruction[s]) * nav / prices[s] for s in prices}
            for s in prices:
                trade = (next_quantities[s] - quantities[s]) * prices[s]
                cash -= trade
                fee += abs(trade) * f(bps[s]) / 10000 * f(multiplier)
            cash -= fee
            quantities = next_quantities
        fees.append(fee)
    return cash + sum(quantities[s] * prices[s] for s in prices), cash, fees


class WeightedCvarTests(unittest.TestCase):
    def test_fractional_atom_auxiliary_oracle(self):
        losses = [Fraction(1, 10), Fraction(1, 25), Fraction(-1, 100)]
        probabilities = [Fraction(1, 50), Fraction(2, 25), Fraction(9, 10)]
        oracle = min(z + sum(p * max(loss - z, 0) for loss, p in
                                 zip(losses, probabilities)) / Fraction(1, 20)
                     for z in losses)
        got = weighted_cvar(list(map(float, losses)), list(map(float, probabilities)), .95)
        self.assertAlmostEqual(got, float(oracle), places=14)
        self.assertAlmostEqual(got, .064, places=14)
        self.assertNotAlmostEqual(got, .052, places=6)

    def test_ties_boundary_and_zero_probability(self):
        self.assertAlmostEqual(weighted_cvar([999, .2, .2, -.1], [0, .02, .04, .94], .95), .2)
        self.assertAlmostEqual(weighted_cvar([.3, .1], [.05, .95], .95), .3)
        self.assertAlmostEqual(weighted_cvar([.2, .1, .2], [.02, .94, .04], .95), .2)

    def test_all_negative_es_is_not_clipped(self):
        self.assertAlmostEqual(weighted_cvar([-.3, -.1], [.97, .03], .95), -.18)

    def test_probability_and_loss_errors(self):
        cases = [([], []), ([1], []), ([1, 2], [1]), ([1, 2], [.2, .2]),
                 ([1, 2], [-.1, 1.1]), ([1], [0]), ([True], [1]), ([1], [True]),
                 ([math.nan], [1]), ([math.inf], [1]), ([1], [math.nan]),
                 ([1], [math.inf]), ([1], ["1"]), (["1"], [1]),
                 ([1, 2], [.5, .500000000001])]
        for losses, probabilities in cases:
            with self.subTest(losses=losses, probabilities=probabilities):
                with self.assertRaises(ValueError):
                    weighted_cvar(losses, probabilities, .95)

    def test_alpha_errors(self):
        for alpha in [0, 1, -.1, 2, True, math.nan, math.inf, ".95"]:
            with self.subTest(alpha=alpha), self.assertRaises(ValueError):
                weighted_cvar([.1], [1], alpha)

    def test_decimal_auxiliary_oracle_with_multiple_boundary_atoms(self):
        with localcontext() as ctx:
            ctx.prec = 60
            d = Decimal
            losses = list(map(d, [".8", ".3", ".3", "-.2"]))
            ps = list(map(d, [".011", ".017", ".031", ".941"]))
            oracle = min(z + sum(p * max(loss - z, d(0)) for loss, p in zip(losses, ps))
                         / d(".05") for z in losses)
        self.assertAlmostEqual(weighted_cvar(list(map(float, losses)), list(map(float, ps)), .95),
                               float(oracle), places=14)

    def test_same_equal_weight_sample_matches_existing_b3_signed_return_tail(self):
        from quant_platform_kit.research_stats.unified_report import build_unified_net_report

        # Thirty observations put 1.5 atoms in the tail, exercising its boundary.
        # These identical simple samples cross-check arithmetic, not horizons.
        for returns in [(-.1, -.04, *([.01] * 28)), (.01, .04, *([.1] * 28))]:
            with self.subTest(returns=returns):
                descriptive = build_unified_net_report(returns).metric("cvar95_return_per_period")
                self.assertEqual(descriptive.status, "COMPUTED")
                es = weighted_cvar(tuple(-r for r in returns), (1. / len(returns),) * len(returns), .95)
                self.assertAlmostEqual(es, -descriptive.value, places=14)


class KernelFixture(unittest.TestCase):
    def args(self):
        desc = descriptor()
        scenario = JointScenario("joint-1", 1., {"RISK": -.1}, {"RISK": -.05})
        return dict(
            decision_book=DecisionBook({"RISK": .4}, 100., "USD", 0.),
            proposal=Action("TARGET_WEIGHTS", {"RISK": .2}),
            approved_anchor=ApprovedAnchor(Action("TARGET_WEIGHTS", {"RISK": 0.}),
                                           "fixture-anchor", True, True),
            policy=TailPolicy(desc, .05, "fixture-tail", True, True),
            scenario_set=ScenarioSet(("RISK",), (scenario,), desc, "fixture-set", "fixture-source",
                                     True, True, False),
            bps_spec=CostSpec("FIXED_NONNEGATIVE_BPS", {"RISK": 10.}, "BPS_PER_SIDE", 1.,
                              "DIMENSIONLESS"),
            hard_policy=HardPolicy((ExposureCap({"RISK": 1.}, 1., "TARGET_ONLY"),),
                                   ("RISK",), "fixture-original-caps", True, True, True),
        )

    def raw(self, args, action=None):
        return scenario_losses(args["decision_book"], action or args["proposal"],
                               args["scenario_set"], args["bps_spec"])

    def assert_no_target(self, result, status):
        self.assertEqual(result.status, status)
        self.assertIsNone(result.action)
        self.assertIsNone(result.target_weights)
        self.assertTrue(result.no_order)
        self.assertFalse(result.live_ready)
        self.assertEqual(result.budget_effect, "NONE")


class ScenarioLedgerTests(KernelFixture):
    def test_contract_one_fill_endpoint_and_true_hold(self):
        args = self.args()
        result = self.raw(args)[0]
        nav, cash, fees = ledger_oracle({"RISK": .4}, {"RISK": .2}, {"RISK": -.1},
                                       {"RISK": -.05}, {"RISK": 10})
        self.assertEqual(nav, Fraction(".950232"))
        self.assertEqual(fees[1], 0)  # Explicit second-close HOLD.
        self.assertAlmostEqual(result.nav_ratio, float(nav), places=14)
        self.assertAlmostEqual(result.loss, .049768, places=14)
        self.assertAlmostEqual(result.G, .96, places=14)
        self.assertAlmostEqual(result.pre_fill_weights["RISK"], .375, places=14)
        self.assertAlmostEqual(result.k, .000175, places=14)
        self.assertAlmostEqual(result.cash_after_fee * result.G, float(cash), places=14)
        twice, _, second_fees = ledger_oracle({"RISK": .4}, {"RISK": .2}, {"RISK": -.1},
                                              {"RISK": -.05}, {"RISK": 10},
                                              second_target={"RISK": .2})
        self.assertEqual(twice, Fraction(".9502243536"))
        self.assertGreater(second_fees[1], 0)
        self.assertGreater(result.nav_ratio, float(twice))
        hold = self.raw(args, Action("HOLD_POSITIONS"))[0]
        hold_nav, _, hold_fees = ledger_oracle({"RISK": .4}, None, {"RISK": -.1},
                                              {"RISK": -.05}, {"RISK": 10})
        self.assertEqual(hold_nav, Fraction(".942"))
        self.assertEqual(sum(hold_fees), 0)
        self.assertAlmostEqual(hold.nav_ratio, float(hold_nav), places=14)
        self.assertAlmostEqual(hold.loss, .058, places=14)
        self.assertEqual(hold.k, 0.)
        self.assertNotEqual(hold.nav_ratio, self.raw(args, Action("TARGET_WEIGHTS", {"RISK": .4}))[0].nav_ratio)
        self.assertEqual(args["scenario_set"].descriptor.endpoint, "PRE_NEXT_FILL_AT_T_PLUS_2")

    def test_all_cash_still_loses_before_exit(self):
        r = self.raw(self.args(), Action("TARGET_WEIGHTS", {"RISK": 0.}))[0]
        self.assertAlmostEqual(r.nav_ratio, .95964, places=14)
        self.assertAlmostEqual(r.loss, .04036, places=14)

    def test_multiple_securities_boxx_joint_paths_and_single_net_fee(self):
        a = self.args()
        w = {"RISK": .4, "OTHER": .2, "BOXX": .35}
        x = {"RISK": .2, "OTHER": .25, "BOXX": .4}
        bps = {"RISK": 10., "OTHER": 7., "BOXX": 3.}
        paths = (({"RISK": -.1, "OTHER": .06, "BOXX": .001},
                  {"RISK": -.05, "OTHER": -.03, "BOXX": .002}),
                 ({"RISK": .08, "OTHER": -.12, "BOXX": -.001},
                  {"RISK": -.07, "OTHER": .09, "BOXX": .001}))
        a["decision_book"] = DecisionBook(w, 500., "USD", 0.)
        a["proposal"] = Action("TARGET_WEIGHTS", x)
        a["bps_spec"] = replace(a["bps_spec"], bps_by_symbol=bps, frozen_multiplier=2.)
        a["scenario_set"] = replace(a["scenario_set"], symbols=tuple(w), scenarios=tuple(
            JointScenario(str(i), .5, f, e) for i, (f, e) in enumerate(paths)))
        for r, (fill, earn) in zip(self.raw(a), paths):
            nav, cash, fees = ledger_oracle(w, x, fill, earn, bps, 2)
            self.assertAlmostEqual(r.nav_ratio, float(nav), places=14)
            self.assertAlmostEqual(r.G * r.k, float(sum(fees)), places=14)
            self.assertAlmostEqual(r.G * r.cash_after_fee, float(cash), places=14)
            self.assertEqual(fees[1], 0)
        no_boxx_fee = replace(a["bps_spec"], bps_by_symbol={**bps, "BOXX": 0.})
        no_fee_results = scenario_losses(a["decision_book"], a["proposal"], a["scenario_set"], no_boxx_fee)
        self.assertGreater(no_fee_results[0].nav_ratio, self.raw(a)[0].nav_ratio)

    def test_returns_coverage_and_values_rejected(self):
        a = self.args()
        scenario = a["scenario_set"].scenarios[0]
        for field in ["fill_returns", "earn_returns"]:
            for values in [{}, {"RISK": 0., "EXTRA": 0.}, {"RISK": -1.}, {"RISK": -2.},
                           {"RISK": math.inf}, {"RISK": math.nan}, {"RISK": True}]:
                with self.subTest(field=field, values=values), self.assertRaises(ValueError):
                    s = replace(scenario, **{field: values})
                    self.raw({**a, "scenario_set": replace(a["scenario_set"], scenarios=(s,))})

    def test_book_weights_target_currency_flow_and_nav_validation(self):
        a = self.args()
        for weights in [{}, {"RISK": -.1}, {"RISK": 1.01}, {"RISK": True}, {"RISK": math.nan}]:
            with self.subTest(weights=weights), self.assertRaises(ValueError):
                self.raw({**a, "decision_book": replace(a["decision_book"], weights=weights)})
        for field, value in [("nav", 0.), ("nav", -1.), ("nav", math.inf), ("nav", True),
                             ("currency", "EUR"), ("external_flow", .01)]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.raw({**a, "decision_book": replace(a["decision_book"], **{field: value})})
        with self.assertRaises(ValueError):
            self.raw(a, Action("TARGET_WEIGHTS", {}))

    def test_negative_loss_preserved_and_nav_only_ratio(self):
        a = self.args()
        s = replace(a["scenario_set"].scenarios[0], fill_returns={"RISK": .1}, earn_returns={"RISK": .2})
        a["scenario_set"] = replace(a["scenario_set"], scenarios=(s,))
        r = self.raw(a)[0]
        self.assertLess(r.loss, 0.)
        a["decision_book"] = replace(a["decision_book"], nav=100000.)
        self.assertEqual(self.raw(a)[0].nav_ratio, r.nav_ratio)

    def test_cash_and_positive_nav_feasibility_diagnostics(self):
        a = self.args()
        a["proposal"] = Action("TARGET_WEIGHTS", {"RISK": 1.})
        r = self.raw(a)[0]
        self.assertLess(r.cash_after_fee, 0.)
        self.assertFalse(r.feasible)
        a["bps_spec"] = replace(a["bps_spec"], bps_by_symbol={"RISK": 100000.})
        r = self.raw(a)[0]
        self.assertLessEqual(r.nav_ratio, 0.)
        self.assertFalse(r.feasible)

    def test_forecast_cost_does_not_mutate_or_debit_book(self):
        a = self.args()
        before = deepcopy(a)
        first, second = self.raw(a), self.raw(a)
        self.assertEqual(first, second)
        self.assertEqual(a, before)
        self.assertEqual(a["decision_book"].nav, 100.)


class ConstraintTests(KernelFixture):
    def test_proposal_preserved_without_anchor(self):
        a = self.args()
        a["approved_anchor"] = None
        r = constrain_tail_target(**a)
        self.assertEqual(r.status, "PROPOSAL_FEASIBLE")
        self.assertIs(r.action, a["proposal"])
        self.assertEqual(r.target_weights, {"RISK": .2})
        self.assertTrue(r.evaluation.feasible)
        self.assertEqual(r.evaluation.alpha_confidence, .95)
        self.assertAlmostEqual(r.evaluation.tail_probability, .05)
        self.assertTrue(r.evaluation.synthetic_scenarios)
        self.assertEqual(r.evaluation.scenario_set_id, a["scenario_set"].set_id)
        self.assertTrue(r.no_order)
        self.assertFalse(r.live_ready)
        self.assertEqual(r.budget_effect, "NONE")

    def test_budget_missing_and_nonfinite(self):
        a = self.args()
        for p in [None, replace(a["policy"], budget=None), replace(a["policy"], source=""),
                  replace(a["policy"], approved=False)]:
            self.assert_no_target(constrain_tail_target(**{**a, "policy": p}), "UNAVAILABLE")
        for value in [math.nan, math.inf, True]:
            self.assert_no_target(constrain_tail_target(**{**a, "policy": replace(a["policy"], budget=value)}),
                                  "REJECTED_INPUT")

    def test_all_descriptor_mismatches_unavailable(self):
        a = self.args()
        changes = dict(scope="another-sleeve", horizon_sessions=1, endpoint="POST_SECOND_FILL",
                       currency="EUR", benchmark="BOXX", alpha_confidence=.99, loss_unit="PERCENT")
        for field, value in changes.items():
            with self.subTest(field=field):
                bad = replace(a["policy"], descriptor=replace(descriptor(), **{field: value}))
                self.assert_no_target(constrain_tail_target(**{**a, "policy": bad}), "UNAVAILABLE")
        for field, value in [("horizon_sessions", 3), ("endpoint", "POST_SECOND_FILL")]:
            d = replace(descriptor(), **{field: value})
            self.assert_no_target(constrain_tail_target(**{**a, "policy": replace(a["policy"], descriptor=d),
                                  "scenario_set": replace(a["scenario_set"], descriptor=d)}), "UNAVAILABLE")

    def test_missing_scenario_identity_probability_and_coverage(self):
        a = self.args()
        ss = a["scenario_set"]
        variants = [None, replace(ss, source=""), replace(ss, set_id=""), replace(ss, coverage_available=False),
                    replace(ss, symbols=()), replace(ss, scenarios=()),
                    replace(ss, scenarios=(replace(ss.scenarios[0], scenario_id=""),)),
                    replace(ss, scenarios=(replace(ss.scenarios[0], probability=None),)),
                    replace(ss, scenarios=(replace(ss.scenarios[0], earn_returns={}),))]
        for ss in variants:
            with self.subTest(scenario_set=ss):
                self.assert_no_target(constrain_tail_target(**{**a, "scenario_set": ss}), "UNAVAILABLE")

    def test_synthetic_real_identity_cannot_mix(self):
        a = self.args()
        for field in ["policy", "hard_policy"]:
            self.assert_no_target(constrain_tail_target(**{**a, field: replace(a[field], synthetic_policy=False)}),
                                  "REJECTED_INPUT")
        self.assert_no_target(constrain_tail_target(**{**a, "scenario_set": replace(a["scenario_set"], synthetic=False)}),
                              "REJECTED_INPUT")

    def test_real_inputs_require_external_quality_attestation(self):
        a = self.args()
        a["policy"] = replace(a["policy"], synthetic_policy=False)
        a["hard_policy"] = replace(a["hard_policy"], synthetic_policy=False)
        a["approved_anchor"] = None
        a["scenario_set"] = replace(a["scenario_set"], synthetic=False)
        self.assert_no_target(constrain_tail_target(**a), "UNAVAILABLE")
        a["scenario_set"] = replace(a["scenario_set"], quality_attested=True)
        r = constrain_tail_target(**a)
        self.assertEqual(r.status, "PROPOSAL_FEASIBLE")
        self.assertFalse(r.live_ready)
        self.assertEqual(r.budget_effect, "NONE")
        self.assertFalse(r.evaluation.synthetic_scenarios)

    def test_nonfinite_data_rejected_by_consumer(self):
        a = self.args()
        s = replace(a["scenario_set"].scenarios[0], fill_returns={"RISK": math.nan})
        self.assert_no_target(constrain_tail_target(**{**a, "scenario_set": replace(a["scenario_set"], scenarios=(s,))}),
                              "REJECTED_INPUT")

    def test_unsupported_cost_models_and_units(self):
        a = self.args()
        for model in ["FIXED_DOLLAR", "MINIMUM_COMMISSION", "MARKET_IMPACT", "FINANCING", "UNKNOWN"]:
            with self.subTest(model=model):
                self.assert_no_target(constrain_tail_target(**{**a, "bps_spec": replace(a["bps_spec"], model=model)}),
                                      "UNSUPPORTED_COST_MODEL")
        for field, value in [("unit", "DECIMAL"), ("multiplier_unit", "BPS")]:
            self.assert_no_target(constrain_tail_target(**{**a, "bps_spec": replace(a["bps_spec"], **{field: value})}),
                                  "UNSUPPORTED_COST_MODEL")
        for field, value in [("frozen_multiplier", -1.), ("frozen_multiplier", math.nan),
                             ("bps_by_symbol", {"RISK": -1.}), ("bps_by_symbol", {"RISK": True})]:
            self.assert_no_target(constrain_tail_target(**{**a, "bps_spec": replace(a["bps_spec"], **{field: value})}),
                                  "REJECTED_INPUT")

    def test_conservative_boundary_bisection_recomputed_against_budget(self):
        a = self.args()
        a["policy"] = replace(a["policy"], budget=.045)
        r = constrain_tail_target(**a)
        self.assertEqual(r.status, "TAIL_TARGET_TIGHTENED")
        self.assertGreater(r.lambda_value, 0.)
        self.assertLess(r.lambda_value, 1.)
        self.assertLessEqual(r.iterations, 60)
        actual = weighted_cvar([s.loss for s in self.raw(a, r.action)], [1.], .95)
        self.assertLessEqual(actual, .045)  # No upward comparison tolerance.
        self.assertLess(.045 - actual, 1e-10)
        self.assertLessEqual(r.bracket_width, 1e-10)
        self.assertTrue(r.mandatory_reduce)
        self.assertTrue(r.bypass_b5_throttle)
        self.assertEqual(r.evaluation.es, actual)

    def test_convex_nonmonotonic_es_path(self):
        a = self.args()
        a["decision_book"] = replace(a["decision_book"], weights={"RISK": .4})
        a["proposal"] = Action("TARGET_WEIGHTS", {"RISK": .9})
        s = replace(a["scenario_set"].scenarios[0], fill_returns={"RISK": 0.}, earn_returns={"RISK": 0.})
        a["scenario_set"] = replace(a["scenario_set"], scenarios=(s,))
        a["bps_spec"] = replace(a["bps_spec"], bps_by_symbol={"RISK": 1000.})
        a["policy"] = replace(a["policy"], budget=.045)
        losses = [self.raw(a, Action("TARGET_WEIGHTS", {"RISK": x}))[0].loss for x in [0., .4, .9]]
        self.assertLess(losses[1], losses[0])
        self.assertGreater(losses[2], losses[0])
        r = constrain_tail_target(**a)
        self.assertEqual(r.status, "TAIL_TARGET_TIGHTENED")
        self.assertAlmostEqual(r.target_weights["RISK"], .85, places=9)
        self.assertLessEqual(r.evaluation.es, a["policy"].budget)

    def test_anchor_failure_is_not_global_infeasibility_even_all_cash(self):
        a = self.args()
        a["policy"] = replace(a["policy"], budget=.03)
        self.assert_no_target(constrain_tail_target(**a), "NO_CERTIFIED_FEASIBLE_FALLBACK")
        self.assertGreater(self.raw(a, a["approved_anchor"].action)[0].loss, .03)

    def test_anchor_missing_unapproved_source_and_synthetic_mismatch(self):
        a = self.args()
        a["policy"] = replace(a["policy"], budget=.045)
        anchor = a["approved_anchor"]
        for candidate in [None, replace(anchor, approved=False), replace(anchor, source=""),
                          replace(anchor, synthetic_policy=False), replace(anchor, action=Action("HOLD_POSITIONS"))]:
            with self.subTest(anchor=candidate):
                self.assert_no_target(constrain_tail_target(**{**a, "approved_anchor": candidate}),
                                      "NO_CERTIFIED_FEASIBLE_FALLBACK")

    def test_hold_failure_direct_anchor_no_interpolation(self):
        a = self.args()
        a["proposal"] = Action("HOLD_POSITIONS")
        r = constrain_tail_target(**a)
        self.assertEqual(r.status, "MANDATORY_TAIL_REDUCTION")
        self.assertIs(r.action, a["approved_anchor"].action)
        self.assertEqual(r.iterations, 0)
        self.assertIsNone(r.lambda_value)
        self.assertTrue(r.mandatory_reduce)
        self.assertTrue(r.bypass_b5_throttle)
        a["approved_anchor"] = None
        self.assert_no_target(constrain_tail_target(**a), "TAIL_REDUCTION_REQUIRED_NO_CERTIFIED_TARGET")

    def test_hold_pass_keeps_real_hold(self):
        a = self.args()
        a["proposal"] = Action("HOLD_POSITIONS")
        a["policy"] = replace(a["policy"], budget=.06)
        a["approved_anchor"] = None
        r = constrain_tail_target(**a)
        self.assertEqual(r.status, "PROPOSAL_FEASIBLE")
        self.assertIs(r.action, a["proposal"])
        self.assertIsNone(r.target_weights)
        self.assertEqual(r.evaluation.scenarios[0].k, 0.)

    def test_reduce_only_each_security_and_forced_caps(self):
        a = self.args()
        a["policy"] = replace(a["policy"], budget=.045)
        a["approved_anchor"] = replace(a["approved_anchor"], action=Action("TARGET_WEIGHTS", {"RISK": .3}))
        self.assert_no_target(constrain_tail_target(**a), "NO_CERTIFIED_FEASIBLE_FALLBACK")
        a = self.args()
        a["approved_anchor"] = replace(a["approved_anchor"], action=Action("TARGET_WEIGHTS", {"RISK": .1}))
        self.assert_no_target(constrain_tail_target(**a, forced_target={"RISK": .05}),
                              "NO_CERTIFIED_FEASIBLE_FALLBACK")
        a["approved_anchor"] = replace(a["approved_anchor"], action=Action("TARGET_WEIGHTS", {"RISK": 0.}))
        r = constrain_tail_target(**a, forced_target={"RISK": .05})
        self.assertEqual(r.status, "TAIL_TARGET_TIGHTENED")
        self.assertLessEqual(r.target_weights["RISK"], .05)

    def test_aggregate_reduction_does_not_allow_cross_security_risk_increase(self):
        a = self.args()
        a["decision_book"] = DecisionBook({"RISK": .4, "OTHER": .2}, 100., "USD", 0.)
        a["proposal"] = Action("TARGET_WEIGHTS", {"RISK": .4, "OTHER": .2})
        a["approved_anchor"] = replace(a["approved_anchor"], action=Action(
            "TARGET_WEIGHTS", {"RISK": 0., "OTHER": .21}))
        a["bps_spec"] = replace(a["bps_spec"], bps_by_symbol={"RISK": 10., "OTHER": 10.})
        s = JointScenario("two-risks", 1., {"RISK": -.1, "OTHER": 0.},
                          {"RISK": -.2, "OTHER": 0.})
        a["scenario_set"] = replace(a["scenario_set"], symbols=("RISK", "OTHER"), scenarios=(s,))
        a["hard_policy"] = replace(a["hard_policy"], risk_symbols=("RISK", "OTHER"),
            caps=(ExposureCap({"RISK": 1., "OTHER": 1.}, 1., "TARGET_ONLY"),))
        self.assert_no_target(constrain_tail_target(**a), "NO_CERTIFIED_FEASIBLE_FALLBACK")
        a["proposal"] = Action("HOLD_POSITIONS")
        self.assert_no_target(constrain_tail_target(**a), "TAIL_REDUCTION_REQUIRED_NO_CERTIFIED_TARGET")

    def test_hold_anchor_cannot_increase_any_risk_security(self):
        a = self.args()
        a["proposal"] = Action("HOLD_POSITIONS")
        a["policy"] = replace(a["policy"], budget=-.1)
        a["approved_anchor"] = replace(a["approved_anchor"], action=Action("TARGET_WEIGHTS", {"RISK": .41}))
        self.assert_no_target(constrain_tail_target(**a), "TAIL_REDUCTION_REQUIRED_NO_CERTIFIED_TARGET")

    def test_cash_cannot_fund_fee_even_if_es_passes(self):
        a = self.args()
        a["policy"] = replace(a["policy"], budget=1.)
        a["proposal"] = Action("TARGET_WEIGHTS", {"RISK": 1.})
        a["approved_anchor"] = None
        self.assert_no_target(constrain_tail_target(**a), "NO_CERTIFIED_FEASIBLE_FALLBACK")

    def test_nonpositive_nav_never_passes_a_large_budget(self):
        a = self.args()
        a["policy"] = replace(a["policy"], budget=100.)
        a["proposal"] = Action("TARGET_WEIGHTS", {"RISK": 1.})
        a["bps_spec"] = replace(a["bps_spec"], bps_by_symbol={"RISK": 100000.})
        a["approved_anchor"] = None
        self.assert_no_target(constrain_tail_target(**a), "NO_CERTIFIED_FEASIBLE_FALLBACK")

    def test_postfee_hard_cap_actual_check_not_target_only(self):
        a = self.args()
        cap = ExposureCap({"RISK": 1.}, .2, "POST_FEE")
        a["hard_policy"] = replace(a["hard_policy"], caps=(cap,))
        a["approved_anchor"] = None
        self.assert_no_target(constrain_tail_target(**a), "NO_CERTIFIED_FEASIBLE_FALLBACK")
        a["approved_anchor"] = self.args()["approved_anchor"]
        r = constrain_tail_target(**a)
        self.assertEqual(r.status, "TAIL_TARGET_TIGHTENED")
        for row in r.evaluation.scenarios:
            self.assertLessEqual(r.target_weights["RISK"] / (1 - row.k), cap.cap)
            self.assertLessEqual(r.target_weights["RISK"] + cap.cap * row.k, cap.cap)

    def test_target_cap_and_explicit_cash_cap_preserved(self):
        a = self.args()
        a["hard_policy"] = replace(a["hard_policy"], caps=(ExposureCap({"RISK": 1.}, .1, "TARGET_ONLY"),))
        r = constrain_tail_target(**a)
        self.assertLessEqual(r.target_weights["RISK"], .1)
        self.assertTrue(r.evaluation.feasible)

    def test_nonconvex_unknown_hard_policy_and_missing_original_caps(self):
        a = self.args()
        for hp in [replace(a["hard_policy"], convex=False),
                   replace(a["hard_policy"], caps=(ExposureCap({"RISK": 1.}, 1., "UNKNOWN"),))]:
            self.assert_no_target(constrain_tail_target(**{**a, "hard_policy": hp}), "UNSUPPORTED_HARD_POLICY")
        for hp in [None, replace(a["hard_policy"], source=""), replace(a["hard_policy"], approved=False),
                   replace(a["hard_policy"], risk_symbols=None), replace(a["hard_policy"], caps=None)]:
            self.assert_no_target(constrain_tail_target(**{**a, "hard_policy": hp}), "UNAVAILABLE")

    def test_bad_probability_and_duplicate_joint_identity_rejected(self):
        a = self.args()
        ss = a["scenario_set"]
        for ps in [(replace(ss.scenarios[0], probability=.9),),
                   (replace(ss.scenarios[0], probability=math.nan),),
                   (replace(ss.scenarios[0], probability=.5), replace(ss.scenarios[0], probability=.5))]:
            self.assert_no_target(constrain_tail_target(**{**a, "scenario_set": replace(ss, scenarios=ps)}),
                                  "REJECTED_INPUT")

    def test_cap_coefficients_and_authority_flags_are_explicit(self):
        a = self.args()
        for coefficients in [{}, {"RISK": -1.}, {"RISK": True}, {"RISK": math.inf}]:
            hp = replace(a["hard_policy"], caps=(ExposureCap(coefficients, 1., "TARGET_ONLY"),))
            status = "UNAVAILABLE" if not coefficients else "REJECTED_INPUT"
            self.assert_no_target(constrain_tail_target(**{**a, "hard_policy": hp}), status)
        for hp in [replace(a["hard_policy"], approved=1), replace(a["hard_policy"], convex=1),
                   replace(a["hard_policy"], risk_symbols=("MISSING",))]:
            self.assert_no_target(constrain_tail_target(**{**a, "hard_policy": hp}), "REJECTED_INPUT")

    def test_nonfinite_intermediate_is_uncomputable(self):
        a = self.args()
        s = replace(a["scenario_set"].scenarios[0], fill_returns={"RISK": 1e308}, earn_returns={"RISK": 1e308})
        a["scenario_set"] = replace(a["scenario_set"], scenarios=(s,))
        self.assert_no_target(constrain_tail_target(**a), "UNCOMPUTABLE")

    def test_consumer_never_mutates_inputs_or_records_real_cost(self):
        a = self.args()
        a["policy"] = replace(a["policy"], budget=.045)
        before = deepcopy(a)
        first = constrain_tail_target(**a)
        second = constrain_tail_target(**a)
        self.assertEqual(a, before)
        self.assertEqual(first, second)
        self.assertEqual(a["decision_book"].nav, 100.)
        self.assertTrue(first.no_order)
        self.assertFalse(first.live_ready)
        self.assertEqual(first.budget_effect, "NONE")


if __name__ == "__main__":
    unittest.main()
