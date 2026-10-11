# RS-08 实盘与研究一致性核查报告（只读）

> 日期 2026-10-11。范围：SOXL（Schwab live、IBKR U15998061）、TQQQ（IBKR U16608560）。全程只读：没有部署，没有改环境变量、密钥或流量，也没有碰下单路径。凡是会改变实盘行为的修复都只是建议，需要人工批准。

## 证据来源

- 只读诊断工作流 `Strategy Parity Check`（仅手动触发，各执行一次）：
  - IBKR：InteractiveBrokersPlatform#597，运行 38113858196
  - Schwab：CharlesSchwabPlatform#512，运行 38113859559
- 工作流做了这些事：读取当前承接流量的 Cloud Run revision；按 digest 拉取镜像，在 `--network none`、不带任何环境变量的容器里读取包提交号和 manifest；读取 `RUNTIME_TARGET_JSON` 里的发布标识（只取哈希和版本号）；汇总近 21 天的日志原因码；IBKR 另外用镜像里的平台代码拉取公开 yfinance 数据，记录行数和复权口径。
- 公开日志里没有账户金额、持仓、令牌、chat id 或完整配置。
- 静态代码核查对象：平台仓库 main、UES `4a394388` / `9d1544d1`、QPK。

## 1. 当前实际运行的版本

| 服务 | 承接流量的 revision（100%） | 镜像 digest | 镜像内 UES | 镜像内 QPK |
| --- | --- | --- | --- | --- |
| Schwab `charles-schwab-quant-service` | `00781-lel`（2026-10-09 创建） | `sha256:cd4779376abf…` | `4a3943883cd6`（0.7.60） | `2dc0b26ad4f1` |
| IBKR U15998061（SOXL） | `00416-8jb`（2026-10-09） | `sha256:9429234a87bf…` | `4a3943883cd6`（`/app/UES_REVISION` 也是这个） | `bd8d06e50f88` |
| IBKR U16608560（TQQQ） | `n0110100718`（2026-10-09） | `sha256:9fca942d3713…` | `4a3943883cd6` | `bd8d06e50f88` |

结论：
- 三个服务运行的 UES 都是 `4a394388`，与 `strategy_release.strategy_revision` 以及风控绑定里的 `ues_revision` 一致。
- 平台仓库的 pin（UES `9d1544d1`、QPK `155d74a6`）**比正在运行的镜像新，但还没有部署**。2026-10-09 的几个 revision 是 NOTIFY-01 复制旧镜像、只换了 Telegram 令牌生成的。
- QRS 依赖矩阵写的 `4a394388` 反而与实盘一致。

`4a394388` 和 `9d1544d1` 的差异：
- SOXL 策略文件和 manifest **完全相同**。
- TQQQ 的 `tqqq_dual_drive_core.py` 只是把判断逻辑抽成了函数。用 5 万组随机输入对比新旧两版 `decide_tqqq_dual_drive`，输出**零差异**。
- `entrypoints/__init__.py` 只增加了研究用参数和 DCA 的透传，不影响 SOXL/TQQQ 实盘。
- QPK 实盘指标函数 `build_semiconductor_rotation_indicators_from_history` 的源码哈希在实盘镜像（bd8d06e5、2dc0b26a）和研究环境里完全一致（`eaffcd10…`）。

**判定：研究用的策略逻辑与实盘等价。** 研究 main 的策略文件与 `9d1544d1` 相同，而 `9d1544d1` 在行为上与 `4a394388` 等价。

**风险提示（重要）**：Schwab 日志显示，2026-09-21 至 09-25 期间旧 revision（`00696`、`00698`）安装的 UES 版本与绑定的版本不一致，触发 `strategy_runtime_binding_mismatch`，接着 `strategy_runtime_risk_reject`（`rejected:runtime_risk_limits`）共 9 次，也就是那几天 SOXL 的调仓被风控拒绝。当前 revision 在 21 天内没有这类日志。如果以后按仓库 pin（`9d1544d1`）重新构建镜像，却不同时更新 `strategy_release` 和风控绑定里的 `ues_revision`，会**再次出现同样的拒单**。

## 2. config_sha256 对不上的原因

