# Research and contract index

[English README](../../README.md) · [中文 README](../../README.zh-CN.md)

## Signal input contracts

- [Signal-source repository design: English overview](generic-signal-source-repository-design-overview.md) · [complete Chinese design](generic-signal-source-repository-design.md). Dated design and ownership boundaries; not a deployment record.
- [Market Signal Bundle: English overview](market-signal-bundle-artifact-contract-overview.md) · [complete contract and CLI examples](market-signal-bundle-artifact-contract.md). Consumer validation and runtime/research handoff rules.

## Research plans

- [Smart DCA: English overview](smart-dca-indicator-research-plan-overview.md) · [complete research plan](smart-dca-indicator-research-plan.md). Baselines, candidate rules, evidence and promotion review remain separate from live enablement.
- [Index LEAPS overlay](index-leaps-growth-overlay.md) ([中文](index-leaps-growth-overlay.zh-CN.md)). Research-only; real option-chain validation remains required before live intent.

## Historical Kelly metric conventions

The frozen fractional Kelly v1 and portfolio Kelly v1/v2 evaluators report
`cvar_95_daily` as the mean of the worst `ceil(0.05 * n)` observed session
returns. Returns are signed: negative values represent losses. QPK's
`cvar95_return_per_period` instead averages exactly the worst 5% probability
mass, splitting the boundary observation; it uses the frequency of its input
net returns, which need not be daily. These statistics can differ even for the
same daily path. Do not rank or compare them as interchangeable metrics; retain
the evaluator version, input window, frequency and cost basis with each result.

The fractional evaluator estimates the mean of strategy returns minus cash but
uses the sample variance of strategy returns. The portfolio evaluator uses
covariance of returns minus cash and reuses the fractional evaluator's mean
shrinkage. When cash returns vary, these are different assumptions. A future
version must declare its cash numeraire, centered variance or covariance versus
second raw moment, return frequency and fee basis, and recompute comparisons
from the same inputs. This records existing behavior and does not select a new
model, change historical results, or authorize adoption. See the
[detailed Chinese contract](fractional-kelly-walk-forward.zh-CN.md).

## Historical runtime reference

- [US equity runtime archive](../us-equity-runtime-archive.md) ([中文](../us-equity-runtime-archive.zh-CN.md)). Retains the profile/evidence snapshot dated 2026-06-28. Its performance rows and runtime labels are historical context, not a current deployment or live-eligibility readback.

The English overviews summarize the linked complete documents; they do not replace their field definitions, examples, risk controls or data-license requirements. Research notes do not authorize trading. See the repository [license](../../LICENSE).
