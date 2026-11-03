"""Create-only offline draft receipts and read-only consumer preflight.

A receipt binds local content and an exact event checkpoint; it does not attest
the real issuer of an account policy or authorize configuration application.
The frozen computation cutoff is never advanced to refresh an old draft.
"""
from collections.abc import Mapping
from copy import deepcopy
import json
import re

from .auto_portfolio_account import adapt_ibkr_account_input, digest, utc
from .funded_portfolio_preview import _bound, _execution_rules, adapt_cash_evidence
from .portfolio_event_journal import PortfolioEventJournal, _json, _unique_object
from .portfolio_intraday_state_io import (
    HEADER, persist_portfolio_intraday_preview, read_portfolio_event_state,
)
from .portfolio_risk_events_preview import _portfolio_id, _require
from .portfolio_risk_state_io import _fingerprint

SCHEMA_VERSION = 'qsl.portfolio-settings-draft.v1'
NAMESPACE = 'portfolio_settings_drafts_v1'
_FIELDS = frozenset(('schema_version','identity','event_id','event_head_sha256',
    'input_sha256','request_sha256','frozen_cutoff_utc','evidence_times','preview',
    'draft_settings','artifact_sha256'))
_TIME_FIELDS = frozenset(('cutoff_utc','observed_at','available_at','max_age_seconds',
    'valid_from','valid_until','training_end','scenario_available_at','valuation_at'))


def _output():
    return dict(status='SETTINGS_DRAFT_GAPS',gaps=[],artifact_receipt=None,
        artifact_readback_verified=False,state_readback=None,next_binding=None,
        state_persisted=False,readback_verified=False,preview=None,draft_settings=None,
        breaker_state='UNKNOWN',new_risk_prohibited=True,application_status='NOT_APPLIED',
        confirmation_granted=False,
        execution_authorized=False,settings_applied=False,breaker_reset=False,
        account_policy_bound=False,financial_risk_certified=False,
        authority_classification='ARTIFICIAL_SOURCE_ATTESTATIONS_ONLY',
        limitations=['EXTERNAL_EXPECTED_RECEIPT_REQUIRED','REAL_POLICY_ISSUER_NOT_VERIFIED',
            'OFFLINE_PREFLIGHT_NOT_RUNTIME_APPLICATION','NO_AUTOMATIC_RESET',
            'NO_NEW_EXPIRY_OR_RISK_DEFAULTS'])


def _error(out, exc):
    code = str(exc)
    out.update(status='SETTINGS_DRAFT_GAPS',preview=None,draft_settings=None,
        artifact_readback_verified=False,new_risk_prohibited=True,
        gaps=[code if code.startswith(('SETTINGS_','INTRADAY_','JOURNAL_')) else 'SETTINGS_DRAFT_INPUT_OR_STORAGE_FAILED'],
        error_type=type(exc).__name__)
    return out


def _location(journal, identity, head):
    _require(isinstance(journal,PortfolioEventJournal), 'SETTINGS_OFFLINE_JOURNAL_REQUIRED')
    _require(type(head) is str and re.fullmatch('[0-9a-f]{64}',head), 'SETTINGS_EVENT_HEAD_REQUIRED')
    path = journal.local_dir / NAMESPACE / digest(identity) / (head+'.json')
    journal._safe_path(path)
    _require(not path.exists() or path.is_file(), 'SETTINGS_ARTIFACT_PATH_INVALID')
    return str(path)


def _times(value, path=''):
    """Retain original source time fields without inventing an artifact TTL."""
    result = {}
    if isinstance(value,Mapping):
        fields = {key:deepcopy(item) for key,item in value.items() if key in _TIME_FIELDS}
        if fields: result[path or '/'] = fields
        for key,item in value.items(): result.update(_times(item,path+'/'+key))
    elif type(value) is list:
        for index,item in enumerate(value): result.update(_times(item,path+'/'+str(index)))
    return result


