from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .utils import tokenize, truncate


_FLAG_RERANKERS: Dict[Tuple[str, bool, str, str, int, int], Any] = {}
_RERANKER_FAILURES: Dict[Tuple[str, str, str, str], str] = {}


def reranker_required(config: Mapping[str, Any]) -> bool:
    reranker_config = _reranker_config(config)
    return bool(reranker_config.get("enabled", True)) and bool(
        reranker_config.get("require_available", False)
    )


def assert_reranker_available(config: Mapping[str, Any]) -> Dict[str, Any]:
    result = preflight_reranker(config)
    if result.get("available"):
        return result
    reason = str(result.get("reason") or "unknown reason")
    provider = str(result.get("provider") or (config.get("reranker") or {}).get("provider") or "reranker")
    model = str(result.get("model") or (config.get("reranker") or {}).get("model") or "")
    raise RuntimeError(
        f"Required reranker is unavailable ({provider}:{model}): {reason}. "
        "Fix the reranker environment or run with --disable-reranker."
    )


def preflight_reranker(config: Mapping[str, Any]) -> Dict[str, Any]:
    preflight_config = dict(config)
    preflight_config["reranker"] = dict(config.get("reranker") or {})
    preflight_config["reranker"]["top_k"] = 1
    preflight_config["reranker"]["dynamic_top_k"] = False
    return rerank_candidates(
        query="Where did Alice move for graduate school?",
        candidates=[
            {
                "id": "preflight_relevant",
                "summary": "Alice moved to Boston for graduate school.",
            },
            {
                "id": "preflight_irrelevant",
                "summary": "The meeting agenda included budget planning.",
            },
        ],
        config=preflight_config,
        default_top_k=1,
    )


def rerank_candidates(
    query: str,
    candidates: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    default_top_k: int = 3,
) -> Dict[str, Any]:
    reranker_config = _reranker_config(config)
    if not bool(reranker_config.get("enabled", True)):
        return {"available": False, "reason": "Reranker disabled."}

    docs = [_candidate_text(item) for item in candidates]
    ids = [str(item.get("id") or "") for item in candidates]
    if not query.strip() or not docs or not any(ids):
        return {"available": False, "reason": "No query or memory candidates to rerank."}

    provider = str(reranker_config.get("provider") or "flagembedding").lower()
    model = str(reranker_config.get("model") or "BAAI/bge-reranker-v2-m3")
    devices = str(reranker_config.get("devices") or "")
    cache_dir = str(reranker_config.get("cache_dir") or "")
    failure_key = (provider, model, devices, cache_dir)
    if failure_key in _RERANKER_FAILURES:
        return {
            "available": False,
            "provider": provider,
            "model": model,
            "reason": _RERANKER_FAILURES[failure_key],
        }
    top_k = int(reranker_config.get("top_k") or default_top_k or 3)
    if bool(reranker_config.get("dynamic_top_k", True)):
        top_k = _target_k(query, top_k)
    top_k = max(1, min(top_k, len(docs)))

    try:
        if provider == "vllm":
            scores = _score_with_vllm(query, docs, reranker_config, model)
        else:
            provider = "flagembedding"
            scores = _score_with_flagembedding(query, docs, reranker_config, model)
    except Exception as exc:  # pragma: no cover - depends on optional local services/models.
        reason = f"Reranker unavailable: {type(exc).__name__}: {exc}"
        _RERANKER_FAILURES[failure_key] = reason
        return {
            "available": False,
            "provider": provider,
            "model": model,
            "reason": reason,
        }

    ranked = []
    recency_values = [_memory_sort_value(memory_id, index) for index, memory_id in enumerate(ids)]
    for index, score in enumerate(scores):
        memory_id = ids[index]
        if not memory_id:
            continue
        component_scores = _rank_component_scores(
            query=query,
            candidate=candidates[index],
            doc=docs[index],
            bge_score=float(score),
            recency_values=recency_values,
            index=index,
            config=reranker_config,
        )
        ranked.append(
            {
                "id": memory_id,
                "score": round(float(component_scores["final_score"]), 6),
                "bge_score": round(float(score), 6),
                "lexical_score": round(float(component_scores["lexical_score"]), 6),
                "recency_score": round(float(component_scores["recency_score"]), 6),
                "repair_score": round(float(component_scores["repair_score"]), 6),
                "path_score": round(float(component_scores["path_score"]), 6),
                "prepared_order_score": round(float(component_scores["prepared_order_score"]), 6),
                "generic_penalty": round(float(component_scores["generic_penalty"]), 6),
                "summary": truncate(docs[index], 220),
                "rank": 0,
            }
        )
    ranked.sort(key=lambda item: item["score"], reverse=True)
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index

    if not ranked:
        return {
            "available": False,
            "provider": provider,
            "model": model,
            "reason": "Reranker returned no valid scores.",
        }

    min_score = reranker_config.get("min_score")
    if min_score is None:
        selected = ranked[:top_k]
    else:
        selected = [item for item in ranked if float(item["score"]) >= float(min_score)][:top_k]
    if not selected and bool(reranker_config.get("select_best_if_empty", True)):
        selected = ranked[:1]

    return {
        "available": True,
        "provider": provider,
        "model": model,
        "top_k": top_k,
        "selected_memory_ids": [str(item["id"]) for item in selected],
        "scores": ranked,
        "reason": "Hybrid reranker scored each prepared memory against the actual query.",
    }


