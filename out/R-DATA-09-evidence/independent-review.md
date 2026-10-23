# R-DATA-09 非作者独立审查

最终结论：**PASS（仅人工 fixture 的 registry/selector 结构草稿）**。初审两项 P2 已由作者修复；独立原样复跑两条缺陷探针均通过。未发现需要阻塞本卡 draft PR 的剩余问题。本结论不授权合并、部署或真实日历接入。

审查人：独立子任务 `/root/r_data_09_independent_review`。审查者未编写或修改产品模块、正式测试或报告正文，只写本审查和 `reviewer-checks*` 证据。作者与审查者分别执行；没有以作者测试摘要代替独立检查。初审完整正文保存在 `independent-review-first.md`，含两项具体发现、复现和初审命令结果。

## 对象与源文件绑定

审查 worktree：`/Users/lisiyi/codex-jobs/worktrees/ues-r-data-09-20261023`；base `aeb200a2ccf65c386f0f8c915a3f4e2748f43ea4`。审查对象是三个新增源码/测试文件及相应 R-DATA-09 报告，不以未跟踪文件的空 `git diff` 冒充无变更。R-DATA-08 实施计划、R-DATA-07 决策节及 B/U1/T1 操作规则已读取；既有 PIT validator、显式 UTC 校验、archive session 接口已核对。

| 最终审查源文件 | SHA256 |
| --- | --- |
| `src/us_equity_strategies/research/calendar_contract.py` | `11a166bcd322d12c3ae2fe134f8c1c10511d55bc6bf7553e289da7a29a913ded` |
| `tests/research/test_calendar_contract.py` | `76c417c6864909237d31da1ca03fb8de5318b945fc397e56c231c741cdd0fac2` |
| `tests/research/test_r_data_08_calendar_t1_contract.py` | `4c475efdb938552b7008e4f7810fdbeb0cf1d1a7e83b9a0cc64b613dcda3fd7d` |

上述 hash 与作者 `verification.json` 独立比对一致；详见 `reviewer-checks-source-final.json`。另记录既有 `pit_session_inputs.py`、`security_netting_runner.py`、`kelly_private_runner.py` 的 SHA256，确认其与初审相同。相对 base 的已有 tracked 文件 diff 为空；新增三个源码/测试文件分别直接读取审查。

## 初审问题的修复复核

1. **同源新字节错误关联旁支：已修复。** 最终 `CalendarRegistry.__post_init__` 要求相同已知 source_ref、scope/year 的旧内容版本实际位于新记录的 supersedes 祖先链，非空旁支链接已不足以通过。同源 A/B 旁支复现现抛出 `CALENDAR_NEW_VERSION_LINK_REQUIRED`。新增正式回归与独立原探针均通过，未靠更改独立反例取消发现。
2. **已排除中间版本进入 selected_ids：已修复。** `selected_ids` 只列实际选中的年度基线和公告 tip；`ancestry_ids` 单独保留纯历史替代引用，不宣称这些祖先在 cutoff 已可知。原 A→晚到 B→早收到 C 的独立复现现在 selected/excluded 无交集；B 仍可作为 ancestry。正式用例分别覆盖祖先晚到与知识未知，报告明确其字段含义。因此无需强制“旧版本文件一定比新版本先收到”，也没有通过移动真实时间满足合同。

## 功能与边界核对

