# Market Signal Bundle Artifact Contract

研究日期：2026-06-19。

本文把后续 `MarketSignalSources` 仓库需要发布的最小 artifact 契约固化下来。
它是平台消费契约，不是 vendor adapter 设计；策略仓库只负责校验和提取 canonical input。

## Boundary

`MarketSignalSources` 应产出可缓存、可审计、可重放的信号 bundle。各策略平台只读取已发布
artifact 并注入 `StrategyContext.market_data`，不得在策略运行过程中调用 vendor API。

| 层 | 允许 | 不允许 |
| --- | --- | --- |
| Signal source repo | provider adapter、raw cache、derived transform、bundle/manifest 发布 | broker 账户、下单、策略启停 |
| Strategy repo | bundle schema 校验、canonical input 提取、非敏感 audit summary | vendor SDK、密钥、网络下载、artifact 写权限 |
| Platform repo | manifest 读取、hash/freshness 校验、market_data 注入、执行日志 | 重算指标、持有 vendor 密钥、改变策略公式 |

## Directory Layout

MVP 目录必须稳定，便于平台按 `domain/signal_family/canonical_input/as_of` 定位：

```text
signal_bundles/
  index.json
  crypto/
    btc/
      derived_indicators/
        2026-06-19/
          signal_bundle.json
          manifest.json
```

后续可以增加 `raw/`、`checksums.sha256`、Parquet 或 Arrow 文件，但 `signal_bundle.json`
和 `manifest.json` 必须保持可用。
`index.json` 是平台运行入口，用来按 canonical input、as_of 和 freshness 选择具体
manifest；平台仍必须对选中的 manifest 和 bundle 做完整校验。

## signal_bundle.json

必填字段：

| Field | Rule |
| --- | --- |
| `schema_version` | 固定为 `market_signal_bundle.v1`，破坏性变更才升级 |
| `bundle_id` | 由 domain、symbol family、canonical input、as_of 稳定推导 |
| `bundle_type` | MVP 只接受 `derived_indicators` |
| `consumer_contract.canonical_input` | 必须等于平台要注入的 canonical input，例如 `derived_indicators` |
| `as_of` | 信号生效日期，不是生成时间 |
| `generated_at` | bundle 生成时间 |
| `symbols` | bundle 覆盖的 canonical symbols |
| `derived_indicators` | 策略实际消费 payload，按 symbol 分组 |
| `freshness.status` | `fresh` 才允许注入智能定投 runtime |
| `freshness.provider_timestamp` | provider 数据时间戳 |
| `provenance` | source repo、version、commit、provider、dataset、raw hash、transform、license |

`provenance` 禁止包含 `token`、`secret`、`cookie`、`signed_url`、`authorization`、
账户号或 broker payload。平台日志只记录 audit summary，不写完整 bundle。

## manifest.json

必填字段：

| Field | Rule |
| --- | --- |
| `schema_version` | 固定为 `market_signal_manifest.v1` |
| `bundle_path` | artifact 目录内的相对路径，通常是 `signal_bundle.json` |
| `bundle_sha256` | `signal_bundle.json` 的 SHA-256 |
| `bundle_id` | 必须与 bundle 内字段一致 |
| `as_of` | 必须与 bundle 内字段一致 |
| `canonical_input` | 必须与 `consumer_contract.canonical_input` 一致 |
| `bundle_schema_version` | 可选；如存在，必须与 bundle `schema_version` 一致 |
| `freshness_status` | 可选；如存在，必须与 bundle `freshness.status` 一致 |

消费者必须拒绝绝对路径、跳出 artifact 目录的路径、hash mismatch、schema mismatch、
freshness mismatch，以及任何包含敏感字段名的 manifest。

## index.json

必填字段：

| Field | Rule |
| --- | --- |
| `schema_version` | 固定为 `market_signal_index.v1` |
| `generated_at` | index 生成时间 |
| `bundles[].manifest_path` | 相对 `index.json` 所在目录的 manifest 路径 |
| `bundles[].manifest_sha256` | manifest 文件的 SHA-256 |
| `bundles[].bundle_id` | 必须与 manifest 一致 |
| `bundles[].as_of` | 必须与 manifest 一致 |
| `bundles[].canonical_input` | 必须与 manifest 一致 |
| `bundles[].freshness_status` | 平台默认只选择 `fresh` |

