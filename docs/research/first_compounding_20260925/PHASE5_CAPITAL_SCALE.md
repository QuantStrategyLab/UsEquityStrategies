# Phase 5: Fixed-policy capital-scale development study

## Frozen scope

`phase5_capital_scale_policy.v1.json` adds a distinct scale-study identity over the reviewed Phase 4 v3 candidate. The only experimental input changed is initial research principal. The four declared points are $1,000, $1,250, $10,000 and $100,000; they are examples, not user account parameters or recommended allocations. The pre-existing $10,000 ledgers and summary are reused by hash. The other points must run the same prior-close guard core, QQQM/TQQQ/BOXX targets, next-open whole-share execution, funding order, event recognition, two-session sale settlement, and 5/10/15 bps per-side cost assumptions on the same 444-session seen-development window.

The two small points straddle an approximately $1,124 initial-NAV one-TQQQ-share threshold, computed from the 2023-03-27 known TQQQ close and the unchanged first-signal core target of 45% of a 5% member budget. The next open, fees and later guard states can move the actual activation threshold. Neither the points nor the threshold use later outcomes. A separate large round point tests whether integer shares become less dominant. No interpolation or grid search is authorized.

`A_init` is the explicit research principal. `N_t` is the actual recursively computed economic NAV; each decision uses the Phase 4 v3 decision-equity rule and funds only from available cash. `W_ref` and `capital_curve_policy` are not applied to this fixed-budget pair, so the experiment cannot claim a capital-curve effect. No new portfolio drawdown limit, fixed commission, margin, shorting or initial locked holding is introduced. The frozen cost contract has zero fixed commission; real minimum-order or per-ticket fees are an identified evidence gap, not silently assumed away. Initial state is cash only. Previously completed Phase 2 direct tests cover locked and unsettled funding behavior, but this historical scale experiment does not validate an account with locked starting positions.

## Review and execution gate

Before any new scale replay, Astra must review whether varying only the initial principal while reusing the frozen v3 policy preserves the research identity, mathematical comparison and funding/known-at semantics. The new runner must report actual TQQQ purchase days and target-versus-filled share gaps, not infer activation from a positive budget. All returns and risk metrics must come from each independently evolved account path; the $10,000 existing ledger is referenced rather than recomputed. The study remains development and cannot select a best size or a personal capital rule.

## Review and completed replay

Astra reviewed policy SHA256 `1321d29984780bd7643fcf1000ccad8934c0b20300fffaac717f2dbf80fb5058` at commit `4a5618d668acd65284f762e787b1fa543686d64c` before the new replay and returned GO for this bounded capital-scale slice. It found no required policy change; it required actual purchase/holding and target-fill reporting. The review did not certify a personal size, the private input values, or the later runner implementation.

`phase5_capital_scale_compare.py` ran the three new principal points as independent cash-only accounts. It reused the six $10,000 Phase 4 v3 ledgers by SHA256 and produced 18 new private daily ledgers (three principal points × two paths × three cost rates). The common 444 trading sessions are 2023-03-28 through 2024-12-31, with the same earlier core warmup. The private summary SHA256 is `f9345361e8b5aed249b4d2039d67ac00b2bc8eeb2c590a9a0f4c587f41edbfef`. It and the 18 new ledgers remain under the approved private research root in `phase5_capital_scale_v1/`; only aggregate evidence belongs in Git. Every new ledger hash, 444-session coverage, daily account identity and NAV component sum were verified. Existing $10,000 source summary and all six source ledger hashes were checked before reuse.

The table shows the 10 bps per-side case. Dollar drawdown is the peak-to-trough loss associated with maximum percentage drawdown; recovery is peak to full recovery in trading sessions. Security exposure is actual QQQM, TQQQ and BOXX value divided by account NAV, including BOXX as a security; Nasdaq lookthrough is `(QQQM value + 3 × TQQQ value) / NAV`, a nominal daily leverage proxy, not a guarantee of multi-day leverage. Cash, unsettled sales and receivables are excluded from security exposure.

| Initial research principal | Path | Cumulative | CAGR | Max drawdown | Drawdown amount | Recovery sessions | Costs | Mean security exposure | Mean Nasdaq lookthrough | TQQQ held sessions |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| $1,000 | enhanced | 28.94% | 15.52% | −3.98% | $43.89 | 82 | $4.11 | 81.48% | 36.80% | 0 |
| $1,000 | matched defense | 30.27% | 16.19% | −6.43% | $82.08 | 67 | $1.10 | 89.05% | 44.40% | 0 |
| $1,250 | enhanced | 28.43% | 15.26% | −5.19% | $81.75 | 67 | $3.60 | 84.45% | 39.46% | 32 |
| $1,250 | matched defense | 31.26% | 16.69% | −6.10% | $97.93 | 64 | $4.48 | 89.80% | 44.24% | 0 |
| $10,000 | enhanced | 34.34% | 18.24% | −6.20% | $815.61 | 67 | $40.17 | 94.45% | 49.39% | 400 |
| $10,000 | matched defense | 35.14% | 18.64% | −6.66% | $879.67 | 67 | $30.46 | 97.61% | 49.11% | 0 |
| $100,000 | enhanced | 34.89% | 18.52% | −6.32% | $8,361.17 | 67 | $363.01 | 95.52% | 50.52% | 400 |
| $100,000 | matched defense | 35.58% | 18.86% | −6.78% | $8,985.54 | 67 | $275.92 | 98.59% | 49.77% | 0 |

