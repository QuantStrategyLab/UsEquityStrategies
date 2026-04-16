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
- [`docs/us_equity_contract_gap_matrix.md`](./docs/us_equity_contract_gap_matrix.md): current live-profile contract gaps versus the cross-platform target.
- [`docs/us_equity_value_mode_input_contract.md`](./docs/us_equity_value_mode_input_contract.md): fixed canonical input contract for the two current value-mode profiles.
- [`docs/research/mega_cap_leader_rotation.md`](./docs/research/mega_cap_leader_rotation.md): mega-cap leader rotation research notes and dynamic top20 runtime profile notes.

### Strategy index

| Canonical profile | Display name | Compatible platforms | Cadence | Benchmark | Role | Status |
| --- | --- | --- | --- | --- | --- | --- |
| `global_etf_rotation` | Global ETF Rotation | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform` | `quarterly + daily canary` | `VOO` | `defensive_rotation` | `runtime_enabled` |
| `russell_1000_multi_factor_defensive` | Russell 1000 Multi-Factor | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform` | `monthly` | `SPY` | `defensive_stock_baseline` | `runtime_enabled` |
| `tech_communication_pullback_enhancement` | Tech/Communication Pullback Enhancement | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform` | `monthly` | `QQQ` | `parallel_cash_buffer_branch` | `runtime_enabled` |
| `mega_cap_leader_rotation_dynamic_top20` | Mega Cap Leader Rotation Dynamic Top20 | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform` | `monthly` | `QQQ` | `concentrated_leader_rotation` | `runtime_enabled` |
| `mega_cap_leader_rotation_aggressive` | Mega Cap Leader Rotation Aggressive | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform` | `monthly` | `QQQ` | `aggressive_leader_rotation` | `runtime_enabled` |
| `dynamic_mega_leveraged_pullback` | Dynamic Mega Leveraged Pullback | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform` | `monthly snapshot + daily runtime` | `QQQ` | `offensive_leveraged_pullback` | `runtime_enabled` |
| `tqqq_growth_income` | TQQQ Growth Income | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform` | `daily` | `QQQ` | `offensive_dual_drive` | `runtime_enabled` |
| `soxl_soxx_trend_income` | SOXL/SOXX Semiconductor Trend Income | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform` | `daily` | `SOXX` | `sector_offensive_income` | `runtime_enabled` |

These strategies are consumed by platform repositories through `QuantPlatformKit` strategy contracts and component loaders. Canonical profile keys are the runtime-facing layer; display names are the human-facing layer. Compatibility here means the strategy is structurally usable on that broker stack. Each deployment explicitly selects its strategy with `STRATEGY_PROFILE`; platform repositories own rollout enablement and broker-specific runtime wiring.

Cadence here is the strategy-level intent. Platform repositories own the actual
Cloud Scheduler / GitHub Actions cron settings:

- daily profiles: run once per trading day near the US close.
- `global_etf_rotation`: evaluate canary risk daily, but perform normal rotation
  only on the last NYSE trading day of March, June, September, and December.
- monthly snapshot profiles: publish feature snapshots monthly from
  `UsEquitySnapshotPipelines`, then execute once in the downstream runtime's
  monthly window.

### Account-size suitability

Current platform runtimes place **integer-share** orders. They do not assume
fractional-share execution. Small accounts can therefore diverge materially from
the weight-based research backtests, especially for multi-stock strategies and
high-priced ETFs. Live entrypoints do not hard-block small accounts, but they
emit `small_account_warning=true` in diagnostics when account equity is below
the suggested minimum.

| Canonical profile | Suggested minimum equity | Small-account behavior |
| --- | ---: | --- |
| `tqqq_growth_income` | `500 USD` | Most suitable for small accounts; TQQQ can usually trade, but BOXX/cash targets may drift. |
| `soxl_soxx_trend_income` | `1000 USD` | Can run with drift; SOXX/BOXX legs may be skipped when target value cannot buy 1 share. |
| `global_etf_rotation` | `3000 USD` | Top-2 ETF rotation can drift when selected ETFs are too expensive for the account. |
| `mega_cap_leader_rotation_dynamic_top20` | `10000 USD` | The strategy may collapse from 4 names to 1 name plus BOXX/cash. |
| `mega_cap_leader_rotation_aggressive` | `10000 USD` | The top-3 concentrated stock basket can collapse to fewer names; integer shares can materially change risk. |
| `dynamic_mega_leveraged_pullback` | `10000 USD` | The top3/max80 2x product backtest is not reproducible at 200-1000 USD. |
| `tech_communication_pullback_enhancement` (`qqq_tech_enhancement` legacy alias) | `10000 USD` | Small accounts reduce position count and single-name concentration rises. |
| `russell_1000_multi_factor_defensive` | `30000 USD` | The default 24-stock basket is not suitable for small accounts. |

