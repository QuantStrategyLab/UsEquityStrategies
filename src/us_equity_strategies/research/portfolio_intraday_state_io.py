"""Artificial intraday state, explicit initialization and sealed handoff.

This consumes source attestations; it does not issue real authority. Existing
LocalObjectStore create-only operations are the only persistence mechanism.
No configuration application, account access, reset or legacy-store mutation.
"""
from copy import deepcopy
import re

from quant_platform_kit.common.strategy_risk_state import StrategyRiskStateIdentity

from .auto_portfolio_account import digest, utc
from .funded_portfolio_preview import _bound
from .portfolio_event_journal import PortfolioEventJournal, build_event_record, validate_event_chain
from .portfolio_risk_events_preview import (
    _portfolio_id, _require, preview_portfolio_risk_settings, validate_portfolio_intraday_state,
)
from .portfolio_risk_state_io import _fingerprint, read_portfolio_risk_state

BINDING_VERSION = 'qsl.portfolio-intraday-binding.v1'
INITIALIZATION_VERSION = 'qsl.portfolio-initial-state.v1'
MIGRATION_VERSION = 'qsl.portfolio-state-migration.v1'
HEADER = frozenset(('synthetic','account_id','scope_ref','currency','source','source_version',
    'observed_at','available_at','max_age_seconds','valid_from','valid_until'))


def _output():
    return dict(status='INTRADAY_STATE_GAPS',gaps=[],chain=[],preview=None,draft_settings=None,
        state_readback=None,next_binding=None,append_receipt=None,readback_verified=False,
        state_persisted=False,new_risk_prohibited=True,breaker_state='UNKNOWN',
        execution_authorized=False,settings_applied=False,breaker_reset=False,
        account_policy_bound=False,financial_risk_certified=False,
        authority_classification='ARTIFICIAL_SOURCE_ATTESTATIONS_ONLY',
        limitations=['REAL_CHECKPOINT_ISSUER_NOT_VERIFIED','NO_AUTOMATIC_RESET',
            'LOCAL_PROCESS_RESTART_NOT_POWER_LOSS_CERTIFICATION'])


def _error(out, exc):
    code = str(exc)
    out['gaps'] = [code if code.startswith(('INTRADAY_','JOURNAL_','STATE_','EVENT_')) else 'INTRADAY_INPUT_OR_STORAGE_FAILED']
    out['error_type'] = type(exc).__name__
    return out


def _identity(value, contract):
    identity = StrategyRiskStateIdentity.from_dict(value)
    expected = StrategyRiskStateIdentity('portfolio_risk_events',contract['scope_ref'],
        identity.candidate_id,identity.config_sha256)
    _require(identity == expected and value == identity.to_dict(), 'INTRADAY_IDENTITY_INVALID')
    return identity.to_dict()


def _binding(binding, contract, *, empty=False):
    _require(type(binding) is dict and set(binding) == HEADER | {
        'schema_version','identity','expected_head_sha256'}, 'INTRADAY_BINDING_INVALID')
    digest(binding)
    _bound(binding,contract)
    _require(binding['schema_version'] == BINDING_VERSION, 'INTRADAY_BINDING_VERSION_INVALID')
    identity = _identity(binding['identity'],contract)
    head = binding['expected_head_sha256']
    _require(head is None if empty else type(head) is str and re.fullmatch('[0-9a-f]{64}',head),
        'INTRADAY_EXPLICIT_CHECKPOINT_REQUIRED')
    return identity


def _proof(proof, contract, version, fields):
    _require(type(proof) is dict and set(proof) == HEADER | {'schema_version','provenance_ref'} | set(fields),
        'INTRADAY_PROVENANCE_INVALID')
    digest(proof)
    _bound(proof,contract)
    # Use the same admission basis when writing and on historical readback.
    # A future-effective attestation must be reissued when effective; do not
    # commit a root/seal whose original availability-time proof cannot verify.
    if utc(contract['cutoff_utc']) != utc(proof['available_at']):
        _bound(proof,dict(contract,cutoff_utc=proof['available_at']))
    _require(proof['schema_version'] == version and type(proof['provenance_ref']) is str
        and proof['provenance_ref'].strip(), 'INTRADAY_PROVENANCE_REQUIRED')


