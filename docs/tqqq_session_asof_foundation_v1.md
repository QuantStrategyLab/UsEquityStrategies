# TQQQ session/as-of foundation v1

This research-only foundation requires caller-supplied `SessionClose` values:
`trading_date` plus canonical UTC `close_at_utc`. The instant must convert to
the same date in `America/New_York`; the foundation never infers holidays,
half-days, timestamps, or `now()`.

Requested window fields are separate from observed fields. `as_of` is always the
last validated observed trading date. By default the requested end must be
observed; callers may explicitly opt into a shorter observed window and receive
the actual `as_of` rather than a misleading requested date.

`build_close_snapshot` stamps the existing planner snapshot with the validated
session-close instant and includes the explicit trading date in metadata. No
store, artifact, network, live, order, account, funds, or leverage behavior is
introduced.
