"""One synthetic RS06 fit -> actual net book -> exact local candidate readback."""
import importlib.util
import os
from datetime import date, timedelta

import pytest

from us_equity_strategies.research import portfolio_kelly_walk_forward as pw
from us_equity_strategies.research.fractional_kelly_walk_forward_plan import WalkForwardPlan


try:
    NETTING_AVAILABLE = importlib.util.find_spec("quant_platform_kit.research_stats.security_netting") is not None
except ModuleNotFoundError:
    NETTING_AVAILABLE = False
pytestmark = pytest.mark.skipif(
    not NETTING_AVAILABLE and os.environ.get("UES_REQUIRE_SECURITY_NETTING") != "1",
    reason="production QPK pin lacks research-only security netting",
)


def fixture_args(tmp_path, *, bps=100., test_sessions=5):
    from quant_platform_kit.research_stats.security_netting import SecurityNettingSpec
    dates = []
    day = date(2025, 1, 2)
    while len(dates) < 121 + test_sessions:
        if day.weekday() < 5:
            dates.append(day)
        day += timedelta(days=1)
    returns = {'A': [.03 + (.2 if i % 2 else -.2) for i in range(120)] + [0.] + ([0., .1, -.2, .03, 0.]*test_sessions)[:test_sessions],
               'B': [.025 + (.16 if i % 4 < 2 else -.16) for i in range(120)] + [0.] + ([0., -.04, .02, -.02, 0.]*test_sessions)[:test_sessions]}
    prices = {}
    for symbol, values in returns.items():
        price = 100.
        prices[symbol] = []
        for session, value in zip(dates, values, strict=True):
            price *= 1. + value
            prices[symbol].append((session, price))
    request = dict(local_root=tmp_path/'candidate', trial_id='synthetic-rs06-one', run_id='synthetic-rs06-run',
                   candidate_id='synthetic-rs06', source_revision='84aeca629affd4eccb9ac260a16286cc4d54fab2',
                   calendar_id='synthetic-supplied-sessions', periods_per_year=252, initial_nav=1000.,
                   close_prices=prices, decision_sessions=list(zip(dates[121:], dates[120:-1], strict=True)),
                   spec=SecurityNettingSpec(bps_per_side={'A': bps, 'B': bps}),
                   fee_cash_role='inner_residual_cash', end_target={})
    return dict(asset_returns=[(s, list(zip(dates, v, strict=True))) for s, v in returns.items()],
                cash_returns=[(d, 0.) for d in dates], benchmark_returns=[],
                plan=WalkForwardPlan(120, test_sessions, 1), cost_bps_per_side=bps,
                fractions=(.25,), methods=('qp',), multipliers=(1.,), netting_candidate=request)


def test_success_actual_fee_cash_and_candidate_readback(tmp_path):
    args = fixture_args(tmp_path)
    result = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert result['status'] == 'SUCCEEDED'
    assert result['synthetic'] and result['no_order'] and not result['live_ready']
    assert result['promotion_eligible'] is False and result['budget_effect'] == 'NONE'
    assert 'comparators' not in result
    assert len(result['folds']) == 1
    fit = result['folds'][0]['portfolio_fit']
    assert fit['status'] == 'COMPUTED'
    assert 0 < sum(fit['weights']['0.25']['qp'].values()) < 1
    assert result['netting']['ledger'][0]['inner_target'] == fit['weights']['0.25']['qp']
    assert result['candidate']['total_return'] == pytest.approx(result['netting']['final_nav']/1000. - 1)


def open_store(args):
    from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore
    return PerformanceStore(local_root=args['netting_candidate']['local_root'], cloud_bucket='')


def no_success(result):
    assert result['status'] in ('UNKNOWN', 'REJECTED')
    assert not set(result).intersection(('candidate', 'netting', 'unified_net_report', 'research_ledger'))
    assert result['synthetic'] and result['no_order']
    assert result['live_ready'] is False and result['promotion_eligible'] is False
    assert result['budget_effect'] == 'NONE'


def test_success_conservation_public_metrics_and_actual_caller_only(tmp_path, monkeypatch):
    import math
    from quant_platform_kit.research_stats import unified_report
    from us_equity_strategies.research import fractional_kelly_walk_forward as rs04
    from us_equity_strategies.research import security_netting_runner as runner
    args = fixture_args(tmp_path)
    calls = []
    fit, replay = pw.fit_portfolio_kelly, runner.run_offline_security_netting

    def fitted(assets, returns, cash, fractions, methods):
        calls.append('fit_train_only')
        assert len(cash) == 120
        assert list(returns['A']) == [v for _, v in args['asset_returns'][0][1][:120]]
        assert tuple(fractions) == (.25,) and tuple(methods) == ('qp',)
        return fit(assets, returns, cash, fractions, methods)

    def replayed(sessions, **kwargs):
        calls.append('actual_netting')
        assert all(type(s) is runner.NettingSession for s in sessions)
        assert sessions[0].decision_session == args['cash_returns'][120][0]
        assert kwargs['execution_timing'] == 'next_close'
        assert kwargs['report_periods_per_year'] == 252
        return replay(sessions, **kwargs)

    def forbidden(*a, **kw):
        pytest.fail('old comparator / virtual cost path invoked')

    monkeypatch.setattr(pw, 'fit_portfolio_kelly', fitted)
    monkeypatch.setattr(runner, 'run_offline_security_netting', replayed)
    monkeypatch.setattr(rs04, '_stats_module', forbidden)
    result = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert calls == ['fit_train_only', 'actual_netting']
    assert result['status'] == 'SUCCEEDED'
    net, persisted = result['netting'], result['research_ledger']
    assert len(net['ledger']) == len(persisted['days']) == 5
    assert persisted['initial_session_date'] == args['cash_returns'][120][0].isoformat()
    assert persisted['initial_nav'] == persisted['initial_cash'] == 1000.
    assert persisted['initial_positions'] == []
    for i, row in enumerate(net['ledger']):
        assert row['f_target'] == 1.  # Outer sleeve allocation, not fraction c.
        assert row['fees'] == pytest.approx(sum(abs(v) for v in row['net_trades'].values())*.01)
        assert row['nav_end'] == pytest.approx(row['nav_pre_trade'] - row['fees'])
        assert row['net_return'] == pytest.approx(row['gross_return'] - row['fees']/row['nav_start'])
        assert row['nav_end'] == pytest.approx(sum(row['security_end'].values())+sum(row['virtual_cash_end'].values()))
        pre_cash = row['nav_pre_trade']-sum(row['security_pre'].values())
        assert sum(row['virtual_cash_end'].values()) == pytest.approx(pre_cash-sum(row['net_trades'].values())-row['fees'])
        for symbol in set(row['security_pre']) | set(row['security_end']):
            assert row['security_end'].get(symbol, 0.) == pytest.approx(row['security_pre'].get(symbol, 0.)+row['net_trades'].get(symbol, 0.))
        assert max(abs(v) for v in row['reconciliation_errors'].values()) < 1e-9
        for mark in persisted['days'][i]['positions']:
            price = dict(args['netting_candidate']['close_prices'][mark['symbol']])[args['cash_returns'][121+i][0]]
            assert mark['quantity']*price == pytest.approx(mark['valuation'])
        if i:
            assert row['nav_start'] == net['ledger'][i-1]['nav_end']
    assert net['net_returns'][0] == pytest.approx(-net['ledger'][0]['fees']/1000.)
    assert net['net_returns'][0] < 0
    assert net['ledger'][-1]['inner_target'] == {}  # Explicit fixture end target.
    assert not any(net['ledger'][-1]['security_end'].values())
    assert net['final_nav'] == pytest.approx(1000.*math.prod(1.+v for v in net['net_returns']))
    assert net['final_nav'] != pytest.approx(1000.*math.prod(1.+r['net_return']-r['fees']/r['nav_start'] for r in net['ledger']))
    report = net['unified_net_report']
    expected = unified_report.build_unified_net_report(net['net_returns'], gross_returns=net['gross_returns'], periods_per_year=252).to_dict()
    # The immutable receipt uses JSON arrays for tuple-valued report details.
    import json
    assert report == json.loads(json.dumps(expected)) and report['observations'] == 5
    metrics = {m['name']: m for m in report['metrics']}
    candidate = result['candidate']
    assert candidate['cagr'] == metrics['net_cagr']['value']
    assert candidate['max_drawdown'] == metrics['max_drawdown']['value']
    assert candidate['calmar_ratio'] == metrics['net_calmar_ratio']['value']
    assert candidate['sharpe_ratio'] is metrics['sharpe_per_period']['value'] is None
    assert candidate['win_rate'] is None and candidate['volatility'] is None
    assert metrics['deflated_sharpe_ratio']['value'] is None
    assert 'unified_net_report' not in str(candidate['params'])
    store = open_store(args)
    trial = store.load_research_trial('us_equity', 'rs06_synthetic_netting', result['trial_id'], run_id=candidate['run_id'], param_version=1)
    ledger = store.load_research_ledger(trial.domain, trial.strategy_profile, trial.trial_id, trial.run_id, 1)
    backtest = store.load_backtest_by_run_id(trial.domain, trial.strategy_profile, trial.run_id, param_version=1)
    assert trial.to_dict() == result['trial']
    assert ledger.to_dict() == persisted and backtest.to_dict() == candidate
    assert trial.input_id == result['binding']['input_sha256']
    assert trial.param_set_id == result['binding']['config_sha256']
    assert result['acceptance_binding']['report_sha256'] == pw._candidate_digest(report)


