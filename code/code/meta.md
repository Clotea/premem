## 工作总结

我的工作关注长期记忆智能体中的一个问题：现有 memory 方法大多在用户 query 到来之后才进行 memory retrieval、graph traversal 或 context construction，因此 memory activation 往往位于 query-time critical path 上。实验统计也说明，不同方法虽然瓶颈位置不同，但都可能带来显著 memory overhead：SimpleMem 和 SimGRAG 主要慢在 query-time memory retrieval / graph activation，而 A-MEM 虽然 retrieval 很快，但 memory construction / organization 阶段成本较高。

因此，我的核心问题不是简单地问“如何检索相关 memory”，而是问：

**在下一轮用户 query 到来之前，agent 能否根据预测到的 future intent，提前检查长期记忆是否足够支撑未来回答，并修复其中的缺口？**

具体来说，我的方法把预测到的 future intent 接入长期异构 memory graph，形成 IntentNode；然后通过 meta-path-guided exploration 激活相关 memory subgraph，检查其中的支撑链是否完整。如果发现缺少 evidence、存在冲突、信息过时、用户目标缺失或相关 memory 覆盖不足，就生成 GapNode，并在 idle-time 中对高价值 gap 进行修复。修复后的 evidence 会被绑定回对应的 GapNode、ClaimNode 或 MemoryNode，最终构造一个可在下一轮 query 到来时验证和使用的 working context。

这个方法的关键区别是：

* 不是普通 query-time retrieval；
* 不是单纯 proactive search；
* 不是传统 graph memory；
* 而是 **future-intent-conditioned memory gap reasoning**。

现有方法通常解决的是：

```text
known query → retrieve memory → construct context → answer
```

而我的方法解决的是：

```text
predicted future intent → diagnose memory support gap → repair gap → prepare verifiable context
```

也就是说，我不是等 query 来了以后再找 memory，而是在 query 来之前判断“未来回答需要的记忆支撑链是否已经准备好”。

## 方法流程

整体流程如下：

```text
当前对话结束
→ 预测 future intent
→ 创建 IntentNode
→ 选择相关 meta-path
→ 激活 memory subgraph
→ 检查 claim / evidence / goal / conflict 支撑链
→  选择相关 meta-path
→ 激活 memory subgraph
→ 检查 claim / evidence / goal发现 memory gap
→ 生成 GapNode
→ 对 gap 排序
→ 检索或补充 evidence
→ evidence binding
→ 构造 working context
→ 下一轮 query 到来后 verifier 决定 use / reject
```

## 主要创新点

第一，提出了 **Future-Intent-Conditioned Memory Gap Reasoning** 问题。现有方法通常在 query-time 检索 memory，而我的问题设置在 query 到来之前，目标是提前判断长期记忆图是否足以支撑未来可能出现的需求。

第二，引入了 **GapNode** 作为结构化缺口表示。memory gap 不只是“没搜到信息”，而是指在 future intent 条件下，当前 memory graph 中缺少支撑未来回答的关键结构，例如 evidence gap、coverage gap、conflict gap、freshness gap 或 personalization gap。

第三，使用 **meta-path-guided graph exploration**。系统不是无目标遍历 graph，而是根据 future intent 选择语义路径，例如：

```text
Intent → UserGoal → Idea
Intent → Paper → Claim → Evidence
Claim → contradicts → Claim
Claim → Gap → Evidence
```

这样可以显式检查未来回答所依赖的支撑链。

第四，提出 **pre-query gap repair**。系统将一部分 memory reasoning、evidence retrieval 和 context preparation 从 query-time 转移到 idle-time，减少用户真正提问时的在线负担。

第五，加入 **verifier**。下一轮 query 到来后，系统不会盲目使用提前准备的 context，而是判断 prepared context 是否与当前 query 匹配，再决定 use、partial use、reject 或 fallback。

## 和现有方法的区别

A-MEM 主要做 memory extraction 和 memory organization，把对话转成 structured memory notes，并建立 memory links。它解决的是“如何组织长期记忆”。

SimGRAG 主要做 graph / subgraph retrieval。它解决的是“query 到来后如何从图中找相关子图”。

