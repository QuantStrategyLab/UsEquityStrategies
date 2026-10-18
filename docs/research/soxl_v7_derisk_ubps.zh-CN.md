# SOXL V7 严格降风险研究

候选 `soxl_v7_strict_derisk_only_v1`。只属于 research，不注册 runtime profile、插件或执行器。它是新的研究身份，不替换旧 V7，也不重置旧 V7 的 252 个 XNYS session 观察窗口。约 20 日只用于通过研究验收后另行草拟的执行一致性 shadow；本次未通过金融验收，因此没有生成或应用 shadow 配置。

研究采用 owner 指定的 UBPS v0.2（2026-10-15），原文 SHA256 `2bf66aa8f0f7c4c9a63824f2428cd10c16a6d436f32b2da035e1c0fe7a8d16ba`。UBPS 对合成历史、插件覆盖、费用、伪留出和证据资格的要求适用于本研究；B1/B2 合同、原 V7 窗口及人工复核保持原样。本文件不将旧审计的“仅文档”范围当作本次 owner 研究授权的限制。

## 变体与不变量

[`soxl_v7_derisk.py`](../../src/us_equity_strategies/research/soxl_v7_derisk.py) 从完整基线配置复制后，只采用 SOXL full/mid 不超过 35%/25%、RSI/Bollinger 降档与叠加、波动清杠杆且不保留、转 BOXX、至少 3% 现金。SOXX active 保持基线 20%；收入层、市场状态保护及其消费开关、期权/AI、趋势入场参数、managed symbols 与交易过滤均保留基线设置。若传入的基线已关闭收入层或市场状态保护，拒绝把它称为本候选的基线。

复用 rs04_p3 的 `rs04_b3_live.LiveRuleAdapter`，直接调用原 `build_rebalance_plan`。基线与候选各按自己的前一收盘净权益、现金和持仓计算，避免收入层阈值因财富路径不同而被遗漏。repo manifest 仅是代码参考，不是平台合并后有效 live config 的证明。

最终目标先按同输入的完整 live 基线目标逐个风险证券取较小值；实际模拟成交再按配对基线已成交持仓限制，整股取整、费用和现金对账后重新断言。每个已完成 bar 的候选风险证券占比、美元金额、风险资产名义敞口及 SOXL 3 倍穿透敞口均不得超过配对基线。未知证券、空基线、非有限值、空头或违规成交均使路径报错，不能算 PASS。超出目标的风险资金转 BOXX 或现金，不能用另一风险证券补齐。

BOXX/SGOV 在此不是半导体或股票风险敞口，分别记为现金替代证券；它们仍有市场、流动性、成本和结算风险。含现金替代 ETF 的全证券名义占比和美元金额也不超过基线，实际现金至少 3%。这项不变量不保证亏损或 MDD 更低。

共享 RS-04 `Ledger` 负责自融资、费用和整股。新增 driver 仅用于 synthetic fixture，调用时序是 t-1 信号、下一收盘模拟成交；最终名义约束使用同一时点的成交价格和基线模拟账本，不读取未来收益。约束是在每个完成成交的 bar 上验证，不能据此保证盘中、部分成交或真实 broker 敞口；成交不完整时无法保证不变量，必须报错。现有日线模拟、订单过滤与实盘成交时钟尚未证明一致，不能称执行等价，不能新增真实下单路径。未引入 B1/B2 sizing 或另一个回测编排器。

## UBPS 计算与数据边界

[`soxl_v7_derisk_history.py`](../../src/us_equity_strategies/research/soxl_v7_derisk_history.py) 实现 3 倍逐日重置、费用 ACT/365、融资 ACT/360、常数 drag 的 train-only 校准和 UBPS 8.2.2 数值门。至少 504 个训练、252 个独立验证观察，验证总体和每个完整 252 日块均检查；2020/2022 与指数/利率制度子段独立列出。输入资格、覆盖、费率/利率、NAV 与市价基差或制度验证缺失时为 INCONCLUSIVE；失败不得放宽门槛。融资 spread 额外 +100/+300 bps、内部不利 drag 2 倍为单独敏感性，不能把有利跟踪收入翻倍当压力。

真实/合成拼接按上市前 synthetic、锚点 synthetic bridge、锚点后 real 标识；不能用模型覆盖上市后缺口，不能截去灾难日或在全损后复活。该计算原语只产生 close-return 模型，不生成 OHLC、成交量、期权链、BOXX 上市前实绩或历史插件输入。SOXL 的逐时期 SOX/官方指数或 SOXX 代理、2021 年指数制度变更、历史利率和费率仍须绑定真实证据。合成校准 PASS 不等于策略 PASS。

