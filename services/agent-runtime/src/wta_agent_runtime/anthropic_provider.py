"""Anthropic Messages API adapter (official SDK)."""

from __future__ import annotations

import json
import time
from typing import Any

import anthropic
from wta_common.logging import get_logger

from wta_agent_runtime.llm import (
    FinishReason,
    GenerateRequest,
    GenerateResult,
    LLMError,
    ProviderCapabilities,
    Usage,
)

log = get_logger("wta_agent_runtime.anthropic")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
_FINISH: dict[str, FinishReason] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "refusal": "refusal",
    "tool_use": "tool_use",
}


class AnthropicLLMProvider:
    provider = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        effort: str | None = None,
        refusal_fallback: bool = True,
        timeout: float = 60.0,
        max_retries: int = 3,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        self.model = model
        self.capabilities = ProviderCapabilities(
            structured_output=True, refusal_fallback=refusal_fallback
        )
        self._effort = effort or None
        self._fallback = refusal_fallback
        # The SDK retries connection errors, 408/409/429 and 5xx with exponential backoff.
        self._client = client or anthropic.AsyncAnthropic(
            api_key=api_key, timeout=timeout, max_retries=max_retries
        )

    async def generate_response(self, request: GenerateRequest) -> GenerateResult:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
        }
        output_config: dict[str, Any] = {}
        if self._effort:
            output_config["effort"] = self._effort
        if request.json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": request.json_schema}
        if output_config:
            params["output_config"] = output_config

        started = time.perf_counter()
        try:
            if self._fallback:
                response = await self._client.beta.messages.create(
                    betas=[FALLBACK_BETA], fallbacks="default", **params
                )
            else:
                response = await self._client.messages.create(**params)
        except anthropic.APIError as exc:
            raise self._normalise(exc) from None
        latency_ms = int((time.perf_counter() - started) * 1000)

        text = "".join(b.text for b in response.content if b.type == "text")
        finish = _FINISH.get(response.stop_reason or "", "other")
        details = getattr(response, "stop_details", None)
        category = getattr(details, "category", None) if finish == "refusal" else None
        usage = response.usage
        return GenerateResult(
            text=text,
            structured=self._parse_structured(text) if request.json_schema else None,
            usage=Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            ),
            finish_reason=finish,
            latency_ms=latency_ms,
            provider=self.provider,
            model=response.model,
            refusal_category=category,
        )

    @staticmethod
    def _parse_structured(text: str) -> dict[str, Any] | None:
        try:
            parsed = json.loads(text)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _normalise(exc: anthropic.APIError) -> LLMError:
        status = getattr(exc, "status_code", None)
        # Only the status and request id are logged: bodies may echo user/page content.
        log.warning(
            "llm_error",
            provider="anthropic",
            kind=type(exc).__name__,
            status=status,
            request_id=getattr(exc, "request_id", None),
        )
        if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
            return LLMError("llm_auth_failed", fatal=True)
        if isinstance(exc, anthropic.NotFoundError):
            return LLMError("llm_model_not_found", fatal=True)
        if isinstance(exc, anthropic.RateLimitError):
            return LLMError("llm_rate_limited", retryable=True)
        if isinstance(exc, anthropic.APITimeoutError):
            return LLMError("llm_timeout", retryable=True)
        if isinstance(exc, anthropic.APIConnectionError):
            return LLMError("llm_unavailable", retryable=True)
        if isinstance(exc, anthropic.BadRequestError):
            return LLMError("llm_bad_request")
        if status is not None and status >= 500:
            return LLMError("llm_unavailable", retryable=True)
        return LLMError("llm_error")
