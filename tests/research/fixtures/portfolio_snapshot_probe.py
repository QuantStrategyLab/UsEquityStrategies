"""Three separate artificial recovery processes with local effect guards."""
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tempfile

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
from test_portfolio_intraday_state_io import intraday_fixture
from us_equity_strategies.research.portfolio_event_journal import PortfolioEventJournal
from us_equity_strategies.research.portfolio_intraday_state_io import (
    initialize_portfolio_event_state, read_portfolio_event_state,
)
from us_equity_strategies.research.portfolio_state_snapshot import (
    save_intraday_state_snapshot, restore_intraday_state_snapshot,
)

phase = sys.argv[1]
packet,context,binding,proof = intraday_fixture(opened=True)
source = PortfolioEventJournal(ROOT/'source',offline_root=ROOT)
target = PortfolioEventJournal(ROOT/'recovered',offline_root=ROOT)
meta = ROOT/'checkpoint.json'


def contents():
    return {str(p):sha256(p.read_bytes()).hexdigest() for p in ROOT.rglob('*.json')}


if phase == 'seed':
    result = initialize_portfolio_event_state(journal=source,binding=binding,
        account_contract=packet['account_contract'],initialization=proof)
    binding = result['next_binding']
    saved = save_intraday_state_snapshot(journal=source,binding=binding,
        account_contract=packet['account_contract'],snapshot_dir=ROOT/'archive',offline_root=ROOT)
    assert saved['readback_verified'],saved['gaps']
    meta.write_text(json.dumps(dict(binding=binding,sha=saved['snapshot_sha256'])))
    breaker = saved['breaker_state']
elif phase == 'restore':
    saved = json.loads(meta.read_text());binding = saved['binding']
    before = {p:p.read_bytes() for p in (ROOT/'source').rglob('*.json')}
    result = restore_intraday_state_snapshot(journal=target,binding=binding,
        account_contract=packet['account_contract'],snapshot_dir=ROOT/'archive',
        expected_sha256=saved['sha'],offline_root=ROOT)
    assert result['state_recovered'] and result['new_risk_prohibited'],result['gaps']
    assert result['source_retired'] is None and not result['breaker_reset']
    assert before == {p:p.read_bytes() for p in before}
    breaker = result['breaker_state']
else:
    assert phase == 'read'
    saved = json.loads(meta.read_text());binding = saved['binding'];before = contents()
    result = read_portfolio_event_state(journal=target,binding=binding,account_contract=packet['account_contract'])
    assert result['readback_verified'] and result['new_risk_prohibited'],result['gaps']
    assert source.load_chain(binding['identity']) == target.load_chain(binding['identity'])
    assert before == contents()
    breaker = result['breaker_state']
assert not attempts
print(json.dumps(dict(phase=phase,pid=os.getpid(),breaker_state=breaker,
    execution_authorized=False,settings_applied=False,breaker_reset=False,forbidden_effect_attempts=attempts)))
