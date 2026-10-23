# R-DATA-09：日历 B/U1 registry/selector 结构草稿

2026-10-23（Asia/Singapore）。结构草稿已实现，非作者独立审查 PASS；正在按授权创建 draft PR（不合并、不部署）。仅人工 fixture，`PIT=NOT_ATTESTED`；真实日历、可信接收、许可及业务接入未验证。MKT-01、OWN-01、LIC-01～04 仍 NEED_USER，数据/PIT/回放第 1 行保持开放。

## 实际范围与文件

独立 worktree：`/Users/lisiyi/codex-jobs/worktrees/ues-r-data-09-20261023`；分支 `codex/r-data-09-calendar-draft-20261023`；基线 `aeb200a2ccf65c386f0f8c915a3f4e2748f43ea4`。本卡 fetch 的 `origin/main` 与 R-DATA-08 源码基线相同。原目录的研究文档、build/out 和其他 worktree 均保留。

已读 R-DATA-08、R-DATA-07 决策节、B/U1 24项草案、既有 PIT validator、registered parser/consumer、archive session 交并检查及 foundation/04/05/06 测试接口。原接口没有年度版本查询，故只新增模块和测试，不接原有路径。输入 hash 见 [inputs.json](R-DATA-09-evidence/inputs.json)。

| 文件 | 用途 |
| --- | --- |
| `src/us_equity_strategies/research/calendar_contract.py` | 纯本地不可变记录/registry、validation、按显式 cutoff 选择日期、U1 next-session/交易日距离。只接受显式 `fixture_only=True` 和 `fixture:` 范围、owner、来源及权利声明。 |
| `tests/research/test_calendar_contract.py` | 人工日期、时刻、文档标识、内容 hash 与权利声明，覆盖基线、更正、未知、冲突、版本及拒绝默认值。没有实际 PDF、真实市场清单或第三方数据。 |
| `tests/research/test_r_data_08_calendar_t1_contract.py` | 从 R-DATA-08 本机候选携带的16项既有 T1 合同测试，直接调用原 validator；只更新说明，不改变测试行为。 |
| `out/R-DATA-09.md`、`out/R-DATA-09-evidence/` | 本报告、命令/退出码、JUnit、输入 hash、矩阵与独立审查证据。 |

无新增生产依赖；未修改已有 parser、consumer、策略、schema、workflow、生产配置、Secret 或 env。`run_check.py` 只为本地验证子进程设置 `PYTHONPATH`、禁用 pytest 插件和字节码写入，不修改环境配置文件。

## 草稿行为

- 年度记录显式登记 scope/segment/timezone/kind、年度与覆盖/生效范围、source/title/revision/hash、发布/接收/持久化时刻、权利引用/版本/有效期及核对/原因引用。记录不可变，追加返回新的 registry；同身份同记录幂等，同身份内容或元信息变化拒绝；构造时重复版本也拒绝。同来源字节变化需要新版本及 supersedes。
- 公告关联年度基线。适用且按 cutoff 可知的公告更正基线；无替代关系的多个公告返回 UNKNOWN，不按加载或接收顺序选胜者。显式 supersedes 保留祖先链。同来源内容变化必须在祖先链关联其实际旧版，不能指向旁支冒充更正。`selected_ids` 只列实际选中的基线/公告 tip；`ancestry_ids` 仅保留历史替代引用，不声称这些祖先在 cutoff 已可知。新年度版本遇到旧版关联公告时停用并要求显式重新关联，避免悄悄丢掉更正。
- 确认晚于 cutoff 的候选列入 excluded；范围、来源、scope 或可知时间未知则停用可能受影响的查询。年度覆盖标签不能证明未列明日期，跨年缺基线也 UNKNOWN。许可有效期仅检查人工声明的区间，不验证真实许可。
- OPEN/EARLY_CLOSE 必须有明确开/收盘时刻；CLOSED/TEMP_CLOSED 不产生 session；UNKNOWN 的 `is_session=None`。单证券、单场所和其他市场由精确 scope 隔离，不能改成全市场闭市。
- `resolve_day(scope, day, cutoff)` 所有查询参数显式；`next_session(scope, after, through, cutoff)` 是有界搜索，返回首个已知 session 前不得跳过 UNKNOWN；`session_distance(scope, start, end, cutoff)` 统计 `(start,end]`，同样检查起点和区间依赖。UNAVAILABLE 的 value 为 None，附 blocked_dates 和逐日 resolution。候选之后的无关 UNKNOWN 不阻塞已找到结果。
- 后来恢复只增加新版本，旧 cutoff 不被回填。日历文档时钟可以早于 session 或跨 UTC 日；它们不能代替 bars 的 event_at。T1 仍由原 `pit_session_inputs.py` 执行同 UTC 日、时间顺序、cutoff 和内容绑定，未放宽。