def _reranker_config(config: Mapping[str, Any]) -> Dict[str, Any]:
    values = dict(config.get("reranker") or {})
    env_required = os.getenv("RERANKER_REQUIRE_AVAILABLE")
    if env_required is not None:
        values["require_available"] = env_required.lower() not in {"0", "false", "no"}
    values.setdefault("enabled", True)
    values.setdefault("require_available", False)
    values.setdefault("provider", "flagembedding")
    values.setdefault("model", "BAAI/bge-reranker-v2-m3")
    values.setdefault("top_k", int(os.getenv("RERANKER_TOP_K", "3")))
    env_dynamic_top_k = os.getenv("RERANKER_DYNAMIC_TOP_K")
    if env_dynamic_top_k is not None:
        values["dynamic_top_k"] = env_dynamic_top_k.lower() not in {"0", "false", "no"}
    values.setdefault("dynamic_top_k", False)
    values.setdefault("normalize", True)
    values["devices"] = _normalize_devices(str(values.get("devices") or "cuda:1"))
    values.setdefault("use_fp16", str(values.get("devices")).lower() not in {"cpu", "['cpu']"})
    values.setdefault("cache_dir", os.getenv("RERANKER_CACHE_DIR", "/home/yanghaotong/models/cache"))
    values.setdefault("batch_size", 16)
    values.setdefault("max_length", 512)
    values.setdefault("select_best_if_empty", True)
    values.setdefault("prefer_for_verifier", True)
    values.setdefault("hybrid", True)
    values.setdefault("bge_weight", 0.62)
    values.setdefault("lexical_weight", 0.18)
    values.setdefault("recency_weight", 0.06)
    values.setdefault("repair_weight", 0.08)
    values.setdefault("path_weight", 0.04)
    values.setdefault("prepared_order_weight", 0.10)
    values.setdefault("generic_penalty_weight", 0.10)
    values.setdefault("timeout", 30)
    return values


def _candidate_text(candidate: Mapping[str, Any]) -> str:
    return " ".join(
        str(candidate.get(key) or "")
        for key in ["summary", "content", "source_turn_id", "path_id"]
    ).strip()


