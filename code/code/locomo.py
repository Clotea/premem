from __future__ import annotations

import argparse
import os
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    loaded_code = sys.modules.get("code")
    if loaded_code is not None and not hasattr(loaded_code, "__path__"):
        del sys.modules["code"]
    from code.pipeline import run_evaluation
    from code.predictors import create_predictor
    from code.reranker import assert_reranker_available, reranker_required
    from code.utils import (
        Sample,
        Turn,
        extract_entities,
        extract_keywords,
        format_table,
        infer_memory_type,
        load_json,
        write_json,
        truncate,
        estimate_importance,
    )
    from code.vllm_client import VLLMClient
    from code.temporal import resolve_temporal_mentions
else:
    from .pipeline import run_evaluation
    from .predictors import create_predictor
    from .reranker import assert_reranker_available, reranker_required
    from .utils import (
        Sample,
        Turn,
        extract_entities,
        extract_keywords,
        format_table,
        infer_memory_type,
        load_json,
        write_json,
        truncate,
        estimate_importance,
    )
    from .vllm_client import VLLMClient
    from .temporal import resolve_temporal_mentions


LOCOMO_URL = "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json"


def load_locomo_samples(
    path: Path | str,
    limit: int | None = None,
    eval_mode: str = "time-sliced",
    sample_mode: str = "head",
    sample_seed: int = 7,
) -> List[Sample]:
    records = load_json(path)
    samples = convert_locomo(records, eval_mode=eval_mode)
    if limit is None:
        return samples
    if limit == 0:
        return samples
    if limit < 0:
        raise ValueError("limit must be >= 0")
    if sample_mode == "head":
        return samples[:limit]
    if sample_mode != "stratified":
        raise ValueError(f"Unsupported sample_mode: {sample_mode}")
    grouped: Dict[str, List[Sample]] = {}
    for sample in samples:
        grouped.setdefault(str(sample.metadata.get("category") or "unknown"), []).append(sample)
    rng = random.Random(sample_seed)
    for rows in grouped.values():
        rng.shuffle(rows)
    selected: List[Sample] = []
    categories = sorted(grouped)
    while len(selected) < limit and any(grouped.values()):
        for category in categories:
            if grouped[category] and len(selected) < limit:
                selected.append(grouped[category].pop())
    return selected


def convert_locomo(records: Sequence[Dict[str, Any]], eval_mode: str = "time-sliced") -> List[Sample]:
    mode = str(eval_mode or "time-sliced").lower().replace("_", "-")
    if mode in {"qa", "full", "full-qa"}:
        return convert_locomo_full_qa(records)
    if mode in {"time-sliced", "timesliced", "sliced"}:
        return convert_locomo_time_sliced(records)
    raise ValueError(f"Unsupported LoCoMo eval_mode: {eval_mode}")


def convert_locomo_full_qa(records: Sequence[Dict[str, Any]]) -> List[Sample]:
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
                        "eval_mode": "qa",
                        "conversation_id": conversation_id,
                        "category": qa.get("category"),
                        "history_turn_count": len(history),
                        "reference_date_time": (
                            history[-1].metadata.get("session_date_time") if history else None
                        ),
                    },
                )
            )
    return samples


def convert_locomo_time_sliced(records: Sequence[Dict[str, Any]]) -> List[Sample]:
    samples: List[Sample] = []
    for conversation_index, record in enumerate(records):
        conversation_id = f"locomo_c{conversation_index + 1:02d}"
        full_history = convert_conversation(record.get("conversation") or {}, conversation_id)
        turn_index = {turn.id: index for index, turn in enumerate(full_history)}
        for qa_index, qa in enumerate(record.get("qa") or []):
            question = str(qa.get("question") or "")
            answer = str(qa.get("answer") or qa.get("adversarial_answer") or "")
            raw_evidence_ids = [str(item) for item in qa.get("evidence") or []]
            valid_evidence_ids = [turn_id for turn_id in raw_evidence_ids if turn_id in turn_index]
            if valid_evidence_ids:
                slice_end = max(turn_index[turn_id] for turn_id in valid_evidence_ids) + 1
                history = full_history[:slice_end]
                slice_reason = "through_latest_gold_evidence"
            else:
                history = full_history
                slice_end = len(full_history)
                slice_reason = "full_history_no_valid_gold_evidence"

            if not history:
                continue

            sample_id = f"{conversation_id}_tsqa_{qa_index + 1:03d}"
            slice_end_turn_id = history[-1].id
            samples.append(
                Sample(
                    id=sample_id,
                    history_cache_key=f"{sample_id}_through_{slice_end_turn_id}",
                    history=history,
                    question=question,
                    answer=answer,
                    evidence_terms=extract_keywords(f"{question} {answer}", 12),
                    gold_evidence_turn_ids=valid_evidence_ids or raw_evidence_ids,
                    metadata={
                        "dataset": "locomo",
                        "eval_mode": "time-sliced",
                        "conversation_id": conversation_id,
                        "category": qa.get("category"),
                        "history_turn_count": len(history),
                        "full_history_turn_count": len(full_history),
                        "slice_end_index": slice_end,
                        "slice_end_turn_id": slice_end_turn_id,
                        "slice_reason": slice_reason,
                        "reference_date_time": history[-1].metadata.get("session_date_time"),
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
        session_date_time = str(conversation.get(f"session_{session_number}_date_time") or "")
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
            raw_metadata = {
                "session_number": session_number,
                "session_date_time": session_date_time,
                "raw_text": str(raw_turn.get("text") or ""),
                "blip_caption": str(raw_turn.get("blip_caption") or ""),
                "image_query": str(raw_turn.get("query") or ""),
                "img_url": list(raw_turn.get("img_url") or []),
            }
            raw_metadata["temporal_mentions"] = resolve_temporal_mentions(
                text,
                session_date_time,
            )
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
                            "metadata": dict(raw_metadata),
                        }
                    ],
                    metadata=raw_metadata,
                )
            )
    return turns


