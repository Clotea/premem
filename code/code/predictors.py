from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Protocol, Sequence

from .graph_store import GraphStore, memory_catalog
from .utils import (
    ActivatedMemory,
    MemoryNode,
    Prediction,
    Turn,
    parse_llm_json,
    safe_float,
    tokenize,
    unique,
)
from .vllm_client import VLLMClient, VLLMError


class MemoryNeedPredictor(Protocol):
    name: str

    def predict(
        self,
        history: Sequence[Turn],
        memory_nodes: Sequence[MemoryNode],
        graph: GraphStore,
        budget: int,
    ) -> Prediction:
        ...


class HeuristicPredictor:
    name = "heuristic"

    def predict(
        self,
        history: Sequence[Turn],
        memory_nodes: Sequence[MemoryNode],
        graph: GraphStore,
        budget: int,
    ) -> Prediction:
        if not memory_nodes or budget <= 0:
            return Prediction(predicted_future_intents=[], activated_memory_ids=[])

        signals = active_signals(history)
        max_timestamp = max(memory.timestamp for memory in memory_nodes) or 1
        ranked = []
        for memory in memory_nodes:
            memory_tokens = tokenize(memory.searchable_text())
            keyword_hits = sum(1 for token in memory_tokens if token in signals["recent_tokens"])
            entity_hits = sum(1 for entity in memory.entities if entity in signals["active_entities"])
            segment_hit = 1 if memory.segment_id in signals["active_segments"] else 0
            recency = memory.timestamp / max_timestamp
            score = (
                keyword_hits * 0.18
                + entity_hits * 0.25
                + segment_hit * 0.28
                + memory.importance * 0.30
                + recency * 0.07
            )
            ranked.append((score, memory))

        ranked.sort(key=lambda item: item[0], reverse=True)
        activated = [
            ActivatedMemory(
                id=memory.id,
                reason="Heuristic prediction from recent keyword/entity overlap, segment, importance, and recency.",
                confidence=round(min(0.99, max(0.01, score)), 3),
            )
            for score, memory in ranked[:budget]
        ]
        return Prediction(
            predicted_future_intents=[
                "The user may continue the active segment.",
                "The user may ask about recently mentioned decisions, entities, or metrics.",
            ],
            activated_memory_ids=activated,
            metadata={"provider": self.name},
        )


