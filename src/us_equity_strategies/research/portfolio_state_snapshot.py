"""Immutable artificial state snapshots and recovery into unused local directories.

This copies an explicitly checkpointed history. It does not retire its writer,
issue a trusted checkpoint, repair damaged files or certify power-loss recovery.
"""
from copy import deepcopy
import json
from pathlib import Path
import re

from quant_platform_kit.common.strategy_risk_state import (
    StrategyRiskStateIdentity, StrategyRiskStateTransition,
)

from .auto_portfolio_account import digest, utc
from .portfolio_event_journal import PortfolioEventJournal, _json, _unique_object
from .portfolio_intraday_state_io import _binding as _intraday_binding, _verify_chain, read_portfolio_event_state
from .portfolio_risk_events_preview import _require
from .portfolio_risk_state_io import (
    _binding as _daily_binding, _safe_chain, _safe_store, read_portfolio_risk_state,
)

VERSION = 'qsl.portfolio-state-snapshot.v1'
NAMESPACE = 'portfolio_state_snapshots_v1'
FIELDS = {'schema_version','kind','account','identity','expected_head_sha256','chains','snapshot_sha256'}


def _output():
    return dict(status='STATE_SNAPSHOT_GAPS',gaps=[],snapshot_sha256=None,
        snapshot_persisted=False,readback_verified=False,state_recovered=False,
        state_readback=None,breaker_state='UNKNOWN',new_risk_prohibited=True,
        source_retired=None,execution_authorized=False,settings_applied=False,breaker_reset=False,
        account_policy_bound=False,financial_risk_certified=False,
        limitations=['ARTIFICIAL_CHECKPOINT_SOURCE_ONLY','SOURCE_WRITER_NOT_RETIRED',
            'EMPTY_RECOVERY_DIRECTORY_REQUIRED','NO_POWER_LOSS_OR_REMOTE_STORAGE_CERTIFICATION'])


def _error(out, exc):
    code = str(exc)
    out['gaps'] = [code if code.startswith(('SNAPSHOT_','STATE_','JOURNAL_','INTRADAY_','EVENT_'))
        else 'SNAPSHOT_INPUT_OR_STORAGE_FAILED']
    out['error_type'] = type(exc).__name__
    return out


def _account(contract):
    _require(contract.get('synthetic') is True, 'SNAPSHOT_ARTIFICIAL_ACCOUNT_REQUIRED')
    return {key:contract[key] for key in ('account_id','scope_ref','currency')}


def _storage(snapshot_dir, offline_root):
    # Reuse the existing approved-local path guard and LocalObjectStore provider;
    # no event journal records are created in this separate snapshot directory.
    return PortfolioEventJournal(snapshot_dir,offline_root=offline_root)


def _path(storage, snapshot_sha256):
    _require(type(snapshot_sha256) is str and re.fullmatch('[0-9a-f]{64}',snapshot_sha256),
        'SNAPSHOT_EXPLICIT_DIGEST_REQUIRED')
    path = storage.local_dir/NAMESPACE/(snapshot_sha256+'.json')
    storage._safe_path(path)
    return path


def _separate(storage, state_dir):
    first,second = storage.local_dir.resolve(),Path(state_dir).resolve()
    _require(not first.is_relative_to(second) and not second.is_relative_to(first),
        'SNAPSHOT_STATE_AND_ARCHIVE_DIRECTORIES_OVERLAP')


class _FrozenChains:
    def __init__(self, chains):
        self.chains = {digest(chain[0]['identity']):chain for chain in chains}

    def load_chain(self, identity):
        return deepcopy(self.chains.get(digest(identity),[]))


