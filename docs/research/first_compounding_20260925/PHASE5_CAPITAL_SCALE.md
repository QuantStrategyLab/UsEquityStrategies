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

This slice establishes that positive member budget need not activate TQQQ and that changing initial principal changes actual fills, cash, cost, exposure and outcomes even with unchanged percentages. It does not establish an optimal principal, a superior enhanced candidate, a real-account recommendation, or a capital-curve effect. The same seen development period was used for all points. No nonzero fixed commission, account-specific order minimum, locked initial holdings, external cash flows, `W_ref` or capital-curve policy was tested. Dividend availability still uses provider process date as a delayed-recognition research proxy, not verified historical announcement time. BOXX remains a cash-management ETF, not short-term Treasury cash. The original complete v2 remains unvalidated.
