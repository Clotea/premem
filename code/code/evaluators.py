from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from .utils import parse_llm_json
from .vllm_client import VLLMClient, VLLMError


JUDGE_PROMPTS = {
    "mem0_paper_v1": (
        "You are an impartial answer correctness judge. Given a question, a "
        "reference answer, and a candidate answer, decide whether the candidate "
        "conveys the same essential information as the reference. Semantic "
        "equivalence is allowed; extra contradictory claims are not. Return JSON "
        'only: {\"label\": 0 or 1, \"reason\": \"brief explanation\"}.'
    ),
    "longmemeval_strict_v1": (
        "You are a strict long-term-memory QA evaluator. Mark the candidate correct "
        "only when it contains every essential fact required by the reference, has "
        "no contradiction, and resolves requested dates or time periods correctly. "
        "For list questions require all reference items; for yes/no questions "
        "require the correct polarity. Return JSON only: "
        '{\"label\": 0 or 1, \"reason\": \"brief explanation\"}.'
    ),
}


def judge_answer(
    *,
    question: str,
    reference: str,
    candidate: str,
    category: Any,
    client: Optional[VLLMClient],
    protocol: str,
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    if client is None:
        return {"score": None, "reason": "judge_client_unavailable", "protocol": protocol}
    system = JUDGE_PROMPTS.get(protocol)
    if system is None:
        raise ValueError(f"Unsupported judge protocol: {protocol}")
    reference_value = str(reference or "")
    if str(category) == "3" and ";" in reference_value:
        reference_value = reference_value.split(";", 1)[0].strip()
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"Question: {question}\n"
                f"Category: {category}\n"
                f"Reference answer: {reference_value}\n"
                f"Candidate answer: {candidate}"
            ),
        },
    ]
    try:
        text, usage = client.chat(
            messages,
            temperature=float(config.get("temperature") or 0.0),
            max_tokens=int(config.get("max_tokens") or 128),
            response_format={"type": "json_object"},
        )
        payload = parse_llm_json(text)
        raw_label = payload.get("label")
        score = 1.0 if raw_label in {1, "1", True, "correct", "CORRECT"} else 0.0
        return {
            "score": score,
            "reason": str(payload.get("reason") or ""),
            "protocol": protocol,
            "model": client.model,
            "endpoint": client.base_url,
            "usage": usage,
        }
    except (VLLMError, ValueError, TypeError) as exc:
        if not bool(config.get("fallback_on_error", True)):
            raise
        return {
            "score": None,
            "reason": f"judge_error:{type(exc).__name__}",
            "protocol": protocol,
            "model": client.model,
        }

