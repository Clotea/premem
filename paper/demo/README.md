# PreMem pre-query prediction demo

This is a deliberately small prototype for validating the task definition:

1. Freeze the next user query.
2. Give the predictor only past conversation and the current heterogeneous
   memory graph.
3. Predict future intents and seed memory IDs during pre-query idle time.
4. Propagate scores over typed graph edges and select Top-K memory.
5. Reveal the hidden query and evaluate prediction and readiness.

The graph propagation in this demo is heuristic, not the final learned HGT
model. It provides a transparent baseline that can later be replaced without
changing the evaluation protocol.

## Run with the local Ollama model

```bash
cd /Users/yanghaotong/CODING/premem/paper/demo
python3 premem_demo.py \
  --provider ollama \
  --model qwen3.5:2b \
  --top-k 3 \
  --pre-query-window-ms 15000
```

## Run without any model service

```bash
python3 premem_demo.py --provider heuristic
```

## Tests

```bash
python3 -m unittest -v
```

The key output fields are:

- `future_need_predictions`: hypotheses made before the query is visible.
- `ranked_memory`: Top-K memory candidates after typed-edge propagation.
- `ready_at_query`: candidates whose simulated load fits the remaining
  pre-query window.
- `recall_at_k`: fraction of gold memory predicted.
- `ready_recall_at_query`: fraction of gold memory ready before query arrival.
- `utility_weighted_recall`: recall weighted by miss/load latency.

The example scenario is fictional. To test a new trace, copy `scenario.json`
and preserve the same schema. The `hidden_next_query` and `gold_memory_ids`
fields are evaluation-only and are never passed to the predictor.
