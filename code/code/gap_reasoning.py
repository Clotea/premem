from __future__ import annotations

import json
import re
from time import perf_counter
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .graph_store import GraphStore, memory_catalog
from .reranker import rerank_candidates
from .utils import (
    MemoryNode,
    Prediction,
    Turn,
    extract_keywords,
    jaccard,
    parse_llm_json,
    safe_float,
    tokenize,
    truncate,
    unique,
)
from .vllm_client import VLLMClient, VLLMError


META_PATH_LIBRARY: List[Dict[str, str]] = [
    {
        "path_id": "P1",
        "path": "Intent -> UserGoal -> Idea",
        "purpose": "Recover the user's goal, method definition, and active idea.",
    },
    {
        "path_id": "P2",
        "path": "Intent -> Paper -> Claim -> Evidence",
        "purpose": "Check whether related-work or paper claims have evidence.",
    },
    {
        "path_id": "P3",
        "path": "Intent -> Topic -> Paper -> Claim",
        "purpose": "Locate representative methods or claims for the topic.",
    },
    {
        "path_id": "P4",
        "path": "Claim -> Evidence",
        "purpose": "Find direct evidence for claims needed by the future answer.",
    },
    {
        "path_id": "P5",
        "path": "Claim -> contradicts -> Claim",
        "purpose": "Look for conflicts, boundary claims, and distinctions.",
    },
    {
        "path_id": "P6",
        "path": "Claim -> Gap",
        "purpose": "Locate missing support, outdated facts, or coverage gaps.",
    },
]

ALLOWED_PATH_IDS = {item["path_id"] for item in META_PATH_LIBRARY}
ALLOWED_GAP_TYPES = {
    "evidence_gap",
    "coverage_gap",
    "conflict_gap",
    "freshness_gap",
    "personalization_gap",
    "definition_gap",
}
ALLOWED_BINDING_TYPES = {"supports", "contradicts", "updates", "clarifies"}
ALLOWED_VERIFIER_DECISIONS = {"use", "partial_use", "reject", "fallback"}


