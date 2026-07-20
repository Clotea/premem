from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from .graph_store import (
    EDGE_EXPRESSES_FACT,
    EDGE_INTENT_ACTIVATES,
    EDGE_MENTIONS,
    EDGE_SIMILAR_TO,
    EDGE_TARGETS_FACT,
    EDGE_TEMPORAL_NEXT,
    NODE_FACT,
    NODE_INTENT,
    NODE_MEMORY,
    GraphStore,
)
from .utils import MemoryNode, Prediction, STOPWORDS, Turn, tokenize, truncate, unique


DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "max_heads": 3,
    "global_cache_budget": 0,
    "per_head_candidate_k": 8,
    "seed_k": 2,
    "embedding_provider": "hashing",
    "embedding_dimensions": 384,
    "shared_node_bonus": 0.12,
    "uncovered_head_bonus": 0.10,
    "diversity_penalty": 0.08,
    "confidence_weight": 0.08,
    "readiness_weight": 0.12,
    "generic_penalty_weight": 0.10,
    "route_threshold": 0.18,
    "native_rag_threshold": 0.08,
    "intent_similarity_threshold": 0.03,
    "multi_head_threshold": 0.14,
    "margin_threshold": 0.05,
    "readiness_threshold": 0.42,
    "semantic_support_threshold": 0.04,
    "merge_max_heads": 2,
    "targeted_repair_top_k": 3,
    "trace_include_embeddings": False,
}


_SEMANTIC_ALIASES = {
    "activities": "activity",
    "activity": "activity",
    "events": "activity",
    "event": "activity",
    "doing": "activity",
    "did": "activity",
    "recent": "recent",
    "recently": "recent",
    "advice": "support",
    "suggestion": "support",
    "suggestions": "support",
    "help": "support",
    "support": "support",
    "feeling": "emotion",
    "feelings": "emotion",
    "felt": "emotion",
    "relationship": "relationship",
    "relationships": "relationship",
    "friend": "relationship",
    "friends": "relationship",
    "job": "career",
    "work": "career",
    "career": "career",
    "location": "location",
    "place": "location",
    "where": "location",
    "time": "time",
    "when": "time",
    "reason": "reason",
    "why": "reason",
}

_RELATION_TERMS = {
    "activity": {"activity", "event", "doing", "visit", "trip", "attend", "weekend"},
    "support": {"advice", "support", "help", "suggestion", "recommend"},
    "emotion": {"feel", "feeling", "emotion", "happy", "sad", "excited", "worried"},
    "relationship": {"friend", "family", "partner", "relationship", "mother", "father"},
    "career": {"work", "job", "career", "interview", "office"},
    "location": {"where", "location", "place", "city", "country"},
    "time": {"when", "time", "date", "year", "month", "day"},
    "reason": {"why", "reason", "because", "motivation"},
}

_GENERIC_INTENT_TERMS = {
    "continue",
    "conversation",
    "follow",
    "followup",
    "dialogue",
    "talk",
    "respond",
    "response",
    "engage",
}

_GRAPH_EXPANSION_TYPES = {EDGE_SIMILAR_TO, EDGE_TEMPORAL_NEXT, EDGE_MENTIONS}


