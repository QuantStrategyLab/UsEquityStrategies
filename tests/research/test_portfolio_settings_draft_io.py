"""Artificial immutable drafts and offline consumer checks, never live apply."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_portfolio_intraday_state_io import intraday_fixture, move_observation, migration_fixture
from test_portfolio_risk_events_preview import add_plan
from test_portfolio_settings_preview import request_fixture
from us_equity_strategies.research.auto_portfolio_account import digest
from us_equity_strategies.research.portfolio_event_journal import PortfolioEventJournal
from us_equity_strategies.research.portfolio_intraday_state_io import (
    initialize_portfolio_event_state, migrate_portfolio_event_state, read_portfolio_event_state,
)
from us_equity_strategies.research.portfolio_settings_draft_io import (
    NAMESPACE, persist_portfolio_settings_draft, read_portfolio_settings_draft,
    preflight_portfolio_settings_draft,
)


class SettingsDraftTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='ues-artificial-settings-draft-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.journal = PortfolioEventJournal(self.root/'journal',offline_root=self.root)

    def seed(self, opened=False, held=False, packet=None, context=None):
        p,c,b,proof = intraday_fixture(opened,held)
        if packet is not None: p = packet
        if context is not None:
            c = context;b['identity']['config_sha256'] = digest(c['policy'])
            proof['identity'] = deepcopy(b['identity'])
        init = initialize_portfolio_event_state(journal=self.journal,binding=b,
            account_contract=p['account_contract'],initialization=proof)
        self.assertTrue(init['readback_verified'],init['gaps'])
        return p,c,init['next_binding']

    def cycle(self,p,c,b,request=None,event_id='allocation-1'):
        return persist_portfolio_settings_draft(request or request_fixture(),journal=self.journal,
            binding=b,event_id=event_id,event_context=c,**p)

    def read(self,p,c,saved,request=None,**kwargs):
        return read_portfolio_settings_draft(request or request_fixture(),journal=self.journal,
            binding=kwargs.pop('binding',saved['next_binding']),
            expected_artifact_sha256=kwargs.pop('expected_artifact_sha256',saved['artifact_receipt']['artifact_sha256']),
            consumption_cutoff_utc=kwargs.pop('consumption_cutoff_utc','2000-01-04T12:01:30Z'),
            event_context=c,**p,**kwargs)

    def artifact_path(self):
        paths = list((self.journal.local_dir/NAMESPACE).rglob('*.json'))
        self.assertEqual(len(paths),1)
        return paths[0]

    def snapshot(self):
        return {str(p):sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*.json')}

    def assert_unarmed(self,value):
        for key in ('execution_authorized','settings_applied','breaker_reset','account_policy_bound','financial_risk_certified'):
            self.assertIs(value[key],False)
        self.assertEqual(value['application_status'],'NOT_APPLIED')

    def test_smart_draft_round_trip_uses_full_receipt_without_recomputing_or_writing(self):
        p,c,b = self.seed();saved = self.cycle(p,c,b)
        self.assertEqual(saved['gaps'],[]);self.assertEqual(saved['status'],'SETTINGS_DRAFT_PERSISTED')
        self.assertTrue(saved['state_persisted']);self.assertTrue(saved['artifact_readback_verified'])
        before = self.snapshot()
        with patch.object(self.journal,'append',side_effect=AssertionError('read must not append')), \
                patch.object(self.journal.object_store,'create_text',side_effect=AssertionError('read must not create')), \
                patch('us_equity_strategies.research.portfolio_settings_draft_io.persist_portfolio_intraday_preview',
                    side_effect=AssertionError('read must not recompute')):
            read = self.read(p,c,saved)
        self.assertEqual(read['gaps'],[]);self.assertEqual(before,self.snapshot())
        self.assertEqual(read['draft_settings'],saved['draft_settings'])
        self.assertEqual(read['frozen_cutoff_utc'],p['account_contract']['cutoff_utc'])
        self.assertEqual(read['consumption_cutoff_utc'],'2000-01-04T12:01:30Z')
        self.assertEqual(read['consumer_action'],'ALLOCATION_PREVIEW_ONLY');self.assert_unarmed(read)

    def test_advanced_confirmation_remains_required_without_server_or_effective_values(self):
        p,c,b = self.seed();req = request_fixture('advanced',stop_loss_ratio=.08,resume_observation_sessions=6)
        saved = self.cycle(p,c,b,req);self.assertEqual(saved['gaps'],[])
        out = preflight_portfolio_settings_draft(req,journal=self.journal,binding=saved['next_binding'],
            expected_artifact_sha256=saved['artifact_receipt']['artifact_sha256'],
            consumption_cutoff_utc='2000-01-04T12:01:30Z',event_context=c,**p)
        self.assertEqual(out['status'],'READY_OFFLINE_SETTINGS_PREFLIGHT');self.assert_unarmed(out)
        self.assertEqual(out['confirmation_state'],'REQUIRED');self.assertFalse(out['confirmation_granted'])
        p4 = out['preview']['p4_preview']
        for key in ('effective_parameters_sha256','effective_observed_at_utc'):self.assertIsNone(p4[key])
        for key in ('server_confirmation_ref','confirmed_at_utc','expires_at_utc'):
            self.assertIsNone(p4['confirmation_binding'][key])

    def test_open_draft_only_previews_qualified_reductions_and_never_unlatches(self):
        p,c,b = self.seed(opened=True,held=True);saved = self.cycle(p,c,b)
        self.assertEqual(saved['gaps'],[]);read = self.read(p,c,saved)
        self.assertEqual(read['gaps'],[]);self.assertEqual(read['breaker_state'],'OPEN')
        self.assertTrue(read['new_risk_prohibited']);self.assertEqual(read['consumer_action'],'REDUCTION_PREVIEW_ONLY')
        self.assertEqual(read['draft_settings']['security_target_weights'],{'A':0.,'B':0.})
        self.assert_unarmed(read)

    def test_replay_returns_confirmed_state_only_and_never_reissues_or_rewrites_draft(self):
        p,c,b = self.seed();saved = self.cycle(p,c,b);before = self.snapshot()
        with patch.object(self.journal.object_store,'create_text',side_effect=AssertionError('no replay write')):
            replay = self.cycle(p,c,b)
        self.assertEqual(replay['intraday_status'],'INTRADAY_EVENT_ALREADY_PERSISTED')
        self.assertTrue(replay['readback_verified']);self.assertIsNone(replay['draft_settings'])
        self.assertIsNone(replay['artifact_receipt']);self.assertEqual(before,self.snapshot())
        self.assertEqual(replay['next_binding'],saved['next_binding'])

    def test_open_event_with_missing_reduction_plan_keeps_state_without_artifact(self):
        p,c,b = self.seed();c['observations']['flow_adjusted_nav_index'] = 80.
        saved = self.cycle(p,c,b)
        self.assertTrue(saved['state_persisted']);self.assertEqual(saved['breaker_state'],'OPEN')
        self.assertIn('APPROVED_EVENT_REDUCTION_TARGET_MISSING',saved['gaps'])
        self.assertIsNone(saved['artifact_receipt']);self.assertIsNone(saved['draft_settings'])
        self.assertFalse((self.journal.local_dir/NAMESPACE).exists())

    def test_old_closed_draft_cannot_survive_a_new_open_checkpoint(self):
        p,c,b = self.seed();old = self.cycle(p,c,b)
        p2,c2,b2 = move_observation(p,c,old['next_binding'])
        c2['observations']['flow_adjusted_nav_index'] = 80.
        opened = self.cycle(p2,c2,b2,event_id='trip')
        for checkpoint in (old['next_binding'],opened['next_binding']):
            out = self.read(p,c,old,binding=checkpoint)
            self.assertIsNone(out['draft_settings']);self.assertTrue(out['new_risk_prohibited'])
        self.assertEqual(opened['breaker_state'],'OPEN')

    def test_explicit_external_receipt_is_required_even_for_valid_self_hashed_content(self):
        p,c,b = self.seed();saved = self.cycle(p,c,b)
        for expected in (None,'','f'*64):
            out = self.read(p,c,saved,expected_artifact_sha256=expected)
            self.assertFalse(out['artifact_readback_verified']);self.assertIsNone(out['draft_settings'])

    def test_p4_server_confirmation_is_covered_by_outer_hash_even_though_p4_hash_excludes_it(self):
        p,c,b = self.seed();saved = self.cycle(p,c,b);path = self.artifact_path()
        value = json.loads(path.read_text());original = value['preview']['p4_preview']['confirmation_binding']['preview_sha256']
        value['preview']['p4_preview']['confirmation_binding']['server_confirmation_ref'] = 'FORGED'
        value['artifact_sha256'] = digest({k:v for k,v in value.items() if k != 'artifact_sha256'})
        self.assertEqual(value['preview']['p4_preview']['confirmation_binding']['preview_sha256'],original)
        path.write_text(json.dumps(value))
        self.assertIn('SETTINGS_ARTIFACT_RECEIPT_MISMATCH',self.read(p,c,saved)['gaps'])
        forged = self.read(p,c,saved,expected_artifact_sha256=value['artifact_sha256'])
        self.assertIn('SETTINGS_SERVER_CONFIRMATION_FORBIDDEN',forged['gaps']);self.assert_unarmed(forged)

    def test_tampered_draft_or_duplicate_json_cannot_read_back(self):
        p,c,b = self.seed();saved = self.cycle(p,c,b);path = self.artifact_path();original = path.read_text()
        value = json.loads(original);value['draft_settings']['tail_budget_ratio'] = 1.
        path.write_text(json.dumps(value));self.assertFalse(self.read(p,c,saved)['artifact_readback_verified'])
        path.write_text('{"schema_version":"extra",'+original[1:])
        self.assertFalse(self.read(p,c,saved)['artifact_readback_verified'])

    def test_rehashed_artifact_with_another_event_input_still_disagrees_with_journal(self):
        p,c,b = self.seed();saved = self.cycle(p,c,b);path = self.artifact_path()
        value = json.loads(path.read_text());value['input_sha256'] = 'f'*64
        value['artifact_sha256'] = digest({k:v for k,v in value.items() if k != 'artifact_sha256'})
        path.write_text(json.dumps(value))
        out = self.read(p,c,saved,expected_artifact_sha256=value['artifact_sha256'])
        self.assertIn('SETTINGS_EVENT_INPUT_MISMATCH',out['gaps']);self.assertIsNone(out['draft_settings'])

    def test_full_original_request_account_policy_fee_and_quantity_inputs_are_bound(self):
        p,c,b = self.seed();saved = self.cycle(p,c,b)
        changes = (('contract',),('facts',),('policy',),('fees',),('quantity',),('request',),('observations',),('cutoff',))
        for change in changes:
            with self.subTest(change=change):
                p2,c2,req = deepcopy(p),deepcopy(c),request_fixture()
                if change[0] == 'contract':p2['account_contract']['settled_cash'] = '99'
                if change[0] == 'facts':p2['facts']['net_assets'] = '99'
                if change[0] == 'policy':c2['policy']['policy_id'] = 'NEW'
                if change[0] == 'fees':p2['execution_rules']['fees']['bps_by_symbol']['A'] = '11'
                if change[0] == 'quantity':p2['execution_rules']['quantity']['step_by_symbol']['A'] = '2'
                if change[0] == 'request':req['mode'] = 'advanced'
                if change[0] == 'observations':c2['observations']['annual_volatility_ratio'] = .02
                if change[0] == 'cutoff':p2['account_contract']['cutoff_utc'] = '2000-01-04T12:01:30Z'
                out = self.read(p2,c2,saved,req)
                self.assertFalse(out['artifact_readback_verified']);self.assertIsNone(out['draft_settings'])

    def test_current_source_freshness_reuses_original_age_and_effective_windows(self):
        for source in ('account','cash','fees','quantity','prices','policy','observations','math'):
            with self.subTest(source=source), tempfile.TemporaryDirectory(dir=self.root) as temp:
                self.journal = PortfolioEventJournal(Path(temp)/'journal',offline_root=self.root)
                p,c,unused,proof = intraday_fixture()
                if source == 'account':p['account_contract']['max_age_seconds'] = 70
                elif source in ('cash','math'):
                    p['cash_evidence' if source == 'cash' else 'math_contract']['valid_until'] = '2000-01-04T12:01:15Z'
                elif source in ('fees','quantity','prices'):p['execution_rules'][source]['valid_until'] = '2000-01-04T12:01:15Z'
                else:c[source]['max_age_seconds'] = 70
                c['observations']['fee_evidence_sha256'] = digest(p['execution_rules']['fees'])
                p,c,b = self.seed(packet=p,context=c);saved = self.cycle(p,c,b)
                self.assertEqual(saved['gaps'],[])
                out = self.read(p,c,saved)
                self.assertFalse(out['artifact_readback_verified']);self.assertIsNone(out['draft_settings'])
                self.assertEqual(p['account_contract']['cutoff_utc'],'2000-01-04T12:01:00Z')

    def test_expired_checkpoint_or_consumption_before_computation_never_refreshes_draft(self):
        p,c,b = self.seed();saved = self.cycle(p,c,b)
        for cutoff in ('2000-01-04T12:02:01Z','2000-01-04T12:00:59Z'):
            out = self.read(p,c,saved,consumption_cutoff_utc=cutoff)
            self.assertFalse(out['artifact_readback_verified']);self.assertIsNone(out['draft_settings'])

    def test_migrated_source_draft_is_retired_despite_matching_receipt(self):
        p,c,b = self.seed(opened=True);saved = self.cycle(p,c,b)
        source = self.journal.load_chain(saved['next_binding']['identity'])[-1]
        destination,proof = migration_fixture(saved['next_binding'],source)
        moved = migrate_portfolio_event_state(journal=self.journal,source_binding=saved['next_binding'],
            destination_binding=destination,account_contract=p['account_contract'],migration=proof)
        self.assertTrue(moved['readback_verified'],moved['gaps'])
        out = self.read(p,c,saved,binding=moved['source_next_binding'])
        self.assertIn('SETTINGS_SOURCE_IDENTITY_RETIRED',out['gaps']);self.assertIsNone(out['draft_settings'])

    def test_artifact_write_before_failure_preserves_confirmed_state_and_never_retries(self):
        p,c,b = self.seed();original = self.journal.object_store.create_text;calls = []
        def fail(location,*args,**kwargs):
            if NAMESPACE in location:calls.append(location);raise OSError('artificial before create')
            return original(location,*args,**kwargs)
        with patch.object(self.journal.object_store,'create_text',side_effect=fail):out = self.cycle(p,c,b)
        self.assertEqual(len(calls),1);self.assertTrue(out['state_persisted']);self.assertTrue(out['readback_verified'])
        self.assertIsNone(out['draft_settings']);self.assertIsNotNone(out['next_binding'])
        read = read_portfolio_event_state(journal=self.journal,binding=out['next_binding'],account_contract=p['account_contract'])
        self.assertTrue(read['readback_verified']);self.assertEqual(len(read['chain']),2)
        replay = self.cycle(p,c,b);self.assertIsNone(replay['draft_settings']);self.assertIsNone(replay['artifact_receipt'])

    def test_artifact_lost_acknowledgement_is_confirmed_once_without_rewrite(self):
        p,c,b = self.seed();original = self.journal.object_store.create_text;calls = []
        def lost(location,*args,**kwargs):
            result = original(location,*args,**kwargs)
            if NAMESPACE in location:calls.append(location);raise OSError('artificial lost acknowledgement')
            return result
        with patch.object(self.journal.object_store,'create_text',side_effect=lost):out = self.cycle(p,c,b)
        self.assertEqual(len(calls),1);self.assertTrue(out['artifact_readback_verified'],out['gaps'])
        self.assertEqual(out['artifact_append_error_type'],'OSError');self.assertEqual(self.read(p,c,out)['gaps'],[])

    def test_partial_artifact_is_preserved_and_does_not_roll_back_or_recreate(self):
        p,c,b = self.seed();original = self.journal.object_store.create_text;calls = []
        def partial(location,*args,**kwargs):
            if NAMESPACE in location:
                calls.append(location);path = Path(location);path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text('{');raise OSError('artificial partial artifact')
            return original(location,*args,**kwargs)
        with patch.object(self.journal.object_store,'create_text',side_effect=partial):out = self.cycle(p,c,b)
        self.assertEqual(len(calls),1);self.assertTrue(out['state_persisted']);self.assertTrue(out['readback_verified'])
        self.assertFalse(out['artifact_readback_verified']);self.assertIsNone(out['draft_settings'])
        self.assertEqual(self.artifact_path().read_text(),'{');before = self.snapshot()
        self.assertIsNone(self.cycle(p,c,b)['draft_settings']);self.assertEqual(before,self.snapshot())

    def test_artifact_scope_symlink_is_rejected_without_overwriting_target(self):
        p,c,b = self.seed();outside = self.root/'outside';outside.mkdir()
        self.journal.local_dir.mkdir(exist_ok=True)
        (self.journal.local_dir/NAMESPACE).symlink_to(outside,target_is_directory=True)
        out = self.cycle(p,c,b)
        self.assertTrue(out['state_persisted']);self.assertFalse(out['artifact_readback_verified'])
        self.assertEqual(list(outside.iterdir()),[]);self.assertIsNone(out['draft_settings'])

    def test_independent_process_draft_readback_and_stale_checkpoint_have_no_side_effects(self):
        repo = Path(__file__).resolve().parents[2]
        fixture = repo/'tests/research/fixtures/portfolio_settings_draft_probe.py'
        env = dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
        env['PYTHONPATH'] = os.pathsep.join((str(repo/'src'),str(repo/'tests/research'),env.get('PYTHONPATH','')))
        reports = {}
        for phase in ('save','reload','trip','old_read'):
            run = subprocess.run([sys.executable,str(fixture),phase,str(self.root)],cwd=repo,env=env,
                capture_output=True,text=True,timeout=30)
            self.assertEqual(run.returncode,0,(phase,run.stdout,run.stderr))
            report = json.loads(run.stdout.strip().splitlines()[-1]);reports[phase] = report
            self.assertEqual(report['forbidden_effect_attempts'],[])
            for key in ('execution_authorized','settings_applied','breaker_reset'):self.assertFalse(report[key])
        self.assertEqual(len({r['pid'] for r in reports.values()}),4)
        self.assertTrue(reports['reload']['artifact_readback_verified'])
        self.assertEqual(reports['reload']['write_calls'],0)
        self.assertFalse(reports['old_read']['artifact_readback_verified'])
        self.assertTrue(reports['old_read']['new_risk_prohibited']);self.assertEqual(reports['old_read']['write_calls'],0)


if __name__ == '__main__':unittest.main()
