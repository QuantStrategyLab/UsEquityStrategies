"""Invented dates/documents/rights only; no official calendar or PIT attestation."""
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256

import pytest

from us_equity_strategies.research.calendar_contract import (
    CalendarRecord, CalendarRegistry, Day, Scope,
)


def d(value):
    return date.fromisoformat(value)


def t(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


SCOPE = Scope("fixture:scope-A-not-a-MIC", "fixture:segment", "UTC", "MARKET")
CUTOFF = t("2098-12-29T21:00:00Z")
DAY = d("2098-12-30")


def baseline(**changes):
    return replace(CalendarRecord(
        version_id="fixture:annual-v1", kind="BASELINE", scope=SCOPE, year=2098,
        coverage_start=d("2098-01-01"), coverage_end=d("2098-12-31"),
        effective_from=d("2098-01-01"), effective_through=d("2098-12-31"),
        source_ref="fixture:annual-document", title="ARTIFICIAL ANNUAL NOT A PDF",
        revision="fixture:v1", content_sha256=sha256(b"ARTIFICIAL annual v1").hexdigest(),
        published_at=t("2097-12-01T10:00:00Z"),
        first_seen=t("2097-12-01T10:01:00Z"), persisted_at=t("2097-12-01T10:02:00Z"),
        license_ref="fixture:rights-not-a-license", license_version="fixture:v1",
        license_from=d("2097-01-01"), license_through=d("2099-12-31"),
        verification_ref="fixture:review-not-authentication", reason="fixture:annual",
        baseline_id=None, supersedes=None,
        days=tuple(Day(d(f"2098-12-{i}"), "OPEN", time(12), time(20))
                   for i in (29, 30, 31))), **changes)


def notice(**changes):
    record = replace(baseline(), version_id="fixture:early", kind="NOTICE",
        coverage_start=DAY, coverage_end=DAY, effective_from=DAY, effective_through=DAY,
        source_ref="fixture:early-notice", title="ARTIFICIAL EARLY CLOSE",
        content_sha256=sha256(b"ARTIFICIAL notice").hexdigest(),
        published_at=t("2098-12-28T10:00:00Z"),
        first_seen=t("2098-12-28T10:01:00Z"), persisted_at=t("2098-12-28T10:02:00Z"),
        baseline_id="fixture:annual-v1", reason="fixture:correction",
        days=(Day(DAY, "EARLY_CLOSE", time(12), time(16)),))
    return replace(record, **changes)


def registry(*records, **changes):
    options = dict(fixture_only=True, scope=SCOPE, owner="fixture:owner-not-appointed",
                   license_ref="fixture:rights-not-a-license", records=())
    options.update(changes)
    result = CalendarRegistry(**options)
    for record in records:
        result = result.register(record)
    return result


def resolve(reg, day=DAY, cutoff=CUTOFF):
    return reg.resolve_day(scope=SCOPE, day=day, cutoff=cutoff)


def test_annual_registration_is_immutable_and_explicit():
    reg = registry(baseline())
    result = resolve(reg)
    assert result.status == "OPEN"
    assert result.selected_ids == ("fixture:annual-v1",)
    assert result.close_utc == time(20)
    assert result.pit == "NOT_ATTESTED"
    assert reg.register(baseline()) == reg
    assert len(reg.records) == 1
    assert resolve(reg, d("2098-12-28")).status == "UNKNOWN"
    assert resolve(reg, d("2099-01-01")).status == "UNKNOWN"


@pytest.mark.parametrize("changes", [dict(scope=None), dict(owner=None), dict(owner=""),
    dict(license_ref=None), dict(license_ref="UNKNOWN"), dict(fixture_only=False),
    dict(scope=Scope("XNYS", "core", "UTC", "MARKET"))])
def test_no_market_owner_rights_or_real_mode_defaults(changes):
    with pytest.raises(ValueError):
        registry(**changes)


def test_no_baseline_means_unknown():
    result = resolve(registry())
    assert result.status == "UNKNOWN"
    assert result.is_session is None


def test_notice_changes_only_affected_date_and_preserves_annual_record():
    base = baseline()
    reg = registry(base, notice())
    assert resolve(reg).status == "EARLY_CLOSE"
    assert resolve(reg).close_utc == time(16)
    assert resolve(reg).selected_ids == (base.version_id, "fixture:early")
    assert resolve(reg, d("2098-12-29")).status == "OPEN"
    assert reg.records[0] == base


def closure(**changes):
    return notice(version_id="fixture:closure", source_ref="fixture:closure-source",
        days=(Day(DAY, "TEMP_CLOSED", None, None),), **changes)


@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_notices_never_use_load_order(reverse):
    notices = (notice(), closure())
    if reverse:
        notices = tuple(reversed(notices))
    reg = registry(baseline(), *notices)
    result = resolve(reg)
    assert (result.status, result.reason, result.is_session) == ("UNKNOWN", "NOTICE_CONFLICT", None)
    derived = reg.next_session(scope=SCOPE, after=d("2098-12-29"),
        through=d("2098-12-31"), cutoff=CUTOFF)
    assert (derived.status, derived.value, derived.blocked_dates) == ("UNAVAILABLE", None, (DAY,))


def test_explicit_notice_supersession_keeps_selection_chain():
    reg = registry(baseline(), notice(), closure(supersedes="fixture:early"))
    result = resolve(reg)
    assert result.status == "TEMP_CLOSED"
    assert result.is_session is False
    assert result.close_utc is None
    assert result.selected_ids == ("fixture:annual-v1", "fixture:closure")
    assert result.ancestry_ids == ("fixture:early",)
    assert len(reg.records) == 3
    assert reg.register(notice()) is reg


@pytest.mark.parametrize("field", ["scope", "source_ref", "effective_from", "effective_through",
    "coverage_start", "coverage_end", "published_at", "first_seen", "persisted_at"])
def test_explicit_unknown_notice_metadata_blocks_potential_impact(field):
    reg = registry(baseline(), notice(**{field: None}))
    result = resolve(reg)
    assert result.status == "UNKNOWN"
    assert result.is_session is None
    assert resolve(reg, d("2098-12-29")).status == "OPEN"


@pytest.mark.parametrize("status", ["OPEN", "EARLY_CLOSE"])
def test_missing_open_or_early_close_time_is_unknown(status):
    reg = registry(baseline(), notice(days=(Day(DAY, status, time(12), None),)))
    assert (resolve(reg).status, resolve(reg).reason) == ("UNKNOWN", "MISSING_SESSION_TIME")


def test_known_late_notice_is_excluded_and_cannot_rewrite_old_cutoff():
    late = notice(first_seen=t("2098-12-30T10:00:00Z"),
                  persisted_at=t("2098-12-30T10:01:00Z"))
    reg = registry(baseline(), late)
    old = resolve(reg)
    assert old.status == "OPEN"
    assert old.excluded_ids == (late.version_id,)
    assert resolve(reg, cutoff=t("2098-12-30T10:01:00Z")).status == "EARLY_CLOSE"
    assert resolve(reg) == old


def test_document_clocks_may_precede_session_and_cross_utc_days():
    base = baseline(published_at=t("2097-12-01T23:59:00Z"),
        first_seen=t("2097-12-02T00:00:00Z"), persisted_at=t("2097-12-02T00:01:00Z"))
    assert resolve(registry(base)).status == "OPEN"
    # T1 bars are tested separately; document clocks do not replace bar event_at.


@pytest.mark.parametrize("kind", ["MARKET", "SINGLE_SECURITY", "VENUE"])
def test_precise_other_scopes_do_not_close_requested_market(kind):
    other = Scope("fixture:other", "fixture:segment", "UTC", kind)
    other_base = baseline(scope=other, version_id="fixture:other-base")
    other_notice = closure(scope=other, baseline_id=other_base.version_id)
    assert resolve(registry(baseline(), other_base, other_notice)).status == "OPEN"


@pytest.mark.parametrize("change", ["missing_day", "explicit_unknown"])
def test_u1_unknown_blocks_search_and_distance(change):
    base = baseline()
    days = tuple(v for v in base.days if v.day != DAY)
    if change == "explicit_unknown":
        days += (Day(DAY, "UNKNOWN", None, None),)
    reg = registry(replace(base, days=days))
    before = reg.records
    kwargs = dict(scope=SCOPE, cutoff=CUTOFF)
    result = reg.next_session(after=d("2098-12-29"), through=d("2098-12-31"), **kwargs)
    assert (result.status, result.value, result.blocked_dates) == ("UNAVAILABLE", None, (DAY,))
    distance = reg.session_distance(start=d("2098-12-29"), end=d("2098-12-31"), **kwargs)
    assert distance.status == "UNAVAILABLE" and distance.value is None
    assert reg.records == before


@pytest.mark.parametrize("closed", ["CLOSED", "TEMP_CLOSED"])
def test_u1_explicit_closure_can_be_crossed(closed):
    reg = registry(baseline(), notice(days=(Day(DAY, closed, None, None),)))
    kwargs = dict(scope=SCOPE, cutoff=CUTOFF)
    result = reg.next_session(after=d("2098-12-29"), through=d("2098-12-31"), **kwargs)
    assert (result.status, result.value, result.blocked_dates) == ("AVAILABLE", d("2098-12-31"), ())
    assert reg.session_distance(start=d("2098-12-29"), end=d("2098-12-31"), **kwargs).value == 1


def test_u1_unrelated_later_unknown_does_not_block_first_next_session():
    reg = registry(baseline(days=baseline().days[:2]))
    result = reg.next_session(scope=SCOPE, after=d("2098-12-29"), through=d("2098-12-31"), cutoff=CUTOFF)
    assert result.value == DAY and len(result.resolutions) == 2


def test_u1_cross_year_and_unknown_anchor_and_empty_bounded_search():
    reg = registry(baseline())
    result = reg.next_session(scope=SCOPE, after=d("2098-12-31"), through=d("2099-01-02"), cutoff=CUTOFF)
    assert result.value is None and result.blocked_dates == (d("2099-01-01"),)
    result = reg.next_session(scope=SCOPE, after=d("2098-12-28"), through=DAY, cutoff=CUTOFF)
    assert result.value is None and result.blocked_dates == (d("2098-12-28"),)
    result = reg.next_session(scope=SCOPE, after=DAY, through=DAY, cutoff=CUTOFF)
    assert result.value is None and result.status == "UNAVAILABLE"


def test_later_baseline_restoration_does_not_backfill_old_cutoff():
    base = baseline(days=(Day(DAY, "UNKNOWN", None, None),))
    revised = baseline(version_id="fixture:annual-v2", supersedes=base.version_id,
        content_sha256=sha256(b"ARTIFICIAL annual v2").hexdigest(), revision="fixture:v2",
        published_at=t("2098-12-30T10:00:00Z"), first_seen=t("2098-12-30T10:01:00Z"),
        persisted_at=t("2098-12-30T10:02:00Z"))
    reg = registry(base, revised)
    assert resolve(reg).status == "UNKNOWN"
    new = resolve(reg, cutoff=t("2098-12-30T10:02:00Z"))
    assert new.status == "OPEN" and new.selected_ids == (revised.version_id,)
    assert new.ancestry_ids == (base.version_id,)
    assert resolve(reg).status == "UNKNOWN"


@pytest.mark.parametrize("changes", [dict(content_sha256="0" * 64), dict(title="Changed title"),
    dict(first_seen=t("2097-12-01T10:01:30Z")), dict(days=())])
def test_duplicate_identity_conflict_does_not_mutate_registry(changes):
    reg = registry(baseline())
    with pytest.raises(ValueError, match="CALENDAR_VERSION_CONFLICT"):
        reg.register(baseline(**changes))
    assert reg.records == (baseline(),)


def test_direct_registry_duplicate_cannot_bypass_validation():
    with pytest.raises(ValueError, match="CALENDAR_DUPLICATE_VERSION"):
        registry(records=(baseline(), baseline()))


def test_same_source_changed_content_requires_new_version_and_supersedes():
    reg = registry(baseline())
    candidate = baseline(version_id="fixture:v2", content_sha256="0" * 64)
    with pytest.raises(ValueError, match="CALENDAR_NEW_VERSION_LINK_REQUIRED"):
        reg.register(candidate)
    revised = reg.register(replace(candidate, supersedes="fixture:annual-v1",
        first_seen=t("2097-12-01T10:03:00Z"), persisted_at=t("2097-12-01T10:04:00Z")))
    assert revised.records[0] == reg.records[0]


@pytest.mark.parametrize("changes", [dict(title=""), dict(revision=None), dict(verification_ref=None),
    dict(reason=None), dict(content_sha256="x" * 64), dict(license_ref=None),
    dict(license_version=None), dict(coverage_start=None), dict(scope=None),
    dict(year=2099), dict(coverage_end=d("2098-01-01")),
    dict(days=(baseline().days[0], baseline().days[0])),
    dict(days=(Day(DAY, "CLOSED", time(12), time(20)),)),
    dict(days=(Day(DAY, "OPEN", time(20), time(12)),)),
    dict(first_seen=datetime(2097, 12, 1, 10, 1)),
    dict(first_seen=datetime(2097, 12, 1, 10, 1, tzinfo=timezone(timedelta(hours=8)))),
    dict(persisted_at=t("2097-12-01T09:00:00Z")), dict(supersedes="fixture:nonexistent")])
def test_missing_malformed_conflicting_fields_are_refused(changes):
    with pytest.raises(ValueError):
        registry(baseline(**changes))


def test_declared_license_expiry_stops_resolution():
    reg = registry(baseline(license_through=d("2098-12-28")))
    assert resolve(reg).reason == "LICENSE_OUTSIDE_DECLARED_RANGE"


def test_constructor_requires_fields_and_query_requires_explicit_scope_cutoff():
    with pytest.raises(TypeError):
        CalendarRecord(version_id="fixture:incomplete")
    with pytest.raises(TypeError):
        CalendarRegistry(fixture_only=True)
    with pytest.raises(TypeError):
        registry().resolve_day(day=DAY)
    with pytest.raises(ValueError, match="CALENDAR_EXPLICIT_UTC_REQUIRED"):
        resolve(registry(), cutoff=datetime(2098, 12, 29))


def test_later_notice_can_resolve_unknown_knowledge_without_rewriting_old_query():
    unknown = notice(first_seen=None)
    repair = notice(version_id="fixture:repair", supersedes=unknown.version_id,
        first_seen=t("2098-12-30T10:00:00Z"), persisted_at=t("2098-12-30T10:01:00Z"))
    reg = registry(baseline(), unknown, repair)
    assert resolve(reg).status == "UNKNOWN"
    assert resolve(reg, cutoff=t("2098-12-30T10:01:00Z")).status == "EARLY_CLOSE"
    assert resolve(reg).status == "UNKNOWN"


def test_new_annual_version_does_not_silently_drop_old_baseline_notice():
    base = baseline()
    new = baseline(version_id="fixture:new-base", supersedes=base.version_id)
    reg = registry(base, closure(), new)
    assert resolve(reg).reason == "NOTICE_REBIND_REQUIRED"


def test_newer_received_conflict_has_no_implicit_priority():
    reg = registry(baseline(), notice(), closure(first_seen=t("2098-12-28T11:00:00Z"),
        persisted_at=t("2098-12-28T11:01:00Z")))
    assert resolve(reg).reason == "NOTICE_CONFLICT"


def test_zero_distance_requires_known_anchor_and_valid_query_range():
    reg = registry(baseline())
    result = reg.session_distance(scope=SCOPE, start=DAY, end=DAY, cutoff=CUTOFF)
    assert result.status == "AVAILABLE" and result.value == 0
    with pytest.raises(ValueError, match="CALENDAR_QUERY_RANGE"):
        reg.next_session(scope=SCOPE, after=DAY, through=d("2098-12-29"), cutoff=CUTOFF)


def test_changed_same_source_must_link_its_actual_prior_version():
    first = baseline()
    unrelated = baseline(version_id="fixture:other-base", source_ref="fixture:other-source")
    changed = baseline(version_id="fixture:annual-v2", content_sha256="1" * 64,
                       supersedes=unrelated.version_id)
    with pytest.raises(ValueError, match="CALENDAR_NEW_VERSION_LINK_REQUIRED"):
        registry(first, unrelated, changed)


@pytest.mark.parametrize("unknown", [False, True])
def test_selected_evidence_is_separate_from_late_or_unknown_ancestry(unknown):
    first = baseline()
    intermediate = baseline(version_id="fixture:intermediate", supersedes=first.version_id,
        published_at=t("2098-12-01T10:00:00Z"),
        first_seen=None if unknown else t("2098-12-30T10:01:00Z"),
        persisted_at=None if unknown else t("2098-12-30T10:02:00Z"), content_sha256="1" * 64)
    tip = baseline(version_id="fixture:tip", supersedes=intermediate.version_id,
        published_at=t("2098-12-28T10:00:00Z"), first_seen=t("2098-12-28T10:01:00Z"),
        persisted_at=t("2098-12-28T10:02:00Z"), content_sha256="2" * 64)
    reg = registry(first, intermediate, tip)
    result = resolve(reg)
    assert result.status == "OPEN" and result.selected_ids == (tip.version_id,)
    assert result.ancestry_ids == (first.version_id, intermediate.version_id)
    assert set(result.selected_ids).isdisjoint(result.excluded_ids)
    assert intermediate.version_id not in result.selected_ids
    assert (intermediate.version_id in result.excluded_ids) is (not unknown)
    old = resolve(reg, cutoff=t("2098-12-27T21:00:00Z"))
    if unknown:
        assert old.status == "UNKNOWN"
    else:
        assert old.selected_ids == (first.version_id,)
