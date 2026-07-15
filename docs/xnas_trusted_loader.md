# Trusted XNAS loader C0

`load_checked_in_xnas_calendar()` is the only public consumer API and accepts no path, bytes or anchor. It reads the exact checked-in artifact adjacent to the module, verifies its raw SHA-256 against an internal code-reviewed anchor, then performs schema-first canonical/inventory/session validation. Parser and anchor are private implementation details; changing the artifact and anchor requires one reviewed PR.

Artifact revision `xnas-2025-research-v1`, generator `offline.xnas.generator.v1`, coverage 2025-01-02..2025-07-21, count 137, inventory SHA `3e8e2933495b077abfd468eaf03c0d6dc984985f7f2bab34a0a791a5437cc2ac`, raw artifact SHA `482b930456e635d9c27c1fbd96d1ea9bd1932e2c212cfb60f7247f9fd4a6b79c`.