- B：年度基线和明确逐日覆盖；公告只影响适用范围；同身份幂等与冲突拒绝；缺少显式关系的互斥公告、独立年度基线和 supersedes 分叉不会按加载顺序选胜者。相同 source 内容变更要求真实关联其旧版；新基线遇旧关联公告会要求重新关联，避免悄悄丢更正。
- 可知边界：明确晚到记录按旧 cutoff 排除；未知来源/scope/范围/知识阻断可能受影响日期；后来修复追加记录，旧查询不被回填。记录/fixture 声明不构成真实 receipt 认证。
- U1：UNKNOWN、缺覆盖和冲突使相关 next-session/距离返回 UNAVAILABLE/None，不能按周末、邻日或 bars 猜日期；明确闭市可跨越；起点也检查；首个候选之后无关未知不阻塞结果。OPEN/EARLY_CLOSE 缺时刻不能冒用普通收盘。
- T1：实际直接调用现有校验器，包含同 UTC 日正例及跨日、晚到、缺失/非 UTC 时间、digest 冲突反例。日历文档时钟独立，不替代 bars 时钟；原 validator/parser/consumer 没有放宽或接线。
- 准入：registry 明确要求 fixture-only 与人工 scope/owner/许可标识；无真实市场/owner/许可默认值。精确单证券、单场所与其他范围不会自动改成全市场状态。

已阅读 R-DATA-09 报告的修复、验证和限制表述，符合实际新增模块未进入原执行路径、只有人工声明、`PIT=NOT_ATTESTED` 的范围。

## 实际命令与结果

以下最终命令在上述 worktree 执行；真实 argv、环境、UTC 起止、退出码及 stdout 见同名 `.command.json` / `.log`。fixture 测试在导入前禁止 Python socket 审计事件，关闭外部 pytest 插件。

| 命令 | 实际结果 |
| --- | --- |
| `python3 out/R-DATA-09-evidence/run_check.py reviewer-checks-final python3 out/R-DATA-09-evidence/no_socket.py pytest -q -rs --tb=short tests/research/test_calendar_contract.py tests/research/test_r_data_08_calendar_t1_contract.py out/R-DATA-09-evidence/reviewer-checks-probes.py out/R-DATA-09-evidence/reviewer-checks-findings.py --junitxml=out/R-DATA-09-evidence/reviewer-checks-final.xml` | **94 passed，退出 0**；85 项正式测试、7 项独立边界探针、2 项初审缺陷原样回归；0 failure/error/skip。 |
| `python3 out/R-DATA-09-evidence/run_check.py reviewer-checks-final-lint python3 -m ruff check src/us_equity_strategies/research/calendar_contract.py tests/research/test_calendar_contract.py tests/research/test_r_data_08_calendar_t1_contract.py out/R-DATA-09-evidence/reviewer-checks-probes.py out/R-DATA-09-evidence/reviewer-checks-findings.py` | **All checks passed，退出 0**；仅既有 Ruff 顶层 ignore 弃用提示。 |
| `python3 out/R-DATA-09-evidence/run_check.py reviewer-checks-source-verify python3 out/R-DATA-09-evidence/reviewer-checks-source.py` | **PASS_SCOPED_INDEPENDENT，退出 0**；源文件 hash、与作者记录一致性、既有文件未改、JUnit 实际计数核验通过。 |

初审另有独立 82 passed / lint PASS / 7 探针通过和 2 缺陷失败的原始证据，均保留。探针首次调用 helper 重复传 `version_id` 导致 1 项失败，修正审查者探针后 7 项通过，未改产品；不把该审查者错误计为产品缺陷。MEMORY 搜索无相关匹配，未使用历史项目事实。

## Skill 与未验证范围

实际读取 Skill：`requesting-code-review`，路径 `/Users/lisiyi/.codex/skills/requesting-code-review/SKILL.md`。Chrome：不适用。

本审查没有联网、下载/嵌入真实年度 PDF、取得真实市场数据、读取凭据、修改生产配置/Secret/env、部署、合并或 kill 进程。GitHub fetch/push/PR 的发布网络由作者另行记录，不能计入“fixture 测试无网络”结论。没有执行全量产品测试、真实回测、许可核验或模型/pending/fill/账本集成验证。

MKT-01、OWN-01、LIC-01～04 与真实生产接入继续 NEED_USER；本 PASS 不替 owner 决策，不认证官方日历、真实接收、许可或历史 PIT，不关闭数据/PIT/回放第 1 行。
