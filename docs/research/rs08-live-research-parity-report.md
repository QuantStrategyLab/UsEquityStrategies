# RS-08 Live vs. Research Parity Check Report (Read-Only)

English | [简体中文](rs08-live-research-parity-report.zh-CN.md)

> Date: 2026-10-11. Scope: SOXL (Schwab live, IBKR SOXL account) and TQQQ (IBKR TQQQ account). Read-only throughout: no deployments, no changes to environment variables, secrets, traffic, or order paths. Any fix that changes live behavior is a proposal requiring manual approval.

## Evidence Sources

- Read-only diagnostic workflow `Strategy Parity Check` (manual trigger, one run each):
  - IBKR: `InteractiveBrokersPlatform#597`, run `<recorded-run-or-artifact-id>`
  - Schwab: `CharlesSchwabPlatform#512`, run `<recorded-run-or-artifact-id>`
- Workflow actions: inspected the active Cloud Run revision serving traffic; pulled the image by digest inside a `--network none` container without env vars to read package commit SHAs and manifests; read release identifiers (hashes and version tags only) from `RUNTIME_TARGET_JSON`; summarized 21-day log reason codes; and on IBKR also fetched public yfinance data using the image's platform code to log bar counts and adjustment basis.
- Public logs contain no account balances, positions, tokens, chat IDs, or full configs.
- Static code inspection targets: platform `main`, UES `4a394388` / `9d1544d1`, and QPK.

## 1. Revisions Currently Running in Production

| Service | Traffic-Serving Revision (100%) | Image Digest | In-Image UES | In-Image QPK |
| --- | --- | --- | --- | --- |
| Schwab `charles-schwab-quant-service` | `00781-lel` (created 2026-10-09) | `sha256:cd4779376abf...` | `4a3943883cd6` (0.7.60) | `2dc0b26ad4f1` |
| IBKR (SOXL) | `00416-8jb` (2026-10-09) | `sha256:9429234a87bf...` | `4a3943883cd6` (`/app/UES_REVISION` matches) | `bd8d06e50f88` |
| IBKR (TQQQ) | `n0110100718` (2026-10-09) | `sha256:9fca942d3713...` | `4a3943883cd6` | `bd8d06e50f88` |

Conclusions:
- All three services run UES `4a394388`, matching `strategy_release.strategy_revision` and `ues_revision` in the risk binding.
- Platform repository pins (UES `9d1544d1`, QPK `155d74a6`) **are newer than the running images, which have not been redeployed**. The 2026-10-09 revisions were created by NOTIFY-01 cloning the previous image with updated Telegram tokens only.
- The QRS dependency matrix entry `4a394388` matches live execution.

Differences between `4a394388` and `9d1544d1`:
- SOXL strategy code and manifest are **identical**.
- In TQQQ `tqqq_dual_drive_core.py`, decision logic was extracted into a helper function. Testing 50,000 random inputs showed **zero diff** in `decide_tqqq_dual_drive` outputs.
- `entrypoints/__init__.py` only added research parameter and DCA pass-through without affecting live SOXL/TQQQ.
- The source hash of QPK live indicator function `build_semiconductor_rotation_indicators_from_history` matches identically (`eaffcd10...`) between live images (`bd8d06e5`, `2dc0b26a`) and the research environment.

**Assessment: research strategy logic is behaviorally equivalent to live.** Research `main` strategy files match `9d1544d1`, and `9d1544d1` is behaviorally equivalent to `4a394388`.

**Risk note (important)**: Schwab logs from 2026-09-21 through 2026-09-25 show earlier revisions (`00696`, `00698`) had a mismatch between installed UES revision and bound `ues_revision`, triggering `strategy_runtime_binding_mismatch` and 9 `strategy_runtime_risk_reject` (`rejected:runtime_risk_limits`) events that blocked SOXL rebalances. Current revisions show zero occurrences in the past 21 days. Rebuilding from repo pin (`9d1544d1`) without simultaneously updating `strategy_release` and risk-binding `ues_revision` will **reproduce those rejections**.

## 2. Why `config_sha256` Does Not Match Manifest Defaults

| Service | Published `config_sha256` | Manifest Default Config Hash (Recomputed in Image) |
| --- | --- | --- |
| Schwab SOXL | `945c852a...` | `b2b8a896...` (compact JSON) / `95776aab...` |
| IBKR SOXL | `0fbd7b3b...` | Same as above |
| IBKR TQQQ | `90e8ded5...` | `eec673a4...` / `c37dde83...` |

Both platforms run the same SOXL manifest yet publish different `config_sha256` values. This indicates the hash covers **platform-merged configuration** (reserved cash, income layer, option flags, plugin mounts, etc.), not raw manifest defaults. Its serialization formula lives outside any repository and was computed externally at release time on 2026-09-17. **Assessment: this is an unarchived hash specification rather than code divergence.** Recomputing it requires the release evidence package matching `evidence_sha256`; Section 6 diagnostics did **not find** that package in `gs://<runtime-logs-bucket>`.

## 3. `trend_ma_window`: Live Value Is 140

- Both platforms compute `merged = manifest.default_config` plus runtime overrides. Overrides cover reserved cash, income layer, options, DCA, IBIT, etc., **never `trend_ma_window`**.
- In-image manifests set `trend_ma_window = 140`. None of the three services sets `STRATEGY_CONFIG_PATH` or any env var mentioning `trend_ma_window`.
- Platform fallback `.get("trend_ma_window", 150)` triggers only when the key is missing and is never reached.
- QPK's required history length is 420 bars under both 140 and 150.

**Assessment: live runs `140`, matching research (`manifest 140`).**
**Proposal (requires manual approval)**: drop the `150` fallback or read directly from the manifest so a missing key cannot silently shift to `150`.

