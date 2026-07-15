# Trusted calendar anchor

The checked-in XNAS loader requires `TRUSTED_XNAS_ANCHOR` before parsing. Anchor fields are code-reviewed and bind schema, exchange, timezone, revision, source generator, coverage, expected count, inventory digest and raw artifact SHA-256. Payload self-digests are not trust roots; payload changes require the external raw-byte hash and anchor metadata to match in the same reviewed PR.

Artifact raw SHA-256: `482b930456e635d9c27c1fbd96d1ea9bd1932e2c212cfb60f7247f9fd4a6b79c`.
