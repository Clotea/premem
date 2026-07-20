# PreMem 主方法改造 TODO

## 当前已实现的实验分支

已新增 `Multi-Intent Prepared + Adaptive Router`，并保留原
`Pre-query Prepared + Reader` 作为单头基线：

- [x] 将每个预测 intent 物化为临时 `IntentNode`，带
      `is_intent=true`、结构化属性和确定性 embedding。
- [x] 每个 intent 独立产生候选 memory branch，并记录真实
      `similar_to / temporal_next / mentions` 图扩展路径。
- [x] 同一 MemoryNode 在多个 branch 间只加载一次；同一规范化事实复用
      `FactNode`，但不覆盖来源 memory。
- [x] 用共享收益、分支覆盖和冗余惩罚做全局预算调度，物理 cache 不超过
      `cache_budget`。
- [x] query 到达后执行 cosine 路由和 readiness/semantic coverage gate。
- [x] 支持单分支、多分支合并、prepared + reactive 补全、native RAG 四种决策。
- [x] Partial repair 保留全部 prepared memory，只追加在线补全，禁止为了给
      fallback 腾位置而淘汰已经预取的事实。
- [x] JSON 与 Markdown trace 输出实际 query、golden evidence、intent 语义、
      候选 memory 内容、图寻路、prefetch 顺序和最终选择。
- [x] 增加单元测试，覆盖预算约束、Intent/Fact 图节点、相关/无关 query
      路由和 pipeline ablation 接入。

当前 embedding provider 为无外部依赖的 `hashing`，用于先验证系统逻辑和
latency。正式准确率实验仍需增加 dense encoder（如 BGE-M3）并做阈值校准；
不能把 hashing 结果当作最终 intent router 结论。

## 1. 目标

将当前 `Pre-query Prepared + Reader` 改造成真正面向系统延迟的：

> Idle-Adaptive Utility-aware Meta-Path Prefetching

目标不是预测与未来 query 完全一致，而是在不读取实际 query 的条件下，利用
idle time 尽早准备下一条 query 最可能需要、且 miss cost 最高的 memory
working set，从而降低 p95 memory stall 和 p95 TTFT。

主路径必须保持：

```text
history-only preparation
  -> prepared cache
  -> query arrival
  -> direct cache read
  -> Qwen reader
```

实际 query 只能交给 reader，不得用于主方法的 memory retrieval 或 cache
construction。

## 2. 当前基线

当前最新配置：

- LoCoMo time-sliced
- vLLM + Qwen2.5-7B，端口 30000
- `cache_budget=5`
- `retrieval_top_k=5`
- prepared-cache compression enabled
- 主方法不使用 query-time BGE reranker
- 主方法不使用 post-query fallback
- Qwen reader：temperature 0，max tokens 128

最新 20 条结果：

| 指标 | 当前值 |
|---|---:|
| Prepared recall | 82.08% |
| Full-query cover | 65.00% |
| Prepared precision | 22.67% |
| Waste rate | 77.33% |
| QA F1 | 23.84 |
| Query-time cache read | 0.025ms |
| Planning mean | 20.47s |
| Intent prediction mean | 6.37s |
| Graph/gap planning mean | 14.10s |
| Planning requests | 4.9 次/样本 |
| Planning tokens | 约 13k/样本 |

解释限制：

- 20 条全部来自 `locomo_c01`，不能代表全量结果。
- 0.025ms 只是 Python prepared-cache 读取，不是 TTFT。
- memory materialization 目前使用确定性重尾模型，不是真实远程存储。
- 旧的 1986 条全量结果使用旧 reader/budget，且 BGE device 配置错误，不能代表当前最终方法。
- 当前 Reactive Vector 实际是 lexical overlap，不是强 dense-retrieval baseline。

## 3. 核心诊断

### 3.1 系统瓶颈

当前 preparation 串行执行多次 Qwen：

```text
future-intent prediction
  -> meta-path selection
  -> support check
  -> gap generation
  -> evidence binding
  -> compression
  -> prefetch
```

必须等完整 planning 结束后才开始 prefetch。因此在 500ms、1s、2s、5s 和
10s idle window 下，Ready 均为 0。

### 3.2 质量瓶颈

Top-5 compression 按单节点分数选择，容易丢失 multi-hop query 的必要
evidence：

