# Lumen Doris Benchmark

五套合成企业数仓及配套 Data Agent 评测。每套使用独立的 Doris 数据库，包含源采集、业务事实、维度、历史版本和多粒度汇总。任务覆盖自动建模、本体语义、单轮问数、多轮追问、探索、报告、反例和权限边界。所有实体、文档和问题均为合成原创。

当前发布的首套基准是 **岚屿零售**。制造、快递物流、SaaS 订阅及批发数仓已在版本合同中定义独立库名和治理等级，按阶段交付。

## 生成零售基准

需要 Python 3.11 或更高版本。生成、离线验证和结构化评分使用标准库。

```bash
python3 -m lumen_bench validate retail
python3 -m lumen_bench build retail --output build/retail-v1
python3 -m unittest discover -s tests
```

构建输出包含两套独立文件。给参测者的 `candidate/` 包含 DDL、逐表装载脚本、公司材料、可获得的 ETL、问题和角色范围。评估端保管 `evaluator/` 中的黄金答案、参考查询、标准关系和逐表指纹。每个问题的自然语言在 `turns` 中，任务 ID 和标签仅供编排。

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

从实际业务客户端加入一个候选环境，按 [评测协议](docs/evaluation.md) 发起自然语言会话并保留真实产物。需要分析与复核时，把二者交给不同人员。使用结构化提取结果与黄金答案评分。

```bash
python3 -m lumen_bench grade \
  --gold build/retail-v1/evaluator/gold.jsonl \
  --observations build/retail-observed.jsonl \
  --output build/retail-score.json
```

[机器合同](contracts/v1.json) · [评测方法参考](docs/methodology.md) · [工作台账](work-ledger.yaml)