The warning is advisory. It is meant to make dry-runs, Telegram messages, and
reports explicit about the gap between account size and backtest assumptions.

### Research candidates

- `mega_cap_leader_rotation_dynamic_top20`: runtime-enabled monthly profile for the historical dynamic top-20 mega-cap universe. It keeps the research defaults that held 4 names at a 25% single-name cap and uses QQQ 200-day trend to reduce stock exposure to 50% when QQQ is below trend.
- `mega_cap_leader_rotation_aggressive`: runtime-enabled monthly profile for higher-return mega-cap leader rotation. It uses the same feature snapshot contract, defaults to top-3 at a 35% single-name cap, and does not de-risk on QQQ trend by default. Historical static expanded-pool research is higher-return but has lookback bias; production should consume a transparent monthly snapshot.
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
- Run the current live configuration as a no-income `QQQ` / `TQQQ` dual-drive growth profile.
- Keep the legacy income and BOXX symbols in the managed universe so existing holdings can be reduced cleanly.

**Portfolio layers**
- Growth layer: `QQQ` and `TQQQ`; broker runtimes can replace the unlevered growth sleeve with a lower-price proxy such as `QQQM` while keeping `QQQ` as the signal source.
- Default active reserve: 2% cash plus 8% BOXX
- Legacy / cleanup layer: `BOXX`, `SPYI`, `QQQI`

**Signals and indicators**
- Uses daily `QQQ` history as the signal source.
- `dual_drive_unlevered_symbol` controls the tradable unlevered growth sleeve and defaults to `QQQ`.
- The live configuration uses `MA200`, `MA20`, and positive `MA20` slope.
- Retired ATR-staged sizing has been removed from the live TQQQ profile; `fixed_qqq_tqqq_pullback` is the only supported allocation mode.

**Default dual-drive rules (`QQQ` / `TQQQ`)**
- Entry requires `QQQ > MA200` and positive `MA20` slope.
- Once risk is active, the profile keeps `QQQ 45% / TQQQ 45% / BOXX 8% / cash 2%` while `QQQ` remains above `MA200`; a short-term negative `MA20` slope alone does not force an exit.
- If `QQQ` falls below `MA200`, the profile exits `QQQ` and `TQQQ`, keeps 2% cash, and parks the rest in `BOXX` by default.
- A below-`MA200` pullback state can still re-enable risk when `QQQ > MA20` and `MA20` slope is positive.

**Income-layer rules (`SPYI` / `QQQI`)**
- The live configuration sets `income_threshold_usd = 1_000_000_000`, so the income layer is disabled for normal account sizes.
- Lowering that threshold opts back into the legacy income sleeve.
- `QQQI_INCOME_RATIO` still decides the split between `QQQI` and `SPYI` when the income layer is enabled.

**Defense behavior (`BOXX` and cash)**
- The fixed dual-drive live configuration keeps a small cash buffer and uses BOXX for the remaining idle capital.
- `BOXX` remains a managed symbol so old BOXX holdings can be traded down if present.
- Downstream execution decides whether the gap to target is large enough to trade via a rebalance threshold.

**Current live profile settings**
- `ATTACK_ALLOCATION_MODE = fixed_qqq_tqqq_pullback`
- `DUAL_DRIVE_QQQ_WEIGHT = 0.45`, `DUAL_DRIVE_TQQQ_WEIGHT = 0.45`
- `DUAL_DRIVE_UNLEVERED_SYMBOL = QQQ`
- `DUAL_DRIVE_CASH_RESERVE_RATIO = 0.02`
- `INCOME_THRESHOLD_USD = 1000000000`
- `CASH_RESERVE_RATIO = 0.02`
- `EXECUTION_CASH_RESERVE_RATIO = 0.0`
- `REBALANCE_THRESHOLD_RATIO = 0.01`

