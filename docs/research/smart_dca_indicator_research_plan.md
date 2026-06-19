# Smart DCA Indicator Research Plan

Run date: 2026-06-19.

本文记录 `nasdaq_sp500_smart_dca` 与 `ibit_smart_dca` 的下一轮指标与
回测优化方向。它只用于研究排期和验收口径，不是投资建议，也不要求当前
策略代码立即变更。

## Current Baseline

两个 profile 的共同约束：

- 默认仍是 `smart_multiplier_enabled = false` 的普通固定金额定投。
- 智能模式只改变本期投入金额，不改变买入标的，不卖出，不做目标比例再平衡。
- 固定定投是主基准；任何智能信号都必须证明它相对固定定投有可复现的收益、
  回撤或执行质量改进。
- 回测必须共享同一贡献节奏、同一 warm-up、同一交易日对齐、同一现金规则，
  不能让 smart 版本因数据启动日或可交易日差异占便宜。

当前实现差异：

| Profile | 当前信号来源 | 当前智能模式状态 | 下一步研究重点 |
| --- | --- | --- | --- |
| `nasdaq_sp500_smart_dca` | `market_history` 里的 QQQ/SPY 日线 | 价格回撤 / SMA200 gap / RSI 规则已存在，但默认关闭；最近 price-only sweep 未战胜固定定投 | 把 CAPE、VIX、breadth、利率/流动性作为外部信号研究，先验证稳健性，不直接上线 |
| `ibit_smart_dca` | 优先 `derived_indicators` 的 AHR999；缺失时回退 BTC price-history | AHR999 GMA gate-tier 已替代 price-only 作为智能模式候选，但默认仍关闭 | 在 AHR999 后继续比较可稳定复现的 BTC 价格/周期指标与需要外部供应的链上/情绪指标 |

## Shared Research Rules

下一轮研究先产出证据，再决定是否进入策略代码。研究脚本可以新增或扩展，
但策略模块的生产行为保持不变，直到单独评审通过。

### Anti-Overfitting Controls

- 先冻结候选指标族，再跑参数搜索；不得在看完全样本结果后临时追加一组只解释
  历史赢家的阈值。
- 每个指标族最多保留 3 到 5 个有经济含义的阈值组合；避免连续参数网格。
- 所有策略候选都必须和固定定投、当前智能规则、简单 dip-only 规则比较。
- 记录所有失败结果；不只保留胜出的组合。
- 使用 walk-forward / expanding-window 评估，禁止把未来分位数、未来成分股、
  修订后的宏观数据当作当时可见输入。
- 对执行日做扰动：月度第 20/25/最后一个交易日前后、周度/季度 cadence 都要抽样，
  确认结论不是单一日期偶然性。
- 对贡献金额做尺度扰动：`$500`、`$1000`、`$3000` 或等比例贡献路径应保持方向一致。
- 对智能倍数施加工程约束：倍率数量少、最大单期投入有限、跳过买入必须有明确解释。

### Evaluation Metrics

每个候选至少报告：

| Metric | 用途 |
| --- | --- |
| Terminal value vs fixed DCA | 终值是否真实改善，不能只看单次最好窗口 |
| Max drawdown delta | 智能模式是否降低账户权益回撤 |
| Underwater duration | 回撤持续时间是否改善 |
| XIRR / money-weighted return | 定投现金流下的可比收益率 |
| Invested amount / deployment rate | 是否靠长期不投现金制造虚假低回撤 |
| Skipped scheduled buys | 跳过定投的频率和集中区间 |
| Worst rolling window gap | 任意 1Y/2Y/3Y 起点下相对固定定投的最差差距 |
| Signal availability and freshness | 外部信号是否能在执行日前稳定拿到 |
| Turnover / order count | 是否增加平台执行复杂度或小额订单噪音 |

### Elimination Rules

任一候选满足以下条件即淘汰，不进入策略实现：

