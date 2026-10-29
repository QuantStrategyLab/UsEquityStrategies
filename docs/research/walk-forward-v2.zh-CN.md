# Walk-forward v2 研究评估入口

v2 是显式 opt-in 的合成研究入口。原 `fractional_kelly_walk_forward`、
`portfolio_kelly_walk_forward` 及其默认消费者保持 v1；M2 冻结源码、协议、
source guard、tag 和哈希不变。旧入口对正 embargo 的连续切片限制仍然存在。

```python
from us_equity_strategies.research.fractional_kelly_walk_forward_plan import WalkForwardPlan
from us_equity_strategies.research.fractional_kelly_walk_forward_v2 import (
    evaluate_fractional_kelly_walk_forward,
)
# 组合入口：portfolio_kelly_walk_forward_v2.evaluate_portfolio_kelly_walk_forward

# plan、人工收益、现金、成本由合成调用方显式提供。
result = evaluate_fractional_kelly_walk_forward(
    strategy_returns=synthetic_strategy_returns,
    benchmark_returns=synthetic_benchmark_returns,
    cash_returns=synthetic_cash_returns,
    plan=plan,
    cost_bps_per_side=synthetic_cost_bps,
)
assert result["evaluator_version"] == "fractional_kelly_walk_forward_v2"
```

两入口复用 v1 的拟合、路径、成本、统计与报告 helper，仅将评估编排版本化。
所有策略、资产、现金、基准、R8 和 turnover 流都按 folds 的实际测试索引取数，
schedule 与输入一一对应，不把测试块间的 embargo 样本纳入统计。
这是原有虚拟资产费用模型上的测试会话拼接，沿用原来的持仓漂移与折间调仓口径；
没有模拟被排除会话的逐日净账，也不能解释为连续持有的真实投资路径。

复用 R-DATA-01B 的 `WalkForwardPlan`：半开 `[start,end)`，按调用方提供的
session 顺序计数，`purge_sessions >= label_horizon_sessions >= 1`。
最后一个训练标签结束位置严格早于验证起点。embargo 是相邻测试/验证块之间的
间隔，内层训练/验证限制在外层训练窗内；后续折可以训练于已结束的过去窗口。
评估器只拟合外层训练窗，不新增内层选参算法。日期必须唯一递增且为 `date`，
不接受 `datetime`；输入日历完整性和官方日历版本仍由调用方负责。

结果记录 v2 版本号、label horizon、embargo、实际测试样本数及块间排除数；
诊断摘要 `trial_log_sha256` 同时绑定版本号和 CV 参数，不登记或启动 trial。
可选 `BacktestReportV1` 保持原 schema，在 limitations 标明版本及拼接语义。

## NEED_USER：真实数据协议尚未注册

v2 没有真实研究协议或新冻结参数，不能继承 v1 的 `RealDataAuthorization`、
`PortfolioRealDataAuthorization` 或 M2 的身份与许可。
`synthetic=False` 或提供 `real_data` 时抛出
`NEED_USER_V2_REAL_DATA_PROTOCOL_NOT_REGISTERED`；组合入口也拒绝
`netting_candidate`（`V2_NETTING_NOT_SUPPORTED`）。

若后续需要真实研究，须先明确并冻结新版本、候选/控制、数据与许可、日历及
可用时点、train/test/purge/horizon/embargo、成本与停止规则，再授权对应接线。
本次测试中的 horizon=3、embargo=2 等数值只属于人工反例，不是实际协议参数。
结果一律为 development、`live_ready=False`、`no_order=True`、`budget_effect=NONE`。