def download_locomo(destination: Path | str) -> None:
    import urllib.request

    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(LOCOMO_URL, target)


def run_locomo_initial_test(
    config: Dict[str, Any],
    locomo_path: Path | str,
    limit: int = 10,
    eval_mode: str = "time-sliced",
) -> List[Dict[str, Any]]:
    samples = load_locomo_samples(locomo_path, limit=limit, eval_mode=eval_mode)
    llm_config = dict(config.get("llm") or {})
    client = VLLMClient(
        base_url=str(llm_config.get("base_url") or "http://127.0.0.1:30000/v1"),
        model=str(llm_config.get("model") or "Qwen/Qwen2.5-7B-Instruct"),
        timeout=float(llm_config.get("timeout") or 30),
        api_key=llm_config.get("api_key"),
    )
    predictor = create_predictor(config, client)
    return run_evaluation(samples, config, predictor, llm_client=client, judge_client=client)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    locomo_path = Path(args.locomo_path)
    if not locomo_path.exists():
        if not args.download_locomo:
            raise FileNotFoundError(
                f"LoCoMo file not found: {locomo_path}. "
                "Use --download-locomo or provide --locomo-path."
            )
        download_locomo(locomo_path)

    config = load_config(args.config)
    apply_overrides(config, args)
    if reranker_required(config):
        reranker_config = config.get("reranker") or {}
        print(
            "Reranker preflight: "
            f"{reranker_config.get('provider')}:{reranker_config.get('model')} "
            f"on devices={reranker_config.get('devices')}",
            flush=True,
        )
        preflight = assert_reranker_available(config)
        print(
            "Reranker preflight OK: "
            f"provider={preflight.get('provider')} selected={preflight.get('selected_memory_ids')}",
            flush=True,
        )
    samples = load_locomo_samples(
        locomo_path,
        limit=args.limit,
        eval_mode=args.eval_mode,
        sample_mode=args.sample_mode,
        sample_seed=args.sample_seed,
    )
    if not samples:
        raise RuntimeError("No LoCoMo samples loaded.")

    llm_config = dict(config.get("llm") or {})
    client = VLLMClient(
        base_url=str(llm_config.get("base_url") or "http://127.0.0.1:30000/v1"),
        model=str(llm_config.get("model") or "Qwen/Qwen2.5-7B-Instruct"),
        timeout=float(llm_config.get("timeout") or 30),
        api_key=llm_config.get("api_key"),
    )
    predictor = create_predictor(config, client)
    judge_config = dict((config.get("evaluation") or {}).get("judge") or {})
    judge_client: Optional[VLLMClient] = None
    if bool(judge_config.get("enabled", False)):
        judge_client = VLLMClient(
            base_url=str(judge_config.get("base_url") or client.base_url),
            model=str(judge_config.get("model") or client.model),
            timeout=float(judge_config.get("timeout") or llm_config.get("timeout") or 30),
            api_key=judge_config.get("api_key") or llm_config.get("api_key"),
        )
    summary = run_evaluation(
        samples,
        config,
        predictor,
        llm_client=client,
        judge_client=judge_client,
    )

    print("PreAct-Memory LoCoMo Evaluation")
    print(f"Eval mode: {args.eval_mode}")
    print(f"Samples: {len(samples)}")
    print(f"Sampling: {args.sample_mode} (seed={args.sample_seed})")
    print(f"Predictor: {getattr(predictor, 'name', config.get('predictor'))}")
    print(f"vLLM endpoint: {client.base_url}")
    print(f"vLLM model: {client.model}")
    print(f"LoCoMo path: {locomo_path.resolve()}")
    print(f"LoCoMo source: {LOCOMO_URL}")
    if args.eval_mode == "time-sliced":
        print("History slicing: per-QA prefix through latest gold evidence turn")
    else:
        print("History slicing: full conversation shared by QA samples")

    print("\nActivation Quality")
    print(format_table(summary, ["method", "budget", "selected_count", "precision", "recall", "hit_rate", "full_cover_rate", "wasted_rate"]))
    print("\nAnswer Quality")
    print(format_table(summary, ["method", "official_f1", "temporal_f1", "bleu1", "llm_judge", "strict_judge", "faithfulness"]))
    print("\nEfficiency")
    print(format_table(summary, ["method", "query_retrieval_latency_ms", "reader_e2e_latency_ms", "reader_prompt_tokens", "idle_time_cost", "hit_rate", "fallback_rate"]))

    if args.details or len(samples) <= 20:
        print("\nPer-Sample Selected Memories")
        for row in summary:
            print(f"\n{row['method']}")
            for sample_row in row["samples"]:
                selected = ", ".join(sample_row["selected_memory_ids"]) or "(none)"
                print(f"  {sample_row['sample_id']}: {selected}")

    if args.json_log:
        write_json(args.json_log, summary)
        print(f"\nJSON log: {Path(args.json_log).resolve()}")
    if args.trace_log:
        write_trace_report(args.trace_log, summary)
        print(f"Trace log: {Path(args.trace_log).resolve()}")


