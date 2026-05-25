# UsEquityStrategies

[English](#english) | [中文](#中文)

---

<a id="english"></a>
## English

Standalone `us_equity` strategy repository for QuantStrategyLab platforms.

This repository is the strategy layer: it owns pure signal, allocation, and target-computation logic plus strategy metadata. Downstream platform repositories still own broker adapters, order routing, schedule, secrets, and notifications.

### Contract boundary

The current integration path is:

- runtime profiles expose manifest-backed unified entrypoints
- downstream platforms load those entrypoints through `QuantPlatformKit`
- strategy outputs stay inside the shared `StrategyDecision` contract
- broker-specific execution order, UI rows, and notification layout stay in platform repositories

Legacy strategy functions may still exist as internal adapters, but downstream runtimes should treat `entrypoints/` and manifests as the supported integration surface.

### Live broker authoring standard

Greenfield `us_equity` profiles should be authored once against the shared
contract and be structurally portable across all live broker runtimes from the
first PR:

- `InteractiveBrokersPlatform`
- `CharlesSchwabPlatform`
- `LongBridgePlatform`
- `FirstradePlatform`

This means:

- keep strategy math, required inputs, and target semantics shared here
- add runtime adapters upstream instead of creating platform-local strategy
  forks
- treat rollout enablement as a downstream platform decision, not a reason to
  omit shared portability by default

If one runtime is intentionally unsupported in a PR, call it out explicitly and
keep the portability gap visible in review notes. Do not plan to “backfill
broker compatibility later” as the default workflow.

### Authoring and portability guides

- [`docs/us_equity_strategy_template.md`](./docs/us_equity_strategy_template.md): template for adding a new US equity profile in this repository.
- [`docs/us_equity_portability_checklist.md`](./docs/us_equity_portability_checklist.md): reviewer checklist before enabling a profile on broker runtimes.
- [`docs/us_equity_contract_gap_matrix.md`](./docs/us_equity_contract_gap_matrix.md): runtime-enabled profile contract gaps versus the cross-platform target.
- [`docs/us_equity_value_mode_input_contract.md`](./docs/us_equity_value_mode_input_contract.md): fixed canonical input contract for the two current value-mode profiles.
- [`docs/us_equity_strategy_status.zh-CN.md`](./docs/us_equity_strategy_status.zh-CN.md): Chinese operator-facing status handbook for switchable profiles, input modes, research candidates, and archived backtest evidence.
- [`docs/research/global_etf_confidence_vol_gate.md`](./docs/research/global_etf_confidence_vol_gate.md): Global ETF confidence plus relative-volatility gate research notes.
- [`docs/research/mega_cap_leader_rotation.md`](./docs/research/mega_cap_leader_rotation.md): mega-cap leader rotation research notes and Top50 balanced profile notes.

### Strategy index

| Canonical profile | Display name | Compatible platforms | Cadence | Benchmark | Role | Status |
| --- | --- | --- | --- | --- | --- | --- |
| `global_etf_rotation` | Global ETF Rotation | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform`, `FirstradePlatform` | `quarterly + daily canary` | `VOO` | `defensive_rotation` | `runtime_enabled` |
| `global_etf_confidence_vol_gate` | Global ETF Confidence Vol Gate | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform`, `FirstradePlatform` | `quarterly + daily canary` | `VOO` | `defensive_rotation_research_candidate` | `runtime_enabled` |
| `russell_1000_multi_factor_defensive` | Russell 1000 Multi-Factor | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform`, `FirstradePlatform` | `monthly` | `SPY` | `defensive_stock_baseline` | `runtime_enabled` |
| `tech_communication_pullback_enhancement` | Tech/Communication Pullback Enhancement | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform`, `FirstradePlatform` | `monthly` | `QQQ` | `parallel_cash_buffer_branch` | `runtime_enabled` |
| `mega_cap_leader_rotation_top50_balanced` | Mega Cap Leader Rotation Top50 Balanced | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform`, `FirstradePlatform` | `monthly` | `QQQ` | `balanced_leader_rotation` | `runtime_enabled` |
| `tqqq_growth_income` | TQQQ Growth Income | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform`, `FirstradePlatform` | `daily` | `QQQ` | `offensive_dual_drive` | `runtime_enabled` |
| `soxl_soxx_trend_income` | SOXL/SOXX Semiconductor Trend Income | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform`, `FirstradePlatform` | `daily` | `SOXX` | `sector_offensive_income` | `runtime_enabled` |

`runtime_enabled` strategies are consumed by platform repositories through `QuantPlatformKit` strategy contracts and component loaders. Canonical profile keys are the runtime-facing layer; display names are the human-facing layer. Compatibility here means the strategy is structurally usable on the listed live broker runtime stack. Each deployment explicitly selects its strategy with `STRATEGY_PROFILE`; platform repositories own runtime-specific wiring.

Cadence here is the strategy-level intent. Platform repositories own the actual
Cloud Scheduler / GitHub Actions cron settings:

- daily profiles: run once per trading day near the US close.
- daily profiles now also publish a runtime execution-timing contract through
  shared metadata:
  - `signal_date`
  - `effective_date`
  - `execution_timing_contract`
  - `signal_effective_after_trading_days`
- the current daily runtime profiles (`global_etf_rotation`,
  `global_etf_confidence_vol_gate`,
  `tqqq_growth_income`, `soxl_soxx_trend_income`) are tagged as
  `next_trading_day` strategies at the strategy/runtime contract layer, so
  downstream runtimes and audit reports do not need to infer this from prose.
- `global_etf_rotation`: evaluate canary risk daily, but perform normal rotation
  only on the last NYSE trading day of March, June, September, and December.
- monthly snapshot profiles: publish feature snapshots monthly from
  `UsEquitySnapshotPipelines`, then execute once in the downstream runtime's
  monthly window.

### Account-size suitability

Platform runtimes must adapt order sizing to broker capability. Schwab remains
integer-share only; LongBridge supports fractional-share execution. IBKR remains
whole-share by default because TWS API fractional order support must be verified
per account/API path before enabling the runtime quantity step. Small accounts
can still diverge from weight-based research backtests on integer-share platforms
or symbols that do not support fractional execution. Live entrypoints do not
hard-block small accounts, but they emit `small_account_warning=true` in
diagnostics when account equity is below the suggested minimum.

| Canonical profile | Suggested minimum equity | Small-account behavior |
| --- | ---: | --- |
| `tqqq_growth_income` | `500 USD` | Most suitable for small accounts; TQQQ can usually trade, but BOXX/cash targets may drift. |
| `soxl_soxx_trend_income` | `1000 USD` | Can run with drift on integer-share platforms; fractional-share runtimes can express the small SOXX/BOXX legs more closely. |
| `global_etf_rotation` (`global_etf_confidence_vol_gate` legacy alias) | `3000 USD` | Top-2 ETF rotation can drift when selected ETFs are too expensive for the account; the legacy alias shares the same execution caveats. |
| `mega_cap_leader_rotation_top50_balanced` | `10000 USD` | The fixed 50% Top2 / 50% Top4 sleeve blend can drift when integer shares cannot represent the intended unequal weights. |
| `tech_communication_pullback_enhancement` (`qqq_tech_enhancement` legacy alias) | `10000 USD` | Small accounts reduce position count and single-name concentration rises. |
| `russell_1000_multi_factor_defensive` | `30000 USD` | The default 24-stock basket is not suitable for small accounts. |

The warning is advisory. It is meant to make dry-runs, Telegram messages, and
reports explicit about the gap between account size and backtest assumptions.

### Research candidates and archive

- `mega_cap_leader_rotation_top50_balanced`: runtime-enabled monthly profile for the current Top50 balanced candidate. It consumes a transparent Top50 monthly snapshot and runs a fixed 50% Top2 cap50 sleeve plus 50% Top4 cap25 sleeve, with no broad QQQ trend de-risking by default.
- `global_etf_confidence_vol_gate`: legacy alias and comparison name for the canonical `global_etf_rotation` runtime profile. It keeps the same universe and canary defense, uses the same SMA250 / z-gap / relative-volatility gate parameter set, and remains available for explicit regression checks.
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
- Trend filter: candidate ETF must be above its 250-day SMA.
- Hold bonus: an existing holding receives `+2%` score bonus to reduce turnover.
- Daily canary check: if all 4 canary assets have negative or missing momentum, the strategy goes `100% BIL` immediately.

**Rebalance behavior**
- Normal rotation only happens on the last NYSE trading day of March, June, September, and December.
- On a rebalance day, the strategy ranks the eligible universe and selects the top 2 ETFs.
- Selected ETFs are normally equally weighted (`50 / 50`), but the default confidence gate can tilt them to `75 / 25` when the Top1 lead is strong and not materially more volatile than Top2.
- If fewer than 2 names survive, the unused slot is parked in `BIL`.
- On non-rebalance days, the strategy returns no target change unless the canary emergency path is triggered.

**Confidence-volatility alias**
- `global_etf_rotation` is the canonical runtime profile and defaults to the SMA250 confidence-gated configuration.
- `global_etf_confidence_vol_gate` now resolves to the same runtime profile and remains only as an explicit comparison alias for regression checks.
- The gate concentrates only when Top1 is clearly ahead and not materially more volatile than Top2; otherwise it remains equal-weight Top2.

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
- Run the default `QQQ` / `TQQQ` dual-drive growth profile with an additive income sleeve.
- Keep BOXX and income symbols in the managed universe so existing holdings can be maintained and added to cleanly.

**Portfolio layers**
- Growth layer: `QQQ` and `TQQQ`; broker runtimes can replace the unlevered growth sleeve with a lower-price proxy such as `QQQM` while keeping `QQQ` as the signal source.
- Default active reserve: 2% cash plus 8% BOXX
- Income layer: `SCHD`, `DGRO`, `SGOV`, `SPYI`, `QQQI`

**Signals and indicators**
- Uses daily `QQQ` history as the signal source.
- `dual_drive_unlevered_symbol` controls the tradable unlevered growth sleeve and defaults to `QQQ`.
- The default configuration uses `MA200`, `MA20`, and positive `MA20` slope.
- Retired ATR-staged sizing has been removed from the TQQQ profile; `fixed_qqq_tqqq_pullback` is the only supported allocation mode.

**Default dual-drive rules (`QQQ` / `TQQQ`)**
- Entry requires `QQQ > MA200` and positive `MA20` slope.
- Once risk is active, the profile keeps `QQQ 45% / TQQQ 45% / BOXX 8% / cash 2%` while `QQQ` remains above `MA200`; a short-term negative `MA20` slope alone does not force an exit.
- If `QQQ` falls below `MA200`, the profile exits `QQQ` and `TQQQ`, keeps 2% cash, and parks the rest in `BOXX` by default.
- A below-`MA200` pullback state can still re-enable risk when `QQQ > MA20`, `MA20` slope is positive, and `QQQ` has rebounded from its rolling 20-day low by more than the dynamic volatility-scaled gate. The default gate is `2.0x` the recent 20-day `QQQ` daily return volatility, which avoids a fixed 3% constant while still filtering weak MA200 chop without changing the normal above-`MA200` trend rule.

**Income-layer rules**
- The sleeve is explicitly controlled by `income_layer_enabled`; keeping it in config makes the income layer an optional risk-control overlay per strategy.
- The default configuration starts the income layer at `income_layer_start_usd = 150000`.
- Runtime defaults use `income_layer_ratio_mode = log_cap`: the target ratio grows on a logarithmic curve up to the configured safety cap.
- The hard safety cap is `income_layer_max_ratio = 50%`, selected from the current full-income replay as the highest-return setting that kept standard windows inside the SPY drawdown benchmark at `1M USD` starting equity.
- Default stress model: income sleeve stress drawdown `30%`, starting loss budget `8%` of account equity, decaying by `1%` per doubling until the `6%` floor.
- Existing income holdings are locked with `max(current_income_layer_value, desired_income_layer_value)`, so the layer adds capital instead of force-selling down.
- New income allocation defaults to `SCHD 30% / DGRO 20% / SGOV 40% / SPYI 8% / QQQI 2%`.
- `income_threshold_usd` and `qqqi_income_ratio` remain as compatibility inputs for older callers.

**Defense behavior (`BOXX` and cash)**
- The fixed dual-drive configuration keeps a small cash buffer and uses BOXX for the remaining idle capital.
- `BOXX` remains a managed symbol so old BOXX holdings can be traded down if present.
- Downstream execution decides whether the gap to target is large enough to trade via a rebalance threshold.

**Default runtime profile settings**
- `ATTACK_ALLOCATION_MODE = fixed_qqq_tqqq_pullback`
- `DUAL_DRIVE_QQQ_WEIGHT = 0.45`, `DUAL_DRIVE_TQQQ_WEIGHT = 0.45`
- `DUAL_DRIVE_UNLEVERED_SYMBOL = QQQ`
- `DUAL_DRIVE_CASH_RESERVE_RATIO = 0.02`
- `DUAL_DRIVE_PULLBACK_REBOUND_WINDOW = 20`
- `DUAL_DRIVE_PULLBACK_REBOUND_THRESHOLD_MODE = volatility_scaled`
- `DUAL_DRIVE_PULLBACK_REBOUND_VOLATILITY_MULTIPLIER = 2.0`
- `DUAL_DRIVE_PULLBACK_REBOUND_THRESHOLD = 0.0` (fixed-mode fallback only)
- `INCOME_LAYER_START_USD = 150000`
- `INCOME_LAYER_RATIO_MODE = log_cap`
- `INCOME_LAYER_MAX_RATIO = 0.50`
- `INCOME_LAYER_STRESS_DRAWDOWN_RATIO = 0.30`
- `INCOME_LAYER_BASE_LOSS_BUDGET_RATIO = 0.08`
- `INCOME_LAYER_MIN_LOSS_BUDGET_RATIO = 0.06`
- `INCOME_LAYER_ALLOCATIONS = SCHD 30% / DGRO 20% / SGOV 40% / SPYI 8% / QQQI 2%`
- `INCOME_THRESHOLD_USD = 150000` (legacy alias)
- `CASH_RESERVE_RATIO = 0.02`
- `EXECUTION_CASH_RESERVE_RATIO = 0.0`
- `REBALANCE_THRESHOLD_RATIO = 0.01`

### soxl_soxx_trend_income

**Objective**
- Use the optimized `SOXX`-gated tiered blend profile for semiconductor exposure.
- Keep a dedicated income sleeve for larger accounts without forcing that sleeve to shrink during normal trading-layer changes.

**Portfolio layers**
- Trading layer: `SOXL`, `SOXX`, `BOXX`
- Income / ballast layer: `SCHD`, `DGRO`, `SGOV`, `SPYI`, `QQQI`

**Trading-layer rules**
- The default runtime mode uses a tiered `SOXX` trend gate to avoid relying on one all-or-nothing threshold.
- If `SOXX > MA140 * 1.08`, the core sleeve targets `SOXL 70% + SOXX 20%`.
- If `SOXX > MA140 * 1.06`, or an existing SOXL sleeve has not broken `MA140 * 0.98`, the core sleeve targets `SOXL 65% + SOXX 20%`.
- If the gate is off, the core sleeve holds defensive `SOXX 15%`.
- Overheat controls are active on the default runtime profile: when the base tier is full or mid, `SOXX` RSI14 above the effective threshold and/or a break above the upper Bollinger band downgrade the tier by one step per trigger.
- The runtime RSI threshold is dynamic: `max(70, prior 252 trading days RSI14 90th percentile)`, with `70` as the fallback floor when the dynamic indicator is unavailable.
- The default volatility delever gate redirects SOXL exposure into SOXX when `SOXX` 10-day annualized realized volatility is at least `50%`.
- Unused trading-layer capital is parked in `BOXX`.

**Sizing behavior**
- The tiered gate directly sets core-sleeve exposure: full, mid, or defensive.
- There is no separate account-size deploy-ratio decay in the SOXL/SOXX profile.
- The downstream runtime also keeps a cash reserve and only trades when the rebalance gap is large enough.

**Income-layer rules**
- The sleeve is explicitly controlled by `income_layer_enabled`; each strategy can keep its own threshold, cap, ratio mode, and allocation basket.
- The income layer starts only after total strategy equity crosses `income_layer_start_usd`.
- Runtime defaults use `log_cap`: logarithmic growth first, then a hard cap tuned by full-income replay against the SPY drawdown benchmark.
- The hard safety cap is `income_layer_max_ratio = 90%`; SOXL keeps a much larger safety layer because the semiconductor leveraged core needs more ballast to keep combined account drawdown inside SPY once the account is above the income-layer threshold.
- Existing income holdings are locked with `max(current_income_layer_value, desired_income_layer_value)`, so the layer only adds capital instead of force-selling down.
- New income allocation uses the configurable diversified `income_layer_allocations` basket.

**Default runtime profile settings**
- `TREND_MA_WINDOW = 140`
- `CASH_RESERVE_RATIO = 0.03`
- `MIN_TRADE_RATIO = 0.01`, `MIN_TRADE_FLOOR = 100 USD`
- `REBALANCE_THRESHOLD_RATIO = 0.01`
- `ATTACK_ALLOCATION_MODE = soxx_gate_tiered_blend`
- `BLEND_GATE_SOXL_WEIGHT = 0.70`, `BLEND_GATE_MID_SOXL_WEIGHT = 0.65`
- `BLEND_GATE_ACTIVE_SOXX_WEIGHT = 0.20`, `BLEND_GATE_DEFENSIVE_SOXX_WEIGHT = 0.15`
- RSI overheat enabled with dynamic threshold `max(70, rolling 252d RSI14 q90)`
- Bollinger overheat enabled; stacked RSI + Bollinger triggers can downgrade full directly to defensive
- Gate buffers: entry `8%`, mid `6%`, exit `2%`
- Income layer starts at `150000 USD`, uses `log_cap`, and hard-caps at `90%`
- Income basket: `SCHD 20%`, `DGRO 10%`, `SGOV 65%`, `SPYI 4%`, `QQQI 1%`

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
- [`docs/us_equity_contract_gap_matrix.md`](./docs/us_equity_contract_gap_matrix.md)：runtime-enabled profile 距离跨平台目标契约的差异矩阵。
- [`docs/us_equity_value_mode_input_contract.md`](./docs/us_equity_value_mode_input_contract.md)：两条 value-mode 策略的 canonical 输入契约定稿。
- [`docs/us_equity_strategy_status.zh-CN.md`](./docs/us_equity_strategy_status.zh-CN.md)：中文运行手册，集中说明可切换 profile、输入类型、研究候选和已归档回测证据。
- [`docs/research/global_etf_confidence_vol_gate.md`](./docs/research/global_etf_confidence_vol_gate.md)：Global ETF 置信度 + 相对波动过滤研究说明。
- [`docs/research/mega_cap_leader_rotation.md`](./docs/research/mega_cap_leader_rotation.md)：巨头强者轮动的研究说明，以及 dynamic top20 运行 profile 说明。

### 策略索引

| Canonical profile | 显示名 | 兼容平台仓库 | 策略频率 | 核心思路 |
| --- | --- | --- | --- | --- |
| `global_etf_rotation` | 全球 ETF 轮动 | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform`, `FirstradePlatform` | 季度调仓 + 每日 canary | 22 只全球 ETF 的季度 Top 2 轮动，默认使用 SMA250 置信度 + 相对波动门控 |
| `global_etf_confidence_vol_gate` | 全球 ETF 置信度波动过滤 | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform`, `FirstradePlatform` | 季度调仓 + 每日 canary | 与 `global_etf_rotation` 同源的显式对照入口，保留 75/25 规则与门控参数 |
| `russell_1000_multi_factor_defensive` | 罗素1000多因子 | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform`, `FirstradePlatform` | 月频 | Russell 1000 个股月频 price-only 选股，带 SPY + breadth 防守和 BOXX 停泊 |
| `tech_communication_pullback_enhancement` | 科技通信回调增强 | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform`, `FirstradePlatform` | 月频 | tech-heavy 月频个股选择，做受控回调，并显式保留 BOXX 缓冲 |
| `mega_cap_leader_rotation_top50_balanced` | Mega Cap Top50 平衡龙头轮动 | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform`, `FirstradePlatform` | 月频 | 当前 Top50 平衡候选，固定 50% Top2 cap50 + 50% Top4 cap25，不因 QQQ 趋势默认降仓 |
| `tqqq_growth_income` | TQQQ 增长收益 | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform`, `FirstradePlatform` | 日频 | `QQQ` / `TQQQ` 双轮增长，默认 45% / 45% / 8% BOXX / 2% 现金 |
| `soxl_soxx_trend_income` | SOXL/SOXX 半导体趋势收益 | `InteractiveBrokersPlatform`, `CharlesSchwabPlatform`, `LongBridgePlatform`, `FirstradePlatform` | 日频 | SOXL / SOXX 趋势切换，剩余资金停在 BOXX，并叠加收入层 |

`runtime_enabled` 策略通过 `QuantPlatformKit` 提供的策略契约和组件加载接口被各个平台仓库引用；`research_only` profile 保留定义、manifest、entrypoint 和 adapter 作为研究/回放存档，但不会进入平台 rollout allowlist。运行时和部署配置统一使用 canonical profile key。这里的“兼容平台”指当前 live broker runtime。
这里的策略频率表达的是策略层意图；实际 Cloud Scheduler / GitHub Actions
cron 配置由各个平台仓库负责：

- 日频策略：每个美股交易日临近收盘运行一次。
- `global_etf_rotation`：每日检查 canary 风险，但正常轮动只在
  `3 / 6 / 9 / 12` 月最后一个 NYSE 交易日触发；默认采用 SMA250
  置信度 + 相对波动门控。
- 月频 snapshot 策略：由 `UsEquitySnapshotPipelines` 按月发布 feature
  snapshot，再由下游运行时在月度窗口内执行一次。

### 小资金适用性

平台运行时必须按券商能力适配下单数量。Schwab 仍按整数股执行；LongBridge 支持碎股执行。IBKR 默认仍按整数股执行，因为 TWS API 是否能接受碎股单需要按账户/API 路径实测确认后才能打开运行时数量步进。小账户在整数股平台或不支持碎股的标的上仍会明显偏离按权重回测得到的收益和回撤。live entrypoint 不会硬性禁止小账户运行，但当账户净值低于建议资金时，会在 diagnostics 里输出 `small_account_warning=true`。

| Canonical profile | 建议最低资金 | 小资金表现 |
| --- | ---: | --- |
| `tqqq_growth_income` | `500 USD` | 最适合小账户；通常能买到 TQQQ，但 BOXX / 现金层会有偏差。 |
| `soxl_soxx_trend_income` | `1000 USD` | 整数股平台会有偏离；支持碎股的运行时可以更接近小额 SOXX / BOXX 目标仓位。 |
| `global_etf_rotation` | `3000 USD` | 默认档已切到 SMA250 + 置信度门控；Top2 ETF 轮动遇到高价 ETF 时仍会偏离。 |
| `global_etf_confidence_vol_gate` | `3000 USD` | 与 `global_etf_rotation` 同一执行约束；保留 75/25 对照入口时，小账户和整数股平台会更容易产生偏离。 |
| `mega_cap_leader_rotation_top50_balanced` | `10000 USD` | 固定 50% Top2 / 50% Top4 袖珍组合需要不等权持仓，小账户整数股会产生明显偏离。 |
| `tech_communication_pullback_enhancement`（历史别名 `qqq_tech_enhancement`） | `10000 USD` | 小账户会降低持仓数，单票集中度上升。 |
| `russell_1000_multi_factor_defensive` | `30000 USD` | 默认 24 只股票组合，不适合小账户。 |

这个提示只是软警告。目的是让 dry-run、Telegram 通知和报告明确显示：当前账户资金量和研究回测假设之间存在差距。

### 研究候选与存档

- `mega_cap_leader_rotation_top50_balanced`：已注册为 runtime-enabled 月频 profile，消费透明 Top50 月度 snapshot，运行固定 50% Top2 cap50 + 50% Top4 cap25 的组合，不默认使用宽基趋势降仓。
- `global_etf_confidence_vol_gate`：`global_etf_rotation` 的显式对照入口，保留同一标的池和 canary 防守。它使用同样的 SMA250 / z-gap / 相对波动门控参数，方便做回归对照和小样本验证。
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

**置信度 + 波动过滤变体**
- `global_etf_rotation` 现在默认使用 `sma_period=250`、`confidence_threshold=1.0`、`confidence_weighting_enabled=True`、`confidence_top1_weight=0.75`、`confidence_volatility_gate_enabled=True`、`confidence_volatility_window=126`、`confidence_volatility_max_ratio=1.3`。
- `global_etf_confidence_vol_gate` 保留为显式对照入口，参数与默认档一致，便于回放和回归比较。
- 只有 Top1 明显领先且相对 Top2 不显著更高波动时，才切到 `75 / 25`；否则仍保持 Top2 等权。

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
- 默认配置采用带加法收入层的 `QQQ` / `TQQQ` 双轮增长策略。
- 继续把 BOXX 和收入资产留在管理列表里，方便维护已有持仓并逐步增配。

**资产层级**
- 增长层：`QQQ`、`TQQQ`；券商运行时可以把非杠杆增长袖子换成低单价代理，例如 `QQQM`，但主信号仍使用 `QQQ`。
- 默认激活时：2% 现金加 8% BOXX
- 收入层：`SCHD`、`DGRO`、`SGOV`、`SPYI`、`QQQI`

**信号和指标**
- 以 `QQQ` 的日线数据作为主信号源。
- `dual_drive_unlevered_symbol` 控制实际交易的非杠杆增长袖子，默认是 `QQQ`。
- 默认配置使用 `MA200`、`MA20` 和正向 `MA20` 斜率。
- 旧 ATR 分段仓位已经从 TQQQ profile 移除；当前只支持 `fixed_qqq_tqqq_pullback`。

**默认双轮规则（`QQQ` / `TQQQ`）**
- 入场需要 `QQQ > MA200` 且 `MA20` 斜率为正。
- 一旦进入风险状态，只要 `QQQ` 仍在 `MA200` 上方，就维持 `QQQ 45% / TQQQ 45% / BOXX 8% / 现金 2%`；短期 `MA20` 斜率转负不会单独触发离场。
- 如果 `QQQ` 跌破 `MA200`，默认退出 `QQQ` 和 `TQQQ`，保留 2% 现金，其余转入 `BOXX`。
- 在 `MA200` 下方也保留一段回调参与逻辑：当 `QQQ > MA20`、`MA20` 斜率为正，且 `QQQ` 较滚动 20 日低点的反弹幅度超过动态波动率门槛时，可重新打开风险仓位。默认门槛是最近 20 日 `QQQ` 日收益波动率的 `2.0x`，避免使用固定 3% 常数，同时继续过滤较弱的 MA200 附近震荡，不改变 `MA200` 上方的主趋势规则。

**收入层规则**
- 收入层由 `income_layer_enabled` 显式控制；它是每个策略可选的风险/资金覆盖层，不是写死在策略里的分红附加项。
- 默认配置在 `income_layer_start_usd = 150000` 时启动收入层。
- 运行默认使用 `income_layer_ratio_mode = log_cap`：目标比例按对数曲线增长，最高到配置的安全上限。
- 硬安全上限是 `income_layer_max_ratio = 50%`；这是用当前真实收入层样本，在 `100 万 USD` 起始权益下按“标准窗口回撤不超过 SPY”筛出来的最高收益默认值。
- `log_loss_budget` 仍保留为可选模式，适合只想约束收入层自身压力亏损的账户。
- 收入层采用 `max(current_income_layer_value, desired_income_layer_value)` 锁定已有收入资产，所以默认只增配，不主动减配。
- 新增收入资金默认按 `SCHD 30% / DGRO 20% / SGOV 40% / SPYI 8% / QQQI 2%` 拆分。
- `income_threshold_usd` 和 `qqqi_income_ratio` 仍保留为旧调用方兼容参数。

**防守行为（`BOXX` 与现金）**
- fixed dual-drive 默认配置只保留一小部分现金，剩余闲置资金进入 BOXX。
- `BOXX` 仍保留为管理资产，方便清理旧 BOXX 持仓。
- 是否真的下单，由下游执行层再结合再平衡阈值判断。

**默认运行 profile 配置值**
- `ATTACK_ALLOCATION_MODE = fixed_qqq_tqqq_pullback`
- `DUAL_DRIVE_QQQ_WEIGHT = 0.45`，`DUAL_DRIVE_TQQQ_WEIGHT = 0.45`
- `DUAL_DRIVE_UNLEVERED_SYMBOL = QQQ`
- `DUAL_DRIVE_CASH_RESERVE_RATIO = 0.02`
- `DUAL_DRIVE_PULLBACK_REBOUND_WINDOW = 20`
- `DUAL_DRIVE_PULLBACK_REBOUND_THRESHOLD_MODE = volatility_scaled`
- `DUAL_DRIVE_PULLBACK_REBOUND_VOLATILITY_MULTIPLIER = 2.0`
- `DUAL_DRIVE_PULLBACK_REBOUND_THRESHOLD = 0.0`（仅作为 fixed 模式 fallback）
- `INCOME_LAYER_START_USD = 150000`
- `INCOME_LAYER_RATIO_MODE = log_cap`
- `INCOME_LAYER_MAX_RATIO = 0.50`
- `INCOME_LAYER_STRESS_DRAWDOWN_RATIO = 0.30`
- `INCOME_LAYER_BASE_LOSS_BUDGET_RATIO = 0.08`
- `INCOME_LAYER_MIN_LOSS_BUDGET_RATIO = 0.06`
- `INCOME_LAYER_ALLOCATIONS = SCHD 30% / DGRO 20% / SGOV 40% / SPYI 8% / QQQI 2%`
- `INCOME_THRESHOLD_USD = 150000`（旧参数别名）
- `CASH_RESERVE_RATIO = 0.02`
- `EXECUTION_CASH_RESERVE_RATIO = 0.0`
- `REBALANCE_THRESHOLD_RATIO = 0.01`

### soxl_soxx_trend_income

**策略目标**
- 使用优化后的 `SOXX` 趋势分层闸门半导体策略。
- 给大账户保留收入层，但不因为交易层切换就强制把收入层减回来。

**资产层级**
- 交易层：`SOXL`、`SOXX`、`BOXX`
- 收入 / 压舱层：`SCHD`、`DGRO`、`SGOV`、`SPYI`、`QQQI`

**交易层规则**
- 默认运行配置使用 `SOXX` 趋势分层闸门，避免仓位完全依赖单一开关。
- 如果 `SOXX > MA140 * 1.08`，核心层目标为 `SOXL 70% + SOXX 20%`。
- 如果 `SOXX > MA140 * 1.06`，或已有 SOXL 仓位尚未跌破 `MA140 * 0.98`，核心层目标为 `SOXL 65% + SOXX 20%`。
- 如果趋势闸门关闭，核心层防守目标为 `SOXX 15%`。
- 线上 profile 启用过热控制：基础档位为 full 或 mid 时，如果 `SOXX` RSI14 高于有效阈值，和/或价格突破布林上轨，会按触发项逐级降档。
- 线上 RSI 阈值为动态阈值：`max(70, 过去 252 个交易日 RSI14 的 90% 分位数)`；动态指标缺失时以 `70` 作为 fallback floor。
- 默认波动率降杠杆闸门：当 `SOXX` 10 日年化实际波动率不低于 `50%` 时，将 SOXL 暴露转向 SOXX。
- 交易层没有部署出去的资金停在 `BOXX`。

**仓位规则**
- 分层闸门直接决定核心层风险暴露：full、mid 或 defensive。
- SOXL/SOXX profile 不再保留单独的账户规模 deploy-ratio 衰减。
- 下游运行层另外还会保留现金储备，并且只有偏离目标足够大时才触发调仓。

**收入层规则**
- 收入层由 `income_layer_enabled` 显式控制；每个策略可以独立配置门槛、上限、增长模式和标的篮子。
- 总策略权益超过 `income_layer_start_usd` 才启动收入层。
- 运行默认使用 `log_cap`：先按对数曲线增长，再由硬上限控制组合风险。
- 硬安全上限是 `income_layer_max_ratio = 90%`；SOXL 半导体杠杆核心波动更高，所以资金过门槛后需要更大的安全/收入层，才能让组合回撤压到 SPY 口径以内。
- 收入层采用 `max(current_income_layer_value, desired_income_layer_value)` 锁定已有收入资产，所以默认只增配，不主动减配。
- 新增收入资金按可配置的多资产 `income_layer_allocations` 篮子拆分。

**默认运行 profile 配置值**
- `TREND_MA_WINDOW = 140`
- `CASH_RESERVE_RATIO = 0.03`
- `MIN_TRADE_RATIO = 0.01`，`MIN_TRADE_FLOOR = 100 USD`
- `REBALANCE_THRESHOLD_RATIO = 0.01`
- `ATTACK_ALLOCATION_MODE = soxx_gate_tiered_blend`
- `BLEND_GATE_SOXL_WEIGHT = 0.70`，`BLEND_GATE_MID_SOXL_WEIGHT = 0.65`
- `BLEND_GATE_ACTIVE_SOXX_WEIGHT = 0.20`，`BLEND_GATE_DEFENSIVE_SOXX_WEIGHT = 0.15`
- RSI 过热已启用，动态阈值为 `max(70, rolling 252d RSI14 q90)`
- 布林带过热已启用；RSI + 布林带双触发时，full 可直接降到 defensive
- 闸门缓冲：入场 `8%`，中档 `6%`，退出 `2%`
- 收入层起点 `150000 USD`，使用 `log_cap`，硬上限 `90%`
- 收入层配比：`SCHD 20%`，`DGRO 10%`，`SGOV 65%`，`SPYI 4%`，`QQQI 1%`

**账户级收入层默认参数**

权重型策略默认也启用收入层，但它作为账户级覆盖层执行：原策略先生成核心权重，收入层再按账户规模缩小核心权重并加入收入篮子；没有 `portfolio_snapshot` 时保持原权重不变。

| Profile | 起点 | 硬上限 | 压力回撤 | 损失预算 | 默认收入篮子 |
| --- | ---: | ---: | ---: | ---: | --- |
| `global_etf_rotation` | `500000` | `15%` | `18%` | `2.5% -> 2.0%` | `SCHD 40% / DGRO 25% / SGOV 30% / SPYI 5%` |
| `russell_1000_multi_factor_defensive` | `400000` | `20%` | `18%` | `3.0% -> 2.5%` | `SCHD 45% / DGRO 30% / SGOV 25%` |
| `tech_communication_pullback_enhancement` | `250000` | `30%` | `22%` | `5.0% -> 4.0%` | `SCHD 40% / DGRO 25% / SGOV 20% / SPYI 10% / QQQI 5%` |
| `mega_cap_leader_rotation_top50_balanced` | `300000` | `25%` | `20%` | `4.0% -> 3.0%` | `SCHD 45% / DGRO 30% / SGOV 20% / SPYI 5%` |
