from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


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
    """一轮原始对话。

    Attributes:
        id: 对话轮次 id，LoCoMo 中通常来自 dia_id。
        timestamp: 对话在当前历史中的顺序时间戳，越大表示越新。
        speaker: 当前轮说话人名称。
        segment_id: 当前轮所属的会话片段 id，例如某个 session。
        segment_summary: 当前会话片段的摘要、日期或简短描述。
        text: 当前轮的原始文本内容。
        memories: 从当前轮预先抽取出的 memory 原始字典列表；为空时由 memory_writer 自动生成。
    """

    id: str
    timestamp: int
    speaker: str
    segment_id: str
    segment_summary: str
    text: str
    memories: List[Dict[str, Any]] = field(default_factory=list)

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
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryNode:
    """写入记忆库后的单条记忆节点。

    Attributes:
        id: memory 节点 id，例如 m_001。
        memory_type: 记忆类型，例如 fact、preference、task、event。
        content: 记忆的完整内容，通常包含 speaker 和原始文本。
        summary: 记忆摘要，用于 cache、检索上下文和答案生成。
        keywords: 从记忆内容中抽取的关键词列表。
        entities: 从记忆内容中抽取的实体列表。
        segment_id: 这条记忆所属的会话片段 id。
        source_turn_id: 这条记忆来源的 Turn.id。
        timestamp: 来源对话轮的时间戳，用于 recency 排序。
        importance: 记忆重要性分数，当前是启发式估计值。
        node_type: 图中的节点类型标记，默认是 MemoryNode。
        metadata: 额外元数据，给后续实验扩展使用。
    """

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
    """一个评测样本，包含历史、问题和标准答案。

    Attributes:
        id: 样本 id。
        history: 回答问题前可见的历史对话轮列表。
        question: 当前需要回答的用户问题。
        answer: 标准答案或参考答案。
        evidence_terms: 用于弱标注 gold evidence 的关键词或短语。
        gold_evidence_turn_ids: 标准证据所在的原始 Turn.id 列表。
        history_cache_key: 可复用历史上下文的缓存 key；相同 key 的样本共享 memory/graph/cache。
        metadata: 数据集来源、类别等额外信息。
    """

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
    """预测器决定提前激活的一条 memory。

    Attributes:
        id: 被激活的 memory id。
        reason: 为什么预测下一轮可能需要这条 memory。
        confidence: 激活置信度，范围通常压到 0.01 到 0.99。
    """

    id: str
    reason: str = ""
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FutureNeedHypothesis:
    """A query-independent hypothesis about a likely future information need."""

    id: str
    intent: str
    rationale: str = ""
    confidence: float = 0.5
    search_queries: List[str] = field(default_factory=list)
    evidence_requirements: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GroundedMemory:
    """A hypothesis-to-memory grounding decision."""

    hypothesis_id: str
    memory_id: str
    score: float
    reason: str = ""
    method: str = "embedding"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryGap:
    """A predicted need that is not sufficiently covered by the memory graph."""

    hypothesis_id: str
    exists: bool
    reason: str
    max_grounding_score: float = 0.0
    search_queries: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExternalEvidence:
    """Web or paper evidence acquired to fill a predicted memory gap."""

    id: str
    hypothesis_id: str
    source_type: str
    title: str
    url: str
    snippet: str = ""
    score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def searchable_text(self) -> str:
        return " ".join(part for part in [self.title, self.snippet] if part)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Prediction:
    """预测器对未来记忆需求的输出。

    Attributes:
        predicted_future_intents: 预测用户接下来可能出现的意图列表。
        activated_memory_ids: 预测应该提前放入 working cache 的 memory 列表。
        metadata: 预测过程的额外信息，例如 provider、usage、fallback 错误。
    """

    predicted_future_intents: List[str]
    activated_memory_ids: List[ActivatedMemory]
    metadata: Dict[str, Any] = field(default_factory=dict)
    hypotheses: List[FutureNeedHypothesis] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "predicted_future_intents": self.predicted_future_intents,
            "activated_memory_ids": [item.to_dict() for item in self.activated_memory_ids],
            "metadata": self.metadata,
            "hypotheses": [item.to_dict() for item in self.hypotheses],
        }


@dataclass
class WorkingCache:
    """一次预测后构造出的工作记忆缓存。

    Attributes:
        cache_id: 当前 working cache 的 id。
        budget: cache 最多允许保留的 memory 数量。
        memory_ids: 实际插入 cache 的 memory id 列表。
        summaries: cache 中 memory 对应的摘要列表。
        local_subgraph: 围绕 cache memory 抽取的局部图，包含 nodes 和 edges。
        prediction: 生成这个 cache 的预测器输出。
        metadata: cache 构造过程的额外信息。
    """

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


@dataclass
class WorkingContextPackage:
    """Proactive context prepared before the next query arrives."""

    package_id: str
    hypotheses: List[FutureNeedHypothesis]
    grounded_memories: List[GroundedMemory]
    memory_ids: List[str]
    summaries: List[str]
    local_subgraph: Dict[str, Any]
    gaps: List[MemoryGap] = field(default_factory=list)
    external_evidence: List[ExternalEvidence] = field(default_factory=list)
    coverage_score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "package_id": self.package_id,
            "hypotheses": [item.to_dict() for item in self.hypotheses],
            "grounded_memories": [item.to_dict() for item in self.grounded_memories],
            "memory_ids": self.memory_ids,
            "summaries": self.summaries,
            "local_subgraph": self.local_subgraph,
            "gaps": [item.to_dict() for item in self.gaps],
            "external_evidence": [item.to_dict() for item in self.external_evidence],
            "coverage_score": self.coverage_score,
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
    return {
        "precision": precision,
        "recall": recall,
        "hit_rate": 1.0 if intersection else 0.0,
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