- 样本外终值低于固定定投超过 `1%`，且最大回撤改善小于 `2 percentage points`。
- 样本外最大回撤更差超过 `1 percentage point`，但终值提升小于 `3%`。
- 只在一个市场阶段或一个起始日显著胜出，walk-forward 多数窗口落后。
- 参数最优点落在搜索边界，或相邻阈值结果方向剧烈变化。
- 跳过买入月份超过计划买入月份的 `30%`，但没有显著降低回撤或改善 XIRR。
- 信号需要不可审计、不可缓存、许可不明确或没有 point-in-time 历史的数据。
- 数据发布日期晚于策略执行日，或者供应商修订会改变历史信号而没有版本锁定。
- 候选逻辑需要卖出、杠杆、保证金、期权或跨账户现金调拨；这超出当前 buy-only DCA profile。

## Nasdaq / S&P 500 Plan

### Candidate Indicators

这些信号先作为外部研究输入，不直接接入 runtime。当前价格型规则仍是最小可用
基线；新指标必须说明相对价格型规则增加了什么独立信息。

| Indicator family | Candidate signals | Data status | Research intent |
| --- | --- | --- | --- |
| Valuation / CAPE | Shiller CAPE、CAPE percentile、earnings yield vs 10Y Treasury、SPX trailing/forward PE | 外部月度或低频数据；需要供应商、发布日期、修订版本 | 判断是否只在极高估值时降低加码，而不是频繁择时 |
| Volatility / VIX | VIX level、VIX percentile、VIX term structure、realized vol vs VIX | VIXCLS 等日线可复现；term structure 需要期货曲线数据 | 区分恐慌加码与高波动环境下的现金保护 |
| Market breadth | SPX/NDX 成分股高于 SMA200 比例、advance/decline、new highs/lows | 最容易产生 survivorship bias；需要历史成分或可靠 breadth 指数 | 验证价格回撤之外，内部广度恶化是否能改善加码时点 |
| Rates / liquidity | 10Y/2Y、real yield、Fed funds/SOFR、T-bill yield、credit spread、NFCI、M2/流动性同比 | 多数可从 FRED/官方数据取得，但要处理发布时间和修订 | 判断利率与流动性环境是否应限制估值信号权重 |
| Trend quality | QQQ/SPY 10M SMA、12M momentum、rolling Sharpe、drawdown speed | 可由价格稳定复现 | 只允许作为当前 price-only baseline 的稳健化，不扩展复杂参数 |

### Data Source Requirements

- 价格路径优先使用 adjusted close / total return proxy；如果只能用 FRED `NASDAQ100`
  和 `SP500` price-only，报告必须标注不含分红和费用。
- CAPE/PE 类指标必须保存原始下载文件、`as_of`、发布日期假设、字段定义和供应商 URL。
- Breadth 数据如果由当前成分股回推，必须标记为研究失败输入；只有 point-in-time
  成分或供应商 breadth 历史可以进入候选比较。
- VIX、利率、信用利差等宏观数据要按实际可获得日滞后至少 1 个交易日，避免收盘后
  才发布的数据影响同日订单。
- 所有外部信号都要产出一个离线 `SignalBundle` 样式快照：`as_of`、`generated_at`、
  `source_name`、`source_version`、`freshness_policy`、`provenance`。

### Sample Split

建议同时跑两个层次：

| Layer | Window | Purpose |
| --- | --- | --- |
| ETF-era core | 1999-03-10 至当前可得日期 | 覆盖 QQQ/SPY ETF 可交易历史，作为主结论 |
| Recent implementation proxy | 2017-06-20 至 2026-06-17 左右 | 对齐现有 FRED price-only sweep，用于和当前文档结果复核 |

主样本切分：

- Discovery: 1999-03-10 至 2009-12-31，用于确定候选指标族是否有基本方向。
- Validation: 2010-01-01 至 2018-12-31，用于缩小到少数阈值组合。
- Out-of-sample: 2019-01-01 至最新可得完整交易日，用于最终保留/淘汰。
- Walk-forward: 5 年训练、2 年验证、2 年测试滚动；每次只允许使用训练窗口确定阈值。
- Stress windows: 2000-2002、2008-2009、2020-03、2022、2024-2026 单独报告。

