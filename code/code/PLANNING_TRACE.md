# Graph / Meta-path Planning Trace

This trace runner executes one LoCoMo time-sliced sample through the same
memory writer, graph builder, vLLM predictor, gap-reasoning planner, and cache
compression used by the main pipeline.

## Run on server3

```bash
cd /home/yanghaotong/premem/code

SAMPLE_ID=locomo_c01_tsqa_016 \
CACHE_BUDGET=5 \
./run_planning_trace.sh
```

Optional arguments can be appended to the script:

```bash
./run_planning_trace.sh \
  --sample-id locomo_c01_tsqa_016 \
  --trace-ranking-limit 20
```

The default output directory is:

```text
/home/yanghaotong/premem/code/outputs/planning_trace_<sample-id>/
```

It contains:

- `planning_trace.json`: complete machine-readable graph and planning trace.
- `planning_trace.html`: detailed interactive report with graph, route, latency,
  and compression views.
- `planning_trace_compact.html`: compact interactive summary.
- `planning_trace.md`: human-readable summary.

## Recorded graph evolution

The final graph is replayed as five cumulative snapshots:

1. `history_structure`: TurnNode, SegmentNode, and turn-to-segment edges.
2. `memory_attachment`: MemoryNode, `derived_from`, and memory `belongs_to`.
3. `entity_linking`: EntityNode and `mentions`.
4. `temporal_chain`: `temporal_next`.
5. `similarity_links`: `similar_to`.

Each snapshot records cumulative and newly added node/edge IDs plus per-type
counts.

## Recorded planning stages

The planner records wall-clock latency and stage data for:

1. intent materialization;
2. meta-path selection;
3. meta-path execution;
4. support checking;
5. gap generation;
6. gap repair;
7. evidence binding;
8. candidate merge;
9. prepared-cache compression.

The vLLM wrapper separately records each request's stage, latency, prompt size,
token usage, and response preview.

## Important current semantics

The current P1-P6 meta-path implementation is not physical hop-by-hop graph
traversal. A selected path applies a path-specific bonus while ranking all
MemoryNodes. The code selects top-k memories and only then follows incident
physical graph edges to create a local subgraph.

The trace makes this explicit with three route operations:

```text
intent_seed                                  uses_graph_edges=false
path_conditioned_global_memory_ranking       uses_graph_edges=false
incident_edge_local_subgraph_expansion       uses_graph_edges=true
```

Therefore, the existing results measure path-conditioned memory ranking plus
local graph expansion. They do not yet establish a novel typed meta-path
traversal algorithm.
