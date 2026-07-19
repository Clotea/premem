# PreAct-Memory Lite Demo

This is a first runnable prototype based on `demo.md`.

The Python implementation now runs this nine-stage pipeline:

1. Build a heterogeneous memory graph.
2. Generate query-independent future-need hypotheses with an LLM or heuristic fallback.
3. Ground hypotheses to the memory graph with embeddings or an optional LLM reranker.
4. Activate grounded memory nodes and local subgraphs.
5. Detect memory gaps with score-based or optional LLM judgment.
6. Search web and paper sources for uncovered needs when external search is enabled.
7. Build a provenance-carrying working context package.
8. Verify package usability after the query arrives.
9. Fall back to reactive vector or graph retrieval when the package is insufficient.

## Run

From the repository root:

```bash
node preact_demo/javascript/run_demo.js
```

If your shell allows npm scripts, this also works:

```bash
npm.cmd run demo
```

PowerShell may block `npm.ps1`, so `node preact_demo/javascript/run_demo.js` is the most reliable command on Windows.

## Python Run With vLLM-Compatible Predictor

The Python implementation follows the same pipeline and adds a vLLM-compatible LLM service adapter plus a replaceable prediction interface.

Run the built-in demo data:

```bash
python preact_demo/code/main.py --dataset demo
```

Run a small LoCoMo smoke test:

```bash
python preact_demo/code/main.py --dataset locomo --limit 10
```

Or use the dedicated LoCoMo entrypoint:

```bash
python preact_demo/code/locomo.py --limit 10
```

By default, `preact_demo/code/configs/python_demo.json` sets `predictor` to `vllm`. The client calls an OpenAI-compatible vLLM endpoint:

```text
http://127.0.0.1:8000/v1/chat/completions
```

The same config is also available as YAML at `preact_demo/code/configs/python_demo.yaml`. If the service is unavailable, the predictor falls back to the deterministic heuristic predictor so the pipeline can still be tested locally. Override the endpoint/model with CLI flags, host/port flags, or environment variables:

```bash
python preact_demo/code/main.py --dataset locomo --limit 10 --vllm-url http://127.0.0.1:8000/v1 --vllm-model Qwen/Qwen2.5-7B-Instruct
```

```bash
python preact_demo/code/main.py --dataset demo --config preact_demo/code/configs/python_demo.yaml --vllm-host 127.0.0.1 --vllm-port 8000 --vllm-model Qwen/Qwen2.5-7B-Instruct
```

```bash
set VLLM_BASE_URL=http://127.0.0.1:8000/v1
set VLLM_MODEL=Qwen/Qwen2.5-7B-Instruct
python preact_demo/code/main.py --dataset demo
```

The default embedding provider is dependency-free signed feature hashing. To use an OpenAI-compatible `/embeddings` endpoint, set `embedding.provider` to `vllm`. External search is opt-in:

```bash
python preact_demo/code/main.py --dataset demo --enable-external-search
```

When enabled, memory gaps are sent to a key-free DuckDuckGo adapter for web results and Semantic Scholar for paper results. Search failures are non-fatal and leave the gap recorded in the working context package.

Start a local vLLM OpenAI-compatible server separately, for example:

```bash
python -m vllm.entrypoints.openai.api_server --model Qwen/Qwen2.5-7B-Instruct --host 127.0.0.1 --port 8000
```

## Run With LoCoMo

Download and run the first 10 LoCoMo QA samples:

```bash
node preact_demo/javascript/run_demo.js --dataset locomo --limit 10 --download-locomo
```

Run 100 LoCoMo QA samples after the file has been downloaded:

```bash
node preact_demo/javascript/run_demo.js --dataset locomo --limit 100
```

Run the full local LoCoMo file:

```bash
node preact_demo/javascript/run_demo.js --dataset locomo --limit 0
```

`--limit 0` means all samples. The default LoCoMo path is:

```text
preact_demo/data/locomo/locomo10.json
```

You can also provide a custom path:

```bash
node preact_demo/javascript/run_demo.js --dataset locomo --locomo-path path/to/locomo10.json --limit 50
```

The LoCoMo adapter uses the official `locomo10.json` shape: each conversation has `qa` plus `conversation.session_*` fields. Each QA becomes one evaluation sample, and each dialogue turn becomes a lightweight memory node. Official source: `https://github.com/snap-research/locomo`.

## Files

- `preact_demo/javascript/run_demo.js`
  - Main entrypoint. Loads config and sample data, runs evaluation, and prints three result tables.