LightMem / SimpleMem 这类文本方法主要做 compressed / flat textual memory retrieval。它们解决的是“如何用文本记忆降低冗余并检索相关内容”。

我的方法关注的是另一个问题：

**未来 query 尚未到来时，如何判断 memory 是否已经 ready，以及哪里不 ready。**

所以我的方法不是替代 retrieval，而是在 retrieval 之前增加一个 memory readiness diagnosis / gap repair 阶段。

## 实验动机

从已有 profiling 结果看，现有方法的 memory overhead 具有阶段差异：

* SimpleMem：query-time retrieval 占比高，说明 adaptive memory retrieval 成本较重；
* SimGRAG：graph retrieval / subgraph activation 占比高，说明 graph-based memory activation 放在 query-time 会带来明显延迟；
* A-MEM：retrieval 很快，但 memory construction / organization 阶段较慢，说明它把成本转移到了 write-side。

这说明长期 memory 系统的问题不只是“能不能召回 memory”，还包括：

```text
memory 什么时候处理？
memory 支撑链是否完整？
retrieved context 是否真正有用？
哪些 memory gap 值得提前修？
```

## 一句话定位

我的工作研究的是：

**在长期记忆智能体中，如何基于预测到的 future intent，在 query 到来之前主动诊断和修复 memory graph 中的支撑缺口，从而构造可验证、低冗余、可用于未来回答的 working context。**


可以，**第一版完全可以先用 LLM 实现**。而且我建议你就这么做。不要一开始上 RL、训练 scorer、复杂 graph executor。你现在最重要的是先证明这个问题设定能跑通：

```text
future intent → memory graph support checking → gap detection → gap repair → verifier
```

但要注意：**不是让一个 LLM 大 prompt 从头到尾瞎跑**，而是把每个阶段都做成独立 LLM module，并强制输出 JSON。

## 第一版可以全 LLM 的部分

可以先用 LLM 做这些：

| 模块                       | 第一版怎么做                                        |
| ------------------------ | --------------------------------------------- |
| Future intent prediction | LLM 根据当前对话预测下一步可能需求                           |
| Meta-path selection      | LLM 从固定 meta-path library 里选路径                |
| Support checking         | LLM 判断当前 memory subgraph 是否足够支撑 future intent |
| Gap detection            | LLM 生成 GapNode                                |
| Gap ranking              | LLM 给 gap 打分                                  |
| Repair query generation  | LLM 为每个 gap 生成检索 query                        |
| Evidence binding         | LLM 判断 evidence 应该绑定到哪个 GapNode / ClaimNode   |
| Verifier                 | LLM 判断 prepared context 是否适合当前 query          |

也就是说，第一版可以是：

```text
LLM as planner
LLM as checker
LLM as gap generator
LLM as verifier
```

## 但不要全都“自由生成”

你要加结构约束。比如每个模块都输出 JSON：

```json
{
  "future_intent": "...",
  "confidence": 0.82,
  "required_support": [
    "user goal",
    "method definition",
    "related work distinction",
    "evidence"
  ]
}
```

GapNode 也固定格式：

```json
{
  "gap_id": "gap_001",
  "gap_type": "evidence_gap",
  "related_claim": "ProAct does not perform memory graph gap diagnosis",
  "missing_support": "Need evidence showing ProAct only performs proactive search/report generation",
  "priority": 0.91,
  "repair_query": "ProAct proactive agent memory future need search report"
}
```

Evidence binding 固定格式：

```json
{
  "evidence_id": "ev_003",
  "bind_to": "gap_001",
  "binding_type": "supports",
  "reason": "This evidence shows ProAct predicts future needs but does not diagnose memory support gaps."
}
```

这样后面你才能统计：

```text
Gap Recall
Gap Precision
Evidence Binding Accuracy
Wrong Context Usage Rate
Unsupported Claim Rate
```

## 哪些部分不要交给 LLM 随便做

这几个最好用规则 / 程序控制：

