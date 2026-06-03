# UsEquityStrategies

[Chinese README](README.zh-CN.md)

> ⚠️ Investing involves risk. This project does not provide investment advice and is for educational and research purposes only.

## What this project does

UsEquityStrategies is a **Strategy package** in the QuantStrategyLab ecosystem. It contains reusable US equity strategy implementations and execution metadata shared by QuantStrategyLab platforms, including ETF rotation and income-oriented allocation modules.

## Who this is for

- Engineers and researchers who want to inspect, reproduce, or extend this part of the QuantStrategyLab stack.
- Operators who need a clear entry point before reading the deeper runbooks or workflow files.
- Reviewers who need to understand the repository purpose, safety boundary, and evidence requirements before enabling automation.

## Current status

Live-facing strategy package. Any live profile should be backed by recent short, medium, and long-window validation.

## Repository layout

- `src/`: main library and runtime code.
- `tests/`: unit and contract tests.
- `docs/`: detailed design notes, runbooks, and evidence docs.
- `.github/workflows/`: CI, scheduled jobs, and deployment workflows.

## Quick start

From a fresh clone:

```bash
python -m pip install -e .
python -m pytest -q
```

If a command requires credentials, run it only after reading the relevant workflow or runbook and configuring secrets outside Git.

## Deployment and operation

Install this package into a platform repository or point the platform strategy loader at this repository. Keep execution credentials in the platform repository, not here.

Prefer manual or dry-run execution first. Enable schedules or live execution only after logs, artifacts, permissions, and rollback steps are reviewed.

## Strategy performance and evidence

Strategy performance must be judged from reproducible backtests and snapshot artifacts, not from README claims. For live use, compare candidate return, max drawdown, and benchmark-relative performance across short, medium, and long periods; strategies that fail the drawdown or benchmark gate should remain research-only.

README files are intentionally not a source of dated performance promises. Re-run the relevant tests, backtests, or pipeline jobs before relying on any result.

## Safety notes

- Never commit API keys, broker credentials, OAuth tokens, cookies, or account identifiers.
- Run new strategies and platform changes in dry-run or paper mode before any live execution.
- Review generated orders, artifacts, and logs manually before enabling schedules.

## Contributing

Keep changes small, reproducible, and covered by the narrowest useful tests. For strategy-facing changes, include the evidence artifact or command used to validate behavior.

## License

See [LICENSE](LICENSE) if present in this repository.