### soxl_soxx_trend_income

**Objective**
- Use the optimized `SOXX`-gated tiered blend profile for semiconductor exposure.
- Keep a dedicated income sleeve for larger accounts without forcing that sleeve to shrink during normal trading-layer changes.

**Portfolio layers**
- Trading layer: `SOXL`, `SOXX`, `BOXX`
- Income layer: `QQQI`, `SPYI`

**Trading-layer rules**
- The current live mode uses a tiered `SOXX` trend gate to avoid relying on one all-or-nothing threshold.
- If `SOXX > MA140 * 1.08`, the core sleeve targets `SOXL 70% + SOXX 20%`.
- If `SOXX > MA140 * 1.06`, or an existing SOXL sleeve has not broken `MA140 * 0.98`, the core sleeve targets `SOXL 65% + SOXX 20%`.
- If the gate is off, the core sleeve holds defensive `SOXX 15%`.
- Unused trading-layer capital is parked in `BOXX`.

**Sizing behavior**
- The tiered gate directly sets core-sleeve exposure: full, mid, or defensive.
- There is no separate account-size deploy-ratio decay in the live SOXL/SOXX profile.
- The downstream runtime also keeps a cash reserve and only trades when the rebalance gap is large enough.

**Income-layer rules**
- The income layer starts only after total strategy equity crosses `income_layer_start_usd`.
- It ramps linearly to `income_layer_max_ratio` by `2x` that threshold.
- Existing income holdings are locked with `max(current_income_layer_value, desired_income_layer_value)`, so the layer only adds capital instead of force-selling down.
- New income allocation is split by configurable `QQQI` / `SPYI` weights.

**Current live LongBridge profile settings**
- `TREND_MA_WINDOW = 140`
- `CASH_RESERVE_RATIO = 0.03`
- `MIN_TRADE_RATIO = 0.01`, `MIN_TRADE_FLOOR = 100 USD`
- `REBALANCE_THRESHOLD_RATIO = 0.01`
- `ATTACK_ALLOCATION_MODE = soxx_gate_tiered_blend`
- `BLEND_GATE_SOXL_WEIGHT = 0.70`, `BLEND_GATE_MID_SOXL_WEIGHT = 0.65`
- `BLEND_GATE_ACTIVE_SOXX_WEIGHT = 0.20`, `BLEND_GATE_DEFENSIVE_SOXX_WEIGHT = 0.15`
- Gate buffers: entry `8%`, mid `6%`, exit `2%`
- Income layer starts at `150000 USD`, caps at `15%`
- Income split: `QQQI 70%`, `SPYI 30%`

---

<a id="中文"></a>
## 中文

这是 `QuantStrategyLab` 的独立美股策略仓。

这个仓库负责**纯策略层**：信号、仓位、目标权重计算，以及策略元数据。下游平台仓库继续负责券商适配、下单方式、调度、密钥和通知。

### 契约边界

当前主线集成方式已经固定为：

- live profile 暴露 manifest 驱动的统一 entrypoint
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
| `mega_cap_leader_rotation_aggressive` | Mega Cap 激进龙头轮动 | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform` | 月频 | 更激进的 mega-cap 龙头轮动，默认 top3、单票 35%，不因 QQQ 趋势默认降仓 |
| `dynamic_mega_leveraged_pullback` | Mega Cap 2x 回调策略 | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform` | 月频 snapshot + 日频运行 | 动态 mega-cap top15 池里选 top3，使用 QQQ 200SMA/ATR 门槛控制 2x 做多产品仓位，剩余资金停 BOXX |
| `tqqq_growth_income` | TQQQ 增长收益 | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform` | 日频 | `QQQ` / `TQQQ` 双轮增长，默认 45% / 45% / 8% BOXX / 2% 现金 |
| `soxl_soxx_trend_income` | SOXL/SOXX 半导体趋势收益 | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform` | 日频 | SOXL / SOXX 趋势切换，剩余资金停在 BOXX，并叠加收入层 |

这些策略通过 `QuantPlatformKit` 提供的策略契约和组件加载接口，被各个平台仓库引用。运行时和部署配置统一使用 canonical profile key。
这里的策略频率表达的是策略层意图；实际 Cloud Scheduler / GitHub Actions
cron 配置由各个平台仓库负责：

