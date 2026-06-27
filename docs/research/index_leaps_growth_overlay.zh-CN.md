# 指数 LEAPS 增长增强层研究设计

[English](index_leaps_growth_overlay.md)

_更新日期：2026-06-23_

> 投资有风险。本文是策略工程研究记录，不构成投资建议，也不代表任何账户应当交易期权。

## 初步结论

买入 `QQQ` / `SPY` LEAPS call 可以作为“增长增强层”研究，但不应直接替代当前收入层。

当前收入层的目标是降低账户级压力回撤：`SCHD`、`DGRO`、`SGOV`、`SPYI`、`QQQI` 等资产按 `log_total_drawdown_budget` 反推比例，服务于大账户波动钝化。LEAPS call 的收益来源不同，它是用有限权利金换取长期指数上涨凸性，最大亏损通常是权利金，组合效果更像受限预算的再杠杆，而不是稳定收入。

因此建议把结构拆成两层：

| 层 | 目标 | 默认处理 |
| --- | --- | --- |
| 收入 / 稳定层 | 降低总组合压力回撤，保留现金流和防守资产 | 保持现有 `income_layer_*` 默认，不把 LEAPS 放入 `income_layer_allocations` |
| LEAPS 增长增强层 | 用小额权利金预算增加 `QQQ` / `SPY` 长期上涨凸性 | 默认配置可见且总开关开启；配方未晋级前由 live gate 阻断，不产生真实订单意图 |

## 外部事实核对

- Cboe 对 LEAPS 的定位是长期期权，最长可到约 3 年，买方以权利金换取长期方向性参与权，而不是持有股票本身。
- OCC 风险披露口径下，买入期权的最大损失一般是已支付权利金和交易成本；但权利金可以全部损失，期权也可能到期归零。
- `SPY` 和 `QQQ` 都是高流动性宽基 ETF，适合作为期权研究底层资产；但期权回测必须使用合约链、bid/ask、希腊值、到期日和分红 / 利率假设，不能只用 ETF 收盘价替代。
- 公开研究里也有反例：长期深度价内 call 并不天然优于股票，尤其在下跌和高隐含波动环境中，时间价值损耗与入场估值会明显影响结果。

参考资料：

- https://www.cboe.com/tradable_products/equity_indices/leaps_options/specifications/
- https://www.theocc.com/company-information/documents-and-archives/options-disclosure-document
- https://www.ssga.com/us/en/intermediary/etfs/resources/doc-viewer#spy&prospectus
- https://www.invesco.com/qqq-etf/en/home.html
- https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1443511

## 仓库现状

`UsEquityStrategies` 已经有独立的 option overlay 执行意图框架：

- `tqqq_leaps_growth_v1`、`qqq_leaps_growth_v1`、`spy_leaps_growth_v1` 和
  `soxx_put_credit_spread_income_v1` 都只作为 `option_overlay.py` 内的 research candidate。
- 组合型 live profile 已携带默认启用的 `option_*` 配置；但这些 recipe 仍是 research candidate，
  `promotion_evidence = false`，entrypoint 会给出 `research_only_recipe` 跳过原因，不生成
  `option_order_intents`。
- `option_overlay.py` 会按 option chain 生成 `option_order_intents`，包括 LEAPS call、put credit spread 的筛选、预算和跳过原因；`entrypoints/_common.py` 只保留兼容导出和配置弹出工具。

本次补充：

- 新增 `spy_leaps_growth_v1`，允许 runtime config 选择 `SPY` LEAPS call。
- 将 option overlay recipe、合约链解析和意图生成从 entrypoint common 工具拆出到独立模块，保持收入层和期权层职责分离。
- 默认 profile 携带期权 overlay 配置，便于设置面和诊断统一；但 live gate 在真实期权链 evidence
  通过前保持关闭，避免未经验证改变实盘行为。

## 候选设计

### `index_leaps_light`

适合先做 paper / advisory 观察。

| 参数 | 建议 |
| --- | --- |
| 底层 | `QQQ` 或 `SPY`，不优先使用 `TQQQ` |
| 权利金预算 | 每个 underlier `1%`-`2%` NAV，组合总预算不超过 `3%` NAV |
| 到期 | 目标 `24` 个月，接受 `540`-`930` DTE |
| Delta | 目标 `0.70`-`0.80`，默认 `0.75` |
| 入场 | 底层在 200 日均线上方，63 日动量为正；或者大跌后重新收复长期趋势线 |
| 滚动 | DTE 低于 `12` 个月时滚动 |
| 止盈 | 合约价值达到成本约 `2x` 且数量允许时，卖出足够合约回收本金，保留 runner |
| 流动性 | bid/ask spread 占 mid 不超过 `8%`-`12%` |