def build_parser() -> argparse.ArgumentParser:
    code_root = Path(__file__).resolve().parent
    default_limit = int(os.getenv("LIMIT", "10"))
    default_eval_mode = os.getenv("LOCOMO_EVAL_MODE", "time-sliced")
    default_run_name = f"locomo_{default_eval_mode.replace('-', '_')}_limit{default_limit}"

    parser = argparse.ArgumentParser(
        description=(
            "Run LoCoMo evaluation. Default mode is time-sliced: each QA gets "
            "a separate history prefix ending at the latest gold evidence turn."
        )
    )
    parser.add_argument("--locomo-path", default=str(code_root / "data" / "locomo" / "locomo10.json"))
    parser.add_argument("--config", default=str(code_root / "configs" / "python_demo.json"))
    parser.add_argument("--limit", type=int, default=default_limit, help="Number of QA samples. Use 0 for all.")
    parser.add_argument("--eval-mode", choices=["time-sliced", "qa"], default=default_eval_mode)
    parser.add_argument("--sample-mode", choices=["head", "stratified"], default=os.getenv("SAMPLE_MODE", "head"))
    parser.add_argument("--sample-seed", type=int, default=int(os.getenv("SAMPLE_SEED", "7")))
    parser.add_argument("--download-locomo", dest="download_locomo", action="store_true", default=True)
    parser.add_argument("--no-download-locomo", dest="download_locomo", action="store_false")
    parser.add_argument("--predictor", choices=["vllm", "heuristic", "auto"], default=None)
    parser.add_argument("--cache-budget", type=int, default=None)
    parser.add_argument("--retrieval-top-k", type=int, default=None)
    parser.add_argument("--fallback-retriever", choices=["vector", "graph"], default=None)
    parser.add_argument("--vllm-url", default=os.getenv("VLLM_BASE_URL"))
    parser.add_argument("--vllm-host", default=os.getenv("VLLM_HOST", "127.0.0.1"))
    parser.add_argument("--vllm-port", type=int, default=int(os.getenv("VLLM_PORT", "30000")))
    parser.add_argument("--vllm-model", default=os.getenv("VLLM_MODEL", "../Qwen2.5-7B-Instruct"))
    parser.add_argument("--disable-reranker", action="store_true")
    parser.add_argument("--reranker-provider", choices=["flagembedding", "vllm"], default=os.getenv("RERANKER_PROVIDER"))
    parser.add_argument("--reranker-model", default=os.getenv("RERANKER_MODEL"))
    parser.add_argument("--reranker-url", default=os.getenv("RERANKER_BASE_URL"))
    parser.add_argument("--reranker-devices", default=os.getenv("RERANKER_DEVICES"))
    parser.add_argument("--reranker-cache-dir", default=os.getenv("RERANKER_CACHE_DIR"))
    parser.add_argument("--require-reranker", action="store_true")
    parser.add_argument("--answer-with-vllm", action="store_true")
    parser.add_argument("--details", action="store_true")
    parser.add_argument(
        "--json-log",
        default=str(code_root / "outputs" / f"{default_run_name}.json"),
        help="Path for full JSON summary log.",
    )
    parser.add_argument(
        "--trace-log",
        default=str(code_root / "outputs" / f"{default_run_name}.trace.md"),
        help="Path for human-readable per-sample trace markdown. Use an empty string to disable.",
    )
    return parser


