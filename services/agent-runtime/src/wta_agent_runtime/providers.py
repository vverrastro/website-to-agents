"""Built-in LLM provider registrations."""

from __future__ import annotations

import html
import re
from typing import Any

from wta_common.settings import ConfigError, Settings

from wta_agent_runtime.llm import (
    FakeLLMProvider,
    GenerateRequest,
    LLMProvider,
    register_llm_provider,
)


def _anthropic(settings: Settings) -> LLMProvider:
    key = settings.anthropic_api_key.get_secret_value()
    if not key:
        raise ConfigError(
            "ANTHROPIC_API_KEY is not set. Create a key at https://console.anthropic.com, put it "
            "in .env (never commit it), or set LLM_PROVIDER=fake for key-less local development."
        )
    from wta_agent_runtime.anthropic_provider import AnthropicLLMProvider

    return AnthropicLLMProvider(
        key,
        settings.llm_model,
        effort=settings.llm_effort or None,
        refusal_fallback=settings.llm_refusal_fallback,
        timeout=settings.llm_timeout_seconds,
    )


_SOURCE = re.compile(r'<source id="1" title="([^"]*)"[^>]*>\n(.*?)\n</source>', re.DOTALL)


def _extractive_answer(request: GenerateRequest) -> dict[str, Any]:
    """Key-less dev responder: quotes the best retrieved source and cites it. Not an LLM."""
    match = _SOURCE.search(request.messages[-1].content)
    if match is None:
        return {"answerable": False, "answer": "No sources were provided.", "used_source_ids": [],
                "follow_ups": [], "navigation": []}  # fmt: skip
    title, text = html.unescape(match.group(1)), html.unescape(match.group(2))
    snippet = " ".join(text.split())[:400]
    return {
        "answerable": True,
        "answer": f"(dev answer, no LLM configured) From “{title}”: {snippet}",
        "used_source_ids": [1],
        "follow_ups": [],
        "navigation": [{"source_id": 1, "label": title}],
    }


def _fake(settings: Settings) -> LLMProvider:
    if settings.is_production:
        raise ConfigError("LLM_PROVIDER=fake is not allowed with APP_ENV=production.")
    return FakeLLMProvider(responder=_extractive_answer)


register_llm_provider("anthropic", _anthropic)
register_llm_provider("fake", _fake)
