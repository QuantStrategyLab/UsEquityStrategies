# MarketSignalSources Bootstrap Plan

研究日期：2026-06-19。

本文把后续独立仓库 `QuantStrategyLab/MarketSignalSources` 的第一阶段落地方案具体化。
它基于当前 `UsEquityStrategies` 消费契约，以及对 `CryptoStrategies` /
`CryptoLivePoolPipelines` 的只读检查；本次不创建远端仓库、不迁移现有平台、不接入
vendor 密钥。

## 1. 当前架构理解

现有策略仓库的共同方向已经比较清晰：

- 策略包只拥有纯策略逻辑、catalog、entrypoint、runtime adapter 和 canonical input 名称。
- 平台仓库拥有 broker/exchange 连接、账户、下单、调度、dry-run/live 和通知。
- Snapshot / pipeline 仓库拥有 artifact 发布、promotion evidence、manifest、hash 和
  point-in-time 回放证据。
- 后续 `MarketSignalSources` 应作为 canonical input 的上游生产者，而不是新的策略运行时。

本次只读到的 crypto 侧证据：

| Source | Evidence |
| --- | --- |
| `QuantStrategyLab/CryptoStrategies/src/crypto_strategies/catalog.py` | `crypto_live_pool_rotation` 声明 `market_prices`、`derived_indicators`、`benchmark_snapshot`、`portfolio_snapshot`、`universe_snapshot` |
| `QuantStrategyLab/CryptoStrategies/src/crypto_strategies/runtime_adapters.py` | Binance runtime adapter 要求 `crypto_live_pool_rotation.live_pool.v1` artifact contract |
| `QuantStrategyLab/CryptoStrategies/src/crypto_strategies/strategies/crypto_live_pool_rotation/core.py` | runtime 排名消费 `roc20/60/120`、SMA、波动率、成交额、trend persistence、BTC-relative momentum 等字段 |
| `QuantStrategyLab/CryptoLivePoolPipelines/src/features.py` | pipeline 已有完整指标工程：SMA、ROC、RS、vol、ATR、drawdown、ulcer、liquidity、BTC beta/correlation、breadth、dispersion |
| `QuantStrategyLab/CryptoLivePoolPipelines/src/release_contract.py` | release contract 校验文件存在、hash、as_of、版本、mode、symbols、ranking 列、selected rank 顺序和 freshness |
| `QuantStrategyLab/CryptoLivePoolPipelines/docs/external_data_validation.md` | 外部数据仍未默认启用；用 whitelist、provider cross-check、coverage、gap、overlap consistency 和 walk-forward 指标治理 |

这些证据说明：crypto 侧已经有较成熟的 artifact governance；通用信号源仓库不应该替代
它，而应抽取可复用的 provider / normalizer / derived signal / manifest 思路，并把输出
压到各策略仓库已经声明的 canonical input。

## 2. 主要问题或设计压力点

- IBIT 是美股交易资产，但信号是 BTC/AHR999/Mayer 等 crypto-native 指标；平台 broker
  行情不能作为唯一数据源。
- Nasdaq/S&P 智能定投下一轮要研究 CAPE、VIX、breadth、利率/流动性等外部信号，也不能
  放进美股策略包或每个平台各算一遍。
- Crypto live-pool 已有较多指标和 artifact contract，但当前结构属于 live-pool pipeline；
  直接复用整仓库会把月度 pool governance 和通用数据层职责混在一起。
- 外部数据很容易产生 survivorship bias、provider drift、timestamp drift 和历史修订问题；
  `MarketSignalSources` 必须先做质量门槛和 provenance，而不是先追求指标数量。
- 用户目标中的智能定投优化要求“不过拟合”。数据层也必须让回测产物能说明：
  输入来自哪里、何时可得、公式版本是什么、是否被事后筛选。

## 3. 推荐方案和为什么是低风险方案

推荐先创建独立仓库 `MarketSignalSources`，但第一阶段只交付本地可运行、artifact-first 的
MVP，不接 live vendor，不改策略包公共 API。