@pytest.mark.parametrize('boundary', ['started', 'backtest', 'ledger', 'terminal'])
def test_unknown_write_success_response_lost_never_replays(tmp_path, monkeypatch, boundary):
    from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore
    args = fixture_args(tmp_path)
    name = {'started':'save_research_trial', 'terminal':'save_research_trial',
            'backtest':'save_backtest_result', 'ledger':'save_research_ledger'}[boundary]
    original = getattr(PerformanceStore, name)
    writes = []

    def response_lost(self, record):
        original(self, record)
        writes.append(name)
        if name != 'save_research_trial' or record.status.value == ('started' if boundary == 'started' else 'succeeded'):
            raise OSError('synthetic lost acknowledgement')

    monkeypatch.setattr(PerformanceStore, name, response_lost)
    result = pw.evaluate_portfolio_kelly_walk_forward(**args)
    no_success(result)
    assert result['status'] == 'UNKNOWN'
    paths = {str(p.relative_to(args['netting_candidate']['local_root'])):p.read_bytes()
             for p in args['netting_candidate']['local_root'].rglob('*.json')}
    count = len(writes)

    def forbidden(*a, **kw):
        pytest.fail('uncertain trial was recomputed')

    monkeypatch.setattr(pw, 'fit_portfolio_kelly', forbidden)
    recovered = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert recovered['status'] == ('SUCCEEDED' if boundary == 'terminal' else 'UNKNOWN')
    assert recovered['trial_id'] == result['trial_id']
    assert len(writes) == count
    assert paths == {str(p.relative_to(args['netting_candidate']['local_root'])):p.read_bytes()
                     for p in args['netting_candidate']['local_root'].rglob('*.json')}


@pytest.mark.parametrize('change', ['decision', 'nan', 'price', 'cost', 'source', 'cash', 'multi_method', 'multi_fraction', 'multi_cost', 'real', 'periods'])
def test_reject_invalid_fixture_inputs_without_success(tmp_path, change):
    args = fixture_args(tmp_path)
    request = args['netting_candidate']
    if change == 'decision':
        request['decision_sessions'][0] = (args['cash_returns'][121][0], args['cash_returns'][121][0])
    elif change == 'nan':
        d, _ = args['asset_returns'][0][1][121]
        args['asset_returns'][0][1][121] = (d, float('nan'))
    elif change == 'price':
        d, p = request['close_prices']['A'][121]
        request['close_prices']['A'][121] = (d, p*2)
    elif change == 'cost':
        args['cost_bps_per_side'] = 200.
    elif change == 'source':
        request['source_revision'] = 'unknown'
    elif change == 'cash':
        d, _ = args['cash_returns'][121]
        args['cash_returns'][121] = (d, .001)
    elif change == 'multi_method':
        args['methods'] = ('qp', 'clip_scale')
    elif change == 'multi_fraction':
        args['fractions'] = (.25, .5)
    elif change == 'multi_cost':
        args['multipliers'] = (1., 2.)
    elif change == 'real':
        args['synthetic'] = False
    elif change == 'periods':
        request['periods_per_year'] = float('nan')
    with pytest.raises(ValueError):
        pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert not request['local_root'].exists()


def test_reject_fee_cash_insufficient_without_weight_reserve(tmp_path, monkeypatch):
    from us_equity_strategies.research import security_netting_runner as runner
    args = fixture_args(tmp_path, bps=1_000_000.)
    observed = []
    original = runner.run_offline_security_netting

    def capture(sessions, **kw):
        observed.extend(sessions)
        with pytest.raises(ValueError, match='FEE_CASH_INSUFFICIENT'):
            original(sessions, **kw)
        raise ValueError('FEE_CASH_INSUFFICIENT')

    monkeypatch.setattr(runner, 'run_offline_security_netting', capture)
    result = pw.evaluate_portfolio_kelly_walk_forward(**args)
    no_success(result)
    assert result['status'] == 'REJECTED'
    fit = pw.fit_portfolio_kelly(('A', 'B'), {s:[v for _,v in rows[:120]] for s,rows in args['asset_returns']}, [0.]*120, (.25,), ('qp',))
    assert observed[0].inner_target == fit['weights']['0.25']['qp']
    assert not list(args['netting_candidate']['local_root'].rglob('backtest*.json'))


@pytest.mark.parametrize('field', ['run_id', 'candidate_id', 'source_revision', 'input', 'config', 'calendar_id', 'periods_per_year'])
def test_recovery_identity_mismatch_rejects_without_rerun(tmp_path, monkeypatch, field):
    args = fixture_args(tmp_path)
    assert pw.evaluate_portfolio_kelly_walk_forward(**args)['status'] == 'SUCCEEDED'
    request = args['netting_candidate']
    if field == 'input':
        args['asset_returns'][0][1][0] = (args['asset_returns'][0][1][0][0], .1)
    elif field == 'config':
        request['end_target'] = None
    elif field == 'source_revision':
        request[field] = 'a'*40
    elif field == 'periods_per_year':
        request[field] = 365
        with pytest.raises(ValueError, match='periods_per_year'):
            pw.evaluate_portfolio_kelly_walk_forward(**args)
        return
    else:
        request[field] += '-other'

    def forbidden(*a, **kw):
        pytest.fail('identity conflict recomputed fit')

    monkeypatch.setattr(pw, 'fit_portfolio_kelly', forbidden)
    result = pw.evaluate_portfolio_kelly_walk_forward(**args)
    no_success(result)
    assert result['status'] == 'REJECTED'


@pytest.mark.parametrize('damage', ['ledger_missing', 'ledger_tamper', 'result_missing', 'metric_tamper', 'quantity_tamper', 'unused_public_tamper', 'terminal_missing', 'started_missing', 'started_tamper'])
def test_recovery_missing_or_tampered_records_fail_closed(tmp_path, monkeypatch, damage):
    import json
    args = fixture_args(tmp_path)
    assert pw.evaluate_portfolio_kelly_walk_forward(**args)['status'] == 'SUCCEEDED'
    root = args['netting_candidate']['local_root']
    target = {'ledger_missing':'ledger.json', 'ledger_tamper':'ledger.json',
              'quantity_tamper':'ledger.json', 'terminal_missing':'terminal.json', 'started_missing':'started.json', 'started_tamper':'started.json'}
    path = next(root.rglob(target[damage])) if damage in target else next(root.rglob('backtest*.json'))
    if damage.endswith('missing'):
        path.unlink()
    else:
        data = json.loads(path.read_text())
        if damage == 'ledger_tamper':
            data['days'][0]['cash'] += 1.
        elif damage == 'metric_tamper':
            data['cagr'] = .314159
        elif damage == 'quantity_tamper':
            data['days'][0]['positions'][0]['quantity'] *= 2.
        elif damage == 'unused_public_tamper':
            data['win_rate'] = .99
        else:
            data['input_id'] = 'tampered'
        path.write_text(json.dumps(data))
    before = {str(p):p.read_bytes() for p in root.rglob('*.json')}

    def forbidden(*a, **kw):
        pytest.fail('damaged record recomputed fit')

    monkeypatch.setattr(pw, 'fit_portfolio_kelly', forbidden)
    result = pw.evaluate_portfolio_kelly_walk_forward(**args)
    no_success(result)
    assert {str(p):p.read_bytes() for p in root.rglob('*.json')} == before


def test_reject_report_ledger_mismatch_without_success(tmp_path, monkeypatch):
    from us_equity_strategies.research import security_netting_runner as runner
    original = runner.run_offline_security_netting

    def tampered(*args, **kwargs):
        result = original(*args, **kwargs)
        result['unified_net_report']['metrics'][0]['value'] += .1
        return result

    monkeypatch.setattr(runner, 'run_offline_security_netting', tampered)
    no_success(pw.evaluate_portfolio_kelly_walk_forward(**fixture_args(tmp_path)))


@pytest.mark.parametrize('observations', [5, 35])
def test_zero_drawdown_and_uncomputable_stay_none(tmp_path, observations):
    args = fixture_args(tmp_path, bps=0., test_sessions=observations)
    for s, rows in args['asset_returns']:
        for i in range(121, 121 + observations):
            rows[i] = (rows[i][0], 0.)
            args['netting_candidate']['close_prices'][s][i] = (rows[i][0], args['netting_candidate']['close_prices'][s][120][1])
    result = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert result['status'] == 'SUCCEEDED'
    metrics = {m['name']:m for m in result['netting']['unified_net_report']['metrics']}
    assert metrics['net_calmar_ratio']['reason_code'] == 'zero_drawdown'
    assert metrics['sharpe_per_period']['reason_code'] == ('insufficient_observations' if observations < 30 else 'zero_variance')
    assert result['candidate']['calmar_ratio'] is None
    assert result['candidate']['sharpe_ratio'] is None
    assert result['candidate']['max_drawdown'] == 0.
    assert result['candidate']['win_rate'] is None


