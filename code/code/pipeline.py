from __future__ import annotations

import json
from time import perf_counter
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .gap_reasoning import build_prepared_context, verify_prepared_context
from .evaluators import judge_answer
from .graph_store import (
    EDGE_SIMILAR_TO,
    EDGE_TEMPORAL_NEXT,
    GraphStore,
    build_memory_graph,
)
from .multi_intent_cache import (
    build_multi_intent_bundle,
    merge_with_reactive_results,
    public_bundle_trace,
    route_multi_intent_query,
)
from .predictors import MemoryNeedPredictor
from .utils import (
    MemoryNode,
    Prediction,
    Sample,
    Turn,
    WorkingCache,
    average,
    average_present,
    compute_activation_metrics,
    estimate_cost,
    estimate_importance,
    extract_entities,
    extract_keywords,
    f1_score,
    faithfulness,
    infer_memory_type,
    bleu1_score,
    locomo_answer_f1,
    overlap_score,
    pseudo_judge,
    rouge_l,
    seeded_pick,
    truncate,
)
from .temporal import canonicalize_relative_answer, temporal_context_lines
from .vllm_client import VLLMClient, VLLMError


METHODS = [
    "Random Cache",
    "Recency Cache",
    "Reactive Vector Retrieval",
    "Reactive Graph Retrieval",
    "LongMemEval-style Full-History Prompting",
    "LLM-Predict Cache Only",
    "Pre-query Prepared + Reader",
    "Multi-Intent Prepared + Adaptive Router",
    "LLM-Predict + Fallback",
    "Budgeted Oracle Cache",
    "MemoryNode Oracle",
    "Raw Evidence Oracle",
]


def memory_writer(history: Sequence[Turn]) -> List[MemoryNode]:
    memories: List[MemoryNode] = []
    for turn in history:
        raw_memories = turn.memories or [_memory_from_turn(turn)]
        for raw in raw_memories:
            memory_id = f"m_{len(memories) + 1:03d}"
            content = str(raw.get("content") or f"{turn.speaker}: {turn.text}")
            summary = str(raw.get("summary") or truncate(content))
            keywords = list(raw.get("keywords") or extract_keywords(content, 10))
            entities = list(raw.get("entities") or extract_entities(content))
            memories.append(
                MemoryNode(
                    id=memory_id,
                    memory_type=str(raw.get("type") or infer_memory_type(content)),
                    content=content,
                    summary=summary,
                    keywords=[str(item) for item in keywords],
                    entities=[str(item) for item in entities],
                    segment_id=turn.segment_id,
                    source_turn_id=turn.id,
                    timestamp=turn.timestamp,
                    importance=float(raw.get("importance") or estimate_importance(content)),
                    metadata={
                        **dict(turn.metadata or {}),
                        **dict(raw.get("metadata") or {}),
                    },
                )
            )
    return memories


def insert_cache(
    cache_id: str,
    budget: int,
    prediction: Prediction,
    memory_nodes: Sequence[MemoryNode],
    graph: GraphStore,
) -> WorkingCache:
    memory_by_id = {memory.id: memory for memory in memory_nodes}
    memory_ids: List[str] = []
    for item in prediction.activated_memory_ids:
        if item.id in memory_by_id and item.id not in memory_ids:
            memory_ids.append(item.id)
        if len(memory_ids) >= budget:
            break
    summaries = [memory_by_id[memory_id].summary for memory_id in memory_ids]
    return WorkingCache(
        cache_id=cache_id,
        budget=budget,
        memory_ids=memory_ids,
        summaries=summaries,
        local_subgraph=graph.local_subgraph(memory_ids),
        prediction=prediction,
        metadata={"cache_inserted": True},
    )

 
