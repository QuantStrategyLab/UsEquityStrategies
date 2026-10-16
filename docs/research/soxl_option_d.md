# SOXL Option D offline state contract

This module is explicitly called research code. No production entrypoint imports it, no platform dependency pin changes, and no broker interface is accepted. Output fractions apply only to simulated **new risk budgets**; they never multiply existing target holdings. All states retain `no_order=true`, `execution_authorized=false`, and the Schwab PARKED constraint. Published SOXL / securities / cash caps remain 0.679 / 0.97 / 0.03; the capital mandate remains unchanged. S2 does not authorize live trading.

## Settled definitions (owner, 2026-10-16)

Signed session loss is `E0 + deposits - withdrawals - E_t`. Keep the sign for loss/non-loss classification. The limit is `max(20, min(E0 * .05, 100))` and equality breaches. An intraday breach remains a loss session even if the close recovered.

`E_halt` is equity at the **soft-halt trigger instant**, and `cluster_loss` is a **positive loss amount**. G6 is `E_t >= E_halt + .5 * cluster_loss`; the former minus sign and `OR E_t >= E_halt` are superseded. For 1700 / 300, the boundary is 1850. In the implementation, recovery equity subtracts verified cumulative external flows since the trigger, so deposits cannot manufacture recovery. Cumulative flows must share one fixed ledger origin.

`HaltEvidence` supplies the trigger equity, positive loss, cluster interval and frozen source hash. The state machine never guesses the loss cluster from the three-day counter. It freezes the reference at the trigger; S1 loss/daily-breach fallback requires fresh trigger facts. Missing reference facts fail G6 and cannot recover. This is intentional: a losing streak alone does not establish a dollar loss cluster.

## Clock and evaluation

Call `freeze_xnys_calendar(start, end)` once for the whole research chain. This explicit helper lazily uses the research environment's `pandas_market_calendars` package. Source version and every regular open/close (including half days and DST) enter the frozen calendar hash. This is calendar-library evidence, not an exchange-certified calendar attestation. Do not build the schedule from observed price dates.

`advance_option_d` is a pure function over the binding, prior QPK transition and one completed session. It requires every frozen-calendar session in sequence. Represent a missing observation with `SessionFacts(session=...)`; do not omit it or forward-fill equity. One transition per session preserves QPK's strictly increasing session contract. There is no intraday persistence or broker polling.

Daily-only and restorable-cap latches clear only with explicit next-regular-open `open_data_ok` / `open_caps_ok` facts and no hard trigger. The session-end result records `daily_cleared_at_open` separately. A close observation alone does not prove the open check passed. New breaches at that day's close relatch. A consecutive/data soft latch cannot use this shortcut.

C=2 complete following XNYS sessions and K=2 consecutive qualified non-loss sessions are independent of the losing-streak reset. All G1–G6 are required for S0→S1. S1 is 50% simulated new-risk budget; at least one **additional** qualified non-loss session with G3–G5 is required for S2. S1 loss/breach restarts S0 and the recovery clock. Unknown data never counts as non-loss; three consecutive missing-data sessions while soft-latched escalate to HARD. HARD has no automatic clear input or branch.

Caps facts concern all published hard caps / cash floor. Unrestorable caps, integrity crisis, manual emergency stop, and any proposed unpark / mandate / guard violation hard-latch. Restorable caps block new simulated risk until the next qualified open or the consecutive recovery gates. Unknown financial facts mark `INCONCLUSIVE`; zero budget marks qualified halted sessions `GUARD_HALTED`.

## Identity and storage

QPK `StrategyRiskStateIdentity` keeps strategy / logical account / candidate / config identities. Policy and overlay hashes remain distinct state fields; calendar and full session-fact bytes are hashed. Changing any binding within a chain is rejected. The existing QPK `StrategyRiskStateStore` persists these transitions create-only; no new state storage framework, runtime gate or environment variable is introduced. Explicit callers must use a new identity for independent research chains and retain the same frozen calendar on restart.

Rollback: revert the new module, tests and this document by a normal new commit, or stop explicitly calling the research function. Preserve existing research evidence, hard-latch history and all independent platform protections. No candidate JSON bytes are rewritten.

## Default-OFF replay

`research.soxl_option_d_replay.replay_option_d(baseline_report, ...)` defaults `option_d_research_replay_enabled=False` and returns the same baseline object without reading observations. Explicit enable adds an `option_d_replay` appendix. Existing holdings and `combined_scale` stay unchanged. `requested_new_risk_usd` is an optional simulated new-risk budget; S0/HARD multiply it by zero, S1 by .5, and S2 by 1. This never forms order quantities. No broker, executor, client or cloud-store argument is accepted, and any mode other than `RESEARCH_ONLY` is rejected.

The opt-in consumer expands absent calendar rows into missing facts, including a missing leading prefix. Set `through_session` to include trailing missing sessions; without it evaluation ends at the last supplied observation. Duplicate, unordered and non-session rows are rejected. Passing `existing_transitions` validates the QPK chain and reproduces the entire frozen input prefix before accepting a restart. Returned QPK transitions can be appended to the existing create-only store by an explicit local research caller; the replay itself writes no storage.

For local files, run:

```bash
# OFF: copies the original bytes, even preserving whitespace.
PYTHONPATH=src python scripts/run_soxl_option_d_replay.py --baseline-report baseline.json

# Explicit offline opt-in; these are frozen local artifacts, not runtime config.
PYTHONPATH=src python scripts/run_soxl_option_d_replay.py \
  --baseline-report baseline.json --option-d-research-replay-enabled \
  --input input.json --config config.json --policy policy.json --overlay overlay.md
```

The manifest contains `identity` (QPK fields), `calendar_start`, `calendar_end`, and ordered `observations` (SessionFacts objects, with optional HaltEvidence). Optional fields are `through_session` and `requested_new_risk_usd`. Other fields are rejected. `config_sha256` must match the config bytes; policy/overlay bytes are independently hashed without rewriting them. The enabled CLI needs the research environment's `pandas_market_calendars`; the default OFF CLI needs only Python and the baseline file. This explicit script is not registered in the production entrypoint/catalog and updates no environment settings.

Replay rollback: leave the enable parameter false, or revert the replay module/script/tests and this documentation extension by a normal commit. Preserve existing evidence and all independent runtime controls.
