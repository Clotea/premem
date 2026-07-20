from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List, Mapping

if __package__ in {None, ""}:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from code.utils import average, average_present, load_json, locomo_answer_f1, write_json
else:
    from .utils import average, average_present, load_json, locomo_answer_f1, write_json


def rescore(summary: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for method in summary:
        rows = list(method.get("samples") or [])
        for row in rows:
            category = row.get("category")
            score = locomo_answer_f1(
                str(row.get("generated_answer") or ""),
                str(row.get("gold_answer") or ""),
                category,
            )
            row["official_f1"] = score
            row["temporal_f1"] = score if str(category) == "2" else None
        method["official_f1"] = average(rows, "official_f1")
        temporal_rows = [row for row in rows if str(row.get("category")) == "2"]
        method["temporal_f1"] = average_present(temporal_rows, "temporal_f1")
        method["by_category"] = {
            category: summarize_category(
                [row for row in rows if str(row.get("category")) == category]
            )
            for category in sorted({str(row.get("category")) for row in rows})
        }
    return summary


def summarize_category(rows: List[Mapping[str, Any]]) -> Dict[str, Any]:
    return {
        "count": len(rows),
        "official_f1": average(rows, "official_f1"),
        "llm_judge": average_present(rows, "llm_judge"),
        "strict_judge": average_present(rows, "strict_judge"),
        "recall": average(rows, "recall"),
        "full_cover_rate": average(rows, "full_cover_rate"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-score a LoCoMo JSON result without rerunning inference.")
    parser.add_argument("input")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    write_json(args.output, rescore(load_json(args.input)))
    print(Path(args.output).resolve())


if __name__ == "__main__":
    main()
