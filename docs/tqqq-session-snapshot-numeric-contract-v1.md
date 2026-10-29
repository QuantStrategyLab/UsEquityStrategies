# TQQQ session snapshot numeric contract v1

Fresh reslice after frozen PR #250. The consumer is closed and immutable: it
consumes merged `SessionClose`/`RequestedObservedWindow`, preserves financial
fields and positions as `ValidatedPosition` scalar values, and retains no source
metadata.

Wire numeric grammar is explicit: financial and position numeric fields must be
JSON numbers represented by Python `int`/`float` objects before parsing; strings,
booleans and `null` are rejected for required fields. Optional buying power,
cash balance and average cost may be JSON `null`. All numbers must be finite,
non-negative-zero and within `abs(value) <= 2^53-1` for cross-consumer JSON
determinism. Values are normalized to finite floats after validation. Unknown
keys, unsafe numbers and session/window/as-of mismatches fail closed with
sanitized `SessionContractError`.

`ValidatedPosition` applies the same checks in its direct constructor and in
`dataclasses.replace`; huge Python integers are range-checked before float
conversion, so overflow cannot escape as a raw exception.

No algorithm, metrics, QPK/store, filesystem, network, live/order/funds or
leverage behavior is included.