## 4. Price Basis and History Length

- Neither platform configures external market signals (no `MARKET_SIGNAL_*` env vars); SOXL builds indicators locally.

| Item | IBKR (SOXL, TQQQ) | Schwab (SOXL) | Research v2 |
| --- | --- | --- | --- |
| Data source | Prefers yfinance (`auto_adjust=False`, uses `Adj Close`); falls back to IBKR `ADJUSTED_LAST` | QPK Schwab `get_price_history`, `TWO_YEARS`, daily | Alpaca SIP raw bars + corporate actions |
| Adjustment | Split + dividend adjusted: on 2026-10-11 `Adj Close / Close` starts at 0.990 (SOXL/SOXX/QQQ) and ends at 1.0 | **Split-adjusted only** (Section 6B: 10/10 sessions match split-adjusted recomputation) | Primary: split-adjusted; sensitivity: total return (`rs04-kelly-soxl-v2-trsig`) |
| History length | `"420 D"` mapped to `"2 Y"`, yielding 501 rows (2024-10-10..2026-10-09); TQQQ's QQQ is also 501 rows | 2 years (row count to be verified) | SOXL up to 504; TQQQ full history |
| Per-run source/row logging | **Not logged** | **Not logged** | — |

Assessment:
- History length: consistent (~500 rows for SOXL; TQQQ's longest window is 252 for MA200 and vol quantiles, invariant past ~260 rows).
- Price basis: **the two brokers differ from each other**. IBKR uses dividend-adjusted series matching research `trsig` sensitivity; Schwab uses split-adjusted series (verified in §6B), matching the research primary basis and backtest standard §4.4. Research registers both (`primary` and `trsig`), while live brokers should be aligned.
- **Proposals (require manual approval)**:
  1. Unify signal price basis: switch IBKR to `Close` (split-adjusted only) to match Schwab and the backtest standard, or align both on total return and revise the standard.
  2. Log non-sensitive signal provenance on each run: source (`yfinance` / `ADJUSTED_LAST` / `Schwab`), row count, first/last dates, and basis label.
  3. Run research parity replays using `trsig` for IBKR and `split_adjusted` for Schwab.

## 5. Additional Observations

- IBKR (TQQQ) logged `runtime_risk_status=unavailable:profile_not_supported` on every run for 21 days. This is expected because runtime risk binding currently supports SOXL only.
- On IBKR SOXL, `RUNTIME_TARGET_ENABLED=false` while `RUNTIME_TARGET_JSON` is populated. No anomalous logs appeared over 21 days; left out of scope here.

## 6. Follow-Up Read-Only Diagnostics: Release Package and Schwab Basis (2026-10-11)

Because `gcloud` and `yfinance` were unavailable in the local Codex environment, the read-only diagnostic workflow was extended and triggered once per broker via deployment service accounts: IBKR `#600` (`08fd12a`, run `<recorded-run-or-artifact-id>`) and Schwab `#515` (`e81cf3c`, run `<recorded-run-or-artifact-id>`). Both service accounts can read `gs://<runtime-logs-bucket>`. Public logs contain only hashes, field names, labels, integers, and error bounds—no balances, positions, or tokens.

**A. `config_sha256` formula: evidence package not found.**
- Among 11,326 bucket objects, none matched the three `release_id` values (`soxl-schwab-runtime-risk-recovery-20260917`, `soxl-ibkr-runtime-risk-20260917`, `tqqq-ibkr-lkg-restore-20260917`) or corresponding evidence/config SHA-256 prefixes.
- Inspecting 250 JSON objects matching release/evidence/lifecycle/promotion/activation names (excluding execution reports) found no references to those values.
- **Assessment: the evidence package is absent from this bucket (or differently named), so recomputation remains blocked.** Other JSON files were filtered by name only and not exhaustively read.
- Recommendation: record `config_sha256` input fields, `sort_keys`, separators, and included platform overrides in the repository or evidence bundle on future releases.

**B. Schwab SOXL signal price basis: split-adjusted only.**
- Method: took SOXX price and 140-day trend MA from Schwab's 10 latest live `soxl_soxx_trend_income` execution reports (2026-09-28..10-09), then recomputed against yfinance daily `Close` (split-adjusted) and `Adj Close` (dividend-adjusted) using 139 prior closes scaled to the run date plus the reported day-140 price.
- Result: **10/10 sessions matched split-adjusted `Close`** with relative errors below `1e-6` (~`1e-8.5`), whereas dividend-adjusted `Adj Close` differed by ~`1e-3.1`.
- Reports include fields for trend symbol SOXX only, **not SOXL `ma_trend` or history row count**; fix #4 adds per-symbol source, row count, date bounds, and basis labels once deployed.
- **Assessment**: Schwab matches the research primary basis (split-adjusted, backtest standard §4.4); IBKR uses dividend-adjusted prices (§4), matching `trsig` sensitivity.

## 7. Remediation Status

| ID | Description | Status |
| --- | --- | --- |
| 1 | Verify in-image UES revision matches `strategy_release` and risk-binding `ues_revision` before deploy; fail deploy on mismatch | Merged: IBKR `#598` (`c438de4`), Schwab `#513` (`7fd258d`). Deploy workflow check only; runtime unchanged |
| 2 | Remove fallback `150` for `trend_ma_window` in platform code | **Manual approval required; not implemented** |
| 3 | Unify signal price basis (switch IBKR to split-adjusted only) | **Manual approval required; not implemented** |
| 4 | Log signal source, row count, start/end dates, and basis label on every run | Merged: IBKR `#599` (`c59aac5`), Schwab `#514` (`e3dea56`). Effective after new revision deploy |

Merging does not deploy: deployment workflows are manual-only while pushes run tests. No environment variables, secrets, traffic, or order paths were changed.