### Phase 1 MVP

第一阶段只做三个 producer：

| Producer | Output canonical input | Primary consumer | Purpose |
| --- | --- | --- | --- |
| `crypto.btc_cycle_daily` | `derived_indicators` | `us_equity:ibit_smart_dca` | 生成 BTC daily AHR999/Mayer/SMA/drawdown 信号 bundle |
| `crypto.live_pool_feature_catalog` | `derived_indicators` / documentation artifact | `crypto_live_pool_rotation` 研究复用 | 固化 crypto pipeline 已有字段目录，不抢 monthly live-pool 发布权 |
| `us.nasdaq_sp500_research_context` | offline CSV / future `derived_indicators` | `nasdaq_sp500_smart_dca` 研究 CLI | 给 CAPE/VIX/breadth/rates 研究预留本地 artifact 格式 |

低风险原因：

- 策略仓库继续只消费 `derived_indicators`、`market_history` 等已存在 input 名。
- 平台仓库只读取已发布 artifact；策略评估期间不调用 vendor。
- Crypto live-pool 仍由 `CryptoLivePoolPipelines` 拥有 monthly publish、selected pool 和
  artifact contract；新仓库只复用通用指标计算和质量门槛思路。
- 第一阶段 provider 可以全部是 local CSV / local artifact，避免密钥和供应商 SDK 进入设计初期。

### Recommended Repository Layout

```text
market_signal_sources/
  contracts/
    signal_bundle.py
    freshness.py
    provenance.py
    release_index.py
  providers/
    base.py
    local_csv.py
    local_artifact.py
  normalizers/
    ohlcv.py
    symbols.py
    calendars.py
  derived/
    crypto/
      btc_cycle.py
      live_pool_features.py
    us/
      nasdaq_sp500_context.py
  quality/
    coverage.py
    overlap.py
    source_crosscheck.py
  artifacts/
    writer.py
    manifest.py
    checksums.py
  cli/
    build_btc_cycle_bundle.py
    validate_bundle.py
    export_feature_catalog.py
tests/
  fixtures/
```

第一版尽量用普通函数和小 dataclass，不急着引入复杂 provider registry。只有当至少两个真实
provider 和两个消费仓库稳定后，再抽象 provider registry。

### Canonical Artifact Families

| Artifact family | Required files | Notes |
| --- | --- | --- |
| `market_signal_bundle.v1` | `signal_bundle.json`、`manifest.json`、顶层 `index.json` | 已在 `UsEquityStrategies` 消费端实现校验和审计 |
| `feature_catalog.v1` | `feature_catalog.json`、`field_lineage.csv` | 只描述字段、公式、输入和 freshness，不是 runtime signal |
| `quality_report.v1` | `quality_report.csv`、`provider_overlap.csv`、`quality_manifest.json` | 记录覆盖率、缺失、gap、overlap consistency、provider cross-check |
| `research_export.v1` | CSV + manifest | 给智能定投研究 CLI 使用，必须包含输入 hash 和日期列说明 |

## 4. Indicator Catalog Seed

### IBIT / BTC Cycle Bundle

第一版 `crypto.btc_cycle_daily` 建议输出：

| Field | Source tier | Notes |
| --- | --- | --- |
| `close` | Tier 1 | BTC 日线 close，必须记录 provider、session close 和 timezone |
| `sma200` | Tier 1 | 200 日简单均线 |
| `gma200` | Tier 1 | 200 日几何均价，可只进 lineage，不必给策略消费 |
| `high252` | Tier 1 | 252 日高点 |
| `drawdown_252d` | Tier 1 | `1 - close / high252` |
| `sma200_gap` | Tier 1 | `close / sma200 - 1` |
| `rsi14` | Tier 1 | 14 日 RSI |
| `mayer_multiple` | Tier 1 | `close / sma200` |
| `ahr999` | Tier 1 | 固定 genesis date 和 growth estimate formula 后可复算 |
| `ahr999_sma` | Tier 1 | SMA 版本 sanity check |
| `ahr999_estimate_price` | Tier 1 | AHR999 growth estimate |
| `provider_timestamp` | Required metadata | 不参与策略公式，只用于 freshness 和审计 |

