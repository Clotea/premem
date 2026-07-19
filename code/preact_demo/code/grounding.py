from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Sequence

from preact_demo.code.embeddings import EmbeddingProvider, cosine_similarity
from preact_demo.code.graph_store import GraphStore
from preact_demo.code.utils import (
    FutureNeedHypothesis,
    GroundedMemory,
    MemoryNode,
    parse_llm_json,
    safe_float,
)
from preact_demo.code.vllm_client import VLLMClient, VLLMError


def ground_hypotheses(
    hypotheses: Sequence[FutureNeedHypothesis],
    memory_nodes: Sequence[MemoryNode],
    graph: GraphStore,
    budget: int,
    embedding_provider: EmbeddingProvider,
    config: Mapping[str, Any],
    llm_client: Optional[VLLMClient] = None,
) -> List[GroundedMemory]:
    if not hypotheses or not memory_nodes or budget <= 0:
        return []

    grounding_config = dict(config.get("grounding") or {})
    candidates_per_hypothesis = int(grounding_config.get("candidates_per_hypothesis") or 5)
    threshold = float(grounding_config.get("activation_threshold") or 0.08)
    hypothesis_vectors = embedding_provider.embed([hypothesis_text(item) for item in hypotheses])
    memory_vectors = embedding_provider.embed([memory.searchable_text() for memory in memory_nodes])

    candidates: List[GroundedMemory] = []
    for hypothesis, hypothesis_vector in zip(hypotheses, hypothesis_vectors):
        ranked = []
        for memory, memory_vector in zip(memory_nodes, memory_vectors):
            score = max(0.0, cosine_similarity(hypothesis_vector, memory_vector))
            score += graph_relevance_boost(memory.id, graph)
            ranked.append((min(1.0, score), memory))
        ranked.sort(key=lambda item: (item[0], item[1].importance, item[1].timestamp), reverse=True)
        for score, memory in ranked[:candidates_per_hypothesis]:
            if score < threshold:
                continue
            candidates.append(
                GroundedMemory(
                    hypothesis_id=hypothesis.id,
                    memory_id=memory.id,
                    score=round(score, 4),
                    reason=f"{embedding_provider.name} similarity to predicted need",
                    method="embedding",
                )
            )

    mode = str(grounding_config.get("mode") or "embedding").lower()
    if mode in {"llm", "hybrid"} and llm_client is not None and candidates:
        candidates = _llm_rerank(hypotheses, memory_nodes, candidates, llm_client, budget) or candidates

    best_by_pair: Dict[tuple[str, str], GroundedMemory] = {}
    for item in candidates:
        pair = (item.hypothesis_id, item.memory_id)
        current = best_by_pair.get(pair)
        if current is None or item.score > current.score:
            best_by_pair[pair] = item

    memory_scores: Dict[str, float] = {}
    for item in best_by_pair.values():
        memory_scores[item.memory_id] = max(memory_scores.get(item.memory_id, 0.0), item.score)
    selected_memory_ids = {
        memory_id
        for memory_id, _score in sorted(
            memory_scores.items(), key=lambda item: item[1], reverse=True
        )[:budget]
    }
    return sorted(
        [item for item in best_by_pair.values() if item.memory_id in selected_memory_ids],
        key=lambda item: item.score,
        reverse=True,
    )


def hypothesis_text(hypothesis: FutureNeedHypothesis) -> str:
    return " ".join(
        part
        for part in [
            hypothesis.intent,
            hypothesis.rationale,
            " ".join(hypothesis.search_queries),
            " ".join(hypothesis.evidence_requirements),
        ]
        if part
    )


def graph_relevance_boost(memory_id: str, graph: GraphStore) -> float:
    degree = len(graph.neighbors(memory_id))
    return min(0.05, degree * 0.005)


def _llm_rerank(
    hypotheses: Sequence[FutureNeedHypothesis],
    memory_nodes: Sequence[MemoryNode],
    candidates: Sequence[GroundedMemory],
    client: VLLMClient,
    budget: int,
) -> List[GroundedMemory]:
    by_id = {memory.id: memory for memory in memory_nodes}
    payload = {
        "hypotheses": [item.to_dict() for item in hypotheses],
        "candidates": [
            {
                "hypothesis_id": item.hypothesis_id,
                "memory_id": item.memory_id,
                "summary": by_id[item.memory_id].summary,
                "embedding_score": item.score,
            }
            for item in candidates
            if item.memory_id in by_id
        ],
        "budget": budget,
    }
    messages = [
        {
            "role": "system",
            "content": "Ground predicted future needs to existing memory. Return strict JSON only.",
        },
        {
            "role": "user",
            "content": (
                "Return grounded_memories as objects with hypothesis_id, memory_id, score, and reason. "
                f"Input:\n{json.dumps(payload, ensure_ascii=False)}"
            ),
        },
    ]
    try:
        content, _usage = client.chat(
            messages, temperature=0.0, max_tokens=600, response_format={"type": "json_object"}
        )
        parsed = parse_llm_json(content)
    except (VLLMError, ValueError, TypeError, json.JSONDecodeError):
        return []

    valid_pairs = {(item.hypothesis_id, item.memory_id) for item in candidates}
    result: List[GroundedMemory] = []
    for raw in parsed.get("grounded_memories") or []:
        if not isinstance(raw, dict):
            continue
        pair = (str(raw.get("hypothesis_id") or ""), str(raw.get("memory_id") or ""))
        if pair not in valid_pairs:
            continue
        result.append(
            GroundedMemory(
                hypothesis_id=pair[0],
                memory_id=pair[1],
                score=max(0.0, min(1.0, safe_float(raw.get("score"), 0.5))),
                reason=str(raw.get("reason") or "Selected by LLM grounder."),
                method="llm",
            )
        )
        if len(result) >= budget:
            break
    return result