def test_default_off_structure_matches_frozen_base(tmp_path):
    args = fixture_args(tmp_path)
    args.pop('netting_candidate')
    implicit = pw.evaluate_portfolio_kelly_walk_forward(**args)
    explicit = pw.evaluate_portfolio_kelly_walk_forward(**args, netting_candidate=None)
    assert implicit == explicit and implicit['status'] == 'COMPUTED'
    assert 'binding' not in implicit and len(implicit['comparators']) > 1


def test_public_sharpe_annualization_on_one_complete_fold(tmp_path):
    import math
    args = fixture_args(tmp_path, test_sessions=35)
    result = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert result['status'] == 'SUCCEEDED'
    metrics = {m['name']:m for m in result['netting']['unified_net_report']['metrics']}
    assert metrics['sharpe_per_period']['status'] == 'COMPUTED'
    assert result['candidate']['sharpe_ratio'] == metrics['sharpe_per_period']['value']*math.sqrt(252)
    assert result['candidate']['observation_count'] == 35


@pytest.mark.parametrize('field', ['session', 'decision_session', 'final_nav'])
def test_reject_net_book_session_or_nav_identity(tmp_path, monkeypatch, field):
    from us_equity_strategies.research import security_netting_runner as runner
    original = runner.run_offline_security_netting

    def tampered(*args, **kwargs):
        result = original(*args, **kwargs)
        if field == 'final_nav':
            result[field] += 1.
        else:
            result['ledger'][0][field] = '1900-01-01'
        return result

    monkeypatch.setattr(runner, 'run_offline_security_netting', tampered)
    no_success(pw.evaluate_portfolio_kelly_walk_forward(**fixture_args(tmp_path)))


def test_explicit_local_store_no_env_or_cloud(tmp_path, monkeypatch):
    from quant_platform_kit.strategy_lifecycle import performance_store as stores

    def forbidden(*args, **kw):
        pytest.fail('environment/cloud store used')

    monkeypatch.setattr(stores.PerformanceStore, 'from_env', forbidden)
    monkeypatch.setattr(stores, 'get_object_store', forbidden)
    args = fixture_args(tmp_path)
    assert pw.evaluate_portfolio_kelly_walk_forward(**args)['status'] == 'SUCCEEDED'
    assert open_store(args).cloud_bucket == ''


def test_store_symlink_rejected_before_write(tmp_path):
    args = fixture_args(tmp_path)
    (tmp_path/'actual').mkdir()
    (tmp_path/'link').symlink_to(tmp_path/'actual', target_is_directory=True)
    args['netting_candidate']['local_root'] = tmp_path/'link'/'candidate'
    with pytest.raises(ValueError, match='STORE_SYMLINK'):
        pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert not (tmp_path/'actual'/'candidate').exists()


def test_r2_fee_cashflow_counterexample_rejected(tmp_path, monkeypatch):
    import json
    args = fixture_args(tmp_path)
    original = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert original['status'] == 'SUCCEEDED'
    path = next(args['netting_candidate']['local_root'].rglob('ledger.json'))
    data = json.loads(path.read_text())
    assert sum(day['fees'] for day in data['days']) > 0
    for day in data['days']:
        day['trade_net_cashflow'] -= day['fees']
        day['fees'] = 0.
    path.write_text(json.dumps(data))

    def forbidden(*a, **kw):
        pytest.fail('recovery recomputed fit')

    monkeypatch.setattr(pw, 'fit_portfolio_kelly', forbidden)
    no_success(pw.evaluate_portfolio_kelly_walk_forward(**args))


def test_r2_recovery_returns_entire_original_report_and_binding(tmp_path, monkeypatch):
    args = fixture_args(tmp_path)
    first = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert first['status'] == 'SUCCEEDED'

    def forbidden(*a, **kw):
        pytest.fail('recovery recomputed fit')

    monkeypatch.setattr(pw, 'fit_portfolio_kelly', forbidden)
    again = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert again['status'] == 'SUCCEEDED'
    for key in ('netting', 'acceptance_binding', 'research_ledger', 'candidate', 'trial', 'folds'):
        assert again[key] == first[key]
    assert next(args['netting_candidate']['local_root'].rglob('receipt.json')).is_file()


@pytest.mark.parametrize('phase', ['first', 'recovery'])
@pytest.mark.parametrize('component', ['research_trial', 'backtest', 'trial_directory', 'result_directory', 'started', 'ledger', 'terminal', 'receipt', 'result'])
def test_r3_all_existing_store_links_rejected_before_io(tmp_path, monkeypatch, phase, component):
    from quant_platform_kit.strategy_lifecycle.contracts import BacktestResult
    from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore
    args = fixture_args(tmp_path)
    if phase == 'recovery':
        assert pw.evaluate_portfolio_kelly_walk_forward(**args)['status'] == 'SUCCEEDED'
    root = args['netting_candidate']['local_root']
    store = open_store(args)
    trial_dir = root / store._research_key('us_equity', 'rs06_synthetic_netting', 'synthetic-rs06-one', 'started')
    result_path = root / store._backtest_key(BacktestResult('rs06_synthetic_netting', 'us_equity', 'x', {}, run_id='synthetic-rs06-run'))
    paths = {'research_trial':root/'research_trial', 'backtest':root/'backtest', 'trial_directory':trial_dir.parent,
             'result_directory':result_path.parent, 'started':trial_dir, 'ledger':trial_dir.with_name('ledger.json'),
             'terminal':trial_dir.with_name('terminal.json'), 'receipt':trial_dir.with_name('receipt.json'), 'result':result_path}
    link = paths[component]
    link.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path/'outside-store'
    outside.mkdir()
    if link.exists():
        link.rename(outside/'original')
    # Broken final links and links into the same approved job are both denied.
    target = outside if component in ('research_trial', 'backtest', 'trial_directory', 'result_directory') else outside/'record.json'
    link.symlink_to(target, target_is_directory=component in ('research_trial', 'backtest', 'trial_directory', 'result_directory'))
    reads = []

    def forbidden(*a, **kw):
        reads.append(True)
        pytest.fail('store I/O occurred before rejecting an existing symlink')

    monkeypatch.setattr(PerformanceStore, 'load_research_trial', forbidden)
    with pytest.raises(ValueError, match='NETTING_STORE_'):
        pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert reads == []
    assert not (outside/'record.json').exists()


@pytest.mark.parametrize('record', ['started', 'terminal', 'ledger', 'backtest', 'receipt'])
@pytest.mark.parametrize('damage', ['missing', 'extra_content', 'identity'])
def test_r2_all_record_and_receipt_content_bound(tmp_path, monkeypatch, record, damage):
    import json
    args = fixture_args(tmp_path)
    first = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert first['status'] == 'SUCCEEDED'
    root = args['netting_candidate']['local_root']
    path = next(root.rglob('backtest*.json' if record == 'backtest' else record+'.json'))
    if damage == 'missing':
        path.unlink()
    else:
        data = json.loads(path.read_text())
        if damage == 'extra_content':
            data['unexpected_content'] = 'changed'
        elif record == 'receipt':
            data['payload']['binding']['run_id'] += '-wrong'
            body = {k:data[k] for k in ('payload', 'record_digests')}
            data['sha256'] = pw._candidate_digest(body)
        else:
            data['input_id' if record in ('started', 'terminal', 'ledger') else 'source_revision'] = 'wrong'
        path.write_text(json.dumps(data))
    before = {str(p):p.read_bytes() for p in root.rglob('*.json')}

    def forbidden(*a, **kw):
        pytest.fail('tampered trial was recomputed')

    monkeypatch.setattr(pw, 'fit_portfolio_kelly', forbidden)
    no_success(pw.evaluate_portfolio_kelly_walk_forward(**args))
    assert before == {str(p):p.read_bytes() for p in root.rglob('*.json')}


@pytest.mark.parametrize('section', ['report', 'net_book', 'candidate', 'original_state', 'binding'])
def test_r2_receipt_payload_tamper_rejected(tmp_path, monkeypatch, section):
    import json
    args = fixture_args(tmp_path)
    assert pw.evaluate_portfolio_kelly_walk_forward(**args)['status'] == 'SUCCEEDED'
    path = next(args['netting_candidate']['local_root'].rglob('receipt.json'))
    receipt = json.loads(path.read_text())
    payload = receipt['payload']
    if section == 'report':
        payload['netting']['unified_net_report']['metrics'][0]['value'] += .1
    elif section == 'net_book':
        payload['netting']['ledger'][0]['fees'] = 0.
    elif section == 'candidate':
        payload['candidate']['cagr'] = .99
    elif section == 'original_state':
        metric = next(m for m in payload['netting']['unified_net_report']['metrics'] if m['name'] == 'deflated_sharpe_ratio')
        assert metric['value'] is None and metric['status'] == 'UNCOMPUTABLE'
        metric.update(value=0., status='COMPUTED', reason_code=None)
    else:
        payload['acceptance_binding']['input_sha256'] = '0'*64
    # Even a rehashed outer envelope cannot hide a changed bound component.
    receipt['sha256'] = pw._candidate_digest({k:receipt[k] for k in ('payload', 'record_digests')})
    path.write_text(json.dumps(receipt))

    def forbidden(*a, **kw):
        pytest.fail('tampered receipt was recomputed')

    monkeypatch.setattr(pw, 'fit_portfolio_kelly', forbidden)
    no_success(pw.evaluate_portfolio_kelly_walk_forward(**args))


