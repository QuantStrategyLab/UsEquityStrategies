# 研究参数域与 schema 验证

R-RISK-02 基线 `b018372bdbdf7e10a863099963e1dd4f5cbde88c`，Python 3.13.7。R-RISK-01 文档来源为 `e259e84d`，新 schema 版本 `r-risk-01.research.v2`；高级模式默认、覆盖、二次确认字段保留。

| 实际命令 / 检查 | 退出码 | 结果与范围 |
|---|---:|---|
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m pytest -q -p no:cacheprovider tests/research/test_bayesian_kelly.py tests/research/test_m1_r1_reserve_trial.py`（修改前） | 0 | 44 passed, 60 subtests passed；旧 .10/.40 拒绝已复现 |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m pytest -q -p no:cacheprovider tests/research/test_bayesian_kelly.py -k 'approved_fractions or invalid_fraction'`（仅新测试，旧内核） | 1 | 11 failed, 2 passed, 38 deselected, 68 subtests passed；.10/.40 拒绝和旧 .50 接受符合预期失败 |
| `PYTHONDONTWRITEBYTECODE=1 UES_REQUIRE_SECURITY_NETTING=1 PYTHONPATH=src:/tmp/r-risk-02-qpk-78401278/src python3 -m pytest -q -p no:cacheprovider tests/research/test_bayesian_kelly.py tests/research/test_m1_r1_reserve_trial.py tests/research/test_rs04_netting_candidate.py -rs` | 0 | 302 passed, 190 subtests passed；无跳过。仅人工输入和临时本地测试 store |
| `PYTHONDONTWRITEBYTECODE=1 UES_REQUIRE_SECURITY_NETTING=1 PYTHONPATH=src:/tmp/r-risk-02-qpk-78401278/src python3 -m pytest -q -p no:cacheprovider tests/research/test_portfolio_kelly_walk_forward.py tests/research/test_r_data_foundation.py -rs`（独审返修后） | 0 | 115 passed，无跳过；跨模块 gross/excess 断言及 R1 前视检查保持 |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 docs/research/risk-levels/verify_design.py` | 0 | 三份 schema、30 参数、示例及三个批准 c 可用；35 个 schema 负例拒绝；六个对照仍 NOT_RUN_BLOCKED，金融指标 null |
| 原版 B4 与当前版纯函数逐项比较（Python，原版取自 `git show b018372b:src/us_equity_strategies/research/bayesian_kelly.py`） | 0 | 160 个 c=.25 人工单/双核心、正/负输入、prior/available、不同 cap/veto 案例的完整 B4Target 精确相等 |
| 原 R-RISK-01 与当前参数表逐字段比较（Python） | 0 | 29 个其他参数完全相等；Kelly 默认值、单位、覆盖和确认字段完全相等 |
| `python3 -m ruff check src/us_equity_strategies/research/bayesian_kelly.py tests/research/test_bayesian_kelly.py docs/research/risk-levels/verify_design.py` | 0 | All checks passed；仅已有 Ruff 配置弃用提示 |
| `git diff --check` | 0 | 无空白错误 |

研究依赖取自 CI 固定 QPK `7840127862ea715e44c8f70b03155a399dc817de` 的 `git archive ... src`，解至 `/tmp/r-risk-02-qpk-78401278`，只以本次进程 PYTHONPATH 使用；未安装/升级共享环境、未修改产品 pin。默认本机依赖下另一次定向检查为 47 passed, 42 skipped, 213 deselected, 130 subtests passed；这些跳过不计通过，随后由上面的严格研究依赖检查覆盖。

接口接受 .10/.25/.40 只证明研究内核参数域，不证明收益、金融资格、生产采用或执行权限。未读市场数据、未运行真实三档回测、未写权威 R4。Grok Bot schema 复核独立于代码审查，当前无调用入口、无发送记录或结论；请求材料在 review-request.zh-CN.md。完整报告、PR、独审与合并证据由本机 `out/R-RISK-02.md` 记录。

初次独审在 `2d61ddef` 发现 `test_portfolio_kelly_walk_forward.py` 仍以 `.50` 调 B4，单项复现 exit1 / 1 failed。仅将该研究测试循环改为 `.10/.25/.40`，保留原现金/gross/excess断言；不恢复非法准入、不改 solver。最终独审结论与旧失败分别留存于本机独审报告。

R-RISK-01 原始执行记录保留在本机 `out/R-RISK-01.md` 和 PR #658；本文件列当前复核，不改写原始失败或旧验证结论。
