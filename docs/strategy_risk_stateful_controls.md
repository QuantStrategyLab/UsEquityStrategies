# Stateful volatility-deleveraging control

`us_equity_strategies.volatility_delever_cooldown` is a pure, reusable rule
for strategies that need to prevent immediate re-entry after a local
volatility-deleveraging event. It can apply to SOXL, TQQQ, TECL, or another
strategy, but it is deliberately not part of a broker, plugin, allocation, or
runtime target.

For a two-session cooldown, a trigger blocks the trigger session plus the next
two effective sessions. A further trigger resets the countdown. Re-entry may
only be considered in the following session, and still needs every existing
strategy/regime/position guard to pass.

The helper refuses malformed state, stale dates, changed cooldown settings, or
ambiguous boolean input. `build_volatility_delever_cooldown_transition` then
wraps the result in QPK's immutable `StrategyRiskStateTransition`, binding it
to the frozen strategy candidate, configuration, account scope, frozen input,
and prior transition.

This is a research and paper-adapter building block only. It does **not** alter
the current SOXL configuration, make an unqualified candidate promotion
eligible, persist state, enable a platform, or submit an order. A future
paper-only platform adapter must use an append-only store and fail closed on a
missing predecessor, duplicate writer, stale source, or divergent frozen input.
