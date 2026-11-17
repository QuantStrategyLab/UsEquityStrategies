# Strategy Repository Owns Research Tasks

English | [简体中文](task-service-v2.zh-CN.md)

The local migration baseline is UsEquityStrategies `<pinned-commit-sha>`. Changes are not yet committed, released, deployed, or connected to a live assistant.

Research invocation entrypoints now live in this repository:

- `us_equity_strategies.research.soxl_new_research`: frozen sources, SOXL candidate patches, isolated tests, and the existing QPK numerical research pipeline.
- `us_equity_strategies.research.global_etf_review`: advisory review over fixed author materials and fixed candidates without modifying candidates.
- `us_equity_strategies.research.soxl_manual_learning`: manually triggered fixed-parameter research, saved validation, quality tail checks, and paired shadow runs.
- `scripts.soxl_financial_explanation`: deterministic explanation over fixed inputs without calling a model. Kept as a standalone script so preloading package imports does not pollute frozen numerical environments.

General calls use QuantPlatformKit's V2 task client. SOXL parameter boundaries stay in `research/watcher_task.py`; the shared layer keeps only the generic task schema. Historical task schemas, source hashes, frozen numerical code, fees, validation, and manual approval gates retain their original semantics. The current producer identity uses the actual strategy repository without rewriting historical producer records.

The API suits short explanations; the resident assistant suits candidate design and longer reviews. Models, modes, and profiles are specified explicitly in accepted configuration rather than hardcoding providers or VPS routes in callers. Resident results record `model_requested` and `model_verification=unavailable` without claiming platform-verified model execution.

Global ETF's `run-v2.json` binds source, candidate, request, and route; completed results are reused and waiting states only poll the original task. Auto-triggering stops when a submit response is lost without a task ID. SOXL codegen likewise persists task identity. Manual research `--resume-ai-task-id` applies only to the original manual learning request; the client re-validates the original request, and altered materials or routes cannot reuse old results. No waiting state starts numerical research.

Local tests use synthetic fixtures and fake clients; real Docker and frozen-research integration tests did not run locally. Current CI and dependency pins do not yet include the new shared interfaces; new modules require verified QPK and PersonalAIService wheels rather than pulling unpublished packages from a public index. The legacy AIAuditBridge workflow is retired and new owner workflows are in place, without switching production entrypoints yet.

The new Global ETF workflow draft lives at `.github/workflows/global-etf-review.yml` in this repository, gated off by default via `AI_SERVICE_RELEASE_READY`; it keeps the frozen candidate commit, main/manual trigger/single-attempt bounds, and Docker checks. YAML and diff checks passed, cloud execution has not run, and consumers await an updated QPK pin after release. The legacy AAB workflow is retired without cloud cutover yet.

## Owner Workflow Closeout

Drift workflows use this repository's reusable workflow, with model review configurable across three roles; source, snapshot, and financial admission boundaries remain intact. New manual strategy research workflows stay off by default, enabled via `AI_SERVICE_RELEASE_READY` and an approved cloud environment without granting trading or auto-adoption authority. Shared dependencies must reference exact verified sources or artifacts; unpublished commits must not be replaced by nonexistent remote SHAs.