### Backtest Method

- 固定定投、当前 smart 规则、每个候选规则共享同一月度贡献和执行窗口。
- 候选先以 signal-only overlay 运行：输出建议 multiplier 序列，不改生产策略。
- 倍率结构优先限制为 `{0.0, 0.5, 1.0, 1.25, 1.5}` 或更少档位。
- 估值/宏观信号只允许降低或限制加码强度；不得让单个低频估值指标驱动频繁交易。
- 对 VIX/breadth 这类高噪音指标，必须要求连续确认或月末采样，避免日度抖动改变定投计划。
- 回测中显式模拟现金未投后的留存现金，不能把跳过买入当作消失的风险资产。
- 输出每月 decision log：信号值、分位数、multiplier、计划投入、实际投入、skip reason。

### Nasdaq / S&P Elimination Rules

除共享淘汰规则外，额外要求：

- CAPE/估值候选如果只在 1999-2002 有效，2010 年后多数窗口无效，则淘汰。
- VIX 候选如果提升终值主要来自危机后追涨，而非危机中可解释加码，则淘汰。
- Breadth 候选如果依赖当前指数成分回推，直接淘汰，不进入结果表。
- 利率/流动性候选如果没有明确发布时间滞后模型，淘汰。
- 任一组合如果需要超过 2 个外部信号同时可用才工作，先降级为研究观察项，不进入策略候选。

## IBIT Plan

### Post-AHR999 Baseline

IBIT 的下一步不是继续把 price-only pullback 当最终结论。当前研究结论已经是：
AHR999 GMA gate-tier 是显式启用 smart mode 时的优先候选，BTC price-history
pullback 只是兼容 fallback。后续研究应围绕两件事展开：

- AHR999 是否在更多样本切分、执行日扰动、贡献金额扰动下仍优于固定定投。
- AHR999 之外的指标是否提供独立信息，且数据供应能被稳定复现和审计。

### Candidate Indicators

| Indicator family | Candidate signals | Reproducibility tier | Notes |
| --- | --- | --- | --- |
| BTC price-derived | Mayer Multiple、SMA200 gap、252d drawdown、realized volatility、12M momentum | Tier 1: 可由缓存 BTC 日线稳定复现 | 可作为 AHR999 的 sanity check 或 fallback，不应重新覆盖 AHR999 结论 |
| AHR999 variants | GMA/SMA 200d cost、AHR999 moving average、AHR999 percentile、AHR999 slope | Tier 1/2: 公式冻结后可复现；若依赖外部 AHR999 服务则需 provenance | 优先验证当前 0.45/0.80/1.20 阈值是否稳健 |
| On-chain valuation | MVRV、MVRV Z-score、NUPL、realized price、SOPR | Tier 3: 需要 CoinMetrics/Glassnode 等外部供应 | 只有在有 point-in-time 历史、许可和滞后模型后才能比较 |
| Sentiment | Fear & Greed、funding rate、perp basis、social/news sentiment | Tier 3: 外部供应且定义可能变化 | 只能作为观察项；不得用不可审计历史优化阈值 |
| Market structure | BTC dominance、stablecoin supply、ETF flow、IBIT premium/discount、spot volume/liquidity | Tier 2/3: 多源数据，字段定义差异大 | 先用于解释 skip / add 行为，不直接驱动倍率 |

Tier 定义：

- Tier 1: 只依赖缓存 BTC 日线 OHLCV 和固定公式，可在本仓库或 signal-source
  包中完全复算。
- Tier 2: 需要外部原始数据，但字段定义稳定、可缓存、可记录 provider timestamp。
- Tier 3: 需要商业链上/情绪供应商或定义频繁变化的数据；没有供应合同和历史快照前，
  不得作为生产候选。

### Data Source Requirements

