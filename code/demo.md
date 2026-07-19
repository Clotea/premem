下面是一个**第一版可落地 demo 设计**。目标是先把 pipeline 跑通，不训练模型，用 LLM 模拟预测器，在一个数据集上验证 **proactive memory activation** 是否有价值。

# Demo 核心目标

你的 demo 要验证三件事：

```text
1. 在下一轮 query 到来前，能否预测未来可能用到的 memory nodes？
2. 提前激活 memory 到 cache 后，是否能降低 query-time 检索成本？
3. verifier + fallback 是否能避免错误激活影响回答质量？
```

一句话：

> 给定长期对话历史，系统在用户空闲时间里从异构记忆图中选择可能有用的 memory nodes / subgraphs，提前放入 cache；下一轮 query 到来后，先用 cache 验证和回答，不够再 fallback 检索。

---

# 1. 数据集设置

先用 **LoCoMo** 或 **LongMemEval**。

第一版推荐 LoCoMo，因为很多 memory agent 论文都在 LoCoMo 上做过，方便和 reported baselines 对齐。

每条样本可以组织成：

```text
History H：当前已经发生的长期对话历史
Question q：模拟下一轮用户 query
Gold Answer a：标准答案
```

关键约束：

```text
Proactive activation 阶段只能看到 H，不能看到 q。
```

也就是：

```text
activation time:
  input = H

query time:
  input = q
```

---

# 2. 简单异构图设计

第一版不要做复杂 GNN，直接构建一个 **MemORAI-lite + A-MEM-style note** 的简单异构图。

## 节点类型

```text
MemoryNode：抽取出的长期记忆
TurnNode：原始对话轮次
SegmentNode：话题片段 / session 片段
EntityNode：实体，如人名、地点、任务、物品、会议名
```

## MemoryNode 字段

```json
{
  "id": "m_001",
  "type": "preference | fact | event | task | summary",
  "content": "The user prefers ACL main conference over Findings.",
  "summary": "User wants to target ACL main, not Findings.",
  "keywords": ["ACL", "main conference", "Findings"],
  "entities": ["ACL", "Findings"],
  "segment_id": "seg_03",
  "source_turn_id": "t_045",
  "timestamp": 45,
  "importance": 0.9,
  "embedding": "..."
}
```

## 边类型

第一版保留 5 类就够：

```text
MemoryNode --derived_from--> TurnNode
MemoryNode --belongs_to--> SegmentNode
TurnNode --belongs_to--> SegmentNode
MemoryNode --mentions--> EntityNode
MemoryNode --similar_to--> MemoryNode
MemoryNode --temporal_next--> MemoryNode
```

可选增强：

```text
MemoryNode --contradicts--> MemoryNode
MemoryNode --supersedes--> MemoryNode
```

但第一版可以先不做冲突边，后面再加。

---

# 3. 整体框架流程

你的 demo pipeline 可以这样画：

```text
Long Dialogue History
        ↓
Memory Writer
        ↓
Simple Heterogeneous Memory Graph
        ↓
Idle-time LLM Predictor
        ↓
Working Memory Cache
        ↓
Next Query Arrives
        ↓
Query-Time Verifier
        ↓
Use Cache OR Fallback Retrieval
        ↓
Answer Generation
        ↓
Evaluation + Feedback
```

更具体：

```text
1. 输入长期对话历史 H

2. Memory Writer
   从 H 中抽取 MemoryNodes：
   preference / fact / event / task / summary

3. 构建简单异构图
   MemoryNode + TurnNode + SegmentNode + EntityNode

4. Idle Time Activation
   LLM 根据 recent dialogue + graph catalog
   预测 top-B memory nodes / subgraphs

5. Working Memory Cache
   缓存 activated memory ids、summary、embedding、local subgraph

6. 下一轮 query q 到来

7. Query-Time Verifier
   判断 cache 中哪些 memory 和 q 匹配

8. 回答策略
   if cache sufficient:
       use activated memories
   else:
       fallback to graph / vector retrieval

9. Answer Generation
   用 verified memory context 生成答案

10. Evaluation
   计算 activation、answer quality、latency、token cost
```

---

# 4. 核心模块设计

## Module 1：Memory Writer

输入：

```text
dialogue history / dialogue chunk
```

输出：

```text
MemoryNode list
```

用 LLM prompt 抽取：

```text
从下面对话中抽取长期有用记忆。
每条记忆输出：
- type
- content
- summary
- keywords
- entities
- importance
- source_turn_id
```

---

## Module 2：Heterogeneous Memory Graph Builder

把 MemoryNodes 接到 TurnNode、SegmentNode、EntityNode 上。

构图规则：

