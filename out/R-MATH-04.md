# R-MATH-04：失败、依赖与累计族登记结构

状态：本地登记结构已实现并通过相关检查；非作者独审 PASS，PR 发布准备中。完整累计来源与可比族认证为 **NEED_USER**。

## NEED_USER

R-MATH-03 的局部原生数据不能回答下列问题，需研究 owner 提供事实或裁定；本卡保留未知，不据名称补造：

1. 全部相关 store/backend/prefix、组织级历史截止点、旧 48 汇总对应的原生 ID/执行状态及遗漏声明。
2. M1 三个不同 ID 的调用/恢复/修复关系、哪些结果曾影响选型；B5 修复及 QQQ A/B 敏感性的原生父子和选择历史；可信事件时间及首次故障证据。
3. 每项选择决定的候选/控制集合、纳入理由、同窗/日历/净 1x 收益/费用/rf/前缀口径、有效独立数方法，以及实际 consumer/build 与真实数据准入。

这些缺口影响累计族认证及 DSR，不阻断本轮只读登记结构。未运行新 trial；M2/协议保持原样。

## 结果与改动

实现位于独立 worktree `/Users/lisiyi/Projects/_worktrees/ues-r-math-04-20261023`，分支 `codex/r-math-04-registration-20261023`，核实后的 origin/main 基线 `da72c6ef142829c4f94a6f3e4e51a23a1a37d303`。原工作目录的既有 dirty 文件没有覆盖。

| 文件 | 用途 |
|---|---|
| `src/us_equity_strategies/research/trial_family_registry.py` | 标准库只读投影、精确原生身份/状态和依赖绑定、内部及原生源校验、显式 CLI |
| `docs/research/trial_registry/r_math_04.json` | 七个显式本地 store 的现存元数据及字节摘要；九个槽、18 份账本，没有新 trial 结果 |
| `docs/research/trial_registry/README.md` | 字段语义、未知边界、生成与校验方法 |
| `tests/research/test_trial_family_registry.py` | 失败/未知/terminal-only、ledger-only、副本/冲突、同指纹不同 ID、漏项及伪造统计主张等元数据测试 |
| `out/R-MATH-04.md`、`out/R-MATH-04-evidence/` | 本报告、38 原生文件/M2 摘要读回和独审/交付证据 |

R-MATH-03 来源为 `/Users/lisiyi/Projects/UsEquityStrategies/out/R-MATH-03.md` §2–6，尤其 A01–A06；`out/R-MATH-03-evidence/native-audit.json` 仅用于定位 store 和旧字节基线，本轮重新直接只读原生文件生成投影，不把旧报告当权威登记。

| R-MATH-03 原生 trial_id | 原生状态 | 登记处理 |
|---|---|---|
| `r0-r2-r3-core-only-d5-20261020-01` | rejected | 保留原拒绝及 reason，无成功路径 |
| `r0-r2-r3-core-only-d5-repair-20261020-02` | succeeded | 1 加总审计账 + 6 依赖子账；不据名称补父 trial |
| `r0-r2-r3-qqq-pair-ab-20261020-01-A-double` | aborted | 保留语义中止及原生依赖假设标签，选择用途未知 |
| `r0-r2-r3-qqq-pair-ab-20261020-01-B-single` | aborted | 同上；不填零收益 |
| `r1-no-plugin-mechanism-d5-20261021-01` | rejected | 旧拒绝保留，首次故障证据未知 |
| `m1-r1-prefee-reserve-v1-d5-20261021-01` | started | UNKNOWN_TERMINAL；不改记失败或未运行 |
| `m1-r1-prefee-reserve-v1-d5-20261021-02` | succeeded | 与上一槽同 source/input/参数指纹仍保留独立 ID；1+2 份账本 |
| `m1-r1-prefee-reserve-v1-20261022-01` | succeeded | 与上项指纹不同不去重；1+2 份账本；选择/修复关系未知 |
| `fixed-f100-f025-common-reserve-d5-20261022-01` | succeeded | 1 加总审计账 + 4 依赖子账 |

当前实读成功 4、拒绝 2、中止 2、STARTED 1；没有发现 failed terminal 不等于历史没有失败。四个已见批次内依赖组有精确父槽及子账路径；其独立数不估计。全局父子/修复/敏感性关系、选型与时间缺证处为 null。member 列表包含全部九个局部身份，绝非已冻结的可比金融族。累计 raw attempts、financial selection candidates、effective independent K 分别保留 null。

A01–A04 已实现可承载来源、状态、身份、依赖与族边界的结构和局部只读对账；组织级完整来源/选择事实仍需补证。A05–A06 保留统计适用性、同口径收益/rf/依赖方法及 consumer/build/真实准入字段，不计算或认证缺失内容。793 历史评价、855 账本 days、252 未来会话不合并；旧 48 声明不加到九槽上。

