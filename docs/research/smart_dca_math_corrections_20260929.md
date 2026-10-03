# 智能定投研究计算修正（2026-09-29）

只修正 `smart_dca_research.py` 的研究模拟、候选评分和导出。生产策略文件没有改。历史研究没有重跑，也没有覆盖已有产物。

## 注资与回撤

模拟顺序是：外部注资进入现金，再按当次收盘价成交，然后用同一收盘价给资产估值。新现金出现在收盘估值里，但不承担上一收盘到这一收盘的价格变化。因此用期初现金流口径：

`收益 = 当日权益 / (上日权益 + 当日注资) - 1`

两次各 1000 的注资、价格从 100 到 50 时，资产仍是 1000 然后 1500。投资指数从 1 降到 0.75，回撤是 25%。这不是把资产回撤当成 0，也不是把注资看成收盘之后才入账的 50%。价格不变的纯注资收益为 0，不能把已有亏损的指数拉回高点。没有新的外部现金流时，指数回撤与资产回撤一致。

权益曲线继续记录原始资产。`drawdown_pct`、`max_drawdown` 和 `max_underwater_days` 改用投资指数。`asset_drawdown_pct` 保留资产曲线自身的回撤，评分不使用它。

## 成交信号

执行只使用该交易日之前最后一个已有会话的信号，不把自然日减一天后再对齐。默认 warmup 改成先留出这些历史会话，再开始模拟。固定定投仍不看信号。周、月、季的执行日规则不变。只改研究路径，不改生产策略。

## 资金加权收益

同一日的现金流先合并。合并后少于两个日期、时间跨度为零，或没有同时的正负现金流时，返回既有的不可用值 `NaN`。跨期现金流合计为零时仍是 0。场景汇总里的 `min_money_weighted_return_pct` 和 `median_money_weighted_return_pct` 在没有任何有限值时也是 `NaN`，不再写成 0，也不再使用二分法的中间值。

## 未重算的历史输出

下列已生成字段来自修正前的计算，不能再当作当前实现的结果：

- 权益曲线 `drawdown_pct`；指标里的 `max_drawdown_pct`、`max_underwater_days`
- `money_weighted_return_pct`，以及稳健性表的 `min_money_weighted_return_pct`、`median_money_weighted_return_pct`
- 由回撤差进入评分的 `max_drawdown_delta_pct_points`、`rank_score`、`passed_promotion_gate`、`failure_reasons`，以及含 drawdown 的 performance diagnosis
- 智能规则的成交 `multiplier`、`buy_value`、`regime`，以及因此变化的终值、deployment 和相对固定定投的比较

`docs/research/smart_dca_decision_summary_2026-06-19.md` 记录的三组矩阵结论属于这批历史输出，其中包括 pass rate、终值差、rank score 和 drawdown diagnosis。本次没有重跑这些矩阵。

## 复算输入与收益口径标记（2026-10-03）

CLI 现在先读取 signal/trade CSV 字节快照，再用同一快照解析数据并生成输入 SHA-256。若模拟期间任一输入路径内容变化，CLI 在写入产物前以错误码 2 停止。两个输入可以合法指向同一文件；该文件只读入一次。可选输入 manifest 的 linked CSV 校验也针对本次消费的快照。

导出 metadata 记录 `smart_dca_research_cli.py` 与 `smart_dca_research.py` 文件内容的 SHA-256，以便区分入口和计算实现；这是源码文件身份标记，不证明实际加载的 bytecode。机器绝对源码路径不写入这些来源标记。研究口径明确标注为 `before_transaction_costs`，且 `transaction_costs_modeled=false`；不模拟费用或滑点。注资先计入现金，再按所选交易价格执行和估值，导出将该同日顺序作为 metadata 记录；不假设任意交易价格列代表收盘价，也不包含日内时点模型。

新增覆盖仅使用 synthetic CSV 验证输入替换 fail-closed、共享输入文件合法性及 metadata。它不构成历史复算、真实数据质量验证或晋级证据。上文所列 June 历史输入仍未重跑，旧产物保持只读；不得将新实现标记或 synthetic 测试解释为这些结果已更新。
