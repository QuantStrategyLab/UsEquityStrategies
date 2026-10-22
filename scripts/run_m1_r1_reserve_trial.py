"""Explicit local M1 rerun; append to the existing PerformanceStore, no orders.

Requires a prior frozen run and its R0 reference. No acquisition, fee override,
model override or implicit retry. Private daily execution facts stay in Store.
"""
from __future__ import annotations

import argparse
from datetime import date
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess


def read_json(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    with Path(path).open('x') as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')


def packet_metrics(packet):
    """Describe actual native net trades, never infer turnover from fees."""
    from us_equity_strategies.research.cvar_exposure_constraint import weighted_cvar

    rows = packet['ledger']
    if not rows or packet['initial_nav'] <= 0:
        raise ValueError('M1_EMPTY_OR_INVALID_BOOK')
    logs, fee_logs, turnover = [], [], []
    peak = packet['initial_nav']
    mdd = 0.
    for row in rows:
        pre, end, start = (row[k] for k in ('nav_pre_trade', 'nav_end', 'nav_start'))
        if min(pre, end, start) <= 0:
            raise ValueError('M1_NONPOSITIVE_WEALTH')
        logs.append(math.log(end / start))
        fee_logs.append(math.log(pre / end))
        actual_turnover = math.fsum(abs(v) for v in row['net_trades'].values()) / pre
        if not math.isclose(actual_turnover, row['net_security_turnover'], rel_tol=1e-12, abs_tol=1e-14):
            raise ValueError('M1_TURNOVER_NATIVE_MISMATCH')
        turnover.append(actual_turnover)
        peak = max(peak, end)
        mdd = max(mdd, 1 - end / peak)
    return {'observations': len(rows), 'final_nav': rows[-1]['nav_end'],
        'mean_net_log_growth': math.fsum(logs) / len(rows),
        'cumulative_net_log_growth': math.fsum(logs),
        'turnover_sum': math.fsum(turnover), 'turnover_mean': math.fsum(turnover) / len(rows),
        'fees_initial_nav': math.fsum(r['fees'] for r in rows) / packet['initial_nav'],
        'fee_log_drag_sum': math.fsum(fee_logs), 'fee_log_drag_mean': math.fsum(fee_logs) / len(rows),
        'mdd': mdd, 'daily_loss_cvar95': weighted_cvar(
            [-r['net_return'] for r in rows], [1 / len(rows)] * len(rows), .95)}


def paired_metrics(candidate, control):
    if ([r['session'] for r in candidate['ledger']] != [r['session'] for r in control['ledger']]
            or candidate['initial_nav'] != control['initial_nav']):
        raise ValueError('M1_UNPAIRED_SESSIONS_OR_CAPITAL')
    new, old = packet_metrics(candidate), packet_metrics(control)
    differences = [math.log(a['nav_end'] / a['nav_start']) - math.log(b['nav_end'] / b['nav_start'])
        for a, b in zip(candidate['ledger'], control['ledger'], strict=True)]
    return {'R1': new, 'R0': old, 'paired_delta_g': math.fsum(differences) / len(differences),
        'R1_minus_R0': {k: new[k] - old[k] for k in (
            'turnover_sum', 'fees_initial_nav', 'fee_log_drag_sum', 'mdd', 'daily_loss_cvar95')}}


def run(args):
    from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore
    from us_equity_strategies.research import rs04_netting_candidate as caller

    prior, r0root = args.prior_run.resolve(), args.r0_run.resolve()
    store_root = prior / 'performance'
    if not store_root.is_dir():
        raise ValueError('M1_EXISTING_STORE_REQUIRED')
    manifest = read_json(r0root / 'FINAL_MANIFEST.json')
    r0path = r0root / 'PRIVATE_BATCH_RESULT.json'
    expected = next(e['sha256'] for e in manifest['members'] if e['path'] == str(r0path))
    if digest(r0path) != expected:
        raise ValueError('M1_R0_SEAL_MISMATCH')
    old = read_json(r0path)
    wire = read_json(prior / 'assembly/KWARGS22.json')
    raw = read_json(prior / 'assembly/RAW_BINDING.json')
    binding = read_json(prior / 'assembly/BINDING.json')
    # Keep source bytes, policy and economics; rebind only current code/parser
    # identities and this explicitly requested new trial. Never edit the old run.
    import quant_platform_kit
    qpk = Path(quant_platform_kit.__file__).parent
    if any(digest(qpk / rel) != sha for rel, sha in binding['qpk_overlay_expected'].items()):
        raise ValueError('M1_RESEARCH_OVERLAY_DRIFT')
    if (wire['dataset_class'] != 'REAL_ARCHIVED_RAW_ACTIONS'
            or caller._r0_r3_sha(wire['r1_fee_reserve_policy']) != caller._R1_FEE_RESERVE_POLICY_SHA256
            or raw['license'] != 'user_attested_non_commercial_private_research'
            or old['trial']['status'] != 'succeeded'
            or old['trial']['cost_inputs'] != {'per_side_bps': 5.}
            or old['derived_input_body_sha256'] != raw['canonical_body_sha256']):
        raise ValueError('M1_FROZEN_SOURCE_OR_COST_MISMATCH')
    package = Path(caller.__file__).resolve().parents[1]
    names = ('partial_rebalance', 'rs04_netting_candidate', 'security_netting_runner',
        'bayesian_kelly', 'kelly_core_return_generator', 'kelly_private_runner', 'portfolio_kelly_walk_forward')
    impl = {'research/' + n + '.py': digest(package / 'research' / (n + '.py')) for n in names}
    impl.update(runner=digest(__file__), research_QPK_overlay_sources=caller._r0_r3_sha(
        binding['qpk_overlay_expected']))
    wire.update(implementation_sha256=impl, source_revision=subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=package, text=True).strip(), trial_id=args.trial_id)
    wire['input_source_registration']['parser_sha256'] = caller.r0_r3_parser_identity()
    profile = 'r0_r3_batch_additive_audit_v1'
    store = PerformanceStore(local_root=store_root)
    # One writer for the shared store; an occupied ID is never resumed/replayed
    # by this real-data command. The native journal also guards its own identity.
    with (store_root / 'm1-rerun.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if store.load_research_trial('us_equity', profile, args.trial_id) is not None:
            raise ValueError('M1_OCCUPIED_NO_RETRY')
        args.run_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        write_new(args.run_dir / 'INVOCATION.json', {'kwargs': wire, 'store_root': str(store_root),
            'r0_result_sha256': expected, 'raw_binding_sha256': digest(prior / 'assembly/RAW_BINDING.json')})

        def source_root():
            started = store.load_research_trial('us_equity', profile, args.trial_id)
            if (started is None or started.status.value != 'started' or started.synthetic
                    or started.actual_params['implementation_sha256'] != impl):
                raise ValueError('M1_STARTED_READBACK_REQUIRED')
            write_new(args.run_dir / 'STARTED_READBACK.json', started.to_dict())
            # The existing parser verifies each of the same 13 frozen objects.
            return Path(raw['raw_root'])

        options = dict(wire)
        options.update(store=store, load_source_root=source_root,
            requested_start=date.fromisoformat(wire['requested_start']),
            requested_end=date.fromisoformat(wire['requested_end']))
        summary_key = store._research_key('us_equity', profile, args.trial_id, 'm1_metrics')
        try:
            result = caller.run_registered_r0_r3_batch(**options)
            if result['status'] != 'succeeded' or result['resumed_without_compute']:
                raise ValueError('M1_NEW_NATIVE_TRIAL_NOT_SUCCEEDED')
            eng = result['engineering']
            if (eng['training']['count'] != 63 or eng['training']['covariance_fits'] != 1
                    or [d['n'] for d in eng['model_decisions']] != list(range(793))
                    or result['derived_input_body_sha256'] != raw['canonical_body_sha256']):
                raise ValueError('M1_TRAINING_OR_SOURCE_MISMATCH')
            metrics, facts = {}, {}
            for name in ('TQQQ', 'SOXL'):
                cell = 'R1_B4_ONLY:' + name
                packet = eng['cells'][cell]
                control = old['engineering']['cells']['R0_ORIGINAL_FULL:' + name]
                universe = set(packet['spec']['bps_per_side'])
                if (packet['spec']['bps_per_side'] != {s: 5. for s in universe}
                        or control['spec']['bps_per_side'] != packet['spec']['bps_per_side']
                        or any(packet['ledger'][-1]['security_end'].values())
                        or any(control['ledger'][-1]['security_end'].values())):
                    raise ValueError('M1_COST_OR_TERMINAL_MISMATCH')
                metrics[name] = paired_metrics(packet, control)
                saved = result['subresults'][cell]['days'][-793:]
                if any(a['nav_end'] != b['nav'] or a['fees'] != b['fees']
                       for a, b in zip(packet['ledger'], saved, strict=True)):
                    raise ValueError('M1_NATIVE_LEDGER_READBACK_MISMATCH')
                facts[cell] = [{k: row[k] for k in ('session', 'decision_session', 'nav_start',
                    'nav_pre_trade', 'nav_end', 'net_trades', 'fees', 'fee_by_security',
                    'net_security_turnover', 'net_return', 'virtual_cash_end')} for row in packet['ledger']]
            facts_key = store._research_key('us_equity', profile, args.trial_id, 'm1_execution_facts')
            store._create_same(facts_key, {'trial_id': args.trial_id, 'run_id': result['trial']['run_id'],
                'source_revision': wire['source_revision'], 'cells': facts})
            summary = {'schema': 'qsl.research.m1.metrics.v1', 'status': 'succeeded',
                'trial_id': args.trial_id, 'run_id': result['trial']['run_id'],
                'source_revision': wire['source_revision'], 'rule_version': caller._R1_FEE_RESERVE_ID,
                'r0_trial_id': old['trial']['trial_id'], 'r0_result_sha256': expected,
                'canonical_body_sha256': result['derived_input_body_sha256'], 'metrics': metrics,
                'failure': None, 'financial': result['financial'], 'PIT': result['PIT'], 'OOS': result['OOS'],
                'post_fee_cap_certified': False, 'no_order': True,
                'definitions': {'paired_delta_g': 'mean(log(W1_next/W1)-log(W0_next/W0))',
                    'turnover': 'sum(abs(native per-security net trades))/native pre-trade NAV',
                    'fee_log_drag': 'sum(log(NAV_pre_trade/NAV_end)); same evolving net book',
                    'cvar95': 'equal-weight upper 5% tail of daily net simple losses; no forecast constraint'},
                'comparison_limit': 'Seen development; R1 Bayes/c and reserve differ from historical R0; not single-factor attribution',
                'execution_facts_key': facts_key}
            store._create_same(summary_key, summary)
            if store._read_research_json(summary_key) != summary:
                raise ValueError('M1_METRICS_READBACK_MISMATCH')
            write_new(args.run_dir / 'RESULT.json', summary)
            return summary
        except BaseException as error:
            terminal = store.load_research_trial('us_equity', profile, args.trial_id)
            failure = {'status': 'stopped_no_retry', 'trial_id': args.trial_id,
                'error_type': type(error).__name__,
                'native_status': None if terminal is None else terminal.status.value,
                'reason_code': None if terminal is None else terminal.reason_code,
                'fee_cash_insufficient': str(error) == 'FEE_CASH_INSUFFICIENT'}
            # A successful native terminal can coexist with failed diagnostics.
            # Do not replace it or any already-written metrics artifact.
            if store._read_research_json(summary_key) is None:
                store._create_same(summary_key, failure)
            write_new(args.run_dir / 'FAILURE.json', failure)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prior-run', type=Path, required=True)
    parser.add_argument('--r0-run', type=Path, required=True)
    parser.add_argument('--trial-id', required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args()
    # Exact local run, bounded to 45 minutes including all callbacks. The
    # journal records interruption; no retry or alternative data source follows.
    def deadline(_signal, _frame):
        raise SystemExit('M1_TIME_LIMIT_NO_RETRY')
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(2700)
    try:
        result = run(args)
        print(json.dumps({k: result[k] for k in ('status', 'trial_id', 'run_id', 'metrics')}))
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    main()
