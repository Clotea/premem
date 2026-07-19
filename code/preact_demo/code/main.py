from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from preact_demo.code.locomo import LOCOMO_URL, download_locomo, load_locomo_samples
from preact_demo.code.pipeline import run_evaluation
from preact_demo.code.predictors import create_predictor
from preact_demo.code.utils import Sample, format_table, load_json
from preact_demo.code.vllm_client import VLLMClient


def main() -> None:
    code_root = Path(__file__).resolve().parent
    project_root = code_root.parent
    parser = argparse.ArgumentParser(description="PreAct-Memory Python demo with vLLM-compatible predictor.")
    parser.add_argument("--dataset", choices=["demo", "locomo"], default="demo")
    parser.add_argument("--config", default=str(code_root / "configs" / "python_demo.json"))
    parser.add_argument("--limit", type=int, default=None, help="Number of QA samples. Use 0 for all.")
    parser.add_argument("--locomo-path", default=str(project_root / "data" / "locomo" / "locomo10.json"))
    parser.add_argument("--download-locomo", action="store_true")
    parser.add_argument("--predictor", choices=["vllm", "heuristic", "auto"], default=None)
    parser.add_argument("--cache-budget", type=int, default=None)
    parser.add_argument("--retrieval-top-k", type=int, default=None)
    parser.add_argument("--fallback-retriever", choices=["vector", "graph"], default=None)
    parser.add_argument("--vllm-url", default=None)
    parser.add_argument("--vllm-host", default=None)
    parser.add_argument("--vllm-port", type=int, default=None)
    parser.add_argument("--vllm-model", default=None)
    parser.add_argument("--answer-with-vllm", action="store_true")
    parser.add_argument("--enable-external-search", action="store_true")
    parser.add_argument("--details", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    apply_overrides(config, args)
    samples = load_dataset(args, project_root)
    if not samples:
        raise RuntimeError("No samples loaded.")

    llm_config = dict(config.get("llm") or {})
    client = VLLMClient(
        base_url=str(llm_config.get("base_url") or "http://127.0.0.1:8000/v1"),
        model=str(llm_config.get("model") or "Qwen/Qwen2.5-7B-Instruct"),
        timeout=float(llm_config.get("timeout") or 30),
        api_key=llm_config.get("api_key"),
    )
    predictor = create_predictor(config, client)
    summary = run_evaluation(samples, config, predictor, llm_client=client)

    print("PreAct-Memory Python Demo")
    print(f"Dataset: {args.dataset}")
    print(f"Samples: {len(samples)}")
    print(f"Predictor: {getattr(predictor, 'name', config.get('predictor'))}")
    print(f"vLLM endpoint: {client.base_url}")
    print(f"vLLM model: {client.model}")
    print(f"Embedding provider: {dict(config.get('embedding') or {}).get('provider', 'hash')}")
    print(f"External search: {'enabled' if dict(config.get('external_search') or {}).get('enabled') else 'disabled'}")
    if args.dataset == "locomo":
        print(f"LoCoMo path: {Path(args.locomo_path).resolve()}")
        print(f"LoCoMo source: {LOCOMO_URL}")

    print("\nActivation Quality")
    print(format_table(summary, ["method", "budget", "precision", "recall", "hit_rate", "wasted_rate"]))

    print("\nAnswer Quality")
    print(format_table(summary, ["method", "f1", "rouge_l", "llm_judge", "faithfulness"]))

    print("\nEfficiency")
    print(format_table(summary, ["method", "query_time_latency_ms", "idle_time_cost", "total_tokens", "hit_rate", "fallback_rate"]))

    if args.details or len(samples) <= 20:
        print("\nPer-Sample Selected Memories")
        for row in summary:
            print(f"\n{row['method']}")
            for sample_row in row["samples"]:
                selected = ", ".join(sample_row["selected_memory_ids"]) or "(none)"
                print(f"  {sample_row['sample_id']}: {selected}")
                if sample_row.get("working_context_package_id"):
                    print(
                        "    package="
                        f"{sample_row['working_context_package_id']} "
                        f"coverage={sample_row.get('working_context_coverage', 0.0):.3f} "
                        f"gaps={sample_row.get('memory_gap_count', 0)} "
                        f"external={sample_row.get('external_evidence_count', 0)} "
                        f"verifier={sample_row.get('verifier_method', 'n/a')}"
                    )
    else:
        print("\nPer-sample details hidden for large runs. Add --details to print them.")


def load_config(path: str) -> Dict[str, Any]:
    config = load_config_file(path)
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
    result.setdefault("cache_budget", 3)
    result.setdefault("retrieval_top_k", 3)
    result.setdefault("fallback_retriever", "vector")
    result.setdefault("verifier_threshold", 0.12)
    result.setdefault("similarity_threshold", 0.28)
    result.setdefault("random_seed", 7)
    result.setdefault("predictor", "vllm")
    result.setdefault("predictor_candidate_limit", 80)
    result.setdefault("answer_with_vllm", False)
    result.setdefault("embedding", {"provider": "hash", "dimensions": 256})
    result.setdefault(
        "grounding",
        {"mode": "embedding", "activation_threshold": 0.08, "candidates_per_hypothesis": 5},
    )
    result.setdefault("memory_gap", {"mode": "score", "coverage_threshold": 0.18})
    result.setdefault(
        "external_search",
        {
            "enabled": False,
            "source_types": ["web", "paper"],
            "results_per_query": 2,
            "max_queries_per_gap": 2,
            "timeout": 8,
        },
    )
    result.setdefault(
        "verifier",
        {
            "mode": "embedding",
            "threshold": result["verifier_threshold"],
            "hypothesis_threshold": result["verifier_threshold"],
        },
    )
    result.setdefault("llm", {})
    llm_config = dict(result["llm"])
    if "url" in llm_config and "base_url" not in llm_config:
        llm_config["base_url"] = llm_config["url"]
    has_config_host = "host" in llm_config
    has_config_port = "port" in llm_config
    llm_config.setdefault("host", os.getenv("VLLM_HOST", "127.0.0.1"))
    llm_config["port"] = _to_int(llm_config.get("port") or os.getenv("VLLM_PORT"), 8000)
    llm_config.setdefault("api_path", os.getenv("VLLM_API_PATH", "/v1"))
    if not llm_config.get("base_url"):
        env_base_url = os.getenv("VLLM_BASE_URL")
        if env_base_url and not has_config_host and not has_config_port:
            llm_config["base_url"] = env_base_url
        else:
            llm_config["base_url"] = build_vllm_base_url(
                llm_config["host"],
                llm_config["port"],
                llm_config["api_path"],
            )
    llm_config.setdefault("model", os.getenv("VLLM_MODEL", "Qwen/Qwen2.5-7B-Instruct"))
    llm_config.setdefault("timeout", float(os.getenv("VLLM_TIMEOUT", "30")))
    llm_config.setdefault("fallback_to_heuristic", True)
    result["llm"] = llm_config
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
    if args.enable_external_search:
        search_config = dict(config.get("external_search") or {})
        search_config["enabled"] = True
        config["external_search"] = search_config
    llm_config = dict(config.get("llm") or {})
    if args.vllm_host is not None:
        llm_config["host"] = args.vllm_host
    if args.vllm_port is not None:
        llm_config["port"] = args.vllm_port
    if args.vllm_host is not None or args.vllm_port is not None:
        llm_config["base_url"] = build_vllm_base_url(
            llm_config.get("host", "127.0.0.1"),
            _to_int(llm_config.get("port"), 8000),
            llm_config.get("api_path", "/v1"),
        )
    if args.vllm_url is not None:
        llm_config["base_url"] = args.vllm_url
    if args.vllm_model is not None:
        llm_config["model"] = args.vllm_model
    config["llm"] = llm_config


def load_config_file(path: str) -> Dict[str, Any]:
    config_path = Path(path)
    suffix = config_path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        config = load_yaml_config(config_path)
    else:
        config = load_json(config_path)
    if not isinstance(config, dict):
        raise ValueError(f"Config must be a mapping: {config_path}")
    return config


def load_yaml_config(path: Path) -> Dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError:
        return _load_simple_yaml(path)

    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"YAML config must be a mapping: {path}")
    return loaded