DSR 仍为 `value=null / UNCOMPUTABLE / COMPLETE_CUMULATIVE_TRIAL_SOURCE_NOT_VERIFIED`。没有回测、拟合、bootstrap、收益/Sharpe/PBO/DSR 计算，没有运行新 trial、补历史终态或写原生 store。未改 M2/协议、QPK pin 或既有统计 consumer。

## 实际验证与限制

| 命令 / 检查 | 退出码及结果 | 范围 |
|---|---|---|
| `git fetch origin main` | 0 | 核对 PR 基线，没有从旧 detached HEAD 发补丁 |
| `PYTHONPATH=src python3 -m pytest -q tests/research/test_trial_family_registry.py` | 0；23 passed | 仅元数据 fixture 与登记投影校验；不是 trial |
| `python3 -m ruff check src/us_equity_strategies/research/trial_family_registry.py tests/research/test_trial_family_registry.py` | 0；All checks passed | 相关 Python；仓库已有顶层 lint 配置 deprecation warning 保留 |
| `PYTHONPATH=src python3 -m us_equity_strategies.research.trial_family_registry --validate docs/research/trial_registry/r_math_04.json --verify-sources` | 0；9 身份/18 账本 | 七 store 全量已列 started/terminal/ledger 的当前字节、元数据与投影一致 |
| `PYTHONPATH=src python3 -m pytest -q tests/test_research_import_boundary.py` | 0；2 passed | AST + 子进程 import，运行时没有导入研究模块 |
| Python 标准库 SHA-256 只读比对 R-MATH-03 `artifacts` 和 `p-protocol` 来源 | 0；38 原生文件 + 1 协议同字节 | `R-MATH-04-evidence/native-readback.json`，未重新计算逐日会计 |
| `git diff --check` | 0 | 最终暂存差异通过（测试 EOF 空白行提示已修正） |

本机使用 `/usr/local/bin/python3`（3.13）、pytest 9.1.1、Ruff 0.15.22，没有安装/升级依赖。本轮本地没有执行全仓测试或金融研究测试；所增模块仅依赖标准库。远端 CI 状态待 PR 后回读，不能用本地通过代替。

内部校验只证明投影一致性；原生源校验也只覆盖显式七 store，不能发现未提供的 store，不能认证全组织历史。时间链、完整失败列表、同口径 Sharpe 分布和有效数没有补造。未读取账户、凭据或原始行情；ledger JSON 为身份与摘要解析，逐日值不导出、不参与统计。

## Skill 与执行入口

实际读取并使用：

- `plan-and-patch`：`/Users/lisiyi/.codex/skills/plan-and-patch/SKILL.md`
- `verification-before-completion`：`/Users/lisiyi/.codex/skills/verification-before-completion/SKILL.md`
- `requesting-code-review`：`/Users/lisiyi/.codex/skills/requesting-code-review/SKILL.md`
- `using-git-worktrees`：`/Users/lisiyi/.codex/skills/using-git-worktrees/SKILL.md`
- `receiving-code-review`：`/Users/lisiyi/.codex/skills/receiving-code-review/SKILL.md`
- `finishing-a-development-branch`：`/Users/lisiyi/.codex/skills/finishing-a-development-branch/SKILL.md`

Chrome：不适用，未使用。非作者只读独审由 `/root/r_math_04_review` 执行，独审最终 PASS（初审缺陷修正后增量复核）；未将作者自检当独审。TASK_CHECKLIST 仅更新 `/Users/lisiyi/codex-jobs/TASK_CHECKLIST.md` 的 R-MATH-04 一行。

## 独审发现与修正

首次非作者审查没有 PASS：发现跨 store 同身份 ledger 的字节冲突未被拒绝，既有检查仅覆盖 STARTED/terminal。该缺陷也影响 ledger-only 依赖子账。补了父 ledger 和子 ledger 两个元数据反例，修正前运行 `PYTHONPATH=src python3 -m pytest -q tests/research/test_trial_family_registry.py -k cross_store_ledger_conflicts` 为退出码 1、2 failed（DID NOT RAISE）；没有运行金融 trial。

修正为对每个 `(native_identity_key, object_kind)` 比较字节摘要与所投影元数据，覆盖 STARTED/terminal/ledger；冲突返回 `CROSS_STORE_OBJECT_CONFLICT`，同字节副本仍保留位置。修正后相关测试 23 passed、Ruff 和原生源验证均退出码 0。原 reviewer 增量复核已 PASS；初审缺陷不会被改记为首次直接 PASS。登记 JSON 本身无需变化。

独立 reviewer 最终实际执行登记测试 23 passed、真实源校验与相关 Ruff，均退出码 0；受审 code/README/registry SHA 见 `R-MATH-04-evidence/review-final.json`。最终测试文件仅去掉末尾一空行以通过 `git diff --cached --check`，无逻辑变化；原 reviewer 对精确新摘要的适用确认单列保存。
