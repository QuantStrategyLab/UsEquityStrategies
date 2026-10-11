# RS-04 分数 Kelly 评估器：SOXL / TQQQ 预注册（v1）

状态：**参数已冻结；数据源与窗口已于 2026-10-11 经用户确认，只允许对照变体 `no_plugin_core` 运行**（主变体 `live_plugins_as_live` 仍被插件阻断）。代码副本：`us_equity_strategies.research.fractional_kelly_preregistration`。
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
| 策略收益路径（两个预注册变体） | **主变体 `live_plugins_as_live`**：所有插件/叠加层与实盘完全一致（见 §2.1）；**对照变体 `no_plugin_core`**：只保留核心规则，插件全部关闭（SOXL 可用 `backtest.soxl_trend_simulator.run_soxl_core_only_backtest`；TQQQ 需新建）。只允许这两个变体，不允许挑选部分插件 |
| 数据身份 | 全部已见历史为 `development`；不得报告为 OOS |
| R8 对照 | R8 v2 是 TQQQ+SOXL 联合账户政策，单策略不可拆分；本预注册默认**不提供** R8 路径（结果标 `R8_PATH_NOT_SUPPLIED`），如需引用须另行确认口径 |
| 门槛 | `NO_BUDGET_INCREASE`、`DEVELOPMENT_ONLY`；SOXL 另有 `SOXL_252_SHADOW_SESSIONS_STILL_REQUIRED` |

### 2.1 实盘启用的插件/叠加层（只读核对）

配置来源：UES manifest `default_config`（平台 pin UES `9d1544d1`）、QRS `platform-config.json`、Schwab 仓库变量 `SCHWAB_STRATEGY_PLUGIN_MOUNTS_JSON` / 预留现金 / 仅现金变量、IBKR `CLOUD_RUN_SERVICE_TARGETS_JSON`（2026-10-10 快照）中的 `strategy_release` 与 `runtime_risk_limits`。

可复现性：YES = 研究路径已能复现；NEEDS_ADAPTER = 规则是确定的，但现有研究路径还没实现；NO = 需要不存在或未核实的历史输入；OFF_IN_LIVE = 有配置但实盘没有仓位影响，预注册按关闭处理。

**SOXL（`soxl_soxx_trend_income`）**

| 插件 | 实盘状态 | 可复现 | 说明 |
| --- | --- | --- | --- |
| `blend_gate_volatility_delever` | 开（滚动分位，转入 SOXX） | YES | 核心规则，核心模拟器已包含 |
| `blend_gate_rsi_bollinger_caps` | 开（动态 RSI 70、布林上限） | YES | 同上 |
| `market_regime_control` | 配置开（apply_risk_off=true，apply_risk_reduced=false）；外部信号在 Schwab 以 `expected_mode=shadow` 挂载；IBKR 挂载未核实 | **NO** | 是否影响仓位取决于每日信号的授权字段；本窗口没有核实过的历史信号归档 |
| `volatility_delever_retention` | mode=environment，context_required=true | **NO** | 读取市场状态上下文；无历史时只能复现 missing_context（保留 0） |
| `income_layer` | 开，起点 150,000 USD，上限 0.95 | NEEDS_ADAPTER | 由账户美元净值决定；需美元净值回放，核心模拟器关闭它 |
| `runtime_risk_gate` | IBKR：SOXL 0.679、SOXX 0.873、总名义 0.97、预留现金 0.03、仅现金；Schwab：预留 0.03（最低 150 USD）、仅现金 | NEEDS_ADAPTER | 确定性上限，但核心模拟器没有；两家券商参数不同，需选定其一 |
| `option_income_overlay`（soxx_put_credit_spread_income_v1） | live_status=research_only，IBKR options_enabled=false | OFF_IN_LIVE | 实盘不下期权单；也无历史期权链 |

**TQQQ（`tqqq_growth_income`）**

| 插件 | 实盘状态 | 可复现 | 说明 |
| --- | --- | --- | --- |
| `dual_drive_core`（QQQ/TQQQ 回调、MA20 斜率） | 开 | YES | 核心规则 |
| `dual_drive_volatility_delever` | 开（分位 0.9、窗口 5） | YES | retention_mode=environment 时 TACO 否决不起作用 |
| `market_regime_control` | 配置开；IBKR 挂载未核实 | **NO** | 无核实的历史信号 |
| `volatility_delever_retention` | mode=environment，context_required=true | **NO** | 读取市场状态上下文 |
| `dual_drive_crisis_defense` | 开 | **NO** | 需要 true_crisis_active（来自市场状态/组合元数据） |
| `income_layer` | 开，起点 250,000 USD，上限 0.55 | NEEDS_ADAPTER | 由账户净值决定 |
| `option_growth_overlay`（tqqq_leaps_growth_v1） | live_status=research_only，live_gate=promotion_required | OFF_IN_LIVE | 实盘不下期权单 |
| `ai_extensions`（taco_panic_rebound、crisis_regime_guard） | 关 | OFF_IN_LIVE | — |

