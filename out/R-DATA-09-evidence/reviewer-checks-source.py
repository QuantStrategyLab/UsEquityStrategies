"""Bind the independent final review to exact synthetic source and test bytes."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

files = [
    'src/us_equity_strategies/research/calendar_contract.py',
    'tests/research/test_calendar_contract.py',
    'tests/research/test_r_data_08_calendar_t1_contract.py',
    'src/us_equity_strategies/research/pit_session_inputs.py',
    'src/us_equity_strategies/research/security_netting_runner.py',
    'src/us_equity_strategies/research/kelly_private_runner.py',
]
root = Path('out/R-DATA-09-evidence')
hashes = {f: sha256(Path(f).read_bytes()).hexdigest() for f in files}
verification = json.loads((root / 'verification.json').read_text())
assert all(hashes[f] == digest for f, digest in verification['source_sha256'].items())
base = 'aeb200a2ccf65c386f0f8c915a3f4e2748f43ea4'
tracked_diff = subprocess.check_output(['git', 'diff', '--name-only', base], text=True).splitlines()
assert not tracked_diff, tracked_diff
suite = ET.parse(root / 'reviewer-checks-final.xml').getroot().find('testsuite')
assert int(suite.attrib['tests']) == 94
assert all(int(suite.attrib[k]) == 0 for k in ['failures', 'errors', 'skipped'])
initial = json.loads((root / 'reviewer-checks-source-initial.json').read_text())
assert all(hashes[f] == initial['source_sha256'][f] for f in files[2:])
result = {
    'status': 'PASS_SCOPED_INDEPENDENT',
    'reviewer': '/root/r_data_09_independent_review',
    'base': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
    'source_sha256': hashes,
    'author_verification_hashes_match': True,
    'tracked_diff': tracked_diff,
    'product_edits_by_reviewer': False,
    'junit': {k: int(suite.attrib[k]) for k in ['tests', 'failures', 'errors', 'skipped']},
}
(root / 'reviewer-checks-source-final.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
