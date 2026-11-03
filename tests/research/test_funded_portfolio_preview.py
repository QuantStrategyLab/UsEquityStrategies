"""Artificial account, cash, cost and quantity contracts; no broker or fitting."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
import unittest

from test_auto_portfolio_preview import packet_fixture, ROOT
from us_equity_strategies.research.funded_portfolio_preview import (
    adapt_cash_evidence, build_three_level_funded_previews, _order_fee,
)
from us_equity_strategies.research.cvar_exposure_constraint import ExposureCap, JointScenario


def header():
    return dict(synthetic=True, account_id='ARTIFICIAL_ACCOUNT', scope_ref='ARTIFICIAL_SCOPE',
        currency='USD', source='ARTIFICIAL_EVIDENCE', source_version='fixture_v1',
        observed_at='2000-01-04T12:00:00Z', available_at='2000-01-04T12:00:00Z',
        max_age_seconds=120, valid_from='2000-01-01T00:00:00Z', valid_until='2001-01-01T00:00:00Z')


def funded_fixture():
    packet = packet_fixture()
    cash = dict(**header(), account_values=[
        dict(account_id='ARTIFICIAL_ACCOUNT',currency='USD',tag='CashBalance',value='100'),
        dict(account_id='ARTIFICIAL_ACCOUNT',currency='USD',tag='SettledCash',value='100')],
        pending_state='CLEAR', obligations_complete=True, reservations_disjoint=True, reservations=[])
    fees = dict(**header(), model='FIXED_NONNEGATIVE_BPS', unit='BPS_PER_SIDE',
        bps_by_symbol={'A':'10','B':'10'}, all_costs_included=True, minimum_fee_applies=False)
    quantity = dict(**header(), method='FLOOR_TRADE_DELTA',
        step_by_symbol={'A':'1','B':'1'}, minimum_buy_notional_by_symbol={'A':'0','B':'0'})
    prices = dict(**header(), mark_by_symbol={'A':'1','B':'1'})
    rules = dict(fees=fees,quantity=quantity,prices=prices)
    packet['risk_inputs']['bps_spec']=replace(packet['risk_inputs']['bps_spec'],bps_by_symbol={'A':10.,'B':10.})
    return dict(**packet,cash_evidence=cash,execution_rules=rules)


def order_cost_fixture():
    args = funded_fixture()
    terms = dict(per_share='0.0001', per_order='0.0002', minimum_amount='0.0005',
        maximum_notional_bps='100', extra_per_share='0.00001', extra_notional_bps='.02')
    args['execution_rules']['fees'] = dict(**header(), model='ORDER_COMPONENTS_V1',
        unit='ACCOUNT_CURRENCY_PER_ORDER', all_costs_included=True,
        order_assumption='ONE_FULL_FILL_ORDER_PER_SYMBOL', rounding='UNROUNDED_DECIMAL_ESTIMATE',
        candidate_cost_role='REFERENCE_BPS_ONLY',
        terms_by_symbol={s:{side:deepcopy(terms) for side in ('BUY','SELL')} for s in ('A','B')})
    return args


class OrderCostTests(unittest.TestCase):
    def test_minimum_cap_extras_and_no_order_hand_math(self):
        terms = order_cost_fixture()['execution_rules']['fees']['terms_by_symbol']['A']['BUY']
        terms.update(per_share='.1',per_order='.2',minimum_amount='.5',
            extra_per_share='.01',extra_notional_bps='2')
        # max(.5, .2 + .1*2) capped at 1% of 20 = .2;
        # third-party model components are outside that commission cap.
        self.assertEqual(_order_fee(terms,Decimal('2'),Decimal('20')),Decimal('.224'))
        self.assertEqual(_order_fee(terms,Decimal('20'),Decimal('2000')),Decimal('2.8'))
        self.assertEqual(_order_fee(terms,Decimal('0'),Decimal('0')),Decimal('0'))

    def test_fractional_quantity_exact_fee_enters_cash_and_risk_once(self):
        args = order_cost_fixture()
        args['execution_rules']['quantity']['step_by_symbol'] = {'A':'.3','B':'.3'}
        result = build_three_level_funded_previews(**args)
        for preview in result['previews'].values():
            self.assertEqual(preview['status'],'READY_SYNTHETIC_FUNDED_PREVIEW',preview['gaps'])
            quantity = preview['quantity_preview']
            self.assertEqual(quantity['cost_evaluation'],'EXACT_ORDER_FEE_RECHECK')
            self.assertFalse(quantity['nonlinear_cost_optimized'])
            for row in quantity['scenarios']:
                total = Decimal(0)
                for trade in row['trades']:
                    self.assertEqual(trade['fee_kind'],'ESTIMATE_NOT_ACTUAL_COMMISSION')
                    terms = args['execution_rules']['fees']['terms_by_symbol'][trade['symbol']][trade['side']]
                    fee = _order_fee(terms,Decimal(str(trade['quantity'])),Decimal(str(trade['notional'])))
                    self.assertAlmostEqual(trade['fee_amount'],float(fee))
                    total += fee
                self.assertAlmostEqual(row['fee_amount'],float(total))
                buys = sum(t['notional'] for t in row['trades'] if t['side']=='BUY')
                self.assertAlmostEqual(row['gross_buy_plus_fees'],buys+float(total))
                self.assertAlmostEqual(row['cash_after_fee_amount'],100-buys-float(total))
                scenario = next(s for s in args['risk_inputs']['scenario_set'].scenarios if s.scenario_id==row['scenario_id'])
                gross_return = sum(w*scenario.earn_returns[s] for s,w in row['rounded_security_weights'].items())
                self.assertAlmostEqual(row['model_loss'],-gross_return+float(total)/100)

    def test_same_fee_rejects_unfunded_and_excess_tail_loss(self):
        for minimum in ('10','100'):
            args = order_cost_fixture()
            for sides in args['execution_rules']['fees']['terms_by_symbol'].values():
                for terms in sides.values():
                    terms.update(minimum_amount=minimum,maximum_notional_bps=None)
            result = build_three_level_funded_previews(**args)
            for p in result['previews'].values():
                self.assertIsNone(p['draft_settings'])
                self.assertIn('ROUNDED_TAIL_BUDGET',p['gaps'])
                if minimum == '100':
                    self.assertIn('ROUNDED_SETTLED_CASH_INSUFFICIENT_NO_SELL_BRIDGE',p['gaps'])

    def test_no_trade_has_no_minimum_commission(self):
        args = order_cost_fixture()
        args['execution_rules']['quantity']['step_by_symbol'] = {'A':'100','B':'100'}
        for p in build_three_level_funded_previews(**args)['previews'].values():
            self.assertEqual(p['status'],'READY_SYNTHETIC_FUNDED_PREVIEW')
            self.assertTrue(all(row['fee_amount']==0 and not row['trades'] for row in p['quantity_preview']['scenarios']))

    def test_sell_side_terms_and_fill_drift_reconcile_to_same_fee(self):
        args = order_cost_fixture()
        args['facts']['cash'][0]['cash_balance']='80'
        args['account_contract']['settled_cash']='80'
        for row in args['cash_evidence']['account_values']: row['value']='80'
        args['facts']['broker_reported_positions']=[dict(symbol='A',currency='USD',quantity='20',market_value='20')]
        scenario_set = args['risk_inputs']['scenario_set']
        args['risk_inputs']['scenario_set'] = replace(scenario_set,scenarios=tuple(
            replace(s,fill_returns={'A':.1,'B':-.05}) for s in scenario_set.scenarios))
        for sides in args['execution_rules']['fees']['terms_by_symbol'].values():
            sides['SELL']['extra_per_share']='.00002'
        seen_sell = False
        for p in build_three_level_funded_previews(**args)['previews'].values():
            quantity = p['quantity_preview']
            self.assertIsNotNone(quantity,p['gaps'])
            self.assertNotIn('ROUNDED_FEE_RECONCILIATION_FAILED',quantity['gaps'])
            for row in quantity['scenarios']:
                buys=sells=fees=0.
                for trade in row['trades']:
                    terms=args['execution_rules']['fees']['terms_by_symbol'][trade['symbol']][trade['side']]
                    fee=_order_fee(terms,Decimal(str(trade['quantity'])),Decimal(str(trade['notional'])))
                    self.assertAlmostEqual(trade['fee_amount'],float(fee))
                    fees += float(fee)
                    if trade['side']=='BUY': buys += trade['notional']
                    else: sells += trade['notional']; seen_sell = True
                self.assertAlmostEqual(row['cash_after_fee_amount'],80-buys+sells-fees)
                earn=next(s.earn_returns for s in args['risk_inputs']['scenario_set'].scenarios if s.scenario_id==row['scenario_id'])
                self.assertAlmostEqual(row['model_loss'],1-1.02*(1+sum(w*earn[s] for s,w in row['rounded_security_weights'].items()))+fees/100)
        self.assertTrue(seen_sell)

    def test_partial_fills_actual_fees_missing_side_and_unknown_clauses_are_not_guessed(self):
        for change in ('partial','actual','side','missing','rebate','rounding','unknown'):
            args = order_cost_fixture();fees = args['execution_rules']['fees']
            if change=='partial': fees['order_assumption']='PARTIAL_FILLS'
            elif change=='actual': fees['actual_commission']='0'
            elif change=='side': fees['terms_by_symbol']['A'].pop('SELL')
            elif change=='missing': fees['terms_by_symbol']['A']['BUY'].pop('per_share')
            elif change=='rebate': fees['terms_by_symbol']['A']['BUY']['extra_per_share']='-.1'
            elif change=='rounding': fees['rounding']='GUESS_CENTS'
            else: fees['terms_by_symbol']['A']['BUY']['venue_fee']='0'
            for p in build_three_level_funded_previews(**args)['previews'].values():
                self.assertIsNone(p['draft_settings'])
                self.assertIn('FEE_RULE_UNBOUND',p['gaps'])

    def test_order_fee_preview_does_not_claim_event_liquidation_contract(self):
        from test_portfolio_risk_events_preview import event_fixture, evaluate
        from us_equity_strategies.research.auto_portfolio_account import digest
        packet,context = event_fixture(held=True)
        packet['execution_rules']['fees'] = order_cost_fixture()['execution_rules']['fees']
        context['observations']['fee_evidence_sha256'] = digest(packet['execution_rules']['fees'])
        result = evaluate(packet,context)
        self.assertIn('EVENT_FEE_MODEL_UNBOUND',result['gaps'])

    def test_explicit_single_account_liquidation_does_not_charge_minimum_per_sleeve(self):
        from test_portfolio_risk_events_preview import event_fixture, evaluate
        from us_equity_strategies.research.auto_portfolio_account import digest
        packet,context = event_fixture(held=True)
        fees = order_cost_fixture()['execution_rules']['fees']
        for sides in fees['terms_by_symbol'].values():
            for terms in sides.values():
                terms.update(per_share='0',per_order='0',minimum_amount='.5',
                    maximum_notional_bps=None,extra_per_share='0',extra_notional_bps='0')
        packet['execution_rules']['fees'] = fees
        obs = context['observations']
        obs.update(fee_evidence_sha256=digest(fees),
            liquidation_fee_allocation='PRO_RATA_MARKET_VALUE_SINGLE_ACCOUNT_ORDER')
        sleeve = obs['sleeves'][0]
        sleeve.update(fee_adjusted_entry_value=10.,market_value=10.,net_liquidation_value=9.75,
            held_cycle_peak_net_value=9.75,security_market_values={'A':10.})
        other = deepcopy(sleeve);other.update(held_cycle_id='cycle-A2')
        obs['sleeves'].append(other)
        result = evaluate(packet,context)
        self.assertEqual(result['gaps'],[])
        for metric in result['metrics']['sleeves']:
            self.assertAlmostEqual(metric['loss_ratio'],.025)
        obs['sleeves'][0]['net_liquidation_value']=9.5
        self.assertIn('EVENT_LIQUIDATION_FEES_NOT_RECONCILED',evaluate(packet,context)['gaps'])


class CashEvidenceTests(unittest.TestCase):
    def adapt(self, args):
        return adapt_cash_evidence(args['facts'],args['account_contract'],args['cash_evidence'])

    def test_native_cash_tag_priority_is_preserved_without_inventing_settlement(self):
        args=funded_fixture();cash=args['cash_evidence']
        cash['account_values'].append(dict(account_id='ARTIFICIAL_ACCOUNT',currency='USD',tag='AvailableFunds',value='9999'))
        result=self.adapt(args)
        self.assertEqual(result['status'],'CASH_EVIDENCE_BOUND_SYNTHETIC')
        self.assertEqual(result['selected_cash_tag'],'CashBalance')
        self.assertEqual(result['account_contract']['settled_cash'],'100')

    def test_explicit_settled_and_reservations_fill_missing_contract_fields(self):
        args=funded_fixture()
        for key in ('settled_cash','reserved_cash','other_cash_obligations'):args['account_contract'].pop(key)
        args['cash_evidence']['reservations']=[dict(id='reserve-1',kind='RESERVE',amount='10'),dict(id='obligation-1',kind='OTHER',amount='5')]
        value=self.adapt(args)['account_contract']
        self.assertEqual((value['reserved_cash'],value['other_cash_obligations']),('10','5'))
        result=build_three_level_funded_previews(**args)
        self.assertEqual(result['account_input']['spendable_cash'],85.)

    def test_other_account_currency_and_buying_power_do_not_fund_this_account(self):
        args=funded_fixture();args['cash_evidence']['account_values']=[
            dict(account_id='OTHER',currency='USD',tag='SettledCash',value='100'),
            dict(account_id='ARTIFICIAL_ACCOUNT',currency='HKD',tag='SettledCash',value='100'),
            dict(account_id='ARTIFICIAL_ACCOUNT',currency='USD',tag='AvailableFunds',value='100')]
        self.assertIn('SETTLED_CASH_EVIDENCE_MISSING',self.adapt(args)['gaps'])

    def test_missing_settled_tag_never_uses_cash_balance(self):
        args=funded_fixture();args['cash_evidence']['account_values'].pop()
        self.assertIn('SETTLED_CASH_EVIDENCE_MISSING',self.adapt(args)['gaps'])

    def test_missing_or_unknown_pending_state_blocks_drafts(self):
        for state in (None,'UNKNOWN','PENDING'):
            args=funded_fixture();args['cash_evidence']['pending_state']=state
            for p in build_three_level_funded_previews(**args)['previews'].values():
                self.assertIsNone(p['draft_settings']);self.assertIn('PENDING_STATE_NOT_CLEAR',p['gaps'])

    def test_projection_is_not_counted_as_cash_or_as_a_completed_fill(self):
        args=funded_fixture();args['cash_evidence']['projected_sale_proceeds']='9999'
        self.assertEqual(self.adapt(args)['account_contract']['settled_cash'],'100')

    def test_conflicting_or_duplicate_cash_tags_rejected(self):
        args=funded_fixture();args['cash_evidence']['account_values'].append(deepcopy(args['cash_evidence']['account_values'][0]))
        self.assertIn('DUPLICATE_CASH_TAG',self.adapt(args)['gaps'])

    def test_disjoint_complete_obligations_are_required(self):
        for key in ('obligations_complete','reservations_disjoint'):
            args=funded_fixture();args['cash_evidence'][key]=False
            self.assertNotEqual(self.adapt(args)['gaps'],[])
        args=funded_fixture();args['cash_evidence']['reservations']=[dict(id='same',kind='RESERVE',amount='1')]*2
        self.assertIn('RESERVATIONS_INVALID',self.adapt(args)['gaps'])

    def test_supplied_contract_conflict_is_not_silently_overwritten(self):
        args=funded_fixture();args['account_contract']['settled_cash']='99'
        self.assertIn('CASH_CONTRACT_EVIDENCE_CONFLICT',self.adapt(args)['gaps'])

    def test_negative_nan_and_boolean_money_rejected(self):
        for value in ('-1','NaN',True,'1e999'):
            args=funded_fixture();args['cash_evidence']['account_values'][1]['value']=value
            self.assertNotEqual(self.adapt(args)['gaps'],[])

    def test_evidence_identity_version_and_time_are_required(self):
        for key,value in [('account_id','OTHER'),('source_version',''),('available_at','2000-01-04T13:00:00Z'),('observed_at','1999-12-01T00:00:00Z'),('synthetic',False)]:
            args=funded_fixture();args['cash_evidence'][key]=value
            self.assertIn('CASH_EVIDENCE_UNBOUND',self.adapt(args)['gaps'])


class FundedPreviewTests(unittest.TestCase):
    def test_matching_unbound_account_identity_never_creates_a_draft(self):
        for account_id in (None,'','   ',True,123):
            args=funded_fixture()
            args['account_contract']['account_id']=account_id
            args['facts']['account_ids']=[account_id]
            args['cash_evidence']['account_id']=account_id
            for row in args['cash_evidence']['account_values']:
                row['account_id']=account_id
            for rule in args['execution_rules'].values():
                rule['account_id']=account_id
            cash=adapt_cash_evidence(args['facts'],args['account_contract'],args['cash_evidence'])
            self.assertIn('CASH_EVIDENCE_UNBOUND',cash['gaps'])
            for p in build_three_level_funded_previews(**args)['previews'].values():
                self.assertIsNone(p['draft_settings'])

    def balanced(self,args):
        return build_three_level_funded_previews(**args)['previews']['balanced']

    def test_three_levels_show_rounded_quantities_fee_and_final_cash(self):
        result=build_three_level_funded_previews(**funded_fixture())
        json.dumps(result,allow_nan=False)
        for p in result['previews'].values():
            self.assertEqual(p['status'],'READY_SYNTHETIC_FUNDED_PREVIEW')
            rows=p['quantity_preview']['scenarios']
            for row in rows:
                self.assertGreaterEqual(row['cash_after_fee_amount'],0.)
                self.assertLessEqual(row['gross_buy_plus_fees'],row['spendable_cash'])
                self.assertAlmostEqual(row['fee_amount'],sum(abs(x['notional'])*.001 for x in row['trades']))
                for trade in row['trades']:self.assertEqual(trade['quantity'],int(trade['quantity']))
            self.assertFalse(p['quantity_preview']['execution_authorized'])

    def test_missing_real_fee_plan_is_not_inferred_from_kernel_bps(self):
        args=funded_fixture();args['execution_rules'].pop('fees')
        p=self.balanced(args);self.assertIsNone(p['draft_settings'])
        self.assertIn('FEE_RULE_UNBOUND',p['gaps'])

    def test_unsupported_minimum_commission_is_not_downgraded(self):
        args=funded_fixture();args['execution_rules']['fees']['minimum_fee_applies']=True
        p=self.balanced(args);self.assertIsNone(p['draft_settings']);self.assertIn('UNSUPPORTED_FEE_RULE',p['gaps'])

    def test_fee_source_rates_and_kernel_must_match(self):
        args=funded_fixture();args['execution_rules']['fees']['bps_by_symbol']['A']='5'
        self.assertIn('FEE_KERNEL_MISMATCH',self.balanced(args)['gaps'])

    def test_expired_fee_and_future_quantity_rules_are_not_used(self):
        for part,key,value in [('fees','valid_until','2000-01-01T00:00:00Z'),('quantity','available_at','2000-01-04T13:00:00Z')]:
            args=funded_fixture();args['execution_rules'][part][key]=value
            self.assertIsNone(self.balanced(args)['draft_settings'])

    def test_quantity_step_missing_zero_nan_or_boolean_has_no_whole_share_fallback(self):
        for value in (None,'0','NaN',True):
            args=funded_fixture();args['execution_rules']['quantity']['step_by_symbol']['A']=value
            p=self.balanced(args);self.assertIsNone(p['draft_settings']);self.assertIn('QUANTITY_RULE_UNBOUND',p['gaps'])

    def test_fractional_step_and_flooring_do_not_increase_buy_budget(self):
        args=funded_fixture();args['execution_rules']['quantity']['step_by_symbol']={'A':'.3','B':'.3'}
        p=self.balanced(args)
        for row in p['quantity_preview']['scenarios']:
            for trade in row['trades']:
                self.assertEqual(Decimal(str(trade['quantity']))%Decimal('.3'),0)
                self.assertLessEqual(trade['notional'],row['continuous_target_value'][trade['symbol']])

    def test_too_small_order_stays_zero_without_forced_one_share(self):
        args=funded_fixture();args['execution_rules']['quantity']['step_by_symbol']={'A':'100','B':'100'}
        p=self.balanced(args)
        self.assertEqual(p['status'],'READY_SYNTHETIC_FUNDED_PREVIEW')
        self.assertTrue(all(not row['trades'] for row in p['quantity_preview']['scenarios']))
        self.assertTrue(all(row['fee_amount']==0 for row in p['quantity_preview']['scenarios']))

    def test_minimum_buy_notional_is_explicit_and_applied_before_rounding(self):
        args=funded_fixture();args['execution_rules']['quantity']['minimum_buy_notional_by_symbol']={'A':'100','B':'100'}
        p=self.balanced(args)
        self.assertTrue(all(not r['trades'] for r in p['quantity_preview']['scenarios']))

    def test_minimum_buy_notional_also_applies_to_actual_rounded_purchase(self):
        args=funded_fixture()
        args['execution_rules']['quantity']['minimum_buy_notional_by_symbol']={'A':'2.1','B':'2.1'}
        p=self.balanced(args)
        self.assertEqual(p['status'],'READY_SYNTHETIC_FUNDED_PREVIEW',p['gaps'])
        for row in p['quantity_preview']['scenarios']:
            self.assertTrue(all(v > 2.1 for v in row['continuous_target_value'].values()))
            self.assertEqual(row['trades'],[])
            self.assertEqual(row['fee_amount'],0.)
            self.assertEqual(row['gross_buy_plus_fees'],0.)
            self.assertEqual(row['cash_after_fee_amount'],100.)

    def test_missing_price_is_not_carried_forward_or_filled(self):
        args=funded_fixture();args['execution_rules']['prices']['mark_by_symbol'].pop('B')
        self.assertIn('PRICE_RULE_UNBOUND',self.balanced(args)['gaps'])

    def test_mark_and_native_quantity_must_reconcile(self):
        args=funded_fixture();args['facts']['cash'][0]['cash_balance']='80';args['account_contract']['settled_cash']='80'
        for row in args['cash_evidence']['account_values']:row['value']='80'
        args['facts']['broker_reported_positions']=[dict(symbol='A',currency='USD',quantity='2',market_value='20')]
        self.assertIn('POSITION_PRICE_QUANTITY_MISMATCH',self.balanced(args)['gaps'])

    def test_missing_cash_evidence_blocks_even_with_old_explicit_contract(self):
        args=funded_fixture();args['cash_evidence']=None
        self.assertIsNone(self.balanced(args)['draft_settings'])

    def test_no_policy_remains_a_gap_without_risk_numbers(self):
        args=funded_fixture();args['risk_inputs']['account_policy']=None
        p=self.balanced(args)
        self.assertIsNone(p['draft_settings']);self.assertIn('UNBOUND_ACCOUNT_POLICY',p['gaps'])
        self.assertIsNone(p['p4_preview']['parameter_comparison']['two_session_cvar95_budget']['recommended_value'])

    def test_contract_hash_covers_rule_metadata_and_cash_evidence(self):
        args=funded_fixture();p=self.balanced(args)
        args['execution_rules']['quantity']['source_version']='fixture_v2'
        q=self.balanced(args)
        self.assertNotEqual(p['p4_preview']['confirmation_binding']['preview_sha256'],q['p4_preview']['confirmation_binding']['preview_sha256'])

    def test_input_not_mutated_and_preview_never_applied(self):
        args=funded_fixture();before=deepcopy(args);result=build_three_level_funded_previews(**args)
        self.assertEqual(args,before)
        for key in ('execution_authorized','settings_applied','account_policy_bound','financial_risk_certified'):
            self.assertIs(result[key],False)
        for p in result['previews'].values():
            self.assertEqual(p['p4_preview']['application_status'],'NOT_APPLIED')
            self.assertTrue(all(v['effective_value'] is None for v in p['p4_preview']['parameter_comparison'].values()))

    def test_schema_stays_original_for_ready_and_missing_fee_cases(self):
        from jsonschema import Draft202012Validator,FormatChecker
        from referencing import Registry,Resource
        base=ROOT/'docs/research/risk-levels'
        schema=json.loads((base/'p4-preview.schema.json').read_text())
        settings=json.loads((base/'risk-settings.schema.json').read_text())
        validator=Draft202012Validator(schema,registry=Registry().with_resource(settings['$id'],Resource.from_contents(settings)),format_checker=FormatChecker())
        args=funded_fixture()
        for ready in (True,False):
            if not ready:args['execution_rules'].pop('fees')
            for p in build_three_level_funded_previews(**args)['previews'].values():validator.validate(p['p4_preview'])

    def held_fixture(self):
        args=funded_fixture()
        args['facts']['cash'][0]['cash_balance']='80';args['account_contract']['settled_cash']='80'
        for row in args['cash_evidence']['account_values']:row['value']='80'
        args['facts']['broker_reported_positions']=[dict(symbol='A',currency='USD',quantity='.5',market_value='20')]
        args['execution_rules']['prices']['mark_by_symbol']['A']='40'
        return args

    def test_untradeable_held_dust_is_retained_and_tail_rechecked(self):
        args=self.held_fixture();args['moments']['mu_excess']=[-.04,-.04]
        p=self.balanced(args)
        self.assertIsNone(p['draft_settings']);self.assertIn('ROUNDED_TAIL_BUDGET',p['gaps'])
        self.assertEqual(p['quantity_preview']['scenarios'][0]['remaining_quantity']['A'],.5)
        self.assertEqual(p['quantity_preview']['scenarios'][0]['fee_amount'],0.)

    def test_rounding_cannot_skip_original_post_fee_exposure_caps(self):
        args=self.held_fixture();args['moments']['mu_excess']=[-.04,-.04]
        risk=args['risk_inputs'];risk['hard_policy']=replace(risk['hard_policy'],caps=(ExposureCap({'A':1.,'B':0.},.01,'POST_FEE'),))
        p=self.balanced(args)
        self.assertIsNone(p['draft_settings']);self.assertIn('ROUNDED_ORIGINAL_HARD_CAP',p['gaps'])

    def test_rounded_dust_does_not_satisfy_a_forced_zero_target(self):
        args=self.held_fixture();args['risk_inputs']['forced_target']={'A':0.,'B':0.}
        p=self.balanced(args)
        self.assertIsNone(p['draft_settings']);self.assertIn('ROUNDED_FORCED_TARGET_CAP',p['gaps'])

    def test_sells_floor_trade_delta_and_do_not_oversell_fractional_holdings(self):
        args=self.held_fixture();args['moments']['mu_excess']=[-.04,-.04]
        args['execution_rules']['quantity']['step_by_symbol']['A']='.1'
        p=self.balanced(args)
        self.assertEqual(p['status'],'READY_SYNTHETIC_FUNDED_PREVIEW')
        for row in p['quantity_preview']['scenarios']:
            sell=row['trades'][0]
            self.assertEqual((sell['side'],sell['quantity']),('SELL',.5))
            self.assertEqual(row['remaining_quantity']['A'],0.)
            self.assertGreater(row['fee_amount'],0.)
            self.assertFalse(row['sale_proceeds_used_for_funding'])

    def test_gap_fill_changes_conditional_prices_and_rounded_weights(self):
        args=funded_fixture();risk=args['risk_inputs']
        scenarios=risk['scenario_set'].scenarios
        risk['scenario_set']=replace(risk['scenario_set'],scenarios=tuple(replace(s,fill_returns={'A':.1,'B':0.}) for s in scenarios))
        p=self.balanced(args)
        self.assertEqual(p['status'],'READY_SYNTHETIC_FUNDED_PREVIEW')
        for row in p['quantity_preview']['scenarios']:
            trade=next(t for t in row['trades'] if t['symbol']=='A')
            self.assertAlmostEqual(trade['fill_price'],1.1)
            self.assertAlmostEqual(row['rounded_security_weights']['A'],trade['quantity']*1.1/100.)

    def test_joint_cvar_is_not_replaced_with_worst_individual_loss(self):
        args=funded_fixture();risk=args['risk_inputs'];down,up=risk['scenario_set'].scenarios
        risk['scenario_set']=replace(risk['scenario_set'],scenarios=(replace(down,probability=.01),replace(up,probability=.99)))
        p=self.balanced(args)
        self.assertEqual(p['status'],'READY_SYNTHETIC_FUNDED_PREVIEW')
        self.assertGreater(max(r['model_loss'] for r in p['quantity_preview']['scenarios']),p['draft_settings']['tail_budget_ratio'])
        self.assertLessEqual(p['quantity_preview']['aggregate_cvar'],p['draft_settings']['tail_budget_ratio'])

    def test_overlapping_strategy_securities_are_charged_once_after_netting(self):
        args=funded_fixture();args['composition']['weights']={'STRATEGY_A':{'A':1.},'STRATEGY_B':{'A':1.}}
        p=self.balanced(args)
        self.assertEqual(p['status'],'READY_SYNTHETIC_FUNDED_PREVIEW')
        for row in p['quantity_preview']['scenarios']:
            self.assertEqual(len(row['trades']),1)
            self.assertAlmostEqual(row['fee_amount'],row['trades'][0]['notional']*.001)

    def test_minimum_fee_amount_cannot_hide_behind_false_flag(self):
        args=funded_fixture();args['execution_rules']['fees']['minimum_fee_amount']='1'
        self.assertIn('UNSUPPORTED_FEE_RULE',self.balanced(args)['gaps'])

    def test_absent_fee_and_policy_are_both_reported_without_candidate_execution(self):
        args=funded_fixture();args['risk_inputs']['account_policy']=None;args['execution_rules'].pop('fees')
        p=self.balanced(args)
        self.assertIn('UNBOUND_ACCOUNT_POLICY',p['gaps']);self.assertIn('FEE_RULE_UNBOUND',p['gaps'])

    def test_approximate_nav_reconciliation_is_not_treated_as_exact_quantity_cash(self):
        args=funded_fixture();args['account_contract']['valuation_tolerance']='.01';args['facts']['net_assets']='100.001'
        args['account_contract']['protected_capital_basis']='101'
        self.assertIn('QUANTITY_NAV_RECONCILIATION_REQUIRED',self.balanced(args)['gaps'])

    def test_frozen_cash_selector_matches_offline_cash_selection(self):
        import ast
        import __future__
        from types import SimpleNamespace
        from us_equity_strategies.research.funded_portfolio_preview import CASH_TAGS
        source=ROOT/'tests/research/fixtures/ibkr_cash_selector.py'
        names={'_normalize_account_ids','_matches_account','_coerce_float','_cash_value_for_currency'}
        parsed=ast.parse(source.read_text())
        native_priority=next(ast.literal_eval(n.value) for n in parsed.body if isinstance(n,ast.Assign)
            and any(isinstance(t,ast.Name) and t.id=='MARKET_CURRENCY_CASH_TAG_PRIORITY' for t in n.targets))
        self.assertEqual(CASH_TAGS,native_priority)
        nodes=[n for n in parsed.body if isinstance(n,ast.FunctionDef) and n.name in names]
        self.assertEqual({n.name for n in nodes},names)
        namespace=dict(MARKET_CURRENCY_CASH_TAG_PRIORITY=native_priority)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec',flags=__future__.annotations.compiler_flag),namespace)
        args=funded_fixture()
        args['cash_evidence']['account_values'].append(dict(account_id='ARTIFICIAL_ACCOUNT',currency='USD',tag='$LEDGER-CashBalance',value='100'))
        args['facts']['cash'][0]['source_tag']='$LEDGER-CashBalance'
        result=adapt_cash_evidence(args['facts'],args['account_contract'],args['cash_evidence'])
        rows=[SimpleNamespace(account=r['account_id'],currency=r['currency'],tag=r['tag'],value=r['value']) for r in args['cash_evidence']['account_values']]
        self.assertEqual(namespace['_cash_value_for_currency'](rows,currency='USD',account_ids=['ARTIFICIAL_ACCOUNT']),100.)
        self.assertEqual(result['selected_cash_tag'],'$LEDGER-CashBalance')


if __name__=='__main__':unittest.main()
