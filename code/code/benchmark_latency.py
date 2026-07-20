from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    loaded_code = sys.modules.get("code")
    if loaded_code is not None and not hasattr(loaded_code, "__path__"):
        del sys.modules["code"]
    from code.gap_reasoning import build_prepared_context
    from code.graph_store import build_memory_graph
    from code.locomo import load_config, load_locomo_samples
    from code.pipeline import (
        graph_retrieve,
        label_gold_evidence,
        memory_writer,
        vector_retrieve,
    )
    from code.predictors import create_predictor
    from code.utils import (
        MemoryNode,
        bleu1_score,
        f1_score,
        locomo_answer_f1,
        rouge_l,
        write_json,
    )
    from code.vllm_client import VLLMClient, VLLMError
else:
    from .gap_reasoning import build_prepared_context
    from .graph_store import build_memory_graph
    from .locomo import load_config, load_locomo_samples
    from .pipeline import (
        graph_retrieve,
        label_gold_evidence,
        memory_writer,
        vector_retrieve,
    )
    from .predictors import create_predictor
    from .utils import (
        MemoryNode,
        bleu1_score,
        f1_score,
        locomo_answer_f1,
        rouge_l,
        write_json,
    )
    from .vllm_client import VLLMClient, VLLMError


METHOD_REACTIVE = "Reactive"
METHOD_PREDICTIVE = "Predictive Graph Cache"
METHOD_ORACLE = "Oracle Prefetch"
METHODS = [METHOD_REACTIVE, METHOD_PREDICTIVE, METHOD_ORACLE]


