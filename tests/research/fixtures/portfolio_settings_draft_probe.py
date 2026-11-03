"""Separate-process offline draft consumption with local effect guards."""
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[2]).resolve(strict=True)
tempfile.tempdir = str(ROOT)
attempts = []
opened = set()


def allowed(value):
    if type(value) is int:
        target = os.fstat(value)
        for path in tuple(opened):
            try: found = os.stat(path)
            except OSError: continue
            if (target.st_dev,target.st_ino) == (found.st_dev,found.st_ino): return True
        return False
    return isinstance(value,(str,bytes,os.PathLike)) and Path(os.fsdecode(value)).resolve().is_relative_to(ROOT)


def guard(event,args):
    blocked = event.startswith('socket.') or event in {'subprocess.Popen','os.system','os.exec','os.posix_spawn'}
    if event == 'open':
        mode,flags = args[1],args[2]
        write = isinstance(mode,str) and any(x in mode for x in 'wax+')
        write |= isinstance(flags,int) and bool(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND))
        if write:
            ok = allowed(args[0]);blocked |= not ok
            if ok and type(args[0]) is not int: opened.add(os.fsdecode(args[0]))
    if event in {'os.mkdir','os.remove','os.rmdir'}: blocked |= not allowed(args[0])
    if event in {'os.link','os.rename','os.symlink'}: blocked |= not (allowed(args[0]) and allowed(args[1]))
    if blocked: attempts.append(event);raise RuntimeError('PROBE_EFFECT_OUTSIDE_OFFLINE_SCOPE')


sys.addaudithook(guard)
from test_portfolio_intraday_state_io import intraday_fixture, move_observation
from test_portfolio_settings_preview import request_fixture
from us_equity_strategies.research.portfolio_event_journal import PortfolioEventJournal
from us_equity_strategies.research.portfolio_intraday_state_io import initialize_portfolio_event_state
from us_equity_strategies.research.portfolio_settings_draft_io import (
    persist_portfolio_settings_draft, preflight_portfolio_settings_draft,
)

phase = sys.argv[1]
journal = PortfolioEventJournal(ROOT/'journal',offline_root=ROOT)
p,c,b,proof = intraday_fixture()
request = request_fixture('advanced',stop_loss_ratio=.08)


def save(name,value): (ROOT/name).write_text(json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n')


def load(name): return json.loads((ROOT/name).read_text())


def snapshot(): return {str(x):sha256(x.read_bytes()).hexdigest() for x in (ROOT/'journal').rglob('*.json')}


def read(binding):
    return preflight_portfolio_settings_draft(request,journal=journal,binding=binding,
        expected_artifact_sha256=load('receipt.json')['artifact_sha256'],
        consumption_cutoff_utc='2000-01-04T12:01:30Z',event_context=c,**p)


write_calls = 0
if phase == 'save':
    initialized = initialize_portfolio_event_state(journal=journal,binding=b,
        account_contract=p['account_contract'],initialization=proof)
    assert initialized['readback_verified'],initialized['gaps']
    result = persist_portfolio_settings_draft(request,journal=journal,binding=initialized['next_binding'],
        event_id='allocation',event_context=c,**p)
    assert result['artifact_readback_verified'],result['gaps']
    save('receipt.json',result['artifact_receipt']);save('closed-binding.json',result['next_binding'])
elif phase == 'reload':
    before = snapshot()
    with patch.object(journal.object_store,'create_text',side_effect=AssertionError('read cannot write')) as create:
        result = read(load('closed-binding.json'));write_calls = create.call_count
    assert result['artifact_readback_verified'],result['gaps']
    assert result['confirmation_state'] == 'REQUIRED' and not result['confirmation_granted']
    assert before == snapshot()
elif phase == 'trip':
    p,c,b = move_observation(p,c,load('closed-binding.json'))
    c['observations']['flow_adjusted_nav_index'] = 80.
    result = persist_portfolio_settings_draft(request,journal=journal,binding=b,
        event_id='trip',event_context=c,**p)
    assert result['state_persisted'] and result['breaker_state'] == 'OPEN' and result['draft_settings'] is None
    save('open-binding.json',result['next_binding'])
elif phase == 'old_read':
    before = snapshot()
    with patch.object(journal.object_store,'create_text',side_effect=AssertionError('read cannot write')) as create:
        old = read(load('closed-binding.json'));result = read(load('open-binding.json'));write_calls = create.call_count
    assert not old['artifact_readback_verified'] and not result['artifact_readback_verified']
    assert result['draft_settings'] is None and result['new_risk_prohibited']
    assert before == snapshot()
else: raise AssertionError('unknown phase')

print(json.dumps(dict(phase=phase,pid=os.getpid(),status=result['status'],
    artifact_readback_verified=result['artifact_readback_verified'],new_risk_prohibited=result['new_risk_prohibited'],
    write_calls=write_calls,execution_authorized=result['execution_authorized'],
    settings_applied=result['settings_applied'],breaker_reset=result['breaker_reset'],
    forbidden_effect_attempts=attempts)),flush=True)