- BTC 价格主路径至少保留 `BTCUSDT` 或 `BTC-USD` 日线 close，记录交易所/供应商、
  时区、收盘时间、缺失日处理、异常价格过滤。
- AHR999 必须保存公式版本：genesis date、growth estimate formula、200d cost 使用
  GMA 还是 SMA、价格源和计算时点。
- MVRV/NUPL 等链上指标必须记录供应商、字段定义、发布时间延迟、历史覆盖开始日、
  修订政策、许可限制；没有这些元数据不跑正式对比。
- Fear & Greed 必须有每日历史快照和当天可获得时间；只有静态 CSV 且无法确认
  point-in-time 的，不进入候选。
- IBIT 交易价格/NAV 需要和 BTC 信号分开：信号可以用 BTC，交易回测应报告使用
  BTC proxy、IBIT market close 或 IBIT NAV proxy 的差异。

### Sample Split

IBIT 本身 2024-01-05 才上市，不能只用 IBIT live history 推断完整周期。建议分层：

| Layer | Window | Purpose |
| --- | --- | --- |
| BTC proxy full cycle | 2017-08-17 至最新完整日 | 继续复核现有 Binance/BTC proxy 结论 |
| Cycle windows | 2018-04-25 起、2020 cycle、2022 bear/recovery | 检查 AHR999 在不同周期位置的表现 |
| IBIT live proxy | 2024-01-25 至最新完整日 | 检查 ETF 上市后 tracking、执行和短样本风险 |
| Provider overlap | 以 MVRV/NUPL/Fear&Greed 可得历史为准 | 只在外部数据覆盖完整且可审计时运行 |

建议切分：

- Discovery: 2017-08-17 至 2020-12-31，只验证指标方向。
- Validation: 2021-01-01 至 2023-12-31，用于固定少数阈值和倍率。
- Out-of-sample: 2024-01-01 至最新完整日，重点看 IBIT-launch proxy 是否继续可接受。
- Rolling starts: 每季度一个起始点，至少覆盖 2018Q2 到 2025Q1。
- Execution jitter: 月度第 10/15/20/25 日、月末、周度 cadence 都要跑。

### Backtest Method

- 以固定定投为主基准，当前 AHR999 GMA gate-tier 为 smart baseline。
- 任何新指标先作为 overlay 生成 multiplier，不直接改 `ibit_smart_dca.py`。
- 新指标不得使用 price-only pullback 结果替代 AHR999 结论；price-only 只作为 fallback
  和 Tier 1 对照。
- 先做单指标比较，再做最多两指标组合；两指标组合必须能解释冲突处理，例如
  AHR999 cheap 但 MVRV expensive 时维持 1.0x 而不是扩大参数网格。
- 对 Tier 3 数据先跑 availability report：覆盖率、缺失率、滞后、修订、许可。
  availability 不过关就不跑策略排名。
- 显式报告 skipped buys 与现金余额路径；AHR999 expensive zone 的 0.0x 可能提高现金
  留存，不能把现金拖累隐藏掉。
- 单独报告 IBIT live window，即使样本短；若 live window 明显落后固定定投，不能把
  全周期 BTC proxy 的胜出当作最终上线理由。

### IBIT Elimination Rules

除共享淘汰规则外，额外要求：

- 新指标不能在 IBIT live proxy 窗口相对当前 AHR999 baseline 明显恶化；若恶化超过
  `2%` 终值且回撤改善小于 `2 percentage points`，淘汰。
- Tier 3 指标若无法提供 provider timestamp、历史字段定义和许可说明，淘汰。
- MVRV/NUPL/Fear&Greed 组合如果只因短期顶部避开买入而胜出，但多数 rolling starts
  落后固定定投，淘汰。
- Mayer/SMA/drawdown 类 price-derived 指标如果不能补充 AHR999，只能保留为 fallback，
  不得替代 AHR999 smart baseline。
- 任一组合需要超过 3 档 BTC 周期状态或超过 4 个阈值，淘汰；当前 DCA profile 不适合
  高维择时模型。