def build_prepared_context(
    context_key: str,
    history: Sequence[Turn],
    memory_nodes: Sequence[MemoryNode],
    graph: GraphStore,
    prediction: Prediction,
    llm_client: Optional[VLLMClient],
    config: Mapping[str, Any],
    planning_trace: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    reasoning_config = _reasoning_config(config)
    trace = planning_trace

    stage_start = perf_counter()
    intent = _intent_from_prediction(context_key, history, prediction)
    _record_planning_stage(
        trace,
        "intent_materialization",
        "Materialize predicted intent",
        stage_start,
        {
            "input_count": len(prediction.predicted_future_intents),
            "intent": intent,
        },
    )

    _set_trace_stage(llm_client, "meta_path_selection")
    stage_start = perf_counter()
    selected_paths = select_meta_paths(intent, history, llm_client, config)
    _record_planning_stage(
        trace,
        "meta_path_selection",
        "Select conceptual meta-paths",
        stage_start,
        {
            "candidate_path_count": len(META_PATH_LIBRARY),
            "selected_path_count": len(selected_paths),
            "selected_paths": selected_paths,
        },
    )

    stage_start = perf_counter()
    activated_nodes, executed_paths, path_memory_ids = execute_meta_paths(
        intent=intent,
        selected_paths=selected_paths,
        memory_nodes=memory_nodes,
        graph=graph,
        config=config,
        include_trace=planning_trace is not None,
    )
    _record_planning_stage(
        trace,
        "meta_path_execution",
        "Execute path-conditioned memory activation",
        stage_start,
        {
            "execution_mode": "path_conditioned_global_ranking_then_local_subgraph",
            "selected_path_count": len(executed_paths),
            "activated_node_count": len(activated_nodes),
            "unique_path_memory_count": len(unique(path_memory_ids)),
            "executed_paths": executed_paths,
        },
    )

    _set_trace_stage(llm_client, "support_check")
    stage_start = perf_counter()
    support_check = check_support(intent, activated_nodes, llm_client, config)
    _record_planning_stage(
        trace,
        "support_check",
        "Check whether activated graph supports the intent",
        stage_start,
        {
            "activated_node_count": len(activated_nodes),
            "support_check": support_check,
        },
    )

    _set_trace_stage(llm_client, "gap_generation")
    stage_start = perf_counter()
    gaps = generate_gaps(intent, support_check, llm_client, config)
    _record_planning_stage(
        trace,
        "gap_generation",
        "Generate missing-support GapNodes",
        stage_start,
        {
            "support_status": support_check.get("support_status"),
            "gap_count": len(gaps),
            "gaps": gaps,
        },
    )

    stage_start = perf_counter()
    evidence = repair_gaps(gaps, memory_nodes, config)
    _record_planning_stage(
        trace,
        "gap_repair",
        "Retrieve evidence for each gap",
        stage_start,
        {
            "gap_count": len(gaps),
            "evidence_count": len(evidence),
            "evidence": evidence,
        },
    )

    _set_trace_stage(llm_client, "evidence_binding")
    stage_start = perf_counter()
    bindings = bind_evidence(intent, gaps, evidence, llm_client, config)
    _record_planning_stage(
        trace,
        "evidence_binding",
        "Bind evidence to GapNodes",
        stage_start,
        {
            "evidence_count": len(evidence),
            "binding_count": len(bindings),
            "bindings": bindings,
        },
    )

    stage_start = perf_counter()
    candidate_memory_ids = unique(
        [
            item.id
            for item in prediction.activated_memory_ids
            if item.id
        ]
        + path_memory_ids
        + [
            str(item.get("source_id"))
            for item in evidence
            if item.get("source_type") == "memory" and item.get("source_id")
        ]
    )

    usable_claims = _build_usable_claims(intent, gaps, evidence, bindings)
    candidate_memory_ids = candidate_memory_ids[: int(reasoning_config.get("working_context_budget") or 12)]
    _record_planning_stage(
        trace,
        "candidate_merge",
        "Merge predictor, path, and repair candidates",
        stage_start,
        {
            "predictor_memory_ids": [
                item.id for item in prediction.activated_memory_ids if item.id
            ],
            "path_memory_ids": unique(path_memory_ids),
            "repair_memory_ids": unique(
                [
                    str(item.get("source_id"))
                    for item in evidence
                    if item.get("source_type") == "memory" and item.get("source_id")
                ]
            ),
            "working_context_budget": int(reasoning_config.get("working_context_budget") or 12),
            "candidate_memory_ids": candidate_memory_ids,
        },
    )

    stage_start = perf_counter()
    final_memory_ids, compression = compress_prepared_memories(
        candidate_memory_ids=candidate_memory_ids,
        intent=intent,
        prediction=prediction,
        executed_paths=executed_paths,
        evidence=evidence,
        bindings=bindings,
        memory_nodes=memory_nodes,
        graph=graph,
        config=config,
    )
    _record_planning_stage(
        trace,
        "cache_compression",
        "Compress candidates into the prepared cache",
        stage_start,
        {
            "candidate_count": len(candidate_memory_ids),
            "final_count": len(final_memory_ids),
            "final_memory_ids": final_memory_ids,
            "compression": compression,
        },
    )

    result = {
        "context_package_id": f"ctx_{_stable_suffix(context_key)}",
        "intent_node": intent,
        "target_intent": intent["content"],
        "possible_user_query": intent.get("possible_user_query", ""),
        "confidence": intent.get("confidence", 0.5),
        "meta_path_library": META_PATH_LIBRARY,
        "selected_paths": selected_paths,
        "executed_paths": executed_paths,
        "activated_nodes": activated_nodes,
        "support_check": support_check,
        "gaps": gaps,
        "evidence": evidence,
        "bindings": bindings,
        "usable_claims": usable_claims,
        "summary": _context_summary(intent, gaps, bindings),
        "risk": (
            "Use only when the next query matches the predicted intent or asks about "
            "the claims, gaps, evidence, or distinctions prepared in this package."
        ),
        "memory_ids": final_memory_ids,
        "memory_candidates": _memory_candidate_records(final_memory_ids, memory_nodes),
        "candidate_memory_ids": candidate_memory_ids,
        "candidate_memory_candidates": _memory_candidate_records(candidate_memory_ids, memory_nodes),
        "compression": compression,
        "metadata": {
            "module": "future_intent_conditioned_gap_reasoning",
            "path_count": len(selected_paths),
            "gap_count": len(gaps),
            "evidence_count": len(evidence),
            "binding_count": len(bindings),
            "candidate_memory_count": len(candidate_memory_ids),
            "final_memory_count": len(final_memory_ids),
            "llm_enabled": bool(reasoning_config.get("use_llm", True)),
        },
    }
    if planning_trace is not None:
        result["planning_trace"] = planning_trace
    return result


def compress_prepared_memories(
    candidate_memory_ids: Sequence[str],
    intent: Mapping[str, Any],
    prediction: Prediction,
    executed_paths: Sequence[Mapping[str, Any]],
    evidence: Sequence[Mapping[str, Any]],
    bindings: Sequence[Mapping[str, Any]],
    memory_nodes: Sequence[MemoryNode],
    graph: GraphStore,
    config: Mapping[str, Any],
) -> Tuple[List[str], Dict[str, Any]]:
    reasoning_config = _reasoning_config(config)
    if not bool(reasoning_config.get("compression_enabled", True)):
        return list(candidate_memory_ids), {
            "enabled": False,
            "reason": "compression_disabled",
            "candidate_count": len(candidate_memory_ids),
            "final_count": len(candidate_memory_ids),
            "scores": [],
        }

    budget = int(reasoning_config.get("prepared_cache_budget") or config.get("cache_budget") or config.get("cacheBudget") or 5)
    if budget <= 0:
        budget = len(candidate_memory_ids)
    if len(candidate_memory_ids) <= budget:
        return list(candidate_memory_ids), {
            "enabled": True,
            "reason": "candidate_count_within_budget",
            "budget": budget,
            "candidate_count": len(candidate_memory_ids),
            "final_count": len(candidate_memory_ids),
            "scores": [],
        }

    by_id = {memory.id: memory for memory in memory_nodes}
    candidate_ids = [memory_id for memory_id in unique(candidate_memory_ids) if memory_id in by_id]
    score_records = _score_prequery_candidates(
        candidate_ids=candidate_ids,
        intent=intent,
        prediction=prediction,
        executed_paths=executed_paths,
        evidence=evidence,
        bindings=bindings,
        memory_nodes=memory_nodes,
        graph=graph,
        config=config,
    )
    score_by_id = {str(item["id"]): item for item in score_records}
    selected = _select_diverse_prequery_cache(
        candidate_ids=candidate_ids,
        score_by_id=score_by_id,
        memory_by_id=by_id,
        budget=budget,
        diversity_weight=float(reasoning_config.get("compression_diversity_weight") or 0.12),
    )
    ranked_scores = []
    selected_set = set(selected)
    for rank, record in enumerate(score_records, start=1):
        item = dict(record)
        item["rank"] = rank
        item["selected"] = item["id"] in selected_set
        ranked_scores.append(item)

    return selected, {
        "enabled": True,
        "method": "query_agnostic_intent_gap_cache_selection",
        "budget": budget,
        "candidate_count": len(candidate_ids),
        "final_count": len(selected),
        "uses_actual_query": False,
        "features": [
            "predicted_intent_overlap",
            "predictor_confidence",
            "gap_repair_support",
            "path_coverage",
            "graph_centrality",
            "memory_importance",
            "recency",
            "diversity",
        ],
        "scores": ranked_scores,
    }


def _score_prequery_candidates(
    candidate_ids: Sequence[str],
    intent: Mapping[str, Any],
    prediction: Prediction,
    executed_paths: Sequence[Mapping[str, Any]],
    evidence: Sequence[Mapping[str, Any]],
    bindings: Sequence[Mapping[str, Any]],
    memory_nodes: Sequence[MemoryNode],
    graph: GraphStore,
    config: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    reasoning_config = _reasoning_config(config)
    by_id = {memory.id: memory for memory in memory_nodes}
    candidate_set = set(candidate_ids)
    intent_tokens = tokenize(_intent_text(intent))
    max_timestamp = max((memory.timestamp for memory in memory_nodes), default=1)

    prediction_scores = {
        item.id: _clamp(safe_float(item.confidence, 0.5))
        for item in prediction.activated_memory_ids
        if item.id
    }
    path_counts: Dict[str, int] = {}
    path_rank_scores: Dict[str, float] = {}
    for path in executed_paths:
        selected = [str(item) for item in path.get("selected_memory_ids") or []]
        count = max(1, len(selected))
        for index, memory_id in enumerate(selected):
            if memory_id not in candidate_set:
                continue
            path_counts[memory_id] = path_counts.get(memory_id, 0) + 1
            path_rank_scores[memory_id] = max(
                path_rank_scores.get(memory_id, 0.0),
                1.0 - (index / count),
            )
    max_path_count = max(path_counts.values(), default=1)

    bound_evidence_ids = {str(binding.get("evidence_id")) for binding in bindings}
    repair_scores: Dict[str, float] = {}
    for item in evidence:
        if str(item.get("source_type") or "") != "memory":
            continue
        memory_id = str(item.get("source_id") or "")
        if memory_id not in candidate_set:
            continue
        score = _clamp(safe_float(item.get("score"), 0.0))
        if str(item.get("evidence_id")) in bound_evidence_ids:
            score = min(1.0, score + 0.15)
        repair_scores[memory_id] = max(repair_scores.get(memory_id, 0.0), score)

    centrality_scores = _graph_centrality_scores(graph, candidate_set)
    weights = {
        "intent": float(reasoning_config.get("compression_intent_weight") or 0.30),
        "prediction": float(reasoning_config.get("compression_prediction_weight") or 0.22),
        "repair": float(reasoning_config.get("compression_repair_weight") or 0.18),
        "path": float(reasoning_config.get("compression_path_weight") or 0.12),
        "importance": float(reasoning_config.get("compression_importance_weight") or 0.08),
        "recency": float(reasoning_config.get("compression_recency_weight") or 0.05),
        "centrality": float(reasoning_config.get("compression_centrality_weight") or 0.05),
    }

    records: List[Dict[str, Any]] = []
    for index, memory_id in enumerate(candidate_ids):
        memory = by_id.get(memory_id)
        if memory is None:
            continue
        intent_score = jaccard(intent_tokens, tokenize(memory.searchable_text()))
        prediction_score = prediction_scores.get(memory_id, 0.0)
        repair_score = repair_scores.get(memory_id, 0.0)
        path_score = 0.5 * (path_counts.get(memory_id, 0) / max_path_count) + 0.5 * path_rank_scores.get(memory_id, 0.0)
        importance_score = _clamp(memory.importance)
        recency_score = memory.timestamp / max_timestamp if max_timestamp else 0.0
        centrality_score = centrality_scores.get(memory_id, 0.0)
        score = (
            weights["intent"] * intent_score
            + weights["prediction"] * prediction_score
            + weights["repair"] * repair_score
            + weights["path"] * path_score
            + weights["importance"] * importance_score
            + weights["recency"] * recency_score
            + weights["centrality"] * centrality_score
        )
        records.append(
            {
                "id": memory_id,
                "score": round(score, 6),
                "intent_score": round(intent_score, 6),
                "prediction_score": round(prediction_score, 6),
                "repair_score": round(repair_score, 6),
                "path_score": round(path_score, 6),
                "importance_score": round(importance_score, 6),
                "recency_score": round(recency_score, 6),
                "centrality_score": round(centrality_score, 6),
                "candidate_order_score": round(1.0 - (index / max(1, len(candidate_ids))), 6),
                "summary": truncate(memory.summary, 180),
            }
        )

    records.sort(
        key=lambda item: (
            float(item["score"]),
            float(item["prediction_score"]),
            float(item["repair_score"]),
            float(item["recency_score"]),
        ),
        reverse=True,
    )
    return records


def _select_diverse_prequery_cache(
    candidate_ids: Sequence[str],
    score_by_id: Mapping[str, Mapping[str, Any]],
    memory_by_id: Mapping[str, MemoryNode],
    budget: int,
    diversity_weight: float,
) -> List[str]:
    remaining = [memory_id for memory_id in candidate_ids if memory_id in score_by_id and memory_id in memory_by_id]
    selected: List[str] = []
    selected_tokens: List[set[str]] = []
    while remaining and len(selected) < budget:
        best_id = ""
        best_adjusted = -1e9
        for memory_id in remaining:
            base_score = safe_float(score_by_id.get(memory_id, {}).get("score"), 0.0)
            tokens = set(tokenize(memory_by_id[memory_id].searchable_text()))
            redundancy = max((jaccard(list(tokens), list(existing)) for existing in selected_tokens), default=0.0)
            adjusted = base_score - diversity_weight * redundancy
            if adjusted > best_adjusted:
                best_adjusted = adjusted
                best_id = memory_id
        if not best_id:
            break
        selected.append(best_id)
        selected_tokens.append(set(tokenize(memory_by_id[best_id].searchable_text())))
        remaining = [memory_id for memory_id in remaining if memory_id != best_id]
    return selected


def _graph_centrality_scores(graph: GraphStore, candidate_ids: set[str]) -> Dict[str, float]:
    degrees: Dict[str, int] = {}
    for edge in graph.edges:
        if edge.source in candidate_ids:
            degrees[edge.source] = degrees.get(edge.source, 0) + 1
        if edge.target in candidate_ids:
            degrees[edge.target] = degrees.get(edge.target, 0) + 1
    max_degree = max(degrees.values(), default=1)
    return {memory_id: degree / max_degree for memory_id, degree in degrees.items()}


def select_meta_paths(
    intent: Mapping[str, Any],
    history: Sequence[Turn],
    llm_client: Optional[VLLMClient],
    config: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    reasoning_config = _reasoning_config(config)
    max_paths = int(reasoning_config.get("max_paths") or 4)
    fallback = _fallback_meta_paths(intent, max_paths)
    if not _can_use_llm(llm_client, config):
        return fallback

    payload = {
        "intent": intent,
        "recent_dialogue": _recent_dialogue(history, 4),
        "candidate_paths": META_PATH_LIBRARY,
        "max_paths": max_paths,
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You select meta-paths for pre-query memory graph diagnosis. "
                "Choose only from the provided candidate_paths. Return strict JSON."
            ),
        },
        {
            "role": "user",
            "content": (
                "Return JSON: {\"selected_paths\":[{\"path_id\":\"P1\","
                "\"reason\":\"...\"}]}\nInput:\n"
                f"{json.dumps(payload, ensure_ascii=False)}"
            ),
        },
    ]
    parsed = _chat_json(llm_client, messages, config, max_tokens=500)
    if parsed is None:
        return fallback

    selected: List[Dict[str, Any]] = []
    seen = set()
    for item in parsed.get("selected_paths") or []:
        if not isinstance(item, Mapping):
            continue
        path_id = str(item.get("path_id") or "")
        if path_id not in ALLOWED_PATH_IDS or path_id in seen:
            continue
        seen.add(path_id)
        selected.append(
            {
                "path_id": path_id,
                "reason": str(item.get("reason") or _path_by_id(path_id).get("purpose", "")),
            }
        )
        if len(selected) >= max_paths:
            break
    return selected or fallback


def execute_meta_paths(
    intent: Mapping[str, Any],
    selected_paths: Sequence[Mapping[str, Any]],
    memory_nodes: Sequence[MemoryNode],
    graph: GraphStore,
    config: Mapping[str, Any],
    include_trace: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    reasoning_config = _reasoning_config(config)
    per_path_k = int(reasoning_config.get("per_path_top_k") or 4)
    intent_text = _intent_text(intent)
    activated_by_id: Dict[str, Dict[str, Any]] = {}
    executed_paths: List[Dict[str, Any]] = []
    memory_ids: List[str] = []

    for selection in selected_paths:
        path_id = str(selection.get("path_id") or "")
        if path_id not in ALLOWED_PATH_IDS:
            continue
        scored = _rank_memories_for_path(intent_text, path_id, memory_nodes)
        selected_memory_ids = [memory.id for _score, memory in scored[:per_path_k]]
        memory_ids.extend(selected_memory_ids)
        subgraph = graph.local_subgraph(selected_memory_ids)
        for node in subgraph.get("nodes") or []:
            node_id = str(node.get("id") or "")
            if not node_id:
                continue
            compact = {
                "id": node_id,
                "node_type": node.get("node_type"),
                "path_id": path_id,
                "summary": truncate(
                    node.get("summary")
                    or node.get("content")
                    or node.get("text")
                    or node.get("name")
                    or "",
                    220,
                ),
                "source_turn_id": node.get("source_turn_id"),
                "segment_id": node.get("segment_id"),
            }
            if node_id not in activated_by_id:
                activated_by_id[node_id] = compact
            else:
                existing_paths = set(str(activated_by_id[node_id].get("path_id", "")).split(","))
                existing_paths.add(path_id)
                activated_by_id[node_id]["path_id"] = ",".join(sorted(item for item in existing_paths if item))

        execution_record: Dict[str, Any] = {
            "path_id": path_id,
            "path": _path_by_id(path_id).get("path"),
            "reason": str(selection.get("reason") or ""),
            "execution_mode": "path_conditioned_global_ranking_then_local_subgraph",
            "selected_memory_ids": selected_memory_ids,
            "node_count": len(subgraph.get("nodes") or []),
            "edge_count": len(subgraph.get("edges") or []),
        }
        if include_trace:
            ranking_limit = int(reasoning_config.get("trace_ranking_limit") or 20)
            execution_record.update(
                {
                    "ranking": [
                        {
                            "rank": rank,
                            "memory_id": memory.id,
                            "score": round(float(score), 6),
                            "selected": rank <= per_path_k,
                            "summary": truncate(memory.summary, 180),
                        }
                        for rank, (score, memory) in enumerate(
                            scored[: max(per_path_k, ranking_limit)],
                            start=1,
                        )
                    ],
                    "route_steps": [
                        {
                            "step": 1,
                            "operation": "intent_seed",
                            "input_ids": [str(intent.get("node_id") or "predicted_intent")],
                            "output_ids": [],
                            "uses_graph_edges": False,
                        },
                        {
                            "step": 2,
                            "operation": "path_conditioned_global_memory_ranking",
                            "input_ids": [],
                            "output_ids": selected_memory_ids,
                            "uses_graph_edges": False,
                        },
                        {
                            "step": 3,
                            "operation": "incident_edge_local_subgraph_expansion",
                            "input_ids": selected_memory_ids,
                            "output_ids": [
                                str(node.get("id") or "")
                                for node in subgraph.get("nodes") or []
                                if node.get("id")
                            ],
                            "uses_graph_edges": True,
                        },
                    ],
                    "local_subgraph": subgraph,
                }
            )
        executed_paths.append(execution_record)

    return list(activated_by_id.values()), executed_paths, unique(memory_ids)


def _record_planning_stage(
    trace: Optional[List[Dict[str, Any]]],
    stage_id: str,
    label: str,
    stage_start: float,
    data: Mapping[str, Any],
) -> None:
    if trace is None:
        return
    trace.append(
        {
            "index": len(trace) + 1,
            "stage_id": stage_id,
            "label": label,
            "elapsed_ms": round((perf_counter() - stage_start) * 1000.0, 3),
            "data": dict(data),
        }
    )


def _set_trace_stage(llm_client: Optional[VLLMClient], stage_id: str) -> None:
    setter = getattr(llm_client, "set_trace_stage", None)
    if callable(setter):
        setter(stage_id)


def check_support(
    intent: Mapping[str, Any],
    activated_nodes: Sequence[Mapping[str, Any]],
    llm_client: Optional[VLLMClient],
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    fallback = _fallback_support_check(intent, activated_nodes)
    if not _can_use_llm(llm_client, config):
        return fallback

    payload = {
        "intent": intent,
        "activated_nodes": list(activated_nodes)[:30],
        "required_schema": {
            "support_status": "sufficient | partial | insufficient",
            "supported_claims": ["claim text"],
            "missing_support": ["missing support item"],
            "confidence": 0.0,
        },
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You check whether an activated memory subgraph can support a future "
                "answer. Be conservative and return strict JSON only."
            ),
        },
        {
            "role": "user",
            "content": f"Input:\n{json.dumps(payload, ensure_ascii=False)}",
        },
    ]
    parsed = _chat_json(llm_client, messages, config, max_tokens=650)
    if parsed is None:
        return fallback

    status = str(parsed.get("support_status") or fallback["support_status"]).lower()
    if status not in {"sufficient", "partial", "insufficient"}:
        status = fallback["support_status"]
    supported_claims = parsed.get("supported_claims") or []
    missing_support = parsed.get("missing_support") or []
    return {
        "support_status": status,
        "supported_claims": [str(item) for item in supported_claims if str(item).strip()][:6],
        "missing_support": [str(item) for item in missing_support if str(item).strip()][:8],
        "confidence": _clamp(safe_float(parsed.get("confidence"), fallback["confidence"])),
        "provider": "vllm",
    }


def generate_gaps(
    intent: Mapping[str, Any],
    support_check: Mapping[str, Any],
    llm_client: Optional[VLLMClient],
    config: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    reasoning_config = _reasoning_config(config)
    max_gaps = int(reasoning_config.get("max_gaps") or 4)
    fallback = _fallback_gaps(intent, support_check, max_gaps)
    if support_check.get("support_status") == "sufficient":
        return []
    if not _can_use_llm(llm_client, config):
        return fallback

    payload = {
        "intent": intent,
        "support_check": support_check,
        "allowed_gap_types": sorted(ALLOWED_GAP_TYPES),
        "required_schema": {
            "gaps": [
                {
                    "gap_type": "evidence_gap",
                    "related_claim": "claim needing support",
                    "missing_support": "what is missing",
                    "priority": 0.0,
                    "repair_query": "query for programmatic retrieval",
                }
            ]
        },
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You generate structured GapNodes for future-intent-conditioned "
                "memory readiness diagnosis. Return strict JSON only."
            ),
        },
        {
            "role": "user",
            "content": f"Input:\n{json.dumps(payload, ensure_ascii=False)}",
        },
    ]
    parsed = _chat_json(llm_client, messages, config, max_tokens=750)
    if parsed is None:
        return fallback
    gaps = _normalize_gaps(parsed.get("gaps") or [], intent, max_gaps)
    return gaps or fallback