def _historical_contract(proof, contract):
    # Historical attestations must have been effective when available. A fresh
    # caller checkpoint is still required now; old evidence is not reissued.
    _require(utc(proof['available_at']) <= utc(contract['cutoff_utc']), 'INTRADAY_FUTURE_PROVENANCE')
    return dict(contract,cutoff_utc=proof['available_at'])


def _initial_record(proof, identity, contract):
    _proof(proof,contract,INITIALIZATION_VERSION,('action','identity','state','state_sha256'))
    _require(proof['action'] == 'INITIALIZE_FROZEN_STATE' and proof['identity'] == identity,
        'INTRADAY_INITIAL_STATE_UNBOUND')
    state = proof['state']
    validate_portfolio_intraday_state(None,state,utc(proof['observed_at']).date().isoformat(),'INITIALIZE')
    _require(state['risk_state']['nav_peak'] > 0 and proof['state_sha256'] == digest(state), 'INTRADAY_INITIAL_STATE_UNBOUND')
    return build_event_record(identity=identity,kind='INITIALIZE',event_id='init-'+digest(proof),
        observed_at=proof['observed_at'],effective_session=utc(proof['observed_at']).date().isoformat(),
        input_sha256=digest(proof),state=state,provenance={'initialization':proof})


def _migration_records(proof, source_head, destination, contract):
    _proof(proof,contract,MIGRATION_VERSION,('action','source_identity','destination_identity',
        'source_head_sha256','state_sha256'))
    _require(proof['action'] == 'MIGRATE_FROZEN_STATE' and proof['source_identity'] == source_head['identity']
        and proof['destination_identity'] == destination
        and proof['source_identity'] != destination
        and proof['source_head_sha256'] == source_head['record_sha256']
        and proof['state_sha256'] == digest(source_head['state']), 'INTRADAY_MIGRATION_UNBOUND')
    _identity(proof['source_identity'],contract)
    _identity(destination,contract)
    event = 'handoff-'+digest(proof)
    observed = proof['observed_at']
    session = utc(observed).date().isoformat()
    outgoing = build_event_record(identity=source_head['identity'],kind='MIGRATE_OUT',event_id=event,
        observed_at=observed,effective_session=session,input_sha256=digest(proof),
        state=source_head['state'],previous=source_head,provenance={'migration':proof})
    incoming = build_event_record(identity=destination,kind='MIGRATE_IN',event_id=event,
        observed_at=observed,effective_session=session,
        input_sha256=digest(dict(migration=digest(proof),source_terminal_sha256=outgoing['record_sha256'])),
        state=source_head['state'],provenance={'migration':proof,'source_terminal_sha256':outgoing['record_sha256']})
    return outgoing,incoming


