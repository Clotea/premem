from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from preact_demo.code.pipeline import run_evaluation
from preact_demo.code.predictors import create_predictor
from preact_demo.code.utils import (
    Sample,
    Turn,
    extract_entities,
    extract_keywords,
    format_table,
    infer_memory_type,
    load_json,
    truncate,
    estimate_importance,
)
from preact_demo.code.vllm_client import VLLMClient


LOCOMO_URL = "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json"


def load_locomo_samples(path: Path | str, limit: int | None = None) -> List[Sample]:
    records = load_json(path)
    samples = convert_locomo(records)
    if limit is None:
        return samples
    if limit == 0:
        return samples
    if limit < 0:
        raise ValueError("limit must be >= 0")
    return samples[:limit]


def convert_locomo(records: Sequence[Dict[str, Any]]) -> List[Sample]:
    samples: List[Sample] = []
    for conversation_index, record in enumerate(records):
        conversation_id = f"locomo_c{conversation_index + 1:02d}"
        history = convert_conversation(record.get("conversation") or {}, conversation_id)
        for qa_index, qa in enumerate(record.get("qa") or []):
            question = str(qa.get("question") or "")
            answer = str(qa.get("answer") or qa.get("adversarial_answer") or "")
            samples.append(
                Sample(
                    id=f"{conversation_id}_qa_{qa_index + 1:03d}",
                    history_cache_key=conversation_id,
                    history=history,
                    question=question,
                    answer=answer,
                    evidence_terms=extract_keywords(f"{question} {answer}", 12),
                    gold_evidence_turn_ids=list(qa.get("evidence") or []),
                    metadata={
                        "dataset": "locomo",
                        "conversation_id": conversation_id,
                        "category": qa.get("category"),
                    },
                )
            )
    return samples


def convert_conversation(conversation: Dict[str, Any], conversation_id: str) -> List[Turn]:
    turns: List[Turn] = []
    timestamp = 0
    session_numbers = sorted(
        int(key.split("_")[1])
        for key, value in conversation.items()
        if key.startswith("session_")
        and key.split("_")[1].isdigit()
        and isinstance(value, list)
    )
    for session_number in session_numbers:
        session_key = f"session_{session_number}"
        session_id = f"seg_{conversation_id}_{session_number}"
        session_summary = str(
            conversation.get(f"session_{session_number}_summary")
            or conversation.get(f"session_{session_number}_date_time")
            or session_id
        )
        for raw_turn in conversation.get(session_key) or []:
            timestamp += 1
            text = " ".join(
                str(part)
                for part in [raw_turn.get("text"), raw_turn.get("blip_caption")]
                if part
            )
            speaker = str(raw_turn.get("speaker") or "unknown")
            turn_id = str(raw_turn.get("dia_id") or f"D{session_number}:{timestamp}")
            content = f"{speaker}: {text}"
            turns.append(
                Turn(
                    id=turn_id,
                    timestamp=timestamp,
                    speaker=speaker,
                    segment_id=session_id,
                    segment_summary=session_summary,
                    text=text,
                    memories=[
                        {
                            "type": infer_memory_type(text),
                            "content": content,
                            "summary": truncate(content, 180),
                            "keywords": extract_keywords(text, 10),
                            "entities": extract_entities(text),
                            "importance": estimate_importance(text),
                        }
                    ],
                )
            )
    return turns


def download_locomo(destination: Path | str) -> None:
    import urllib.request

    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(LOCOMO_URL, target)


def run_locomo_initial_test(config: Dict[str, Any], locomo_path: Path | str, limit: int = 10) -> List[Dict[str, Any]]:
    samples = load_locomo_samples(locomo_path, limit=limit)
    llm_config = dict(config.get("llm") or {})
    client = VLLMClient(
        base_url=str(llm_config.get("base_url") or "http://127.0.0.1:8000/v1"),
        model=str(llm_config.get("model") or "Qwen/Qwen2.5-7B-Instruct"),
        timeout=float(llm_config.get("timeout") or 30),
        api_key=llm_config.get("api_key"),
    )
    predictor = create_predictor(config, client)
    return run_evaluation(samples, config, predictor, llm_client=client)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a small LoCoMo smoke test for the Python demo.")
    project_root = Path(__file__).resolve().parents[1]
    code_root = Path(__file__).resolve().parent
    parser.add_argument("--locomo-path", default=str(project_root / "data" / "locomo" / "locomo10.json"))
    parser.add_argument("--config", default=str(code_root / "configs" / "python_demo.json"))
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--download-locomo", action="store_true")
    args = parser.parse_args()

    locomo_path = Path(args.locomo_path)
    if not locomo_path.exists():
        if not args.download_locomo:
            raise FileNotFoundError(f"LoCoMo file not found: {locomo_path}")
        download_locomo(locomo_path)

    config = load_json(args.config)
    summary = run_locomo_initial_test(config, locomo_path, limit=args.limit)
    print(f"LoCoMo path: {locomo_path}")
    print(f"Samples: {args.limit if args.limit else 'all'}")
    print("\nActivation Quality")
    print(format_table(summary, ["method", "budget", "precision", "recall", "hit_rate", "wasted_rate"]))
    print("\nAnswer Quality")
    print(format_table(summary, ["method", "f1", "rouge_l", "llm_judge", "faithfulness"]))
    print("\nEfficiency")
    print(format_table(summary, ["method", "query_time_latency_ms", "idle_time_cost", "total_tokens", "fallback_rate"]))


if __name__ == "__main__":
    main()