- 如果外部信号缺失时的 fallback 会把策略行为从 AHR999 切到 price-only 且结果差异
  很大，必须先设计 stale/missing-data handling；否则不进入策略候选。

## Deliverables

下一轮研究产物应按以下顺序交付：

1. `SignalBundle` 样例数据：Nasdaq/S&P 外部信号与 IBIT BTC/AHR999/Tier 1 指标各一份。
2. Backtest notebook 或脚本：能复现固定定投、当前 smart baseline、候选指标结果。
3. Metrics CSV：包含所有候选、失败组合、样本切分、执行日扰动和评价指标。
4. Decision log：逐月记录每个候选的 signal、multiplier、planned investment、skip reason。
5. Research summary：只推荐通过淘汰规则的少数候选；没有候选通过时明确保持普通定投默认。

当前仓库先落地一个轻量离线入口：`us_equity_strategies.backtests.smart_dca_research`。
它只接受调用方提供的 pandas price series，不下载外部数据；候选集固定为少量命名 preset，
用于比较 fixed DCA 与 `nasdaq_sp500_price_defensive`、`nasdaq_sp500_price_no_skip`、
`nasdaq_sp500_production_equivalent`、`ibit_btc_ahr999_price`、
`ibit_btc_ahr999_precomputed`、`ibit_btc_ahr999_precomputed_variants`、
`ibit_btc_ahr999_mayer_cycle`、
`ibit_btc_precomputed_ahr999_mayer_cycle`、`ibit_btc_precomputed_ahr999_sma_mayer_cycle`
等
研究候选。`evaluate_candidate_results` 会按本节淘汰规则标注候选是否通过、失败原因、
跳过买入比例和相对 fixed DCA 的回撤差异；`results_to_metrics_rows` 与
`results_to_decision_log_rows` 则提供可直接写入 CSV 的 metrics 和逐月 decision log
行；`results_to_equity_curve_rows` 会输出每日账户权益、现金、持仓、投入和 drawdown，
便于检查 underwater duration；`results_to_cash_flow_rows` 会输出月度注资和期末账户价值，
用于审计 money-weighted return / XIRR；`write_research_artifacts` 会把 metrics、evaluation summary、
decision log、equity curve、cash flows、candidate summary 和 candidate specs 写成
CSV artifact；`compare_monthly_execution_day_scenarios` 用固定候选集跑不同月度执行日，
用于检查结论是否依赖单一执行日；`write_scenario_research_artifacts` 会为每个场景写出
独立 CSV、`run_manifest.json` 和顶层 `scenario_index.csv`、`robustness_summary.csv` /
`scenario_manifest.json`，
其中 manifest 记录每个 CSV 的 sha256。CLI 写出的 `scenario_manifest.json` 还会记录
candidate set、执行日、定投金额、rolling-start 起点、日期列、输入 CSV 路径、输入 CSV
sha256 和文件大小，方便后续复现实验并证明没有临时更换数据。这个 helper 是后续批量数据回测和结果 CSV 的基础。

生产等价候选必须和研究 variant 分开记录：

- `nasdaq_sp500_smart_dca` 的生产等价 smart 候选是
  `nasdaq_sp500_price_no_skip`，也可以通过
  `nasdaq_sp500_production_equivalent` candidate set 直接运行。昂贵区降额或跳过的
  `nasdaq_sp500_price_defensive` 只是研究 variant，不代表当前生产 smart-mode 默认参数。
- `ibit_smart_dca` 的优先生产等价 smart 候选是
  `ibit_btc_precomputed_ahr999_cycle`，即使用外部 `derived_indicators` 里的 AHR999
  分档，不让 Mayer Multiple 改变分档；Mayer 相关候选只作为 sanity check / variant。
- `candidate_specs.csv` 和 `candidate_summary.csv` 会输出 `candidate_role` 与
  `production_equivalent_profile`，用于审计回测验证的是生产等价规则还是研究 variant。
