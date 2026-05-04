# US equity runtime archive

_更新日期：2026-05-04_

这份索引只记录当前 `runtime_enabled` 的美股 profile 及其可复查证据。  
它不重复运行契约细节，也不把仍缺长期 summary 的 profile 伪装成已归档完成。

## 归档总览

| Profile | 归档状态 | 可复查证据 | CAGR | 最大回撤 | 备注 |
| --- | --- | --- | ---: | ---: | --- |
| `global_etf_rotation` | 已归档（阈值4复核版） | `/home/ubuntu/Projects/UsEquitySnapshotPipelines/data/output/global_etf_rotation_threshold4_2026-05-04/summary.csv`、`docs/us_equity_strategy_status.zh-CN.md`、`docs/us_equity_contract_gap_matrix.md`、`/home/ubuntu/Projects/UsEquitySnapshotPipelines/docs/operator_runbook.md` | 13.25% | -23.29% | 复核后确认阈值 4 版本可保留，优于 SPY 的回撤且 CAGR 略高于 SPY。 |
| `tqqq_growth_income` | 已归档 | `docs/us_equity_strategy_status.zh-CN.md` | 33.96% | -31.48% | 这是当前可直接复查的近似可执行证据。 |
| `soxl_soxx_trend_income` | 已归档 | `/home/ubuntu/Projects/UsEquitySnapshotPipelines/data/output/soxl_soxx_trend_income_archive_2026-05-04/summary.csv`、`docs/us_equity_value_mode_input_contract.md`、`docs/us_equity_contract_gap_matrix.md` | 98.03% | -39.29% | 100k 初始权益、5 bps 成本下的日频回放已补上长期 summary；收入层在后半段开始参与。 |
| `russell_1000_multi_factor_defensive` | 已归档 | `/home/ubuntu/Projects/UsEquitySnapshotPipelines/data/output/russell_1000_multi_factor_defensive_archive_2026-05-04/summary.csv`、`/home/ubuntu/Projects/UsEquitySnapshotPipelines/data/output/current_strategy_artifacts/russell_release_status_summary.json`、`/home/ubuntu/Projects/UsEquitySnapshotPipelines/docs/operator_runbook.md` | 16.64% | -27.62% | 已补上完整回测输出，现可与其他 runtime profile 做同口径比较。 |
| `tech_communication_pullback_enhancement` | 已归档 | `/home/ubuntu/Projects/UsEquitySnapshotPipelines/data/output/tech_communication_pullback_enhancement_archive_2026-05-04/summary.csv`、`/home/ubuntu/Projects/UsEquitySnapshotPipelines/data/output/current_strategy_artifacts/tech_release_status_summary.json`、`/home/ubuntu/Projects/UsEquitySnapshotPipelines/docs/operator_runbook.md` | 24.31% | -30.84% | 月频回放已补上长期 summary。 |
| `mega_cap_leader_rotation_top50_balanced` | 已归档 | `/home/ubuntu/Projects/UsEquitySnapshotPipelines/data/output/mega_cap_leader_rotation_top50_concentration_variants/concentration_variant_summary.csv`、`/home/ubuntu/Projects/UsEquitySnapshotPipelines/data/output/mega_cap_leader_rotation_top50_balanced_staging/release_status_summary.json` | 36.41% | -30.56% | 当前保留的无杠杆龙头轮动路线。 |

## 已删除的旧研究

这些名称不再是有效的 `STRATEGY_PROFILE`，但它们的历史结果保留为离线证据：

| 已删除 profile | 状态 | 关键结果 |
| --- | --- | --- |
| `mega_cap_leader_rotation_dynamic_top20` | 已移除 | CAGR 21.51%，最大回撤 -23.14%。 |
| `mega_cap_leader_rotation_aggressive` | 已移除 | CAGR 32.42%，最大回撤 -28.64%。 |
| `dynamic_mega_leveraged_pullback` | 已移除 | CAGR 30.96%，最大回撤 -34.80%。 |

## 归档口径

- `已归档` 表示仓库里有可直接复查的长期 summary，或者有足够完整的离线研究输出。
- `运行已就绪，性能归档待补` 表示策略契约、发布状态和运行入口已齐备，但还没有统一的长期回测摘要。
- `待补长期 summary` 表示策略逻辑已在仓库里，但还缺最终可审查的绩效归档。
- 运行契约和回测证据是两件事，不能混为一谈。