class InstrumentedVLLMClient(VLLMClient):
    """VLLMClient that records planning requests without changing call sites."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.request_count = 0
        self.request_latency_ms = 0.0
        self.usage: Dict[str, float] = {
            "prompt_tokens": 0.0,
            "completion_tokens": 0.0,
            "total_tokens": 0.0,
        }

    def snapshot(self) -> Dict[str, float]:
        return {
            "request_count": float(self.request_count),
            "request_latency_ms": self.request_latency_ms,
            **self.usage,
        }

    def chat(
        self,
        messages: List[Mapping[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 512,
        response_format: Optional[Mapping[str, Any]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        started = time.perf_counter()
        content, usage = super().chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )
        self.request_count += 1
        self.request_latency_ms += elapsed_ms(started)
        for key in ["prompt_tokens", "completion_tokens", "total_tokens"]:
            self.usage[key] += safe_float(usage.get(key), 0.0)
        return content, usage


class MemoryCostModel:
    """Deterministic heavy-tail latency model for memory materialization.

    Replace this class with calls to the real memory/object store when that
    service is available. Keeping the model deterministic makes paired
    Reactive/Predictive/Oracle comparisons reproducible.
    """

    def __init__(
        self,
        base_ms: float,
        tail_ms: float,
        per_kb_ms: float,
        max_ms: float,
        seed: int,
    ) -> None:
        self.base_ms = base_ms
        self.tail_ms = tail_ms
        self.per_kb_ms = per_kb_ms
        self.max_ms = max_ms
        self.seed = seed

    def byte_size(self, memory: MemoryNode) -> int:
        return len(memory.content.encode("utf-8"))

    def load_ms(self, memory: MemoryNode) -> float:
        digest = hashlib.sha256(
            f"{self.seed}:{memory.id}:{memory.source_turn_id}:{memory.content}".encode("utf-8")
        ).digest()
        unit = (int.from_bytes(digest[:8], "big") + 1) / (2**64 + 1)
        heavy_tail = -math.log(max(1e-12, 1.0 - unit)) / 3.0
        size_cost = self.per_kb_ms * self.byte_size(memory) / 1024.0
        return min(self.max_ms, self.base_ms + self.tail_ms * heavy_tail + size_cost)


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config)
    apply_benchmark_overrides(config, args)

    locomo_path = Path(args.locomo_path)
    if not locomo_path.exists():
        raise FileNotFoundError(
            f"LoCoMo file not found: {locomo_path}. "
            "Download it first or pass --locomo-path."
        )

    samples = load_locomo_samples(
        locomo_path,
        limit=args.limit,
        eval_mode=args.eval_mode,
    )
    if not samples:
        raise RuntimeError("No LoCoMo samples loaded.")

    llm_config = dict(config.get("llm") or {})
    client = InstrumentedVLLMClient(
        base_url=str(llm_config.get("base_url") or "http://127.0.0.1:30000/v1"),
        model=str(llm_config.get("model") or "Qwen/Qwen2.5-7B-Instruct"),
        timeout=float(llm_config.get("timeout") or 120),
        api_key=llm_config.get("api_key"),
    )
    predictor = create_predictor(config, client)
    cost_model = MemoryCostModel(
        base_ms=args.memory_load_base_ms,
        tail_ms=args.memory_load_tail_ms,
        per_kb_ms=args.memory_load_per_kb_ms,
        max_ms=args.memory_load_max_ms,
        seed=args.seed,
    )

    print("PreMem latency benchmark")
    print(f"Samples: {len(samples)}")
    print(f"vLLM: {client.base_url}")
    print(f"Model: {client.model}")
    print(f"Working set: {args.working_set}")
    print(f"Pre-query window: {args.prequery_window_ms:.1f} ms")
    print(f"Cache budget: {config.get('cache_budget')}")
    print(f"Memory load model: base={args.memory_load_base_ms} tail={args.memory_load_tail_ms} max={args.memory_load_max_ms} ms")
    print(f"Generation: {'skipped' if args.skip_generation else 'vLLM streaming'}")
    print()

    all_rows: List[Dict[str, Any]] = []
    planning_rows: List[Dict[str, Any]] = []
    rng = random.Random(args.seed)

    if not args.skip_generation and args.warmup_requests > 0:
        warmup_vllm(client, args.warmup_requests, args.reader_max_tokens)

    for sample_index, sample in enumerate(samples, start=1):
        print(f"[{sample_index}/{len(samples)}] planning {sample.id}", flush=True)
        graph_started = time.perf_counter()
        memory_nodes = memory_writer(sample.history)
        graph = build_memory_graph(
            memory_nodes,
            sample.history,
            similarity_threshold=float(config.get("similarity_threshold") or 0.28),
        )
        graph_build_ms = elapsed_ms(graph_started)
        by_id = {memory.id: memory for memory in memory_nodes}

        gold_ids = [
            memory_id
            for memory_id in label_gold_evidence(
                sample.evidence_terms,
                sample.gold_evidence_turn_ids,
                memory_nodes,
            )
            if memory_id in by_id
        ]

        planning_usage_before = client.snapshot()
        predictor_started = time.perf_counter()
        prediction = predictor.predict(
            sample.history,
            memory_nodes,
            graph,
            int(config.get("cache_budget") or 5),
        )
        intent_prediction_ms = elapsed_ms(predictor_started)

        graph_plan_started = time.perf_counter()
        prepared_context = build_prepared_context(
            context_key=sample.history_cache_key or sample.id,
            history=sample.history,
            memory_nodes=memory_nodes,
            graph=graph,
            prediction=prediction,
            llm_client=client,
            config=config,
        )
        graph_planning_ms = elapsed_ms(graph_plan_started)
        planning_usage = subtract_usage(client.snapshot(), planning_usage_before)
        planning_total_ms = intent_prediction_ms + graph_planning_ms

        predicted_ids = [
            memory_id
            for memory_id in prepared_context.get("memory_ids", [])
            if memory_id in by_id
        ]
        reactive_started = time.perf_counter()
        if args.reactive_retriever == "graph":
            reactive_memories = graph_retrieve(
                sample.question,
                graph,
                memory_nodes,
                int(config.get("retrieval_top_k") or 5),
            )
        else:
            reactive_memories = vector_retrieve(
                sample.question,
                memory_nodes,
                int(config.get("retrieval_top_k") or 5),
            )
        measured_retrieval_ms = elapsed_ms(reactive_started)
        reactive_ids = [memory.id for memory in reactive_memories]

        if args.working_set == "reactive":
            working_set_ids = reactive_ids
        else:
            working_set_ids = gold_ids or reactive_ids

        planning = build_planning_row(
            sample_id=sample.id,
            graph_build_ms=graph_build_ms,
            intent_prediction_ms=intent_prediction_ms,
            graph_planning_ms=graph_planning_ms,
            planning_total_ms=planning_total_ms,
            planning_usage=planning_usage,
            prediction=prediction,
            prepared_context=prepared_context,
            working_set_ids=working_set_ids,
            memory_nodes=memory_nodes,
        )
        planning_rows.append(planning)

        method_order = list(METHODS)
        rng.shuffle(method_order)
        for method in method_order:
            print(f"[{sample_index}/{len(samples)}] {method} {sample.id}", flush=True)
            if method == METHOD_REACTIVE:
                prefetch_ids: List[str] = []
                method_planning_ms = 0.0
            elif method == METHOD_ORACLE:
                prefetch_ids = list(working_set_ids)
                method_planning_ms = 0.0
            else:
                prefetch_ids = list(predicted_ids)
                method_planning_ms = planning_total_ms

            row = run_method(
                method=method,
                sample=sample,
                memory_nodes=memory_nodes,
                working_set_ids=working_set_ids,
                gold_ids=gold_ids,
                prefetch_ids=prefetch_ids,
                planning_ms=method_planning_ms,
                measured_retrieval_ms=measured_retrieval_ms,
                client=client,
                cost_model=cost_model,
                args=args,
            )
            row["reactive_candidate_ids"] = reactive_ids
            row["prepared_memory_ids"] = predicted_ids
            row["candidate_memory_ids"] = list(prepared_context.get("candidate_memory_ids") or [])
            row["compression"] = prepared_context.get("compression") or {}
            all_rows.append(row)

    report = build_report(args, config, client, all_rows, planning_rows)
    output_path = Path(args.output)
    write_json(output_path, report)
    print_summary(report)
    print(f"\nJSON report: {output_path.resolve()}")


def run_method(
    method: str,
    sample: Any,
    memory_nodes: Sequence[MemoryNode],
    working_set_ids: Sequence[str],
    gold_ids: Sequence[str],
    prefetch_ids: Sequence[str],
    planning_ms: float,
    measured_retrieval_ms: float,
    client: InstrumentedVLLMClient,
    cost_model: MemoryCostModel,
    args: argparse.Namespace,
) -> Dict[str, Any]:
    by_id = {memory.id: memory for memory in memory_nodes}
    working_ids = [memory_id for memory_id in unique(working_set_ids) if memory_id in by_id]
    gold = [memory_id for memory_id in unique(gold_ids) if memory_id in by_id]
    prefetched = [memory_id for memory_id in unique(prefetch_ids) if memory_id in by_id]

    query_arrival = time.perf_counter()
    planning_overrun_ms = max(0.0, planning_ms - args.prequery_window_ms)
    planning_ready_at_arrival = planning_ms <= args.prequery_window_ms
    available_prefetch_ms = (
        max(0.0, args.prequery_window_ms - planning_ms)
        if planning_ready_at_arrival
        else 0.0
    )
    prefetch_completion_times = compute_prefetch_completion_times(
        prefetched,
        by_id,
        cost_model,
        args.prefetch_concurrency,
    )
    completed_cache = {
        memory_id
        for memory_id in prefetched
        if prefetch_completion_times[memory_id] <= available_prefetch_ms
    }
    missing_ids = [memory_id for memory_id in working_ids if memory_id not in completed_cache]

    lookup_started = time.perf_counter()
    _ = [by_id[memory_id] for memory_id in working_ids if memory_id in completed_cache]
    cache_lookup_ms = elapsed_ms(lookup_started)

    retrieval_ms = 0.0
    query_encoding_ms = 0.0
    query_rerank_ms = 0.0
    needs_reactive_lookup = method == METHOD_REACTIVE or (
        method == METHOD_PREDICTIVE and bool(missing_ids)
    )
    if needs_reactive_lookup:
        query_encoding_ms = args.query_encoding_ms
        query_rerank_ms = args.query_rerank_ms
        retrieval_ms = measured_retrieval_ms
        sleep_ms(query_encoding_ms + retrieval_ms + query_rerank_ms, args.sleep_scale)

    online_load_ms_model = 0.0
    for memory_id in missing_ids:
        full_cost = cost_model.load_ms(by_id[memory_id])
        if memory_id in prefetched:
            remaining = max(
                0.0,
                prefetch_completion_times[memory_id] - available_prefetch_ms,
            )
        else:
            remaining = full_cost
        online_load_ms_model = max(online_load_ms_model, remaining)

    sleep_ms(online_load_ms_model, args.sleep_scale)
    memory_ready = time.perf_counter()
    measured_memory_stall_ms = (memory_ready - query_arrival) * 1000.0
    modeled_memory_stall_ms = (
        cache_lookup_ms
        + query_encoding_ms
        + retrieval_ms
        + query_rerank_ms
        + online_load_ms_model
    )

    prompt_started = time.perf_counter()
    context_memories = [by_id[memory_id] for memory_id in working_ids]
    messages = build_reader_messages(sample.question, context_memories)
    prompt_build_ms = elapsed_ms(prompt_started)

    if args.skip_generation:
        generation = {
            "answer": "",
            "vllm_ttft_ms": None,
            "vllm_e2e_ms": None,
            "completion_tokens": 0,
            "prompt_tokens": 0,
            "total_tokens": 0,
            "tpot_ms": None,
        }
        ttft_ms = None
        e2e_ms = None
    else:
        generation = stream_chat_timed(
            client=client,
            messages=messages,
            max_tokens=args.reader_max_tokens,
            temperature=args.reader_temperature,
        )
        ttft_ms = measured_memory_stall_ms + prompt_build_ms + safe_float(
            generation.get("vllm_ttft_ms"), 0.0
        )
        e2e_ms = measured_memory_stall_ms + prompt_build_ms + safe_float(
            generation.get("vllm_e2e_ms"), 0.0
        )

    answer = str(generation.get("answer") or "")
    cache_metrics = compute_cache_metrics(
        working_ids=working_ids,
        gold_ids=gold,
        prefetched_ids=prefetched,
        completed_cache=completed_cache,
        by_id=by_id,
        cost_model=cost_model,
    )
    ready_curve = compute_ready_curve(
        windows_ms=args.ready_windows_ms,
        planning_ms=planning_ms,
        gold_ids=gold,
        prefetched_ids=prefetched,
        by_id=by_id,
        cost_model=cost_model,
        prefetch_concurrency=args.prefetch_concurrency,
    )
    prediction_metrics = compute_prediction_calibration(
        prefetched_ids=prefetched,
        working_ids=working_ids,
        memory_nodes=memory_nodes,
        prepared_context=None,
    )
    deadline_sweep = compute_nonblocking_deadline_sweep(
        windows_ms=args.ready_windows_ms,
        planning_ms=planning_ms,
        working_ids=working_ids,
        gold_ids=gold,
        prefetched_ids=prefetched,
        by_id=by_id,
        cost_model=cost_model,
        prefetch_concurrency=args.prefetch_concurrency,
        measured_retrieval_ms=measured_retrieval_ms,
        query_encoding_ms=args.query_encoding_ms,
        query_rerank_ms=args.query_rerank_ms,
        prompt_build_ms=prompt_build_ms,
        vllm_ttft_ms=generation.get("vllm_ttft_ms"),
        vllm_e2e_ms=generation.get("vllm_e2e_ms"),
    )

    return {
        "sample_id": sample.id,
        "method": method,
        "question": sample.question,
        "gold_answer": sample.answer,
        "generated_answer": answer,
        "working_set_memory_ids": working_ids,
        "gold_evidence_memory_ids": gold,
        "prefetch_memory_ids": prefetched,
        "completed_cache_memory_ids": sorted(completed_cache),
        "memory_stall_ms": measured_memory_stall_ms,
        "modeled_memory_stall_ms": modeled_memory_stall_ms,
        "ready_at_windows": ready_curve,
        "planning_overrun_ms": planning_overrun_ms,
        "planning_ready_at_arrival": planning_ready_at_arrival,
        "fallback_used_at_arrival": bool(missing_ids),
        "available_prefetch_ms": available_prefetch_ms,
        "cache_lookup_ms": cache_lookup_ms,
        "query_encoding_ms": query_encoding_ms,
        "retrieval_ms": retrieval_ms,
        "query_rerank_ms": query_rerank_ms,
        "online_load_ms": online_load_ms_model,
        "prompt_build_ms": prompt_build_ms,
        "vllm_ttft_ms": generation.get("vllm_ttft_ms"),
        "ttft_ms": ttft_ms,
        "vllm_e2e_ms": generation.get("vllm_e2e_ms"),
        "e2e_ms": e2e_ms,
        "tpot_ms": generation.get("tpot_ms"),
        "deadline_sweep": deadline_sweep,
        "prompt_tokens": generation.get("prompt_tokens", 0),
        "completion_tokens": generation.get("completion_tokens", 0),
        "total_tokens": generation.get("total_tokens", 0),
        "official_f1": locomo_answer_f1(answer, sample.answer, sample.metadata.get("category"))
        if answer
        else None,
        "token_f1": f1_score(answer, sample.answer) if answer else None,
        "bleu1": bleu1_score(answer, sample.answer) if answer else None,
        "rouge_l": rouge_l(answer, sample.answer) if answer else None,
        **cache_metrics,
        **prediction_metrics,
    }


def compute_nonblocking_deadline_sweep(
    *,
    windows_ms: Sequence[float],
    planning_ms: float,
    working_ids: Sequence[str],
    gold_ids: Sequence[str],
    prefetched_ids: Sequence[str],
    by_id: Mapping[str, MemoryNode],
    cost_model: MemoryCostModel,
    prefetch_concurrency: int,
    measured_retrieval_ms: float,
    query_encoding_ms: float,
    query_rerank_ms: float,
    prompt_build_ms: float,
    vllm_ttft_ms: Any,
    vllm_e2e_ms: Any,
) -> Dict[str, Dict[str, Any]]:
    """Counterfactual query arrivals; incomplete planning never blocks the query."""
    working = [item for item in unique(working_ids) if item in by_id]
    gold = {item for item in gold_ids if item in by_id}
    prefetched = [item for item in unique(prefetched_ids) if item in by_id]
    completion_times = compute_prefetch_completion_times(
        prefetched,
        by_id,
        cost_model,
        prefetch_concurrency,
    )
    result: Dict[str, Dict[str, Any]] = {}
    for window_ms in windows_ms:
        planning_ready = planning_ms <= float(window_ms)
        available_ms = max(0.0, float(window_ms) - planning_ms) if planning_ready else 0.0
        completed = {
            memory_id
            for memory_id in prefetched
            if planning_ready and completion_times[memory_id] <= available_ms
        }
        missing = [memory_id for memory_id in working if memory_id not in completed]
        online_load_ms = 0.0
        for memory_id in missing:
            full_cost = cost_model.load_ms(by_id[memory_id])
            if planning_ready and memory_id in completion_times:
                remaining = max(0.0, completion_times[memory_id] - available_ms)
                load_cost = min(full_cost, remaining)
            else:
                load_cost = full_cost
            online_load_ms = max(online_load_ms, load_cost)
        fallback_used = bool(missing)
        stall_ms = online_load_ms
        if fallback_used:
            stall_ms += query_encoding_ms + measured_retrieval_ms + query_rerank_ms
        ready_gold = gold & completed
        item: Dict[str, Any] = {
            "planning_ready": planning_ready,
            "fallback_used": fallback_used,
            "ready": ratio(len(ready_gold), len(gold)),
            "full_query_hit": float(bool(working) and set(working) <= completed),
            "memory_stall_ms": stall_ms,
            "completed_cache_memory_ids": sorted(completed),
        }
        if vllm_ttft_ms is not None:
            item["ttft_ms"] = stall_ms + prompt_build_ms + safe_float(vllm_ttft_ms)
        if vllm_e2e_ms is not None:
            item["e2e_ms"] = stall_ms + prompt_build_ms + safe_float(vllm_e2e_ms)
        result[format_window_key(window_ms)] = item
    return result


def stream_chat_timed(
    client: VLLMClient,
    messages: Sequence[Mapping[str, str]],
    max_tokens: int,
    temperature: float,
) -> Dict[str, Any]:
    payload = {
        "model": client.model,
        "messages": list(messages),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    request = urllib.request.Request(
        client.base_url + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {client.api_key}",
        },
        method="POST",
    )
    started = time.perf_counter()
    first_token_ms: Optional[float] = None
    chunks: List[str] = []
    usage: Dict[str, Any] = {}
    try:
        with urllib.request.urlopen(request, timeout=client.timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if event.get("usage"):
                    usage = dict(event["usage"])
                choices = event.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    if first_token_ms is None:
                        first_token_ms = elapsed_ms(started)
                    chunks.append(str(content))
    except urllib.error.URLError as exc:
        raise VLLMError(f"Streaming vLLM request failed: {exc}") from exc

    e2e_ms = elapsed_ms(started)
    if first_token_ms is None:
        first_token_ms = e2e_ms
    answer = "".join(chunks).strip()
    completion_tokens = int(
        safe_float(usage.get("completion_tokens"), estimate_tokens_rough(answer))
    )
    prompt_tokens = int(safe_float(usage.get("prompt_tokens"), 0))
    total_tokens = int(
        safe_float(usage.get("total_tokens"), prompt_tokens + completion_tokens)
    )
    if completion_tokens > 1:
        tpot_ms = max(0.0, e2e_ms - first_token_ms) / (completion_tokens - 1)
    else:
        tpot_ms = 0.0
    return {
        "answer": answer,
        "vllm_ttft_ms": first_token_ms,
        "vllm_e2e_ms": e2e_ms,
        "completion_tokens": completion_tokens,
        "prompt_tokens": prompt_tokens,
        "total_tokens": total_tokens,
        "tpot_ms": tpot_ms,
    }


def build_reader_messages(
    query: str,
    memories: Sequence[MemoryNode],
) -> List[Dict[str, str]]:
    context_lines = [
        "[{id} | turn={turn} | segment={segment} | time={time}] {content}".format(
            id=memory.id,
            turn=memory.source_turn_id,
            segment=memory.segment_id,
            time=memory.timestamp,
            content=memory.content,
        )
        for memory in sorted(memories, key=lambda item: item.timestamp)
    ]
    context = "\n".join(context_lines)
    return [
        {
            "role": "system",
            "content": (
                "You are the downstream QA reader for a memory latency benchmark. "
                "Use only the supplied memory context. Return only the shortest answer "
                "phrase or sentence. If the answer is not supported, return "
                "\"No information available.\""
            ),
        },
        {
            "role": "user",
            "content": f"Memory context:\n{context}\n\nQuestion: {query}\n\nAnswer:",
        },
    ]


def compute_cache_metrics(
    working_ids: Sequence[str],
    gold_ids: Sequence[str],
    prefetched_ids: Sequence[str],
    completed_cache: set[str],
    by_id: Mapping[str, MemoryNode],
    cost_model: MemoryCostModel,
) -> Dict[str, Any]:
    working = set(working_ids)
    gold = set(gold_ids)
    completed = set(completed_cache)
    used_cache = working & completed
    ready_gold = gold & completed
    completed_prefetch = set(prefetched_ids) & completed

    working_bytes = sum(cost_model.byte_size(by_id[item]) for item in working if item in by_id)
    hit_bytes = sum(cost_model.byte_size(by_id[item]) for item in used_cache if item in by_id)
    gold_utility = sum(cost_model.load_ms(by_id[item]) for item in gold if item in by_id)
    ready_utility = sum(cost_model.load_ms(by_id[item]) for item in ready_gold if item in by_id)
    working_utility = sum(cost_model.load_ms(by_id[item]) for item in working if item in by_id)
    hit_utility = sum(cost_model.load_ms(by_id[item]) for item in used_cache if item in by_id)
    attempted_prefetch = {item for item in prefetched_ids if item in by_id}
    prefetch_bytes = sum(
        cost_model.byte_size(by_id[item]) for item in prefetched_ids if item in by_id
    )
    completed_bytes = sum(
        cost_model.byte_size(by_id[item]) for item in completed_prefetch if item in by_id
    )
    unused_completed = completed_prefetch - working
    unused_attempted = attempted_prefetch - working
    unused_bytes = sum(
        cost_model.byte_size(by_id[item]) for item in unused_completed if item in by_id
    )

    return {
        "ready_at_budget_window": ratio(len(ready_gold), len(gold)),
        "utility_weighted_recall": ratio(ready_utility, gold_utility),
        "node_hit_rate": ratio(len(used_cache), len(working)),
        "byte_weighted_hit_rate": ratio(hit_bytes, working_bytes),
        "latency_weighted_hit_rate": ratio(hit_utility, working_utility),
        "full_query_hit": float(bool(working) and working <= completed),
        "waste_rate": ratio(len(unused_attempted), len(attempted_prefetch)),
        "completed_waste_rate": ratio(
            len(unused_completed), len(completed_prefetch)
        ),
        "cache_pollution": ratio(unused_bytes, completed_bytes),
        "prefetch_attempted_count": len(prefetched_ids),
        "prefetch_completed_count": len(completed_prefetch),
        "prefetch_completion_rate": ratio(
            len(completed_prefetch), len(prefetched_ids)
        ),
        "prefetch_bytes": prefetch_bytes,
        "completed_prefetch_bytes": completed_bytes,
        "cache_occupancy_nodes": len(completed_prefetch),
        "cache_occupancy_bytes": completed_bytes,
    }


def compute_ready_curve(
    windows_ms: Sequence[float],
    planning_ms: float,
    gold_ids: Sequence[str],
    prefetched_ids: Sequence[str],
    by_id: Mapping[str, MemoryNode],
    cost_model: MemoryCostModel,
    prefetch_concurrency: int,
) -> Dict[str, float]:
    gold = {item for item in gold_ids if item in by_id}
    completion_times = compute_prefetch_completion_times(
        prefetched_ids,
        by_id,
        cost_model,
        prefetch_concurrency,
    )
    result: Dict[str, float] = {}
    for window_ms in windows_ms:
        available_ms = max(0.0, float(window_ms) - planning_ms)
        ready = {
            memory_id
            for memory_id, completion_ms in completion_times.items()
            if completion_ms <= available_ms
        }
        result[format_window_key(window_ms)] = ratio(len(gold & ready), len(gold))
    return result


def compute_prefetch_completion_times(
    prefetched_ids: Sequence[str],
    by_id: Mapping[str, MemoryNode],
    cost_model: MemoryCostModel,
    concurrency: int,
) -> Dict[str, float]:
    worker_available_ms = [0.0] * max(1, int(concurrency))
    completion_times: Dict[str, float] = {}
    for memory_id in unique(prefetched_ids):
        if memory_id not in by_id:
            continue
        worker_index = min(
            range(len(worker_available_ms)),
            key=worker_available_ms.__getitem__,
        )
        completion_ms = (
            worker_available_ms[worker_index]
            + cost_model.load_ms(by_id[memory_id])
        )
        worker_available_ms[worker_index] = completion_ms
        completion_times[memory_id] = completion_ms
    return completion_times


def compute_prediction_calibration(
    prefetched_ids: Sequence[str],
    working_ids: Sequence[str],
    memory_nodes: Sequence[MemoryNode],
    prepared_context: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    # Prepared-cache scores are not probabilities. Report a coverage-oriented
    # proxy now; calibrated intent/cache probabilities can replace it later.
    predicted = set(prefetched_ids)
    working = set(working_ids)
    all_ids = {memory.id for memory in memory_nodes}
    if not all_ids:
        return {"predictor_brier_proxy": None}
    brier = sum(
        ((1.0 if item in predicted else 0.0) - (1.0 if item in working else 0.0)) ** 2
        for item in all_ids
    ) / len(all_ids)
    return {"predictor_brier_proxy": brier}


def build_planning_row(
    sample_id: str,
    graph_build_ms: float,
    intent_prediction_ms: float,
    graph_planning_ms: float,
    planning_total_ms: float,
    planning_usage: Mapping[str, float],
    prediction: Any,
    prepared_context: Mapping[str, Any],
    working_set_ids: Sequence[str],
    memory_nodes: Sequence[MemoryNode],
) -> Dict[str, Any]:
    predicted_ids = [
        str(item.id) for item in prediction.activated_memory_ids if item.id
    ]
    prepared_ids = [str(item) for item in prepared_context.get("memory_ids") or []]
    working = set(working_set_ids)
    confidences = {
        str(item.id): safe_float(item.confidence, 0.0)
        for item in prediction.activated_memory_ids
        if item.id
    }
    brier_values = []
    for memory in memory_nodes:
        probability = confidences.get(memory.id, 0.0)
        label = 1.0 if memory.id in working else 0.0
        brier_values.append((probability - label) ** 2)
    return {
        "sample_id": sample_id,
        "graph_build_ms": graph_build_ms,
        "intent_prediction_ms": intent_prediction_ms,
        "graph_planning_ms": graph_planning_ms,
        "planning_total_ms": planning_total_ms,
        "planning_request_count": planning_usage.get("request_count", 0),
        "planning_prompt_tokens": planning_usage.get("prompt_tokens", 0),
        "planning_completion_tokens": planning_usage.get("completion_tokens", 0),
        "planning_total_tokens": planning_usage.get("total_tokens", 0),
        "predictor_memory_ids": predicted_ids,
        "prepared_memory_ids": prepared_ids,
        "working_set_memory_ids": list(working_set_ids),
        "predictor_working_set_recall": ratio(
            len(set(predicted_ids) & working), len(working)
        ),
        "prepared_working_set_recall": ratio(
            len(set(prepared_ids) & working), len(working)
        ),
        "predictor_brier_score": (
            sum(brier_values) / len(brier_values) if brier_values else None
        ),
    }


def build_report(
    args: argparse.Namespace,
    config: Mapping[str, Any],
    client: InstrumentedVLLMClient,
    rows: Sequence[Mapping[str, Any]],
    planning_rows: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    summaries = []
    by_method: Dict[str, List[Mapping[str, Any]]] = {
        method: [row for row in rows if row.get("method") == method]
        for method in METHODS
    }
    for method in METHODS:
        method_rows = by_method[method]
        summaries.append(
            {
                "method": method,
                "samples": len(method_rows),
                "memory_stall_ms": latency_stats(method_rows, "memory_stall_ms"),
                "modeled_memory_stall_ms": latency_stats(
                    method_rows, "modeled_memory_stall_ms"
                ),
                "ttft_ms": latency_stats(method_rows, "ttft_ms"),
                "e2e_ms": latency_stats(method_rows, "e2e_ms"),
                "tpot_ms": latency_stats(method_rows, "tpot_ms"),
                "ready_at_windows": mean_mapping_field(
                    method_rows, "ready_at_windows"
                ),
                "ready_at_budget_window": mean_field(
                    method_rows, "ready_at_budget_window"
                ),
                "utility_weighted_recall": mean_field(
                    method_rows, "utility_weighted_recall"
                ),
                "node_hit_rate": mean_field(method_rows, "node_hit_rate"),
                "byte_weighted_hit_rate": mean_field(
                    method_rows, "byte_weighted_hit_rate"
                ),
                "latency_weighted_hit_rate": mean_field(
                    method_rows, "latency_weighted_hit_rate"
                ),
                "full_query_hit_rate": mean_field(
                    method_rows, "full_query_hit"
                ),
                "waste_rate": mean_field(method_rows, "waste_rate"),
                "completed_waste_rate": mean_field(
                    method_rows, "completed_waste_rate"
                ),
                "cache_pollution": mean_field(method_rows, "cache_pollution"),
                "prefetch_completion_rate": mean_field(
                    method_rows, "prefetch_completion_rate"
                ),
                "prefetch_bytes": mean_field(method_rows, "prefetch_bytes"),
                "cache_occupancy_nodes": mean_field(
                    method_rows, "cache_occupancy_nodes"
                ),
                "official_f1": mean_field(method_rows, "official_f1"),
                "bleu1": mean_field(method_rows, "bleu1"),
                "rouge_l": mean_field(method_rows, "rouge_l"),
            }
        )

    summary_by_method = {row["method"]: row for row in summaries}
    reactive = summary_by_method[METHOD_REACTIVE]
    predictive = summary_by_method[METHOD_PREDICTIVE]
    oracle = summary_by_method[METHOD_ORACLE]
    reactive_stall_p95 = nested_float(reactive, "memory_stall_ms", "p95")
    predictive_stall_p95 = nested_float(predictive, "memory_stall_ms", "p95")
    oracle_stall_p95 = nested_float(oracle, "memory_stall_ms", "p95")
    reactive_ttft_p95 = nested_float(reactive, "ttft_ms", "p95")
    predictive_ttft_p95 = nested_float(predictive, "ttft_ms", "p95")
    oracle_ttft_p95 = nested_float(oracle, "ttft_ms", "p95")
    saved_stall = max(0.0, reactive_stall_p95 - predictive_stall_p95)
    avg_planning_ms = mean_field(planning_rows, "planning_total_ms")
    deadline_sweep = summarize_deadline_sweep(
        by_method[METHOD_PREDICTIVE],
        args.ready_windows_ms,
    )

    return {
        "benchmark": "PreMem post-query memory stall / TTFT",
        "configuration": {
            "locomo_path": str(Path(args.locomo_path).resolve()),
            "limit": args.limit,
            "eval_mode": args.eval_mode,
            "working_set": args.working_set,
            "reactive_retriever": args.reactive_retriever,
            "cache_budget": config.get("cache_budget"),
            "retrieval_top_k": config.get("retrieval_top_k"),
            "prequery_window_ms": args.prequery_window_ms,
            "ready_windows_ms": args.ready_windows_ms,
            "prefetch_concurrency": args.prefetch_concurrency,
            "memory_cost_model": {
                "base_ms": args.memory_load_base_ms,
                "tail_ms": args.memory_load_tail_ms,
                "per_kb_ms": args.memory_load_per_kb_ms,
                "max_ms": args.memory_load_max_ms,
                "sleep_scale": args.sleep_scale,
            },
            "query_encoding_ms": args.query_encoding_ms,
            "query_rerank_ms": args.query_rerank_ms,
            "vllm_base_url": client.base_url,
            "vllm_model": client.model,
            "reader_max_tokens": args.reader_max_tokens,
            "skip_generation": args.skip_generation,
            "resource_competition_modeled": False,
        },
        "headroom": {
            "reactive_p95_memory_stall_ms": reactive_stall_p95,
            "oracle_p95_memory_stall_ms": oracle_stall_p95,
            "oracle_max_memory_stall_reduction": reduction(
                reactive_stall_p95, oracle_stall_p95
            ),
            "reactive_p95_ttft_ms": reactive_ttft_p95,
            "oracle_p95_ttft_ms": oracle_ttft_p95,
            "oracle_max_ttft_reduction": reduction(
                reactive_ttft_p95, oracle_ttft_p95
            ),
        },
        "predictive_effect": {
            "hidden_latency_ratio": reduction(
                reactive_stall_p95, predictive_stall_p95
            ),
            "p95_memory_stall_reduction": reduction(
                reactive_stall_p95, predictive_stall_p95
            ),
            "p95_ttft_reduction": reduction(
                reactive_ttft_p95, predictive_ttft_p95
            ),
            "p95_memory_stall_saved_ms": saved_stall,
            "average_prediction_graph_cost_ms": avg_planning_ms,
            "planning_cost_to_saved_stall_ratio": ratio(
                avg_planning_ms, saved_stall
            ),
        },
        "planning_summary": {
            "graph_build_ms": stats(
                values(planning_rows, "graph_build_ms")
            ),
            "intent_prediction_ms": stats(
                values(planning_rows, "intent_prediction_ms")
            ),
            "graph_planning_ms": stats(
                values(planning_rows, "graph_planning_ms")
            ),
            "planning_total_ms": stats(
                values(planning_rows, "planning_total_ms")
            ),
            "planning_total_tokens_mean": mean_field(
                planning_rows, "planning_total_tokens"
            ),
            "predictor_working_set_recall": mean_field(
                planning_rows, "predictor_working_set_recall"
            ),
            "prepared_working_set_recall": mean_field(
                planning_rows, "prepared_working_set_recall"
            ),
            "predictor_brier_score": mean_field(
                planning_rows, "predictor_brier_score"
            ),
        },
        "nonblocking_deadline_sweep": deadline_sweep,
        "method_summaries": summaries,
        "planning_samples": list(planning_rows),
        "samples": list(rows),
        "notes": [
            "Normal mode measures TTFT/E2E from query arrival using vLLM streaming.",
            "Idle lead time is an experimental sweep variable, not inferred from LoCoMo timestamps.",
            "At query arrival the cache snapshot is frozen; incomplete planning never blocks and missing memories use reactive fallback.",
            "Memory materialization uses a deterministic latency model; replace MemoryCostModel with the real store for production numbers.",
            "The gold working-set mode isolates cache latency headroom from retrieval accuracy.",
            "Query resource contention is not modeled and needs a separate concurrent-load experiment.",
        ],
    }


def print_summary(report: Mapping[str, Any]) -> None:
    print("\nLatency summary")
    print(
        "method                     p50 stall  p95 stall  p95 TTFT   p95 E2E    Ready     LatHit    FullHit   Waste"
    )
    print(
        "-------------------------  ---------  ---------  ---------  ---------  --------  --------  --------  --------"
    )
    for row in report.get("method_summaries") or []:
        print(
            f"{str(row.get('method')):25}  "
            f"{format_num(nested_float(row, 'memory_stall_ms', 'p50')):>9}  "
            f"{format_num(nested_float(row, 'memory_stall_ms', 'p95')):>9}  "
            f"{format_num(nested_float(row, 'ttft_ms', 'p95')):>9}  "
            f"{format_num(nested_float(row, 'e2e_ms', 'p95')):>9}  "
            f"{format_num(row.get('ready_at_budget_window')):>8}  "
            f"{format_num(row.get('latency_weighted_hit_rate')):>8}  "
            f"{format_num(row.get('full_query_hit_rate')):>8}  "
            f"{format_num(row.get('waste_rate')):>8}"
        )
    effect = report.get("predictive_effect") or {}
    headroom = report.get("headroom") or {}
    print("\nHeadroom / effect")
    print(
        f"Oracle p95 stall reduction: {format_percent(headroom.get('oracle_max_memory_stall_reduction'))}"
    )
    print(
        f"Predictive hidden latency ratio: {format_percent(effect.get('hidden_latency_ratio'))}"
    )
    print(
        f"Predictive p95 TTFT reduction: {format_percent(effect.get('p95_ttft_reduction'))}"
    )
    sweep = report.get("nonblocking_deadline_sweep") or {}
    if sweep:
        print("\nNon-blocking lead-time sweep")
        print("window      plan-ready  Ready     FullHit   Fallback  p95 stall  p95 TTFT")
        print("----------  ----------  --------  --------  --------  ---------  --------")
        for window, row in sweep.items():
            print(
                f"{window:10}  "
                f"{format_num(row.get('planning_ready_rate')):>10}  "
                f"{format_num(row.get('ready')):>8}  "
                f"{format_num(row.get('full_query_hit_rate')):>8}  "
                f"{format_num(row.get('fallback_rate')):>8}  "
                f"{format_num(nested_float(row, 'memory_stall_ms', 'p95')):>9}  "
                f"{format_num(nested_float(row, 'ttft_ms', 'p95')):>8}"
            )


def summarize_deadline_sweep(
    rows: Sequence[Mapping[str, Any]],
    windows_ms: Sequence[float],
) -> Dict[str, Dict[str, Any]]:
    summary: Dict[str, Dict[str, Any]] = {}
    for window_ms in windows_ms:
        key = format_window_key(window_ms)
        items = [
            dict((row.get("deadline_sweep") or {}).get(key) or {})
            for row in rows
        ]
        items = [item for item in items if item]
        summary[key] = {
            "samples": len(items),
            "planning_ready_rate": mean_field(items, "planning_ready"),
            "ready": mean_field(items, "ready"),
            "full_query_hit_rate": mean_field(items, "full_query_hit"),
            "fallback_rate": mean_field(items, "fallback_used"),
            "memory_stall_ms": latency_stats(items, "memory_stall_ms"),
            "ttft_ms": latency_stats(items, "ttft_ms"),
            "e2e_ms": latency_stats(items, "e2e_ms"),
        }
    return summary


def apply_benchmark_overrides(
    config: Dict[str, Any],
    args: argparse.Namespace,
) -> None:
    config["cache_budget"] = args.cache_budget
    config["retrieval_top_k"] = args.retrieval_top_k
    config["predictor"] = args.predictor
    llm_config = dict(config.get("llm") or {})
    llm_config["base_url"] = args.vllm_url or (
        f"http://{args.vllm_host}:{args.vllm_port}/v1"
    )
    llm_config["model"] = args.vllm_model
    llm_config["timeout"] = args.vllm_timeout
    llm_config["fallback_to_heuristic"] = not args.require_vllm
    config["llm"] = llm_config
    gap_config = dict(config.get("gap_reasoning") or {})
    gap_config["prepared_cache_budget"] = args.cache_budget
    gap_config["compression_enabled"] = not args.disable_compression
    config["gap_reasoning"] = gap_config
    reranker_config = dict(config.get("reranker") or {})
    reranker_config["enabled"] = False
    config["reranker"] = reranker_config


def build_parser() -> argparse.ArgumentParser:
    code_root = Path(__file__).resolve().parent
    project_root = code_root.parent
    default_data = project_root / "preact_demo" / "data" / "locomo" / "locomo10.json"
    if not default_data.exists():
        default_data = code_root / "data" / "locomo" / "locomo10.json"
    parser = argparse.ArgumentParser(
        description=(
            "Measure post-query memory stall, vLLM TTFT/E2E, Ready@B@W, "
            "utility-weighted recall, hit rates, waste, and Oracle headroom."
        )
    )
    parser.add_argument("--locomo-path", default=str(default_data))
    parser.add_argument("--config", default=str(code_root / "configs" / "python_demo.json"))
    parser.add_argument("--output", default=str(code_root / "outputs" / "latency_benchmark.json"))
    parser.add_argument("--limit", type=int, default=20, help="0 means all samples.")
    parser.add_argument("--eval-mode", choices=["time-sliced", "qa"], default="time-sliced")
    parser.add_argument("--working-set", choices=["gold", "reactive"], default="gold")
    parser.add_argument("--reactive-retriever", choices=["vector", "graph"], default="graph")
    parser.add_argument("--predictor", choices=["vllm", "heuristic", "auto"], default="vllm")
    parser.add_argument("--cache-budget", type=int, default=5)
    parser.add_argument("--retrieval-top-k", type=int, default=5)
    parser.add_argument("--disable-compression", action="store_true")
    parser.add_argument("--prequery-window-ms", type=float, default=500.0)
    parser.add_argument(
        "--ready-windows-ms",
        type=parse_float_csv,
        default=parse_float_csv("100,250,500,1000,2000"),
        help=(
            "Comma-separated windows for Ready@B@W curves. These are computed "
            "from the same run and do not trigger extra vLLM calls."
        ),
    )
    parser.add_argument("--memory-load-base-ms", type=float, default=40.0)
    parser.add_argument("--memory-load-tail-ms", type=float, default=180.0)
    parser.add_argument("--memory-load-per-kb-ms", type=float, default=3.0)
    parser.add_argument("--memory-load-max-ms", type=float, default=500.0)
    parser.add_argument("--prefetch-concurrency", type=int, default=4)
    parser.add_argument("--query-encoding-ms", type=float, default=20.0)
    parser.add_argument("--query-rerank-ms", type=float, default=30.0)
    parser.add_argument("--sleep-scale", type=float, default=1.0)
    parser.add_argument("--vllm-url", default=None)
    parser.add_argument("--vllm-host", default="127.0.0.1")
    parser.add_argument("--vllm-port", type=int, default=30000)
    parser.add_argument("--vllm-model", default="../Qwen2.5-7B-Instruct")
    parser.add_argument("--vllm-timeout", type=float, default=120.0)
    parser.add_argument("--require-vllm", action="store_true")
    parser.add_argument("--reader-max-tokens", type=int, default=128)
    parser.add_argument("--reader-temperature", type=float, default=0.0)
    parser.add_argument("--warmup-requests", type=int, default=2)
    parser.add_argument(
        "--skip-generation",
        action="store_true",
        help="Skip vLLM reader calls; useful for local metric smoke tests.",
    )
    parser.add_argument("--seed", type=int, default=7)
    return parser


def warmup_vllm(
    client: VLLMClient,
    requests: int,
    max_tokens: int,
) -> None:
    messages = [
        {"role": "system", "content": "Reply with OK."},
        {"role": "user", "content": "Warm up the model."},
    ]
    for index in range(requests):
        result = stream_chat_timed(client, messages, min(max_tokens, 8), 0.0)
        print(
            f"Warmup {index + 1}/{requests}: "
            f"TTFT={safe_float(result.get('vllm_ttft_ms'), 0.0):.1f} ms",
            flush=True,
        )


def subtract_usage(
    after: Mapping[str, float],
    before: Mapping[str, float],
) -> Dict[str, float]:
    return {
        key: safe_float(after.get(key), 0.0) - safe_float(before.get(key), 0.0)
        for key in set(after) | set(before)
    }


def latency_stats(
    rows: Sequence[Mapping[str, Any]],
    key: str,
) -> Dict[str, Optional[float]]:
    return stats(values(rows, key))


def stats(numbers: Sequence[float]) -> Dict[str, Optional[float]]:
    clean = [float(item) for item in numbers if item is not None and math.isfinite(float(item))]
    if not clean:
        return {"mean": None, "p50": None, "p95": None, "p99": None}
    return {
        "mean": statistics.fmean(clean),
        "p50": percentile(clean, 50),
        "p95": percentile(clean, 95),
        "p99": percentile(clean, 99),
    }


def percentile(numbers: Sequence[float], percent: float) -> float:
    ordered = sorted(float(item) for item in numbers)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percent / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def values(
    rows: Sequence[Mapping[str, Any]],
    key: str,
) -> List[float]:
    result = []
    for row in rows:
        value = row.get(key)
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            result.append(number)
    return result


def mean_field(
    rows: Sequence[Mapping[str, Any]],
    key: str,
) -> Optional[float]:
    numbers = values(rows, key)
    return statistics.fmean(numbers) if numbers else None


def mean_mapping_field(
    rows: Sequence[Mapping[str, Any]],
    key: str,
) -> Dict[str, Optional[float]]:
    nested_keys: List[str] = []
    for row in rows:
        mapping = row.get(key)
        if not isinstance(mapping, Mapping):
            continue
        for nested_key in mapping:
            nested_key_str = str(nested_key)
            if nested_key_str not in nested_keys:
                nested_keys.append(nested_key_str)
    return {
        nested_key: mean_field(
            [
                {nested_key: row.get(key, {}).get(nested_key)}
                for row in rows
                if isinstance(row.get(key), Mapping)
            ],
            nested_key,
        )
        for nested_key in nested_keys
    }


def nested_float(
    row: Mapping[str, Any],
    outer: str,
    inner: str,
) -> float:
    value = (row.get(outer) or {}).get(inner)
    return safe_float(value, 0.0)


def reduction(baseline: float, current: float) -> Optional[float]:
    if baseline <= 0:
        return None
    return 1.0 - current / baseline


def ratio(numerator: float, denominator: float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def parse_float_csv(raw: str) -> List[float]:
    values_list: List[float] = []
    for part in str(raw).split(","):
        value = part.strip()
        if not value:
            continue
        number = float(value)
        if number < 0:
            raise argparse.ArgumentTypeError("ready windows must be non-negative")
        values_list.append(number)
    if not values_list:
        raise argparse.ArgumentTypeError("at least one ready window is required")
    return sorted(set(values_list))


def format_window_key(milliseconds: float) -> str:
    return f"{float(milliseconds):g}ms"


def elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def sleep_ms(milliseconds: float, scale: float) -> None:
    duration = max(0.0, milliseconds) * max(0.0, scale) / 1000.0
    if duration:
        time.sleep(duration)


def estimate_tokens_rough(text: str) -> int:
    return max(0, math.ceil(len(text) / 4))


def unique(items: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        value = str(item)
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def format_num(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return "-"


def format_percent(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value) * 100.0:.1f}%"
    except (TypeError, ValueError):
        return "-"


if __name__ == "__main__":
    main()
