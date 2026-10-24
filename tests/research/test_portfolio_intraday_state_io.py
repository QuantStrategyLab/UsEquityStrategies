"""Artificial intraday events and provenance; no real account or backtest."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch

from test_portfolio_risk_events_preview import event_fixture, add_plan, set_prior
from test_portfolio_settings_preview import request_fixture
from us_equity_strategies.research.auto_portfolio_account import digest
from us_equity_strategies.research.portfolio_event_journal import PortfolioEventJournal, build_event_record
from us_equity_strategies.research.portfolio_intraday_state_io import (
    BINDING_VERSION, INITIALIZATION_VERSION, MIGRATION_VERSION,
    initialize_portfolio_event_state, read_portfolio_event_state,
    persist_portfolio_intraday_preview, migrate_portfolio_event_state, prepare_daily_state_import,
)
from us_equity_strategies.research.portfolio_risk_events_preview import _portfolio_id
from quant_platform_kit.common.strategy_risk_state import StrategyRiskStateIdentity, StrategyRiskStateStore


def intraday_fixture(opened=False,held=False):
    packet,context=event_fixture(held)
    # Fixtures freeze the original artificial policy across these intraday
    # observations; this age allowance does not define a risk threshold.
    context['policy']['max_age_seconds']=200000
    context['state_readback']['identity']['config_sha256']=digest(context['policy'])
    binding=deepcopy(context.pop('state_readback'))
    binding.pop('chain');binding.pop('origin')
    binding['schema_version']=BINDING_VERSION
    state=dict(breaker_open=opened,original_daily_trigger=.03 if opened else None,
        trigger_session='2000-01-04' if opened else None,
        latched_limits=deepcopy(context['policy']['thresholds_by_level']['balanced']) if opened else None,
        nav_peak=100.,sleeves={})
    initial=dict(risk_state=state,session='2000-01-04',session_start_nav=100.,session_net_external_flow=0.)
    proof={k:deepcopy(binding[k]) for k in binding if k not in ('schema_version','identity','expected_head_sha256')}
    proof.update(schema_version=INITIALIZATION_VERSION,action='INITIALIZE_FROZEN_STATE',
        provenance_ref='ARTIFICIAL_INITIAL_STATE_RECORD',identity=deepcopy(binding['identity']),
        state=initial,state_sha256=digest(initial))
    if opened:add_plan(packet,context)
    return packet,context,binding,proof


def move_observation(packet,context,binding,seconds=30,next_day=False):
    def move(value):
        if isinstance(value,dict):return {k:move(v) for k,v in value.items()}
        if isinstance(value,list):return [move(v) for v in value]
        if isinstance(value,str):
            if next_day:return value.replace('2000-01-04','2000-01-05')
            return value.replace('12:00:00Z',f'12:{seconds//60:02d}:{seconds%60:02d}Z').replace(
                '12:01:00Z',f'12:{1+seconds//60:02d}:{seconds%60:02d}Z')
        return value
    p,c,b=move(deepcopy(packet)),move(deepcopy(context)),move(deepcopy(binding))
    c['policy']=deepcopy(context['policy'])
    c['observations']['account_snapshot_sha256']=digest(p['facts'])
    c['observations']['fee_evidence_sha256']=digest(p['execution_rules']['fees'])
    if c.get('reduction_plan'):
        c['reduction_plan']['policy_sha256']=digest(c['policy'])
        c['reduction_plan']['account_snapshot_sha256']=digest(p['facts'])
    return p,c,b


def migration_fixture(binding,source_head):
    destination=deepcopy(binding)
    destination['identity']['config_sha256']='d'*64
    destination['expected_head_sha256']=None
    proof={k:deepcopy(binding[k]) for k in binding if k not in ('schema_version','identity','expected_head_sha256')}
    proof.update(schema_version=MIGRATION_VERSION,action='MIGRATE_FROZEN_STATE',
        provenance_ref='ARTIFICIAL_POLICY_HANDOFF',source_identity=deepcopy(binding['identity']),
        destination_identity=deepcopy(destination['identity']),source_head_sha256=source_head['record_sha256'],
        state_sha256=digest(source_head['state']))
    return destination,proof


class IntradayStateTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='ues-artificial-intraday-')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.journal=PortfolioEventJournal(self.root/'journal',offline_root=self.root)

    def seed(self,opened=False,held=False):
        p,c,b,proof=intraday_fixture(opened,held)
        result=initialize_portfolio_event_state(journal=self.journal,binding=b,
            account_contract=p['account_contract'],initialization=proof)
        self.assertEqual(result['gaps'],[])
        self.assertTrue(result['readback_verified'])
        self.assertIsNone(result['draft_settings'])
        return p,c,result['next_binding'],proof

    def cycle(self,p,c,b,event_id='event-1',mode='simple'):
        return persist_portfolio_intraday_preview(request_fixture(mode),journal=self.journal,
            binding=b,event_id=event_id,event_context=c,**p)

    def read(self,p,b):
        return read_portfolio_event_state(journal=self.journal,binding=b,account_contract=p['account_contract'])

    def test_empty_store_has_no_closed_state_and_needs_explicit_provenance(self):
        p,c,b,proof=intraday_fixture()
        read=self.read(p,b)
        self.assertIn('INTRADAY_EXPLICIT_CHECKPOINT_REQUIRED',read['gaps'])
        self.assertEqual(read['breaker_state'],'UNKNOWN');self.assertTrue(read['new_risk_prohibited'])
        for missing in (None,{},dict(proof,provenance_ref=''),dict(proof,state_sha256='f'*64)):
            result=initialize_portfolio_event_state(journal=self.journal,binding=b,
                account_contract=p['account_contract'],initialization=missing)
            self.assertFalse(result['readback_verified']);self.assertTrue(result['new_risk_prohibited'])
        self.assertEqual(list(self.root.rglob('*.json')),[])

    def test_initialization_checks_scope_identity_time_and_complete_state(self):
        for key,value in [('account_id','OTHER'),('synthetic',False),('available_at','2000-01-04T13:00:00Z'),
                ('source_version',''),('action','ASSUME_CLOSED')]:
            p,c,b,proof=intraday_fixture();proof[key]=value
            result=initialize_portfolio_event_state(journal=self.journal,binding=b,
                account_contract=p['account_contract'],initialization=proof)
            self.assertNotEqual(result['gaps'],[])
        p,c,b,proof=intraday_fixture();proof['state']['risk_state']['nav_peak']=0.
        proof['state_sha256']=digest(proof['state'])
        self.assertNotEqual(initialize_portfolio_event_state(journal=self.journal,binding=b,
            account_contract=p['account_contract'],initialization=proof)['gaps'],[])
        self.assertEqual(list(self.root.rglob('*.json')),[])

    def test_frozen_initialization_is_idempotent_but_cannot_reset_a_used_chain(self):
        p,c,b,proof=self.seed()
        original=dict(b,expected_head_sha256=None)
        replay=initialize_portfolio_event_state(journal=self.journal,binding=original,
            account_contract=p['account_contract'],initialization=proof)
        self.assertTrue(replay['readback_verified']);self.assertEqual(len(self.journal.load_chain(b['identity'])),1)
        self.assertTrue(self.cycle(p,c,b)['readback_verified'])
        reset=initialize_portfolio_event_state(journal=self.journal,binding=original,
            account_contract=p['account_contract'],initialization=proof)
        self.assertIn('INTRADAY_INITIALIZATION_ALREADY_EXISTS',reset['gaps'])
        self.assertEqual(len(self.journal.load_chain(b['identity'])),2)

    def test_not_yet_effective_at_issuance_is_rejected_before_initialization_write(self):
        p,c,b,proof=intraday_fixture()
        proof['valid_from']='2000-01-04T12:00:30Z'
        with patch.object(self.journal,'append',side_effect=AssertionError('must reject before write')) as append:
            r=initialize_portfolio_event_state(journal=self.journal,binding=b,
                account_contract=p['account_contract'],initialization=proof)
        self.assertFalse(r['readback_verified']);self.assertTrue(r['new_risk_prohibited'])
        self.assertEqual(append.call_count,0);self.assertEqual(list(self.root.rglob('*.json')),[])

    def test_initializer_newer_than_checkpoint_is_rejected_before_write(self):
        p,c,b,proof=intraday_fixture()
        proof.update(observed_at='2000-01-04T12:00:30Z',available_at='2000-01-04T12:00:30Z')
        with patch.object(self.journal,'append',side_effect=AssertionError('must not write')) as append:
            r=initialize_portfolio_event_state(journal=self.journal,binding=b,
                account_contract=p['account_contract'],initialization=proof)
        self.assertIn('INTRADAY_FUTURE_STATE',r['gaps']);self.assertEqual(append.call_count,0)

    def test_new_policy_cannot_initialize_another_closed_root_in_the_same_scope(self):
        p,c,b,proof=self.seed(opened=True)
        candidate=deepcopy(b);candidate['expected_head_sha256']=None
        candidate['identity']['config_sha256']='e'*64
        new=deepcopy(proof);new['identity']=deepcopy(candidate['identity'])
        new['state']['risk_state'].update(breaker_open=False,original_daily_trigger=None,
            trigger_session=None,latched_limits=None)
        new['state_sha256']=digest(new['state'])
        r=initialize_portfolio_event_state(journal=self.journal,binding=candidate,
            account_contract=p['account_contract'],initialization=new)
        self.assertFalse(r['readback_verified']);self.assertTrue(r['new_risk_prohibited'])
        self.assertEqual(self.read(p,b)['breaker_state'],'OPEN')
        self.assertEqual(self.journal.load_chain(candidate['identity']),[])

    def test_event_newer_than_fresh_checkpoint_is_rejected_before_write(self):
        p,c,b,proof=self.seed()
        p,c,updated=move_observation(p,c,b)
        with patch.object(self.journal,'append',side_effect=AssertionError('must not write')) as append:
            r=self.cycle(p,c,b)
        self.assertIn('INTRADAY_FUTURE_STATE',r['gaps']);self.assertEqual(append.call_count,0)
        self.assertIsNone(r['draft_settings']);self.assertTrue(r['new_risk_prohibited'])

    def test_same_day_closed_to_open_and_multiple_open_events_in_both_modes(self):
        for mode in ('simple','advanced'):
            temporary=tempfile.TemporaryDirectory(dir=self.root);self.addCleanup(temporary.cleanup)
            self.journal=PortfolioEventJournal(Path(temporary.name)/'journal',offline_root=Path(temporary.name))
            p,c,b,proof=self.seed()
            first=self.cycle(p,c,b,'clear',mode)
            self.assertTrue(first['readback_verified'],first['gaps'])
            self.assertEqual(first['breaker_state'],'CLOSED')
            p,c,b=move_observation(p,c,first['next_binding'])
            c['observations']['flow_adjusted_nav_index']=80.;add_plan(p,c)
            second=self.cycle(p,c,b,'trip',mode)
            self.assertTrue(second['readback_verified'],second['gaps'])
            self.assertEqual(second['breaker_state'],'OPEN');self.assertTrue(second['new_risk_prohibited'])
            c['observations']['flow_adjusted_nav_index']=100.
            third=self.cycle(p,c,second['next_binding'],'still-open',mode)
            self.assertTrue(third['readback_verified'],third['gaps']);self.assertEqual(third['breaker_state'],'OPEN')
            chain=self.journal.load_chain(b['identity'])
            self.assertEqual([x['sequence'] for x in chain],[0,1,2,3])
            self.assertEqual({x['effective_session'] for x in chain},{'2000-01-04'})
            self.assertEqual(chain[-1]['state']['risk_state']['trigger_session'],'2000-01-04')
            self.assertFalse(third['breaker_reset']);self.assertFalse(third['settings_applied'])

    def test_intraday_baseline_cannot_be_reset_before_or_after_round_trip(self):
        p,c,b,proof=self.seed()
        c['observations']['session_start_nav']=99.
        r=self.cycle(p,c,b)
        self.assertFalse(r['readback_verified']);self.assertIsNone(r['draft_settings'])
        self.assertEqual(len(self.journal.load_chain(b['identity'])),1)

    def test_external_flow_is_cumulative_not_added_again_on_each_event(self):
        p,c,b,proof=self.seed()
        c['observations']['session_net_external_flow']=-5.
        first=self.cycle(p,c,b)
        second=self.cycle(p,c,first['next_binding'],'event-2')
        self.assertTrue(second['readback_verified'],second['gaps'])
        self.assertEqual(second['preview']['requested_events']['metrics']['daily_loss_ratio'],0.)
        self.assertEqual(self.journal.load_chain(b['identity'])[-1]['state']['session_net_external_flow'],-5.)

    def test_next_session_can_have_a_new_explicit_opening_basis(self):
        p,c,b,proof=self.seed()
        p,c,b=move_observation(p,c,b,next_day=True)
        c['observations']['session_start_nav']=99.
        r=self.cycle(p,c,b)
        self.assertTrue(r['readback_verified'],r['gaps'])
        self.assertEqual(self.journal.load_chain(b['identity'])[-1]['state']['session_start_nav'],99.)

    def test_duplicate_recovers_state_only_and_changed_event_id_content_is_refused(self):
        p,c,b,proof=self.seed();first=self.cycle(p,c,b)
        with patch.object(self.journal,'append',side_effect=AssertionError('must not append')):
            replay=self.cycle(p,c,b)
        self.assertEqual(replay['status'],'INTRADAY_EVENT_ALREADY_PERSISTED')
        self.assertIsNone(replay['draft_settings']);self.assertIsNone(replay['preview'])
        c['observations']['annual_volatility_ratio']=.02
        conflict=self.cycle(p,c,first['next_binding'])
        self.assertIn('INTRADAY_EVENT_ID_CONFLICT',conflict['gaps'])
        self.assertEqual(len(self.journal.load_chain(b['identity'])),2)

    def test_prior_event_replay_cannot_replace_latest_open_checkpoint(self):
        p,c,b,proof=self.seed();first=self.cycle(p,c,b)
        c['observations']['flow_adjusted_nav_index']=80.;add_plan(p,c)
        second=self.cycle(p,c,first['next_binding'],'trip')
        p,c,unused,proof=intraday_fixture()
        r=self.cycle(p,c,first['next_binding'])
        self.assertFalse(r['readback_verified']);self.assertIsNone(r['draft_settings'])
        self.assertTrue(r['new_risk_prohibited'])
        self.assertEqual(self.read(p,second['next_binding'])['breaker_state'],'OPEN')

    def test_freshness_and_current_policy_identity_are_not_inferred(self):
        p,c,b,proof=self.seed()
        for key,value in [('expected_head_sha256',None),('source_version',''),('account_id',None)]:
            changed=dict(b);changed[key]=value
            r=self.cycle(p,c,changed)
            self.assertFalse(r['readback_verified']);self.assertIsNone(r['draft_settings'])
        c['policy']['policy_id']='OTHER'
        self.assertIn('INTRADAY_CURRENT_POLICY_OR_PORTFOLIO_MISMATCH',self.cycle(p,c,b)['gaps'])

    def test_open_state_is_persisted_even_when_reduction_preview_has_gaps(self):
        p,c,b,proof=self.seed()
        c['observations']['flow_adjusted_nav_index']=80.
        r=self.cycle(p,c,b)
        self.assertTrue(r['readback_verified']);self.assertEqual(r['breaker_state'],'OPEN')
        self.assertIn('APPROVED_EVENT_REDUCTION_TARGET_MISSING',r['gaps']);self.assertIsNone(r['draft_settings'])

    def test_missing_numeric_risk_policy_never_creates_a_draft_or_an_event(self):
        p,c,b,proof=intraday_fixture()
        del c['policy']['thresholds_by_level']['balanced']['stop_loss_ratio']
        b['identity']['config_sha256']=digest(c['policy']);proof['identity']=deepcopy(b['identity'])
        initialized=initialize_portfolio_event_state(journal=self.journal,binding=b,
            account_contract=p['account_contract'],initialization=proof)
        self.assertTrue(initialized['readback_verified'],initialized['gaps'])
        r=self.cycle(p,c,initialized['next_binding'])
        self.assertIsNone(r['draft_settings']);self.assertFalse(r['state_persisted'])
        self.assertEqual(len(self.journal.load_chain(b['identity'])),1)

    def test_lost_append_acknowledgement_is_confirmed_without_write_retry(self):
        p,c,b,proof=self.seed();original=self.journal.append
        def lost(record):original(record);raise OSError('artificial lost response')
        with patch.object(self.journal,'append',side_effect=lost) as append:
            r=self.cycle(p,c,b)
        self.assertEqual(append.call_count,1);self.assertTrue(r['readback_verified'],r['gaps'])
        self.assertEqual(r['append_error_type'],'OSError')

    def test_unknown_partial_write_remains_blocked_and_is_not_deleted(self):
        p,c,b,proof=self.seed();original=self.journal.object_store.create_text
        def partial(uri,data,content_type='application/json'):
            path=Path(uri);path.parent.mkdir(parents=True,exist_ok=True);path.write_text('{')
            raise OSError('artificial interrupted create')
        with patch.object(self.journal.object_store,'create_text',side_effect=partial) as create:
            r=self.cycle(p,c,b)
        self.assertEqual(create.call_count,1);self.assertFalse(r['readback_verified'])
        self.assertTrue(r['new_risk_prohibited']);self.assertIsNone(r['draft_settings'])
        self.assertEqual(len(list(self.root.rglob('*.json'))),2)
        self.assertTrue(any(x.read_text()=='{' for x in self.root.rglob('*.json')))
        self.assertIsNotNone(original)

    def test_two_competing_events_never_both_return_a_draft(self):
        p,c,b,proof=self.seed();barrier=Barrier(2);original=self.journal.object_store.create_text
        def race(*args,**kwargs):barrier.wait(timeout=15);return original(*args,**kwargs)
        with patch.object(self.journal.object_store,'create_text',side_effect=race):
            with ThreadPoolExecutor(max_workers=2) as executor:
                jobs=[executor.submit(self.cycle,p,c,b,event_id) for event_id in ('one','two')]
                results=[job.result(timeout=30) for job in jobs]
        self.assertLessEqual(sum(r['draft_settings'] is not None for r in results),1)
        self.assertEqual(len(self.journal.load_chain(b['identity'])),2)

    def test_hash_valid_protection_or_daily_basis_regression_is_refused(self):
        for field,value,expected in [('nav_peak',99.,'STATE_HIGH_WATERMARK_REGRESSION'),
                ('breaker_open',False,'STATE_AUTOMATIC_BREAKER_CLEAR_FORBIDDEN')]:
            temporary=tempfile.TemporaryDirectory(dir=self.root);self.addCleanup(temporary.cleanup)
            self.journal=PortfolioEventJournal(Path(temporary.name)/'journal',offline_root=Path(temporary.name))
            p,c,b,proof=self.seed(opened=True)
            prior=self.journal.load_chain(b['identity'])[-1]
            state=deepcopy(prior['state']);state['risk_state'][field]=value
            if field=='breaker_open':
                state['risk_state'].update(original_daily_trigger=None,trigger_session=None,latched_limits=None)
            forged=build_event_record(identity=b['identity'],kind='EVENT',event_id='forged',
                observed_at='2000-01-04T12:00:00Z',effective_session='2000-01-04',input_sha256='b'*64,
                state=state,previous=prior)
            self.journal.append(forged)
            r=self.read(p,dict(b,expected_head_sha256=forged['record_sha256']))
            self.assertIn(expected,r['gaps']);self.assertTrue(r['new_risk_prohibited'])

    def test_migration_seals_source_and_preserves_open_hwm_and_session_basis(self):
        p,c,b,proof=self.seed(opened=True,held=True)
        first=self.cycle(p,c,b)
        self.assertTrue(first['readback_verified'],first['gaps'])
        b=first['next_binding'];source=self.journal.load_chain(b['identity'])[-1]
        destination,migration=migration_fixture(b,source)
        r=migrate_portfolio_event_state(journal=self.journal,source_binding=b,destination_binding=destination,
            account_contract=p['account_contract'],migration=migration)
        self.assertEqual(r['gaps'],[]);self.assertTrue(r['source_retired'])
        self.assertIsNone(r['draft_settings']);self.assertTrue(r['new_risk_prohibited'])
        target=self.journal.load_chain(destination['identity'])
        self.assertEqual(target[0]['state'],source['state'])
        old=self.read(p,r['source_next_binding'])
        self.assertEqual(old['status'],'INTRADAY_IDENTITY_RETIRED');self.assertTrue(old['new_risk_prohibited'])
        blocked=self.cycle(p,c,r['source_next_binding'],'old-consumer')
        self.assertIn('INTRADAY_SOURCE_IDENTITY_RETIRED',blocked['gaps'])

    def test_incomplete_or_wrong_migration_never_seals_source(self):
        p,c,b,proof=self.seed(opened=True);head=self.journal.load_chain(b['identity'])[-1]
        destination,migration=migration_fixture(b,head)
        for changes in ({'state_sha256':'f'*64},{'account_id':'OTHER'},{'source_head_sha256':'e'*64},
                {'provenance_ref':''},{'synthetic':False},{'destination_identity':b['identity']},
                {'valid_from':'2000-01-04T12:00:30Z'}):
            changed=dict(migration,**changes)
            r=migrate_portfolio_event_state(journal=self.journal,source_binding=b,destination_binding=destination,
                account_contract=p['account_contract'],migration=changed)
            self.assertFalse(r['source_retired']);self.assertTrue(r['new_risk_prohibited'])
        self.assertEqual(len(self.journal.load_chain(b['identity'])),1)
        self.assertEqual(self.journal.load_chain(destination['identity']),[])

    def test_unknown_destination_commit_can_resume_without_resealing_source(self):
        p,c,b,proof=self.seed(opened=True);head=self.journal.load_chain(b['identity'])[-1]
        destination,migration=migration_fixture(b,head)
        original=self.journal.object_store.create_text
        def fail_destination(uri,*args,**kwargs):
            if digest(destination['identity']) in uri:raise OSError('artificial before destination create')
            return original(uri,*args,**kwargs)
        with patch.object(self.journal.object_store,'create_text',side_effect=fail_destination):
            first=migrate_portfolio_event_state(journal=self.journal,source_binding=b,destination_binding=destination,
                account_contract=p['account_contract'],migration=migration)
        self.assertTrue(first['source_retired']);self.assertFalse(first['readback_verified'])
        self.assertTrue(first['new_risk_prohibited'])
        with patch.object(self.journal.object_store,'create_text',wraps=original) as create:
            resumed=migrate_portfolio_event_state(journal=self.journal,source_binding=b,destination_binding=destination,
                account_contract=p['account_contract'],migration=migration)
        self.assertTrue(resumed['readback_verified'],resumed['gaps']);self.assertEqual(create.call_count,1)
        self.assertEqual(len(self.journal.load_chain(b['identity'])),2)

    def test_unconfirmed_source_write_never_reports_definite_unretired_or_proceeds(self):
        p,c,b,proof=self.seed(opened=True)
        destination,migration=migration_fixture(b,self.journal.load_chain(b['identity'])[-1])
        def partial(uri,data,content_type='application/json'):
            path=Path(uri);path.parent.mkdir(parents=True,exist_ok=True);path.write_text('{')
            raise OSError('artificial source write interruption')
        with patch.object(self.journal.object_store,'create_text',side_effect=partial) as create:
            r=migrate_portfolio_event_state(journal=self.journal,source_binding=b,destination_binding=destination,
                account_contract=p['account_contract'],migration=migration)
        self.assertEqual(create.call_count,1);self.assertIsNone(r['source_retired'])
        self.assertEqual(r['source_write_outcome'],'UNCONFIRMED');self.assertTrue(r['new_risk_prohibited'])
        self.assertFalse(r['readback_verified']);self.assertEqual(self.journal.load_chain(destination['identity']),[])

    def test_destination_readback_checks_actual_source_seal_not_just_root_hash(self):
        p,c,b,proof=self.seed(opened=True);head=self.journal.load_chain(b['identity'])[-1]
        destination,migration=migration_fixture(b,head)
        # A valid record with made-up linkage cannot manufacture a migration.
        forged=build_event_record(identity=destination['identity'],kind='MIGRATE_IN',event_id='forged-import',
            observed_at='2000-01-04T12:00:00Z',effective_session='2000-01-04',input_sha256='c'*64,
            state=head['state'],provenance={'migration':migration,'source_terminal_sha256':'f'*64})
        self.journal.append(forged)
        r=self.read(p,dict(destination,expected_head_sha256=forged['record_sha256']))
        self.assertIn('INTRADAY_SOURCE_NOT_SEALED',r['gaps']);self.assertTrue(r['new_risk_prohibited'])

    def test_new_policy_event_and_second_handoff_keep_original_protections(self):
        p,c,b,proof=self.seed(opened=True,held=True)
        first=self.cycle(p,c,b);b=first['next_binding']
        source=self.journal.load_chain(b['identity'])[-1]
        destination,migration=migration_fixture(b,source)
        c2=deepcopy(c);c2['policy']['policy_id']='ARTIFICIAL_POLICY_V2'
        for row in c2['policy']['thresholds_by_level'].values():row['stop_loss_ratio']=.10
        destination['identity']['config_sha256']=digest(c2['policy'])
        migration['destination_identity']=deepcopy(destination['identity'])
        moved=migrate_portfolio_event_state(journal=self.journal,source_binding=b,destination_binding=destination,
            account_contract=p['account_contract'],migration=migration)
        self.assertTrue(moved['readback_verified'],moved['gaps'])
        add_plan(p,c2)
        event=self.cycle(p,c2,moved['next_binding'],'after-policy-change')
        self.assertTrue(event['readback_verified'],event['gaps'])
        self.assertEqual(event['breaker_state'],'OPEN')
        state=self.journal.load_chain(destination['identity'])[-1]['state']['risk_state']
        self.assertEqual(state['original_daily_trigger'],source['state']['risk_state']['original_daily_trigger'])
        self.assertEqual(state['sleeves']['cycle-A']['stop_limit'],.05)
        next_binding,proof2=migration_fixture(event['next_binding'],self.journal.load_chain(destination['identity'])[-1])
        proof2['provenance_ref']='ARTIFICIAL_NEXT_HANDOFF'
        next_binding['identity']['config_sha256']='e'*64
        proof2['destination_identity']=deepcopy(next_binding['identity'])
        second=migrate_portfolio_event_state(journal=self.journal,source_binding=event['next_binding'],
            destination_binding=next_binding,account_contract=p['account_contract'],migration=proof2)
        self.assertTrue(second['readback_verified'],second['gaps'])
        self.assertEqual(self.journal.load_chain(next_binding['identity'])[0]['state']['risk_state'],state)

    def test_independent_process_intraday_restart_crashes_and_handoff_resume(self):
        repo=Path(__file__).resolve().parents[2]
        probe=repo/'tests/research/fixtures/portfolio_intraday_probe.py'
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
        env['PYTHONPATH']=os.pathsep.join((str(repo/'src'),str(repo/'tests/research'),env.get('PYTHONPATH','')))
        phases=('seed','trip','reload','continue','crash_before','verify_before','crash_after','verify_after',
            'partial','verify_partial','migrate_crash','verify_migrate')
        reports={}
        for phase in phases:
            result=subprocess.run([sys.executable,str(probe),phase,str(self.root)],
                cwd=repo,env=env,capture_output=True,text=True,timeout=30)
            expected={'crash_before':73,'crash_after':74,'partial':75,'migrate_crash':76}.get(phase,0)
            self.assertEqual(result.returncode,expected,(phase,result.stdout,result.stderr))
            report=json.loads(result.stdout.strip().splitlines()[-1])
            self.assertEqual(report['forbidden_effect_attempts'],[])
            for key in ('execution_authorized','settings_applied','breaker_reset'):self.assertFalse(report[key])
            reports[phase]=report
        self.assertEqual(len({r['pid'] for r in reports.values()}),len(phases))
        self.assertEqual(reports['seed']['breaker_state'],'CLOSED')
        for phase in ('trip','reload','continue','verify_before','verify_after','verify_migrate'):
            self.assertEqual(reports[phase]['breaker_state'],'OPEN')
        self.assertEqual(reports['trip']['chain_length'],3)
        self.assertEqual(reports['continue']['chain_length'],4)
        self.assertEqual(reports['reload']['state_append_calls'],0)
        self.assertEqual(reports['verify_after']['state_append_calls'],0)
        self.assertTrue(reports['verify_partial']['new_risk_prohibited'])
        self.assertEqual(reports['verify_migrate']['create_calls'],1)
        self.assertTrue(reports['verify_migrate']['source_retired'])

    def test_legacy_daily_import_is_read_only_preparation_with_explicit_gap(self):
        p,c=event_fixture();set_prior(c,opened=True)
        binding=deepcopy(c['state_readback']);wire=binding.pop('chain')[0];binding['schema_version']='qsl.portfolio-event-state-binding.v1'
        from quant_platform_kit.common.strategy_risk_state import StrategyRiskStateTransition
        store=StrategyRiskStateStore(local_dir=self.root/'daily')
        store.append(StrategyRiskStateTransition.from_dict(wire))
        with patch.object(StrategyRiskStateStore,'append',side_effect=AssertionError('must not mutate daily store')):
            r=prepare_daily_state_import(store=store,binding=binding,account_contract=p['account_contract'],offline_root=self.root)
        self.assertEqual(r['status'],'LEGACY_IMPORT_PREPARATION_ONLY')
        self.assertTrue(r['legacy_candidate']['state']['breaker_open'])
        self.assertIn('INTRADAY_LEGACY_WRITER_RETIREMENT_UNPROVEN',r['gaps'])
        self.assertTrue(r['new_risk_prohibited']);self.assertIsNone(r['draft_settings'])


if __name__ == '__main__':unittest.main()