平台选择规则：

- 如果指定 `bundle_id`，只允许匹配该 bundle。
- 如果指定 `as_of`，选择 `as_of <= requested_as_of` 的最新 fresh entry。
- 如果未指定 `as_of`，选择 index 中最新 fresh entry。
- index 先用 `manifest_sha256` 锁定 manifest 文件；选中后仍要校验 manifest 内容、bundle schema、
  freshness、provenance 和 canonical input。
- index 和 manifest 一样禁止绝对路径、目录逃逸和敏感字段。

## Platform Validation Gate

平台在调用策略前应按顺序执行：

1. 读取 `index.json`，或读取平台已明确指定的 `manifest.json`。
2. 如果从 index 选择，按 canonical input、as_of、freshness 和可选 bundle id 定位
   manifest，先校验 `manifest_sha256`，再校验 index entry 与 manifest 一致。
3. 校验 manifest schema、相对路径和敏感字段。
4. 若 manifest 声明 `quality_report_path`，读取 `quality_report.json`，校验
   `quality_report_sha256`、schema、敏感字段，并拒绝 `quality_status=fail`。
5. 读取 `signal_bundle.json` 并校验 SHA-256。
6. 校验 bundle schema、canonical input、symbols、freshness、provenance。
7. 对 `smart_multiplier_enabled=True` 的策略，只允许 `fresh` bundle 注入。
8. 把 `bundle["derived_indicators"]` 注入
   `StrategyContext.market_data["derived_indicators"]`。
9. 日志只写 `bundle_id`、`schema_version`、`provider_timestamp`、`source_version`、
   `code_commit`、`transform`、`bundle_sha256`、每个 symbol 的指标字段名和字段数量。
   不写 `close`、`ahr999`、`mayer_multiple` 等指标的具体数值。

本仓库当前的消费者侧校验入口是：

```bash
python -m us_equity_strategies.signals.signal_bundle_cli \
  examples/signal_bundles/crypto/btc/derived_indicators/2026-06-19/manifest.json \
  --pretty
```

也可以从 index 选择最新可用 manifest：

```bash
python -m us_equity_strategies.signals.signal_bundle_cli \
  --index examples/signal_bundles/index.json \
  --as-of 2026-06-20 \
  --pretty
```

平台或策略仓 CI 也应校验 `MarketSignalSources` 发布的 consumer contract registry artifact，
确认上游声明的字段集合没有和本策略仓的本地契约漂移：

```bash
python -m us_equity_strategies.signals.signal_bundle_cli \
  --consumer-contract-registry ./data/output/market_signal_consumers.json \
  --require-all-known-consumers \
  --pretty
```

若 `MarketSignalSources` 同时发布了 registry manifest，平台或策略仓 CI 应优先校验 manifest
和其指向的 registry，以确认 registry 文件路径、sha256、schema、大小和 consumer coverage
没有漂移：

```bash
python -m us_equity_strategies.signals.signal_bundle_cli \
  --consumer-contract-registry-manifest ./data/output/contracts/market_signal_consumers.manifest.json \
  --require-all-known-consumers \
  --pretty
```

该校验只读取本地 JSON，不引入 `MarketSignalSources` 运行时依赖；它会拒绝 schema mismatch、
unknown consumer、字段漂移、重复字段、缺少本策略仓已知 consumer，以及疑似 token /
secret / signed URL key。当前已知 consumer 包括 IBIT runtime AHR999-only、
IBIT AHR999 helper variants、IBIT Mayer variants，以及 Nasdaq/S&P external context
research consumers。其中
`research:nasdaq_sp500_cape_vix_external_context_precomputed` 只要求
`US-EQUITY-CONTEXT.cape_percentile` 和 `US-EQUITY-CONTEXT.vix_percentile`，
用于 public-data-only CAPE/VIX 候选；完整
`research:nasdaq_sp500_external_context_precomputed` 仍要求额外的
`breadth_above_sma200_pct`。

