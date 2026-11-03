# UX1 异步研究预览作业

本入口是一条手动研究作业，复用已安装的 `ux1-preview`。它不修改 15 个计算文件，不读取本机旧输入，也不把 Batch A 的 CSV 当作 UX1 输入。作业没有交易权限：计算器结果仍须保持 `no_order=true` 与 `execution_authority_granted=false`。这里的离线 synthetic 测试不能代替云端输入定位或作业采用。

## Workflow

文件 `.github/workflows/ux1-research-preview.yml` 只接受 `workflow_dispatch`。唯一 input 是 `ux1_request_id`。run-name 为 `UX1 preview <UUID>`。作业只在仓库默认分支运行；QRS 调度 ref 为 `main`。权限只有 `contents: read` 和 `id-token: write`。`concurrency.group` 固定为 `ux1-research-preview`，`cancel-in-progress` 为 false。超时 10 分钟。不上传 artifact。

WIF 与 Cloud SDK 使用既有 action pin。只有 `UX1_INPUT_BUCKET` 非空时才做这两步。本批不写入具体云位置。

安装使用已验证的 Python 3.13。`uv sync --python 3.13 --frozen --extra research --no-editable --no-dev` 只消费仓库里的 `uv.lock`，不更新 lock，也不改生产依赖。入口由这次 frozen 环境运行：`uv run --frozen --no-sync --python 3.13 python scripts/run_ux1_async_job.py`。不发布镜像，不安装交易客户端。

`GITHUB_RUN_ATTEMPT` 不是 `1` 时，shell 与 Python 都直接拒绝，不 claim、不下载、不计算。

## 回调与计算

`UX1_CONSOLE_URL` 必须是 HTTPS origin，来自操作变量，不来自 workflow input。Bearer 使用 `UX1_RESEARCH_JOB_TOKEN`，只请求两个路径：`POST /api/ux1/jobs/claim` 与 `POST /api/ux1/jobs/result`。每个请求的 30 秒是连接和响应体的总期限，不是每段 socket 空闲时间。正文不超过 80KiB。3xx 直接拒绝，不读取 body，也不跟随，因此不会把 Bearer 送到重定向目标。网络调用零自动重试；结果不明时不换身份再打一次。

claim 正文只有 `request_id`、`run_id`、`run_attempt`。后两项来自 `GITHUB_RUN_ID` 与 `GITHUB_RUN_ATTEMPT`，且必须是正整数字符串。`claimed` 不是 true 时不下载、不计算、不提交第二次执行。已 claim 之后才会处理输入或计算器失败，并用一次 result 收口。result 正文只有 `request_id`、`run_id`、`run_attempt`、`result`、`error_code`。成功必须带有效 result，且 `error_code` 为 null。失败必须 `result` 为 null，且 `error_code` 只能是 `input_unavailable` 或 `calculator_failed`。两者都空、result 与失败码并存，以及其他非法组合，在终态写入或改写成另一种终态之前拒绝。相同终态不在本进程内重放；失败后不自动再 POST。

claim 成功后，request 经 stdin 交给 `ux1-preview`，argv 只有该可执行文件，无 shell。stdout 按块读取，超过 65536 字节立即终止进程，不等待 EOF。stderr 只丢弃，不累积。总期限仍是 30 秒。子进程环境只有 `LANG`、`LC_ALL`、固定 `PATH`（`/usr/bin:/bin`）、`PYTHONNOUSERSITE`、`PYTHONDONTWRITEBYTECODE`，以及 `UX1_RAW_ROOT`、`UX1_R6_ROOT`、`UX1_MATERIALIZED`。不传入 callback、token、WIF、Google 或 GitHub 凭据。stdout/stderr 只保留脱敏类别，不打印 request、响应正文或底层错误。

最终 SHA 与研究身份仍由既有 `ux1-preview` loader 校验。本作业不新增输入 schema 或快照 registry。

## 输入

四个值都来自仓库变量，缺任何一个都不下载：

- `UX1_RAW_GCS_PREFIX`
- `UX1_R6_GCS_PREFIX`
- `UX1_MATERIALIZED_GCS_URI`
- `UX1_INPUT_BUCKET`

三个云位置变量保持为空，本批不写入 bucket 或前缀。未配置、对象缺失或被拒绝时，claim 之后回报 `input_unavailable`。只读看到的候选不能标成 ready。raw 候选 `qqqm-boxx-raw-20260925-001` 有 manifest 和 12 个 bars/actions 页，仍缺必需的 policy/contract 文件。materialized 云对象未发现。

允许下载的对象只有这一组：raw 的 `manifest.json`、`tqqq_qqq_guard_cash_contract.v1.json`、`boxx_outer_cash_policy.v1.json`、`s4_budget_policy.v1.json`，以及 QQQ、TQQQ、SOXL、SOXX、BOXX、QQQM 的 `bars`/`actions` `page-001.json`。BOXX 政策的 SHA-256 必须等于现有 loader 中的冻结摘要 `cfed32767cb367ccce7ef880c3c15f82d50b99fe805d542e85839fc67270c905`，之后才接受该政策 `source_objects` 里的安全相对路径。R6 固定 case 的云对象是前缀下的 `complete.json`，以及 `p1/manifest.json`、`p1/binding.json`、`p1/closes.json`、`p1/assurance.json`。staging 把它们放到既有 loader 读取的临时文件：`complete.json`、`manifest.json`、`binding.json`、`closes.json`、`assurance.json`。只读核对到的候选根是 `research/v2/input/soxl-v7-td-single-source-20260926-001/`，该路径没有写入仓库变量。materialized 是单个对象，不是目录。

调用方式复用现有 `gcloud storage`：先 `objects describe`，核对 bucket、对象名、大小与 generation，再用 `cp --no-clobber` 下载带该 generation 的 URL。每次 describe 和 cp 都带上 staging 剩余总期限；超时停止，不重试，输出不进入结果，并脱敏为 `input_unavailable`。拒绝跨 bucket、目录穿越、符号链接、通配和超限。单对象上限 16MiB，总字节 64MiB，对象数 40，staging 截止 120 秒。首个错误停止。
