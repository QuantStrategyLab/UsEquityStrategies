# UsEquityStrategies

[Research and contract index](docs/research/README.md) — English overviews, complete source documents and historical runtime references.

UsEquityStrategies is the strategy-lib behind QuantStrategyLab's US equity trading: it holds the strategy implementations, manifests, and runtime metadata that platform repositories load through strategy loaders. It consumes QuantPlatformKit and validated snapshot artifacts from `UsEquitySnapshotPipelines`, but it does not hold broker credentials, submit orders, or own deployment settings — that lives in the platform repositories (CharlesSchwabPlatform, InteractiveBrokersPlatform, LongBridgePlatform, FirstradePlatform).

[Chinese README](README.zh-CN.md)

> Investing involves risk. This project does not provide investment advice and is for education, research, and engineering review only.

## Option retirement and income budget migration

Production entrypoints no longer advertise or generate proprietary option
overlays. Explicit legacy option recipes, budgets or known option obligations
are rejected for migration; literal boolean `false` switches and empty recipes
are the only accepted legacy no-op controls. Missing account evidence is not
proof of no option holdings. Historical research replay remains separate. See
[option overlay retirement](docs/option-overlay-retirement.md).

Weight-based income allocation now funds per-symbol minimum targets from the
existing invested target budget and preserves implicit cash. It does not
rescale a 60% invested portfolio to 100%. Existing income holdings remain locked
when disabled. An infeasible budget aborts the entire strategy decision before
returning executable targets; this can also delay otherwise valid risk-reducing
orders for that cycle. It does not guarantee those orders execute. Independent
platform protections remain necessary. TQQQ/SOXL retain their existing amount
plans and internal risk rules. See [income budget contract](docs/income-budget-contract.md).

## What this repository is

UsEquityStrategies is the QuantStrategyLab US equity strategy package. It provides reusable strategy implementations and runtime metadata for QuantStrategyLab US equity platforms.

It is one layer of a multi-repository system:

- **Strategy packages**: hold reusable strategy code, metadata, and runtime entrypoints.
- **Snapshot pipelines**: produce feature snapshots, rankings, backtests, and release evidence.
- **Platform runtimes**: connect strategies to brokers, dry-run checks, notifications, and live deployment controls.
- **Shared infrastructure**: keeps contracts, settings, adapters, plugins, and audit workflows reusable across repositories.

This repository owns strategy code and metadata. It does not hold broker credentials, submit orders by itself, or replace the snapshot/backtest evidence required before a profile is enabled for live runtime settings.

## Configuration

Export these environment variables before importing or running the offline R3 evidence runner to use existing local files. Relative defaults resolve from the working directory.

| Variable | Purpose | Required | Default |
|---|---|---|---|
| `UES_R3_OUTPUT_ROOT` | Offline evidence output directory | Optional | `.local/r3_joint_evidence` |
| `UES_R3_CONTRACT_PATH` | Local acceptance contract | Optional | `.local/r3_joint_evidence/acceptance-contract.md` |
| `UES_R3_WORKER_PROMPT_PATH` | Local worker prompt | Optional | `.local/r3_joint_evidence/worker-prompt.md` |
| `UES_R3_PRIVATE_ROOT` | Private research input root | Optional | `.local/private_research` |


## Strategy profiles

### Direct runtime strategies

These profiles can run from market history, portfolio snapshots, or other runtime inputs without a separate feature-snapshot build step.

| Profile | Name | Notes |
| --- | --- | --- |
| `tqqq_growth_income` | TQQQ Growth Income | runtime-enabled QQQ/TQQQ dual-drive profile with defensive and income sleeves. |
| `soxl_soxx_trend_income` | SOXL/SOXX Semiconductor Trend Income | runtime-enabled semiconductor ETF trend profile. |
| `nasdaq_sp500_smart_dca` | Nasdaq 100 / S&P 500 Smart DCA | runtime-enabled buy-only DCA profile for broad US equity ETFs; defaults to monthly fixed-amount DCA with configurable weekly/monthly/quarterly cadence and optional smart sizing. |
| `ibit_smart_dca` | IBIT Smart DCA | runtime-enabled buy-only spot Bitcoin ETF DCA profile; defaults to monthly fixed-amount DCA with configurable weekly/monthly/quarterly cadence and optional smart sizing. |

