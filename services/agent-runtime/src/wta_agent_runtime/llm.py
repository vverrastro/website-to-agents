"""Provider-neutral LLM interface, normalised errors, registry and a fake for tests/dev.

Business logic (RAG, API) depends only on this module. A new provider is added by implementing
``LLMProvider`` and calling ``register_llm_provider``; nothing else changes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from wta_common.settings import ConfigError, Settings

Role = Literal["user", "assistant"]
FinishReason = Literal["stop", "length", "refusal", "tool_use", "other"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True)
class GenerateRequest:
    system: str
    messages: list[Message]
    max_tokens: int = 2000
    # JSON schema the answer must follow. Providers without native support must say so via
    # ``capabilities.structured_output`` and the caller falls back to plain text.
    json_schema: dict[str, Any] | None = None


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class GenerateResult:
    text: str
    structured: dict[str, Any] | None
    usage: Usage
    finish_reason: FinishReason
    latency_ms: int
    provider: str
    model: str  # the model that actually served the request (may differ after a fallback)
    refusal_category: str | None = None


@dataclass(frozen=True)
class ProviderCapabilities:
    structured_output: bool = False
    refusal_fallback: bool = False


class LLMError(Exception):
    """Normalised failure. ``code`` is safe to log/store/return; it never carries provider
    response bodies, request contents or credentials."""

    def __init__(self, code: str, *, retryable: bool = False, fatal: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.fatal = fatal  # configuration problem (bad key / unknown model)


class LLMProvider(Protocol):
    provider: str
    model: str
    capabilities: ProviderCapabilities

    async def generate_response(self, request: GenerateRequest) -> GenerateResult: ...


Factory = Callable[[Settings], LLMProvider]
LLM_PROVIDERS: dict[str, Factory] = {}


def register_llm_provider(name: str, factory: Factory) -> None:
    LLM_PROVIDERS[name] = factory


def build_llm_provider(settings: Settings) -> LLMProvider:
    # Imported here so merely importing this module never requires a vendor SDK.
    from wta_agent_runtime import providers  # noqa: F401  (registers built-ins)

    try:
        factory = LLM_PROVIDERS[settings.llm_provider]
    except KeyError:
        raise ConfigError(
            f"Unknown LLM_PROVIDER={settings.llm_provider!r}. "
            f"Available: {', '.join(sorted(LLM_PROVIDERS))}."
        ) from None
    return factory(settings)


@dataclass
class FakeLLMProvider:
    """Scriptable provider for tests and key-less local development (refused in production).
    ``responder`` maps a request to answer text or a structured dict."""

    responder: Callable[[GenerateRequest], str | dict[str, Any]]
    provider: str = "fake"
    model: str = "fake-llm"
    capabilities: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(structured_output=True)
    )
    requests: list[GenerateRequest] = field(default_factory=list)

    async def generate_response(self, request: GenerateRequest) -> GenerateResult:
        self.requests.append(request)
        out = self.responder(request)
        text = out if isinstance(out, str) else __import__("json").dumps(out)
        structured = out if isinstance(out, dict) else None
        return GenerateResult(
            text=text,
            structured=structured,
            usage=Usage(input_tokens=len(request.system) // 4, output_tokens=len(text) // 4),
            finish_reason="stop",
            latency_ms=1,
            provider=self.provider,
            model=self.model,
        )