| 配置 | Avg memories | Precision | Recall | Full cover | QA F1 |
|---|---:|---:|---:|---:|---:|
| 未压缩 | 9.8 | 13.79% | 87.08% | 75% | 28.21 |
| Top-5 | 4.9 | 22.67% | 82.08% | 65% | 23.84 |

Compression 减少一半 cache，但损失 5 点 recall、10 点 full-cover 和 4.37
点 QA F1。

### 3.3 当前不应优先优化的模块

- 不优先调 query-time verifier。
- 不把 BGE reranker 放回主路径。
- 不优先优化 future-intent 文本的 exact accuracy。
- 不通过盲目增大 Top-K 掩盖 selection 问题。

## 4. P0：先修正评测协议

### 4.1 Reader / LoCoMo 数据协议（2026-07-20 已完成）

- [x] `Turn` 和 `MemoryNode.metadata` 保留 `session_date_time`。
- [x] Reader context 显式展示每条 memory 的 session 日期。
- [x] 对 `yesterday / last weekday / last week / next month` 做确定性换算，
      并禁止 Reader 用相对时间作答。
- [x] 保留原始 `text / query / blip_caption / img_url` 多模态元数据。
- [x] LoCoMo 官方 token F1 增加 Porter stemming，并保留 category 1/3/5
      的官方分支规则。
- [x] 增加 Mem0-style 和 LongMemEval-strict 两套真实 LLM Judge；旧
      pseudo judge 只作为诊断项。
- [x] 增加 budgeted Oracle、完整 MemoryNode Oracle 和 Raw Evidence
      Oracle，隔离 cache budget、memory writer、Reader 三层误差。
- [x] 增加 `--sample-mode stratified --sample-seed`，并按 category 输出
      F1、Judge、recall、full-cover。
- [x] 本地和服务器 `/home/yanghaotong/premem/exp/mem` 回归测试 9/9。

相同前 10 条的 2026-07-20 回归结果：

| 方法 | 旧 official F1 | 新 official F1 | Recall | Full-cover |
|---|---:|---:|---:|---:|
| Pre-query Prepared + Reader | 15.94 | 58.22 | 95.00 | 90.00 |
| Multi-Intent + Router | 15.94 | 58.22 | 95.00 | 90.00 |
| Oracle（旧 / 新 MemoryNode） | 31.27 | 70.22 | 100.00 | 100.00 |

该回归说明这 10 条上的主要跌分来自 Reader/时间协议，而不是 metapath
召回。仍需用分层 100 条确认；当前 head-10 包含 6 条 temporal，不能作为
论文最终结果。结果文件：
`code/server_results/reader_protocol_v2_limit10/reader_protocol_v2_limit10_official.json`。

### 4.2 外部 prompting baseline（2026-07-20 已完成）

- [x] 接入 `LongMemEval-style Full-History Prompting`：timestamped sessions、
      JSON history、双方消息、`con` 阅读方式，同一 Qwen reader。
- [x] 记录真实 reader API E2E、vLLM prompt/completion tokens 和上下文规模。
- [x] head-10：PreMem official F1 `58.22`，full history `42.71`；
      reader prompt tokens `635.7` 对 `1050.3`。
- [ ] 给 full-history baseline 加相同的确定性时间注释，隔离 temporal
      preprocessing 与 cache selection 的贡献。
- [ ] 在分层 100 条和自然 history prefix 上重跑。

### 4.3 系统延迟协议（仍待完成）

- [ ] 将 `prequery_window_ms` 明确定义为 history commit 到 query arrival 的总
      idle lead time。
- [ ] 记录候选首次产生时间，而不是只记录完整 planning 结束时间。
- [ ] 支持候选增量释放和边规划边预取。
- [ ] query 到达时冻结 cache snapshot，并取消低优先级预取。
- [ ] 报告 idle window sweep：
      `0.5/1/2/5/10/15/20/25/30/60s`。
- [ ] 按 idle window 分桶报告 p50/p95/p99 memory stall、TTFT 和 Ready。
- [ ] 将 planning latency、prefetch latency、query-time latency 分开统计。
- [ ] 用相同 reader、相同 memory budget、相同上下文输入规则重跑所有 baseline。
- [ ] 增加真正的 dense vector baseline，不再将 lexical overlap 称为 Vector
      Retrieval。
