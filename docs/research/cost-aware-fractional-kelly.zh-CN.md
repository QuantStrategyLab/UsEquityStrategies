# R5 成本感知分数 Kelly 目标（仅研究）

模块：`us_equity_strategies.research.cost_aware_fractional_kelly`
测试：`tests/research/test_cost_aware_fractional_kelly.py`（只用合成输入，无网络）

## 为什么需要

RS-04 只在路径结束后用 `cost_stress_recompute` 扣成本；Kelly 目标本身没有交易成本，因此高换手策略可能被高估。R5 把单边成本放进目标决策，先形成一个可独立验证的 kernel，再考虑接回 walk-forward。

同窗口滚动前推对照已落为独立模块 [cost_aware_walk_forward_comparator](cost-aware-walk-forward-comparator.zh-CN.md)：同一折、同一 fit、同一测试窗口，比较固定分数 Kelly 与成本感知带的净增长、回撤、CVaR 和换手。

## 公式

输入均为每会话小数：

- `mu`：已收缩的超额均值，直接使用 RS-04 `fit["mu_shrunk"]`，不得再传原始均值；
- `sigma2`：样本方差（ddof=1）；
- `c`：分数 Kelly，`0 < c < 1`；
- `k = cost_bps_per_side / 10_000`；
- `H = holding_sessions`（成本摊销的持有会话数）；
- `w0`：当前权重，`0 ≤ w0 ≤ 1`。

无成本目标：

```text
center = clip(c * mu / sigma2, 0, position_cap)
```

使用分数 Kelly 对应的二次效用：

```text
U(w) = mu * w - sigma2 * w^2 / (2 * c)
```

其最大值正是 `center`，曲率为 `sigma2 / c`（等效风险厌恶 `1/c`）。加入成本后最大化 `U(w) - (k/H) * |w - w0|`，得到无交易带：

```text
delta = c * k / (H * sigma2)
[lower, upper] = [center - delta, center + delta] ∩ [0, position_cap]
```

决策：

- `w0 < lower`：目标 `lower`（从下方进入，但不追到 center）；
- `w0 > upper`：目标 `upper`（从上方减仓，但不追到 center）；
- 带内：保持 `w0`，不交易。

零成本时 `delta = 0`，退化为 RS-04 的分数 Kelly 目标。`position_cap` 是硬上限；`w0` 因漂移超过上限时仍会拉回上限以内。

## API

```python
from us_equity_strategies.research.cost_aware_fractional_kelly import (
    CostAwareKellyPolicy,
    cost_aware_fractional_kelly,
    from_fractional_kelly_fit,
)

policy = CostAwareKellyPolicy(
    c=0.25,
    position_cap=1.0,
    cost_bps_per_side=5.0,
    holding_sessions=1,
    min_trade_weight=0.0,
)

target = cost_aware_fractional_kelly(
    mu_excess_shrunk=fit["mu_shrunk"],
    sigma2=fit["sigma2"],
    current_weight=0.10,
    policy=policy,
)

# 或直接消费 RS-04 fit，自动检查 c 一致并拒绝 PARKED fit
target = from_fractional_kelly_fit(fit, current_weight=0.10, policy=policy)
```

## 输出字段

- `status`：`COMPUTED`（有交易）、`HOLD`（带内/低于最小交易额/无正边际）、`PARKED`（零方差或上游 fit 不可用）；
- `reason_code`：`WITHIN_NO_TRADE_BAND`、`BELOW_MIN_TRADE_WEIGHT`、`NO_POSITIVE_EDGE`、`CURRENT_ABOVE_POSITION_CAP`、`ZERO_VARIANCE`、`FIT_NOT_COMPUTED` 等；
- `target_weight` / `trade_weight`：研究目标与差额，不是订单；
- `no_trade_lower` / `no_trade_upper` / `cost_half_band` / `band_saturated`；
- `estimated_cost_fraction`：本次交易的单边成本占资本比例；
- `gross_log_growth_per_session_at_current` / `at_target`、`net_log_growth_per_session_at_target`：按 `mu*w - 0.5*sigma2*w^2` 计算的披露值；
- 恒定 `no_order=True`、`live_ready=False`、`budget_effect="NONE"`。

## 边界

- 仅研究；不产生订单、不提高预算、不授权晋级/纸面/影子/实盘。
- 不加杠杆、不做空；`current_weight` 必须在 `[0, 1]`。
- 成本是调用方假设，不是券商事实；`band_saturated=True` 表示成本带覆盖整个可配区间，此时不应把 `HOLD` 解释为“成本为零”。
- 报告的增长是二次近似，不是精确复利；没有 PIT、OOS、真实数据或实际 consumer 证据。
- 运行时入口/策略不得导入本模块；边界测试沿用 `tests/test_research_import_boundary.py`。

## 已验证

- 19 项合成测试：零成本退化、成本上升降低入场目标、带内持有、上沿减仓、硬上限、最小交易额、负边际、零方差 PARKED、RS-04 收缩均值消费、`c` 不一致拒绝、净增长手算、JSON 安全、无授权字段、饱和带、换手低于逐日全额再平衡。
- 相关回归：RS-04、B4、研究导入边界测试通过。

## 合成 sanity（不是收益证据）

固定参数 `mu=0.00125`、`sigma2=0.0025`、`c=0.5`、`H=1`、500 个合成会话、单边 5 bps：

| 方案 | 总换手 | 费用占资本 | 每会话 log growth | 最大回撤 |
| --- | --- | --- | --- | --- |
| 逐日再平衡到 0.25 | 1.008 | 0.0005 | 0.000290 | -0.0271 |
| cost-aware 带 | 0.152 | 0.0001 | 0.000218 | -0.0250 |

这组样本里成本-aware 明显降低换手和费用，但因为入场目标只有 0.15（低于 0.25），净增长反而更低。**不能把“省成本”当成“提高复利”**。同窗口 walk-forward 对照已实现，但仍只有合成证据；`H`（预期持有期）与 `c` 尚未校准，bootstrap 区间也包含 0。

`holding_sessions` 是成本摊销的持有期，不是“每天都重新估一次”的窗口。若策略实际持有数周，用 `H=1` 会把成本看得过重，导致长期低配。

## 尚未做

1. 在 pinned QPK 的 `research-stats-integration` job 中运行 comparator，确认费用口径；本地只用了替代副本。
2. 在真实 SOXL/TQQQ 数据上按预注册窗口运行；当前只有合成证据。
3. 与 B4 后验、CVaR、组合 Kelly 组合时，先做同输入 oracle，不直接接运行时。
