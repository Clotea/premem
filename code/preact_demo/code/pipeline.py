from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from preact_demo.code.embeddings import EmbeddingProvider, create_embedding_provider
from preact_demo.code.external_search import (
    SearchProvider,
    create_search_provider,
    search_memory_gaps,
)
from preact_demo.code.graph_store import (
    EDGE_SIMILAR_TO,
    EDGE_TEMPORAL_NEXT,
    GraphStore,
    build_memory_graph,
)
from preact_demo.code.grounding import ground_hypotheses
from preact_demo.code.memory_gap import detect_memory_gaps
from preact_demo.code.predictors import MemoryNeedPredictor
from preact_demo.code.utils import (
    ActivatedMemory,
    ExternalEvidence,
    FutureNeedHypothesis,
    MemoryNode,
    Prediction,
    Sample,
    Turn,
    WorkingCache,
    WorkingContextPackage,
    average,
    compute_activation_metrics,
    estimate_cost,
    estimate_importance,
    extract_entities,
    extract_keywords,
    f1_score,
    faithfulness,
    infer_memory_type,
    overlap_score,
    pseudo_judge,
    rouge_l,
    seeded_pick,
    truncate,
)
from preact_demo.code.vllm_client import VLLMClient, VLLMError
from preact_demo.code.working_context import (
    build_working_context_package,
    verify_working_context,
)


METHODS = [
    "Random Cache",
    "Recency Cache",
    "Reactive Vector Retrieval",
    "Reactive Graph Retrieval",
    "LLM-Predict Cache Only",
    "LLM-Predict + Fallback",
    "Oracle Cache",
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
) -> Dict[str, Any]:
    memory_by_id = {memory.id: memory for memory in memory_nodes}
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
        "memories": memories,
        "scores": [
            {"id": item["memory"].id, "score": round(float(item["score"]), 3)}
            for item in scored
        ],
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
    external_evidence: Sequence[ExternalEvidence] = (),
    llm_client: Optional[VLLMClient] = None,
    config: Optional[Mapping[str, Any]] = None,
) -> str:
    config = config or {}
    llm_config = dict(config.get("llm") or {})
    use_vllm = bool(config.get("answer_with_vllm") or llm_config.get("use_for_answer", False))
    if use_vllm and llm_client is not None and (memories or external_evidence):
        memory_context = "\n".join(f"- [memory] {memory.summary}" for memory in memories)
        external_context = "\n".join(
            f"- [{item.source_type}] {item.title}: {item.snippet} ({item.url})"
            for item in external_evidence
        )
        context = "\n".join(part for part in [memory_context, external_context] if part)
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

    if not memories and not external_evidence:
        return f"No verified memory was found for: {query}"
    parts = [memory.summary for memory in memories]
    parts.extend(f"{item.title}: {item.snippet}" for item in external_evidence)
    return "Based on verified context: " + " ".join(parts)


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


def prepare_working_context(
    context_key: str,
    history: Sequence[Turn],
    config: Mapping[str, Any],
    predictor: MemoryNeedPredictor,
    llm_client: Optional[VLLMClient] = None,
    embedding_provider: Optional[EmbeddingProvider] = None,
    search_provider: Optional[SearchProvider] = None,
) -> Dict[str, Any]:
    """Run the query-independent stages and return a reusable context package."""
    memory_nodes = memory_writer(history)
    graph = build_memory_graph(
        memory_nodes,
        history,
        similarity_threshold=float(
            config.get("similarity_threshold") or config.get("similarityThreshold") or 0.28
        ),
    )
    budget = int(config.get("cache_budget") or config.get("cacheBudget") or 3)
    prediction = predictor.predict(history, memory_nodes, graph, budget)
    hypotheses = list(prediction.hypotheses) or [
        FutureNeedHypothesis(
            id=f"h_{index + 1:03d}",
            intent=intent,
            rationale="Converted from a legacy future-intent prediction.",
            confidence=0.5,
            search_queries=[intent],
        )
        for index, intent in enumerate(prediction.predicted_future_intents)
        if intent
    ]
    if not hypotheses and history:
        hypotheses = [
            FutureNeedHypothesis(
                id="h_001",
                intent=f"Continue the recent topic: {truncate(history[-1].text, 120)}",
                rationale="Fallback hypothesis derived from the most recent turn.",
                confidence=0.4,
                search_queries=[history[-1].text],
            )
        ]
    prediction.hypotheses = hypotheses
    if not prediction.predicted_future_intents:
        prediction.predicted_future_intents = [item.intent for item in hypotheses]

    embedding_provider = embedding_provider or create_embedding_provider(config, llm_client)
    grounded = ground_hypotheses(
        hypotheses,
        memory_nodes,
        graph,
        budget,
        embedding_provider,
        config,
        llm_client=llm_client,
    )
    activated_by_memory: Dict[str, ActivatedMemory] = {}
    for item in grounded:
        current = activated_by_memory.get(item.memory_id)
        if current is None or item.score > current.confidence:
            activated_by_memory[item.memory_id] = ActivatedMemory(
                id=item.memory_id,
                reason=item.reason,
                confidence=item.score,
            )
    prediction.activated_memory_ids = sorted(
        activated_by_memory.values(), key=lambda item: item.confidence, reverse=True
    )[:budget]
    gaps = detect_memory_gaps(
        hypotheses,
        grounded,
        memory_nodes,
        config,
        llm_client=llm_client,
    )
    search_provider = search_provider or create_search_provider(config)
    external_evidence = search_memory_gaps(gaps, config, search_provider)
    package = build_working_context_package(
        package_id=f"wcp_{context_key}",
        hypotheses=hypotheses,
        grounded=grounded,
        gaps=gaps,
        external_evidence=external_evidence,
        memory_nodes=memory_nodes,
        graph=graph,
        metadata={
            "embedding_provider": getattr(embedding_provider, "name", "unknown"),
            "search_provider": getattr(search_provider, "name", "unknown"),
        },
    )
    cache = insert_cache(
        cache_id=f"cache_{context_key}",
        budget=budget,
        prediction=prediction,
        memory_nodes=memory_nodes,
        graph=graph,
    )
    return {
        "memory_nodes": memory_nodes,
        "graph": graph,
        "prediction": prediction,
        "cache": cache,
        "working_context": package,
    }