def write_trace_report(path: str, summary: Sequence[Mapping[str, Any]]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = build_trace_report(summary)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_trace_report(summary: Sequence[Mapping[str, Any]]) -> List[str]:
    rows_by_method = {str(row.get("method")): row for row in summary}
    prequery_row = rows_by_method.get("Pre-query Prepared + Reader", {})
    fallback_row = rows_by_method.get("LLM-Predict + Fallback", {})
    cache_only_row = rows_by_method.get("LLM-Predict Cache Only", {})
    multi_intent_row = rows_by_method.get("Multi-Intent Prepared + Adaptive Router", {})
    primary_row = prequery_row or fallback_row
    cache_only_by_id = {
        str(sample.get("sample_id")): sample
        for sample in cache_only_row.get("samples", [])
    }
    fallback_by_id = {
        str(sample.get("sample_id")): sample
        for sample in fallback_row.get("samples", [])
    }
    multi_intent_by_id = {
        str(sample.get("sample_id")): sample
        for sample in multi_intent_row.get("samples", [])
    }

    lines: List[str] = [
        "# LoCoMo Trace Report",
        "",
        "This report shows ground truth, prediction, gap reasoning, verifier choice, and final selection.",
        "",
        "## Method Summary",
        "",
        "| method | selected | precision | recall | hit_rate | full_cover | query_retrieval_ms | fallback_rate |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary:
        lines.append(
            "| {method} | {selected_count:.2f} | {precision:.3f} | {recall:.3f} | {hit_rate:.3f} | {full_cover_rate:.3f} | {query_retrieval_latency_ms:.3f} | {fallback_rate:.3f} |".format(
                method=row.get("method", ""),
                selected_count=float(row.get("selected_count") or 0.0),
                precision=float(row.get("precision") or 0.0),
                recall=float(row.get("recall") or 0.0),
                hit_rate=float(row.get("hit_rate") or 0.0),
                full_cover_rate=float(row.get("full_cover_rate") or 0.0),
                query_retrieval_latency_ms=float(row.get("query_retrieval_latency_ms") or 0.0),
                fallback_rate=float(row.get("fallback_rate") or 0.0),
            )
        )

    for sample in primary_row.get("samples", []):
        trace = sample.get("trace") or {}
        if not trace:
            continue
        sample_id = str(sample.get("sample_id"))
        cache_only = cache_only_by_id.get(sample_id, {})
        fallback = fallback_by_id.get(sample_id, {})
        multi_intent = multi_intent_by_id.get(sample_id, {})
        multi_trace = multi_intent.get("multi_intent_trace") or {}
        cache_trace = cache_only.get("trace") or trace

        lines.extend(
            [
                "",
                "---",
                "",
                f"## {sample_id}",
                "",
                "### Selection Snapshot",
                "",
                "```text",
                f"Gold evidence: {join_values(trace_get(trace, 'ground_truth.gold_evidence_memory_ids'))}",
                f"Candidate pool: {join_values(trace_get(trace, 'gap_reasoning.candidate_memory_ids'))}",
                f"Prepared memories: {join_values(trace_get(trace, 'gap_reasoning.prepared_memory_ids'))}",
                f"Verifier selected: {join_values(trace_get(trace, 'verifier.selected_memory_ids'))}",
                f"Final selected: {join_values(trace_get(trace, 'final_selection.selected_memory_ids'))}",
                "```",
                "",
                "### Question / Ground Truth",
                "",
                f"- Question: {trace_get(trace, 'sample.question')}",
                f"- Gold answer: {trace_get(trace, 'ground_truth.gold_answer')}",
                f"- Gold evidence turn ids: {join_values(trace_get(trace, 'ground_truth.gold_evidence_turn_ids'))}",
                f"- Gold evidence memory ids: {join_values(trace_get(trace, 'ground_truth.gold_evidence_memory_ids'))}",
                f"- History turns: {trace_get(trace, 'sample.history_turn_count')}",
                "",
                "Gold evidence memories:",
                *format_memory_list(trace_get(trace, "ground_truth.gold_evidence_memories")),
                "",
                "### Prediction / Prepared Cache",
                "",
                f"- Predicted future intents: {join_values(trace_get(trace, 'prediction.predicted_future_intents'))}",
                f"- Prediction provider metadata: `{trace_get(trace, 'prediction.metadata')}`",
                "",
                "Activated memories from predictor:",
                *format_memory_list(trace_get(trace, "prediction.activated_memories")),
                "",
                "Cache memories after insertion:",
                *format_memory_list(trace_get(trace, "cache.cache_memories")),
                "",
                "### Gap Reasoning",
                "",
                f"- Context package: {trace_get(trace, 'gap_reasoning.context_package_id')}",
                f"- Target intent: {trace_get(trace, 'gap_reasoning.target_intent')}",
                f"- Possible user query: {trace_get(trace, 'gap_reasoning.possible_user_query')}",
                f"- Support check: `{trace_get(trace, 'gap_reasoning.support_check')}`",
                "",
                "Selected meta paths:",
                *format_dict_list(trace_get(trace, "gap_reasoning.selected_paths"), keys=["path_id", "reason"]),
                "",
                "Executed paths:",
                *format_dict_list(trace_get(trace, "gap_reasoning.executed_paths"), keys=["path_id", "selected_memory_ids", "node_count", "edge_count"]),
                "",
                "Gaps:",
                *format_dict_list(trace_get(trace, "gap_reasoning.gaps"), keys=["gap_id", "gap_type", "missing_support", "priority", "repair_query"]),
                "",
                "Repair evidence:",
                *format_dict_list(trace_get(trace, "gap_reasoning.repair_evidence"), keys=["evidence_id", "candidate_gap_id", "source_id", "chunk_id", "score", "content"]),
                "",
                "Evidence bindings:",
                *format_dict_list(trace_get(trace, "gap_reasoning.bindings"), keys=["evidence_id", "bind_to", "binding_type", "reason"]),
                "",
                "Pre-query compression:",
                f"- Method: {trace_get(trace, 'gap_reasoning.compression.method')}",
                f"- Uses actual query: {trace_get(trace, 'gap_reasoning.compression.uses_actual_query')}",
                f"- Budget: {trace_get(trace, 'gap_reasoning.compression.budget')}",
                f"- Candidate count: {trace_get(trace, 'gap_reasoning.compression.candidate_count')}",
                f"- Final count: {trace_get(trace, 'gap_reasoning.compression.final_count')}",
                "",
                "Candidate memory pool before compression:",
                *format_memory_list(trace_get(trace, "gap_reasoning.candidate_memories")),
                "",
                "Compression scores:",
                *format_dict_list(trace_get(trace, "gap_reasoning.compression.scores"), keys=["rank", "id", "selected", "score", "intent_score", "prediction_score", "repair_score", "path_score", "summary"]),
                "",
                "Prepared memories:",
                *format_memory_list(trace_get(trace, "gap_reasoning.prepared_memories")),
                "",
                "### Verifier",
                "",
                f"- Decision: {trace_get(trace, 'verifier.decision')}",
                f"- Provider: {trace_get(trace, 'verifier.provider')}",
                f"- Confidence: {trace_get(trace, 'verifier.confidence')}",
                f"- Reason: {trace_get(trace, 'verifier.reason')}",
                f"- Verifier selected ids: {join_values(trace_get(trace, 'verifier.selected_memory_ids'))}",
                "",
                "Verifier memory candidates:",
                *format_dict_list(trace_get(trace, "verifier.memory_candidates"), keys=["id", "source_turn_id", "path_id", "summary"]),
                "",
                "Verifier scores:",
                *format_dict_list(trace_get(trace, "verifier.scores"), keys=["rank", "id", "score", "summary"]),
                "",
                "Verifier selected memories:",
                *format_memory_list(trace_get(trace, "verifier.selected_memories")),
                "",
                "### Final Selection",
                "",
                f"- Cache-only selected ids: {join_values(cache_only.get('selected_memory_ids', []))}",
                f"- Cache-only metrics: precision={float(cache_only.get('precision') or 0.0):.3f}, recall={float(cache_only.get('recall') or 0.0):.3f}, hit={float(cache_only.get('hit_rate') or 0.0):.3f}, full_cover={float(cache_only.get('full_cover_rate') or 0.0):.3f}",
                f"- Pre-query prepared selected ids: {join_values(sample.get('selected_memory_ids', []))}",
                f"- Pre-query prepared metrics: precision={float(sample.get('precision') or 0.0):.3f}, recall={float(sample.get('recall') or 0.0):.3f}, hit={float(sample.get('hit_rate') or 0.0):.3f}, full_cover={float(sample.get('full_cover_rate') or 0.0):.3f}",
                f"- Pre-query query-time retrieval latency ms: {float(sample.get('query_retrieval_latency_ms') or 0.0):.3f}",
                f"- Pre-query reader answer: {sample.get('generated_answer')}",
                f"- Pre-query reader official_f1={float(sample.get('official_f1') or 0.0):.3f}, bleu1={float(sample.get('bleu1') or 0.0):.3f}, rouge_l={float(sample.get('rouge_l') or 0.0):.3f}",
                f"- Cache+fallback selected ids: {join_values(fallback.get('selected_memory_ids', []))}",
                f"- Cache+fallback fallback used: {bool(fallback.get('fallback_rate'))}",
                f"- Cache+fallback metrics: precision={float(fallback.get('precision') or 0.0):.3f}, recall={float(fallback.get('recall') or 0.0):.3f}, hit={float(fallback.get('hit_rate') or 0.0):.3f}, full_cover={float(fallback.get('full_cover_rate') or 0.0):.3f}",
                f"- Proactive metrics before verifier: precision={float(sample.get('proactive_precision') or 0.0):.3f}, recall={float(sample.get('proactive_recall') or 0.0):.3f}, hit={float(sample.get('proactive_hit_rate') or 0.0):.3f}, full_cover={float(sample.get('proactive_full_cover_rate') or 0.0):.3f}",
                "",
                "Final selected memories:",
                *format_memory_list(trace_get(trace, "final_selection.selected_memories")),
                "",
                "### 多意图缓存规划与路由",
                "",
                f"- 实际 Query: {trace_get(multi_trace, 'sample.actual_query')}",
                f"- Golden Answer: {trace_get(multi_trace, 'sample.gold_answer')}",
                f"- Golden Memory: {join_values(trace_get(multi_trace, 'sample.gold_evidence_memory_ids'))}",
                f"- 全局物理缓存预算: {trace_get(multi_trace, 'idle_time_planning.global_cache_budget')}",
                f"- 实际预取 Memory: {join_values(trace_get(multi_trace, 'idle_time_planning.physical_memory_ids'))}",
                f"- 多分支共享 Memory: {join_values(trace_get(multi_trace, 'idle_time_planning.shared_memory_ids'))}",
                f"- 多分支共享 Fact: {join_values(trace_get(multi_trace, 'idle_time_planning.shared_fact_ids'))}",
                "",
                "Intent 分支（语义内容、候选事实、图寻路）:",
                *format_multi_intent_heads(
                    trace_get(multi_trace, "idle_time_planning.intent_heads")
                ),
                "",
                "联合预算 / 增量 Prefetch 顺序:",
                *format_dict_list(
                    trace_get(multi_trace, "idle_time_planning.prefetch_plan"),
                    keys=[
                        "prefetch_order",
                        "memory_id",
                        "priority",
                        "branch_ids",
                        "fact_ids",
                        "physical_cache_occupancy",
                    ],
                ),
                "",
                "Query-time cosine + coverage gate:",
                f"- 路由决策: {trace_get(multi_trace, 'query_time_routing.decision')}",
                f"- 决策原因: {trace_get(multi_trace, 'query_time_routing.reason')}",
                f"- 选中 Intent Head: {join_values(trace_get(multi_trace, 'query_time_routing.selected_head_ids'))}",
                *format_dict_list(
                    trace_get(multi_trace, "query_time_routing.head_scores"),
                    keys=[
                        "head_id",
                        "raw_intent",
                        "intent_similarity",
                        "prepared_readiness",
                        "semantic_support",
                        "route_score",
                        "resident_memory_ids",
                    ],
                ),
                "",
                "最终回答上下文:",
                f"- Prepared Memory: {join_values(trace_get(multi_trace, 'final_selection.prepared_memory_ids'))}",
                f"- Reactive 补全 Memory: {join_values(trace_get(multi_trace, 'final_selection.reactive_repair_memory_ids'))}",
                f"- Final Memory: {join_values(trace_get(multi_trace, 'final_selection.final_memory_ids'))}",
                *format_memory_list(
                    trace_get(multi_trace, "final_selection.final_memories")
                ),
            ]
        )
    return lines


def trace_get(data: Mapping[str, Any], dotted: str) -> Any:
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def join_values(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def format_memory_list(value: Any) -> List[str]:
    if not value:
        return ["- (none)"]
    lines = []
    for item in value:
        if not isinstance(item, Mapping):
            lines.append(f"- {item}")
            continue
        lines.append(
            "- {id} turn={turn} time={time} :: {summary}".format(
                id=item.get("id", ""),
                turn=item.get("source_turn_id", ""),
                time=item.get("timestamp", ""),
                summary=item.get("summary") or item.get("content") or "",
            )
        )
    return lines


def format_dict_list(value: Any, keys: Sequence[str]) -> List[str]:
    if not value:
        return ["- (none)"]
    lines = []
    for item in value:
        if not isinstance(item, Mapping):
            lines.append(f"- {item}")
            continue
        parts = []
        for key in keys:
            if key not in item:
                continue
            parts.append(f"{key}={item.get(key)}")
        lines.append("- " + "; ".join(parts))
    return lines


def format_multi_intent_heads(value: Any) -> List[str]:
    if not value:
        return ["- (none)"]
    lines: List[str] = []
    for head in value:
        if not isinstance(head, Mapping):
            lines.append(f"- {head}")
            continue
        structured = head.get("structured") or {}
        lines.append(
            "- {head_id}: intent={intent}; relation={relation}; answer_type={answer_type}; "
            "confidence={confidence}; readiness={readiness}; resident={resident}".format(
                head_id=head.get("id", ""),
                intent=head.get("raw_intent", ""),
                relation=structured.get("relation", ""),
                answer_type=structured.get("answer_type", ""),
                confidence=head.get("confidence", ""),
                readiness=head.get("readiness", ""),
                resident=head.get("resident_memory_ids", []),
            )
        )
        for candidate in head.get("candidates") or []:
            if not isinstance(candidate, Mapping):
                continue
            lines.append(
                "  - 候选 {memory_id}: score={score}; fact={fact_id}; {summary}".format(
                    memory_id=candidate.get("memory_id", ""),
                    score=candidate.get("score", ""),
                    fact_id=candidate.get("fact_id", ""),
                    summary=candidate.get("summary", ""),
                )
            )
        for step in head.get("traversal") or []:
            if not isinstance(step, Mapping):
                continue
            lines.append(
                "  - 寻路 {path}: {seed} -> {target} (via={via})".format(
                    path=step.get("path", ""),
                    seed=step.get("seed_memory_id", ""),
                    target=step.get("target_memory_id", ""),
                    via=step.get("via_node_id") or "-",
                )
            )
    return lines


def load_config(path: str) -> Dict[str, Any]:
    config = load_json(path)
    return normalize_config(config)


def normalize_config(config: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(config)
    aliases = {
        "cacheBudget": "cache_budget",
        "retrievalTopK": "retrieval_top_k",
        "fallbackRetriever": "fallback_retriever",
        "verifierThreshold": "verifier_threshold",
        "similarityThreshold": "similarity_threshold",
        "randomSeed": "random_seed",
    }
    for old_key, new_key in aliases.items():
        if old_key in result and new_key not in result:
            result[new_key] = result[old_key]
    result.setdefault("cache_budget", 5)
    result.setdefault("retrieval_top_k", 5)
    result.setdefault("fallback_retriever", "vector")
    result.setdefault("verifier_threshold", 0.12)
    result.setdefault("similarity_threshold", 0.28)
    result.setdefault("random_seed", 7)
    result.setdefault("predictor", "vllm")
    result.setdefault("predictor_candidate_limit", 80)
    result.setdefault("answer_with_vllm", False)
    result.setdefault("llm", {})
    llm_config = dict(result["llm"])
    llm_config.setdefault("base_url", os.getenv("VLLM_BASE_URL", "http://127.0.0.1:30000/v1"))
    llm_config.setdefault("model", os.getenv("VLLM_MODEL", "Qwen/Qwen2.5-7B-Instruct"))
    llm_config.setdefault("timeout", float(os.getenv("VLLM_TIMEOUT", "30")))
    llm_config.setdefault("fallback_to_heuristic", True)
    result["llm"] = llm_config
    result.setdefault("qa_reader", {})
    qa_reader_config = dict(result["qa_reader"])
    qa_reader_config.setdefault("enabled", True)
    qa_reader_config.setdefault(
        "use_for_all_methods",
        os.getenv("QA_READER_USE_FOR_ALL_METHODS", "1").lower() not in {"0", "false", "no"},
    )
    qa_reader_config.setdefault("max_memories", int(os.getenv("QA_READER_MAX_MEMORIES", "12")))
    qa_reader_config.setdefault("temperature", float(os.getenv("QA_READER_TEMPERATURE", "0")))
    qa_reader_config.setdefault("max_tokens", int(os.getenv("QA_READER_MAX_TOKENS", "128")))
    qa_reader_config.setdefault("fallback_to_heuristic", True)
    result["qa_reader"] = qa_reader_config
    result.setdefault("reranker", {})
    reranker_config = dict(result["reranker"])
    env_enabled = os.getenv("RERANKER_ENABLED")
    if env_enabled is not None:
        reranker_config["enabled"] = env_enabled.lower() not in {"0", "false", "no"}
    env_require = os.getenv("RERANKER_REQUIRE_AVAILABLE")
    if env_require is not None:
        reranker_config["require_available"] = env_require.lower() not in {"0", "false", "no"}
    reranker_config.setdefault("enabled", True)
    reranker_config.setdefault("require_available", False)
    reranker_config.setdefault("provider", os.getenv("RERANKER_PROVIDER", "flagembedding"))
    reranker_config.setdefault("model", os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"))
    reranker_config.setdefault("base_url", os.getenv("RERANKER_BASE_URL", "http://127.0.0.1:30001"))
    reranker_config.setdefault("top_k", int(os.getenv("RERANKER_TOP_K", "3")))
    reranker_config.setdefault(
        "dynamic_top_k",
        os.getenv("RERANKER_DYNAMIC_TOP_K", "0").lower() not in {"0", "false", "no"},
    )
    reranker_config.setdefault("normalize", True)
    reranker_config.setdefault("devices", os.getenv("RERANKER_DEVICES", "cuda:1"))
    reranker_config.setdefault("use_fp16", os.getenv("RERANKER_USE_FP16", "1").lower() not in {"0", "false", "no"})
    reranker_config.setdefault("cache_dir", os.getenv("RERANKER_CACHE_DIR", "/home/yanghaotong/models/cache"))
    reranker_config.setdefault("batch_size", int(os.getenv("RERANKER_BATCH_SIZE", "16")))
    reranker_config.setdefault("max_length", int(os.getenv("RERANKER_MAX_LENGTH", "512")))
    reranker_config.setdefault("prefer_for_verifier", True)
    reranker_config.setdefault("select_best_if_empty", True)
    reranker_config.setdefault("hybrid", True)
    reranker_config.setdefault("bge_weight", 0.62)
    reranker_config.setdefault("lexical_weight", 0.18)
    reranker_config.setdefault("recency_weight", 0.06)
    reranker_config.setdefault("repair_weight", 0.08)
    reranker_config.setdefault("path_weight", 0.04)
    reranker_config.setdefault("prepared_order_weight", 0.10)
    reranker_config.setdefault("generic_penalty_weight", 0.10)
    result["reranker"] = reranker_config
    result.setdefault(
        "gap_reasoning",
        {
            "enabled": True,
            "use_llm": True,
            "use_llm_verifier": True,
            "max_paths": 4,
            "per_path_top_k": 4,
            "max_gaps": 4,
            "repair_top_k": 3,
            "working_context_budget": 12,
        },
    )
    return result


def apply_overrides(config: Dict[str, Any], args: argparse.Namespace) -> None:
    if args.predictor is not None:
        config["predictor"] = args.predictor
    if args.cache_budget is not None:
        config["cache_budget"] = args.cache_budget
    if args.retrieval_top_k is not None:
        config["retrieval_top_k"] = args.retrieval_top_k
    if args.fallback_retriever is not None:
        config["fallback_retriever"] = args.fallback_retriever
    if args.answer_with_vllm:
        config["answer_with_vllm"] = True
    llm_config = dict(config.get("llm") or {})
    if args.vllm_url is not None:
        llm_config["base_url"] = args.vllm_url
    elif args.vllm_host is not None or args.vllm_port is not None:
        host = args.vllm_host or "127.0.0.1"
        port = args.vllm_port or 30000
        llm_config["base_url"] = f"http://{host}:{port}/v1"
    if args.vllm_model is not None:
        llm_config["model"] = args.vllm_model
    config["llm"] = llm_config
    evaluation_config = dict(config.get("evaluation") or {})
    judge_config = dict(evaluation_config.get("judge") or {})
    if bool(judge_config.get("use_same_vllm", False)):
        judge_config["base_url"] = llm_config["base_url"]
        judge_config["model"] = llm_config["model"]
    evaluation_config["judge"] = judge_config
    config["evaluation"] = evaluation_config
    reranker_config = dict(config.get("reranker") or {})
    if args.disable_reranker:
        reranker_config["enabled"] = False
    if args.reranker_provider is not None:
        reranker_config["provider"] = args.reranker_provider
    if args.reranker_model is not None:
        reranker_config["model"] = args.reranker_model
    if args.reranker_url is not None:
        reranker_config["base_url"] = args.reranker_url
    if args.reranker_devices is not None:
        reranker_config["devices"] = args.reranker_devices
    if args.reranker_cache_dir is not None:
        reranker_config["cache_dir"] = args.reranker_cache_dir
    if args.require_reranker:
        reranker_config["require_available"] = True
    config["reranker"] = reranker_config


def find_data_root(project_root: Path) -> Path:
    direct = project_root / "data"
    if (direct / "locomo" / "locomo10.json").exists():
        return direct
    return direct


if __name__ == "__main__":
    main()
