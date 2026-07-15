# TQQQ concrete research simulator v1

Caller-owned, offline `tqqq_growth_income` vertical slice. It consumes
validated `SessionClose`, `RequestedObservedWindow`, and the final
`ValidatedSessionSnapshot`; it does not write artifacts or call stores,
networks, brokers, live entrypoints or QPK runtime paths.

Input bars are strictly ordered by the raw `(date, symbol)` composite key,
unique, positive and finite. QQQ plus every manifest-managed target symbol is
required, with 200 QQQ warmup observations before the requested start.

At each observed session close, the existing planner sees data through that
close only. Its target dollar values are converted to allocation ratios against
prior-close equity. At the next session open, existing holdings are revalued,
actual open equity is computed, and ratios are applied; cash and holdings remain
conserved. The final close plan has no next open and is discarded. Costs are
fixed at zero for this research-only v1.

Result fields and metrics are immutable and deterministic. `as_of` is the
validated observed end/session close, distinct from requested dates. No
lookahead, latest behavior, persistence, or live behavior is implemented.