def run_evaluation(
    samples: Sequence[Sample],
    config: Mapping[str, Any],
    predictor: MemoryNeedPredictor,
    llm_client: Optional[VLLMClient] = None,
) -> List[Dict[str, Any]]:
    results: Dict[str, List[Dict[str, Any]]] = {method: [] for method in METHODS}
    context_cache: Dict[str, Dict[str, Any]] = {}
    embedding_provider = create_embedding_provider(config, llm_client)
    search_provider = create_search_provider(config)

    for sample in samples:
        context_key = sample.history_cache_key or sample.id
        context = context_cache.get(context_key)
        if context is None:
            context = prepare_working_context(
                context_key=context_key,
                history=sample.history,
                config=config,
                predictor=predictor,
                llm_client=llm_client,
                embedding_provider=embedding_provider,
                search_provider=search_provider,
            )
            context_cache[context_key] = context

        memory_nodes = context["memory_nodes"]
        graph = context["graph"]
        cache = context["cache"]
        working_context: WorkingContextPackage = context["working_context"]
        budget = int(config.get("cache_budget") or config.get("cacheBudget") or 3)
        evidence = label_gold_evidence(
            sample.evidence_terms,
            sample.gold_evidence_turn_ids,
            memory_nodes,
        )

        random_ids = seeded_pick([memory.id for memory in memory_nodes], budget, int(config.get("random_seed") or config.get("randomSeed") or 7))
        _evaluate_cache_method("Random Cache", sample, memory_nodes, random_ids, evidence, config, results, llm_client)

        recency_ids = [
            memory.id
            for memory in sorted(memory_nodes, key=lambda item: item.timestamp, reverse=True)[:budget]
        ]
        _evaluate_cache_method("Recency Cache", sample, memory_nodes, recency_ids, evidence, config, results, llm_client)

        _evaluate_retrieval_method(
            method="Reactive Vector Retrieval",
            sample=sample,
            selected=vector_retrieve(sample.question, memory_nodes, int(config.get("retrieval_top_k") or config.get("retrievalTopK") or 3)),
            activated=None,
            evidence=evidence,
            fallback_used=True,
            idle_prediction_used=False,
            results=results,
            config=config,
            llm_client=llm_client,
        )

        _evaluate_retrieval_method(
            method="Reactive Graph Retrieval",
            sample=sample,
            selected=graph_retrieve(sample.question, graph, memory_nodes, int(config.get("retrieval_top_k") or config.get("retrievalTopK") or 3)),
            activated=None,
            evidence=evidence,
            fallback_used=True,
            idle_prediction_used=False,
            results=results,
            config=config,
            llm_client=llm_client,
        )

        verified = verify_working_context(
            sample.question,
            working_context,
            memory_nodes,
            embedding_provider,
            config,
            llm_client=llm_client,
        )
        diagnostics = {
            "working_context_package_id": working_context.package_id,
            "predicted_future_hypotheses": [item.intent for item in working_context.hypotheses],
            "grounded_memory_ids": working_context.memory_ids,
            "memory_gap_count": sum(1 for item in working_context.gaps if item.exists),
            "external_evidence_count": len(working_context.external_evidence),
            "working_context_coverage": working_context.coverage_score,
            "verifier_score": verified["score"],
            "verifier_hypothesis_score": verified["hypothesis_score"],
            "verifier_reason": verified["reason"],
            "verifier_method": verified["method"],
        }
        _evaluate_retrieval_method(
            method="LLM-Predict Cache Only",
            sample=sample,
            selected=verified["memories"],
            activated=cache.memory_ids,
            evidence=evidence,
            fallback_used=False,
            idle_prediction_used=True,
            results=results,
            config=config,
            llm_client=llm_client,
            external_evidence=verified["external_evidence"],
            diagnostics=diagnostics,
        )

        selected = verified["memories"] if verified["sufficient"] else fallback_retrieve(
            sample.question,
            graph,
            memory_nodes,
            config,
        )
        _evaluate_retrieval_method(
            method="LLM-Predict + Fallback",
            sample=sample,
            selected=selected,
            activated=cache.memory_ids,
            evidence=evidence,
            fallback_used=not verified["sufficient"],
            idle_prediction_used=True,
            results=results,
            config=config,
            llm_client=llm_client,
            external_evidence=(verified["external_evidence"] if verified["sufficient"] else []),
            diagnostics=diagnostics,
        )

        oracle_ids = evidence[:budget]
        oracle_selected = [memory for memory in memory_nodes if memory.id in set(oracle_ids)]
        _evaluate_retrieval_method(
            method="Oracle Cache",
            sample=sample,
            selected=oracle_selected,
            activated=oracle_ids,
            evidence=evidence,
            fallback_used=False,
            idle_prediction_used=False,
            results=results,
            config=config,
            llm_client=llm_client,
        )

    return _summarize(results, int(config.get("cache_budget") or config.get("cacheBudget") or 3))


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
    verified = verify_cache(
        sample.question,
        cache,
        memory_nodes,
        threshold=float(config.get("verifier_threshold") or config.get("verifierThreshold") or 0.12),
    )
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
    external_evidence: Sequence[ExternalEvidence] = (),
    diagnostics: Optional[Mapping[str, Any]] = None,
) -> None:
    answer = generate_answer(
        sample.question,
        selected,
        external_evidence=external_evidence,
        llm_client=llm_client,
        config=config,
    )
    activation = compute_activation_metrics(
        activated if activated is not None else [memory.id for memory in selected],
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
    row = {
            "sample_id": sample.id,
            "precision": activation["precision"],
            "recall": activation["recall"],
            "hit_rate": activation["hit_rate"],
            "wasted_rate": activation["wasted_rate"],
            "fallback_rate": 1.0 if fallback_used else 0.0,
            "f1": f1_score(answer, sample.answer),
            "rouge_l": rouge_l(answer, sample.answer),
            "llm_judge": pseudo_judge(answer, sample.answer),
            "faithfulness": faithfulness(answer, selected),
            "query_time_latency_ms": costs["query_time_latency_ms"],
            "idle_time_cost": costs["idle_time_cost"],
            "total_tokens": costs["total_tokens"],
            "selected_memory_ids": [memory.id for memory in selected],
            "selected_external_evidence_ids": [item.id for item in external_evidence],
            "memory_gap_count": 0.0,
            "external_evidence_count": float(len(external_evidence)),
            "working_context_coverage": 0.0,
        }
    if diagnostics:
        row.update(dict(diagnostics))
    results[method].append(row)


def _summarize(results: Mapping[str, Sequence[Mapping[str, Any]]], budget: int) -> List[Dict[str, Any]]:
    summary = []
    for method, rows in results.items():
        summary.append(
            {
                "method": method,
                "budget": budget,
                "precision": average(rows, "precision"),
                "recall": average(rows, "recall"),
                "hit_rate": average(rows, "hit_rate"),
                "wasted_rate": average(rows, "wasted_rate"),
                "fallback_rate": average(rows, "fallback_rate"),
                "memory_gap_count": average(rows, "memory_gap_count"),
                "external_evidence_count": average(rows, "external_evidence_count"),
                "working_context_coverage": average(rows, "working_context_coverage"),
                "f1": average(rows, "f1"),
                "rouge_l": average(rows, "rouge_l"),
                "llm_judge": average(rows, "llm_judge"),
                "faithfulness": average(rows, "faithfulness"),
                "query_time_latency_ms": average(rows, "query_time_latency_ms"),
                "idle_time_cost": average(rows, "idle_time_cost"),
                "total_tokens": average(rows, "total_tokens"),
                "samples": list(rows),
            }
        )
    return summary