| 服务 | 发布 config_sha256 | manifest 默认配置哈希（镜像内复算） |
| --- | --- | --- |
| Schwab SOXL | `945c852a…` | `b2b8a896…`（紧凑 JSON）/ `95776aab…` |
| IBKR SOXL | `0fbd7b3b…` | 同上 |
| IBKR TQQQ | `90e8ded5…` | `eec673a4…` / `c37dde83…` |

两个平台跑的是同一份 SOXL manifest，发布的 config_sha256 却不同。说明这个哈希覆盖的是**平台合并后的配置**（预留现金、收入层、期权开关、插件挂载等平台覆盖项），不是 manifest 默认值。生成方法不在任何仓库里，是 2026-09-17 发布时在仓库之外算出来的。**判定：这不是代码不一致，而是哈希口径没有归档。** 要复算，需要找到 `evidence_sha256` 对应的发布证据包。第 6 节的只读诊断在 `gs://qsl-runtime-logs-shared` 里**没有找到**证据包，计算方法仍未归档。

## 3. trend_ma_window：实盘是 140

- 两个平台都用 `merged = manifest.default_config` 再叠加运行时覆盖。覆盖项只有预留现金、收入层、期权、DCA、IBIT 等，**没有 trend_ma_window**。
- 镜像内 manifest 的 `trend_ma_window` = 140。三个服务的环境变量里都没有 `STRATEGY_CONFIG_PATH`，也没有任何变量提到 `trend_ma_window`。
- 平台代码里的 `.get("trend_ma_window", 150)` 只在键缺失时才生效，实际不会走到。
- QPK 要求的历史长度在 140 和 150 下都是 420。

**判定：实盘是 140，与研究（manifest 140）一致。**
**建议（需人工批准）**：把平台代码里的缺省值 150 删掉，或改成直接读取 manifest，避免日后 manifest 缺键时悄悄变成 150。

## 4. 价格口径与历史长度

- 两个平台都**没有配置**外部市场信号（没有 `MARKET_SIGNAL_*` 变量），SOXL 走本地指标构建。

| 项 | IBKR（SOXL、TQQQ） | Schwab（SOXL） | 研究 v2 |
| --- | --- | --- | --- |
| 数据源 | 优先用 yfinance（`auto_adjust=False`，取 `Adj Close`）；拿不到时才用 IBKR `ADJUSTED_LAST` | QPK Schwab `get_price_history`，TWO_YEARS，日线 | Alpaca SIP 原始价格 + 公司行动 |
| 复权 | 拆股+分红复权：2026-10-11 实测 Adj Close 除以 Close 的比值，首行 SOXL 0.990、SOXX 0.990、QQQ 0.990，末行 1.0 | **只做拆股调整**（第 6 节 B：10/10 个交易日与只拆股调整的复算一致） | 主口径：拆股复权；敏感性：总收益（`rs04-kelly-soxl-v2-trsig`） |
| 历史长度 | "420 D" 被换算成 "2 Y"，实测 501 行（2024-10-10..2026-10-09）；TQQQ 的 QQQ 也是 2 年 501 行 | 2 年（行数待核实） | SOXL 最多 504；TQQQ 用全部历史 |
| 每次运行的实际来源和行数 | **没有日志记录** | **没有日志记录** | — |

判定：
- 历史长度：一致。SOXL 都约 500 行。TQQQ 用到的窗口最长 252（MA200、波动率分位 252），历史超过约 260 行后结果不受长度影响。
- 价格口径：**两家券商本身就不一致**。IBKR 含分红复权，对应研究的 trsig 敏感性；Schwab 只做拆股调整（已核实，见第 6 节 B），对应研究主口径，也符合回测标准 §4.4。研究已经同时登记了两种口径（主口径和 trsig），可以分别对照；但实盘应该统一。
- **建议（需人工批准）**：
  1. 统一信号价格口径：IBKR 改用 `Close`（只拆股调整），与回测标准和 Schwab 一致；或者两边都按总收益口径，并修改标准。
  2. 每次运行记录不敏感的信号来源字段：来源（yfinance / ADJUSTED_LAST / Schwab）、行数、首尾日期、口径标签。
  3. 研究对 IBKR 采用 trsig 口径、对 Schwab 采用 split_adjusted 口径分别做一致性回放。

