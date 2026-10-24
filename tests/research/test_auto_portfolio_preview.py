"""Offline, artificial inputs only; no fitting or historical return paths."""
from copy import deepcopy
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import unittest

from us_equity_strategies.research.auto_portfolio_account import adapt_ibkr_account_input
try:
    from us_equity_strategies.research.auto_portfolio_preview import generate_joint_candidate, build_three_level_previews
except ModuleNotFoundError as exc:
    if (exc.name not in {"quant_platform_kit.research_stats", "quant_platform_kit.research_stats.security_netting"}
            or os.environ.get("UES_REQUIRE_SECURITY_NETTING") == "1"):
        raise
    raise unittest.SkipTest("requires the isolated QPK security-netting research overlay") from exc
from test_capital_adaptive_risk import fixture as risk_fixture


ROOT = Path(__file__).resolve().parents[2]
DEFINITION = (ROOT/'docs/research/risk-levels/parameters.json').read_bytes()


def account_fixture():
    facts = dict(schema_version='ibkr_account_snapshot.v1', account_ids=['ARTIFICIAL_ACCOUNT'],
        currency='USD', observed_at='2000-01-04T12:00:00Z', net_assets='100',
        cash=[dict(currency='USD',cash_balance='100',source_tag='CashBalance')],
        broker_reported_positions=[], broker_reported_positions_scope='stocks_only')
    contract = dict(scope_ref='ARTIFICIAL_SCOPE',account_id='ARTIFICIAL_ACCOUNT',currency='USD',
        source='ARTIFICIAL_ACCOUNT_CONTRACT',source_version='fixture_v1',synthetic=True,
        cutoff_utc='2000-01-04T12:01:00Z',max_age_seconds=120,
        nav_basis='WHOLE_ACCOUNT_NET_LIQUIDATION',asset_scope='STOCKS_AND_CASH_ONLY',
        positions_complete=True,obligations_complete=True,
        settled_cash='100',reserved_cash='0',other_cash_obligations='0',
        cash_rule='SETTLED_MINUS_DISJOINT_RESERVATIONS_AND_OBLIGATIONS_NO_SELL_BRIDGE',
        cash_rule_source='ARTIFICIAL_CASH_RULE',valuation_tolerance='0',
        protected_capital_basis='100',capital_state_source='ARTIFICIAL_RETAINED_STATE')
    return facts,contract


def packet_fixture():
    facts,contract=account_fixture()
    moments=dict(strategy_ids=['STRATEGY_A','STRATEGY_B'],strategy_versions={'STRATEGY_A':'v1','STRATEGY_B':'v1'},
        eligible_strategies={'STRATEGY_A':'v1','STRATEGY_B':'v1'},eligibility_source='ARTIFICIAL_ELIGIBILITY',
        scope_ref='ARTIFICIAL_SCOPE',currency='USD',synthetic=True,source='ARTIFICIAL_MOMENTS',
        estimator_version='fixture_moments_v1',joint_moment_model_id='ARTIFICIAL_JOINT_MODEL',unit='SHRUNKEN_EXCESS_SIMPLE_RETURN_PER_SESSION',
        training_end='2000-01-03T21:00:00Z',available_at='2000-01-04T11:00:00Z',
        mu_excess=[.04,.04],covariance=[[.04,0.],[0.,.04]])
    composition=dict(scope_ref='ARTIFICIAL_SCOPE',currency='USD',source='ARTIFICIAL_COMPOSITION',
        available_at='2000-01-04T11:00:00Z',strategy_versions=dict(moments['strategy_versions']),
        weights={'STRATEGY_A':{'A':1.},'STRATEGY_B':{'B':1.}})
    risk=risk_fixture();risk.pop('level');risk.pop('proposal');risk.pop('decision_book')
    risk['scenario_available_at']='2000-01-04T11:00:00Z'
    return dict(facts=facts,account_contract=contract,moments=moments,composition=composition,
        risk_inputs=risk,definition_bytes=DEFINITION,request_id='artificial-request',math_contract=math_contract_fixture())


