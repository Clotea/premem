from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .gap_reasoning import META_PATH_LIBRARY, build_prepared_context
from .graph_store import (
    EDGE_BELONGS_TO,
    EDGE_DERIVED_FROM,
    EDGE_MENTIONS,
    EDGE_SIMILAR_TO,
    EDGE_TEMPORAL_NEXT,
    NODE_ENTITY,
    NODE_MEMORY,
    NODE_SEGMENT,
    NODE_TURN,
    GraphStore,
    build_memory_graph,
)
from .locomo import load_config, load_locomo_samples
from .pipeline import label_gold_evidence, memory_writer
from .predictors import create_predictor
from .utils import MemoryNode, Sample, compute_activation_metrics, truncate, write_json
from .vllm_client import VLLMClient


class TracingVLLMClient:
    """Transparent vLLM client wrapper that records each planning call."""

    def __init__(self, client: VLLMClient) -> None:
        self.client = client
        self.base_url = client.base_url
        self.model = client.model
        self.stage = "unlabeled"
        self.calls: List[Dict[str, Any]] = []

    def set_trace_stage(self, stage: str) -> None:
        self.stage = stage

    def chat(
        self,
        messages: List[Mapping[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 512,
        response_format: Optional[Mapping[str, Any]] = None,
    ) -> tuple[str, Dict[str, Any]]:
        started = perf_counter()
        content, usage = self.client.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )
        self.calls.append(
            {
                "index": len(self.calls) + 1,
                "stage": self.stage,
                "elapsed_ms": round((perf_counter() - started) * 1000.0, 3),
                "message_count": len(messages),
                "prompt_characters": sum(len(str(item.get("content") or "")) for item in messages),
                "max_tokens": max_tokens,
                "usage": dict(usage),
                "response_preview": truncate(content, 360),
            }
        )
        return content, usage


def run_planning_trace(args: argparse.Namespace) -> Dict[str, Any]:
    config = load_config(args.config)
    config["cache_budget"] = args.cache_budget
    config["show_progress"] = False
    gap_config = dict(config.get("gap_reasoning") or {})
    gap_config["enabled"] = True
    gap_config["prepared_cache_budget"] = args.cache_budget
    gap_config["trace_ranking_limit"] = args.trace_ranking_limit
    if args.no_llm:
        gap_config["use_llm"] = False
        config["predictor"] = "heuristic"
    config["gap_reasoning"] = gap_config

    samples = load_locomo_samples(args.locomo_path, limit=0, eval_mode=args.eval_mode)
    sample = _select_sample(samples, args.sample_id, args.sample_index)

    raw_client = VLLMClient(
        base_url=args.vllm_url,
        model=args.vllm_model,
        timeout=args.vllm_timeout,
    )
    client = TracingVLLMClient(raw_client)
    predictor = create_predictor(config, None if args.no_llm else client)  # type: ignore[arg-type]

    stage_timings: List[Dict[str, Any]] = []

    started = perf_counter()
    memory_nodes = memory_writer(sample.history)
    stage_timings.append(_timing("memory_writer", "Write memories from history", started))

    started = perf_counter()
    graph = build_memory_graph(
        memory_nodes,
        sample.history,
        similarity_threshold=float(config.get("similarity_threshold") or 0.28),
    )
    stage_timings.append(_timing("graph_build", "Build heterogeneous memory graph", started))

    client.set_trace_stage("future_intent_prediction")
    started = perf_counter()
    prediction = predictor.predict(sample.history, memory_nodes, graph, args.cache_budget)
    stage_timings.append(_timing("future_intent_prediction", "Predict future memory need", started))

    planning_stages: List[Dict[str, Any]] = []
    started = perf_counter()
    prepared = build_prepared_context(
        context_key=sample.history_cache_key or sample.id,
        history=sample.history,
        memory_nodes=memory_nodes,
        graph=graph,
        prediction=prediction,
        llm_client=None if args.no_llm else client,  # type: ignore[arg-type]
        config=config,
        planning_trace=planning_stages,
    )
    stage_timings.append(_timing("prepared_context", "Run complete pre-query planner", started))

    gold_memory_ids = label_gold_evidence(
        sample.evidence_terms,
        sample.gold_evidence_turn_ids,
        memory_nodes,
    )
    prepared_ids = list(prepared.get("memory_ids") or [])
    activation = compute_activation_metrics(prepared_ids, gold_memory_ids)

    node_flags = _node_flags(
        memory_nodes=memory_nodes,
        prediction_ids=[item.id for item in prediction.activated_memory_ids],
        candidate_ids=list(prepared.get("candidate_memory_ids") or []),
        prepared_ids=prepared_ids,
        gold_ids=gold_memory_ids,
    )
    graph_payload = _serialize_graph(graph, node_flags)
    graph_payload["evolution"] = _graph_evolution(graph)

    total_planning_ms = sum(
        float(item.get("elapsed_ms") or 0.0)
        for item in stage_timings
        if item["stage_id"] in {"future_intent_prediction", "prepared_context"}
    )
    report: Dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sample": {
            "id": sample.id,
            "history_cache_key": sample.history_cache_key,
            "question": sample.question,
            "answer": sample.answer,
            "metadata": sample.metadata,
            "history_turn_count": len(sample.history),
            "history_tail": [
                {
                    "id": turn.id,
                    "speaker": turn.speaker,
                    "text": truncate(turn.text, 220),
                    "segment_id": turn.segment_id,
                    "timestamp": turn.timestamp,
                }
                for turn in sample.history[-6:]
            ],
            "gold_evidence_turn_ids": list(sample.gold_evidence_turn_ids),
            "gold_evidence_memory_ids": gold_memory_ids,
        },
        "runtime": {
            "predictor": getattr(predictor, "name", str(config.get("predictor"))),
            "vllm_url": args.vllm_url,
            "vllm_model": args.vllm_model,
            "cache_budget": args.cache_budget,
            "similarity_threshold": float(config.get("similarity_threshold") or 0.28),
            "llm_enabled": not args.no_llm,
            "total_planning_ms": round(total_planning_ms, 3),
        },
        "stage_timings": stage_timings,
        "llm_calls": client.calls,
        "prediction": {
            "predicted_future_intents": list(prediction.predicted_future_intents),
            "activated_memory_ids": [item.to_dict() for item in prediction.activated_memory_ids],
            "metadata": dict(prediction.metadata),
        },
        "graph": graph_payload,
        "planning_stages": planning_stages,
        "prepared_context": prepared,
        "evaluation_overlay": {
            "uses_actual_query_during_planning": False,
            "uses_gold_evidence_during_planning": False,
            "prepared_memory_ids": prepared_ids,
            "gold_memory_ids": gold_memory_ids,
            "activation_metrics": activation,
        },
        "metapath_semantics": {
            "conceptual_library": META_PATH_LIBRARY,
            "physical_graph_node_types": [
                NODE_TURN,
                NODE_SEGMENT,
                NODE_MEMORY,
                NODE_ENTITY,
            ],
            "physical_graph_edge_types": [
                EDGE_BELONGS_TO,
                EDGE_DERIVED_FROM,
                EDGE_MENTIONS,
                EDGE_TEMPORAL_NEXT,
                EDGE_SIMILAR_TO,
            ],
            "ontology_aligned": False,
            "current_execution_mode": "path_conditioned_global_ranking_then_local_subgraph",
            "finding": (
                "The selected P1-P6 path changes the global memory ranking bonus. "
                "Only after top-k selection does the code use physical graph edges "
                "to expand an incident-edge local subgraph; it does not traverse the "
                "conceptual Intent/UserGoal/Paper/Claim/Evidence ontology hop by hop."
            ),
        },
    }
    return report


