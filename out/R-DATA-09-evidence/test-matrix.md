# 人工测试映射

全部日期/来源/范围/owner/rights 是人工 fixture 声明。prefix 限制只隔离草稿用途，不构成身份、许可或 PIT 认证。

| R-DATA-08 草案 | R-DATA-09 实测对应 |
| --- | --- |
| B01-03 | annual_registration / no_baseline |
| B04-05 | notice_changes / explicit_notice_supersession / explicit_closure |
| B06-08 | conflicting_notices / explicit_notice_supersession / newer_received_conflict |
| B09-12 | precise_other_scopes / explicit_unknown_notice_metadata / known_late_notice |
| B13 | same_source_changed_content / duplicate_identity_conflict |
| B14-15 | missing_open_or_early_close_time / precise_other_scopes |
| U01-02/U06 | u1_unknown_blocks_search_and_distance |
| U03-05 | u1_explicit_closure / u1_unrelated_later_unknown / u1_cross_year |
| U07-09 | notice_changes / conflicting_notices / later_baseline_restoration |
| T1 | test_r_data_08_calendar_t1_contract.py：16项，直接调用既有 validator |
| 准入/结构 | no_market_owner_rights / malformed_fields / duplicate / explicit query / license expiry |

未接 consumer，未声称模型/账本集成保护已验证。

独审回归：同源改字节拒绝误指旁支；晚到和未知祖先仅列 ancestry_ids，不列为 cutoff 已选证据（3项）。
