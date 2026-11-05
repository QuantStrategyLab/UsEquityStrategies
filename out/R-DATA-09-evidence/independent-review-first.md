# R-DATA-09 非作者独立审查

初审结论：**CHANGES_REQUIRED**。发现两项可复现的 P2 结构缺陷；修复前不满足用户要求的独立审查 PASS，不能据本审查创建 draft PR。

审查人：独立子任务 `/root/r_data_09_independent_review`。审查者未编写或修改产品模块与正式测试，仅写本审查及 `reviewer-checks*` 证据。代码作者与审查者分别执行；没有使用作者的测试摘要代替独立检查。

审查位置：`/Users/lisiyi/codex-jobs/worktrees/ues-r-data-09-20261023`；base `aeb200a2ccf65c386f0f8c915a3f4e2748f43ea4`。审查对象是三个新增未跟踪源码/测试文件，不能以空的 `git diff` 代替本次变更。初审源文件 SHA256 逐项保存在 `reviewer-checks-source-initial.json`。其中产品模块 SHA256 为 `9a55d7ea7b5fd6bfd1694f99c4bcbde66e308a329511f367c966174380d9c673`。

## 发现

1. **P2：同一 source_ref 内容变更可错误关联旁支。**

   位置：`src/us_equity_strategies/research/calendar_contract.py:195-204`。登记 A 源年度基线和独立 B 源基线后，将 A 新字节版本的 `supersedes` 指向 B，登记仍成功。当前代码只检查有非空 `supersedes`，未证明该链关联 A 的旧版。独立复现：`reviewer-checks-findings.py::test_changed_same_source_must_link_its_actual_prior_version`，预期拒绝，实际 `DID NOT RAISE ValueError`。

   影响：违反 R-DATA-08 “相同引用内容变更必须新 version_id、关联 supersedes”的结构要求，登记了错误的版本血缘。这个复现中 selector 仍会留下另一年度候选冲突，未观察到由此错误返回 OPEN；不夸大成运行保护已绕过。

   最小建议：对已知相同 `source_ref`、scope/year 的字节更正，要求 `supersedes` 祖先链实际包含该来源已有版本；错误关联旁支应拒绝，补回归用例。若另有合法跨来源裁定关系，不能因此替代同源旧版的关联。

2. **P2：cutoff 后的中间版本同时被排除和列入已选链。**

   位置：`src/us_equity_strategies/research/calendar_contract.py:248-251,266-277`，以及公告对应 `:289-290`。A→B→C 的 supersedes 链中，B 的发布/接收/保存均在 cutoff 后，C 声明在 cutoff 前；登记接受这条关系。查询返回 OPEN，B 同时存在于 `selected_ids` 和 `excluded_ids`。独立复现：`reviewer-checks-findings.py::test_selected_chain_must_not_include_a_cutoff_excluded_intermediate`。

   影响：返回的“按 cutoff 已选链”包含明确当时未知的节点，审计字段互相矛盾。`PIT=NOT_ATTESTED` 不能消除本地 selector 自身的可知边界错误；这不证明真实历史 PIT 已有问题，因为本卡没有真实数据和接线。

   最小建议：链校验/选择中处理晚到和知识未知的祖先，不把它们计入当时可知的 selected 链。可保守拒绝不一致的版本关系或返回 UNKNOWN；若保留纯 ancestry，须与按 cutoff 已选依赖明确区分并测试。正式声明的文档发布时间、接收与保存时刻均不得人为移动以通过。

## 已核对范围

读取 R-DATA-08 实施计划、R-DATA-07 决策节及 B/U1/T1 操作规则；读取实际新增模块和两份测试，并核对原有 `pit_session_inputs.py`、`security_netting_runner._rs04_time`、`kelly_private_runner._sessions` 接口。没有访问报告中外部网页或官方 PDF。

年度覆盖、逐日显式证据、公告覆盖、冲突不按加载顺序选择、未知来源/范围/知识导致 UNKNOWN、已证明晚到的公告按旧 cutoff 排除、U1 阻断、明确闭市可跨越、缺默认市场/owner/许可拒绝、fixture-only 准入均有对应源码和人工测试。T1 测试直接调用现有校验器；原有 parser/consumer/配置代码未被修改，未增加生产接线。上述两项发现针对新增 registry/selector 的结构；不以可选风格调整阻塞。

## 实际验证

以下均在审查 worktree 执行，命令、UTC 起止、退出码和 stdout 见对应 `.command.json` / `.log`：

| 命令 | 实际结果 |
| --- | --- |
| `python3 out/R-DATA-09-evidence/run_check.py reviewer-checks-focused python3 out/R-DATA-09-evidence/no_socket.py pytest -q -rs --tb=short tests/research/test_calendar_contract.py tests/research/test_r_data_08_calendar_t1_contract.py --junitxml=out/R-DATA-09-evidence/reviewer-checks-focused.xml` | 82 passed，退出 0，无 skip；导入前禁止 socket，关闭外部 pytest 插件。 |
| `python3 out/R-DATA-09-evidence/run_check.py reviewer-checks-probes-final python3 out/R-DATA-09-evidence/no_socket.py pytest -q -rs --tb=short out/R-DATA-09-evidence/reviewer-checks-probes.py --junitxml=out/R-DATA-09-evidence/reviewer-checks-probes-final.xml` | 7 passed，退出 0；补测独立基线和分叉替代冲突、范围全未知、互不重叠公告、晚到修复保持旧 cutoff。 |
| `python3 out/R-DATA-09-evidence/run_check.py reviewer-checks-findings python3 out/R-DATA-09-evidence/no_socket.py pytest -q -rs --tb=short out/R-DATA-09-evidence/reviewer-checks-findings.py --junitxml=out/R-DATA-09-evidence/reviewer-checks-findings.xml` | 2 failed，退出 1；分别证实上述两项缺陷。 |
| `python3 out/R-DATA-09-evidence/run_check.py reviewer-checks-lint python3 -m ruff check src/us_equity_strategies/research/calendar_contract.py tests/research/test_calendar_contract.py tests/research/test_r_data_08_calendar_t1_contract.py` | All checks passed，退出 0；仅项目已有 Ruff 顶层 ignore 弃用提示。 |

独立探针首次执行 `reviewer-checks-probes` 为 6 passed / 1 failed，退出 1，原因是审查者调用 fixture helper 时重复传入 `version_id`。修正审查者探针后上述 7 项通过；未改产品代码，首次日志保留。这次错误不是产品缺陷。无匹配的 MEMORY 查询退出 1，未使用历史项目事实；worktree 无 AGENTS.md，父目录 Projects 入口已读，仅用于确认适用范围。

Skill 实际读取：`requesting-code-review`，`/Users/lisiyi/.codex/skills/requesting-code-review/SKILL.md`。Chrome：不适用。

本审查仅人工 fixture / 本地合同检查。没有联网、下载或嵌入真实日历/PDF、读取真实市场数据或凭据、修改生产配置/Secret/env、部署、合并或 kill 进程。MKT-01、OWN-01、LIC-01～04 的真实决策保持 NEED_USER；本审查不认证官方日历、许可、真实 receipt 或 PIT，也不关闭主清单第 1 行。