def build_vllm_base_url(host: Any, port: Any, api_path: Any = "/v1") -> str:
    host_value = str(host or "127.0.0.1").strip().rstrip("/")
    scheme = "http"
    if "://" in host_value:
        scheme, host_value = host_value.split("://", 1)
    path = str(api_path or "/v1").strip()
    if not path.startswith("/"):
        path = "/" + path
    return f"{scheme}://{host_value}:{_to_int(port, 8000)}{path}".rstrip("/")


def _to_int(value: Any, default: int) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _load_simple_yaml(path: Path) -> Dict[str, Any]:
    lines = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            without_comment = _strip_yaml_comment(raw_line).rstrip()
            if not without_comment.strip():
                continue
            indent = len(without_comment) - len(without_comment.lstrip(" "))
            lines.append((indent, without_comment.strip()))
    if not lines:
        return {}
    parsed, index = _parse_yaml_block(lines, 0, lines[0][0])
    if index != len(lines):
        raise ValueError(f"Unsupported YAML structure near line {index + 1}: {path}")
    if not isinstance(parsed, dict):
        raise ValueError(f"YAML config must be a mapping: {path}")
    return parsed


def _strip_yaml_comment(line: str) -> str:
    quote: Optional[str] = None
    for index, char in enumerate(line):
        if char in {"'", '"'} and (index == 0 or line[index - 1] != "\\"):
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
        elif char == "#" and quote is None:
            return line[:index]
    return line


