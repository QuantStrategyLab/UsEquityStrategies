# 通用数据源/信号源仓库设计

研究日期：2026-06-19。

本文设计一个后续可独立创建的通用数据源/信号源仓库，例如
`QuantSignalSources` 或 `QuantStrategyLab/MarketSignalSources`。本次只记录
设计，不创建远端仓库，不改变现有策略包、平台仓库或 SnapshotPipelines 的
部署边界。

## 1. 当前架构理解

现有 `UsEquityStrategies` 的策略层已经在向 canonical input 收敛。当前 catalog
和 runtime adapter 的关键事实是：

- `catalog.py` 只声明策略元数据、`required_inputs`、默认配置、入口点和目标模式。
- `runtime_adapters.py` 按平台声明可用输入、`portfolio_input_name`、快照 artifact
  contract、信号生效时点等运行时契约。
- 策略入口点通过 `StrategyContext.market_data` 和 `StrategyContext.portfolio`
  消费 canonical inputs，而不是直接读取平台原始账户或 broker payload。
- value-mode 策略当前固定输入包括：
  - `tqqq_growth_income`: `benchmark_history` + `portfolio_snapshot`
  - `soxl_soxx_trend_income`: `derived_indicators` + `portfolio_snapshot`
  - `nasdaq_sp500_smart_dca`: `market_history` + `portfolio_snapshot`
  - `ibit_smart_dca`: `derived_indicators` + `portfolio_snapshot`
- IBIT 是跨市场边界最明显的例子：交易资产是美股 ETF `IBIT`，信号源却是
  BTC / crypto-native 指标。现有 `ibit_smart_dca` entrypoint 优先读取
  `market_data["derived_indicators"]`，兼容读取 `crypto_indicator_snapshot`，
  并只在缺少外部 cycle snapshot 时回退到 `market_history` 计算。

跨仓库职责应保持如下边界：

| 仓库/层 | 应负责 | 不应负责 |
| --- | --- | --- |
| `UsEquityStrategies` | 美股策略纯逻辑、catalog、manifest、entrypoint、runtime adapter 契约 | vendor SDK、数据源密钥、直接抓取 BTC/US/HK 行情、artifact 存储权限 |
| `HkEquityStrategies` | 港股策略纯逻辑、港股 canonical input 契约、策略元数据 | 直接维护美股/crypto 数据抓取、broker 密钥 |
| `CryptoStrategies` | crypto 策略纯逻辑、crypto canonical input 契约、crypto 组合/风控策略 | 美股 broker 账户状态、平台下单密钥 |
| 各 Platform repo | broker/exchange 连接、账户快照、调度、dry-run/live、下单、通知、artifact 读取和注入 | 重新实现策略指标算法、拥有跨平台不一致的数据口径 |
| SnapshotPipelines | 月度/研究型 feature snapshot、点时证据、promotion gate、manifest、回放证据 | 被通用数据层替代，或把策略执行时账户/下单职责收进 pipeline |
| 后续 `MarketSignalSources` | provider adapter、原始数据缓存、标准化、衍生指标、provenance、新鲜度、可发布 artifact | 策略下单、账户密钥、策略组合决策、平台启用状态 |

因此，通用数据源仓库应是 canonical input 的上游生产者，而不是新的策略运行时。
策略包继续消费 `market_history`、`benchmark_history`、`derived_indicators`、
`feature_snapshot`、`benchmark_snapshot`、`universe_snapshot`、`portfolio_snapshot`
等已知输入名。

## 2. 主要问题或设计压力点

当前压力不在单个指标缺失，而在跨平台口径可能漂移：

- 同一个策略 profile 可能在不同平台由不同代码计算同名指标，导致 live 决策不一致。
- `market_history` 和 `derived_indicators` 目前只表达数据形状，没有强制表达 provider、
  transform 版本、原始时间戳、币种、复权/汇率策略和新鲜度。
- BTC cycle 指标、链上指标、crypto dominance、Fear & Greed 等不适合放进美股
  broker platform 的市场数据层，也不应放进策略包。
- SnapshotPipelines 已经承担点时 artifact 治理；如果通用数据源仓库越界替代它，
  会削弱 promotion evidence 和回放能力。
