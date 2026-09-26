# First Compounding Research: Phase 0 Scope

## Scope and identity

This change publishes offline research code and synthetic verification on top of the UES full-parameter research draft. It does not merge or revise the original SOXL/TQQQ v2 candidates. The independent TQQQ/QQQ guard candidate, the S4 member-budget comparison, and the BOXX outer-capital policy retain separate identities. QQQM and BOXX buy-and-hold paths are reference assets, not additional strategy builders. The 31% lower fixed-budget path is a post hoc exposure attribution, not a selected policy.

The existing `smooth_bounded_capital_risk_ratio` function and the `capital_curve_policy` / `explicit_reference_v1` input remain the capital-curve implementation. Member budget, actual security market value, member internal cash, outer settled cash, unsettled sale proceeds, dividend receivables, and reference wealth have distinct meanings. No new capital-curve formula or default personal allocation is introduced.

## Implementation and execution

- `auto_allocate.py` provides the bounded two-member research budget search and the existing single-member S4 estimator. The latter uses 60 completed one-day scenarios, an observed high-water reference, and the frozen grid and loss constraint; it does not guarantee future drawdown.
- `small_account_demo.py` exercises both existing full-parameter builders with artificial dated inputs, funded transfers, integer shares, continuous position state, and QPK research-ledger readback. Its scenario and sale-policy JSON files are synthetic research inputs.
- `tqqq_qqq_guard_cash_research.py` runs the independent TQQQ candidate from source-checked QQQ/TQQQ bars and corporate actions; QQQ is a signal, not a simulated holding. `tqqq_cash_budget_compare.py` and `s4_exposure_attribution.py` retain the fixed-budget, automatic-budget, and post hoc lower-budget comparisons.
- `boxx_outer_cash_compare.py` and `boxx_outer_cash_attribution.py` compare the four fixed/automatic × cash/BOXX outer policies and the QQQM/BOXX buy-and-hold references. TQQQ's builder is unchanged. Sales enter a declared two-session unsettled queue; BOXX purchases can use only settled outer funds after the full member budget is reserved. The automatic BOXX branch evaluates paired past TQQQ/BOXX scenarios and has an independent outer-policy identity.
- `local_member_replay_input.py` and `optimized_strategy_replay.py` provide the bounded fixture and continuous replay changes required by the small-account research path. The QPK ledger contract remains a separate dependency in draft PR #637.

All real raw prices, corporate-action pages, policy files, and daily ledgers remain in the previously approved private research location. The CLI research entries require that private bundle and verify its frozen manifest, object hashes, and candidate/policy identities before replay. No raw private input, private path, credentials, or daily holdings are published in this change.

## Development evidence and limitations

The independent TQQQ/QQQ guard and cash path was studied on 2023-01-11 through 2024-12-31. The common BOXX comparison starts on 2023-03-28 after 61 shared BOXX price points and ends on 2024-12-31 (444 sessions). In the 5bps research model, the aligned fixed-50%-budget cash and BOXX paths returned 24.04% and 29.51% respectively, with maximum drawdowns of 9.95% and 9.33%. The aligned automatic cash and BOXX paths returned 13.81% and 16.55%, with maximum drawdowns of 6.19% and 5.63%; at 10/15bps the automatic BOXX branch did not retain a net advantage. These are development-period, cost-model-dependent observations. BOXX is an ETF with price and settlement risk, not Treasury cash or a risk-free rate.

Corporate actions are used for retrospective effective-date accounting. The provider's processing date does not certify the historical announcement time. The two-session sale settlement rule is an explicit research assumption, not a statement about every historical broker. Costs do not establish actual executable fills or taxes. These paths do not validate the original full SOXL/TQQQ v2 candidate, SOXL external state, a short-Treasury sleeve, out-of-sample performance, personal allocation, paper/shadow/live admission, deployment, or trading authority.

## Verification and dependency

The direct synthetic tests cover the budget cap/reference distinction, funding and whole-share execution, event/position continuity, private-input identity, and BOXX cash reservation, settlement, and prefix-only scenarios. The private historical summary binds all 18 aligned daily ledgers by SHA-256. Fifteen exports permit independent NAV reconstruction from security values, settled cash, unsettled proceeds, and receivables; the three QQQM buy-and-hold exports omit QQQM position value, so their hashes, dates, NAV, costs, and producer identity are checked without claiming independent component reconciliation. Historical aggregate numbers in this document are not CI fixtures and CI does not read the private input bundle.

This is a stacked draft research change above UES PR #523. The branch now pins its ordinary QPK dependency to the exact, still-unmerged research commit from QPK draft PR #637 and installs a separately pinned QSP research extra in ordinary CI. CI verifies both installed source revisions; this is a draft-branch cross-repository integration test, not adoption by the parent PR, other workflows, or production consumers. QPK #637 remains unmerged. This research PR is not a merge, release, or execution request.
