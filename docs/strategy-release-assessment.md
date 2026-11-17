# Strategy Pre-Release Assessment

English | [简体中文](strategy-release-assessment.zh-CN.md)

`scripts/assess_strategy_release.py` is the shared read-only entrypoint from strategy packages to the QuantPlatformKit release gate. It applies to every strategy, broker target, and plugin combination.

Callers must explicitly provide the candidate release ID, committed source revision, configuration, risk rules, evidence package, full plugin bundle, and all target platforms. The script outputs sanitized diagnostics only: `ready: true` only indicates that a controlled release pipeline may generate a manifest, while `ready: false` forbids reloading the strategy onto any platform.

It does not generate manifests, alter parameters, deploy services, or place orders. Formal platform reload still requires matching release-identity receipts from every target followed by an observation window before entering `ACTIVE`.

SOXL currently lacks a verifiable in-repo promotion evidence package, so paper reload stays blocked with `evidence_package_missing` until reproducible backtests and manual acceptance are completed.