### `index_leaps_balanced`

适合验证后给增长型账户使用。

| 参数 | 建议 |
| --- | --- |
| 底层 | `QQQ` 为主，`SPY` 分散；实现上可先用单一 recipe，后续再支持多底层篮子 |
| 权利金预算 | 总预算 `3%` NAV，硬上限 `5%` NAV |
| 入场 | 必须满足趋势门控；不在 MA200 下方摊低 LEAPS |
| 与收入层关系 | 保留收入层，不从 `SGOV` 防守资产里强行挪钱；权利金预算应来自增长预算 |

### `crash_reentry_leaps`

适合研究“熊市后重新站上趋势线”的高赔率窗口。

| 参数 | 建议 |
| --- | --- |
| 触发 | `QQQ` 或 `SPY` 从 `20%+` 回撤后重新站上 MA200，且 63 日动量转正 |
| 预算 | 首次 `1%` NAV，确认后最多加到 `3%` NAV |
| 目的 | 参与大级别修复，而不是日常持有 LEAPS |

## 回测验证口径

正式 promotion evidence 应放在 `UsEquitySnapshotPipelines`，而不是只在策略仓库写结论。

### 输入数据

必须有真实期权链历史，至少包括：

- 底层 ETF 复权价格：`SPY`、`QQQ`。
- 合约级日线：expiration、strike、right、bid、ask、mid/mark、volume、open interest。
- 希腊值：delta、theta、vega；没有 vendor greeks 时要固定模型重算并记录参数。
- 利率与分红假设：短债利率、ETF 分红收益率。
- 交易假设：开仓用 ask 或 mid+滑点，平仓用 bid 或 mid-滑点，合约乘数 100，佣金可单独建列。

### 代理验证

如果暂时拿不到历史期权链，可以先做 Black-Scholes 代理，但只能用于筛方向，不可作为 promotion evidence：

- 用 `SPY` / `QQQ` 复权价格生成 252 日 realized vol。
- 用 `realized_vol * IV multiplier` 估计隐含波动，设置 floor/cap。
- 每次入场反解目标 delta strike。
- 每日按剩余 DTE、底层价格、估计 IV 重新定价。
- 明确记录误差：忽略真实 skew、合约流动性、早期行权、分红变化和实盘 bid/ask。

### 对照组

至少比较这些组：

- 当前默认收入层，无 LEAPS。
- 当前默认收入层 + `QQQ` LEAPS 1% / 3% / 5% NAV budget。
- 当前默认收入层 + `SPY` LEAPS 1% / 3% / 5% NAV budget。
- 只买 `QQQ` / `SPY`。
- 当前 `tqqq_growth_income` / `russell_top50_leader_rotation` 默认策略。
- 现有 `soxx_put_credit_spread_income_v1`，作为“卖方收入”对照。

### 指标

推广前必须同时看收益和失败路径：

- CAGR、总收益、最大回撤、Calmar。
- 最差 1 年 / 3 年滚动收益。
- 相对 `QQQ` / `SPY` 的超额 CAGR 和回撤差。
- 权利金归零次数、连续亏损开仓次数、平均持有期、到期前滚动比例。
- 最差单笔 LEAPS 亏损、最佳单笔收益、2x 回本触发率。
- 年化 turnover、bid/ask 成本、无法成交 / 合约不满足流动性筛选的比例。
- 与收入层相互影响：收入层比例、现金 / SGOV 比例、增长核心仓位被挤占程度。

## 风控边界

- LEAPS 默认不属于 `income_layer_allocations`。
- live 默认配置可以携带 `option_*` key；只有 `OPTION_OVERLAY_RESEARCH_CANDIDATES` 中对应 recipe
  晋级为 `status = live` 且 `promotion_evidence = true` 后才允许生成真实订单意图。
- 未有真实期权链证据前，LEAPS overlay 只能是 `shadow` / `paper` / `advisory`。
- 默认权利金预算不超过 `3%` NAV；研究可扫到 `5%`，但超过 `5%` 应视为显著再杠杆。
- 优先研究 `QQQ` / `SPY`，不优先推广 `TQQQ` LEAPS。`TQQQ` 本身已是杠杆 ETF，LEAPS 叠加后路径风险、波动率定价和流动性风险都更复杂。
- 不做裸卖期权；卖方收入策略必须是定义风险结构，例如 put credit spread。
- 任何默认切换必须附带短、中、长窗口 evidence，并通过同仓库的 contract / entrypoint 测试。

## Runtime 配置面

期权层现在有一个总开关和两个分层开关：

