# Multi-Intent Prepared Cache

## Method

The new ablation is named:

```text
Multi-Intent Prepared + Adaptive Router
```

It is evaluated alongside the original `Pre-query Prepared + Reader`; the old
method is not overwritten.

### Idle-time path

```text
history-only future-intent prediction
  -> transient IntentNode(is_intent=true) per predicted intent
  -> per-intent memory branch
  -> real graph expansion over similar_to / temporal_next / mentions
  -> conservative FactNode materialization
  -> global shared-memory budget scheduler
  -> incremental prefetch order
```

The idle-time builder never receives the actual query. Each intent and memory is
embedded in the same deterministic hashing vector space in the first prototype.
This keeps the routing benchmark dependency-free and reproducible, but is not
intended to be the final semantic encoder.

Deduplication is conservative:

- The same MemoryNode used by multiple heads is physically loaded once.
- An exactly normalized fact gets one shared FactNode with multiple evidence
  references.
- Merely similar memories are not overwritten or treated as the same fact.

### Query-time path

```text
actual query
  -> cosine(query, IntentNode)
  -> prepared readiness + memory semantic-support gate
  -> single_head | merge_heads | partial_repair | native_rag
```

`partial_repair` retains every prepared memory and appends at most
`targeted_repair_top_k` online results. It must not evict an already prefetched
fact to make room for fallback.

## Code

- `multi_intent_cache.py`
  - intent/fact materialization
  - deterministic embeddings and cosine
  - graph-aware per-head branch planning
  - joint cache scheduler
  - adaptive query router
- `graph_store.py`
  - `IntentNode`, `FactNode`, new edge types, and graph cloning
- `pipeline.py`
  - new evaluation method and four routing outcomes
  - proactive/final metrics and complete JSON trace
- `locomo.py`
  - Chinese semantic trace with actual query, golden evidence, intent content,
    candidate memory text, traversal, prefetch order, and final context
- `configs/python_demo.json`
  - `multi_intent_cache` parameters
- `tests/test_multi_intent_cache.py`
  - budget, graph node, routing, partial-repair, pipeline, and trace tests

## Run

The normal LoCoMo script automatically includes the new method:

```bash
cd /home/yanghaotong/premem/code
LIMIT=10 \
OUTPUT_DIR=/home/yanghaotong/premem/code/outputs/multi_intent_router_limit10 \
PYTHON_BIN=/home/yanghaotong/premem/exp/mem/bin/python \
VLLM_MODEL=../Qwen2.5-7B-Instruct \
./run_locomo_eval.sh
```

The configured vLLM endpoint is `http://127.0.0.1:30000/v1`.

## Server smoke result

Run:

```text
LoCoMo time-sliced, first 10 QA samples
Qwen2.5-7B through vLLM:30000
cache_budget=5
retrieval_top_k=5
```

| Method | Recall | Full cover | Official F1 | Mean query retrieval | Fallback |
|---|---:|---:|---:|---:|---:|
| Pre-query Prepared + Reader | 0.95 | 0.90 | 0.159 | 0.013 ms | 0.00 |
| Multi-Intent Prepared + Adaptive Router | 0.95 | 0.90 | 0.159 | 11.745 ms | 0.80 |

Multi-intent proactive-cache-only diagnostics:

- proactive recall: `0.95`
- proactive full cover: `0.90`
- proactive precision: `0.233`
- mean physical cache size: `4.8`
- mean logical branch candidates: `21.3`
- p95 routing/retrieval latency: `16.435 ms`
- route decisions: `1 single`, `1 merge`, `8 partial repair`

## Current interpretation

The implementation is working, but this first configuration is an architecture
prototype rather than a quality win:

1. The multi-head physical cache has the same recall as the single-head prepared
   cache, so the additional heads have not yet improved preparedness.
2. Most branches overlap heavily. High-frequency `mentions` anchors such as
   participant names expand to similar graph regions for every intent.
3. Hashing cosine misses semantic matches such as `career choices` versus
   `fields ... education`, causing 8/10 queries to enter partial repair.
4. Partial repair restores recall after retaining all prepared memories, but
   increases the mean final context from `4.8` to `7.1` without improving QA F1.

The next controlled ablations should therefore be:

1. Replace hashing with a real dense encoder such as BGE-M3 and calibrate the
   router thresholds.
2. Add entity degree/IDF filtering and relation-conditioned typed traversal so
   the intent branches are genuinely different.
3. Improve intent-head diversity for cases where no predicted head covers the
   actual need.
4. Tune fallback only after the above; the query-time verifier is not the first
   bottleneck in this result.