- `preact_demo/javascript/configs/demo.json`
  - Runtime parameters: cache budget, retrieval top-k, fallback retriever, verifier threshold, graph similarity threshold, random seed.
- `preact_demo/data/samples.json`
  - Small built-in demo dataset shaped like `History H`, `Question q`, `Gold Answer a`.
- `preact_demo/javascript/data/load_samples.js`
  - Dataset loader. Supports the built-in demo data and LoCoMo `locomo10.json`, including `--limit 0` for full-file evaluation.
- `preact_demo/javascript/memory/memory_writer.js`
  - Converts annotated dialogue turns into `MemoryNode` objects. Replace this with an LLM extractor later.
- `preact_demo/javascript/graph/schema.js`
  - Defines node and edge type constants.
- `preact_demo/javascript/graph/build_graph.js`
  - Builds `MemoryNode`, `TurnNode`, `SegmentNode`, `EntityNode` plus `derived_from`, `belongs_to`, `mentions`, `similar_to`, and `temporal_next` edges.
- `preact_demo/javascript/activation/llm_predictor.js`
  - Simulates an idle-time LLM predictor without seeing the next query.
- `preact_demo/javascript/activation/working_cache.js`
  - Creates the working-memory cache and local subgraph.
- `preact_demo/javascript/activation/verifier.js`
  - Checks cache relevance after the query arrives.
- `preact_demo/javascript/retrieval/vector_retriever.js`
  - Reactive lexical vector-style retrieval baseline.
- `preact_demo/javascript/retrieval/graph_retriever.js`
  - Reactive graph retrieval baseline with simple graph-neighbor boosts.
- `preact_demo/javascript/generation/answer_generator.js`
  - Generates a minimal answer from selected memory summaries.
- `preact_demo/javascript/evaluation/evidence_labeler.js`
  - Creates pseudo-gold evidence labels from sample evidence terms.
- `preact_demo/javascript/evaluation/metrics.js`
  - Computes activation precision/recall/hit/waste, F1, ROUGE-L, pseudo judge, faithfulness, and rough cost estimates.
- `preact_demo/javascript/evaluation/run_eval.js`
  - Orchestrates all methods and aggregates metrics.
- `preact_demo/javascript/common/text.js`
  - Tokenization, similarity, and token-estimation helpers.

### Python Files

- `preact_demo/code/main.py`
  - Python CLI entrypoint for demo and LoCoMo runs.
- `preact_demo/code/utils.py`
  - Shared dataclasses, text utilities, metrics, and table formatting.
- `preact_demo/code/vllm_client.py`
  - Minimal OpenAI-compatible vLLM chat and embedding client.
- `preact_demo/code/predictors.py`
  - `MemoryNeedPredictor` protocol, heuristic predictor, and vLLM predictor.
- `preact_demo/code/graph_store.py`
  - JSON-friendly heterogeneous graph store and graph builder.
- `preact_demo/code/embeddings.py`
  - Replaceable hash or vLLM embedding provider.
- `preact_demo/code/grounding.py`
  - Embedding grounding with optional LLM reranking.
- `preact_demo/code/memory_gap.py`
  - Score-based or LLM memory-gap detection.
- `preact_demo/code/external_search.py`
  - Opt-in web and paper search adapters.
- `preact_demo/code/working_context.py`
  - Working context package builder and query-time verifier.
- `preact_demo/code/pipeline.py`
  - Memory writing, cache insertion, verification, retrieval, answer generation, baselines, and evaluation.
- `preact_demo/code/locomo.py`
  - LoCoMo adapter and smoke-test CLI.
- `preact_demo/code/configs/python_demo.json`
  - Python runtime config.

To change the prediction method later, implement the `MemoryNeedPredictor` protocol in `preact_demo/code/predictors.py` and wire it in `create_predictor`. The rest of the cache, verifier, retrieval, and evaluation pipeline can stay unchanged.

## Current Scope

The JavaScript path remains the lightweight original baseline. The Python path now has replaceable LLM, embedding, grounding, gap, search, verifier, and fallback stages while retaining deterministic local fallbacks.

- proactive cache versus random and recency cache
- cache-only versus cache plus fallback
- reactive vector and graph retrieval baselines
- oracle cache upper bound

Next upgrades should replace the heuristic memory writer with an LLM extractor, add a production vector index, persist working context packages, and evaluate learned predictors behind the `MemoryNeedPredictor` interface.
