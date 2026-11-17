"""Provider-neutral embedding interface, Voyage AI adapter, deterministic fake, and registry.

Embeddings are separate from LLM generation. Index-time and query-time embeddings must come from
the same provider/model/dimensions; that identity is persisted as an ``EmbeddingSpace``.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Literal, Protocol

import httpx
from wta_common.logging import get_logger
from wta_common.settings import ConfigError, Settings

log = get_logger("wta_indexing.embeddings")
InputType = Literal["document", "query"]


class EmbeddingError(Exception):
    """Normalised, safe-to-store failure. Never carries response bodies or credentials."""

    def __init__(self, code: str, *, retryable: bool = False, fatal: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.fatal = fatal  # configuration problem: retrying other pages will not help


class EmbeddingProvider(Protocol):
    provider: str
    model: str
    dimensions: int

    async def embed(self, texts: Sequence[str], *, input_type: InputType) -> list[list[float]]: ...


class VoyageEmbeddingProvider:
    provider = "voyage"
    URL = "https://api.voyageai.com/v1/embeddings"
    MAX_BATCH = 64
    MAX_ATTEMPTS = 3

    def __init__(
        self,
        api_key: str,
        model: str,
        dimensions: int,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.model = model
        self.dimensions = dimensions
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0))
        self._sleep = sleep

    async def embed(self, texts: Sequence[str], *, input_type: InputType) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), self.MAX_BATCH):
            batch = list(texts[start : start + self.MAX_BATCH])
            out.extend(await self._embed_batch(batch, input_type))
        return out

    async def _embed_batch(self, batch: list[str], input_type: InputType) -> list[list[float]]:
        payload = {
            "input": batch,
            "model": self.model,
            "input_type": input_type,
            "output_dimension": self.dimensions,
        }
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                resp = await self._client.post(
                    self.URL, json=payload, headers={"Authorization": f"Bearer {self._api_key}"}
                )
            except (httpx.TimeoutException, httpx.TransportError):
                error = EmbeddingError("embedding_network_error", retryable=True)
            else:
                if resp.status_code == 200:
                    return self._parse(resp, len(batch))
                error = self._error_for(resp.status_code)
            if not error.retryable or attempt == self.MAX_ATTEMPTS:
                raise error
            log.warning("embedding_retry", provider=self.provider, attempt=attempt, code=error.code)
            await self._sleep(min(2.0 ** (attempt - 1), 8.0))
        raise EmbeddingError("embedding_failed")  # pragma: no cover

    @staticmethod
    def _error_for(status: int) -> EmbeddingError:
        if status in (401, 403):
            return EmbeddingError("embedding_auth_failed", fatal=True)
        if status == 429:
            return EmbeddingError("embedding_rate_limited", retryable=True)
        if status >= 500:
            return EmbeddingError("embedding_provider_unavailable", retryable=True)
        return EmbeddingError(f"embedding_rejected_{status}")

    def _parse(self, resp: httpx.Response, expected: int) -> list[list[float]]:
        try:
            rows = sorted(resp.json()["data"], key=lambda r: r["index"])
            vectors = [[float(x) for x in r["embedding"]] for r in rows]
        except (ValueError, KeyError, TypeError) as exc:
            raise EmbeddingError("embedding_bad_response") from exc
        if len(vectors) != expected or any(len(v) != self.dimensions for v in vectors):
            raise EmbeddingError("embedding_dimension_mismatch", fatal=True)
        return vectors


class FakeEmbeddingProvider:
    """Deterministic bag-of-words hashing embeddings: similar texts get similar vectors.
    For tests and key-less local development only (refused in production)."""

    provider = "fake"
    model = "fake-hash-v1"

    def __init__(self, dimensions: int = 1024) -> None:
        self.dimensions = dimensions
        self.calls: list[tuple[InputType, int]] = []  # (input_type, batch size), for assertions

    async def embed(self, texts: Sequence[str], *, input_type: InputType) -> list[list[float]]:
        self.calls.append((input_type, len(texts)))
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        for word in re.findall(r"\w+", text.lower()):
            digest = hashlib.sha256(word.encode()).digest()
            vec[int.from_bytes(digest[:4], "big") % self.dimensions] += 1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


Factory = Callable[[Settings], EmbeddingProvider]


def _voyage(settings: Settings) -> EmbeddingProvider:
    key = settings.voyage_api_key.get_secret_value()
    if not key:
        raise ConfigError(
            "VOYAGE_API_KEY is not set. Create a key at https://dash.voyageai.com and put it in "
            ".env, or set EMBEDDING_PROVIDER=fake for key-less local development."
        )
    return VoyageEmbeddingProvider(key, settings.embedding_model, settings.embedding_dimensions)


def _fake(settings: Settings) -> EmbeddingProvider:
    if settings.is_production:
        raise ConfigError("EMBEDDING_PROVIDER=fake is not allowed with APP_ENV=production.")
    return FakeEmbeddingProvider(settings.embedding_dimensions)


EMBEDDING_PROVIDERS: dict[str, Factory] = {"voyage": _voyage, "fake": _fake}


def register_embedding_provider(name: str, factory: Factory) -> None:
    EMBEDDING_PROVIDERS[name] = factory


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    try:
        factory = EMBEDDING_PROVIDERS[settings.embedding_provider]
    except KeyError:
        raise ConfigError(
            f"Unknown EMBEDDING_PROVIDER={settings.embedding_provider!r}. "
            f"Available: {', '.join(sorted(EMBEDDING_PROVIDERS))}."
        ) from None
    return factory(settings)
