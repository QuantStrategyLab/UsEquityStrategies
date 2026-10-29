# Research and contract index

[English README](../../README.md) · [中文 README](../../README.zh-CN.md)

## Signal input contracts

- [Signal-source repository design: English overview](generic-signal-source-repository-design-overview.md) · [complete Chinese design](generic-signal-source-repository-design.md). Dated design and ownership boundaries; not a deployment record.
- [Market Signal Bundle: English overview](market-signal-bundle-artifact-contract-overview.md) · [complete contract and CLI examples](market-signal-bundle-artifact-contract.md). Consumer validation and runtime/research handoff rules.

## Research plans

- [Smart DCA: English overview](smart-dca-indicator-research-plan-overview.md) · [complete research plan](smart-dca-indicator-research-plan.md). Baselines, candidate rules, evidence and promotion review remain separate from live enablement.
- [Index LEAPS overlay](index-leaps-growth-overlay.md) ([中文](index-leaps-growth-overlay.zh-CN.md)). Research-only; real option-chain validation remains required before live intent.

## Evidence boundaries and corrections

- [Smart DCA mathematics correction](smart-dca-math-corrections-20260929.md) and [historical decision summary](smart-dca-decision-summary-2026-06-19.md): the three pre-correction matrices were not rerun; their historical metrics and pass labels do not establish current economics or promotion.
- [SOXL static-selection qualification](soxl-v1-static-selection-qualification.md): the affected SMA and relative-volatility branches are retrospective, not causal walk-forward. The distinct RSI2 research adapter is not a runtime strategy or promotion proof.
- [Global ETF research evidence](../evidence/global_etf_rotation/README.md): the current research package remains `rework` with missing point-in-time and complete trial/cost evidence, independently of its runtime catalog status.
- [Trial registry](trial_registry/README.md), including [r_math_04](trial_registry/r_math_04.json): retain failed and rejected attempts; its DSR is uncomputable without a verified complete cumulative trial source.

Catalog and allowlist membership preserve an interface and do not attest current deployment, economic effectiveness, independent out-of-sample performance or trading authority. Missing evidence is not proof of loss.

Global ETF Rotation and its aliases are no longer in the runtime selection
allowlist. Its historical catalog and research artifacts remain available.

## Historical runtime reference

- [US equity runtime archive](../us-equity-runtime-archive.md) ([中文](../us-equity-runtime-archive.zh-CN.md)). Retains the profile/evidence snapshot dated 2026-06-28. Its performance rows and runtime labels are historical context, not a current deployment or live-eligibility readback.

The English overviews summarize the linked complete documents; they do not replace their field definitions, examples, risk controls or data-license requirements. Research notes do not authorize trading. See the repository [license](../../LICENSE).
