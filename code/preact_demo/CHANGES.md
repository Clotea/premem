# PreAct-Memory Python Demo 变更说明

## 本次目标

根据 `demo.md` 的要求，补齐一个可运行的 Python 版 PreAct-Memory Lite demo：

- 用 vLLM OpenAI-compatible 服务作为未来记忆需求预测器。
- 提供本地启发式兜底，保证没有 vLLM 服务时也能跑通 pipeline。
- 实现记忆写入、异构图存储、cache 插入、cache 验证、检索、fallback、答案生成和评估。
- 在 LoCoMo 数据上实现初步 smoke test。
- 为后续替换预测方式保留统一接口。

## 文件位置调整

新 Python demo 相关文件已统一转移到 `preact_demo/code/`：

```text
preact_demo/
  code/
    __init__.py
    main.py
    locomo.py
    utils.py
    graph_store.py
    pipeline.py
    predictors.py
    vllm_client.py
    configs/
      python_demo.json
```

原有 JavaScript demo 已统一移动到 `preact_demo/javascript/`，数据文件继续由 `preact_demo/data/` 共享。

## 新增核心能力

### 主入口

- `preact_demo/code/main.py`
  - 支持 `demo` 和 `locomo` 数据集。
  - 支持配置 cache budget、retrieval top-k、fallback retriever。
  - 支持通过 CLI 或环境变量配置 vLLM endpoint/model。

### LoCoMo 初步测试

- `preact_demo/code/locomo.py`
  - 读取 `locomo10.json`。
  - 将 `conversation.session_*` 转换为统一 `Sample` / `Turn` 格式。
  - 每个 QA 样本复用同一段 conversation history。
  - 支持 `--limit` 做小样本 smoke test。

### 图存储

- `preact_demo/code/graph_store.py`
  - 采用 JSON-friendly 内存图结构。
  - 节点包括 `MemoryNode`、`TurnNode`、`SegmentNode`、`EntityNode`。
  - 边包括 `derived_from`、`belongs_to`、`mentions`、`similar_to`、`temporal_next`。
  - 提供 `local_subgraph()`，用于 working memory cache。

### Pipeline 函数

- `preact_demo/code/pipeline.py`
  - `memory_writer()`
  - `insert_cache()`
  - `verify_cache()`
  - `vector_retrieve()`
  - `graph_retrieve()`
  - `fallback_retrieve()`
  - `generate_answer()`
  - `run_evaluation()`

### 可替换预测器

- `preact_demo/code/predictors.py`
  - 定义 `MemoryNeedPredictor` 协议。
  - `HeuristicPredictor` 用于本地兜底。
  - `VLLMPredictor` 调用 vLLM，并在失败时回退到 heuristic。
  - 后续可以新增 learned predictor，只要实现同一接口即可。

### vLLM 客户端

- `preact_demo/code/vllm_client.py`
  - 调用 `/v1/chat/completions`。
  - 兼容 vLLM 的 OpenAI-compatible API。
  - 默认 endpoint 为 `http://127.0.0.1:8000/v1`。

## 运行方式

运行内置 demo：

```bash
python preact_demo/code/main.py --dataset demo
```

运行 LoCoMo 小样本：

```bash
python preact_demo/code/main.py --dataset locomo --limit 10
```

只跑 LoCoMo 初测入口：

```bash
python preact_demo/code/locomo.py --limit 10
```

指定 vLLM 服务：

```bash
python preact_demo/code/main.py --dataset locomo --limit 10 --vllm-url http://127.0.0.1:8000/v1 --vllm-model Qwen/Qwen2.5-7B-Instruct
```

本地启动 vLLM 示例：

```bash
python -m vllm.entrypoints.openai.api_server --model Qwen/Qwen2.5-7B-Instruct --host 127.0.0.1 --port 8000
```

## 已验证

- JS demo：`node preact_demo/javascript/run_demo.js`
- Python demo：`python preact_demo/code/main.py --dataset demo`
- LoCoMo 小样本：`python preact_demo/code/main.py --dataset locomo --limit 3 --predictor heuristic`
- LoCoMo 专用入口：`python preact_demo/code/locomo.py --limit 2`

当前环境没有真实 vLLM 服务时，默认 `vllm` predictor 会按配置回退到 heuristic predictor。
