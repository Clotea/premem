from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


TARGET_METHOD = "Pre-query Prepared + Reader"
CACHE_ONLY_METHOD = "LLM-Predict Cache Only"


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze LoCoMo evaluation JSON logs.")
    parser.add_argument("json_path", help="Path to the LoCoMo JSON log.")
    parser.add_argument("--baseline-json", default=None, help="Optional previous JSON log to compare against.")
    parser.add_argument("--out", default=None, help="Optional markdown report path.")
    args = parser.parse_args()

    rows = load_rows(Path(args.json_path))
    baseline_rows = load_rows(Path(args.baseline_json)) if args.baseline_json else None
    report = build_report(rows, baseline_rows=baseline_rows, json_path=Path(args.json_path))
    print(report)
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(report + "\n", encoding="utf-8")


def load_rows(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError(f"Expected top-level list in {path}")
    return [dict(item) for item in data if isinstance(item, Mapping)]


def build_report(
    rows: Sequence[Mapping[str, Any]],
    baseline_rows: Optional[Sequence[Mapping[str, Any]]] = None,
    json_path: Optional[Path] = None,
) -> str:
    by_method = {str(row.get("method")): row for row in rows}
    target = by_method.get(TARGET_METHOD)
    cache_only = by_method.get(CACHE_ONLY_METHOD)
    if target is None:
        raise ValueError(f"Cannot find method: {TARGET_METHOD}")

    lines: List[str] = []
    lines.append("# LoCoMo Result Analysis")
    if json_path is not None:
        lines.append(f"\n- Result file: `{json_path}`")
    lines.append(f"- Samples: {len(target.get('samples') or [])}")

    lines.append("\n## Method Summary")
    lines.extend(format_method_table(rows))

    lines.append("\n## Main Comparisons")
    for baseline in ["Random Cache", "Recency Cache", "Reactive Vector Retrieval", "Reactive Graph Retrieval", CACHE_ONLY_METHOD]:
        if baseline in by_method:
            lines.extend(compare_methods(target, by_method[baseline], baseline))

    if baseline_rows is not None:
        baseline_target = {str(row.get("method")): row for row in baseline_rows}.get(TARGET_METHOD)
        if baseline_target is not None:
            lines.append("\n## Previous-Run Comparison")
            lines.extend(compare_methods(target, baseline_target, f"previous {TARGET_METHOD}"))
            if len(target.get("samples") or []) != len(baseline_target.get("samples") or []):
                lines.append(
                    "- Note: sample counts differ, so this previous-run comparison is directional only."
                )

    lines.append("\n## Verifier / Reranker Diagnosis")
    lines.extend(analyze_target_samples(target, cache_only))

    lines.append("\n## Literature-Level Check")
    lines.extend(literature_check(target, by_method))

    lines.append("\n## Bottom Line")
    lines.extend(bottom_line(target, by_method))
    return "\n".join(lines)


def format_method_table(rows: Sequence[Mapping[str, Any]]) -> List[str]:
    lines = [
        "| method | samples | selected | precision | recall | hit_rate | full_cover | query_retrieval_ms | official_f1 | bleu1 | rouge_l |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {method} | {samples} | {selected_count:.2f} | {precision:.4f} | {recall:.4f} | {hit_rate:.4f} | "
            "{full_cover_rate:.4f} | {query_retrieval_latency_ms:.3f} | {official_f1:.4f} | {bleu1:.4f} | {rouge_l:.4f} |".format(
                method=row.get("method"),
                samples=len(row.get("samples") or []),
                selected_count=float(row.get("selected_count") or 0.0),
                precision=float(row.get("precision") or 0.0),
                recall=float(row.get("recall") or 0.0),
                hit_rate=float(row.get("hit_rate") or 0.0),
                full_cover_rate=float(row.get("full_cover_rate") or 0.0),
                query_retrieval_latency_ms=float(row.get("query_retrieval_latency_ms") or 0.0),
                official_f1=float(row.get("official_f1") or 0.0),
                bleu1=float(row.get("bleu1") or 0.0),
                rouge_l=float(row.get("rouge_l") or 0.0),
            )
        )
    return lines


def compare_methods(target: Mapping[str, Any], baseline: Mapping[str, Any], baseline_name: str) -> List[str]:
    lines = [f"\nCompared with `{baseline_name}`:"]
    for metric in [
        "selected_count",
        "precision",
        "recall",
        "hit_rate",
        "full_cover_rate",
        "query_retrieval_latency_ms",
        "official_f1",
        "bleu1",
        "f1",
        "rouge_l",
    ]:
        current = float(target.get(metric) or 0.0)
        old = float(baseline.get(metric) or 0.0)
        delta = current - old
        rel = (delta / old * 100.0) if old else 0.0
        lines.append(f"- {metric}: {current:.4f} vs {old:.4f}, delta={delta:+.4f}, rel={rel:+.1f}%")
    return lines


def analyze_target_samples(
    target: Mapping[str, Any],
    cache_only: Optional[Mapping[str, Any]],
) -> List[str]:
    samples = list(target.get("samples") or [])
    lines: List[str] = []
    if not samples:
        return ["- No samples found."]

    provider_counts: Counter[str] = Counter()
    decision_counts: Counter[str] = Counter()
    reranker_counts: Counter[str] = Counter()
    reranker_reasons: Counter[str] = Counter()
    prepared_hit = 0
    final_hit = 0
    prepared_full = 0
    final_full = 0
    selection_losses: List[Mapping[str, Any]] = []
    prepared_misses: List[Mapping[str, Any]] = []

    for sample in samples:
        trace = sample.get("trace") or {}
        verifier = trace.get("verifier") or {}
        provider_counts[str(verifier.get("provider") or "missing")] += 1
        decision_counts[str(verifier.get("decision") or "missing")] += 1
        reranker = verifier.get("reranker") or {}
        if reranker:
            status = "available" if reranker.get("available") else "unavailable"
            reranker_counts[status] += 1
            if not reranker.get("available"):
                reranker_reasons[str(reranker.get("reason") or "unknown")] += 1
        else:
            reranker_counts["not_recorded"] += 1

        gold = set(trace_get(trace, "ground_truth.gold_evidence_memory_ids") or [])
        prepared = set(trace_get(trace, "gap_reasoning.prepared_memory_ids") or [])
        final = set(sample.get("selected_memory_ids") or [])
        if gold and gold & prepared:
            prepared_hit += 1
        if gold and gold <= prepared:
            prepared_full += 1
        if gold and gold & final:
            final_hit += 1
        if gold and gold <= final:
            final_full += 1
        if gold and gold & prepared and not (gold & final):
            selection_losses.append(sample)
        if gold and not (gold & prepared):
            prepared_misses.append(sample)

    n = len(samples)
    avg_proactive_precision = average(sample.get("proactive_precision", 0.0) for sample in samples)
    avg_proactive_recall = average(sample.get("proactive_recall", 0.0) for sample in samples)
    avg_proactive_hit = average(sample.get("proactive_hit_rate", 0.0) for sample in samples)
    avg_proactive_full = average(sample.get("proactive_full_cover_rate", sample.get("full_cover_rate", 0.0)) for sample in samples)
    avg_query_retrieval = average(sample.get("query_retrieval_latency_ms", 0.0) for sample in samples)

    lines.append(f"- Proactive precision: {avg_proactive_precision:.4f}")
    lines.append(f"- Proactive recall: {avg_proactive_recall:.4f}")
    lines.append(f"- Proactive hit_rate: {avg_proactive_hit:.4f}")
    lines.append(f"- Proactive full_cover_rate: {avg_proactive_full:.4f}")
    lines.append(f"- Avg query-time retrieval latency: {avg_query_retrieval:.3f} ms")
    lines.append(f"- Prepared hit samples: {prepared_hit}/{n} ({prepared_hit / n:.1%})")
    lines.append(f"- Prepared full-cover samples: {prepared_full}/{n} ({prepared_full / n:.1%})")
    lines.append(f"- Final hit samples: {final_hit}/{n} ({final_hit / n:.1%})")
    lines.append(f"- Final full-cover samples: {final_full}/{n} ({final_full / n:.1%})")
    lines.append(f"- Selection losses: {len(selection_losses)} samples where prepared hit but final missed")
    lines.append(f"- Prepared misses: {len(prepared_misses)} samples where prepared missed all gold evidence")
    lines.append(f"- Verifier providers: {dict(provider_counts)}")
    lines.append(f"- Verifier decisions: {dict(decision_counts)}")
    lines.append(f"- Reranker status: {dict(reranker_counts)}")
    if reranker_reasons:
        lines.append("- Reranker unavailable reasons:")
        for reason, count in reranker_reasons.most_common(5):
            lines.append(f"  - {count}: {reason}")

    lines.append("\nRepresentative selection losses:")
    lines.extend(format_sample_examples(selection_losses[:5]))

    lines.append("\nRepresentative prepared misses:")
    lines.extend(format_sample_examples(prepared_misses[:5]))

    if cache_only is not None:
        lines.append("\nCache-only vs pre-query reader:")
        lines.append(
            f"- Cache-only recall={float(cache_only.get('recall') or 0.0):.4f}, "
            f"pre-query prepared recall={float(target.get('recall') or 0.0):.4f}"
        )
        lines.append(
            f"- Cache-only query retrieval={float(cache_only.get('query_retrieval_latency_ms') or 0.0):.3f} ms, "
            f"pre-query cache read={float(target.get('query_retrieval_latency_ms') or 0.0):.3f} ms"
        )
        lines.append(
            f"- Fallback rate={float(target.get('fallback_rate') or 0.0):.4f}; "
            "this target method does not use post-query fallback for memory selection."
        )
    return lines


def literature_check(target: Mapping[str, Any], by_method: Mapping[str, Mapping[str, Any]]) -> List[str]:
    answer_f1 = float(target.get("official_f1") or target.get("f1") or 0.0) * 100.0
    recall = float(target.get("recall") or 0.0) * 100.0
    full_cover = float(target.get("full_cover_rate") or 0.0) * 100.0
    budget = int(float(target.get("budget") or 0.0))
    selected_count = float(target.get("selected_count") or 0.0)
    query_retrieval_ms = float(target.get("query_retrieval_latency_ms") or 0.0)
    recency_recall = float((by_method.get("Recency Cache") or {}).get("recall") or 0.0) * 100.0
    recency_delta = recall - recency_recall
    if recency_delta >= 0:
        recency_text = (
            "- Recency evidence recall in the same run is {:.2f}; "
            "the method is {:+.2f} points higher."
        ).format(recency_recall, recency_delta)
    else:
        recency_text = (
            "- Recency evidence recall in the same run is {:.2f}; "
            "the method is {:+.2f} points lower."
        ).format(recency_recall, recency_delta)
    lines = [
        "- This run's answer F1 is {:.2f} on a 0-100 scale.".format(answer_f1),
        "- This run's evidence-selection recall is {:.2f} and full-cover is {:.2f} on a 0-100 scale; cache budget={}, avg selected memories={:.2f}.".format(recall, full_cover, budget, selected_count),
        "- Avg measured query-time retrieval latency is {:.3f} ms for the target method.".format(query_retrieval_ms),
        recency_text,
        "- Published LoCoMo papers usually report answer F1 / recall@k under their own retrieval units and k values, so the numbers are not strictly one-to-one comparable.",
        "- The current run should not be claimed as surpassing paper-level LoCoMo results. It is below the original RAG-style LoCoMo answer-F1 range and also below recent dedicated memory-system reports.",
    ]
    return lines


def bottom_line(target: Mapping[str, Any], by_method: Mapping[str, Mapping[str, Any]]) -> List[str]:
    recency = by_method.get("Recency Cache") or {}
    vector = by_method.get("Reactive Vector Retrieval") or {}
    target_recall = float(target.get("recall") or 0.0)
    target_precision = float(target.get("precision") or 0.0)
    target_full_cover = float(target.get("full_cover_rate") or 0.0)
    target_retrieval_ms = float(target.get("query_retrieval_latency_ms") or 0.0)
    vector_retrieval_ms = float(vector.get("query_retrieval_latency_ms") or 0.0)
    lines = [
        f"- Current final precision={target_precision:.4f}, recall={target_recall:.4f}, hit_rate={float(target.get('hit_rate') or 0.0):.4f}, full_cover={target_full_cover:.4f}.",
        f"- Query-time retrieval latency={target_retrieval_ms:.3f} ms; vector retrieval latency={vector_retrieval_ms:.3f} ms.",
        f"- Compared with vector retrieval, recall delta={target_recall - float(vector.get('recall') or 0.0):+.4f}.",
        f"- Compared with recency, recall delta={target_recall - float(recency.get('recall') or 0.0):+.4f}.",
    ]
    if target_recall < float(recency.get("recall") or 0.0):
        lines.append("- The method is not yet competitive with the strong recency baseline on this time-sliced setup.")
    else:
        lines.append("- The method is competitive with the recency baseline on recall for this run, but answer F1 is still far below paper-level LoCoMo results.")
    return lines


def format_sample_examples(samples: Sequence[Mapping[str, Any]]) -> List[str]:
    if not samples:
        return ["- (none)"]
    lines = []
    for sample in samples:
        trace = sample.get("trace") or {}
        verifier = trace.get("verifier") or {}
        lines.append(
            "- {sample_id}: gold={gold}; prepared={prepared}; verifier={verifier_ids}; final={final}; provider={provider}".format(
                sample_id=sample.get("sample_id"),
                gold=",".join(trace_get(trace, "ground_truth.gold_evidence_memory_ids") or []),
                prepared=",".join(trace_get(trace, "gap_reasoning.prepared_memory_ids") or []),
                verifier_ids=",".join(verifier.get("selected_memory_ids") or []),
                final=",".join(sample.get("selected_memory_ids") or []),
                provider=verifier.get("provider"),
            )
        )
    return lines


def trace_get(data: Mapping[str, Any], dotted: str) -> Any:
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def average(values: Iterable[Any]) -> float:
    vals = [float(value or 0.0) for value in values]
    return sum(vals) / len(vals) if vals else 0.0


if __name__ == "__main__":
    main()
