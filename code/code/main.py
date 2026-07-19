from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    loaded_code = sys.modules.get("code")
    if loaded_code is not None and not hasattr(loaded_code, "__path__"):
        del sys.modules["code"]
    from code.locomo import LOCOMO_URL, download_locomo, load_locomo_samples
    from code.pipeline import run_evaluation
    from code.predictors import create_predictor
    from code.reranker import assert_reranker_available, reranker_required
    from code.utils import Sample, format_table, load_json, write_json
    from code.vllm_client import VLLMClient
else:
    from .locomo import LOCOMO_URL, download_locomo, load_locomo_samples
    from .pipeline import run_evaluation
    from .predictors import create_predictor
    from .reranker import assert_reranker_available, reranker_required
    from .utils import Sample, format_table, load_json, write_json
    from .vllm_client import VLLMClient


def main() -> None:
    code_root = Path(__file__).resolve().parent
    project_root = code_root.parent
    data_root = find_data_root(project_root)
    parser = argparse.ArgumentParser(description="PreAct-Memory Python demo with vLLM-compatible predictor.")
    parser.add_argument("--dataset", choices=["demo", "locomo"], default="demo")
    parser.add_argument("--config", default=str(code_root / "configs" / "python_demo.json"))
    parser.add_argument("--limit", type=int, default=None, help="Number of QA samples. Use 0 for all.")
    parser.add_argument("--locomo-path", default=str(data_root / "locomo" / "locomo10.json"))
    parser.add_argument("--download-locomo", action="store_true")
    parser.add_argument("--predictor", choices=["vllm", "heuristic", "auto"], default=None)
    parser.add_argument("--cache-budget", type=int, default=None)
    parser.add_argument("--retrieval-top-k", type=int, default=None)
    parser.add_argument("--fallback-retriever", choices=["vector", "graph"], default=None)
    parser.add_argument("--vllm-url", default=None)
    parser.add_argument("--vllm-host", default=None)
    parser.add_argument("--vllm-port", type=int, default=None)
    parser.add_argument("--vllm-model", default=None)
    parser.add_argument("--disable-reranker", action="store_true")
    parser.add_argument("--reranker-provider", choices=["flagembedding", "vllm"], default=os.getenv("RERANKER_PROVIDER"))
    parser.add_argument("--reranker-model", default=os.getenv("RERANKER_MODEL"))
    parser.add_argument("--reranker-url", default=os.getenv("RERANKER_BASE_URL"))
    parser.add_argument("--reranker-devices", default=os.getenv("RERANKER_DEVICES"))
    parser.add_argument("--reranker-cache-dir", default=os.getenv("RERANKER_CACHE_DIR"))
    parser.add_argument("--require-reranker", action="store_true")
    parser.add_argument("--answer-with-vllm", action="store_true")
    parser.add_argument("--json-log", default=None, help="Optional path for the full JSON summary log.")
    parser.add_argument("--details", action="store_true")
    args = parser.parse_args()

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
    samples = load_dataset(args, data_root)
    if not samples:
        raise RuntimeError("No samples loaded.")

    llm_config = dict(config.get("llm") or {})
    client = VLLMClient(
        base_url=str(llm_config.get("base_url") or "http://127.0.0.1:30000/v1"),
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
    if args.dataset == "locomo":
        print(f"LoCoMo path: {Path(args.locomo_path).resolve()}")
        print(f"LoCoMo source: {LOCOMO_URL}")

    print("\nActivation Quality")
    print(format_table(summary, ["method", "budget", "selected_count", "precision", "recall", "hit_rate", "full_cover_rate", "wasted_rate"]))

    print("\nAnswer Quality")
    print(format_table(summary, ["method", "official_f1", "bleu1", "f1", "rouge_l", "llm_judge", "faithfulness"]))

    print("\nEfficiency")
    print(format_table(summary, ["method", "query_retrieval_latency_ms", "query_time_latency_ms", "idle_time_cost", "total_tokens", "hit_rate", "fallback_rate"]))

    if args.details or len(samples) <= 20:
        print("\nPer-Sample Selected Memories")
        for row in summary:
            print(f"\n{row['method']}")
            for sample_row in row["samples"]:
                selected = ", ".join(sample_row["selected_memory_ids"]) or "(none)"
                print(f"  {sample_row['sample_id']}: {selected}")
    else:
        print("\nPer-sample details hidden for large runs. Add --details to print them.")

    if args.json_log:
        write_json(args.json_log, summary)
        print(f"\nJSON log: {Path(args.json_log).resolve()}")


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
        current = str(llm_config.get("base_url") or "http://127.0.0.1:30000/v1")
        host = args.vllm_host or _host_from_base_url(current) or "127.0.0.1"
        port = args.vllm_port or _port_from_base_url(current) or 30000
        llm_config["base_url"] = f"http://{host}:{port}/v1"
    if args.vllm_model is not None:
        llm_config["model"] = args.vllm_model
    config["llm"] = llm_config
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


def load_dataset(args: argparse.Namespace, data_root: Path) -> List[Sample]:
    if args.dataset == "demo":
        raw_samples = load_json(data_root / "samples.json")
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


def find_data_root(project_root: Path) -> Path:
    direct = project_root / "data"
    if (direct / "samples.json").exists():
        return direct
    preact_demo = project_root / "preact_demo" / "data"
    if (preact_demo / "samples.json").exists():
        return preact_demo
    return direct


def _host_from_base_url(base_url: str) -> Optional[str]:
    match = re_match_base_url(base_url)
    return match.group("host") if match else None


def _port_from_base_url(base_url: str) -> Optional[int]:
    match = re_match_base_url(base_url)
    if match and match.group("port"):
        return int(match.group("port"))
    return None


def re_match_base_url(base_url: str):
    import re

    return re.match(r"^https?://(?P<host>[^/:]+)(?::(?P<port>\d+))?", str(base_url or ""))


if __name__ == "__main__":
    main()
