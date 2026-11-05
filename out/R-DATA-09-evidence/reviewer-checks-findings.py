"""Independent reproductions of registry identity/selection defects; synthetic only."""
import runpy

import pytest

H = runpy.run_path('tests/research/test_calendar_contract.py')
baseline, registry, resolve, t = (H[name] for name in ('baseline', 'registry', 'resolve', 't'))


def test_changed_same_source_must_link_its_actual_prior_version():
    first = baseline()
    unrelated = baseline(version_id='fixture:independent-base', source_ref='fixture:independent-source')
    changed = baseline(version_id='fixture:annual-v2', content_sha256='1' * 64,
                       supersedes=unrelated.version_id)
    with pytest.raises(ValueError):
        registry(first, unrelated, changed)


def test_selected_chain_must_not_include_a_cutoff_excluded_intermediate():
    first = baseline()
    late = baseline(version_id='fixture:late-intermediate', supersedes=first.version_id,
        published_at=t('2098-12-30T10:00:00Z'), first_seen=t('2098-12-30T10:01:00Z'),
        persisted_at=t('2098-12-30T10:02:00Z'), content_sha256='1' * 64)
    tip = baseline(version_id='fixture:early-tip', supersedes=late.version_id,
        published_at=t('2098-12-28T10:00:00Z'), first_seen=t('2098-12-28T10:01:00Z'),
        persisted_at=t('2098-12-28T10:02:00Z'), content_sha256='2' * 64)
    result = resolve(registry(first, late, tip))
    assert not (set(result.selected_ids) & set(result.excluded_ids)), result
