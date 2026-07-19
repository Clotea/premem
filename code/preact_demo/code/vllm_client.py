from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Mapping, Optional, Tuple


class VLLMError(RuntimeError):
    """vLLM 客户端请求失败或响应解析失败时抛出的异常。"""

    pass


class VLLMClient:
    """Minimal OpenAI-compatible client for a local vLLM server.

    Attributes:
        base_url: vLLM OpenAI-compatible 服务地址，不包含末尾斜杠。
        model: 请求时使用的模型名称。
        timeout: 单次 HTTP 请求超时时间，单位是秒。
        api_key: Authorization header 使用的 API key；本地 vLLM 常用 EMPTY。
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000/v1",
        model: str = "Qwen/Qwen2.5-7B-Instruct",
        timeout: float = 30.0,
        api_key: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or "EMPTY"

    def chat(
        self,
        messages: List[Mapping[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 512,
        response_format: Optional[Mapping[str, Any]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        try:
            import urllib.error
            import urllib.request
        except ImportError as exc:
            raise VLLMError(f"Python urllib is unavailable: {exc}") from exc

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format is not None:
            payload["response_format"] = dict(response_format)

        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise VLLMError(f"vLLM request failed: {exc}") from exc

        try:
            parsed = json.loads(raw)
            content = parsed["choices"][0]["message"]["content"]
            return str(content), dict(parsed.get("usage") or {})
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise VLLMError(f"Invalid vLLM response: {raw[:500]}") from exc

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Call an OpenAI-compatible embeddings endpoint."""
        try:
            import urllib.error
            import urllib.request
        except ImportError as exc:
            raise VLLMError(f"Python urllib is unavailable: {exc}") from exc

        payload = {"model": self.model, "input": texts}
        request = urllib.request.Request(
            self.base_url + "/embeddings",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise VLLMError(f"vLLM embedding request failed: {exc}") from exc

        try:
            parsed = json.loads(raw)
            rows = sorted(parsed["data"], key=lambda item: int(item.get("index", 0)))
            vectors = [[float(value) for value in item["embedding"]] for item in rows]
            if len(vectors) != len(texts):
                raise ValueError("Embedding response length mismatch")
            return vectors
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise VLLMError(f"Invalid vLLM embedding response: {raw[:500]}") from exc
