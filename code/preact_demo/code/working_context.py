from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Sequence

from preact_demo.code.embeddings import EmbeddingProvider, cosine_similarity
from preact_demo.code.graph_store import GraphStore
from preact_demo.code.grounding import hypothesis_text
from preact_demo.code.utils import (
    ExternalEvidence,
    FutureNeedHypothesis,
    GroundedMemory,
    MemoryGap,
    MemoryNode,
    WorkingContextPackage,
    parse_llm_json,
)
from preact_demo.code.vllm_client import VLLMClient, VLLMError


def build_working_context_package(
    package_id: str,
    hypotheses: Sequence[FutureNeedHypothesis],
    grounded: Sequence[GroundedMemory],
    gaps: Sequence[MemoryGap],
    external_evidence: Sequence[ExternalEvidence],
    memory_nodes: Sequence[MemoryNode],
    graph: GraphStore,
    metadata: Optional[Mapping[str, Any]] = None,
) -> WorkingContextPackage:
    memory_by_id = {memory.id: memory for memory in memory_nodes}
    memory_ids: List[str] = []
    for item in grounded:
        if item.memory_id in memory_by_id and item.memory_id not in memory_ids:
            memory_ids.append(item.memory_id)
    coverage_by_hypothesis: Dict[str, float] = {item.id: 0.0 for item in hypotheses}
    for item in grounded:
        coverage_by_hypothesis[item.hypothesis_id] = max(
            coverage_by_hypothesis.get(item.hypothesis_id, 0.0), item.score
        )
    coverage_score = (
        sum(coverage_by_hypothesis.values()) / len(coverage_by_hypothesis)
        if coverage_by_hypothesis
        else 0.0
    )
    return WorkingContextPackage(
        package_id=package_id,
        hypotheses=list(hypotheses),
        grounded_memories=list(grounded),
        memory_ids=memory_ids,
        summaries=[memory_by_id[item].summary for item in memory_ids],
        local_subgraph=graph.local_subgraph(memory_ids),
        gaps=list(gaps),
        external_evidence=list(external_evidence),
        coverage_score=round(coverage_score, 4),
        metadata=dict(metadata or {}),
    )


def verify_working_context(
    query: str,
    package: WorkingContextPackage,
    memory_nodes: Sequence[MemoryNode],
    embedding_provider: EmbeddingProvider,
    config: Mapping[str, Any],
    llm_client: Optional[VLLMClient] = None,
) -> Dict[str, Any]:
    verifier_config = dict(config.get("verifier") or {})
    threshold = float(
        verifier_config.get("threshold")
        or config.get("verifier_threshold")
        or config.get("verifierThreshold")
        or 0.12
    )
    hypothesis_threshold = float(verifier_config.get("hypothesis_threshold") or threshold)
    memory_by_id = {memory.id: memory for memory in memory_nodes}
    candidate_memories = [memory_by_id[item] for item in package.memory_ids if item in memory_by_id]
    candidate_evidence = list(package.external_evidence)
    hypothesis_texts = [hypothesis_text(item) for item in package.hypotheses]
    texts = hypothesis_texts + [memory.searchable_text() for memory in candidate_memories] + [
        item.searchable_text() for item in candidate_evidence
    ]
    if not texts:
        return _empty_verification("Working context package has no usable evidence.")

    vectors = embedding_provider.embed([query, *texts])
    query_vector = vectors[0]
    memory_scores = []
    evidence_scores = []
    offset = 1
    hypothesis_scores = [
        max(0.0, cosine_similarity(query_vector, vector))
        for vector in vectors[offset : offset + len(hypothesis_texts)]
    ]
    offset += len(hypothesis_texts)
    for memory, vector in zip(candidate_memories, vectors[offset : offset + len(candidate_memories)]):
        memory_scores.append((max(0.0, cosine_similarity(query_vector, vector)), memory))
    offset += len(candidate_memories)
    for evidence, vector in zip(candidate_evidence, vectors[offset:]):
        evidence_scores.append((max(0.0, cosine_similarity(query_vector, vector)), evidence))

    selected_memories = [memory for score, memory in memory_scores if score >= threshold]
    selected_evidence = [item for score, item in evidence_scores if score >= threshold]
    max_score = max([score for score, _item in memory_scores + evidence_scores], default=0.0)
    max_hypothesis_score = max(hypothesis_scores, default=0.0)
    evidence_supported = bool(selected_memories or selected_evidence)
    hypothesis_supported = max_hypothesis_score >= hypothesis_threshold
    sufficient = evidence_supported and hypothesis_supported
    result = {
        "use_package": sufficient,
        "sufficient": sufficient,
        "selected_memory_ids": [memory.id for memory in selected_memories],
        "selected_external_evidence_ids": [item.id for item in selected_evidence],
        "memories": selected_memories,
        "external_evidence": selected_evidence,
        "score": round(max_score, 4),
        "hypothesis_score": round(max_hypothesis_score, 4),
        "reason": (
            f"Evidence score {max_score:.3f}/{threshold:.3f}; "
            f"hypothesis score {max_hypothesis_score:.3f}/{hypothesis_threshold:.3f}."
        ),
        "method": "embedding",
    }

    mode = str(verifier_config.get("mode") or "embedding").lower()
    if mode in {"llm", "hybrid"} and llm_client is not None:
        return _llm_verify(query, package, candidate_memories, candidate_evidence, result, llm_client) or result
    return result


