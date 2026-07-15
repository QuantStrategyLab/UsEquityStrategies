# TQQQ research baseline v1

`us_equity.tqqq_research_baseline.v1` is a caller-owned, offline and research-only identity. It is not production-equivalent and is unrelated to `tqqq_growth_income` production execution.

- Profile: `tqqq_growth_income_research_baseline_v1`, domain `us_equity`, timing `next_open`.
- Control policy is explicit and immutable: `market_regime=false`, `crisis_defense=false`, `macro_risk_governor=false`, `volatility_delever=false`, `retention=false`, `taco=false`, `production_equivalent=false`. No open metadata or fallback is accepted.
- Input is an explicitly supplied, strictly `(date,symbol)` sorted daily frame with positive finite `open`/`close` for `BOXX`, `QQQ`, and `TQQQ`; the observed dates must exactly equal the supplied session/window dates and 200 QQQ warm-up observations are required.
- The baseline-owned planner is fixed QQQ SMA200: 90% TQQQ/10% BOXX only when the close is above its 200-observation mean, otherwise 100% BOXX. Signals use close `t`; execution uses next-day open. Existing holdings are marked at next open before allocation; costs are zero by explicit v1 policy; the final day's unexecutable plan is discarded.
- Result is immutable in memory, canonical JSON only, and includes source revision, explicit UTC `computed_at`, input/parameter digests, run identity, session-bound as-of, equity curve, returns, finite metrics and policy. No store/filesystem/QPK/network/live/order/account/funds/leverage side effects.