Across all 5/10/15 bps cases, the $1,000 enhanced path held zero TQQQ shares despite a positive TQQQ target on 330 sessions. The $1,250 path first bought TQQQ on 2023-03-28 but held it on only 32 sessions and had 300 positive-target/zero-share sessions. The $10,000 and $100,000 enhanced paths both held TQQQ on 400 sessions. The private summary records actual target-share shortfalls and whole-share rounding gaps for every path. At all four principal points and all three cost rates, the enhanced path had lower cumulative return and smaller percentage drawdown than the matched-defense baseline. The low-capital reduction in drawdown coincided with materially lower actual security and Nasdaq exposure; it is not proof of a better risk-adjusted policy. The $1,000 enhanced cumulative return at 10 bps exceeds its 5 bps result because whole-share execution changes along the recursively diverging paths; it is not a monotonic fee response or an economic benefit of higher fees.

## Interpretation boundary

This slice establishes that positive member budget need not activate TQQQ and that changing initial principal changes actual fills, cash, cost, exposure and outcomes even with unchanged percentages. It does not establish an optimal principal, a superior enhanced candidate, a real-account recommendation, or a capital-curve effect. The same seen development period was used for all points. No nonzero fixed commission, account-specific order minimum, locked initial holdings, external cash flows or applied capital-curve policy was tested. Dividend availability still uses provider process date as a delayed-recognition research proxy, not verified historical announcement time. BOXX remains a cash-management ETF, not short-term Treasury cash. The original complete v2 remains unvalidated.

## Read-only capital-curve binding diagnostic

`phase5_curve_binding_diagnostic.v1.json` freezes a *descriptive overlay* on the 12 existing enhanced ledgers. It uses the previously recorded S4 curve parameters (`a0=$10,000`, `r_min=0.2`, `r_max=0.8`, `k=1`) and the existing `smooth_bounded_capital_risk_ratio` function. `M={TQQQ}` covers only the TQQQ **member budget**, including its internal cash; QQQM, BOXX and outer cash are outside this diagnostic set. This is not a change to the Phase 4/5 candidate, a second curve implementation, an allocation decision or a new historical return path.

The first draft was held by Astra because retrospectively accrued economic NAV can include a dividend claim that was not yet identifiable at the signal. The revised policy SHA256 `10f0d2b5b56269713d33c98a0ac9f48d74d80751a547b7c8a1f63b2553355a93` uses `W_ref,t = max(A_init, decision equity observed through the current signal close)` under the existing delayed-recognition proxy. Astra then returned GO for that narrow time-causality correction. `N_t` is the recorded decision equity at the signal, excluding still-unrecognized claims. The reference amount `W_ref,t × r_t` is not spendable; the hypothetical current cap is `N_t × r_t`. The recorded budget remains `0.05 × N_t` and is compared with the cap without changing any trade.

At 10 bps per side, the first signal and final signal values are:

| Principal | First `W_ref` | First `N_t` | First `r_t` | First `N_t × r_t` | Recorded TQQQ budget | Final `W_ref` | Final `N_t` | Final `r_t` | Minimum cap less budget / `N_t` | Binding sessions |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| $1,000 | $1,000 | $1,000 | 74.55% | $745.45 | $50 | $1,309.74 | $1,292.71 | 73.05% | 68.05% | 0 |
| $1,250 | $1,250 | $1,250 | 73.33% | $916.67 | $62.50 | $1,635.94 | $1,610.31 | 71.56% | 66.56% | 0 |
| $10,000 | $10,000 | $10,000 | 50.00% | $5,000 | $500 | $13,770.14 | $13,486.62 | 45.24% | 40.24% | 0 |
| $100,000 | $100,000 | $100,000 | 25.45% | $25,454.55 | $5,000 | $138,356.82 | $135,446.38 | 24.04% | 19.04% | 0 |

All 12 paths at 5/10/15 bps had zero binding sessions; the smallest cap-minus-budget gap was 19.037% of decision equity. This also follows from the frozen 5% member budget and the curve's 20% lower bound. The existing ledgers separately show actual post-execution settled cash, restricted paid cash, unsettled sale proceeds, receivables, member-reserved cash at the open and actual security exposure; the private diagnostic summary reports their means per path. Those amounts have different timestamps and roles, and none is replaced by `N_t × r_t`. For the $10,000/10 bps path, mean post-execution settled cash was $591.61, restricted paid cash $0.19, unsettled proceeds $69.33, receivables $0.63, member reserve at the open $369.49, and actual security value 94.45% of NAV. These are descriptive account aggregates, not one simultaneous spendable balance.