- 当 CLI 使用 precomputed IBIT AHR999 候选并传入 `--signal-manifest` 时，manifest 必须是
  `research_export.v1`、`artifact_type=btc_cycle_research_csv` 且
  `transform=crypto.btc.ahr999.v1`。这防止列名看似正确但 transform 版本不匹配的 CSV
  进入候选选择流程。
顶层 `review_decision.json` 会显式区分 `observed_best_smart_candidates`
和 `runtime_default_recommendation = fixed_dca`：即使某个智能候选在场景矩阵中表现最好，
也只会进入 `manual_review_candidate`，不会自动改变生产默认定投模式或当前生产策略行为。
每个场景还会写 `candidate_summary.csv` 和 `candidate_specs.csv`。`candidate_summary.csv`
按候选记录 parameter count、threshold count、multiplier count、unique multiplier count、
min/max multiplier、是否允许 0 倍跳过、`open_parameter_search=false` 等候选复杂度字段；
`candidate_specs.csv` 再按候选和参数逐行记录 family、rule type、signal symbols、
min history、parameter name/value。两者共同作为候选参数冻结和防过拟合证据。

本地 CSV 研究入口可以直接生成同一组 artifact，便于后续把外部数据源仓库产出的
price CSV 或预计算指标 CSV 接到研究流程里。若输入是 BTC close，并希望研究工具内部复算
AHR999 / Mayer，可使用 price-derived 候选：

Nasdaq/S&P 当前也提供 `nasdaq_sp500_price_variants`，同时跑 defensive 和 no-skip
两个固定候选；no-skip 把昂贵区间倍率固定为 `1.0`，用于检查 price-only 智能定投跑输
fixed DCA 是否主要来自少投或跳过买入后的现金拖累，而不是作为生产默认。

```bash
python -m us_equity_strategies.backtests.smart_dca_research_cli \
  --signal-csv ./research_inputs/signals.csv \
  --trade-csv ./research_inputs/trade_prices.csv \
  --output-dir ./artifacts/smart_dca_research/ibit_btc_ahr999 \
  --candidate-set ibit_btc_ahr999_mayer_price \
  --signal-columns BTC-USD \
  --trade-column close \
  --execution-days 1,10,15,20,25 \
  --cadences weekly,monthly,quarterly \
  --monthly-contribution-usd-values 500,1000,3000 \
  --start-dates 2018-04-25,2020-01-02,2021-01-04,2024-01-02 \
  --pretty
```

若输入来自 `MarketSignalSources` 导出的 BTC cycle research CSV，已经包含 `ahr999` 和
`mayer_multiple`，可直接使用 precomputed 候选，避免在策略研究层重复实现数据源侧指标：

```bash
python -m us_equity_strategies.backtests.smart_dca_research_cli \
  --signal-csv ./research_inputs/btc_cycle_indicators.csv \
  --trade-csv ./research_inputs/ibit_prices.csv \
  --signal-manifest ./research_inputs/btc_cycle_indicators.manifest.json \
  --output-dir ./artifacts/smart_dca_research/ibit_btc_cycle_precomputed \
  --candidate-set ibit_btc_ahr999_mayer_precomputed_variants \
  --signal-columns ahr999,ahr999_sma,mayer_multiple \
  --trade-column close \
  --execution-days 1,10,15,20,25 \
  --cadences weekly,monthly,quarterly \
  --monthly-contribution-usd-values 500,1000,3000 \
  --start-dates 2018-04-25,2020-01-02,2021-01-04,2024-01-02 \
  --pretty
```

