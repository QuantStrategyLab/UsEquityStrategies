# R5 同窗口滚动前推对照：固定分数 Kelly vs 成本感知带（仅研究）

模块：`us_equity_strategies.research.cost_aware_walk_forward_comparator`
测试：`tests/research/test_cost_aware_walk_forward_comparator.py`（只用合成输入，无网络）

## 做什么

在**完全相同的折、拟合参数、拼接测试窗口和费用档**上比较两种 sizing：

| 变体 | 规则 |
| --- | --- |
| `kelly_c*` | RS-04 固定分数 Kelly：每个会话开始再平衡到该折的 `f` |
| `kelly_cost_aware_c*` | R5 无交易带：用同一折的 `mu_shrunk` / `sigma2` 建带，带内保持当前权重 |

关键约束：

- 两变体消费同一个 `fit_fractional_kelly` 结果；没有第二次收缩、没有全样本重估。
- 固定路径直接复用 RS-04 `_scaled_path`，测试锁定与 RS-04 逐元素一致。
- 决策只用基础单边成本 `cost_bps_per_side`；`multipliers` 只在事后由 QPK `cost_stress_recompute` 做会计压力，不反写目标。因此各档描述同一条决策路径。
- 某折 fit 为 `PARKED` 时，两变体都回到现金（目标 0）；成本带不允许在无有效 fit 的折上继续持仓。
- `position_cap` 同时约束固定变体和成本感知变体，默认 1.0 时不改变 RS-04 基线。
- `holding_sessions` 是成本摊销假设，每个会话重新评估，不是“必须持有 H 个会话”的承诺。

## API

```python
from us_equity_strategies.research.cost_aware_walk_forward_comparator import (
    evaluate_cost_aware_walk_forward,
)
from us_equity_strategies.research.fractional_kelly_walk_forward_plan import WalkForwardPlan

result = evaluate_cost_aware_walk_forward(
    strategy_returns=rows,              # [(date, full_exposure_return), ...]
    plan=WalkForwardPlan(train_sessions=120, test_sessions=40, purge_sessions=2),
    cost_bps_per_side=5.0,
    holding_sessions=1,                 # 研究假设，真实数据前须预注册
    min_trade_weight=0.0,
    fractions=(0.25, 0.5),
    multipliers=(1.0, 2.0, 3.0),
    assume_zero_cash=True,
    bootstrap_seed=7,
)
```

不支持真实数据和回测报告：`synthetic=False` 抛 `REAL_DATA_REQUIRES_PREREGISTRATION_EXTENSION`，`report_context` 抛 `REPORT_CONTEXT_NOT_SUPPORTED_IN_COMPARATOR`。原因分别是预注册尚未包含 `holding_sessions` / `min_trade_weight`，以及本切片不改变 RS-04 的报告与 DSR 试验集。

## 输出

- `comparators`：四个变体（两个 `c` × 固定/成本感知），每个含 `tiers`（1x/2x/3x）和 `path_summary`；
- `tiers[*]`：`log_growth_per_session`、`cagr`、`max_drawdown`、`cvar_95_daily`、`turnover_total`、`total_cost_log_drag`；
- `path_summary`：再平衡会话数、再平衡换手、总换手、目标权重均值/最小/最大、决策状态计数；
- `paired_deltas_net1x`：成本感知减固定，逐项配对；可选 `paired_stationary_bootstrap`（净 1x log growth 差的 95% 区间）；
- 恒定 `live_ready=False`、`no_order=True`、`budget_effect="NONE"`；`kelly_ready_role=COMPARATOR_ONLY_NEVER_RAISES_BUDGET`。

符号约定：log growth / CVaR 差值 > 0 偏向成本感知；`max_drawdown` 差值 > 0 表示回撤更浅；换手或成本拖累差值 < 0 表示更省。

## 合成 sanity（不是 OOS，不是收益证据）

参数：种子 7、400 个工作日、日收益 `N(0.001, 0.05^2)`、240 个拼接测试会话、单边 5 bps、`H=1`。

| 变体 | 每会话 log growth | CAGR | 最大回撤 | CVaR 95% | 总换手 | 成本 log drag |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `kelly_c0.25` | -0.000202 | -4.96% | -17.82% | -2.68% | 1.435 | 0.000718 |
| `kelly_cost_aware_c0.25` | -0.000176 | -4.33% | -17.40% | -2.52% | 0.715 | 0.000358 |
| `kelly_c0.5` | -0.000485 | -11.50% | -33.39% | -5.35% | 2.236 | 0.001118 |
| `kelly_cost_aware_c0.5` | -0.000378 | -9.09% | -31.73% | -4.88% | 1.357 | 0.000679 |

配对差（成本感知 − 固定）：

| 对比 | Δlog/session | ΔCAGR | ΔMDD | ΔCVaR | Δ换手 | bootstrap 95% CI（Δlog） | P(Δ≤0) |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| c=0.25 | +0.000026 | +0.63pp | +0.42pp | +0.16pp | -0.720 | [-0.000169, +0.000203] | 38.3% |
| c=0.5 | +0.000107 | +2.41pp | +1.66pp | +0.47pp | -0.879 | [-0.000306, +0.000474] | 30.4% |

这组样本里成本感知带的点估计在净增长、回撤、CVaR 和成本上都更好，换手明显更低；但两个 bootstrap 区间都包含 0，不能据此宣称“成本感知提高复利”。负的绝对增长来自合成参数本身（5% 日波动、0.1% 日漂移、扣除成本后接近或低于零），不是策略结论。

## 边界

- 仅研究；不接运行时、插件、券商或账户，不产生订单或预算变更。
- 合成数据默认；真实数据须先扩展预注册，把 `holding_sessions`、`min_trade_weight`、`position_cap` 和窗口写死，再单独审阅。
- 本模块不计算 DSR、PSR、PBO；加入变体会改变 RS-04 的试验集与多重性修正。
- 成本带是二次效用下的局部决策规则，不是最优控制；QPK 会计口径为 `(1 - turnover*rate)*(1+gross) - 1`，与 R5 披露的 `estimated_cost_fraction` 不是同一个量。
- 运行时入口/策略不得导入本模块；边界沿用 `tests/test_research_import_boundary.py`。

## 已验证

- 17 项测试：固定路径与 RS-04 `_scaled_path` 逐元素一致；折内 fit 只来自训练段；零成本退化为固定分数 Kelly；成本带降低再平衡换手；权重不越界；PARKED 折回现金；内部换手计入会计但不改变带；`H` 与 `min_trade_weight` 的方向性；输入校验；缺 QPK 时无数字；公开输出的配对差、可复现 bootstrap，以及固定变体与 RS-04 公开输出的窗口/折/各档指标逐项一致。
- 本地用 QPK `research_stats` 副本（`39484acc`，非 UES pin `442b729`）跑通全部 17 项；pinned QPK 的 `research-stats-integration` CI 尚未运行。
- 相关回归：R5 kernel 19 项、RS-04 15 项、研究导入边界 1 项，共 52 项通过（同一副本）。

## 尚未做

1. 在 pinned QPK `442b729` 的 CI job 中运行本模块，确认 `cost_stress_recompute` 与本地副本口径一致。
2. 在真实 SOXL/TQQQ 数据上按预注册窗口运行；当前只有合成证据，且 `H`、`c`、`min_trade_weight` 尚未校准。
3. 组合层（B4 后验、CVaR、组合 Kelly）的同输入 oracle；本模块不做组合。
