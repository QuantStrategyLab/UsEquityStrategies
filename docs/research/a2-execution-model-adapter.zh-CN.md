# A2 研究成交接口接入（第一波）

状态：依赖尚未准入，提交应作为 draft PR（integration pending）。
本增量不是审计 A2 全部完成的声明。

新增 `research.execution_model_adapter` 是主动调用的研究接口。它接受单证券及
零收益现金的完成收盘目标，比较 `same_close`、`next_close`、`next_open`。
现有 Kelly 生成器、研究 runner、策略与生产入口均保持原默认行为。

依赖 QPK 的 `research_stats.execution_models` 新能力。模块加载不要求该能力；
调用时缺失会明确报 `QPK_EXECUTION_MODELS_REQUIRED`，不会退回旧时点。
本提交不调整 QPK pin。QPK 接口通过审查后，另行按原准入流程采用准确 SHA；
不能仅凭本地 sibling 源码验证声称正式依赖已接入。

返回结果包含 `model_version`、`timing`、`price_basis`、`cost_accounting`、成本参数、
实际成交日/价/换手及区间外未成交信号，状态始终为 `DEVELOPMENT_ONLY`。
未强制期末清仓。最后收盘发生的真实成交成本会计入该日，最后信号需要下日成交
但下日缺失时只记未成交。`same_close` 明确为收盘完成后信号使用同一收盘价的假设。

调用方只可提供合成或一致公司行动调整后的 open/close 序列；原始未复权 OHLC、
分红/拆股现金记账、多证券漂移与真实策略回放仍需单独适配。预先提供的目标必须
已按其决策日冻结；此接口不负责信号因果性证明，也不推断数据许可证。

成本按每次成交前 NAV 的证券换手计算，入场与退出都扣除。开盘成交先扣成本再
计算日内收益；收盘成交先获得原持仓当日收益再扣成本。与现有研究 evaluator 一样，
费用只作用于净 NAV，不反馈至独立毛收益权重路径。

`legacy_turnover` 仅用于差分：旧 next_close 引擎将收盘换手记到下个收益 session，
这会遗漏区间最后收盘的费用；新 `fill_turnover` 是实际成交日换手，不能两者同时扣费。
在零费用时，固定种子的 40 日目标路径与旧 `_replay` 的毛收益和延迟换手位级一致。
平价三日先买后卖的例子：新换手 `[0,1,1]`，旧换手 `[0,0,1]`，1% 每边成本
使新净收益为 `[0,-0.01,-0.01]`。差异是收费时点，不是新策略表现结论。

联合离线验证需要安装含新接口的准确 QPK 来源，然后执行：

```bash
UES_REQUIRE_QPK_EXECUTION_MODELS=1 python -m unittest discover -s tests/research -p 'test_execution_model_adapter.py' -v
```

现有 pin 不具备新能力时，六项集成用例显式跳过；缺失依赖的拒绝用例照常运行。
强制门设置为 `1` 时不会跳过。测试用固定种子和合成 OHLC，覆盖三时点、成本、
漂移、终端未成交、未来扰动、输入拒绝与旧结果差分。
这里只建立测量接口，没有注册新策略候选或运行真实收益实验；B1/B2 的预注册、
累计 trial ledger 与统一指标验收仍需后续任务处理。未取得 UES #575 的可读证据，
不能把本接口视为该 PR 争议已解决。