暂不进入 Phase 1 production 的字段：

- `MVRV`、`MVRV Z-score`、`NUPL`、`SOPR`、realized price：需要商业或链上 provider、
  point-in-time 历史、字段定义、许可和修订政策。
- Fear & Greed、funding、perp basis、ETF flow、social/news sentiment：先作为观察项，
  没有 provider timestamp 和历史快照前不做策略排名。

### Crypto Live-Pool Feature Catalog

从 `CryptoLivePoolPipelines` 可复用的字段族：

| Family | Fields |
| --- | --- |
| Trend / momentum | `roc20`、`roc60`、`roc120`、`momentum_combo`、`price_vs_sma20/60/120/200`、`ma200_slope` |
| Relative strength | `rs20`、`rs60`、`rs120`、`rs_combo`、`rs_risk_adj` |
| Breakout / drawdown | `dist_to_90d_high`、`dist_to_180d_high`、`breakout_proximity`、`rolling_drawdown`、`drawdown_severity` |
| Risk / volatility | `vol20`、`vol60`、`downside_volatility`、`atr14`、`atr_ratio`、`ulcer_index` |
| Liquidity / tradability | `quote_volume`、`avg_quote_vol_30/90/180`、`liquidity_stability`、`age_days`、`tradable_ratio_180`、`recent_liquidity_acceleration` |
| BTC-relative context | `btc_roc20/60/120`、`btc_above_ma200`、`btc_ma200_slope`、`btc_zscore_120`、`rolling_beta_to_btc`、`rolling_corr_to_btc` |
| Universe context | `breadth_above_sma60`、`breadth_above_sma200`、`universe_momentum_dispersion`、`universe_rs_dispersion`、`single_leader_burst` |
| Ranking diagnostics | `rule_score`、`linear_score`、`ml_score`、`final_score`、`confidence`、`current_rank`、`selected_flag` |

这些字段不应直接全部塞进 IBIT 或 Nasdaq/S&P 智能定投。它们先进入 `feature_catalog.v1`，
供后续研究筛选；进入 production bundle 前必须通过 profile-specific elimination rules。

### Nasdaq / S&P Research Context

第一阶段只定义本地 CSV / artifact 口径：

| Family | Candidate fields | Gate |
| --- | --- | --- |
| Price / trend | QQQ/SPY close、SMA、drawdown、RSI、12M momentum | 可由价格复算，Tier 1 |
| Volatility | VIX level、VIX percentile、realized vol | 需要发布时间滞后模型 |
| Valuation | CAPE、earnings yield、PE | 低频，必须记录发布日期和修订政策 |
| Breadth | above SMA200 ratio、new high/low、advance/decline | 必须 point-in-time，禁止当前成分股回推 |
| Rates / liquidity | 10Y/2Y、real yield、SOFR/Fed funds、credit spread、NFCI | 必须有 provider timestamp 和 release lag |

## 5. 不推荐方案及原因

- 不推荐把 `CryptoLivePoolPipelines` 直接变成通用数据源仓库。它拥有 monthly pool governance，
  直接复用会模糊“发布 live pool”和“生产通用信号”的边界。
- 不推荐第一阶段接入 Glassnode、CoinMetrics、FRED、VIX、CAPE 等所有 provider。真实难点在
  provenance / freshness / point-in-time，而不是 SDK 数量。
- 不推荐在平台仓库本地计算 AHR999、MVRV、breadth 或 macro transforms。平台应加载
  artifact，避免不同平台的 live 决策漂移。
- 不推荐让智能定投策略直接消费完整 `feature_catalog`。策略只能消费经过 profile-specific
  gate 的小 bundle，研究工具再读取宽表 CSV。
- 不推荐在 signal bundle 中写 signed URL、token、cookie、账户 ID、broker payload 或原始
  vendor 响应。

## 6. 需要修改的文件/模块范围