[`soxl_v7_derisk_study.py`](../../src/us_equity_strategies/research/soxl_v7_derisk_study.py) 冻结新候选、1x/2x 外部成本、WFA、最后 24 个月伪留出、extended/real-only 独立重跑和强制 2000—2002/2008/2020/2022 压力要求。WFA 按 UBPS 新 US 研究日期模板，训练仅用过去；测试与伪留出重叠的折明确阻塞，不暗中删折。伪留出始终是较弱的已见历史证据。既有 RS-04 B3 的训练几何与前瞻 holdout 属于该候选，不被本研究修改或继承为 SOXL 资格。

插件集合从安装的 QPK default registry、QSP catalog、UES AI/期权配置和收入层取并集；保留 legacy 观察器。每个 ID 均有十项评分及 BASE/FULL/SINGLE_i/FULL_MINUS_i 要求，当前未做历史联合回放的格子保持 missing。FULL 只是待解析的兼容集合要求，不把所有名字当作支持的组合；必须先核实宿主、版本、consumer、顺序和历史可用时点。BASE 只关闭可选挂载，保留宿主强制保护，所有消融仍以冻结完整 live 基线为风险上限。不能为了跑出完整表格而给通知/no-op 新增仓位效果。自定义 registry 与平台实际加载 manifest 缺失时，coverage 仍为 INCOMPLETE；默认名单齐全不能冒充所有 live-configurable 插件已验收。

两套完整报告均要求 gross/net、CAGR、Sharpe、Sortino、Calmar、MDD/持续期、CVaR、盈利回吐、换手、敞口、现金、全部费用、置信区间、DSR/PBO、容量及分状态统计。新候选损失改善目标及允许收益牺牲预算尚未绑定，不能自行填入 B3 的收益容忍度或凭较低仓位宣布金融 PASS。

## 当前阻塞和云端入口

复用 soxl_cap2 的 names-only GCS 发现并在本任务重新查阅研究工作流、预注册和 RS-08 配置证据报告。确切 RS-04 云端副本仍未定位；允许根为 `gs://<research-evidence-bucket>/research/v2/input/`。文档唯一固定归档 manifest SHA256 为 `b5a3dd40cb0d4260fb15492108bde7ab544ac0d191807943637b8216a6808482`，明确存于 box 私有目录；本任务禁止访问 server，未访问该目录。

该归档只含 SOXL/SOXX/BOXX/QQQ/TQQQ/QQQM，2021-12-01—2026-08-25；BOXX 自 2022-12-28 开始。因此即使找到 GCS 副本，也不能覆盖 2000/2008/2020、收入证券全集、早期 BOXX、历史插件/PIT 或完整长期校准。Batch A、QQQM/BOXX、R7、R9、旧 V7 输入不能替代固定 RS-04 归档或完整研究数据。

另缺平台合并配置及原哈希口径、历史上下文、同输入 recorded-live parity、完整 raw+actions/许可/已发布时点和费用。RS-08 文档第 2/6 节证明 repo manifest 哈希不能替代平台发布配置哈希。静态代码相同、服务 active 或旧影子收据存在均不补足这些证据。

`.github/workflows/soxl-v7-derisk-research.yml` 只手动 main、首次 attempt、Linux runner，先跑 synthetic 测试，再生成脱敏 qualification artifact。缺口未解决时以 PARKED/非零退出停止；不认证、不访问 secret/IAM/server、不下载行情。该入口不是已完成的真实回测器：合格云端数据/插件联合回放适配尚未绑定，不能仅填一个 GCS 路径就解锁计算。完整 extended、real-only、WFA、伪留出、2x 成本与插件成绩当前全部 NOT_RUN/NOT_COMPARABLE。真实序列以后仍只允许留云端私有存储，本机只接收摘要。

## 协作与验证

本任务只新增 `soxl_v7_derisk*` research 模块、测试、文档、CLI 和独立工作流。rs04_p3 的 adapter、RS-04 文件、既有 CI、生产依赖 pin、live/shadow 配置和 B1/B2 合同均不在写集；不 force-push。PR 必须所有检查成功，再 squash 并绑定 head SHA，不使用 admin。

合成测试复现旧 V7：同一有效 risk-off 输入下基线 SOXL/SOXX 为零，旧 V7 穿透敞口 1.261；新候选为零。另覆盖 RSI/Bollinger/叠加/波动退出、收入层阈值、随机目标、不同财富路径、1x/2x 费用、整股、逐 bar 实持约束、未来扰动因果性、错误成交、独立校准与失败门。它们只证明工程性质，不是真实收益或金融验收。
