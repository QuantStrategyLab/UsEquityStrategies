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

## Independent synthetic metric analysis

`compute_synthetic_research_metrics` is a separate, pure opt-in research function
for an existing synthetic `ResearchDailyLedger`. It accepts explicit
`annual_risk_free_rate` and `annual_minimum_acceptable_return` (MAR), both finite
annual simple decimal fractions. Each is divided by 252 to obtain its session
rate; this is not a compounded annual-to-session conversion. Negative rates are
allowed. The ledger must declare XNYS/252, and external cash flows are refused.
This declaration does not verify a real exchange calendar or input provenance.

Optional `start_session`/`end_session` bounds name exact, included return sessions
in the ledger. Absent, reversed or initial-mark bounds are refused rather than
clipped; omitted bounds use all ledger return sessions. The preceding ledger NAV
is the initial drawdown mark and is not counted as a return. Ledger validation
already refuses missing/nonfinite returns and duplicate or out-of-order sessions;
the analysis neither sorts late rows nor fills gaps. It cannot establish market
publication/availability times from ledger dates. Existing source and causal
simulation checks continue to govern those boundaries.

The locked QPK `performance_metrics.compute_window_metrics` supplies compounding,
initial-mark drawdown and target-downside Sortino. Its population volatility
(`ddof=0`) is multiplied by `sqrt(N/(N-1))` to obtain sample volatility (`ddof=1`).
Sharpe is `(mean(session net return) - rf/252) / sample_std * sqrt(252)`.
Sortino is `(mean(session net return) - MAR/252) / RMS(min(return-MAR/252, 0)) *
sqrt(252)`, with the RMS denominator averaging **all** N sessions, including
zero shortfalls. The QPK call receives MAR as its target rate; rf and MAR need
not be equal. Both ratios retain negative numerators and results. CAGR uses
252/N return observations, and cash remains part of account NAV. Fees and
slippage already in net NAV are never deducted again; interest is only what the
ledger actually records, with no additional accrual.

Fewer than two observations give `None` for sample volatility and both ratios.
Exact constant-return samples give actual zero volatility and `None` for both
ratios, even if their MAR shortfall is nonzero. A nonconstant sample with no
target downside gives `None` for Sortino. Each ratio has an explicit reason
status, so a computed zero remains distinct from undefined. DSR, PBO and effective
independent sample size remain uncomputed/unknown: this function has no trial
matrix or established dependence assumptions.

The returned `synthetic_account_metrics_v1` contract is report-only and explicitly
research/synthetic, with `promotion_eligible=false`, `live_ready=false`,
`size_zero_required=true`, and `no_order=true`. It is not a promotion evidence
package; the pinned validator still requires its complete, finite risk metrics
and all other evidence and acceptance. The function writes nothing and never
simulates, selects or changes a candidate. The frozen v1 profile, parameters,
`evaluation_contract`, selection, journal result fields and old artifacts keep
their prior meaning, including uncomputed v1 Sharpe/Sortino. Source fingerprint
changes still require a new journal namespace; existing identities are not
rewritten. SOXL's existing Sharpe/selection also remain unchanged.

Hand-calculated synthetic checks distinguish rf from MAR, sample from population
variance, full-sample downside from negative-only variance, signed losses, zero
and constant samples, insufficient observations, exact window bounds and initial
drawdown. The existing fee/slippage simulation and journal are checked for net
cost accounting and immutable stored output during the independent analysis.

## Opt-in consumers of existing research outputs

Two independent report entrypoints now consume the same metric function. They
do not add metrics to the frozen optimizer or journal result, change selection,
replay simulations, or write evidence.

`report_journaled_tqqq_core_metrics(*, store, trial_id,
annual_risk_free_rate, annual_minimum_acceptable_return,
start_session=None, end_session=None)` reads one existing synthetic SMA trial
from an explicit local store. The pinned store must verify its SUCCEEDED
result/ledger/terminal triplet. Missing or corrupted readback is refused;
STARTED, FAILED, REJECTED and ABORTED trials are refused with their status.
They remain in the original audit collection, with all failure reasons and
never-started slots preserved. This single-trial report does not summarize
the study or select only successes into a new trial population.

`soxl_core_optimization.report_soxl_core_candidate_metrics(source, points, *,
window_days, scenario, synthetic, calendar_id, periods_per_year,
annual_risk_free_rate, annual_minimum_acceptable_return,
start_session=None, end_session=None)` consumes an existing static-SMA
`simulate_candidate` tuple of `DailyPoint`, not a volatility-scaling or RSI
variant. It validates the source's existing shape/canonical-byte contract and
each point's dates, opening/closing NAV, quantities, adverse-fill cashflow,
commission and slippage. It constructs a synthetic typed ledger with the
existing USD 100,000 initial cash, source-close marks and commission-only fees;
slippage is already in trade cashflow. It then calls the same metric function.
It does not rerun the simulator or certify that caller-supplied points were
produced by the declared SMA window. The window/scenario are declared inputs,
not execution or data-provenance attestations.

SOXL requires explicit `synthetic=True`, `calendar_id="XNYS"` and
`periods_per_year=252`. An unknown calendar, real-data classification or another
basis is refused. Synthetic source dates and this declaration do not prove
real XNYS sessions, missing market sessions, PIT availability or licensed data.
Both consumers preserve all supplied rows in their validation; they do not sort
late rows, fill missing prices, or treat a window bound as a provenance cutoff.
The C3 ledger's `synthetic_declared_sessions` is currently refused; combination
support needs an explicit later contract, without relabeling those dates XNYS.

Each report retains all fields from `synthetic_account_metrics_v1`, including
the explicit rf/MAR, ddof=1, 252-session, initial-NAV and net-cost definitions
above. It adds `report_profile`, `source_candidate` and `scope`; the TQQQ reader
also adds `source_trial_status`. Report profiles are
`tqqq_core_trial_metric_report_v1` and `soxl_core_candidate_metric_report_v1`.
`strategy_profile`/`trial_id`/`run_id`/`input_id` continue to identify the ledger
being analyzed, so a new report never overwrites its source identity. The
reports remain research/synthetic, report-only and ineligible for promotion,
live use or sizing. DSR/PBO remain `NOT_COMPUTED`, and effective independent
sample size remains `NOT_ESTIMATED`; a single path supplies no full trial matrix
or justified dependence/multiple-testing assumptions.

Consumer regression checks exercise actual simulator points and stored journal
outputs, including identical generated bars/costs across SOXL and TQQQ. They
check signed net returns, cash/zero days, hand-calculated rf/MAR ratios, initial
drawdown, fees once, later-return invariance, invalid/duplicate/late rows,
undefined ratios and unchanged source results/files. R3 joint evidence, R8
promotion runner and C3 comparison still use their existing metric contracts;
this stage does not claim their adoption or real financial qualification.

## Acceptance boundary

Synthetic mechanism checks do not verify a production durable location, retention, permissions, deployed environment, qualified data, or a real research run. Those require a separately approved location/input/execution context and readback acceptance. This source change performs no cloud-bucket write, deployment, market/provider/model/broker access or real-data replay.