| 部分                       | 为什么                       |
| ------------------------ | ------------------------- |
| Node / Edge ID 管理        | 要可复现，不能让 LLM 乱编号          |
| Meta-path execution      | LLM 选路径，程序执行路径            |
| Retrieval top-k          | 程序检索，不要让 LLM 自称检索到了       |
| Timing / token 统计        | 必须程序记录                    |
| JSON log                 | 程序写入                      |
| Evidence source tracking | 必须保留 source id / chunk id |

所以最合理的第一版是：

```text
LLM 负责语义判断
程序负责执行、记录、检索、统计
```

## 第一版 MVP 架构

你可以这样做：

```text
1. Memory Graph
   先用 JSON / NetworkX 存节点和边，不用图数据库。

2. Intent Predictor
   LLM 输入当前 dialogue state，输出 future intent。

3. Meta-path Selector
   LLM 从固定路径库中选择 2-4 条路径。

4. Graph Executor
   程序沿路径取出相关节点。

5. Support Checker
   LLM 判断支撑链是否完整。

6. Gap Generator
   LLM 输出 GapNode。

7. Repair Retriever
   用 BM25 / embedding / web / paper chunks 检索 evidence。

8. Evidence Binder
   LLM 判断 evidence 绑定到哪个 gap。

9. Context Builder
   程序构造 prepared context。

10. Query-time Verifier
   LLM 判断当前 query 是否应该使用 prepared context。
```

## 你第一版可以不用训练

不需要训练：

```text
不训练 intent predictor
不训练 gap scorer
不训练 verifier
不训练 retriever
不训练 graph policy
```

全部用 prompt + JSON schema 就够。

后面论文里可以说：

> We instantiate the framework with LLM-based modules for intent prediction, support checking, gap generation, evidence binding, and verification. This design allows us to evaluate the problem formulation without introducing additional trainable components.

中文就是：

> 我们第一版使用 LLM-based modules 实现各个语义决策阶段，以避免引入额外训练变量，重点评估问题设定和 pre-query gap reasoning 的有效性。

## 但你要做 ablation

否则别人会说：“这不就是多调几次 LLM？”

你要消融：

| Ablation              | 目的                           |
| --------------------- | ---------------------------- |
| w/o future intent     | 证明预测未来意图有用                   |
| w/o meta-path         | 证明结构化路径探索有用                  |
| w/o gap repair        | 证明修 gap 有用                   |
| w/o evidence binding  | 证明 evidence 绑定有用             |
| w/o verifier          | 证明 prepared context 不能盲用     |
| query-time gap repair | 证明 pre-query 比 query-time 更好 |

## 最终判断

可以先全部用 LLM 实现，而且这是最稳的原型路线。

但你的表达要注意：

> 不是“我们提出一个训练好的新模型”，而是“我们提出一个 memory gap reasoning framework，并用 LLM-based modules 实例化”。

最短结论：

**可以。第一版就用 LLM 做 intent prediction、support checking、gap detection、gap repair decision 和 verifier；但 graph execution、retrieval、ID 管理、日志和指标统计必须程序化。这样既能快速验证 idea，又不会变成不可复现的大 prompt。**


可以。你第一版的工作流可以写成：**LLM 做语义判断，程序做执行、检索、存储和日志**。这个设定和 agent memory 的基本生命周期是一致的：已有综述把 memory system 拆成 formation、evolution、retrieval 三类操作；你的工作相当于在 retrieval 之前增加一个 pre-query diagnosis / repair 阶段。

## 1. 整体工作流程

```text
当前对话结束
→ LLM 预测 future intent
→ 程序创建 IntentNode
→ LLM 选择 meta-path
→ 程序执行 graph traversal
→ LLM 检查支撑链是否完整
→ LLM 生成 GapNode
→ LLM 给 gap 排序
→ 程序检索 evidence
→ LLM 绑定 evidence 到 GapNode / ClaimNode
→ 程序构造 prepared context
→ 下一轮 query 到来
→ LLM verifier 判断 use / reject
→ 生成最终答案
```

核心不是：

```text
query 来了 → 检索 memory → 回答
```

而是：

```text
query 还没来 → 预测未来需求 → 检查 memory 是否 ready → 修 gap → 等 query 来验证使用
```

---

