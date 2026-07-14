# TQQQ session/as-of foundation v1

This research-only foundation requires caller-supplied `SessionClose` values:
`trading_date` plus canonical UTC `close_at_utc`. The instant must convert to
the same date in `America/New_York`; the foundation never infers holidays,
holidays or `now()`. The converted local time must be exactly 16:00 regular
close or 13:00 approved half-day close, with zero seconds/microseconds.

Requested window fields are separate from observed fields. `as_of` is always the
last validated observed trading date. By default the requested end must be
observed; callers may explicitly opt into a shorter observed window and receive
the actual `as_of` rather than a misleading requested date.

`build_close_snapshot` accepts an existing planner snapshot, changes only
`as_of`, and merges contract metadata (including the explicit trading date).
All equity, cash, buying power, positions, and existing metadata are retained.
No store, artifact, network, live, order, account, funds, or leverage behavior
is introduced.