## 5. 其他观察

- IBKR U16608560（TQQQ）近 21 天每次运行都记录 `runtime_risk_status=unavailable:profile_not_supported`。这是设计如此（运行时风控绑定目前只支持 SOXL），不是故障。
- IBKR U15998061 的服务环境里 `RUNTIME_TARGET_ENABLED=false`，但 `RUNTIME_TARGET_JSON` 已设置。21 天内没有异常日志，本次没有展开排查。

## 6. 后续只读诊断：发布证据包与 Schwab 价格口径（2026-10-11）

用户的 Codex 环境里没有 gcloud 和 yfinance，没法执行转交的提示词。所以改为扩展只读诊断工作流，用部署服务账号执行：IBKR #600（`08fd12a`），运行 38116620189；Schwab #515（`e81cf3c`），运行 38116622385。两个工作流各手动触发一次。两个服务账号都能读 `gs://qsl-runtime-logs-shared`。公开日志里只有哈希、字段名、标签、整数和误差区间，没有金额、持仓或令牌。

**A. config_sha256 口径：没找到证据包。**
- 桶里共 11326 个对象，名字里都没有这 3 个 release_id（`soxl-schwab-runtime-risk-recovery-20260917`、`soxl-ibkr-runtime-risk-20260917`、`tqqq-ibkr-lkg-restore-20260917`），也没有对应的 evidence/config sha256 前缀。
- 又按名字挑了 250 个可能相关的 JSON（名字含 release、evidence、strategy-lifecycle、promotion、activation，不含执行报告）读了内容，都没提到这些值。
- **判定：证据包不在这个桶里（或者不按这些值命名），没法复算。哈希口径仍然没有归档。** 其余 JSON 只按名字筛过，没有逐个读，所以不能完全排除。
- 建议：以后发布时，把 config_sha256 的输入字段和规范化方式（是否 sort_keys、分隔符、包含哪些平台覆盖项）写进仓库或证据包。

**B. Schwab SOXL 的信号价格口径：只拆股调整。**
- 方法：读取 Schwab 最近 10 个交易日（2026-09-28 至 10-09）的实盘 `soxl_soxx_trend_income` 执行报告，取报告里的 SOXX 价格和 140 日趋势均线。再用 yfinance 公开日线分别按只拆股调整（`Close`）和含分红复权（`Adj Close`）复算：前 139 天用历史收盘价，按运行当天口径换算，第 140 天用报告里的价格。
- 结果：**10/10 天只拆股调整的复算最接近**。只拆股调整的相对误差全部小于 1e-6（约 10^-8.5）；含分红复权的相对误差约 10^-3.1，确定不是这一种。
- 报告里只有趋势标的 SOXX 的字段，**没有 SOXL 的 ma_trend**，也**没有历史行数**。修复 4 部署后，报告里会出现每个标的的数据来源、行数、首尾日期和口径标签。
- **判定**：Schwab 和研究主口径（只拆股调整，回测标准 §4.4）一致；IBKR 是含分红复权（第 4 节），对应研究的 trsig 敏感性口径。两家券商的口径确实不同。

## 7. 修复进度

| 编号 | 内容 | 状态 |
| --- | --- | --- |
| 1 | 部署前检查镜像里的 UES 版本是否与 `strategy_release` 和风控绑定的 `ues_revision` 一致，不一致就让部署失败 | 已合并：IBKR #598（`c438de4`）、Schwab #513（`7fd258d`）。只改了部署工作流，不改运行行为 |
| 2 | 去掉平台代码里 trend_ma_window 的缺省值 150 | **需人工批准，未实施** |
| 3 | 统一信号价格口径（IBKR 改用只拆股调整） | **需人工批准，未实施** |
| 4 | 每次运行记录信号数据来源、行数、首尾日期和口径标签 | 已合并：IBKR #599（`c59aac5`）、Schwab #514（`e3dea56`）。新 revision 部署后生效 |

合并不会部署。部署工作流只能手动触发，push 只会跑测试；本轮没有改环境变量、密钥、流量或下单路径。
