# AI-5: Logging-Only Research Hook

English | [简体中文](ai-5-shadow.zh-CN.md)

The target is QuantAIService v2 only; `config/ai-hook-shadow.json` stores runtime variable references with no organization, domain, account, or real secret names. The accepted runner client is `quant_ai_service-2.*`; the older `personal_ai_service` wheel is no longer accepted by the install entrypoint. Existing wheel SHA-256 verification and local accepted artifact requirements stay in place; no replacement package is downloaded, and this does not declare the new client released or installed.

Q18's public research entrypoint still defaults to `q18_summary=None` without altering the calculation path. When explicitly supplied, the summary hook stays fixed at `unavailable`: it does not read the supplied client or call network, order, trading, deployment, config-mutation, or funding interfaces. Old submit, poll, and summary checkpoint paths were removed; corresponding submit/resume tests were replaced by tests verifying no external object access, no AST client calls, and unchanged research outputs. This tightening enforces AI-5 shadow mode with no bypass fallback branch.

The three research workflows no longer fall back to `default` for a missing profile. Existing enablement conditions remain off; this change does not run research AI, create tasks, or alter research conclusions or admission status.

The seven-day schedule, run log template, and daily checkpoints live in the service repository (`docs/AI-5-Q04-SHADOW.zh-CN.md`). Until organization variable values and permissions are verified, only report that the source configuration contract is ready—do not claim the target is connected. Report `"shadow in progress"` before seven days complete; formal enablement requires a separate user decision.