@pytest.mark.parametrize('boundary', ['receipt_ack', 'partial_receipt', 'terminal_before_write'])
def test_r2_receipt_ack_and_terminal_order_remain_unknown(tmp_path, monkeypatch, boundary):
    from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore
    args = fixture_args(tmp_path)
    create = PerformanceStore._create_same
    terminal = PerformanceStore.save_research_trial

    def create_lost(self, key, payload):
        if key.endswith('/receipt.json'):
            if boundary == 'partial_receipt':
                path = self.local_root/key
                with path.open('x') as handle:
                    handle.write('{')
            else:
                create(self, key, payload)
            raise OSError('synthetic receipt write or acknowledgement uncertain')
        create(self, key, payload)

    def terminal_lost(self, record):
        if record.status.value == 'succeeded':
            raise OSError('synthetic terminal not written after complete receipt')
        terminal(self, record)

    if boundary == 'terminal_before_write':
        monkeypatch.setattr(PerformanceStore, 'save_research_trial', terminal_lost)
    else:
        monkeypatch.setattr(PerformanceStore, '_create_same', create_lost)
    first = pw.evaluate_portfolio_kelly_walk_forward(**args)
    no_success(first)
    assert first['status'] == 'UNKNOWN'
    root = args['netting_candidate']['local_root']
    before = {str(p):p.read_bytes() for p in root.rglob('*.json')}
    assert next(root.rglob('receipt.json')).is_file()
    assert not list(root.rglob('terminal.json'))

    def forbidden(*a, **kw):
        pytest.fail('uncertain receipt retried a write or replay')

    monkeypatch.setattr(pw, 'fit_portfolio_kelly', forbidden)
    monkeypatch.setattr(PerformanceStore, 'save_research_trial', forbidden)
    monkeypatch.setattr(PerformanceStore, '_create_same', forbidden)
    again = pw.evaluate_portfolio_kelly_walk_forward(**args)
    no_success(again)
    assert again['status'] == 'UNKNOWN' and again['trial_id'] == first['trial_id']
    assert before == {str(p):p.read_bytes() for p in root.rglob('*.json')}


def test_r2_receipt_exclusive_create_is_immutable(tmp_path):
    import json
    args = fixture_args(tmp_path)
    result = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert result['status'] == 'SUCCEEDED'
    store = open_store(args)
    path = next(store.local_root.rglob('receipt.json'))
    original = path.read_bytes()
    changed = json.loads(original)
    changed['payload']['netting']['final_nav'] += 1.
    with pytest.raises(ValueError):
        store._create_same(str(path.relative_to(store.local_root)), changed)
    assert path.read_bytes() == original


@pytest.mark.parametrize('change', ['report_unknown_state', 'netting_fees', 'safety_envelope'])
def test_r2_followup_rehashed_receipt_cannot_override_records_or_envelope(tmp_path, monkeypatch, change):
    import json
    from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore
    from us_equity_strategies.research import security_netting_runner as runner
    args = fixture_args(tmp_path)
    first = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert first['status'] == 'SUCCEEDED'
    root = args['netting_candidate']['local_root']
    path = next(root.rglob('receipt.json'))
    original_records = {str(p):p.read_bytes() for p in root.rglob('*.json') if p != path}
    receipt = json.loads(path.read_text())
    payload = receipt['payload']
    if change == 'report_unknown_state':
        metric = next(m for m in payload['netting']['unified_net_report']['metrics'] if m['name'] == 'deflated_sharpe_ratio')
        assert metric['value'] is None and metric['status'] == 'UNCOMPUTABLE'
        metric.update(value=0., status='COMPUTED', reason_code=None)
        payload['acceptance_binding']['report_sha256'] = pw._candidate_digest(payload['netting']['unified_net_report'])
        payload['acceptance_binding']['netting_sha256'] = pw._candidate_digest(payload['netting'])
    elif change == 'netting_fees':
        for day in payload['netting']['ledger']:
            day['fees'] = 0.
        payload['acceptance_binding']['netting_sha256'] = pw._candidate_digest(payload['netting'])
    else:
        payload.update(no_order=False, live_ready=True, promotion_eligible=True, budget_effect='RAISE')
    receipt['sha256'] = pw._candidate_digest({k:receipt[k] for k in ('payload', 'record_digests')})
    path.write_text(json.dumps(receipt))
    before = {str(p):p.read_bytes() for p in root.rglob('*.json')}
    assert original_records == {str(p):p.read_bytes() for p in root.rglob('*.json') if p != path}

    def denied(*a, **kw):
        pytest.fail('unexpected fit, netting or store write during recovery')

    monkeypatch.setattr(pw, 'fit_portfolio_kelly', denied)
    monkeypatch.setattr(runner, 'run_offline_security_netting', denied)
    for method in ('_create_same', 'save_research_trial', 'save_research_ledger', 'save_backtest_result'):
        monkeypatch.setattr(PerformanceStore, method, denied)
    recovered = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert before == {str(p):p.read_bytes() for p in root.rglob('*.json')}
    no_success(recovered)


@pytest.mark.parametrize('flat', [False, True])
def test_r2_followup_normal_recovery_revalidates_b3_without_replay(tmp_path, monkeypatch, flat):
    from quant_platform_kit.research_stats import unified_report
    from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore
    from us_equity_strategies.research import security_netting_runner as runner
    args = fixture_args(tmp_path, bps=0. if flat else 100., test_sessions=35 if flat else 5)
    if flat:
        for symbol, rows in args['asset_returns']:
            for i in range(121, len(rows)):
                rows[i] = (rows[i][0], 0.)
                args['netting_candidate']['close_prices'][symbol][i] = (rows[i][0], args['netting_candidate']['close_prices'][symbol][120][1])
    first = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert first['status'] == 'SUCCEEDED'
    root = args['netting_candidate']['local_root']
    before = {str(p):p.read_bytes() for p in root.rglob('*.json')}
    actual_builder = unified_report.build_unified_net_report
    report_inputs = []

    def checked_builder(net_returns, **kwargs):
        if 'gross_returns' in kwargs:
            report_inputs.append((list(net_returns), kwargs))
        return actual_builder(net_returns, **kwargs)

    def denied(*a, **kw):
        pytest.fail('normal recovery must read existing records without fit, engine or writes')

    monkeypatch.setattr(unified_report, 'build_unified_net_report', checked_builder)
    monkeypatch.setattr(pw, 'fit_portfolio_kelly', denied)
    monkeypatch.setattr(runner, 'run_offline_security_netting', denied)
    for method in ('_create_same', 'save_research_trial', 'save_research_ledger', 'save_backtest_result'):
        monkeypatch.setattr(PerformanceStore, method, denied)
    recovered = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert recovered == {**first, 'recovered': True}
    assert before == {str(p):p.read_bytes() for p in root.rglob('*.json')}
    assert len(report_inputs) == 1
    net_returns, kwargs = report_inputs[0]
    assert net_returns == [day['daily_return'] for day in recovered['research_ledger']['days']]
    assert kwargs['gross_returns'] == recovered['netting']['gross_returns']
    assert kwargs['periods_per_year'] == 252
    metrics = {m['name']:m for m in recovered['netting']['unified_net_report']['metrics']}
    assert metrics['deflated_sharpe_ratio']['status'] == 'UNCOMPUTABLE'
    assert metrics['deflated_sharpe_ratio']['value'] is None
    assert metrics['deflated_sharpe_ratio']['reason_code'] == 'missing_trial_evidence'
    if flat:
        assert metrics['net_calmar_ratio']['reason_code'] == 'zero_drawdown'
        assert metrics['net_calmar_ratio']['value'] is None
    assert recovered['no_order'] is True and recovered['live_ready'] is False
    assert recovered['promotion_eligible'] is False and recovered['budget_effect'] == 'NONE'