def _validate(snapshot, kind, binding, contract):
    (_intraday_binding if kind == 'INTRADAY' else _daily_binding)(binding,contract)
    _require(type(snapshot) is dict and set(snapshot) == FIELDS, 'SNAPSHOT_FIELDS_INVALID')
    _json(snapshot)
    _require(snapshot['schema_version'] == VERSION and snapshot['kind'] == kind,
        'SNAPSHOT_VERSION_OR_KIND_INVALID')
    _require(snapshot['snapshot_sha256'] == digest({k:v for k,v in snapshot.items() if k != 'snapshot_sha256'}),
        'SNAPSHOT_CONTENT_DIGEST_MISMATCH')
    _require(snapshot['account'] == _account(contract) and snapshot['identity'] == binding['identity']
        and snapshot['expected_head_sha256'] == binding['expected_head_sha256'], 'SNAPSHOT_CHECKPOINT_MISMATCH')
    chains = snapshot['chains']
    _require(type(chains) is list and bool(chains) and all(type(c) is list and bool(c) for c in chains),
        'SNAPSHOT_COMPLETE_HISTORY_REQUIRED')
    if kind == 'DAILY':
        _require(len(chains) == 1, 'SNAPSHOT_DAILY_HISTORY_INVALID')
        identity = StrategyRiskStateIdentity.from_dict(binding['identity'])
        transitions = tuple(StrategyRiskStateTransition.from_dict(x) for x in chains[0])
        _require([x.to_dict() for x in transitions] == chains[0], 'SNAPSHOT_DAILY_RECORD_NOT_CANONICAL')
        _safe_chain(transitions,identity)
        head_hash = chains[0][-1]['transition_sha256']
        _require(chains[0][-1]['effective_session'] <= utc(binding['observed_at']).date().isoformat(),
            'SNAPSHOT_FUTURE_STATE')
    else:
        identities = [digest(c[0]['identity']) for c in chains]
        _require(len(set(identities)) == len(identities), 'SNAPSHOT_DUPLICATE_IDENTITY')
        _require(chains[0][0]['kind'] == 'INITIALIZE', 'SNAPSHOT_ORIGIN_HISTORY_MISSING')
        for previous,current in zip(chains,chains[1:]):
            root = current[0]
            _require(root['kind'] == 'MIGRATE_IN'
                and root['provenance']['migration']['source_identity'] == previous[0]['identity'],
                'SNAPSHOT_ANCESTRY_ORDER_INVALID')
        _verify_chain(_FrozenChains(chains),chains[-1],contract)
        head_hash = chains[-1][-1]['record_sha256']
        _require(utc(chains[-1][-1]['observed_at']) <= utc(binding['observed_at']), 'SNAPSHOT_FUTURE_STATE')
    _require(chains[-1][0]['identity'] == binding['identity'] and head_hash == binding['expected_head_sha256'],
        'SNAPSHOT_HISTORY_HEAD_MISMATCH')
    return chains


def _read_snapshot(storage, expected_sha256, kind, binding, contract):
    raw = storage.object_store.read_text(str(_path(storage,expected_sha256)))
    snapshot = json.loads(raw,object_pairs_hook=_unique_object)
    _validate(snapshot,kind,binding,contract)
    _require(snapshot['snapshot_sha256'] == expected_sha256, 'SNAPSHOT_EXPECTED_DIGEST_MISMATCH')
    return snapshot


def _read_state(kind, store, binding, contract, offline_root):
    if kind == 'INTRADAY':
        return read_portfolio_event_state(journal=store,binding=binding,account_contract=contract)
    return read_portfolio_risk_state(store=store,binding=binding,account_contract=contract,offline_root=offline_root)


def _save(kind, store, binding, contract, snapshot_dir, offline_root):
    out = _output()
    try:
        storage = _storage(snapshot_dir,offline_root)
        _separate(storage,store.local_dir)
        _storage(store.local_dir,offline_root)
        read = _read_state(kind,store,binding,contract,offline_root)
        _require(read['readback_verified'], (read['gaps'] or ['SNAPSHOT_SOURCE_NOT_VERIFIED'])[0])
        chains = [read['chain']]
        if kind == 'INTRADAY':
            seen = {digest(binding['identity'])}
            while chains[0][0]['kind'] == 'MIGRATE_IN':
                identity = chains[0][0]['provenance']['migration']['source_identity']
                _require(digest(identity) not in seen, 'SNAPSHOT_ANCESTRY_CYCLE')
                seen.add(digest(identity));chains.insert(0,store.load_chain(identity))
                _require(bool(chains[0]), 'SNAPSHOT_ORIGIN_HISTORY_MISSING')
        snapshot = dict(schema_version=VERSION,kind=kind,account=_account(contract),
            identity=deepcopy(binding['identity']),expected_head_sha256=binding['expected_head_sha256'],chains=chains)
        snapshot['snapshot_sha256'] = digest(snapshot)
        _validate(snapshot,kind,binding,contract)
        # Checkpoint verification is repeated before the single snapshot write.
        # This is an as-of copy, not a lock or a promise that its writer stopped.
        second = _read_state(kind,store,binding,contract,offline_root)
        _require(second['readback_verified'] and second['chain'] == read['chain'], 'SNAPSHOT_SOURCE_CHANGED')
        sha = snapshot['snapshot_sha256'];out['snapshot_sha256'] = sha
        try:
            receipt = storage.object_store.create_text(str(_path(storage,sha)),
                json.dumps(snapshot,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False),
                content_type='application/json')
            _require(type(receipt) is bool, 'SNAPSHOT_CREATE_RECEIPT_INVALID')
        except Exception as exc:
            out['append_error_type'] = type(exc).__name__
        recovered = _read_snapshot(storage,sha,kind,binding,contract)
        _require(recovered == snapshot, 'SNAPSHOT_READBACK_MISMATCH')
        out.update(status='STATE_SNAPSHOT_SAVED',snapshot_persisted=True,readback_verified=True,
            breaker_state=read['breaker_state'])
    except Exception as exc:
        _error(out,exc)
    return out


