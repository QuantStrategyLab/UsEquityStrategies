# Grok Bot 审阅记录（已复核：PASS_SCOPED）

## R-RISK-03B 状态复核增补（2026-10-23 SGT）

记录版本：`r-risk-03b.review.v1`。本次是既有证据与消费者绑定缺口的文档复核，未重新调用 Grok Bot；原 Grok 复核时间仍为 2026-10-22 21:47（UTC+8），原结论仍为 `PASS_SCOPED（设计/接口）`。

- 核对基线：UES `c4279eef09c660dca6774a7a61a0fe6d7816a7a9`；GitHub 只读回查 PR #661 / #662 均已合并，merge 分别为 `a72ada054a7668a2342caa2e4a1802bb94028f21` / `3aa004b6b752430cab1cccb8fed234a58a1c5860`，均包含于本基线。原 R-RISK-03 执行报告“等待 CI/未合并”是当时快照，不是当前状态。R-RISK-01 设计由 #661 带入；不把原 #658 草稿称为已合并。
- schema：`r-risk-01.research.v2`；状态精确值 `RESEARCH_SCHEMA_APPROVED_NOT_ARMED`。参数定义字节 SHA256 仍为 `703550b4a2b7371ab7343afc201d0747831a98ce80be6a8525d3c8cd72d387a1`。保守 `conservative` c=0.10、均衡 `balanced` c=0.25、进取 `growth` c=0.40；旧文案“稳健”仍对应同一 `balanced` id。默认值、硬区间、覆盖和确认规则均未修改。
- 本次结论：复核记录及三档绑定要求已整理，**三档尚未验证**。六个原设计 cell 仍 `NOT_RUN_BLOCKED`，金融指标 null，`financial_qualification=NOT_READY`、`live_ready=false`、`execution_authorized=false`。本次新增 trial/回测/shadow 数为 0，未写权威 PerformanceStore。
- 消费者：`b4_fractional_target` 接受三个离散 c；R1/M1 两处调用仍固定 .25。尚无本 schema 到三档全链路的已验收消费者。复用 [README §3](README.zh-CN.md#3-复用接口与调用边界) 中既有 B4、compose/netter、静态预算和两会话 CVaR，逐档绑定，不能缩放已封顶 .25 目标代替重算。
- 联合 CVaR：内核已有 `DecisionBook / Action / ScenarioSet / TailPolicy / CostSpec / HardPolicy`，但现有 RS04 接线明确只接受 synthetic，且只支持 `DECISION_TIME_TARGET`，拒绝原政策的 fill/post-fee basis；不能从内核支持 `POST_FEE` 推断消费链已支持。联合情景生产者、相关性/配对、训练窗口、许可/PIT、真实政策来源、最终动作与费用后现金/上限仍未绑定。
- G06 中 Grok 待审这一子项已由原 R-RISK-03 完成；账户预算身份/来源仍未绑定。README、VALIDATION、comparison-gaps 和 verification 中的旧待审文字保留为历史快照；当前复核状态以本记录为准，不改写旧检查产物。

本次未覆盖：非 c 参数金融有效性、三档消费者实现/合成运行、真实来源及许可认证、账户与预算读取、联合情景估计或尾部覆盖验证、收益/DSR、P4/网站确认实现、产品 pin/部署/实盘采用。日监测的 252 会话窗不等于两会话情景训练窗；后者尚未冻结，不在本次补定。M2 的 R3/R0 冻结协议、源码与哈希不变，也不扩成三档研究协议。

详细 C01–C08 消费者、D01–D08 数据和 J01–J08 联合 CVaR 绑定要求、六个 cell 状态及后继卡建议见本次 R-RISK-03B 交付报告与证据目录。这些编号仅用于文档交叉引用，不是新 trial 或派工卡。

## 原 R-RISK-03 复核记录（保留）

本目录包含 R-RISK-01 设计与 R-RISK-02 参数域扩展。用户于 2026-10-22 21:07 批准 c=.10/.25/.40 与 schema；当前版本为 `r-risk-01.research.v2`。Grok Bot 已于 **2026-10-22 21:47（Asia/Shanghai，UTC+8）** 完成复核，结论为 **PASS_SCOPED（设计/接口）**。本次 R-RISK-03 仅记录既有复核回执，不修改 schema 数值、研究内核、消费者、产品、执行或部署。

## 复核证据与范围

- Grok Bot 报告：`<codex-job-root>/out/R-RISK-SCHEMA-GROK-REVIEW.md`（即 `~/codex-jobs/out/R-RISK-SCHEMA-GROK-REVIEW.md`）。
- 复核版本：[PR #661](https://github.com/QuantStrategyLab/UsEquityStrategies/pull/661)，merge `a72ada05`（完整 SHA：`a72ada054a7668a2342caa2e4a1802bb94028f21`）。
- `parameter_definition_sha256`：`703550b4a2b7371ab7343afc201d0747831a98ce80be6a8525d3c8cd72d387a1`。
- 报告引用的技术证据：`<codex-job-root>/out/R-RISK-02.md`、`<codex-job-root>/out/R-RISK-02-independent-review.md` 的技术 PASS，以及本目录 `verify_design.py` 的 `PASS_SCOPED_DESIGN_AND_INTERFACE_CHECKS`。技术检查与 Grok Bot 复核均不等于金融验证。

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
