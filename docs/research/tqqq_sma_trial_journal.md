# TQQQ SMA attempt journal

The opt-in `run_journaled_tqqq_core_optimization` entrypoint records new attempts for the existing frozen TQQQ SMA study. It reuses the original simulation and evaluation body. The legacy `run_tqqq_core_optimization` entrypoint and its invocation-only twelve-slot manifest retain their existing behavior. No historical attempts or archived evidence are backfilled.

## Scope and identity

This entrypoint currently requires `synthetic=True`, an explicit trial namespace, and an explicit local `PerformanceStore(local_root=..., cloud_bucket="")`. Real-run qualification is refused. A caller's synthetic label never authenticates input provenance or qualifies data. Tests use generated artificial bars and a temporary local store; they bind only their own test artifact hash, following the legacy test convention.

Each namespace contains the twelve fixed SMA-window/cost slots. Before a slot executes, its STARTED record is written and read back. Identity freezes the SMA window, distinct commission/slippage inputs, initial equity, study configuration, input artifact bytes/hash, accepted input-digest literal and actual implementation hashes. The accepted composite input digest remains a literal comparison, not independently recomputed provenance. The shared journal helper, SMA adapter, SMA evaluation body, input/baseline modules and executing QPK contracts/store are included in the implementation identity. This is a bounded fingerprint, not a claim to hash the complete dependency graph or execution environment.

The consumer uses its existing QPK pin `62bcd5f6d5e236c315a7383ddf7b35e2aac30b62`; no schema or dependency upgrade is required. Preflight uses that store's existing raw research-object existence read to distinguish absent evidence from an invalid typed readback. Invalid evidence fails closed rather than becoming an apparently unattempted slot.

## Readback and restart behavior

A slot succeeds only after its QPK result and daily ledger are stored and the terminal record passes the store's matching readback. A slot's success means that its simulation and accounting triplet are verified. It does not mean that later aggregate selection, Monte Carlo evaluation, qualification, or publication succeeded.

An existing successful slot is never simulated again. Repeating a fully successful namespace returns `stored`, with `optimization=None`; it does not reconstruct or rerun the aggregate study. Partial attempts return `incomplete` or their known rejected/failed/aborted status, leaving never-started slots untouched. Changed parameters, input bytes, or implementation identity conflict with the old namespace. A genuinely new attempt needs a new namespace.

An interrupted process can leave STARTED without a terminal. A caught KeyboardInterrupt/SystemExit is rethrown after best-effort ABORTED recording. Failed secondary recording never replaces the original exception. A terminal write that may already have committed is not compensated over a verified or uncertain immutable terminal. Aggregate evaluation failure leaves already verified per-slot successes intact and returns a rejected aggregate result.

This is single-caller replay protection. It is not a distributed lock or atomic claim between concurrent workers. Callers must not run the same namespace concurrently.

## Accounting and unchanged research semantics

The adapter preserves fractional TQQQ shares and source-close valuations. Equity, cash, commissions, slippage impact and trade notional are USD. Commission/slippage parameters remain separate basis-point values, converted to rates by dividing by 10,000. Ledger fees contain commission only; trade cashflow uses the original adverse fill, so slippage is not subtracted a second time. Returns are signed decimal fractions.

The initial ledger mark is the source session preceding the first execution. Initial NAV/cash remain USD 100,000. Return observations begin at the existing SMA execution index. Calendar identity remains XNYS with 252 periods/year; synthetic dates do not validate a real exchange calendar. The frozen SMA model does not supply corporate-action events, so the ledger does not claim complete corporate-action evidence.

Candidate windows, signals, next-open timing, fee rates, selection rules, Monte Carlo settings and holdout boundaries are unchanged. The new journal provides no untouched-holdout claim, promotion authority, position-sizing authority or execution authority.

The guard-cash caller now delegates to the same internal transaction helper. Its financial behavior and report fields retain their meaning, while its actual implementation hashes naturally change. Its source revision now fingerprints the caller and shared helper. Old stored identities are not forged, rewritten or relabeled as the new implementation.

## Frozen evaluation semantics

New attempts include `evaluation_contract` in the existing `study_config`, so the
existing candidate configuration digest, STARTED/readback and result parameters
bind it before simulation. The outer journal report echoes that same contract;
the legacy core result, financial formulas, ordering and eligibility remain
unchanged. No QPK schema or dependency pin changes are required. A changed
contract conflicts with an existing namespace before simulation and never
rewrites or backfills old evidence; use a new namespace for a new attempt.

This adopts the reporting discipline of the fixed
[QPK evaluation standard](https://github.com/QuantStrategyLab/QuantPlatformKit/blob/4cbb0bbdffc35994be4b21cbacdcd4c771abe883/docs/strategy_promotion_risk_standard.zh-CN.md)
within this synthetic-only caller under `STRAT-02`. The
[UES portfolio boundary](https://github.com/QuantStrategyLab/UsEquityStrategies/blob/fa3f6b3737983d2c3c700a6fb57f63dcb6179bfd/docs/research/portfolio_risk_budget_contract.md)
still governs distinct Markowitz/Kelly/log-growth research; this adds no allocator.

The declaration describes actual `core._window_metrics`,
`core._five_metric_winner`, `core._eligibility`, bootstrap and `_build_ledger`
behavior, bound to their existing implementation hashes. Window returns are
signed decimal session-level account net returns including zero-return sessions;
sample volatility uses `ddof=1` and `sqrt(252)`. Journal CAGR uses 252 divided by
return observations, not calendar years. Initial NAV contributes to drawdown but
is not an extra return observation. ES95 averages the worst `ceil(N*0.05)` signed
returns; the existing window evidence supplies the actual tail count.

Current window metrics do not use rf or MAR, and do not compute Sharpe/Sortino;
these remain `NOT_USED`/uncomputed, never a fabricated zero. Effective independent
sample size is unknown. Configured bootstrap path counts are neither execution
receipts nor additional independent observations. Autocorrelation-adjusted Sharpe,
DSR/PBO, complete corporate actions and separate financing/market-impact/FX
modeling remain unimplemented here. Cash interest is not accrued in the frozen
model. Existing USD cost accounting above is preserved without a second deduction.

Return, drawdown, volatility and ES participate in the existing Pareto comparison;
stress return must also be no worse than other candidates. If there is no unique
winner, the baseline is retained. Fold/final positive-return conditions and bootstrap terminal
loss probability below 0.5 are the existing eligibility vetoes. Cost amount,
transition count and journal CAGR are reported without new financial thresholds.
The core also compares candidate metrics on `FINAL_HOLDOUT` for its final
recommendation. This declaration does not establish an untouched holdout or
independent OOS, and 252 is an annualization basis, not verified forward history.

Synthetic regression checks compare the common TQQQ/SOXL window statistics on
NAV 100→90→90→99 and distinguish actual zero from SOXL's undefined Sharpe and
TQQQ's uncomputed Sharpe. They also check STARTED identity conflicts, exact legacy
result parity, cost accounting and network refusal. SOXL receives no journal
adoption from these comparisons, and no synthetic check qualifies real data.

## Acceptance boundary

Synthetic mechanism checks do not verify a production durable location, retention, permissions, deployed environment, qualified data, or a real research run. Those require a separately approved location/input/execution context and readback acceptance. This source change performs no cloud-bucket write, deployment, market/provider/model/broker access or real-data replay.
