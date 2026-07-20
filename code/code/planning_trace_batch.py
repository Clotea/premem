from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from .localize_planning_trace import collect_semantic_strings, translate_strings
from .planning_trace import write_report
from .utils import unique, write_json
from .vllm_client import VLLMClient


def build_sample_diagnosis(report: Mapping[str, Any]) -> Dict[str, Any]:
    graph_nodes = {
        str(node.get("id") or ""): node
        for node in (report.get("graph") or {}).get("nodes") or []
    }
    prediction = report.get("prediction") or {}
    prepared = report.get("prepared_context") or {}
    overlay = report.get("evaluation_overlay") or {}

    predictor_ids = {
        str(item.get("id") or "")
        for item in prediction.get("activated_memory_ids") or []
    }
    path_ids = {
        str(memory_id)
        for path in prepared.get("executed_paths") or []
        for memory_id in path.get("selected_memory_ids") or []
    }
    repair_ids = {
        str(item.get("source_id") or "")
        for item in prepared.get("evidence") or []
        if item.get("source_type") == "memory"
    }
    candidate_ids = {str(item) for item in prepared.get("candidate_memory_ids") or []}
    final_ids = {str(item) for item in prepared.get("memory_ids") or []}
    gold_ids = [str(item) for item in overlay.get("gold_memory_ids") or []]
    gold_set = set(gold_ids)

    def memory_summary(memory_id: str) -> str:
        return str((graph_nodes.get(memory_id) or {}).get("summary") or memory_id)

    gold_rows = [
        {
            "summary": memory_summary(memory_id),
            "predictor": memory_id in predictor_ids,
            "path": memory_id in path_ids,
            "repair": memory_id in repair_ids,
            "candidate": memory_id in candidate_ids,
            "final": memory_id in final_ids,
        }
        for memory_id in gold_ids
    ]

    predictor_items = [
        {
            "summary": memory_summary(str(item.get("id") or "")),
            "reason": str(item.get("reason") or ""),
            "confidence": float(item.get("confidence") or 0.0),
            "gold": str(item.get("id") or "") in gold_set,
        }
        for item in prediction.get("activated_memory_ids") or []
    ]

    paths: List[Dict[str, Any]] = []
    for path in prepared.get("executed_paths") or []:
        selected = [
            item
            for item in path.get("ranking") or []
            if item.get("selected")
        ]
        paths.append(
            {
                "path_id": path.get("path_id"),
                "path": path.get("path"),
                "reason": path.get("reason"),
                "execution_mode": path.get("execution_mode"),
                "gold_count": sum(
                    1 for memory_id in path.get("selected_memory_ids") or [] if memory_id in gold_set
                ),
                "items": [
                    {
                        "summary": str(item.get("summary") or ""),
                        "score": float(item.get("score") or 0.0),
                        "rank": int(item.get("rank") or 0),
                        "gold": str(item.get("memory_id") or "") in gold_set,
                    }
                    for item in selected
                ],
            }
        )

    repair_items: List[Dict[str, Any]] = []
    seen_repair = set()
    for item in prepared.get("evidence") or []:
        memory_id = str(item.get("source_id") or "")
        if not memory_id or memory_id in seen_repair:
            continue
        seen_repair.add(memory_id)
        repair_items.append(
            {
                "summary": str(item.get("content") or memory_summary(memory_id)),
                "score": float(item.get("score") or 0.0),
                "gold": memory_id in gold_set,
                "gap_id": item.get("candidate_gap_id"),
            }
        )

    counts = {
        "gold": len(gold_ids),
        "predictor": sum(1 for memory_id in gold_ids if memory_id in predictor_ids),
        "path": sum(1 for memory_id in gold_ids if memory_id in path_ids),
        "repair": sum(1 for memory_id in gold_ids if memory_id in repair_ids),
        "candidate": sum(1 for memory_id in gold_ids if memory_id in candidate_ids),
        "final": sum(1 for memory_id in gold_ids if memory_id in final_ids),
    }
    counts["compression_lost"] = counts["candidate"] - counts["final"]
    counts["path_rescued"] = sum(
        1 for memory_id in gold_ids if memory_id in path_ids and memory_id not in predictor_ids
    )
    counts["repair_rescued"] = sum(
        1
        for memory_id in gold_ids
        if memory_id in repair_ids and memory_id not in predictor_ids and memory_id not in path_ids
    )

    return {
        "sample_id": (report.get("sample") or {}).get("id"),
        "history_turn_count": (report.get("sample") or {}).get("history_turn_count"),
        "actual_query": (report.get("sample") or {}).get("question"),
        "golden_answer": (report.get("sample") or {}).get("answer"),
        "predicted_intents": list(prediction.get("predicted_future_intents") or []),
        "predictor_items": predictor_items,
        "paths": paths,
        "support_check": dict(prepared.get("support_check") or {}),
        "gaps": list(prepared.get("gaps") or []),
        "repair_items": repair_items,
        "bindings": list(prepared.get("bindings") or []),
        "gold_rows": gold_rows,
        "counts": counts,
        "metrics": dict(overlay.get("activation_metrics") or {}),
        "planning_ms": float((report.get("runtime") or {}).get("total_planning_ms") or 0.0),
        "llm_calls": len(report.get("llm_calls") or []),
    }