- IBIT 当前需要的是小而稳定的 `derived_indicators`，不需要为了第一个用例一次性
  设计完整跨市场数据平台。

## 3. 推荐方案和为什么是低风险方案

推荐新建独立仓库 `MarketSignalSources`，但第一阶段只发布能被现有
`UsEquityStrategies` 直接消费的 artifact，不改策略包公共 API。

低风险原则：

- 先作为上游 artifact producer 存在，平台 repo 读取 artifact 后注入现有
  canonical input。
- IBIT MVP 只输出 `derived_indicators`，不要求 `UsEquityStrategies` 增加新 input 名。
- provider SDK、缓存、重试、密钥和数据授权全部留在信号源仓库或 pipeline 环境，
  策略包不感知。
- SnapshotPipelines 可以复用信号源仓库的标准化/衍生计算函数，但仍拥有 promotion
  gate 和生产 manifest。
- 平台 repo 在订单生成前读取准备好的 bundle；策略评估期间不发起外部 vendor 请求。

### MVP：IBIT AHR999 `derived_indicators`

第一个可交付目标：

1. 获取 BTC 日线或读取已缓存 BTC 日线。
2. 计算 IBIT 当前策略可使用的 BTC 指标。
3. 产出一个 `SignalBundle` artifact。
4. 平台 repo 读取 bundle，并把 `bundle["derived_indicators"]` 注入
   `StrategyContext.market_data["derived_indicators"]`。
5. `UsEquityStrategies` 现有 `ibit_smart_dca` 不需要改代码即可使用。

MVP 的 `derived_indicators` 建议始终使用符号级 payload：

```json
{
  "BTC-USD": {
    "close": 64000.0,
    "sma200": 59000.0,
    "high252": 73000.0,
    "drawdown_252d": 0.1232876712,
    "sma200_gap": 0.0847457627,
    "rsi14": 54.2,
    "ahr999": 0.72,
    "ahr999_sma": 0.75,
    "mayer_multiple": 1.0847457627,
    "ahr999_estimate_price": 78000.0,
    "provider_timestamp": "2026-06-19T00:00:00Z"
  }
}
```

当前 IBIT 策略已经能接受 `BTC-USD`、`BTCUSDT`、`BTC` 等 key，但 artifact 应选
`BTC-USD` 作为 canonical key，其他 key 只作为消费者兼容。

### SignalBundle schema

MVP 使用 JSON 作为平台消费主格式；后续可增加 Parquet/Arrow 给 pipeline 和批量研究。

```json
{
  "schema_version": "market_signal_bundle.v1",
  "bundle_id": "crypto.btc.derived_indicators.2026-06-19",
  "bundle_type": "derived_indicators",
  "domain": "crypto",
  "consumer_contract": {
    "canonical_input": "derived_indicators",
    "compatible_profiles": ["us_equity:ibit_smart_dca"],
    "min_strategy_contract": "derived_indicators+portfolio_snapshot"
  },
  "as_of": "2026-06-19",
  "generated_at": "2026-06-19T00:15:00Z",
  "symbols": ["BTC-USD"],
  "derived_indicators": {},
  "freshness": {
    "policy": "crypto_daily_close_t_plus_1",
    "max_age_hours": 36,
    "provider_timestamp": "2026-06-19T00:00:00Z",
    "status": "fresh"
  },
  "provenance": {
    "source_repo": "QuantStrategyLab/MarketSignalSources",
    "source_version": "0.1.0",
    "code_commit": "<git-sha>",
    "provider": "selected_btc_daily_provider",
    "provider_dataset": "btc_usd_daily_ohlcv",
    "raw_artifact_sha256": "<sha256>",
    "transform": "crypto.btc.ahr999.v1",
    "license_scope": "internal_runtime",
    "generated_by": "scheduled_pipeline"
  }
}
```

字段规则：

