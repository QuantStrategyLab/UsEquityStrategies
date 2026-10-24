"""Artificial local durable IO and restart seams, never production storage."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
from threading import Barrier
import unittest
from unittest.mock import patch

from test_portfolio_risk_events_preview import event_fixture,add_plan,set_prior,add_recovery
from test_portfolio_settings_preview import request_fixture
from us_equity_strategies.research.auto_portfolio_account import digest
from us_equity_strategies.research.portfolio_risk_state_io import (
    BINDING_VERSION,persist_portfolio_risk_preview,read_portfolio_risk_state,
)
from quant_platform_kit.common.strategy_risk_state import (
    StrategyRiskStateIdentity,StrategyRiskStateStore,build_strategy_risk_state_transition,
)


def io_fixture(held=False,opened=None):
    p,c=event_fixture(held)
    # A frozen artificial policy remains valid across the following two fixture
    # sessions. This freshness setting is not an account risk threshold.
    c['policy']['max_age_seconds']=200000
    c['state_readback']['identity']['config_sha256']=digest(c['policy'])
    prior=set_prior(c,opened=opened) if opened is not None else None
    binding=deepcopy(c.pop('state_readback'));binding.pop('chain')
    binding['schema_version']=BINDING_VERSION
    if opened is True:add_plan(p,c)
    return p,c,binding,prior


def advance_fixture(packet,context,binding):
    def move(value):
        if isinstance(value,dict):return {k:move(v) for k,v in value.items()}
        if isinstance(value,list):return [move(v) for v in value]
        if isinstance(value,str):return value.replace('2000-01-04','2000-01-05')
        return value
    p=move(deepcopy(packet));c=move(deepcopy(context));b=move(deepcopy(binding))
    c['policy']=deepcopy(context['policy'])
    c['observations']['account_snapshot_sha256']=digest(p['facts'])
    c['observations']['fee_evidence_sha256']=digest(p['execution_rules']['fees'])
    if c.get('reduction_plan'):
        c['reduction_plan']['policy_sha256']=digest(c['policy'])
        c['reduction_plan']['account_snapshot_sha256']=digest(p['facts'])
    c.pop('recovery',None)
    return p,c,b


class StateIOTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='ues-artificial-state-')
        self.addCleanup(temporary.cleanup)
        self.base=Path(temporary.name)
        self.root=self.base/'offline'
        self.root.mkdir()
        self.store=StrategyRiskStateStore(local_dir=self.root/'state')

    def seed(self,held=False,opened=None):
        p,c,b,prior=io_fixture(held,opened)
        if prior:self.store.append(prior)
        return p,c,b

    def run_cycle(self,p,c,b,req=None,store=None):
        return persist_portfolio_risk_preview(req or request_fixture(),store=store or self.store,binding=b,
            event_context=c,offline_root=self.root,**p)

    def read(self,p,b,store=None):
        return read_portfolio_risk_state(store=store or self.store,binding=b,
            account_contract=p['account_contract'],offline_root=self.root)

    def test_empty_verified_genesis_is_uninitialized_and_not_risk_permission(self):
        p,c,b=self.seed();r=self.read(p,b)
        self.assertEqual(r['status'],'STATE_STORE_EMPTY_VERIFIED_GENESIS')
        self.assertFalse(r['readback_verified']);self.assertTrue(r['new_risk_prohibited'])
        self.assertEqual(r['breaker_state'],'UNKNOWN')

    def test_missing_expected_chain_never_becomes_closed(self):
        p,c,b=self.seed();b.update(origin='VERIFIED_CHAIN',expected_head_sha256='b'*64)
        r=self.read(p,b)
        self.assertIn('STATE_EXPECTED_CHAIN_MISSING',r['gaps']);self.assertTrue(r['new_risk_prohibited'])

    def test_explicit_checkpoint_origin_and_hash_are_required(self):
        for key in ('origin','expected_head_sha256','schema_version'):
            p,c,b=self.seed();del b[key]
            self.assertNotEqual(self.read(p,b)['gaps'],[])

    def test_read_binding_source_scope_version_and_time_are_checked(self):
        for key,value in [('account_id','OTHER'),('source_version',''),('available_at','2000-01-04T13:00:00Z'),
                ('synthetic',False),('max_age_seconds',0)]:
            p,c,b=self.seed();b[key]=value
            self.assertNotEqual(self.read(p,b)['gaps'],[])

    def test_inline_state_cannot_override_durable_readback(self):
        p,c,b=self.seed();c['state_readback']={}
        r=self.run_cycle(p,c,b)
        self.assertIn('STATE_INLINE_READBACK_FORBIDDEN',r['gaps'])
        self.assertEqual(list(self.root.rglob('*.json')),[])

    def test_binding_cannot_contain_an_inline_chain(self):
        p,c,b=self.seed();b['chain']=[]
        self.assertIn('STATE_BINDING_INVALID',self.read(p,b)['gaps'])

    def test_cloud_missing_backend_and_custom_namespace_are_refused(self):
        p,c,b=self.seed()
        for store in (StrategyRiskStateStore(),StrategyRiskStateStore(cloud_prefix_uri='gs://artificial-only'),
                StrategyRiskStateStore(local_dir=self.root/'state',object_store=object()),
                StrategyRiskStateStore(local_dir=self.root/'state',namespace='../commands')):
            self.assertIn('STATE_LOCAL_OFFLINE_STORE_REQUIRED',self.read(p,b,store)['gaps'])

    def test_store_path_must_stay_inside_independent_offline_root(self):
        p,c,b=self.seed()
        for location in (self.root,self.root.parent/'other'):
            self.assertIn('STATE_STORE_OUTSIDE_OFFLINE_ROOT',self.read(p,b,StrategyRiskStateStore(local_dir=location))['gaps'])

    def test_symlink_escape_is_refused_before_any_store_read(self):
        p,c,b=self.seed();outside=self.base/'outside-fixture';outside.mkdir(exist_ok=True)
        local=Path(self.store.local_dir);local.mkdir()
        (local/'strategy_risk_states').symlink_to(outside,target_is_directory=True)
        with patch.object(StrategyRiskStateStore,'load_chain',side_effect=AssertionError('must not read')):
            self.assertIn('STATE_STORE_SYMLINK_ESCAPE',self.read(p,b)['gaps'])

    def test_successor_file_symlink_escape_is_refused_before_store_read(self):
        p,c,b=self.seed()
        first=self.run_cycle(p,c,b)
        p,c,b=advance_fixture(p,c,first['next_binding'])
        second=self.run_cycle(p,c,b)
        self.assertTrue(second['readback_verified'],second['gaps'])
        successor=next(self.root.rglob('successors/*.json'))
        outside=self.base/'outside-transition.json'
        outside.write_bytes(successor.read_bytes())
        successor.unlink();successor.symlink_to(outside)
        with patch.object(StrategyRiskStateStore,'load_chain',side_effect=AssertionError('must not read')):
            r=self.read(p,second['next_binding'])
        self.assertIn('STATE_STORE_SYMLINK_ESCAPE',r['gaps'])
        self.assertFalse(r['readback_verified']);self.assertTrue(r['new_risk_prohibited'])

    def test_first_valid_cycle_appends_and_reads_back_original_store(self):
        p,c,b=self.seed();r=self.run_cycle(p,c,b)
        self.assertEqual(r['status'],'STATE_PERSISTED_PREVIEW_READY',r['gaps'])
        self.assertTrue(r['state_persisted']);self.assertTrue(r['readback_verified'])
        self.assertEqual(r['append_receipt']['status'],'created')
        self.assertEqual(len(list(self.root.rglob('root.json'))),1)
        self.assertEqual(r['state_readback']['breaker_state'],'CLOSED')

    def test_drawdown_trip_is_persisted_open(self):
        p,c,b=self.seed(True);c['observations']['flow_adjusted_nav_index']=80.;add_plan(p,c)
        r=self.run_cycle(p,c,b)
        self.assertTrue(r['state_persisted']);self.assertEqual(r['state_readback']['breaker_state'],'OPEN')
        self.assertFalse(r['breaker_reset']);self.assertTrue(r['new_risk_prohibited'])

    def test_open_is_persisted_even_without_exit_plan(self):
        p,c,b=self.seed(True);c['observations']['flow_adjusted_nav_index']=80.
        r=self.run_cycle(p,c,b)
        self.assertEqual(r['status'],'STATE_PERSISTED_PREVIEW_HAS_GAPS')
        self.assertTrue(r['state_persisted']);self.assertIsNone(r['draft_settings'])
        self.assertIn('APPROVED_EVENT_REDUCTION_TARGET_MISSING',r['gaps'])

    def test_unknown_pending_persists_open_without_a_configuration_draft(self):
        p,c,b=self.seed();p['cash_evidence']['pending_state']='UNKNOWN'
        r=self.run_cycle(p,c,b)
        self.assertTrue(r['state_persisted']);self.assertEqual(r['state_readback']['breaker_state'],'OPEN')
        self.assertIsNone(r['draft_settings'])

    def test_quantity_failure_does_not_discard_observed_open_state(self):
        p,c,b=self.seed(True,True);p['execution_rules']['quantity']['step_by_symbol']['A']='3'
        r=self.run_cycle(p,c,b)
        self.assertTrue(r['state_persisted']);self.assertIsNone(r['draft_settings'])
        self.assertTrue(r['new_risk_prohibited'])

    def test_missing_event_policy_never_creates_a_policy_or_state(self):
        p,c,b=self.seed();del c['policy']
        r=self.run_cycle(p,c,b)
        self.assertFalse(r['state_persisted']);self.assertIn('STATE_NO_VALID_EVENT_TRANSITION',r['gaps'])
        self.assertEqual(list(self.root.rglob('*.json')),[])

    def test_invalid_execution_request_does_not_write(self):
        p,c,b=self.seed();req=request_fixture();req['execution_authorized']=True
        r=self.run_cycle(p,c,b,req)
        self.assertFalse(r['state_persisted']);self.assertEqual(list(self.root.rglob('*.json')),[])

    def test_missing_original_tail_policy_keeps_financial_gap_after_state_save(self):
        p,c,b=self.seed();p['risk_inputs']['account_policy']=None
        r=self.run_cycle(p,c,b)
        self.assertTrue(r['state_persisted']);self.assertIsNone(r['draft_settings'])
        self.assertFalse(r['account_policy_bound']);self.assertNotEqual(r['gaps'],[])

    def test_identical_delivery_with_old_genesis_checkpoint_is_idempotent(self):
        p,c,b=self.seed();first=self.run_cycle(p,c,b)
        with patch.object(StrategyRiskStateStore,'append',side_effect=AssertionError('must not write twice')):
            replay=self.run_cycle(p,c,b)
        self.assertEqual(replay['status'],'STATE_ALREADY_PERSISTED_READ_BACK')
        self.assertEqual(replay['next_binding'],first['next_binding'])
        self.assertIsNone(replay['draft_settings']);self.assertIsNone(replay['preview'])

    def test_identical_delivery_with_confirmed_checkpoint_recovers_state_only(self):
        p,c,b=self.seed();first=self.run_cycle(p,c,b)
        result=self.run_cycle(p,c,first['next_binding'])
        self.assertEqual(result['status'],'STATE_ALREADY_PERSISTED_READ_BACK')
        self.assertTrue(result['state_recovered']);self.assertIsNone(result['draft_settings'])

    def test_same_session_changed_request_is_refused_without_rewriting_state(self):
        p,c,b=self.seed();first=self.run_cycle(p,c,b)
        r=self.run_cycle(p,c,first['next_binding'],request_fixture('advanced',kelly_fraction=.1))
        self.assertIn('STATE_SAME_SESSION_INPUT_CHANGED_OR_STALE',r['gaps'])
        self.assertIsNone(r['draft_settings'])
        self.assertEqual(self.read(p,first['next_binding'])['observed_head_sha256'],first['next_binding']['expected_head_sha256'])

    def test_same_session_changed_packet_is_not_a_duplicate(self):
        p,c,b=self.seed();first=self.run_cycle(p,c,b);p['moments']['source']='ARTIFICIAL_CHANGED_SOURCE'
        r=self.run_cycle(p,c,first['next_binding'])
        self.assertIn('STATE_SAME_SESSION_INPUT_CHANGED_OR_STALE',r['gaps'])

    def test_changed_policy_or_portfolio_never_starts_a_new_closed_chain(self):
        for change in ('policy','portfolio'):
            p,c,b=self.seed()
            if change=='policy':c['policy']['policy_id']='ARTIFICIAL_OTHER_POLICY'
            else:p['composition']['weights']['STRATEGY_A']={'B':1.}
            r=self.run_cycle(p,c,b)
            self.assertIn('STATE_CURRENT_POLICY_OR_PORTFOLIO_MISMATCH',r['gaps'])
            self.assertEqual(list(self.root.rglob('*.json')),[])

    def test_fresh_store_object_restores_open_and_next_session_uses_it(self):
        p,c,b=self.seed(True,True);first=self.run_cycle(p,c,b)
        p,c,b=advance_fixture(p,c,first['next_binding'])
        fresh=StrategyRiskStateStore(local_dir=self.root/'state')
        r=self.run_cycle(p,c,b,store=fresh)
        self.assertTrue(r['state_recovered']);self.assertTrue(r['state_persisted'])
        self.assertEqual(r['state_readback']['breaker_state'],'OPEN')
        self.assertEqual(len(r['state_readback']['chain']),3)

    def test_readback_does_not_need_new_risk_thresholds_to_keep_open(self):
        p,c,b=self.seed(True,True);r=self.read(p,b)
        self.assertEqual(r['breaker_state'],'OPEN');self.assertTrue(r['new_risk_prohibited'])

    def test_qualified_recovery_is_persisted_as_open(self):
        p,c,b=self.seed(False,True)
        # Existing artificial recovery helper expects the original readback head.
        c['state_readback']={'expected_head_sha256':b['expected_head_sha256']};add_recovery(c);c.pop('state_readback')
        r=self.run_cycle(p,c,b)
        self.assertEqual(r['preview']['requested_events']['recovery']['status'],'QUALIFIED_FOR_RESET_REVIEW')
        self.assertEqual(r['state_readback']['breaker_state'],'OPEN');self.assertFalse(r['breaker_reset'])

    def test_restart_preserves_sleeve_peaks_and_stricter_stops(self):
        p,c,b=self.seed(True);first=self.run_cycle(p,c,b,request_fixture('advanced',stop_loss_ratio=.02))
        p,c,b=advance_fixture(p,c,first['next_binding']);r=self.run_cycle(p,c,b)
        sleeve=r['state_readback']['chain'][-1]['state']['sleeves']['cycle-A']
        self.assertEqual(sleeve['stop_limit'],.02);self.assertEqual(sleeve['peak'],19.98)

    def test_truncated_root_and_tampered_state_are_fail_closed(self):
        for data in ('{',json.dumps({'state':{'breaker_open':False}})):
            p,c,b=self.seed();first=self.run_cycle(p,c,b)
            identity=StrategyRiskStateIdentity.from_dict(b['identity'])
            root=Path(self.store._root_location(identity));root.write_text(data)
            r=self.read(p,first['next_binding'])
            self.assertNotEqual(r['gaps'],[]);self.assertTrue(r['new_risk_prohibited'])
            # Reset only our test case directory, using a new subdirectory.
            self.store=StrategyRiskStateStore(local_dir=self.root/('state-'+str(len(data))))

    def test_missing_root_is_not_healed_by_successor_files(self):
        p,c,b=self.seed(False,True);r=self.run_cycle(p,c,b)
        next(self.root.rglob('root.json')).unlink()
        read=self.read(p,r['next_binding'])
        self.assertIn('STATE_EXPECTED_CHAIN_MISSING',read['gaps']);self.assertTrue(read['new_risk_prohibited'])

    def test_orphan_committed_successor_cannot_be_relabelled_as_genesis(self):
        p,c,b=self.seed(False,True);self.run_cycle(p,c,b)
        next(self.root.rglob('root.json')).unlink()
        b.update(origin='VERIFIED_CLOSED_GENESIS',expected_head_sha256=None)
        read=self.read(p,b)
        self.assertIn('STATE_ORPHAN_SUCCESSORS_REQUIRE_RECOVERY',read['gaps'])
        self.assertIsNone(read['state_readback']);self.assertTrue(read['new_risk_prohibited'])

    def test_missing_tail_is_detected_by_last_known_checkpoint(self):
        p,c,b=self.seed(False,True);r=self.run_cycle(p,c,b)
        next(self.root.rglob('successors/*.json')).unlink()
        read=self.read(p,r['next_binding'])
        self.assertIn('STATE_HEAD_CHECKPOINT_MISMATCH',read['gaps']);self.assertTrue(read['new_risk_prohibited'])

    def test_orphan_temporary_file_does_not_become_a_committed_state(self):
        p,c,b=self.seed();identity=StrategyRiskStateIdentity.from_dict(b['identity'])
        directory=Path(self.store._location(identity));directory.mkdir(parents=True)
        (directory/'.root.json.artificial.tmp').write_text('incomplete artificial write')
        self.assertEqual(self.read(p,b)['status'],'STATE_STORE_EMPTY_VERIFIED_GENESIS')
        r=self.run_cycle(p,c,b);self.assertTrue(r['state_persisted'])

    def test_append_failure_is_not_automatically_retried(self):
        p,c,b=self.seed()
        with patch.object(StrategyRiskStateStore,'append',side_effect=PermissionError('ARTIFICIAL_PRIVATE_DETAIL')) as append:
            r=self.run_cycle(p,c,b)
        append.assert_called_once();self.assertFalse(r['readback_verified']);self.assertIsNone(r['draft_settings'])
        self.assertEqual(r['write_outcome'],'UNCONFIRMED')
        self.assertNotIn('ARTIFICIAL_PRIVATE_DETAIL',json.dumps(r))

    def test_committed_write_with_lost_response_is_reconciled_read_only(self):
        p,c,b=self.seed();original=StrategyRiskStateStore.append;calls=[]
        def lost(store,transition):
            calls.append(transition);original(store,transition);raise OSError('ARTIFICIAL_LOST_ACK')
        with patch.object(StrategyRiskStateStore,'append',lost):r=self.run_cycle(p,c,b)
        self.assertEqual(len(calls),1);self.assertTrue(r['state_persisted']);self.assertTrue(r['readback_verified'])
        self.assertEqual(r['write_outcome'],'COMMITTED_READ_BACK_AFTER_APPEND_ERROR')

    def test_unreadable_post_write_never_returns_pre_save_configuration(self):
        p,c,b=self.seed();original=StrategyRiskStateStore.append;committed=[]
        def append(store,transition):
            result=original(store,transition);committed.append(transition);return result
        original_load=StrategyRiskStateStore.load_chain
        def load(store,identity):
            if committed:raise OSError('ARTIFICIAL_READ_UNAVAILABLE')
            return original_load(store,identity)
        with patch.object(StrategyRiskStateStore,'append',append),patch.object(StrategyRiskStateStore,'load_chain',load):
            r=self.run_cycle(p,c,b)
        self.assertEqual(r['write_outcome'],'UNCONFIRMED');self.assertIsNone(r['draft_settings']);self.assertTrue(r['new_risk_prohibited'])
        fresh=self.run_cycle(p,c,b)
        self.assertEqual(fresh['status'],'STATE_ALREADY_PERSISTED_READ_BACK')

    def test_competing_writers_share_original_create_only_successor(self):
        p,c,b=self.seed(False,False);barrier=Barrier(2);original=StrategyRiskStateStore._create_text
        def create(store,location,data):
            barrier.wait(timeout=10);return original(store,location,data)
        with patch.object(StrategyRiskStateStore,'_create_text',create),ThreadPoolExecutor(max_workers=2) as executor:
            jobs=[executor.submit(self.run_cycle,p,c,b,request_fixture(mode)) for mode in ('simple','advanced')]
            results=[job.result(timeout=20) for job in jobs]
        self.assertEqual(sum(r['readback_verified'] for r in results),1)
        self.assertEqual(sum(r['draft_settings'] is not None for r in results),1)
        identity=StrategyRiskStateIdentity.from_dict(b['identity']);self.assertEqual(len(self.store.load_chain(identity)),2)

    def test_hash_valid_automatic_open_to_closed_transition_is_rejected(self):
        p,c,b=self.seed(False,True);identity=StrategyRiskStateIdentity.from_dict(b['identity']);prior=self.store.load_chain(identity)[-1]
        state=deepcopy(prior.state);state.update(breaker_open=False,original_daily_trigger=None,trigger_session=None,latched_limits=None)
        child=build_strategy_risk_state_transition(identity=identity,effective_session='1999-12-22',input_sha256='b'*64,state=state,previous_transition=prior)
        self.store.append(child);b['expected_head_sha256']=child.transition_sha256
        r=self.read(p,b);self.assertIn('STATE_AUTOMATIC_BREAKER_CLEAR_FORBIDDEN',r['gaps'])
        self.assertTrue(r['new_risk_prohibited'])

    def test_hash_valid_peak_regression_is_rejected(self):
        p,c,b=self.seed(False,False);identity=StrategyRiskStateIdentity.from_dict(b['identity']);prior=self.store.load_chain(identity)[-1]
        state=deepcopy(prior.state);state['nav_peak']=99.
        child=build_strategy_risk_state_transition(identity=identity,effective_session='1999-12-22',input_sha256='b'*64,state=state,previous_transition=prior)
        self.store.append(child);b['expected_head_sha256']=child.transition_sha256
        self.assertIn('STATE_HIGH_WATERMARK_REGRESSION',self.read(p,b)['gaps'])

    def test_future_state_cannot_be_used_as_a_current_closed_observation(self):
        p,c,b=self.seed(False,False);identity=StrategyRiskStateIdentity.from_dict(b['identity'])
        prior=self.store.load_chain(identity)[-1]
        child=build_strategy_risk_state_transition(identity=identity,effective_session='2000-02-01',
            input_sha256='b'*64,state=prior.state,previous_transition=prior)
        self.store.append(child);b['expected_head_sha256']=child.transition_sha256
        read=self.read(p,b)
        self.assertIn('STATE_FUTURE_EFFECTIVE_SESSION',read['gaps']);self.assertTrue(read['new_risk_prohibited'])

    def test_independent_process_restart_and_crash_reconciliation(self):
        repo=Path(__file__).resolve().parents[2]
        probe=repo/'tests/research/fixtures/portfolio_restart_probe.py'
        (self.root/'restart-evidence').mkdir()
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        env['PYTHONPATH']=os.pathsep.join((str(repo/'src'),str(repo/'tests/research'),env.get('PYTHONPATH','')))
        reports={}
        for phase in ('seed','reload','continue','crash_before','verify_before','crash_after','verify_after'):
            result=subprocess.run([sys.executable,str(probe),phase,str(self.root)],
                cwd=repo,env=env,capture_output=True,text=True,timeout=30)
            expected={'crash_before':73,'crash_after':74}.get(phase,0)
            self.assertEqual(result.returncode,expected,(phase,result.stdout,result.stderr))
            report=json.loads(result.stdout.strip().splitlines()[-1])
            self.assertEqual(report['forbidden_effect_attempts'],[])
            self.assertEqual(report['breaker_state'],'OPEN')
            for key in ('execution_authorized','settings_applied','breaker_reset'):
                self.assertFalse(report[key])
            reports[phase]=report
        self.assertEqual(len({r['pid'] for r in reports.values()}),7)
        self.assertEqual(reports['seed']['head'],reports['reload']['head'])
        self.assertNotEqual(reports['seed']['head'],reports['continue']['head'])
        self.assertEqual(reports['continue']['chain_length'],2)
        self.assertEqual(reports['verify_before']['chain_length'],1)
        self.assertEqual(reports['verify_after']['chain_length'],2)
        for phase in ('reload','verify_before','verify_after'):
            self.assertEqual(reports[phase]['state_append_calls'],0)

    def test_original_store_and_kernel_objects_remain_the_storage_path(self):
        p,c,b=self.seed();original=StrategyRiskStateStore.append
        with patch.object(StrategyRiskStateStore,'append',autospec=True,side_effect=original) as append:
            r=self.run_cycle(p,c,b)
        append.assert_called_once();self.assertTrue(r['readback_verified'])
        names=[x.name for x in self.root.rglob('*') if x.is_file()]
        self.assertEqual(names,['root.json']);self.assertFalse(any('latest' in x for x in names))


if __name__=='__main__':unittest.main()