def _timing(stage_id: str, label: str, started: float) -> Dict[str, Any]:
    return {
        "stage_id": stage_id,
        "label": label,
        "elapsed_ms": round((perf_counter() - started) * 1000.0, 3),
    }


def _select_sample(
    samples: Sequence[Sample],
    sample_id: str,
    sample_index: int,
) -> Sample:
    if sample_id:
        for sample in samples:
            if sample.id == sample_id:
                return sample
        raise ValueError(f"Unknown sample id: {sample_id}")
    if sample_index < 1 or sample_index > len(samples):
        raise ValueError(f"sample-index must be between 1 and {len(samples)}")
    return samples[sample_index - 1]


def _node_flags(
    memory_nodes: Sequence[MemoryNode],
    prediction_ids: Sequence[str],
    candidate_ids: Sequence[str],
    prepared_ids: Sequence[str],
    gold_ids: Sequence[str],
) -> Dict[str, Dict[str, bool]]:
    known_ids = {memory.id for memory in memory_nodes}
    flag_sets = {
        "predicted": set(prediction_ids),
        "candidate": set(candidate_ids),
        "prepared": set(prepared_ids),
        "gold": set(gold_ids),
    }
    return {
        memory_id: {
            flag: memory_id in values
            for flag, values in flag_sets.items()
        }
        for memory_id in known_ids
    }