def _qualified(preview, draft, request, head):
    _require(type(preview) is dict and preview.get('gaps') == []
        and preview.get('status') == 'READY_SYNTHETIC_RISK_SETTINGS_PREVIEW'
        and type(draft) is dict and bool(draft) and preview.get('draft_settings') == draft,
        'SETTINGS_QUALIFIED_DRAFT_REQUIRED')
    for value in (preview,draft):
        _require(value.get('execution_authorized') is False and value.get('settings_applied') is False,
            'SETTINGS_APPLICATION_AUTHORITY_FORBIDDEN')
    _require(preview.get('breaker_reset') is False, 'SETTINGS_APPLICATION_AUTHORITY_FORBIDDEN')
    event = preview['requested_events']
    _require(event['state_update'] == head['state']['risk_state']
        and event['state_identity'] == head['identity'], 'SETTINGS_EVENT_STATE_MISMATCH')
    p4 = preview['p4_preview']
    _require(p4['requested_settings'] == request and p4['application_status'] == 'NOT_APPLIED'
        and p4['execution_authorized'] is False and p4['research_only'] is True and p4['live_ready'] is False
        and p4['effective_parameters_sha256'] is None and p4['effective_observed_at_utc'] is None
        and all(row['effective_value'] is None for row in p4['parameter_comparison'].values()),
        'SETTINGS_EFFECTIVE_CONFIGURATION_FORBIDDEN')
    confirmation = p4['confirmation_binding']
    _require(p4['confirmation_state'] in ('REQUIRED','NOT_REQUIRED')
        and all(confirmation[key] is None for key in ('server_confirmation_ref','confirmed_at_utc','expires_at_utc'))
        and confirmation['requested_parameters_sha256'] == digest(request),
        'SETTINGS_SERVER_CONFIRMATION_FORBIDDEN')
    body = deepcopy(p4)
    for key in ('preview_sha256','server_confirmation_ref','confirmed_at_utc','expires_at_utc'):
        body['confirmation_binding'].pop(key)
    _require(confirmation['preview_sha256'] == digest(body), 'SETTINGS_P4_HASH_MISMATCH')
    if head['state']['risk_state']['breaker_open']:
        _require(event['new_risk_prohibited'] is True
            and preview['constrained_parameters'].get('target_role') == 'APPROVED_EVENT_REDUCE_ONLY_UPPER_BOUND',
            'SETTINGS_OPEN_REDUCTION_CONTRACT_REQUIRED')
        allocation = preview['allocation_preview']
        for row in (allocation.get('requested_preview'),allocation.get('system_recommendation')):
            for scenario in ((row or {}).get('quantity_preview') or {}).get('scenarios',[]):
                _require(not any(t['side'] == 'BUY' and t['quantity'] > 0 for t in scenario['trades']),
                    'SETTINGS_OPEN_DRAFT_WOULD_BUY')


def _fingerprint_event(request, event_context, packet, event_id):
    return digest(dict(delivery=_fingerprint(request,event_context,packet),event_id=event_id))


def _artifact(request, context, packet, preview, draft, head):
    value = dict(schema_version=SCHEMA_VERSION,identity=deepcopy(head['identity']),
        event_id=head['event_id'],event_head_sha256=head['record_sha256'],input_sha256=head['input_sha256'],
        request_sha256=digest(request),frozen_cutoff_utc=packet['account_contract']['cutoff_utc'],
        evidence_times=_times(dict(packet=packet,event_context=context)),
        preview=deepcopy(preview),draft_settings=deepcopy(draft))
    _json(value)
    value['artifact_sha256'] = digest(value)
    return value


def _parse(raw, expected):
    value = json.loads(raw,object_pairs_hook=_unique_object)
    _json(value)
    _require(type(value) is dict and set(value) == _FIELDS and value['schema_version'] == SCHEMA_VERSION,
        'SETTINGS_ARTIFACT_SCHEMA_INVALID')
    _require(type(expected) is str and re.fullmatch('[0-9a-f]{64}',expected),
        'SETTINGS_EXPLICIT_ARTIFACT_RECEIPT_REQUIRED')
    _require(value['artifact_sha256'] == expected
        and digest({key:item for key,item in value.items() if key != 'artifact_sha256'}) == expected,
        'SETTINGS_ARTIFACT_RECEIPT_MISMATCH')
    return value