def _restore(kind, store, binding, contract, snapshot_dir, expected_sha256, offline_root):
    out = _output();out['snapshot_sha256'] = expected_sha256
    try:
        storage = _storage(snapshot_dir,offline_root)
        _separate(storage,store.local_dir)
        _storage(store.local_dir,offline_root)
        snapshot = _read_snapshot(storage,expected_sha256,kind,binding,contract)
        if kind == 'INTRADAY':
            _require(isinstance(store,PortfolioEventJournal), 'SNAPSHOT_LOCAL_JOURNAL_REQUIRED')
            store._safe_path(store.local_dir)
        else:
            _safe_store(store,offline_root,StrategyRiskStateIdentity.from_dict(binding['identity']))
        # The whole history and fresh caller checkpoint have been checked in
        # memory before any target writes. A backup cannot authorize rollback.
        local = Path(store.local_dir)
        _require(not local.exists() or (local.is_dir() and not any(local.iterdir())),
            'SNAPSHOT_RECOVERY_DIRECTORY_NOT_EMPTY')
        out['snapshot_persisted'] = True
        for chain in snapshot['chains']:
            for record in chain:
                try:
                    store.append(record if kind == 'INTRADAY' else StrategyRiskStateTransition.from_dict(record))
                except Exception as exc:
                    # Unknown writes are reconciled once, never blindly retried.
                    out['append_error_type'] = type(exc).__name__
                    actual = store.load_chain(record['identity'])
                    wires = actual if kind == 'INTRADAY' else [x.to_dict() for x in actual]
                    _require(wires == chain[:chain.index(record)+1], 'SNAPSHOT_RESTORE_WRITE_UNCONFIRMED')
        read = _read_state(kind,store,binding,contract,offline_root)
        out['state_readback'] = read
        _require(read['readback_verified'], (read['gaps'] or ['SNAPSHOT_RESTORED_STATE_UNVERIFIED'])[0])
        for chain in snapshot['chains']:
            actual = store.load_chain(chain[0]['identity'])
            wires = actual if kind == 'INTRADAY' else [x.to_dict() for x in actual]
            _require(wires == chain, 'SNAPSHOT_RECOVERY_CONTENT_MISMATCH')
        out.update(status='STATE_SNAPSHOT_RESTORED',state_recovered=True,readback_verified=True,
            breaker_state=read['breaker_state'])
        # Recovery yields retained state only, never a configuration or permit.
    except Exception as exc:
        _error(out,exc)
    return out


def save_intraday_state_snapshot(*, journal, binding, account_contract, snapshot_dir, offline_root):
    return _save('INTRADAY',journal,binding,account_contract,snapshot_dir,offline_root)


def restore_intraday_state_snapshot(*, journal, binding, account_contract, snapshot_dir, expected_sha256, offline_root):
    return _restore('INTRADAY',journal,binding,account_contract,snapshot_dir,expected_sha256,offline_root)


def save_daily_state_snapshot(*, store, binding, account_contract, snapshot_dir, offline_root):
    return _save('DAILY',store,binding,account_contract,snapshot_dir,offline_root)


def restore_daily_state_snapshot(*, store, binding, account_contract, snapshot_dir, expected_sha256, offline_root):
    return _restore('DAILY',store,binding,account_contract,snapshot_dir,expected_sha256,offline_root)