def math_contract_fixture():
    return dict(schema_version='qsl.kelly-math.v1',method='SECOND_RAW_MOMENT_TAYLOR',
        scope_ref='ARTIFICIAL_SCOPE',currency='USD',source='ARTIFICIAL_MATH_CONTRACT',source_version='fixture_v1',
        available_at='2000-01-04T11:00:00Z',valid_from='2000-01-01T00:00:00Z',valid_until='2001-01-01T00:00:00Z',
        joint_moment_model_id='ARTIFICIAL_JOINT_MODEL',moment_estimator_version='fixture_moments_v1',
        covariance_basis='PREDICTIVE_CENTERED_COVARIANCE',cash_numeraire='ZERO_RETURN_CASH',cash_simple_return=0.,
        return_cost_basis='BEFORE_ACCOUNT_TRANSACTION_COSTS',c_semantics='QP_CURVATURE_PENALTY_ONCE')


class AccountAdapterTests(unittest.TestCase):
    def test_complete_account_separates_equity_and_spendable_cash(self):
        facts,contract=account_fixture();contract.update(reserved_cash='10',other_cash_obligations='5')
        value=adapt_ibkr_account_input(facts,contract)
        self.assertEqual(value['status'],'READY_ARTIFICIAL_INPUT')
        self.assertEqual(value['nav'],100.);self.assertEqual(value['cash_balance'],100.)
        self.assertEqual(value['spendable_cash'],85.)

    def test_cash_balance_is_not_inferred_as_settled_cash(self):
        facts,contract=account_fixture();contract.pop('settled_cash')
        value=adapt_ibkr_account_input(facts,contract)
        self.assertIn('MISSING_SETTLED_CASH',value['gaps']);self.assertIsNone(value['spendable_cash'])

    def test_missing_pending_obligations_is_not_zero(self):
        facts,contract=account_fixture();contract.pop('other_cash_obligations')
        value=adapt_ibkr_account_input(facts,contract)
        self.assertIn('MISSING_OTHER_CASH_OBLIGATIONS',value['gaps'])

    def test_incomplete_positions_are_not_complete_account(self):
        facts,contract=account_fixture();contract['positions_complete']=False
        self.assertIn('POSITIONS_INCOMPLETE',adapt_ibkr_account_input(facts,contract)['gaps'])

    def test_wrong_account_and_multiple_accounts_rejected(self):
        for ids in [['OTHER'],['ARTIFICIAL_ACCOUNT','OTHER']]:
            facts,contract=account_fixture();facts['account_ids']=ids
            self.assertIn('ACCOUNT_SCOPE_MISMATCH',adapt_ibkr_account_input(facts,contract)['gaps'])

    def test_matching_missing_or_invalid_account_ids_are_not_bound(self):
        for account_id in (None,'','   ',True,123):
            facts,contract=account_fixture()
            facts['account_ids']=[account_id];contract['account_id']=account_id
            self.assertIn('ACCOUNT_SCOPE_MISMATCH',adapt_ibkr_account_input(facts,contract)['gaps'])

    def test_currency_does_not_trigger_implicit_fx(self):
        facts,contract=account_fixture();facts['currency']='HKD'
        self.assertIn('CURRENCY_MISMATCH',adapt_ibkr_account_input(facts,contract)['gaps'])

    def test_stale_and_future_snapshot_rejected(self):
        for value in ['2000-01-04T11:00:00Z','2000-01-04T12:02:00Z']:
            facts,contract=account_fixture();facts['observed_at']=value
            self.assertIn('SNAPSHOT_TIME_INVALID',adapt_ibkr_account_input(facts,contract)['gaps'])

    def test_native_amounts_require_finite_nonboolean_values(self):
        for value in [True,'NaN','-100','1e999']:
            facts,contract=account_fixture();facts['net_assets']=value
            self.assertIn('INVALID_NET_ASSETS',adapt_ibkr_account_input(facts,contract)['gaps'])

    def test_marked_values_define_weights_not_average_cost(self):
        facts,contract=account_fixture();facts['cash'][0]['cash_balance']='80';contract['settled_cash']='80'
        facts['broker_reported_positions']=[dict(symbol='A',currency='USD',quantity='2',market_value='20',avg_cost='999')]
        value=adapt_ibkr_account_input(facts,contract)
        self.assertEqual(value['status'],'READY_ARTIFICIAL_INPUT');self.assertEqual(value['current_weights'],{'A':.2})

    def test_unreconciled_nav_not_filled_with_imaginary_cash(self):
        facts,contract=account_fixture();facts['cash'][0]['cash_balance']='90';contract['settled_cash']='90'
        self.assertIn('NAV_RECONCILIATION_FAILED',adapt_ibkr_account_input(facts,contract)['gaps'])

    def test_missing_protected_state_does_not_reset_to_nav(self):
        facts,contract=account_fixture();contract.pop('protected_capital_basis')
        self.assertIn('MISSING_PROTECTED_CAPITAL_BASIS',adapt_ibkr_account_input(facts,contract)['gaps'])

    def test_malformed_cash_and_position_rows_leave_gaps(self):
        for key,value in [('cash',['bad-row']),('broker_reported_positions',['bad-row'])]:
            facts,contract=account_fixture();facts[key]=value
            self.assertEqual(adapt_ibkr_account_input(facts,contract)['status'],'ACCOUNT_INPUT_GAPS')

    def test_cash_source_and_freshness_limit_need_evidence(self):
        facts,contract=account_fixture();facts['cash'][0].pop('source_tag');contract['max_age_seconds']=math.inf
        gaps=adapt_ibkr_account_input(facts,contract)['gaps']
        self.assertIn('CASH_SOURCE_UNBOUND',gaps);self.assertIn('SNAPSHOT_TIME_INVALID',gaps)

    def test_frozen_producer_projection_is_accepted(self):
        import ast
        import __future__
        from collections.abc import Mapping
        from datetime import datetime,timezone
        from decimal import Decimal,InvalidOperation
        import re
        from types import SimpleNamespace
        source=ROOT/'tests/research/fixtures/ibkr_account_facts.py'
        names={'_finite_decimal_text','_verified_usd_net_assets','_report_positions','build_ibkr_account_facts'}
        nodes=[n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name in names]
        self.assertEqual({n.name for n in nodes},names)
        namespace=dict(Mapping=Mapping,datetime=datetime,timezone=timezone,Decimal=Decimal,InvalidOperation=InvalidOperation,re=re,
            MARKET_CURRENCY_CASH_TAG_PRIORITY=('$LEDGER-CashBalance','$LEDGER-TotalCashBalance','CashBalance','TotalCashBalance','SettledCash'))
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec',flags=__future__.annotations.compiler_flag),namespace)
        snapshot=SimpleNamespace(as_of=datetime(2000,1,4,12,tzinfo=timezone.utc),metadata=dict(
            account_ids=['ARTIFICIAL_ACCOUNT'],cash_balances=[dict(account_id='ARTIFICIAL_ACCOUNT',currency='USD',CashBalance='80',NetLiquidation='100')],
            total_equity_source='broker_net_liquidation',broker_net_liquidation='100',
            broker_reported_positions_source='ibkr_portfolio_cache',broker_reported_positions=[dict(
                account_id='ARTIFICIAL_ACCOUNT',security_type='STK',symbol='A',currency='USD',quantity='1',market_value='20')]))
        facts=namespace['build_ibkr_account_facts'](snapshot)
        _,contract=account_fixture();contract['settled_cash']='80'
        normalized=adapt_ibkr_account_input(facts,contract)
        self.assertEqual(normalized['status'],'READY_ARTIFICIAL_INPUT')
        self.assertEqual(normalized['current_weights'],{'A':.2})


