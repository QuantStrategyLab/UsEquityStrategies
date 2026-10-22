"""Hand-sized metrics oracle; no real data, Store or trial invocation."""
import importlib.util
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
