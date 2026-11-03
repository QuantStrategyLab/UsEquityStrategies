"""Artificial local journal contract; no account, network or runtime configuration."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import json
import tempfile
from threading import Barrier
import unittest

from quant_platform_kit.cloud.local_provider import LocalObjectStore

from us_equity_strategies.research.auto_portfolio_account import digest
from us_equity_strategies.research.portfolio_event_journal import (
    PortfolioEventJournal, build_event_record, validate_event_chain,
)


IDENTITY = dict(strategy_profile='portfolio.risk.events', account_scope='artificial.account',
    candidate_id='synthetic.portfolio', config_sha256='a' * 64)


def record(previous=None, *, kind=None, event_id=None, observed_at=None, **changes):
    number = 0 if previous is None else previous['sequence'] + 1
    values = dict(identity=IDENTITY, kind=kind or ('INITIALIZE' if previous is None else 'EVENT'),
        event_id=f'event-{number}' if event_id is None else event_id,
        observed_at=observed_at or f'2026-10-25T12:{number:02d}:00Z',
        effective_session='2026-10-25', input_sha256=digest(dict(number=number)),
        state=dict(breaker_open=False, value=number), previous=previous,
        provenance={'source': 'artificial.fixture'} if previous is None or kind == 'MIGRATE_OUT' else None)
    values.update(changes)
    return build_event_record(**values)


def resigned(value):
    result = deepcopy(value)
    result['record_sha256'] = digest({k: v for k, v in result.items() if k != 'record_sha256'})
    return result


class EventRecordTests(unittest.TestCase):
    def test_same_day_multiple_and_next_day(self):
        first = record(); second = record(first)
        third = record(second, observed_at='2026-10-26T00:00:00Z', effective_session='2026-10-26')
        validate_event_chain([first, second, third])
        self.assertEqual([x['sequence'] for x in (first, second, third)], [0, 1, 2])
        self.assertEqual(first['observed_at'], '2026-10-25T12:00:00+00:00')

    def test_aware_timestamp_normalizes_before_session_check(self):
        result = record(observed_at='2026-10-25T20:00:00+08:00')
        self.assertEqual(result['observed_at'], '2026-10-25T12:00:00+00:00')

    def test_root_kind_and_provenance_are_required(self):
        for updates in ({'kind': 'EVENT'}, {'kind': 'MIGRATE_OUT'}, {'provenance': None},
                {'provenance': {}}, {'kind': 'unknown'}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                record(**updates)
        self.assertEqual(record(kind='MIGRATE_IN')['kind'], 'MIGRATE_IN')

    def test_successor_kind_and_event_provenance(self):
        first = record()
        for updates in ({'kind': 'INITIALIZE'}, {'kind': 'MIGRATE_IN'},
                {'provenance': {'not': 'allowed'}}, {'kind': 'MIGRATE_OUT', 'provenance': None}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                record(first, **updates)

    def test_migration_out_is_terminal(self):
        first = record(); terminal = record(first, kind='MIGRATE_OUT')
        validate_event_chain([first, terminal])
        with self.assertRaises(ValueError): record(terminal)

    def test_time_date_event_and_json_strictness(self):
        invalid = [dict(observed_at='2026-10-25T12:00:00'), dict(effective_session='2026-02-30'),
            dict(effective_session='20261025'), dict(effective_session='2026-10-24'),
            dict(event_id=''), dict(event_id='../escape'), dict(event_id='中文'),
            dict(event_id='x' * 129), dict(state={'x': float('nan')}),
            dict(state={'x': (1, 2)}), dict(state={1: 'invalid'}), dict(input_sha256='B' * 64)]
        for updates in invalid:
            with self.subTest(updates=updates), self.assertRaises(ValueError): record(**updates)

    def test_wire_exact_fields_types_identity_and_hashes(self):
        first = record()
        mutations = [dict(sequence=True), dict(sequence=1), dict(state_sha256='f' * 64),
            dict(record_sha256='f' * 64), dict(extra=1), dict(observed_at='2026-10-25T12:00:00Z'),
            dict(identity={**IDENTITY, 'account_scope': ' ARTIFICIAL.ACCOUNT '}),
            dict(identity={**IDENTITY, 'candidate_id': 1})]
        for changes in mutations:
            value = {**first, **changes}
            with self.subTest(changes=changes), self.assertRaises(ValueError): validate_event_chain([value])
        missing = dict(first); missing.pop('provenance')
        with self.assertRaises(ValueError): validate_event_chain([missing])

    def test_duplicate_event_reverse_sequence_identity_and_time(self):
        first = record(); second = record(first)
        mutations = [dict(event_id=first['event_id']), dict(sequence=8),
            dict(identity={**IDENTITY, 'account_scope': 'another.account'}),
            dict(observed_at='2026-10-25T11:00:00+00:00'),
            dict(previous_record_sha256='f' * 64)]
        for changes in mutations:
            changed = resigned({**second, **changes})
            with self.subTest(changes=changes), self.assertRaises(ValueError): validate_event_chain([first, changed])
        with self.assertRaises(ValueError): validate_event_chain([second, first])
        repeated = record(second, event_id=first['event_id'])
        with self.assertRaises(ValueError): validate_event_chain([first, second, repeated])


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.local = self.root / 'journal'
        self.store = PortfolioEventJournal(self.local, offline_root=self.root)

    def chain_dir(self): return Path(self.store._location(IDENTITY))

    def test_constructor_does_not_write_and_namespaces_are_separate(self):
        self.assertFalse(self.local.exists())
        self.assertEqual(self.store.load_chain(IDENTITY), [])
        self.assertFalse(self.local.exists())
        self.assertNotIn('strategy_risk_states', self.chain_dir().parts)

    def test_append_replay_and_restart(self):
        first = record(); second = record(first)
        self.assertEqual(self.store.append(first), dict(status='created', head_sha256=first['record_sha256'], chain_length=1))
        self.store.append(second)
        recovered = PortfolioEventJournal(self.local, offline_root=self.root)
        self.assertEqual(recovered.load_chain(IDENTITY), [first, second])
        self.assertEqual(recovered.append(first), dict(status='already_appended', head_sha256=second['record_sha256'], chain_length=2))

    def test_initialization_has_one_scope_address_without_duplicate_copy(self):
        first = record(); self.store.append(first)
        origin = Path(self.store._root_location(IDENTITY))
        self.assertTrue(origin.is_file())
        self.assertFalse((self.chain_dir() / 'root.json').exists())
        other = {**IDENTITY, 'candidate_id': 'different.portfolio', 'config_sha256': 'b' * 64}
        self.assertEqual(self.store._root_location(IDENTITY), self.store._root_location(other))
        self.assertEqual(self.store.load_chain(other), [])
        with self.assertRaises(ValueError): self.store.append(record(identity=other))
        self.assertEqual(self.store.load_chain(IDENTITY), [first])
        self.assertEqual(json.loads(origin.read_text()), first)

    def test_distinct_account_scope_can_initialize_independently(self):
        first = record(); self.store.append(first)
        other = {**IDENTITY, 'account_scope': 'another.artificial.account'}
        second = record(identity=other)
        self.store.append(second)
        self.assertEqual(self.store.load_chain(IDENTITY), [first])
        self.assertEqual(self.store.load_chain(other), [second])

    def test_bad_origin_in_another_scope_does_not_block_new_account(self):
        self.store.append(record())
        Path(self.store._root_location(IDENTITY)).write_text('{')
        other = {**IDENTITY, 'account_scope': 'another.artificial.account'}
        second = record(identity=other)
        self.store.append(second)
        self.assertEqual(self.store.load_chain(other), [second])
        with self.assertRaises(ValueError): self.store.load_chain(IDENTITY)

    def test_two_identity_initialization_race_has_only_one_scope_winner(self):
        barrier = Barrier(2)
        class SynchronizedCreate(LocalObjectStore):
            def create_text(self, uri, data, content_type='text/plain'):
                barrier.wait(timeout=5)
                return super().create_text(uri, data, content_type)
        store = PortfolioEventJournal(self.local, offline_root=self.root, object_store=SynchronizedCreate())
        other = {**IDENTITY, 'config_sha256': 'b' * 64}
        candidates = [record(), record(identity=other)]
        def append(value):
            try: return store.append(value)['status']
            except ValueError: return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(append, candidates))
        self.assertEqual(sorted(results), ['conflict', 'created'])
        self.assertEqual(sum(len(store.load_chain(identity)) for identity in (IDENTITY, other)), 1)

    def test_removed_origin_with_successors_cannot_restart_same_or_changed_identity(self):
        first = record(); self.store.append(first); self.store.append(record(first))
        origin = Path(self.store._root_location(IDENTITY)); origin.unlink()
        with self.assertRaises(ValueError): self.store.load_chain(IDENTITY)
        for identity in (IDENTITY, {**IDENTITY, 'config_sha256': 'b' * 64}):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                self.store.append(record(identity=identity))
        self.assertFalse(origin.exists())

    def test_invalid_origin_blocks_other_identity_in_same_scope(self):
        first = record(); self.store.append(first)
        origin = Path(self.store._root_location(IDENTITY))
        origin.write_text('{')
        other = {**IDENTITY, 'config_sha256': 'b' * 64}
        with self.assertRaises(ValueError): self.store.load_chain(other)
        with self.assertRaises(ValueError): self.store.append(record(identity=other))
        self.assertEqual(origin.read_text(), '{')

    def test_origin_wrong_scope_wrong_kind_or_extra_file_rejected(self):
        first = record(); self.store.append(first)
        origin = Path(self.store._root_location(IDENTITY))
        for wrong in (record(identity={**IDENTITY, 'account_scope': 'different.account'}), record(kind='MIGRATE_IN')):
            origin.write_text(json.dumps(wrong))
            with self.assertRaises(ValueError): self.store.load_chain(IDENTITY)
        origin.write_text(json.dumps(first))
        (origin.parent / 'uncommitted.tmp').write_text('pending')
        with self.assertRaises(ValueError): self.store.load_chain(IDENTITY)

    def test_per_identity_initialize_and_duplicate_root_addresses_rejected(self):
        first = record(); self.store.append(first)
        old_address = Path(self.store._root_location(IDENTITY, kind='MIGRATE_IN'))
        old_address.parent.mkdir(parents=True)
        old_address.write_text(json.dumps(first))
        with self.assertRaises(ValueError): self.store.load_chain(IDENTITY)
        old_address.write_text(json.dumps(record(kind='MIGRATE_IN')))
        with self.assertRaises(ValueError): self.store.load_chain(IDENTITY)

    def test_structural_migration_root_has_identity_address_and_blocks_reinitialize(self):
        first = record(kind='MIGRATE_IN'); self.store.append(first)
        self.assertTrue(Path(self.store._root_location(IDENTITY, kind='MIGRATE_IN')).is_file())
        self.assertFalse(Path(self.store._root_location(IDENTITY)).exists())
        self.assertEqual(self.store.load_chain(IDENTITY), [first])
        other = {**IDENTITY, 'config_sha256': 'b' * 64}
        with self.assertRaises(ValueError): self.store.append(record(identity=other))

    def test_migration_to_other_identity_can_share_scope_origin(self):
        first = record(); self.store.append(first)
        other = {**IDENTITY, 'config_sha256': 'b' * 64}
        migrated = record(kind='MIGRATE_IN', identity=other)
        self.store.append(migrated)
        self.assertEqual(self.store.load_chain(IDENTITY), [first])
        self.assertEqual(self.store.load_chain(other), [migrated])

    def test_conflicting_event_id_and_stale_predecessor(self):
        first = record(); second = record(first)
        self.store.append(first); self.store.append(second)
        for value in (record(first, input_sha256='b' * 64), record(first, event_id='other')):
            with self.assertRaises(ValueError): self.store.append(value)
        self.assertEqual(self.store.load_chain(IDENTITY), [first, second])

    def test_create_only_concurrent_identical_append(self):
        first = record(); self.store.append(first)
        second = record(first)
        def append(_):
            other = PortfolioEventJournal(self.local, offline_root=self.root)
            try: return other.append(second)['status']
            except ValueError: return 'partial_read_rejected'
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(append, range(8)))
        self.assertIn('created', results)
        self.assertEqual(self.store.load_chain(IDENTITY), [first, second])
        self.assertEqual(self.store.append(second)['status'], 'already_appended')

    def test_two_competing_successors_cannot_both_commit(self):
        first = record(); self.store.append(first)
        barrier = Barrier(2)
        class SynchronizedCreate(LocalObjectStore):
            def create_text(self, uri, data, content_type='text/plain'):
                barrier.wait(timeout=5)
                return super().create_text(uri, data, content_type)
        store = PortfolioEventJournal(self.local, offline_root=self.root, object_store=SynchronizedCreate())
        candidates = [record(first, event_id='left'), record(first, event_id='right')]
        def append(value):
            try: return store.append(value)['status']
            except ValueError: return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(append, candidates))
        self.assertEqual(sorted(results), ['conflict', 'created'])
        self.assertEqual(len(self.store.load_chain(IDENTITY)), 2)

    def test_false_create_receipt_requires_matching_readback(self):
        class FalseReceipt(LocalObjectStore):
            def create_text(self, uri, data, content_type='text/plain'):
                super().create_text(uri, data, content_type)
                return False
        store = PortfolioEventJournal(self.local, offline_root=self.root, object_store=FalseReceipt())
        first = record()
        self.assertEqual(store.append(first)['status'], 'already_appended')
        self.assertEqual(store.load_chain(IDENTITY), [first])

    def test_false_receipt_without_write_and_lost_acknowledgement(self):
        class MissingWrite(LocalObjectStore):
            def create_text(self, uri, data, content_type='text/plain'): return False
        missing = PortfolioEventJournal(self.local, offline_root=self.root, object_store=MissingWrite())
        with self.assertRaises(ValueError): missing.append(record())
        class LostAcknowledgement(LocalObjectStore):
            calls = 0
            def create_text(self, uri, data, content_type='text/plain'):
                self.calls += 1
                super().create_text(uri, data, content_type)
                raise OSError('artificial lost acknowledgement')
        backend = LostAcknowledgement()
        lost = PortfolioEventJournal(self.local, offline_root=self.root, object_store=backend)
        first = record()
        with self.assertRaises(OSError): lost.append(first)
        self.assertEqual(backend.calls, 1)
        self.assertEqual(lost.load_chain(IDENTITY), [first])

    def test_partial_create_is_never_overwritten_or_treated_empty(self):
        class PartialWrite(LocalObjectStore):
            calls = 0
            def create_text(self, uri, data, content_type='text/plain'):
                self.calls += 1
                super().create_text(uri, '{', content_type)
                raise OSError('artificial interrupted create')
        backend = PartialWrite()
        store = PortfolioEventJournal(self.local, offline_root=self.root, object_store=backend)
        with self.assertRaises(OSError): store.append(record())
        with self.assertRaises(ValueError): store.load_chain(IDENTITY)
        with self.assertRaises(ValueError): store.append(record())
        self.assertEqual(backend.calls, 1)
        self.assertEqual(Path(self.store._root_location(IDENTITY)).read_text(), '{')

    def test_orphan_successor_and_unknown_temporary_file_are_errors(self):
        directory = self.chain_dir() / 'successors'; directory.mkdir(parents=True)
        orphan = directory / ('f' * 64 + '.json'); orphan.write_text(json.dumps(record()))
        with self.assertRaises(ValueError): self.store.load_chain(IDENTITY)
        orphan.unlink(); self.store.append(record())
        (directory / 'uncommitted.tmp').write_text('pending')
        with self.assertRaises(ValueError): self.store.load_chain(IDENTITY)
        with self.assertRaises(ValueError): self.store.append(record())

    def test_hash_forgery_duplicate_keys_and_bad_path_address(self):
        first = record(); self.store.append(first)
        root = Path(self.store._root_location(IDENTITY))
        forged = {**first, 'state': {'forged': True}}
        root.write_text(json.dumps(forged))
        with self.assertRaises(ValueError): self.store.load_chain(IDENTITY)
        root.write_text(json.dumps(first)[:-1] + ',"sequence":0}')
        with self.assertRaises(ValueError): self.store.load_chain(IDENTITY)
        root.write_text(json.dumps(first)); successor = self.chain_dir() / 'successors'
        successor.mkdir(parents=True); (successor / ('f' * 64 + '.json')).write_text(json.dumps(record(first)))
        with self.assertRaises(ValueError): self.store.load_chain(IDENTITY)

    def test_outside_paths_cloud_and_nonlocal_backend_rejected(self):
        for path in ('gs://bucket/journal', 'file:///tmp/journal', self.root.parent / 'outside', self.root):
            with self.subTest(path=path), self.assertRaises(ValueError):
                PortfolioEventJournal(path, offline_root=self.root)
        with self.assertRaises(ValueError): PortfolioEventJournal(self.local, offline_root=self.root, object_store=object())

    def test_file_instead_of_parent_directory_never_means_empty(self):
        self.local.write_text('not a journal directory')
        with self.assertRaises(ValueError): self.store.load_chain(IDENTITY)
        with self.assertRaises(ValueError): PortfolioEventJournal(self.local, offline_root=self.root)

    def test_dangling_successor_symlink_is_not_ignored(self):
        first = record(); self.store.append(first)
        successors = self.chain_dir() / 'successors'; successors.mkdir(parents=True)
        (successors / (first['record_sha256'] + '.json')).symlink_to(self.root / 'missing.json')
        with self.assertRaises(ValueError): self.store.load_chain(IDENTITY)
        with self.assertRaises(ValueError): self.store.append(record(first))

    def test_directory_and_single_file_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            self.local.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError): self.store.load_chain(IDENTITY)
            self.local.unlink(); first = record(); self.store.append(first)
            root = Path(self.store._root_location(IDENTITY)); root.unlink()
            target = Path(outside) / 'private.json'; target.write_text(json.dumps(first))
            root.symlink_to(target)
            with self.assertRaises(ValueError): self.store.load_chain(IDENTITY)
            with self.assertRaises(ValueError): self.store.append(first)

    def test_terminal_is_enforced_on_disk(self):
        first = record(); last = record(first, kind='MIGRATE_OUT')
        self.store.append(first); self.store.append(last)
        fabricated = resigned({**last, 'kind': 'EVENT', 'sequence': 2, 'event_id': 'later',
            'previous_record_sha256': last['record_sha256'], 'provenance': None})
        with self.assertRaises(ValueError): self.store.append(fabricated)
        self.assertEqual(self.store.load_chain(IDENTITY), [first, last])


if __name__ == '__main__': unittest.main()
