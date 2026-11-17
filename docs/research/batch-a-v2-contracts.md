# Batch A Research Data v2 Contracts (Frozen)

English | [简体中文](batch-a-v2-contracts.zh-CN.md)

Source: audit §9.15.27 D0/D2. This document freezes a **breaking, incompatible** research input contract; legacy R3, `price_snapshot.v1`, and `c3-batch-a-frozen-member-pack.v1` stay only as historical evidence and Git history, **do not enter active Batch A entrypoints**, and have no adapter, alias, dual-version dispatcher, or automatic conversion.

## Scope and Boundaries

| Item | Convention |
|---|---|
| Canonical storage | Existing private bucket; new object prefix `research/v2/input/<dataset_id>/` with matching `output`/`quarantine` |
| Object writes | Create-only; manifest locked last; bound to GCS generation, byte size, and SHA-256 |
| Active path | `batch_a_dataset` -> `batch_a_member_pack` -> Batch A validator/CLI; `run_batch_a_from_gcs` performs exact staging + local materialization only |
| Rejected | Any `*.v1` schema, v1-to-v2 auto-conversion, Yahoo/R3 five-file layout, `--private-root` as a Batch A precondition |
| Cash leg | `ASSUMED_ZERO_USD_CASH` only; BOXX must not be treated as risk-free cash |
| Member returns | Must be **full strategy daily returns** from a frozen strategy + cost model; underlying ticker returns are forbidden |
| Authority | `research_only=true`; `execution_authorized=false`; `no_order=true`; grants no paper, live, or promotion authority |

Legacy `research/v1`, `run_r3_from_gcs`, `run_r3_joint_evidence`, and R3 source remain in the repository but have exited active Batch A paths. Shared offline v1 types and the Alpaca adapter still serve active consumers (SOXL RSI2, typed baseline, etc.) and are not removed here.

## `qsl.research.price_snapshot.v2`

Each dataset directory contains at minimum:

- `prices.csv`: UTF-8 LF; fixed header `symbol,as_of,open,high,low,close,volume`; sorted ascending by `(as_of,symbol)`; unique dates per symbol; finite positive OHLC and non-negative volume; identical date sets across all symbols in the dataset.
- `prices.csv.manifest.json`: exact field set below (no extra or missing keys).

| Field | Requirement |
|---|---|
| `schema` | Must be `qsl.research.price_snapshot.v2` (reject v1) |
| `research_only` | `true` |
| `dataset_id` | Non-empty identifier matching object path `research/v2/input/<dataset_id>/` |
| `provider` / `feed` / `price_field` / `adjustment` | Explicit strings; Batch A prefers `alpaca` + `sip` + `adjusted_close` + `all` |
| `calendar` / `timezone` | Explicit strings (such as `XNYS` / `America/New_York`) |
| `license_retention` | Non-empty license and retention condition summary |
| `code_version` | Non-empty ingestion/materialization code version |
| `source_revision` / `retrieved_at` | Non-empty |
| `symbols` | Non-empty unique list matching the CSV symbol set |
| `request.start` / `request.end_exclusive` | ISO dates; `start < end_exclusive`; all rows inside the half-open interval |
| `gcs.bucket` / `gcs.object` / `gcs.generation` / `gcs.bytes` / `gcs.sha256` | Object identity; 64-char lowercase hex `sha256`; positive integer `bytes`; non-empty decimal string `generation` |
| `counts` / `coverage` | Per symbol; `coverage` is `{start,end}` matching first and last dates for that symbol |

Loading simultaneously verifies:

1. File byte size matches `gcs.bytes` and content SHA-256 matches `gcs.sha256`;
2. Declared relative `gcs.object` path matches the actual staging relative path (mismatch rejected);
3. If `object_identity.json` (with `generation`) is present, it must match `gcs.generation` (mismatch rejected);
4. Incomplete dates (missing symbol, misaligned dates, missing calendar dates when complete coverage is required, empty series) are rejected.

## `qsl.c3-batch-a-frozen-member-pack.v2`

| Field | Requirement |
|---|---|
| `schema_version` | `qsl.c3-batch-a-frozen-member-pack.v2` (reject v1) |
| `research_only` | `true` |
| `execution_authorized` | `false` |
| `cash_return_policy` | `ASSUMED_ZERO_USD_CASH` only |
| `members` | Exactly and uniquely `cash_sleeve`, `soxl_core`, `tqqq_core` (reject duplicates, missing, or unknown IDs) |
| Per member | `member_id`, `evidence_digest`, `input_digest`, aligned `dates`/`returns`, and six C2 comparability fields: `as_of`, `quote_currency`, `capital_basis_digest`, `cost_model_digest`, `risk_policy_digest`, `data_scope_digest` |
| `cash_sleeve.returns` | All zeros; otherwise rejected |
| `soxl_core` / `tqqq_core` | Returns must come from the frozen strategy path (typed SMA200 baseline + declared cost model), never underlying buy-and-hold returns |
| `pack_digest` | Canonical JSON SHA-256 over the pack **excluding** `pack_digest`; mismatch rejected |

Batch A consumers accept this schema only; missing or invalid packs stay `PARKED` without fabricating returns.

## Active Entrypoints

1. `us_equity_strategies.research.batch_a_dataset`: load and validate v2 snapshots.
2. `us_equity_strategies.research.batch_a_member_pack`: materialize v2 member packs from validated datasets and check `pack_digest`.
3. `scripts/run_batch_a_from_gcs.py`: exact copy of v2 objects into an empty temp directory followed by local materialization; no broker/production access by default; tests must not hit live GCS.
4. `evaluate_batch_a_existing_member_baselines` / `run_c3_batch_a_existing_member_baseline.py`: v2 packs only.

Out of scope for this batch: live ingestion, live GCS reads/writes, data purchases, or changes to cloud resources, QPK, platforms, accounts, or trading.
