from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Sequence

from preact_demo.code.utils import (
    FutureNeedHypothesis,
    GroundedMemory,
    MemoryGap,
    MemoryNode,
    parse_llm_json,
    safe_float,
)
from preact_demo.code.vllm_client import VLLMClient, VLLMError


def detect_memory_gaps(
    hypotheses: Sequence[FutureNeedHypothesis],
    grounded: Sequence[GroundedMemory],
    memory_nodes: Sequence[MemoryNode],
    config: Mapping[str, Any],
    llm_client: Optional[VLLMClient] = None,
) -> List[MemoryGap]:
    gap_config = dict(config.get("memory_gap") or {})
    threshold = float(gap_config.get("coverage_threshold") or 0.18)
    score_by_hypothesis: Dict[str, float] = {}
    for item in grounded:
        score_by_hypothesis[item.hypothesis_id] = max(
            score_by_hypothesis.get(item.hypothesis_id, 0.0), item.score
        )

    gaps = []
    for hypothesis in hypotheses:
        score = score_by_hypothesis.get(hypothesis.id, 0.0)
        exists = score < threshold
        queries = hypothesis.search_queries or [hypothesis.intent]
        gaps.append(
            MemoryGap(
                hypothesis_id=hypothesis.id,
                exists=exists,
                reason=(
                    f"Best memory grounding score {score:.3f} is below {threshold:.3f}."
                    if exists
                    else f"Memory graph coverage score {score:.3f} meets the threshold."
                ),
                max_grounding_score=round(score, 4),
                search_queries=queries[:3] if exists else [],
            )
        )

    mode = str(gap_config.get("mode") or "score").lower()
    if mode in {"llm", "hybrid"} and llm_client is not None:
        return _llm_detect(hypotheses, grounded, memory_nodes, gaps, llm_client) or gaps
    return gaps


def _llm_detect(
    hypotheses: Sequence[FutureNeedHypothesis],
    grounded: Sequence[GroundedMemory],
    memory_nodes: Sequence[MemoryNode],
    score_gaps: Sequence[MemoryGap],
    client: VLLMClient,
) -> List[MemoryGap]:
    memory_by_id = {item.id: item for item in memory_nodes}
    payload = {
        "hypotheses": [item.to_dict() for item in hypotheses],
        "grounded_memories": [
            {
                **item.to_dict(),
                "summary": memory_by_id[item.memory_id].summary,
            }
            for item in grounded
            if item.memory_id in memory_by_id
        ],
        "score_based_gaps": [item.to_dict() for item in score_gaps],
    }
    messages = [
        {
            "role": "system",
            "content": "Judge whether the memory graph lacks information for each future need. Return JSON only.",
        },
        {
            "role": "user",
            "content": (
                "Return gaps as objects with hypothesis_id, exists, reason, confidence, and search_queries. "
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

    valid_ids = {item.id for item in hypotheses}
    result = []
    for raw in parsed.get("gaps") or []:
        if not isinstance(raw, dict):
            continue
        hypothesis_id = str(raw.get("hypothesis_id") or "")
        if hypothesis_id not in valid_ids:
            continue
        exists = bool(raw.get("exists"))
        result.append(
            MemoryGap(
                hypothesis_id=hypothesis_id,
                exists=exists,
                reason=str(raw.get("reason") or "LLM gap judgment."),
                max_grounding_score=safe_float(raw.get("confidence"), 0.0),
                search_queries=[str(item) for item in raw.get("search_queries") or []] if exists else [],
            )
        )
    return result
