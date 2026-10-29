# 历史三腿组合收益回放（仅探索）

`us_equity_combo_backtest_20260628.json` 是 2026-06-28 留下的派生收益研究产物，不是可晋级的市场数据证据：当前仓库没有它对应的不可变原始价格输入、时点成分股记录或交易成本模型。

`scripts/run_legacy_combo_derived_returns_replay.py` 只接受该文件 SHA-256 为 `9382b6d371de7c96c0fb508434007228f52d36b94e52c5eb5c044bae87eb5c4b` 的原始字节，固定比较六组预先登记的权重，并把 2015–2021 与 2022–2026 分开报告。它不会挑选赢家，也不会产生 P3、晋级、paper、shadow 或 live 权限。

可以只读打印报告：

```bash
uv run --locked python scripts/run_legacy_combo_derived_returns_replay.py
```

如需保存研究报告，输出路径是 create-only：已有不同内容会拒绝覆盖。

```bash
uv run --locked python scripts/run_legacy_combo_derived_returns_replay.py --output /tmp/legacy-combo-report.json
```

这项工作的正确用途是探索“值得重新用合规、不可变 P1 数据重建的组合假设”，而不是让旧回测直接驱动策略变更。新组合候选仍须独立拥有 P1 输入绑定、P2 不变配置、P3 回放证据，之后才可讨论 P4/P5。
