# PreAct-Memory Python 使用指南

本文说明如何运行 `preact_demo/code/` 下的九阶段 proactive memory pipeline。

## 1. Pipeline 流程

```text
Dialogue History
  -> 异构 Memory Graph
  -> Future Need Hypotheses
  -> Memory Graph Grounding
  -> Memory Nodes / Subgraph Activation
  -> Memory Gap Detection
  -> Web / Paper Search（可选）
  -> Working Context Package
  -> Query-Time Verifier
  -> Fallback Retrieval（必要时）
  -> Answer Generation
```

主动准备阶段只能读取历史对话，不会读取下一轮 query。query 到来后才运行 verifier 和 fallback retrieval。

## 2. 环境要求

- Python 3.10 或更高版本。
- 默认本地模式不需要安装第三方 Python 包。
- 使用 LLM 时，需要单独启动 OpenAI-compatible vLLM 服务。
- web/paper search 需要网络访问。

所有命令均从仓库根目录运行：

```powershell
cd D:\MEM\code
```

如果 `python` 不在 PATH 中，可以使用本机 Python 的完整路径，例如：

```powershell
D:\CONDA\python.exe preact_demo/code/main.py --dataset demo --predictor heuristic
```

## 3. 最快运行

不依赖 LLM 和网络，使用本地 heuristic predictor 与 hash embedding：

```powershell
python preact_demo/code/main.py --dataset demo --predictor heuristic --details
```

关键参数：

- `--dataset demo`：使用内置演示数据。
- `--predictor heuristic`：不调用 vLLM。
- `--details`：显示每条样本选择的 memory、package coverage、gap 数量和 verifier 类型。

## 4. 运行 LoCoMo

运行前 10 条 QA：

```powershell
python preact_demo/code/main.py --dataset locomo --limit 10 --predictor heuristic
```

运行全部本地 LoCoMo 数据：

```powershell
python preact_demo/code/main.py --dataset locomo --limit 0 --predictor heuristic
```

默认数据文件：

```text
preact_demo/data/locomo/locomo10.json
```

指定其他文件：

```powershell
python preact_demo/code/main.py `
  --dataset locomo `
  --locomo-path D:\data\locomo10.json `
  --limit 100 `
  --predictor heuristic
```

本地没有数据时，可以尝试自动下载：

```powershell
python preact_demo/code/main.py --dataset locomo --limit 10 --download-locomo
```

## 5. 使用 vLLM Predictor

先单独启动 vLLM OpenAI-compatible 服务：

```powershell
python -m vllm.entrypoints.openai.api_server `
  --model Qwen/Qwen2.5-7B-Instruct `
  --host 127.0.0.1 `
  --port 8000
```

再运行 pipeline：

```powershell
python preact_demo/code/main.py `
  --dataset demo `
  --predictor vllm `
  --vllm-host 127.0.0.1 `
  --vllm-port 8000 `
  --vllm-model Qwen/Qwen2.5-7B-Instruct `
  --details
```

也可以直接指定完整 endpoint：

```powershell
python preact_demo/code/main.py `
  --dataset demo `
  --predictor vllm `
  --vllm-url http://127.0.0.1:8000/v1 `
  --vllm-model Qwen/Qwen2.5-7B-Instruct
```

vLLM 不可用时，默认会退回 heuristic predictor。该行为由配置中的 `llm.fallback_to_heuristic` 控制。

也可以通过环境变量设置服务：

```powershell
$env:VLLM_BASE_URL = "http://127.0.0.1:8000/v1"
$env:VLLM_MODEL = "Qwen/Qwen2.5-7B-Instruct"
python preact_demo/code/main.py --dataset demo --predictor vllm
```

## 6. 使用 Embedding

默认配置使用 dependency-free hash embedding：

```json
"embedding": {
  "provider": "hash",
  "dimensions": 256
}
```

它用于：

- future need 到 memory graph grounding；
- query 到 working context package 的 verifier；
- 无模型环境下的确定性测试。

如果 vLLM 服务支持 `/v1/embeddings`，可以修改配置：

```json
"embedding": {
  "provider": "vllm",
  "dimensions": 256
}
```

embedding 请求失败时会回退到 hash embedding。

注意：chat model 不一定支持 embedding API。生产环境通常应为 embedding 单独配置模型和 endpoint；当前 demo 复用 `llm` 配置。

## 7. Grounding 和激活

相关配置：

```json
"cache_budget": 3,
"grounding": {
  "mode": "embedding",
  "activation_threshold": 0.08,
  "candidates_per_hypothesis": 5
}
```

- `cache_budget`：最多激活的不同 memory node 数量。
- `grounding.mode`：`embedding`、`llm` 或 `hybrid`。
- `activation_threshold`：hypothesis-memory 最低 grounding 分数。
- `candidates_per_hypothesis`：每个 hypothesis 保留的候选数量。

命令行可以临时修改 cache budget：

```powershell
python preact_demo/code/main.py --dataset demo --predictor heuristic --cache-budget 5
```

## 8. Memory Gap 和外部检索

默认只检测 gap，不访问网络：

```json
"memory_gap": {
  "mode": "score",
  "coverage_threshold": 0.18
},
"external_search": {
  "enabled": false,
  "source_types": ["web", "paper"],
  "results_per_query": 2,
  "max_queries_per_gap": 2,
  "timeout": 8
}
```

启用 web/paper search：

```powershell
python preact_demo/code/main.py `
  --dataset demo `
  --predictor heuristic `
  --enable-external-search `
  --details