def build_batch(
    reports: Sequence[Dict[str, Any]],
    localization: Mapping[str, Any],
) -> Dict[str, Any]:
    samples = [build_sample_diagnosis(report) for report in reports]
    totals = {
        key: sum(int(sample["counts"].get(key) or 0) for sample in samples)
        for key in [
            "gold",
            "predictor",
            "path",
            "repair",
            "candidate",
            "final",
            "compression_lost",
            "path_rescued",
            "repair_rescued",
        ]
    }
    return {
        "schema_version": 1,
        "sample_count": len(samples),
        "samples": samples,
        "totals": totals,
        "localization": dict(localization),
    }


def write_batch(
    batch: Mapping[str, Any],
    output_dir: Path,
    template_path: Path,
) -> Dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "planning_trace_batch.json"
    html_path = output_dir / "planning_trace_batch.html"
    write_json(json_path, batch)
    payload = json.dumps(batch, ensure_ascii=False).replace("</", "<\\/")
    template = template_path.read_text(encoding="utf-8")
    html_path.write_text(
        template.replace("__PLANNING_TRACE_BATCH_DATA__", payload),
        encoding="utf-8",
    )
    return {"json": json_path, "html": html_path}


def build_parser() -> argparse.ArgumentParser:
    code_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Build a multi-sample intent/meta-path planning diagnosis."
    )
    parser.add_argument("--input-root", required=True)
    parser.add_argument("--sample-ids", required=True, help="Comma-separated sample ids")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--template",
        default=str(code_dir / "templates" / "planning_trace_batch.html"),
    )
    parser.add_argument(
        "--single-template",
        default=str(code_dir / "templates" / "planning_trace.html"),
    )
    parser.add_argument("--translate", action="store_true")
    parser.add_argument("--vllm-url", default="http://127.0.0.1:30000/v1")
    parser.add_argument("--vllm-model", default="../Qwen2.5-7B-Instruct")
    parser.add_argument("--vllm-timeout", type=float, default=120.0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    input_root = Path(args.input_root)
    sample_ids = [item.strip() for item in args.sample_ids.split(",") if item.strip()]
    report_paths = [
        input_root / f"planning_trace_{sample_id}" / "planning_trace.json"
        for sample_id in sample_ids
    ]
    missing = [str(path) for path in report_paths if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing reports: " + ", ".join(missing))
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in report_paths]

    strings = unique(
        text
        for report in reports
        for text in collect_semantic_strings(report)
    )
    localization: Dict[str, Any] = {
        "language": "zh-CN",
        "target_language": "简体中文",
        "strings": {},
        "calls": [],
        "excluded_from_planning_latency": True,
    }
    if args.translate:
        client = VLLMClient(
            base_url=args.vllm_url,
            model=args.vllm_model,
            timeout=args.vllm_timeout,
        )
        translations, calls = translate_strings(strings, client)
        localization["strings"] = translations
        localization["calls"] = calls
        localization["string_count"] = len(strings)
        for report, report_path in zip(reports, report_paths):
            report["localization"] = localization
            write_report(report, report_path.parent, Path(args.single_template))

    batch = build_batch(reports, localization)
    paths = write_batch(batch, Path(args.output_dir), Path(args.template))
    print("Planning trace batch complete")
    print(f"  samples: {len(reports)}")
    print(f"  gold: {batch['totals']['gold']}")
    print(
        "  coverage: "
        f"predictor={batch['totals']['predictor']} "
        f"path={batch['totals']['path']} "
        f"repair={batch['totals']['repair']} "
        f"candidate={batch['totals']['candidate']} "
        f"final={batch['totals']['final']}"
    )
    for label, path in paths.items():
        print(f"  {label}: {path.resolve()}")


if __name__ == "__main__":
    main()