def _rank_component_scores(
    query: str,
    candidate: Mapping[str, Any],
    doc: str,
    bge_score: float,
    recency_values: Sequence[float],
    index: int,
    config: Mapping[str, Any],
) -> Dict[str, float]:
    if not bool(config.get("hybrid", True)):
        return {
            "final_score": bge_score,
            "lexical_score": 0.0,
            "recency_score": 0.0,
            "repair_score": 0.0,
            "path_score": 0.0,
            "prepared_order_score": 0.0,
            "generic_penalty": 0.0,
        }

    lexical_score = _query_term_coverage(query, doc)
    recency_score = _normalize_position(recency_values, index)
    repair_score = _bounded_float(candidate.get("repair_score"), 0.0)
    path_score = 0.0
    if candidate.get("path_id"):
        path_score += 0.45
    if candidate.get("evidence_ids"):
        path_score += 0.35
    if candidate.get("source") == "repair_evidence":
        path_score += 0.20
    path_score = min(1.0, path_score)
    prepared_order_score = _prepared_order_score(index, len(recency_values))
    generic_penalty = _generic_memory_penalty(doc)

    final_score = (
        float(config.get("bge_weight", 0.62)) * bge_score
        + float(config.get("lexical_weight", 0.18)) * lexical_score
        + float(config.get("recency_weight", 0.06)) * recency_score
        + float(config.get("repair_weight", 0.08)) * repair_score
        + float(config.get("path_weight", 0.04)) * path_score
        + float(config.get("prepared_order_weight", 0.10)) * prepared_order_score
        - float(config.get("generic_penalty_weight", 0.10)) * generic_penalty
    )
    return {
        "final_score": final_score,
        "lexical_score": lexical_score,
        "recency_score": recency_score,
        "repair_score": repair_score,
        "path_score": path_score,
        "prepared_order_score": prepared_order_score,
        "generic_penalty": generic_penalty,
    }


_FOCUS_STOPWORDS = {
    "the",
    "and",
    "for",
    "that",
    "this",
    "with",
    "you",
    "your",
    "are",
    "was",
    "were",
    "have",
    "has",
    "had",
    "but",
    "not",
    "about",
    "from",
    "they",
    "them",
    "then",
    "there",
    "what",
    "when",
    "where",
    "why",
    "how",
    "who",
    "did",
    "does",
    "can",
    "could",
    "would",
    "should",
    "into",
    "onto",
    "too",
    "very",
    "really",
    "just",
    "like",
    "also",
    "their",
    "his",
    "her",
    "him",
    "she",
    "he",
    "its",
    "our",
    "will",
    "been",
    "being",
    "than",
}


def _focus_tokens(text: str) -> List[str]:
    return [
        token
        for token in tokenize(text)
        if len(token) >= 3 and token not in _FOCUS_STOPWORDS
    ]


def _query_term_coverage(query: str, doc: str) -> float:
    query_terms = set(_focus_tokens(query))
    if not query_terms:
        return 0.0
    doc_terms = set(_focus_tokens(doc))
    return len(query_terms & doc_terms) / len(query_terms)


def _memory_sort_value(memory_id: str, fallback_index: int) -> float:
    parts = str(memory_id or "").rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return float(parts[1])
    digits = "".join(ch for ch in str(memory_id or "") if ch.isdigit())
    if digits:
        return float(digits)
    return float(fallback_index)


def _normalize_position(values: Sequence[float], index: int) -> float:
    if index < 0 or index >= len(values):
        return 0.0
    low = min(values) if values else 0.0
    high = max(values) if values else 0.0
    if high <= low:
        return 0.0
    return (values[index] - low) / (high - low)


def _prepared_order_score(index: int, count: int) -> float:
    if count <= 1:
        return 1.0
    return 1.0 - min(max(index, 0), count - 1) / float(count - 1)


def _generic_memory_penalty(doc: str) -> float:
    tokens = _focus_tokens(doc)
    if not tokens:
        return 1.0
    penalty = 0.0
    if len(tokens) <= 5:
        penalty += 0.35
    generic_terms = {
        "hello",
        "hey",
        "hi",
        "okay",
        "thanks",
        "thank",
        "great",
        "good",
        "nice",
        "sounds",
        "chat",
        "talk",
        "today",
        "hope",
    }
    generic_hits = len(set(tokens) & generic_terms)
    if generic_hits:
        penalty += min(0.45, generic_hits * 0.15)
    if len(set(tokens)) <= 3:
        penalty += 0.20
    return min(1.0, penalty)


def _bounded_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return max(0.0, min(1.0, number))


def _normalize_devices(devices: str) -> str:
    value = devices.strip()
    if value.isdigit():
        return f"cuda:{value}"
    if "," in value:
        return ",".join(_normalize_devices(item) for item in value.split(",") if item.strip())
    return value


