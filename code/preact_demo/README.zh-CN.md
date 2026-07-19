# PreAct-Memory Lite Demo 项目结构说明

本文档说明 `preact_demo` 的目录结构、核心文件职责、运行方式和整体数据流。

这个项目是一个轻量版 proactive memory activation demo。它的目标不是训练模型，而是先把完整实验链路跑通：

1. 从长对话历史中构造长期记忆。
2. 构建简单异构记忆图。
3. 在 idle-time 预测未来可能用到的 memory nodes。
4. 把预测出的 memory 放入 working memory cache。
5. 下一轮 query 到来后验证 cache 是否可用。
6. cache 不足时 fallback 到检索。
7. 生成回答并输出评估指标。

## Python 版九阶段改进链路

`preact_demo/code/` 已按下面的职责拆分，且主动阶段严格不读取下一轮 query：

1. 构建 `MemoryNode / TurnNode / SegmentNode / EntityNode` 异构图。
2. LLM 或本地启发式生成结构化 future-need hypotheses。
3. 使用 hash/vLLM embedding grounding，可选 LLM rerank。
4. 按 grounding 分数激活 memory nodes 和 local subgraph。
5. 使用覆盖分数或 LLM 判断 memory gap。
6. gap 存在且开启配置时，执行 web search 和 paper search。
7. 构造包含 hypothesis、memory、subgraph、gap、外部证据和 provenance 的 working context package。
8. query 到来后由 embedding/LLM verifier 判断 package 是否可用。
9. package 不够时 fallback 到 vector 或 graph retrieval。

默认配置不需要第三方 Python 包：embedding 使用确定性 hash embedding，外部搜索关闭。启用外部检索：

```bash
python preact_demo/code/main.py --dataset demo --enable-external-search
```

主要新增模块：

- `code/embeddings.py`：可替换 embedding provider。
- `code/grounding.py`：hypothesis 到 memory graph grounding。
- `code/memory_gap.py`：memory gap 判断。
- `code/external_search.py`：web/paper search provider。
- `code/working_context.py`：working context package 和 query-time verifier。

完整运行、vLLM 部署和 YAML 配置说明见：

```text
preact_demo/USAGE_AND_DEPLOYMENT.zh-CN.md
```

Python 版支持用 YAML 或命令行参数指定 vLLM 端口和模型：

```bash
python preact_demo/code/main.py \
  --dataset demo \
  --config preact_demo/code/configs/python_demo.yaml \
  --vllm-port 8000 \
  --vllm-model Qwen/Qwen2.5-7B-Instruct
```

## 目录结构

```text
preact_demo/
  README.md
  README.zh-CN.md
  code/
  javascript/
    run_demo.js

    activation/
      llm_predictor.js
      verifier.js
      working_cache.js

    common/
      text.js

    configs/
      demo.json

    data/
      load_samples.js

    evaluation/
      evidence_labeler.js
      metrics.js
      run_eval.js

    generation/
      answer_generator.js

    graph/
      build_graph.js
      schema.js

    memory/
      memory_writer.js

    retrieval/
      graph_retriever.js
      vector_retriever.js

  data/
    samples.json
    locomo/
      locomo10.json
```

## 顶层文件

### `javascript/run_demo.js`

项目主入口。

职责：

- 解析命令行参数。
- 读取配置文件 `javascript/configs/demo.json`。
- 加载数据集，包括内置 demo 数据和 LoCoMo 数据。
- 调用 `javascript/evaluation/run_eval.js` 执行完整评估。
- 打印三类结果表：
  - Activation Quality
  - Answer Quality
  - Efficiency

常用命令：

```bash
node preact_demo/javascript/run_demo.js
node preact_demo/javascript/run_demo.js --dataset locomo --limit 10
node preact_demo/javascript/run_demo.js --dataset locomo --limit 0
```

其中 `--limit 0` 表示跑全集。

### `README.md`