这两个候选使用同一组阈值和倍率。区别只在信号来源：`ibit_btc_ahr999_mayer_cycle`
从 BTC close 内部复算，`ibit_btc_precomputed_ahr999_mayer_cycle` 信任外部
`derived_indicators` 宽表中的 `ahr999` / `mayer_multiple`。显式跑 variants 候选集时，
还会比较 `ahr999_sma` 口径和 no-skip 口径：前者检查 GMA/SMA 公式选择是否改变结论，
后者检查智能定投跑输 fixed DCA 是否主要来自昂贵区间跳过买入后的现金拖累。后续比较时
应把这些固定候选同时放进 robustness matrix，检查公式版本、provider timestamp 和字段覆盖
是否会改变结论。
使用 `MarketSignalSources` 导出的 research CSV 时，应同时传入 `--signal-manifest`；
CLI 会把上游 `research_export.v1`、transform、source version、列集合、日期范围和
CSV hash 校验结果写入 `scenario_manifest.json`。manifest 中出现疑似密钥、token、cookie
或 signed URL 字段，缺少 `output_csv.sha256`，声明的 hash / size 与实际 CSV 不一致，
或 `research_export.v1` 的 columns、row_count、first_date、last_date 与 CSV 实际内容
不一致时，研究运行会直接失败。
若把运行时 `market_signal_bundle.v1` 交给策略平台，应先用 consumer contract 校验
`consumer_contract.compatible_profiles` 和字段覆盖；例如
`--consumer research:ibit_btc_ahr999_mayer_precomputed_variants` 会要求 bundle profile
包含该 consumer，且 `BTC-USD` payload 同时包含 `ahr999`、`ahr999_sma` 和
`mayer_multiple`。