- `schema_version` 只在破坏性 schema 变更时升级。
- `bundle_id` 必须可由 domain、signal family、as_of 稳定推导，便于审计和缓存。
- `consumer_contract.canonical_input` 表达平台应注入哪个 canonical input 名。
- `freshness.status` 至少支持 `fresh`、`stale`、`missing`、`partial`。
- `provenance` 不存 signed URL、token、cookie、账户号或 broker 原始 payload。
- `derived_indicators` 是策略消费主体；其他元数据只供平台校验、日志和审计。

### Provenance

provenance 必须能回答三个问题：

- 数据从哪里来：provider、dataset、provider timestamp、raw artifact hash。
- 指标怎么来：transform 名称、transform 版本、代码 commit、参数版本。
- 谁能复现：source repo、source version、运行环境标识、生成时间、artifact hash。

字段级 provenance 在 MVP 可以不展开到每个指标，但同一 bundle 内所有指标必须共享同一
可复现 lineage。后续引入多 provider 或链上指标时，再增加 `field_provenance`：

```json
{
  "field_provenance": {
    "BTC-USD.ahr999": {
      "inputs": ["BTC-USD.close", "BTC-USD.gma200", "ahr999_estimate_price"],
      "transform": "crypto.btc.ahr999.v1"
    }
  }
}
```

### 新鲜度策略

MVP 建议采用 profile-aware 新鲜度，而不是全局统一 TTL：

| Signal family | 建议 freshness | 处理策略 |
| --- | --- | --- |
| BTC daily AHR999 | `provider_timestamp` 距平台运行时不超过 36 小时 | fresh 时允许 smart mode；stale 时平台阻断 smart mode 或发出 no-execute 风险标记 |
| US/HK 日线历史 | 交易日历 T+1，可按市场假日放宽 | stale 时阻断依赖该市场的主动再平衡 |
| 月度 feature snapshot | snapshot month lag + manifest promotion gate | 未 promotion 不进入 runtime |
| crypto live-pool 指标 | 由 Crypto pipeline 定义分钟级或小时级 SLA | stale 时直接禁止 live action |

IBIT 普通定投理论上可在没有 AHR999 时运行，但如果 `smart_multiplier_enabled=True`，
平台应在调用策略前校验 bundle fresh。当前策略不会读取 freshness 元数据，所以 freshness
不能只写进 bundle 后忽略，必须由平台 loader 或后续统一 input builder 执行。

### 缓存

缓存分两层：

- raw cache：按 `provider/dataset/symbol/date` 保存 provider 原始响应和 hash，避免重复
  请求和便于重算。
- derived cache：按 `transform/version/as_of/input_hash` 保存指标结果和 bundle hash。

运行规则：

- artifact 生成任务可以访问 provider；策略评估和下单任务只读已发布 bundle。
- provider 请求必须有超时、重试上限、速率限制和明确错误码。
- cache 命中不等于 fresh；freshness 必须用 provider timestamp / as_of / 市场日历判断。
- 同一 as_of、同一 input hash、同一 transform version 应产出相同 bundle hash。

### 密钥归属

- vendor market-data / on-chain-data API key：归 `MarketSignalSources` 运行环境或
  SnapshotPipelines 运行环境，不进入策略包。
- broker/exchange account key：继续归各 Platform repo，不进入信号源仓库。
- artifact 存储写权限：归生成 artifact 的 pipeline；平台 repo 只拿最小化只读权限。
- 公开或内部 artifact URI 可以进 manifest；signed URL、token、cookie、账户 ID 不进入
  bundle、策略 diagnostics 或通知文本。

### Artifact 输出

MVP 推荐目录形态：

```text
artifacts/
  signal_bundles/
    crypto/
      btc/
        derived_indicators/
          2026-06-19/
            signal_bundle.json
            derived_indicators.json
            manifest.json
            checksums.sha256
```

`derived_indicators.json` 是平台最小消费文件；`signal_bundle.json` 是完整审计文件；
`manifest.json` 包含 artifact 路径、大小、hash、schema_version、as_of、generated_at、
freshness status 和 provenance 摘要。

### 平台消费方式

平台 repo 的最小接入方式：

1. 在调度任务开始时解析目标 profile 和 as_of。
2. 从 artifact store 或本地缓存读取 `signal_bundle.json`。
3. 校验 schema、hash、freshness、`consumer_contract.canonical_input`。
4. 把 `derived_indicators` 注入：