def repair_gaps(
    gaps: Sequence[Mapping[str, Any]],
    memory_nodes: Sequence[MemoryNode],
    config: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    reasoning_config = _reasoning_config(config)
    top_k = int(reasoning_config.get("repair_top_k") or 3)
    min_score = float(reasoning_config.get("repair_min_score") or 0.01)
    evidence: List[Dict[str, Any]] = []
    seen = set()
    for gap in gaps:
        gap_id = str(gap.get("gap_id") or "")
        query = " ".join(
            str(gap.get(key) or "")
            for key in ["repair_query", "missing_support", "related_claim"]
        )
        ranked = _rank_memories(query, memory_nodes)
        for score, memory in ranked[:top_k]:
            if score < min_score:
                continue
            dedupe_key = (gap_id, memory.id)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            evidence.append(
                {
                    "evidence_id": f"ev_{len(evidence) + 1:03d}",
                    "candidate_gap_id": gap_id,
                    "source_type": "memory",
                    "source_id": memory.id,
                    "chunk_id": memory.source_turn_id,
                    "content": memory.summary,
                    "score": round(float(score), 4),
                    "provenance": {
                        "memory_id": memory.id,
                        "turn_id": memory.source_turn_id,
                        "segment_id": memory.segment_id,
                    },
                }
            )
    return evidence


def bind_evidence(
    intent: Mapping[str, Any],
    gaps: Sequence[Mapping[str, Any]],
    evidence: Sequence[Mapping[str, Any]],
    llm_client: Optional[VLLMClient],
    config: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    fallback = _fallback_bindings(gaps, evidence)
    if not gaps or not evidence:
        return fallback
    if not _can_use_llm(llm_client, config):
        return fallback

    payload = {
        "intent": intent,
        "gaps": list(gaps),
        "evidence": list(evidence)[:20],
        "allowed_binding_types": sorted(ALLOWED_BINDING_TYPES),
        "required_schema": {
            "bindings": [
                {
                    "evidence_id": "ev_001",
                    "bind_to": "gap_001",
                    "binding_type": "supports",
                    "reason": "why this evidence binds to this gap",
                }
            ]
        },
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You bind retrieved evidence to GapNodes. Use only provided ids and "
                "return strict JSON only."
            ),
        },
        {
            "role": "user",
            "content": f"Input:\n{json.dumps(payload, ensure_ascii=False)}",
        },
    ]
    parsed = _chat_json(llm_client, messages, config, max_tokens=750)
    if parsed is None:
        return fallback

    evidence_ids = {str(item.get("evidence_id")) for item in evidence}
    gap_ids = {str(item.get("gap_id")) for item in gaps}
    bindings: List[Dict[str, Any]] = []
    seen = set()
    for item in parsed.get("bindings") or []:
        if not isinstance(item, Mapping):
            continue
        evidence_id = str(item.get("evidence_id") or "")
        bind_to = str(item.get("bind_to") or "")
        binding_type = str(item.get("binding_type") or "supports")
        key = (evidence_id, bind_to)
        if (
            evidence_id not in evidence_ids
            or bind_to not in gap_ids
            or binding_type not in ALLOWED_BINDING_TYPES
            or key in seen
        ):
            continue
        seen.add(key)
        bindings.append(
            {
                "evidence_id": evidence_id,
                "bind_to": bind_to,
                "binding_type": binding_type,
                "reason": str(item.get("reason") or "Bound by vLLM evidence binder."),
            }
        )
    return bindings or fallback