def _parse_yaml_block(
    lines: List[tuple[int, str]],
    index: int,
    indent: int,
) -> tuple[Any, int]:
    if index >= len(lines):
        return {}, index
    is_list = lines[index][1].startswith("- ")
    if is_list:
        result: List[Any] = []
        while index < len(lines):
            current_indent, text = lines[index]
            if current_indent < indent:
                break
            if current_indent != indent or not text.startswith("- "):
                break
            item_text = text[2:].strip()
            if item_text:
                result.append(_parse_yaml_scalar(item_text))
                index += 1
            elif index + 1 < len(lines) and lines[index + 1][0] > current_indent:
                item, index = _parse_yaml_block(lines, index + 1, lines[index + 1][0])
                result.append(item)
            else:
                result.append(None)
                index += 1
        return result, index

    result: Dict[str, Any] = {}
    while index < len(lines):
        current_indent, text = lines[index]
        if current_indent < indent:
            break
        if current_indent != indent or text.startswith("- "):
            break
        key, separator, raw_value = text.partition(":")
        if not separator:
            raise ValueError(f"Invalid YAML entry: {text}")
        key = key.strip()
        value = raw_value.strip()
        if value:
            result[key] = _parse_yaml_scalar(value)
            index += 1
        elif index + 1 < len(lines) and lines[index + 1][0] > current_indent:
            nested, index = _parse_yaml_block(lines, index + 1, lines[index + 1][0])
            result[key] = nested
        else:
            result[key] = {}
            index += 1
    return result, index


def _parse_yaml_scalar(value: str) -> Any:
    stripped = value.strip()
    if not stripped:
        return ""
    if (stripped[0], stripped[-1]) in {("'", "'"), ('"', '"')}:
        return stripped[1:-1]
    lowered = stripped.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none", "~"}:
        return None
    if stripped.startswith("[") and stripped.endswith("]"):
        inner = stripped[1:-1].strip()
        if not inner:
            return []
        return [_parse_yaml_scalar(item.strip()) for item in _split_inline_list(inner)]
    try:
        return int(stripped)
    except ValueError:
        pass
    try:
        return float(stripped)
    except ValueError:
        return stripped


def _split_inline_list(value: str) -> List[str]:
    parts: List[str] = []
    current: List[str] = []
    quote: Optional[str] = None
    for index, char in enumerate(value):
        if char in {"'", '"'} and (index == 0 or value[index - 1] != "\\"):
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
        if char == "," and quote is None:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    parts.append("".join(current).strip())
    return parts


def load_dataset(args: argparse.Namespace, root: Path) -> List[Sample]:
    if args.dataset == "demo":
        raw_samples = load_json(root / "data" / "samples.json")
        samples = [Sample.from_dict(item) for item in raw_samples]
        return apply_limit(samples, args.limit)

    locomo_path = Path(args.locomo_path)
    if not locomo_path.exists():
        if not args.download_locomo:
            raise FileNotFoundError(
                f"LoCoMo file not found: {locomo_path}. "
                "Use --download-locomo or provide --locomo-path."
            )
        download_locomo(locomo_path)
    return load_locomo_samples(locomo_path, limit=args.limit)


def apply_limit(samples: List[Sample], limit: Optional[int]) -> List[Sample]:
    if limit is None:
        return samples
    if limit == 0:
        return samples
    if limit < 0:
        raise ValueError("--limit must be >= 0")
    return samples[:limit]


if __name__ == "__main__":
    main()