def verify_cache(
    query: str,
    cache: WorkingCache,
    memory_nodes: Sequence[MemoryNode],
    threshold: float = 0.12,
    top_k: Optional[int] = None,
    llm_client: Optional[VLLMClient] = None,
    config: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    memory_by_id = {memory.id: memory for memory in memory_nodes}
    prepared_context = cache.metadata.get("prepared_context")
    if prepared_context and config is not None:
        verifier = verify_prepared_context(query, prepared_context, llm_client, config)
        if verifier["decision"] in {"use", "partial_use"}:
            selected_ids = [
                memory_id
                for memory_id in verifier.get("selected_memory_ids", [])
                if memory_id in memory_by_id
            ]
            if top_k is not None and top_k > 0:
                selected_ids = selected_ids[:top_k]
            memories = [memory_by_id[memory_id] for memory_id in selected_ids]
            return {
                "use_cache": bool(memories),
                "sufficient": bool(memories),
                "selected_memory_ids": selected_ids,
                "activated_memory_ids": list(prepared_context.get("memory_ids", cache.memory_ids)),
                "memories": memories,
                "scores": list(verifier.get("scores") or []),
                "verifier": verifier,
                "prepared_context_id": prepared_context.get("context_package_id"),
            }

    scored = []
    for memory_id in cache.memory_ids:
        memory = memory_by_id.get(memory_id)
        if memory is None:
            continue
        score = overlap_score(query, memory)
        if score >= threshold:
            scored.append({"memory": memory, "score": score})
    scored.sort(key=lambda item: item["score"], reverse=True)
    if top_k is not None and top_k > 0:
        scored = scored[:top_k]
    memories = [item["memory"] for item in scored]
    return {
        "use_cache": bool(memories),
        "sufficient": bool(memories),
        "selected_memory_ids": [memory.id for memory in memories],
        "activated_memory_ids": list(cache.memory_ids),
        "memories": memories,
        "scores": [
            {"id": item["memory"].id, "score": round(float(item["score"]), 3)}
            for item in scored
        ],
        "verifier": {
            "decision": "use" if memories else "fallback",
            "confidence": 0.0,
            "reason": "Lexical cache verifier fallback.",
            "selected_memory_ids": [memory.id for memory in memories],
            "provider": "lexical",
        },
        "prepared_context_id": None,
    }


def vector_retrieve(query: str, memory_nodes: Sequence[MemoryNode], top_k: int) -> List[MemoryNode]:
    ranked = sorted(
        ((overlap_score(query, memory), memory) for memory in memory_nodes),
        key=lambda item: item[0],
        reverse=True,
    )
    return [memory for _score, memory in ranked[:top_k]]


def graph_retrieve(
    query: str,
    graph: GraphStore,
    memory_nodes: Sequence[MemoryNode],
    top_k: int,
) -> List[MemoryNode]:
    boosts: Dict[str, float] = {}
    for edge in graph.edges:
        if edge.edge_type in {EDGE_SIMILAR_TO, EDGE_TEMPORAL_NEXT}:
            boosts[edge.source] = boosts.get(edge.source, 0.0) + 0.03
            boosts[edge.target] = boosts.get(edge.target, 0.0) + 0.03
    ranked = sorted(
        ((overlap_score(query, memory) + boosts.get(memory.id, 0.0), memory) for memory in memory_nodes),
        key=lambda item: item[0],
        reverse=True,
    )
    return [memory for _score, memory in ranked[:top_k]]


def fallback_retrieve(
    query: str,
    graph: GraphStore,
    memory_nodes: Sequence[MemoryNode],
    config: Mapping[str, Any],
) -> List[MemoryNode]:
    top_k = int(config.get("retrieval_top_k") or config.get("retrievalTopK") or 3)
    retriever = str(config.get("fallback_retriever") or config.get("fallbackRetriever") or "vector")
    if retriever == "graph":
        return graph_retrieve(query, graph, memory_nodes, top_k)
    return vector_retrieve(query, memory_nodes, top_k)


def generate_answer(
    query: str,
    memories: Sequence[MemoryNode],
    llm_client: Optional[VLLMClient] = None,
    config: Optional[Mapping[str, Any]] = None,
) -> str:
    config = config or {}
    llm_config = dict(config.get("llm") or {})
    use_vllm = bool(config.get("answer_with_vllm") or llm_config.get("use_for_answer", False))
    if use_vllm and llm_client is not None and memories:
        context = "\n".join(f"- {memory.summary}" for memory in memories)
        messages = [
            {
                "role": "system",
                "content": "Answer the user question using only the supplied memory context. Be concise.",
            },
            {
                "role": "user",
                "content": f"Memory context:\n{context}\n\nQuestion: {query}",
            },
        ]
        try:
            answer, _usage = llm_client.chat(messages, temperature=0.0, max_tokens=256)
            if answer.strip():
                return answer.strip()
        except VLLMError:
            if not bool(llm_config.get("fallback_to_heuristic", True)):
                raise

    if not memories:
        return f"No verified memory was found for: {query}"
    return "Based on memory: " + " ".join(memory.summary for memory in memories)


def generate_reader_answer(
    query: str,
    memories: Sequence[MemoryNode],
    llm_client: Optional[VLLMClient] = None,
    config: Optional[Mapping[str, Any]] = None,
    sample: Optional[Sample] = None,
    reader_trace: Optional[Dict[str, Any]] = None,
) -> str:
    """Universal downstream QA reader over pre-query prepared memories."""
    config = config or {}
    reader_config = dict(config.get("qa_reader") or {})
    if not memories:
        if reader_trace is not None:
            reader_trace.update({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
        return "No information available."

    if llm_client is None:
        if reader_trace is not None:
            reader_trace.update({"provider": "heuristic", "context_count": len(memories)})
        return " ".join(memory.summary for memory in memories)

    max_memories = int(reader_config.get("max_memories") or 12)
    sorted_memories = sorted(memories, key=lambda item: item.timestamp)[:max_memories]
    context_lines = []
    for memory in sorted_memories:
        protocol_lines = temporal_context_lines(memory.metadata)
        protocol = "\n    ".join(protocol_lines)
        context_lines.append(
            "[{id} | turn={turn} | ordinal={time}]\n"
            "    {protocol}\n"
            "    memory: {content}".format(
                id=memory.id,
                turn=memory.source_turn_id,
                time=memory.timestamp,
                protocol=protocol or "session datetime: unavailable",
                content=memory.content,
            )
        )
    context = "\n".join(context_lines)
    messages = [
        {
            "role": "system",
            "content": (
                "You are the downstream QA reader for a pre-query memory system. "
                "Use only the supplied prepared memories. Return only the shortest "
                "answer phrase or sentence. Copy exact wording from the memories "
                "when possible. If the memories do not contain the answer, return "
                "\"No information available.\" Dates in the deterministic-time "
                "annotations are authoritative: answer temporal questions with the "
                "resolved absolute date or displayed period, never with yesterday, "
                "last week, last Saturday, or next month. Image query/caption fields "
                "are evidence, not decoration."
            ),
        },
        {
            "role": "user",
            "content": (
                "Prepared memories:\n"
                f"{context}\n\n"
                f"Question: {query}\n\n"
                f"Question reference datetime: "
                f"{(sample.metadata.get('reference_date_time') if sample else None) or 'unavailable'}\n\n"
                "Answer:"
            ),
        },
    ]
    if reader_trace is not None:
        reader_trace.update(
            {
                "provider": "vllm",
                "prompt_characters": sum(len(item["content"]) for item in messages),
                "context_count": len(sorted_memories),
            }
        )
    try:
        answer, _usage = llm_client.chat(
            messages,
            temperature=float(reader_config.get("temperature") or 0.0),
            max_tokens=int(reader_config.get("max_tokens") or 128),
        )
        if reader_trace is not None:
            reader_trace.update(dict(_usage or {}))
        answer = answer.strip()
        if answer:
            if "ANSWER:" in answer:
                answer = answer.rsplit("ANSWER:", 1)[-1].strip()
            return canonicalize_relative_answer(answer, sorted_memories)
    except VLLMError:
        if not bool(reader_config.get("fallback_to_heuristic", True)):
            raise
    return " ".join(memory.summary for memory in memories)


def generate_full_history_prompt_answer(
    sample: Sample,
    llm_client: Optional[VLLMClient],
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    """LongMemEval-style long-context prompting adapted to LoCoMo turns."""
    baseline_config = dict(config.get("full_history_prompting") or {})
    sessions: List[Dict[str, Any]] = []
    current_by_id: Dict[str, Dict[str, Any]] = {}
    for turn in sample.history:
        session = current_by_id.get(turn.segment_id)
        if session is None:
            session = {
                "session_id": turn.segment_id,
                "timestamp": turn.metadata.get("session_date_time") or turn.segment_summary,
                "turns": [],
            }
            current_by_id[turn.segment_id] = session
            sessions.append(session)
        item: Dict[str, Any] = {
            "speaker": turn.speaker,
            "content": turn.metadata.get("raw_text") or turn.text,
        }
        if turn.metadata.get("image_query"):
            item["image_query"] = turn.metadata["image_query"]
        if turn.metadata.get("blip_caption"):
            item["image_caption"] = turn.metadata["blip_caption"]
        session["turns"].append(item)

    history_json = json.dumps(sessions, ensure_ascii=False, separators=(",", ":"))
    messages = [
        {
            "role": "system",
            "content": (
                "You answer questions about a timestamped conversation history. "
                "First identify the relevant sessions and facts, then reason over "
                "them internally. Use only the history. Return only the shortest "
                "final answer, without reasoning. If unsupported, return "
                "\"No information available.\" Resolve relative dates using the "
                "timestamp of the session containing the statement."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Timestamped conversation history (JSON):\n{history_json}\n\n"
                f"Question: {sample.question}\n\nAnswer:"
            ),
        },
    ]
    trace: Dict[str, Any] = {
        "baseline": "LongMemEval-style full-history-session / JSON / con",
        "adaptation_dataset": "LoCoMo",
        "history_turn_count": len(sample.history),
        "history_session_count": len(sessions),
        "prompt_characters": sum(len(item["content"]) for item in messages),
        "uses_retrieval": False,
        "uses_prequery_planning": False,
    }
    started = perf_counter()
    if llm_client is None:
        answer = "No information available."
        trace["provider"] = "unavailable"
    else:
        try:
            answer, usage = llm_client.chat(
                messages,
                temperature=float(baseline_config.get("temperature") or 0.0),
                max_tokens=int(baseline_config.get("max_tokens") or 128),
            )
            trace.update(dict(usage or {}))
            trace["provider"] = "vllm"
        except VLLMError:
            if not bool(baseline_config.get("fallback_to_heuristic", False)):
                raise
            answer = "No information available."
            trace["provider"] = "fallback"
    trace["reader_e2e_latency_ms"] = _elapsed_ms(started)
    answer = str(answer or "").strip()
    if "ANSWER:" in answer:
        answer = answer.rsplit("ANSWER:", 1)[-1].strip()
    return {"answer": answer or "No information available.", "reader_trace": trace}


def label_gold_evidence(
    evidence_terms: Sequence[str],
    evidence_turn_ids: Sequence[str],
    memory_nodes: Sequence[MemoryNode],
) -> List[str]:
    turn_ids = set(evidence_turn_ids or [])
    if turn_ids:
        return [memory.id for memory in memory_nodes if memory.source_turn_id in turn_ids]

    terms = set()
    for term in evidence_terms or []:
        terms.update(extract_keywords(term, 10))
    if not terms:
        return []

    evidence = []
    for memory in memory_nodes:
        memory_terms = set(extract_keywords(memory.searchable_text(), 30))
        if len(memory_terms & terms) >= 2:
            evidence.append(memory.id)
    return evidence


def raw_evidence_memories(sample: Sample) -> List[MemoryNode]:
    """Build an uncompressed oracle context directly from annotated source turns."""
    gold_turn_ids = set(str(item) for item in sample.gold_evidence_turn_ids)
    selected: List[MemoryNode] = []
    for turn in sample.history:
        if turn.id not in gold_turn_ids:
            continue
        metadata = dict(turn.metadata or {})
        evidence_parts = [f"{turn.speaker}: {metadata.get('raw_text') or turn.text}"]
        if metadata.get("image_query"):
            evidence_parts.append(f"image query: {metadata['image_query']}")
        if metadata.get("blip_caption"):
            evidence_parts.append(f"image caption: {metadata['blip_caption']}")
        content = "\n".join(evidence_parts)
        selected.append(
            MemoryNode(
                id=f"raw_{turn.id}",
                memory_type=infer_memory_type(content),
                content=content,
                summary=content,
                keywords=extract_keywords(content, 20),
                entities=extract_entities(content),
                segment_id=turn.segment_id,
                source_turn_id=turn.id,
                timestamp=turn.timestamp,
                importance=1.0,
                metadata=metadata,
            )
        )
    return selected


def run_evaluation(
    samples: Sequence[Sample],
    config: Mapping[str, Any],
    predictor: MemoryNeedPredictor,
    llm_client: Optional[VLLMClient] = None,
    judge_client: Optional[VLLMClient] = None,
) -> List[Dict[str, Any]]:
    config = dict(config)
    config["_judge_client"] = judge_client
    results: Dict[str, List[Dict[str, Any]]] = {method: [] for method in METHODS}
    context_cache: Dict[str, Dict[str, Any]] = {}

    total_samples = len(samples)
    show_progress = bool(config.get("show_progress", True))
    progress_bar = None
    if show_progress:
        try:
            from tqdm.auto import tqdm  # type: ignore

            progress_bar = tqdm(
                total=total_samples,
                desc="LoCoMo",
                unit="sample",
                dynamic_ncols=True,
            )
        except ImportError:
            progress_bar = None

    for index, sample in enumerate(samples, start=1):
        if progress_bar is not None:
            progress_bar.set_postfix_str(f"running {sample.id}", refresh=True)
        elif show_progress:
            print(
                f"[eval] start {index}/{total_samples} sample={sample.id} history_turns={len(sample.history)}",
                flush=True,
            )
        context_key = sample.history_cache_key or sample.id
        context = context_cache.get(context_key)
        if context is None:
            memory_nodes = memory_writer(sample.history)
            graph = build_memory_graph(
                memory_nodes,
                sample.history,
                similarity_threshold=float(config.get("similarity_threshold") or config.get("similarityThreshold") or 0.28),
            )
            budget = int(config.get("cache_budget") or config.get("cacheBudget") or 3)
            prediction = predictor.predict(sample.history, memory_nodes, graph, budget)
            cache = insert_cache(
                cache_id=f"cache_{context_key}",
                budget=budget,
                prediction=prediction,
                memory_nodes=memory_nodes,
                graph=graph,
            )
            gap_config = dict(config.get("gap_reasoning") or {})
            if bool(gap_config.get("enabled", True)):
                prepared_context = build_prepared_context(
                    context_key=context_key,
                    history=sample.history,
                    memory_nodes=memory_nodes,
                    graph=graph,
                    prediction=prediction,
                    llm_client=llm_client,
                    config=config,
                )
                cache.metadata["prepared_context"] = prepared_context
            multi_config = dict(config.get("multi_intent_cache") or {})
            if bool(multi_config.get("enabled", True)):
                multi_graph = graph.clone()
                cache.metadata["multi_intent_bundle"] = build_multi_intent_bundle(
                    context_key=context_key,
                    history=sample.history,
                    memory_nodes=memory_nodes,
                    graph=multi_graph,
                    prediction=prediction,
                    config=config,
                )
            context = {"memory_nodes": memory_nodes, "graph": graph, "cache": cache}
            context_cache[context_key] = context

        memory_nodes = context["memory_nodes"]
        graph = context["graph"]
        cache = context["cache"]
        budget = int(config.get("cache_budget") or config.get("cacheBudget") or 3)
        evidence = label_gold_evidence(
            sample.evidence_terms,
            sample.gold_evidence_turn_ids,
            memory_nodes,
        )
        ground_truth = build_ground_truth_trace(sample, memory_nodes, evidence)

        random_ids = seeded_pick([memory.id for memory in memory_nodes], budget, int(config.get("random_seed") or config.get("randomSeed") or 7))
        _evaluate_cache_method("Random Cache", sample, memory_nodes, random_ids, evidence, config, results, llm_client)

        recency_ids = [
            memory.id
            for memory in sorted(memory_nodes, key=lambda item: item.timestamp, reverse=True)[:budget]
        ]
        _evaluate_cache_method("Recency Cache", sample, memory_nodes, recency_ids, evidence, config, results, llm_client)

        retrieval_top_k = int(config.get("retrieval_top_k") or config.get("retrievalTopK") or 3)
        retrieval_start = perf_counter()
        vector_selected = vector_retrieve(sample.question, memory_nodes, retrieval_top_k)
        vector_retrieval_latency_ms = _elapsed_ms(retrieval_start)
        _evaluate_retrieval_method(
            method="Reactive Vector Retrieval",
            sample=sample,
            selected=vector_selected,
            activated=None,
            evidence=evidence,
            fallback_used=True,
            idle_prediction_used=False,
            results=results,
            config=config,
            llm_client=llm_client,
            query_retrieval_latency_ms=vector_retrieval_latency_ms,
        )

        retrieval_start = perf_counter()
        graph_selected = graph_retrieve(sample.question, graph, memory_nodes, retrieval_top_k)
        graph_retrieval_latency_ms = _elapsed_ms(retrieval_start)
        _evaluate_retrieval_method(
            method="Reactive Graph Retrieval",
            sample=sample,
            selected=graph_selected,
            activated=None,
            evidence=evidence,
            fallback_used=True,
            idle_prediction_used=False,
            results=results,
            config=config,
            llm_client=llm_client,
            query_retrieval_latency_ms=graph_retrieval_latency_ms,
        )

        if bool((config.get("full_history_prompting") or {}).get("enabled", False)):
            full_history_result = generate_full_history_prompt_answer(
                sample,
                llm_client,
                config,
            )
            _evaluate_retrieval_method(
                method="LongMemEval-style Full-History Prompting",
                sample=sample,
                selected=memory_nodes,
                activated=[memory.id for memory in memory_nodes],
                evidence=evidence,
                fallback_used=False,
                idle_prediction_used=False,
                results=results,
                config=config,
                llm_client=llm_client,
                answer_mode="full_history_prompt",
                answer_override=str(full_history_result["answer"]),
                reader_trace_override=dict(full_history_result["reader_trace"]),
                query_retrieval_latency_ms=0.0,
                extra={
                    "uses_full_history": True,
                    "uses_query_for_retrieval": False,
                    "uses_prequery_planning": False,
                },
            )

        retrieval_start = perf_counter()
        verified = verify_cache(
            sample.question,
            cache,
            memory_nodes,
            threshold=float(config.get("verifier_threshold") or config.get("verifierThreshold") or 0.12),
            llm_client=llm_client,
            config=config,
        )
        verifier_retrieval_latency_ms = _elapsed_ms(retrieval_start)
        proactive_activation = compute_activation_metrics(
            verified.get("activated_memory_ids", cache.memory_ids),
            evidence,
        )
        cache_only_trace = build_llm_trace(
            sample=sample,
            memory_nodes=memory_nodes,
            ground_truth=ground_truth,
            cache=cache,
            verified=verified,
            selected=verified["memories"],
            final_method="cache_only",
            fallback_used=False,
            final_metrics=compute_activation_metrics(
                [memory.id for memory in verified["memories"]],
                evidence,
            ),
            proactive_metrics=proactive_activation,
        )
        _evaluate_retrieval_method(
            method="LLM-Predict Cache Only",
            sample=sample,
            selected=verified["memories"],
            activated=None,
            evidence=evidence,
            fallback_used=False,
            idle_prediction_used=True,
            results=results,
            config=config,
            llm_client=llm_client,
            extra={
                "prepared_context_id": verified.get("prepared_context_id"),
                "verifier": verified.get("verifier"),
                "prepared_context": cache.metadata.get("prepared_context"),
                "proactive_precision": proactive_activation["precision"],
                "proactive_recall": proactive_activation["recall"],
                "proactive_hit_rate": proactive_activation["hit_rate"],
                "proactive_full_cover_rate": proactive_activation["full_cover_rate"],
                "proactive_wasted_rate": proactive_activation["wasted_rate"],
                "trace": cache_only_trace,
            },
            query_retrieval_latency_ms=verifier_retrieval_latency_ms,
        )

        retrieval_start = perf_counter()
        memory_by_id = {memory.id: memory for memory in memory_nodes}
        prepared_context = cache.metadata.get("prepared_context") or {}
        prepared_ids = [
            memory_id
            for memory_id in prepared_context.get("memory_ids", cache.memory_ids)
            if memory_id in memory_by_id
        ]
        prepared_selected = [memory_by_id[memory_id] for memory_id in prepared_ids]
        prequery_retrieval_latency_ms = _elapsed_ms(retrieval_start)
        prepared_metrics = compute_activation_metrics(prepared_ids, evidence)
        prequery_trace = build_llm_trace(
            sample=sample,
            memory_nodes=memory_nodes,
            ground_truth=ground_truth,
            cache=cache,
            verified=verified,
            selected=prepared_selected,
            final_method="prequery_prepared_reader",
            fallback_used=False,
            final_metrics=prepared_metrics,
            proactive_metrics=prepared_metrics,
        )
        _evaluate_retrieval_method(
            method="Pre-query Prepared + Reader",
            sample=sample,
            selected=prepared_selected,
            activated=prepared_ids,
            evidence=evidence,
            fallback_used=False,
            idle_prediction_used=True,
            results=results,
            config=config,
            llm_client=llm_client,
            answer_mode="qa_reader",
            extra={
                "prepared_context_id": verified.get("prepared_context_id"),
                "verifier": verified.get("verifier"),
                "prepared_context": prepared_context,
                "proactive_precision": prepared_metrics["precision"],
                "proactive_recall": prepared_metrics["recall"],
                "proactive_hit_rate": prepared_metrics["hit_rate"],
                "proactive_full_cover_rate": prepared_metrics["full_cover_rate"],
                "proactive_wasted_rate": prepared_metrics["wasted_rate"],
                "uses_query_for_retrieval": False,
                "reader_uses_query": True,
                "verifier_ablation_available": bool((verified.get("verifier") or {}).get("selected_memory_ids")),
                "trace": prequery_trace,
            },
            query_retrieval_latency_ms=prequery_retrieval_latency_ms,
        )

        multi_bundle = cache.metadata.get("multi_intent_bundle")
        if multi_bundle:
            retrieval_start = perf_counter()
            multi_route = route_multi_intent_query(
                query=sample.question,
                bundle=multi_bundle,
                memory_nodes=memory_nodes,
                config=config,
            )
            multi_decision = str(multi_route.get("decision") or "native_rag")
            multi_prepared_ids = [
                memory_id
                for memory_id in multi_route.get("prepared_memory_ids") or []
                if memory_id in memory_by_id
            ]
            reactive_repair_ids: List[str] = []
            if multi_decision in {"partial_repair", "native_rag"}:
                reactive_candidates = fallback_retrieve(
                    sample.question,
                    graph,
                    memory_nodes,
                    config,
                )
                if multi_decision == "partial_repair":
                    repair_k = max(1, int(multi_route.get("repair_top_k") or 1))
                    reader_config = dict(config.get("qa_reader") or {})
                    reader_limit = max(
                        len(multi_prepared_ids),
                        int(reader_config.get("max_memories") or 12),
                    )
                    retrieval_limit = min(
                        reader_limit,
                        len(multi_prepared_ids) + repair_k,
                    )
                    multi_selected, reactive_repair_ids = merge_with_reactive_results(
                        prepared_ids=multi_prepared_ids,
                        reactive_memories=reactive_candidates,
                        memory_nodes=memory_nodes,
                        limit=retrieval_limit,
                    )
                else:
                    multi_selected = list(reactive_candidates[:retrieval_top_k])
                    reactive_repair_ids = [memory.id for memory in multi_selected]
            else:
                multi_selected = [
                    memory_by_id[memory_id]
                    for memory_id in multi_prepared_ids[:retrieval_top_k]
                ]
            multi_retrieval_latency_ms = _elapsed_ms(retrieval_start)
            multi_physical_ids = [
                memory_id
                for memory_id in multi_bundle.get("physical_memory_ids") or []
                if memory_id in memory_by_id
            ]
            multi_proactive_metrics = compute_activation_metrics(
                multi_physical_ids,
                evidence,
            )
            multi_final_metrics = compute_activation_metrics(
                [memory.id for memory in multi_selected],
                evidence,
            )
            multi_trace = {
                "sample": {
                    "sample_id": sample.id,
                    "actual_query": sample.question,
                    "gold_answer": sample.answer,
                    "gold_evidence_memory_ids": list(evidence),
                    "gold_evidence_memories": memory_refs(evidence, memory_nodes),
                },
                "idle_time_planning": public_bundle_trace(multi_bundle),
                "query_time_routing": multi_route,
                "final_selection": {
                    "decision": multi_decision,
                    "selected_head_ids": list(multi_route.get("selected_head_ids") or []),
                    "prepared_memory_ids": multi_prepared_ids,
                    "reactive_repair_memory_ids": reactive_repair_ids,
                    "final_memory_ids": [memory.id for memory in multi_selected],
                    "final_memories": memory_refs(
                        [memory.id for memory in multi_selected],
                        memory_nodes,
                    ),
                    "metrics": multi_final_metrics,
                },
            }
            _evaluate_retrieval_method(
                method="Multi-Intent Prepared + Adaptive Router",
                sample=sample,
                selected=multi_selected,
                activated=None,
                evidence=evidence,
                fallback_used=multi_decision in {"partial_repair", "native_rag"},
                idle_prediction_used=True,
                results=results,
                config=config,
                llm_client=llm_client,
                answer_mode="qa_reader",
                extra={
                    "route_decision": multi_decision,
                    "selected_head_ids": list(multi_route.get("selected_head_ids") or []),
                    "reactive_repair_memory_ids": reactive_repair_ids,
                    "prepared_physical_memory_ids": multi_physical_ids,
                    "prepared_physical_cache_size": len(multi_physical_ids),
                    "logical_branch_memory_count": int(
                        multi_bundle.get("logical_branch_memory_count") or 0
                    ),
                    "shared_memory_ids": list(multi_bundle.get("shared_memory_ids") or []),
                    "shared_fact_ids": list(multi_bundle.get("shared_fact_ids") or []),
                    "proactive_precision": multi_proactive_metrics["precision"],
                    "proactive_recall": multi_proactive_metrics["recall"],
                    "proactive_hit_rate": multi_proactive_metrics["hit_rate"],
                    "proactive_full_cover_rate": multi_proactive_metrics["full_cover_rate"],
                    "proactive_wasted_rate": multi_proactive_metrics["wasted_rate"],
                    "uses_query_for_preparation": False,
                    "uses_query_for_routing": True,
                    "reader_uses_query": True,
                    "multi_intent_trace": multi_trace,
                },
                query_retrieval_latency_ms=multi_retrieval_latency_ms,
            )

        fallback_retrieval_latency_ms = 0.0
        if verified["sufficient"]:
            selected = verified["memories"]
        else:
            retrieval_start = perf_counter()
            selected = fallback_retrieve(
                sample.question,
                graph,
                memory_nodes,
                config,
            )
            fallback_retrieval_latency_ms = _elapsed_ms(retrieval_start)
        fallback_trace = build_llm_trace(
            sample=sample,
            memory_nodes=memory_nodes,
            ground_truth=ground_truth,
            cache=cache,
            verified=verified,
            selected=selected,
            final_method="cache_plus_fallback",
            fallback_used=not verified["sufficient"],
            final_metrics=compute_activation_metrics(
                [memory.id for memory in selected],
                evidence,
            ),
            proactive_metrics=proactive_activation,
        )
        _evaluate_retrieval_method(
            method="LLM-Predict + Fallback",
            sample=sample,
            selected=selected,
            activated=None,
            evidence=evidence,
            fallback_used=not verified["sufficient"],
            idle_prediction_used=True,
            results=results,
            config=config,
            llm_client=llm_client,
            extra={
                "prepared_context_id": verified.get("prepared_context_id"),
                "verifier": verified.get("verifier"),
                "prepared_context": cache.metadata.get("prepared_context"),
                "proactive_precision": proactive_activation["precision"],
                "proactive_recall": proactive_activation["recall"],
                "proactive_hit_rate": proactive_activation["hit_rate"],
                "proactive_full_cover_rate": proactive_activation["full_cover_rate"],
                "proactive_wasted_rate": proactive_activation["wasted_rate"],
                "trace": fallback_trace,
            },
            query_retrieval_latency_ms=verifier_retrieval_latency_ms + fallback_retrieval_latency_ms,
        )

        retrieval_start = perf_counter()
        oracle_ids = evidence[:budget]
        oracle_selected = [memory for memory in memory_nodes if memory.id in set(oracle_ids)]
        oracle_retrieval_latency_ms = _elapsed_ms(retrieval_start)
        _evaluate_retrieval_method(
            method="Budgeted Oracle Cache",
            sample=sample,
            selected=oracle_selected,
            activated=oracle_ids,
            evidence=evidence,
            fallback_used=False,
            idle_prediction_used=False,
            results=results,
            config=config,
            llm_client=llm_client,
            query_retrieval_latency_ms=oracle_retrieval_latency_ms,
            extra={"oracle_uses_gold_evidence": True},
        )

        full_oracle_selected = [
            memory for memory in memory_nodes if memory.id in set(evidence)
        ]
        _evaluate_retrieval_method(
            method="MemoryNode Oracle",
            sample=sample,
            selected=full_oracle_selected,
            activated=evidence,
            evidence=evidence,
            fallback_used=False,
            idle_prediction_used=False,
            results=results,
            config=config,
            llm_client=llm_client,
            query_retrieval_latency_ms=0.0,
            extra={
                "oracle_uses_gold_evidence": True,
                "oracle_budget_limited": False,
                "oracle_available": bool(evidence),
            },
        )

        raw_oracle_selected = raw_evidence_memories(sample)
        _evaluate_retrieval_method(
            method="Raw Evidence Oracle",
            sample=sample,
            selected=raw_oracle_selected,
            activated=evidence,
            evidence=evidence,
            fallback_used=False,
            idle_prediction_used=False,
            results=results,
            config=config,
            llm_client=llm_client,
            query_retrieval_latency_ms=0.0,
            extra={
                "oracle_uses_raw_annotated_turns": True,
                "oracle_budget_limited": False,
                "oracle_available": bool(raw_oracle_selected),
            },
        )

        if show_progress:
            final_ids = [memory.id for memory in selected]
            verifier = verified.get("verifier") or {}
            message = (
                "[eval] done "
                f"{index}/{total_samples} sample={sample.id} "
                f"proactive_recall={proactive_activation['recall']:.3f} "
                f"final_ids={','.join(final_ids) or '-'} "
                f"verifier={verifier.get('provider', '-')}/{verifier.get('decision', '-')}"
            )
            if progress_bar is not None:
                progress_bar.set_postfix_str(
                    f"{sample.id} recall={proactive_activation['recall']:.3f} "
                    f"verifier={verifier.get('provider', '-')}",
                    refresh=False,
                )
                progress_bar.update(1)
                progress_bar.write(message)
            else:
                print(message, flush=True)

    if progress_bar is not None:
        progress_bar.close()

    return _summarize(results, int(config.get("cache_budget") or config.get("cacheBudget") or 3))


def build_ground_truth_trace(
    sample: Sample,
    memory_nodes: Sequence[MemoryNode],
    evidence_ids: Sequence[str],
) -> Dict[str, Any]:
    return {
        "gold_answer": sample.answer,
        "gold_evidence_turn_ids": list(sample.gold_evidence_turn_ids),
        "gold_evidence_memory_ids": list(evidence_ids),
        "gold_evidence_memories": memory_refs(evidence_ids, memory_nodes),
        "evidence_terms": list(sample.evidence_terms),
    }


def build_llm_trace(
    sample: Sample,
    memory_nodes: Sequence[MemoryNode],
    ground_truth: Mapping[str, Any],
    cache: WorkingCache,
    verified: Mapping[str, Any],
    selected: Sequence[MemoryNode],
    final_method: str,
    fallback_used: bool,
    final_metrics: Mapping[str, float],
    proactive_metrics: Mapping[str, float],
) -> Dict[str, Any]:
    prepared_context = cache.metadata.get("prepared_context") or {}
    predicted_ids = [item.id for item in cache.prediction.activated_memory_ids]
    prepared_ids = list(prepared_context.get("memory_ids") or [])
    verifier_selected_ids = list((verified.get("verifier") or {}).get("selected_memory_ids") or [])
    final_ids = [memory.id for memory in selected]
    return {
        "sample": {
            "sample_id": sample.id,
            "history_cache_key": sample.history_cache_key,
            "question": sample.question,
            "metadata": sample.metadata,
            "history_turn_count": len(sample.history),
            "history_tail": [
                {
                    "turn_id": turn.id,
                    "speaker": turn.speaker,
                    "text": truncate(turn.text, 220),
                    "segment_id": turn.segment_id,
                    "timestamp": turn.timestamp,
                }
                for turn in sample.history[-5:]
            ],
        },
        "ground_truth": dict(ground_truth),
        "prediction": {
            "predicted_future_intents": list(cache.prediction.predicted_future_intents),
            "activated_memory_ids": [item.to_dict() for item in cache.prediction.activated_memory_ids],
            "metadata": dict(cache.prediction.metadata),
            "activated_memories": memory_refs(predicted_ids, memory_nodes),
        },
        "cache": {
            "cache_id": cache.cache_id,
            "budget": cache.budget,
            "cache_memory_ids": list(cache.memory_ids),
            "cache_memories": memory_refs(cache.memory_ids, memory_nodes),
        },
        "gap_reasoning": {
            "context_package_id": prepared_context.get("context_package_id"),
            "target_intent": prepared_context.get("target_intent"),
            "possible_user_query": prepared_context.get("possible_user_query"),
            "selected_paths": prepared_context.get("selected_paths", []),
            "executed_paths": prepared_context.get("executed_paths", []),
            "support_check": prepared_context.get("support_check", {}),
            "gaps": prepared_context.get("gaps", []),
            "repair_evidence": prepared_context.get("evidence", []),
            "bindings": prepared_context.get("bindings", []),
            "usable_claims": prepared_context.get("usable_claims", []),
            "candidate_memory_ids": prepared_context.get("candidate_memory_ids", []),
            "candidate_memories": memory_refs(prepared_context.get("candidate_memory_ids", []), memory_nodes),
            "compression": prepared_context.get("compression", {}),
            "prepared_memory_ids": prepared_ids,
            "prepared_memories": memory_refs(prepared_ids, memory_nodes),
            "summary": prepared_context.get("summary"),
            "risk": prepared_context.get("risk"),
        },
        "verifier": {
            **dict(verified.get("verifier") or {}),
            "selected_memories": memory_refs(verifier_selected_ids, memory_nodes),
            "scores": list(verified.get("scores") or []),
        },
        "final_selection": {
            "method": final_method,
            "fallback_used": fallback_used,
            "selected_memory_ids": final_ids,
            "selected_memories": memory_refs(final_ids, memory_nodes),
            "metrics": dict(final_metrics),
        },
        "proactive_metrics": dict(proactive_metrics),
    }


def memory_refs(memory_ids: Sequence[str], memory_nodes: Sequence[MemoryNode]) -> List[Dict[str, Any]]:
    by_id = {memory.id: memory for memory in memory_nodes}
    refs: List[Dict[str, Any]] = []
    for memory_id in memory_ids:
        memory = by_id.get(memory_id)
        if memory is None:
            continue
        refs.append(
            {
                "id": memory.id,
                "source_turn_id": memory.source_turn_id,
                "timestamp": memory.timestamp,
                "segment_id": memory.segment_id,
                "type": memory.memory_type,
                "summary": memory.summary,
                "content": truncate(memory.content, 400),
                "keywords": list(memory.keywords),
                "entities": list(memory.entities),
                "importance": memory.importance,
            }
        )
    return refs


def _memory_from_turn(turn: Turn) -> Dict[str, Any]:
    content = f"{turn.speaker}: {turn.text}"
    return {
        "type": infer_memory_type(content),
        "content": content,
        "summary": truncate(content),
        "keywords": extract_keywords(content, 10),
        "entities": extract_entities(content),
        "importance": estimate_importance(content),
    }


def _elapsed_ms(start: float) -> float:
    return (perf_counter() - start) * 1000.0


def _evaluate_cache_method(
    method: str,
    sample: Sample,
    memory_nodes: Sequence[MemoryNode],
    cache_ids: Sequence[str],
    evidence: Sequence[str],
    config: Mapping[str, Any],
    results: Dict[str, List[Dict[str, Any]]],
    llm_client: Optional[VLLMClient],
) -> None:
    prediction = Prediction(predicted_future_intents=[], activated_memory_ids=[])
    graph = GraphStore()
    cache = insert_cache(
        cache_id=f"{method.lower().replace(' ', '_')}_{sample.id}",
        budget=len(cache_ids),
        prediction=prediction,
        memory_nodes=memory_nodes,
        graph=graph,
    )
    cache.memory_ids = list(cache_ids)
    cache.summaries = [
        memory.summary for memory in memory_nodes if memory.id in set(cache_ids)
    ]
    retrieval_start = perf_counter()
    verified = verify_cache(
        sample.question,
        cache,
        memory_nodes,
        threshold=float(config.get("verifier_threshold") or config.get("verifierThreshold") or 0.12),
    )
    query_retrieval_latency_ms = _elapsed_ms(retrieval_start)
    _evaluate_retrieval_method(
        method=method,
        sample=sample,
        selected=verified["memories"],
        activated=list(cache_ids),
        evidence=evidence,
        fallback_used=False,
        idle_prediction_used=False,
        results=results,
        config=config,
        llm_client=llm_client,
        query_retrieval_latency_ms=query_retrieval_latency_ms,
    )


def _evaluate_retrieval_method(
    method: str,
    sample: Sample,
    selected: Sequence[MemoryNode],
    activated: Optional[Sequence[str]],
    evidence: Sequence[str],
    fallback_used: bool,
    idle_prediction_used: bool,
    results: Dict[str, List[Dict[str, Any]]],
    config: Mapping[str, Any],
    llm_client: Optional[VLLMClient],
    extra: Optional[Mapping[str, Any]] = None,
    answer_mode: str = "default",
    query_retrieval_latency_ms: Optional[float] = None,
    answer_override: Optional[str] = None,
    reader_trace_override: Optional[Mapping[str, Any]] = None,
) -> None:
    reader_config = dict(config.get("qa_reader") or {})
    use_reader = answer_mode == "qa_reader" or (
        answer_mode == "default" and bool(reader_config.get("use_for_all_methods", False))
    )
    effective_answer_mode = (
        answer_mode if answer_override is not None else ("qa_reader" if use_reader else "default")
    )
    reader_trace: Dict[str, Any] = dict(reader_trace_override or {})
    reader_started = perf_counter()
    if answer_override is not None:
        answer = answer_override
    elif use_reader:
        answer = generate_reader_answer(
            sample.question,
            selected,
            llm_client=llm_client,
            config=config,
            sample=sample,
            reader_trace=reader_trace,
        )
    else:
        answer = generate_answer(sample.question, selected, llm_client=llm_client, config=config)
    reader_e2e_latency_ms = float(
        reader_trace.get("reader_e2e_latency_ms")
        or _elapsed_ms(reader_started)
    )
    selected_ids = [memory.id for memory in selected]
    activation = compute_activation_metrics(
        activated if activated is not None else selected_ids,
        evidence,
    )
    costs = estimate_cost(
        history=sample.history,
        query=sample.question,
        memories=selected,
        generated_answer=answer,
        idle_prediction_used=idle_prediction_used,
        fallback_used=fallback_used,
    )
    category = sample.metadata.get("category")
    category_names = {
        1: "multi_hop",
        2: "temporal",
        3: "open_domain",
        4: "single_hop",
        5: "adversarial_unanswerable",
    }
    judge_config = dict((config.get("evaluation") or {}).get("judge") or {})
    judge_methods = set(judge_config.get("methods") or [])
    run_judge = bool(judge_config.get("enabled", False)) and (
        not judge_methods or method in judge_methods
    )
    judge_client = config.get("_judge_client")
    judge_details: Dict[str, Any] = {}
    for protocol in judge_config.get("protocols") or ["mem0_paper_v1"]:
        if run_judge:
            judge_details[str(protocol)] = judge_answer(
                question=sample.question,
                reference=sample.answer,
                candidate=answer,
                category=category,
                client=judge_client if isinstance(judge_client, VLLMClient) else None,
                protocol=str(protocol),
                config=judge_config,
            )
    row = {
        "sample_id": sample.id,
        "category": category,
        "category_name": category_names.get(int(category or 0), "unknown"),
        "precision": activation["precision"],
        "recall": activation["recall"],
        "hit_rate": activation["hit_rate"],
        "full_cover_rate": activation["full_cover_rate"],
        "wasted_rate": activation["wasted_rate"],
        "fallback_rate": 1.0 if fallback_used else 0.0,
        "f1": f1_score(answer, sample.answer),
        "official_f1": locomo_answer_f1(answer, sample.answer, category),
        "temporal_f1": locomo_answer_f1(answer, sample.answer, category) if str(category) == "2" else None,
        "bleu1": bleu1_score(answer, sample.answer),
        "rouge_l": rouge_l(answer, sample.answer),
        "pseudo_judge": pseudo_judge(answer, sample.answer),
        "llm_judge": (judge_details.get("mem0_paper_v1") or {}).get("score"),
        "strict_judge": (judge_details.get("longmemeval_strict_v1") or {}).get("score"),
        "judge_details": judge_details,
        "faithfulness": faithfulness(answer, selected),
        "query_retrieval_latency_ms": float(query_retrieval_latency_ms or 0.0),
        "reader_e2e_latency_ms": reader_e2e_latency_ms,
        "reader_prompt_tokens": reader_trace.get("prompt_tokens"),
        "reader_completion_tokens": reader_trace.get("completion_tokens"),
        "reader_total_tokens": reader_trace.get("total_tokens"),
        "reader_prompt_characters": reader_trace.get("prompt_characters"),
        "reader_trace": reader_trace,
        "query_time_latency_ms": costs["query_time_latency_ms"],
        "idle_time_cost": costs["idle_time_cost"],
        "total_tokens": costs["total_tokens"],
        "selected_memory_ids": selected_ids,
        "selected_count": float(len(selected_ids)),
        "generated_answer": answer,
        "gold_answer": sample.answer,
        "answer_mode": effective_answer_mode,
        "reference_date_time": sample.metadata.get("reference_date_time"),
    }
    if extra:
        row.update(dict(extra))
    results[method].append(row)


def _summarize(results: Mapping[str, Sequence[Mapping[str, Any]]], budget: int) -> List[Dict[str, Any]]:
    summary = []
    for method, rows in results.items():
        if not rows:
            continue
        summary.append(
            {
                "method": method,
                "budget": budget,
                "precision": average(rows, "precision"),
                "recall": average(rows, "recall"),
                "hit_rate": average(rows, "hit_rate"),
                "full_cover_rate": average(rows, "full_cover_rate"),
                "wasted_rate": average(rows, "wasted_rate"),
                "fallback_rate": average(rows, "fallback_rate"),
                "selected_count": average(rows, "selected_count"),
                "f1": average(rows, "f1"),
                "official_f1": average(rows, "official_f1"),
                "temporal_f1": average_present(
                    [row for row in rows if str(row.get("category")) == "2"],
                    "temporal_f1",
                ),
                "bleu1": average(rows, "bleu1"),
                "rouge_l": average(rows, "rouge_l"),
                "pseudo_judge": average(rows, "pseudo_judge"),
                "llm_judge": average_present(rows, "llm_judge"),
                "strict_judge": average_present(rows, "strict_judge"),
                "faithfulness": average(rows, "faithfulness"),
                "query_retrieval_latency_ms": average(rows, "query_retrieval_latency_ms"),
                "reader_e2e_latency_ms": average(rows, "reader_e2e_latency_ms"),
                "reader_prompt_tokens": average_present(rows, "reader_prompt_tokens"),
                "reader_total_tokens": average_present(rows, "reader_total_tokens"),
                "query_time_latency_ms": average(rows, "query_time_latency_ms"),
                "idle_time_cost": average(rows, "idle_time_cost"),
                "total_tokens": average(rows, "total_tokens"),
                "samples": list(rows),
                "by_category": {
                    str(category): {
                        "count": len(category_rows),
                        "official_f1": average(category_rows, "official_f1"),
                        "llm_judge": average_present(category_rows, "llm_judge"),
                        "strict_judge": average_present(category_rows, "strict_judge"),
                        "recall": average(category_rows, "recall"),
                        "full_cover_rate": average(category_rows, "full_cover_rate"),
                    }
                    for category in sorted(
                        {str(row.get("category")) for row in rows}
                    )
                    for category_rows in [[
                        row for row in rows if str(row.get("category")) == category
                    ]]
                },
            }
        )
    return summary