def verify_prepared_context(
    query: str,
    prepared_context: Mapping[str, Any],
    llm_client: Optional[VLLMClient],
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    reasoning_config = _reasoning_config(config)
    verifier_top_k = int(reasoning_config.get("verifier_top_k") or 3)
    memory_candidates = _verifier_memory_candidates(prepared_context)
    reranker_result = rerank_candidates(
        query=query,
        candidates=memory_candidates,
        config=config,
        default_top_k=verifier_top_k,
    )
    if (
        bool((config.get("reranker") or {}).get("prefer_for_verifier", True))
        and reranker_result.get("available")
        and reranker_result.get("selected_memory_ids")
    ):
        top_score = 0.0
        if reranker_result.get("scores"):
            top_score = safe_float(reranker_result["scores"][0].get("score"), 0.0)
        return {
            "decision": "use",
            "confidence": _clamp(top_score),
            "reason": str(reranker_result.get("reason") or "Reranker selected prepared memories."),
            "selected_memory_ids": list(reranker_result.get("selected_memory_ids") or []),
            "provider": f"reranker:{reranker_result.get('provider')}",
            "memory_candidates": memory_candidates,
            "scores": list(reranker_result.get("scores") or []),
            "reranker": reranker_result,
        }

    fallback = _fallback_verifier(query, prepared_context, config)
    if reranker_result.get("reason"):
        fallback["reranker"] = reranker_result
    if not _can_use_llm(llm_client, config, verifier=True):
        return fallback

    payload = {
        "query": query,
        "prepared_context": {
            "context_package_id": prepared_context.get("context_package_id"),
            "target_intent": prepared_context.get("target_intent"),
            "possible_user_query": prepared_context.get("possible_user_query"),
            "summary": prepared_context.get("summary"),
            "risk": prepared_context.get("risk"),
            "usable_claims": prepared_context.get("usable_claims", [])[:8],
            "memory_ids": prepared_context.get("memory_ids", []),
            "memory_candidates": memory_candidates,
            "repair_evidence": prepared_context.get("evidence", [])[:12],
            "bindings": prepared_context.get("bindings", [])[:12],
        },
        "selection_policy": [
            "Judge the actual query against candidate memory text, not only the predicted intent.",
            "Select every candidate memory that directly helps answer the query, up to max_selected.",
            "Prefer specific evidence memories over greeting, transition, or generic support memories.",
            "If the prepared context contains useful evidence but the predicted intent is broad, still select the useful evidence.",
            "Use reject/fallback only when no candidate memory can help answer the query.",
        ],
        "max_selected": verifier_top_k,
        "allowed_decisions": sorted(ALLOWED_VERIFIER_DECISIONS),
        "required_schema": {
            "decision": "use | partial_use | reject | fallback",
            "confidence": 0.0,
            "reason": "short reason",
            "selected_memory_ids": ["m_001"],
        },
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You are a query-time verifier for a pre-query prepared context. "
                "Your job is to decide whether prepared memory candidates can answer "
                "the actual query and to select the best candidate memory ids. Do not "
                "select ids from intent alone; use the candidate memory text. Return strict JSON only."
            ),
        },
        {
            "role": "user",
            "content": f"Input:\n{json.dumps(payload, ensure_ascii=False)}",
        },
    ]
    parsed = _chat_json(llm_client, messages, config, max_tokens=450)
    if parsed is None:
        return fallback

    decision = str(parsed.get("decision") or fallback["decision"]).lower()
    if decision not in ALLOWED_VERIFIER_DECISIONS:
        decision = fallback["decision"]
    allowed_memory_ids = {str(item) for item in prepared_context.get("memory_ids", [])}
    allowed_memory_ids.update(str(item.get("id")) for item in memory_candidates if item.get("id"))
    selected_memory_ids = [
        str(item)
        for item in parsed.get("selected_memory_ids") or []
        if str(item) in allowed_memory_ids
    ]
    if decision in {"use", "partial_use"} and bool(reasoning_config.get("verifier_augment_with_overlap", True)):
        selected_memory_ids = _augment_verifier_selection(
            query=query,
            selected_ids=selected_memory_ids,
            candidates=memory_candidates,
            top_k=verifier_top_k,
            min_score=float(reasoning_config.get("verifier_candidate_min_score") or 0.01),
        )
    if decision in {"use", "partial_use"} and not selected_memory_ids:
        selected_memory_ids = fallback.get("selected_memory_ids", [])
    return {
        "decision": decision,
        "confidence": _clamp(safe_float(parsed.get("confidence"), fallback["confidence"])),
        "reason": str(parsed.get("reason") or fallback["reason"]),
        "selected_memory_ids": selected_memory_ids,
        "provider": "vllm",
        "memory_candidates": memory_candidates,
        "scores": list(fallback.get("scores") or []),
        "reranker": reranker_result,
    }


