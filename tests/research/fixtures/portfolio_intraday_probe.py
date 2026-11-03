"""Separate-process synthetic journal checks, with local-only effect guards."""
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.dont_write_bytecode=True
ROOT=Path(sys.argv[2]).resolve(strict=True)
tempfile.tempdir=str(ROOT)
attempts=[]
opened=set()


def allowed(value):
    if type(value) is int:
        target=os.fstat(value)
        for path in tuple(opened):
            try:found=os.stat(path)
            except OSError:continue
            if (target.st_dev,target.st_ino)==(found.st_dev,found.st_ino):return True
        return False
    return isinstance(value,(str,bytes,os.PathLike)) and Path(os.fsdecode(value)).resolve().is_relative_to(ROOT)


def guard(event,args):
    blocked=event.startswith('socket.') or event in {'subprocess.Popen','os.system','os.exec','os.posix_spawn'}
    if event=='open':
        mode,flags=args[1],args[2]
        write=isinstance(mode,str) and any(x in mode for x in 'wax+')
        write |= isinstance(flags,int) and bool(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND))
        if write:
            ok=allowed(args[0]);blocked |= not ok
            if ok and type(args[0]) is not int:opened.add(os.fsdecode(args[0]))
    if event in {'os.mkdir','os.remove','os.rmdir'}:blocked |= not allowed(args[0])
    if event in {'os.link','os.rename','os.symlink'}:blocked |= not (allowed(args[0]) and allowed(args[1]))
    if blocked:attempts.append(event);raise RuntimeError('PROBE_EFFECT_OUTSIDE_OFFLINE_SCOPE')


sys.addaudithook(guard)
from test_portfolio_intraday_state_io import intraday_fixture, move_observation, migration_fixture
from test_portfolio_risk_events_preview import add_plan
from test_portfolio_settings_preview import request_fixture
from us_equity_strategies.research.portfolio_event_journal import PortfolioEventJournal
from us_equity_strategies.research.portfolio_intraday_state_io import (
    initialize_portfolio_event_state,read_portfolio_event_state,
    persist_portfolio_intraday_preview,migrate_portfolio_event_state,
)

phase=sys.argv[1]
normal=phase in ('seed','trip','reload','continue')
group='normal' if normal else ('migration' if 'migrat' in phase else
    ('partial' if 'partial' in phase else ('before' if 'before' in phase else 'after')))
case=ROOT/group
if phase in ('seed','crash_before','crash_after','partial','migrate_crash'):
    assert not case.exists();case.mkdir()
else:assert case.is_dir()
journal=PortfolioEventJournal(case/'journal',offline_root=ROOT)
p,c,b,initialization=intraday_fixture(opened=not normal)