class VLLMPredictor:
    name = "vllm"

    def __init__(
        self,
        client: VLLMClient,
        fallback: Optional[MemoryNeedPredictor] = None,
        fallback_on_error: bool = True,
        candidate_limit: int = 80,
    ) -> None:
        self.client = client
        self.fallback = fallback or HeuristicPredictor()
        self.fallback_on_error = fallback_on_error
        self.candidate_limit = candidate_limit

    def predict(
        self,
        history: Sequence[Turn],
        memory_nodes: Sequence[MemoryNode],
        graph: GraphStore,
        budget: int,
    ) -> Prediction:
        if not memory_nodes or budget <= 0:
            return Prediction(predicted_future_intents=[], activated_memory_ids=[])

        candidates = self._candidate_memories(history, memory_nodes)
        prompt_payload = {
            "recent_dialogue": recent_dialogue_summary(history, window_size=4),
            "active_entities": active_signals(history)["active_entities"],
            "cache_budget": budget,
            "memory_catalog": memory_catalog(candidates, limit=self.candidate_limit),
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a future memory need predictor for a proactive memory cache. "
                    "You only see dialogue history and a memory catalog. You must not assume "
                    "the next query is available. Return strict JSON only."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Select memory ids that are likely useful for the next user turn. "
                    "Return JSON with keys predicted_future_intents and activated_memory_ids. "
                    "activated_memory_ids must be an array of objects with id, reason, confidence. "
                    f"Input:\n{json.dumps(prompt_payload, ensure_ascii=False)}"
                ),
            },
        ]

        try:
            content, usage = self.client.chat(
                messages,
                temperature=0.0,
                max_tokens=600,
                response_format={"type": "json_object"},
            )
            parsed = parse_llm_json(content)
            prediction = self._prediction_from_response(parsed, candidates, budget)
            prediction.metadata.update({"provider": self.name, "usage": usage})
            if len(prediction.activated_memory_ids) < budget and self.fallback is not None:
                prediction = self._fill_with_fallback(prediction, history, memory_nodes, graph, budget)
            return prediction
        except (VLLMError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            if not self.fallback_on_error:
                raise
            fallback_prediction = self.fallback.predict(history, memory_nodes, graph, budget)
            fallback_prediction.metadata.update(
                {
                    "provider": self.name,
                    "fallback_provider": getattr(self.fallback, "name", "fallback"),
                    "error": str(exc),
                }
            )
            return fallback_prediction

    def _candidate_memories(
        self,
        history: Sequence[Turn],
        memory_nodes: Sequence[MemoryNode],
    ) -> List[MemoryNode]:
        heuristic = HeuristicPredictor().predict(
            history,
            memory_nodes,
            GraphStore(),
            max(self.candidate_limit, 1),
        )
        by_id = {memory.id: memory for memory in memory_nodes}
        chosen = [by_id[item.id] for item in heuristic.activated_memory_ids if item.id in by_id]
        if len(chosen) < min(len(memory_nodes), self.candidate_limit):
            chosen_ids = {memory.id for memory in chosen}
            remainder = sorted(memory_nodes, key=lambda item: (item.importance, item.timestamp), reverse=True)
            for memory in remainder:
                if memory.id not in chosen_ids:
                    chosen.append(memory)
                    chosen_ids.add(memory.id)
                if len(chosen) >= self.candidate_limit:
                    break
        return chosen[: self.candidate_limit]

    def _prediction_from_response(
        self,
        parsed: Dict[str, Any],
        candidates: Sequence[MemoryNode],
        budget: int,
    ) -> Prediction:
        valid_ids = {memory.id for memory in candidates}
        seen = set()
        activated: List[ActivatedMemory] = []
        raw_items = parsed.get("activated_memory_ids") or []
        for item in raw_items:
            if isinstance(item, str):
                memory_id = item
                reason = "Selected by vLLM predictor."
                confidence = 0.5
            elif isinstance(item, dict):
                memory_id = str(item.get("id") or "")
                reason = str(item.get("reason") or "Selected by vLLM predictor.")
                confidence = safe_float(item.get("confidence"), 0.5)
            else:
                continue
            if memory_id not in valid_ids or memory_id in seen:
                continue
            seen.add(memory_id)
            activated.append(
                ActivatedMemory(
                    id=memory_id,
                    reason=reason,
                    confidence=round(min(0.99, max(0.01, confidence)), 3),
                )
            )
            if len(activated) >= budget:
                break

        intents = parsed.get("predicted_future_intents") or []
        if not isinstance(intents, list):
            intents = [str(intents)]
        return Prediction(
            predicted_future_intents=[str(item) for item in intents[:5]],
            activated_memory_ids=activated,
        )

    def _fill_with_fallback(
        self,
        prediction: Prediction,
        history: Sequence[Turn],
        memory_nodes: Sequence[MemoryNode],
        graph: GraphStore,
        budget: int,
    ) -> Prediction:
        selected = {item.id for item in prediction.activated_memory_ids}
        fallback = self.fallback.predict(history, memory_nodes, graph, budget)
        for item in fallback.activated_memory_ids:
            if item.id in selected:
                continue
            prediction.activated_memory_ids.append(item)
            selected.add(item.id)
            if len(prediction.activated_memory_ids) >= budget:
                break
        if not prediction.predicted_future_intents:
            prediction.predicted_future_intents = fallback.predicted_future_intents
        return prediction


def create_predictor(config: Dict[str, Any], client: Optional[VLLMClient]) -> MemoryNeedPredictor:
    predictor_name = str(config.get("predictor") or "vllm").lower()
    heuristic = HeuristicPredictor()
    if predictor_name == "heuristic":
        return heuristic
    if predictor_name in {"vllm", "auto"} and client is not None:
        llm_config = dict(config.get("llm") or {})
        return VLLMPredictor(
            client=client,
            fallback=heuristic,
            fallback_on_error=bool(llm_config.get("fallback_to_heuristic", True)),
            candidate_limit=int(config.get("predictor_candidate_limit") or 80),
        )
    return heuristic


def recent_dialogue_summary(history: Sequence[Turn], window_size: int = 2) -> str:
    return "\n".join(f"{turn.speaker}: {turn.text}" for turn in history[-window_size:])


def active_signals(history: Sequence[Turn]) -> Dict[str, Any]:
    recent = recent_dialogue_summary(history, window_size=2)
    active_segments = unique(turn.segment_id for turn in history[-2:])
    active_entities = unique(
        entity
        for turn in history[-2:]
        for memory in turn.memories
        for entity in memory.get("entities", [])
    )
    return {
        "recent": recent,
        "recent_tokens": set(tokenize(recent)),
        "active_segments": active_segments,
        "active_entities": active_entities,
    }
