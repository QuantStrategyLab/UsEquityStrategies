"""Artificial policy/scenario examples, not return-path backtests."""
from dataclasses import replace
import math
import unittest

from us_equity_strategies.research.capital_adaptive_risk import (
    CapitalProtectionCurve, capital_protection_factor, preview_capital_adaptive_target,
)
from us_equity_strategies.research.cvar_exposure_constraint import (
    ENDPOINT, Action, ApprovedAnchor, CostSpec, DecisionBook, ExposureCap,
    HardPolicy, JointScenario, ScenarioSet, TailDescriptor, TailPolicy,
)


def fixture():
    descriptor = TailDescriptor("ARTIFICIAL_SCOPE", 2, ENDPOINT, "USD", "ZERO_RETURN_CASH", .95, "DECISION_NAV_FRACTION")
    return dict(
        level="balanced",
        presets={level: TailPolicy(descriptor, budget, "ARTIFICIAL_PRESET", True, True)
                 for level, budget in zip(("conservative", "balanced", "growth"), (.01, .02, .04))},
        account_policy=TailPolicy(descriptor, .1, "ARTIFICIAL_ACCOUNT_CAP", True, True),
        curve=CapitalProtectionCurve(100., 1., 100., "USD", "ARTIFICIAL_CURVE"),
        proposal=Action("TARGET_WEIGHTS", {"A": .4, "B": .4}),
        approved_anchor=ApprovedAnchor(Action("TARGET_WEIGHTS", {"A": 0., "B": 0.}), "ARTIFICIAL_ANCHOR", True, True),
        decision_book=DecisionBook({"A": 0., "B": 0.}, 100., "USD", 0.),
        scenario_set=ScenarioSet(("A", "B"), (
            JointScenario("down", .5, {"A": 0., "B": 0.}, {"A": -.2, "B": -.2}),
            JointScenario("up", .5, {"A": 0., "B": 0.}, {"A": .2, "B": .2}),
        ), descriptor, "ARTIFICIAL_SCENARIOS", "ARTIFICIAL_SOURCE", True, True, False),
        bps_spec=CostSpec("FIXED_NONNEGATIVE_BPS", {"A": 0., "B": 0.}, "BPS_PER_SIDE", 1., "DIMENSIONLESS"),
        hard_policy=HardPolicy((ExposureCap({"A": 1., "B": 1.}, 1., "POST_FEE"),), ("A", "B"), "ARTIFICIAL_HARD_CAP", True, True, True),
    )


