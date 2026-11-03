# 历史组合 P2 候选契约

`qsl.us-equity-historical-combo-p2-candidate.v1` 是历史多策略组合进入
P2 前的**冻结描述符**。它把下列内容绑定到一个自校验 SHA-256：

- 已有 P1 输入绑定的 SHA-256；
- 组合候选本身，以及每个策略袖子的代码 revision、配置哈希和目标权重；
- 明确分离的选择期与随后 holdout 期；以及
- `portfolio_risk_budget` 策略的独立哈希。

它拒绝重叠的选择/holdout 窗口、权重不为 100%、重复或无序的袖子、
不固定的 revision、以及任何自摘要篡改。这样，未来的 P3 回放不能在看过
holdout 后悄悄改权重、策略版本或风险预算。

这只是控制面元数据，不读取 P1 原始行情或成分股，不计算收益或挑选优胜者，
不写入候选注册表，也不改变任何现有策略。它固定写入
`p4_paper_authorized=false`、`p5_shadow_authorized=false` 和
`p6_live_authorized=false`；P3 证据、P4 paper、P5 shadow 与 P6 live 仍是
各自独立的后续阶段。

当前没有已注册的组合候选。旧的三腿派生收益回放只能帮助提出假设；只有新的
不可变 P1 数据输入与本契约同时满足后，未来驱动器才可以开始可复现回放。