def _receipt(value):
    return dict(schema_version=SCHEMA_VERSION,artifact_sha256=value['artifact_sha256'],
        identity=deepcopy(value['identity']),event_head_sha256=value['event_head_sha256'])


def persist_portfolio_settings_draft(request, *, journal, binding, event_id, event_context, **packet):
    """Persist one new event, then its full qualified draft, without applying it.

    An interrupted draft write never rolls back a confirmed risk event. An
    identical event replay returns state only and never recreates an old draft.
    """
    out = _output()
    state = persist_portfolio_intraday_preview(request,journal=journal,binding=binding,
        event_id=event_id,event_context=event_context,**packet)
    for key in ('state_readback','next_binding','state_persisted','readback_verified',
            'breaker_state','new_risk_prohibited'):
        out[key] = state[key]
    out.update(intraday_status=state['status'],gaps=deepcopy(state['gaps']))
    if state['status'] != 'INTRADAY_EVENT_PERSISTED' or not state['readback_verified'] or state['gaps']:
        out['status'] = 'SETTINGS_STATE_ONLY'
        return out
    try:
        head = state['state_readback']['chain'][-1]
        _qualified(state['preview'],state['draft_settings'],request,head)
        value = _artifact(request,event_context,packet,state['preview'],state['draft_settings'],head)
        location = _location(journal,head['identity'],head['record_sha256'])
        try:
            created = journal.object_store.create_text(location,json.dumps(value,sort_keys=True,
                separators=(',',':'),ensure_ascii=False,allow_nan=False),content_type='application/json')
            out['artifact_create_receipt'] = created
        except Exception as exc:
            out['artifact_append_error_type'] = type(exc).__name__
        # Exactly one read confirms normal/lost acknowledgement. No write retry
        # and no removal of partial artifacts. The state checkpoint stays valid.
        read = _parse(journal.object_store.read_text(location),value['artifact_sha256'])
        _require(read == value, 'SETTINGS_ARTIFACT_READBACK_MISMATCH')
        out.update(status='SETTINGS_DRAFT_PERSISTED',artifact_receipt=_receipt(value),
            artifact_readback_verified=True,preview=deepcopy(read['preview']),
            draft_settings=deepcopy(read['draft_settings']))
    except Exception as exc:
        _error(out,exc)
    return out


def _fresh_sources(packet, context, current_contract):
    """Reuse admission validators at consumption time, without recomputation."""
    cash = adapt_cash_evidence(packet['facts'],current_contract,packet['cash_evidence'])
    _require(not cash['gaps'], 'SETTINGS_CURRENT_CASH_EVIDENCE_GAPS')
    account = adapt_ibkr_account_input(packet['facts'],cash['account_contract'])
    _require(not account['gaps'], 'SETTINGS_CURRENT_ACCOUNT_INPUT_GAPS')
    _,gaps = _execution_rules(packet['execution_rules'],current_contract,packet['risk_inputs'])
    _require(not gaps, 'SETTINGS_CURRENT_EXECUTION_RULE_GAPS')
    # This also rechecks event observations, policy, optional reduction and
    # recovery attestations. Each source keeps its original time/age allowance.
    def bound(value):
        if isinstance(value,Mapping):
            if HEADER <= set(value):
                _bound(value,current_contract)
            elif 'valid_from' in value or 'valid_until' in value:
                # The existing Kelly math contract has an effectiveness window
                # but no account evidence HEADER. Keep that explicit window.
                _require(utc(value['valid_from']) <= utc(current_contract['cutoff_utc'])
                    < utc(value['valid_until']), 'SETTINGS_SOURCE_NOT_EFFECTIVE')
            for item in value.values(): bound(item)
        elif type(value) is list:
            for item in value: bound(item)
    try: bound(dict(packet=packet,event_context=context))
    except (ValueError,TypeError,KeyError,OverflowError) as exc:
        raise ValueError('SETTINGS_CURRENT_SOURCE_EVIDENCE_EXPIRED_OR_UNBOUND') from exc