def _verify_chain(journal, chain, contract, visited=None):
    validate_event_chain(chain)
    _require(bool(chain), 'INTRADAY_INITIAL_STATE_MISSING')
    root = chain[0]
    _identity(root['identity'],contract)
    visited = set() if visited is None else set(visited)
    key = digest(root['identity'])
    _require(key not in visited, 'INTRADAY_MIGRATION_CYCLE')
    visited.add(key)
    previous = None
    for record in chain:
        validate_portfolio_intraday_state(previous,record['state'],record['effective_session'],record['kind'])
        previous = record['state']
    if root['kind'] == 'INITIALIZE':
        proof = root['provenance']['initialization']
        expected = _initial_record(proof,root['identity'],_historical_contract(proof,contract))
        _require(root == expected, 'INTRADAY_INITIAL_PROVENANCE_MISMATCH')
    else:
        proof = root['provenance']['migration']
        source = journal.load_chain(proof['source_identity'])
        _verify_chain(journal,source,contract,visited)
        _require(len(source) >= 2 and source[-1]['kind'] == 'MIGRATE_OUT', 'INTRADAY_SOURCE_NOT_SEALED')
        outgoing,incoming = _migration_records(proof,source[-2],root['identity'],_historical_contract(proof,contract))
        _require(source[-1] == outgoing and root == incoming, 'INTRADAY_MIGRATION_CONTINUITY_INVALID')
    if chain[-1]['kind'] == 'MIGRATE_OUT':
        _require(len(chain) >= 2, 'INTRADAY_MIGRATION_CONTINUITY_INVALID')
        proof = chain[-1]['provenance']['migration']
        outgoing,_ = _migration_records(proof,chain[-2],proof['destination_identity'],_historical_contract(proof,contract))
        _require(chain[-1] == outgoing, 'INTRADAY_MIGRATION_CONTINUITY_INVALID')


def _checkpoint(binding, record):
    return dict(deepcopy(binding),expected_head_sha256=record['record_sha256'])


def read_portfolio_event_state(*, journal, binding, account_contract):
    """Verify local state and its injected provenance without issuing authority."""
    out = _output()
    try:
        _require(isinstance(journal,PortfolioEventJournal), 'INTRADAY_OFFLINE_JOURNAL_REQUIRED')
        identity = _binding(binding,account_contract)
        chain = journal.load_chain(identity)
        _verify_chain(journal,chain,account_contract)
        out['chain'] = deepcopy(chain)
        head = chain[-1]
        _require(utc(head['observed_at']) <= utc(binding['observed_at']), 'INTRADAY_FUTURE_STATE')
        _require(head['record_sha256'] == binding['expected_head_sha256'], 'INTRADAY_HEAD_CHECKPOINT_MISMATCH')
        retired = head['kind'] == 'MIGRATE_OUT'
        wire = dict(deepcopy(binding),schema_version='qsl.portfolio-journal-readback.v1',
            origin='VERIFIED_EVENT_JOURNAL',chain=deepcopy(chain))
        out.update(status='INTRADAY_IDENTITY_RETIRED' if retired else 'INTRADAY_STATE_READ_BACK',
            state_readback=wire,next_binding=deepcopy(binding),readback_verified=True,
            breaker_state='OPEN' if head['state']['risk_state']['breaker_open'] else 'CLOSED',
            new_risk_prohibited=retired or head['state']['risk_state']['breaker_open'])
    except Exception as exc:
        _error(out,exc)
    return out


def _append_confirm(out, journal, record, binding, contract):
    try:
        out['append_receipt'] = journal.append(record)
    except Exception as exc:
        out['append_error_type'] = type(exc).__name__
    # A single read confirms either a normal append or its lost acknowledgement.
    # No append retry and no file removal, even after an unknown/partial write.
    post = read_portfolio_event_state(journal=journal,binding=_checkpoint(binding,record),account_contract=contract)
    out.update(state_readback=post,state_persisted=any(r['record_sha256'] == record['record_sha256'] for r in post['chain']),
        readback_verified=post['readback_verified'],breaker_state=post['breaker_state'])
    if not post['readback_verified']:
        out['gaps'] = sorted(set(post['gaps']+['INTRADAY_APPEND_UNCONFIRMED']))
        return False
    out.update(next_binding=post['next_binding'],new_risk_prohibited=post['new_risk_prohibited'])
    return True


