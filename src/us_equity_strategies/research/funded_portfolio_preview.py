"""Synthetic cash/cost/quantity projections on the existing three-level preview.

No account I/O, fitting, configuration writes, or broker execution. Rule source
strings are bindings to supplied artificial evidence, never real authorization.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math

from quant_platform_kit.common.quantity import floor_to_quantity_step
from .auto_portfolio_account import digest, utc
from .auto_portfolio_preview import build_three_level_previews, _hash, _p4_preview
from .cvar_exposure_constraint import Action, DecisionBook, _evaluate, weighted_cvar

# Exact order in the fixed IBKR producer. This selects balance, not spendability.
CASH_TAGS = ('$LEDGER-CashBalance', '$LEDGER-TotalCashBalance', 'CashBalance',
             'TotalCashBalance', 'SettledCash')


def _amount(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError('explicit amount required')
    try:
        number = Decimal(str(value))
        if not number.is_finite() or not math.isfinite(float(number)) or number < 0 or (positive and number == 0):
            raise ValueError('invalid amount')
        return number
    except (InvalidOperation, OverflowError) as exc:
        raise ValueError('invalid amount') from exc


def _bound(evidence, contract):
    if not isinstance(evidence, Mapping) or evidence.get('synthetic') is not True:
        raise ValueError('artificial source required')
    for key in ('account_id', 'scope_ref', 'currency'):
        value = contract.get(key)
        if not isinstance(value,str) or not value.strip() or evidence.get(key) != value:
            raise ValueError('scope mismatch')
    for key in ('source', 'source_version'):
        if not isinstance(evidence.get(key), str) or not evidence[key].strip():
            raise ValueError('source/version missing')
    cutoff, observed, available = (utc(contract.get('cutoff_utc')),
        utc(evidence.get('observed_at')), utc(evidence.get('available_at')))
    age_limit = _amount(evidence.get('max_age_seconds'))
    if not (0 <= (cutoff-observed).total_seconds() <= float(age_limit)) or not observed <= available <= cutoff:
        raise ValueError('evidence time invalid')
    if not utc(evidence.get('valid_from')) <= cutoff < utc(evidence.get('valid_until')):
        raise ValueError('rule not effective at cutoff')


def adapt_cash_evidence(facts, account_contract, cash_evidence):
    """Bind native cash tags plus explicit complete/disjoint obligation evidence.

    Projected sells/partial-fill reports never add settled cash. Their proceeds
    require a fresh settled-cash observation. Existing explicit values may be
    filled when absent, but a conflicting supplied value is never overwritten.
    """
    result = dict(status='CASH_EVIDENCE_GAPS', gaps=[], account_contract=deepcopy(account_contract),
        selected_cash_tag=None, execution_authorized=False, account_policy_bound=False)
    gaps = result['gaps']
    try:
        _bound(cash_evidence, account_contract)
    except (ValueError, TypeError, AttributeError, OverflowError):
        gaps.append('CASH_EVIDENCE_UNBOUND')
        return result
    if cash_evidence.get('pending_state') != 'CLEAR':
        gaps.append('PENDING_STATE_NOT_CLEAR')
    if cash_evidence.get('obligations_complete') is not True or cash_evidence.get('reservations_disjoint') is not True:
        gaps.append('OBLIGATIONS_EVIDENCE_INCOMPLETE')
    tags = {}
    try:
        rows = cash_evidence.get('account_values')
        if not isinstance(rows, list):
            raise ValueError('native account values required')
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError('invalid cash row')
            if row.get('account_id') != account_contract.get('account_id') or row.get('currency') != account_contract.get('currency'):
                continue
            tag = row.get('tag')
            if tag not in CASH_TAGS:
                continue
            if tag in tags:
                gaps.append('DUPLICATE_CASH_TAG')
            tags[tag] = _amount(row.get('value'))
    except (ValueError, TypeError):
        gaps.append('CASH_VALUES_INVALID')
    selected = next((tag for tag in CASH_TAGS if tag in tags), None)
    result['selected_cash_tag'] = selected
    if selected is None:
        gaps.append('CASH_BALANCE_EVIDENCE_MISSING')
    else:
        try:
            projected = facts['cash']
            if (len(projected) != 1 or projected[0]['currency'] != account_contract['currency']
                    or projected[0]['source_tag'] != selected or _amount(projected[0]['cash_balance']) != tags[selected]):
                raise ValueError('cash projection mismatch')
        except (ValueError, TypeError, KeyError, IndexError):
            gaps.append('NATIVE_CASH_PROJECTION_MISMATCH')
    if 'SettledCash' not in tags:
        gaps.append('SETTLED_CASH_EVIDENCE_MISSING')
    reserves = {'RESERVE': Decimal(0), 'OTHER': Decimal(0)}
    try:
        obligations = cash_evidence.get('reservations')
        if not isinstance(obligations, list):
            raise ValueError('explicit reservations required')
        seen = set()
        for row in obligations:
            identity, kind = row['id'], row['kind']
            if not isinstance(identity, str) or not identity.strip() or identity in seen or kind not in reserves:
                raise ValueError('duplicate/invalid obligation')
            seen.add(identity)
            reserves[kind] += _amount(row['amount'])
    except (ValueError, TypeError, KeyError):
        gaps.append('RESERVATIONS_INVALID')
    derived = dict(settled_cash=tags.get('SettledCash'), reserved_cash=reserves['RESERVE'],
                   other_cash_obligations=reserves['OTHER'])
    contract = result['account_contract']
    if contract.get('obligations_complete') is False:
        gaps.append('CASH_CONTRACT_EVIDENCE_CONFLICT')
    try:
        for key, value in derived.items():
            if value is None:
                continue
            if contract.get(key) is not None and _amount(contract[key]) != value:
                gaps.append('CASH_CONTRACT_EVIDENCE_CONFLICT')
            else:
                contract[key] = str(value)
    except (ValueError, TypeError):
        gaps.append('CASH_CONTRACT_EVIDENCE_CONFLICT')
    if not gaps:
        contract['cash_rule_source'] = cash_evidence['source'] + ':' + cash_evidence['source_version']
        contract['obligations_complete'] = True
        result['status'] = 'CASH_EVIDENCE_BOUND_SYNTHETIC'
    result['gaps'] = sorted(set(gaps))
    return result


_ORDER_TERM_FIELDS = frozenset(('per_share','per_order','minimum_amount',
    'maximum_notional_bps','extra_per_share','extra_notional_bps'))


def _order_fee(terms, quantity, notional):
    """One full-fill order estimate; cap commission before additive charges.

    This is a declared artificial model, not an IBKR plan/routing attestation.
    Zero quantity is no order, so it never incurs the per-order minimum.
    """
    if quantity == 0:
        return Decimal(0)
    base = max(_amount(terms['minimum_amount']),
        _amount(terms['per_order']) + quantity*_amount(terms['per_share']))
    if terms['maximum_notional_bps'] is not None:
        base = min(base,notional*_amount(terms['maximum_notional_bps'])/Decimal(10000))
    return base + quantity*_amount(terms['extra_per_share']) + notional*_amount(terms['extra_notional_bps'])/Decimal(10000)


def _order_terms(rule, symbols):
    allowed = set(('model','unit','all_costs_included','order_assumption','rounding',
        'candidate_cost_role','terms_by_symbol')) | set(('synthetic','account_id','scope_ref',
        'currency','source','source_version','observed_at','available_at','max_age_seconds','valid_from','valid_until'))
    if (set(rule) != allowed or rule['unit'] != 'ACCOUNT_CURRENCY_PER_ORDER'
            or rule['all_costs_included'] is not True
            or rule['order_assumption'] != 'ONE_FULL_FILL_ORDER_PER_SYMBOL'
            or rule['rounding'] != 'UNROUNDED_DECIMAL_ESTIMATE'
            or rule['candidate_cost_role'] != 'REFERENCE_BPS_ONLY'):
        raise ValueError('unsupported order cost contract')
    by_symbol = rule['terms_by_symbol']
    if not isinstance(by_symbol,Mapping) or set(by_symbol) != set(symbols):
        raise ValueError('incomplete fee coverage')
    result = {}
    for symbol,sides in by_symbol.items():
        if not isinstance(sides,Mapping) or set(sides) != {'BUY','SELL'}:
            raise ValueError('explicit side terms required')
        result[symbol] = {}
        for side,terms in sides.items():
            if not isinstance(terms,Mapping) or set(terms) != _ORDER_TERM_FIELDS:
                raise ValueError('unsupported fee components')
            result[symbol][side] = {key:None if key=='maximum_notional_bps' and value is None
                else _amount(value) for key,value in terms.items()}
    return result


def _execution_rules(rules, contract, risk):
    gaps = []
    values = {}
    if not isinstance(rules, Mapping):
        return {}, ['FEE_RULE_UNBOUND', 'QUANTITY_RULE_UNBOUND', 'PRICE_RULE_UNBOUND']
    symbols = risk['scenario_set'].symbols
    for part, code in (('fees','FEE_RULE_UNBOUND'),('quantity','QUANTITY_RULE_UNBOUND'),('prices','PRICE_RULE_UNBOUND')):
        try:
            rule = rules.get(part)
            _bound(rule, contract)
            order_model = part == 'fees' and rule.get('model') == 'ORDER_COMPONENTS_V1'
            fields = {'fees': () if order_model else ('bps_by_symbol',), 'quantity': ('step_by_symbol','minimum_buy_notional_by_symbol'),
                      'prices': ('mark_by_symbol',)}[part]
            for field in fields:
                raw = rule.get(field)
                if not isinstance(raw, Mapping) or set(raw) != set(symbols):
                    raise ValueError('incomplete security coverage')
                values[field] = {s: _amount(raw[s], positive=field in ('step_by_symbol','mark_by_symbol')) for s in symbols}
            if part == 'quantity' and rule.get('method') != 'FLOOR_TRADE_DELTA':
                raise ValueError('unknown rounding rule')
            if order_model:
                values['order_terms'] = _order_terms(rule,symbols)
            elif part == 'fees':
                if (rule.get('model') != 'FIXED_NONNEGATIVE_BPS' or rule.get('unit') != 'BPS_PER_SIDE'
                        or rule.get('minimum_fee_applies') is not False or rule.get('all_costs_included') is not True):
                    gaps.append('UNSUPPORTED_FEE_RULE')
                if any(rule.get(key) not in (None, 0, '0') for key in
                       ('minimum_fee_amount','per_order_fee','per_share_fee','maximum_fee_amount')):
                    gaps.append('UNSUPPORTED_FEE_RULE')
                spec = risk['bps_spec']
                if (spec.model != rule.get('model') or spec.unit != rule.get('unit')
                        or spec.multiplier_unit != 'DIMENSIONLESS'
                        or any(values['bps_by_symbol'][s] != _amount(spec.bps_by_symbol[s]) * _amount(spec.frozen_multiplier,positive=True) for s in symbols)):
                    gaps.append('FEE_KERNEL_MISMATCH')
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            gaps.append(code)
    return values, sorted(set(gaps))


def _quantity_projection(facts, contract, account, draft, rules, risk):
    """Floor conditional trade deltas; evaluate original caps and aggregate CVaR.

    Fill mark is an explicit artificial execution-price assumption. Quantities
    can differ by future scenario; this is a conditional projection, not a
    pre-signed order basket or a simulation of the production execution loop.
    """
    values, gaps = _execution_rules(rules, contract, risk)
    out = dict(status='QUANTITY_PREVIEW_GAPS', gaps=gaps, scenarios=[],
        conditional_on_scenario_fill=True, live_execution_parity=False,
        aggregate_cvar=None, execution_authorized=False, financial_risk_certified=False)
    if gaps:
        return out
    order_model = 'order_terms' in values
    out.update(cost_evaluation='EXACT_ORDER_FEE_RECHECK' if order_model else 'FIXED_BPS',
        nonlinear_cost_optimized=False, actual_commissions_included=False)
    symbols, scenario_set = risk['scenario_set'].symbols, risk['scenario_set']
    positions = {s:Decimal(0) for s in symbols}
    try:
        for row in facts['broker_reported_positions']:
            symbol = row['symbol']
            positions[symbol] = _amount(row['quantity'], positive=True)
            if positions[symbol]*values['mark_by_symbol'][symbol] != _amount(row['market_value']):
                gaps.append('POSITION_PRICE_QUANTITY_MISMATCH')
        if (_amount(facts['net_assets']) != _amount(account['cash_balance'])
                + sum(positions[s]*values['mark_by_symbol'][s] for s in symbols)):
            gaps.append('QUANTITY_NAV_RECONCILIATION_REQUIRED')
        if gaps:
            return out
        nav = _amount(facts['net_assets'],positive=True)
        cash = _amount(account['cash_balance'])
        book = DecisionBook({s:account['current_weights'].get(s,0.) for s in symbols},float(nav),account['currency'],0.)
        losses, probabilities = [], []
        for scenario in scenario_set.scenarios:
            prices = {s:values['mark_by_symbol'][s] * (Decimal(1)+Decimal(str(scenario.fill_returns[s]))) for s in symbols}
            fill_nav = cash + sum(positions[s]*prices[s] for s in symbols)
            target_values = {s:_amount(draft['security_target_weights'][s])*fill_nav for s in symbols}
            trades, final_values = [], {}
            gross_buy, gross_sell, total_fee = Decimal(0), Decimal(0), Decimal(0)
            evaluation_bps = dict.fromkeys(symbols,0.)
            for symbol in symbols:
                current = positions[symbol]*prices[symbol]
                delta = target_values[symbol]-current
                buy = delta > 0
                budget = abs(delta)
                if buy and budget < values['minimum_buy_notional_by_symbol'][symbol]:
                    budget = Decimal(0)
                raw_quantity = min(budget/prices[symbol],positions[symbol]) if not buy else budget/prices[symbol]
                # Reuse the deployed helper, but validate missing/invalid step
                # above instead of accepting its convenience default of one.
                quantity = _amount(floor_to_quantity_step(raw_quantity, values['step_by_symbol'][symbol]))
                if quantity % values['step_by_symbol'][symbol] != 0 or quantity > raw_quantity:
                    raise ValueError('quantity precision cannot preserve floor')
                notional = quantity*prices[symbol]
                if buy and notional < values['minimum_buy_notional_by_symbol'][symbol]:
                    quantity = notional = Decimal(0)
                fee = (_order_fee(values['order_terms'][symbol]['BUY' if buy else 'SELL'],quantity,notional)
                    if order_model else notional*values['bps_by_symbol'][symbol]/Decimal(10000))
                if quantity:
                    evaluation_bps[symbol] = float(fee/notional*Decimal(10000))
                total_fee += fee
                gross_buy += notional if buy else Decimal(0)
                gross_sell += notional if not buy else Decimal(0)
                final_values[symbol] = current+(notional if buy else -notional)
                if quantity:
                    trades.append(dict(symbol=symbol, side='BUY' if buy else 'SELL', quantity=float(quantity),
                        notional=float(notional), fee_amount=float(fee), fill_price=float(prices[symbol]),
                        fee_kind='ESTIMATE_NOT_ACTUAL_COMMISSION'))
            target = {s:float(final_values[s]/fill_nav) for s in symbols}
            one = replace(scenario_set, scenarios=(replace(scenario,probability=1.),))
            # Rates are an exact algebraic representation of these already
            # fixed scenario orders only. Never feed them into the convex
            # candidate solver or infer a nonlinear optimum from this recheck.
            evaluation_costs = (replace(risk['bps_spec'],bps_by_symbol=evaluation_bps,frozen_multiplier=1.)
                if order_model else risk['bps_spec'])
            evaluated = _evaluate(book,Action('TARGET_WEIGHTS',target),one,evaluation_costs,
                risk['hard_policy'],draft['tail_budget_ratio'],risk.get('forced_target'))
            loss = evaluated.scenarios[0]
            # Tail budget belongs to the JOINT distribution, not the worst
            # individual scenario. All other original constraints still apply.
            gaps.extend('ROUNDED_'+x for x in evaluated.failures if x != 'TAIL_BUDGET')
            model_fee = float(nav)*loss.G*loss.k
            if not math.isclose(model_fee,float(total_fee),rel_tol=1e-10,abs_tol=1e-10):
                gaps.append('ROUNDED_FEE_RECONCILIATION_FAILED')
            required = gross_buy+total_fee
            if required > _amount(account['spendable_cash']):
                gaps.append('ROUNDED_SETTLED_CASH_INSUFFICIENT_NO_SELL_BRIDGE')
            cash_after = cash-gross_buy+gross_sell-total_fee
            if cash_after < 0:
                gaps.append('ROUNDED_CASH_NEGATIVE')
            out['scenarios'].append(dict(scenario_id=scenario.scenario_id, probability=scenario.probability,
                trades=trades, outcome='TRADES_PROJECTED' if trades else 'NO_TRADE_IF_THIS_SCENARIO',
                continuous_target_value={s:float(v) for s,v in target_values.items()},
                rounded_security_weights=target, fee_amount=float(total_fee), gross_buy_plus_fees=float(required),
                spendable_cash=account['spendable_cash'], sale_proceeds_used_for_funding=False,
                cash_after_fee_amount=float(cash_after), post_fee_security_weights={s:v/(1.-loss.k) for s,v in target.items()},
                remaining_quantity={s:float(final_values[s]/prices[s]) for s in symbols},
                model_loss=loss.loss, original_cap_failures=[x for x in evaluated.failures if x != 'TAIL_BUDGET'],
                standalone_loss_above_joint_budget=loss.loss > draft['tail_budget_ratio']))
            losses.append(loss.loss); probabilities.append(scenario.probability)
        es = weighted_cvar(tuple(losses),tuple(probabilities),scenario_set.descriptor.alpha_confidence)
        out['aggregate_cvar'] = es
        if es > draft['tail_budget_ratio']:
            gaps.append('ROUNDED_TAIL_BUDGET')
        if not gaps:
            out['status'] = 'QUANTITY_COST_RISK_CHECKED_SYNTHETIC'
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError):
        gaps.append('QUANTITY_PROJECTION_UNSUPPORTED')
    out['gaps'] = sorted(set(gaps))
    return out


def build_three_level_funded_previews(*, cash_evidence=None, execution_rules=None, **packet):
    """New guarded entry for three-level configuration plus quantity previews."""
    cash = adapt_cash_evidence(packet['facts'],packet['account_contract'],cash_evidence)
    args = dict(packet,account_contract=cash['account_contract'])
    result = build_three_level_previews(**args)
    result['schema_version'] = 'qsl.funded-auto-portfolio-offline-preview.v3'
    result['cash_evidence'] = {k:v for k,v in cash.items() if k != 'account_contract'}
    hashes = result['input_hashes']
    hashes.update(cash_evidence=_hash(cash_evidence),execution_rules=_hash(execution_rules))
    definition = json.loads(packet['definition_bytes'])
    try:
        _, rule_gaps = _execution_rules(execution_rules,cash['account_contract'],packet['risk_inputs'])
    except (ValueError, TypeError, KeyError, AttributeError):
        rule_gaps = ['EXECUTION_RULE_COVERAGE_UNBOUND']
    for level, preview in result['previews'].items():
        gaps = list(preview['gaps'])+cash['gaps']+rule_gaps
        if hashes['cash_evidence'] is None or hashes['execution_rules'] is None:
            gaps.append('INPUT_BINDING_UNAVAILABLE')
        draft = preview['draft_settings']
        quantity = None
        if draft is not None and not gaps:
            quantity = _quantity_projection(packet['facts'],cash['account_contract'],result['account_input'],
                draft,execution_rules,packet['risk_inputs'])
            gaps.extend(quantity['gaps'])
        if gaps:
            draft = None
        elif draft is not None:
            draft = dict(draft, quantity_projection_kind='CONDITIONAL_SCENARIO_FILL_MARK',
                discrete_strategy_attribution='NOT_INFERRED', continuous_allocation_only=True)
        preview.update(status='READY_SYNTHETIC_FUNDED_PREVIEW' if draft is not None else 'PREVIEW_HAS_GAPS',
            gaps=sorted(set(gaps)),draft_settings=draft,quantity_preview=quantity)
        preview['p4_preview'] = _p4_preview(definition=definition,
            definition_hash=hashlib.sha256(packet['definition_bytes']).hexdigest(),
            request_id=f"{packet['request_id']}:{level}",level=level,account=result['account_input'],
            candidate=preview['candidate'],draft=draft,gaps=gaps,
            es=quantity['aggregate_cvar'] if quantity is not None else None,input_hashes=hashes)
        # The original P4 estimates describe a continuous target. Conditional
        # rounded outcomes stay explicitly separate rather than replacing them.
        metric = preview['p4_preview']['estimates']['system_recommended']['two_session_cvar95_loss']
        metric.update(status='UNCOMPUTABLE',value=None,reason_codes=['CONDITIONAL_QUANTITY_RESULT_SEPARATE'])
        p4 = preview['p4_preview']
        body = deepcopy(p4)
        for key in ('preview_sha256','server_confirmation_ref','confirmed_at_utc','expires_at_utc'):
            body['confirmation_binding'].pop(key)
        p4['confirmation_binding']['preview_sha256'] = digest(body)
    return result