## 2. 模块级流程

### Step 1：Future Intent Prediction

输入当前对话历史：

```text
用户一直在讨论：
- 我的 idea 是否像 ProAct
- graph memory 是否有 novelty
- baseline 怎么选
- memory retrieval 是否慢
```

LLM 输出：

```json
{
  "future_intent": "用户可能会问如何说明自己的方法区别于 ProAct 和已有 graph memory。",
  "possible_user_query": "这不就是 ProAct + graph memory 吗？",
  "confidence": 0.86,
  "required_support": [
    "用户自己方法的定义",
    "ProAct 的机制",
    "graph memory 的机制",
    "用户方法和二者的区别",
    "可引用的 evidence"
  ]
}
```

程序把这个写成：

```json
{
  "node_id": "intent_001",
  "node_type": "IntentNode",
  "content": "distinguish our method from ProAct and graph memory"
}
```

---

### Step 2：Meta-path Selection

LLM 从固定路径库里选路径，不让它自由编路径。

候选路径：

```text
P1: Intent → UserGoal → Idea
P2: Intent → Paper → Claim → Evidence
P3: Intent → Topic → Paper → Claim
P4: Claim → Evidence
P5: Claim → contradicts → Claim
P6: Claim → Gap
```

LLM 输出：

```json
{
  "selected_paths": [
    {
      "path_id": "P1",
      "reason": "需要找到用户自己的方法定义和研究目标"
    },
    {
      "path_id": "P2",
      "reason": "需要检查 ProAct 和 graph memory 的 claim 是否有 evidence"
    },
    {
      "path_id": "P5",
      "reason": "需要检查已有记忆中是否存在冲突判断"
    }
  ]
}
```

程序执行路径，不让 LLM 假装检索。

---

### Step 3：Support Checking

程序返回 activated subgraph：

```json
{
  "activated_nodes": [
    {
      "id": "idea_001",
      "type": "IdeaNode",
      "content": "future-intent-conditioned memory gap reasoning"
    },
    {
      "id": "paper_001",
      "type": "PaperNode",
      "content": "ProAct"
    },
    {
      "id": "claim_001",
      "type": "ClaimNode",
      "content": "ProAct predicts future user needs and prepares information"
    },
    {
      "id": "claim_002",
      "type": "ClaimNode",
      "content": "Our method diagnoses support gaps in a memory graph before query-time"
    }
  ]
}
```

LLM 检查：

```json
{
  "support_status": "insufficient",
  "missing_support": [
    "缺少 ProAct 不做 memory graph gap diagnosis 的 evidence",
    "缺少 graph memory 主要是 query-time retrieval 的 evidence",
    "缺少用户方法中 GapNode / evidence binding 的清晰定义"
  ]
}
```

---

### Step 4：GapNode Generation

LLM 生成结构化 gap：

```json
[
  {
    "gap_id": "gap_001",
    "gap_type": "evidence_gap",
    "related_claim": "ProAct does not perform memory graph gap diagnosis",
    "missing_support": "Need evidence showing ProAct focuses on proactive need prediction/search rather than graph support-chain diagnosis.",
    "priority": 0.93
  },
  {
    "gap_id": "gap_002",
    "gap_type": "coverage_gap",
    "related_claim": "Existing graph memory mainly performs query-time retrieval",
    "missing_support": "Need representative graph memory baselines and their query-time retrieval workflow.",
    "priority": 0.88
  },
  {
    "gap_id": "gap_003",
    "gap_type": "definition_gap",
    "related_claim": "Our method performs memory gap reasoning",
    "missing_support": "Need a concise definition of GapNode and support-chain readiness.",
    "priority": 0.81
  }
]
```

---

### Step 5：Gap Repair

LLM 先生成 repair query：

```json
[
  {
    "gap_id": "gap_001",
    "repair_query": "ProAct proactive agent future need prediction search report memory"
  },
  {
    "gap_id": "gap_002",
    "repair_query": "graph memory query time retrieval memory graph long term conversational memory"
  }
]
```

程序检索相关 paper chunk / memory chunk。

检索到 evidence 后，LLM 做 evidence binding：

