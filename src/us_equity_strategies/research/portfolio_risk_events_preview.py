"""Source-bound artificial event previews, using the existing QPK risk core.

No policy defaults, broker reads, persistence, breaker reset or settings apply.
The old settings entry remains a lower-level allocation preview. This entry
requires event evidence for BOTH modes before exposing a configuration draft.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal
import json
import math

from quant_platform_kit.risk.engine import RiskEngine
from quant_platform_kit.risk.contracts import RiskSignal
from quant_platform_kit.risk.cycle_new_risk_health import (
    CycleNewRiskHealthEvidence, project_cycle_new_risk_health_axes,
)
from quant_platform_kit.common.strategy_risk_state import (
    StrategyRiskStateIdentity, StrategyRiskStateTransition,
    build_strategy_risk_state_transition, validate_strategy_risk_state_chain,
)
from .auto_portfolio_account import adapt_ibkr_account_input, digest, utc
from .cvar_exposure_constraint import ExposureCap, HardPolicy
from .funded_portfolio_preview import _bound
from .portfolio_event_journal import validate_event_chain
from .portfolio_settings_preview import (
    SUPPORTED, _direction, _validate_request, preview_portfolio_settings,
    portfolio_settings_capabilities,
)

EVENT_FIELDS = frozenset(('stop_loss_ratio', 'trailing_stop_ratio',
    'breaker_daily_loss', 'breaker_drawdown', 'resume_observation_sessions',
    'resume_daily_loss_fraction_of_trigger', 'resume_cvar_vol_fraction_of_limit'))
MONITOR_FIELDS = frozenset(('max_drawdown_tolerance', 'daily_cvar95_loss_limit', 'target_annual_volatility'))
LIMIT_FIELDS = EVENT_FIELDS | MONITOR_FIELDS
RECOVERY_REVERSE = frozenset(('resume_observation_sessions',))
LEVELS = ('conservative', 'balanced', 'growth')
BASES = dict(daily_loss_basis='SESSION_START_NAV_WITH_EXPLICIT_NET_EXTERNAL_FLOW',
    drawdown_basis='RETAINED_FLOW_ADJUSTED_NAV_INDEX',
    sleeve_basis='FEE_ADJUSTED_ENTRY_AND_NET_LIQUIDATION_VALUE',
    trigger_comparison='AT_OR_ABOVE', recovery_comparison='STRICTLY_BELOW')


def _require(condition, code):
    if not condition:
        raise ValueError(code)


def _number(value, *, positive=False):
    _require(type(value) in (int, float) and math.isfinite(value)
        and value >= 0 and (not positive or value > 0), 'EVENT_NUMBER_INVALID')
    return float(value)


def _text(value):
    _require(type(value) is str and 0 < len(value) <= 120, 'EVENT_ID_INVALID')
    return value


def _session(value):
    _require(type(value) is str and date.fromisoformat(value).isoformat() == value, 'EVENT_SESSION_INVALID')
    return value


def _limits(raw):
    _require(type(raw) is dict and set(raw) == LIMIT_FIELDS, 'EVENT_POLICY_FIELDS_MISSING')
    for key, value in raw.items():
        if key == 'resume_observation_sessions':
            _require(type(value) is int and 5 <= value <= 20, 'RECOVERY_SESSION_POLICY_INVALID')
        else:
            _require(0 < _number(value) <= 1, 'EVENT_POLICY_RATIO_INVALID')
    _require(raw['breaker_drawdown'] <= raw['max_drawdown_tolerance'], 'BREAKER_EXCEEDS_DRAWDOWN_LIMIT')
    _require(raw['resume_daily_loss_fraction_of_trigger'] <= .5
        and raw['resume_cvar_vol_fraction_of_limit'] <= .8, 'RECOVERY_POLICY_OUTSIDE_CONTRACT')
    return dict(raw)


def _tighten(original, requested):
    return {k:(max(original[k], requested.get(k, original[k])) if k in RECOVERY_REVERSE
        else min(original[k], requested.get(k, original[k]))) for k in original}


def _portfolio_id(packet):
    return 'portfolio-'+digest(dict(versions=packet['moments']['strategy_versions'],
        composition=packet['composition']['weights']))


def validate_portfolio_event_state(state):
    """Validate the existing event-state payload for preview and store readback."""
    _require(type(state) is dict and set(state) == {'breaker_open','original_daily_trigger',
        'trigger_session','latched_limits','nav_peak','sleeves'}
        and type(state['breaker_open']) is bool and type(state['sleeves']) is dict, 'EVENT_STATE_INVALID')
    _number(state['nav_peak'])
    for cycle,row in state['sleeves'].items():
        _text(cycle)
        _require(set(row) == {'entry','peak','stop_limit','trailing_limit','strategy_id'}, 'EVENT_STATE_INVALID')
        for key in ('entry','peak','stop_limit','trailing_limit'):_number(row[key],positive=True)
        _text(row['strategy_id'])
    if state['breaker_open']:
        _number(state['original_daily_trigger'],positive=True)
        _session(state['trigger_session'])
        _limits(state['latched_limits'])
    else:
        _require(state['original_daily_trigger'] is state['trigger_session'] is state['latched_limits'] is None,
            'EVENT_STATE_INVALID')


def validate_portfolio_state_progress(previous, state, effective_session):
    """Preserve the existing state protections across either storage contract."""
    validate_portfolio_event_state(state)
    if state['breaker_open']:
        _require(state['trigger_session'] <= effective_session, 'STATE_TRIGGER_SESSION_INVALID')
    if previous is None:
        return
    old = previous
    _require(not old['breaker_open'] or state['breaker_open'], 'STATE_AUTOMATIC_BREAKER_CLEAR_FORBIDDEN')
    _require(state['nav_peak'] >= old['nav_peak'], 'STATE_HIGH_WATERMARK_REGRESSION')
    if old['breaker_open']:
        _require(state['original_daily_trigger'] == old['original_daily_trigger']
            and state['trigger_session'] == old['trigger_session'], 'STATE_LATCHED_TRIGGER_CHANGED')
        _require(_tighten(old['latched_limits'],state['latched_limits']) == state['latched_limits'],
            'STATE_LATCHED_LIMIT_LOOSENED')
    _require(set(old['sleeves']) <= set(state['sleeves']), 'STATE_RETAINED_SLEEVE_MISSING')
    for cycle,row in old['sleeves'].items():
        new = state['sleeves'][cycle]
        _require(new['entry'] == row['entry'] and new['strategy_id'] == row['strategy_id'],
            'STATE_ENTRY_BASIS_CHANGED')
        _require(new['peak'] >= row['peak'] and new['stop_limit'] <= row['stop_limit']
            and new['trailing_limit'] <= row['trailing_limit'], 'STATE_SLEEVE_PROTECTION_LOOSENED')


def validate_portfolio_intraday_state(previous, state, effective_session, kind):
    """Retain the daily baseline while observations accumulate within a session."""
    _require(type(state) is dict and set(state) == {'risk_state','session','session_start_nav',
        'session_net_external_flow'}, 'EVENT_INTRADAY_STATE_INVALID')
    _session(state['session'])
    _require(state['session'] <= effective_session and (kind != 'EVENT' or state['session'] == effective_session),
        'EVENT_INTRADAY_STATE_SESSION_INVALID')
    _number(state['session_start_nav'],positive=True)
    _require(type(state['session_net_external_flow']) in (int,float)
        and math.isfinite(state['session_net_external_flow']), 'EVENT_FLOW_UNBOUND')
    if previous is not None:
        _require(state['session'] >= previous['session'], 'EVENT_INTRADAY_STATE_SESSION_INVALID')
        if state['session'] == previous['session']:
            _require(state['session_start_nav'] == previous['session_start_nav'], 'EVENT_INTRADAY_DAILY_BASELINE_CHANGED')
    validate_portfolio_state_progress(previous['risk_state'] if previous is not None else None,
        state['risk_state'],effective_session)


def _state(context, policy, observations, contract, packet):
    wire = context['state_readback']
    _bound(wire, contract)
    identity = StrategyRiskStateIdentity.from_dict(wire['identity'])
    expected = StrategyRiskStateIdentity('portfolio_risk_events', contract['scope_ref'],
        _portfolio_id(packet), digest(policy))
    _require(identity == expected, 'EVENT_STATE_IDENTITY_MISMATCH')
    _text(identity.candidate_id)
    if wire.get('schema_version') == 'qsl.portfolio-journal-readback.v1':
        chain = wire['chain']
        validate_event_chain(chain)
        _require(chain and wire['origin'] == 'VERIFIED_EVENT_JOURNAL', 'EVENT_STATE_READBACK_REQUIRED')
        _require(all(x['identity'] == identity.to_dict() for x in chain), 'EVENT_STATE_IDENTITY_MISMATCH')
        head = chain[-1]
        _require(head['kind'] != 'MIGRATE_OUT', 'EVENT_JOURNAL_IDENTITY_RETIRED')
        _require(wire['expected_head_sha256'] == head['record_sha256'], 'EVENT_STATE_HEAD_MISMATCH')
        _require(head['effective_session'] <= observations['session']
            and utc(head['observed_at']) <= utc(observations['valuation_at']), 'EVENT_STATE_OBSERVATION_NOT_ADVANCING')
        previous = None
        for item in chain:
            validate_portfolio_intraday_state(previous,item['state'],item['effective_session'],item['kind'])
            previous = item['state']
        if head['state']['session'] == observations['session']:
            _require(head['state']['session_start_nav'] == observations['session_start_nav'],
                'EVENT_INTRADAY_DAILY_BASELINE_CHANGED')
        return identity, head, deepcopy(head['state']['risk_state'])
    chain = [StrategyRiskStateTransition.from_dict(x) for x in wire['chain']]
    validate_strategy_risk_state_chain(chain)
    _require(all(x.identity == identity for x in chain), 'EVENT_STATE_IDENTITY_MISMATCH')
    head = chain[-1] if chain else None
    _require(wire['expected_head_sha256'] == (head.transition_sha256 if head else None), 'EVENT_STATE_HEAD_MISMATCH')
    _require((head is not None and wire['origin'] == 'VERIFIED_CHAIN') or
        (head is None and wire['origin'] == 'VERIFIED_CLOSED_GENESIS'), 'EVENT_STATE_READBACK_REQUIRED')
    if head is None:
        return identity, None, dict(breaker_open=False, original_daily_trigger=None,
            trigger_session=None, latched_limits=None, nav_peak=0., sleeves={})
    _require(head.effective_session < observations['session'], 'EVENT_STATE_SESSION_NOT_ADVANCING')
    state = head.state
    validate_portfolio_event_state(state)
    if state['breaker_open']:
        _require(state['trigger_session'] <= head.effective_session, 'EVENT_STATE_INVALID')
    return identity, head, state


def _observations(context, packet, account, previous):
    obs = context['observations']
    _bound(obs, packet['account_contract'])
    _require(obs['account_snapshot_sha256'] == digest(packet['facts'])
        and obs['valuation_at'] == packet['facts']['observed_at']
        and obs['fee_evidence_sha256'] == digest(packet['execution_rules']['fees']), 'EVENT_SNAPSHOT_MISMATCH')
    _require(all(obs.get(k) == v for k, v in BASES.items() if k.endswith('_basis')), 'EVENT_BASIS_UNBOUND')
    _session(obs['session'])
    _require(obs['session'] == utc(obs['valuation_at']).date().isoformat(), 'EVENT_SESSION_INVALID')
    _require(obs['sleeves_complete'] is True and obs['external_flows_complete'] is True,
        'EVENT_OBSERVATION_INCOMPLETE')
    start = _number(obs['session_start_nav'], positive=True)
    flow = obs['session_net_external_flow']
    _require(type(flow) in (int,float) and math.isfinite(flow), 'EVENT_FLOW_UNBOUND')
    daily = float(max(Decimal(0), (Decimal(str(start)) + Decimal(str(flow))
        - Decimal(str(account['nav']))) / Decimal(str(start))))
    index = _number(obs['flow_adjusted_nav_index'], positive=True)
    peak = _number(obs['retained_nav_index_peak'], positive=True)
    _require(peak >= index and peak >= previous['nav_peak'], 'EVENT_HIGH_WATERMARK_REGRESSION')
    drawdown = float(max(Decimal(0), 1 - Decimal(str(index)) / Decimal(str(peak))))
    cvar = _number(obs['daily_cvar95_loss_ratio'])
    vol = _number(obs['annual_volatility_ratio'])
    sleeves = obs['sleeves']
    _require(type(sleeves) is list, 'EVENT_SLEEVES_UNBOUND')
    records, metrics = {}, []
    market_total = 0.
    securities = dict.fromkeys(packet['risk_inputs']['scenario_set'].symbols, 0.)
    fees = packet['execution_rules']['fees']
    _bound(fees, packet['account_contract'])
    _require(fees['model'] == 'FIXED_NONNEGATIVE_BPS' and fees['unit'] == 'BPS_PER_SIDE'
        and fees['all_costs_included'] is True and fees['minimum_fee_applies'] is False,
        'EVENT_FEE_MODEL_UNBOUND')
    for row in sleeves:
        cycle, strategy = _text(row['held_cycle_id']), _text(row['strategy_id'])
        _require(cycle not in records and strategy in packet['moments']['strategy_ids'], 'EVENT_SLEEVE_IDENTITY_INVALID')
        entry = _number(row['fee_adjusted_entry_value'], positive=True)
        mark = _number(row['market_value'], positive=True)
        net = _number(row['net_liquidation_value'])
        high = _number(row['held_cycle_peak_net_value'], positive=True)
        _require(net <= mark and high >= net, 'EVENT_SLEEVE_VALUES_INVALID')
        values = row['security_market_values']
        _require(type(values) is dict and values and set(values) <= set(securities), 'EVENT_SLEEVE_SECURITIES_UNBOUND')
        fee_net = 0.
        for symbol,value in values.items():
            value = _number(value)
            bps = float(fees['bps_by_symbol'][symbol])
            _require(math.isfinite(bps) and 0 <= bps < 10000, 'EVENT_FEE_MODEL_UNBOUND')
            securities[symbol] += value
            fee_net += value*(1.-bps/10000.)
        _require(math.isclose(sum(values.values()), mark, rel_tol=0., abs_tol=1e-9)
            and math.isclose(fee_net, net, rel_tol=0., abs_tol=1e-9), 'EVENT_LIQUIDATION_FEES_NOT_RECONCILED')
        old = previous['sleeves'].get(cycle)
        if old:
            _require(old['entry'] == entry and old['strategy_id'] == strategy, 'EVENT_ENTRY_BASIS_CHANGED')
            _require(high >= old['peak'], 'EVENT_HIGH_WATERMARK_REGRESSION')
        records[cycle] = dict(entry=entry, peak=high, strategy_id=strategy)
        metrics.append(dict(held_cycle_id=cycle,
            loss_ratio=float(max(Decimal(0), 1-Decimal(str(net))/Decimal(str(entry)))),
            trailing_loss_ratio=float(max(Decimal(0), 1-Decimal(str(net))/Decimal(str(high))))))
        market_total += mark
    _require(math.isclose(market_total, account['nav']-account['cash_balance'], rel_tol=0., abs_tol=1e-9),
        'EVENT_SLEEVE_VALUATION_NOT_RECONCILED')
    _require(all(math.isclose(v, account['current_weights'].get(s,0.)*account['nav'],
        rel_tol=0., abs_tol=1e-9) for s,v in securities.items()), 'EVENT_SLEEVE_SECURITIES_NOT_RECONCILED')
    return obs, dict(daily_loss_ratio=daily, drawdown_ratio=drawdown,
        daily_cvar95_loss_ratio=cvar, annual_volatility_ratio=vol, sleeves=metrics), records


def _recovery(context, packet, previous, limits, metrics, active):
    if not previous['breaker_open']:
        return dict(status='NOT_LATCHED', reason_codes=[])
    recovery = context.get('recovery')
    if recovery is None:
        return dict(status='RECOVERY_EVIDENCE_MISSING', reason_codes=['REMEDIATION_AND_COMPLETED_SESSIONS_REQUIRED'])
    try:
        _bound(recovery, packet['account_contract'])
        _require(recovery['state_head_sha256'] == context['state_readback']['expected_head_sha256']
            and recovery['causes_resolved'] is True, 'RECOVERY_REMEDIATION_UNBOUND')
        done = utc(recovery['remediated_at'])
        _require(previous['trigger_session'] <= done.date().isoformat()
            and done <= utc(packet['account_contract']['cutoff_utc']), 'RECOVERY_REMEDIATION_UNBOUND')
        calendar = recovery['calendar']
        _bound(calendar, packet['account_contract'])
        dates = calendar['completed_sessions_after_remediation']
        _require(type(dates) is list and dates == sorted(set(dates))
            and all(_session(d) > done.date().isoformat() for d in dates), 'RECOVERY_CALENDAR_INVALID')
        _require(dates and dates[-1] == calendar['last_completed_session']
            and dates[-1] < context['observations']['session'], 'RECOVERY_CALENDAR_INVALID')
        rows = recovery['sessions']
        _require(type(rows) is list and [x['session'] for x in rows] == dates, 'RECOVERY_SESSION_COVERAGE_INCOMPLETE')
        clean = 0
        def product(a,b):
            return float(Decimal(str(a))*Decimal(str(b)))
        bound = dict(daily_loss_ratio=product(previous['original_daily_trigger'],limits['resume_daily_loss_fraction_of_trigger']),
            daily_cvar95_loss_ratio=product(limits['daily_cvar95_loss_limit'],limits['resume_cvar_vol_fraction_of_limit']),
            annual_volatility_ratio=product(limits['target_annual_volatility'],limits['resume_cvar_vol_fraction_of_limit']),
            drawdown_ratio=limits['breaker_drawdown'])
        for row in rows:
            _require(row['completed'] is True and row['reconciled'] is True and row['pending_state'] == 'CLEAR'
                and row['drawdown_basis'] == BASES['drawdown_basis']
                and utc(row['completed_at']) > done
                and utc(row['completed_at']).date().isoformat() == row['session']
                and utc(row['completed_at']) <= utc(calendar['available_at']), 'RECOVERY_SESSION_UNVERIFIED')
            values = {k:_number(row[k]) for k in bound}
            clean = clean+1 if all(values[k] < bound[k] for k in bound) else 0
        current_clean = all(metrics[k] < bound[k] for k in bound)
        ready = clean >= limits['resume_observation_sessions'] and current_clean and not active
        return dict(status='QUALIFIED_FOR_RESET_REVIEW' if ready else 'RECOVERY_NOT_QUALIFIED',
            consecutive_clean_sessions=clean, required_sessions=limits['resume_observation_sessions'],
            strict_upper_bounds=bound, reason_codes=[] if ready else ['RECOVERY_CONDITIONS_NOT_MET'])
    except (ValueError,TypeError,KeyError,AttributeError,ArithmeticError):
        return dict(status='RECOVERY_EVIDENCE_INVALID', reason_codes=['RECOVERY_SOURCE_OR_COVERAGE_UNBOUND'])


def evaluate_portfolio_risk_events(context, *, packet, overrides=None):
    """Evaluate an injected artificial observation; return a proposed transition.

    A qualified recovery never clears OPEN. The caller still needs a separate
    trusted reset consumer and actual state readback, neither implemented here.
    """
    result = dict(status='RISK_EVENT_GAPS', gaps=[], active_events=[], limits=None, metrics=None,
        new_risk_prohibited=True, forced_target=None, transition=None, recovery=None,
        engine_action=None, health_axes=None, synthetic=True, no_order=True,
        breaker_reset=False, state_persisted=False, execution_authorized=False)
    try:
        _require(type(context) is dict, 'EVENT_CONTEXT_MISSING')
        digest(context)  # ordinary, finite JSON only; no opaque service handles
        for key in ('policy','observations','state_readback'):
            _require(type(context.get(key)) is dict, 'EVENT_'+key.upper()+'_UNBOUND')
        _require(overrides is None or type(overrides) is dict and set(overrides) <= EVENT_FIELDS,
            'EVENT_OVERRIDE_UNSUPPORTED')
        for key,value in (overrides or {}).items():
            spec = json.loads(packet['definition_bytes'])['parameters'][key]['hard_limits']
            _require(spec['minimum'] <= _number(value) <= spec['maximum']
                and (spec['type'] != 'integer' or type(value) is int), 'EVENT_OVERRIDE_VALUE_INVALID')
        policy = context['policy']
        _bound(policy, packet['account_contract'])
        _require(policy['approved'] is True and all(policy.get(k) == v for k,v in BASES.items()),
            'EVENT_POLICY_UNBOUND')
        _text(policy['policy_id'])
        _require(set(policy['thresholds_by_level']) == set(LEVELS), 'EVENT_POLICY_FIELDS_MISSING')
        levels = {k:_limits(v) for k,v in policy['thresholds_by_level'].items()}
        level = context['level']
        _require(level in LEVELS, 'EVENT_LEVEL_INVALID')
        limits = _tighten(levels[level], overrides or {})
        _limits(limits)
        account = adapt_ibkr_account_input(packet['facts'], packet['account_contract'])
        _require(not account['gaps'], 'EVENT_ACCOUNT_INPUT_UNBOUND')
        identity, head, previous = _state(context, policy, context['observations'], packet['account_contract'], packet)
        obs, metrics, records = _observations(context, packet, account, previous)
        if previous['breaker_open']:
            limits = _tighten(limits, previous['latched_limits'])
        active = []
        if metrics['daily_loss_ratio'] >= limits['breaker_daily_loss']:active.append('DAILY_LOSS_BREAKER')
        if metrics['drawdown_ratio'] >= limits['breaker_drawdown']:active.append('DRAWDOWN_BREAKER')
        retained = deepcopy(previous['sleeves'])
        for row in metrics['sleeves']:
            cycle = row['held_cycle_id']; record = records[cycle]; old = retained.get(cycle, {})
            record['stop_limit'] = min(limits['stop_loss_ratio'], old.get('stop_limit', limits['stop_loss_ratio']))
            record['trailing_limit'] = min(limits['trailing_stop_ratio'], old.get('trailing_limit', limits['trailing_stop_ratio']))
            row.update(stop_limit=record['stop_limit'], trailing_limit=record['trailing_limit'])
            if row['loss_ratio'] >= record['stop_limit']:active.append('SLEEVE_STOP_LOSS:'+cycle)
            if row['trailing_loss_ratio'] >= record['trailing_limit']:active.append('SLEEVE_TRAILING_STOP:'+cycle)
            retained[cycle] = record
        pending = packet['cash_evidence']['pending_state']
        verified = obs['reconciliation_status'] == 'VERIFIED' and pending == 'CLEAR'
        axes = project_cycle_new_risk_health_axes(CycleNewRiskHealthEvidence(
            observation_ok=True, unknown_pending=not verified, digests_configured=True,
            digests_verified=verified, durable_breaker_open=previous['breaker_open'], cycle_trip_open=bool(active)))
        latched = axes['circuit_breaker_state'] == 'OPEN'
        reasons = list(active)
        if previous['breaker_open']:reasons.append('DURABLE_BREAKER_OPEN')
        if not verified:reasons.append('RECONCILIATION_OR_PENDING_UNVERIFIED')
        engine = RiskEngine()
        assessment = engine.evaluate({}, plugin_signals=(RiskSignal(
            plugin='source_bound_portfolio_events', schema_version='qsl.portfolio-events.v1',
            route='blocked' if not verified else ('risk_off' if latched else 'no_action'),
            confidence=1., suggested_action='NO_EXECUTION', reason_codes=tuple(reasons),
            emergency=bool(active), as_of=obs['valuation_at']),))
        action = engine.resolve(assessment)
        recovery = _recovery(context, packet, previous, limits, metrics, active)
        new_state = dict(breaker_open=latched,
            original_daily_trigger=(previous['original_daily_trigger'] if previous['breaker_open'] else limits['breaker_daily_loss']) if latched else None,
            trigger_session=(previous['trigger_session'] if previous['breaker_open'] else obs['session']) if latched else None,
            latched_limits=limits if latched else None, nav_peak=obs['retained_nav_index_peak'], sleeves=retained)
        transition = None
        if not isinstance(head,dict):
            transition = build_strategy_risk_state_transition(identity=identity,
                effective_session=obs['session'], input_sha256=digest(dict(context=context, overrides=overrides or {})),
                state=new_state, previous_transition=head).to_dict()
        validate_portfolio_state_progress(previous,new_state,obs['session'])
        result.update(status='EVENTS_EVALUATED_SYNTHETIC', active_events=active, limits=limits,
            metrics=metrics, new_risk_prohibited=latched, engine_action=action.action,
            health_axes=axes, recovery=recovery, transition=transition,
            state_update=deepcopy(new_state), state_identity=identity.to_dict())
        if not verified:
            result['gaps'].append('EVENT_RECONCILIATION_OR_PENDING_UNVERIFIED')
        elif latched:
            plan = context.get('reduction_plan')
            _require(type(plan) is dict, 'APPROVED_EVENT_REDUCTION_TARGET_MISSING')
            _bound(plan, packet['account_contract'])
            _require(plan['approved'] is True and plan['policy_sha256'] == digest(policy)
                and plan['account_snapshot_sha256'] == digest(packet['facts'])
                and plan['action'] == 'REDUCE_ONLY_UPPER_BOUND', 'EVENT_REDUCTION_PLAN_UNBOUND')
            symbols = packet['risk_inputs']['scenario_set'].symbols
            target = plan['target_weights']
            _require(type(target) is dict and set(target) == set(symbols), 'EVENT_REDUCTION_TARGET_COVERAGE')
            _require(all(_number(target[s]) <= account['current_weights'].get(s,0.) for s in symbols),
                'EVENT_REDUCTION_TARGET_INCREASES_RISK')
            result['forced_target'] = dict(target)
    except (ValueError,TypeError,KeyError,AttributeError,ArithmeticError) as exc:
        code = str(exc)
        result['gaps'].append(code if code.startswith(('EVENT_','RECOVERY_','BREAKER_','APPROVED_')) else 'EVENT_EVIDENCE_UNBOUND')
    if result['gaps']:
        result['status'] = 'RISK_EVENT_GAPS'
        result['new_risk_prohibited'] = True
    return result


def portfolio_risk_settings_capabilities(definition_bytes):
    result = portfolio_settings_capabilities(definition_bytes)
    for name in EVENT_FIELDS:
        result['fields'][name]['status'] = 'SUPPORTED_WITH_BOUND_EVENT_EVIDENCE_OFFLINE'
    result.update(entry='preview_portfolio_risk_settings', event_evidence_required_in_both_modes=True,
        recovery_effect='QUALIFICATION_ONLY_NO_RESET', old_entry_scope='ALLOCATION_PREVIEW_WITHOUT_EVENT_COVERAGE')
    return result


def _event_packet(packet, event):
    args = deepcopy(packet)
    if event['forced_target'] is not None:
        original = args['risk_inputs'].get('forced_target')
        target = event['forced_target']
        if original is not None:
            _require(set(original) == set(target), 'EVENT_ORIGINAL_FORCED_TARGET_INVALID')
            target = {s:min(_number(original[s]),target[s]) for s in target}
        args['risk_inputs']['forced_target'] = target
        hard = args['risk_inputs']['hard_policy']
        _require(isinstance(hard,HardPolicy), 'EVENT_ORIGINAL_HARD_POLICY_UNBOUND')
        # The old forced-target contract covers only hard.risk_symbols. The
        # approved event plan covers the entire security universe, so express
        # its remaining bounds as existing linear caps. Original caps and the
        # original risk-symbol meaning are retained, including after rounding.
        extra = tuple(ExposureCap({s:float(s == symbol) for s in target},limit,'TARGET_ONLY')
            for symbol,limit in target.items() if symbol not in hard.risk_symbols)
        args['risk_inputs']['hard_policy'] = replace(hard,caps=hard.caps+extra)
    return args


def preview_portfolio_risk_settings(request, *, event_context=None, **packet):
    """Smart and advanced event-aware previews; no automatic execution/reset."""
    out = dict(contract_version='qsl.portfolio-risk-settings-preview.v1', status='RISK_SETTINGS_PREVIEW_HAS_GAPS',
        gaps=[], system_events=None, requested_events=None, allocation_preview=None,
        p4_preview=None, draft_settings=None, constrained_parameters={},
        execution_authorized=False, settings_applied=False, account_policy_bound=False,
        financial_risk_certified=False, breaker_reset=False, state_persisted=False)
    out['limitations'] = ['SYNTHETIC_SOURCE_ATTESTATIONS_NOT_REAL_POLICY',
        'STOP_THRESHOLD_NOT_A_GUARANTEED_LOSS_CEILING', 'RECOVERY_QUALIFICATION_ONLY_NO_RESET',
        'NOT_A_REPLACEMENT_FOR_RUNTIME_RISK_GATE']
    try:
        definition = json.loads(packet['definition_bytes'])
        _require(type(request) is dict and type(request.get('overrides')) is dict, 'EVENT_REQUEST_INVALID')
        filtered = deepcopy(request)
        filtered['overrides'] = {k:v for k,v in request['overrides'].items() if k not in EVENT_FIELDS}
        gaps = _validate_request(filtered, definition, packet)
        if request['overrides'] and (request['mode'] != 'advanced' or request['restore_system_recommendation']):
            gaps.append('EVENT_OVERRIDE_MODE_INVALID')
        overrides = {k:v for k,v in request['overrides'].items() if k in EVENT_FIELDS}
        for key,value in overrides.items():
            limits = definition['parameters'][key]['hard_limits']
            _number(value)
            if not limits['minimum'] <= value <= limits['maximum'] or (limits['type'] == 'integer' and type(value) is not int):
                gaps.append('SETTING_VALUE_INVALID:'+key)
        if gaps:
            out.update(status='INVALID_SETTINGS_REQUEST', gaps=sorted(set(gaps)))
            return out
        context = deepcopy(event_context)
        _require(type(context) is dict and 'level' not in context, 'EVENT_CONTEXT_MISSING_OR_CONTAINS_REQUEST_LEVEL')
        context['level'] = request['level']
        system_event = evaluate_portfolio_risk_events(context, packet=packet)
        event = evaluate_portfolio_risk_events(context, packet=packet, overrides=overrides) if overrides else system_event
        out.update(system_events=system_event, requested_events=event)
        if event['gaps']:
            out['gaps'] = event['gaps']
            return out
        allocation = preview_portfolio_settings(filtered, **_event_packet(packet,event))
        # A requested tighter trigger may change the action. Keep the original
        # policy's recommendation separate from that requested reduction.
        system_allocation = allocation
        if overrides:
            system_request = deepcopy(filtered);system_request.update(mode='simple',overrides={})
            system_allocation = preview_portfolio_settings(system_request, **_event_packet(packet,system_event))
        # A cap in weight space is not a reduce-only order: fill-time price
        # drift can require buys to maintain that weight. Reuse the original
        # quantity projection, then refuse ANY such buy while OPEN.
        for base,signals in ((allocation,event),(system_allocation,system_event)):
            if signals['new_risk_prohibited']:
                for row in (base.get('requested_preview'),base.get('system_recommendation')):
                    q = (row or {}).get('quantity_preview') or {}
                    buys = [dict(scenario_id=s['scenario_id'],trade=t) for s in q.get('scenarios',[])
                        for t in s['trades'] if t['side']=='BUY' and t['quantity']>0]
                    if buys:
                        out.update(gaps=['EVENT_REDUCTION_WOULD_BUY_AFTER_FILL_DRIFT'],conditional_buys_detected=buys)
                        return out
        out['allocation_preview'] = allocation
        draft = allocation['requested_preview']['draft_settings'] if allocation['requested_preview'] else None
        out['gaps'] = allocation['gaps']
        p4 = deepcopy(allocation['p4_preview'])
        if p4 is not None:
            p4['requested_settings'] = deepcopy(request)
            if overrides and system_allocation['p4_preview'] is not None:
                p4['estimates']['with_overrides'] = deepcopy(p4['estimates']['system_recommended'])
                p4['estimates']['system_recommended'] = deepcopy(system_allocation['p4_preview']['estimates']['system_recommended'])
            for key in SUPPORTED:
                system_p4 = system_allocation['p4_preview']
                if system_p4 is not None:
                    p4['parameter_comparison'][key]['recommended_value'] = system_p4['parameter_comparison'][key]['recommended_value']
            for key in EVENT_FIELDS:
                row = p4['parameter_comparison'][key]; item = definition['parameters'][key]
                row['recommended_value'] = system_event['limits'][key]
                row['limiting_sources'] = ['SOURCE_BOUND_SYNTHETIC_POLICY','RETAINED_STATE_NEVER_RESET','PREVIEW_ONLY']
                if key in overrides:
                    direction = _direction(overrides[key], row['recommended_value'], item['risk_increase_when'])
                    row.update(override_value=overrides[key],has_override=True,risk_direction=direction,
                        confirmation_required=item['secondary_confirmation']=='ALWAYS' or direction in ('INCREASE','UNKNOWN_OR_MIXED'))
            p4['confirmation_state'] = 'REQUIRED' if any(x['confirmation_required'] for x in p4['parameter_comparison'].values()) else 'NOT_REQUIRED'
            p4['confirmation_binding']['requested_parameters_sha256'] = digest(request)
            p4['evidence']['data_sha256'] = digest(dict(allocation=p4['evidence']['data_sha256'],
                system_allocation=system_allocation['p4_preview'], event_context=context,
                requested_event=event, system_event=system_event))
            p4['reason_codes'] = sorted(set(p4['reason_codes'] + ['EVENT_AWARE_OFFLINE_PREVIEW','NO_BREAKER_RESET']))
            body = deepcopy(p4)
            for k in ('preview_sha256','server_confirmation_ref','confirmed_at_utc','expires_at_utc'):body['confirmation_binding'].pop(k)
            p4['confirmation_binding']['preview_sha256'] = digest(body)
        out.update(status='READY_SYNTHETIC_RISK_SETTINGS_PREVIEW' if draft is not None else 'RISK_SETTINGS_PREVIEW_HAS_GAPS',
            p4_preview=p4, draft_settings=deepcopy(draft), constrained_parameters=dict(event_limits=event['limits'],
                allocation=allocation['constrained_parameters']))
        if event['new_risk_prohibited']:
            out['constrained_parameters']['target_role'] = 'APPROVED_EVENT_REDUCE_ONLY_UPPER_BOUND'
        out['system_recommendation'] = system_allocation['system_recommendation']
        out['final_target_is_qp_optimum'] = False
    except (ValueError,TypeError,KeyError,AttributeError,ArithmeticError) as exc:
        code = str(exc)
        out['gaps'] = [code if code.startswith('EVENT_') else 'EVENT_REQUEST_OR_CONTEXT_INVALID']
    return out
