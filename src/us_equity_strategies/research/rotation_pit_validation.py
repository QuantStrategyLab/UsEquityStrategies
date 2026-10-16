"""Opt-in, offline checks of supplied rotation research metadata.

This does not authenticate visibility/coverage assertions or qualify a strategy.
It does not import or change any production consumer.
"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import math


@dataclass(frozen=True)
class RotationPitCheck:
    enabled: bool
    members: tuple[str, ...] = ()
    live_ready: bool = False


def _time(value, field: str) -> datetime:
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{field} requires timezone")
    return result


def _symbols(values) -> tuple[str, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or not values:
        raise ValueError("members must be a nonempty sequence")
    result = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("invalid symbol")
        result.append(value.strip().upper())
    if len(result) != len(set(result)):
        raise ValueError("duplicate symbol")
    return tuple(result)


def validate_rotation_pit_inputs(
    *, enabled: bool = False, window_start=None, window_end=None, decision_at=None,
    snapshots=(), selected_symbols=(), security_history=None,
) -> RotationPitCheck:
    """Reject unsupported metadata before a caller starts an offline replay.

    Snapshots are complete member lists, not additions. Visibility must come
    from independent evidence; an effective date alone never implies visibility.
    Security coverage includes removed members, not just the selected survivors.
    """
    if enabled is False:
        return RotationPitCheck(enabled=False)
    if enabled is not True:
        raise ValueError("enabled must be boolean")
    start = _time(window_start, "window_start")
    end = _time(window_end, "window_end")
    decision = _time(decision_at, "decision_at")
    if not start <= decision <= end:
        raise ValueError("decision outside research window")
    if not isinstance(security_history, Mapping):
        raise ValueError("security_history required")
    if not snapshots:
        raise ValueError("first_snapshot missing")
    versions = []
    for snapshot in snapshots:
        if not isinstance(snapshot, Mapping):
            raise ValueError("invalid snapshot")
        effective = _time(snapshot.get("effective_at"), "effective_at")
        visible = _time(snapshot.get("visible_at"), "visible_at")
        versions.append((effective, visible, _symbols(snapshot.get("members"))))
    if len({effective for effective, _, _ in versions}) != len(versions):
        raise ValueError("duplicate snapshot effective_at")
    if not any(effective <= start and visible <= start for effective, visible, _ in versions):
        raise ValueError("first_snapshot cannot be backfilled before visibility")
    eligible = [v for v in versions if v[0] <= decision and v[1] <= decision]
    members = max(eligible, key=lambda version: version[0])[2]
    selected = _symbols(selected_symbols)
    if not set(selected).issubset(members):
        raise ValueError("future_member or nonmember selected")

    # Also validate disappeared members; dropping them would hide survivorship.
    all_members = {symbol for effective, _, symbols in versions if effective <= end for symbol in symbols}
    for symbol in all_members:
        record = security_history.get(symbol)
        if not isinstance(record, Mapping):
            raise ValueError("security_history missing member")
        if (_time(record.get("coverage_start"), "coverage_start") > start
                or _time(record.get("coverage_end"), "coverage_end") < end):
            raise ValueError("security coverage incomplete")
        if record.get("delisting_complete") is not True:
            raise ValueError("delisting coverage unknown")
        if record.get("corporate_actions_complete") is not True:
            raise ValueError("corporate_actions coverage unknown")
        listed = _time(record.get("listed_at"), "listed_at")
        if "delisted_at" not in record:
            raise ValueError("delisting status missing")
        delisted = None if record["delisted_at"] is None else _time(record["delisted_at"], "delisted_at")
        if delisted is not None:
            if delisted < listed:
                raise ValueError("delisting precedes listing")
            if delisted <= end:
                terminal = record.get("terminal_value")
                if (isinstance(terminal, bool) or not isinstance(terminal, (int, float))
                        or not math.isfinite(terminal) or terminal < 0):
                    raise ValueError("terminal_value required for delisted member")
        membership_times = [effective for effective, _, symbols in versions if symbol in symbols and effective <= end]
        if any(listed > effective or (delisted is not None and delisted <= effective) for effective in membership_times):
            raise ValueError("snapshot contains member not_listed at effective time")
        if symbol in members and (listed > decision or (delisted is not None and delisted <= decision)):
            raise ValueError("current member not_listed at decision")
    return RotationPitCheck(enabled=True, members=members)