| 配置 | 作用 |
| --- | --- |
| `option_overlay_enabled` | 总开关；设为 `false` 时关闭所有期权 overlay，不生成 `option_order_intents`。 |
| `option_growth_overlay_enabled` | 增长增强层开关，例如 `QQQ` / `SPY` LEAPS call。 |
| `option_growth_overlay_recipe` | 增长增强层 recipe，例如 `qqq_leaps_growth_v1`、`spy_leaps_growth_v1`。 |
| `option_growth_overlay_start_usd` | 账户权益达到该阈值后才激活增长增强层。 |
| `option_growth_overlay_nav_budget_ratio` | LEAPS 权利金预算占 NAV 比例；实现侧硬限制不超过 `10%`。 |
| `option_income_overlay_enabled` | 期权收入层开关，例如定义风险 put credit spread。 |
| `option_income_overlay_recipe` | 期权收入层 recipe，例如 `soxx_put_credit_spread_income_v1`。 |
| `option_income_overlay_start_usd` | 账户权益达到该阈值后才激活期权收入层。 |
| `option_income_overlay_nav_risk_ratio` | 卖方结构最大风险预算占 NAV 比例；实现侧硬限制不超过 `2%`。 |

live 默认：组合型 profile 会带默认启用的期权层设置，但当前所有 recipe 仍是 research candidate。
因此 entrypoint 会生成期权层诊断，不会生成真实 `option_order_intents`；执行效果等同关闭，但设置面能提前固定。

| Profile | 默认期权层 | 默认 recipe | 起点 | 预算 |
| --- | --- | --- | ---: | ---: |
| `global_etf_rotation` | growth overlay | `spy_leaps_growth_v1` | `500000` | `1.5%` NAV premium |
| `tqqq_growth_income` | growth overlay | `tqqq_leaps_growth_v1` | `250000` | `3%` NAV premium |
| `soxl_soxx_trend_income` | income overlay | `soxx_put_credit_spread_income_v1` | `150000` | `1%` NAV max loss |
| `russell_top50_leader_rotation` | growth overlay | `spy_leaps_growth_v1` | `300000` | `1.5%` NAV premium |

`nasdaq_sp500_smart_dca` / `ibit_smart_dca` 是只买不卖的定投 profile，默认不带直接期权 overlay。

显式关闭期权层的兼容写法：

```json
{
  "option_overlay_enabled": false
}
```

示例 1：打开较保守的 `SPY` LEAPS 增长增强层，把权利金预算压到 `1.5%` NAV：

```json
{
  "option_overlay_enabled": true,
  "option_growth_overlay_enabled": true,
  "option_growth_overlay_recipe": "spy_leaps_growth_v1",
  "option_growth_overlay_start_usd": 250000,
  "option_growth_overlay_nav_budget_ratio": 0.015
}
```

示例 2：打开成长型 `QQQ` LEAPS 增长增强层，权利金预算为 `3%` NAV：

```json
{
  "option_overlay_enabled": true,
  "option_growth_overlay_enabled": true,
  "option_growth_overlay_recipe": "qqq_leaps_growth_v1",
  "option_growth_overlay_start_usd": 250000,
  "option_growth_overlay_nav_budget_ratio": 0.03
}
```

LEAPS 开仓不是盲买 call。`SPY` / `QQQ` recipe 会先看 entrypoint 的 `signal` / `regime`，
并在底层指标存在时要求长期趋势和中期动量通过，例如 `above_200dma` / `sma200_pass`
为真且 `momentum_63d` 为正；否则只记录 skip，不生成买入意图。

## 建议路线

1. 保留现有收入层默认。
2. 用 `spy_leaps_growth_v1` / `qqq_leaps_growth_v1` 做 runtime override 或 paper signal 观察。
3. 使用 `UsEquitySnapshotPipelines` 的 `useq-research-index-leaps-growth-overlay` 先跑
   Black-Scholes proxy research；该输出只用于方向筛选，不能作为 promotion evidence。拿到真实
   历史期权链后，用同一 CLI 的 `--mode option-chain --option-chain <csv>` 跑 bid/ask 链路，
   作为后续 promotion review 的证据输入。
4. 若 `QQQ` / `SPY` LEAPS 在 2000、2008、2020、2022 等窗口的 worst-case 可接受，再把对应
   recipe 晋级为 `status = live` 且写入 `promotion_evidence = true`；优先从宽基
   `qqq_leaps_growth_v1` / `spy_leaps_growth_v1` 开始，不优先启用 `TQQQ` LEAPS。
5. 若结果只是在牛市提高 CAGR、但最差 1-3 年窗口明显恶化，则保持为手动 overlay，不进入默认配置。