- [ ] 保留 Oracle，用于判断 TTFT headroom。
- [ ] 当前配置先随机/分层抽样至少 100 条，再运行全量 1986 条。

涉及文件：

- `code/code/benchmark_latency.py`
- `code/code/pipeline.py`
- `code/code/locomo.py`
- `code/code/analyze_locomo_results.py`

## 5. P1：实现快速真实 Meta-Path Fast Path

### 5.1 图索引

- [ ] 在 `GraphStore` 中增加按 node type 和 edge type 的 adjacency index。
- [ ] 增加 bounded typed-path traversal API。
- [ ] 每轮历史更新时增量维护图，不在每条 query 前重新构图。
- [ ] 为每个候选记录 path provenance、hop count 和 traversal cost。

建议优先支持：

```text
RecentTurn -> Entity -> Memory
RecentTurn -> Segment -> Turn -> Memory
RecentMemory -> temporal_next -> Memory
Entity -> related Memory
Goal/Event -> supporting Memory
Memory -> similar_to -> Memory
```

### 5.2 Fast path

- [ ] 默认关闭主路径中的 LLM meta-path selection。
- [ ] 默认关闭主路径中的 LLM support check。
- [ ] 默认关闭主路径中的 LLM gap generation。
- [ ] 默认关闭主路径中的 LLM evidence binding。
- [ ] 由最近 turn、活跃 entity、未完成 goal/event 产生 traversal seeds。
- [ ] 在 50ms 内输出第一批 Top-3 候选并开始预取。
- [ ] 在 100–500ms 内扩展并更新为 Top-5。
- [ ] LLM gap reasoning 仅作为长 idle window 下的可选 refinement/诊断。

涉及文件：

- `code/code/graph_store.py`
- `code/code/gap_reasoning.py`
- `code/code/pipeline.py`

## 6. P1：Utility-aware Top-5 Selection

每条 memory 的目标分数：

\[
U(v \mid H,W)=
P(v\in Y_q\mid H)
\cdot missCost(v)
\cdot P(T_{plan}+T_{load}(v)\le W)
-\lambda size(v)
-\mu redundancy(v)
\]

- [ ] predictor 输出候选 memory 的 calibrated probability，而不只输出自然语言
      intent。
- [ ] 接入 memory miss cost、byte size 和预计完成时间。
- [ ] 对超过当前 idle deadline 才能完成的候选降权。
- [ ] 对语义重复、同一来源或同一路径的候选施加 redundancy penalty。
- [ ] 在固定 `B=5` 下实现 budgeted/knapsack selection。
- [ ] 增加 latency-weighted 和 byte-weighted ablation。

涉及文件：

- `code/code/predictors.py`
- `code/code/gap_reasoning.py`
- `code/code/utils.py`

## 7. P1：保护 Multi-hop Evidence Bundle

单节点相关度不足以优化 full-query hit。需要先按预测子目标、实体、事件或
meta-path 构造 evidence bundle。

目标函数增加 bundle coverage：

\[
Score(C)=
\sum_{v\in C}U(v)
+\gamma\sum_g \mathbf{1}[g\subseteq C]
\]

- [ ] 定义 bundle：同一预测子目标所需的 supporting/temporal/causal memories。
- [ ] 选择时奖励完整 bundle，而不是只奖励高分单节点。
- [ ] 为不同预测子目标保留最小 coverage。
- [ ] 对 multi-hop gold evidence 单独报告 recall 和 full-cover。
- [ ] 对 compression loss 输出具体丢失节点及其 path/bundle。

首轮目标：

- Recall：82.08% -> 至少 85%
- Full-cover：65% -> 至少 75%
- QA F1：23.84 -> 至少恢复到 27+

## 8. P2：Anytime / Cascaded Predictor

- [ ] Stage 0：纯 graph fast path，目标 p95 小于 50ms。
- [ ] Stage 1：轻量 intent predictor 更新 memory probability。
- [ ] Stage 2：只有 idle time 足够时才运行 Qwen refinement。
- [ ] 每个 stage 都能产生可用 cache snapshot，不等待后续 stage。
- [ ] 新候选优先级高于旧候选时，支持增量替换和取消。
- [ ] query 到达后禁止继续占用在线关键资源。
- [ ] 记录各 stage 的 marginal Ready gain、token cost 和 compute cost。

