"""Offline event-state IO using QPK's existing create-only chain store.

Only explicitly scoped local artificial storage is accepted. No configuration
apply, broker, cloud backend, state reset, mutable latest file or new database.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import re

from quant_platform_kit.common.strategy_risk_state import (
    DEFAULT_STRATEGY_RISK_STATE_NAMESPACE, StrategyRiskStateIdentity,
    StrategyRiskStateStore, StrategyRiskStateTransition,
    build_strategy_risk_state_transition, validate_strategy_risk_state_chain,
)
from .auto_portfolio_account import digest,utc
from .auto_portfolio_preview import _hash
from .funded_portfolio_preview import _bound
from .portfolio_risk_events_preview import (
    _require, _tighten, _portfolio_id, preview_portfolio_risk_settings, validate_portfolio_event_state,
)

BINDING_VERSION = 'qsl.portfolio-event-state-binding.v1'


def _safe_store(store, offline_root, identity):
    _require(isinstance(store,StrategyRiskStateStore) and store.local_dir is not None
        and not store.cloud_prefix_uri and store.object_store is None
        and store.namespace == DEFAULT_STRATEGY_RISK_STATE_NAMESPACE, 'STATE_LOCAL_OFFLINE_STORE_REQUIRED')
    root = Path(offline_root).resolve(strict=True)
    local = Path(store.local_dir).resolve()
    _require(root.is_dir() and local != root and local.is_relative_to(root), 'STATE_STORE_OUTSIDE_OFFLINE_ROOT')
    # Check the actual existing link resolution before the original store reads
    # its root or any successor. The fixed QPK layout is reused unchanged.
    chain_dir = Path(store._location(identity))
    for path in (chain_dir,chain_dir/'root.json',chain_dir/'successors'):
        _require(path.resolve().is_relative_to(root), 'STATE_STORE_SYMLINK_ESCAPE')
    for path in (chain_dir/'successors').glob('*.json'):
        _require(path.resolve().is_relative_to(root), 'STATE_STORE_SYMLINK_ESCAPE')


def _binding(binding, contract):
    _require(type(binding) is dict and 'chain' not in binding
        and {'schema_version','identity','origin','expected_head_sha256'} <= set(binding), 'STATE_BINDING_INVALID')
    digest(binding)
    _bound(binding,contract)
    _require(binding.get('schema_version') == BINDING_VERSION, 'STATE_BINDING_VERSION_INVALID')
    identity = StrategyRiskStateIdentity.from_dict(binding['identity'])
    expected = StrategyRiskStateIdentity('portfolio_risk_events',contract['scope_ref'],
        identity.candidate_id,identity.config_sha256)
    _require(identity == expected,'STATE_BINDING_IDENTITY_INVALID')
    origin,head = binding.get('origin'),binding.get('expected_head_sha256')
    _require(origin in ('VERIFIED_CHAIN','VERIFIED_CLOSED_GENESIS'),'STATE_BINDING_ORIGIN_REQUIRED')
    _require((origin == 'VERIFIED_CLOSED_GENESIS' and head is None) or
        (origin == 'VERIFIED_CHAIN' and type(head) is str and re.fullmatch('[0-9a-f]{64}',head)),
        'STATE_CHECKPOINT_REQUIRED')
    return identity


def _safe_chain(chain, identity):
    validate_strategy_risk_state_chain(chain)
    previous = None
    for item in chain:
        _require(item.identity == identity,'STATE_CHAIN_IDENTITY_MISMATCH')
        state = item.state
        validate_portfolio_event_state(state)
        if state['breaker_open']:
            _require(state['trigger_session'] <= item.effective_session,'STATE_TRIGGER_SESSION_INVALID')
        if previous is not None:
            old = previous.state
            _require(not old['breaker_open'] or state['breaker_open'],'STATE_AUTOMATIC_BREAKER_CLEAR_FORBIDDEN')
            _require(state['nav_peak'] >= old['nav_peak'],'STATE_HIGH_WATERMARK_REGRESSION')
            if old['breaker_open']:
                _require(state['original_daily_trigger'] == old['original_daily_trigger']
                    and state['trigger_session'] == old['trigger_session'], 'STATE_LATCHED_TRIGGER_CHANGED')
                _require(_tighten(old['latched_limits'],state['latched_limits']) == state['latched_limits'],
                    'STATE_LATCHED_LIMIT_LOOSENED')
            _require(set(old['sleeves']) <= set(state['sleeves']),'STATE_RETAINED_SLEEVE_MISSING')
            for cycle,row in old['sleeves'].items():
                new = state['sleeves'][cycle]
                _require(new['entry'] == row['entry'] and new['strategy_id'] == row['strategy_id'],
                    'STATE_ENTRY_BASIS_CHANGED')
                _require(new['peak'] >= row['peak'] and new['stop_limit'] <= row['stop_limit']
                    and new['trailing_limit'] <= row['trailing_limit'], 'STATE_SLEEVE_PROTECTION_LOOSENED')
        previous = item


def _wire(binding, chain):
    value = deepcopy(binding);value.pop('schema_version')
    value.update(chain=[x.to_dict() for x in chain],
        expected_head_sha256=chain[-1].transition_sha256 if chain else None,
        origin='VERIFIED_CHAIN' if chain else 'VERIFIED_CLOSED_GENESIS')
    return value


def read_portfolio_risk_state(*, store, binding, account_contract, offline_root):
    """Read one trusted identity/checkpoint without inferring CLOSED from absence.

    The caller must obtain the binding from its trusted state/checkpoint source.
    A hash verifies content continuity, not the authenticity of that source.
    """
    out = dict(status='STATE_READBACK_GAPS',gaps=[],chain=[],state_readback=None,
        observed_head_sha256=None,breaker_state='UNKNOWN',new_risk_prohibited=True,
        readback_verified=False,execution_authorized=False,settings_applied=False,breaker_reset=False)
    try:
        identity = _binding(binding,account_contract)
        _safe_store(store,offline_root,identity)
        chain = store.load_chain(identity)
        _safe_chain(chain,identity)
        out['chain'] = [x.to_dict() for x in chain]
        head = chain[-1] if chain else None
        out['observed_head_sha256'] = head.transition_sha256 if head else None
        if not chain:
            _require(binding['origin'] == 'VERIFIED_CLOSED_GENESIS','STATE_EXPECTED_CHAIN_MISSING')
            successors=Path(store._location(identity))/'successors'
            _require(not any(successors.glob('*.json')),'STATE_ORPHAN_SUCCESSORS_REQUIRE_RECOVERY')
            out.update(status='STATE_STORE_EMPTY_VERIFIED_GENESIS',state_readback=_wire(binding,chain))
            # Empty is not an observed CLOSED state or permission to add risk.
            return out
        _require(head.effective_session <= utc(binding['observed_at']).date().isoformat(),
            'STATE_FUTURE_EFFECTIVE_SESSION')
        _require(binding['origin'] != 'VERIFIED_CLOSED_GENESIS','STATE_GENESIS_STORE_NOT_EMPTY')
        _require(binding['expected_head_sha256'] == head.transition_sha256,'STATE_HEAD_CHECKPOINT_MISMATCH')
        out.update(status='STATE_READ_BACK',state_readback=_wire(binding,chain),
            breaker_state='OPEN' if head.state['breaker_open'] else 'CLOSED',
            new_risk_prohibited=head.state['breaker_open'],readback_verified=True)
    except Exception as exc:
        text = str(exc)
        out['gaps'] = [text if text.startswith('STATE_') or text.startswith('EVENT_') else 'STATE_READBACK_FAILED']
        out['error_type'] = type(exc).__name__
    return out


def _fingerprint(request, context, packet):
    _require(type(context) is dict and 'state_readback' not in context,'STATE_INLINE_READBACK_FORBIDDEN')
    content = {k:v for k,v in packet.items() if k != 'definition_bytes'}
    packet_hash = _hash(content)
    _require(packet_hash is not None,'STATE_FROZEN_PACKET_UNBOUND')
    return digest(dict(request=request,event_context=context,packet_sha256=packet_hash,
        definition_sha256=sha256(packet['definition_bytes']).hexdigest()))


def persist_portfolio_risk_preview(request, *, store, binding, event_context, offline_root, **packet):
    """Read → existing pure preview → append once → verify complete readback.

    An identical frozen delivery is idempotent. Same-session changed input is
    refused, following QPK's session-granularity contract. A repeated delivery
    returns recovered state only, never a cached configuration draft.
    """
    out = dict(contract_version='qsl.portfolio-risk-state-io.v1',status='STATE_IO_GAPS',gaps=[],
        preview=None,draft_settings=None,state_readback=None,next_binding=None,append_receipt=None,
        state_persisted=False,state_recovered=False,readback_verified=False,
        new_risk_prohibited=True,write_outcome='NOT_ATTEMPTED',
        storage_scope='OFFLINE_ARTIFICIAL_LOCAL_STORE',consumer_state_persisted=False,
        execution_authorized=False,settings_applied=False,breaker_reset=False,
        account_policy_bound=False,financial_risk_certified=False,
        limitations=['NO_AUTOMATIC_RESET','ONE_TRANSITION_PER_EFFECTIVE_SESSION',
            'TRUSTED_CHECKPOINT_SOURCE_REQUIRED','PROCESS_RESTART_NOT_POWER_LOSS_CERTIFICATION'])
    try:
        fingerprint = _fingerprint(request,event_context,packet)
        read = read_portfolio_risk_state(store=store,binding=binding,
            account_contract=packet['account_contract'],offline_root=offline_root)
        out['state_readback'] = read
        out['state_recovered'] = read['readback_verified']
        chain = tuple(StrategyRiskStateTransition.from_dict(x) for x in read['chain'])
        head = chain[-1] if chain else None
        session = event_context['observations']['session']
        if type(event_context.get('policy')) is dict:
            expected = StrategyRiskStateIdentity('portfolio_risk_events',packet['account_contract']['scope_ref'],
                _portfolio_id(packet),digest(event_context['policy']))
            _require(expected.to_dict() == binding['identity'],'STATE_CURRENT_POLICY_OR_PORTFOLIO_MISMATCH')
        # Handles a lost acknowledgement using a single read, even when the
        # checkpoint still names our predecessor. No append is retried here.
        replay = head is not None and head.effective_session == session and head.input_sha256 == fingerprint
        replay_gaps = {'STATE_HEAD_CHECKPOINT_MISMATCH','STATE_GENESIS_STORE_NOT_EMPTY'}
        if replay and set(read['gaps']) <= replay_gaps and binding['expected_head_sha256'] in (
                head.transition_sha256,head.previous_transition_sha256):
            checkpoint = deepcopy(binding);checkpoint.update(origin='VERIFIED_CHAIN',expected_head_sha256=head.transition_sha256)
            verified = read_portfolio_risk_state(store=store,binding=checkpoint,
                account_contract=packet['account_contract'],offline_root=offline_root)
            _require(verified['readback_verified'],'STATE_REPLAY_READBACK_UNCONFIRMED')
            out.update(status='STATE_ALREADY_PERSISTED_READ_BACK',state_readback=verified,next_binding=checkpoint,
                state_persisted=True,state_recovered=True,readback_verified=True,
                new_risk_prohibited=verified['new_risk_prohibited'],write_outcome='PREVIOUSLY_COMMITTED_READ_BACK')
            return out
        if read['gaps']:
            out['gaps'] = read['gaps'];return out
        if head is not None:
            _require(session > head.effective_session,'STATE_SAME_SESSION_INPUT_CHANGED_OR_STALE')
        context = deepcopy(event_context);context['state_readback'] = read['state_readback']
        preview = preview_portfolio_risk_settings(request,event_context=context,**packet)
        event = preview.get('requested_events') or {}
        wire = event.get('transition')
        if wire is None:
            out.update(gaps=sorted(set(preview['gaps']+['STATE_NO_VALID_EVENT_TRANSITION'])),preview=preview)
            return out
        raw = StrategyRiskStateTransition.from_dict(wire)
        target = build_strategy_risk_state_transition(identity=raw.identity,effective_session=raw.effective_session,
            input_sha256=fingerprint,state=raw.state,previous_transition=head)
        _safe_chain((*chain,target),raw.identity)
        _safe_store(store,offline_root,raw.identity)
        error_type = None
        try:
            out['write_outcome'] = 'UNCONFIRMED'
            receipt = store.append(target)
            out['append_receipt'] = receipt.to_dict()
        except Exception as exc:
            error_type = type(exc).__name__
        # Always confirm through the existing store, including a thrown/lost
        # append response. This is read-only reconciliation, not a write retry.
        checkpoint = deepcopy(binding);checkpoint.update(origin='VERIFIED_CHAIN',expected_head_sha256=target.transition_sha256)
        post = read_portfolio_risk_state(store=store,binding=checkpoint,
            account_contract=packet['account_contract'],offline_root=offline_root)
        out['state_readback'] = post
        out['state_persisted'] = any(x['transition_sha256'] == target.transition_sha256 for x in post['chain'])
        if not post['readback_verified']:
            out['gaps'] = sorted(set(post['gaps']+['STATE_APPEND_READBACK_UNCONFIRMED']))
            if error_type:out['append_error_type'] = error_type
            return out
        out.update(status='STATE_PERSISTED_PREVIEW_READY' if preview['draft_settings'] is not None else 'STATE_PERSISTED_PREVIEW_HAS_GAPS',
            gaps=preview['gaps'],preview=preview,draft_settings=deepcopy(preview['draft_settings']),
            next_binding=checkpoint,state_persisted=True,readback_verified=True,
            new_risk_prohibited=post['new_risk_prohibited'] or bool(preview['gaps']),
            write_outcome='COMMITTED_READ_BACK_AFTER_APPEND_ERROR' if error_type else 'COMMITTED_READ_BACK')
        if error_type:out['append_error_type'] = error_type
    except Exception as exc:
        text = str(exc)
        out['gaps'] = [text if text.startswith(('STATE_','EVENT_')) else 'STATE_IO_INPUT_OR_STORAGE_FAILED']
        out['error_type'] = type(exc).__name__
    return out