当前 `UsEquityStrategies` 内只需要保持：

- `docs/research/generic_signal_source_repository_design.md`
- `docs/research/market_signal_bundle_artifact_contract.md`
- `docs/research/cross_market_signal_source_layer.md`
- `docs/research/market_signal_sources_bootstrap_plan.md`
- `src/us_equity_strategies/signals/signal_bundle_contract.py`
- `src/us_equity_strategies/signals/signal_bundle_cli.py`

后续新仓库创建后，第一批 PR 应只包含：

1. `pyproject.toml`、包目录、README。
2. local CSV provider、OHLCV normalizer、BTC cycle transform。
3. bundle writer、manifest writer、index writer。
4. CLI：从本地 BTC CSV 生成 `market_signal_bundle.v1`。
5. contract tests：hash、manifest、freshness、sensitive-field rejection、idempotency。

平台仓库的接入应另开 PR：

- 读取 index/manifest。
- 校验 sha256/freshness/canonical input。
- 注入 `StrategyContext.market_data["derived_indicators"]`。
- dry-run/live 日志写非敏感 audit summary 和字段覆盖。

## 7. 验证策略

新仓库 Phase 1 必须具备以下验证：

- Golden indicator test：固定 BTC OHLCV fixture 生成的 `sma200`、`mayer_multiple`、
  `ahr999`、`ahr999_sma` 在容差内稳定。
- Idempotency test：相同 input hash、transform version、as_of 生成相同 bundle hash。
- Manifest test：`manifest.json` hash 与 `signal_bundle.json` 一致，路径不能逃逸 artifact 目录。
- Index selection test：按 canonical input、as_of 和 freshness 选择最新 fresh manifest。
- Sensitive-field test：bundle/manifest/index 拒绝 token、secret、cookie、signed URL。
- Freshness test：fresh bundle 可注入 smart mode；stale/missing bundle 在平台侧阻断或
  标记 no-execute，不静默降级。
- Research export test：CSV 输出能被 `smart_dca_research_cli` 读取，并记录输入 CSV hash。
- Crypto parity test：可复用字段与 `CryptoLivePoolPipelines` 在同一 fixture 上保持一致，
  但不接管 live-pool monthly publish。

对智能定投研究，只有当候选通过以下证据后才考虑进入生产策略：

- `candidate_summary.csv` 证明不是开放参数搜索。
- `candidate_specs.csv` 固化所有阈值和倍率。
- `robustness_summary.csv` 在执行日、贡献金额、cadence 和 rolling starts 上保持方向一致。
- `decision_log.csv` 能解释每次加码/少投/跳过。
- 外部信号有 provider timestamp、release lag、coverage 和许可说明。

## 8. 兼容性、安全、性能或迁移风险

兼容性：

- `market_signal_bundle.v1` 必须保持可读，直到所有平台迁移。
- `crypto_indicator_snapshot` 只能作为 IBIT 旧兼容路径，新接入统一走 `derived_indicators`。
- Crypto live-pool 的 `crypto_live_pool_rotation.live_pool.v1` 不应被本计划替换。

安全：

- provider key 只属于 `MarketSignalSources` 运行环境，不进入策略包和平台 dry-run 日志。
- artifact URI 可以记录内部只读路径；signed URL 不得进入 bundle、manifest 或通知。
- 平台只需要 artifact 只读权限，不需要 provider API 权限。

性能：

- DCA / monthly snapshot 对延迟不敏感，应该用预生成 artifact。
- provider fetch 任务要有超时、重试上限、速率限制和缓存。
- 大型 universe 后续用 Parquet/Arrow；JSON 只保留 manifest、小型 bundle 和 audit summary。

迁移：

- 先 IBIT BTC/AHR999，后 Nasdaq/S&P 外部研究信号，再考虑 HK/Crypto 宽表信号复用。
- 每个 profile 单独接入，保留 characterization test。
- provider 替换必须升级 provenance 中的 provider/dataset/transform version，并至少经过
  一个完整 dry-run 周期。