涉及文件：

- `code/code/predictors.py`
- `code/code/pipeline.py`
- `code/code/benchmark_latency.py`

## 9. P2：Query-time 主路径

继续保持：

```text
query arrival
  -> prepared cache snapshot
  -> reader
```

- [ ] 不使用 BGE reranker作为主方法必要组件。
- [ ] 如需安全判断，只增加 O(K) 的轻量 cache/query gate，目标 p95 小于 1ms。
- [ ] fallback 作为单独 deployment variant 和 ablation，不混入论文主方法。
- [ ] query 到达后立即取消未完成的低优先级 prefetch。
- [ ] 单独测资源竞争下 vLLM queue/prefill 是否恶化。

## 10. P2：真实 Memory Backend

- [ ] 用真实文件、对象存储或 RPC 替换 `MemoryCostModel`。
- [ ] 记录 node/byte/latency-weighted hit rate。
- [ ] 记录实际 prefetch bytes、partial transfer 和 cancellation waste。
- [ ] 测 cache lookup、remote load、prompt build、prefill 和 decode 分解。
- [ ] 测单用户与并发负载下的 p50/p95/p99。
- [ ] 验证 background predictor 是否与在线 Qwen reader 争用 GPU。

## 11. 必做 Ablation

- [ ] Reactive dense vector retrieval。
- [ ] Reactive typed-graph retrieval。
- [ ] Recency Top-5，直接给相同 reader，不经过额外 lexical verifier。
- [ ] Fast meta-path only。
- [ ] Qwen intent only。
- [ ] Fast meta-path + Qwen refinement。
- [ ] Relevance-only selection。
- [ ] Utility-aware selection。
- [ ] Utility + bundle-aware selection。
- [ ] Top-3/Top-5/Top-10 budget sweep。
- [ ] 有/无 compression。
- [ ] 有/无 query-time BGE，仅作为 ablation。
- [ ] Oracle Prefetch。

## 12. 最终验收标准

### 系统指标

- [ ] Fast-path first candidate p95 <= 50ms。
- [ ] Fast-path Top-5 decision p95 <= 100ms。
- [ ] Ready@5@500ms >= 70%。
- [ ] p95 memory stall 相比 Reactive 至少下降 40%。
- [ ] p95 TTFT 相比 Reactive 至少下降 20%。
- [ ] TPOT 基本不变。
- [ ] prediction + graph 决策成本低于节省 latency 的 10%，或明确作为
      background compute 单独报告。

### 质量与成本

- [ ] Evidence recall >= 85%。
- [ ] Full-query hit >= 75%。
- [ ] QA F1 相比无压缩版本下降不超过 1 点。
- [ ] Waste rate 首轮降到 40% 以下，最终目标 25% 以下。
- [ ] 报告 cache bytes、prefetch bytes、token cost 和 GPU/CPU 占用。

### 实验完整性

- [ ] 当前配置完成至少 100 条随机/分层样本。
- [ ] 当前配置完成全量 1986 条。
- [ ] 所有主比较使用相同 reader 和相同输入预算。
- [ ] 不使用旧版 BGE device 配置错误的全量结果支撑最终结论。
- [ ] 不将 Python list lookup latency 表述为完整 TTFT。

## 13. 推荐执行顺序

1. 修公平 baseline 和 idle-window 评测。
2. 实现 typed adjacency 和真实 meta-path traversal。
3. 关闭四次 LLM graph/gap 调用，建立 fast-path baseline。
4. 实现增量候选发布和边规划边预取。
5. 实现 utility-aware Top-5。
6. 实现 bundle-aware multi-hop coverage。
7. 将 Qwen 降级为可选 refinement。
8. 跑 100 条分层实验。
9. 接入真实 memory backend 和资源竞争测试。
10. 确认指标后再跑全量与论文 ablation。

## 14. 暂不做

- 不优先调 verifier prompt 或 BGE 权重。
- 不将 query-time BGE 加回论文主路径。
- 不先增加更大的 LLM。
- 不只优化 future-query 文本预测准确率。
- 不只报告平均 latency 或 retrieval latency。
- 不在 baseline 输入 memory 数不一致时比较 QA F1。