- 日频策略：每个美股交易日临近收盘运行一次。
- `global_etf_rotation`：每日检查 canary 风险，但正常轮动只在
  `3 / 6 / 9 / 12` 月最后一个 NYSE 交易日触发。
- 月频 snapshot 策略：由 `UsEquitySnapshotPipelines` 按月发布 feature
  snapshot，再由下游运行时在月度窗口内执行一次。

### 小资金适用性

当前平台运行时按**整数股**下单，不假设碎股执行。因此小账户会明显偏离按权重回测得到的收益和回撤，尤其是多股票组合和高价 ETF。live entrypoint 不会硬性禁止小账户运行，但当账户净值低于建议资金时，会在 diagnostics 里输出 `small_account_warning=true`。

| Canonical profile | 建议最低资金 | 小资金表现 |
| --- | ---: | --- |
| `tqqq_growth_income` | `500 USD` | 最适合小账户；通常能买到 TQQQ，但 BOXX / 现金层会有偏差。 |
| `soxl_soxx_trend_income` | `1000 USD` | 可以运行但会偏离；SOXX / BOXX 目标金额不够买 1 股时会跳过。 |
| `global_etf_rotation` | `3000 USD` | Top2 ETF 轮动遇到高价 ETF 时会明显偏离。 |
| `mega_cap_leader_rotation_dynamic_top20` | `10000 USD` | 可能从 4 只股票降成 1 只股票加 BOXX / 现金。 |
| `mega_cap_leader_rotation_aggressive` | `10000 USD` | top3 集中持股可能退化成更少股票，整数股会明显改变风险。 |
| `dynamic_mega_leveraged_pullback` | `10000 USD` | top3 / max80 的 2x 产品回测，200-1000 USD 账户无法原样复现。 |
| `tech_communication_pullback_enhancement`（历史别名 `qqq_tech_enhancement`） | `10000 USD` | 小账户会降低持仓数，单票集中度上升。 |
| `russell_1000_multi_factor_defensive` | `30000 USD` | 默认 24 只股票组合，不适合小账户。 |

这个提示只是软警告。目的是让 dry-run、Telegram 通知和报告明确显示：当前账户资金量和研究回测假设之间存在差距。

### 研究候选策略

- `mega_cap_leader_rotation_dynamic_top20`：已注册为 runtime-enabled 月频 profile，使用历史动态 mega-cap top20 池，默认选 4 只、单票 25%，QQQ 跌破 200 日线时股票仓位降到 50%。
- `mega_cap_leader_rotation_aggressive`：已注册为 runtime-enabled 月频 profile，目标是更高收益的 mega-cap 龙头轮动。默认 top3、单票 35%，不因 QQQ 趋势默认降仓；静态 expanded 池历史回测更高但有后视偏差，实盘应消费透明的月度 snapshot。
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
- 当前实盘配置采用不带收入层的 `QQQ` / `TQQQ` 双轮增长策略。
- 继续把旧收入层和 BOXX 资产留在管理列表里，方便把已有持仓平滑降下来。

**资产层级**
- 增长层：`QQQ`、`TQQQ`；券商运行时可以把非杠杆增长袖子换成低单价代理，例如 `QQQM`，但主信号仍使用 `QQQ`。
- 默认激活时：2% 现金加 8% BOXX
- 旧持仓清理 / 兼容层：`BOXX`、`SPYI`、`QQQI`

**信号和指标**
- 以 `QQQ` 的日线数据作为主信号源。
- `dual_drive_unlevered_symbol` 控制实际交易的非杠杆增长袖子，默认是 `QQQ`。
- 当前实盘配置使用 `MA200`、`MA20` 和正向 `MA20` 斜率。
- 旧 ATR 分段仓位已经从 live TQQQ profile 移除；当前只支持 `fixed_qqq_tqqq_pullback`。

**默认双轮规则（`QQQ` / `TQQQ`）**
- 入场需要 `QQQ > MA200` 且 `MA20` 斜率为正。
- 一旦进入风险状态，只要 `QQQ` 仍在 `MA200` 上方，就维持 `QQQ 45% / TQQQ 45% / BOXX 8% / 现金 2%`；短期 `MA20` 斜率转负不会单独触发离场。
- 如果 `QQQ` 跌破 `MA200`，默认退出 `QQQ` 和 `TQQQ`，保留 2% 现金，其余转入 `BOXX`。
- 在 `MA200` 下方也保留一段回调参与逻辑：当 `QQQ > MA20` 且 `MA20` 斜率为正时，可重新打开风险仓位。

