"""Analytic artificial moment contracts, not return-path or real-data backtests."""
from copy import deepcopy
from fractions import Fraction
import json
import math
import unittest

from test_auto_portfolio_preview import packet_fixture
from us_equity_strategies.research.auto_portfolio_preview import generate_joint_candidate, build_three_level_previews
from us_equity_strategies.research.funded_portfolio_preview import build_three_level_funded_previews
from us_equity_strategies.research.portfolio_kelly_walk_forward import constrained_kelly_qp
from us_equity_strategies.research.kelly_candidate_math import prepare_kelly_quadratic


def math_fixture(method='SECOND_RAW_MOMENT_TAYLOR'):
    return dict(schema_version='qsl.kelly-math.v1',method=method,
        scope_ref='ARTIFICIAL_SCOPE',currency='USD',source='ARTIFICIAL_MATH_CONTRACT',source_version='fixture_v1',
        available_at='2000-01-04T11:00:00Z',valid_from='2000-01-01T00:00:00Z',valid_until='2001-01-01T00:00:00Z',
        joint_moment_model_id='ARTIFICIAL_JOINT_MODEL',moment_estimator_version='fixture_moments_v1',
        covariance_basis='PREDICTIVE_CENTERED_COVARIANCE',cash_numeraire='ZERO_RETURN_CASH',cash_simple_return=0.,
        return_cost_basis='BEFORE_ACCOUNT_TRANSACTION_COSTS',c_semantics='QP_CURVATURE_PENALTY_ONCE')


