# B1/B2 D03–D08 parameter proposal (PROPOSED, pending owner approval)

Status: **PROPOSED / NOT_FROZEN / DEVELOPMENT_ONLY**. Nothing here fills the D-item fields in
`b1-b2-freeze-decision-contract.zh-CN.md`; those stay `UNSPECIFIED` / `NOT_ATTESTED` until the owner
approves them. `live_ready=False`, `no_order=True`, `budget_effect=NONE`. Values come from the freeze
contract, the original audit guidance (quarter Kelly by default, never above 0.5, and budgets may only
be lowered or capped) and common literature conventions. None are fitted to real data.
Scope is the RS-04 SOXL/TQQQ research core (3x leveraged ETFs).

| D-item | Parameter | Proposed value | Sensitivity (report-only) | Rationale |
|---|---|---|---|---|
| D03 | B1 rows | `fixed_50_subject_to_original_cap`, `kelly_c_0.25_once` (primary), `kelly_c_0.5_once`, `ewma_vol_target_and_kelly_cap` | none | Contract row list; quarter Kelly is the audit default |
| D03 | Kelly multiple `c` | 0.25 primary; 0.5 as ceiling/sensitivity only | 0.5 | Audit: `c <= 0.5`; estimation error in `f*` makes full Kelly unsafe |
| D03 | `c` in the vol-target row | 0.25 | 0.5 | Same as primary; applied once |
| D01/D03 | Exposure cap | `min(original_scope_cap, 1.0)`; if the original cap is not attested the row is `UNCOMPUTABLE` | none | No margin on top of 3x ETFs; budgets never raised |
| D04 | B2 base sizing | `kelly_c_0.25_once` output | none | One shared base; no B1×B2 cartesian product |
| D04 | B2 rows | `original_ladder` vs `continuous_drawdown` | `normalized_cushion` not registered | Contract first-comparison list |
| D06 | Target vol | 20% annualized (≈1.26% daily, `0.20/sqrt(252)`) | 30% annualized | Close to a broad equity index; far below 3x ETF realized vol, so it de-risks rather than levers |
| D06 | EWMA form | zero-mean `v_next = λ v + (1-λ) r²`, daily simple returns | none | RiskMetrics convention |
| D06 | λ (decay) | 0.94 | 0.97 | RiskMetrics daily standard; ~17-day effective memory |
| D06 | Initial variance | sample variance of the first 63 sessions of the training prefix only | none | Causal; no look-ahead; no invented constant |
| D06 | Warmup | 63 sessions, exposure 0 (PARKED) during warmup | none | About one quarter, ~3.7x the effective EWMA memory |
| D06 | Update time | forecast for session t uses returns through t-1 | none | Causal prefix (D09 fill timing decided separately) |
| D06 | Missing/non-finite input | PARK at 0, never switch to HAR | none | Contract rule |
| D07 | HAR | PARKED | none | No licensed intraday RV |
| D08 | `D_max` | `min(original_scope_D_max, 0.20)` | 0.15 (tighter only) | Contract: never wider than the original scope |
| D08 | Ladder thresholds | full until 10% drawdown, half until 20%, zero beyond | none | Matches `D_max`; simple, auditable |
| D08 | Peak rule | net investment-index peak including initial NAV; never reset on loss; external cash does not clear drawdown | none | Contract rule |
| D05 | Return basis | daily net log growth vs same-frequency cash; initial NAV 1.0 | none | Primary endpoint per D13 proposal (not frozen here) |

## Explicitly not proposed
Fees and slippage (D10), PIT, source and license (D11), holdout boundaries (D12), CVaR budgets (D14) and the
reviewer binding (D16) are owner-only and are left empty here on purpose.

## Approval
The owner must accept or edit each row. Only after that may a separate PR copy the values into the freeze
contract and register trials in the journal.
