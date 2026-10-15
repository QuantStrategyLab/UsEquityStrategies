# SOXL de-risk-only: 20-session forward observation preregistration

Owner decision: **2026-10-15 22:58 Asia/Singapore** (14:58 UTC).
Registration: `soxl-derisk-only-202610-forward-20-xnys-20261016-v1`.
Status: **PREREGISTERED / COLLECTION_NOT_ACTIVATED**; financial status
**INCONCLUSIVE**. This record is not an executable configuration.

## Frozen identity and parameters

| Item | Frozen value |
| --- | --- |
| Candidate A | `soxl-derisk-only-202610` |
| Engineering alias | `soxl_v7_strict_derisk_only_v1` from [UES #604](https://github.com/QuantStrategyLab/UsEquityStrategies/pull/604) |
| Candidate code | [`0f48d49c8b066b03a47b54c9a6a1d538241eb394`](https://github.com/QuantStrategyLab/UsEquityStrategies/commit/0f48d49c8b066b03a47b54c9a6a1d538241eb394); never a moving branch |
| Materialized candidate parameter file SHA-256 | `fbfba885215aaf19d40f7e94f1401a7aeb5876b744ba410bcd13d73bacf1da10` |
| Public [parameter binding](soxl_derisk_only_202610_forward_parameters.json) byte SHA-256 | `dc092e734c204857d22a18246373f57a6fd5f52fbe96edb99d2e2935e222d2b5` |
| Approved research baseline config byte SHA-256 | `8f4aed33bdbcdcd96d8fb563c6af6f73ab91f57c8bff4091aab7eee7fdacaa70` |
| Approved research risk-policy byte SHA-256 | `5077c0ff9e1ca58c1f08654ed12d84de7c4ecf8920b8c7f3481a2e1344c4fc29` |
| Adopted policy overlay, including Option D, byte SHA-256 | `c81910666830f183398f99f1e3d397d98f585dc80ff3251d17f9f3be4dc316c0` |
| QPK dependency | `f6f2079f6b53d3cbb3b72bdcc5c2476ed8f170f1` |
| QSP research dependency, if consumed | `3416da47580be1537183e378f3ca9efacc6f9b8c` |

The materialized file is the exact result of the frozen code's
`variant_config(approved_config["parameters"]["values"])`, serialized as specified
in the public binding. Its bytes are retained privately; no private config or
policy payload is published. A collector must verify those bytes and the approved
inputs independently in the approved cloud. Local preparation is not evidence of
a cloud upload, installed runtime, or completed observation.

The binding preserves the baseline SOXX allocation, income layer, native market
protection, entry rules and trade filters. Only #604's reviewed reductions apply:
SOXL full/mid at most 35%/25%, RSI/Bollinger reductions, volatility exit without
retention, BOXX/cash routing, and at least 3% cash. The volatility threshold seed
is 0.55 with the frozen rolling-percentile rule; the prior signal-side 0.75
conflict remains unresolved evidence, not an alternative to select during this
window. Existing hard caps remain SOXL 0.679 / all securities 0.97 / cash 0.03,
with stricter inherited limits retained. These are research assertions, not live
position or target changes.

## Comparator and interpretation

The requested comparator B is the **live SOXL baseline**
`soxl_soxx_trend_income`, with the same inputs, valuation clock, capital/flows
and costs as A. The available owner-approved pair above is
`soxl-research-config-rebuild-20261015`, classified
**REBUILT_RESEARCH_BASELINE / REBUILT_FROM_PARTIAL_NON_CANONICAL**. It is not
proof of the actual merged live configuration or historical production policy.
Any comparison using it must say `B_REBUILT`; it cannot be relabeled `B_LIVE`.

The actual live baseline's complete effective config, policy, installed versions
and plugin files/order still need immutable, approved cloud references. Until
bound before the first session, live equivalence is **NOT_COMPARABLE**. Preserve
any subsequent naturally occurring baseline changes as separate evidence;
never silently replace the frozen comparator. Plugin-off or incomplete-layer
results do not establish a full-live comparison. S is the SOXX-only reference
with the same 3% reserve, needed for the approved 2x-cost discipline.

Historical backtests ending **2026-08-25 are REFERENCE ONLY / CONTAMINATED**.
The prior 252-session historical "holdout" proposal is superseded for untouched
claims. Prior use is documented by the R9 [#533](https://github.com/QuantStrategyLab/UsEquityStrategies/pull/533)
research history; the owner accepted the contamination finding before this
registration. A new candidate name/hash does not restore historical OOS status.

## Fixed future window

**Start: 2026-10-16. End: 2026-11-12. Both endpoints inclusive: 20 sessions.**
October 16 is session 1, not a day before a further 20 sessions.
Calendar: `exchange-calendars==4.13.2`, `XNYS`, `America/New_York`;
cross-checked against the [NYSE official calendar](https://www.nyse.com/trade/hours-calendars).
November 11 is an NYSE session. The expected session list is:

```text
2026-10-16  2026-10-19  2026-10-20  2026-10-21  2026-10-22
2026-10-23  2026-10-26  2026-10-27  2026-10-28  2026-10-29
2026-10-30  2026-11-02  2026-11-03  2026-11-04  2026-11-05
2026-11-06  2026-11-09  2026-11-10  2026-11-11  2026-11-12
```

Reproduce with calendar metadata only:

```python
import exchange_calendars as xc

sessions = xc.get_calendar("XNYS").sessions_in_range("2026-10-16", "2026-12-01")[:20]
assert len(sessions) == 20
assert str(sessions[-1].date()) == "2026-11-12"
```

First regular open is `2026-10-16T09:30:00-04:00`; final regular close is
`2026-11-12T16:00:00-05:00`. NYSE closes shift from 20:00 UTC to 21:00 UTC on
November 2 because of DST. Session evidence uses the actual exchange close and
the separately frozen provider-finality cutoff, not a fixed UTC weekday timer.
Paired return accounting needs the preceding October 15 close valuation.
The frozen research clock uses completed t-close information and next-session
close simulated fills; adjusted signal prices are not executable quotes.

Missing collection, late startup or an unexpected closure stays visible against
these fixed dates. Do not backfill availability timestamps, drop missing days,
extend the end date or silently slide the window. An incomplete window remains
incomplete. Twenty days is an engineering observation, not the required
12-calendar-month independent OOS, V7's original 252 valid XNYS sessions, or a
promotion decision. Old V7 counts do not transfer to this candidate.

## Metrics and approved gate semantics

Report A/B/S separately at 1x and 2x adverse external costs and paired A-minus-B
differences. Costs feed back through each independent self-financing book; 2x
does not double favorable income. The prior plan's cost coefficients and initial
capital were proposals, not approvals; freeze those and initial books/flows
before any result, or mark affected financial comparisons NOT_COMPARABLE.

Use a unitized net wealth path including its initial NAV. Primary geometric
growth is `g = sum(log(1+r)) / T`, where T is elapsed calendar years
(`elapsed_days/365.25`); `CAGR = exp(g)-1`. Also report mean session log growth
and its 252-session annualization separately. Calmar is CAGR / positive MDD
loss magnitude on the same path; zero MDD makes the ratio UNCOMPUTABLE.

| Metric or gate | Frozen treatment |
| --- | --- |
| Geometric growth | Approved primary research objective; observe this one frozen candidate, with no frontier/grid search or reselection during the window. Relative floor `L_g = development delta_g - epsilon_g` remains NOT_ESTIMATED. |
| Calmar | Always reported with g/CAGR and paired differences; a twin metric, not a replacement objective or independent tuning switch. |
| MDD | Positive peak-to-trough loss from initial NAV. Approved same-cost envelope `U_D1 = development delta_MDD + epsilon_D1` remains NOT_ESTIMATED and subordinate to absolute policy. |
| 2x-cost SOXX discipline | Paired upper bound of `MDD(A,2x)-MDD(S,2x)` must be <= 0; calibrated `U_D2 = min(0, development delta + epsilon_D2)` remains NOT_ESTIMATED. An interval crossing zero is INCONCLUSIVE. |
| Worst month | Worst complete calendar-month compounded return, with partial months separately labeled. Neither October nor November is fully covered here: full-month metric is UNCOMPUTABLE, n=0. Rolling 21-session return is also UNCOMPUTABLE with only 20 sessions. |
| CVaR | Loss `L=-r`, exact upper probability mass at daily/weekly q=95%/99%; monthly q=95% only if full-month observations exist. Fractional boundary weights, counts and tail mass must be reported; do not floor negative loss to zero. Twenty daily returns give only 1 / 0.2 observations of 95% / 99% tail mass. |
| Tail / worst-month gates | At both costs, simultaneous upper bounds of loss differences vs B must be <= their frozen `U_j,c = min(0, development delta_j,c + epsilon_j,c)`. Numeric envelopes remain NOT_ESTIMATED; weak evidence is not non-inferiority. |
| Cost sensitivity | `K_g=(g_A,1x-g_A,2x)-(g_B,1x-g_B,2x)`; approved `U_cost=development K_g+epsilon_cost` remains NOT_ESTIMATED. |
| Full-active-path feasibility | Retain positive net growth at 2x cost with its interval and no original risk-budget breach. Missing policy or inadequate samples remain INCONCLUSIVE; 20 sessions cannot establish full-study eligibility. |
| Kelly / half-Kelly | Diagnostic only, with SOFR availability, estimation uncertainty and sample count; no gate, sizing instruction, plugin switch or trading authority. Missing inputs yield UNCOMPUTABLE. |

The owner-approved calibration method uses paired stationary-block bootstrap,
10,000 replicates, seed 20261015, 95% simultaneous coverage, train-only block
length and `epsilon=max(epsilon_stat,epsilon_drift)`. Numeric epsilon, block
length, `L_g` and envelopes have not been estimated/frozen on qualified data.
**They cannot be fitted using this window or filled during it.** Financial
acceptance stays INCONCLUSIVE. The superseded 20% MDD / 25% CAGR placeholders
are not scoring criteria. Applicable UBPS full-study screens and original V7
requirements retain their own longer evidence requirements.

Engineering reporting includes all 20 expected sessions, valid/missing counts,
input hashes and event/published/fetched/available timestamps, revisions,
same-input determinism, explained candidate/baseline differences, conservation
of cash/shares, cap breaches and missing reasons. Candidate economic differences
are not replay implementation errors. Unapproved matching tolerances cannot
produce a parity PASS; no hash-only receipt establishes return tracking.

## Approved-cloud evidence and collection follow-up

Data source: the approved **Alpaca SIP raw bars / corporate actions cloud
pipeline**, with adjustment basis declared separately, and existing **NY Fed
SOFR** with decision-time availability/revisions. The previous pilot stops at
2026-08-25; it does not supply future sessions. No Board-rate substitute fill,
paid feed, new broker query or Mac/Grok-host market-data download is authorized.
Raw market, account, holdings, decision and fill tapes stay in approved private
GCS and restricted GitHub-hosted runner temporary storage. Public logs/artifacts
and local summaries contain only sanitized dates, counts, hashes and statuses.

No schedule, cloud job, dispatch or collector is activated by this PR.
Actually collecting the observations still requires a separate bounded follow-up:

1. Bind approved private cloud URI + generation + byte SHA-256 for the frozen
   parameters/config/policy/overlay, genuine live comparator and plugin inputs,
   installed code/dependencies and request manifest. Verify licenses and hashes
   inside the approved cloud. Do not fabricate missing canonical evidence.
2. Freeze initial research books/flows, warmup, cost coefficients, provider
   finality/revision rules, parity tolerances and execution budget before the
   first observation. Missing financial inputs may be recorded as gaps but
   cannot be supplied later as if preregistered.
3. Implement/reuse a read-only collector with an approved future-data path.
   The #604 replay is synthetic-only and its cloud qualification remains PARKED;
   do not feed it real bars or bypass that boundary. Use existing approved cloud
   access only; any unavailable permission stops the affected operation.
4. Supply configurable private references such as `FORWARD_REQUEST_MANIFEST_URI`,
   `FORWARD_REQUEST_MANIFEST_GENERATION`, `FORWARD_REQUEST_MANIFEST_SHA256`,
   `FORWARD_INPUT_PREFIX` and `FORWARD_EVIDENCE_PREFIX` in that future collector.
   These are documentation placeholders, not new CLI or workflow inputs. Store
   raw evidence create-only with generation/hash receipts and the approved
   90-day research retention; no lifecycle/IAM change is part of this PR.
5. Separately authorize any daily schedule after the actual XNYS close and source
   finality cutoff, with DST handling, bounded budget and missing-day reporting.
   Retain paired inputs/decisions and hypothetical ledgers privately; any actual
   fills must come from already-retained natural production records and remain
   distinguished from hypothetical execution. Review the final engineering
   report manually. No automatic acceptance or release follows.

**Nothing may be tuned during the window:** candidate parameters, code,
comparator definition, plugin set/order, price basis, clocks, capital/flows,
costs, metrics, tolerances, gates, missing-data policy and date boundaries are
fixed. Preserve failures and all revisions. A necessary correction gets a new
registration and a separately approved prospective interval; it cannot rescue
or rewrite this window. Daily diagnostics are permitted only for collection
health, without adaptive strategy or gate changes.

Operating permission remains **OBSERVATION_ONLY / no_order=true /
size_zero_required=true / live_ready=false**. No orders, paper/shadow trading
enablement, live config, position/target change, capital increase, Schwab unpark,
secret/IAM operation or deployment is authorized here.