```python
StrategyContext(
    as_of=as_of,
    market_data={"derived_indicators": bundle["derived_indicators"]},
    portfolio=portfolio_snapshot,
    runtime_config=profile_config,
)
```

5. 在 dry-run/live 日志记录 `bundle_id`、`schema_version`、`provider_timestamp`、
   `source_version` 和 `code_commit`。

平台不应把 bundle 的 provenance 直接塞进策略算法输入；如果需要审计，可放在平台日志或
`runtime_config` 的执行审计字段中，但不要让策略公式依赖它。

当前 `UsEquityStrategies` 只保留消费者侧最小 contract：
`us_equity_strategies.signals.signal_bundle_contract` 可以校验
`market_signal_bundle.v1` 的 `derived_indicators` bundle，并提取可直接注入
`StrategyContext.market_data` 的 canonical input；平台侧最小接入可以用
`extract_canonical_input_from_manifest` 从本地 manifest 读取，并校验 bundle sha256、
`bundle_id`、`as_of`、schema version、freshness status 和 canonical input 一致性。示例 fixture 位于
`examples/signal_bundles/crypto/btc/derived_indicators/2026-06-19/manifest.json`。
manifest 里的 `bundle_path` 必须是 artifact 目录内的相对路径，不能是绝对路径或跳出目录。
消费者 contract 也会拒绝包含 token、signed URL、cookie、secret 等敏感字段的 bundle。
平台日志可使用 `signal_bundle_audit_summary_from_manifest` 记录 bundle id、schema、freshness、
provider timestamp、source version 和 transform，不需要把完整 bundle 或供应商密钥写进日志。
这不是 vendor adapter，也不拥有密钥、缓存或 artifact 发布职责；这些仍属于后续
`MarketSignalSources` / pipeline 环境。

### 扩展路线

Phase 1：IBIT / BTC cycle MVP

- BTC daily OHLCV 标准化。
- AHR999、AHR999 SMA、Mayer Multiple、SMA200、252 日高点、回撤、RSI14。
- JSON bundle、manifest、hash、freshness 校验。
- 一个平台 repo 先以只读 artifact loader 接入 `ibit_smart_dca`。

Phase 2：US/HK/Crypto canonical bundles

- US：`market_history`、`benchmark_history`、行业/成分股基础 universe、常用趋势/波动
  derived indicators。
- HK：港股日线、港币/美元汇率、交易日历、港股 feature snapshot 上游标准化。
- Crypto：`market_prices`、`derived_indicators`、`benchmark_snapshot`、`universe_snapshot`
  的统一 schema，并对齐现有 crypto pipeline artifact contract。

Phase 3：多 provider 与 SnapshotPipelines 复用

- 增加 provider adapter：broker history、crypto exchange klines、on-chain metrics、
  artifact provider、CSV/local provider。
- SnapshotPipelines 复用标准化和衍生计算模块，但继续拥有 promotion evidence。
- 在至少两个真实 provider 和两个消费仓库稳定后，再抽象 provider registry。

## 4. 不推荐方案及原因

- 不推荐把 vendor 调用放进 `UsEquityStrategies`、`HkEquityStrategies` 或
  `CryptoStrategies`。这会把密钥、重试、授权、速率限制和数据许可混进策略纯逻辑。
- 不推荐每个 Platform repo 自己计算 AHR999、MVRV、NUPL 或 feature snapshot。这会
  造成口径漂移，也让审计证据散落在多个运行时。
- 不推荐让通用数据源仓库替代 SnapshotPipelines。快照 promotion、点时证据、回放和
  artifact contract 是治理职责，不只是数据抓取职责。
- 不推荐第一版就建设完整 provider marketplace 或复杂插件系统。当前真实需求是
  IBIT 的 BTC/AHR999 bundle；过早抽象会增加迁移成本。
- 不推荐新增策略输入名如 `crypto_signal_bundle` 作为 IBIT MVP 的必需输入。现有
  `derived_indicators` 已足够承载，新增 input 会扩大平台和 adapter 改动面。

