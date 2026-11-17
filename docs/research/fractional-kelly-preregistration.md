# RS-04 Fractional Kelly Evaluator: SOXL / TQQQ Preregistration (v1)

English | [简体中文](fractional-kelly-preregistration.zh-CN.md)

Status: **Parameters frozen; data source and window confirmed by the user on 2026-10-11, restricted to control variant `no_plugin_core` only** (primary variant `live_plugins_as_live` remains blocked by plugins). Code mirror: `us_equity_strategies.research.fractional_kelly_preregistration`.
Only the two current live strategies SOXL and TQQQ were confirmed by the user on 2026-10-11; any other symbol remains `REAL_DATA_REQUIRES_APPROVAL`.
This document and associated registration **did not execute on real data** and contain no real backtest figures.

## 1. Corresponding Live Configurations (Read-Only Check, Unmodified)

| Key | Live Profile | Deployment Location (2026-10-10/11 Read-Only Check) | Live Parameter Source |
| --- | --- | --- | --- |
| SOXL | `soxl_soxx_trend_income` | Schwab live (`RUNTIME_TARGET_JSON`), IBKR `live-u15998061`; LongBridge env vars unreadable in check | UES manifest defaults; platform pin UES `9d1544d1` matches main manifest/strategy code; target JSON has no parameter overrides |
| TQQQ | `tqqq_growth_income` | IBKR `live-u16608560` | Same as above |

Notes:
- **V7** (`soxl_soxx_core_only_p2_v7_longterm_compounding_cash_reserve`, config SHA-256 `843ab4e9...`) is a `runtime_enabled=false` research/shadow candidate, **not** the live SOXL strategy. Core parameters differ (live SOXL uses 0.70 / mid 0.65 / active SOXX 0.20 / de-risk into SOXX vs V7 0.35 / 0.25 / 0.25 / into BOXX; live also enables income layer, market regime control, and option income flags). Existing real-data studies (R6/R7/R8/R9) used V7 and cannot serve as live SOXL return series.
- Live income-layer thresholds: SOXL 150,000 USD, TQQQ 250,000 USD; option overlay `live_status=research_only`, no live option orders.
- Live schedule `45 15 * * 1-5` (US/Eastern) evaluates ~15 minutes before close using partial intraday bars and executes at close. IBKR TQQQ requires the signal day's bar. This differs from research `next_close` and is tracked in UES #575.

## 2. Frozen Parameters (Shared Across Both Strategies Unless Noted)

| Item | Value |
| --- | --- |
| Preregistration ID | SOXL: `rs04-kelly-soxl-v1`; TQQQ: `rs04-kelly-tqqq-v1` |
| Walk-forward plan | 252 training, 63 test, 5 purge sessions; rolling (non-anchored) |
| `c` | `{0.25, 0.5}`; `c >= 1` rejected; exposure in `[0, 1]`, no leverage, no shorting |
| Costs | Base one-way 5 bps on traded notional, stressed at 1x/2x/3x (= 5/10/15 bps) |
| Signal cutoff | `completed_regular_session_close_t` (data known after regular close on day `t`) |
| Execution | `next_close` (day `t+1` close); same-day close allowed only as sensitivity, never primary |
| Calendar | `XNYS` |
| Benchmark | SOXL -> SOXX; TQQQ -> QQQ (`benchmark_symbol` in manifest) |
| Cash leg | BOXX total return from the same data source (dividends accrued on ex-date); if unavailable, update preregistration rather than silently assuming 0 |
| Strategy return paths (two variants) | **Primary `live_plugins_as_live`**: all plugins/overlays match live (see §2.1); **Control `no_plugin_core`**: core rules only, all plugins off (SOXL via `backtest.soxl_trend_simulator.run_soxl_core_only_backtest`; TQQQ needs new builder). Only these two variants allowed; cherry-picking plugins is forbidden |
| Data identity | All seen history is `development`; must not be reported as OOS |
| R8 comparison | R8 v2 is a joint TQQQ+SOXL account policy and cannot be split per strategy; this preregistration omits R8 paths by default (`R8_PATH_NOT_SUPPLIED`) unless a convention is confirmed separately |
| Gates | `NO_BUDGET_INCREASE`, `DEVELOPMENT_ONLY`; SOXL also keeps `SOXL_252_SHADOW_SESSIONS_STILL_REQUIRED` |

### 2.1 Live-Enabled Plugins and Overlays (Read-Only Check)

Sources: UES manifest `default_config` (platform pin UES `9d1544d1`), QRS `platform-config.json`, Schwab repo vars `SCHWAB_STRATEGY_PLUGIN_MOUNTS_JSON` / cash reserve / cash-only vars, and IBKR `CLOUD_RUN_SERVICE_TARGETS_JSON` (`strategy_release` and `runtime_risk_limits`, 2026-10-10 snapshot).

Reproducibility: `YES` = research path reproduces it; `NEEDS_ADAPTER` = deterministic rule lacking a research adapter; `NO` = requires missing or unverified historical inputs; `OFF_IN_LIVE` = configured with zero live position impact, treated as off.

