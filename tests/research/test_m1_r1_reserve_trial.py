"""Hand-sized metrics oracle; no real data, Store or trial invocation."""
import copy
import importlib.util
import json
import math
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location('m1_trial',
    Path(__file__).resolve().parents[2] / 'scripts/run_m1_r1_reserve_trial.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def packet(marks):
    start, rows = 1., []
    for index, (pre, fee, traded) in enumerate(marks):
        end = pre - fee
        rows.append({'session': f'2026-01-{index+20}', 'nav_start': start,
            'nav_pre_trade': pre, 'nav_end': end, 'fees': fee,
            'net_trades': {'BOXX': -traded, 'TQQQ': traded},
            'net_security_turnover': 2 * traded / pre, 'net_return': end / start - 1})
        start = end
    return {'initial_nav': 1., 'ledger': rows}


def test_paired_net_growth_turnover_fee_drag_and_tail_oracle():
    new = packet([(1.1, .01, .2), (.981, .002, .1)])
    old = packet([(1.02, .001, .05), (1.1, .003, .3)])
    result = runner.paired_metrics(new, old)
    assert result['paired_delta_g'] == pytest.approx((math.log(.979) - math.log(1.097)) / 2)
    assert result['R1']['turnover_sum'] == pytest.approx(.4 / 1.1 + .2 / .981)
    assert result['R1']['fees_initial_nav'] == pytest.approx(.012)
    assert result['R1']['fee_log_drag_sum'] == pytest.approx(math.log(1.1 / 1.09) + math.log(.981 / .979))
    assert result['R1']['mdd'] == pytest.approx(1 - .979 / 1.09)
    assert result['R1']['daily_loss_cvar95'] == pytest.approx(1 - .979 / 1.09)
    assert result['R1_minus_R0']['fees_initial_nav'] == pytest.approx(.008)


def test_turnover_rejects_incompatible_native_denominator():
    value = packet([(1.1, .01, .2)])
    value['ledger'][0]['net_security_turnover'] = .4  # initial NAV, not pre-trade NAV
    with pytest.raises(ValueError, match='M1_TURNOVER_NATIVE_MISMATCH'):
        runner.packet_metrics(value)


def test_pairing_requires_identical_sessions_and_opening_capital():
    new, old = packet([(1.1, .01, .2)]), packet([(1.02, .001, .05)])
    old['ledger'][0]['session'] = '2026-01-21'
    with pytest.raises(ValueError, match='M1_UNPAIRED'):
        runner.paired_metrics(new, old)
    old['ledger'][0]['session'] = new['ledger'][0]['session']
    old['initial_nav'] = 2.
    with pytest.raises(ValueError, match='M1_UNPAIRED'):
        runner.paired_metrics(new, old)


def test_empty_book_does_not_become_a_successful_zero_metric():
    with pytest.raises(ValueError, match='M1_EMPTY_OR_INVALID_BOOK'):
        runner.packet_metrics({'initial_nav': 1., 'ledger': []})


def _binding_fixture():
    wire = {'dataset_class': 'REAL_ARCHIVED_RAW_ACTIONS',
        'r1_mechanism_policy': {'predecessor_trial_id': 'r0-frozen'},
        'requested_start': '2023-03-28', 'requested_end': '2026-08-25'}
    raw = {'canonical_body_sha256': 'a' * 64}
    old = {'derived_input_body_sha256': 'a' * 64, 'trial': {
        'trial_id': 'r0-frozen', 'synthetic': False,
        'cost_source': 'ASSUMED_FLAT_BPS5_SINGLE_CASE_NOT_BROKER_FEE_FACT',
        'window_start': '2023-03-28', 'window_end': '2026-08-25', 'calendar_id': 'XNYS'}}
    return old, wire, raw


def test_r0_binding_requires_frozen_predecessor_and_non_synthetic():
    old, wire, raw = _binding_fixture()
    runner.validate_r0_binding(old, wire, raw)
    for field, value in (('trial_id', 'other-trial'), ('synthetic', True),
                         ('cost_source', 'DIFFERENT'), ('window_start', '2023-03-29'),
                         ('calendar_id', 'XHKG')):
        bad = copy.deepcopy(old)
        bad['trial'][field] = value
        with pytest.raises(ValueError, match='M1_R0_BINDING_MISMATCH'):
            runner.validate_r0_binding(bad, wire, raw)


class _FailureStore:
    def __init__(self):
        self.created = []

    def _read_research_json(self, key):
        return None

    def _create_same(self, key, value):
        self.created.append((key, value))


def test_prejournal_failure_does_not_occupy_shared_metrics_key(tmp_path):
    store = _FailureStore()
    failure = {'status': 'stopped_no_retry', 'trial_id': 'new'}
    (tmp_path / 'no-native').mkdir()
    runner.record_failure(store, 'metrics-key', failure, None, tmp_path / 'no-native')
    assert store.created == []
    assert json.loads((tmp_path / 'no-native' / 'FAILURE.json').read_text()) == failure

    (tmp_path / 'native').mkdir()
    runner.record_failure(store, 'metrics-key', failure, object(), tmp_path / 'native')
    assert store.created == [('metrics-key', failure)]
    assert json.loads((tmp_path / 'native' / 'FAILURE.json').read_text()) == failure