def _intent_from_prediction(
    context_key: str,
    history: Sequence[Turn],
    prediction: Prediction,
) -> Dict[str, Any]:
    future_intents = [str(item) for item in prediction.predicted_future_intents if str(item).strip()]
    content = future_intents[0] if future_intents else _fallback_intent_text(history)
    return {
        "node_id": f"intent_{_stable_suffix(context_key)}",
        "node_type": "IntentNode",
        "content": content,
        "possible_user_query": future_intents[1] if len(future_intents) > 1 else "",
        "confidence": _intent_confidence(prediction),
        "required_support": [
            "user goal",
            "method definition",
            "related work distinction",
            "evidence",
        ],
    }


def _fallback_intent_text(history: Sequence[Turn]) -> str:
    recent = _recent_dialogue(history, 3)
    keywords = extract_keywords(recent, 8)
    if keywords:
        return "The user may continue asking about " + ", ".join(keywords) + "."
    return "The user may continue the current conversation topic."


def _intent_confidence(prediction: Prediction) -> float:
    values = [item.confidence for item in prediction.activated_memory_ids if item.confidence]
    if not values:
        return 0.5
    return round(sum(values) / len(values), 3)


def _fallback_meta_paths(intent: Mapping[str, Any], max_paths: int) -> List[Dict[str, Any]]:
    text = _intent_text(intent).lower()
    candidates = ["P1", "P4"]
    if any(term in text for term in ["paper", "proact", "simgrag", "lightmem", "simplemem", "graph", "baseline"]):
        candidates.extend(["P2", "P3"])
    if any(term in text for term in ["different", "distinguish", "novel", "conflict", "versus", "vs"]):
        candidates.append("P5")
    if any(term in text for term in ["gap", "missing", "evidence", "support", "ready"]):
        candidates.append("P6")
    return [
        {"path_id": path_id, "reason": _path_by_id(path_id).get("purpose", "")}
        for path_id in unique(candidates)[:max_paths]
    ]


