# 合成 RESEARCH 离线 packet 校验

`research.risk_preset_contract.validate_packet(packet, *, fixture_manifest,
evidence_table, fixture_clock)` 只检查调用者传入的普通 JSON 值，返回
`{"valid": bool, "issues": [{"path": ..., "code": ...}]}`。失败时返回第一个确定的问题，
不回显输入内容、不修改输入。只接受内置 dict/list/str/int/float/bool/null；拒绝自定义对象、
非字符串键、循环、超过 64 层的容器，以及非有限或溢出浮点表示范围的数值。

这是测试用途的纯校验器。没有消费者、配置应用、风险计算、交易、清仓或收益结论。
F1–F7 中的数字、共同 c、窗口、时效、修订标识和证据都是本次授权下显式选择的合成测试输入，
不是推荐风险参数、经济默认值或实际运行来源。B4 Bayesian 继续延期。

## 冻结输入

`tests/research/fixtures/risk_preset_offline/` 保存 release7 candidate、F0 和 sidecar 的原字节，
以及独立的 `DESIGN_FREEZE_RECORD.json`。原文件的旧状态标记保持原样；后续冻结和
F1–F7 授权由独立记录解释。测试核对三个约定的 SHA256，不读取原任务目录。

packet 固定为 candidate 的 17 个字段。scope 必须精确匹配 manifest 中的合成 RESEARCH
条目；真实身份键、未知结构字段、旧顶层 status 别名和执行权限都被拒绝。
`missing_parameters` 只接受冻结的九个逻辑枚举。没有选择或没有完整定义引用时，
使用 NOT_CONFIGURED 和相应缺项；不构造版本或 hash。

## 本实现支持的明确 wire 形式

以下是测试所需的局部表示选择，不增加经济规则或通用 schema 系统。未列出的替代形状返回 issues。

- definition 固定字段：`config_version,preset_id,scope,model_binding,input_semantics,
  constraints,window,qualification_rules,content_sha256`。只有完整定义进入 definitions；
  不完整选择用 `requested={preset_id,definition_ref:null}`。
- 引用为 `{preset_id,definition_ref:{config_version,content_sha256}}`，同时要求 packet 本地唯一
  定义和 manifest 精确许可。definition 摘要只排除自身 `content_sha256`，不接收显示或读回字段。
  JSON 使用 `sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False`，编码 UTF-8，
  数组顺序保留。证据记录没有自身摘要字段。摘要表示完整性，许可只来自显式传入的清单。
- `model_binding` 固定为 `method,fraction,preregistration_ref,ues_revision,qpk_revision,evaluator_version`，
  必须精确匹配 manifest 的 `preregistrations[preregistration_ref]`。共同 fraction 在 `(0,0.5]`；
  所有定义使用相同 c。不把 main/latest/HEAD 当作实际修订身份。
- `input_semantics` 固定为 `input_sha256,currency,asof,baseline_ref,return_semantics,cash_semantics`。
  本地测试形式只支持 `SYNTHETIC_SIMPLE_RETURNS` 与 `SYNTHETIC_CASH_SECURITY`。
  不解释收益序列、NAV 或现金证券，不执行 FX；`capital_basis.fx_ref` 必须 null。
- constraints 使用冻结表的八个键；`cash_symbol` 是已许可资产字符串。其他键是 LimitItem，
  两种逐项 cap 使用已许可资产或底层键的 map。空 map 是显式无逐项项，不补造 cap。
  LimitItem 固定为 `mode,value,unit,denominator,source_ref`。单位和数值范围严格遵循冻结表。
  只有 turnover 允许 DISABLED/null；UNSPECIFIED/null 被识别为未完成配置，不能进入完整定义。
- denominator 固定为 `{kind:"SYNTHETIC_NAV",fixture_id,input_sha256,asof,baseline_ref}`，精确绑定
  对应定义的 scope/input。`source_ref` 是本地 `{preset_id,config_version}`，或已许可的
  EFFECTIVE_OBSERVATION 证据键。前者通过完整定义的摘要核验，避免自摘要循环；后者必须绑定同一定义/input。
