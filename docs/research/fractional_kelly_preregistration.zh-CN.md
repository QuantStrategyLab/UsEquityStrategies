# RS-04 分数 Kelly 评估器：SOXL / TQQQ 预注册（v1）

状态：**参数已冻结，数据源与窗口待用户确认**。代码副本：`us_equity_strategies.research.fractional_kelly_preregistration`。
用户 2026-10-11 批准的候选只有当前两条实盘策略 SOXL 与 TQQQ；其他任何标的仍然 `REAL_DATA_REQUIRES_APPROVAL`。
本文件与本次 PR **没有在真实数据上运行**，也不包含任何真实回测数字。

## 1. 对应的实盘配置（只读核对，未修改）

| 键 | 实盘 profile | 运行位置（2026-10-10/11 只读核对） | 实盘参数来源 |
| --- | --- | --- | --- |
| SOXL | `soxl_soxx_trend_income` | Schwab live（`RUNTIME_TARGET_JSON`）、IBKR `live-u15998061`；LongBridge 的环境变量本次无权读取，未核实 | UES manifest 默认值；平台 pin UES `9d1544d1`，与当前 main 的 manifest/策略文件无差异；目标 JSON 无参数覆盖 |
| TQQQ | `tqqq_growth_income` | IBKR `live-u16608560` | 同上 |

注意：
- **V7**（`soxl_soxx_core_only_p2_v7_longterm_compounding_cash_reserve`，config SHA-256 `843ab4e9…`）是 `runtime_enabled=false` 的研究/影子候选，**不是**当前实盘 SOXL。两者核心参数不同（实盘 SOXL 权重 0.70 / mid 0.65 / active SOXX 0.20 / 降杠杆转入 SOXX，V7 为 0.35 / 0.25 / 0.25 / 转入 BOXX；实盘还开启收入层、市场状态控制、期权收入层开关）。既有真实数据研究（R6/R7/R8/R9）中的 SOXL 用的是 V7，不能当作实盘 SOXL 的收益序列。
- 实盘收入层门槛 SOXL 150,000 USD、TQQQ 250,000 USD；期权层 `live_status=research_only`，实盘不下期权单。
- 实盘调度 `45 15 * * 1-5`（美东），即收盘前约 15 分钟用当日未完结行情决策并成交。IBKR TQQQ 要求信号日当天的 bar 存在。这与研究里的 `next_close` 不同，属于已登记问题 UES #575。

## 2. 冻结参数（两条策略相同，除注明外）

| 项 | 值 |
| --- | --- |
| 预注册 id | SOXL：`rs04-kelly-soxl-v1`；TQQQ：`rs04-kelly-tqqq-v1` |
| 前推方案 | 训练 252、测试 63、purge 5 个会话；滚动（不锚定） |
| c | {0.25, 0.5}；c ≥ 1 拒绝；暴露 0–1，不加杠杆、不做空 |
| 费用 | 基础单边 5 bps（交易名义额），档位 1x/2x/3x（= 5/10/15 bps） |
| 信号截止 | `completed_regular_session_close_t`（t 日常规收盘后已知数据） |
| 成交 | `next_close`（t+1 收盘）；同日收盘只可作研究敏感性，不作主结果 |
| 日历 | `XNYS` |
| 基准 | SOXL → SOXX；TQQQ → QQQ（manifest 的 `benchmark_symbol`） |
| 现金腿 | 同一数据源的 BOXX 总收益（分红按除息日应计）；如不可得须改预注册，不能静默按 0 |
| 策略收益生成 | SOXL：`backtest.soxl_trend_simulator.run_soxl_core_only_backtest`，用实盘 manifest 默认核心参数（收入层、市场状态、期权关闭——与实盘的差异须在报告 limitations 写明）；TQQQ：需新建同口径核心回放（见 §4 缺口） |
| 数据身份 | 全部已见历史为 `development`；不得报告为 OOS |
| R8 对照 | R8 v2 是 TQQQ+SOXL 联合账户政策，单策略不可拆分；本预注册默认**不提供** R8 路径（结果标 `R8_PATH_NOT_SUPPLIED`），如需引用须另行确认口径 |
| 门槛 | `NO_BUDGET_INCREASE`、`DEVELOPMENT_ONLY`；SOXL 另有 `SOXL_252_SHADOW_SESSIONS_STILL_REQUIRED` |

## 3. 数据清单必填字段

`RealDataAuthorization(strategy_key, preregistration_id, DataManifest(sha256, source, license, calendar, start_date, end_date, observations))`：
- `sha256`：所消费逐会话文件的 64 位小写 SHA-256；
- `source`、`calendar`、`start_date`/`end_date` 必须与预注册**完全一致**；`observations` 必须等于输入会话数；
- `report_context` 必须给出相同的 `manifest_sha256`/`source`/`license` 以及预注册的信号截止与成交时点；
- 预注册的 `data_source`/`window_*` 仍为空时，一律 `PREREGISTRATION_PENDING_DATA_CONFIRMATION`。填写它们需要用户确认后单独审阅的 PR。

## 4. 待用户确认的数据源与窗口（提议）

| 键 | 提议数据源 | 提议窗口 | 说明 |
| --- | --- | --- | --- |
| SOXL | 既有私有 Alpaca SIP 原始价 + 公司行动归档（user_attested 非商业研究许可，raw manifest `cb14a511…`），信号用同源复权价 | 2023-03-28 至 2026-08-25（R9 已用 856 会话；之前需 ≥260 会话预热） | 按 252/63/5 约 9 个测试折；全为 development |
| TQQQ | 同上 | 同上 | 同上 |
| 备选 | 新取更长历史（例如 Alpaca 2016 起），以增加折数 | 2016 起 | 需单独批准取数与许可核对；Yahoo 复权快照完整性/许可未核实，不建议作主源 |

## 5. 已知缺口

1. TQQQ 实盘口径的真实数据回放生成器不存在：`optimized_strategy_replay` 仅限 fixture，`tqqq_typed_baseline_result` 关闭所有控制且费用为 0。
2. SOXL 核心模拟器只输出扣费后日收益，不输出逐日换手；需要研究适配器以 0 费用运行并记录逐日交易名义额/NAV，供成本档位压力。
3. 市场状态控制（实盘开启）需要历史逐日市场状态输入，核心模拟器关闭它；与实盘不一致。
4. 私有 raw/R6 归档的云端引用、覆盖到 2026-08-25 的 SOXL/SOXX 完整性、BOXX 分红 `process_date` 可知性需在运行前读回确认。
5. 实盘 15:45 成交与研究 `next_close` 的差异（UES #575）。
