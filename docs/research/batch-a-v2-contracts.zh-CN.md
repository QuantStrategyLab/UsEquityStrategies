# Batch A 研究数据 v2 合同（冻结）

来源：审计 §9.15.27 D0/D2。本文件冻结**破坏性、不兼容**的新研究输入合同；旧 R3/`price_snapshot.v1`/`c3-batch-a-frozen-member-pack.v1` 仅保留历史证据与 Git 历史，**不进入新 Batch A 活动入口**，不提供 adapter、alias、双版本 dispatcher 或自动转换。

## 范围与边界

| 项 | 约定 |
|---|---|
| 正式存储 | 既有私有桶；新对象前缀 `research/v2/input/<dataset_id>/` 与对应 `output`/`quarantine` |
| 对象写入 | create-only；manifest 最后锁定；绑定 GCS generation、字节数、SHA-256 |
| 新入口 | `batch_a_dataset` → `batch_a_member_pack` → Batch A validator/CLI；`run_batch_a_from_gcs` 仅做精确 staging + 本地物化 |
| 拒绝 | 任意 `*.v1` schema、v1→v2 自动转换、Yahoo/R3 五文件布局、`--private-root` 作为 Batch A 前置 |
| 现金腿 | 仅 `ASSUMED_ZERO_USD_CASH`；不得把 BOXX 当作无风险现金 |
| 成员收益 | 必须是冻结策略+成本模型产出的**完整策略日收益**；禁止用标的涨跌幅冒充 |
| 权限 | `research_only=true`；`execution_authorized=false`；`no_order=true`；不授予 paper/live/晋级 |

旧 `research/v1`、`run_r3_from_gcs`、`run_r3_joint_evidence` 与 R3 源码保留，但已退出 Batch A 活动入口。共享 offline v1 类型 / Alpaca adapter 仍有其他实际 consumer（SOXL RSI2、typed baseline 等），本批**不删除**。

## `qsl.research.price_snapshot.v2`

每个 dataset 目录最少包含：

- `prices.csv`：UTF-8 LF；表头固定 `symbol,as_of,open,high,low,close,volume`；按 `(as_of,symbol)` 升序；同 symbol 日期唯一；OHLC 有限且为正、volume 非负；同 dataset 内各 symbol 日期集合必须完全一致。
- `prices.csv.manifest.json`：字段集合固定如下（多一字/少一字均无效）。

| 字段 | 要求 |
|---|---|
| `schema` | 必须为 `qsl.research.price_snapshot.v2`（拒绝 v1） |
| `research_only` | `true` |
| `dataset_id` | 非空标识；与对象路径 `research/v2/input/<dataset_id>/` 一致 |
| `provider` / `feed` / `price_field` / `adjustment` | 显式字符串；Batch A 首选 `alpaca` + `sip` + `adjusted_close` + `all` |
| `calendar` / `timezone` | 显式字符串（如 `XNYS` / `America/New_York`） |
| `license_retention` | 非空许可/保留条件摘要 |
| `code_version` | 非空采集/物化代码版本 |
| `source_revision` / `retrieved_at` | 非空 |
| `symbols` | 非空唯一列表，与 CSV 实际 symbol 集合一致 |
| `request.start` / `request.end_exclusive` | ISO 日期；`start < end_exclusive`；所有行落在半开区间内 |
| `gcs.bucket` / `gcs.object` / `gcs.generation` / `gcs.bytes` / `gcs.sha256` | 对象身份；`sha256` 为 64 位小写 hex；`bytes` 正整数；`generation` 非空十进制字符串 |
| `counts` / `coverage` | 按 symbol；coverage 为 `{start,end}` 且等于该 symbol 首末日 |

加载时必须同时验证：

1. 文件字节数与 `gcs.bytes`、内容 SHA-256 与 `gcs.sha256` 一致；
2. 声明的 `gcs.object` 相对根路径与实际 staging 相对路径一致（根错配拒绝）；
3. 若提供 `object_identity.json`（含 `generation`），必须与 `gcs.generation` 一致（generation 错配拒绝）；
4. 日期不完整（缺 symbol、日期不对齐、区间内缺日若日历声明要求完整、空序列）拒绝。

## `qsl.c3-batch-a-frozen-member-pack.v2`

| 字段 | 要求 |
|---|---|
| `schema_version` | `qsl.c3-batch-a-frozen-member-pack.v2`（拒绝 v1） |
| `research_only` | `true` |
| `execution_authorized` | `false` |
| `cash_return_policy` | 仅 `ASSUMED_ZERO_USD_CASH` |
| `members` | 恰好且唯一：`cash_sleeve`、`soxl_core`、`tqqq_core`（拒绝重复/缺失/未知 id） |
| 每成员 | `member_id`、`evidence_digest`、`input_digest`、对齐 `dates`/`returns`，以及 C2 六项可比字段：`as_of`、`quote_currency`、`capital_basis_digest`、`cost_model_digest`、`risk_policy_digest`、`data_scope_digest` |
| `cash_sleeve.returns` | 全为 0；否则拒绝 |
| `soxl_core` / `tqqq_core` | 收益必须来自冻结策略路径（typed SMA200 基线 + 声明成本模型），不得写入标的 buy-and-hold 涨跌幅 |
| `pack_digest` | 对**去掉** `pack_digest` 后的整包做 canonical JSON SHA-256；不一致即拒绝 |

Batch A 消费端只接受本 schema；缺包或校验失败保持 `PARKED`，不发明收益。

## 活动入口

1. `us_equity_strategies.research.batch_a_dataset`：加载/校验 v2 snapshot。
2. `us_equity_strategies.research.batch_a_member_pack`：由已校验 dataset 物化 v2 成员包并核对 `pack_digest`。
3. `scripts/run_batch_a_from_gcs.py`：精确复制 v2 对象到空临时目录后本地物化；默认不访问券商/生产；测试不得真实打 GCS。
4. `evaluate_batch_a_existing_member_baselines` / `run_c3_batch_a_existing_member_baseline.py`：只消费 v2 包。

本批不做：真实采集、真实 GCS 读写、购买数据、改云资源/QPK/平台/账户/交易。