class CapitalAdaptiveRiskTests(unittest.TestCase):
    def test_curve_hand_calculation(self):
        self.assertAlmostEqual(capital_protection_factor(CapitalProtectionCurve(100., 1., 100., "USD", "fixture")), .5)

    def test_curve_continuous_and_nonincreasing(self):
        values=[capital_protection_factor(CapitalProtectionCurve(100., .5, x, "USD", "fixture")) for x in (10., 50., 100., 200., 10000.)]
        self.assertTrue(all(a > b for a,b in zip(values, values[1:])))
        left=capital_protection_factor(CapitalProtectionCurve(100., .5, 100.-1e-6, "USD", "fixture"))
        right=capital_protection_factor(CapitalProtectionCurve(100., .5, 100.+1e-6, "USD", "fixture"))
        self.assertLess(abs(left-right),1e-8)

    def test_larger_capital_does_not_reduce_dollar_budget_for_power_at_most_one(self):
        amounts=[x*.02*capital_protection_factor(CapitalProtectionCurve(100., .5, x, "USD", "fixture")) for x in (10., 100., 1000.)]
        self.assertTrue(all(a < b for a,b in zip(amounts,amounts[1:])))

    def test_invalid_curve_rejected(self):
        for ref,power,basis in [(0.,1.,100.),(100.,0.,100.),(100.,-1.,100.),(100.,1.1,100.),(True,1.,100.),(100.,1.,math.inf)]:
            with self.subTest(ref=ref,power=power,basis=basis), self.assertRaises(ValueError):
                capital_protection_factor(CapitalProtectionCurve(ref,power,basis,"USD","fixture"))

    def test_three_levels_order_budget_and_joint_loss_constrained_targets(self):
        outputs=[]
        for level in ("conservative","balanced","growth"):
            args=fixture();args['level']=level
            result=preview_capital_adaptive_target(**args);outputs.append(result)
            self.assertEqual(result['status'],'TAIL_TARGET_TIGHTENED')
            self.assertLessEqual(result['decision'].evaluation.es,result['effective_tail_budget_ratio'])
            self.assertFalse(result['execution_authorized'])
            self.assertFalse(result['financial_risk_certified'])
            self.assertFalse(result['account_policy_bound'])
        budgets=[x['effective_tail_budget_ratio'] for x in outputs]
        self.assertEqual(budgets,[.005,.01,.02])
        exposures=[sum(x['decision'].action.target_weights.values()) for x in outputs]
        self.assertTrue(all(a < b for a,b in zip(exposures,exposures[1:])))

    def test_absolute_tail_amount_and_account_limit_both_tighten(self):
        args=fixture();args['absolute_tail_loss_limit']=.25
        result=preview_capital_adaptive_target(**args)
        self.assertAlmostEqual(result['effective_tail_budget_ratio'],.0025)
        self.assertAlmostEqual(result['effective_tail_budget_amount'],.25)
        args=fixture();args['account_policy']=replace(args['account_policy'],budget=.001)
        self.assertEqual(preview_capital_adaptive_target(**args)['effective_tail_budget_ratio'],.001)

    def test_drawdown_does_not_relax_relative_budget_with_protected_basis(self):
        args=fixture();before=preview_capital_adaptive_target(**args)
        args['decision_book']=replace(args['decision_book'],nav=80.)
        args['previous_tail_budget_ratio']=before['effective_tail_budget_ratio']
        after=preview_capital_adaptive_target(**args)
        self.assertLessEqual(after['effective_tail_budget_ratio'],before['effective_tail_budget_ratio'])

    def test_lower_curve_basis_than_nav_rejected(self):
        args=fixture();args['curve']=replace(args['curve'],protected_capital=99.)
        result=preview_capital_adaptive_target(**args)
        self.assertEqual(result['status'],'REJECTED_INPUT');self.assertIsNone(result['decision'])

    def test_missing_account_policy_does_not_get_created_from_presets(self):
        args=fixture();args['account_policy']=None
        result=preview_capital_adaptive_target(**args)
        self.assertEqual(result['status'],'UNBOUND_ACCOUNT_POLICY');self.assertIsNone(result['decision'])

    def test_unapproved_account_policy_is_not_armed(self):
        args=fixture();args['account_policy']=replace(args['account_policy'],approved=False)
        result=preview_capital_adaptive_target(**args)
        self.assertEqual(result['status'],'UNBOUND_ACCOUNT_POLICY');self.assertIsNone(result['decision'])

    def test_real_scenarios_are_outside_this_offline_adapter(self):
        args=fixture();args['scenario_set']=replace(args['scenario_set'],synthetic=False,quality_attested=True)
        result=preview_capital_adaptive_target(**args)
        self.assertEqual(result['status'],'SYNTHETIC_ONLY');self.assertIsNone(result['decision'])

    def test_scope_and_currency_mismatch_rejected(self):
        for field in ('scope','currency','horizon_sessions'):
            args=fixture();desc=replace(args['account_policy'].descriptor,**{field:3 if field=='horizon_sessions' else 'OTHER'})
            args['account_policy']=replace(args['account_policy'],descriptor=desc)
            self.assertEqual(preview_capital_adaptive_target(**args)['status'],'REJECTED_INPUT')

    def test_presets_must_be_ordered(self):
        args=fixture();args['presets']['conservative']=replace(args['presets']['conservative'],budget=.08)
        self.assertEqual(preview_capital_adaptive_target(**args)['status'],'REJECTED_INPUT')

    def test_unknown_level_does_not_default_to_balanced(self):
        args=fixture();args['level']='unknown'
        self.assertEqual(preview_capital_adaptive_target(**args)['status'],'REJECTED_INPUT')

    def test_unapproved_preset_is_not_armed(self):
        args=fixture();args['presets']['balanced']=replace(args['presets']['balanced'],approved=False)
        self.assertEqual(preview_capital_adaptive_target(**args)['status'],'UNBOUND_PRESET_POLICY')

    def test_nonfinite_budget_is_rejected(self):
        args=fixture();args['absolute_tail_loss_limit']=math.nan
        self.assertEqual(preview_capital_adaptive_target(**args)['status'],'REJECTED_INPUT')

    def test_joint_scenarios_preserve_comovement(self):
        args=fixture();together=preview_capital_adaptive_target(**args)
        args['scenario_set']=replace(args['scenario_set'],scenarios=(
            JointScenario('offset-1',.5,{'A':0.,'B':0.},{'A':-.2,'B':.2}),
            JointScenario('offset-2',.5,{'A':0.,'B':0.},{'A':.2,'B':-.2}),
        ))
        offset=preview_capital_adaptive_target(**args)
        self.assertEqual(offset['status'],'PROPOSAL_FEASIBLE')
        self.assertGreater(together['decision'].proposal_evaluation.es,offset['decision'].evaluation.es)

    def test_post_fee_cap_rechecked_by_existing_kernel(self):
        args=fixture();args['proposal']=Action('TARGET_WEIGHTS',{'A':.6,'B':0.})
        args['scenario_set']=replace(args['scenario_set'],scenarios=(JointScenario('flat',1.,{'A':0.,'B':0.},{'A':0.,'B':0.}),))
        args['bps_spec']=replace(args['bps_spec'],bps_by_symbol={'A':10.,'B':10.})
        args['hard_policy']=replace(args['hard_policy'],caps=(ExposureCap({'A':1.,'B':1.},.6,'POST_FEE'),))
        result=preview_capital_adaptive_target(**args);decision=result['decision']
        self.assertEqual(result['status'],'TAIL_TARGET_TIGHTENED')
        weight=sum(decision.action.target_weights.values());fee=decision.evaluation.scenarios[0].k
        self.assertLessEqual(weight/(1.-fee),.6)

    def test_no_certified_anchor_yields_no_target(self):
        args=fixture();args['approved_anchor']=None
        result=preview_capital_adaptive_target(**args)
        self.assertIsNone(result['decision'].action)

    def test_unsafe_hold_uses_existing_mandatory_reduction(self):
        args=fixture();args['decision_book']=replace(args['decision_book'],weights={'A':.4,'B':.4})
        args['proposal']=Action('HOLD_POSITIONS')
        result=preview_capital_adaptive_target(**args)
        self.assertEqual(result['status'],'MANDATORY_TAIL_REDUCTION')
        self.assertTrue(result['decision'].mandatory_reduce)


if __name__=='__main__':
    unittest.main()
