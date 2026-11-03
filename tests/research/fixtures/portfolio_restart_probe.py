"""Separate-process artificial restart/crash probe; writes only probe evidence."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.dont_write_bytecode=True
ROOT=Path(sys.argv[2]).resolve(strict=True)
EVIDENCE=ROOT/'restart-evidence'
tempfile.tempdir=str(EVIDENCE)
opened=set();effects=[];attempts=[]

def allowed(value):
    if type(value) is int:
        target=os.fstat(value)
        for path in tuple(opened):
            try:found=os.stat(path)
            except OSError:continue
            if (target.st_dev,target.st_ino)==(found.st_dev,found.st_ino):return True
        return False
    return isinstance(value,(str,bytes,os.PathLike)) and Path(os.fsdecode(value)).resolve().is_relative_to(EVIDENCE)

def guard(event,args):
    blocked=event.startswith('socket.') or event in {'subprocess.Popen','os.system','os.exec','os.posix_spawn'}
    if event=='open':
        mode,flags=args[1],args[2]
        write=isinstance(mode,str) and any(x in mode for x in 'wax+')
        write |= isinstance(flags,int) and bool(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND))
        if write:
            ok=allowed(args[0]);blocked |= not ok
            if ok and type(args[0]) is not int:opened.add(os.fsdecode(args[0]))
            effects.append(event)
    if event in {'os.mkdir','os.remove','os.rmdir'}:blocked |= not allowed(args[0]);effects.append(event)
    if event in {'os.link','os.rename','os.symlink'}:
        blocked |= not (allowed(args[0]) and allowed(args[1]));effects.append(event)
    if blocked:attempts.append(event);raise RuntimeError('PROBE_EFFECT_OUTSIDE_OFFLINE_SCOPE')

sys.addaudithook(guard)
from test_portfolio_risk_state_io import io_fixture,advance_fixture
from test_portfolio_risk_events_preview import add_plan
from test_portfolio_settings_preview import request_fixture
from us_equity_strategies.research.portfolio_risk_state_io import (
    persist_portfolio_risk_preview,read_portfolio_risk_state,
)
from quant_platform_kit.common.strategy_risk_state import StrategyRiskStateStore

phase=sys.argv[1]
assert phase in ('seed','reload','continue','crash_before','verify_before','crash_after','verify_after')
case_name='normal' if phase in ('seed','reload','continue') else ('before-link' if 'before' in phase else 'after-commit')
case=EVIDENCE/case_name
if phase in ('seed','crash_before','crash_after'):
    assert not case.exists(), 'probe case must be fresh; do not repeat an unknown write'
    case.mkdir()
else:assert case.is_dir()
store=StrategyRiskStateStore(local_dir=case/'state')
p,c,b,prior=io_fixture(held=True,opened=True if phase not in ('seed','reload','continue') else None)
if case_name=='normal':c['observations']['flow_adjusted_nav_index']=80.;add_plan(p,c)
request=request_fixture()

def save(name,value):
    (case/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')

def report(**fields):
    value=dict(phase=phase,pid=os.getpid(),synthetic=True,execution_authorized=False,
        settings_applied=False,breaker_reset=False,forbidden_effect_attempts=attempts,
        allowed_effect_count=len(effects),**fields)
    save(phase+'-result.json',value)
    print(json.dumps(value,ensure_ascii=False),flush=True)

def read(binding):
    return read_portfolio_risk_state(store=store,binding=binding,
        account_contract=p['account_contract'],offline_root=ROOT)

def cycle(binding):
    return persist_portfolio_risk_preview(request,store=store,binding=binding,
        event_context=c,offline_root=ROOT,**p)

if phase=='seed':
    result=cycle(b);assert result['readback_verified'] and result['state_persisted'],result['gaps']
    assert result['state_readback']['breaker_state']=='OPEN'
    save('checkpoint.json',result['next_binding']);save('seed-state-io.json',result)
    report(status=result['status'],head=result['next_binding']['expected_head_sha256'],
        chain_length=len(result['state_readback']['chain']),breaker_state='OPEN')
elif phase=='reload':
    b=json.loads((case/'checkpoint.json').read_text());restored=read(b)
    assert restored['readback_verified'] and restored['breaker_state']=='OPEN',restored
    with patch.object(StrategyRiskStateStore,'append',side_effect=AssertionError('reload must not write state')):
        result=cycle(b)
    assert result['status']=='STATE_ALREADY_PERSISTED_READ_BACK' and result['draft_settings'] is None
    save('reload-state-io.json',result)
    report(status=result['status'],head=restored['observed_head_sha256'],
        chain_length=len(restored['chain']),breaker_state=restored['breaker_state'],state_append_calls=0)
elif phase=='continue':
    b=json.loads((case/'checkpoint.json').read_text());p,c,b=advance_fixture(p,c,b)
    result=cycle(b);assert result['readback_verified'] and len(result['state_readback']['chain'])==2,result['gaps']
    assert result['state_readback']['breaker_state']=='OPEN'
    save('continued-checkpoint.json',result['next_binding']);save('continue-state-io.json',result)
    report(status=result['status'],head=result['next_binding']['expected_head_sha256'],chain_length=2,breaker_state='OPEN')
elif phase in ('crash_before','crash_after'):
    store.append(prior);save('checkpoint.json',b)
    code=73 if phase=='crash_before' else 74
    report(status='CRASH_ARMED',expected_exit=code,prior_head=b['expected_head_sha256'],breaker_state='OPEN')
    if phase=='crash_before':
        def die_before_link(*args,**kwargs):os._exit(code)
        with patch('os.link',die_before_link):cycle(b)
    else:
        append=StrategyRiskStateStore.append
        def die_after_append(owner,transition):
            append(owner,transition);os._exit(code)
        with patch.object(StrategyRiskStateStore,'append',die_after_append):cycle(b)
    raise AssertionError('probe did not reach injected crash')
elif phase=='verify_before':
    b=json.loads((case/'checkpoint.json').read_text());restored=read(b)
    assert restored['readback_verified'] and restored['breaker_state']=='OPEN' and len(restored['chain'])==1
    orphans=list((case/'state').rglob('*.tmp'));assert len(orphans)==1
    report(status='PRIOR_OPEN_RECOVERED_AFTER_PRECOMMIT_CRASH',head=restored['observed_head_sha256'],
        chain_length=1,breaker_state='OPEN',ignored_uncommitted_temp_files=len(orphans),state_append_calls=0)
else:
    b=json.loads((case/'checkpoint.json').read_text());stale=read(b)
    assert stale['gaps']==['STATE_HEAD_CHECKPOINT_MISMATCH'] and stale['new_risk_prohibited']
    with patch.object(StrategyRiskStateStore,'append',side_effect=AssertionError('must reconcile by read only')):
        result=cycle(b)
    assert result['status']=='STATE_ALREADY_PERSISTED_READ_BACK' and result['readback_verified'],result['gaps']
    assert result['state_readback']['breaker_state']=='OPEN' and result['draft_settings'] is None
    save('reconciled-checkpoint.json',result['next_binding']);save('reconciled-state-io.json',result)
    report(status='COMMITTED_OPEN_RECOVERED_AFTER_LOST_ACK',head=result['next_binding']['expected_head_sha256'],
        chain_length=len(result['state_readback']['chain']),breaker_state='OPEN',state_append_calls=0)
assert not attempts
