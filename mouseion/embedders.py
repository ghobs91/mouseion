"""Embedding backends.

Two ways to get vectors, both local:

* ``OpenAIEmbedder`` — an OpenAI-compatible ``/v1/embeddings`` endpoint. This is the
  one to use if llama.cpp is already running, and adds no Python dependency.
* ``FastEmbedder`` — ONNX Runtime via ``fastembed``, for machines with no model server.
  Optional extra: ``uv sync --extra embeddings``.

Selection is explicit. Nothing downloads a model as a side effect of a search.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

import httpx


class EmbeddingError(Exception):
    pass


class Embedder(Protocol):
    identity: str

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def aclose(self) -> None: ...


class OpenAIEmbedder:
    """Any server speaking POST /v1/embeddings (llama.cpp, LM Studio, Ollama, vLLM)."""

    def __init__(
        self,
        base_url: str,
        model: str = "",
        api_key: str = "",
        *,
        timeout_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.identity = f"openai-compatible:{self.base_url}:{model or 'default'}"
        self._http = httpx.AsyncClient(timeout=timeout_s, transport=transport)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload: dict[str, object] = {"input": texts}
        if self.model:
            payload["model"] = self.model
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

        try:
            response = await self._http.post(
                f"{self.base_url}/v1/embeddings", json=payload, headers=headers
            )
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"embedding backend unreachable: {exc}") from None

        if response.status_code >= 400:
            raise EmbeddingError(
                f"embedding backend returned {response.status_code}: {response.text[:200]}"
            )
        try:
            data = response.json()["data"]
            ordered = sorted(data, key=lambda item: item.get("index", 0))
            return [item["embedding"] for item in ordered]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError(f"unexpected embedding response: {exc}") from None

    async def aclose(self) -> None:
        await self._http.aclose()


class FastEmbedder:
    """Local ONNX embeddings through fastembed. No torch, no model server."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5", cache_dir: str | None = None):
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise EmbeddingError(
                "fastembed is not installed; run `uv sync --extra embeddings`"
            ) from exc

        self.model_name = model_name
        self.identity = f"fastembed:{model_name}"
        self._model: Any = TextEmbedding(model_name=model_name, cache_dir=cache_dir)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(self._embed_sync, list(texts))

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        try:
            return [[float(value) for value in vector] for vector in self._model.embed(texts)]
        except Exception as exc:  # noqa: BLE001 - backend errors must become EmbeddingError
            raise EmbeddingError(f"fastembed failed: {exc}") from None

    async def aclose(self) -> None:
        self._model = None
