from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Mapping, Protocol, Sequence

from preact_demo.code.utils import ExternalEvidence, MemoryGap


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, source_type: str, limit: int) -> List[Dict[str, Any]]:
        ...


class NoopSearchProvider:
    name = "disabled"

    def search(self, query: str, source_type: str, limit: int) -> List[Dict[str, Any]]:
        return []


class HttpSearchProvider:
    """Small key-free adapters for web and academic search."""

    name = "http"

    def __init__(self, timeout: float = 8.0, user_agent: str = "PreAct-Memory/0.2") -> None:
        self.timeout = timeout
        self.user_agent = user_agent

    def search(self, query: str, source_type: str, limit: int) -> List[Dict[str, Any]]:
        if source_type == "paper":
            return self._semantic_scholar(query, limit)
        return self._duckduckgo(query, limit)

    def _duckduckgo(self, query: str, limit: int) -> List[Dict[str, Any]]:
        params = urllib.parse.urlencode(
            {"q": query, "format": "json", "no_html": 1, "skip_disambig": 1}
        )
        payload = self._get_json(f"https://api.duckduckgo.com/?{params}")
        rows: List[Dict[str, Any]] = []
        if payload.get("AbstractText"):
            rows.append(
                {
                    "title": payload.get("Heading") or query,
                    "url": payload.get("AbstractURL") or "",
                    "snippet": payload.get("AbstractText") or "",
                }
            )
        for topic in payload.get("RelatedTopics") or []:
            if isinstance(topic, dict) and topic.get("Topics"):
                topics = topic.get("Topics") or []
            else:
                topics = [topic]
            for item in topics:
                if not isinstance(item, dict) or not item.get("Text"):
                    continue
                rows.append(
                    {
                        "title": str(item.get("Text") or "").split(" - ", 1)[0],
                        "url": item.get("FirstURL") or "",
                        "snippet": item.get("Text") or "",
                    }
                )
                if len(rows) >= limit:
                    return rows
        return rows[:limit]

    def _semantic_scholar(self, query: str, limit: int) -> List[Dict[str, Any]]:
        params = urllib.parse.urlencode(
            {
                "query": query,
                "limit": max(1, min(limit, 20)),
                "fields": "title,url,abstract,year,authors",
            }
        )
        payload = self._get_json(
            f"https://api.semanticscholar.org/graph/v1/paper/search?{params}"
        )
        rows = []
        for item in payload.get("data") or []:
            rows.append(
                {
                    "title": item.get("title") or query,
                    "url": item.get("url") or "",
                    "snippet": item.get("abstract") or "",
                    "metadata": {
                        "year": item.get("year"),
                        "authors": [author.get("name") for author in item.get("authors") or []],
                    },
                }
            )
        return rows

    def _get_json(self, url: str) -> Dict[str, Any]:
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"External search failed: {exc}") from exc


def create_search_provider(config: Mapping[str, Any]) -> SearchProvider:
    search_config = dict(config.get("external_search") or {})
    if not bool(search_config.get("enabled", False)):
        return NoopSearchProvider()
    return HttpSearchProvider(timeout=float(search_config.get("timeout") or 8.0))


def search_memory_gaps(
    gaps: Sequence[MemoryGap],
    config: Mapping[str, Any],
    provider: SearchProvider,
) -> List[ExternalEvidence]:
    search_config = dict(config.get("external_search") or {})
    if not bool(search_config.get("enabled", False)):
        return []
    per_query = int(search_config.get("results_per_query") or 2)
    max_queries = int(search_config.get("max_queries_per_gap") or 2)
    source_types = [str(item) for item in search_config.get("source_types") or ["web", "paper"]]
    evidence: List[ExternalEvidence] = []
    seen_urls = set()
    for gap in gaps:
        if not gap.exists:
            continue
        for query in gap.search_queries[:max_queries]:
            for source_type in source_types:
                try:
                    rows = provider.search(query, source_type, per_query)
                except RuntimeError:
                    continue
                for row in rows:
                    url = str(row.get("url") or "")
                    dedupe_key = url or f"{source_type}:{row.get('title')}"
                    if dedupe_key in seen_urls:
                        continue
                    seen_urls.add(dedupe_key)
                    evidence.append(
                        ExternalEvidence(
                            id=f"ext_{len(evidence) + 1:03d}",
                            hypothesis_id=gap.hypothesis_id,
                            source_type=source_type,
                            title=str(row.get("title") or query),
                            url=url,
                            snippet=str(row.get("snippet") or ""),
                            metadata=dict(row.get("metadata") or {}),
                        )
                    )
    return evidence
