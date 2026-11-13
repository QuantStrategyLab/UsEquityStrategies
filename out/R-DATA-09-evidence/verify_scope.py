"""Verify delivery boundaries against the exact fetched base, not live eligibility."""
from pathlib import Path
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parents[2]
out = root / 'out/R-DATA-09-evidence'
base = 'aeb200a2ccf65c386f0f8c915a3f4e2748f43ea4'
code = {
    'src/us_equity_strategies/research/calendar_contract.py',
    'tests/research/test_calendar_contract.py',
    'tests/research/test_r_data_08_calendar_t1_contract.py',
}

def git(*args):
    return subprocess.check_output(['git', *args], cwd=root, text=True)

changes = {}
for line in git('diff', '--name-status', base).splitlines():
    status, path = line.split('\t', 1)
    assert status == 'A', (status, path)
    changes[path] = status
for path in git('ls-files', '--others', '--exclude-standard').splitlines():
    changes[path] = '?'
for path in changes:
    assert path in code or path == 'out/R-DATA-09.md' or path.startswith('out/R-DATA-09-evidence/'), path
assert code <= changes.keys(), changes
suites = list(ET.parse(out / 'focused.xml').getroot().iter('testsuite'))
counts = {key: sum(int(s.get(key, 0)) for s in suites) for key in ('tests', 'failures', 'errors', 'skipped')}
assert counts['tests'] >= 82 and counts['failures'] == counts['errors'] == counts['skipped'] == 0, counts
tracked = git('ls-tree', '-r', '--name-only', base).splitlines()
assert not (code & set(tracked))
# All tracked base files, including parser, schema, config and workflows, are unchanged.
assert git('diff', '--name-only', base, '--', *tracked) == ''
for name in ('focused', 'lint', 'lint-all'):
    result = json.loads((out / (name + '.command.json')).read_text())
    assert result['exit_code'] == 0, result
check = Path('/Users/lisiyi/codex-jobs/TASK_CHECKLIST.md').read_text()
before = Path('/Users/lisiyi/Projects/UsEquityStrategies/out/R-DATA-09-evidence/checklist-before.md').read_text()
def first(text):
    return next(row for row in text.splitlines() if row.startswith('| 1 数据/PIT/回放 |'))

assert first(check) == first(before), 'Central first row changed during task; inspect external writer before concluding'
result = {
    'status': 'PASS_SCOPED', 'base': base, 'worktree': str(root),
    'existing_tracked_files_unchanged': len(tracked), 'added_code': sorted(code),
    'source_sha256': {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in sorted(code)},
    'junit': counts, 'first_row_unchanged_since_start': True,
    'first_row_sha256': hashlib.sha256(first(check).encode()).hexdigest(),
    'fixture_only': True, 'real_data_or_license_verification': False,
    'production_integration_or_deployment': False,
    'network_scope': 'Socket-denied fixture tests; GitHub publication network recorded separately',
}
(out / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