class HashingEmbeddingEncoder:
    """Small dependency-free encoder used for intent routing and cache planning.

    It is deliberately deterministic so a prepared cache built during idle time
    and a query routed later (or in another process) use the same vector space.
    """

    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = max(64, int(dimensions))

    def encode(self, text: str) -> List[float]:
        features = _semantic_terms(text)
        vector = [0.0] * self.dimensions
        if not features:
            return vector
        weighted: List[Tuple[str, float]] = [(term, 1.0) for term in features]
        weighted.extend(
            (f"{left}::{right}", 0.55)
            for left, right in zip(features, features[1:])
        )
        for feature, weight in weighted:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=16).digest()
            index = int.from_bytes(digest[:8], "big") % self.dimensions
            sign = 1.0 if digest[8] & 1 else -1.0
            vector[index] += sign * weight
        norm = math.sqrt(sum(value * value for value in vector))
        if norm <= 0.0:
            return vector
        return [value / norm for value in vector]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right:
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def build_multi_intent_bundle(
    context_key: str,
    history: Sequence[Turn],
    memory_nodes: Sequence[MemoryNode],
    graph: GraphStore,
    prediction: Prediction,
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    """Create query-free intent heads and a globally budgeted physical cache."""

    multi_config = _config(config)
    budget = int(
        multi_config.get("global_cache_budget")
        or config.get("cache_budget")
        or config.get("cacheBudget")
        or 5
    )
    max_heads = max(1, int(multi_config["max_heads"]))
    encoder = HashingEmbeddingEncoder(int(multi_config["embedding_dimensions"]))
    raw_intents = unique(
        str(intent).strip()
        for intent in prediction.predicted_future_intents
        if str(intent).strip()
    )[:max_heads]
    if not raw_intents:
        raw_intents = ["continue the active conversation"]

    priors = _rank_priors(len(raw_intents))
    prediction_scores = {
        item.id: max(0.0, min(1.0, float(item.confidence)))
        for item in prediction.activated_memory_ids
    }
    memory_embeddings = {
        memory.id: encoder.encode(memory.searchable_text()) for memory in memory_nodes
    }
    fact_records = _materialize_fact_nodes(memory_nodes, graph)
    heads: List[Dict[str, Any]] = []

    for index, (raw_intent, confidence) in enumerate(zip(raw_intents, priors), start=1):
        head_id = f"intent_{_stable_suffix(context_key)}_{index:02d}"
        structured = _structure_intent(raw_intent, history)
        intent_text = _intent_embedding_text(raw_intent, structured)
        embedding = encoder.encode(intent_text)
        graph.add_node(
            head_id,
            NODE_INTENT,
            is_intent=True,
            is_transient=True,
            raw_intent=raw_intent,
            description=intent_text,
            structured=structured,
            confidence=round(confidence, 6),
            embedding_provider=multi_config["embedding_provider"],
            embedding_dimensions=len(embedding),
            embedding=embedding,
        )
        branch, traversal = _plan_branch(
            head_id=head_id,
            structured=structured,
            embedding=embedding,
            confidence=confidence,
            memory_nodes=memory_nodes,
            memory_embeddings=memory_embeddings,
            prediction_scores=prediction_scores,
            graph=graph,
            fact_records=fact_records,
            config=multi_config,
        )
        heads.append(
            {
                "id": head_id,
                "node_type": NODE_INTENT,
                "is_intent": True,
                "is_transient": True,
                "raw_intent": raw_intent,
                "description": intent_text,
                "structured": structured,
                "confidence": round(confidence, 6),
                "genericness": structured["genericness"],
                "embedding_provider": multi_config["embedding_provider"],
                "embedding_dimensions": len(embedding),
                "embedding": embedding,
                "candidates": branch,
                "traversal": traversal,
            }
        )

    selected_ids, scheduler_trace = _schedule_joint_cache(
        heads=heads,
        memory_embeddings=memory_embeddings,
        budget=max(0, budget),
        config=multi_config,
    )
    selected_set = set(selected_ids)
    for head in heads:
        resident_ids = [
            item["memory_id"]
            for item in head["candidates"]
            if item["memory_id"] in selected_set
        ]
        candidate_utility = sum(float(item["score"]) for item in head["candidates"])
        resident_utility = sum(
            float(item["score"])
            for item in head["candidates"]
            if item["memory_id"] in selected_set
        )
        utility_coverage = resident_utility / candidate_utility if candidate_utility else 0.0
        count_coverage = len(resident_ids) / max(1, len(head["candidates"]))
        head["resident_memory_ids"] = resident_ids
        head["readiness"] = round(0.75 * utility_coverage + 0.25 * count_coverage, 6)

    membership: Dict[str, List[str]] = defaultdict(list)
    for head in heads:
        for item in head["candidates"]:
            membership[item["memory_id"]].append(head["id"])
    shared_memory_ids = sorted(
        memory_id for memory_id, head_ids in membership.items() if len(set(head_ids)) > 1
    )
    used_fact_ids = {
        item["fact_id"] for head in heads for item in head["candidates"] if item.get("fact_id")
    }
    shared_fact_ids = sorted(
        fact_id
        for fact_id in used_fact_ids
        if len(
            {
                head["id"]
                for head in heads
                if any(item.get("fact_id") == fact_id for item in head["candidates"])
            }
        )
        > 1
    )
    overlay_node_ids = {
        *(head["id"] for head in heads),
        *(
            item["memory_id"]
            for head in heads
            for item in head["candidates"]
        ),
        *used_fact_ids,
    }
    graph_overlay = {
        "nodes": [
            {
                **{
                    key: value
                    for key, value in graph.nodes[node_id].items()
                    if key != "embedding"
                },
                "is_resident": node_id in selected_set,
            }
            for node_id in sorted(overlay_node_ids)
            if node_id in graph.nodes
        ],
        "edges": [
            edge.to_dict()
            for edge in graph.edges
            if edge.source in overlay_node_ids
            and edge.target in overlay_node_ids
            and edge.edge_type
            in {EDGE_INTENT_ACTIVATES, EDGE_TARGETS_FACT, EDGE_EXPRESSES_FACT}
        ],
    }

    return {
        "bundle_id": f"mic_{_stable_suffix(context_key)}",
        "method": "multi_intent_prepared_adaptive_router",
        "uses_actual_query": False,
        "embedding_provider": multi_config["embedding_provider"],
        "embedding_dimensions": int(multi_config["embedding_dimensions"]),
        "global_cache_budget": budget,
        "intent_heads": heads,
        "physical_memory_ids": selected_ids,
        "physical_cache_size": len(selected_ids),
        "logical_branch_memory_count": sum(len(head["candidates"]) for head in heads),
        "shared_memory_ids": shared_memory_ids,
        "shared_fact_ids": shared_fact_ids,
        "fact_nodes": [
            fact_records[fact_id]
            for fact_id in sorted(used_fact_ids)
            if fact_id in fact_records
        ],
        "graph_overlay": graph_overlay,
        "prefetch_plan": scheduler_trace,
        "config": {
            key: multi_config[key]
            for key in (
                "max_heads",
                "per_head_candidate_k",
                "route_threshold",
                "native_rag_threshold",
                "intent_similarity_threshold",
                "multi_head_threshold",
                "margin_threshold",
                "readiness_threshold",
                "semantic_support_threshold",
            )
        },
    }


def route_multi_intent_query(
    query: str,
    bundle: Mapping[str, Any],
    memory_nodes: Sequence[MemoryNode],
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    """Route an arriving query to one/multiple prepared branches or to RAG."""

    multi_config = _config(config)
    encoder = HashingEmbeddingEncoder(int(bundle.get("embedding_dimensions") or 384))
    query_embedding = encoder.encode(query)
    memory_by_id = {memory.id: memory for memory in memory_nodes}
    scores: List[Dict[str, Any]] = []

    for raw_head in bundle.get("intent_heads") or []:
        head = dict(raw_head)
        head_embedding = head.get("embedding") or encoder.encode(str(head.get("description") or ""))
        intent_similarity = cosine_similarity(query_embedding, head_embedding)
        resident_ids = [
            memory_id
            for memory_id in head.get("resident_memory_ids") or []
            if memory_id in memory_by_id
        ]
        resident_scores = [
            (
                memory_id,
                cosine_similarity(
                    query_embedding,
                    encoder.encode(memory_by_id[memory_id].searchable_text()),
                ),
            )
            for memory_id in resident_ids
        ]
        resident_scores.sort(key=lambda item: item[1], reverse=True)
        semantic_support = max((score for _memory_id, score in resident_scores), default=0.0)
        readiness = float(head.get("readiness") or 0.0)
        effective_readiness = 0.65 * readiness + 0.35 * max(0.0, semantic_support)
        route_score = (
            intent_similarity
            + float(multi_config["confidence_weight"]) * float(head.get("confidence") or 0.0)
            + float(multi_config["readiness_weight"]) * readiness
            - float(multi_config["generic_penalty_weight"]) * float(head.get("genericness") or 0.0)
        )
        scores.append(
            {
                "head_id": head["id"],
                "raw_intent": head.get("raw_intent"),
                "structured": head.get("structured"),
                "intent_similarity": round(intent_similarity, 6),
                "prior_confidence": round(float(head.get("confidence") or 0.0), 6),
                "prepared_readiness": round(readiness, 6),
                "semantic_support": round(semantic_support, 6),
                "effective_readiness": round(effective_readiness, 6),
                "route_score": round(route_score, 6),
                "resident_memory_ids": resident_ids,
                "resident_query_ranking": [
                    {"memory_id": memory_id, "cosine": round(score, 6)}
                    for memory_id, score in resident_scores
                ],
            }
        )

    scores.sort(key=lambda item: item["route_score"], reverse=True)
    top = scores[0] if scores else None
    second = scores[1] if len(scores) > 1 else None
    selected_heads: List[Dict[str, Any]] = []
    decision = "native_rag"
    reason = "No usable intent head was prepared."

    has_semantic_evidence = bool(
        top
        and (
            top["intent_similarity"] >= float(multi_config["intent_similarity_threshold"])
            or top["semantic_support"] >= float(multi_config["semantic_support_threshold"])
        )
    )
    if (
        top
        and top["route_score"] >= float(multi_config["native_rag_threshold"])
        and has_semantic_evidence
    ):
        ambiguous = bool(
            second
            and second["route_score"] >= float(multi_config["multi_head_threshold"])
            and (
                second["intent_similarity"] >= float(multi_config["intent_similarity_threshold"])
                or second["semantic_support"] >= float(multi_config["semantic_support_threshold"])
            )
            and top["route_score"] - second["route_score"]
            <= float(multi_config["margin_threshold"])
        )
        top_ready = (
            top["effective_readiness"] >= float(multi_config["readiness_threshold"])
            and top["semantic_support"] >= float(multi_config["semantic_support_threshold"])
        )
        if ambiguous:
            selected_heads = [
                item
                for item in scores[: int(multi_config["merge_max_heads"])]
                if item["route_score"] >= float(multi_config["multi_head_threshold"])
            ]
            merged_ready = any(
                item["effective_readiness"] >= float(multi_config["readiness_threshold"])
                and item["semantic_support"] >= float(multi_config["semantic_support_threshold"])
                for item in selected_heads
            )
            if merged_ready:
                decision = "merge_heads"
                reason = "Multiple intent heads are similarly plausible and prepared; merge their resident cache branches."
            else:
                decision = "partial_repair"
                reason = "Multiple intent heads are plausible but their prepared coverage is weak; merge available memories and repair online."
        elif top["route_score"] >= float(multi_config["route_threshold"]) and top_ready:
            selected_heads = [top]
            decision = "single_head"
            reason = "One prepared intent head passes both semantic and readiness gates."
        else:
            selected_heads = [top]
            decision = "partial_repair"
            reason = "The best intent head is plausible but not fully ready; use it and reactively repair missing support."
    elif top:
        reason = "All intent heads are below the calibrated native-RAG threshold."

    prepared_ids: List[str] = []
    prepared_query_scores: Dict[str, float] = {}
    for head in selected_heads:
        for item in head["resident_query_ranking"]:
            memory_id = item["memory_id"]
            prepared_query_scores[memory_id] = max(
                prepared_query_scores.get(memory_id, -1.0),
                float(item["cosine"]),
            )
    prepared_ids = sorted(
        prepared_query_scores,
        key=lambda memory_id: prepared_query_scores[memory_id],
        reverse=True,
    )
    if decision == "native_rag":
        prepared_ids = []

    return {
        "decision": decision,
        "reason": reason,
        "uses_actual_query": True,
        "query": query,
        "selected_head_ids": [item["head_id"] for item in selected_heads],
        "prepared_memory_ids": prepared_ids,
        "requires_reactive_retrieval": decision in {"partial_repair", "native_rag"},
        "repair_top_k": int(multi_config["targeted_repair_top_k"]),
        "head_scores": scores,
        "thresholds": {
            "route_threshold": float(multi_config["route_threshold"]),
            "native_rag_threshold": float(multi_config["native_rag_threshold"]),
            "intent_similarity_threshold": float(multi_config["intent_similarity_threshold"]),
            "multi_head_threshold": float(multi_config["multi_head_threshold"]),
            "margin_threshold": float(multi_config["margin_threshold"]),
            "readiness_threshold": float(multi_config["readiness_threshold"]),
            "semantic_support_threshold": float(multi_config["semantic_support_threshold"]),
        },
    }


def public_bundle_trace(bundle: Mapping[str, Any]) -> Dict[str, Any]:
    """Remove dense vectors while retaining all human-readable planning data."""

    include_embeddings = bool((bundle.get("config") or {}).get("trace_include_embeddings"))
    result = {key: value for key, value in bundle.items() if key != "intent_heads"}
    result["intent_heads"] = []
    for raw_head in bundle.get("intent_heads") or []:
        head = dict(raw_head)
        if not include_embeddings:
            head.pop("embedding", None)
        result["intent_heads"].append(head)
    return result


def merge_with_reactive_results(
    prepared_ids: Sequence[str],
    reactive_memories: Sequence[MemoryNode],
    memory_nodes: Sequence[MemoryNode],
    limit: int,
) -> Tuple[List[MemoryNode], List[str]]:
    """Merge prepared and online repair results without duplicate memory loads."""

    memory_by_id = {memory.id: memory for memory in memory_nodes}
    final_ids = [
        memory_id for memory_id in unique(prepared_ids) if memory_id in memory_by_id
    ]
    repair_ids: List[str] = []
    for memory in reactive_memories:
        if memory.id in final_ids:
            continue
        final_ids.append(memory.id)
        repair_ids.append(memory.id)
        if len(final_ids) >= max(1, limit):
            break
    return [memory_by_id[memory_id] for memory_id in final_ids[:limit]], repair_ids


def _plan_branch(
    head_id: str,
    structured: Mapping[str, Any],
    embedding: Sequence[float],
    confidence: float,
    memory_nodes: Sequence[MemoryNode],
    memory_embeddings: Mapping[str, Sequence[float]],
    prediction_scores: Mapping[str, float],
    graph: GraphStore,
    fact_records: Mapping[str, Mapping[str, Any]],
    config: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    max_timestamp = max((memory.timestamp for memory in memory_nodes), default=1) or 1
    entity_terms = set(
        _semantic_terms(
            " ".join(
                list(structured.get("entities") or [])
                + list(structured.get("context_entities") or [])
            )
        )
    )
    relation = str(structured.get("relation") or "generic")
    ranked: List[Tuple[float, MemoryNode, Dict[str, float]]] = []

    for memory in memory_nodes:
        semantic = max(0.0, cosine_similarity(embedding, memory_embeddings[memory.id]))
        predicted = float(prediction_scores.get(memory.id, 0.0))
        recency = max(0.0, min(1.0, memory.timestamp / max_timestamp))
        importance = max(0.0, min(1.0, float(memory.importance)))
        memory_entity_terms = set(_semantic_terms(" ".join(memory.entities)))
        entity_overlap = (
            len(entity_terms & memory_entity_terms) / len(entity_terms)
            if entity_terms
            else 0.0
        )
        relation_match = 1.0 if _infer_relation(memory.searchable_text()) == relation else 0.0
        score = (
            0.48 * semantic
            + 0.20 * predicted
            + 0.09 * recency
            + 0.08 * importance
            + 0.08 * entity_overlap
            + 0.07 * relation_match
        )
        ranked.append(
            (
                score,
                memory,
                {
                    "semantic": semantic,
                    "predictor": predicted,
                    "recency": recency,
                    "importance": importance,
                    "entity_overlap": entity_overlap,
                    "relation_match": relation_match,
                    "graph_boost": 0.0,
                },
            )
        )

    ranked.sort(key=lambda item: item[0], reverse=True)
    seed_ids = [memory.id for _score, memory, _parts in ranked[: int(config["seed_k"])]]
    traversal = _expand_seed_memories(seed_ids, graph)
    graph_boosts: Dict[str, float] = defaultdict(float)
    for step in traversal:
        target_id = str(step.get("target_memory_id") or "")
        if target_id:
            graph_boosts[target_id] = max(
                graph_boosts[target_id],
                float(step.get("boost") or 0.0),
            )

    rescored: List[Tuple[float, MemoryNode, Dict[str, float]]] = []
    for base_score, memory, parts in ranked:
        parts = dict(parts)
        parts["graph_boost"] = graph_boosts.get(memory.id, 0.0)
        rescored.append((base_score + parts["graph_boost"], memory, parts))
    rescored.sort(key=lambda item: item[0], reverse=True)

    fact_id_by_memory = {
        memory_id: fact_id
        for fact_id, record in fact_records.items()
        for memory_id in record.get("evidence_memory_ids") or []
    }
    branch: List[Dict[str, Any]] = []
    for score, memory, parts in rescored[: int(config["per_head_candidate_k"])]:
        fact_id = fact_id_by_memory.get(memory.id)
        graph.add_edge(head_id, memory.id, EDGE_INTENT_ACTIVATES)
        if fact_id:
            graph.add_edge(head_id, fact_id, EDGE_TARGETS_FACT)
        branch.append(
            {
                "memory_id": memory.id,
                "fact_id": fact_id,
                "score": round(max(0.0, score), 6),
                "head_confidence": round(confidence, 6),
                "score_components": {
                    key: round(float(value), 6) for key, value in parts.items()
                },
                "summary": truncate(memory.summary, 220),
                "source_turn_id": memory.source_turn_id,
            }
        )
    return branch, traversal


def _expand_seed_memories(seed_ids: Sequence[str], graph: GraphStore) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []
    seen_targets = set(seed_ids)
    memory_ids = {
        node_id
        for node_id, node in graph.nodes.items()
        if node.get("node_type") == NODE_MEMORY
    }
    for seed_id in seed_ids:
        for edge in graph.edges:
            if edge.edge_type not in _GRAPH_EXPANSION_TYPES:
                continue
            if edge.source == seed_id:
                neighbor = edge.target
            elif edge.target == seed_id:
                neighbor = edge.source
            else:
                continue
            if neighbor in memory_ids and neighbor not in seen_targets:
                seen_targets.add(neighbor)
                steps.append(
                    {
                        "path": f"{NODE_INTENT} -> {NODE_MEMORY} -> {edge.edge_type} -> {NODE_MEMORY}",
                        "seed_memory_id": seed_id,
                        "via_node_id": None,
                        "target_memory_id": neighbor,
                        "edge_types": [edge.edge_type],
                        "boost": 0.08 if edge.edge_type == EDGE_SIMILAR_TO else 0.05,
                    }
                )
            elif edge.edge_type == EDGE_MENTIONS:
                for second in graph.edges:
                    if second.edge_type != EDGE_MENTIONS:
                        continue
                    if second.source == neighbor:
                        target = second.target
                    elif second.target == neighbor:
                        target = second.source
                    else:
                        continue
                    if target in memory_ids and target not in seen_targets:
                        seen_targets.add(target)
                        steps.append(
                            {
                                "path": (
                                    f"{NODE_INTENT} -> {NODE_MEMORY} -> "
                                    "mentions -> EntityNode <- mentions <- MemoryNode"
                                ),
                                "seed_memory_id": seed_id,
                                "via_node_id": neighbor,
                                "target_memory_id": target,
                                "edge_types": [EDGE_MENTIONS, EDGE_MENTIONS],
                                "boost": 0.07,
                            }
                        )
    return steps


def _schedule_joint_cache(
    heads: Sequence[Mapping[str, Any]],
    memory_embeddings: Mapping[str, Sequence[float]],
    budget: int,
    config: Mapping[str, Any],
) -> Tuple[List[str], List[Dict[str, Any]]]:
    contributions: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for head in heads:
        for candidate in head.get("candidates") or []:
            contributions[candidate["memory_id"]].append(
                {
                    "head_id": head["id"],
                    "head_confidence": float(head.get("confidence") or 0.0),
                    "candidate_score": float(candidate.get("score") or 0.0),
                    "fact_id": candidate.get("fact_id"),
                }
            )

    selected: List[str] = []
    covered_heads = set()
    trace: List[Dict[str, Any]] = []
    while len(selected) < budget:
        best_id = None
        best_score = -float("inf")
        best_parts: Dict[str, Any] = {}
        for memory_id, items in contributions.items():
            if memory_id in selected:
                continue
            weighted_utility = sum(
                item["head_confidence"] * item["candidate_score"] for item in items
            )
            unique_heads = {item["head_id"] for item in items}
            shared_bonus = float(config["shared_node_bonus"]) * max(0, len(unique_heads) - 1)
            uncovered_bonus = float(config["uncovered_head_bonus"]) * sum(
                item["head_confidence"]
                for item in items
                if item["head_id"] not in covered_heads
            )
            redundancy = max(
                (
                    max(0.0, cosine_similarity(memory_embeddings[memory_id], memory_embeddings[chosen]))
                    for chosen in selected
                ),
                default=0.0,
            )
            redundancy_penalty = float(config["diversity_penalty"]) * redundancy
            marginal = weighted_utility + shared_bonus + uncovered_bonus - redundancy_penalty
            if marginal > best_score:
                best_id = memory_id
                best_score = marginal
                best_parts = {
                    "weighted_utility": weighted_utility,
                    "shared_bonus": shared_bonus,
                    "uncovered_head_bonus": uncovered_bonus,
                    "redundancy_penalty": redundancy_penalty,
                    "branch_ids": sorted(unique_heads),
                    "fact_ids": sorted(
                        {
                            str(item["fact_id"])
                            for item in items
                            if item.get("fact_id")
                        }
                    ),
                }
        if best_id is None:
            break
        selected.append(best_id)
        covered_heads.update(best_parts["branch_ids"])
        trace.append(
            {
                "prefetch_order": len(selected),
                "memory_id": best_id,
                "priority": round(best_score, 6),
                **{
                    key: round(value, 6) if isinstance(value, float) else value
                    for key, value in best_parts.items()
                },
                "physical_cache_occupancy": len(selected),
                "dedupe_policy": "same memory id is loaded once and shared by all matching heads",
            }
        )
    return selected, trace


def _materialize_fact_nodes(
    memory_nodes: Sequence[MemoryNode],
    graph: GraphStore,
) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, List[MemoryNode]] = defaultdict(list)
    for memory in memory_nodes:
        grouped[_canonical_fact_signature(memory)].append(memory)
    records: Dict[str, Dict[str, Any]] = {}
    for signature, memories in grouped.items():
        fact_id = f"fact_{_stable_suffix(signature)}"
        record = {
            "id": fact_id,
            "node_type": NODE_FACT,
            "is_fact": True,
            "canonical_signature": signature,
            "summary": memories[0].summary,
            "evidence_memory_ids": [memory.id for memory in memories],
            "is_shared_fact": len(memories) > 1,
            "merge_policy": (
                "Only exact canonical facts share this node; semantically similar facts remain separate."
            ),
        }
        records[fact_id] = record
        graph.add_node(fact_id, NODE_FACT, **{key: value for key, value in record.items() if key not in {"id", "node_type"}})
        for memory in memories:
            graph.add_edge(memory.id, fact_id, EDGE_EXPRESSES_FACT)
    return records


def _canonical_fact_signature(memory: MemoryNode) -> str:
    content = re.sub(r"^\s*[^:]{1,40}:\s*", "", memory.content.lower()).strip()
    content = re.sub(r"[^a-z0-9]+", " ", content)
    content = " ".join(content.split())
    entities = ",".join(sorted(term for term in _semantic_terms(" ".join(memory.entities)) if term))
    return f"{memory.memory_type.lower()}|{entities}|{content}"


def _structure_intent(raw_intent: str, history: Sequence[Turn]) -> Dict[str, Any]:
    recent_turns = list(history[-4:])
    recent_entities = unique(
        entity
        for turn in recent_turns
        for entity in _named_terms(turn.text)
    )[:8]
    focus_entities = [
        entity for entity in recent_entities if entity.lower() in raw_intent.lower()
    ]
    relation = _infer_relation(raw_intent)
    terms = set(_semantic_terms(raw_intent))
    generic_hits = len(terms & _GENERIC_INTENT_TERMS)
    specificity = min(
        1.0,
        0.20 * len(terms - _GENERIC_INTENT_TERMS) + 0.10 * len(focus_entities),
    )
    genericness = min(1.0, max(0.0, 0.65 * (generic_hits > 0) + 0.35 * (1.0 - specificity)))
    answer_type = {
        "location": "location",
        "time": "time",
        "reason": "explanation",
        "activity": "event_or_list",
        "support": "recommendation",
        "emotion": "state_or_explanation",
        "relationship": "person_or_relation",
        "career": "event_or_state",
    }.get(relation, "fact")
    return {
        "subject": recent_turns[-1].speaker if recent_turns else "user",
        "relation": relation,
        "answer_type": answer_type,
        "entities": focus_entities,
        "context_entities": recent_entities,
        "constraints": [],
        "genericness": round(genericness, 6),
    }


def _intent_embedding_text(raw_intent: str, structured: Mapping[str, Any]) -> str:
    return " ".join(
        [
            raw_intent.replace("_", " "),
            str(structured.get("relation") or ""),
            str(structured.get("answer_type") or ""),
            " ".join(structured.get("entities") or []),
        ]
    ).strip()


def _infer_relation(text: str) -> str:
    terms = set(_semantic_terms(text))
    ranked = [
        (len(terms & {_SEMANTIC_ALIASES.get(term, term) for term in relation_terms}), relation)
        for relation, relation_terms in _RELATION_TERMS.items()
    ]
    ranked.sort(reverse=True)
    return ranked[0][1] if ranked and ranked[0][0] > 0 else "generic"


def _semantic_terms(text: str) -> List[str]:
    normalized = str(text or "").replace("_", " ").replace("-", " ").lower()
    terms: List[str] = []
    for token in tokenize(normalized):
        if token in STOPWORDS:
            continue
        stem = token
        if len(stem) > 5 and stem.endswith("ing"):
            stem = stem[:-3]
        elif len(stem) > 4 and stem.endswith("ed"):
            stem = stem[:-2]
        elif len(stem) > 4 and stem.endswith("s"):
            stem = stem[:-1]
        terms.append(_SEMANTIC_ALIASES.get(token, _SEMANTIC_ALIASES.get(stem, stem)))
    cjk = "".join(re.findall(r"[\u3400-\u9fff]", normalized))
    terms.extend(cjk[index : index + 2] for index in range(max(0, len(cjk) - 1)))
    return terms


def _named_terms(text: str) -> List[str]:
    return [
        token
        for token in re.findall(r"\b[A-Z][A-Za-z0-9'-]{2,}\b", str(text or ""))
        if token.lower() not in {"the", "this", "that", "what", "when", "where", "how"}
    ]


def _rank_priors(count: int) -> List[float]:
    raw = [math.exp(-0.55 * index) for index in range(count)]
    total = sum(raw) or 1.0
    return [value / total for value in raw]


def _config(config: Mapping[str, Any]) -> Dict[str, Any]:
    result = dict(DEFAULT_CONFIG)
    result.update(dict(config.get("multi_intent_cache") or {}))
    provider = str(result.get("embedding_provider") or "hashing").lower()
    if provider != "hashing":
        raise ValueError(
            f"Unsupported multi_intent_cache.embedding_provider={provider!r}; "
            "this implementation currently provides deterministic hashing embeddings."
        )
    result["embedding_provider"] = provider
    return result


def _stable_suffix(value: str) -> str:
    return hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:12]
