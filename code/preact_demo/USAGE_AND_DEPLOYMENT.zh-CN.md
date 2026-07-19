# PreAct-Memory 使用与部署文档

本文档说明如何运行 `meta.md` 中描述的第一版 MVP：LLM 负责语义判断，程序负责图执行、检索、ID 管理、日志和指标统计。

## 1. 功能范围

Python 实现位于 `preact_demo/code/`，主入口是：

```powershell
python preact_demo/code/main.py
```

当前流程包括：

```text
历史对话
-> 构建异构 memory graph
-> vLLM/heuristic 预测 future need hypotheses
-> 程序化 grounding 到 memory graph
-> 检测 memory gap
-> 可选 web/paper evidence repair
-> 构建 working context package
-> 下一轮 query 到来后 verifier 决定 use/reject
-> 不足时 fallback 到 vector/graph retrieval
-> 生成回答与评估指标
```

主动准备阶段不会读取下一轮 query；query 只在 verifier 和 fallback retrieval 阶段使用。

## 2. 本地快速运行

不启动大模型服务，使用确定性 heuristic predictor 和 hash embedding：

```powershell
cd D:\MEM\code
python preact_demo/code/main.py --dataset demo --predictor heuristic --details
```

运行 LoCoMo 前 10 条 QA：

```powershell
python preact_demo/code/main.py --dataset locomo --limit 10 --predictor heuristic
```

`--limit 0` 表示运行全部本地 LoCoMo 样本。

## 3. 启动 vLLM 服务

先单独启动 OpenAI-compatible vLLM server：

```powershell
python -m vllm.entrypoints.openai.api_server `
  --model Qwen/Qwen2.5-7B-Instruct `
  --host 127.0.0.1 `
  --port 8000
```

然后运行 pipeline：

```powershell
python preact_demo/code/main.py `
  --dataset demo `
  --predictor vllm `
  --vllm-host 127.0.0.1 `
  --vllm-port 8000 `
  --vllm-model Qwen/Qwen2.5-7B-Instruct `
  --details
```

如果已经有完整 endpoint，也可以直接指定：

```powershell
python preact_demo/code/main.py `
  --dataset demo `
  --predictor vllm `
  --vllm-url http://127.0.0.1:8000/v1 `
  --vllm-model Qwen/Qwen2.5-7B-Instruct
```

默认配置中 `llm.fallback_to_heuristic: true`，因此 vLLM 不可用时会回退到 heuristic predictor，方便本地调试。

## 4. 使用 YAML 配置

示例配置文件：

```text
preact_demo/code/configs/python_demo.yaml
```

运行：

```powershell
python preact_demo/code/main.py `
  --dataset demo `
  --config preact_demo/code/configs/python_demo.yaml `
  --details
```

关键 YAML 字段：

```yaml
predictor: vllm
cache_budget: 3
retrieval_top_k: 3
fallback_retriever: vector

llm:
  host: 127.0.0.1
  port: 8000
  api_path: /v1
  model: Qwen/Qwen2.5-7B-Instruct
  timeout: 30
  fallback_to_heuristic: true
  use_for_answer: false

embedding:
  provider: hash
  dimensions: 256
```

命令行参数优先级高于 YAML。例如临时换端口和模型：

```powershell
python preact_demo/code/main.py `
  --dataset demo `
  --config preact_demo/code/configs/python_demo.yaml `
  --vllm-port 8123 `
  --vllm-model Qwen/Qwen3-8B `
  --details
```

JSON 配置仍然兼容：

```powershell
python preact_demo/code/main.py --config preact_demo/code/configs/python_demo.json
```

## 5. 启用 LLM 模块

默认只让 vLLM 做 future intent / future need prediction。可以通过配置逐步启用更多 LLM 判断：

```yaml
grounding:
  mode: hybrid

memory_gap:
  mode: hybrid

verifier:
  mode: hybrid
```

使用 vLLM 生成最终答案：

```powershell
python preact_demo/code/main.py `
  --dataset demo `
  --predictor vllm `
  --answer-with-vllm `
  --details
```

## 6. 外部证据修复

默认不访问网络。启用 web/paper search：

```powershell
python preact_demo/code/main.py `
  --dataset demo `
  --predictor vllm `
  --enable-external-search `
  --details
```

相关 YAML：

```yaml
external_search:
  enabled: true
  source_types: [web, paper]
  results_per_query: 2
  max_queries_per_gap: 2
  timeout: 8
```

外部检索失败不会中断 pipeline；gap 会被保留在 working context package 中。

## 7. 输出指标

程序输出三组表：

- `Activation Quality`：precision、recall、hit_rate、wasted_rate。
- `Answer Quality`：F1、ROUGE-L、pseudo judge、faithfulness。
- `Efficiency`：query_time_latency_ms、idle_time_cost、total_tokens、fallback_rate。

使用 `--details` 会显示每条样本的 selected memory、working context package、coverage、gap 数量、external evidence 数量和 verifier 类型。

## 8. 测试

```powershell
python -B -m unittest discover -s preact_demo/tests -v
```

测试覆盖 YAML 配置、vLLM host/port/model override、future need hypotheses、graph grounding、gap detection、working context verifier 和 fallback pipeline。