@pytest.mark.parametrize('change', ['cash', 'security', 'nav', 'net_return', 'gross_return', 'missing_day'])
def test_r2_followup_book_fields_checked_against_authoritative_ledger(tmp_path, monkeypatch, change):
    import json
    from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore
    from us_equity_strategies.research import security_netting_runner as runner
    args = fixture_args(tmp_path)
    first = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert first['status'] == 'SUCCEEDED'
    root = args['netting_candidate']['local_root']
    path = next(root.rglob('receipt.json'))
    receipt = json.loads(path.read_text())
    net = receipt['payload']['netting']
    if change == 'cash':
        net['ledger'][0]['virtual_cash_end']['inner_residual_cash'] += 1.
    elif change == 'security':
        net['ledger'][0]['security_end']['A'] += 1.
    elif change == 'nav':
        net['ledger'][0]['nav_end'] += 1.
    elif change == 'net_return':
        net['ledger'][0]['net_return'] += .1
        net['net_returns'][0] += .1
    elif change == 'gross_return':
        net['ledger'][0]['gross_return'] += .1
        net['gross_returns'][0] += .1
    else:
        net['ledger'].pop()
    receipt['payload']['acceptance_binding']['netting_sha256'] = pw._candidate_digest(net)
    receipt['sha256'] = pw._candidate_digest({k:receipt[k] for k in ('payload', 'record_digests')})
    path.write_text(json.dumps(receipt))
    before = {str(p):p.read_bytes() for p in root.rglob('*.json')}

    def denied(*a, **kw):
        pytest.fail('conflicting receipt must not be replayed or written')

    monkeypatch.setattr(pw, 'fit_portfolio_kelly', denied)
    monkeypatch.setattr(runner, 'run_offline_security_netting', denied)
    for method in ('_create_same', 'save_research_trial', 'save_research_ledger', 'save_backtest_result'):
        monkeypatch.setattr(PerformanceStore, method, denied)
    no_success(pw.evaluate_portfolio_kelly_walk_forward(**args))
    assert before == {str(p):p.read_bytes() for p in root.rglob('*.json')}


def synthetic_budget(**limits):
    """Explicit engineering fixture values, never a production risk preset."""
    from us_equity_strategies.portfolio_risk_budget import (
        PortfolioAssetRiskSpec, PortfolioRiskBudgetPolicy,
    )
    policy = dict(cash_symbol='SYNTHETIC_CASH', max_effective_risk_exposure=100.,
                  max_symbol_weights={}, max_underlying_effective_exposure={})
    policy.update(limits)
    return dict(policy=PortfolioRiskBudgetPolicy(**policy), asset_risk_specs={
        'A': PortfolioAssetRiskSpec('A', 3., 'SYNTHETIC_RISK'),
        'B': PortfolioAssetRiskSpec('B', 1., 'SYNTHETIC_RISK'),
        'SYNTHETIC_CASH': PortfolioAssetRiskSpec('SYNTHETIC_CASH', 1., 'USD_CASH', True),
    })


def test_budget_default_off_never_assesses_or_changes_identity(tmp_path, monkeypatch):
    from us_equity_strategies import portfolio_risk_budget as budget

    def forbidden(**kwargs):
        pytest.fail('budget assessor invoked without explicit opt-in')

    monkeypatch.setattr(budget, 'assess_portfolio_risk_budget', forbidden)
    args = fixture_args(tmp_path)
    result = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert result['status'] == 'SUCCEEDED'
    assert 'risk_budget' not in result['binding']
    assert result['netting']['ledger'][0]['inner_target'] == result['folds'][0]['portfolio_fit']['weights']['0.25']['qp']
    assert result['netting']['ledger'][-1]['inner_target'] == {}


@pytest.mark.parametrize('unconfigured', [None, {}, {'policy': None},
    {'request_status': 'NOT_CONFIGURED', 'requested': None, 'final_constraints': None}])
def test_budget_explicit_unconfigured_never_falls_back(tmp_path, unconfigured):
    args = fixture_args(tmp_path)
    args['netting_candidate']['risk_budget'] = unconfigured
    with pytest.raises(ValueError, match='NETTING_RISK_BUDGET'):
        pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert not args['netting_candidate']['local_root'].exists()


@pytest.mark.parametrize('change', ['missing_security', 'cash_collision', 'cash_security', 'declared_current'])
def test_budget_rejects_unmapped_or_arbitrary_book(tmp_path, change):
    from dataclasses import replace
    args = fixture_args(tmp_path)
    budget = synthetic_budget()
    if change == 'missing_security':
        del budget['asset_risk_specs']['B']
    elif change == 'cash_collision':
        budget['policy'] = replace(budget['policy'], cash_symbol='A')
    elif change == 'cash_security':
        budget['asset_risk_specs']['A'] = replace(budget['asset_risk_specs']['A'], is_cash=True)
    else:
        budget['current_weights'] = {'SYNTHETIC_CASH': 1.}
    args['netting_candidate']['risk_budget'] = budget
    with pytest.raises(ValueError, match='NETTING_RISK_BUDGET'):
        pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert not args['netting_candidate']['local_root'].exists()


@pytest.mark.parametrize('limits,expected_status', [({}, 'APPROVE'),
    ({'max_effective_risk_exposure': .05}, 'REDUCE'),
    ({'max_symbol_weights': {'A': .005}}, 'REDUCE'),
    ({'max_underlying_effective_exposure': {'SYNTHETIC_RISK': .05}}, 'REDUCE')])
def test_budget_actual_target_uses_assessor_and_virtual_cash(tmp_path, monkeypatch, limits, expected_status):
    from dataclasses import asdict
    from us_equity_strategies import portfolio_risk_budget as budgets
    from us_equity_strategies.research import security_netting_runner as runner
    args = fixture_args(tmp_path)
    budget = synthetic_budget(**limits)
    args['netting_candidate']['risk_budget'] = budget
    original_assess = budgets.assess_portfolio_risk_budget
    original_run = runner.run_offline_security_netting
    assessments, actual_sessions = [], []

    def assess(**kwargs):
        result = original_assess(**kwargs)
        assessments.append((kwargs['target_weights'], result))
        return result

    def replay(sessions, **kwargs):
        actual_sessions.extend(sessions)
        return original_run(sessions, **kwargs)

    monkeypatch.setattr(budgets, 'assess_portfolio_risk_budget', assess)
    monkeypatch.setattr(runner, 'run_offline_security_netting', replay)
    result = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert result['status'] == 'SUCCEEDED'
    assert len(assessments) == len(actual_sessions) == 5
    assert assessments[0][1]['status'] == expected_status
    fitted = result['folds'][0]['portfolio_fit']['weights']['0.25']['qp']
    assert assessments[0][0] == {**fitted, 'SYNTHETIC_CASH': 1.-sum(fitted.values())}
    for (target, assessed), session, row in zip(assessments, actual_sessions, result['netting']['ledger'], strict=True):
        recommended = assessed['recommended_target_weights']
        securities = {s:w for s,w in recommended.items() if s != 'SYNTHETIC_CASH'}
        assert session.inner_target == row['inner_target'] == securities
        assert sum(securities.values()) + recommended.get('SYNTHETIC_CASH', 0.) == pytest.approx(1.)
        assert 'SYNTHETIC_CASH' not in row['security_end']
        assert row['virtual_cash_end']['inner_residual_cash'] == pytest.approx(
            row['nav_pre_trade']*recommended.get('SYNTHETIC_CASH', 0.) - row['fees'])
    # The explicit existing liquidation override is assessed as all cash too.
    assert assessments[-1][0] == {'SYNTHETIC_CASH': 1.}
    assert actual_sessions[-1].inner_target == {}
    bound = result['binding']['risk_budget']
    assert bound['policy'] == asdict(budget['policy'])
    assert bound['asset_risk_specs'] == {s:asdict(v) for s,v in budget['asset_risk_specs'].items()}
    assert result['trial']['param_set_id'] == result['binding']['config_sha256']
    assert not result['live_ready'] and result['budget_effect'] == 'NONE'


def test_budget_parked_after_override_never_becomes_successful_liquidation(tmp_path, monkeypatch):
    from us_equity_strategies.research import security_netting_runner as runner
    args = fixture_args(tmp_path)
    fit = pw.fit_portfolio_kelly(('A', 'B'), {s:[v for _,v in rows[:120]] for s,rows in args['asset_returns']},
                                [0.]*120, (.25,), ('qp',))
    # Earlier fitted residual fits this fixture cap; the final all-cash override does not.
    cap = 1.-sum(fit['weights']['0.25']['qp'].values())/2.
    args['netting_candidate']['risk_budget'] = synthetic_budget(max_symbol_weights={'SYNTHETIC_CASH': cap})

    def forbidden(*args, **kwargs):
        pytest.fail('PARKED empty recommendation reached netting')

    monkeypatch.setattr(runner, 'run_offline_security_netting', forbidden)
    result = pw.evaluate_portfolio_kelly_walk_forward(**args)
    no_success(result)
    assert result['status'] == 'REJECTED'
    assert result['reason_code'].startswith('NETTING_RISK_BUDGET_PARKED:')
    assert 'symbol weight limit' in result['reason_code']
    terminal = open_store(args).load_research_trial('us_equity', 'rs06_synthetic_netting', result['trial_id'])
    assert terminal.status.value == 'rejected' and terminal.reason_code == 'netting_input_or_ledger_rejected'
    assert not list(args['netting_candidate']['local_root'].rglob('ledger.json'))
    assert not list(args['netting_candidate']['local_root'].rglob('backtest*.json'))


