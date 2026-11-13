"""Independent synthetic probes; reviewer-owned, offline, no real calendars."""
import runpy
from dataclasses import replace
from datetime import time

import pytest

H = runpy.run_path('tests/research/test_calendar_contract.py')
baseline, notice, closure = (H[name] for name in ('baseline', 'notice', 'closure'))
registry, resolve = (H[name] for name in ('registry', 'resolve'))
d, t, DAY = (H[name] for name in ('d', 't', 'DAY'))


@pytest.mark.parametrize('reverse', [False, True])
def test_independent_baselines_conflict_without_load_priority(reverse):
    records = (baseline(), baseline(version_id='fixture:independent', source_ref='fixture:independent'))
    if reverse:
        records = tuple(reversed(records))
    result = resolve(registry(*records))
    assert (result.status, result.reason) == ('UNKNOWN', 'BASELINE_CONFLICT')


@pytest.mark.parametrize('reverse', [False, True])
def test_branched_supersession_does_not_silently_choose_a_tip(reverse):
    original = notice()
    tips = (closure(supersedes=original.version_id),
            notice(version_id='fixture:branch', supersedes=original.version_id))
    if reverse:
        tips = tuple(reversed(tips))
    result = resolve(registry(baseline(), original, *tips))
    assert (result.status, result.reason) == ('UNKNOWN', 'NOTICE_CONFLICT')


def test_fully_unknown_effective_and_coverage_range_blocks_earlier_day_too():
    unknown = notice(coverage_start=None, coverage_end=None,
                     effective_from=None, effective_through=None)
    result = resolve(registry(baseline(), unknown), d('2098-12-29'))
    assert (result.status, result.reason) == ('UNKNOWN', 'UNRESOLVED_EFFECTIVE_RANGE')


def test_nonoverlapping_notices_coexist():
    next_day = d('2098-12-31')
    Day = H['Day']
    later = replace(closure(), version_id='fixture:later', coverage_start=next_day,
        coverage_end=next_day, effective_from=next_day, effective_through=next_day,
        days=(Day(next_day, 'CLOSED', None, None),))
    reg = registry(baseline(), notice(), later)
    assert resolve(reg).close_utc == time(16)
    assert resolve(reg, next_day).status == 'CLOSED'


def test_unknown_predecessor_cannot_be_repaired_before_late_successor_cutoff():
    original = notice(first_seen=None)
    successor = closure(supersedes=original.version_id,
        first_seen=t('2098-12-30T10:00:00Z'), persisted_at=t('2098-12-30T10:01:00Z'))
    reg = registry(baseline(), original, successor)
    old = resolve(reg)
    assert old.status == 'UNKNOWN' and successor.version_id in old.excluded_ids
    assert resolve(reg, cutoff=t('2098-12-30T10:01:00Z')).status == 'TEMP_CLOSED'
    assert resolve(reg) == old
