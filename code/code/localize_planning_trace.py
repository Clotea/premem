from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List, Mapping, Sequence

from .planning_trace import write_report
from .utils import parse_llm_json, unique
from .vllm_client import VLLMClient


def collect_semantic_strings(report: Mapping[str, Any]) -> List[str]:
    values: List[str] = [
        str((report.get("sample") or {}).get("question") or ""),
        str((report.get("sample") or {}).get("answer") or ""),
    ]
    values.extend(
        str(item)
        for item in (report.get("prediction") or {}).get("predicted_future_intents") or []
    )
    values.extend(
        str(item.get("reason") or "")
        for item in (report.get("prediction") or {}).get("activated_memory_ids") or []
    )

    prepared = report.get("prepared_context") or {}
    for path in prepared.get("executed_paths") or []:
        values.append(str(path.get("reason") or ""))
        for item in path.get("ranking") or []:
            if item.get("selected"):
                values.append(str(item.get("summary") or ""))
    for item in (prepared.get("compression") or {}).get("scores") or []:
        values.append(str(item.get("summary") or ""))
    support = prepared.get("support_check") or {}
    values.extend(str(item) for item in support.get("supported_claims") or [])
    values.extend(str(item) for item in support.get("missing_support") or [])
    for gap in prepared.get("gaps") or []:
        values.extend(
            str(gap.get(key) or "")
            for key in ["related_claim", "missing_support", "repair_query"]
        )
    for evidence in prepared.get("evidence") or []:
        values.append(str(evidence.get("content") or ""))
    for binding in prepared.get("bindings") or []:
        values.append(str(binding.get("reason") or ""))

    relevant_ids = set(
        list((report.get("evaluation_overlay") or {}).get("prepared_memory_ids") or [])
        + list((report.get("evaluation_overlay") or {}).get("gold_memory_ids") or [])
    )
    for node in (report.get("graph") or {}).get("nodes") or []:
        if str(node.get("id") or "") in relevant_ids:
            values.append(str(node.get("summary") or ""))

    return [
        value
        for value in unique(value.strip() for value in values)
        if value
    ]


def translate_strings(
    strings: Sequence[str],
    client: VLLMClient,
    target_language: str = "简体中文",
    chunk_size: int = 18,
) -> tuple[Dict[str, str], List[Dict[str, Any]]]:
    translations: Dict[str, str] = {}
    calls: List[Dict[str, Any]] = []
    for offset in range(0, len(strings), max(1, chunk_size)):
        chunk = list(strings[offset : offset + max(1, chunk_size)])
        keyed = {f"s{offset + index + 1:03d}": text for index, text in enumerate(chunk)}
        messages = [
            {
                "role": "system",
                "content": (
                    "你是科研系统可视化的翻译器。把输入中的英文语义内容翻译成"
                    f"{target_language}。保持人名、技术名词、数字和事实准确；不要解释、"
                    "不要总结、不要增删信息。必须返回严格 JSON，键与输入完全一致。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(keyed, ensure_ascii=False),
            },
        ]
        started = perf_counter()
        content, usage = client.chat(
            messages,
            temperature=0.0,
            max_tokens=5000,
            response_format={"type": "json_object"},
        )
        parsed = parse_llm_json(content)
        for key, original in keyed.items():
            translated = str(parsed.get(key) or "").strip()
            translations[original] = translated or original
        calls.append(
            {
                "chunk_index": len(calls) + 1,
                "string_count": len(chunk),
                "elapsed_ms": round((perf_counter() - started) * 1000.0, 3),
                "usage": dict(usage),
            }
        )
    return translations, calls


def localize_report(
    report: Dict[str, Any],
    client: VLLMClient,
    language_code: str = "zh-CN",
    target_language: str = "简体中文",
) -> Dict[str, Any]:
    strings = collect_semantic_strings(report)
    translations, calls = translate_strings(strings, client, target_language=target_language)
    report["localization"] = {
        "language": language_code,
        "target_language": target_language,
        "string_count": len(strings),
        "strings": translations,
        "calls": calls,
        "excluded_from_planning_latency": True,
    }
    return report


def build_parser() -> argparse.ArgumentParser:
    code_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Translate the semantic content of an existing planning trace report."
    )
    parser.add_argument("--input", required=True, help="Existing planning_trace.json")
    parser.add_argument("--output-dir", default="")
    parser.add_argument(
        "--template",
        default=str(code_dir / "templates" / "planning_trace.html"),
    )
    parser.add_argument("--vllm-url", default="http://127.0.0.1:30000/v1")
    parser.add_argument("--vllm-model", default="../Qwen2.5-7B-Instruct")
    parser.add_argument("--vllm-timeout", type=float, default=120.0)
    parser.add_argument("--language-code", default="zh-CN")
    parser.add_argument("--target-language", default="简体中文")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    input_path = Path(args.input)
    report = json.loads(input_path.read_text(encoding="utf-8"))
    client = VLLMClient(
        base_url=args.vllm_url,
        model=args.vllm_model,
        timeout=args.vllm_timeout,
    )
    localized = localize_report(
        report,
        client,
        language_code=args.language_code,
        target_language=args.target_language,
    )
    output_dir = Path(args.output_dir) if args.output_dir else input_path.parent
    paths = write_report(localized, output_dir, Path(args.template))
    print("Planning trace localization complete")
    print(f"  language: {localized['localization']['language']}")
    print(f"  strings:  {localized['localization']['string_count']}")
    print(f"  calls:    {len(localized['localization']['calls'])}")
    for label, path in paths.items():
        print(f"  {label}: {path.resolve()}")


if __name__ == "__main__":
    main()