```json
[
  {
    "evidence_id": "ev_001",
    "bind_to": "gap_001",
    "binding_type": "supports",
    "reason": "This evidence describes ProAct as future-need prediction and proactive preparation, not memory graph support-chain diagnosis."
  },
  {
    "evidence_id": "ev_002",
    "bind_to": "gap_002",
    "binding_type": "supports",
    "reason": "This evidence shows graph memory methods retrieve graph context after receiving the query."
  }
]
```

---

### Step 6：Prepared Context Construction

程序构造可用 context package：

```json
{
  "context_package_id": "ctx_001",
  "target_intent": "distinguish our method from ProAct and graph memory",
  "summary": "Our method differs from ProAct and graph memory because it performs pre-query memory support diagnosis and gap repair.",
  "usable_claims": [
    {
      "claim": "ProAct predicts future needs and prepares information.",
      "evidence": ["ev_001"]
    },
    {
      "claim": "Graph memory methods usually retrieve graph context after query arrival.",
      "evidence": ["ev_002"]
    },
    {
      "claim": "Our method creates GapNode and repairs missing support before the next query.",
      "evidence": ["gap_001", "gap_002", "gap_003"]
    }
  ],
  "risk": "Use only if the next query asks about novelty, ProAct, graph memory, or method distinction."
}
```

---

### Step 7：Query-time Verifier

下一轮用户真的问：

```text
这不就是 ProAct + graph memory 吗？
```

LLM verifier 判断：

```json
{
  "decision": "use",
  "matched_intent": "distinguish our method from ProAct and graph memory",
  "confidence": 0.91,
  "reason": "The current query directly matches the prepared context package."
}
```

如果用户问的是：

```text
帮我写个 GitHub README
```

verifier 应该输出：

```json
{
  "decision": "reject",
  "confidence": 0.95,
  "reason": "The prepared context is about method novelty, not README writing."
}
```

---

## 3. 完整例子：论文 novelty 场景

### 当前对话

```text
用户：我这个 idea 会不会像 ProAct？
用户：graph memory 已经有了，我还有 novelty 吗？
用户：我想投 ACL main，怎么说区别？
```

### 系统预测

```text
用户下一步很可能会问：
“这不就是 ProAct + graph memory 吗？”
```

### 系统提前做的事

```text
1. 把该 future intent 写入 IntentNode
2. 沿 Intent → UserGoal → Idea 找到用户自己的方法定义
3. 沿 Intent → Paper → Claim → Evidence 找 ProAct 的证据
4. 沿 Intent → Topic → Paper → Claim 找 graph memory 的边界
5. 检查 claim 是否有 evidence
6. 发现 ProAct distinction evidence gap
7. 发现 graph memory boundary gap
8. 检索并绑定 evidence
9. 生成 prepared context
```

### 用户下一轮真的问

```text
这不就是 ProAct + graph memory 吗？
```

### 系统回答

```text
不是单纯的 ProAct + graph memory。

ProAct 的核心是预测用户未来可能需要什么，然后提前搜索或准备信息；graph memory 的核心是 query 到来后，在图结构中检索相关记忆或子图。

你的方法关注的是另一个问题：在 query 还没来之前，系统根据 predicted future intent 检查长期 memory graph 是否足以支撑未来回答。如果发现 claim 缺 evidence、memory 覆盖不足、信息冲突或用户目标缺失，就生成 GapNode，并在 idle-time 里修复这些 gap。

所以区别是：
ProAct 提前找信息；
Graph memory 事后找记忆；
你的方法提前检查并修复未来回答所依赖的 memory support chain。
```

---

## 4. 一句话版

你的工作流程可以概括成：

```text
Predict future intent → Ground it into memory graph → Explore support paths → Detect missing support → Generate GapNodes → Repair high-value gaps → Bind evidence → Build verifiable context → Verify at query-time.
```

中文：

```text
预测未来意图 → 接入记忆图 → 探索支撑路径 → 发现支撑缺口 → 生成 GapNode → 修复高价值 gap → 绑定 evidence → 构造可验证上下文 → 下一轮 query 到来后验证使用。
```
