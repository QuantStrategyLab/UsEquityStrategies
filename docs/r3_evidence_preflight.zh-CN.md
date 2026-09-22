# R3 私有证据预检（历史入口，已退出 Batch A 活动路径）

> **状态（§9.15.27）**：原 R3 数据架构不再延续到新 Batch A。`run_r3_from_gcs.py` / `run_r3_joint_evidence.py` / `--private-root` 仅保留历史证据与 Git 历史；新活动入口为 `docs/research/batch_a_v2_contracts.zh-CN.md` 与 `scripts/run_batch_a_from_gcs.py`。不提供 v1→v2 转换或双版本 dispatcher。

SOXL 的 R3 回测使用锁定的私有输入数据；数据内容、字节数、合同、工作提示词和研究源码均有固定 SHA-256。原始脚本的默认路径是历史工作机路径，不能把该路径当成运行环境要求。

正式存储使用私有 GCS 对象前缀；不要把对象存储直接挂载成普通文件系统。历史流程在获准的云端临时环境用 `run_r3_from_gcs.py` 精确复制五个锁定输入对象，再执行现有只读预检：

```bash
PYTHONPATH=src python scripts/run_r3_from_gcs.py \
  --gcs-input-prefix gs://qsl-research-evidence-831478360303/research/v1/input/r3 \
  --contract-uri gs://qsl-research-evidence-831478360303/research/v1/input/r3/acceptance-contract.md \
  --worker-prompt-uri gs://qsl-research-evidence-831478360303/research/v1/input/r3/worker-prompt.md
```

本命令默认只做 staging 和 preflight，不运行回测、不上传结果。它不列举 bucket、不覆盖对象、不删除对象；目标临时目录必须为空，复制使用 `--no-clobber`。合同和 worker prompt 也必须是获准的、哈希匹配的对象，不能由脚本现场生成。

如果已经在云端临时环境准备好 staging 目录，也可以直接执行底层只读预检：

```bash
PYTHONPATH=src python scripts/run_r3_joint_evidence.py \
  --preflight \
  --private-root /tmp/private_research \
  --contract-path /tmp/private_research/acceptance-contract.md \
  --worker-prompt-path /tmp/private_research/worker-prompt.md
```

预检只输出 `ready`、已提交的 source revision 和稳定错误码；不输出私有路径、不读取交易账户、不运行回测，也不写证据 bundle。`ready: false` 时不得重载任何纸面平台。

只有预检通过后，才可显式传入 `--output-root` 运行 R3。R3 输出仍是研究证据，不是策略发布身份；后续还必须通过策略证据包、发布身份、全平台纸面回执和观察期门禁。
