# Phase 1: Research Candidate Adapter Boundary

## Existing contracts and the bounded addition

UES catalog definitions already identify the production strategy profile, entrypoint, target mode, and coarse required inputs. QPK `StrategyDecision` already carries position targets, budget intents, risk flags, and diagnostics. The independent TQQQ/QQQ guard study has a separate frozen candidate contract and a daily result with member budget, TQQQ target, guard route, and core state. QPK candidate-control and research-ledger records bind research evidence but do not authorize execution.

`portfolio_candidate_adapter.py` projects those existing outputs into a research-only member view. It does not run a builder, estimate returns, choose a budget, change a target, or convert a reference asset into an independent strategy. The QQQM and BOXX buy-and-hold adapters produce reference identities with `portfolio_member=False`. The BOXX outer-settlement policy remains an account-level funding policy, not a second TQQQ member or a cash asset.

| Identity | Adapter source | Capital and target unit | Historical evidence status |
| --- | --- | --- | --- |
| Full `tqqq_growth_income` candidate | UES catalog and its unchanged QPK `StrategyDecision` | Supplied member budget; catalog value targets | Original full v2 history not validated; no historical ranking eligibility |
| Full `soxl_soxx_trend_income` candidate | UES catalog and its unchanged QPK `StrategyDecision` | Supplied member budget; catalog value targets | Original full v2 history not validated; no historical ranking eligibility |
| `tqqq_qqq_guard_cash_research_v1` | Frozen independent core's daily decision | Supplied member budget and TQQQ value target; QQQ is signal only | Development evidence only; not original full v2 |
| QQQM and BOXX buy-and-hold | Existing study reference identities | Reference assets, not member budgets | Development benchmarks only |
| `tqqq_qqq_guard_outer_boxx_settlement_research_v1` | Existing four-branch outer funding study | Account-level outer cash/BOXX allocation after member reservation | Development policy evidence only |

The adapter carries observed state and risk flags from the source decision. Its `required_inputs` for catalog profiles are the catalog's coarse declarations; the full v2 external-state and corporate-action conditions remain separate, stricter research prerequisites. Missing capital minimum, capacity, cost identity, or qualified joint history is not replaced with zero or a fabricated risk score. `cost_model_id` is an explicit optional binding; absent values must be resolved before cost-based portfolio comparison. An adapter result is not a historical return series, portfolio allocation, promotion approval, or order authorization.

## Next integration gate

A portfolio decision may consume only a frozen candidate identity, dated source decision, explicit budget and cost contract, and matching joint evidence. Phase 2 must check actual builder affordability and account settlement after choosing budgets. The existing `auto_allocate.py` and `smooth_bounded_capital_risk_ratio` remain the allocation and capital-curve entrances. Extending the capital-curve member set, changing the outer BOXX ownership, or qualifying a full v2 historical series requires a separately frozen policy/evidence decision; this adapter does none of those things.