**SOXL (`soxl_soxx_trend_income`)**

| Plugin | Live Status | Reproducible | Notes |
| --- | --- | --- | --- |
| `blend_gate_volatility_delever` | On (rolling quantile, shift to SOXX) | YES | Core rule included in core simulator |
| `blend_gate_rsi_bollinger_caps` | On (dynamic RSI 70, Bollinger cap) | YES | Same as above |
| `market_regime_control` | Configured on (`apply_risk_off=true`, `apply_risk_reduced=false`); Schwab mounts external signal with `expected_mode=shadow`; IBKR mount unverified | **NO** | Position impact depends on daily signal authorization field; no verified historical signal archive for window |
| `volatility_delever_retention` | `mode=environment`, `context_required=true` | **NO** | Reads market regime context; missing history reproduces only `missing_context` (retention 0) |
| `income_layer` | On, threshold 150,000 USD, cap 0.95 | NEEDS_ADAPTER | Depends on account USD NAV; requires dollar-NAV replay, disabled in core simulator |
| `runtime_risk_gate` | IBKR: SOXL 0.679, SOXX 0.873, gross 0.97, reserve 0.03, cash-only; Schwab: reserve 0.03 (min 150 USD), cash-only | NEEDS_ADAPTER | Deterministic caps absent from core simulator; broker parameters differ and one must be chosen |
| `option_income_overlay` (`soxx_put_credit_spread_income_v1`) | `live_status=research_only`, IBKR `options_enabled=false` | OFF_IN_LIVE | No live option orders; no historical option chain |

**TQQQ (`tqqq_growth_income`)**

| Plugin | Live Status | Reproducible | Notes |
| --- | --- | --- | --- |
| `dual_drive_core` (QQQ/TQQQ pullback, MA20 slope) | On | YES | Core rule |
| `dual_drive_volatility_delever` | On (quantile 0.9, window 5) | YES | TACO veto inactive when `retention_mode=environment` |
| `market_regime_control` | Configured on; IBKR mount unverified | **NO** | No verified historical signals |
| `volatility_delever_retention` | `mode=environment`, `context_required=true` | **NO** | Reads market regime context |
| `dual_drive_crisis_defense` | On | **NO** | Requires `true_crisis_active` from market regime/portfolio metadata |
| `income_layer` | On, threshold 250,000 USD, cap 0.55 | NEEDS_ADAPTER | Depends on account NAV |
| `option_growth_overlay` (`tqqq_leaps_growth_v1`) | `live_status=research_only`, `live_gate=promotion_required` | OFF_IN_LIVE | No live option orders |
| `ai_extensions` (`taco_panic_rebound`, `crisis_regime_guard`) | Off | OFF_IN_LIVE | — |

**Conclusion**: While any `NO` or `NEEDS_ADAPTER` entry remains, `live_plugins_as_live` rejects real data with `LIVE_PLUGIN_VARIANT_NOT_REPRODUCIBLE:<plugin>`. Unlocking requires: (a) a user-supplied/confirmed archive of market regime signals, or verifying live signals lacked position-control authority (`shadow`) and confirming zero-impact equivalence; and (b) building dollar-NAV income-layer replay and runtime-risk-gate adapters with a selected broker config. The `no_plugin_core` control variant depends only on data confirmation.

Additionally, IBKR `strategy_release.strategy_revision` is UES `4a394388` (2026-09-17) while platform pin is `9d1544d1`, with diffs in `tqqq_dual_drive_core.py` and `entrypoints/__init__.py` requiring verification of the active revision. Recomputing `strategy_release.config_sha256` (SOXL `0fbd7b3b...`, TQQQ `90e8ded5...`) against manifest defaults does not match, indicating platform-merged overrides that must be read back and pinned before running.

## 3. Required Data Manifest Fields

`RealDataAuthorization(strategy_key, preregistration_id, DataManifest(sha256, source, license, calendar, start_date, end_date, observations), variant)`, where `variant` is `live_plugins_as_live` or `no_plugin_core`:
- `sha256`: 64-char lowercase SHA-256 of the consumed per-session file;
- `source`, `calendar`, `start_date`/`end_date` must match the preregistration **exactly**; `observations` must match input session count;
- `report_context` must carry identical `manifest_sha256`/`source`/`license` plus preregistered signal cutoff and execution timing;
- While `data_source`/`window_*` remain empty, every run rejects with `PREREGISTRATION_PENDING_DATA_CONFIRMATION`. Populating them requires a separately reviewed PR after user confirmation.

## 4. Confirmed Data Source and Window (Confirmed by User 2026-10-11)

