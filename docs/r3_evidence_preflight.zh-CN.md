# R3 私有证据预检

SOXL 的 R3 回测使用锁定的私有输入数据；数据内容、字节数、合同、工作提示词和研究源码均有固定 SHA-256。原始脚本的默认路径是历史工作机路径，不能把该路径当成运行环境要求。

在已授权的数据卷挂载后，先执行只读预检：

```bash
PYTHONPATH=src python scripts/run_r3_joint_evidence.py \
  --preflight \
  --private-root /mounted/private_research \
  --contract-path /mounted/acceptance-contract.md \
  --worker-prompt-path /mounted/worker-prompt.md
```

预检只输出 `ready`、已提交的 source revision 和稳定错误码；不输出私有路径、不读取交易账户、不运行回测，也不写证据 bundle。`ready: false` 时不得重载任何纸面平台。

只有预检通过后，才可显式传入 `--output-root` 运行 R3。R3 输出仍是研究证据，不是策略发布身份；后续还必须通过策略证据包、发布身份、全平台纸面回执和观察期门禁。
