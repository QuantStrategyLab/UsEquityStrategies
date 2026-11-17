# B1/B2 Freeze Decision Contract Draft

English | [简体中文](b1-b2-freeze-decision-contract.zh-CN.md)

Status: **DRAFT / NOT_FROZEN / DEVELOPMENT_ONLY**. This document turns existing proposals into an item-by-item decision sheet and records owner-specified parameters for D03-D08; it authorizes no trials, private data fetches, ledger writes, or live risk-gate changes. Tracked in [QRS #632](https://github.com/QuantStrategyLab/QuantRuntimeSettings/issues/632), [QPK #659](https://github.com/QuantStrategyLab/QuantPlatformKit/issues/659), and [UES #588](https://github.com/QuantStrategyLab/UsEquityStrategies/issues/588). See [the B4 standalone draft](bayesian-kelly-preregistration-proposal.md) for B4 and [the CVaR decision contract](cvar-exposure-constraint-decision-contract.md) for tail constraints; B5 is excluded from this batch.

## Boundary Between Original Audit and Existing Engineering

The 12-page original "Quant System Compounding Mathematics and Engineering Audit Report" is on hand (SHA-256 `f60ce29f267722245cf62e308883e11b001f38efc3ee2de9d6c861806acb1bfb`). Page 4 proposes continuous drawdown/cushion scaling, p5 requires comparing HAR-RV against EWMA, pp9-10 require A2/A3 before B1/B2 and freezing preregistrations before running calculations, and pp6/p10 require counting new research in cumulative trials, capping/reducing budgets only, and limiting Kelly multipliers to <= 0.5. Illustrative returns, the earlier 48-trial count claim, and then-UNKNOWN GitHub states in the report are not input proof here.

Reading baselines are UES `<pinned-commit-sha>` and QPK `<pinned-commit-sha>`. A2/A3/B3 primitives and UES `#590`'s default-off legacy CLI consumer passed PR and `main` CI; that verifies engineering contracts only, not B1/B2 execution, complete real-fee accounting, or strategy edge. Unfrozen parameters from [QPK's original B1/B2 proposal](https://github.com/QuantStrategyLab/QuantPlatformKit/blob/<pinned-commit-sha>/docs/b1_b2_preregistration_proposal.zh-CN.md) remain preserved.

The active project order is measurement suitability and complete freeze facts -> **B1/B2 reliable benchmark -> independent B4 study -> E1**. The original PDF labeled B4 "wave 2" without spelling out that full extra dependency; this section records the subsequently authorized execution order. C2/candidate enablement and real-data execution require separate admission not granted here.

## First Verification Path Selected

Under user delegation to select the first research path, [issue #588](https://github.com/QuantStrategyLab/UsEquityStrategies/issues/588#issuecomment-<recorded-run-or-artifact-id>) selects the existing **RS-04 SOXL/TQQQ research core**. This closes only the first-path choice; it does not freeze single-strategy vs. portfolio scope, profiles/variants, parameters, caps, runners, or new data licenses, nor does it establish live plugin parity. Three existing contracts stay listed below for applicability comparison.

Current engineering source is UES [PR #593](https://github.com/QuantStrategyLab/UsEquityStrategies/pull/593) (merge `<pinned-commit-sha>`): the terminal close-fill cost measurement entrypoint is complete while legacy defaults, outputs, ledgers, and production pins remain unchanged. Engineering completion does not prove runner adoption or full cash/net-position accounting.

| Existing Path | Reusable Inputs and Behavior | Applicability Bindings Needed Before B1/B2 |
|---|---|---|
| [RS-04 core generator](../../src/us_equity_strategies/research/kelly_core_return_generator.py): `generate_soxl_core_path` / `generate_tqqq_core_path`, `CorePath` | Ordered sessions, gross returns, legacy turnover, per-asset returns/weights; raw bars + corporate actions; explicit `close_fill_turnover` and `measure_close_fill_costs(CostModel)` | First verification path selected; multi-asset core != full plugin/live parity. Legacy turnover shifts to next earning session; new entrypoint charges actual closes including terminal fill as a `no_weight_feedback` NAV overlay only. Still must bind variant, cash/corporate actions, inner/outer fees, and actual runner adoption; past RS results are never rewritten |
| [A2 `replay_targets`](../../src/us_equity_strategies/research/execution_model_adapter.py) | Caller-frozen completed-close targets, single security, zero-return cash, synthetic or consistently adjusted OHLC; same-session gross/net and true fill turnover | Generates no signals; lacks raw corporate actions, BOXX cash, multi-asset drift, and full plugins. Costs are an independent NAV overlay with no feedback into gross target weights |
| [Batch A legacy report CLI](../../scripts/run_c3_batch_a_existing_member_baseline.py), [v2 contracts](batch-a-v2-contracts.md) | Typed SMA200 control members, source re-binding, net ledger, and `#590` optional 12-item report-only diagnostics | Not the full RS-04 core path; `ASSUMED_ZERO_USD_CASH`, seen-development source, unattested license/PIT/OOS. Cost drag and DSR remain explicitly uncomputable when same-path gross or full trials are missing |

Parameters from the legacy [RS-04 frozen preregistration](fractional-kelly-preregistration.md) (252/63/5, 5/10/15 bps, `next_close`, BOXX, variant permissions, historical manifest) serve as reference only and do not transfer automatically to B1/B2.

## Decision Log and Status

Each freeze entry binds decision, scope, version, source digest, owner/independent reviewer scope, dependencies, and validity/halt duties. Only the first path is marked `PATH_SELECTED`; undecided values stay `UNSPECIFIED / NOT_FROZEN`, and external proof stays `NOT_ATTESTED`. Missing objects get no fabricated hashes, private values stay in protected storage, and filling tables or passing CI never flips status to `FROZEN`.

| ID | Items to Decide and Bind | Current Status |
|---|---|---|
| D01 | Strategy key/profile/variant, single vs. portfolio scope, core-only vs. full-plugin boundary, original config and scope risk/cash cap sources | First path RS-04 SOXL/TQQQ research core: `PATH_SELECTED`; full scope/profile/variant/params unfrozen, cap sources `NOT_ATTESTED` |
| D02 | Actual consumer/generator immutable source, runner, lockfile/installed deps; input/output applicability and A2 diff | Core generator first path: `PATH_SELECTED`; `#593` measurement source bindable; actual runner/installed deps/full applicability `UNSPECIFIED / NOT_ATTESTED` |
| D03 | B1 candidate IDs and roles: fixed 50%, quarter/half Kelly, EWMA, HAR, vol-target + Kelly cap; parameters and rejection rules | **OWNER_APPROVED 2026-10-14**: B1 registers exactly four rows (`fixed_50_subject_to_original_cap`, `kelly_c_0.25_once` primary, `kelly_c_0.5_once` comparison, `ewma_vol_target_and_kelly_cap` with `c=0.25`); `c` applied once from `{0.25, 0.5}` then min with original cap; `c>0.5` rejected; no standalone vol-target row; HAR not registered (see D07) |
| D04 | B2 fixed base sizing and independent comparison rows: original ladder, continuous drawdown, normalized cushion; no B1 x B2 Cartesian product | **OWNER_APPROVED 2026-10-14**: B2 base sizing = `kelly_c_0.25_once` output; compares `original_ladder` vs `continuous_drawdown` only; `normalized_cushion` excluded; no B1 x B2 Cartesian product |
| D05 | Same-frequency net/excess returns, cash/FX, calendar/annualization, initial NAV, full test sessions; asset benchmarks and mismatch/drop handling | **OWNER_APPROVED 2026-10-14 (partial)**: primary endpoint is same-frequency daily net log growth against same-frequency cash; initial NAV=1.0. Asset benchmark, mismatch/drop handling, and FX stay `UNSPECIFIED`; fees in D10 (`UNSPECIFIED`) |
| D06 | `target_vol` unit/horizon, zero-mean EWMA or alternative, decay, initial variance, warmup, update timing, missing-value rule | **OWNER_APPROVED 2026-10-14**: annualized `target_vol` 20% (daily `0.20/sqrt(252)`); zero-mean EWMA `v_next=lambda*v+(1-lambda)*r^2` on daily simple returns, `lambda=0.94`; initial variance = sample variance of first 63 training sessions; 63-session warmup at exposure 0 (`PARKED`); session `t` forecast uses `t-1` and earlier only; missing/non-finite -> `PARKED` at 0 without HAR fallback. Report-only sensitivity: `target_vol` 30%, `lambda=0.97` |
| D07 | True intraday HAR RV source/license, sampling/timezone/session/cleaning/availability, D/W/M windows, fit/refit/warmup, failure rules | **OWNER_APPROVED 2026-10-14: PARKED**. Without licensed intraday RV, HAR is neither registered nor run |
| D08 | B2 scope `D_max`, ladder thresholds, hard gates; net NAV peak persistence, cash-flow adjustment, cost feedback, restart/recovery/park rules | **OWNER_APPROVED 2026-10-14**: `D_max = min(scope, 0.20) = 0.20` (scope lacked drawdown cap, `UNSPECIFIED`); ladder: drawdown <=10% -> 1.0, <=20% -> 0.5, >20% -> 0; net unit-NAV peak includes initial NAV, never resets on loss, and external flows do not erase drawdown. Scope caps below; cost feedback and restart/recovery remain `UNSPECIFIED` |
| D09 | Signal availability cutoff, primary vs. sensitivity roles of `next_close`/`next_open`, corporate actions, terminal unexecuted fills, actual fee timing | `UNSPECIFIED` |
| D10 | Commissions, spread, slippage, financing, inner/outer cost and cash attribution; cost tiers as report scenarios vs. selection trials without double-charging | `UNSPECIFIED` |
| D11 | Real source/license/PIT/coverage, raw/split/total-return basis, corporate-action and cash availability timing, manifest identity | `UNSPECIFIED / NOT_ATTESTED` |
| D12 | Train/test/purge/embargo, warmup and uncovered tails, untouched holdout boundary/digest, isolation custodian, unseen-data attestation | `UNSPECIFIED / NOT_ATTESTED` |
| D13 | Net log-growth primary endpoint and paired difference; metric report/ranking/veto roles, thresholds, bootstrap/PBO/PSR/DSR/MinTRL assumptions, stop rules | `UNSPECIFIED` |
| D14 | Separation of descriptive CVaR from CVaR constraints: tail-budget source/unit/window, cash/cost homogeneity, missing coverage, B1 consumption point | `UNSPECIFIED / NOT_ATTESTED` |
| D15 | Trial unit per candidate/model/param/cost role/sensitivity/repeat; failed/rejected/interrupted logging, full cumulative journal, Sharpe variance / missing-Sharpe rules | `UNSPECIFIED / NOT_ATTESTED` |
| D16 | Independent binding of research owner/reviewer, preregistration and real-data approvals, validity window, output paths, stop/re-review authority | `NOT_ATTESTED` |

### B1/B2 Approved Values and Sources (2026-10-14, Owner-Approved)

Effective value = `min(original scope value, proposed value)`. Research limits follow IBKR runtime risk limits.

| Item | Original Scope Value | Proposal | Effective Value | Source |
|---|---|---|---|---|
| Total nominal exposure cap (SOXL path) | 0.97 | 1.00 | **0.97** | IBKR `soxl_soxx_trend_income` `runtime_risk_limits.total_nominal_exposure_cap`; `reserved_cash_ratio` 0.03 |
| Total nominal exposure cap (TQQQ path) | 0.98 (`cash_reserve_ratio` 0.02) | 1.00 | **0.98** | UES `manifests/__init__.py` `tqqq_growth_income` default config (`ca599d5`) |
| SOXL single-symbol nominal cap | 0.679 | 1.00 | **0.679** | IBKR `runtime_risk_limits.nominal_caps.SOXL` |
| `D_max` | `UNSPECIFIED` | 0.20 | **0.20** | Original scope had no drawdown limit |
| Kelly multiplier `c` | <= 0.5 (audit) | 0.25 primary / 0.5 comparison | **0.25 / 0.5** | Audit pp6/p10 |

Source: IBKR repository variable `CLOUD_RUN_SERVICE_TARGETS_JSON` (updated `2026-09-25T15:30:24Z`), inspected read-only on owner side, matching the 2026-10-10 snapshot. No account identifiers appear here.

Still `UNSPECIFIED / NOT_ATTESTED`: fees/slippage (D10), data source/license/PIT (D11), holdout (D12), CVaR (D14), trial journal (D15), and reviewer binding (D16). This approval grants no real-data execution, pin changes, or budget increases.

## Draft Comparison Forms (Not Runnable Directly)

B1 uses D06's zero-mean EWMA `v_next = lambda*v_current + (1-lambda)*r_t^2`, `lambda`, initial variance, and warmup; session `t` uses `t-1` and earlier data only. HAR stays `PARKED` per D07 and is never substituted from squared daily returns or silently swapped in.

B1's combined vol-target and Kelly row is registered in D03 as `ewma_vol_target_and_kelly_cap` (`c=0.25`), computing `min(existing_cap, clip(target_vol/forecast_vol, 0, 1), clip(c*f_star, 0, 1))` over the same horizon. `c` is at most 0.5 and applied once (`0.25` primary, `0.5` comparison only; `0.5` is the Kelly multiplier ceiling, not a uniform 50% exposure cap). The fixed 50% benchmark is also clipped by the same scope cap and cannot raise existing budgets.

B2 keeps the original proposal forms: net wealth `W > 0`, peak `M >= W` including initial NAV, `d = 1 - W/M`, `alpha = 1 - D_max`; D04 registers only `original_ladder` and `continuous_drawdown` `clip(1 - d/D_max, 0, 1)`, keeping `normalized_cushion` `clip((W - alpha*M)/(D_max*W), 0, 1)` as an unscheduled reference form only. `D_max` is `0.20` per D08 and never relaxes stricter scope caps. Under cash flows, D08 uses the net unit-investment index and its peak rather than raw dollars `W`; post-cost peaks await frozen decision cutoffs. `M` never resets on losses; gap risk and discrete costs can breach the floor, so no hard drawdown guarantee is claimed.

All candidates pass through existing hard gates, cash reserves, product caps, breakers, and vetoes first; future outputs stay `DEVELOPMENT_ONLY` / `live_ready=False` / `no_order=True` / `budget_effect=NONE` as cap/reduction diagnostics only. Missing CVaR budgets or forecast failures never waive existing constraints; cash disposition and recovery require explicit D06/D08/D14 bindings or research stays parked.

## Three Real-Fact Packages Required Before Freezing

1. **Scope and source/limit package**: concrete D01/D02/D05/D07/D09-D11/D14 candidate, actual inputs/license/PIT, risk/cash/tail budgets, and runner source. Report examples and legacy licenses do not substitute.
2. **Holdout package**: D12 boundary/digest, isolation custodian, unseen-data proof, and revision/re-freeze rules. Previously seen development data cannot be relabeled OOS.
3. **Journal and accountability package**: D15/D16 complete cumulative journal including all failures, independent integrity review, same-frequency Sharpe definitions, and owner/reviewer sign-offs. The earlier 7-row 48-trial summary is not a verified complete set, and missing Sharpe values are never zero-filled.

Parameter choices must also be filled; the three packages do not pick them automatically. Only metrics and data actually consumed by a candidate are required. Once all three packages are complete and independently verified against committed revisions, the protocol can be marked `FROZEN`; revisions register new identities without mutating historical ledger rows.

## Future Synthetic Acceptance and Stop Conditions

The table below remains pre-freeze verification design; no `D` row is `FROZEN`. Seeds, tolerances, and collection commands bind only engineering fixtures—not real fee schedules, risk caps, PIT, holdouts, or trial journals.

| Acceptance Area | Contract to Prove |
|---|---|
| Causal forecasting | Perturbing future bars/RV/late corporate actions leaves prior forecasts, weights, and net returns identical; observation/availability/signal/fill timestamps stay distinct |
| Volatility & missing data | Hand-checked recursion, matching units/frequency, warmup and zero/negative/non-finite handling; failures park cleanly without switching HAR/EWMA or producing unbounded exposure |
| Drawdown state | Boundary/monotonicity of both continuous functions, exact ladder thresholds, cash flows not erasing investment losses, checkpoint recovery/idempotence, and peak persistence |
| Execution & net costs | Explicit diffs across the three timings on identical inputs; entry/exit/terminal close fees, missing next fill, cash/corporate actions, cost feedback, and same-path gross/net reconciliation |
| Prior-cap precedence | Fractional multiplier applied once and <= 0.5; fixed 50% clipped by scope cap; breaker/veto and missing CVaR never relaxed |
| Measurement | Same sessions including zero observations and initial NAV; B3 raw-net `ddof=1` separated from legacy annualized fields; null when gross/full trials missing; period diagnostics != per-trade win rate |
| Trial integrity | Preregistration precedes computation; every attempt logged upfront with failed/rejected/interrupted rows retained; no cherry-picking or overwriting failed rows |

Identity drift, unauthorized inputs, lookahead leakage, quantity/cost/peak reconciliation failures, relaxed scope caps, out-of-scope candidates, or missing trial records halt research while preserving evidence. Halts isolate research artifacts only without lifting production breakers or modifying pins, budgets, or candidates. Passing engineering/research checks still leaves E1 dual operational and statistical acceptance separate.

## RS-04 Cash, Execution, and Cost Roles

Status matches this document: **DRAFT / NOT_FROZEN / DEVELOPMENT_ONLY**. This describes current UES code boundaries, not live parity or a full self-financing net ledger. Production pins stay unchanged; `live_ready=False`, `no_order=True`, `budget_effect=NONE`.

| Role | Current Contract | Explicitly Not |
|---|---|---|
| Inner residual cash | Unallocated weight remainder inside `_replay` earns 0 | Not BOXX and not outer Kelly's cash leg |
| Inner BOXX | Tradable asset inside core path; buys/sells of BOXX contribute to inner turnover | Not exempt from inner fees despite its name |
| Outer cash leg | Evaluator treats total strategy gross return as one virtual asset and multiplies `(1-f)` by caller cash series; private runner supplies BOXX total return here fee-free | Does not replace inner residual cash or net against inner BOXX |
| A2 cash | Single-security remainder in `replay_targets`, fixed at 0 return | Not BOXX, financing, or multi-asset drift |
| `legacy_turnover` | Close turnover shifted to next earning session; consumed by private runner and legacy cost stress | Drops terminal fill without a next session; cannot be added to the two columns below |
| `close_fill_turnover` | True close one-sided security turnover / pre-fill NAV, including terminal fill; used only by `measure_close_fill_costs` (`charge_at=end`, `no_weight_feedback`) | Does not alter gross weights, decisions, or reference equity; not per-security cross-layer netting |
| Outer traded notional | `abs(outer_weight_delta) + outer_weight * inner_legacy_turnover` passed to QPK `cost_stress_recompute` | Does not use `close_fill_turnover` or net against inner fills |
| A2 fill turnover | Single-security actual fill-day turnover; `legacy_turnover` kept only for diffing | Must never double-charge both columns |

Corporate actions enter core-path total returns via `(close_t * split_ratio + dividend) / close_{t-1} - 1`; A2 rejects `raw_unadjusted`. All three timings (`same_close`, `next_close`, `next_open`) must be reported separately on identical inputs; `same_close` remains a post-close signal using same-close fill assumption. Observation, availability, signal, and fill phases stay explicit (completed-close observation and post-close availability share session `t`, while `next_close` fills at `t+1`—distinct from the live 15:45 clock).

## Runner Diffs

| Interface | Actually Consumes | Not Consumed / Not Proven |
|---|---|---|
| `kelly_private_runner.run_private` | In-window `gross_returns`, shifted `turnover`, outer BOXX cash series, legacy fee tiers | Does not call `measure_close_fill_costs`; outputs omit `close_fill_turnover`; unexecuted here |
| Close-fill measurement | Explicitly injected QPK `CostModel` | Not wired into default wires, path summaries, or private runner |
| A2 `replay_targets` | Caller-frozen single-security targets | Does not generate RS-04 signals or handle raw corporate actions/BOXX |
| Outer `evaluate_fractional_kelly_walk_forward` | Strategy gross stream, caller cash series, optional inner legacy turnover | Does not read close-fill net returns or net outer vs. inner trades |

Having the measurement hook does not mean runners consume it, and core-only paths do not establish live parity (`runner_adoption=NOT_ATTESTED`).

## Approved Parameters and Trial Units (Not Frozen)

Status: **OWNER_APPROVED / NOT_FROZEN**. Parameter choices follow D03-D08 and the table above, while the full preregistration remains unfrozen. `RECOMMENDED_PROPOSAL` in `research/b1_b2_rs04_preparation.py` preserves synthetic prep defaults rather than machine-binding these approved values. Legacy RS-04 parameters (252/63/5, 5/10/15 bps, legacy manifest, seen development window) do not transfer here.

| Item | Approved Choice | Open Bindings and Stop Rules |
|---|---|---|
| B1 rows (exact set) | `fixed_50_subject_to_original_cap`, `kelly_c_0.25_once`, `kelly_c_0.5_once`, `ewma_vol_target_and_kelly_cap` | No standalone vol-target row without separate D03 approval; no Cartesian product with B2 |
| HAR-RV | `PARKED` | Squared daily returns must never be relabeled RV or auto-swapped to EWMA |
| B2 first comparison (not crossed with all B1) | `original_ladder` and `continuous_drawdown` sharing `kelly_c_0.25_once` output from D04 as base sizing | `normalized_cushion` excluded from this trial set; ladder 10%/20% and `D_max=0.20` approved in D08; synthetic bounds != live gates, runner applicability still required |
| Primary timing | `next_close` | `next_open` and `same_close` are separate sensitivity trials; `same_close` is never primary |
| EWMA form | `v_next = lambda*v_current + (1-lambda)*r_t^2`, per-observation simple return squared (unannualized) | D06 sets annualized `target_vol` 20%, `lambda=0.94`, 63-session sample variance init, 63-session warmup, `t-1` cutoff; missing finite positive init or warmup parks at 0 exposure |
| `c` | `0.25` primary, `0.5` comparison only; applied once from `{0.25, 0.5}` then min with original cap | `c > 0.5` rejected; fixed 50% also clipped first by original cap; scope caps/targets from tables above, missing cap -> null exposure (never fill 1) |
| Cost tiers | `REPORT_SCENARIO_NOT_SELECTION_TRIAL` until D10 is attested; never multiplies selection trial count | Fee schedule `NOT_ATTESTED`; any selection by fee tier turns that tier into a selection trial; legacy 5/10/15 bps not imported as this scope's fee schedule |
| Trial unit | One attempt = RS-04 research-core path + candidate row + param binding + cost role + timing role + attempt seq | `COMPUTED`, `PARKED`, `REJECTED`, `INTERRUPTED` all counted; register before compute; never keep winners only or delete/overwrite failures |
| PIT / holdout / journal | No default recommendations | All three `NOT_ATTESTED`; halt without boundaries, unseen proof, or cumulative journal; never invent hashes, relabel development as holdout, or zero-fill missing Sharpe |

Cost roles are not second economic trials: legacy, close-fill, and outer traded notional sit side-by-side for one candidate and must not be summed. Security-level cross-layer netting still lacks a QPK interface.

## Synthetic Counterexamples Bound in the Reference Implementation

Module: `src/us_equity_strategies/research/b1_b2_rs04_preparation.py`; tests: `tests/research/test_b1_b2_rs04_synthetic_acceptance.py`.

| Binding | Value | Not |
|---|---|---|
| Fixture seed | `20261014` | Not a frozen RNG seed for real research; unused `20261013` in QPK draft stays unadopted |
| Absolute tolerance | `1e-12` | Never loosened to mask reconciliation errors |
| Cost & cap fixtures | Explicit synthetic test values | `NOT_A_FEE_SCHEDULE` / `NOT_A_RISK_CAP`; never copied into D10 or D08 |

Coverage: future returns and late corporate actions leave prefix gross returns, weights, and close-fill untouched; hand-checked EWMA recursion and parking on missing/non-finite/zero init without HAR fallback; continuous drawdown/cushion boundaries and monotonicity, exact ladder steps, cash flows not erasing drawdown, checkpoint peak persistence; three timings, terminal close fill, missing next fill, no fee feedback into gross weights; single fractional application `<= 0.5`, fixed 50% cap clipping, breaker/veto/missing CVaR never relaxing caps; null measurement on missing gross/trials, B3 `ddof=1` vs. legacy annualized fields, period diagnostics vs. per-trade win rate; upfront attempt logging with failure retention.

Collection command:

```bash
PYTHONPATH=src python -m pytest -q tests/research/test_b1_b2_rs04_synthetic_acceptance.py
```

When main CI uses the production pin lacking A2/B3 overlays, the two overlay execution/QPK-null cases skip while the remaining counterexamples run; `research-execution-integration` and `research-unified-net-consumer` enforce those two cases on their respective overlays. Skipping or passing never marks this document `FROZEN`.

## Cross-Repo QPK Gaps

UES implementation covers UES interfaces only. Symbols below reflect inspected overlay interfaces; missing contracts are never faked locally in UES, and production pin `<pinned-commit-sha>` is unaffected by overlay SHAs.

| Existing Symbol | Inspected Overlay | Missing Contract |
|---|---|---|
| `CostModel.net_return(gross, security_turnover, charge_at)` | `<pinned-commit-sha>` | No inner/outer cash roles, multi-asset cash balances, per-security netting, or financing |
| `FillModel.resolve(signal_index, available_index, bar_count)` | Same as above | No cash balance, BOXX, or multi-asset targets |
| `cost_stress_recompute(gross, traded, ...)` | `<pinned-commit-sha>` | Accepts aggregate traded notional only; cannot net outer `f` against inner security weights |
| `build_unified_net_report` | `<pinned-commit-sha>` | Cost drag `UNCOMPUTABLE` when gross missing, DSR `UNCOMPUTABLE` when trials missing; no RS-04 role parameters; caller-supplied digest is not ledger proof |

`docs/research/kelly-security-level-ledger-design.zh-CN.md` remains a design note. Until QPK ships an admitted security-level net-trading interface, RS-04 cash/cost roles stay side-by-side diffs as above and research stops at `QPK_INTERFACE_ABSENT` without summing the three turnover columns.

## Common Cap for Offline Comparisons (2026-10-16)

The synthetic harness's B1 and B2 paths now share an explicit `ScopeLimits` (`total_gross_cap`, `cash_reserve`, and other existing exposures); product caps reuse B1's `existing_cap` so B2 cannot bypass it. Both tables cap targets at `min(existing_cap, total_gross_cap - other_gross_exposure, 1 - cash_reserve - other_gross_exposure)`. B2 clips base exposure to that cap before multiplying by the drawdown factor. If any limit is missing, both tables return `UNCOMPUTABLE` instead of defaulting to 1 or reporting cash-backtest performance; if other exposures already exceed the budget, execution halts.

These are synthetic per-decision constraints; price drift, gaps, and fees still move subsequent weights and make no live intraday guarantee. Real account limit sources stay `NOT_ATTESTED`, and this module never reads or modifies production limits. Regression fixture values `.679`/`.97`/`.03` reproduce reported gaps only and are not universal defaults. Full fill, net-cost, and input-freeze rules belong in the N6 specification; gross tables never qualify as net CAGR/Calmar promotion results.