```text
MemoryNode derived_from source TurnNode
MemoryNode belongs_to SegmentNode
MemoryNode mentions extracted EntityNode
相邻 timestamp 的 MemoryNode 连 temporal_next
embedding 相似度超过阈值的 MemoryNode 连 similar_to
```

第一版用 NetworkX 就够。

---

## Module 3：LLM Future Memory Need Predictor

这是 demo 的核心。

输入给 LLM：

```text
Recent dialogue summary
Active segment summary
Active entities
Memory catalog:
  id / type / summary / keywords / entities / timestamp / importance
Cache budget B
```

输出：

```json
{
  "predicted_future_intents": [
    "The user may continue asking about ACL paper positioning.",
    "The user may ask how this differs from existing memory systems."
  ],
  "activated_memory_ids": [
    {
      "id": "m_012",
      "reason": "Relevant to ACL-main preference.",
      "confidence": 0.86
    },
    {
      "id": "m_027",
      "reason": "Relevant to comparison with Mem0 and A-MEM.",
      "confidence": 0.78
    }
  ]
}
```

注意：

```text
LLM predictor 不能看到 q。
```

---

## Module 4：Working Memory Cache

缓存内容：

```json
{
  "cache_id": "cache_001",
  "budget": 5,
  "memory_ids": ["m_012", "m_027", "m_033"],
  "summaries": ["...", "..."],
  "local_subgraph": {
    "nodes": ["m_012", "seg_03", "e_acl"],
    "edges": ["m_012-belongs_to-seg_03", "m_012-mentions-e_acl"]
  }
}
```

缓存预算建议扫：

```text
B = 3, 5, 10, 20
```

---

## Module 5：Query-Time Verifier

query 到来后，判断 cache 是否可用。

简单版：

```text
embedding(query) vs embedding(cached summaries)
```

强一点：

```text
LLM 判断哪些 cached memory 支持回答当前 query。
```

输出：

```json
{
  "use_cache": true,
  "selected_memory_ids": ["m_012", "m_027"],
  "sufficient": true
}
```

如果不够：

```text
fallback to graph retrieval / vector retrieval
```

---

# 5. Baseline 设计

你可以分两类。

## A. 自己必须跑的轻量 baseline

这些是为了证明机制有效：

```text
1. Random Cache
随机选 B 个 memory nodes 放入 cache。

2. Recency Cache
选最近 B 个 memory nodes 放入 cache。

3. Reactive Vector Retrieval
query 到来后，用 query embedding 检索 top-k memories。

4. Reactive Graph Retrieval
query 到来后，在异构图上检索相关 nodes / subgraphs。

5. LLM-Predict Cache
你的 proactive cache，不带 fallback。

6. LLM-Predict Cache + Fallback
你的主方法。

7. Oracle Cache
用 gold evidence 选 memory，作为上界。
```

## B. 和论文 reported baselines 对齐

可以在表里引用：

```text
Mem0 / Mem0g
A-MEM
MemORAI
SGMem
MemCog
MGRetrieval
Full-context
RAG
```

但要标注：

```text
Reported results from prior papers.
```

不要写成完全公平复现，除非你真的用了同样设置。

---

# 6. 评估指标

## 6.1 Activation 指标

先构造 pseudo-gold evidence：

```text
query + gold answer + all memory nodes
→ LLM judge 哪些 memory nodes 支持 gold answer
```

然后计算：

```text
Activation Precision
= |activated ∩ evidence| / |activated|

Activation Recall
= |activated ∩ evidence| / |evidence|

Hit Rate
= 1[activated ∩ evidence ≠ ∅]

Wasted Activation Rate
= 1 - Activation Precision

Fallback Rate
= fallback samples / all samples
```

---

## 6.2 回答质量指标

```text
F1
ROUGE-L
LLM-as-a-Judge
Faithfulness to retrieved memory
```

第一版最重要的是：

```text
F1 + LLM Judge + Faithfulness
```

---

## 6.3 效率指标

要分清两类成本。

### Query-time cost

用户 query 到来之后才发生的成本：

```text
verifier time
+ cache read time
+ fallback retrieval time
+ generation time
```

这是用户感知延迟。

### Total cost

完整系统成本：

```text
memory writing cost
+ graph construction cost
+ idle-time predictor cost
+ query-time cost
```

你的方法不是消灭成本，而是：

```text
把一部分计算从 query-time 转移到 idle-time。
```

所以一定要同时报告：

```text
Query-time latency
Idle-time cost
Total token cost
Input tokens
Cache size
```

---

# 7. 主要结果表

## 表 1：Activation 质量

