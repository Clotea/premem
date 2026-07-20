#!/usr/bin/env python3
"""Minimal pre-query future-memory prediction demo.

The predictor can see only the history and memory graph available before the
next query. The hidden next query is revealed only after ranking for evaluation.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any


EDGE_WEIGHTS = {
    "next_access": 0.85,
    "co_access": 0.65,
    "constrains": 0.55,
    "compared_by_distance": 0.55,
    "located_at": 0.45,
    "precedes": 0.35,
    "participates_in": 0.25,
}

UNDIRECTED_EDGE_TYPES = {
    "co_access",
    "constrains",
    "compared_by_distance",
    "located_at",
    "participates_in",
}


def load_scenario(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    required = {
        "history",
        "memory_nodes",
        "memory_edges",
        "hidden_next_query",
        "gold_memory_ids",
    }
    missing = required.difference(data)
    if missing:
        raise ValueError(f"Scenario is missing fields: {sorted(missing)}")
    return data


def visible_scenario(data: dict[str, Any]) -> dict[str, Any]:
    """Return the only information the predictor is allowed to observe."""
    return {
        "history": data["history"],
        "memory_nodes": data["memory_nodes"],
        "memory_edges": data["memory_edges"],
    }


def build_prompt(visible: dict[str, Any]) -> str:
    history = "\n".join(
        f'{turn["role"]}: {turn["content"]}' for turn in visible["history"]
    )
    catalog = "\n".join(
        f'- {node["id"]} [{node["type"]}] {node["label"]}: {node["content"]}'
        for node in visible["memory_nodes"]
    )
    return f"""你是一个pre-query agent memory预测器。
现在处于上一轮回复结束后的idle time，真实的下一条用户query尚未到达。
只能根据截至目前的对话历史和已有memory，预测下一条query可能涉及的需求。

对话历史：
{history}

可用memory目录：
{catalog}

请给出最多3个未来需求假设。每个假设包含：
- intent：未来需求
- pseudo_query：可能的下一条query
- probability：0到1的概率，所有概率之和不超过1
- entities：可能涉及的实体或主题
- seed_memory_ids：最可能需要的memory id