class KellyMathTests(unittest.TestCase):
    def candidate(self, *, method='SECOND_RAW_MOMENT_TAYLOR', c=.25, **changes):
        packet=packet_fixture();packet['moments'].update(changes)
        return generate_joint_candidate(packet['moments'],c=c,scope_ref='ARTIFICIAL_SCOPE',currency='USD',
            cutoff_utc=packet['account_contract']['cutoff_utc'],math_contract=math_fixture(method))

    def terms(self,mu,cov,**changes):
        p=packet_fixture();contract=math_fixture();contract.update(changes)
        return prepare_kelly_quadratic(mu,cov,math_contract=contract,moments=p['moments'],
            scope_ref='ARTIFICIAL_SCOPE',currency='USD',cutoff_utc=p['account_contract']['cutoff_utc'])

    def test_raw_second_moment_includes_diagonal_and_cross_mean_products(self):
        out=self.terms([.2,.1],[[.01,.002],[.002,.04]])
        self.assertEqual(out['status'],'MATH_CONTRACT_BOUND_SYNTHETIC')
        expected=[[.05,.022],[.022,.05]]
        for row,wanted in zip(out['second_raw_moment'],expected):
            for value,reference in zip(row,wanted):self.assertAlmostEqual(value,reference)
        self.assertEqual(out['quadratic_matrix'],out['second_raw_moment'])

    def test_corrected_candidate_matches_independent_fraction_hand_solution(self):
        out=self.candidate()
        # Q=(1/625)*ones + (1/25)*I; symmetry gives c*mu/(Q11+Q12).
        expected=float(Fraction(1,4)*Fraction(1,25)/(Fraction(1,25)+2*Fraction(1,625)))
        self.assertAlmostEqual(out['strategy_weights']['STRATEGY_A'],expected)
        self.assertAlmostEqual(out['strategy_weights']['STRATEGY_B'],expected)
        self.assertNotAlmostEqual(expected,.25)

    def test_covariance_only_method_is_explicit_further_approximation(self):
        out=self.candidate(method='MEAN_COVARIANCE_FURTHER_APPROX')
        self.assertAlmostEqual(out['strategy_weights']['STRATEGY_A'],.25)
        self.assertEqual(out['math']['quadratic_matrix_kind'],'CENTERED_COVARIANCE')
        self.assertTrue(out['math']['mean_outer_product_omitted'])
        self.assertFalse(out['math']['exact_log_optimal'])

    def test_objective_difference_is_the_squared_portfolio_mean_term(self):
        mu=[.2,.1];cov=[[.01,.002],[.002,.04]];w=[.3,.2];c=.25
        q=self.terms(mu,cov)['second_raw_moment']
        def objective(matrix):
            return sum(a*b for a,b in zip(mu,w))-sum(w[i]*matrix[i][j]*w[j] for i in range(2) for j in range(2))/(2*c)
        self.assertAlmostEqual(objective(cov)-objective(q),sum(a*b for a,b in zip(mu,w))**2/(2*c))

    def test_zero_mean_gives_equal_matrices_and_all_cash_candidate(self):
        out=self.candidate(mu_excess=[0.,0.])
        self.assertEqual(out['math']['second_raw_moment'],[[.04,0.],[0.,.04]])
        self.assertEqual(out['strategy_weights'],{'STRATEGY_A':0.,'STRATEGY_B':0.})

    def test_c_acts_once_for_an_interior_solution(self):
        a=self.candidate(c=.1);b=self.candidate(c=.25)
        self.assertAlmostEqual(a['strategy_weights']['STRATEGY_A']/b['strategy_weights']['STRATEGY_A'],.4)
        self.assertEqual(a['math']['c_applications'],1)
        self.assertAlmostEqual(b['strategy_weights']['STRATEGY_A'],25/108)

    def test_constrained_curvature_scaling_is_not_post_scaling_full_kelly(self):
        out=self.terms([.2,.1],[[.01,0.],[0.,.01]])
        q=out['quadratic_matrix'];full=constrained_kelly_qp([.2,.1],q,1.)
        curved=constrained_kelly_qp([.2,.1],q,.1)
        self.assertAlmostEqual(full[0],1.);self.assertAlmostEqual(full[1],0.)
        self.assertAlmostEqual(curved[0],1/3);self.assertAlmostEqual(curved[1],1/6)
        self.assertNotAlmostEqual(curved[0],.1*full[0])
        self.assertEqual(out['c_semantics'],'QP_CURVATURE_PENALTY_ONCE')

    def test_c_zero_is_not_an_approved_preset_and_is_not_silently_replaced(self):
        out=self.candidate(c=0.)
        self.assertEqual(out['status'],'CANDIDATE_UNAVAILABLE')

    def test_positive_c_approaches_zero_risky_allocation_at_zero_limit(self):
        q=self.terms([.04,.04],[[.04,0.],[0.,.04]])['quadratic_matrix']
        a=constrained_kelly_qp([.04,.04],q,.001);b=constrained_kelly_qp([.04,.04],q,.0001)
        self.assertLess(sum(b),sum(a));self.assertAlmostEqual(sum(b)/sum(a),.1)

    def test_taylor_surrogate_is_not_exact_kelly_even_with_full_second_moment(self):
        # +100% with p=.6, -50% with p=.4: mu=.4, Q=.7.
        out=self.terms([.4,0.],[[.54,0.],[0.,.04]])
        w=constrained_kelly_qp([.4,0.],out['quadratic_matrix'],1.)[0]
        self.assertAlmostEqual(w,4/7)
        exact=.8
        def growth(x):
            return .6*math.log1p(x)+.4*math.log1p(-.5*x)
        self.assertGreater(growth(exact),growth(w))
        self.assertFalse(out['exact_log_optimal']);self.assertFalse(out['cost_net_optimal'])

    def test_small_return_taylor_error_obeys_analytic_remainder_bound(self):
        weights=[.2,.3];base=[[.2,.1],[-.1,-.15]]
        errors=[]
        for epsilon in (.1,.05):
            z=[epsilon*sum(a*b for a,b in zip(weights,row)) for row in base]
            actual=sum(math.log1p(x) for x in z)/2
            approximation=sum(x-.5*x*x for x in z)/2
            radius=max(abs(x) for x in z)
            bound=sum(abs(x)**3 for x in z)/(2*3*(1-radius)**3)
            error=abs(actual-approximation);self.assertLessEqual(error,bound)
            errors.append(error)
        self.assertLess(errors[1],errors[0]/6)

    def test_same_first_two_moments_do_not_certify_positive_log_wealth_domain(self):
        safe=[(-.5,.8),(2.,.2)];ruin=[(-1.,.5),(1.,.5)]
        for sample in (safe,ruin):
            self.assertAlmostEqual(sum(x*p for x,p in sample),0.)
            self.assertAlmostEqual(sum(x*x*p for x,p in sample),1.)
        self.assertTrue(all(1+x>0 for x,_ in safe));self.assertTrue(any(1+x==0 for x,_ in ruin))
        self.assertFalse(self.terms([0.,0.],[[1.,0.],[0.,.04]])['log_wealth_domain_certified'])

    def test_missing_math_contract_does_not_fall_back_to_covariance(self):
        p=packet_fixture()
        out=generate_joint_candidate(p['moments'],c=.25,scope_ref='ARTIFICIAL_SCOPE',currency='USD',cutoff_utc=p['account_contract']['cutoff_utc'])
        self.assertIn('KELLY_MATH_CONTRACT_UNBOUND',out['gaps']);self.assertNotIn('strategy_weights',out)

    def test_unknown_method_never_downgrades(self):
        self.assertIn('KELLY_MATH_METHOD_UNSUPPORTED',self.candidate(method='EXACT_KELLY')['gaps'])

    def test_nonzero_or_unknown_cash_return_requires_a_separate_contract(self):
        for value in (None,True,.01):
            out=self.terms([.04,.04],[[.04,0.],[0.,.04]],cash_simple_return=value)
            self.assertIn('KELLY_CASH_NUMERAIRE_UNSUPPORTED',out['gaps'])

    def test_wrong_centering_cost_basis_and_c_semantics_are_rejected(self):
        for field,value in [('covariance_basis','UNCENTERED_SECOND_MOMENT'),('return_cost_basis','ALREADY_NET_OF_ACCOUNT_COSTS'),('c_semantics','POST_SCALE_AGAIN')]:
            out=self.terms([.04,.04],[[.04,0.],[0.,.04]],**{field:value})
            self.assertNotEqual(out['gaps'],[])

    def test_moment_model_and_estimator_versions_must_match(self):
        for field,value in [('joint_moment_model_id','OTHER'),('moment_estimator_version','OTHER')]:
            out=self.terms([.04,.04],[[.04,0.],[0.,.04]],**{field:value})
            self.assertIn('KELLY_MOMENT_MODEL_UNBOUND',out['gaps'])

    def test_source_scope_and_effective_time_are_bound(self):
        for field,value in [('source_version',''),('scope_ref','OTHER'),('available_at','2000-01-04T13:00:00Z'),('valid_until','2000-01-01T00:00:00Z')]:
            out=self.terms([.04,.04],[[.04,0.],[0.,.04]],**{field:value})
            self.assertIn('KELLY_MATH_CONTRACT_UNBOUND',out['gaps'])

    def test_real_inputs_and_invalid_covariance_are_not_repaired(self):
        for cov in ([[.04,.04],[.04,.04]],[[.04,.1],[.1,.04]],[[.04,0.],[.01,.04]]):
            self.assertNotEqual(self.terms([.04,.04],cov)['gaps'],[])
        self.assertIn('SYNTHETIC_MOMENTS_REQUIRED',self.candidate(synthetic=False)['gaps'])

    def test_raw_second_moment_overflow_is_an_explicit_gap(self):
        self.assertNotEqual(self.terms([1e200,1e200],[[.04,0.],[0.,.04]])['gaps'],[])

    def test_input_objects_are_not_mutated(self):
        p=packet_fixture();before=deepcopy(p);build_three_level_previews(**p)
        self.assertEqual(p,before)

    def test_math_method_changes_preview_identity(self):
        p=packet_fixture();a=build_three_level_previews(**p)
        p['math_contract']['method']='MEAN_COVARIANCE_FURTHER_APPROX'
        b=build_three_level_previews(**p)
        for level in a['previews']:
            self.assertNotEqual(a['previews'][level]['p4_preview']['confirmation_binding']['preview_sha256'],b['previews'][level]['p4_preview']['confirmation_binding']['preview_sha256'])

    def test_missing_policy_keeps_risk_numbers_unbound_in_corrected_chain(self):
        from test_funded_portfolio_preview import funded_fixture
        p=funded_fixture();p['risk_inputs']['account_policy']=None
        for preview in build_three_level_funded_previews(**p)['previews'].values():
            self.assertIsNone(preview['draft_settings']);self.assertIn('UNBOUND_ACCOUNT_POLICY',preview['gaps'])
            self.assertIsNone(preview['p4_preview']['parameter_comparison']['two_session_cvar95_budget']['recommended_value'])

    def test_corrected_funded_chain_is_unarmed_and_json_serializable(self):
        from test_funded_portfolio_preview import funded_fixture
        result=build_three_level_funded_previews(**funded_fixture());json.dumps(result,allow_nan=False)
        self.assertFalse(result['settings_applied']);self.assertFalse(result['financial_risk_certified'])
        for preview in result['previews'].values():
            self.assertEqual(preview['status'],'READY_SYNTHETIC_FUNDED_PREVIEW')
            self.assertEqual(preview['candidate']['math']['quadratic_matrix_kind'],'SECOND_RAW_MOMENT')

    def test_asymmetric_case_matches_exact_two_by_two_inverse(self):
        out=self.candidate(mu_excess=[.04,.02],covariance=[[.04,.01],[.01,.09]])
        a,b,d=Fraction(104,2500),Fraction(27,2500),Fraction(226,2500)
        determinant=a*d-b*b
        wanted=[Fraction(1,4)*(d*Fraction(1,25)-b*Fraction(1,50))/determinant,
                Fraction(1,4)*(a*Fraction(1,50)-b*Fraction(1,25))/determinant]
        for actual,reference in zip(out['strategy_weights'].values(),wanted):self.assertAlmostEqual(actual,float(reference))

    def test_permuting_strategy_order_preserves_named_allocations(self):
        a=self.candidate(mu_excess=[.04,.02],covariance=[[.04,.01],[.01,.09]])
        b=self.candidate(strategy_ids=['STRATEGY_B','STRATEGY_A'],mu_excess=[.02,.04],covariance=[[.09,.01],[.01,.04]])
        for key in a['strategy_weights']:self.assertAlmostEqual(a['strategy_weights'][key],b['strategy_weights'][key])

    def test_negative_mean_leg_can_remain_a_joint_risk_hedge(self):
        out=self.candidate(mu_excess=[.04,-.005],covariance=[[.04,-.015],[-.015,.04]])
        self.assertEqual(out['status'],'CANDIDATE_COMPUTED')
        self.assertGreater(out['strategy_weights']['STRATEGY_B'],0.)

    def test_cash_benchmark_must_match_candidate_and_tail_scenarios(self):
        from dataclasses import replace
        p=packet_fixture();scenarios=p['risk_inputs']['scenario_set']
        p['risk_inputs']['scenario_set']=replace(scenarios,descriptor=replace(scenarios.descriptor,benchmark='OTHER_CASH_BENCHMARK'))
        for preview in build_three_level_previews(**p)['previews'].values():
            self.assertIsNone(preview['draft_settings'])
            self.assertIn('KELLY_SCENARIO_CASH_BENCHMARK_MISMATCH',preview['gaps'])

    def test_p4_model_hash_and_c_explanation_bind_math_semantics(self):
        p=packet_fixture();a=build_three_level_previews(**p)['previews']['balanced']['p4_preview']
        p['math_contract']['method']='MEAN_COVARIANCE_FURTHER_APPROX'
        b=build_three_level_previews(**p)['previews']['balanced']['p4_preview']
        self.assertNotEqual(a['evidence']['model_sha256'],b['evidence']['model_sha256'])
        self.assertIn('QP_CURVATURE_PARAMETER_NOT_POST_SCALE_FRACTION',a['parameter_comparison']['kelly_fraction']['limiting_sources'])

    def test_matrix_helper_does_not_label_real_or_log_returns_as_bound_synthetic(self):
        p=packet_fixture()
        for key,value in [('synthetic',False),('unit','LOG_GROSS_RETURNS')]:
            moments=dict(p['moments'],**{key:value})
            out=prepare_kelly_quadratic([.04,.04],[[.04,0.],[0.,.04]],math_contract=math_fixture(),moments=moments,
                scope_ref='ARTIFICIAL_SCOPE',currency='USD',cutoff_utc=p['account_contract']['cutoff_utc'])
            self.assertEqual(out['status'],'KELLY_MATH_GAPS')

    def test_numerically_lost_covariance_is_not_repaired_by_an_undocumented_ridge(self):
        self.assertIn('KELLY_MOMENT_MATRIX_UNSUPPORTED',self.terms([1e16,1e16],[[.04,0.],[0.,.04]])['gaps'])


if __name__=='__main__':unittest.main()