def _llm_verify(
    query: str,
    package: WorkingContextPackage,
    memories: Sequence[MemoryNode],
    evidence: Sequence[ExternalEvidence],
    embedding_result: Mapping[str, Any],
    client: VLLMClient,
) -> Dict[str, Any]:
    payload = {
        "query": query,
        "hypotheses": [item.to_dict() for item in package.hypotheses],
        "memories": [{"id": item.id, "summary": item.summary} for item in memories],
        "external_evidence": [item.to_dict() for item in evidence],
        "embedding_result": dict(embedding_result),
    }
    messages = [
        {
            "role": "system",
            "content": "Verify whether a proactive working context can answer the current query. Return JSON only.",
        },
        {
            "role": "user",
            "content": (
                "Return sufficient, selected_memory_ids, selected_external_evidence_ids, and reason. "
                f"Input:\n{json.dumps(payload, ensure_ascii=False)}"
            ),
        },
    ]
    try:
        content, _usage = client.chat(
            messages, temperature=0.0, max_tokens=400, response_format={"type": "json_object"}
        )
        parsed = parse_llm_json(content)
    except (VLLMError, ValueError, TypeError, json.JSONDecodeError):
        return {}

    memory_by_id = {item.id: item for item in memories}
    evidence_by_id = {item.id: item for item in evidence}
    memory_ids = [str(item) for item in parsed.get("selected_memory_ids") or []]
    evidence_ids = [str(item) for item in parsed.get("selected_external_evidence_ids") or []]
    selected_memories = [memory_by_id[item] for item in memory_ids if item in memory_by_id]
    selected_evidence = [evidence_by_id[item] for item in evidence_ids if item in evidence_by_id]
    sufficient = bool(parsed.get("sufficient")) and bool(selected_memories or selected_evidence)
    return {
        "use_package": sufficient,
        "sufficient": sufficient,
        "selected_memory_ids": [item.id for item in selected_memories],
        "selected_external_evidence_ids": [item.id for item in selected_evidence],
        "memories": selected_memories,
        "external_evidence": selected_evidence,
        "score": embedding_result.get("score", 0.0),
        "hypothesis_score": embedding_result.get("hypothesis_score", 0.0),
        "reason": str(parsed.get("reason") or "LLM verifier decision."),
        "method": "llm",
    }


def _empty_verification(reason: str) -> Dict[str, Any]:
    return {
        "use_package": False,
        "sufficient": False,
        "selected_memory_ids": [],
        "selected_external_evidence_ids": [],
        "memories": [],
        "external_evidence": [],
        "score": 0.0,
        "hypothesis_score": 0.0,
        "reason": reason,
        "method": "embedding",
    }
