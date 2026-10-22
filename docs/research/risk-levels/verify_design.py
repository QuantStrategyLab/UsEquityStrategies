#!/usr/bin/env python3
"""Offline design/schema and existing-kernel probes; no financial backtest or I/O to accounts/stores."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
LEVELS = ('conservative', 'balanced', 'growth')


def load(name):
    return json.loads((HERE / name).read_text(), parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    checks = []
    validators = {}
    settings_schema = load('risk-settings.schema.json')
    registry = Registry().with_resource(settings_schema['$id'], Resource.from_contents(settings_schema))
    for name in ('parameter-catalogue', 'risk-settings', 'p4-preview'):
        schema = load(name + '.schema.json')
        Draft202012Validator.check_schema(schema)
        validators[name] = Draft202012Validator(schema, format_checker=FormatChecker(), registry=registry)
        checks.append('schema_valid:' + name)
    catalogue = load('parameters.json')
    validators['parameter-catalogue'].validate(catalogue)
    params = catalogue['parameters']
    assert params['kelly_fraction']['hard_limits'] == {
        'type': 'number', 'enum': [.10, .25, .40], 'minimum': .10, 'maximum': .40,
    }
    for name, param in params.items():
        Draft202012Validator.check_schema(param['hard_limits'])
        validator = Draft202012Validator(param['hard_limits'])
        for level in LEVELS:
            validator.validate(param['defaults_by_level'][level])
        assert param['user_override_allowed'] or param['secondary_confirmation'] == 'NEVER', name
    override_properties = settings_schema['properties']['overrides']['properties']
    assert set(override_properties) == {k for k, v in params.items() if v['user_override_allowed']}
    for name, item in override_properties.items():
        param = params[name]
        assert all(item[k] == v for k, v in param['hard_limits'].items()), name
        assert item['x-defaults-by-level'] == param['defaults_by_level'], name
        assert item['x-secondary-confirmation'] == param['secondary_confirmation'], name
    checks.append(f'all_parameter_defaults_and_metadata:{len(params)}')
    req = load('examples/advanced-settings.json')
    preview = load('examples/preview-unavailable.json')
    validators['risk-settings'].validate(req)
    validators['p4-preview'].validate(preview)
    for level in LEVELS:
        request = deepcopy(req)
        request['level'] = level
        request['overrides'] = {k: v['defaults_by_level'][level] for k, v in params.items() if v['user_override_allowed']}
        validators['risk-settings'].validate(request)
    simple = deepcopy(req)
    simple['mode'] = 'simple'
    validators['risk-settings'].validate(simple)  # Mode alone must not erase overrides.
    for c in (.10, .25, .40):
        request = deepcopy(req)
        request['overrides']['kelly_fraction'] = c
        validators['risk-settings'].validate(request)
    checks.append('examples_and_all_three_default_settings_valid')

    rejected = []

    def reject(label, validator, value):
        assert not validator.is_valid(value), label
        rejected.append(label)

    for c in (0, -.1, .05, .2, .3, .41, .5, float('nan'), float('inf'),
              float('-inf'), '0.10', '0.25', '0.40', True, False, None):
        bad = deepcopy(req)
        bad['overrides']['kelly_fraction'] = c
        reject('kelly_fraction:' + repr(c), validators['risk-settings'], bad)
    bad = deepcopy(catalogue)
    bad['parameters']['kelly_fraction']['hard_limits'] = {'type': 'number', 'minimum': 0, 'maximum': .4}
    reject('catalogue_continuous_c_domain', validators['parameter-catalogue'], bad)

    for key, value in (
        ('kelly_fraction', .41), ('cash_floor_ratio', -.01),
        ('max_leveraged_etf_nominal_weight', .31), ('benchmark', 'UNKNOWN'),
        ('strategy_weights', {'bad': 1.1}), ('m1_fee_reserve_ratio', 0),
        ('upgrade_cooldown_seconds', 0), ('unknown_parameter', 1),
    ):
        bad = deepcopy(req)
        bad['overrides'][key] = value
        reject(key, validators['risk-settings'], bad)
    bad = deepcopy(req)
    bad['execution_authorized'] = True
    reject('execution_authorized_true', validators['risk-settings'], bad)
    bad = deepcopy(req)
    bad['restore_system_recommendation'] = True
    reject('restore_with_residual_overrides', validators['risk-settings'], bad)
    restored = deepcopy(bad)
    restored['overrides'] = {}
    validators['risk-settings'].validate(restored)
    for key in ('defaults_by_level', 'hard_limits', 'user_override_allowed', 'secondary_confirmation'):
        bad = deepcopy(catalogue)
        del bad['parameters']['kelly_fraction'][key]
        reject('catalogue_missing_' + key, validators['parameter-catalogue'], bad)
    bad = deepcopy(preview)
    bad['estimates']['with_overrides']['max_drawdown']['status'] = 'COMPUTED'
    reject('computed_null_metric', validators['p4-preview'], bad)
    bad = deepcopy(preview)
    bad['estimates']['with_overrides']['max_drawdown']['value'] = 0
    reject('uncomputable_zero_metric', validators['p4-preview'], bad)
    bad = deepcopy(preview)
    bad['application_status'] = 'APPLIED_OBSERVED'
    reject('applied_without_effective_readback', validators['p4-preview'], bad)
    bad = deepcopy(preview)
    bad['live_ready'] = True
    reject('live_ready_true', validators['p4-preview'], bad)

    gaps, ledger = load('comparison-gaps.json'), load('r4-trial-ledger.json')
    assert len(gaps['cells']) == len(ledger['planned_cells']) == 6
    assert {(c['symbol'], c['level']) for c in gaps['cells']} == {(s, l) for s in ('TQQQ', 'SOXL') for l in LEVELS}
    assert len({c['cell_id'] for c in gaps['cells']}) == 6
    for cell in gaps['cells']:
        assert cell['status'] == 'NOT_RUN_BLOCKED'
        assert cell['evidence_classification'] == 'DESIGN_ONLY'
        assert all(v is None for v in cell['metrics'].values())
        assert set(cell['gap_codes']) <= set(gaps['gap_definitions'])
    assert gaps['dsr']['value'] is None and ledger['dsr_value'] is None
    assert ledger['cumulative_trials_current'] is None
    assert gaps['real_trials_run'] == gaps['synthetic_backtests_run'] == 0
    assert ledger['new_real_trials'] == ledger['new_synthetic_performance_trials'] == 0
    historical = ROOT / ledger['historical_ledger']['path']
    assert sha(historical) == ledger['historical_ledger']['sha256']
    assert json.loads(historical.read_text())['cumulative_sizing_trials'] == 48
    assert preview['definition_sha256'] == gaps['definition_sha256'] == sha(HERE / 'parameters.json')
    checks.append('six_blocked_cells_and_historical_48_lower_bound_no_financial_result')

    from us_equity_strategies.research.bayesian_kelly import (
        B4Error, B4Observation, B4Spec, b4_fractional_target,
        b4_predictive_moments, initialise_b4, update_b4,
    )
    from us_equity_strategies.research.cvar_exposure_constraint import weighted_cvar
    from us_equity_strategies.portfolio_risk_budget import (
        PortfolioAssetRiskSpec, PortfolioRiskBudgetPolicy, assess_portfolio_risk_budget,
    )

    anchor = datetime(2026, 1, 1, 21, tzinfo=timezone.utc)
    spec = B4Spec(('TQQQ',), anchor)
    state = initialise_b4(spec, ((.0004,),), 'SYNTHETIC_INTERFACE_PROBE_NOT_A_TRIAL')
    t = anchor + timedelta(days=1)
    obs = B4Observation('SYNTHETIC_OBSERVATION', 1, t, t + timedelta(seconds=1), spec.core_ids, (.002,))
    state = update_b4(state, obs, decision_cutoff=t + timedelta(seconds=2)).state
    moments = b4_predictive_moments(state)
    b4_results = {}
    for c in (.10, .25, .40):
        result = b4_fractional_target(moments, c=c, scope_cap={'TQQQ': .2}, cap_source='SYNTHETIC_ONLY')
        assert result.c == c and result.status == 'AVAILABLE'
        assert result.no_order and not result.live_ready and result.fractional_applied_once
        b4_results[str(c)] = 'AVAILABLE_RESEARCH_ONLY'
    assert abs(weighted_cvar([.10, .04, -.01], [.02, .08, .90], .95) - .064) < 1e-12
    assert weighted_cvar([-.30, -.10], [.97, .03], .95) < 0
    checks.append('existing_weighted_cvar_fractional_tail_and_negative_loss_sign')
    specs = {
        'TQQQ': PortfolioAssetRiskSpec('TQQQ', 3, 'NASDAQ100'),
        'CASH': PortfolioAssetRiskSpec('CASH', 1, 'TRUE_CASH', is_cash=True),
    }
    budget = assess_portfolio_risk_budget(
        target_weights={'TQQQ': .5, 'CASH': .5}, asset_risk_specs=specs,
        policy=PortfolioRiskBudgetPolicy('CASH', .3, {'TQQQ': .1}, {'NASDAQ100': .3}),
    )
    assert budget['status'] == 'REDUCE' and budget['execution_authorized'] is False
    assert budget['recommended_target_weights']['TQQQ'] <= .1 + 1e-12
    checks.append('existing_risk_budget_reduces_synthetic_target_without_execution_authority')
    # Read local module availability only. Never install, connect, or invoke QPK stats.
    try:
        stats = importlib.util.find_spec('quant_platform_kit.research_stats')
    except ModuleNotFoundError:
        stats = None
    source_names = (
        'src/us_equity_strategies/research/bayesian_kelly.py',
        'src/us_equity_strategies/research/rs04_netting_candidate.py',
        'src/us_equity_strategies/portfolio_risk_budget.py',
        'src/us_equity_strategies/research/cvar_exposure_constraint.py',
        'src/us_equity_strategies/research/portfolio_kelly_walk_forward.py',
        'src/us_equity_strategies/research/trial_overfitting_report.py',
    )
    return {
        'status': 'PASS_SCOPED_DESIGN_AND_INTERFACE_CHECKS',
        'command': 'PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 docs/research/risk-levels/verify_design.py',
        'checks': checks, 'negative_schema_cases_rejected': rejected,
        'b4_c_domain_synthetic_probe': b4_results,
        'qpk_research_stats_available': stats is not None,
        'source_sha256': {name: sha(ROOT / name) for name in source_names},
        'parameter_definition_sha256': sha(HERE / 'parameters.json'),
        'validation_scope': 'Schema/default/example checks and synthetic existing-function probes only; no three-level performance backtest.',
        'financial_qualification': 'NOT_READY', 'real_trials': 0,
        'synthetic_performance_trials': 0, 'r4_store_mutated': False,
        'execution_authorized': False, 'python_version': sys.version.split()[0],
    }


if __name__ == '__main__':
    print(json.dumps(main(), ensure_ascii=False, indent=2, allow_nan=False))