### Snapshot-backed strategies

These profiles depend on artifacts produced by `UsEquitySnapshotPipelines` before downstream platforms should use them.

| Profile | Name | Notes |
| --- | --- | --- |
| `global_etf_rotation` | Global ETF Rotation | retained for historical research and compatibility; excluded from the runtime-selectable allowlist. |
| `russell_top50_leader_rotation` | Russell Top50 Leader Rotation | runtime-enabled feature-snapshot Russell Top50 leader rotation. |

### Research-only candidates

Research-only profiles may stay in code for reproducibility and future review, but they should not appear in current configurable live profiles.

| Profile | Name | Notes |
| --- | --- | --- |
| `tech_communication_pullback_enhancement` | Tech/Communication Pullback Enhancement | archived research-only; no longer a catalog/entrypoint runtime profile. |

## How this connects to execution

Execution platforms consume this package through strategy loaders and runtime metadata. Current downstream platforms: CharlesSchwabPlatform, InteractiveBrokersPlatform, LongBridgePlatform, and FirstradePlatform.

Use the platform repositories for broker credentials, dry-run/live switches, order submission, and deployment settings.

The opt-in [`qqq-dca-qndx-newbuys-202610` configuration](docs/qqq-dca-qndx-newbuys-202610.md) routes only new Nasdaq DCA contributions to QNDX, retains existing QQQ/QQQM holdings, and keeps QQQ signals. The default remains QQQM; selecting the descriptive alias alone does not enable the switch.

## QSL architecture role

- **Layer**: `strategy-lib`.
- **Responsibility**: US equity strategy implementation package.
- **Owns**: runtime-enabled US strategy code, manifests, execution metadata.
- **Consumes**: QuantPlatformKit and validated US snapshot artifacts.
- **Must not**: own broker credentials or deployment settings.

## Evidence and live enablement

Use this README as a map of the project, not as live performance data. Before enabling or changing a live profile, rerun the relevant snapshot/backtest pipeline and review short, medium, and long windows: return, max drawdown, benchmark-relative return, turnover, data freshness, and artifact version. If evidence is stale, incomplete, or the profile is marked research-only, keep it out of live runtime settings.

## Repository layout

- `src/`: library and runtime code.
- `tests/`: unit, contract, and regression tests.
- `docs/`: runbooks, design notes, evidence, and integration contracts.
- `.github/workflows/`: CI, scheduled jobs, release, or deployment workflows.

## Quick start

```bash
python -m pip install -e .
python -m pytest -q
```

## Useful docs

- [`docs/tqqq-ai-extensions.md`](docs/tqqq-ai-extensions.md)
- [`docs/us-equity-contract-gap-matrix.md`](docs/us-equity-contract-gap-matrix.md)
- [`docs/us-equity-notification-i18n-contract.md`](docs/us-equity-notification-i18n-contract.md)
- [`docs/us-equity-notification-i18n-contract.zh-CN.md`](docs/us-equity-notification-i18n-contract.zh-CN.md)
- [`docs/us-equity-portability-checklist.md`](docs/us-equity-portability-checklist.md)
- [`docs/us-equity-runtime-archive.md`](docs/us-equity-runtime-archive.md) ([简体中文](docs/us-equity-runtime-archive.zh-CN.md))
- [`docs/us-equity-strategy-status.md`](docs/us-equity-strategy-status.md) ([简体中文](docs/us-equity-strategy-status.zh-CN.md))
- [`docs/research/ibit-smart-dca.md`](docs/research/ibit-smart-dca.md)
- [`docs/us-equity-strategy-template.md`](docs/us-equity-strategy-template.md)

## Safety and contribution notes

- AI assistants and maintainers follow [AGENTS.md](AGENTS.md).

- Keep secrets, account identifiers, tokens, cookies, and broker credentials out of Git and logs.
- Prefer small, reviewable changes with tests or reproducible evidence.
- For strategy changes, include the command or artifact used to validate behavior.

## Community and security

- See [CONTRIBUTING.md](CONTRIBUTING.md) for pull request scope, local verification, and documentation expectations.
- Follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for maintainer and contributor conduct.
- Report credential, automation, broker, exchange, or cloud-resource vulnerabilities through [SECURITY.md](SECURITY.md); do not open public issues for secrets or live-execution risk.

## License

See [LICENSE](LICENSE).