def _flagembedding_devices_arg(devices: str) -> Any:
    value = _normalize_devices(devices)
    if not value:
        return None
    if "," in value:
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def _resolve_flagembedding_model(model: str, cache_dir: str) -> str:
    model_path = Path(model).expanduser()
    if model_path.exists():
        return str(model_path)
    if not cache_dir or "/" not in model:
        return model

    snapshots_dir = (
        Path(cache_dir).expanduser()
        / "hub"
        / f"models--{model.replace('/', '--')}"
        / "snapshots"
    )
    if not snapshots_dir.exists():
        return model

    candidates = [
        item
        for item in snapshots_dir.iterdir()
        if item.is_dir() and (item / "config.json").exists()
    ]
    if not candidates:
        return model
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return str(candidates[0])


def _score_with_flagembedding(
    query: str,
    docs: Sequence[str],
    reranker_config: Mapping[str, Any],
    model: str,
) -> List[float]:
    try:
        from FlagEmbedding import FlagReranker  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional dependency.
        raise RuntimeError("Install FlagEmbedding to use BAAI/bge-reranker-v2-m3.") from exc

    use_fp16 = bool(reranker_config.get("use_fp16", True))
    devices = _normalize_devices(str(reranker_config.get("devices") or ""))
    cache_dir = str(reranker_config.get("cache_dir") or "")
    model_arg = _resolve_flagembedding_model(model, cache_dir)
    batch_size = int(reranker_config.get("batch_size") or 16)
    max_length = int(reranker_config.get("max_length") or 512)
    cache_key = (model_arg, use_fp16, devices, cache_dir, batch_size, max_length)
    reranker = _FLAG_RERANKERS.get(cache_key)
    if reranker is None:
        reranker = FlagReranker(
            model_arg,
            use_fp16=use_fp16,
            devices=_flagembedding_devices_arg(devices),
            cache_dir=cache_dir or None,
            batch_size=batch_size,
            max_length=max_length,
        )
        _FLAG_RERANKERS[cache_key] = reranker

    pairs = [[query, doc] for doc in docs]
    normalize = bool(reranker_config.get("normalize", True))
    try:
        raw_scores = reranker.compute_score(pairs, normalize=normalize)
    except TypeError:
        raw_scores = reranker.compute_score(pairs)

    if hasattr(raw_scores, "tolist"):
        raw_scores = raw_scores.tolist()
    if not isinstance(raw_scores, list):
        raw_scores = [raw_scores]
    scores = [float(item) for item in raw_scores]
    if normalize or not scores:
        return scores
    return [_sigmoid(score) for score in scores]


def _score_with_vllm(
    query: str,
    docs: Sequence[str],
    reranker_config: Mapping[str, Any],
    model: str,
) -> List[float]:
    base_url = str(reranker_config.get("base_url") or "http://127.0.0.1:30001").rstrip("/")
    url = base_url + "/rerank"
    payload = {
        "model": model,
        "query": query,
        "documents": list(docs),
        "top_n": len(docs),
    }
    timeout = float(reranker_config.get("timeout") or 30)
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:  # pragma: no cover - optional service.
        raise RuntimeError(f"Cannot reach reranker service at {url}") from exc

    scores = [0.0 for _ in docs]
    results = data.get("results") or data.get("data") or []
    for item in results:
        index = int(item.get("index", item.get("document_index", -1)))
        if index < 0 or index >= len(scores):
            continue
        score = item.get("relevance_score", item.get("score", item.get("logit", 0.0)))
        scores[index] = float(score)
    return scores


def _target_k(query: str, top_k: int) -> int:
    lower = str(query or "").lower().strip()
    multi_markers = [
        "fields",
        "examples",
        "reasons",
        "things",
        "relationship status",
        "identity",
        "support system",
    ]
    if any(marker in lower for marker in multi_markers):
        return min(top_k, 2)
    single_fact_prefixes = (
        "when did",
        "when is",
        "where did",
        "where is",
        "who did",
        "who is",
        "what did",
        "what is",
    )
    if lower.startswith(single_fact_prefixes):
        return 1
    return min(top_k, 2)


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)