def test_budget_turnover_uses_same_session_net_book_and_effective_target(tmp_path, monkeypatch):
    from us_equity_strategies import portfolio_risk_budget as budgets
    args = fixture_args(tmp_path)
    budget = synthetic_budget(max_one_way_risk_turnover=.1)
    args['netting_candidate']['risk_budget'] = budget
    original = budgets.assess_portfolio_risk_budget
    calls = []

    def assess(**kwargs):
        result = original(**kwargs)
        calls.append((kwargs, result))
        return result

    monkeypatch.setattr(budgets, 'assess_portfolio_risk_budget', assess)
    result = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert result['status'] == 'SUCCEEDED'
    rows = result['netting']['ledger']
    assert len(calls) == len(rows) == 5
    assert calls[0][0]['current_weights'] == {'SYNTHETIC_CASH': 1.}
    fit = result['folds'][0]['portfolio_fit']['weights']['0.25']['qp']
    for i, ((inputs, assessed), row) in enumerate(zip(calls, rows, strict=True)):
        current = inputs['current_weights']
        assert current == pytest.approx({**{s:v/row['nav_pre_trade'] for s,v in row['security_pre'].items()},
                                        'SYNTHETIC_CASH': (row['nav_pre_trade']-sum(row['security_pre'].values()))/row['nav_pre_trade']})
        expected = {s:w for s,w in assessed['recommended_target_weights'].items() if s != 'SYNTHETIC_CASH'}
        assert row['inner_target'] == expected
        assert row['security_end'] == pytest.approx({s:row['nav_pre_trade']*w for s,w in expected.items()})
        assert sum(max(v, 0.) for v in row['net_trades'].values())/row['nav_pre_trade'] <= .1+1e-12
        assert row['fees'] == pytest.approx(sum(abs(v) for v in row['net_trades'].values())*.01)
        assert row['nav_end'] == pytest.approx(sum(row['security_end'].values())+sum(row['virtual_cash_end'].values()))
        proposal = inputs['target_weights']
        assert proposal == ({'SYNTHETIC_CASH': 1.} if i == 4 else {**fit, 'SYNTHETIC_CASH': 1.-sum(fit.values())})
    assert calls[1][0]['current_weights']['A'] != pytest.approx(rows[0]['inner_target']['A'])
    assert rows[-1]['inner_target'] == {} and not any(rows[-1]['security_end'].values())
    assert rows[-1]['fees'] > 0.  # Sales still pay original two-sided execution fees.
    assert result['binding']['risk_budget']['turnover_current_book_basis']


@pytest.mark.parametrize('change', ['policy', 'look_through', 'enable', 'disable'])
def test_budget_recovery_identity_cannot_adopt_different_assessment(tmp_path, monkeypatch, change):
    from dataclasses import replace
    args = fixture_args(tmp_path)
    budget = synthetic_budget()
    if change != 'enable':
        args['netting_candidate']['risk_budget'] = budget
    first = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert first['status'] == 'SUCCEEDED'
    root = args['netting_candidate']['local_root']
    before = {str(p):p.read_bytes() for p in root.rglob('*.json')}
    if change == 'policy':
        budget['policy'] = replace(budget['policy'], max_effective_risk_exposure=99.)
    elif change == 'look_through':
        budget['asset_risk_specs']['A'] = replace(budget['asset_risk_specs']['A'], effective_exposure_factor=2.)
    elif change == 'enable':
        args['netting_candidate']['risk_budget'] = budget
    else:
        del args['netting_candidate']['risk_budget']

    def forbidden(*args, **kwargs):
        pytest.fail('budget identity conflict recomputed fit')

    monkeypatch.setattr(pw, 'fit_portfolio_kelly', forbidden)
    result = pw.evaluate_portfolio_kelly_walk_forward(**args)
    no_success(result)
    assert result['status'] == 'REJECTED' and result['reason_code'] == 'NETTING_CANDIDATE_IDENTITY_MISMATCH'
    assert before == {str(p):p.read_bytes() for p in root.rglob('*.json')}


def test_budget_same_identity_recovers_without_refit_or_netting(tmp_path, monkeypatch):
    from us_equity_strategies.research import security_netting_runner as runner
    args = fixture_args(tmp_path)
    args['netting_candidate']['risk_budget'] = synthetic_budget(max_effective_risk_exposure=.05)
    first = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert first['status'] == 'SUCCEEDED'

    def forbidden(*args, **kwargs):
        pytest.fail('budgeted receipt was replayed')

    monkeypatch.setattr(pw, 'fit_portfolio_kelly', forbidden)
    monkeypatch.setattr(runner, 'run_offline_security_netting', forbidden)
    assert pw.evaluate_portfolio_kelly_walk_forward(**args) == {**first, 'recovered': True}


def test_budget_approve_follows_epsilon_filtered_recommendation(tmp_path, monkeypatch):
    args = fixture_args(tmp_path, bps=0.)
    args['netting_candidate']['risk_budget'] = synthetic_budget()
    original = pw.fit_portfolio_kelly

    def fitted(*args, **kwargs):
        result = original(*args, **kwargs)
        result['weights']['0.25']['qp'] = {'A': 1., 'B': 5e-13}
        return result

    monkeypatch.setattr(pw, 'fit_portfolio_kelly', fitted)
    result = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert result['status'] == 'SUCCEEDED'
    assert result['netting']['ledger'][0]['inner_target'] == {'A': 1.}


def test_budget_snapshots_applied_mutable_limit_maps(tmp_path, monkeypatch):
    args = fixture_args(tmp_path)
    budget = synthetic_budget(max_symbol_weights={'A': .005})
    args['netting_candidate']['risk_budget'] = budget
    original = pw.fit_portfolio_kelly

    def fitted(*args, **kwargs):
        budget['policy'].max_symbol_weights['A'] = 1.
        return original(*args, **kwargs)

    monkeypatch.setattr(pw, 'fit_portfolio_kelly', fitted)
    result = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert result['status'] == 'SUCCEEDED'
    assert result['binding']['risk_budget']['policy']['max_symbol_weights'] == {'A': .005}
    assert result['netting']['ledger'][0]['inner_target']['A'] == pytest.approx(.005)


def test_budget_invalid_policy_retains_assessor_rejection(tmp_path, monkeypatch):
    from us_equity_strategies.research import security_netting_runner as runner
    args = fixture_args(tmp_path)
    args['netting_candidate']['risk_budget'] = synthetic_budget(max_effective_risk_exposure=-1.)

    def forbidden(*args, **kwargs):
        pytest.fail('invalid policy reached netting')

    monkeypatch.setattr(runner, 'run_offline_security_netting', forbidden)
    result = pw.evaluate_portfolio_kelly_walk_forward(**args)
    no_success(result)
    assert result['reason_code'] == 'NETTING_RISK_BUDGET_PARKED:invalid maximum effective risk exposure'


def test_turnover_parked_stops_before_successful_book(tmp_path):
    args = fixture_args(tmp_path)
    args['netting_candidate']['risk_budget'] = synthetic_budget(
        max_one_way_risk_turnover=.1, max_symbol_weights={'SYNTHETIC_CASH': .5})
    result = pw.evaluate_portfolio_kelly_walk_forward(**args)
    no_success(result)
    assert result['status'] == 'REJECTED' and result['reason_code'].startswith('NETTING_RISK_BUDGET_PARKED:')
    assert not list(args['netting_candidate']['local_root'].rglob('ledger.json'))


@pytest.mark.parametrize('change', ['synthetic', 'unit', 'f', 'cash_return', 'cash_role', 'initial_f',
                                    'cash_target', 'unknown_held', 'missing_policy'])
def test_turnover_runner_refuses_unsupported_context(tmp_path, change):
    from us_equity_strategies.research import security_netting_runner as runner
    args = fixture_args(tmp_path)
    budget = synthetic_budget(max_one_way_risk_turnover=.1)
    context = {**budget, 'synthetic': True, 'valuation_basis': runner.RS06_TURNOVER_CURRENT_BOOK_BASIS}
    day = args['cash_returns'][121][0]
    row = runner.NettingSession(day, args['cash_returns'][120][0], .5 if change == 'f' else 1.,
                                {'A': .2, 'SYNTHETIC_CASH': .5} if change == 'cash_target' else {'A': .2},
                                {'A': 0., 'B': 0., 'UNKNOWN': 0.}, .01 if change == 'cash_return' else 0.)
    if change == 'synthetic':context['synthetic'] = False
    if change == 'unit':context['valuation_basis'] = 'USD_LABEL_WITHOUT_SHARED_UNIT_EVIDENCE'
    if change == 'missing_policy':context['policy'] = None
    with pytest.raises(ValueError, match='NETTING_RISK_BUDGET'):
        runner.run_offline_security_netting([row], initial_nav=1000., initial_f=.5 if change == 'initial_f' else 1.,
            initial_inner_weights={'UNKNOWN': .2} if change == 'unknown_held' else {}, spec=args['netting_candidate']['spec'],
            fee_cash_role='outer_cash_leg' if change == 'cash_role' else 'inner_residual_cash',
            execution_timing='next_close', report_periods_per_year=252, rs06_risk_budget=context)