def read_portfolio_settings_draft(request, *, journal, binding, expected_artifact_sha256,
        consumption_cutoff_utc, event_context, **packet):
    """Read only, requiring both an external receipt and the latest checkpoint.

    The caller supplies the original frozen packet plus a separate current
    cutoff. An old P4 hash alone, a file's own digest, or a past checkpoint does
    not qualify a draft. Advanced confirmation remains REQUIRED, never granted.
    """
    out = _output()
    try:
        frozen = packet['account_contract']
        _require(utc(consumption_cutoff_utc) >= utc(frozen['cutoff_utc']), 'SETTINGS_CONSUMPTION_BEFORE_COMPUTATION')
        contract = dict(frozen,cutoff_utc=consumption_cutoff_utc)
        state = read_portfolio_event_state(journal=journal,binding=binding,account_contract=contract)
        out.update(state_readback=state,readback_verified=state['readback_verified'],
            next_binding=state['next_binding'],breaker_state=state['breaker_state'])
        _require(state['readback_verified'], 'SETTINGS_LATEST_STATE_UNVERIFIED')
        _require(state['status'] != 'INTRADAY_IDENTITY_RETIRED', 'SETTINGS_SOURCE_IDENTITY_RETIRED')
        head = state['chain'][-1]
        _require(head['kind'] == 'EVENT', 'SETTINGS_EVENT_HEAD_REQUIRED')
        identity = binding['identity']
        _require(identity['candidate_id'] == _portfolio_id(packet)
            and identity['config_sha256'] == digest(event_context['policy']), 'SETTINGS_CURRENT_POLICY_OR_PORTFOLIO_MISMATCH')
        value = _parse(journal.object_store.read_text(_location(journal,identity,head['record_sha256'])),
            expected_artifact_sha256)
        expected_input = _fingerprint_event(request,event_context,packet,head['event_id'])
        _require(value['identity'] == identity and value['event_head_sha256'] == head['record_sha256']
            and value['event_id'] == head['event_id'] and value['input_sha256'] == head['input_sha256'] == expected_input
            and value['request_sha256'] == digest(request), 'SETTINGS_EVENT_INPUT_MISMATCH')
        _require(value['frozen_cutoff_utc'] == frozen['cutoff_utc']
            and value['evidence_times'] == _times(dict(packet=packet,event_context=event_context)),
            'SETTINGS_FROZEN_EVIDENCE_MISMATCH')
        _qualified(value['preview'],value['draft_settings'],request,head)
        _fresh_sources(packet,event_context,contract)
        out.update(status='SETTINGS_DRAFT_READ_BACK',artifact_receipt=_receipt(value),
            artifact_readback_verified=True,preview=deepcopy(value['preview']),
            draft_settings=deepcopy(value['draft_settings']),new_risk_prohibited=state['new_risk_prohibited'],
            confirmation_state=value['preview']['p4_preview']['confirmation_state'],
            confirmation_granted=False,consumer_action='REDUCTION_PREVIEW_ONLY' if state['new_risk_prohibited'] else 'ALLOCATION_PREVIEW_ONLY',
            frozen_cutoff_utc=value['frozen_cutoff_utc'],consumption_cutoff_utc=consumption_cutoff_utc)
    except Exception as exc:
        _error(out,exc)
    return out


def preflight_portfolio_settings_draft(request, **kwargs):
    """Offline consumer seam only; there is deliberately no apply operation."""
    out = read_portfolio_settings_draft(request,**kwargs)
    if out['artifact_readback_verified']: out['status'] = 'READY_OFFLINE_SETTINGS_PREFLIGHT'
    return out