```

当前 provider：

- `web`：DuckDuckGo Instant Answer API。
- `paper`：Semantic Scholar API。

搜索失败不会中断 pipeline。系统会保留 gap，并继续进入 query-time verifier 和 fallback retrieval。

## 9. Query-Time Verifier

```json
"verifier": {
  "mode": "embedding",
  "threshold": 0.12,
  "hypothesis_threshold": 0.12
}
```

package 被判定为可用，需要同时满足：

1. query 匹配至少一个 future need hypothesis；
2. query 匹配至少一条 activated memory 或 external evidence。

配置说明：

- `mode`：`embedding`、`llm` 或 `hybrid`。
- `threshold`：query 与 package evidence 的最低相似度。
- `hypothesis_threshold`：query 与 future hypothesis 的最低相似度。

如果 verifier 判定不够，系统自动进入 fallback retrieval。

## 10. Fallback Retrieval

默认配置：

```json
"retrieval_top_k": 3,
"fallback_retriever": "vector"
```

使用 vector fallback：

```powershell
python preact_demo/code/main.py `
  --dataset demo `
  --predictor heuristic `
  --fallback-retriever vector `
  --retrieval-top-k 5
```

使用 graph fallback：

```powershell
python preact_demo/code/main.py `
  --dataset demo `
  --predictor heuristic `
  --fallback-retriever graph `
  --retrieval-top-k 5
```

## 11. 使用 LLM 生成答案

默认回答生成器只拼接 verified context，便于测试 pipeline。

使用 vLLM 生成最终回答：

```powershell
python preact_demo/code/main.py `
  --dataset demo `
  --predictor vllm `
  --answer-with-vllm `
  --details
```

LLM 回答被约束为只使用 supplied memory/external context。

## 12. 自定义配置

默认配置文件：

```text
preact_demo/code/configs/python_demo.json
```

同等 YAML 配置文件：

```text
preact_demo/code/configs/python_demo.yaml
```

复制并修改配置后运行：

```powershell
python preact_demo/code/main.py `
  --dataset demo `
  --config D:\MEM\code\my_memory_config.json `
  --details
```

命令行参数会覆盖配置文件中的对应字段。

YAML 中可以直接指定 vLLM 服务：

```yaml
llm:
  host: 127.0.0.1
  port: 8000
  api_path: /v1
  model: Qwen/Qwen2.5-7B-Instruct
```

命令行临时覆盖端口和模型：

```powershell
python preact_demo/code/main.py `
  --dataset demo `
  --config preact_demo/code/configs/python_demo.yaml `
  --vllm-port 8123 `
  --vllm-model Qwen/Qwen3-8B `
  --details
```

## 13. 输出说明

程序输出三组聚合指标。

### Activation Quality

- `precision`：激活 memory 中 gold evidence 的比例。
- `recall`：gold evidence 被激活的比例。
- `hit_rate`：是否至少命中一条 evidence。
- `wasted_rate`：无效激活比例。

### Answer Quality

- `f1`
- `rouge_l`
- `llm_judge`：当前为轻量 pseudo judge。
- `faithfulness`：回答与选中 context 的一致性。

### Efficiency

- `query_time_latency_ms`：估算的 query-time 延迟。
- `idle_time_cost`：主动准备阶段的估算成本。
- `total_tokens`：估算 token 总量。
- `fallback_rate`：进入 fallback retrieval 的比例。

使用 `--details` 时，主动方法还会显示：

```text
package=wcp_sample_id coverage=0.455 gaps=0 external=0 verifier=embedding
```

- `package`：working context package ID。
- `coverage`：hypotheses 的平均 memory grounding coverage。
- `gaps`：未被 memory graph 覆盖的 hypothesis 数量。
- `external`：提前获取的外部证据数量。
- `verifier`：query-time verifier 类型。

## 14. 运行测试

```powershell
python -B -m unittest discover -s preact_demo/tests -v
```

测试覆盖：

- structured future need hypotheses；
- hypothesis-memory grounding；
- memory gap detection；
- web/paper search provider；
- working context verifier；
- package 不够时的 fallback pipeline。

## 15. 常用命令汇总

纯本地 demo：

```powershell
python preact_demo/code/main.py --dataset demo --predictor heuristic --details
```

LoCoMo 前 100 条：

```powershell
python preact_demo/code/main.py --dataset locomo --limit 100 --predictor heuristic
```

vLLM predictor：

```powershell
python preact_demo/code/main.py --dataset demo --predictor vllm --details
```

外部搜索：

```powershell
python preact_demo/code/main.py --dataset demo --predictor heuristic --enable-external-search --details
```

vLLM predictor、外部搜索和 LLM answer：

```powershell
python preact_demo/code/main.py `
  --dataset demo `
  --predictor vllm `
  --enable-external-search `
  --answer-with-vllm `
  --details
```