The read-only diagnostic wrote only its aggregate summary under the approved private root, SHA256 `54fdd0014f33be9b805be3e117656660606e5853e1d5e17c41b052c40ad9c225`; its 12 input ledgers were hash checked. This proves that the reproduced curve would *not* have constrained the recorded 5% TQQQ budget in this window. It does not test a larger member budget, identify a useful curve parameter, prove the S4 automatic policy effective, or permit the curve to be applied to the candidate. The dividend known-at assumption remains conditional on provider process date.

## Frozen fixed-ticket-fee sensitivity gate

`phase5_fixed_ticket_fee_policy.v1.json` declares a separate research-only cost sensitivity, not a replacement for the Phase 4 v3 or Phase 5 scale candidate. It reuses the previously declared $1,000/$1,250 threshold pair and $10,000 reference at 10 bps per side. The only new assumption is a synthetic $1 fixed charge per nonzero executed symbol-side ticket; it is not a claimed broker schedule. The fee must be funded at execution, so each account requires a new daily recursion, with the frozen zero-fixed-fee paths reused by hash as comparators. This policy is frozen before any new fee run and requires Astra review of ticket counting, settled funding, account identity and preserved candidate scope. No fee grid or capital point will be selected from results.

### Completed fee sensitivity

The frozen fee policy SHA256 is `dc94e426bfe0e48a59465898ab3dac2b8c32a7b0537c92fd5742bf85aba20809`. Astra returned GO before replay and, after inspecting the isolated execution change and aggregate evidence, GO for the fee/funding accounting. `phase5_fixed_ticket_fee_compare.py` leaves the frozen Phase 4 builder, signal timing, dividend recognition, account state and performance calculations intact. It substitutes only a serial, scoped execution callback for this independent sensitivity and restores the original callback afterward. A nonzero buy or sale of one symbol at an open pays one fixed ticket fee plus 10 bps of executed notional; zero fill pays nothing. Sell costs reduce proceeds awaiting two-session settlement. Buy size is recomputed as the maximum affordable whole-share fill after all applicable cash restrictions and both charges.

Six independent cash-only paths ran for the same 444 development sessions, and each was compared with the SHA-bound zero-fixed-fee path at the same principal. The new private summary SHA256 is `56a40c9ab0e63f087b416340e031e4c20892290c5adb53feedfd935c0c64ef5b`, retained under the approved private root in `phase5_fixed_ticket_fee_v1/`. All six new ledger hashes, dates, daily account identities, NAV components and nonnegative cash/pending proceeds were checked. The table reports total trading cost, including both bps and ticket fees, on the new path. Recovery is peak to full recovery in trading sessions; actual security exposure includes QQQM, TQQQ and BOXX value divided by NAV.

| Principal | Path | Cumulative, $1 fee / $0 fee | CAGR with $1 fee | Max drawdown, $1 fee / $0 fee | Drawdown amount with $1 fee | Recovery | Total cost, $1 fee / $0 fee | $1-fee tickets | Actual security exposure | Nasdaq lookthrough | TQQQ held sessions |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| $1,000 | enhanced | 25.95% / 28.94% | 13.99% | −4.14% / −3.98% | $51.13 | 64 | $16.67 / $4.11 | 14 | 81.83% | 36.66% | 0 |
| $1,000 | matched defense | 29.76% / 30.27% | 15.94% | −6.45% / −6.43% | $82.08 | 67 | $6.10 / $1.10 | 5 | 89.34% | 44.56% | 0 |
| $1,250 | enhanced | 26.19% / 28.43% | 14.12% | −5.30% / −5.19% | $82.63 | 67 | $36.97 / $3.60 | 32 | 83.97% | 39.42% | 31 |
| $1,250 | matched defense | 30.58% / 31.26% | 16.35% | −6.09% / −6.10% | $97.21 | 64 | $26.23 / $4.48 | 22 | 89.63% | 44.07% | 0 |
| $10,000 | enhanced | 31.66% / 34.34% | 16.89% | −6.23% / −6.20% | $806.32 | 78 | $256.56 / $40.17 | 219 | 94.32% | 49.28% | 400 |
| $10,000 | matched defense | 33.41% / 35.14% | 17.78% | −6.69% / −6.66% | $874.69 | 67 | $209.67 / $30.46 | 177 | 97.66% | 49.16% | 0 |

All six $1-fee paths had lower cumulative returns than their same-principal $0-fee controls. The effect is not calculated by subtracting `$1 ×` the old ticket count from old NAV: fees changed affordable share counts and subsequent states. For example, the $1,250 enhanced path held TQQQ on 31 sessions rather than 32 and executed 32 symbol-side tickets rather than the zero-fee path's 24. The $1,000 enhanced path still never bought TQQQ. Percentage drawdowns did not improve consistently, so this sensitivity offers no adoption argument. These outcomes are conditional on a hypothetical flat fee; they are not a real broker-cost estimate, personal account advice, or evidence outside the seen development window.
