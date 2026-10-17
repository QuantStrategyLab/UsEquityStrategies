"""One synthetic RS06 fit -> actual net book -> exact local candidate readback."""
from datetime import date, timedelta

import pytest
from quant_platform_kit.research_stats.security_netting import SecurityNettingSpec

from us_equity_strategies.research import portfolio_kelly_walk_forward as pw
from us_equity_strategies.research.fractional_kelly_walk_forward_plan import WalkForwardPlan


def fixture_args(tmp_path, *, bps=100., test_sessions=5):
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
    assert report == expected and report['observations'] == 5
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
