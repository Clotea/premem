from __future__ import annotations

import hashlib
import math
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence

from preact_demo.code.utils import tokenize
from preact_demo.code.vllm_client import VLLMClient, VLLMError


class EmbeddingProvider(Protocol):
    name: str

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        ...


class HashEmbeddingProvider:
    """Dependency-free signed feature hashing for deterministic local runs."""

    name = "hash"

    def __init__(self, dimensions: int = 256) -> None:
        self.dimensions = max(32, int(dimensions))

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> List[float]:
        vector = [0.0] * self.dimensions
        tokens = tokenize(text)
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        return normalize(vector)


class VLLMEmbeddingProvider:
    name = "vllm"

    def __init__(self, client: VLLMClient, fallback: Optional[EmbeddingProvider] = None) -> None:
        self.client = client
        self.fallback = fallback or HashEmbeddingProvider()

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        try:
            return self.client.embed(list(texts))
        except VLLMError:
            return self.fallback.embed(texts)


def create_embedding_provider(
    config: Mapping[str, Any],
    llm_client: Optional[VLLMClient] = None,
) -> EmbeddingProvider:
    embedding_config: Dict[str, Any] = dict(config.get("embedding") or {})
    local = HashEmbeddingProvider(int(embedding_config.get("dimensions") or 256))
    provider = str(embedding_config.get("provider") or "hash").lower()
    if provider in {"vllm", "openai"} and llm_client is not None:
        return VLLMEmbeddingProvider(llm_client, fallback=local)
    return local


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    if denominator <= 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / denominator


def normalize(vector: Sequence[float]) -> List[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return list(vector)
    return [value / norm for value in vector]