def test_turnover_inconsistent_actual_current_book_is_rejected(tmp_path, monkeypatch):
    from quant_platform_kit.research_stats import security_netting
    args = fixture_args(tmp_path)
    args['netting_candidate']['risk_budget'] = synthetic_budget(max_one_way_risk_turnover=.1)
    original = security_netting.drift_inner_weights
    count = 0

    def inconsistent(*args, **kwargs):
        nonlocal count
        count += 1
        weights, ret = original(*args, **kwargs)
        return (weights, ret+.1) if count == 2 else (weights, ret)

    monkeypatch.setattr(security_netting, 'drift_inner_weights', inconsistent)
    result = pw.evaluate_portfolio_kelly_walk_forward(**args)
    no_success(result)
    assert result['reason_code'] == 'NETTING_RISK_BUDGET_CURRENT_BOOK_INVALID'


@pytest.mark.parametrize('change', ['same', 'policy', 'basis'])
def test_turnover_recovery_binds_policy_and_book_basis_without_replay(tmp_path, monkeypatch, change):
    from dataclasses import replace
    from us_equity_strategies.research import security_netting_runner as runner
    args = fixture_args(tmp_path)
    args['netting_candidate']['risk_budget'] = synthetic_budget(max_one_way_risk_turnover=.1)
    first = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert first['status'] == 'SUCCEEDED'
    root = args['netting_candidate']['local_root']
    before = {str(p):p.read_bytes() for p in root.rglob('*.json')}
    if change == 'policy':
        budget = args['netting_candidate']['risk_budget']
        budget['policy'] = replace(budget['policy'], max_one_way_risk_turnover=.11)
    elif change == 'basis':
        monkeypatch.setattr(runner, 'RS06_TURNOVER_CURRENT_BOOK_BASIS', 'changed_test_semantics')

    def forbidden(*args, **kwargs):
        pytest.fail('turnover recovery replayed fit/netting')

    monkeypatch.setattr(pw, 'fit_portfolio_kelly', forbidden)
    monkeypatch.setattr(runner, 'run_offline_security_netting', forbidden)
    recovered = pw.evaluate_portfolio_kelly_walk_forward(**args)
    if change == 'same':assert recovered == {**first, 'recovered': True}
    else:
        no_success(recovered)
        assert recovered['reason_code'] == 'NETTING_CANDIDATE_IDENTITY_MISMATCH'
    assert before == {str(p):p.read_bytes() for p in root.rglob('*.json')}


def test_turnover_future_rows_do_not_change_fixed_prefix(tmp_path):
    outputs = []
    for n in (5, 6):
        args = fixture_args(tmp_path/str(n), test_sessions=n)
        args['netting_candidate']['end_target'] = None
        args['netting_candidate']['risk_budget'] = synthetic_budget(max_one_way_risk_turnover=.1)
        result = pw.evaluate_portfolio_kelly_walk_forward(**args)
        assert result['status'] == 'SUCCEEDED'
        outputs.append(result['netting']['ledger'])
    assert outputs[0] == outputs[1][:5]


def test_turnover_recovery_rejects_effective_target_changed_in_receipt(tmp_path, monkeypatch):
    import json
    args = fixture_args(tmp_path)
    args['netting_candidate']['risk_budget'] = synthetic_budget(max_one_way_risk_turnover=.1)
    assert pw.evaluate_portfolio_kelly_walk_forward(**args)['status'] == 'SUCCEEDED'
    path = next(args['netting_candidate']['local_root'].rglob('receipt.json'))
    receipt = json.loads(path.read_text())
    receipt['payload']['netting']['ledger'][0]['inner_target']['A'] += .1
    receipt['payload']['acceptance_binding']['netting_sha256'] = pw._candidate_digest(receipt['payload']['netting'])
    receipt['sha256'] = pw._candidate_digest({k:receipt[k] for k in ('payload', 'record_digests')})
    path.write_text(json.dumps(receipt))
    monkeypatch.setattr(pw, 'fit_portfolio_kelly', lambda *a, **k:pytest.fail('tampered receipt refit'))
    no_success(pw.evaluate_portfolio_kelly_walk_forward(**args))


def cost_companion_options(args):
    request = args['netting_candidate']
    return dict(policy_id='RESEARCH_LONG_ONLY_WEIGHTED_AVERAGE_BUY_COST_CAPITALIZED_V1',
                synthetic_book_id='rs06-declared-cost-book',
                opening_book=dict(session=request['decision_sessions'][0][1].isoformat(),
                                  currency='USD', cash=request['initial_nav'], positions={}),
                fill_marks={day.isoformat(): {symbol: dict(rows)[day]
                                             for symbol, rows in request['close_prices'].items()}
                            for day, _ in request['decision_sessions']})


def cost_companion_args(tmp_path):
    args = fixture_args(tmp_path)
    args['netting_candidate']['research_cost_inputs'] = cost_companion_options(args)
    return args


def cost_companion_files(args):
    root = args['netting_candidate']['local_root']
    return {str(p.relative_to(root)): p.read_bytes().hex() for p in root.rglob('*.json')}


def test_cost_companion_absent_option_baseline_bytes(tmp_path):
    import json
    args = fixture_args(tmp_path)
    first = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert first['status'] == 'SUCCEEDED'
    assert not any('research_cost' in key for key in first['netting'])
    assert not any('research_cost' in key for key in first['binding'])
    before = cost_companion_files(args)
    assert len(before) == 5
    recovered = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert recovered == {**first, 'recovered': True}
    assert before == cost_companion_files(args)
    # The author harness preserves this exact baseline artifact before source
    # edits and compares it after implementation; normal CI needs no gold file.
    (tmp_path/'absent-option-evidence.json').write_text(json.dumps(
        dict(result=first, files=before), sort_keys=True, allow_nan=False))


def test_cost_companion_nonzero_save_reopen_and_economics(tmp_path, monkeypatch):
    from us_equity_strategies.research import security_netting_runner as runner
    legacy = pw.evaluate_portfolio_kelly_walk_forward(**fixture_args(tmp_path/'legacy'))
    args = cost_companion_args(tmp_path/'cost')
    result = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert result['status'] == 'SUCCEEDED'
    net = result['netting']
    assert 'research_cost_receipt' in net
    cost = net['research_cost_receipt']
    binding = net['research_cost_binding']
    assert cost['status'] == 'PASS_SCOPED_SYNTHETIC'
    assert cost['source_origin_verified'] is cost['broker_tax_qualified'] is False
    assert binding['candidate_identity'] == result['binding']
    assert binding['options_sha256'] == pw._candidate_digest(args['netting_candidate']['research_cost_inputs'])
    for key in ('receipt_id', 'source_hash', 'ledger_hash'):
        assert binding[key] == cost[key]
    assert cost['ledger_identity']['run_id'] != result['research_ledger']['run_id']
    assert cost['ledger_hash'] != pw._candidate_digest(result['research_ledger'])
    assert cost['days'][-1]['realized_net_cumulative'] == pytest.approx(net['final_nav']-1000.)
    assert cost['days'][-1]['unrealized'] == pytest.approx(0.)
    assert sum(row['fees'] for row in net['ledger']) > 0
    assert {k:v for k,v in net.items() if not k.startswith('research_cost_')} == legacy['netting']
    for key in ('total_return', 'cagr', 'calmar_ratio', 'max_drawdown', 'sharpe_ratio'):
        assert result['candidate'][key] == legacy['candidate'][key]
    metrics = {m['name']:m for m in net['unified_net_report']['metrics']}
    assert metrics['deflated_sharpe_ratio']['value'] is None
    assert result['candidate']['sharpe_ratio'] is None
    before = cost_companion_files(args)
    assert len(before) == 5
    def forbidden(*a, **k):
        pytest.fail('cost recovery replayed fit/netter')
    monkeypatch.setattr(pw, 'fit_portfolio_kelly', forbidden)
    monkeypatch.setattr(runner, 'run_offline_security_netting', forbidden)
    monkeypatch.setattr(runner, '_research_cost_day', forbidden)
    recovered = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert recovered == {**result, 'recovered': True}
    assert before == cost_companion_files(args)


@pytest.mark.parametrize('change', ['policy', 'book', 'opening', 'marks', 'cutoff', 'run', 'trial', 'calendar', 'remove'])
def test_cost_companion_changed_request_never_recovers_stale(tmp_path, monkeypatch, change):
    from us_equity_strategies.research import security_netting_runner as runner
    args = cost_companion_args(tmp_path)
    first = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert 'research_cost_receipt' in first['netting']
    before = cost_companion_files(args)
    request = args['netting_candidate']; options = request['research_cost_inputs']
    if change == 'policy': options['policy_id'] = 'OTHER'
    elif change == 'book': options['synthetic_book_id'] += '-changed'
    elif change == 'opening': options['opening_book']['cash'] += 1.
    elif change == 'marks': next(iter(options['fill_marks'].values()))['A'] *= 2
    elif change == 'cutoff': options['fill_marks'].pop(next(reversed(options['fill_marks'])))
    elif change in ('run', 'trial'): request[change+'_id'] += '-changed'
    elif change == 'calendar': request['calendar_id'] += '-changed'
    else: del request['research_cost_inputs']
    def forbidden(*a, **k):
        pytest.fail('changed cost identity replayed')
    monkeypatch.setattr(pw, 'fit_portfolio_kelly', forbidden)
    monkeypatch.setattr(runner, 'run_offline_security_netting', forbidden)
    try:
        no_success(pw.evaluate_portfolio_kelly_walk_forward(**args))
    except ValueError:
        pass  # Invalid explicit inputs may refuse before any store access.
    assert before == cost_companion_files(args)