英文说明文档。

### `README.zh-CN.md`

当前中文说明文档。

## `javascript/configs/` 配置目录

### `javascript/configs/demo.json`

运行配置文件。

当前主要字段：

```json
{
  "cacheBudget": 3,
  "retrievalTopK": 3,
  "fallbackRetriever": "vector",
  "verifierThreshold": 0.12,
  "similarityThreshold": 0.28,
  "randomSeed": 7
}
```

字段含义：

- `cacheBudget`：proactive cache 最多保留几个 memory。
- `retrievalTopK`：fallback 或 reactive retrieval 返回几个 memory。
- `fallbackRetriever`：主方法 fallback 使用的检索器，当前默认是 `vector`。
- `verifierThreshold`：query 到来后，verifier 判断 cache memory 是否相关的阈值。
- `similarityThreshold`：构建 `similar_to` 图边时的相似度阈值。
- `randomSeed`：Random Cache baseline 使用的固定种子。

## `data/` 数据目录

### `data/samples.json`

内置小型 demo 数据。

它用于快速验证 pipeline 是否能跑通，不依赖外部数据集。

每条样本结构大致是：

```json
{
  "id": "sample_acl_positioning",
  "history": [],
  "question": "...",
  "answer": "...",
  "evidence_terms": []
}
```

含义：

- `history`：长对话历史。
- `question`：模拟下一轮用户 query。
- `answer`：gold answer。
- `evidence_terms`：用于构造 pseudo-gold evidence 的关键词。

### `javascript/data/load_samples.js`

数据加载和转换模块。

职责：

- 加载内置 demo 数据。
- 加载 LoCoMo `locomo10.json`。
- 支持 `--limit N`。
- 支持 `--limit 0` 表示全集。
- 支持 `--download-locomo` 自动下载 LoCoMo。
- 把 LoCoMo 原始格式转换成当前评估 pipeline 使用的统一 sample 格式。

LoCoMo 转换逻辑：

- 每个 LoCoMo QA 转成一个评估样本。
- 每个 conversation turn 转成一个轻量 memory。
- LoCoMo 的 `evidence` 字段会转成 `gold_evidence_turn_ids`。
- 同一个 conversation 的多个 QA 共享 `history_cache_key`，避免重复建图。

### `data/locomo/locomo10.json`

LoCoMo 官方数据文件。

当前已下载到：

```text
preact_demo/data/locomo/locomo10.json
```

官方来源：

```text
https://github.com/snap-research/locomo
```

## `javascript/memory/` 记忆写入模块

### `javascript/memory/memory_writer.js`

Memory Writer 模块。

职责：

- 把 dialogue history 里的 memory 标注转成统一的 `MemoryNode`。

输出的 memory 结构包括：

```js
{
  id,
  node_type: "MemoryNode",
  type,
  content,
  summary,
  keywords,
  entities,
  segment_id,
  source_turn_id,
  timestamp,
  importance
}
```

当前版本没有调用真实 LLM，而是使用样本或 LoCoMo loader 预先构造的 memories。

后续如果接真实 LLM 记忆抽取，主要替换这个文件。

## `javascript/graph/` 异构图模块

### `javascript/graph/schema.js`

定义图节点类型和边类型。

节点类型：

```text
MemoryNode
TurnNode
SegmentNode
EntityNode
```

边类型：

```text
derived_from
belongs_to
mentions
similar_to
temporal_next
```

### `javascript/graph/build_graph.js`

构建简单异构记忆图。

职责：

- 为每个对话 turn 构造 `TurnNode`。
- 为每个 session/segment 构造 `SegmentNode`。
- 为每个 memory 构造 `MemoryNode`。
- 为 memory 中出现的实体构造 `EntityNode`。
- 添加以下边：
  - `MemoryNode --derived_from--> TurnNode`
  - `MemoryNode --belongs_to--> SegmentNode`
  - `TurnNode --belongs_to--> SegmentNode`
  - `MemoryNode --mentions--> EntityNode`
  - `MemoryNode --temporal_next--> MemoryNode`
  - `MemoryNode --similar_to--> MemoryNode`