def _serialize_graph(
    graph: GraphStore,
    node_flags: Mapping[str, Mapping[str, bool]],
) -> Dict[str, Any]:
    nodes: List[Dict[str, Any]] = []
    for node_id, node in graph.nodes.items():
        text = (
            node.get("summary")
            or node.get("content")
            or node.get("text")
            or node.get("name")
            or node_id
        )
        nodes.append(
            {
                "id": node_id,
                "node_type": node.get("node_type"),
                "label": truncate(text, 74),
                "summary": truncate(text, 260),
                "timestamp": node.get("timestamp"),
                "segment_id": node.get("segment_id"),
                "flags": dict(node_flags.get(node_id) or {}),
            }
        )
    edges = [
        {
            "index": index,
            "source": edge.source,
            "target": edge.target,
            "type": edge.edge_type,
        }
        for index, edge in enumerate(graph.edges)
    ]
    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
        "node_type_counts": dict(Counter(str(node["node_type"]) for node in nodes)),
        "edge_type_counts": dict(Counter(str(edge["type"]) for edge in edges)),
    }


def _graph_evolution(graph: GraphStore) -> List[Dict[str, Any]]:
    node_type_by_id = {
        node_id: str(node.get("node_type") or "")
        for node_id, node in graph.nodes.items()
    }
    stages = [
        {
            "stage_id": "history_structure",
            "label": "1. History structure",
            "node_types": {NODE_TURN, NODE_SEGMENT},
            "edge_filter": lambda edge: (
                edge.edge_type == EDGE_BELONGS_TO
                and node_type_by_id.get(edge.source) == NODE_TURN
            ),
        },
        {
            "stage_id": "memory_attachment",
            "label": "2. Memory attachment",
            "node_types": {NODE_TURN, NODE_SEGMENT, NODE_MEMORY},
            "edge_filter": lambda edge: edge.edge_type in {EDGE_BELONGS_TO, EDGE_DERIVED_FROM}
            and node_type_by_id.get(edge.source) in {NODE_TURN, NODE_MEMORY},
        },
        {
            "stage_id": "entity_linking",
            "label": "3. Entity linking",
            "node_types": {NODE_TURN, NODE_SEGMENT, NODE_MEMORY, NODE_ENTITY},
            "edge_filter": lambda edge: edge.edge_type
            in {EDGE_BELONGS_TO, EDGE_DERIVED_FROM, EDGE_MENTIONS},
        },
        {
            "stage_id": "temporal_chain",
            "label": "4. Temporal chain",
            "node_types": {NODE_TURN, NODE_SEGMENT, NODE_MEMORY, NODE_ENTITY},
            "edge_filter": lambda edge: edge.edge_type
            in {
                EDGE_BELONGS_TO,
                EDGE_DERIVED_FROM,
                EDGE_MENTIONS,
                EDGE_TEMPORAL_NEXT,
            },
        },
        {
            "stage_id": "similarity_links",
            "label": "5. Similarity links",
            "node_types": {NODE_TURN, NODE_SEGMENT, NODE_MEMORY, NODE_ENTITY},
            "edge_filter": lambda edge: True,
        },
    ]
    result: List[Dict[str, Any]] = []
    previous_nodes: set[str] = set()
    previous_edges: set[int] = set()
    for stage in stages:
        node_ids = {
            node_id
            for node_id, node_type in node_type_by_id.items()
            if node_type in stage["node_types"]
        }
        edge_indices = {
            index
            for index, edge in enumerate(graph.edges)
            if stage["edge_filter"](edge)
            and edge.source in node_ids
            and edge.target in node_ids
        }
        node_counts = Counter(node_type_by_id[node_id] for node_id in node_ids)
        edge_counts = Counter(graph.edges[index].edge_type for index in edge_indices)
        result.append(
            {
                "stage_id": stage["stage_id"],
                "label": stage["label"],
                "node_ids": sorted(node_ids),
                "edge_indices": sorted(edge_indices),
                "added_node_ids": sorted(node_ids - previous_nodes),
                "added_edge_indices": sorted(edge_indices - previous_edges),
                "node_count": len(node_ids),
                "edge_count": len(edge_indices),
                "node_type_counts": dict(node_counts),
                "edge_type_counts": dict(edge_counts),
            }
        )
        previous_nodes = node_ids
        previous_edges = edge_indices
    return result


