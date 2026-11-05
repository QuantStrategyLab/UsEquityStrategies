"""Offline B/U1 structural draft. Accepts artificial fixtures only.

No source fetching, consumer integration, real-market admission, license
verification or historical PIT attestation is provided by this module.
Calendar document clocks are independent of the unchanged session T1 contract.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise ValueError("CALENDAR_" + reason)


def _fixture(value: object) -> bool:
    return isinstance(value, str) and value.startswith("fixture:") and len(value) > 8


def _utc(value: datetime) -> None:
    _require(isinstance(value, datetime) and value.utcoffset() == timedelta(0),
             "EXPLICIT_UTC_REQUIRED")


def _date(value: date) -> None:
    _require(type(value) is date, "DATE_REQUIRED")


@dataclass(frozen=True)
class Scope:
    scope_id: str
    segment: str
    timezone: str
    kind: str

    def validate(self) -> None:
        _require(_fixture(self.scope_id) and _fixture(self.segment), "FIXTURE_SCOPE_REQUIRED")
        _require(self.kind in {"MARKET", "SINGLE_SECURITY", "VENUE"}, "SCOPE_KIND")
        try:
            ZoneInfo(self.timezone)
        except (ValueError, TypeError, KeyError):
            raise ValueError("CALENDAR_TIMEZONE_REQUIRED") from None


@dataclass(frozen=True)
class Day:
    day: date
    status: str
    open_utc: time | None
    close_utc: time | None

    def validate(self) -> None:
        _date(self.day)
        _require(self.status in {"OPEN", "EARLY_CLOSE", "CLOSED", "TEMP_CLOSED", "UNKNOWN"},
                 "DAY_STATUS")
        for clock in (self.open_utc, self.close_utc):
            _require(clock is None or (type(clock) is time and clock.tzinfo is None), "DAY_TIME")
        if self.open_utc is not None and self.close_utc is not None:
            _require(self.open_utc < self.close_utc, "DAY_TIME_ORDER")
        if self.status in {"CLOSED", "TEMP_CLOSED", "UNKNOWN"}:
            _require(self.open_utc is None and self.close_utc is None, "NON_SESSION_TIME")


@dataclass(frozen=True)
class CalendarRecord:
    version_id: str
    kind: str
    scope: Scope | None
    year: int
    coverage_start: date | None
    coverage_end: date | None
    effective_from: date | None
    effective_through: date | None
    source_ref: str | None
    title: str
    revision: str
    content_sha256: str
    published_at: datetime | None
    first_seen: datetime | None
    persisted_at: datetime | None
    license_ref: str
    license_version: str
    license_from: date
    license_through: date
    verification_ref: str
    reason: str
    baseline_id: str | None
    supersedes: str | None
    days: tuple[Day, ...]

    def validate(self) -> None:
        _require(self.kind in {"BASELINE", "NOTICE"}, "RECORD_KIND")
        _require(all(_fixture(v) for v in (self.version_id, self.revision, self.license_ref,
            self.license_version, self.verification_ref, self.reason)), "FIXTURE_METADATA_REQUIRED")
        _require(isinstance(self.title, str) and bool(self.title.strip()), "TITLE_REQUIRED")
        _require(self.source_ref is None or _fixture(self.source_ref), "FIXTURE_SOURCE_REQUIRED")
        _require(isinstance(self.content_sha256, str) and len(self.content_sha256) == 64
                 and all(c in "0123456789abcdef" for c in self.content_sha256), "CONTENT_DIGEST")
        _require(type(self.year) is int and 1 <= self.year <= 9999, "YEAR_REQUIRED")
        _require(self.scope is None or isinstance(self.scope, Scope), "SCOPE_REQUIRED")
        if self.scope is not None:
            self.scope.validate()
        if self.kind == "BASELINE":
            _require(self.scope is not None and self.source_ref is not None
                     and self.baseline_id is None, "BASELINE_IDENTITY")
            _require(all(v is not None for v in (self.coverage_start, self.coverage_end,
                self.effective_from, self.effective_through)), "BASELINE_RANGE_REQUIRED")
        else:
            _require(_fixture(self.baseline_id), "NOTICE_BASELINE_REQUIRED")
        for start, end in ((self.coverage_start, self.coverage_end),
                           (self.effective_from, self.effective_through)):
            for value in (start, end):
                if value is not None:
                    _date(value)
                    _require(value.year == self.year, "YEAR_RANGE_MISMATCH")
            if start is not None and end is not None:
                _require(start <= end, "RANGE_ORDER")
        clocks = (self.published_at, self.first_seen, self.persisted_at)
        for value in clocks:
            if value is not None:
                _utc(value)
        known = [v for v in clocks if v is not None]
        _require(known == sorted(known), "KNOWLEDGE_TIME_ORDER")
        _date(self.license_from)
        _date(self.license_through)
        _require(self.license_from <= self.license_through, "LICENSE_RANGE")
        _require(type(self.days) is tuple and all(type(v) is Day for v in self.days), "DAYS_REQUIRED")
        _require(len({v.day for v in self.days}) == len(self.days), "DUPLICATE_DAY")
        for value in self.days:
            value.validate()
            _require(value.day.year == self.year, "YEAR_RANGE_MISMATCH")
            for start, end in ((self.coverage_start, self.coverage_end),
                               (self.effective_from, self.effective_through)):
                _require((start is None or start <= value.day)
                         and (end is None or value.day <= end), "DAY_OUTSIDE_RANGE")
        _require(self.supersedes is None or _fixture(self.supersedes), "SUPERSEDES_ID")


@dataclass(frozen=True)
class Resolution:
    scope: Scope
    day: date
    cutoff: datetime
    status: str
    reason: str
    selected_ids: tuple[str, ...] = ()
    excluded_ids: tuple[str, ...] = ()
    # Superseded references are lineage, not evidence admitted as known at cutoff.
    ancestry_ids: tuple[str, ...] = ()
    open_utc: time | None = None
    close_utc: time | None = None
    pit: str = "NOT_ATTESTED"

    @property
    def is_session(self) -> bool | None:
        if self.status == "UNKNOWN":
            return None
        return self.status in {"OPEN", "EARLY_CLOSE"}


@dataclass(frozen=True)
class Derivation:
    status: str
    value: date | int | None
    blocked_dates: tuple[date, ...]
    resolutions: tuple[Resolution, ...]


@dataclass(frozen=True)
class CalendarRegistry:
    fixture_only: bool
    scope: Scope
    owner: str
    license_ref: str
    records: tuple[CalendarRecord, ...] = ()

    def __post_init__(self) -> None:
        _require(self.fixture_only is True, "FIXTURE_ONLY")
        _require(isinstance(self.scope, Scope), "SCOPE_REQUIRED")
        self.scope.validate()
        _require(_fixture(self.owner), "OWNER_REQUIRED")
        _require(_fixture(self.license_ref), "LICENSE_REQUIRED")
        _require(type(self.records) is tuple, "RECORDS_REQUIRED")
        previous: dict[str, CalendarRecord] = {}
        for record in self.records:
            _require(type(record) is CalendarRecord, "RECORD_REQUIRED")
            record.validate()
            _require(record.license_ref == self.license_ref, "LICENSE_MISMATCH")
            _require(record.version_id not in previous, "DUPLICATE_VERSION")
            if record.kind == "NOTICE":
                base = previous.get(record.baseline_id)
                _require(base is not None and base.kind == "BASELINE" and base.year == record.year
                         and (record.scope is None or record.scope == base.scope), "NOTICE_BASELINE")
            if record.supersedes is not None:
                old = previous.get(record.supersedes)
                _require(old is not None and old.kind == record.kind
                         and (old.scope is None or old.scope == record.scope)
                         and old.year == record.year and old.baseline_id == record.baseline_id,
                         "SUPERSEDES_RELATION")
            changed_sources = [v for v in previous.values()
                if record.source_ref is not None and v.source_ref == record.source_ref
                and v.scope == record.scope and v.year == record.year
                and v.content_sha256 != record.content_sha256]
            if changed_sources:
                ancestors = set()
                parent = record.supersedes
                while parent is not None:
                    ancestors.add(parent)
                    parent = previous[parent].supersedes
                _require(all(v.version_id in ancestors for v in changed_sources),
                         "NEW_VERSION_LINK_REQUIRED")
            previous[record.version_id] = record

    def register(self, record: CalendarRecord) -> CalendarRegistry:
        for old in self.records:
            if old.version_id == record.version_id:
                _require(old == record, "VERSION_CONFLICT")
                return self
        return replace(self, records=(*self.records, record))

    def _chain(self, record: CalendarRecord) -> tuple[str, ...]:
        by_id = {v.version_id: v for v in self.records}
        result = [record.version_id]
        while record.supersedes is not None:
            record = by_id[record.supersedes]
            result.append(record.version_id)
        return tuple(reversed(result))

    def resolve_day(self, *, scope: Scope, day: date, cutoff: datetime) -> Resolution:
        """Select only explicitly evidenced dates as of caller-declared UTC cutoff."""
        _require(isinstance(scope, Scope), "SCOPE_REQUIRED")
        scope.validate()
        _date(day)
        _utc(cutoff)
        selected: list[str] = []
        excluded: list[str] = []
        ancestry: list[str] = []

        def result(status: str, reason: str, entry: Day | None = None) -> Resolution:
            return Resolution(scope, day, cutoff, status, reason,
                selected_ids=tuple(selected), excluded_ids=tuple(sorted(excluded)),
                ancestry_ids=tuple(ancestry), open_utc=entry.open_utc if entry else None,
                close_utc=entry.close_utc if entry else None)

        if scope != self.scope:
            return result("UNKNOWN", "SCOPE_NOT_REGISTERED")
        candidates = []
        uncertainties = {}
        for record in self.records:
            if record.year != day.year or (record.scope is not None and record.scope != scope):
                continue
            # Known bounds restrict impact even when the opposite bound is unknown.
            if any((start is not None and day < start) or (end is not None and day > end)
                   for start, end in ((record.coverage_start, record.coverage_end),
                                      (record.effective_from, record.effective_through))):
                continue
            clocks = (record.published_at, record.first_seen, record.persisted_at)
            if any(v is not None and v > cutoff for v in clocks):
                excluded.append(record.version_id)
                continue
            reason = None
            if any(v is None for v in clocks):
                reason = "UNKNOWN_KNOWLEDGE"
            elif record.scope is None or record.source_ref is None:
                reason = "UNRESOLVED_SOURCE_OR_SCOPE"
            elif any(v is None for v in (record.coverage_start, record.coverage_end,
                                       record.effective_from, record.effective_through)):
                reason = "UNRESOLVED_EFFECTIVE_RANGE"
            elif not (record.license_from <= record.first_seen.date() <= record.license_through
                    and record.license_from <= cutoff.date() <= record.license_through):
                reason = "LICENSE_OUTSIDE_DECLARED_RANGE"
            if reason:
                uncertainties[record.version_id] = reason
            candidates.append(record)
        candidates = [v for v in candidates if not any(v.version_id in self._chain(other)[:-1]
                                                      for other in candidates)]
        unresolved_scope = [v for v in candidates if v.scope is None]
        if unresolved_scope:
            return result("UNKNOWN", "UNRESOLVED_SOURCE_OR_SCOPE")
        bases = [v for v in candidates if v.kind == "BASELINE"]
        bases = [v for v in bases if not any(v.version_id in self._chain(other)[:-1]
                                            for other in bases)]
        if len(bases) != 1:
            return result("UNKNOWN", "BASELINE_CONFLICT" if bases else "MISSING_ANNUAL_COVERAGE")
        base = bases[0]
        selected.append(base.version_id)
        ancestry.extend(self._chain(base)[:-1])
        if base.version_id in uncertainties:
            return result("UNKNOWN", uncertainties[base.version_id])
        if any(v.kind == "NOTICE" and v.baseline_id in self._chain(base)[:-1] for v in candidates):
            return result("UNKNOWN", "NOTICE_REBIND_REQUIRED")
        notices = [v for v in candidates if v.kind == "NOTICE"
                   and v.baseline_id == base.version_id]
        tips = [v for v in notices if not any(v.version_id in self._chain(other)[:-1]
                                             for other in notices)]
        if len(tips) > 1:
            return result("UNKNOWN", "NOTICE_CONFLICT")
        chosen = tips[0] if tips else base
        if tips:
            selected.append(chosen.version_id)
            ancestry.extend(self._chain(chosen)[:-1])
        if chosen.version_id in uncertainties:
            return result("UNKNOWN", uncertainties[chosen.version_id])
        entry = next((v for v in chosen.days if v.day == day), None)
        if entry is None or entry.status == "UNKNOWN":
            return result("UNKNOWN", "MISSING_DAY_EVIDENCE")
        if entry.status in {"OPEN", "EARLY_CLOSE"} and (
                entry.open_utc is None or entry.close_utc is None):
            return result("UNKNOWN", "MISSING_SESSION_TIME")
        return result(entry.status, "EXPLICIT_FIXTURE_EVIDENCE", entry)

    def next_session(self, *, scope: Scope, after: date, through: date,
                     cutoff: datetime) -> Derivation:
        """Bounded search over (after, through]; UNKNOWN cannot be skipped."""
        return self._derive(scope, after, through, cutoff, next_only=True)

    def session_distance(self, *, scope: Scope, start: date, end: date,
                         cutoff: datetime) -> Derivation:
        """Count explicit sessions in (start, end]; validate start as a dependency."""
        return self._derive(scope, start, end, cutoff, next_only=False)

    def _derive(self, scope, start, end, cutoff, *, next_only):
        _date(start)
        _date(end)
        _utc(cutoff)
        _require(start <= end, "QUERY_RANGE")
        rows = []
        count = 0
        # The anchor itself must be known; it need not be an open session.
        current = start
        while True:
            row = self.resolve_day(scope=scope, day=current, cutoff=cutoff)
            rows.append(row)
            if row.status == "UNKNOWN":
                return Derivation("UNAVAILABLE", None, (current,), tuple(rows))
            if current > start and row.is_session:
                if next_only:
                    return Derivation("AVAILABLE", current, (), tuple(rows))
                count += 1
            if current == end:
                break
            current += timedelta(days=1)
        if next_only:
            return Derivation("UNAVAILABLE", None, (), tuple(rows))
        return Derivation("AVAILABLE", count, (), tuple(rows))
