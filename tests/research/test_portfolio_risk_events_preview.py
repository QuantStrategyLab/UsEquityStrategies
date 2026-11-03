"""Artificial event policies and ledgers only; every number below is a fixture."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from test_funded_portfolio_preview import funded_fixture, header
from test_portfolio_settings_preview import request_fixture
from us_equity_strategies.research.auto_portfolio_account import digest
from us_equity_strategies.research.portfolio_risk_events_preview import (
    BASES, EVENT_FIELDS, evaluate_portfolio_risk_events, preview_portfolio_risk_settings,
    portfolio_risk_settings_capabilities, _portfolio_id,
)
from quant_platform_kit.common.strategy_risk_state import (
    StrategyRiskStateIdentity, build_strategy_risk_state_transition,
    StrategyRiskStateTransition, validate_strategy_risk_state_chain,
)


def event_fixture(held=False):
    packet = funded_fixture()
    limits = dict(stop_loss_ratio=.05, trailing_stop_ratio=.06, breaker_daily_loss=.03,
        breaker_drawdown=.1, resume_observation_sessions=5, resume_daily_loss_fraction_of_trigger=.5,
        resume_cvar_vol_fraction_of_limit=.8, max_drawdown_tolerance=.12,
        daily_cvar95_loss_limit=.02, target_annual_volatility=.1)
    policy = dict(**header(), policy_id='ARTIFICIAL_EVENT_POLICY', approved=True, **BASES,
        thresholds_by_level={k:deepcopy(limits) for k in ('conservative','balanced','growth')})
    identity = StrategyRiskStateIdentity('portfolio_risk_events','ARTIFICIAL_SCOPE',
        _portfolio_id(packet),digest(policy))
    context = dict(policy=policy, observations=dict(**header(),
        **{k:v for k,v in BASES.items() if k.endswith('_basis')},
        session='2000-01-04', valuation_at=packet['facts']['observed_at'],
        account_snapshot_sha256=digest(packet['facts']), fee_evidence_sha256=digest(packet['execution_rules']['fees']),
        sleeves_complete=True, external_flows_complete=True, session_start_nav=100.,session_net_external_flow=0.,
        flow_adjusted_nav_index=100., retained_nav_index_peak=100., daily_cvar95_loss_ratio=.001,
        annual_volatility_ratio=.01, reconciliation_status='VERIFIED', sleeves=[]),
        state_readback=dict(**header(),identity=identity.to_dict(),chain=[],expected_head_sha256=None,
            origin='VERIFIED_CLOSED_GENESIS'))
    if held:
        packet['facts']['cash'][0]['cash_balance']='80'
        packet['facts']['broker_reported_positions']=[dict(symbol='A',currency='USD',quantity='20',market_value='20')]
        packet['account_contract']['settled_cash']='80'
        for row in packet['cash_evidence']['account_values']:row['value']='80'
        context['observations'].update(account_snapshot_sha256=digest(packet['facts']),sleeves=[dict(
            strategy_id='STRATEGY_A',held_cycle_id='cycle-A',fee_adjusted_entry_value=20.,
            market_value=20.,net_liquidation_value=19.98,held_cycle_peak_net_value=19.98,
            security_market_values={'A':20.})])
    return packet, context


def evaluate(packet, context, **overrides):
    context=deepcopy(context);context['level']='balanced'
    return evaluate_portfolio_risk_events(context,packet=packet,overrides=overrides)


def add_plan(packet, context, target=None):
    context['reduction_plan']=dict(**header(), approved=True,
        policy_sha256=digest(context['policy']),account_snapshot_sha256=digest(packet['facts']),
        action='REDUCE_ONLY_UPPER_BOUND',target_weights=target or {'A':0.,'B':0.})


def set_prior(context, *, opened=True, nav_peak=100., sleeves=None):
    identity=StrategyRiskStateIdentity.from_dict(context['state_readback']['identity'])
    state=dict(breaker_open=opened,original_daily_trigger=.02 if opened else None,
        trigger_session='1999-12-20' if opened else None,
        latched_limits=deepcopy(context['policy']['thresholds_by_level']['balanced']) if opened else None,
        nav_peak=nav_peak,sleeves=sleeves or {})
    transition=build_strategy_risk_state_transition(identity=identity,effective_session='1999-12-21',
        input_sha256='a'*64,state=state)
    context['state_readback'].update(chain=[transition.to_dict()],origin='VERIFIED_CHAIN',
        expected_head_sha256=transition.transition_sha256)
    return transition


def add_recovery(context):
    dates=['1999-12-27','1999-12-28','1999-12-29','1999-12-30','2000-01-03']
    context['recovery']=dict(**header(),state_head_sha256=context['state_readback']['expected_head_sha256'],
        causes_resolved=True,remediated_at='1999-12-22T21:00:00Z',
        calendar=dict(**header(),completed_sessions_after_remediation=dates,last_completed_session=dates[-1]),
        sessions=[dict(session=d,completed_at=d+'T21:00:00Z',completed=True,reconciled=True,pending_state='CLEAR',
            drawdown_basis=BASES['drawdown_basis'],daily_loss_ratio=.001,daily_cvar95_loss_ratio=.001,
            annual_volatility_ratio=.01,drawdown_ratio=.001) for d in dates])


class EventEvidenceTests(unittest.TestCase):
    def test_no_policy_defaults_are_used(self):
        p,c=event_fixture();del c['policy']
        result=evaluate(p,c)
        self.assertIsNone(result['limits']);self.assertTrue(result['new_risk_prohibited'])

    def test_incomplete_policy_and_unapproved_policy_rejected(self):
        for change in ('field','level','approval'):
            p,c=event_fixture()
            if change=='field':del c['policy']['thresholds_by_level']['balanced']['stop_loss_ratio']
            elif change=='level':del c['policy']['thresholds_by_level']['growth']
            else:c['policy']['approved']=False
            self.assertNotEqual(evaluate(p,c)['gaps'],[])

    def test_all_cash_complete_evidence_is_clear(self):
        p,c=event_fixture();r=evaluate(p,c)
        self.assertEqual(r['gaps'],[]);self.assertEqual(r['active_events'],[])
        self.assertEqual(r['engine_action'],'no_action');self.assertFalse(r['new_risk_prohibited'])

    def test_stop_uses_net_exit_fees_and_entry_value(self):
        p,c=event_fixture(True);row=c['observations']['sleeves'][0]
        row['fee_adjusted_entry_value']=21.
        # Mark-only loss=4.76%; fee-net loss=4.86%, so the tighter request trips.
        add_plan(p,c);r=evaluate(p,c,stop_loss_ratio=.048)
        self.assertIn('SLEEVE_STOP_LOSS:cycle-A',r['active_events'])
        self.assertEqual(r['engine_action'],'risk_off')

    def test_exact_stop_boundary_trips(self):
        p,c=event_fixture(True);row=c['observations']['sleeves'][0]
        c['policy']['thresholds_by_level']['balanced']['stop_loss_ratio']=.1
        c['state_readback']['identity']['config_sha256']=digest(c['policy'])
        row['fee_adjusted_entry_value']=22.2  # 19.98/22.2=.9 exactly
        add_plan(p,c);r=evaluate(p,c,stop_loss_ratio=.1)
        self.assertIn('SLEEVE_STOP_LOSS:cycle-A',r['active_events'])

    def test_original_stricter_stop_is_not_loosened(self):
        p,c=event_fixture(True);c['observations']['sleeves'][0]['fee_adjusted_entry_value']=21.2
        add_plan(p,c);r=evaluate(p,c,stop_loss_ratio=.1)
        self.assertEqual(r['limits']['stop_loss_ratio'],.05)
        self.assertIn('SLEEVE_STOP_LOSS:cycle-A',r['active_events'])

    def test_trailing_stop_uses_held_cycle_highwater(self):
        p,c=event_fixture(True);c['observations']['sleeves'][0]['held_cycle_peak_net_value']=22.
        add_plan(p,c);self.assertIn('SLEEVE_TRAILING_STOP:cycle-A',evaluate(p,c)['active_events'])

    def test_sleeve_valuation_and_security_attribution_must_reconcile(self):
        for field,value in [('market_value',21.),('security_market_values',{'B':20.}),('net_liquidation_value',20.)]:
            p,c=event_fixture(True);c['observations']['sleeves'][0][field]=value
            self.assertNotEqual(evaluate(p,c)['gaps'],[])

    def test_missing_and_duplicate_sleeves_do_not_mean_flat(self):
        for duplicate in (False,True):
            p,c=event_fixture(True)
            c['observations']['sleeves'] = c['observations']['sleeves']*2 if duplicate else []
            self.assertNotEqual(evaluate(p,c)['gaps'],[])

    def test_unknown_or_changed_cost_evidence_is_rejected(self):
        for change in ('digest','minimum','bps'):
            p,c=event_fixture(True)
            if change=='digest':c['observations']['fee_evidence_sha256']='b'*64
            elif change=='minimum':p['execution_rules']['fees']['minimum_fee_applies']=True
            else:p['execution_rules']['fees']['bps_by_symbol']['A']='50'
            self.assertNotEqual(evaluate(p,c)['gaps'],[])

    def test_daily_loss_accounts_for_explicit_external_flow(self):
        p,c=event_fixture();c['observations'].update(session_start_nav=100.,session_net_external_flow=3.)
        add_plan(p,c);r=evaluate(p,c)
        self.assertEqual(r['metrics']['daily_loss_ratio'],.03)
        self.assertIn('DAILY_LOSS_BREAKER',r['active_events'])

    def test_withdrawal_is_not_automatically_investment_loss(self):
        p,c=event_fixture();c['observations'].update(session_start_nav=110.,session_net_external_flow=-10.)
        self.assertEqual(evaluate(p,c)['metrics']['daily_loss_ratio'],0.)

    def test_drawdown_at_boundary_trips_without_daily_loss(self):
        p,c=event_fixture();c['observations'].update(flow_adjusted_nav_index=90.)
        add_plan(p,c);r=evaluate(p,c)
        self.assertEqual(r['metrics']['drawdown_ratio'],.1)
        self.assertEqual(r['metrics']['daily_loss_ratio'],0.)
        self.assertIn('DRAWDOWN_BREAKER',r['active_events'])

    def test_stale_future_identity_and_non_synthetic_sources_fail(self):
        for key,value in [('observed_at','1999-01-01T00:00:00Z'),('available_at','2000-01-04T13:00:00Z'),
                ('scope_ref','OTHER'),('source_version',''),('synthetic',False)]:
            p,c=event_fixture();c['observations'][key]=value
            self.assertNotEqual(evaluate(p,c)['gaps'],[])

    def test_snapshot_and_unit_basis_must_match(self):
        for key,value in [('account_snapshot_sha256','f'*64),('daily_loss_basis','CASH_BALANCE'),
                ('drawdown_basis','NAV_RESET_ON_MODE_SWITCH'),('external_flows_complete',False)]:
            p,c=event_fixture();c['observations'][key]=value
            self.assertNotEqual(evaluate(p,c)['gaps'],[])

    def test_boolean_nan_missing_numbers_fail_closed(self):
        for value in (True,float('nan'),None):
            p,c=event_fixture();c['observations']['session_net_external_flow']=value
            self.assertNotEqual(evaluate(p,c)['gaps'],[])

    def test_kernel_reject_does_not_manufacture_exit_target(self):
        p,c=event_fixture();c['observations']['flow_adjusted_nav_index']=80.
        r=evaluate(p,c)
        self.assertIn('APPROVED_EVENT_REDUCTION_TARGET_MISSING',r['gaps'])
        self.assertIsNone(r['forced_target']);self.assertTrue(r['new_risk_prohibited'])

    def test_unapproved_cross_policy_or_increasing_exit_plan_fails(self):
        for field,value in [('approved',False),('policy_sha256','a'*64),('target_weights',{'A':.1,'B':0.}),
                ('target_weights',{'A':0.})]:
            p,c=event_fixture();set_prior(c);add_plan(p,c);c['reduction_plan'][field]=value
            self.assertNotEqual(evaluate(p,c)['gaps'],[])

    def test_unknown_pending_and_unverified_reconciliation_stay_open(self):
        for source in ('cash','observation'):
            p,c=event_fixture()
            if source=='cash':p['cash_evidence']['pending_state']='UNKNOWN'
            else:c['observations']['reconciliation_status']='UNVERIFIED'
            r=evaluate(p,c)
            self.assertEqual(r['engine_action'],'blocked');self.assertTrue(r['transition']['state']['breaker_open'])
            self.assertNotEqual(r['gaps'],[])

    def test_original_engine_and_health_projection_are_called(self):
        import us_equity_strategies.research.portfolio_risk_events_preview as module
        p,c=event_fixture()
        with patch.object(module,'RiskEngine',wraps=module.RiskEngine) as engine, patch.object(
                module,'project_cycle_new_risk_health_axes',wraps=module.project_cycle_new_risk_health_axes) as health:
            self.assertEqual(evaluate(p,c)['gaps'],[])
        engine.assert_called_once();health.assert_called_once()

    def test_public_event_adapter_rejects_unknown_override_keys(self):
        p,c=event_fixture()
        self.assertIn('EVENT_OVERRIDE_UNSUPPORTED',evaluate(p,c,unknown_loss=.01)['gaps'])


class EventStateAndRecoveryTests(unittest.TestCase):
    def test_durable_open_survives_clear_prices_and_mode_changes(self):
        p,c=event_fixture();set_prior(c);add_plan(p,c)
        for mode in ('simple','advanced'):
            r=preview_portfolio_risk_settings(request_fixture(mode),event_context=c,**p)
            self.assertTrue(r['requested_events']['new_risk_prohibited'])
            self.assertFalse(r['breaker_reset'])

    def test_state_chain_missing_tampered_or_wrong_head_rejected(self):
        for change in ('missing','tamper','head','identity'):
            p,c=event_fixture();set_prior(c)
            if change=='missing':c['state_readback']['chain']=[]
            elif change=='tamper':c['state_readback']['chain'][0]['state']['breaker_open']=False
            elif change=='head':c['state_readback']['expected_head_sha256']='b'*64
            else:c['state_readback']['identity']['candidate_id']='OTHER'
            self.assertNotEqual(evaluate(p,c)['gaps'],[])

    def test_changed_portfolio_cannot_reuse_even_a_genesis_identity(self):
        p,c=event_fixture();p['moments']['strategy_versions']['STRATEGY_A']='v2'
        self.assertIn('EVENT_STATE_IDENTITY_MISMATCH',evaluate(p,c)['gaps'])

    def test_repeated_or_backward_session_rejected(self):
        p,c=event_fixture();r=evaluate(p,c);wire=c['state_readback']
        wire.update(chain=[r['transition']],expected_head_sha256=r['transition']['transition_sha256'],origin='VERIFIED_CHAIN')
        self.assertIn('EVENT_STATE_SESSION_NOT_ADVANCING',evaluate(p,c)['gaps'])

    def test_account_peak_never_resets_to_current_nav(self):
        p,c=event_fixture();set_prior(c,opened=False,nav_peak=120.)
        self.assertIn('EVENT_HIGH_WATERMARK_REGRESSION',evaluate(p,c)['gaps'])

    def test_held_cycle_peak_and_entry_survive_level_change(self):
        for changed in ('peak','entry'):
            p,c=event_fixture(True);old=dict(entry=20.,peak=21.,stop_limit=.02,trailing_limit=.03,strategy_id='STRATEGY_A')
            if changed=='entry':old.update(entry=19.,peak=19.)
            set_prior(c,opened=False,sleeves={'cycle-A':old})
            self.assertNotEqual(evaluate(p,c)['gaps'],[])

    def test_existing_stricter_sleeve_stop_preserved_on_restore(self):
        p,c=event_fixture(True);set_prior(c,opened=False,sleeves={'cycle-A':dict(
            entry=20.,peak=19.98,stop_limit=.0005,trailing_limit=.03,strategy_id='STRATEGY_A')})
        add_plan(p,c);r=evaluate(p,c)
        self.assertIn('SLEEVE_STOP_LOSS:cycle-A',r['active_events'])

    def test_missing_recovery_does_not_clear_open(self):
        p,c=event_fixture();set_prior(c);add_plan(p,c);r=evaluate(p,c)
        self.assertEqual(r['recovery']['status'],'RECOVERY_EVIDENCE_MISSING')
        self.assertTrue(r['transition']['state']['breaker_open'])

    def test_clean_sessions_only_qualify_reset_review(self):
        p,c=event_fixture();prior=set_prior(c);add_plan(p,c);add_recovery(c);r=evaluate(p,c)
        self.assertEqual(r['recovery']['status'],'QUALIFIED_FOR_RESET_REVIEW')
        self.assertTrue(r['new_risk_prohibited']);self.assertFalse(r['breaker_reset'])
        self.assertTrue(r['transition']['state']['breaker_open'])
        validate_strategy_risk_state_chain([prior,StrategyRiskStateTransition.from_dict(r['transition'])])

    def test_daily_recovery_uses_original_trigger_not_new_looser_level(self):
        p,c=event_fixture();set_prior(c);add_plan(p,c);add_recovery(c)
        c['recovery']['sessions'][-1]['daily_loss_ratio']=.01
        r=evaluate(p,c,breaker_daily_loss=.04,resume_daily_loss_fraction_of_trigger=.5)
        self.assertEqual(r['recovery']['strict_upper_bounds']['daily_loss_ratio'],.01)
        self.assertEqual(r['recovery']['status'],'RECOVERY_NOT_QUALIFIED')

    def test_cvar_and_volatility_are_separate_strict_monitor_units(self):
        for key,value in [('daily_cvar95_loss_ratio',.016),('annual_volatility_ratio',.08)]:
            p,c=event_fixture();set_prior(c);add_plan(p,c);add_recovery(c)
            c['recovery']['sessions'][-1][key]=value
            self.assertEqual(evaluate(p,c)['recovery']['status'],'RECOVERY_NOT_QUALIFIED')

    def test_drawdown_breach_cannot_be_cleared_by_waiting(self):
        p,c=event_fixture();set_prior(c);add_plan(p,c);add_recovery(c)
        c['observations']['flow_adjusted_nav_index']=89.
        self.assertEqual(evaluate(p,c)['recovery']['status'],'RECOVERY_NOT_QUALIFIED')

    def test_breach_resets_consecutive_observation_count(self):
        p,c=event_fixture();set_prior(c);add_plan(p,c);add_recovery(c)
        c['recovery']['sessions'][2]['daily_loss_ratio']=.02
        r=evaluate(p,c)['recovery'];self.assertEqual(r['consecutive_clean_sessions'],2)
        self.assertEqual(r['status'],'RECOVERY_NOT_QUALIFIED')

    def test_missing_duplicate_future_or_incomplete_sessions_fail(self):
        for change in ('missing','duplicate','future','incomplete','pending','unresolved','head'):
            p,c=event_fixture();set_prior(c);add_plan(p,c);add_recovery(c);r=c['recovery']
            if change=='missing':r['sessions'].pop(1)
            elif change=='duplicate':r['sessions'][1]=deepcopy(r['sessions'][0])
            elif change=='future':r['sessions'][-1]['completed_at']='2000-01-04T13:00:00Z'
            elif change=='incomplete':r['sessions'][-1]['completed']=False
            elif change=='pending':r['sessions'][-1]['pending_state']='UNKNOWN'
            elif change=='unresolved':r['causes_resolved']=False
            else:r['state_head_sha256']='c'*64
            self.assertEqual(evaluate(p,c)['recovery']['status'],'RECOVERY_EVIDENCE_INVALID')

    def test_recovery_overrides_can_only_tighten_original_policy(self):
        p,c=event_fixture();set_prior(c);add_plan(p,c);add_recovery(c)
        r=evaluate(p,c,resume_observation_sessions=6,resume_daily_loss_fraction_of_trigger=.1,
            resume_cvar_vol_fraction_of_limit=.2)
        self.assertEqual(r['limits']['resume_observation_sessions'],6)
        self.assertEqual(r['recovery']['status'],'RECOVERY_NOT_QUALIFIED')


class RiskSettingsIntegrationTests(unittest.TestCase):
    def test_reduction_plan_covers_securities_outside_original_risk_subset(self):
        for mode in ('simple','advanced'):
            p,c=event_fixture(True)
            p['risk_inputs']['hard_policy']=replace(p['risk_inputs']['hard_policy'],risk_symbols=('B',))
            p['moments']['mu_excess']=[.01,0.]
            scenarios=p['risk_inputs']['scenario_set']
            p['risk_inputs']['scenario_set']=replace(scenarios,scenarios=tuple(
                replace(s,earn_returns=dict.fromkeys(scenarios.symbols,0.)) for s in scenarios.scenarios))
            set_prior(c,opened=True);add_plan(p,c)
            r=preview_portfolio_risk_settings(request_fixture(mode),event_context=c,**p)
            self.assertEqual(r['gaps'],[])
            self.assertIsNotNone(r['draft_settings'])
            self.assertEqual(r['draft_settings']['security_target_weights'],{'A':0.,'B':0.})
            for row in r['allocation_preview']['requested_preview']['quantity_preview']['scenarios']:
                self.assertEqual(row['remaining_quantity']['A'],0.)

    def test_both_modes_and_three_levels_use_event_aware_entry(self):
        p,c=event_fixture()
        for mode in ('simple','advanced'):
            for level in ('conservative','balanced','growth'):
                req=request_fixture(mode);req['level']=level
                r=preview_portfolio_risk_settings(req,event_context=c,**p)
                self.assertEqual(r['status'],'READY_SYNTHETIC_RISK_SETTINGS_PREVIEW',r['gaps'])
                self.assertIsNotNone(r['draft_settings']);self.assertFalse(r['settings_applied'])

    def test_missing_context_blocks_draft_in_both_modes(self):
        for mode in ('simple','advanced'):
            r=preview_portfolio_risk_settings(request_fixture(mode),**funded_fixture())
            self.assertIsNone(r['draft_settings']);self.assertNotEqual(r['gaps'],[])

    def test_request_cannot_inject_level_or_hidden_candidate_preference(self):
        p,c=event_fixture();c['level']='growth'
        self.assertIsNone(preview_portfolio_risk_settings(request_fixture(),event_context=c,**p)['draft_settings'])
        del c['level'];p['candidate_preferences']={'level':'balanced','c':.4}
        r=preview_portfolio_risk_settings(request_fixture(),event_context=c,**p)
        self.assertEqual(r['status'],'INVALID_SETTINGS_REQUEST')

    def test_simple_override_restore_conflict_and_unsupported_take_profit_rejected(self):
        p,c=event_fixture()
        requests=[request_fixture('simple',stop_loss_ratio=.03),request_fixture('advanced',take_profit_ratio=.1)]
        req=request_fixture('advanced',stop_loss_ratio=.03);req['restore_system_recommendation']=True;requests.append(req)
        for req in requests:
            self.assertEqual(preview_portfolio_risk_settings(req,event_context=c,**p)['status'],'INVALID_SETTINGS_REQUEST')

    def test_request_numeric_limits_boolean_and_fractional_sessions_rejected(self):
        p,c=event_fixture()
        for key,value in [('stop_loss_ratio',True),('stop_loss_ratio',.5),('resume_observation_sessions',5.5),
                ('breaker_daily_loss',float('nan'))]:
            r=preview_portfolio_risk_settings(request_fixture('advanced',**{key:value}),event_context=c,**p)
            self.assertIsNone(r['draft_settings'])

    def test_tighter_stop_and_manual_strategy_settings_share_core(self):
        p,c=event_fixture();req=request_fixture('advanced',stop_loss_ratio=.03,kelly_fraction=.1)
        r=preview_portfolio_risk_settings(req,event_context=c,**p)
        self.assertEqual(r['status'],'READY_SYNTHETIC_RISK_SETTINGS_PREVIEW')
        self.assertEqual(r['constrained_parameters']['event_limits']['stop_loss_ratio'],.03)
        self.assertEqual(r['constrained_parameters']['allocation']['kelly_fraction'],.1)

    def test_requested_tighter_trigger_keeps_original_policy_recommendation_separate(self):
        p,c=event_fixture(True);c['observations']['sleeves'][0]['fee_adjusted_entry_value']=21.;add_plan(p,c)
        req=request_fixture('advanced',stop_loss_ratio=.048)
        r=preview_portfolio_risk_settings(req,event_context=c,**p)
        self.assertEqual(r['status'],'READY_SYNTHETIC_RISK_SETTINGS_PREVIEW',r['gaps'])
        self.assertFalse(r['system_events']['new_risk_prohibited']);self.assertTrue(r['requested_events']['new_risk_prohibited'])
        self.assertGreater(r['system_recommendation']['composed_proposal']['A'],.001)
        # This unchanged artificial reference leaves rounding risk. Do not tune
        # it away or substitute the requested zero target for the reference.
        self.assertIsNone(r['system_recommendation']['draft_settings'])
        self.assertIn('ROUNDED_TAIL_BUDGET',r['system_recommendation']['gaps'])
        self.assertLessEqual(r['draft_settings']['security_target_weights']['A'],1e-9)
        self.assertIn('stop_loss_ratio',r['p4_preview']['requested_settings']['overrides'])

    def test_manual_strategy_weights_cannot_bypass_event_cap(self):
        from test_portfolio_settings_preview import explicit_request
        p,c=event_fixture(True);set_prior(c);add_plan(p,c)
        req=explicit_request({'STRATEGY_A':.5,'STRATEGY_B':.2})
        r=preview_portfolio_risk_settings(req,event_context=c,**p)
        self.assertEqual(r['status'],'READY_SYNTHETIC_RISK_SETTINGS_PREVIEW',r['gaps'])
        self.assertTrue(all(v<=1e-9 for v in r['draft_settings']['security_target_weights'].values()))
        self.assertFalse(r['final_target_is_qp_optimum'])

    def test_triggered_exit_passes_original_cash_fees_quantity_and_tail_checks(self):
        p,c=event_fixture(True);c['observations']['flow_adjusted_nav_index']=80.;add_plan(p,c)
        r=preview_portfolio_risk_settings(request_fixture(),event_context=c,**p)
        self.assertEqual(r['status'],'READY_SYNTHETIC_RISK_SETTINGS_PREVIEW',r['gaps'])
        q=r['allocation_preview']['requested_preview']['quantity_preview']
        self.assertEqual(q['status'],'QUANTITY_COST_RISK_CHECKED_SYNTHETIC')
        for row in q['scenarios']:
            self.assertEqual(row['original_cap_failures'],[])
            self.assertLessEqual(row['gross_buy_plus_fees'],row['spendable_cash'])
        self.assertTrue(r['requested_events']['new_risk_prohibited']);self.assertFalse(r['execution_authorized'])

    def test_price_gap_is_not_guaranteed_stop_price_or_loss_ceiling(self):
        p,c=event_fixture(True);c['observations']['sleeves'][0]['fee_adjusted_entry_value']=40.;add_plan(p,c)
        r=preview_portfolio_risk_settings(request_fixture(),event_context=c,**p)
        self.assertGreater(r['requested_events']['metrics']['sleeves'][0]['loss_ratio'],.4)
        self.assertFalse(r['financial_risk_certified'])

    def test_no_plan_cannot_expose_pre_stop_allocation(self):
        p,c=event_fixture(True);c['observations']['flow_adjusted_nav_index']=80.
        r=preview_portfolio_risk_settings(request_fixture(),event_context=c,**p)
        self.assertIsNone(r['draft_settings']);self.assertIsNone(r['allocation_preview'])

    def test_original_forced_target_is_not_widened(self):
        p,c=event_fixture(True);set_prior(c);add_plan(p,c,{'A':.1,'B':0.})
        p['risk_inputs']['forced_target']={'A':0.,'B':0.}
        r=preview_portfolio_risk_settings(request_fixture(),event_context=c,**p)
        self.assertEqual(r['status'],'READY_SYNTHETIC_RISK_SETTINGS_PREVIEW',r['gaps'])
        self.assertLessEqual(r['draft_settings']['security_target_weights']['A'],1e-9)

    def test_dust_cannot_be_falsely_reported_as_fully_liquidated(self):
        p,c=event_fixture(True);set_prior(c);add_plan(p,c)
        p['execution_rules']['quantity']['step_by_symbol']['A']='3'
        r=preview_portfolio_risk_settings(request_fixture(),event_context=c,**p)
        self.assertIsNone(r['draft_settings']);self.assertNotEqual(r['gaps'],[])

    def test_open_weight_cap_cannot_allow_replenishment_buy_after_fill_drift(self):
        from test_portfolio_settings_preview import explicit_request
        p,c=event_fixture(True);set_prior(c);add_plan(p,c,{'A':.2,'B':0.})
        p['execution_rules']['quantity']['step_by_symbol']['A']='0.01'
        scenarios=p['risk_inputs']['scenario_set']
        p['risk_inputs']['scenario_set']=replace(scenarios,scenarios=tuple(replace(s,
            fill_returns={'A':-.01,'B':0.},earn_returns={'A':0.,'B':0.}) for s in scenarios.scenarios))
        r=preview_portfolio_risk_settings(explicit_request({'STRATEGY_A':.2,'STRATEGY_B':0.}),event_context=c,**p)
        self.assertIn('EVENT_REDUCTION_WOULD_BUY_AFTER_FILL_DRIFT',r['gaps'])
        self.assertIsNone(r['draft_settings']);self.assertIsNone(r['allocation_preview'])
        self.assertGreater(r['conditional_buys_detected'][0]['trade']['quantity'],0.)

    def test_event_source_changes_preview_commitment(self):
        p,c=event_fixture();req=request_fixture()
        first=preview_portfolio_risk_settings(req,event_context=c,**p)
        c['observations']['source_version']='fixture_v2'
        second=preview_portfolio_risk_settings(req,event_context=c,**p)
        self.assertNotEqual(first['p4_preview']['confirmation_binding']['preview_sha256'],
            second['p4_preview']['confirmation_binding']['preview_sha256'])

    def test_repeated_previews_are_deterministic_and_do_not_mutate_inputs(self):
        p,c=event_fixture(True);set_prior(c);add_plan(p,c);add_recovery(c)
        saved=deepcopy(c);req=request_fixture('advanced',stop_loss_ratio=.03)
        a=preview_portfolio_risk_settings(req,event_context=c,**p)
        self.assertEqual(a,preview_portfolio_risk_settings(req,event_context=c,**p));self.assertEqual(c,saved)
        for flag in ('breaker_reset','state_persisted','settings_applied','execution_authorized'):self.assertFalse(a[flag])

    def test_full_request_p4_schema_and_hash_are_preserved(self):
        from jsonschema import Draft202012Validator,FormatChecker
        from referencing import Registry,Resource
        from test_auto_portfolio_preview import ROOT
        p,c=event_fixture();req=request_fixture('advanced',stop_loss_ratio=.08,resume_observation_sessions=6)
        r=preview_portfolio_risk_settings(req,event_context=c,**p);p4=r['p4_preview']
        path=ROOT/'docs/research/risk-levels'
        settings=json.loads((path/'risk-settings.schema.json').read_text())
        validator=Draft202012Validator(json.loads((path/'p4-preview.schema.json').read_text()),
            registry=Registry().with_resource(settings['$id'],Resource.from_contents(settings)),format_checker=FormatChecker())
        validator.validate(p4);self.assertEqual(p4['requested_settings'],req)
        self.assertTrue(p4['parameter_comparison']['stop_loss_ratio']['confirmation_required'])
        self.assertIsNone(p4['parameter_comparison']['stop_loss_ratio']['effective_value'])
        body=deepcopy(p4)
        for key in ('preview_sha256','server_confirmation_ref','confirmed_at_utc','expires_at_utc'):body['confirmation_binding'].pop(key)
        self.assertEqual(digest(body),p4['confirmation_binding']['preview_sha256'])

    def test_capabilities_distinguish_old_entry_and_conditional_support(self):
        cap=portfolio_risk_settings_capabilities(funded_fixture()['definition_bytes'])
        self.assertTrue(cap['event_evidence_required_in_both_modes'])
        self.assertEqual(cap['fields']['take_profit_ratio']['status'],'CONSUMER_UNSUPPORTED')
        for key in EVENT_FIELDS:
            self.assertEqual(cap['fields'][key]['status'],'SUPPORTED_WITH_BOUND_EVENT_EVIDENCE_OFFLINE')


if __name__ == '__main__':unittest.main()