| Item | Value |
| --- | --- |
| `data_source` | `alpaca_sip_raw_bars_plus_corporate_actions_private_archive` (existing private Alpaca SIP raw bars + corporate actions archive) |
| `data_manifest_sha256` | `cb14a511083c824a748d137a271c93cfe0e8adf38f648905b26e37decf4c6182` (SHA-256 of archive `manifest.json`; `DataManifest.sha256` must equal it) |
| `data_license` | `user_attested_non_commercial_private_research` |
| Window | 2023-03-28 through 2026-08-25 (return window; prior sessions used for signal warmup only) |
| Allowed variant | `no_plugin_core` only; `live_plugins_as_live` requires separate confirmation (`VARIANT_NOT_CONFIRMED_FOR_REAL_DATA`) and stays blocked by plugins |
| Execution host | Authorized private data environment only; raw data and daily outputs never enter Git or public CI/logs |

### 4.0 Revision (RS-04e, 2026-10-11): Switched Fetch Archive

Because the original archive (raw manifest `cb14a511...`) was unavailable in the authorized runtime environment and warmup before 2023-03-28 fell short of the ~252 sessions required by core rules, the user authorized a **read-only** re-fetch from `data.alpaca.markets` (`feed=sip`, `adjustment=raw`, daily bars + corporate actions, 2021-12-01 through 2026-08-25 for SOXL, SOXX, BOXX, QQQ, TQQQ, QQQM), stored in a private `0700`/`0600` directory outside Git.

| Item | Updated Value |
| --- | --- |
| `data_source` | `alpaca_sip_raw_bars_plus_corporate_actions_box_fetch_20261011` |
| `data_manifest_sha256` | `b5a3dd40cb0d4260fb15492108bde7ab544ac0d191807943637b8216a6808482` |
| `data_retrieved_at` | `2026-10-11T02:07:07+00:00` (10:07 CST) |
| Coverage | SOXL/SOXX/QQQ/TQQQ/QQQM: 1,187 bars each (2021-12-01..2026-08-25); BOXX: 917 bars from 2022-12-28. 331 pre-window warmup sessions, 856 in-window sessions |

Window, walk-forward plan, `c`, costs, timing, and variant limits remain unchanged; this revision supersedes `cb14a511...` above.

### 4.1 Original Proposal (Archived)

| Key | Proposed Source | Proposed Window | Notes |
| --- | --- | --- | --- |
| SOXL | Existing private Alpaca SIP raw + corporate actions archive (`user_attested` non-commercial license, raw manifest `cb14a511...`), same-source adjusted prices for signals | 2023-03-28..2026-08-25 (856 sessions used in R9; requires >= 260 prior warmup sessions) | ~9 test folds under 252/63/5; all `development` |
| TQQQ | Same as above | Same as above | Same as above |
| Alternative | Fetch longer history (e.g., Alpaca from 2016) to increase fold count | From 2016 | Requires separate fetch and license approval; Yahoo adjusted snapshots lack completeness/license verification |

## 5. Known Gaps

1. No real-data TQQQ live-parity replay generator exists yet: `optimized_strategy_replay` is fixture-only, and `tqqq_typed_baseline_result` disables all controls with zero fees.
2. The SOXL core simulator outputs post-fee daily returns without daily turnover; a research adapter must run at zero fees and record daily traded notional/NAV for cost-tier stress testing.
3. Plugins: market regime control, retention, and TQQQ crisis defense cannot be reproduced historically (§2.1); income layer and runtime risk gate need adapters; IBKR plugin mounts, release revision, and config SHA need readback verification.
4. Cloud paths for private raw/R6 archives, SOXL/SOXX coverage through 2026-08-25, and BOXX dividend `process_date` availability require pre-run readback verification.
5. Gap between live 15:45 fills and research `next_close` (UES #575).

## 6. RS-07 Live-Research Parity Revision (2026-10-11, New Trial)

An external review flagged two live-vs-research discrepancies that were fixed in research code and registered as **new trials** (see `docs/research/kelly_trial_ledger.json`), while v1 remains preserved and reproducible:

| Preregistration ID | Generator | Signal Price | SOXL Indicator Source | Role |
| --- | --- | --- | --- | --- |
| `rs04-kelly-soxl-v2` | `core_v2_parity` | Split-adjusted (ex-dividend, backtest standard v1 §4.4) | QPK live function `build_semiconductor_rotation_indicators_from_history` (with dynamic RSI thresholds), up to 504 latest sessions | Primary |
| `rs04-kelly-tqqq-v2` | `core_v2_parity` | Split-adjusted | Strategy internal computation | Primary |
| `rs04-kelly-soxl-v2-trsig` | `core_v2_parity` | Total return index (approximates IBKR `ADJUSTED_LAST`) | QPK live function | Sensitivity |

All other parameters (data, window, walk-forward, `c`, costs, timing, `no_plugin_core` only) match v1.

- 504 trading days corresponds to IBKR live's `"420 D"` request converted by QPK to `"2 Y"`. Because only 331 sessions precede the window start, early decisions use shorter history (minimum 280 sessions = 252 + 28 for dynamic RSI thresholds); initial RSI exponential smoothing differences decay over time.
- Live passes only `trend_ma_window` and takes QPK defaults for remaining parameters; tests verify these defaults match the manifest.
