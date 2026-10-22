# Grok Bot 审阅记录（已复核：PASS_SCOPED）

本目录包含 R-RISK-01 设计与 R-RISK-02 参数域扩展。用户于 2026-10-22 21:07 批准 c=.10/.25/.40 与 schema；当前版本为 `r-risk-01.research.v2`。Grok Bot 已于 **2026-10-22 21:47（Asia/Shanghai，UTC+8）** 完成复核，结论为 **PASS_SCOPED（设计/接口）**。本次 R-RISK-03 仅记录既有复核回执，不修改 schema 数值、研究内核、消费者、产品、执行或部署。

## 复核证据与范围

- Grok Bot 报告：`/Users/lisiyi/codex-jobs/out/R-RISK-SCHEMA-GROK-REVIEW.md`（即 `~/codex-jobs/out/R-RISK-SCHEMA-GROK-REVIEW.md`）。
- 复核版本：[PR #661](https://github.com/QuantStrategyLab/UsEquityStrategies/pull/661)，merge `a72ada05`（完整 SHA：`a72ada054a7668a2342caa2e4a1802bb94028f21`）。
- `parameter_definition_sha256`：`703550b4a2b7371ab7343afc201d0747831a98ce80be6a8525d3c8cd72d387a1`。
- 报告引用的技术证据：`/Users/lisiyi/codex-jobs/out/R-RISK-02.md`、`/Users/lisiyi/codex-jobs/out/R-RISK-02-independent-review.md` 的技术 PASS，以及本目录 `verify_design.py` 的 `PASS_SCOPED_DESIGN_AND_INTERFACE_CHECKS`。技术检查与 Grok Bot 复核均不等于金融验证。

PASS 仅覆盖上述版本的设计与接口：**不含真实 trial、金融资格、实盘采用或执行授权，也不为其余 29 个非 c 参数的金融最优性背书**。这些参数仍是设计默认；后续调整须另批，不得根据已见收益选优。采纳结果、新增策略、加杠杆或提高风险仍须通过网站由用户确认。

三档对照保持原状态：TQQQ/SOXL × 保守/稳健/进取的 **六个 cell 全部为 `NOT_RUN_BLOCKED`**，g/Δg/MDD/日及两会话 CVaR/换手仍为 null，真实 trial 与合成收益回测执行数均为 0。`live_ready` 与 `execution_authorized` 保持 false。本次记录不更新原缺口或验证产物；其中关于 Grok 待审的文字为复核前快照，当前复核状态以本记录及上述报告为准，其余阻塞条件继续有效。

## 原审阅要点与结论

原请求覆盖本目录 README、parameters.json、三个 schema、comparison-gaps.json、r4-trial-ledger.json 和 verification.json。Grok Bot 已逐项给出结论（详见报告）：第 1 项接受为设计建议，第 2–4、6–7 项通过，第 5 项通过且缺口明示。保留原审阅要点供追溯：

1. 保守/稳健/进取的波动6/10/15%、MDD8/12/18%、日CVaR1/2/3.5%、两会话CVaR1.5/3/5%、c=.10/.25/.40及硬上限。它们是未经收益数据选优的建议，是否适合进入后续验证？
2. 当前B4只接受离散集合 .10/.25/.40，0、负数、区间内其他数、>.40、非有限、字符串与bool均拒绝。R1/M1固定.25，不能静默改档或放大已封顶结果。是否准确保留了内核复用及未来接入的边界？
3. 战略真现金20/10/5%与M1额外2/2001预留、BOXX收费、费用前后与POST_FEE限制、无跨券商净额、无新增solver是否清楚？
4. 任何一维增险/未知或混合变化均要二次确认；降级重审旧覆盖、升级48小时+新预览确认、恢复建议同样受限；高级模式不可越账户预算或熔断。
5. 熔断与最大回撤容忍的差异、5个会话恢复条件、旧高水位不重置，以及原消费者未绑定的明确缺口。
6. 三档六个cell未运行，全部金融指标null；早期48是下界，完整累计DSR不可计算；本地R4清单不是权威PerformanceStore入账。
7. P4是否能展示建议/覆盖/实际生效状态和暂不可估的金融指标，且不将JSON schema校验或设计建议当实盘授权？

## 后续条件

Grok Bot schema 复核已完成，结论为 `PASS_SCOPED`；这不授权运行真实 trial。仍须另卡绑定协议数据/PIT/许可、联合 CVaR、完整三档消费者及实际账户风险预算身份/来源和冻结基线，具备条件后再进入三档对照；绑定前六个 cell 继续 `NOT_RUN_BLOCKED`。累计 DSR 仍不可计算，早期 48 仅为下界；权威 R4 入账继续使用既有 PerformanceStore，本目录清单不能替代。前端 P4、发布与 pin 不在本次复核范围。