若 `MarketSignalSources` 发布了 `market_signal_platform_handoff.v1`，平台或策略仓 CI
应优先校验 handoff manifest。它会同时 pin 住 signal bundle manifest、source family
catalog manifest 和 consumer contract registry manifest，并在本策略仓侧复算三份 linked
manifest 的 SHA-256、bundle consumer 字段覆盖、source family consumer coverage，以及
consumer registry 与本地 contract 是否漂移：

```bash
python -m us_equity_strategies.signals.signal_bundle_cli \
  --platform-handoff-manifest ./data/output/platform_handoff.json \
  --consumer us_equity:ibit_smart_dca \
  --require-all-known-families \
  --require-all-known-consumers \
  --pretty
```

若发布侧提供的是 handoff index，平台 CI 可以让策略仓按 consumer 和 `as_of` 解析最新
handoff，再执行同一套 linked manifest 校验：

```bash
python -m us_equity_strategies.signals.signal_bundle_cli \
  --platform-handoff-index ./data/output/platform_handoff_index.json \
  --consumer us_equity:ibit_smart_dca \
  --as-of 2026-06-20 \
  --require-all-known-families \
  --require-all-known-consumers \
  --pretty
```

运行时注入可使用 `extract_canonical_input_from_platform_handoff_for_consumer()`：
它先验证 handoff 和全部 linked manifest，再只返回
`StrategyContext.market_data["derived_indicators"]` 需要的 canonical input。

如果 `MarketSignalSources` 同时发布
`market_signal_platform_handoff_index.v1`，平台可以先用
`resolve_platform_signal_handoff_manifest_from_index()` 按 consumer、canonical input、
freshness 和 `as_of` 选出最新匹配 handoff manifest，再走同一套 handoff 校验。直接注入时
可使用 `extract_canonical_input_from_platform_handoff_index_for_consumer()`；该入口不会信任
index 中的摘要字段，仍会校验 handoff manifest 的 SHA-256、linked manifest SHA-256、
bundle freshness 和 consumer 字段覆盖。

该审计输出会包含：

- `indicator_fields_by_symbol`：例如 `BTC-USD` 下有哪些字段名，包括 `ahr999`、
  `ahr999_sma`、`mayer_multiple`、`sma200_gap`。
- `indicator_field_count_by_symbol`：每个 symbol 的字段数量，便于平台日志和告警做覆盖度检查。

审计输出只暴露字段名和 provenance 摘要，不暴露指标数值、provider 原始响应或任何
token / signed URL / cookie / secret。

平台 adapter 如果要直接构造 `StrategyContext.market_data`，应优先使用 consumer-aware
提取入口。它会在返回 payload 前同时完成 manifest/index、bundle freshness 和 consumer
profile compatibility / 字段覆盖校验：

```python
from us_equity_strategies.signals import extract_canonical_input_from_index_for_consumer

market_data = extract_canonical_input_from_index_for_consumer(
    "examples/signal_bundles/index.json",
    consumer="us_equity:ibit_smart_dca",
    as_of="2026-06-20",
)
```

返回值形如 `{"derived_indicators": {"BTC-USD": {...}}}`，可以直接注入
`StrategyContext.market_data`。平台日志仍应使用 audit summary，只记录字段名、hash 和
provenance 摘要，不记录指标数值。

## Research Compatibility

研究回测不直接依赖 vendor。`MarketSignalSources` 或人工下载流程应先把历史价格/指标
导出为本地 CSV，再由智能定投研究 CLI 读取。这样可以把 provider 可用性、指标公式和
策略 ranking 分开审计，避免在回测脚本里隐藏数据选择和参数搜索。
当传入 `--signal-manifest` 且候选集使用 precomputed signal 时，CLI 会拒绝重复日期、
非单调日期、`last_date > as_of`、Nasdaq context 百分位越界、非正 AHR999/Mayer 值、
越界 helper percentile 或非有限 slope。这个 gate 不是完整点时数据证明，但能阻止明显
不合格的研究 CSV 进入 robustness matrix。