当前图结构用普通 JS 对象表示：

```js
{
  nodes: {},
  edges: []
}
```

## `javascript/activation/` 主动激活模块

### `javascript/activation/llm_predictor.js`

Idle-time future memory need predictor。

它是 demo 的核心模块。

重要约束：

```text
predictor 只能看 history，不能看下一轮 question。
```

当前版本用启发式逻辑模拟 LLM predictor，主要参考：

- recent dialogue keywords
- active segment
- active entities
- memory importance
- recency

输出：

```js
{
  predicted_future_intents: [],
  activated_memory_ids: [
    {
      id,
      reason,
      confidence
    }
  ]
}
```

后续如果接真实 LLM predictor，主要替换这个文件。

### `javascript/activation/working_cache.js`

Working Memory Cache 模块。

职责：

- 接收 predictor 选出的 memory ids。
- 构造 cache 对象。
- 提取这些 memory 周围的 local subgraph。

输出结构：

```js
{
  cache_id,
  budget,
  memory_ids,
  summaries,
  local_subgraph,
  prediction
}
```

### `javascript/activation/verifier.js`

Query-time verifier。

它在下一轮 query 到来后运行。

职责：

- 判断 cache 里的 memory 是否和当前 query 相关。
- 过滤掉错误激活的 memory。
- 如果没有任何 cache memory 通过验证，则主方法进入 fallback retrieval。

当前版本用 lexical overlap 做相关性判断。

后续可以替换成：

- embedding similarity verifier
- LLM verifier
- hybrid verifier

## `javascript/retrieval/` 检索模块

### `javascript/retrieval/vector_retriever.js`

Reactive vector retrieval baseline。

当前版本没有真实 embedding，所以用 lexical overlap 模拟向量检索。

它代表 query 到来之后再检索 memory 的 reactive baseline。

### `javascript/retrieval/graph_retriever.js`

Reactive graph retrieval baseline。

当前版本在 lexical overlap 的基础上，对图邻居关系加简单 boost。

主要使用：

- `similar_to`
- `temporal_next`

注意：在当前 LoCoMo 启发式实现上，graph retrieval 表现弱于 vector retrieval，所以主方法的 fallback 默认用 `vector`，但 graph retrieval 仍作为独立 baseline 保留。

## `javascript/generation/` 回答生成模块

### `javascript/generation/answer_generator.js`

Answer Generator。

当前版本是极简实现：把选中的 memory summaries 拼成回答。

输出类似：

```text
Based on memory: ...
```

它的目标不是生成高质量自然语言，而是让评估 pipeline 能完整跑通。

后续如果接真实 LLM 回答生成，主要替换这个文件。

## `javascript/evaluation/` 评估模块

### `javascript/evaluation/run_eval.js`

评估编排核心。

它会对每个 sample 跑以下方法：

```text
Random Cache
Recency Cache
Reactive Vector Retrieval
Reactive Graph Retrieval
LLM-Predict Cache Only
LLM-Predict + Fallback
Oracle Cache
```

并聚合每种方法的平均指标。

LoCoMo 性能优化也在这里：

- 同一个 conversation 的多个 QA 共享 `history_cache_key`。
- memory graph 和 proactive cache 只构建一次。

### `javascript/evaluation/evidence_labeler.js`

Gold evidence 标注模块。

支持两种 evidence 来源：

1. 如果样本有 `gold_evidence_turn_ids`，优先按 turn id 匹配。
2. 否则使用 `evidence_terms` 和 memory 内容做关键词匹配。

LoCoMo 数据会优先走第一种，因为 LoCoMo QA 里通常有 evidence 字段。

### `javascript/evaluation/metrics.js`

指标计算模块。

