R-DATA-09 为年度日历基线 + 公告更正增加纯本地 registry/selector 草稿。显式登记年度版本、来源及许可声明、生效/可知时间和 supersedes；缺覆盖、未知或冲突时返回 UNKNOWN，next-session/交易日距离在受影响日期不可用。只接受人工 fixture，市场、owner 和许可均无真实默认值。

只新增一个 research 模块及人工测试，不接既有 parser/consumer，不改生产配置、Secret、env 或依赖。MKT-01、OWN-01、LIC-01～04 仍 NEED_USER；PIT=NOT_ATTESTED，数据/PIT 主线保持开放。

验证：禁止 socket 的85项人工测试通过（含16项原T1合同测试），相关及全仓 Ruff 通过。非作者独立审查修正两项版本/审计问题后复核 PASS；报告、初审、复核及命令退出码见 out/R-DATA-09.md 和 out/R-DATA-09-evidence/。

这是结构草稿；未采集真实日历/PDF/市场数据，未验证官方日历、可信 receipt、真实许可或生产行为。仅创建 draft PR；不 auto-merge、不合并、不部署。
