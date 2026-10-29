# RS-04 cloud archive registration

The cloud producer completed one create-only regeneration on 2026-10-15.
This registration follows the source-identity PR flow used in
[RS-04e #580](https://github.com/QuantStrategyLab/UsEquityStrategies/pull/580).
The B3 and SOXL V7 de-risk qualification reports now expect the new manifest
digest. Historical Kelly preregistrations and trial records retain their original
identities; this is a new archive, not an alias for old bytes.

| Metadata | Registered value |
| --- | --- |
| Producer run | [RS-04 Cloud Archive](https://github.com/QuantStrategyLab/UsEquitySnapshotPipelines/actions/runs/38675222637) |
| Producer revision | `d3217b2297dbde7c3ce7f56ff6b9ef2c99ea2fbb` |
| Locator | `<configured input_root>/rs04-alpaca-raw-20261015-refreshed/` |
| Input-root setting | Workflow input `input_root`, falling back to repository variable `RS04_INPUT_ROOT`; no default bucket is supplied |
| Manifest object | `manifest.json`, 3083 bytes, generation `1792034116906264` |
| Manifest SHA-256 | `d5d40dfc54f55fe59c0eba57302ef9b018d006a0835f33f969053e52d05cc9e7` |
| Final registration object | `registration.json`, 2683 bytes, generation `1792034117144504` |
| Registration SHA-256 | `6dc9ea78b279aadc61b9e21ec16be216c548cd6c9876d61b085767baccd9f9cd` |
| Previous manifest SHA-256 | `b5a3dd40cb0d4260fb15492108bde7ab544ac0d191807943637b8216a6808482` (provenance only for this qualification path) |

The producer reported `ARCHIVE_REGISTERED` only after create-only writes and
exact-generation byte readback in the cloud runner. The sizes above describe
the two named metadata objects, not the sum of all archive members. Verification
used the producer's sanitized log output; no archive or market-data object was
downloaded to the local machine.

The acquisition uses Alpaca SIP raw daily bars plus corporate actions for
SOXL, SOXX, QQQ, TQQQ, QQQM and BOXX through 2026-08-25. The producer enforces
1187 sessions from 2021-12-01 for the first five symbols and 917 sessions from
2022-12-28 for BOXX. Content is private research input and is not committed.

The producer reported `LIVE_CONFIG_URI_UNAVAILABLE` and historical context
`NOT_BOUND`. Matching this manifest digest does not qualify historical
point-in-time availability, effective live configuration, parity, fees or the
trial universe. Existing qualification gates still return `PARKED` with
`INSUFFICIENT_EVIDENCE`; this registration authorizes no trade or deployment.