class CandidateTests(unittest.TestCase):
    def candidate(self,**changes):
        packet=packet_fixture();moments=packet['moments'];moments.update(changes)
        return generate_joint_candidate(moments,c=.25,scope_ref='ARTIFICIAL_SCOPE',currency='USD',cutoff_utc=packet['account_contract']['cutoff_utc'],math_contract=packet['math_contract'])

    def test_existing_qp_hand_solution(self):
        result=self.candidate();self.assertEqual(result['status'],'CANDIDATE_COMPUTED')
        self.assertAlmostEqual(result['strategy_weights']['STRATEGY_A'],25/108)
        self.assertAlmostEqual(result['strategy_weights']['STRATEGY_B'],25/108)

    def test_covariance_changes_allocation(self):
        independent=self.candidate();correlated=self.candidate(covariance=[[.04,.03],[.03,.04]])
        self.assertLess(sum(correlated['strategy_weights'].values()),sum(independent['strategy_weights'].values()))
        self.assertAlmostEqual(correlated['correlation'][0][1],.75)

    def test_negative_excess_can_park_in_cash(self):
        result=self.candidate(mu_excess=[-.04,-.04])
        self.assertEqual(result['strategy_weights'],{'STRATEGY_A':0.,'STRATEGY_B':0.})

    def test_future_moments_are_not_usable(self):
        self.assertIn('MOMENTS_TIME_INVALID',self.candidate(available_at='2000-01-04T13:00:00Z')['gaps'])

    def test_nonpositive_definite_or_asymmetric_covariance_rejected(self):
        for cov in [[[.04,.05],[.05,.04]],[[.04,.01],[0.,.04]],[[.04,.04],[.04,.04]]]:
            self.assertEqual(self.candidate(covariance=cov)['status'],'CANDIDATE_UNAVAILABLE')

    def test_missing_or_ineligible_version_is_not_selected(self):
        self.assertIn('STRATEGY_ELIGIBILITY_UNBOUND',self.candidate(eligible_strategies={'STRATEGY_A':'v1'})['gaps'])

    def test_raw_returns_or_wrong_units_not_implicitly_converted(self):
        self.assertIn('MOMENT_UNIT_UNSUPPORTED',self.candidate(unit='LOG_GROSS_RETURNS')['gaps'])

    def test_real_moments_never_start_computation(self):
        self.assertIn('SYNTHETIC_MOMENTS_REQUIRED',self.candidate(synthetic=False)['gaps'])