def save(name,value):
    (case/name).write_text(json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n')


def load(name):return json.loads((case/name).read_text())


def snapshot():return {str(x):sha256(x.read_bytes()).hexdigest() for x in (case/'journal').rglob('*.json')}


def report(**fields):
    value=dict(phase=phase,pid=os.getpid(),synthetic=True,execution_authorized=False,
        settings_applied=False,breaker_reset=False,forbidden_effect_attempts=attempts,**fields)
    save(phase+'-result.json',value)
    print(json.dumps(value),flush=True)


def read(binding):return read_portfolio_event_state(journal=journal,binding=binding,account_contract=p['account_contract'])


def cycle(binding,event_id):
    return persist_portfolio_intraday_preview(request_fixture(),journal=journal,binding=binding,
        event_id=event_id,event_context=c,**p)


if phase in ('seed','crash_before','crash_after','partial','migrate_crash'):
    initialized=initialize_portfolio_event_state(journal=journal,binding=b,
        account_contract=p['account_contract'],initialization=initialization)
    assert initialized['readback_verified'],initialized['gaps']
    b=initialized['next_binding'];save('checkpoint.json',b)

if phase=='seed':
    result=cycle(b,'clear');assert result['readback_verified'],result['gaps']
    save('clear-binding.json',result['next_binding'])
    report(status=result['status'],breaker_state=result['breaker_state'],chain_length=2)
elif phase in ('trip','reload'):
    b=load('clear-binding.json');p,c,b=move_observation(p,c,b)
    c['observations']['flow_adjusted_nav_index']=80.;add_plan(p,c)
    if phase=='trip':
        result=cycle(b,'intraday-trip');assert result['readback_verified'],result['gaps']
        save('trip-binding.json',result['next_binding'])
        report(status=result['status'],breaker_state=result['breaker_state'],chain_length=3)
    else:
        restored=read(load('trip-binding.json'));assert restored['readback_verified'] and restored['breaker_state']=='OPEN'
        before=snapshot()
        with patch.object(journal,'append',side_effect=AssertionError('replay must not write')):
            result=cycle(b,'intraday-trip')
        assert result['status']=='INTRADAY_EVENT_ALREADY_PERSISTED' and result['draft_settings'] is None
        assert before==snapshot()
        report(status=result['status'],breaker_state=result['breaker_state'],chain_length=3,state_append_calls=0)
elif phase=='continue':
    b['expected_head_sha256']=load('trip-binding.json')['expected_head_sha256']
    p,c,b=move_observation(p,c,b,seconds=60);add_plan(p,c)
    result=cycle(b,'still-open');assert result['readback_verified'],result['gaps']
    assert result['breaker_state']=='OPEN'
    report(status=result['status'],breaker_state='OPEN',chain_length=4)
elif phase in ('crash_before','crash_after','partial'):
    original=journal.object_store.create_text
    code={'crash_before':73,'crash_after':74,'partial':75}[phase]
    report(status='CRASH_ARMED',breaker_state='OPEN',expected_exit=code)
    def interrupt(uri,data,content_type='application/json'):
        if phase=='crash_after':original(uri,data,content_type)
        elif phase=='partial':
            path=Path(uri);path.parent.mkdir(parents=True,exist_ok=True);path.write_text('{')
        os._exit(code)
    with patch.object(journal.object_store,'create_text',side_effect=interrupt):cycle(b,'interrupted-event')
    raise AssertionError('injected crash not reached')
elif phase in ('verify_before','verify_after','verify_partial'):
    b=load('checkpoint.json');before=snapshot();restored=read(b)
    if phase=='verify_before':
        assert restored['readback_verified'] and restored['breaker_state']=='OPEN'
        assert before==snapshot()
        report(status='PRIOR_OPEN_READ_BACK',breaker_state='OPEN',chain_length=1,state_append_calls=0)
    elif phase=='verify_partial':
        assert not restored['readback_verified'] and restored['new_risk_prohibited']
        with patch.object(journal,'append',side_effect=AssertionError('broken store must not append')):
            result=cycle(b,'interrupted-event')
        assert result['draft_settings'] is None and result['new_risk_prohibited']
        assert before==snapshot() and any(x.read_text()=='{' for x in (case/'journal').rglob('*.json'))
        report(status='PARTIAL_WRITE_REQUIRES_RECOVERY',breaker_state='UNKNOWN',new_risk_prohibited=True)
    else:
        assert not restored['readback_verified'] and restored['new_risk_prohibited']
        with patch.object(journal,'append',side_effect=AssertionError('must reconcile by read only')):
            result=cycle(b,'interrupted-event')
        assert result['status']=='INTRADAY_EVENT_ALREADY_PERSISTED' and result['draft_settings'] is None
        assert before==snapshot()
        report(status=result['status'],breaker_state=result['breaker_state'],chain_length=2,state_append_calls=0)
elif phase=='migrate_crash':
    destination,migration=migration_fixture(b,journal.load_chain(b['identity'])[-1])
    save('destination.json',destination);save('migration.json',migration)
    original=journal.object_store.create_text
    report(status='HANDOFF_CRASH_ARMED',breaker_state='OPEN',expected_exit=76)
    def interrupt_destination(uri,data,content_type='application/json'):
        if json.loads(data)['kind']=='MIGRATE_IN':os._exit(76)
        return original(uri,data,content_type)
    with patch.object(journal.object_store,'create_text',side_effect=interrupt_destination):
        migrate_portfolio_event_state(journal=journal,source_binding=b,destination_binding=destination,
            account_contract=p['account_contract'],migration=migration)
    raise AssertionError('handoff crash not reached')
else:
    assert phase=='verify_migrate'
    b=load('checkpoint.json');destination=load('destination.json');migration=load('migration.json')
    restored=read(b);assert not restored['readback_verified'] and restored['new_risk_prohibited']
    original=journal.object_store.create_text
    with patch.object(journal.object_store,'create_text',wraps=original) as create:
        result=migrate_portfolio_event_state(journal=journal,source_binding=b,destination_binding=destination,
            account_contract=p['account_contract'],migration=migration)
    assert result['readback_verified'] and result['source_retired'],result['gaps']
    assert result['draft_settings'] is None and result['breaker_state']=='OPEN'
    assert create.call_count==1 and len(journal.load_chain(b['identity']))==2
    report(status=result['status'],breaker_state='OPEN',source_retired=True,create_calls=1)
assert not attempts