Activation 指标：

```text
precision
recall
hit_rate
wasted_rate
```

Answer quality 指标：

```text
F1
ROUGE-L
pseudo LLM judge
faithfulness
```

Efficiency 指标：

```text
query_time_latency_ms
idle_time_cost
total_tokens
fallback_rate
```

注意：当前 latency 和 token cost 是估算值，用于比较不同方法的趋势，不代表真实生产耗时。

## `javascript/common/` 通用工具

### `javascript/common/text.js`

文本处理工具。

职责：

- tokenization
- keyword extraction
- Jaccard similarity
- query-memory overlap score
- token 数量估算

当前 demo 没接 embedding，所以很多模块都依赖这里的轻量文本相似度函数。

## 当前数据流

完整流程如下：

```text
Dataset sample
  |
  |-- history
  |-- question
  |-- answer
  |-- evidence
  v
Memory Writer
  v
MemoryNode list
  v
Heterogeneous Graph Builder
  v
Memory graph
  v
Idle-time Predictor
  v
Working Memory Cache
  v
Query arrives
  v
Verifier
  |
  |-- cache sufficient --> use cache memories
  |
  |-- cache insufficient --> fallback retrieval
  v
Answer Generator
  v
Evaluation Metrics
```

## 支持的运行方式

### 跑内置 demo

```bash
node preact_demo/javascript/run_demo.js
```

或：

```bash
npm.cmd run demo
```

### 跑 LoCoMo 前 10 条 QA

```bash
node preact_demo/javascript/run_demo.js --dataset locomo --limit 10
```

如果本地还没有 LoCoMo 文件：

```bash
node preact_demo/javascript/run_demo.js --dataset locomo --limit 10 --download-locomo
```

### 跑 LoCoMo 前 100 条 QA

```bash
node preact_demo/javascript/run_demo.js --dataset locomo --limit 100
```

### 跑 LoCoMo 全集

```bash
node preact_demo/javascript/run_demo.js --dataset locomo --limit 0
```

`--limit 0` 表示全集。

当前 `locomo10.json` 会转换出 1986 个 QA 样本。

## 输出表说明

### Activation Quality

衡量提前激活的 memory 是否命中 gold evidence。

字段：

- `precision`：激活的 memory 里有多少是真正证据。
- `recall`：真正证据里有多少被激活。
- `hit_rate`：是否至少命中一个证据 memory。
- `wasted_rate`：无效激活比例。

### Answer Quality

衡量生成答案和 gold answer 的相似度。

字段：

- `f1`
- `rouge_l`
- `llm_judge`
- `faithfulness`

当前 `llm_judge` 是伪指标，不是真实 LLM judge。

### Efficiency

衡量 query-time 成本和总成本趋势。

字段：

- `query_time_latency_ms`
- `idle_time_cost`
- `total_tokens`
- `hit_rate`
- `fallback_rate`

核心观察点：

```text
proactive 方法不是消灭成本，而是把一部分 query-time 工作提前移动到 idle-time。
```

## 当前版本限制

当前版本是 lightweight prototype，有这些限制：

- 没有真实 LLM memory writer。
- 没有真实 LLM future predictor。
- 没有真实 embedding。
- 没有真实 LLM answer generator。
- latency 和 token cost 是估算值。
- LoCoMo QA 不一定是自然的下一轮 query，因此 proactive cache 命中低是合理现象。

## 后续建议

下一步可以按这个顺序升级：

1. 用真实 embedding 替换 lexical overlap。
2. 用 LLM 替换 `memory_writer.js`。
3. 用 LLM 替换 `llm_predictor.js`。
4. 用 LLM 或 embedding verifier 替换 `verifier.js`。
5. 用真实 LLM 替换 `answer_generator.js`。
6. 增加 cache budget sweep，例如 `B=3,5,10,20`。
7. 把评估结果保存成 CSV/JSON，方便画表和写论文。

