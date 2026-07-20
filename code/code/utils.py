from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    from nltk.stem import PorterStemmer as _NltkPorterStemmer  # type: ignore
except ImportError:
    _NltkPorterStemmer = None

_OFFICIAL_PORTER = _NltkPorterStemmer() if _NltkPorterStemmer is not None else None


STOPWORDS = {
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
    "then",
}


@dataclass
class Turn:
    id: str
    timestamp: int
    speaker: str
    segment_id: str
    segment_summary: str
    text: str
    memories: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Turn":
        return cls(
            id=str(raw.get("id") or raw.get("dia_id") or ""),
            timestamp=int(raw.get("timestamp") or 0),
            speaker=str(raw.get("speaker") or "unknown"),
            segment_id=str(raw.get("segment_id") or "seg_default"),
            segment_summary=str(raw.get("segment_summary") or ""),
            text=str(raw.get("text") or ""),
            memories=list(raw.get("memories") or []),
            metadata=dict(raw.get("metadata") or {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryNode:
    id: str
    memory_type: str
    content: str
    summary: str
    keywords: List[str]
    entities: List[str]
    segment_id: str
    source_turn_id: str
    timestamp: int
    importance: float = 0.5
    node_type: str = "MemoryNode"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def searchable_text(self) -> str:
        parts = [
            self.content,
            self.summary,
            " ".join(self.keywords or []),
            " ".join(self.entities or []),
        ]
        return " ".join(part for part in parts if part)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Sample:
    id: str
    history: List[Turn]
    question: str
    answer: str
    evidence_terms: List[str] = field(default_factory=list)
    gold_evidence_turn_ids: List[str] = field(default_factory=list)
    history_cache_key: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Sample":
        return cls(
            id=str(raw.get("id") or "sample"),
            history=[Turn.from_dict(item) for item in raw.get("history", [])],
            question=str(raw.get("question") or ""),
            answer=str(raw.get("answer") or raw.get("adversarial_answer") or ""),
            evidence_terms=list(raw.get("evidence_terms") or []),
            gold_evidence_turn_ids=list(raw.get("gold_evidence_turn_ids") or []),
            history_cache_key=raw.get("history_cache_key"),
            metadata=dict(raw.get("metadata") or {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "history": [turn.to_dict() for turn in self.history],
            "question": self.question,
            "answer": self.answer,
            "evidence_terms": self.evidence_terms,
            "gold_evidence_turn_ids": self.gold_evidence_turn_ids,
            "history_cache_key": self.history_cache_key,
            "metadata": self.metadata,
        }


@dataclass
class ActivatedMemory:
    id: str
    reason: str = ""
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Prediction:
    predicted_future_intents: List[str]
    activated_memory_ids: List[ActivatedMemory]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "predicted_future_intents": self.predicted_future_intents,
            "activated_memory_ids": [item.to_dict() for item in self.activated_memory_ids],
            "metadata": self.metadata,
        }


@dataclass
class WorkingCache:
    cache_id: str
    budget: int
    memory_ids: List[str]
    summaries: List[str]
    local_subgraph: Dict[str, Any]
    prediction: Prediction
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cache_id": self.cache_id,
            "budget": self.budget,
            "memory_ids": self.memory_ids,
            "summaries": self.summaries,
            "local_subgraph": self.local_subgraph,
            "prediction": self.prediction.to_dict(),
            "metadata": self.metadata,
        }


def load_json(path: Path | str) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path | str, data: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def tokenize(text: Any) -> List[str]:
    value = str(text or "").lower()
    value = re.sub(r"[^a-z0-9=\-]+", " ", value)
    return [token for token in value.strip().split() if token]


def unique(items: Iterable[Any]) -> List[Any]:
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    a = set(left)
    b = set(right)
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def overlap_score(query: str, memory: MemoryNode) -> float:
    return jaccard(tokenize(query), tokenize(memory.searchable_text()))


def estimate_tokens(text: Any) -> int:
    return max(1, len(tokenize(text)))


def extract_keywords(text: Any, limit: int = 10) -> List[str]:
    counts: Dict[str, int] = {}
    for token in tokenize(text):
        if len(token) < 3 or token in STOPWORDS:
            continue
        counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [token for token, _count in ranked[:limit]]


def extract_entities(text: Any, limit: int = 8) -> List[str]:
    matches = re.findall(r"\b[A-Z][a-zA-Z0-9_\-]{2,}\b", str(text or ""))
    return unique(matches)[:limit]


def infer_memory_type(text: Any) -> str:
    lower = str(text or "").lower()
    if "i like" in lower or "i love" in lower or "favorite" in lower or "prefer" in lower:
        return "preference"
    if "will" in lower or "plan" in lower or "going to" in lower or "need to" in lower:
        return "task"
    if re.search(r"\b(yesterday|today|tomorrow|week|month|year|20\d\d)\b", lower):
        return "event"
    return "fact"


def estimate_importance(text: Any) -> float:
    keyword_count = len(extract_keywords(text, 20))
    return min(0.95, 0.35 + keyword_count * 0.03)


def truncate(text: Any, max_length: int = 180) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= max_length:
        return value
    return value[: max(0, max_length - 3)] + "..."


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or isinstance(value, bool):
            return default
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def parse_llm_json(text: str) -> Dict[str, Any]:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?", "", value, flags=re.IGNORECASE).strip()
        value = re.sub(r"```$", "", value).strip()
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start >= 0 and end > start:
            parsed = json.loads(value[start : end + 1])
            return parsed if isinstance(parsed, dict) else {}
        raise


def seeded_pick(items: Sequence[str], count: int, seed: int) -> List[str]:
    ranked: List[Tuple[float, str]] = []
    for index, item in enumerate(items):
        ranked.append((math.sin(seed + index * 997), item))
    ranked.sort(key=lambda entry: entry[0])
    return [item for _key, item in ranked[:count]]


def compute_activation_metrics(activated: Sequence[str], evidence: Sequence[str]) -> Dict[str, float]:
    activated_set = set(activated)
    evidence_set = set(evidence)
    intersection = activated_set & evidence_set
    precision = len(intersection) / len(activated_set) if activated_set else 0.0
    recall = len(intersection) / len(evidence_set) if evidence_set else 0.0
    full_cover = bool(evidence_set) and evidence_set <= activated_set
    return {
        "precision": precision,
        "recall": recall,
        "hit_rate": 1.0 if intersection else 0.0,
        "full_cover_rate": 1.0 if full_cover else 0.0,
        "wasted_rate": 1.0 - precision,
    }


def f1_score(prediction: str, gold: str) -> float:
    pred = tokenize(prediction)
    ref = tokenize(gold)
    if not pred or not ref:
        return 0.0
    pred_counts = _counts(pred)
    ref_counts = _counts(ref)
    overlap = 0
    for token, count in pred_counts.items():
        overlap += min(count, ref_counts.get(token, 0))
    if overlap == 0:
        return 0.0
    precision = overlap / len(pred)
    recall = overlap / len(ref)
    return (2 * precision * recall) / (precision + recall)


def locomo_answer_f1(prediction: str, gold: str, category: Any = None) -> float:
    """LoCoMo-style token F1 with category handling from the official evaluator."""
    try:
        category_id = int(category)
    except (TypeError, ValueError):
        category_id = 0

    answer = str(gold or "")
    if category_id == 3 and ";" in answer:
        answer = answer.split(";", 1)[0].strip()
    if category_id == 5:
        lowered = str(prediction or "").lower()
        if "no information available" in lowered or "not mentioned" in lowered:
            return 1.0
        return 0.0
    if category_id == 1:
        return _locomo_multi_f1(prediction, answer)
    return _locomo_single_f1(prediction, answer)


def bleu1_score(prediction: str, gold: str) -> float:
    pred = _locomo_tokens(prediction)
    ref = _locomo_tokens(gold)
    if not pred or not ref:
        return 0.0
    pred_counts = _counts(pred)
    ref_counts = _counts(ref)
    overlap = sum(min(count, ref_counts.get(token, 0)) for token, count in pred_counts.items())
    precision = overlap / len(pred)
    if len(pred) > len(ref):
        brevity_penalty = 1.0
    else:
        brevity_penalty = math.exp(1.0 - (len(ref) / max(1, len(pred))))
    return precision * brevity_penalty


def rouge_l(prediction: str, gold: str) -> float:
    pred = tokenize(prediction)
    ref = tokenize(gold)
    if not pred or not ref:
        return 0.0
    return _longest_common_subsequence(pred, ref) / len(ref)


def pseudo_judge(prediction: str, gold: str) -> float:
    return 0.5 * f1_score(prediction, gold) + 0.5 * rouge_l(prediction, gold)


def faithfulness(prediction: str, memories: Sequence[MemoryNode]) -> float:
    if not memories:
        return 0.0
    memory_text = " ".join(memory.summary for memory in memories)
    return f1_score(prediction, memory_text)


def average(rows: Sequence[Mapping[str, Any]], key: str) -> float:
    if not rows:
        return 0.0
    return sum(safe_float(row.get(key), 0.0) for row in rows) / len(rows)


def average_present(rows: Sequence[Mapping[str, Any]], key: str) -> Optional[float]:
    values = [
        safe_float(row.get(key))
        for row in rows
        if row.get(key) is not None
    ]
    return sum(values) / len(values) if values else None


def estimate_cost(
    history: Sequence[Turn],
    query: str,
    memories: Sequence[MemoryNode],
    generated_answer: str,
    idle_prediction_used: bool,
    fallback_used: bool,
    idle_tokens: int = 0,
) -> Dict[str, float]:
    history_tokens = estimate_tokens(" ".join(turn.text for turn in history))
    memory_tokens = sum(estimate_tokens(memory.summary) for memory in memories)
    input_tokens = history_tokens + estimate_tokens(query) + memory_tokens
    output_tokens = estimate_tokens(generated_answer)
    idle_cost = idle_tokens if idle_tokens else (30 if idle_prediction_used else 0)
    return {
        "input_tokens": float(input_tokens),
        "output_tokens": float(output_tokens),
        "total_tokens": float(input_tokens + output_tokens + idle_cost),
        "idle_time_cost": float(idle_cost),
        "query_time_latency_ms": float(6 + len(memories) * 2 + (12 if fallback_used else 0)),
    }


def format_table(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> str:
    if not rows:
        return "(empty)"
    formatted_rows: List[Dict[str, str]] = []
    for row in rows:
        item = {}
        for column in columns:
            value = row.get(column, "")
            if isinstance(value, float):
                item[column] = f"{value:.3f}"
            else:
                item[column] = str(value)
        formatted_rows.append(item)
    widths = {
        column: max(len(column), *(len(row[column]) for row in formatted_rows))
        for column in columns
    }
    header = " | ".join(column.ljust(widths[column]) for column in columns)
    divider = "-+-".join("-" * widths[column] for column in columns)
    body = [
        " | ".join(row[column].ljust(widths[column]) for column in columns)
        for row in formatted_rows
    ]
    return "\n".join([header, divider, *body])


def _counts(tokens: Sequence[str]) -> Dict[str, int]:
    result: Dict[str, int] = {}
    for token in tokens:
        result[token] = result.get(token, 0) + 1
    return result


def _locomo_tokens(text: Any) -> List[str]:
    value = str(text or "").lower().replace(",", "")
    value = re.sub(r"[^a-z0-9\s]+", " ", value)
    value = re.sub(r"\b(a|an|the|and)\b", " ", value)
    return [
        _OFFICIAL_PORTER.stem(token) if _OFFICIAL_PORTER is not None else _porter_stem(token)
        for token in value.split()
        if token
    ]


def _porter_stem(word: str) -> str:
    """Compact Porter stemmer used by the official LoCoMo token-F1 protocol."""
    if len(word) <= 2:
        return word

    def consonant(index: int) -> bool:
        char = word[index]
        if char in "aeiou":
            return False
        if char == "y":
            return index == 0 or not consonant(index - 1)
        return True

    def measure(stem: str) -> int:
        nonlocal word
        old = word
        word = stem
        pattern = "".join("c" if consonant(i) else "v" for i in range(len(word)))
        word = old
        return len(re.findall(r"vc", pattern))

    def has_vowel(stem: str) -> bool:
        nonlocal word
        old = word
        word = stem
        result = any(not consonant(i) for i in range(len(word)))
        word = old
        return result

    def cvc(stem: str) -> bool:
        nonlocal word
        if len(stem) < 3:
            return False
        old = word
        word = stem
        result = (
            consonant(len(stem) - 3)
            and not consonant(len(stem) - 2)
            and consonant(len(stem) - 1)
            and stem[-1] not in "wxy"
        )
        word = old
        return result

    def replace(suffix: str, replacement: str, minimum: int = 0) -> bool:
        nonlocal word
        if word.endswith(suffix) and measure(word[: -len(suffix)]) > minimum:
            word = word[: -len(suffix)] + replacement
            return True
        return False

    if word.endswith("sses"):
        word = word[:-2]
    elif word.endswith("ies"):
        word = word[:-2]
    elif word.endswith("ss"):
        pass
    elif word.endswith("s"):
        word = word[:-1]

    changed = False
    if word.endswith("eed"):
        if measure(word[:-3]) > 0:
            word = word[:-1]
    elif word.endswith("ed") and has_vowel(word[:-2]):
        word, changed = word[:-2], True
    elif word.endswith("ing") and has_vowel(word[:-3]):
        word, changed = word[:-3], True
    if changed:
        if word.endswith(("at", "bl", "iz")):
            word += "e"
        elif len(word) >= 2 and word[-1] == word[-2] and word[-1] not in "lsz":
            word = word[:-1]
        elif measure(word) == 1 and cvc(word):
            word += "e"
    if word.endswith("y") and has_vowel(word[:-1]):
        word = word[:-1] + "i"

    for suffix, replacement in {
        "ational": "ate", "tional": "tion", "enci": "ence", "anci": "ance",
        "izer": "ize", "abli": "able", "alli": "al", "entli": "ent",
        "eli": "e", "ousli": "ous", "ization": "ize", "ation": "ate",
        "ator": "ate", "alism": "al", "iveness": "ive", "fulness": "ful",
        "ousness": "ous", "aliti": "al", "iviti": "ive", "biliti": "ble",
        "logi": "log",
    }.items():
        if replace(suffix, replacement):
            break
    for suffix, replacement in {
        "icate": "ic", "ative": "", "alize": "al", "iciti": "ic",
        "ical": "ic", "ful": "", "ness": "",
    }.items():
        if replace(suffix, replacement):
            break
    for suffix in (
        "al", "ance", "ence", "er", "ic", "able", "ible", "ant", "ement",
        "ment", "ent", "ion", "ou", "ism", "ate", "iti", "ous", "ive", "ize",
    ):
        if not word.endswith(suffix):
            continue
        stem = word[:-len(suffix)]
        if measure(stem) > 1 and (suffix != "ion" or stem.endswith(("s", "t"))):
            word = stem
        break
    if word.endswith("e"):
        stem = word[:-1]
        if measure(stem) > 1 or (measure(stem) == 1 and not cvc(stem)):
            word = stem
    if word.endswith("ll") and measure(word) > 1:
        word = word[:-1]
    return word


def _locomo_single_f1(prediction: str, gold: str) -> float:
    pred = _locomo_tokens(prediction)
    ref = _locomo_tokens(gold)
    if not pred or not ref:
        return 0.0
    pred_counts = _counts(pred)
    ref_counts = _counts(ref)
    overlap = sum(min(count, ref_counts.get(token, 0)) for token, count in pred_counts.items())
    if overlap == 0:
        return 0.0
    precision = overlap / len(pred)
    recall = overlap / len(ref)
    return (2 * precision * recall) / (precision + recall)


def _locomo_multi_f1(prediction: str, gold: str) -> float:
    predictions = [item.strip() for item in str(prediction or "").split(",") if item.strip()]
    golds = [item.strip() for item in str(gold or "").split(",") if item.strip()]
    if not predictions:
        predictions = [str(prediction or "")]
    if not golds:
        golds = [str(gold or "")]
    return sum(max(_locomo_single_f1(pred, ref) for pred in predictions) for ref in golds) / len(golds)


def _longest_common_subsequence(left: Sequence[str], right: Sequence[str]) -> int:
    previous = [0] * (len(right) + 1)
    for left_token in left:
        current = [0] * (len(right) + 1)
        for index, right_token in enumerate(right, start=1):
            if left_token == right_token:
                current[index] = previous[index - 1] + 1
            else:
                current[index] = max(previous[index], current[index - 1])
        previous = current
    return previous[-1]