**收入层规则（`SPYI` / `QQQI`）**
- 实盘配置把 `income_threshold_usd` 设为 `1_000_000_000`，普通账户规模下等于关闭收入层。
- 如果以后要重新启用收入层，可以把这个阈值调低。
- `QQQI_INCOME_RATIO` 仍然决定收入层启用时 `QQQI` 和 `SPYI` 的拆分比例。

**防守行为（`BOXX` 与现金）**
- fixed dual-drive 实盘配置只保留一小部分现金，剩余闲置资金进入 BOXX。
- `BOXX` 仍保留为管理资产，方便清理旧 BOXX 持仓。
- 是否真的下单，由下游执行层再结合再平衡阈值判断。

**当前 live profile 配置值**
- `ATTACK_ALLOCATION_MODE = fixed_qqq_tqqq_pullback`
- `DUAL_DRIVE_QQQ_WEIGHT = 0.45`，`DUAL_DRIVE_TQQQ_WEIGHT = 0.45`
- `DUAL_DRIVE_UNLEVERED_SYMBOL = QQQ`
- `DUAL_DRIVE_CASH_RESERVE_RATIO = 0.02`
- `INCOME_THRESHOLD_USD = 1000000000`
- `CASH_RESERVE_RATIO = 0.02`
- `EXECUTION_CASH_RESERVE_RATIO = 0.0`
- `REBALANCE_THRESHOLD_RATIO = 0.01`

### soxl_soxx_trend_income

**策略目标**
- 使用优化后的 `SOXX` 趋势分层闸门半导体策略。
- 给大账户保留收入层，但不因为交易层切换就强制把收入层减回来。

**资产层级**
- 交易层：`SOXL`、`SOXX`、`BOXX`
- 收入层：`QQQI`、`SPYI`

**交易层规则**
- 当前 live 配置使用 `SOXX` 趋势分层闸门，避免仓位完全依赖单一开关。
- 如果 `SOXX > MA140 * 1.08`，核心层目标为 `SOXL 70% + SOXX 20%`。
- 如果 `SOXX > MA140 * 1.06`，或已有 SOXL 仓位尚未跌破 `MA140 * 0.98`，核心层目标为 `SOXL 65% + SOXX 20%`。
- 如果趋势闸门关闭，核心层防守目标为 `SOXX 15%`。
- 交易层没有部署出去的资金停在 `BOXX`。

**仓位规则**
- 分层闸门直接决定核心层风险暴露：full、mid 或 defensive。
- live SOXL/SOXX profile 不再保留单独的账户规模 deploy-ratio 衰减。
- 下游运行层另外还会保留现金储备，并且只有偏离目标足够大时才触发调仓。

**收入层规则**
- 总策略权益超过 `income_layer_start_usd` 才启动收入层。
- 到 `2 倍阈值` 时，收入层线性抬升到 `income_layer_max_ratio`。
- 收入层采用 `max(current_income_layer_value, desired_income_layer_value)` 锁定已有收入资产，所以默认只增配，不主动减配。
- 新增收入资金按可配置的 `QQQI / SPYI` 比例拆分。

**当前 LongBridge live profile 配置值**
- `TREND_MA_WINDOW = 140`
- `CASH_RESERVE_RATIO = 0.03`
- `MIN_TRADE_RATIO = 0.01`，`MIN_TRADE_FLOOR = 100 USD`
- `REBALANCE_THRESHOLD_RATIO = 0.01`
- `ATTACK_ALLOCATION_MODE = soxx_gate_tiered_blend`
- `BLEND_GATE_SOXL_WEIGHT = 0.70`，`BLEND_GATE_MID_SOXL_WEIGHT = 0.65`
- `BLEND_GATE_ACTIVE_SOXX_WEIGHT = 0.20`，`BLEND_GATE_DEFENSIVE_SOXX_WEIGHT = 0.15`
- 闸门缓冲：入场 `8%`，中档 `6%`，退出 `2%`
- 收入层起点 `150000 USD`，上限 `15%`
- 收入层配比：`QQQI 70%`，`SPYI 30%`
