# US equity portability checklist

Use this checklist before enabling a US equity profile on any runtime.

This document is intentionally shorter than the authoring template. It is a reviewer-facing list for portability sign-off.

## 1. Contract registration

- [ ] canonical profile exists in `catalog.py`
- [ ] `StrategyMetadata` is present
- [ ] `StrategyDefinition` is present
- [ ] `target_mode` is exactly one of `weight` / `value`
- [ ] manifest and entrypoint are present and aligned with the catalog entry

## 2. Canonical inputs only

- [ ] `required_inputs` uses only canonical names:
  - `market_history`
  - `benchmark_history`
  - `portfolio_snapshot`
  - `derived_indicators`
  - `feature_snapshot`
- [ ] no new ad-hoc runtime input name was introduced
- [ ] if `ctx.portfolio` is required, adapters set `portfolio_input_name="portfolio_snapshot"`

## 3. Output portability

- [ ] the profile emits exactly one target mode
- [ ] weight profiles return only `target_weight`
- [ ] value profiles return only `target_value`
- [ ] `StrategyDecision` contains no broker-specific order fields
- [ ] `StrategyDecision.diagnostics` contains strategy diagnostics only
- [ ] structured notification data is available without parsing dashboard text
- [ ] if `execution_annotations` exists, it carries `notification_context`

## 4. Runtime-adapter matrix

Complete this table in the PR or working notes.

| Platform | Adapter present | `available_inputs` aligned | `portfolio_input_name` set when needed | Translation path understood | Notes |
| --- | --- | --- | --- | --- | --- |
| `ibkr` | [ ] | [ ] | [ ] | [ ] | |
| `schwab` | [ ] | [ ] | [ ] | [ ] | |
| `longbridge` | [ ] | [ ] | [ ] | [ ] | |

Translation path means the platform runtime knows how to consume the profile's declared `target_mode` without adding platform branches inside strategy code.

## 5. Artifact-backed profiles

If the profile uses `feature_snapshot`:

- [ ] required feature columns are declared
- [ ] accepted date columns are declared
- [ ] freshness lag rule is declared
- [ ] manifest/checksum requirement is declared when needed
- [ ] contract version is declared when manifest validation is required
- [ ] strategy code does not open files directly
- [ ] platform runtime owns transport and freshness validation

If the profile needs an artifact type other than `feature_snapshot`:

- [ ] `QuantPlatformKit` contract/spec was extended first
- [ ] the new artifact type was not introduced as a one-off local convention

## 6. Forbidden-pattern scan

- [ ] no `if platform_id == ...` logic in strategy code
- [ ] no broker env reads in strategy code
- [ ] no imports from platform runtime repositories
- [ ] no broker-native order payload fields in strategy outputs
- [ ] no service-local artifact path assumptions

## 7. Minimum tests

- [ ] contract test exists
- [ ] adapter test exists
- [ ] portability smoke test exists
- [ ] any artifact schema validation test exists when applicable

Suggested evidence:

- `tests/test_catalog.py`
- `tests/test_entrypoints.py`
- profile-specific regression tests
- runtime smoke fixtures using normalized `StrategyContext`

## 8. Legacy profile-key policy

- [ ] canonical profile keys are used everywhere in source, examples, docs, configs, and manifests
- [ ] removed legacy keys appear only in explicit rejection tests
- [ ] no new alias was added for a retired engineering name
- [ ] migration compatibility, if absolutely needed, is documented as a temporary runtime bridge rather than hidden in catalog aliases

Legacy keys such as `hybrid_growth_income`, `semiconductor_rotation_income`, and `tech_pullback_cash_buffer` should now be treated as retired names. They may remain only in negative tests that prove the old keys are rejected.

## 9. Eligible vs enabled

- [ ] PR notes say whether each platform is only `eligible` or also `enabled`
- [ ] rollout allowlist changes are kept separate from portability logic
- [ ] unsupported platforms are explained explicitly instead of being hidden in strategy code

## 10. Reviewer sign-off summary

Paste a short note like this into the PR before merge:

```text
Profile: my_new_profile
Target mode: weight
Canonical inputs: market_history, benchmark_history, portfolio_snapshot
Adapters: ibkr=yes, schwab=yes, longbridge=no (reason documented)
Artifact contract: none
Tests: contract=yes, adapter=yes, portability smoke=yes
Eligible/enabled note: eligible on ibkr+schwab, enabled on none in this PR
```
