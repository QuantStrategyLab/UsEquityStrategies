"""Pure smart/advanced settings previews; never applies settings or orders.

Only supported fields from the existing research catalogue are consumed. All
numeric policies remain caller supplied. Request limits tighten existing rules;
manual targets pass through the same compose, risk, cash and quantity core.
"""
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
import math
import re

from .auto_portfolio_account import digest
from .cvar_exposure_constraint import ExposureCap, HardPolicy
from .funded_portfolio_preview import build_three_level_funded_previews

SUPPORTED = frozenset(('kelly_fraction','two_session_cvar95_budget','cash_floor_ratio',
    'max_symbol_nominal_weight','strategy_selection_mode','strategy_ids','strategy_weight_mode','strategy_weights','benchmark'))
SELECTION = frozenset(('strategy_selection_mode','strategy_ids','strategy_weight_mode','strategy_weights'))
REQUEST_KEYS = frozenset(('definition_version','request_id','scope_ref','mode','level','overrides',
    'restore_system_recommendation','research_only','execution_authorized'))
ID_PATTERN = re.compile(r'^[a-zA-Z0-9_.:-]+$')


def _number(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError('finite nonboolean number required')
    return float(value)


def _validate_request(request,definition,packet):
    gaps = []
    if packet.get('candidate_preferences') is not None:
        return ['SETTINGS_CONTEXT_CONTAINS_REQUEST_OVERRIDES']
    if not isinstance(request,Mapping) or set(request) != REQUEST_KEYS:
        return ['SETTINGS_REQUEST_INVALID']
    try:
        digest(request)
        if (request['definition_version'] != definition['definition_version']
                or request['scope_ref'] != packet['account_contract']['scope_ref']
                or request['request_id'] != packet['request_id']):
            raise ValueError('identity/version mismatch')
        for key in ('request_id','scope_ref'):
            if not isinstance(request[key],str) or not 1 <= len(request[key]) <= 120:
                raise ValueError('identity format')
        if request['mode'] not in ('simple','advanced') or request['level'] not in ('conservative','balanced','growth'):
            raise ValueError('mode/level')
        if request['research_only'] is not True or request['execution_authorized'] is not False:
            raise ValueError('research request only')
        if not isinstance(request['restore_system_recommendation'],bool) or not isinstance(request['overrides'],Mapping):
            raise ValueError('request flags')
    except (ValueError,TypeError,KeyError,OverflowError):
        return ['SETTINGS_REQUEST_INVALID']
    overrides = request['overrides']
    if request['mode'] == 'simple' and overrides:
        gaps.append('SIMPLE_MODE_OVERRIDE_NOT_ALLOWED')
    if request['restore_system_recommendation'] and overrides:
        gaps.append('RESTORE_WITH_OVERRIDES_INVALID')
    for key,value in overrides.items():
        item = definition['parameters'].get(key)
        if item is None or item.get('user_override_allowed') is not True:
            gaps.append('SETTING_NOT_OVERRIDABLE:'+key)
            continue
        if key not in SUPPORTED:
            gaps.append('SETTING_CONSUMER_UNSUPPORTED:'+key)
            continue
        limits = item['hard_limits']
        try:
            if key in ('kelly_fraction','two_session_cvar95_budget','cash_floor_ratio','max_symbol_nominal_weight'):
                number = _number(value)
                if number < limits['minimum'] or number > limits['maximum'] or ('enum' in limits and number not in limits['enum']):
                    raise ValueError('numeric setting outside catalogue')
            elif key in ('strategy_selection_mode','strategy_weight_mode','benchmark'):
                if value not in limits['enum']:
                    raise ValueError('unknown selection mode')
            elif key == 'strategy_ids':
                if (not isinstance(value,list) or len(value)>limits['maxItems']
                        or any(not isinstance(s,str) or not 1<=len(s)<=120 or not ID_PATTERN.fullmatch(s) for s in value)
                        or len(value)!=len(set(value))):
                    raise ValueError('strategy id list')
            else:
                if not isinstance(value,Mapping) or len(value)>limits['maxProperties']:
                    raise ValueError('weight mapping')
                for strategy,weight in value.items():
                    if not isinstance(strategy,str) or not ID_PATTERN.fullmatch(strategy) or not 0 <= _number(weight) <= 1:
                        raise ValueError('weight value')
                if sum(Decimal(str(v)) for v in value.values()) > Decimal(1):
                    raise ValueError('weights must not exceed capital')
        except (ValueError,TypeError,OverflowError):
            gaps.append('SETTING_VALUE_INVALID:'+key)
    if set(overrides) & SELECTION:
        system_selection = overrides.get('strategy_selection_mode','system_eligible') == 'system_eligible' and overrides.get('strategy_weight_mode','system_recommended') == 'system_recommended'
        if system_selection and not overrides.get('strategy_ids') and not overrides.get('strategy_weights'):
            pass
        elif (not SELECTION <= set(overrides) or overrides.get('strategy_selection_mode') != 'explicit'
                or overrides.get('strategy_weight_mode') != 'explicit'
                or not isinstance(overrides.get('strategy_ids'),list)
                or not isinstance(overrides.get('strategy_weights'),Mapping)
                or set(overrides['strategy_ids']) != set(overrides['strategy_weights'])):
            gaps.append('EXPLICIT_SELECTION_CONTRACT_INCOMPLETE')
        elif any(s not in packet['moments']['strategy_ids'] for s in overrides['strategy_ids']):
            gaps.append('UNAPPROVED_STRATEGY_SELECTION')
    return sorted(set(gaps))


def _requested_packet(request,packet,reference):
    args = deepcopy(packet)
    overrides = request['overrides']
    controls = dict(level=request['level'],source_request_sha256=digest(request))
    if 'kelly_fraction' in overrides:
        controls['c'] = overrides['kelly_fraction']
    if overrides.get('strategy_weight_mode') == 'explicit':
        controls['strategy_weights'] = {s:overrides['strategy_weights'].get(s,0.) for s in packet['moments']['strategy_ids']}
    if len(controls)>2:
        args['candidate_preferences'] = controls
    risk = args['risk_inputs']
    if 'two_session_cvar95_budget' in overrides:
        nav = reference['account_input']['nav']
        if nav is None:
            raise ValueError('ACCOUNT_NAV_REQUIRED_FOR_BUDGET_OVERRIDE')
        cap = nav*overrides['two_session_cvar95_budget']
        old = risk.get('absolute_tail_loss_limit')
        risk['absolute_tail_loss_limit'] = cap if old is None else min(old,cap)
    if set(overrides) & {'cash_floor_ratio','max_symbol_nominal_weight'}:
        hard = risk.get('hard_policy')
        if not isinstance(hard,HardPolicy):
            raise ValueError('ORIGINAL_HARD_POLICY_UNBOUND')
        symbols = risk['scenario_set'].symbols
        caps = list(hard.caps)
        if 'cash_floor_ratio' in overrides:
            caps.append(ExposureCap(dict.fromkeys(symbols,1.),1.-overrides['cash_floor_ratio'],'POST_FEE'))
        if 'max_symbol_nominal_weight' in overrides:
            caps.extend(ExposureCap({s:float(s==symbol) for s in symbols},overrides['max_symbol_nominal_weight'],'POST_FEE') for symbol in symbols)
        risk['hard_policy'] = replace(hard,caps=tuple(caps),source=hard.source+';REQUEST_TIGHTENING:'+digest(request))
    return args


def _direction(value,reference,rule):
    if reference is None or rule == 'NON_MONOTONE':
        return 'UNKNOWN_OR_MIXED'
    if value == reference:
        return 'UNCHANGED'
    if isinstance(value,(int,float)) and not isinstance(value,bool) and isinstance(reference,(int,float)):
        increases = value > reference if rule == 'INCREASE' else value < reference
        return 'INCREASE' if increases else 'DECREASE'
    return 'UNKNOWN_OR_MIXED'


def _render_p4(request,definition,reference,requested,system_hashes,requested_hashes):
    preview = deepcopy(requested['p4_preview'])
    preview.update(request_id=request['request_id'],mode=request['mode'],requested_settings=deepcopy(dict(request)))
    comparison = deepcopy(reference['p4_preview']['parameter_comparison'])
    required = []
    for key,value in request['overrides'].items():
        row = comparison[key];item = definition['parameters'][key]
        direction = _direction(value,row['recommended_value'],item['risk_increase_when'])
        confirmation = item['secondary_confirmation']=='ALWAYS' or (
            item['secondary_confirmation']=='IF_RISK_INCREASING' and direction in ('INCREASE','UNKNOWN_OR_MIXED'))
        row.update(override_value=deepcopy(value),has_override=True,risk_direction=direction,confirmation_required=confirmation)
        row['limiting_sources'] = list(dict.fromkeys(row['limiting_sources']+['ORIGINAL_POLICY_REMAINS_BINDING','REQUEST_PREVIEW_ONLY']))
        if confirmation:required.append(key)
    preview['parameter_comparison'] = comparison
    preview['confirmation_state'] = 'REQUIRED' if required else 'NOT_REQUIRED'
    preview['confirmation_binding']['requested_parameters_sha256'] = digest(request)
    preview['estimates']['system_recommended'] = deepcopy(reference['p4_preview']['estimates']['system_recommended'])
    if request['overrides']:
        preview['estimates']['with_overrides'] = deepcopy(requested['p4_preview']['estimates']['system_recommended'])
    if requested['candidate'].get('target_source') == 'USER_EXPLICIT_TARGET':
        comparison['kelly_fraction']['limiting_sources'].append('REFERENCE_ONLY_FOR_USER_EXPLICIT_TARGET')
    preview['evidence']['data_sha256'] = digest(dict(request=request,system_inputs=system_hashes,requested_inputs=requested_hashes))
    preview['reason_codes'] = sorted(set(preview['reason_codes']+['SETTINGS_PREVIEW_ONLY']))
    body = deepcopy(preview)
    for key in ('preview_sha256','server_confirmation_ref','confirmed_at_utc','expires_at_utc'):
        body['confirmation_binding'].pop(key)
    preview['confirmation_binding']['preview_sha256'] = digest(body)
    return preview,sorted(required)


def portfolio_settings_capabilities(definition_bytes):
    """Report real offline consumer coverage; catalogue presence is not support."""
    definition = json.loads(definition_bytes)
    fields = {}
    for name,item in definition['parameters'].items():
        state = 'SUPPORTED_OFFLINE' if name in SUPPORTED else (
            'NOT_OVERRIDABLE' if not item['user_override_allowed'] else 'CONSUMER_UNSUPPORTED')
        fields[name] = dict(status=state,user_override_allowed=item['user_override_allowed'],
            limits=item['hard_limits'],secondary_confirmation=item['secondary_confirmation'],
            catalogue_research_default_used=name=='kelly_fraction',account_policy_default_inferred=False)
    return dict(definition_version=definition['definition_version'],fields=fields,
        explicit_selection_requires_all_four_fields=sorted(SELECTION),
        benchmark_role='REPORTING_ONLY_NO_MODEL_OR_RISK_BENCHMARK_CHANGE',
        execution_authorized=False,settings_applied=False)


def preview_portfolio_settings(request, **packet):
    """One deterministic, synthetic preview cycle; contains no apply operation.

    Smart calls use mode=simple. Advanced requests consume only the explicitly
    supported catalogue fields. Repeated calls do not create orders or state.
    """
    out = dict(contract_version='qsl.portfolio-settings-preview.v1',status='INVALID_SETTINGS_REQUEST',
        gaps=[],system_recommendation=None,requested_preview=None,p4_preview=None,
        constrained_parameters={},confirmation_required_fields=[],reporting_benchmark=None,
        limitations=['ORIGINAL_POLICY_REMAINS_BINDING','SYNTHETIC_ONLY','NO_APPLY_OR_SCHEDULER'],
        execution_authorized=False,settings_applied=False,account_policy_bound=False,financial_risk_certified=False)
    try:
        definition = json.loads(packet['definition_bytes'])
        gaps = _validate_request(request,definition,packet)
    except (KeyError,ValueError,TypeError,AttributeError):
        gaps = ['SETTINGS_PACKET_UNBOUND']
    if gaps:
        out['gaps'] = gaps
        return out
    try:
        system = build_three_level_funded_previews(**packet)
        reference = system['previews'][request['level']]
        args = _requested_packet(request,packet,system)
        result = build_three_level_funded_previews(**args) if request['overrides'] else system
        requested = result['previews'][request['level']]
        p4,required = _render_p4(request,definition,reference,requested,system['input_hashes'],result['input_hashes'])
    except (KeyError,ValueError,TypeError,AttributeError,ArithmeticError) as exc:
        out['status'] = 'SETTINGS_PREVIEW_HAS_GAPS'
        out['gaps'] = [str(exc) if str(exc) in ('ACCOUNT_NAV_REQUIRED_FOR_BUDGET_OVERRIDE','ORIGINAL_HARD_POLICY_UNBOUND') else 'SETTINGS_INPUT_CONTRACT_UNBOUND']
        return out
    constrained = {}
    draft = requested['draft_settings']
    if draft is not None:
        constrained = dict(kelly_fraction=requested['candidate']['c'],
            two_session_cvar95_budget=draft['tail_budget_ratio'],strategy_weights=draft['strategy_weights'])
    out.update(status='READY_SYNTHETIC_SETTINGS_PREVIEW' if draft is not None else 'SETTINGS_PREVIEW_HAS_GAPS',
        gaps=requested['gaps'],system_recommendation=reference,requested_preview=requested,
        p4_preview=p4,constrained_parameters=constrained,confirmation_required_fields=required,
        reporting_benchmark=request['overrides'].get('benchmark'))
    return out