class PreviewTests(unittest.TestCase):
    def test_all_three_levels_produce_json_serializable_previews(self):
        result=build_three_level_previews(**packet_fixture())
        self.assertEqual(set(result['previews']),{'conservative','balanced','growth'})
        self.assertEqual(json.loads(json.dumps(result,allow_nan=False)),result)
        for level,p in result['previews'].items():
            self.assertEqual(p['status'],'READY_SYNTHETIC_PREVIEW');self.assertIsNotNone(p['draft_settings'])
            self.assertFalse(p['p4_preview']['execution_authorized']);self.assertEqual(p['p4_preview']['application_status'],'NOT_APPLIED')
            self.assertTrue(all(x['effective_value'] is None for x in p['p4_preview']['parameter_comparison'].values()))

    def test_missing_risk_policy_keeps_numeric_risk_fields_unknown(self):
        args=packet_fixture();args['risk_inputs']['account_policy']=None
        for p in build_three_level_previews(**args)['previews'].values():
            self.assertIn('UNBOUND_ACCOUNT_POLICY',p['gaps']);self.assertIsNone(p['draft_settings'])
            self.assertIsNone(p['p4_preview']['parameter_comparison']['two_session_cvar95_budget']['recommended_value'])

    def test_incomplete_cash_blocks_final_configuration(self):
        args=packet_fixture();args['account_contract'].pop('settled_cash')
        for p in build_three_level_previews(**args)['previews'].values():self.assertIsNone(p['draft_settings'])

    def test_composition_merges_overlapping_security_once(self):
        args=packet_fixture();args['composition']['weights']['STRATEGY_B']={'A':1.}
        result=build_three_level_previews(**args)['previews']['balanced']
        self.assertAlmostEqual(result['composed_proposal']['A'],25/54);self.assertEqual(result['composed_proposal']['B'],0.)

    def test_sleeve_cash_is_not_counted_twice(self):
        args=packet_fixture();args['composition']['weights']={'STRATEGY_A':{'A':.5},'STRATEGY_B':{'B':.5}}
        result=build_three_level_previews(**args)['previews']['balanced']
        self.assertAlmostEqual(sum(result['composed_proposal'].values()),25/108)

    def test_candidate_path_never_calls_fitting_functions(self):
        from unittest.mock import patch
        with patch('us_equity_strategies.research.portfolio_kelly_walk_forward.fit_portfolio_kelly',side_effect=AssertionError('fitting forbidden')), \
             patch('us_equity_strategies.research.fractional_kelly_walk_forward.fit_fractional_kelly',side_effect=AssertionError('fitting forbidden')):
            self.assertEqual(build_three_level_previews(**packet_fixture())['previews']['balanced']['status'],'READY_SYNTHETIC_PREVIEW')

    def test_unsupported_input_type_does_not_hash_as_null(self):
        args=packet_fixture();args['moments']['unknown_metadata']=object()
        result=build_three_level_previews(**args)['previews']['balanced']
        self.assertIn('INPUT_BINDING_UNAVAILABLE',result['gaps']);self.assertIsNone(result['draft_settings'])

    def test_composition_version_and_time_must_match(self):
        for field,value in [('strategy_versions',{'STRATEGY_A':'v2','STRATEGY_B':'v1'}),('available_at','2000-01-04T13:00:00Z')]:
            args=packet_fixture();args['composition'][field]=value
            for p in build_three_level_previews(**args)['previews'].values():
                self.assertIsNone(p['draft_settings']);self.assertIn('COMPOSITION_UNBOUND',p['gaps'])

    def test_unsettled_cash_and_projected_sales_never_fund_buys(self):
        args=packet_fixture();args['facts']['cash'][0]['cash_balance']='10';args['account_contract']['settled_cash']='10'
        args['facts']['broker_reported_positions']=[dict(symbol='A',currency='USD',quantity='1',market_value='90')]
        args['risk_inputs']['scenario_set']=replace(args['risk_inputs']['scenario_set'],scenarios=(
            args['risk_inputs']['scenario_set'].scenarios[0].__class__('flat',1.,{'A':0.,'B':0.},{'A':0.,'B':0.}),))
        for p in build_three_level_previews(**args)['previews'].values():
            if sum(p.get('candidate',{}).get('strategy_weights',{}).values())>.2:
                self.assertIn('SETTLED_CASH_INSUFFICIENT_NO_SELL_BRIDGE',p['gaps']);self.assertIsNone(p['draft_settings'])

    def test_actual_risk_fields_and_diagnostics_are_not_invented(self):
        for p in build_three_level_previews(**packet_fixture())['previews'].values():
            for key in ['sharpe','win_rate','payoff_ratio']:self.assertIsNone(p['diagnostics'][key]['value'])
            self.assertIsNone(p['p4_preview']['effective_parameters_sha256'])
            self.assertIsNone(p['p4_preview']['confirmation_binding']['server_confirmation_ref'])

    def test_preview_hash_changes_with_cash_contract(self):
        args=packet_fixture();before=build_three_level_previews(**args)['previews']['balanced']['p4_preview']
        args['account_contract']['reserved_cash']='1'
        after=build_three_level_previews(**args)['previews']['balanced']['p4_preview']
        self.assertNotEqual(before['confirmation_binding']['preview_sha256'],after['confirmation_binding']['preview_sha256'])

    def test_preview_digest_excludes_hash_and_confirmation_receipt(self):
        from us_equity_strategies.research.auto_portfolio_account import digest
        preview=build_three_level_previews(**packet_fixture())['previews']['balanced']['p4_preview']
        body=deepcopy(preview)
        for key in ('preview_sha256','server_confirmation_ref','confirmed_at_utc','expires_at_utc'):
            body['confirmation_binding'].pop(key)
        self.assertEqual(preview['confirmation_binding']['preview_sha256'],digest(body))

    def test_input_objects_not_mutated(self):
        args=packet_fixture();copy=deepcopy(args);build_three_level_previews(**args)
        self.assertEqual(args,copy)

    def test_real_account_contract_stops_at_offline_boundary(self):
        args=packet_fixture();args['account_contract']['synthetic']=False
        for p in build_three_level_previews(**args)['previews'].values():
            self.assertIsNone(p['draft_settings']);self.assertIn('SYNTHETIC_ACCOUNT_REQUIRED',p['gaps'])

    def test_existing_p4_schema_contract(self):
        from jsonschema import Draft202012Validator,FormatChecker
        from referencing import Registry,Resource
        base=ROOT/'docs/research/risk-levels'
        schemas=[json.loads((base/f).read_text()) for f in ['p4-preview.schema.json','risk-settings.schema.json']]
        registry=Registry().with_resources((s['$id'],Resource.from_contents(s)) for s in schemas if '$id' in s)
        validator=Draft202012Validator(schemas[0],registry=registry,format_checker=FormatChecker())
        packets=[packet_fixture(),packet_fixture(),packet_fixture()]
        packets[1]['risk_inputs']['account_policy']=None
        packets[2]['account_contract'].pop('settled_cash')
        for packet in packets:
            for p in build_three_level_previews(**packet)['previews'].values():validator.validate(p['p4_preview'])


if __name__=='__main__':unittest.main()
