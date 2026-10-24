"""Artificial smart/advanced requests through one unchanged risk/cash core."""
from copy import deepcopy
from dataclasses import replace
import json
import math
from pathlib import Path
import unittest

from test_funded_portfolio_preview import funded_fixture
from test_auto_portfolio_preview import ROOT
from us_equity_strategies.research.portfolio_settings_preview import preview_portfolio_settings,portfolio_settings_capabilities


def request_fixture(mode='simple', **overrides):
    return dict(definition_version='r-risk-01.research.v2',request_id='artificial-request',
        scope_ref='ARTIFICIAL_SCOPE',mode=mode,level='balanced',overrides=overrides,
        restore_system_recommendation=False,research_only=True,execution_authorized=False)


def explicit_request(weights=None):
    weights={'STRATEGY_A':.01,'STRATEGY_B':.01} if weights is None else weights
    return request_fixture('advanced',strategy_selection_mode='explicit',strategy_ids=list(weights),
        strategy_weight_mode='explicit',strategy_weights=weights)


class SettingsModeTests(unittest.TestCase):
    def preview(self,request=None,packet=None):
        return preview_portfolio_settings(request_fixture() if request is None else request,
            **(funded_fixture() if packet is None else packet))

    def test_smart_mode_uses_system_candidate_without_confirmation(self):
        out=self.preview()
        self.assertEqual(out['status'],'READY_SYNTHETIC_SETTINGS_PREVIEW')
        self.assertEqual(out['p4_preview']['mode'],'simple')
        self.assertEqual(out['confirmation_required_fields'],[])
        self.assertEqual(out['requested_preview']['candidate']['target_source'],'SYSTEM_QP')

    def test_empty_advanced_mode_has_the_same_economic_result_as_smart(self):
        a=self.preview();b=self.preview(request_fixture('advanced'))
        self.assertEqual(a['requested_preview']['draft_settings'],b['requested_preview']['draft_settings'])
        self.assertEqual(b['confirmation_required_fields'],[])

    def test_each_level_remains_selectable(self):
        for level in ('conservative','balanced','growth'):
            req=request_fixture();req['level']=level
            out=self.preview(req)
            self.assertEqual(out['p4_preview']['level'],level)
            self.assertIsNotNone(out['requested_preview']['draft_settings'])

    def test_explicit_weights_are_not_renormalized_or_relabelled_as_optimizer_solution(self):
        out=self.preview(explicit_request())
        preview=out['requested_preview']
        self.assertEqual(preview['candidate']['strategy_weights'],{'STRATEGY_A':.01,'STRATEGY_B':.01})
        self.assertEqual(preview['candidate']['target_source'],'USER_EXPLICIT_TARGET')
        self.assertFalse(preview['candidate']['optimizer_applies_to_final_weights'])
        self.assertLess(sum(preview['draft_settings']['strategy_weights'].values()),1.)
        self.assertIsNotNone(preview['candidate']['system_strategy_weights'])

    def test_selection_only_uses_existing_eligible_versions(self):
        out=self.preview(explicit_request({'STRATEGY_A':.01}))
        self.assertEqual(out['status'],'READY_SYNTHETIC_SETTINGS_PREVIEW')
        self.assertEqual(out['requested_preview']['candidate']['strategy_weights']['STRATEGY_B'],0.)
        bad=self.preview(explicit_request({'UNKNOWN':.01}))
        self.assertIn('UNAPPROVED_STRATEGY_SELECTION',bad['gaps']);self.assertIsNone(bad['requested_preview'])

    def test_selection_cannot_grant_permission_to_an_ineligible_existing_id(self):
        packet=funded_fixture();packet['moments']['eligible_strategies'].pop('STRATEGY_A')
        out=self.preview(explicit_request(),packet)
        self.assertIn('STRATEGY_ELIGIBILITY_UNBOUND',out['gaps'])
        self.assertIsNone(out['requested_preview']['draft_settings'])

    def test_incomplete_explicit_selection_contract_is_rejected(self):
        for key in ('strategy_selection_mode','strategy_ids','strategy_weight_mode','strategy_weights'):
            req=explicit_request();req['overrides'].pop(key)
            self.assertIn('EXPLICIT_SELECTION_CONTRACT_INCOMPLETE',self.preview(req)['gaps'])

    def test_weights_must_match_selected_ids_and_sum_at_most_one(self):
        for weights in ({'STRATEGY_A':.8,'STRATEGY_B':.8},{'STRATEGY_A':-.1},{'STRATEGY_A':True}):
            out=self.preview(explicit_request(weights))
            self.assertEqual(out['status'],'INVALID_SETTINGS_REQUEST')
        req=explicit_request();req['overrides']['strategy_ids']=['STRATEGY_A']
        self.assertIn('EXPLICIT_SELECTION_CONTRACT_INCOMPLETE',self.preview(req)['gaps'])

    def test_c_override_recomputes_reference_math_once(self):
        out=self.preview(request_fixture('advanced',kelly_fraction=.1))
        c=out['requested_preview']['candidate']
        self.assertEqual(c['c'],.1);self.assertEqual(c['math']['c_applications'],1)
        self.assertAlmostEqual(c['strategy_weights']['STRATEGY_A'],5/54)

    def test_manual_target_does_not_get_an_extra_c_scaling(self):
        req=explicit_request();req['overrides']['kelly_fraction']=.1
        out=self.preview(req)
        self.assertEqual(out['requested_preview']['candidate']['strategy_weights']['STRATEGY_A'],.01)

    def test_budget_override_can_tighten_but_cannot_relax_original_policy(self):
        tight=self.preview(request_fixture('advanced',two_session_cvar95_budget=.002))
        self.assertLessEqual(tight['requested_preview']['draft_settings']['tail_budget_ratio'],.002)
        high=self.preview(request_fixture('advanced',two_session_cvar95_budget=.06))
        self.assertEqual(high['requested_preview']['draft_settings']['tail_budget_ratio'],high['system_recommendation']['draft_settings']['tail_budget_ratio'])
        self.assertIn('ORIGINAL_POLICY_REMAINS_BINDING',high['limitations'])

    def test_post_fee_cash_floor_uses_original_hard_cap_evaluator(self):
        packet=funded_fixture();risk=packet['risk_inputs'];scenarios=risk['scenario_set']
        risk['scenario_set']=replace(scenarios,scenarios=tuple(replace(s,earn_returns={'A':-.01,'B':-.01}) for s in scenarios.scenarios))
        out=self.preview(request_fixture('advanced',cash_floor_ratio=.9),packet)
        self.assertEqual(out['status'],'READY_SYNTHETIC_SETTINGS_PREVIEW')
        for row in out['requested_preview']['quantity_preview']['scenarios']:
            self.assertLessEqual(sum(row['post_fee_security_weights'].values()),.1)

    def test_symbol_cap_is_checked_after_fees_and_rounding(self):
        out=self.preview(request_fixture('advanced',max_symbol_nominal_weight=.01))
        self.assertEqual(out['status'],'READY_SYNTHETIC_SETTINGS_PREVIEW')
        for row in out['requested_preview']['quantity_preview']['scenarios']:
            self.assertTrue(all(w<=.01 for w in row['post_fee_security_weights'].values()))

    def test_unsupported_event_settings_are_not_silently_accepted(self):
        for field,value in [('stop_loss_ratio',.03),('rebalance_method','daily'),('target_annual_volatility',.1)]:
            out=self.preview(request_fixture('advanced',**{field:value}))
            self.assertIn('SETTING_CONSUMER_UNSUPPORTED:'+field,out['gaps'])
            self.assertIsNone(out['requested_preview'])

    def test_readonly_catalogue_fields_cannot_be_overridden(self):
        out=self.preview(request_fixture('advanced',max_gross_nominal_weight=1.))
        self.assertEqual(out['status'],'INVALID_SETTINGS_REQUEST')

    def test_simple_mode_rejects_hidden_overrides(self):
        self.assertIn('SIMPLE_MODE_OVERRIDE_NOT_ALLOWED',self.preview(request_fixture(kelly_fraction=.1))['gaps'])

    def test_invalid_identity_scope_version_and_execution_flags_rejected(self):
        for field,value in [('scope_ref','OTHER'),('definition_version','OLD'),('execution_authorized',True),('research_only',False),('request_id','OTHER')]:
            req=request_fixture();req[field]=value
            self.assertEqual(self.preview(req)['status'],'INVALID_SETTINGS_REQUEST')

    def test_unknown_fields_and_nonfinite_values_rejected(self):
        req=request_fixture();req['unexpected']='x'
        self.assertEqual(self.preview(req)['status'],'INVALID_SETTINGS_REQUEST')
        for value in (math.nan,math.inf,True):
            self.assertEqual(self.preview(request_fixture('advanced',cash_floor_ratio=value))['status'],'INVALID_SETTINGS_REQUEST')

    def test_catalogue_enum_and_numeric_limits_remain_binding(self):
        for field,value in [('kelly_fraction',.2),('cash_floor_ratio',.99),('two_session_cvar95_budget',.0001),('max_symbol_nominal_weight',.5)]:
            self.assertEqual(self.preview(request_fixture('advanced',**{field:value}))['status'],'INVALID_SETTINGS_REQUEST')

    def test_restore_system_recommendation_is_preview_only(self):
        req=request_fixture('advanced');req['restore_system_recommendation']=True
        out=self.preview(req);baseline=self.preview()
        self.assertEqual(out['requested_preview']['draft_settings'],baseline['requested_preview']['draft_settings'])
        self.assertTrue(out['p4_preview']['requested_settings']['restore_system_recommendation'])
        self.assertFalse(out['settings_applied'])
        req['overrides']={'kelly_fraction':.1}
        self.assertEqual(self.preview(req)['status'],'INVALID_SETTINGS_REQUEST')

    def test_comparison_keeps_recommendation_override_and_constrained_values_separate(self):
        req=request_fixture('advanced',two_session_cvar95_budget=.06)
        out=self.preview(req);row=out['p4_preview']['parameter_comparison']['two_session_cvar95_budget']
        self.assertEqual(row['override_value'],.06)
        self.assertEqual(row['recommended_value'],.01)
        self.assertIsNone(row['effective_value'])
        self.assertEqual(out['constrained_parameters']['two_session_cvar95_budget'],.01)

    def test_existing_confirmation_rules_are_reported_without_granting_authority(self):
        low=self.preview(request_fixture('advanced',kelly_fraction=.1))
        high=self.preview(request_fixture('advanced',kelly_fraction=.4))
        self.assertNotIn('kelly_fraction',low['confirmation_required_fields'])
        self.assertIn('kelly_fraction',high['confirmation_required_fields'])
        self.assertEqual(high['p4_preview']['confirmation_state'],'REQUIRED')
        self.assertIsNone(high['p4_preview']['confirmation_binding']['server_confirmation_ref'])
        self.assertFalse(high['execution_authorized'])

    def test_strategy_change_is_not_assumed_monotone_risk_reduction(self):
        out=self.preview(explicit_request())
        self.assertIn('strategy_weights',out['confirmation_required_fields'])
        self.assertEqual(out['p4_preview']['parameter_comparison']['strategy_weights']['risk_direction'],'UNKNOWN_OR_MIXED')

    def test_policy_cash_fee_and_quantity_gaps_block_both_modes(self):
        for mode in ('simple','advanced'):
            for defect in ('policy','cash','fees','quantity'):
                packet=funded_fixture()
                if defect=='policy':packet['risk_inputs']['account_policy']=None
                if defect=='cash':packet['cash_evidence']['pending_state']='UNKNOWN'
                if defect=='fees':packet['execution_rules'].pop('fees')
                if defect=='quantity':packet['execution_rules']['quantity']['step_by_symbol'].pop('A')
                out=self.preview(request_fixture(mode),packet)
                self.assertEqual(out['status'],'SETTINGS_PREVIEW_HAS_GAPS')
                self.assertIsNone(out['requested_preview']['draft_settings'])

    def test_no_policy_does_not_receive_catalogue_risk_defaults(self):
        packet=funded_fixture();packet['risk_inputs']['account_policy']=None
        out=self.preview(request_fixture(),packet)
        self.assertIsNone(out['p4_preview']['parameter_comparison']['two_session_cvar95_budget']['recommended_value'])
        self.assertEqual(out['constrained_parameters'],{})

    def test_existing_budget_and_caps_are_not_mutated(self):
        packet=funded_fixture();before=deepcopy(packet)
        self.preview(request_fixture('advanced',cash_floor_ratio=.9,two_session_cvar95_budget=.002),packet)
        self.assertEqual(packet,before)

    def test_each_request_binds_its_p4_hash_and_preserves_server_nulls(self):
        a=self.preview();b=self.preview(request_fixture('advanced',kelly_fraction=.1))
        self.assertNotEqual(a['p4_preview']['confirmation_binding']['preview_sha256'],b['p4_preview']['confirmation_binding']['preview_sha256'])
        for out in (a,b):
            self.assertEqual(out['p4_preview']['requested_settings']['request_id'],'artificial-request')
            self.assertEqual(out['p4_preview']['application_status'],'NOT_APPLIED')
            self.assertIsNone(out['p4_preview']['effective_parameters_sha256'])

    def test_both_p4_modes_validate_against_original_schemas(self):
        from jsonschema import Draft202012Validator,FormatChecker
        from referencing import Registry,Resource
        base=ROOT/'docs/research/risk-levels'
        settings=json.loads((base/'risk-settings.schema.json').read_text())
        validator=Draft202012Validator(json.loads((base/'p4-preview.schema.json').read_text()),registry=Registry().with_resource(settings['$id'],Resource.from_contents(settings)),format_checker=FormatChecker())
        for req in (request_fixture(),request_fixture('advanced',kelly_fraction=.1),explicit_request()):
            out=self.preview(req);validator.validate(out['p4_preview']);Draft202012Validator(settings).validate(out['p4_preview']['requested_settings'])

    def test_repeated_preview_is_deterministic_and_has_no_apply_path(self):
        packet=funded_fixture();req=explicit_request();a=self.preview(req,packet);b=self.preview(req,packet)
        self.assertEqual(a,b);json.dumps(a,allow_nan=False)
        for flag in ('execution_authorized','settings_applied','account_policy_bound','financial_risk_certified'):
            self.assertIs(a[flag],False)

    def test_reporting_benchmark_does_not_change_model_tail_or_targets(self):
        base=self.preview();out=self.preview(request_fixture('advanced',benchmark='QQQM_TOTAL_RETURN_USD'))
        self.assertEqual(out['reporting_benchmark'],'QQQM_TOTAL_RETURN_USD')
        self.assertEqual(base['requested_preview']['draft_settings'],out['requested_preview']['draft_settings'])
        self.assertEqual(base['p4_preview']['evidence']['model_sha256'],out['p4_preview']['evidence']['model_sha256'])
        self.assertIsNone(out['requested_preview']['diagnostics']['sharpe']['value'])

    def test_supported_system_selection_mode_does_not_create_an_empty_target(self):
        out=self.preview(request_fixture('advanced',strategy_selection_mode='system_eligible',strategy_weight_mode='system_recommended'))
        self.assertEqual(out['requested_preview']['draft_settings'],self.preview()['requested_preview']['draft_settings'])
        self.assertGreater(sum(out['requested_preview']['candidate']['strategy_weights'].values()),0.)

    def test_zero_manual_weights_are_a_valid_cash_target(self):
        out=self.preview(explicit_request({'STRATEGY_A':0.,'STRATEGY_B':0.}))
        self.assertEqual(out['status'],'READY_SYNTHETIC_SETTINGS_PREVIEW')
        self.assertTrue(all(not row['trades'] for row in out['requested_preview']['quantity_preview']['scenarios']))

    def test_manual_target_cannot_bypass_forced_reduction(self):
        p=funded_fixture();p['risk_inputs']['forced_target']={'A':0.,'B':0.}
        out=self.preview(explicit_request(),p)
        self.assertEqual(out['status'],'READY_SYNTHETIC_SETTINGS_PREVIEW')
        self.assertEqual(sum(out['requested_preview']['draft_settings']['strategy_weights'].values()),0.)

    def test_stricter_existing_cash_floor_remains_binding(self):
        from us_equity_strategies.research.cvar_exposure_constraint import ExposureCap
        p=funded_fixture();risk=p['risk_inputs'];hard=risk['hard_policy']
        risk['hard_policy']=replace(hard,caps=hard.caps+(ExposureCap({'A':1.,'B':1.},.01,'POST_FEE'),))
        out=self.preview(request_fixture('advanced',cash_floor_ratio=.1),p)
        self.assertEqual(out['status'],'READY_SYNTHETIC_SETTINGS_PREVIEW')
        for row in out['requested_preview']['quantity_preview']['scenarios']:
            self.assertLessEqual(sum(row['post_fee_security_weights'].values()),.01)

    def test_prior_absolute_limit_is_not_replaced_by_a_looser_request(self):
        p=funded_fixture();p['risk_inputs']['absolute_tail_loss_limit']=.1
        out=self.preview(request_fixture('advanced',two_session_cvar95_budget=.06),p)
        self.assertLessEqual(out['requested_preview']['draft_settings']['tail_budget_ratio'],.001)

    def test_capability_table_distinguishes_catalogue_from_consumed_fields(self):
        capabilities=portfolio_settings_capabilities(funded_fixture()['definition_bytes'])
        fields=capabilities['fields']
        self.assertEqual(fields['strategy_weights']['status'],'SUPPORTED_OFFLINE')
        self.assertEqual(fields['stop_loss_ratio']['status'],'CONSUMER_UNSUPPORTED')
        self.assertEqual(fields['m1_fee_reserve_ratio']['status'],'NOT_OVERRIDABLE')
        self.assertTrue(all(not row['account_policy_default_inferred'] for row in fields.values()))
        self.assertTrue(fields['kelly_fraction']['catalogue_research_default_used'])

    def test_preview_does_not_fit_or_start_a_return_path_evaluator(self):
        from unittest.mock import patch
        with patch('us_equity_strategies.research.portfolio_kelly_walk_forward.fit_portfolio_kelly',side_effect=AssertionError('fitting forbidden')):
            self.assertEqual(self.preview()['status'],'READY_SYNTHETIC_SETTINGS_PREVIEW')

    def test_preference_parameters_are_bound_in_model_hash(self):
        a=self.preview();b=self.preview(request_fixture('advanced',kelly_fraction=.1))
        self.assertNotEqual(a['p4_preview']['evidence']['model_sha256'],b['p4_preview']['evidence']['model_sha256'])

    def test_unavailable_math_does_not_claim_an_optimizer_result(self):
        p=funded_fixture();p.pop('math_contract')
        out=self.preview(packet=p)
        self.assertFalse(out['requested_preview']['candidate']['optimizer_applies_to_final_weights'])

    def test_smart_request_does_not_inherit_hidden_candidate_preferences(self):
        packet=funded_fixture();packet['candidate_preferences']={
            'level':'balanced','strategy_weights':{'STRATEGY_A':0.,'STRATEGY_B':0.}}
        out=self.preview(packet=packet)
        self.assertEqual(out['status'],'INVALID_SETTINGS_REQUEST')
        self.assertIn('SETTINGS_CONTEXT_CONTAINS_REQUEST_OVERRIDES',out['gaps'])


if __name__=='__main__':unittest.main()
