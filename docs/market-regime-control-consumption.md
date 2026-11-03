# Market regime and QQQ observation consumption

When `market_regime_control_enabled` is true, the weight consumer requires an independent `market_regime_control_expected` binding in runtime configuration. The binding contains `plugin`, `schema_version`, `as_of`, `valid_until` and `payload_sha256`. The digest covers the exact signal encoded as UTF-8 JSON with sorted keys, ASCII escaping, compact separators and no non-finite numbers. The payload date must equal the binding date and the decision date must lie between `as_of` and `valid_until`, inclusive.

A missing, expired, mismatched or invalid binding makes ordinary targets non-actionable. A separately established strategy emergency retains its existing emergency target; account-level brake behavior and order authority remain platform responsibilities. An explicit valid scalar of zero is preserved. Invalid scalars do not create a liquidation target.

The optional Global ETF QQQ observer uses `qqq_observer_expected_binding` with independently supplied `producer`, `input` and lowercase `payload_sha256`. A missing or invalid binding produces an unavailable diagnostic; a matching observation is recorded as read-only and does not change target weights. A hash match is not producer authentication. The runtime operator must supply trusted bindings; the payload cannot authorize itself.
