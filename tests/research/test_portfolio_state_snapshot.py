"""Checkpointed artificial backups; no real account or deployment authority."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from quant_platform_kit.cloud.local_provider import LocalObjectStore
from quant_platform_kit.common.strategy_risk_state import StrategyRiskStateStore
from test_portfolio_intraday_state_io import intraday_fixture, migration_fixture
from test_portfolio_risk_state_io import io_fixture
from test_portfolio_settings_preview import request_fixture
from us_equity_strategies.research.auto_portfolio_account import digest
from us_equity_strategies.research.portfolio_event_journal import PortfolioEventJournal, build_event_record
from us_equity_strategies.research.portfolio_intraday_state_io import (
    initialize_portfolio_event_state, persist_portfolio_intraday_preview, migrate_portfolio_event_state,
    read_portfolio_event_state,
)
from us_equity_strategies.research.portfolio_risk_state_io import persist_portfolio_risk_preview
from us_equity_strategies.research import portfolio_state_snapshot as snapshots


class StateSnapshotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ues-artificial-snapshot-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.archive = self.root/'archive'

    def seed(self, kind='INTRADAY'):
        if kind == 'INTRADAY':
            packet,context,binding,proof = intraday_fixture(opened=True,held=True)
            store = PortfolioEventJournal(self.root/'intraday',offline_root=self.root)
            initial = initialize_portfolio_event_state(journal=store,binding=binding,
                account_contract=packet['account_contract'],initialization=proof)
            result = persist_portfolio_intraday_preview(request_fixture(),journal=store,
                binding=initial['next_binding'],event_id='first',event_context=context,**packet)
        else:
            packet,context,binding,prior = io_fixture(held=True,opened=True)
            store = StrategyRiskStateStore(local_dir=self.root/'daily')
            store.append(prior)
            result = persist_portfolio_risk_preview(request_fixture(),store=store,binding=binding,
                event_context=context,offline_root=self.root,**packet)
        self.assertTrue(result['readback_verified'],result['gaps'])
        return store,result['next_binding'],packet['account_contract']

    def save(self, store, binding, contract, kind='INTRADAY', **changes):
        args = dict(binding=binding,account_contract=contract,snapshot_dir=self.archive,offline_root=self.root)
        args.update(changes)
        if kind == 'INTRADAY': return snapshots.save_intraday_state_snapshot(journal=store,**args)
        return snapshots.save_daily_state_snapshot(store=store,**args)

    def target(self, kind='INTRADAY', name='restored'):
        if kind == 'INTRADAY': return PortfolioEventJournal(self.root/name,offline_root=self.root)
        return StrategyRiskStateStore(local_dir=self.root/name)

    def restore(self, target, binding, contract, sha, kind='INTRADAY', **changes):
        args = dict(binding=binding,account_contract=contract,snapshot_dir=self.archive,
            expected_sha256=sha,offline_root=self.root)
        args.update(changes)
        if kind == 'INTRADAY': return snapshots.restore_intraday_state_snapshot(journal=target,**args)
        return snapshots.restore_daily_state_snapshot(store=target,**args)

    def test_daily_and_intraday_roundtrip_preserve_complete_open_history(self):
        for kind in ('DAILY','INTRADAY'):
            with self.subTest(kind=kind):
                store,binding,contract = self.seed(kind)
                before = {p:p.read_bytes() for p in Path(store.local_dir).rglob('*') if p.is_file()}
                saved = self.save(store,binding,contract,kind)
                self.assertTrue(saved['snapshot_persisted'],saved['gaps'])
                target = self.target(kind,'restored-'+kind)
                restored = self.restore(target,binding,contract,saved['snapshot_sha256'],kind)
                self.assertTrue(restored['state_recovered'],restored['gaps'])
                self.assertEqual(restored['breaker_state'],'OPEN')
                self.assertTrue(restored['new_risk_prohibited'])
                self.assertIsNone(restored['source_retired'])
                self.assertFalse(restored['settings_applied'] or restored['breaker_reset'] or restored['execution_authorized'])
                self.assertEqual(target.load_chain(binding['identity']),store.load_chain(binding['identity']))
                self.assertEqual(before,{p:p.read_bytes() for p in before})

    def test_migrated_history_includes_origin_and_terminal_source(self):
        store,binding,contract = self.seed()
        destination,proof = migration_fixture(binding,store.load_chain(binding['identity'])[-1])
        migrated = migrate_portfolio_event_state(journal=store,source_binding=binding,
            destination_binding=destination,account_contract=contract,migration=proof)
        saved = self.save(store,migrated['next_binding'],contract)
        target = self.target()
        restored = self.restore(target,migrated['next_binding'],contract,saved['snapshot_sha256'])
        self.assertTrue(restored['state_recovered'],restored['gaps'])
        self.assertEqual(target.load_chain(binding['identity'])[-1]['kind'],'MIGRATE_OUT')
        self.assertEqual(target.load_chain(destination['identity'])[0]['kind'],'MIGRATE_IN')
        self.assertEqual(restored['breaker_state'],'OPEN')

    def test_same_snapshot_save_is_create_only_idempotent(self):
        store,binding,contract = self.seed()
        first = self.save(store,binding,contract);second = self.save(store,binding,contract)
        self.assertEqual(first['snapshot_sha256'],second['snapshot_sha256'])
        self.assertTrue(second['readback_verified']);self.assertEqual(len(list(self.archive.rglob('*.json'))),1)

    def test_missing_source_and_empty_genesis_cannot_be_backed_up(self):
        store,binding,contract = self.seed()
        binding['expected_head_sha256'] = 'f'*64
        self.assertFalse(self.save(store,binding,contract)['readback_verified'])
        packet,context,binding,prior = io_fixture()
        empty = StrategyRiskStateStore(local_dir=self.root/'empty')
        self.assertFalse(self.save(empty,binding,packet['account_contract'],'DAILY')['readback_verified'])
        self.assertFalse(self.archive.exists())

    def test_archive_path_must_be_separate_local_and_inside_approved_root(self):
        store,binding,contract = self.seed()
        for path in (store.local_dir,Path(store.local_dir)/'archive',self.root,'gs://artificial',self.root.parent/'outside'):
            with self.subTest(path=str(path)):
                self.assertFalse(self.save(store,binding,contract,snapshot_dir=path)['readback_verified'])
        self.archive.symlink_to(Path(store.local_dir),target_is_directory=True)
        self.assertFalse(self.save(store,binding,contract)['readback_verified'])

    def test_source_changes_during_snapshot_confirmation_do_not_write_archive(self):
        store,binding,contract = self.seed();original = snapshots._read_state;calls = []
        def changing(*args):
            calls.append(True)
            if len(calls) == 2:
                previous = store.load_chain(binding['identity'])[-1]
                store.append(build_event_record(identity=binding['identity'],kind='EVENT',event_id='later',
                    observed_at=previous['observed_at'],effective_session=previous['effective_session'],
                    input_sha256=digest('different'),state=previous['state'],previous=previous))
            return original(*args)
        with patch.object(snapshots,'_read_state',side_effect=changing): result = self.save(store,binding,contract)
        self.assertIn('SNAPSHOT_SOURCE_CHANGED',result['gaps']);self.assertFalse(self.archive.exists())

    def test_lost_snapshot_receipt_is_read_once_without_rewrite(self):
        store,binding,contract = self.seed();original = LocalObjectStore.create_text;calls = []
        def lost(provider,*args,**kwargs):
            calls.append(True);original(provider,*args,**kwargs);raise OSError('artificial lost receipt')
        with patch.object(LocalObjectStore,'create_text',new=lost): result = self.save(store,binding,contract)
        self.assertTrue(result['snapshot_persisted'],result['gaps']);self.assertEqual(len(calls),1)

    def test_partial_snapshot_is_preserved_and_never_repaired(self):
        store,binding,contract = self.seed()
        def partial(provider,uri,data,**kwargs):
            path = Path(uri);path.parent.mkdir(parents=True);path.write_text('{');raise OSError('partial')
        with patch.object(LocalObjectStore,'create_text',new=partial): failed = self.save(store,binding,contract)
        path = next(self.archive.rglob('*.json'));self.assertEqual(path.read_text(),'{')
        self.assertFalse(failed['readback_verified'])
        self.assertFalse(self.save(store,binding,contract)['readback_verified']);self.assertEqual(path.read_text(),'{')

    def test_restore_rejects_nonempty_target_without_touching_it(self):
        store,binding,contract = self.seed();saved = self.save(store,binding,contract)
        target = self.target();Path(target.local_dir).mkdir();file = Path(target.local_dir)/'retained';file.write_text('keep')
        result = self.restore(target,binding,contract,saved['snapshot_sha256'])
        self.assertIn('SNAPSHOT_RECOVERY_DIRECTORY_NOT_EMPTY',result['gaps']);self.assertEqual(file.read_text(),'keep')

    def test_current_checkpoint_rejects_older_backup_and_future_state_before_writes(self):
        store,binding,contract = self.seed();saved = self.save(store,binding,contract);target = self.target()
        future = dict(binding,observed_at='2000-01-04T11:59:59Z')
        failed = self.restore(target,future,contract,saved['snapshot_sha256'])
        self.assertIn('SNAPSHOT_FUTURE_STATE',failed['gaps']);self.assertFalse(Path(target.local_dir).exists())
        previous = store.load_chain(binding['identity'])[-1]
        later = build_event_record(identity=binding['identity'],kind='EVENT',event_id='new-checkpoint',
            observed_at=previous['observed_at'],effective_session=previous['effective_session'],
            input_sha256=digest('new-checkpoint'),state=previous['state'],previous=previous)
        store.append(later)
        current = dict(binding,expected_head_sha256=later['record_sha256'])
        failed = self.restore(target,current,contract,saved['snapshot_sha256'])
        self.assertIn('SNAPSHOT_CHECKPOINT_MISMATCH',failed['gaps']);self.assertFalse(Path(target.local_dir).exists())

    def test_daily_snapshot_rejects_noncanonical_records_before_target_writes(self):
        store,binding,contract = self.seed('DAILY');saved = self.save(store,binding,contract,'DAILY')
        path = self.archive/snapshots.NAMESPACE/(saved['snapshot_sha256']+'.json')
        value = json.loads(path.read_text());value['chains'][0][0]['unbound_extra'] = True
        value['snapshot_sha256'] = digest({k:v for k,v in value.items() if k != 'snapshot_sha256'})
        path = self.archive/snapshots.NAMESPACE/(value['snapshot_sha256']+'.json');path.write_text(json.dumps(value))
        target = self.target('DAILY')
        failed = self.restore(target,binding,contract,value['snapshot_sha256'],'DAILY')
        self.assertFalse(failed['state_recovered']);self.assertFalse(Path(target.local_dir).exists())

    def test_checkpoint_identity_scope_freshness_and_digest_rejected_before_target_writes(self):
        store,binding,contract = self.seed();saved = self.save(store,binding,contract);target = self.target()
        for key,value in [('expected_head_sha256','f'*64),('account_id','OTHER'),
                ('observed_at','1999-01-01T12:00:00Z'),('source_version','')]:
            bad = deepcopy(binding);bad[key] = value
            self.assertFalse(self.restore(target,bad,contract,saved['snapshot_sha256'])['state_recovered'])
        for sha in (None,'../escape','A'*64,'f'*64):
            self.assertFalse(self.restore(target,binding,contract,sha)['state_recovered'])
        self.assertFalse(Path(target.local_dir).exists())

    def test_corruption_duplicate_keys_and_missing_ancestry_are_rejected_before_writes(self):
        store,binding,contract = self.seed();saved = self.save(store,binding,contract)
        path = next(self.archive.rglob('*.json'));original = path.read_text();target = self.target()
        for raw in ('{',original[:-1]+',"kind":"INTRADAY"}',original.replace('"breaker_open":true','"breaker_open":false')):
            path.write_text(raw)
            self.assertFalse(self.restore(target,binding,contract,saved['snapshot_sha256'])['state_recovered'])
            self.assertFalse(Path(target.local_dir).exists())
        path.write_text(original)
        destination,proof = migration_fixture(binding,store.load_chain(binding['identity'])[-1])
        migrated = migrate_portfolio_event_state(journal=store,source_binding=binding,
            destination_binding=destination,account_contract=contract,migration=proof)
        saved = self.save(store,migrated['next_binding'],contract)
        path = self.archive/snapshots.NAMESPACE/(saved['snapshot_sha256']+'.json')
        value = json.loads(path.read_text());value['chains'] = value['chains'][1:]
        value['snapshot_sha256'] = digest({k:v for k,v in value.items() if k != 'snapshot_sha256'})
        path = self.archive/snapshots.NAMESPACE/(value['snapshot_sha256']+'.json');path.write_text(json.dumps(value))
        failed = self.restore(target,migrated['next_binding'],contract,value['snapshot_sha256'])
        self.assertIn('SNAPSHOT_ORIGIN_HISTORY_MISSING',failed['gaps']);self.assertFalse(Path(target.local_dir).exists())

    def test_restore_lost_append_receipt_confirms_exact_prefix_without_retry(self):
        for kind,cls in (('INTRADAY',PortfolioEventJournal),('DAILY',StrategyRiskStateStore)):
            store,binding,contract = self.seed(kind);saved = self.save(store,binding,contract,kind)
            target = self.target(kind,'lost-'+kind);original = cls.append;calls = []
            def lost(instance,record):
                calls.append(True);original(instance,record);raise OSError('lost')
            with patch.object(cls,'append',new=lost):
                result = self.restore(target,binding,contract,saved['snapshot_sha256'],kind)
            self.assertTrue(result['state_recovered'],result['gaps'])
            self.assertEqual(len(calls),len(store.load_chain(binding['identity'])))

    def test_restore_partial_write_blocks_and_keeps_target_for_diagnosis(self):
        store,binding,contract = self.seed();saved = self.save(store,binding,contract);target = self.target()
        def partial(provider,uri,data,**kwargs):
            path = Path(uri);path.parent.mkdir(parents=True,exist_ok=True);path.write_text('{');raise OSError('partial')
        with patch.object(LocalObjectStore,'create_text',new=partial):
            result = self.restore(target,binding,contract,saved['snapshot_sha256'])
        self.assertFalse(result['state_recovered']);path = next(Path(target.local_dir).rglob('*.json'))
        self.assertEqual(path.read_text(),'{')
        result = self.restore(target,binding,contract,saved['snapshot_sha256'])
        self.assertIn('SNAPSHOT_RECOVERY_DIRECTORY_NOT_EMPTY',result['gaps']);self.assertEqual(path.read_text(),'{')

    def test_restored_state_requires_new_event_and_does_not_retire_original_writer(self):
        store,binding,contract = self.seed();saved = self.save(store,binding,contract);target = self.target()
        result = self.restore(target,binding,contract,saved['snapshot_sha256'])
        self.assertNotIn('draft_settings',result);self.assertTrue(result['new_risk_prohibited'])
        previous = store.load_chain(binding['identity'])[-1]
        store.append(build_event_record(identity=binding['identity'],kind='EVENT',event_id='still-runs',
            observed_at=previous['observed_at'],effective_session=previous['effective_session'],
            input_sha256=digest('new'),state=previous['state'],previous=previous))
        self.assertTrue(read_portfolio_event_state(journal=target,binding=binding,account_contract=contract)['readback_verified'])
        self.assertIsNone(result['source_retired'])

    def test_independent_process_snapshot_restore_and_read_only_restart(self):
        script = Path(__file__).parent/'fixtures'/'portfolio_snapshot_probe.py'
        repo = Path(__file__).resolve().parents[2];pids = []
        env = dict(os.environ,PYTHONPATH=os.pathsep.join((str(repo/'src'),str(Path(__file__).parent))),PYTHONDONTWRITEBYTECODE='1')
        for phase in ('seed','restore','read'):
            proc = subprocess.run([sys.executable,str(script),phase,str(self.root)],cwd=repo,env=env,
                capture_output=True,text=True,timeout=90)
            self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
            value = json.loads(proc.stdout.splitlines()[-1]);pids.append(value['pid'])
            self.assertEqual(value['forbidden_effect_attempts'],[])
            self.assertEqual(value['breaker_state'],'OPEN');self.assertFalse(value['execution_authorized'])
        self.assertEqual(len(set(pids)),3)