def initialize_portfolio_event_state(*, journal, binding, account_contract, initialization):
    """Create one explicit artificial initial state; absence never supplies CLOSED."""
    out = _output()
    try:
        identity = _binding(binding,account_contract,empty=True)
        record = _initial_record(initialization,identity,account_contract)
        _require(utc(record['observed_at']) <= utc(binding['observed_at']), 'INTRADAY_FUTURE_STATE')
        existing = journal.load_chain(identity)
        _require(not existing or existing == [record], 'INTRADAY_INITIALIZATION_ALREADY_EXISTS')
        if _append_confirm(out,journal,record,binding,account_contract):
            out['status'] = 'INTRADAY_INITIAL_STATE_PERSISTED'
    except Exception as exc:
        _error(out,exc)
    return out


def persist_portfolio_intraday_preview(request, *, journal, binding, event_id, event_context, **packet):
    """One event → same risk/quantity kernels → append → complete state readback."""
    out = _output()
    try:
        fingerprint = digest(dict(delivery=_fingerprint(request,event_context,packet),event_id=event_id))
        identity = _binding(binding,packet['account_contract'])
        expected = StrategyRiskStateIdentity('portfolio_risk_events',packet['account_contract']['scope_ref'],
            _portfolio_id(packet),digest(event_context['policy'])).to_dict()
        _require(identity == expected, 'INTRADAY_CURRENT_POLICY_OR_PORTFOLIO_MISMATCH')
        read = read_portfolio_event_state(journal=journal,binding=binding,account_contract=packet['account_contract'])
        out['state_readback'] = read
        chain = read['chain']
        head = chain[-1] if chain else None
        if head and head['kind'] == 'EVENT' and head['event_id'] == event_id and head['input_sha256'] == fingerprint:
            _require(set(read['gaps']) <= {'INTRADAY_HEAD_CHECKPOINT_MISMATCH'}
                and binding['expected_head_sha256'] in (head['record_sha256'],head['previous_record_sha256']),
                'INTRADAY_REPLAY_CHECKPOINT_INVALID')
            confirmed = read_portfolio_event_state(journal=journal,binding=_checkpoint(binding,head),
                account_contract=packet['account_contract'])
            _require(confirmed['readback_verified'], 'INTRADAY_REPLAY_UNCONFIRMED')
            out.update(status='INTRADAY_EVENT_ALREADY_PERSISTED',state_readback=confirmed,
                next_binding=confirmed['next_binding'],readback_verified=True,state_persisted=True,
                breaker_state=confirmed['breaker_state'],new_risk_prohibited=confirmed['new_risk_prohibited'])
            return out  # state recovery only, never return an old draft
        if read['gaps']:
            out['gaps'] = read['gaps'];return out
        _require(read['status'] != 'INTRADAY_IDENTITY_RETIRED', 'INTRADAY_SOURCE_IDENTITY_RETIRED')
        _require(not any(r['event_id'] == event_id for r in chain), 'INTRADAY_EVENT_ID_CONFLICT')
        obs = event_context['observations']
        _require(utc(obs['valuation_at']) <= utc(binding['observed_at']), 'INTRADAY_FUTURE_STATE')
        _require(utc(head['observed_at']) <= utc(obs['valuation_at'])
            and head['effective_session'] <= obs['session'], 'INTRADAY_OBSERVATION_STALE')
        context = deepcopy(event_context);context['state_readback'] = read['state_readback']
        preview = preview_portfolio_risk_settings(request,event_context=context,**packet)
        event = preview.get('requested_events') or {}
        _require(event.get('state_update') is not None and event.get('state_identity') == identity,
            'INTRADAY_NO_VALID_EVENT_STATE')
        record = build_event_record(identity=identity,kind='EVENT',event_id=event_id,
            observed_at=obs['valuation_at'],effective_session=obs['session'],input_sha256=fingerprint,
            state=dict(risk_state=event['state_update'],session=obs['session'],
                session_start_nav=obs['session_start_nav'],session_net_external_flow=obs['session_net_external_flow']),
            previous=head)
        validate_portfolio_intraday_state(head['state'],record['state'],record['effective_session'],'EVENT')
        if _append_confirm(out,journal,record,binding,packet['account_contract']):
            out.update(status='INTRADAY_EVENT_PERSISTED',preview=preview,gaps=preview['gaps'],
                draft_settings=deepcopy(preview['draft_settings']),
                new_risk_prohibited=out['new_risk_prohibited'] or bool(preview['gaps']))
    except Exception as exc:
        _error(out,exc)
    return out