- NAV、risk_scalar、权重和 metrics 是冻结表规定的裸有限数字或数字 map，不使用 LimitItem。
  bool 不能充当数字，未知 null 不变成零；合法零原样保留，权重不归一化。
  metrics 固定为 `nominal_weight,effective_risk_exposure,underlying_effective_exposure,one_way_risk_turnover`。

manifest 沿用 F0 的固定根字段与每个 fixture 的五个字段。非空测试条目可显式增加
`underlying_ids,preset_ids,explanations,preregistrations`；它们分别提供许可集合、固定显示文本和
模型绑定。F0 使用原冻结文本。证据键为不含路径分隔符的不透明标识，不进行路径或 URL 解析。
`allowed_definition_refs` 是上述完整引用的数组；`allowed_evidence_key_digests` 是独立提供的键/摘要 map。

REQUEST_STATE 记录固定为 `{type,scope,requested,request_status}`，绑定请求和独立处理时间。
EFFECTIVE_OBSERVATION 记录固定为 `{type,qualification,effective,scope,source,timing,
capital_basis,upstream,risk_assessment,final_constraints}`；qualification 只接受
QUALIFIED/UNKNOWN/UNQUALIFIED。source 的 mode 固定为 SYNTHETIC_RESEARCH，其修订/input
与 effective 定义一致。所有观察字段必须与同一记录逐字典规范化绑定；不会把 True 当成 1。
传入的历史记录也检查类型、摘要、引用、时间和内部绑定，不把它当当前 effective。

## 请求、观察和时间

完整请求没有回执时必须 UNKNOWN/READBACK_MISSING；已处理状态需要精确 REQUEST_STATE。
APPLIED 还要求 requested 与 effective 相同。新请求 UNKNOWN、REJECTED、NOT_APPLIED 或
NOT_CONFIGURED 可保留独立有证据的旧 effective；唯一一组观察字段始终属于旧 effective。
没有 effective 时观察字段 null、upstream 空、timing 全 null、freshness=MISSING。

时间只接受显式 UTC ISO 秒时间（可有 1–6 位小数，Z 或 +00:00）。window 固定为
`effective_from,expires_at`；qualification_rules 固定为显式的非负 `max_age_seconds`
和 `required_qualification:"QUALIFIED"`。有定义或处理/观察时间谓词时必须传 fixture_clock。
没有时间谓词才允许 null；不会读取当前时间。

支持的观察谓词为：窗口起点 ≤ asof ≤ available_at ≤ observed_at ≤ fixture_clock，
observed_at < valid_until ≤ 策略窗口终点；当前 effective 还要求 clock < 策略窗口终点。
capital/input 的 asof 必须精确绑定。QUALIFIED 且 age ≤ 显式阈值、clock < valid_until 时 FRESH；
超过观察阈值或 valid_until 为 STALE，未知/未通过资格为 UNKNOWN。策略本身到期不能仍为当前 effective，
其完整历史证据可以保留。请求窗口只检查顺序，不拿新请求窗口覆盖旧观察窗口。

effective 必须有完整观察证据、明确资本基础和 COMPLETE 最终限制。最终限制绑定 effective/scope/input，
不得放松已声明 cap 或现金下限；可增加已许可的更严格项。校验器不计算约束交集或预算。
APPROVE 需要已知的 scalar、目标、metrics 与 QUALIFIED 输入。APPLIED+STALE 不表示准备就绪。
upstream 的 stage+subject 唯一，原始状态/原因逐项保留；外层 COMPUTED/SUCCEEDED 不消除内部 PARKED
或未知状态。任何校验通过都不推导交易、清仓或提升风险的权限。

F0–F7 测试依次覆盖：未配置、选择未完成、结构/范围/权限、数值与单位、无回执完整请求、
请求与观察及时间矩阵、引用与证据及最终限制、原始阶段失败与未知状态。测试不依赖 QPK overlay。
