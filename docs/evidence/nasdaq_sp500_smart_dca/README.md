# Nasdaq/S&P 500 Smart DCA — P3 evidence boundary

This directory is reserved for the unified P3 evidence package for
`nasdaq_sp500_smart_dca`.

The current research notes and tests are not promoted to a P3 evidence package
yet. The existing sweep is a price-only proxy and does not include a committed,
hash-pinned input manifest, point-in-time validation, or a complete cost/turnover
ledger. Those omissions must be resolved before the lifecycle matrix can mark
P3 as verified.

## Current status

- lifecycle stage: `P3`
- status: `DEFERRED`
- authority: research only; `no_order=true`
- source notes: `docs/research/nasdaq_sp500_smart_dca.md`
- follow-up matrix: `docs/research/nasdaq_sp500_price_proxy_matrix_2026-06-19.md`

## Required artifacts before registration

1. Hash-pinned QQQ/SPY proxy input manifest.
2. Frozen configuration snapshot and code revision.
3. Benchmark and cost-model records.
4. Reproducible trial ledger and locked holdout result.
5. Validated evidence package consumed by `gate_evidence_package.py`.

No file in this directory asserts performance, shadow, paper, live, or capital
authority. It is intentionally a visible placeholder so the missing evidence
cannot be mistaken for an untracked implementation gap.
