# PreMem 延迟基准

这个基准回答两个问题：

1. 当前 memory 层是否有足够大的 TTFT 优化空间？
2. 图预测缓存能把多少在线 memory latency 移到 query 到达之前？

它在同一批 LoCoMo time-sliced 样本上比较三种方法：

- `Reactive`：query 到达后才检索并加载需要的 memory。
- `Predictive Graph Cache`：只用历史做 intent prediction、graph planning 和 prepared-cache compression；query 到达时先读已经完成的预取，miss 再回源。
- `Oracle Prefetch`：提前知道真实 working set，用来估计系统可达到的 latency 上限，不是可部署方法。

默认 `cache_budget=5`、`retrieval_top_k=5`，与
`configs/python_demo.json` 和现有 LoCoMo 脚本一致。

## 实现方式

每个样本先从历史构造 memory graph，然后在 query 到达前执行当前项目的：

```text
history
  -> vLLM/Qwen intent predictor
  -> graph gap reasoning / candidate planning
  -> prepared cache compression
  -> asynchronous memory prefetch
```

query 到达后计时：

```text
query arrival
  -> cache lookup
  -> miss 时的 reactive retrieval / materialization
  -> prompt construction
  -> vLLM streaming first token
  -> generation complete
```

代码中的定义是：

```text
memory_stall = memory_ready - query_arrival
TTFT = memory_stall + prompt_build + vLLM_streaming_TTFT
E2E = memory_stall + prompt_build + vLLM_streaming_E2E
TPOT = (generation_E2E - generation_TTFT) / (completion_tokens - 1)
```

三个方法给 reader 的 working-set context 相同，因此 latency 对比不会把
retrieval quality 混进来；默认 `--working-set gold` 专门用于测 headroom。
需要更接近部署行为时可以改成 `WORKING_SET=reactive`。

当前项目没有真实远程 memory store，所以 benchmark 用确定性的重尾
`MemoryCostModel` 模拟 materialization，并真正等待相应时长。这样可以先验证
系统设计、长尾指标和 Oracle 上限，但论文最终数值必须把
`MemoryCostModel.load_ms()` 替换成真实对象存储/RPC 的读取计时。
预取由固定 worker 池调度，默认并发度为 4，避免无限并发带来的虚假高 Ready。

## 服务器启动

先在一个终端启动 OpenAI-compatible vLLM。模型名必须与 benchmark 请求中的
`VLLM_MODEL` 完全一致：

```bash
cd /home/yanghaotong/premem/code/code

CUDA_VISIBLE_DEVICES=0 \
/home/yanghaotong/premem/exp/mem/bin/python \
  -m vllm.entrypoints.openai.api_server \
  --model ../Qwen2.5-7B-Instruct \
  --served-model-name ../Qwen2.5-7B-Instruct \
  --host 127.0.0.1 \
  --port 30000
```

确认服务可用：

```bash
curl http://127.0.0.1:30000/v1/models
```

再开一个终端运行 20 条冒烟实验：

```bash
cd /home/yanghaotong/premem/code
chmod +x run_latency_benchmark.sh
./run_latency_benchmark.sh
```

正式 p95 至少建议 100 条：

```bash
LIMIT=100 ./run_latency_benchmark.sh
```

全量：

```bash
LIMIT=0 ./run_latency_benchmark.sh
```

脚本默认同时计算 `Ready@B@100ms/250ms/500ms/1000ms/2000ms`，不增加
Qwen 调用。可以覆盖窗口和 budget：

```bash
LIMIT=100 \
CACHE_BUDGET=5 \
PREQUERY_WINDOW_MS=500 \
READY_WINDOWS_MS=100,250,500,1000,2000 \
PREFETCH_CONCURRENCY=4 \
./run_latency_benchmark.sh
```

budget sweep 仍需分别运行，因为它会改变实际 prepared cache：

```bash
CACHE_BUDGET=3 LIMIT=100 ./run_latency_benchmark.sh
CACHE_BUDGET=5 LIMIT=100 ./run_latency_benchmark.sh
CACHE_BUDGET=10 LIMIT=100 ./run_latency_benchmark.sh
```

输出写到 `code/outputs/`：

- `.json`：完整配置、每个样本、三种方法、分位数和 headroom。
- `.txt`：终端日志及汇总表。

## 输出指标

主结果：

- p50/p95/p99 memory stall
- p50/p95/p99 TTFT、E2E、TPOT
- LoCoMo official F1、BLEU-1、ROUGE-L
- Oracle 对 p95 memory stall 和 p95 TTFT 的最大可改善比例
- Predictive Graph Cache 的 hidden latency ratio

机制指标：

- `Ready@B@W` 以及多窗口 Ready 曲线
- utility/latency-weighted recall
- node、byte、latency-weighted cache hit rate
- full-query hit rate

成本指标：

- attempted waste rate 和 completed waste rate
- prefetch bytes、completion rate、cache occupancy/pollution
- intent prediction、graph planning latency和 token 数
- predictor working-set recall 和 Brier score

优先先看 JSON 的 `headroom`。如果
`oracle_max_ttft_reduction < 0.15`，继续优化 predictor 或 meta-path 的收益上限
已经很低，应先改变缓存对象、memory store 或 prefill 占比。若 Oracle 很强但
Predictive 很弱，再看：

- Ready/working-set recall 低：优先改 intent prediction 或 graph/meta-path。
- recall 高但 utility recall 低：优先改 cost-aware graph ranking。
- Ready 高但 stall 仍高：优先检查 planning overrun、prefetch completion 和资源竞争。
- full-query hit 低而 node hit 高：说明 multi-hop evidence 覆盖不完整，不能只优化平均 Recall@5。

`resource_competition_modeled=false` 会明确写入报告。当前脚本不把并发预取对
vLLM queue/prefill 的 GPU、CPU、网络争用伪装成已测结果；这需要在真实存储接入后
补充并发负载和 p99 实验。
