# UsEquityStrategies

[English](#english) | [中文](#中文)

---

<a id="english"></a>
## English

Standalone `us_equity` strategy repository for QuantStrategyLab platforms.

This repository is the strategy layer: it owns pure signal, allocation, and target-computation logic plus strategy metadata. Downstream platform repositories still own broker adapters, order routing, schedule, secrets, and notifications.

### Contract boundary

The current integration path is:

- live profiles expose manifest-backed unified entrypoints
- downstream platforms load those entrypoints through `QuantPlatformKit`
- strategy outputs stay inside the shared `StrategyDecision` contract
- broker-specific execution order, UI rows, and notification layout stay in platform repositories

Legacy strategy functions may still exist as internal adapters, but downstream runtimes should treat `entrypoints/` and manifests as the supported integration surface.

### Authoring and portability guides

- [`docs/us_equity_strategy_template.md`](./docs/us_equity_strategy_template.md): template for adding a new US equity profile in this repository.
- [`docs/us_equity_portability_checklist.md`](./docs/us_equity_portability_checklist.md): reviewer checklist before enabling a profile on broker runtimes.
- [`docs/us_equity_contract_gap_matrix.md`](./docs/us_equity_contract_gap_matrix.md): runtime-enabled profile contract gaps versus the cross-platform target.
- [`docs/us_equity_value_mode_input_contract.md`](./docs/us_equity_value_mode_input_contract.md): fixed canonical input contract for the two current value-mode profiles.
- [`docs/research/mega_cap_leader_rotation.md`](./docs/research/mega_cap_leader_rotation.md): mega-cap leader rotation research notes and dynamic top20 runtime profile notes.

### Strategy index

| Canonical profile | Display name | Compatible platforms | Cadence | Benchmark | Role | Status |
| --- | --- | --- | --- | --- | --- | --- |
| `global_etf_rotation` | Global ETF Rotation | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform` | `quarterly + daily canary` | `VOO` | `defensive_rotation` | `runtime_enabled` |
| `russell_1000_multi_factor_defensive` | Russell 1000 Multi-Factor | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform` | `monthly` | `SPY` | `defensive_stock_baseline` | `runtime_enabled` |
| `tech_communication_pullback_enhancement` | Tech/Communication Pullback Enhancement | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform` | `monthly` | `QQQ` | `parallel_cash_buffer_branch` | `runtime_enabled` |
| `mega_cap_leader_rotation_dynamic_top20` | Mega Cap Leader Rotation Dynamic Top20 | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform` | `monthly` | `QQQ` | `concentrated_leader_rotation` | `runtime_enabled` |
| `dynamic_mega_leveraged_pullback` | Dynamic Mega Leveraged Pullback | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform` | `monthly snapshot + daily runtime` | `QQQ` | `offensive_leveraged_pullback` | `runtime_enabled` |
| `tqqq_growth_income` | TQQQ Growth Income | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform` | `daily` | `QQQ` | `offensive_income` | `runtime_enabled` |
| `soxl_soxx_trend_income` | SOXL/SOXX Semiconductor Trend Income | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform` | `daily` | `SOXX` | `sector_offensive_income` | `runtime_enabled` |

These strategies are consumed by platform repositories through `QuantPlatformKit` strategy contracts and component loaders. Canonical profile keys are the runtime-facing layer; display names are the human-facing layer. Compatibility here means the strategy is structurally usable on that broker stack. Whether a profile is actually enabled, default, or rollback is now owned by each platform repository.

Cadence here is the strategy-level intent. Platform repositories own the actual
Cloud Scheduler / GitHub Actions cron settings:

- daily profiles: run once per trading day near the US close.
- `global_etf_rotation`: evaluate canary risk daily, but perform normal rotation
  only on the last NYSE trading day of March, June, September, and December.
- monthly snapshot profiles: publish feature snapshots monthly from
  `UsEquitySnapshotPipelines`, then execute once in the downstream runtime's
  monthly window.

### Research candidates

- `mega_cap_leader_rotation_dynamic_top20`: runtime-enabled monthly profile for the historical dynamic top-20 mega-cap universe. It keeps the research defaults that held 4 names at a 25% single-name cap and uses QQQ 200-day trend to reduce stock exposure to 50% when QQQ is below trend.
- `mega_cap_leader_rotation`: umbrella research/backtest name for the static and dynamic variants; see [`docs/research/mega_cap_leader_rotation.md`](./docs/research/mega_cap_leader_rotation.md).

### global_etf_rotation

**Objective**
- Keep a broad, lower-beta rotation framework for US equity accounts.
- Stay open to leadership from tech and semiconductors without concentrating only in high-beta products.
- Fall back to a short-duration safe haven when the cross-asset risk picture is weak.

**Universe**
- 22 rotation ETFs: `EWY`, `EWT`, `INDA`, `FXI`, `EWJ`, `VGK`, `VOO`, `XLK`, `SMH`, `GLD`, `SLV`, `USO`, `DBA`, `XLE`, `XLF`, `ITA`, `XLP`, `XLU`, `XLV`, `IHI`, `VNQ`, `KRE`
- Canary basket: `SPY`, `EFA`, `EEM`, `AGG`
- Safe haven: `BIL`

**Indicators and rules**
- Momentum uses Keller-style `13612W` monthly momentum: `(12×R1M + 4×R3M + 2×R6M + R12M) / 19`.
- Trend filter: candidate ETF must be above its 200-day SMA.
- Hold bonus: an existing holding receives `+2%` score bonus to reduce turnover.
- Daily canary check: if all 4 canary assets have negative or missing momentum, the strategy goes `100% BIL` immediately.

**Rebalance behavior**
- Normal rotation only happens on the last NYSE trading day of March, June, September, and December.
- On a rebalance day, the strategy ranks the eligible universe and selects the top 2 ETFs.
- Selected ETFs are equally weighted (`50 / 50`).
- If fewer than 2 names survive, the unused slot is parked in `BIL`.
- On non-rebalance days, the strategy returns no target change unless the canary emergency path is triggered.

**Why it exists**
- Compared with a pure tech or leveraged-Nasdaq approach, this profile is meant to be steadier.
- It still allows `VOO`, `XLK`, and `SMH` to win their way into the rotation instead of hard-coding them out.

### russell_1000_multi_factor_defensive

**Objective**
- Provide a first stock-level US equity strategy that stays close to the current platform architecture.
- Start with a price-only factor stack before adding fundamentals or ML reranking.
- Keep execution realistic by consuming a precomputed feature snapshot instead of fetching 1000 symbols live during the rebalance run.

**Universe**
- Point-in-time Russell 1000 constituent snapshot supplied by an upstream data task.
- Benchmark row: `SPY`
- Safe haven: `BOXX`

**Signals and rules**
- Current V1 factors are price-only:
  - `mom_6_1`
  - `mom_12_1`
  - `sma200_gap`
  - `vol_63`
  - `maxdd_126`
- Factors are standardized within sector, then combined into one total score.
- Existing holdings receive a configurable hold bonus.
- Market defense uses:
  - `SPY` trend (`sma200_gap > 0`)
  - breadth = share of eligible universe above `200MA`

**Portfolio behavior**
- Rebalance cadence is monthly in the downstream runtime.
- Default stock exposure:
  - `100%` in `risk_on`
  - `50%` in `soft_defense`
  - `10%` in `hard_defense`
- Default position count is `24`.
- Unused capital is parked in `BOXX`.

**Feature snapshot schema**
- Required price-history input columns:
  - `symbol`, `as_of`, `close`, `volume`
- Required universe input columns:
  - `symbol`, `sector`
  - optional: `start_date`, `end_date` for point-in-time membership during backtests
- Generated snapshot columns:
  - `as_of`, `symbol`, `sector`, `close`, `volume`, `adv20_usd`, `history_days`
  - `mom_6_1`, `mom_12_1`, `sma200_gap`, `vol_63`, `maxdd_126`, `eligible`

**Snapshot pipeline ownership**

Feature-snapshot generation, Russell 1000 input preparation, ranking artifacts, and the research backtest CLI now live in `../UsEquitySnapshotPipelines`.
This repo only owns the runtime strategy logic and catalog metadata.

Use the upstream repo for artifact jobs:

```bash
cd ../UsEquitySnapshotPipelines
PYTHONPATH=src:../UsEquityStrategies/src:../QuantPlatformKit/src python scripts/update_russell_1000_input_data.py \
  --output-dir data/input/refreshed/r1000_official_monthly_v2_alias \
  --universe-start 2018-01-01 \
  --price-start 2018-01-01 \
  --extra-symbols QQQ,SPY,BOXX
PYTHONPATH=src:../UsEquityStrategies/src:../QuantPlatformKit/src python scripts/build_russell_1000_feature_snapshot.py \
  --prices data/input/refreshed/r1000_official_monthly_v2_alias/r1000_price_history.csv \
  --universe data/input/refreshed/r1000_official_monthly_v2_alias/r1000_universe_history.csv \
  --output-dir data/output/russell_1000_multi_factor_defensive
PYTHONPATH=src:../UsEquityStrategies/src:../QuantPlatformKit/src python scripts/backtest_russell_1000_multi_factor_defensive.py \
  --prices data/input/refreshed/r1000_official_monthly_v2_alias/r1000_price_history.csv \
  --universe data/input/refreshed/r1000_official_monthly_v2_alias/r1000_universe_history.csv \
  --output-dir data/output/russell_1000_multi_factor_defensive_backtest
```

The backtest output directory still includes `summary.csv`, `portfolio_returns.csv`, `weights_history.csv`, and `turnover_history.csv`.

### tqqq_growth_income

**Objective**
- Combine growth exposure, income production, and idle-cash defense in one profile.
- Let the attack sleeve react to QQQ trend conditions while keeping a separate income sleeve for larger accounts.

**Portfolio layers**
- Attack layer: `TQQQ`
- Income layer: `SPYI`, `QQQI`
- Defense / cash-like layer: `BOXX` plus a cash reserve

**Signals and indicators**
- Uses daily `QQQ` history as the signal source.
- Core indicators are `MA200` and `ATR14%`.
- The strategy derives two ATR-adjusted lines around `MA200`:
  - `entry_line = MA200 × clamp(1 + ATR% × atr_entry_scale)`
  - `exit_line = MA200 × clamp(1 - ATR% × atr_exit_scale)`
- The exact clamp floors/caps are injected by the downstream runtime.

**Attack-layer rules (`TQQQ`)**
- Position size comes from `get_hybrid_allocation(strategy_equity, qqq_p, exit_line)`.
- That sizing is applied only to strategy-layer equity, which is total equity after subtracting the income layer.
- If already holding `TQQQ`:
  - `QQQ < exit_line` → target `TQQQ = 0`
  - `exit_line <= QQQ < MA200` → target `TQQQ = agg_ratio × 0.33`
  - `QQQ >= MA200` → target `TQQQ = agg_ratio`
- If flat and `QQQ > entry_line` → open `TQQQ` at `agg_ratio`.

**Income-layer rules (`SPYI` / `QQQI`)**
- `get_income_ratio(total_equity)` stays at `0` below the configured threshold.
- From `1x` to `2x` the threshold, the income sleeve ramps linearly to `40%`.
- Above `2x` the threshold, the income sleeve caps at `60%`.
- `QQQI_INCOME_RATIO` decides the split between `QQQI` and `SPYI`.

**Defense behavior (`BOXX` and cash)**
- A cash reserve is kept at the strategy layer.
- After reserving cash and sizing `TQQQ`, the remaining strategy-layer capital is assigned to `BOXX`.
- Downstream execution decides whether the gap to target is large enough to trade via a rebalance threshold.

**Current live Charles Schwab profile defaults**
- `INCOME_THRESHOLD_USD = 100000`
- `QQQI_INCOME_RATIO = 0.5`
- `CASH_RESERVE_RATIO = 0.05`
- `REBALANCE_THRESHOLD_RATIO = 0.01`
- `RISK_LEVERAGE_FACTOR = 3.0`, `RISK_NUMERATOR = 0.30`, `RISK_AGG_CAP = 0.50`
- `ATR_EXIT_SCALE = 2.0`, `ATR_ENTRY_SCALE = 2.5`
- `EXIT_LINE_FLOOR / CAP = 0.92 / 0.98`, `ENTRY_LINE_FLOOR / CAP = 1.02 / 1.08`

### soxl_soxx_trend_income

**Objective**
- Use a simpler semiconductor trend switch than the Schwab profile.
- Keep a dedicated income sleeve for larger accounts without forcing that sleeve to shrink during normal trading-layer changes.

**Portfolio layers**
- Trading layer: `SOXL`, `SOXX`, `BOXX`
- Income layer: `QQQI`, `SPYI`

**Trading-layer rules**
- The core signal compares `SOXL` to a configurable trend moving average window.
- If `SOXL > trend MA`, the active risk asset is `SOXL`.
- If `SOXL <= trend MA`, the strategy delevers into `SOXX`.
- Unused trading-layer capital is parked in `BOXX`.

**Sizing behavior**
- The deploy ratio is dynamic and depends on account size.
- Small, mid, and large accounts use different base deploy ratios.
- Above the large-account breakpoint, the trading-layer deploy ratio decays logarithmically so very large accounts do not keep scaling risk linearly.
- The downstream runtime also keeps a cash reserve and only trades when the rebalance gap is large enough.

**Income-layer rules**
- The income layer starts only after total strategy equity crosses `income_layer_start_usd`.
- It ramps linearly to `income_layer_max_ratio` by `2x` that threshold.
- Existing income holdings are locked with `max(current_income_layer_value, desired_income_layer_value)`, so the layer only adds capital instead of force-selling down.
- New income allocation is split by configurable `QQQI` / `SPYI` weights.

**Current live LongBridge profile defaults**
- `TREND_MA_WINDOW = 150`
- `CASH_RESERVE_RATIO = 0.03`
- `MIN_TRADE_RATIO = 0.01`, `MIN_TRADE_FLOOR = 100 USD`
- `REBALANCE_THRESHOLD_RATIO = 0.01`
- Deploy ratios: `0.60 / 0.57 / 0.50` for small / mid / large accounts
- `TRADE_LAYER_DECAY_COEFF = 0.04` above `180000 USD`
- Income layer starts at `150000 USD`, caps at `15%`
- Income split: `QQQI 70%`, `SPYI 30%`

---

<a id="中文"></a>
## 中文

这是 `QuantStrategyLab` 的独立美股策略仓。

这个仓库负责**纯策略层**：信号、仓位、目标权重计算，以及策略元数据。下游平台仓库继续负责券商适配、下单方式、调度、密钥和通知。

### 契约边界

当前主线集成方式已经固定为：

- runtime profile 暴露 manifest 驱动的统一 entrypoint
- 下游平台通过 `QuantPlatformKit` 加载这些 entrypoint
- 策略输出保持在共享 `StrategyDecision` 契约内
- 券商专属执行顺序、UI 展示行和通知布局继续留在平台仓库

旧策略函数可以继续作为仓库内部 adapter 存在，但下游运行时应把 `entrypoints/` 和 manifest 当成正式接入面。

### 编写与可移植性文档

- [`docs/us_equity_strategy_template.md`](./docs/us_equity_strategy_template.md)：新增美股策略时使用的模板文档。
- [`docs/us_equity_portability_checklist.md`](./docs/us_equity_portability_checklist.md)：策略进入各券商运行时前的可移植性检查清单。
- [`docs/us_equity_contract_gap_matrix.md`](./docs/us_equity_contract_gap_matrix.md)：当前 6 条 live profile 距离跨平台目标契约的差异矩阵。
- [`docs/us_equity_value_mode_input_contract.md`](./docs/us_equity_value_mode_input_contract.md)：两条 value-mode 策略的 canonical 输入契约定稿。
- [`docs/research/mega_cap_leader_rotation.md`](./docs/research/mega_cap_leader_rotation.md)：巨头强者轮动的研究说明，以及 dynamic top20 运行 profile 说明。

### 策略索引

| Canonical profile | 显示名 | 兼容平台仓库 | 策略频率 | 核心思路 |
| --- | --- | --- | --- | --- |
| `global_etf_rotation` | 全球 ETF 轮动 | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform` | 季度调仓 + 每日 canary | 22 只全球 ETF 的季度 Top 2 轮动，带每日 canary 防守 |
| `russell_1000_multi_factor_defensive` | 罗素1000多因子 | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform` | 月频 | Russell 1000 个股月频 price-only 选股，带 SPY + breadth 防守和 BOXX 停泊 |
| `tech_communication_pullback_enhancement` | 科技通信回调增强 | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform` | 月频 | tech-heavy 月频个股选择，做受控回调，并显式保留 BOXX 缓冲 |
| `mega_cap_leader_rotation_dynamic_top20` | Mega Cap 动态 Top20 龙头轮动 | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform` | 月频 | 从历史动态 mega-cap top20 池里选 4 只强势龙头，默认单票 25%，QQQ 跌破 200 日线时降到 50% 股票仓位 |
| `dynamic_mega_leveraged_pullback` | Mega Cap 2x 回调策略 | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform` | 月频 snapshot + 日频运行 | 动态 mega-cap top15 池里选 top3，使用 QQQ 200SMA/ATR 门槛控制 2x 做多产品仓位，剩余资金停 BOXX |
| `tqqq_growth_income` | TQQQ 增长收益 | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform` | 日频 | 由 QQQ 驱动的 TQQQ 攻击层，加上 SPYI / QQQI 收入层和 BOXX 防守层 |
| `soxl_soxx_trend_income` | SOXL/SOXX 半导体趋势收益 | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform` | 日频 | SOXL / SOXX 趋势切换，剩余资金停在 BOXX，并叠加收入层 |

这些策略通过 `QuantPlatformKit` 提供的策略契约和组件加载接口，被各个平台仓库引用。运行时和部署配置统一使用 canonical profile key。
这里的策略频率表达的是策略层意图；实际 Cloud Scheduler / GitHub Actions
cron 配置由各个平台仓库负责：

- 日频策略：每个美股交易日临近收盘运行一次。
- `global_etf_rotation`：每日检查 canary 风险，但正常轮动只在
  `3 / 6 / 9 / 12` 月最后一个 NYSE 交易日触发。
- 月频 snapshot 策略：由 `UsEquitySnapshotPipelines` 按月发布 feature
  snapshot，再由下游运行时在月度窗口内执行一次。

### 研究候选策略

- `mega_cap_leader_rotation_dynamic_top20`：已注册为 runtime-enabled 月频 profile，使用历史动态 mega-cap top20 池，默认选 4 只、单票 25%，QQQ 跌破 200 日线时股票仓位降到 50%。
- `mega_cap_leader_rotation`：静态池和动态池的研究/回测总称；说明见 [`docs/research/mega_cap_leader_rotation.md`](./docs/research/mega_cap_leader_rotation.md)。

### global_etf_rotation

**策略目标**
- 给美股账户提供一个更分散、波动更低的轮动框架。
- 不把科技和半导体硬排除在外，但也不把风险全部集中到高弹性品种上。
- 当跨资产风险明显转弱时，退回短久期避险仓位。

**标的池**
- 22 只轮动 ETF：`EWY`、`EWT`、`INDA`、`FXI`、`EWJ`、`VGK`、`VOO`、`XLK`、`SMH`、`GLD`、`SLV`、`USO`、`DBA`、`XLE`、`XLF`、`ITA`、`XLP`、`XLU`、`XLV`、`IHI`、`VNQ`、`KRE`
- Canary 篮子：`SPY`、`EFA`、`EEM`、`AGG`
- 避险资产：`BIL`

**指标和规则**
- 动量使用 Keller 风格的 `13612W` 月频动量：`(12×R1M + 4×R3M + 2×R6M + R12M) / 19`。
- 趋势过滤：候选 ETF 必须站上 `200 日均线`。
- 持有加分：当前持仓会获得 `+2%` 分数加成，用来降低换手。
- 每日 canary 检查：如果 `SPY / EFA / EEM / AGG` 这 4 个资产的动量全部为负，或缺失到全部失效，就立刻切到 `100% BIL`。

**调仓行为**
- 正常轮动只在 `3 / 6 / 9 / 12` 月最后一个 NYSE 交易日触发。
- 到调仓日后，对合格标的打分，选出前 2 名。
- 前 2 名等权配置，默认 `50 / 50`。
- 如果合格标的不满 2 个，空出来的部分停到 `BIL`。
- 非调仓日默认不改目标仓位，除非触发 canary 应急防守。

**这套策略的定位**
- 相比纯科技或者杠杆纳指路线，这个档位更稳。
- 但它仍然允许 `VOO`、`XLK`、`SMH` 靠表现进入组合，而不是事先把它们排除。

### russell_1000_multi_factor_defensive

**策略目标**
- 作为第一版个股策略，先尽量复用现有平台边界。
- 第一阶段只用价格因子，不急着上基本面和机器学习。
- 运行时只消费预先算好的 feature snapshot，不在调仓时现场拉 1000 只股票历史数据。

**股票池**
- 上游数据任务提供的 Russell 1000 点时成分快照
- 基准行：`SPY`
- 防守资产：`BOXX`

**当前 V1 因子**
- `mom_6_1`
- `mom_12_1`
- `sma200_gap`
- `vol_63`
- `maxdd_126`

策略先在行业内做标准化，再合成总分。当前持仓可以拿到一小段 hold bonus。

**防守规则**
- `SPY` 的 `sma200_gap > 0` 代表 benchmark 趋势正常
- breadth = 合格股票里站上 `200MA` 的比例
- 默认风险暴露：
  - `risk_on`：`100%`
  - `soft_defense`：`50%`
  - `hard_defense`：`10%`

**组合规则**
- 下游运行时按月调仓
- 默认持仓数 `24`
- 剩余资金停在 `BOXX`

**feature snapshot 输入/输出约定**
- 价格历史输入列：
  - `symbol`、`as_of`、`close`、`volume`
- 股票池输入列：
  - `symbol`、`sector`
  - 可选：`start_date`、`end_date`（用于回测时按日期启用 / 退出成分股）
- 生成后的 snapshot 列：
  - `as_of`、`symbol`、`sector`、`close`、`volume`、`adv20_usd`、`history_days`
  - `mom_6_1`、`mom_12_1`、`sma200_gap`、`vol_63`、`maxdd_126`、`eligible`

**Snapshot 流水线归属**

Feature snapshot 生成、Russell 1000 输入数据准备、ranking 产物和研究回测 CLI 已迁移到 `../UsEquitySnapshotPipelines`。
本仓库只保留运行时策略逻辑和策略目录元数据。

产物任务请在上游仓库执行：

```bash
cd ../UsEquitySnapshotPipelines
PYTHONPATH=src:../UsEquityStrategies/src:../QuantPlatformKit/src python scripts/update_russell_1000_input_data.py \
  --output-dir data/input/refreshed/r1000_official_monthly_v2_alias \
  --universe-start 2018-01-01 \
  --price-start 2018-01-01 \
  --extra-symbols QQQ,SPY,BOXX
PYTHONPATH=src:../UsEquityStrategies/src:../QuantPlatformKit/src python scripts/build_russell_1000_feature_snapshot.py \
  --prices data/input/refreshed/r1000_official_monthly_v2_alias/r1000_price_history.csv \
  --universe data/input/refreshed/r1000_official_monthly_v2_alias/r1000_universe_history.csv \
  --output-dir data/output/russell_1000_multi_factor_defensive
PYTHONPATH=src:../UsEquityStrategies/src:../QuantPlatformKit/src python scripts/backtest_russell_1000_multi_factor_defensive.py \
  --prices data/input/refreshed/r1000_official_monthly_v2_alias/r1000_price_history.csv \
  --universe data/input/refreshed/r1000_official_monthly_v2_alias/r1000_universe_history.csv \
  --output-dir data/output/russell_1000_multi_factor_defensive_backtest
```

回测输出目录仍然会包含 `summary.csv`、`portfolio_returns.csv`、`weights_history.csv`、`turnover_history.csv`。

### tqqq_growth_income

**策略目标**
- 把增长、分红收入、闲置现金防守放进同一个档位里。
- 攻击层根据 `QQQ` 趋势动态调节，收入层则服务于更大的账户规模。

**资产层级**
- 攻击层：`TQQQ`
- 收入层：`SPYI`、`QQQI`
- 防守 / 现金类：`BOXX` 加现金储备

**信号和指标**
- 以 `QQQ` 的日线数据作为主信号源。
- 核心指标是 `MA200` 和 `ATR14%`。
- 策略会围绕 `MA200` 生成两条 ATR 调整后的线：
  - `entry_line = MA200 × clamp(1 + ATR% × atr_entry_scale)`
  - `exit_line = MA200 × clamp(1 - ATR% × atr_exit_scale)`
- 具体的 clamp 上下界由下游运行仓库注入。

**攻击层规则（`TQQQ`）**
- 仓位大小来自 `get_hybrid_allocation(strategy_equity, qqq_p, exit_line)`。
- 这个仓位只作用在**策略层资产**上，也就是总资产扣掉收入层之后的部分。
- 如果当前已经持有 `TQQQ`：
  - `QQQ < exit_line` → `TQQQ` 目标仓位归零
  - `exit_line <= QQQ < MA200` → `TQQQ` 目标仓位降到 `agg_ratio × 0.33`
  - `QQQ >= MA200` → `TQQQ` 维持 `agg_ratio`
- 如果当前空仓且 `QQQ > entry_line` → 按 `agg_ratio` 开仓。

**收入层规则（`SPYI` / `QQQI`）**
- `get_income_ratio(total_equity)` 在阈值以下为 `0`。
- 从 `1 倍阈值` 到 `2 倍阈值` 之间，收入层线性抬升到 `40%`。
- 超过 `2 倍阈值` 后，收入层上限为 `60%`。
- `QQQI_INCOME_RATIO` 决定 `QQQI` 和 `SPYI` 的拆分比例。

**防守行为（`BOXX` 与现金）**
- 策略层先保留一部分现金储备。
- 扣掉现金储备并算出 `TQQQ` 目标后，剩余策略层资金进入 `BOXX`。
- 是否真的下单，由下游执行层再结合再平衡阈值判断。

**当前 Charles Schwab live profile 默认值**
- `INCOME_THRESHOLD_USD = 100000`
- `QQQI_INCOME_RATIO = 0.5`
- `CASH_RESERVE_RATIO = 0.05`
- `REBALANCE_THRESHOLD_RATIO = 0.01`
- `RISK_LEVERAGE_FACTOR = 3.0`，`RISK_NUMERATOR = 0.30`，`RISK_AGG_CAP = 0.50`
- `ATR_EXIT_SCALE = 2.0`，`ATR_ENTRY_SCALE = 2.5`
- `EXIT_LINE_FLOOR / CAP = 0.92 / 0.98`，`ENTRY_LINE_FLOOR / CAP = 1.02 / 1.08`

### soxl_soxx_trend_income

**策略目标**
- 用一套比 Schwab 档位更直接的半导体趋势切换逻辑。
- 给大账户保留收入层，但不因为交易层切换就强制把收入层减回来。

**资产层级**
- 交易层：`SOXL`、`SOXX`、`BOXX`
- 收入层：`QQQI`、`SPYI`

**交易层规则**
- 核心信号是比较 `SOXL` 与一条可配置的趋势均线。
- 如果 `SOXL > trend MA`，风险资产使用 `SOXL`。
- 如果 `SOXL <= trend MA`，策略降杠杆切到 `SOXX`。
- 交易层没有部署出去的资金停在 `BOXX`。

**仓位规则**
- 交易层 deploy ratio 会随账户规模变化。
- 小账户、中账户、大账户各有一档基础 deploy ratio。
- 超过大账户断点后，交易层 deploy ratio 会按对数方式继续衰减，避免超大账户风险线性放大。
- 下游运行层另外还会保留现金储备，并且只有偏离目标足够大时才触发调仓。

**收入层规则**
- 总策略权益超过 `income_layer_start_usd` 才启动收入层。
- 到 `2 倍阈值` 时，收入层线性抬升到 `income_layer_max_ratio`。
- 收入层采用 `max(current_income_layer_value, desired_income_layer_value)` 锁定已有收入资产，所以默认只增配，不主动减配。
- 新增收入资金按可配置的 `QQQI / SPYI` 比例拆分。

**当前 LongBridge live profile 默认值**
- `TREND_MA_WINDOW = 150`
- `CASH_RESERVE_RATIO = 0.03`
- `MIN_TRADE_RATIO = 0.01`，`MIN_TRADE_FLOOR = 100 USD`
- `REBALANCE_THRESHOLD_RATIO = 0.01`
- 小 / 中 / 大账户 deploy ratio：`0.60 / 0.57 / 0.50`
- `TRADE_LAYER_DECAY_COEFF = 0.04`，在 `180000 USD` 以上继续衰减
- 收入层起点 `150000 USD`，上限 `15%`
- 收入层配比：`QQQI 70%`，`SPYI 30%`