@pytest.mark.parametrize('change', ['missing', 'basis', 'quantity', 'mark', 'fee', 'realized', 'cash',
                                  'identity', 'source', 'cutoff', 'revision', 'late', 'missing_fact',
                                  'bridge', 'options_digest', 'boolean_shape', 'bridge_shape'])
def test_cost_companion_rehashed_tampering_refuses_without_replay(tmp_path, monkeypatch, change):
    import json
    from us_equity_strategies.research import security_netting_runner as runner
    args = cost_companion_args(tmp_path)
    first = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert 'research_cost_receipt' in first['netting']
    path = next(args['netting_candidate']['local_root'].rglob('receipt.json'))
    envelope = json.loads(path.read_text()); net = envelope['payload']['netting']
    cost = net['research_cost_receipt']
    first_day = cost['days'][0]
    if change == 'missing': del net['research_cost_receipt']
    elif change in ('basis', 'quantity', 'mark'):
        position = next(iter(first_day['positions'].values()))
        position['total_basis' if change == 'basis' else change] += 1.
    elif change == 'fee': first_day['fills'][0]['fee'] += 1.
    elif change == 'realized': first_day['realized_net_cumulative'] += 1.
    elif change == 'cash': first_day['cash'] += 1.
    elif change == 'identity': cost['ledger_identity']['run_id'] += '-foreign'
    elif change == 'source': cost['source_label'] = 'BROKER'
    elif change == 'cutoff': cost['cutoff'] = '2099-01-01'
    elif change == 'revision': first_day['fills'][0]['revision'] = 2
    elif change == 'late': first_day['fills'][0]['known_at'] = '2099-01-01'
    elif change == 'missing_fact': del first_day['realized_net_cumulative']
    elif change == 'bridge': net['research_cost_binding']['candidate_identity']['calendar_id'] = 'foreign'
    elif change == 'bridge_shape': net['research_cost_binding']['candidate_identity']['periods_per_year'] = 252.
    elif change == 'boolean_shape': cost['source_origin_verified'] = 0
    else: net['research_cost_binding']['options_sha256'] = '0'*64
    # Attacker updates every enclosing/self digest, including the companion
    # link. Semantic and current-request cross checks must still reject it.
    if change != 'missing':
        # 0 == False in Python, but is a different immutable JSON receipt.
        if change != 'boolean_shape':
            cost['receipt_id'] = pw._candidate_digest({k:v for k,v in cost.items() if k != 'receipt_id'})
        for key in ('receipt_id', 'source_hash', 'ledger_hash'):
            net['research_cost_binding'][key] = cost[key]
    envelope['payload']['acceptance_binding']['netting_sha256'] = pw._candidate_digest(net)
    envelope['sha256'] = pw._candidate_digest({k:envelope[k] for k in ('payload', 'record_digests')})
    path.write_text(json.dumps(envelope))
    before = cost_companion_files(args)
    def forbidden(*a, **k):
        pytest.fail('tampered cost recovery replayed')
    monkeypatch.setattr(pw, 'fit_portfolio_kelly', forbidden)
    monkeypatch.setattr(runner, 'run_offline_security_netting', forbidden)
    monkeypatch.setattr(runner, '_research_cost_day', forbidden)
    no_success(pw.evaluate_portfolio_kelly_walk_forward(**args))
    assert before == cost_companion_files(args)


@pytest.mark.parametrize('change', ['none', 'opening', 'cash_none', 'marks', 'corrections', 'late_fees'])
def test_cost_companion_missing_or_unsupported_input_refused(tmp_path, change):
    args = cost_companion_args(tmp_path)
    options = args['netting_candidate']['research_cost_inputs']
    if change == 'none': args['netting_candidate']['research_cost_inputs'] = None
    elif change == 'cash_none': options['opening_book']['cash'] = None
    elif change in ('opening', 'marks'): del options['opening_book' if change == 'opening' else 'fill_marks']
    else: options[change] = [{'id':'unsupported'}]
    try:
        result = pw.evaluate_portfolio_kelly_walk_forward(**args)
    except ValueError:
        return
    no_success(result)


@pytest.mark.parametrize('boundary', ['fit', 'native'])
def test_cost_companion_freezes_caller_before_callbacks_and_after_return(tmp_path, monkeypatch, boundary):
    import copy
    from us_equity_strategies.research import security_netting_runner as runner
    args = cost_companion_args(tmp_path)
    original = args['netting_candidate']['research_cost_inputs']
    frozen = copy.deepcopy(original)
    target, name = (pw, 'fit_portfolio_kelly') if boundary == 'fit' else (runner, 'run_offline_security_netting')
    actual = getattr(target, name)
    def mutating(*a, **kw):
        original['synthetic_book_id'] = 'caller-changed'
        original['opening_book']['cash'] = None
        next(iter(original['fill_marks'].values()))['A'] *= 2
        if boundary == 'native':
            assert kw['research_cost_inputs'] == frozen
        return actual(*a, **kw)
    monkeypatch.setattr(target, name, mutating)
    first = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert first['status'] == 'SUCCEEDED'
    assert 'research_cost_receipt' in first['netting']
    assert first['binding']['research_cost_inputs'] == frozen
    before = cost_companion_files(args)
    original.clear()
    assert before == cost_companion_files(args)
    args['netting_candidate']['research_cost_inputs'] = frozen
    def forbidden(*a, **kw):
        pytest.fail('frozen request recovery replayed')
    monkeypatch.setattr(pw, 'fit_portfolio_kelly', forbidden)
    monkeypatch.setattr(runner, 'run_offline_security_netting', forbidden)
    assert pw.evaluate_portfolio_kelly_walk_forward(**args) == {**first, 'recovered': True}
    assert before == cost_companion_files(args)


@pytest.mark.parametrize('boundary', ['started', 'backtest', 'ledger', 'terminal'])
def test_cost_companion_existing_uncertain_write_contract(tmp_path, monkeypatch, boundary):
    import sys
    original = fixture_args
    def with_cost(*a, **kw):
        args = original(*a, **kw)
        args['netting_candidate']['research_cost_inputs'] = cost_companion_options(args)
        return args
    monkeypatch.setattr(sys.modules[__name__], 'fixture_args', with_cost)
    test_unknown_write_success_response_lost_never_replays(tmp_path, monkeypatch, boundary)


@pytest.mark.parametrize('boundary', ['receipt_ack', 'partial_receipt', 'terminal_before_write'])
def test_cost_companion_existing_partial_receipt_contract(tmp_path, monkeypatch, boundary):
    import sys
    original = fixture_args
    def with_cost(*a, **kw):
        args = original(*a, **kw)
        args['netting_candidate']['research_cost_inputs'] = cost_companion_options(args)
        return args
    monkeypatch.setattr(sys.modules[__name__], 'fixture_args', with_cost)
    test_r2_receipt_ack_and_terminal_order_remain_unknown(tmp_path, monkeypatch, boundary)


@pytest.mark.parametrize('fields', ['receipt', 'binding', 'both'])
def test_cost_companion_absent_opt_in_refuses_injected_reserved_fields(tmp_path, monkeypatch, fields):
    import copy
    import json
    from us_equity_strategies.research import security_netting_runner as runner

    supplied = pw.evaluate_portfolio_kelly_walk_forward(**cost_companion_args(tmp_path/'supplied'))
    assert supplied['status'] == 'SUCCEEDED'
    args = fixture_args(tmp_path/'legacy')
    first = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert 'research_cost_inputs' not in first['binding']
    path = next(args['netting_candidate']['local_root'].rglob('receipt.json'))
    envelope = json.loads(path.read_text())
    net = envelope['payload']['netting']
    for key in ('research_cost_receipt', 'research_cost_binding'):
        if fields == 'both' or key == 'research_cost_' + fields:
            net[key] = copy.deepcopy(supplied['netting'][key])
    envelope['payload']['acceptance_binding']['netting_sha256'] = pw._candidate_digest(net)
    envelope['sha256'] = pw._candidate_digest({k:envelope[k] for k in ('payload', 'record_digests')})
    path.write_text(json.dumps(envelope))
    before = cost_companion_files(args)

    def forbidden(*a, **k):
        pytest.fail('unrequested cost receipt replayed')

    monkeypatch.setattr(pw, 'fit_portfolio_kelly', forbidden)
    monkeypatch.setattr(runner, 'run_offline_security_netting', forbidden)
    no_success(pw.evaluate_portfolio_kelly_walk_forward(**args))
    assert before == cost_companion_files(args)