def write_report(report: Mapping[str, Any], output_dir: Path, template_path: Path) -> Dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "planning_trace.json"
    html_path = output_dir / "planning_trace.html"
    summary_path = output_dir / "planning_trace.md"
    write_json(json_path, report)

    template = template_path.read_text(encoding="utf-8")
    payload = json.dumps(report, ensure_ascii=False).replace("</", "<\\/")
    html_path.write_text(
        template.replace("__PLANNING_TRACE_DATA__", payload),
        encoding="utf-8",
    )
    summary_path.write_text(_markdown_summary(report), encoding="utf-8")
    return {"json": json_path, "html": html_path, "summary": summary_path}


def _markdown_summary(report: Mapping[str, Any]) -> str:
    sample = report["sample"]
    runtime = report["runtime"]
    overlay = report["evaluation_overlay"]
    metrics = overlay["activation_metrics"]
    prepared = report["prepared_context"]
    lines = [
        "# Planning trace",
        "",
        f"- Sample: `{sample['id']}`",
        f"- History turns: {sample['history_turn_count']}",
        f"- Graph: {report['graph']['node_count']} nodes / {report['graph']['edge_count']} edges",
        f"- Selected conceptual paths: {', '.join(item['path_id'] for item in prepared['selected_paths'])}",
        f"- Prepared cache: {', '.join(overlay['prepared_memory_ids']) or '(empty)'}",
        f"- Gold memories: {', '.join(overlay['gold_memory_ids']) or '(empty)'}",
        f"- Prepared recall: {float(metrics['recall']):.3f}",
        f"- Full cover: {float(metrics['full_cover_rate']):.3f}",
        f"- Total prediction + planning latency: {float(runtime['total_planning_ms']):.3f} ms",
        "",
        "## Important implementation finding",
        "",
        str(report["metapath_semantics"]["finding"]),
        "",
        "## Planning stages",
        "",
        "| Stage | Latency (ms) |",
        "|---|---:|",
    ]
    lines.extend(
        f"| {stage['label']} | {float(stage['elapsed_ms']):.3f} |"
        for stage in report["planning_stages"]
    )
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    project_code_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Trace one LoCoMo pre-query planning run and emit JSON + interactive HTML."
    )
    parser.add_argument(
        "--locomo-path",
        default=str(project_code_dir / "preact_demo" / "data" / "locomo" / "locomo10.json"),
    )
    parser.add_argument(
        "--config",
        default=str(Path(__file__).resolve().parent / "configs" / "python_demo.json"),
    )
    parser.add_argument("--eval-mode", choices=["time-sliced", "qa"], default="time-sliced")
    parser.add_argument("--sample-id", default="locomo_c01_tsqa_016")
    parser.add_argument("--sample-index", type=int, default=16)
    parser.add_argument("--cache-budget", type=int, default=5)
    parser.add_argument("--trace-ranking-limit", type=int, default=20)
    parser.add_argument("--vllm-url", default="http://127.0.0.1:30000/v1")
    parser.add_argument("--vllm-model", default="../Qwen2.5-7B-Instruct")
    parser.add_argument("--vllm-timeout", type=float, default=90.0)
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument(
        "--output-dir",
        default=str(project_code_dir / "outputs" / "planning_trace"),
    )
    parser.add_argument(
        "--template",
        default=str(Path(__file__).resolve().parent / "templates" / "planning_trace.html"),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = run_planning_trace(args)
    paths = write_report(report, Path(args.output_dir), Path(args.template))
    metrics = report["evaluation_overlay"]["activation_metrics"]
    print("PreMem planning trace complete")
    print(f"  sample:        {report['sample']['id']}")
    print(f"  graph:         {report['graph']['node_count']} nodes / {report['graph']['edge_count']} edges")
    print(f"  meta-paths:    {', '.join(item['path_id'] for item in report['prepared_context']['selected_paths'])}")
    print(f"  prepared IDs:  {', '.join(report['evaluation_overlay']['prepared_memory_ids']) or '-'}")
    print(f"  gold IDs:      {', '.join(report['evaluation_overlay']['gold_memory_ids']) or '-'}")
    print(f"  recall:        {float(metrics['recall']):.3f}")
    print(f"  planning:      {float(report['runtime']['total_planning_ms']):.3f} ms")
    for label, path in paths.items():
        print(f"  {label}: {path.resolve()}")


if __name__ == "__main__":
    main()