def _rank_memories_for_path(
    intent_text: str,
    path_id: str,
    memory_nodes: Sequence[MemoryNode],
) -> List[Tuple[float, MemoryNode]]:
    scored: List[Tuple[float, MemoryNode]] = []
    intent_tokens = tokenize(intent_text)
    for memory in memory_nodes:
        text = memory.searchable_text()
        lower = text.lower()
        score = jaccard(intent_tokens, tokenize(text)) + memory.importance * 0.03
        if path_id == "P1" and any(term in lower for term in ["goal", "idea", "method", "plan", "need"]):
            score += 0.08
        elif path_id in {"P2", "P3"} and any(
            term in lower for term in ["paper", "proact", "simgrag", "lightmem", "simplemem", "claim", "baseline"]
        ):
            score += 0.10
        elif path_id == "P4" and any(term in lower for term in ["evidence", "result", "metric", "show", "because"]):
            score += 0.08
        elif path_id == "P5" and any(term in lower for term in ["not", "but", "however", "different", "versus", "instead"]):
            score += 0.10
        elif path_id == "P6" and any(term in lower for term in ["gap", "missing", "lack", "insufficient", "support"]):
            score += 0.10
        scored.append((score, memory))
    scored.sort(key=lambda item: (item[0], item[1].importance, item[1].timestamp), reverse=True)
    return scored


