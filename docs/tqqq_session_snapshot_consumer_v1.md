# TQQQ session-bound snapshot consumer v1

This research-only slice consumes the merged pure `SessionClose` and
`RequestedObservedWindow` contracts and wraps an existing
`PortfolioSnapshot` in a closed immutable `SessionBoundSnapshot`.

The consumer preserves total equity, buying power, cash balance and positions,
but intentionally does not retain source metadata. It accepts no open
`contract_metadata` mapping, so reserved session/window/as-of fields cannot be
overwritten and nested mutable aliases cannot escape. Session/window/as-of
consistency is validated before construction. The exact wire shape contains
only known scalar fields, validated positions, and the two pure contract wire
objects; canonical JSON is deterministic.

No planner algorithm, metrics, persistence, QPK store, filesystem, network,
live entrypoint, order, account, funds or leverage behavior is changed.