## 5. 需要修改的文件/模块范围

本次研究文档只新增：

- `docs/research/generic_signal_source_repository_design.md`

后续真正实现时，建议分仓库小步推进。

新仓库建议模块：

```text
market_signal_sources/
  contracts/
    signal_bundle_schema.py
    freshness_policy.py
  providers/
    base.py
    artifact_provider.py
    crypto_daily_bars.py
  normalizers/
    ohlcv.py
    calendars.py
  derived/
    crypto/
      btc_cycle.py
  cache/
    raw_cache.py
    derived_cache.py
  artifacts/
    writer.py
    manifest.py
  cli/
    build_signal_bundle.py
```

现有仓库的后续最小改动范围：

- Platform repo：新增 artifact loader，把 `derived_indicators` 注入 `StrategyContext`。
- `UsEquityStrategies`：IBIT MVP 不需要改；后续可只补文档或 adapter metadata。
- `HkEquityStrategies` / `CryptoStrategies`：等 Phase 2 有真实消费方后再接入。
- SnapshotPipelines：可选择性引入新仓库作为库依赖，不迁移 promotion gate。

## 6. 验证策略

文档阶段验证：

- 检查新文档是否只描述设计，不要求当前代码变更。
- 检查方案是否保持现有 `required_inputs` 和 runtime adapter 契约。

实现阶段验证：

- schema test：`signal_bundle.json` 必须通过 `market_signal_bundle.v1` 校验。
- golden test：固定 BTC 日线样本计算出的 AHR999、AHR999 SMA、Mayer Multiple 与
  golden file 在容差内一致。
- provenance test：每个发布 bundle 都有 provider、dataset、raw hash、transform、
  code commit、generated_at。
- freshness test：fresh bundle 可被平台 loader 接受；stale/missing bundle 被阻断或
  产生明确 no-execute 风险。
- idempotency test：相同 raw input hash + transform version + as_of 生成相同 bundle hash。
- integration test：平台 loader 读取 MVP bundle 后，`UsEquityStrategies`
  `ibit_smart_dca` 通过 `market_data["derived_indicators"]` 得到与现有单元测试一致的
  AHR999 regime、multiplier 和 target values。
- fallback test：`smart_multiplier_enabled=False` 时，IBIT 普通定投仍不依赖 BTC
  provider；`smart_multiplier_enabled=True` 且 bundle stale 时不能静默退化为错误口径。

## 7. 兼容性、安全、性能或迁移风险

兼容性风险：

- 低到中。IBIT MVP 不改变 `UsEquityStrategies` 的 canonical input，但平台 repo 需要
  新增 bundle loader 和 freshness 校验。
- `crypto_indicator_snapshot` 只能继续作为兼容别名，新的平台接入应统一使用
  `derived_indicators`。
- 若后续收紧 schema，需要保留 `market_signal_bundle.v1` 读取能力直到所有平台升级。

安全风险：

- 最大风险是把 provider key、signed URL 或账户信息写进 artifact。bundle schema 必须
  明确禁止这些字段。
- artifact 读权限应最小化；平台只需要读已发布 bundle，不需要 provider key 或写权限。
- 策略 diagnostics 和通知文本只能记录 bundle id / provider timestamp / schema version
  等非敏感审计信息。

性能风险：

- IBIT 和月度/日度信号对性能要求低，主要风险是平台运行时误触发实时 vendor 请求。
- 生成任务应做缓存、超时、重试上限和速率限制；平台策略评估只读本地或对象存储 artifact。
- 大型 US/HK universe 后续应优先输出列式 artifact，JSON 只保留 manifest 和小型
  derived indicator payload。

迁移风险：

- 不要一次性把所有 US/HK/Crypto 数据输入迁到新仓库。按 profile 接入，先 IBIT，再复用
  到已有 snapshot pipeline，最后扩展到多市场常用信号。
- 每个迁移 profile 都需要保留 characterization test，证明同一 bundle 在不同平台产生
  相同策略决策。
- provider 替换必须升级 provenance 中的 provider/dataset/transform 版本，并在平台
  dry-run 中观察至少一个完整周期后再进入 live。