不要假装已经看到真实的下一条query。只输出JSON。"""


def extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"Model did not return a JSON object: {text[:200]}")
    return json.loads(text[start : end + 1])


def predict_with_ollama(
    visible: dict[str, Any], model: str, endpoint: str
) -> dict[str, Any]:
    schema = {
        "type": "object",
        "properties": {
            "predictions": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "intent": {"type": "string"},
                        "pseudo_query": {"type": "string"},
                        "probability": {"type": "number"},
                        "entities": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "seed_memory_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "intent",
                        "pseudo_query",
                        "probability",
                        "entities",
                        "seed_memory_ids",
                    ],
                },
            }
        },
        "required": ["predictions"],
    }
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "format": schema,
        "messages": [{"role": "user", "content": build_prompt(visible)}],
        "options": {"temperature": 0.2, "seed": 7},
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach Ollama at {endpoint}: {exc}") from exc
    return extract_json(result["message"]["content"])


def normalize_text(text: str) -> str:
    return "".join(re.findall(r"[\w\u4e00-\u9fff]+", text.lower()))


def ngrams(text: str, n: int = 2) -> set[str]:
    normalized = normalize_text(text)
    if len(normalized) < n:
        return {normalized} if normalized else set()
    return {normalized[i : i + n] for i in range(len(normalized) - n + 1)}


def lexical_similarity(left: str, right: str) -> float:
    a, b = ngrams(left), ngrams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / math.sqrt(len(a) * len(b))


def heuristic_prediction(visible: dict[str, Any]) -> dict[str, Any]:
    """Offline fallback. It is intentionally simple and acts as a baseline."""
    recent = " ".join(turn["content"] for turn in visible["history"][-3:])
    scored = sorted(
        (
            (
                lexical_similarity(
                    recent, f'{node["label"]} {node["content"]}'
                ),
                node["id"],
                node["label"],
            )
            for node in visible["memory_nodes"]
        ),
        reverse=True,
    )
    seeds = [item[1] for item in scored[:3]]
    labels = [item[2] for item in scored[:3]]
    return {
        "predictions": [
            {
                "intent": f"继续讨论最近出现的主题：{'、'.join(labels)}",
                "pseudo_query": recent[-120:],
                "probability": 0.7,
                "entities": labels,
                "seed_memory_ids": seeds,
            }
        ]
    }


def validate_predictions(
    prediction: dict[str, Any], valid_memory_ids: set[str]
) -> list[dict[str, Any]]:
    raw_items = prediction.get("predictions", [])
    if not raw_items and isinstance(prediction.get("intent"), list):
        intents = prediction.get("intent", [])
        pseudo_queries = prediction.get("pseudo_query", [])
        probabilities = prediction.get("probability", [])
        entities = prediction.get("entities", [])
        seeds = prediction.get("seed_memory_ids", [])
        raw_items = []
        for index, intent in enumerate(intents[:3]):
            raw_items.append(
                {
                    "intent": intent,
                    "pseudo_query": (
                        pseudo_queries[index]
                        if index < len(pseudo_queries)
                        else ""
                    ),
                    "probability": (
                        probabilities[index]
                        if index < len(probabilities)
                        else 0.0
                    ),
                    "entities": entities,
                    "seed_memory_ids": seeds if index == 0 else [],
                }
            )

    cleaned: list[dict[str, Any]] = []
    for item in raw_items[:3]:
        probability = min(1.0, max(0.0, float(item.get("probability", 0.0))))
        seeds = [
            memory_id
            for memory_id in item.get("seed_memory_ids", [])
            if memory_id in valid_memory_ids
        ]
        cleaned.append(
            {
                "intent": str(item.get("intent", "")),
                "pseudo_query": str(item.get("pseudo_query", "")),
                "probability": probability,
                "entities": [str(x) for x in item.get("entities", [])],
                "seed_memory_ids": seeds,
            }
        )
    if not cleaned:
        raise ValueError("Predictor returned no valid future-need hypotheses")
    total = sum(item["probability"] for item in cleaned)
    if total > 1.0:
        for item in cleaned:
            item["probability"] /= total
    return cleaned


def rank_graph(
    visible: dict[str, Any],
    predictions: list[dict[str, Any]],
    propagation_steps: int = 2,
) -> list[dict[str, Any]]:
    nodes = {node["id"]: node for node in visible["memory_nodes"]}
    adjacency: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for edge in visible["memory_edges"]:
        source, target, edge_type = edge["source"], edge["target"], edge["type"]
        adjacency[source].append((target, edge_type))
        if edge_type in UNDIRECTED_EDGE_TYPES:
            adjacency[target].append((source, edge_type))

    direct = defaultdict(float)
    reasons: dict[str, list[str]] = defaultdict(list)
    for hypothesis in predictions:
        probability = hypothesis["probability"]
        prediction_text = " ".join(
            [
                hypothesis["intent"],
                hypothesis["pseudo_query"],
                *hypothesis["entities"],
            ]
        )
        for memory_id in hypothesis["seed_memory_ids"]:
            direct[memory_id] += probability
            reasons[memory_id].append(f"LLM seed ({probability:.2f})")
        for memory_id, node in nodes.items():
            similarity = lexical_similarity(
                prediction_text, f'{node["label"]} {node["content"]}'
            )
            if similarity:
                direct[memory_id] += 0.45 * probability * similarity
                reasons[memory_id].append(f"semantic={similarity:.2f}")

    scores = defaultdict(float, direct)
    frontier = dict(direct)
    for step in range(propagation_steps):
        next_frontier = defaultdict(float)
        for source, source_score in frontier.items():
            for target, edge_type in adjacency.get(source, []):
                contribution = (
                    source_score
                    * EDGE_WEIGHTS.get(edge_type, 0.2)
                    * (0.45 ** (step + 1))
                )
                next_frontier[target] += contribution
                if contribution >= 0.02:
                    reasons[target].append(
                        f"{edge_type} from {source} (+{contribution:.2f})"
                    )
        for memory_id, contribution in next_frontier.items():
            scores[memory_id] += contribution
        frontier = next_frontier

    return sorted(
        (
            {
                "memory_id": memory_id,
                "type": nodes[memory_id]["type"],
                "label": nodes[memory_id]["label"],
                "score": round(score, 4),
                "load_ms": nodes[memory_id]["load_ms"],
                "reasons": reasons[memory_id],
            }
            for memory_id, score in scores.items()
        ),
        key=lambda item: (-item["score"], item["memory_id"]),
    )


def simulate_prefetch(
    ranked: list[dict[str, Any]],
    top_k: int,
    pre_query_window_ms: float,
    prediction_ms: float,
) -> tuple[list[str], float]:
    remaining_ms = max(0.0, pre_query_window_ms - prediction_ms)
    ready: list[str] = []
    spent_ms = 0.0
    for item in ranked[:top_k]:
        if spent_ms + item["load_ms"] > remaining_ms:
            break
        spent_ms += item["load_ms"]
        ready.append(item["memory_id"])
    return ready, remaining_ms


def evaluate(
    data: dict[str, Any],
    ranked: list[dict[str, Any]],
    ready_ids: list[str],
    top_k: int,
) -> dict[str, float]:
    gold = set(data["gold_memory_ids"])
    selected = {item["memory_id"] for item in ranked[:top_k]}
    ready = set(ready_ids)
    nodes = {node["id"]: node for node in data["memory_nodes"]}
    total_gold_cost = sum(nodes[memory_id]["load_ms"] for memory_id in gold)
    selected_gold_cost = sum(
        nodes[memory_id]["load_ms"] for memory_id in gold & selected
    )
    ready_gold_cost = sum(
        nodes[memory_id]["load_ms"] for memory_id in gold & ready
    )
    return {
        "precision_at_k": len(gold & selected) / max(1, len(selected)),
        "recall_at_k": len(gold & selected) / max(1, len(gold)),
        "ready_recall_at_query": len(gold & ready) / max(1, len(gold)),
        "utility_weighted_recall": selected_gold_cost / max(1, total_gold_cost),
        "simulated_hidden_latency_ratio": ready_gold_cost
        / max(1, total_gold_cost),
    }


def parse_args() -> argparse.Namespace:
    default_scenario = Path(__file__).with_name("scenario.json")
    parser = argparse.ArgumentParser(
        description="Predict future memory before revealing the next query."
    )
    parser.add_argument("--scenario", type=Path, default=default_scenario)
    parser.add_argument(
        "--provider", choices=("ollama", "heuristic"), default="ollama"
    )
    parser.add_argument("--model", default="qwen3.5:2b")
    parser.add_argument(
        "--ollama-endpoint", default="http://127.0.0.1:11434/api/chat"
    )
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--pre-query-window-ms", type=float, default=15_000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data = load_scenario(args.scenario)
    visible = visible_scenario(data)
    valid_ids = {node["id"] for node in visible["memory_nodes"]}

    prediction_start = time.perf_counter()
    if args.provider == "ollama":
        raw_prediction = predict_with_ollama(
            visible, args.model, args.ollama_endpoint
        )
    else:
        raw_prediction = heuristic_prediction(visible)
    llm_prediction_ms = (time.perf_counter() - prediction_start) * 1000
    predictions = validate_predictions(raw_prediction, valid_ids)

    graph_start = time.perf_counter()
    ranked = rank_graph(visible, predictions)
    graph_prediction_ms = (time.perf_counter() - graph_start) * 1000
    total_prediction_ms = llm_prediction_ms + graph_prediction_ms
    ready_ids, remaining_ms = simulate_prefetch(
        ranked,
        top_k=args.top_k,
        pre_query_window_ms=args.pre_query_window_ms,
        prediction_ms=total_prediction_ms,
    )

    metrics = evaluate(data, ranked, ready_ids, args.top_k)
    result = {
        "protocol": {
            "predictor_observed_hidden_query": False,
            "provider": args.provider,
            "model": args.model if args.provider == "ollama" else None,
            "top_k": args.top_k,
            "pre_query_window_ms": args.pre_query_window_ms,
        },
        "timing_ms": {
            "llm_prediction": round(llm_prediction_ms, 2),
            "graph_ranking": round(graph_prediction_ms, 2),
            "total_prediction": round(total_prediction_ms, 2),
            "remaining_for_prefetch": round(remaining_ms, 2),
        },
        "future_need_predictions": predictions,
        "ranked_memory": ranked[: args.top_k],
        "ready_at_query": ready_ids,
        "revealed_after_prediction": {
            "hidden_next_query": data["hidden_next_query"],
            "gold_memory_ids": data["gold_memory_ids"],
        },
        "metrics": {key: round(value, 4) for key, value in metrics.items()},
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
