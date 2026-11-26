# Lumen Doris Benchmark

五套合成企业数仓及配套 Data Agent 评测。每套使用独立的 Doris 数据库，包含源采集、业务事实、维度、历史版本和多粒度汇总。任务覆盖自动建模、本体语义、单轮问数、多轮追问、探索、报告、反例和权限边界。所有实体、文档和问题均为合成原创。

| 数据集 | 独立数据库 | 建设质量 | 业务表 | 评测任务 |
|---|---|---|---:|---:|
| 零售 | `lumen_bench_retail_v1` | 统一建模与完整文档 | 158 | 32 |
| 制造 | `lumen_bench_manufacturing_v1` | 治理较好，保留旧口径 | 161 | 33 |
| 物流 | `lumen_bench_logistics_v1` | 部分治理，流程与账单分域 | 165 | 33 |
| SaaS 订阅 | `lumen_bench_subscription_v1` | 部门烟囱，分歧口径 | 172 | 33 |
| 批发 | `lumen_bench_wholesale_v1` | 拼音缩写、弱文档、重复与错误 ETL | 186 | 37 |

指标为默认种子 `7719` 的生成结果，专用发布标记表单列。[数仓设计](docs/warehouses.md) · [Doris 实测报告](docs/release-v1.md) · [下载五套候选包](https://github.com/originoneai/lumen-doris-benchmark/releases/tag/v1.0.0)

## 生成评测基准

需要 Python 3.11 或更高版本。生成、离线验证和结构化评分使用标准库。

```bash
python3 -m lumen_bench validate retail
python3 -m lumen_bench build retail --output build/retail-v1
python3 -m unittest discover -s tests
```

将 `retail` 换成 `manufacturing`、`logistics`、`subscription` 或 `wholesale` 即可生成相应评测。构建输出包含两套独立文件。给参测者的 `candidate/` 包含 DDL、逐表装载脚本、公司材料、可获得的 ETL、问题和角色范围。评估端保管 `evaluator/` 中的黄金答案、参考查询、标准关系和逐表指纹。每个问题的自然语言在 `turns` 中，任务 ID 和标签仅供编排。默认种子是公开可复现基线。盲评用自选种子在独立空白环境构建，并由评分者管理种子及其评估包。

## Doris 部署与核验

```bash
python3 -m pip install -r requirements-doris.txt
python3 -m lumen_bench deploy-doris retail \
  --connection /path/to/private-connection.json \
  --cluster-id AUTHORIZED_CLUSTER_ID \
  --receipt build/retail-doris-deployment.json
python3 -m lumen_bench verify-doris retail \
  --connection /path/to/private-connection.json \
  --cluster-id AUTHORIZED_CLUSTER_ID \
  --receipt build/retail-doris-verification.json
```

连接文件使用私有文件权限 `0600`，内容包含 `host`、`port`、`user` 和 `password`。可用 `--ssh-host` 经由获准跳板转发。接入账号应具有查看 FE/BE 以及创建、加载并读取目标基准库的权限，参测 Agent 应使用单独的只读身份。

部署固定使用版本合同中的库名。现有同名库必须有匹配的专用发布指纹，已装载的非空表按行核对，重试只装载缺失或尚空的目标表。结构、内容、参考查询和可见 ETL 均在 Doris 上单独核对。

## 执行评测

从实际业务客户端发起自然语言会话。可以先生成一个任务的独立会话输入、角色、工作步骤和取证路径。完整流程见 [评测协议](docs/evaluation.md)。

```bash
python3 -m lumen_bench plan-run --bundle build/retail-v1 \
  --scenario retail/RPT-01 --output build/retail-report-run
```

把回答和工具回执交给独立评估者，再将结构化结果与黄金答案比较。

```bash
python3 -m lumen_bench grade \
  --gold build/retail-v1/evaluator/gold.jsonl \
  --observations build/retail-observed.jsonl \
  --tasks build/retail-v1/candidate/tasks.jsonl \
  --output build/retail-score.json
```

[机器合同](contracts/v1.json) · [评测方法参考](docs/methodology.md) · [工作台账](work-ledger.yaml)