**结论**：只要清单里还有 NO 或 NEEDS_ADAPTER，主变体 `live_plugins_as_live` 就拒绝真实数据，报 `LIVE_PLUGIN_VARIANT_NOT_REPRODUCIBLE:<插件名>`。要解锁，需要两件事：(a) 用户提供或批准市场状态信号的历史归档，或者先核实实盘信号确实未授权仓位控制（shadow），再批准按“无仓位影响”等价处理；(b) 补齐美元净值的收入层回放和 runtime risk gate 适配器，并选定按哪个券商的参数。对照变体 `no_plugin_core` 只受数据确认这一项限制。

另外，IBKR `strategy_release.strategy_revision` 是 UES `4a394388`（2026-09-17），平台 pin 是 `9d1544d1`。两者在 `tqqq_dual_drive_core.py`、`entrypoints/__init__.py` 上有差异，实际运行的是哪一版需要确认。`strategy_release.config_sha256`（SOXL `0fbd7b3b…`、TQQQ `90e8ded5…`）用 manifest 默认值复算不一致，说明实盘配置可能还有平台合并的覆盖项；运行前必须读回并绑定。

## 3. 数据清单必填字段

`RealDataAuthorization(strategy_key, preregistration_id, DataManifest(sha256, source, license, calendar, start_date, end_date, observations), variant)`，其中 variant 只能是 `live_plugins_as_live` 或 `no_plugin_core`：
- `sha256`：所消费逐会话文件的 64 位小写 SHA-256；
- `source`、`calendar`、`start_date`/`end_date` 必须与预注册**完全一致**；`observations` 必须等于输入会话数；
- `report_context` 必须给出相同的 `manifest_sha256`/`source`/`license` 以及预注册的信号截止与成交时点；
- 预注册的 `data_source`/`window_*` 仍为空时，一律 `PREREGISTRATION_PENDING_DATA_CONFIRMATION`。填写它们需要用户确认后单独审阅的 PR。

## 4. 已确认的数据源与窗口（2026-10-11 用户确认）

| 项 | 值 |
| --- | --- |
| `data_source` | `alpaca_sip_raw_bars_plus_corporate_actions_private_archive`（既有私有 Alpaca SIP 原始价 + 公司行动归档） |
| `data_manifest_sha256` | `cb14a511083c824a748d137a271c93cfe0e8adf38f648905b26e37decf4c6182`（归档 `manifest.json` 的 SHA-256；DataManifest.sha256 必须等于它） |
| `data_license` | `user_attested_non_commercial_private_research` |
| 窗口 | 2023-03-28 至 2026-08-25（收益窗口；之前的会话只作信号预热） |
| 允许的变体 | 仅 `no_plugin_core`；`live_plugins_as_live` 需另行确认（`VARIANT_NOT_CONFIRMED_FOR_REAL_DATA`），且目前仍被插件阻断 |
| 运行位置 | 仅限批准的私有数据环境；原始数据和逐日输出不进仓库、不进公开 CI/日志 |

### 4.1 原提议（存档）

| 键 | 提议数据源 | 提议窗口 | 说明 |
| --- | --- | --- | --- |
| SOXL | 既有私有 Alpaca SIP 原始价 + 公司行动归档（user_attested 非商业研究许可，raw manifest `cb14a511…`），信号用同源复权价 | 2023-03-28 至 2026-08-25（R9 已用 856 会话；之前需 ≥260 会话预热） | 按 252/63/5 约 9 个测试折；全为 development |
| TQQQ | 同上 | 同上 | 同上 |
| 备选 | 新取更长历史（例如 Alpaca 2016 起），以增加折数 | 2016 起 | 需单独批准取数与许可核对；Yahoo 复权快照完整性/许可未核实，不建议作主源 |

## 5. 已知缺口

1. TQQQ 实盘口径的真实数据回放生成器不存在：`optimized_strategy_replay` 仅限 fixture，`tqqq_typed_baseline_result` 关闭所有控制且费用为 0。
2. SOXL 核心模拟器只输出扣费后日收益，不输出逐日换手；需要研究适配器以 0 费用运行并记录逐日交易名义额/NAV，供成本档位压力。
3. 插件：市场状态控制、保留比例、TQQQ 危机防御无法用历史复现（见 §2.1）；收入层和 runtime risk gate 需要新的适配器；IBKR 的插件挂载、release 修订与 config SHA 需读回确认。
4. 私有 raw/R6 归档的云端引用、覆盖到 2026-08-25 的 SOXL/SOXX 完整性、BOXX 分红 `process_date` 可知性需在运行前读回确认。
5. 实盘 15:45 成交与研究 `next_close` 的差异（UES #575）。
