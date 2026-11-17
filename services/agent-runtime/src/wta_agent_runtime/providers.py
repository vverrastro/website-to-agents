"""Built-in LLM provider registrations."""

from __future__ import annotations

from wta_common.settings import ConfigError, Settings

from wta_agent_runtime.llm import FakeLLMProvider, LLMProvider, register_llm_provider


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


def _fake(settings: Settings) -> LLMProvider:
    if settings.is_production:
        raise ConfigError("LLM_PROVIDER=fake is not allowed with APP_ENV=production.")
    reply = {"answer": "Fake provider (no LLM configured).", "source_ids": []}
    return FakeLLMProvider(responder=lambda _: reply)


register_llm_provider("anthropic", _anthropic)
register_llm_provider("fake", _fake)