def migrate_portfolio_event_state(*, journal, source_binding, destination_binding, account_contract, migration):
    """Seal the source first, then import its exact protective state into a new identity.

    The two create-only writes are intentionally not advertised as a transaction.
    A committed source with no confirmed destination remains retired; an exact
    frozen handoff may resume without writing a second source transition.
    """
    out = _output()
    out.update(source_retired=False,source_next_binding=None,source_write_outcome='NOT_ATTEMPTED')
    try:
        source_id = _binding(source_binding,account_contract)
        target_id = _binding(destination_binding,account_contract,empty=True)
        source = journal.load_chain(source_id)
        _verify_chain(journal,source,account_contract)
        if source[-1]['kind'] == 'MIGRATE_OUT':
            prior = source[-2]
        else:
            prior = source[-1]
        _require(source_binding['expected_head_sha256'] == prior['record_sha256'], 'INTRADAY_SOURCE_CHECKPOINT_MISMATCH')
        outgoing,incoming = _migration_records(migration,prior,target_id,account_contract)
        if source[-1]['kind'] == 'MIGRATE_OUT':
            _require(source[-1] == outgoing, 'INTRADAY_SOURCE_HANDOFF_CONFLICT')
            out.update(source_retired=True,source_next_binding=_checkpoint(source_binding,outgoing),
                source_write_outcome='PREVIOUSLY_SEALED_READ_BACK')
        target = journal.load_chain(target_id)
        _require(not target or target == [incoming], 'INTRADAY_DESTINATION_ALREADY_EXISTS')
        _require(utc(outgoing['observed_at']) <= utc(source_binding['observed_at'])
            and utc(incoming['observed_at']) <= utc(destination_binding['observed_at']), 'INTRADAY_FUTURE_STATE')
        if source[-1]['kind'] != 'MIGRATE_OUT':
            seal = _output()
            out.update(source_retired=None,source_write_outcome='UNCONFIRMED')
            if not _append_confirm(seal,journal,outgoing,source_binding,account_contract):
                out['gaps'] = seal['gaps'];return out
            out['source_write_outcome'] = 'SEALED_READ_BACK'
        out.update(source_retired=True,source_next_binding=_checkpoint(source_binding,outgoing))
        if _append_confirm(out,journal,incoming,destination_binding,account_contract):
            out.update(status='INTRADAY_MIGRATION_PERSISTED',new_risk_prohibited=True)
            # Migration returns state only; the next fresh event must qualify
            # under the destination's actual policy and complete account input.
    except Exception as exc:
        _error(out,exc)
    return out


def prepare_daily_state_import(*, store, binding, account_contract, offline_root):
    """Read the legacy contract; do not pretend its writer can be atomically retired."""
    out = _output()
    source = read_portfolio_risk_state(store=store,binding=binding,
        account_contract=account_contract,offline_root=offline_root)
    out['state_readback'] = source
    if not source['readback_verified']:
        out['gaps'] = source['gaps'] or ['INTRADAY_LEGACY_STATE_UNVERIFIED']
        return out
    head = source['chain'][-1]
    out.update(status='LEGACY_IMPORT_PREPARATION_ONLY',readback_verified=True,
        legacy_candidate=dict(identity=head['identity'],source_head_sha256=head['transition_sha256'],
            state=deepcopy(head['state']),state_sha256=digest(head['state']),intraday_session_basis=None),
        gaps=['INTRADAY_LEGACY_WRITER_RETIREMENT_UNPROVEN','INTRADAY_LEGACY_SESSION_BASIS_UNBOUND'])
    return out