CLI 只读取本地 CSV、写出 metrics / decision log / manifest，不负责下载行情、读取密钥
或选择 vendor。这样研究回测和后续 `MarketSignalSources` 的 provider adapter 仍保持解耦。
未设置 `--monthly-contribution-usd-values` 时，CLI 只使用单个
`--monthly-contribution-usd`；设置后会生成执行日 × 定投金额的稳健性矩阵，帮助确认
智能定投候选不是只在某一个现金流尺度下偶然胜出。
未设置 `--start-dates` 时，CLI 按单一起始日运行；设置后会生成起始日 × 执行日 ×
定投金额矩阵，适合先跑季度或年度 rolling-start smoke test。
`--cadences` 默认只跑 `monthly`；设置为 `weekly,monthly,quarterly` 后会把 cadence 纳入
矩阵。周投金额按 `monthly_contribution_usd * 12 / 52` 折算，季投金额按
`monthly_contribution_usd * 3` 折算，用同一个月度基准值近似保持年化现金流可比。
metrics 目前会包含 `max_underwater_days`、`worst_relative_value_gap_after_1y_pct`、
`worst_relative_value_gap_after_2y_pct`、`worst_relative_value_gap_after_3y_pct` 和
`money_weighted_return_pct`，以及 `average_cash_ratio_pct`、`max_cash_ratio_pct`、
`terminal_cash_ratio_pct`、`scheduled_decision_count`、zero/boosted multiplier 占比和
`regimes_seen`，
用于先筛掉长期显著落后 fixed DCA 的候选；这些列不是完整 rolling-start 回测的替代品，
后续仍需要按季度或年度起点重跑样本外窗口。
顶层 `robustness_summary.csv` 会按候选聚合所有场景，报告 `pass_rate`、`passed_count`、
`review_status`、`review_rank`、最差终值差距、最差回撤差距、最大跳过比例、最差
rolling gap、最差和中位 money-weighted return、最大现金占比、`weakest_scenario` 和失败原因。
`review_status` 只表达矩阵内所有场景是否
都通过 promotion gate，不自动上线策略；智能定投候选进入下一轮人工评审前，应优先看这个
汇总，而不是只挑单个最优场景。
顶层 `scenario_coverage.csv` 是矩阵级审计文件，用来确认场景数量是否达到
`--min-review-scenarios`、每个场景是否都有 fixed benchmark、候选集是否一致；覆盖不足时
不会阻止 CSV 生成，但会在 `coverage_status` 和 `failure_reasons` 中显式标记。它也会记录
`scenario_cadences`、`scenario_execution_days`、`scenario_contribution_amounts_usd`
和 `scenario_start_dates`，用于确认研究矩阵确实覆盖了频率、执行日、金额和 rolling-start
扰动，而不是只在单一配置上挑选候选。它还会从 fixed benchmark equity curve 记录
`scenario_sample_windows`、样本首尾日期和 `scenario_sample_window_audit_passed`，用于确认
每个场景实际使用的历史窗口。`coverage_gate_passed` 还要求至少一个可识别维度
确实有多个取值；如果只是用多个手写场景名凑够数量，会记录
`scenario_dimension_coverage_missing` 并保持 `hold_default_fixed_dca`。
顶层 `selection_summary.csv` 会按候选 family 选择当前矩阵内最强的固定候选，并明确给出
`recommendation_status`。若没有候选通过全部 robustness gate，它会标记
`hold_default_fixed_dca`，避免把“最不差”的 smart 版本误当成可上线推荐。
若 `scenario_coverage.csv` 的 `coverage_gate_passed=false`，`selection_summary.csv` 也会保持
`hold_default_fixed_dca`，并记录 `insufficient_scenario_matrix_coverage`，避免候选集不一致的
矩阵进入人工评审。
`selection_summary.csv` 同时复制矩阵级覆盖字段，例如 `matrix_scenario_count`、
`matrix_scenario_cadences`、`matrix_scenario_execution_days`、
`matrix_scenario_contribution_amounts_usd`、`matrix_scenario_start_dates`、
`matrix_scenario_sample_windows`、`matrix_scenario_varied_dimensions`、
`matrix_candidate_set_consistent`、`matrix_fixed_benchmark_present_all` 和
`matrix_candidate_names`，便于人工评审时只看 selection summary 也能识别覆盖缺口。
它还会复制被选候选跨场景的 zero/boosted multiplier 占比、scheduled multiplier 范围和
`selected_regimes_seen`，便于确认候选不是只靠某一个 regime 或跳过买入路径胜出。
`selection_summary.csv` 还会执行固定 effect-size gate：候选最差相对终值不能低于 fixed，
中位相对终值至少要高于 fixed `1%`，最差 `rank_score` 不能为负，且终值现金占比不能超过
`35%`；这些阈值是预先固定的反过拟合门槛，不会按回测结果搜索。未达标时会保持
`hold_default_fixed_dca`，并把
`recommendation_reason` 记录为 `insufficient_effect_size_vs_fixed_dca`，避免“所有场景都只是
边际跑赢”或长期留现金的智能定投被误认为值得上线。
顶层 `review_decision.json` 是给平台 CI 或研究看板读取的单一机器可读结论，会汇总
`scenario_coverage.csv` 和 `selection_summary.csv` 的 gate 状态、阻塞原因、selection group
以及被选候选的定义 hash；它也会在顶层记录 `selection_policy`、`effect_size_policy` 和
固定 effect-size 阈值，便于 CI 证明本次选择没有临时搜索参数。它只决定是否进入人工评审，
不会绕过人工评审直接启用智能定投。
即使候选在已有场景内全部通过，也必须达到 `--min-review-scenarios`（默认 3）后才会进入
`promote_to_manual_review`；否则 `recommendation_reason` 会记录为
`insufficient_robustness_scenarios`。这个 gate 不是统计显著性证明，只是防止单一窗口或单一
现金流设置被误用为上线证据。
`candidate_summary.csv` 和 `selection_summary.csv` 同时记录候选定义 SHA-256，包括 family、
rule type、signal symbols、min history 和参数集合，用来证明被选择的是预先固定的 preset，
不是回测后临时搜索出的参数组合。

## Promotion Gate

只有同时满足以下条件，才考虑后续策略代码改动：

- 数据源可复现、可缓存、可审计，且符合对应 profile 的 runtime 输入边界。
- 样本外和 rolling starts 结果均通过淘汰规则。
- 候选规则比当前实现复杂度只小幅增加，且有清晰 missing/stale data 行为。
- 固定定投默认不变；智能模式仍需显式配置开启。
- 评审明确记录：该改动是研究驱动的可选 smart sizing，不是收益承诺。