def _fallback_support_check(
    intent: Mapping[str, Any],
    activated_nodes: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    memory_nodes = [
        node for node in activated_nodes if node.get("node_type") == "MemoryNode"
    ]
    text = " ".join(str(node.get("summary") or "") for node in memory_nodes)
    intent_text = _intent_text(intent)
    score = jaccard(tokenize(intent_text), tokenize(text))
    missing: List[str] = []
    if len(memory_nodes) < 2:
        missing.append("Not enough activated memory nodes to support the predicted future intent.")
    if score < 0.04:
        missing.append("Activated memories have weak lexical overlap with the predicted future intent.")
    if any(term in intent_text.lower() for term in ["evidence", "claim", "paper", "baseline"]) and not any(
        term in text.lower() for term in ["evidence", "paper", "claim", "result", "baseline"]
    ):
        missing.append("Missing explicit claim or evidence support for the predicted intent.")
    if not missing:
        return {
            "support_status": "sufficient",
            "supported_claims": [f"Activated memory appears relevant to: {intent_text}"],
            "missing_support": [],
            "confidence": min(0.9, 0.55 + score),
            "provider": "heuristic",
        }
    return {
        "support_status": "partial" if memory_nodes else "insufficient",
        "supported_claims": [],
        "missing_support": missing,
        "confidence": max(0.2, min(0.75, 0.45 + score)),
        "provider": "heuristic",
    }


def _fallback_gaps(
    intent: Mapping[str, Any],
    support_check: Mapping[str, Any],
    max_gaps: int,
) -> List[Dict[str, Any]]:
    missing = support_check.get("missing_support") or []
    if not missing and support_check.get("support_status") != "sufficient":
        missing = ["Missing support for the predicted future intent."]
    raw = []
    for item in missing:
        text = str(item)
        lower = text.lower()
        if "conflict" in lower or "contradict" in lower:
            gap_type = "conflict_gap"
        elif "outdated" in lower or "fresh" in lower:
            gap_type = "freshness_gap"
        elif "definition" in lower:
            gap_type = "definition_gap"
        elif "coverage" in lower or "not enough" in lower:
            gap_type = "coverage_gap"
        else:
            gap_type = "evidence_gap"
        raw.append(
            {
                "gap_type": gap_type,
                "related_claim": _intent_text(intent),
                "missing_support": text,
                "priority": 0.8,
                "repair_query": " ".join(extract_keywords(f"{_intent_text(intent)} {text}", 12)),
            }
        )
    return _normalize_gaps(raw, intent, max_gaps)


def _normalize_gaps(
    raw_gaps: Sequence[Any],
    intent: Mapping[str, Any],
    max_gaps: int,
) -> List[Dict[str, Any]]:
    gaps: List[Dict[str, Any]] = []
    for item in raw_gaps:
        if not isinstance(item, Mapping):
            continue
        gap_type = str(item.get("gap_type") or "evidence_gap")
        if gap_type not in ALLOWED_GAP_TYPES:
            gap_type = "evidence_gap"
        missing_support = str(item.get("missing_support") or "").strip()
        related_claim = str(item.get("related_claim") or _intent_text(intent)).strip()
        if not missing_support:
            continue
        repair_query = str(item.get("repair_query") or "").strip()
        if not repair_query:
            repair_query = " ".join(extract_keywords(f"{related_claim} {missing_support}", 12))
        gaps.append(
            {
                "gap_id": f"gap_{len(gaps) + 1:03d}",
                "gap_type": gap_type,
                "related_claim": related_claim,
                "missing_support": missing_support,
                "priority": _clamp(safe_float(item.get("priority"), 0.7)),
                "repair_query": repair_query,
            }
        )
        if len(gaps) >= max_gaps:
            break
    gaps.sort(key=lambda gap: gap["priority"], reverse=True)
    for index, gap in enumerate(gaps, start=1):
        gap["gap_id"] = f"gap_{index:03d}"
    return gaps


def _rank_memories(query: str, memory_nodes: Sequence[MemoryNode]) -> List[Tuple[float, MemoryNode]]:
    query_tokens = tokenize(query)
    ranked: List[Tuple[float, MemoryNode]] = []
    for memory in memory_nodes:
        score = jaccard(query_tokens, tokenize(memory.searchable_text())) + memory.importance * 0.02
        ranked.append((score, memory))
    ranked.sort(key=lambda item: (item[0], item[1].importance, item[1].timestamp), reverse=True)
    return ranked


def _fallback_bindings(
    gaps: Sequence[Mapping[str, Any]],
    evidence: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    gap_ids = {str(gap.get("gap_id")) for gap in gaps}
    bindings: List[Dict[str, Any]] = []
    for item in evidence:
        gap_id = str(item.get("candidate_gap_id") or "")
        if gap_id not in gap_ids:
            continue
        bindings.append(
            {
                "evidence_id": str(item.get("evidence_id")),
                "bind_to": gap_id,
                "binding_type": "supports",
                "reason": "Programmatic binding from repair-query retrieval.",
            }
        )
    return bindings


def _build_usable_claims(
    intent: Mapping[str, Any],
    gaps: Sequence[Mapping[str, Any]],
    evidence: Sequence[Mapping[str, Any]],
    bindings: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    evidence_by_id = {str(item.get("evidence_id")): item for item in evidence}
    gaps_by_id = {str(item.get("gap_id")): item for item in gaps}
    grouped: Dict[str, List[str]] = {}
    for binding in bindings:
        grouped.setdefault(str(binding.get("bind_to")), []).append(str(binding.get("evidence_id")))

    claims: List[Dict[str, Any]] = []
    for gap_id, evidence_ids in grouped.items():
        gap = gaps_by_id.get(gap_id)
        if gap is None:
            continue
        claims.append(
            {
                "claim": str(gap.get("related_claim") or _intent_text(intent)),
                "gap_id": gap_id,
                "evidence": [
                    evidence_id for evidence_id in evidence_ids if evidence_id in evidence_by_id
                ],
                "status": "repaired" if evidence_ids else "unrepaired",
            }
        )
    if not claims:
        claims.append(
            {
                "claim": _intent_text(intent),
                "gap_id": None,
                "evidence": [],
                "status": "support_unverified",
            }
        )
    return claims


def _context_summary(
    intent: Mapping[str, Any],
    gaps: Sequence[Mapping[str, Any]],
    bindings: Sequence[Mapping[str, Any]],
) -> str:
    if not gaps:
        return f"Prepared context for predicted intent: {_intent_text(intent)}"
    repaired = {str(binding.get("bind_to")) for binding in bindings}
    gap_bits = [
        f"{gap.get('gap_id')}:{gap.get('gap_type')}:{'repaired' if gap.get('gap_id') in repaired else 'open'}"
        for gap in gaps
    ]
    return f"Prepared context for predicted intent: {_intent_text(intent)}. Gaps: {'; '.join(gap_bits)}"


def _fallback_verifier(
    query: str,
    prepared_context: Mapping[str, Any],
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    reasoning_config = _reasoning_config(config)
    threshold = float(reasoning_config.get("verifier_match_threshold") or 0.04)
    top_k = int(reasoning_config.get("verifier_top_k") or 3)
    candidates = _verifier_memory_candidates(prepared_context)
    context_text = " ".join(
        str(prepared_context.get(key) or "")
        for key in ["target_intent", "possible_user_query", "summary", "risk"]
    )
    for claim in prepared_context.get("usable_claims") or []:
        context_text += " " + str(claim.get("claim") or "")
    score = jaccard(tokenize(query), tokenize(context_text))
    selected_memory_ids = _augment_verifier_selection(
        query=query,
        selected_ids=[],
        candidates=candidates,
        top_k=top_k,
        min_score=float(reasoning_config.get("verifier_candidate_min_score") or 0.01),
    )
    if score >= threshold:
        decision = "use"
    elif score >= threshold / 2 or selected_memory_ids:
        decision = "partial_use"
    else:
        decision = "reject"
    return {
        "decision": decision,
        "confidence": round(min(0.95, max(0.05, 0.45 + score)), 3),
        "reason": f"Heuristic verifier lexical score={score:.3f}.",
        "selected_memory_ids": selected_memory_ids if decision in {"use", "partial_use"} else [],
        "provider": "heuristic",
        "memory_candidates": candidates,
    }


def _verifier_memory_candidates(prepared_context: Mapping[str, Any]) -> List[Dict[str, Any]]:
    candidates: Dict[str, Dict[str, Any]] = {}
    allowed_ids = {str(item) for item in prepared_context.get("memory_ids", [])}
    for memory in prepared_context.get("memory_candidates") or []:
        if not isinstance(memory, Mapping):
            continue
        memory_id = str(memory.get("id") or "")
        if not memory_id or (allowed_ids and memory_id not in allowed_ids):
            continue
        candidates[memory_id] = {
            "id": memory_id,
            "summary": str(memory.get("summary") or memory.get("content") or ""),
            "content": str(memory.get("content") or ""),
            "source_turn_id": memory.get("source_turn_id"),
            "segment_id": memory.get("segment_id"),
            "timestamp": memory.get("timestamp"),
            "path_id": memory.get("path_id"),
            "source": "prepared_memory",
        }

    for node in prepared_context.get("activated_nodes") or []:
        if not isinstance(node, Mapping) or node.get("node_type") != "MemoryNode":
            continue
        memory_id = str(node.get("id") or "")
        if not memory_id or (allowed_ids and memory_id not in allowed_ids):
            continue
        item = candidates.setdefault(memory_id, {"id": memory_id, "summary": ""})
        if node.get("summary"):
            item["summary"] = str(node.get("summary"))
        if node.get("content"):
            item["content"] = str(node.get("content"))
        item["source_turn_id"] = node.get("source_turn_id", item.get("source_turn_id"))
        item["segment_id"] = node.get("segment_id", item.get("segment_id"))
        item["timestamp"] = node.get("timestamp", item.get("timestamp"))
        item["path_id"] = node.get("path_id", item.get("path_id"))
        item["source"] = "activated_node"

    for evidence in prepared_context.get("evidence") or []:
        if not isinstance(evidence, Mapping) or evidence.get("source_type") != "memory":
            continue
        memory_id = str(evidence.get("source_id") or "")
        if not memory_id or (allowed_ids and memory_id not in allowed_ids):
            continue
        item = candidates.setdefault(
            memory_id,
            {
                "id": memory_id,
                "summary": "",
                "source_turn_id": evidence.get("chunk_id"),
                "segment_id": (evidence.get("provenance") or {}).get("segment_id"),
                "path_id": None,
                "source": "repair_evidence",
            },
        )
        if evidence.get("content") and not item.get("summary"):
            item["summary"] = str(evidence.get("content"))
        item.setdefault("evidence_ids", [])
        item["evidence_ids"].append(evidence.get("evidence_id"))
        item["repair_score"] = evidence.get("score")

    ordered_ids = [str(item) for item in prepared_context.get("memory_ids", [])]
    ordered = [candidates[memory_id] for memory_id in ordered_ids if memory_id in candidates]
    remaining = [item for memory_id, item in candidates.items() if memory_id not in set(ordered_ids)]
    return ordered + remaining


def _augment_verifier_selection(
    query: str,
    selected_ids: Sequence[str],
    candidates: Sequence[Mapping[str, Any]],
    top_k: int,
    min_score: float,
) -> List[str]:
    selected = []
    seen = set()
    for memory_id in selected_ids:
        if memory_id in seen:
            continue
        seen.add(memory_id)
        selected.append(memory_id)
        if len(selected) >= top_k:
            return selected

    scored = []
    query_tokens = tokenize(query)
    for candidate in candidates:
        memory_id = str(candidate.get("id") or "")
        if not memory_id or memory_id in seen:
            continue
        text = " ".join(
            str(candidate.get(key) or "")
            for key in ["summary", "source_turn_id", "path_id"]
        )
        score = jaccard(query_tokens, tokenize(text))
        if score >= min_score:
            scored.append((score, memory_id))
    scored.sort(key=lambda item: item[0], reverse=True)
    for _score, memory_id in scored:
        selected.append(memory_id)
        seen.add(memory_id)
        if len(selected) >= top_k:
            break
    return selected


def _memory_candidate_records(
    memory_ids: Sequence[str],
    memory_nodes: Sequence[MemoryNode],
) -> List[Dict[str, Any]]:
    by_id = {memory.id: memory for memory in memory_nodes}
    records: List[Dict[str, Any]] = []
    for memory_id in memory_ids:
        memory = by_id.get(str(memory_id))
        if memory is None:
            continue
        records.append(
            {
                "id": memory.id,
                "summary": memory.summary,
                "content": memory.content,
                "source_turn_id": memory.source_turn_id,
                "segment_id": memory.segment_id,
                "timestamp": memory.timestamp,
                "keywords": list(memory.keywords),
                "entities": list(memory.entities),
            }
        )
    return records


def _chat_json(
    llm_client: Optional[VLLMClient],
    messages: Sequence[Mapping[str, str]],
    config: Mapping[str, Any],
    max_tokens: int,
) -> Optional[Dict[str, Any]]:
    if llm_client is None:
        return None
    llm_config = dict(config.get("llm") or {})
    try:
        content, usage = llm_client.chat(
            list(messages),
            temperature=float(llm_config.get("temperature", 0.0)),
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        parsed = parse_llm_json(content)
        parsed["_usage"] = usage
        return parsed
    except (VLLMError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        if not bool(llm_config.get("fallback_to_heuristic", True)):
            raise
        return None


def _can_use_llm(
    llm_client: Optional[VLLMClient],
    config: Mapping[str, Any],
    verifier: bool = False,
) -> bool:
    reasoning_config = _reasoning_config(config)
    if verifier and not bool(reasoning_config.get("use_llm_verifier", True)):
        return False
    return llm_client is not None and bool(reasoning_config.get("use_llm", True))


def _reasoning_config(config: Mapping[str, Any]) -> Dict[str, Any]:
    defaults = {
        "enabled": True,
        "use_llm": True,
        "use_llm_verifier": True,
        "max_paths": 4,
        "per_path_top_k": 5,
        "max_gaps": 4,
        "repair_top_k": 5,
        "repair_min_score": 0.01,
        "working_context_budget": 12,
        "compression_enabled": True,
        "prepared_cache_budget": 0,
        "compression_intent_weight": 0.30,
        "compression_prediction_weight": 0.22,
        "compression_repair_weight": 0.18,
        "compression_path_weight": 0.12,
        "compression_importance_weight": 0.08,
        "compression_recency_weight": 0.05,
        "compression_centrality_weight": 0.05,
        "compression_diversity_weight": 0.12,
        "verifier_top_k": 3,
        "verifier_match_threshold": 0.04,
    }
    values = dict(config.get("gap_reasoning") or {})
    defaults.update(values)
    return defaults


def _path_by_id(path_id: str) -> Dict[str, str]:
    for item in META_PATH_LIBRARY:
        if item["path_id"] == path_id:
            return item
    return {}


def _recent_dialogue(history: Sequence[Turn], window_size: int) -> str:
    return "\n".join(f"{turn.speaker}: {turn.text}" for turn in history[-window_size:])


def _intent_text(intent: Mapping[str, Any]) -> str:
    return " ".join(
        str(intent.get(key) or "")
        for key in ["content", "possible_user_query", "required_support"]
    )


def _stable_suffix(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "ctx")).strip("_").lower()
    return cleaned[:40] or "ctx"


def _clamp(value: float) -> float:
    return round(min(0.99, max(0.01, value)), 3)
