# UsEquityStrategies

[Chinese README](README.zh-CN.md)

> Investing involves risk. This project does not provide investment advice and is for education, research, and engineering review only.

## What this repository is

UsEquityStrategies is the QuantStrategyLab US equity strategy package. It provides reusable strategy implementations and runtime metadata for QuantStrategyLab US equity platforms.

It is one layer of a multi-repository system:

- **Strategy packages**: hold reusable strategy code, metadata, and runtime entrypoints.
- **Snapshot pipelines**: produce feature snapshots, rankings, backtests, and release evidence.
- **Platform runtimes**: connect strategies to brokers, dry-run checks, notifications, and live deployment controls.
- **Shared infrastructure**: keeps contracts, settings, adapters, plugins, and audit workflows reusable across repositories.

This repository owns strategy code and metadata. It does not hold broker credentials, submit orders by itself, or replace the snapshot/backtest evidence required before a profile is enabled for live runtime settings.

## Strategy profiles

### Direct runtime strategies

These profiles can run from market history, portfolio snapshots, or other runtime inputs without a separate feature-snapshot build step.

| Profile | Name | Notes |
| --- | --- | --- |
| `global_etf_rotation` | Global ETF Rotation | runtime-enabled ETF rotation using market history. |
| `tqqq_growth_income` | TQQQ Growth Income | runtime-enabled QQQ/TQQQ dual-drive profile with defensive and income sleeves. |
| `soxl_soxx_trend_income` | SOXL/SOXX Semiconductor Trend Income | runtime-enabled semiconductor ETF trend profile. |
| `nasdaq_sp500_smart_dca` | Nasdaq/S&P 500 Smart DCA | runtime-enabled buy-only DCA profile for broad US equity ETFs. |
| `ibit_smart_dca` | IBIT Smart DCA | runtime-enabled buy-only spot Bitcoin ETF DCA profile with capped satellite exposure. |

### Snapshot-backed strategies

These profiles depend on artifacts produced by `UsEquitySnapshotPipelines` before downstream platforms should use them.

| Profile | Name | Notes |
| --- | --- | --- |
| `russell_1000_multi_factor_defensive` | Russell 1000 Multi-Factor | runtime-enabled feature-snapshot stock selector. |
| `mega_cap_leader_rotation_top50_balanced` | Mega Cap Leader Rotation Top50 Balanced | runtime-enabled feature-snapshot mega-cap leader rotation. |

### Research-only candidates

Research-only profiles may stay in code for reproducibility and future review, but they should not appear in current configurable live profiles.

| Profile | Name | Notes |
| --- | --- | --- |
| `tech_communication_pullback_enhancement` | Tech/Communication Pullback Enhancement | archived research-only; no longer a catalog/entrypoint runtime profile. |

## How this connects to execution

Execution platforms consume this package through strategy loaders and runtime metadata. Current downstream platforms: CharlesSchwabPlatform, InteractiveBrokersPlatform, LongBridgePlatform, and FirstradePlatform.

Use the platform repositories for broker credentials, dry-run/live switches, order submission, and deployment settings.

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

- [`docs/tqqq_ai_extensions.md`](docs/tqqq_ai_extensions.md)
- [`docs/us_equity_contract_gap_matrix.md`](docs/us_equity_contract_gap_matrix.md)
- [`docs/us_equity_notification_i18n_contract.md`](docs/us_equity_notification_i18n_contract.md)
- [`docs/us_equity_notification_i18n_contract.zh-CN.md`](docs/us_equity_notification_i18n_contract.zh-CN.md)
- [`docs/us_equity_portability_checklist.md`](docs/us_equity_portability_checklist.md)
- [`docs/us_equity_runtime_archive.zh-CN.md`](docs/us_equity_runtime_archive.zh-CN.md)
- [`docs/us_equity_strategy_status.zh-CN.md`](docs/us_equity_strategy_status.zh-CN.md)
- [`docs/research/ibit_smart_dca.md`](docs/research/ibit_smart_dca.md)
- [`docs/us_equity_strategy_template.md`](docs/us_equity_strategy_template.md)

## Safety and contribution notes

- Keep secrets, account identifiers, tokens, cookies, and broker credentials out of Git and logs.
- Prefer small, reviewable changes with tests or reproducible evidence.
- For strategy changes, include the command or artifact used to validate behavior.

## Community and security

- See [CONTRIBUTING.md](CONTRIBUTING.md) for pull request scope, local verification, and documentation expectations.
- Follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for maintainer and contributor conduct.
- Report credential, automation, broker, exchange, or cloud-resource vulnerabilities through [SECURITY.md](SECURITY.md); do not open public issues for secrets or live-execution risk.

## License

See [LICENSE](LICENSE).