这只是基于调用者声明的结构选择器；没有可信 receipt、真实历史版本全集、官方日历、当地日期/DST 到 session 的可信映射或许可认证。`fixture:` 是草稿隔离标记，不能用它证明字段真实。没有 consumer 接线，因此也未声称已验证模型、pending/fill 或账本的集成阻断。

## 验证与独立审查

最终命令、真实退出码和输出保存在证据目录。测试入口 `no_socket.py` 在导入产品/测试前拒绝 Python socket 审计事件；fixture 测试无网络、无真实数据。GitHub fetch，以及后续 push/PR 是另行记录的发布网络操作，不纳入“测试无网络”的声明。

| 命令 | 当前结果 |
| --- | --- |
| `python3 out/R-DATA-09-evidence/run_check.py focused python3 out/R-DATA-09-evidence/no_socket.py pytest -q -rs --tb=short tests/research/test_calendar_contract.py tests/research/test_r_data_08_calendar_t1_contract.py --junitxml=out/R-DATA-09-evidence/focused.xml` | 85 passed，退出0，无 skip。 |
| `python3 out/R-DATA-09-evidence/run_check.py lint python3 -m ruff check src/us_equity_strategies/research/calendar_contract.py tests/research/test_calendar_contract.py tests/research/test_r_data_08_calendar_t1_contract.py` | PASS，退出0。 |
| `python3 out/R-DATA-09-evidence/run_check.py lint-all python3 -m ruff check .` | PASS，退出0；仅既有 Ruff 顶层配置弃用提示。 |

R-DATA-08 的24项 B/U1 草案对应关系见 [test-matrix.md](R-DATA-09-evidence/test-matrix.md)，本卡新增反例也直接调用新模块。最初 red 运行因模块尚不存在收集退出2；扩展阶段77 passed/1 failed，暴露了不必要的跨文档接收先后限制，已删除该假设并复测。相关初始日志保留，不把失败算成通过。交付辅助脚本首次全仓 lint 报 E731，已改为普通函数并保留失败输出。

非作者独立复核 PASS：85项正式测试 + 7项独立边界探针 + 2项原缺陷回归，共94 passed、退出0，无skip；Ruff与最终源码hash核验也退出0。实际命令和结果见 [独立审查](R-DATA-09-evidence/independent-review.md)、`reviewer-checks-final.*`、`reviewer-checks-source-final.json`。

非作者审查由独立子智能体 `/root/r_data_09_independent_review` 执行；不由作者冒充独审。审查记录与源文件 hash 保存到 `R-DATA-09-evidence/independent-review.md`。初审发现两项 P2：同源更正可误指旁支、selected/excluded 中间版本重叠。作者已修正并新增3项回归；初审记录保存在 independent-review-first.md。两项修复已获独立复核 PASS；审查源码 SHA256 与最终验证记录相同。

`verify_scope.py` 退出0：基线549个已有文件均未改，新增仅限本模块/测试与本卡 out/，中央清单第1行与任务开始时相同。

没有跑全量产品测试、真实回测或数据验证；新模块未进入现有执行路径。远端 CI 与本地人工测试分别报告，不互相代替。

## NEED_USER 与交付边界

发现时已追加中央 `/Users/lisiyi/codex-jobs/grokbot/NEED_USER.md` 的 R-DATA-09 节，见 [need-user-append.md](R-DATA-09-evidence/need-user-append.md)。

| 依赖 | 当前状态 |
| --- | --- |
| MKT-01 | 精确市场/执行场所及资产映射、时段、年度/日期范围仍待 owner；无默认市场。 |
| OWN-01 | 基线/公告维护、复核、冲突裁定、频率/响应和替补仍待 owner；无默认维护人。 |
| LIC-01～04 | 适用 PDF/公告许可来源、版本/有效期，以及获取、保存、派生、共享/终止限制仍待 owner；无默认许可。 |
| 生产接入 | 本卡不接线；后续如需接入，须明确 consumer 范围、兼容迁移和相应执行授权。 |

这些依赖阻塞真实接入，不阻塞本卡人工结构草稿。FX 保持暂停，不重选 B/U1/T1，不关闭第1行。不 auto-merge、不合并、不部署，不 kill 进程。

实际读取的 Skills：

- `using-git-worktrees`：`/Users/lisiyi/.codex/skills/using-git-worktrees/SKILL.md`
- `plan-and-patch`：`/Users/lisiyi/.codex/skills/plan-and-patch/SKILL.md`
- `requesting-code-review`：`/Users/lisiyi/.codex/skills/requesting-code-review/SKILL.md`
- `verification-before-completion`：`/Users/lisiyi/.codex/skills/verification-before-completion/SKILL.md`
- `test-driven-development`：`/Users/lisiyi/.codex/skills/test-driven-development/SKILL.md`

Chrome：不适用。报告与证据将镜像至原目录指定 out/；中央清单只更新本卡一行，其他行保留。