| Method            | Budget | Precision | Recall | Hit Rate | Wasted Rate |
| ----------------- | -----: | --------: | -----: | -------: | ----------: |
| Random Cache      |      5 |           |        |          |             |
| Recency Cache     |      5 |           |        |          |             |
| LLM-Predict Cache |      5 |           |        |          |             |
| Oracle Cache      |      5 |           |        |          |             |

## 表 2：回答质量

| Method                    | F1 | ROUGE-L | LLM Judge | Faithfulness |
| ------------------------- | -: | ------: | --------: | -----------: |
| Reactive Vector Retrieval |    |         |           |              |
| Reactive Graph Retrieval  |    |         |           |              |
| LLM-Predict Cache Only    |    |         |           |              |
| LLM-Predict + Fallback    |    |         |           |              |
| Oracle Cache              |    |         |           |              |

## 表 3：效率

| Method                 | Query-time Latency | Idle-time Cost | Total Tokens | Cache Hit | Fallback Rate |
| ---------------------- | -----------------: | -------------: | -----------: | --------: | ------------: |
| Reactive Retrieval     |                    |              - |              |         - |             - |
| Recency Cache          |                    |                |              |           |               |
| LLM-Predict Cache      |                    |                |              |           |               |
| LLM-Predict + Fallback |                    |                |              |           |               |

---

# 8. Demo 伪代码

```python
for sample in dataset:
    history = sample["history"]
    query = sample["question"]
    gold_answer = sample["answer"]

    # 1. Build memory graph
    memory_nodes = memory_writer(history)
    graph = build_heterogeneous_graph(memory_nodes, history)

    # 2. Idle-time activation
    cache = llm_predict_activation(
        recent_dialogue=get_recent_dialogue(history),
        graph_catalog=summarize_graph(graph),
        budget=B
    )

    # 3. Query arrives
    verified_memory = verify_cache(
        query=query,
        cache=cache
    )

    # 4. Use cache or fallback
    if verified_memory:
        context = verified_memory
        fallback_used = False
    else:
        context = graph_retrieve(query, graph, top_k=K)
        fallback_used = True

    # 5. Generate answer
    pred_answer = generate_answer(query, context)

    # 6. Evidence labeling
    evidence = label_gold_evidence(
        query=query,
        gold_answer=gold_answer,
        memory_nodes=memory_nodes
    )

    # 7. Metrics
    activation_metrics = compute_activation_metrics(
        activated=cache.memory_ids,
        evidence=evidence
    )

    quality_metrics = evaluate_answer(
        pred_answer,
        gold_answer
    )

    efficiency_metrics = record_costs()
```

---

# 9. 文件结构建议

```text
preact_demo/
  data/
    locomo/
  graph/
    build_graph.py
    schema.py
  memory/
    memory_writer.py
    memory_store.py
  activation/
    llm_predictor.py
    working_cache.py
    verifier.py
  retrieval/
    vector_retriever.py
    graph_retriever.py
  generation/
    answer_generator.py
  evaluation/
    evidence_labeler.py
    metrics.py
    run_eval.py
  configs/
    demo.yaml
```

---

# 10. 第一版最小可行实验

最小版本就做：

```text
Dataset:
  LoCoMo 100 samples

Graph:
  MemoryNode + TurnNode + SegmentNode + EntityNode

Predictor:
  LLM 选择 top-B memory ids

Cache:
  memory summaries + local subgraph ids

Verifier:
  LLM verifier 或 embedding similarity

Baselines:
  random cache
  recency cache
  reactive vector retrieval
  reactive graph retrieval
  oracle cache

Metrics:
  activation precision / recall / hit rate
  answer F1 / LLM judge
  query-time latency
  idle-time cost
  token cost
```

---

# 11. 你希望看到的结果

第一版如果能得到下面这些趋势，就说明 demo 成立：
~~~~
```text
1. LLM-Predict Cache 的 Hit Rate 高于 Random / Recency。
2. LLM-Predict + Fallback 的回答质量接近 Reactive Retrieval。
3. LLM-Predict + Fallback 的 query-time latency 低于 Reactive Graph Retrieval。
4. Budget 增大时，Recall 和 Hit Rate 上升，但 Wasted Rate 也上升。
5. Oracle Cache 明显优于 LLM-Predict，说明后续 learned predictor 有提升空间。
```

---

# 最终 demo 定位

你可以把 demo 介绍成：

```text
We first implement a lightweight prototype of PreAct-Memory.
It uses a simple temporal heterogeneous memory graph and an LLM-simulated future memory need predictor.
The goal is not to train a new predictor yet, but to verify whether future memory needs are predictable and whether proactive activation can reduce query-time memory retrieval cost under a cache budget.
```

中文就是：

> 第一版先不训练模型，只用 LLM 模拟未来记忆需求预测器；重点验证“未来 memory need 是否可预测”和“提前激活是否能降低 query-time 成本”。
