import json
from collections.abc import Callable
from typing import Any

import anthropic
import httpx2
import pytest
from wta_agent_runtime.anthropic_provider import FALLBACK_BETA, AnthropicLLMProvider
from wta_agent_runtime.llm import (
    LLM_PROVIDERS,
    FakeLLMProvider,
    GenerateRequest,
    LLMError,
    Message,
    build_llm_provider,
    register_llm_provider,
)
from wta_common.settings import DEFAULT_LLM_MODEL, ConfigError, Settings

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
REQ = GenerateRequest(
    system="SYSTEM PROMPT", messages=[Message("user", "hello")], max_tokens=500, json_schema=SCHEMA
)


def message_json(
    text: str = '{"answer": "hi"}', stop: str = "end_turn", **extra: Any
) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_input_tokens": 60,
            "cache_creation_input_tokens": 5,
        },
        **extra,
    }


def provider(
    handler: Callable[[httpx2.Request], httpx2.Response], **kw: Any
) -> AnthropicLLMProvider:
    kw.setdefault("max_retries", 0)
    kw.setdefault("refusal_fallback", False)
    client = anthropic.AsyncAnthropic(
        api_key="sk-ant-test-secret",
        max_retries=kw.pop("max_retries"),
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    return AnthropicLLMProvider("sk-ant-test-secret", "claude-opus-5-5", client=client, **kw)


async def test_request_shape_and_result_normalisation() -> None:
    seen: list[httpx2.Request] = []

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen.append(req)
        return httpx2.Response(200, json=message_json())

    result = await provider(handler, effort="low").generate_response(REQ)

    req = seen[0]
    body = json.loads(req.content)
    assert req.url.path == "/v1/messages" and "anthropic-beta" not in req.headers
    auth = req.headers.get("authorization", "") + req.headers.get("x-api-key", "")
    assert "sk-ant-test-secret" in auth  # the SDK picks the header; we only pass the key
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] == 500
    assert body["system"] == "SYSTEM PROMPT" and body["messages"] == [
        {"role": "user", "content": "hello"}
    ]
    assert body["output_config"] == {
        "effort": "low",
        "format": {"type": "json_schema", "schema": SCHEMA},
    }
    assert "thinking" not in body and "tool_choice" not in body and "temperature" not in body

    assert result.structured == {"answer": "hi"} and result.finish_reason == "stop"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (100, 20)
    assert (result.usage.cache_read_tokens, result.usage.cache_write_tokens) == (60, 5)
    assert result.provider == "anthropic" and result.model == "claude-opus-5-5"
    assert result.latency_ms >= 0


async def test_plain_text_request_has_no_output_config() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(req: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(req.content))
        return httpx2.Response(200, json=message_json("plain"))

    result = await provider(handler).generate_response(GenerateRequest("s", [Message("user", "q")]))
    assert "output_config" not in bodies[0] and result.text == "plain" and result.structured is None


async def test_refusal_fallback_uses_beta_endpoint_with_default_mode() -> None:
    seen: list[httpx2.Request] = []

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen.append(req)
        return httpx2.Response(200, json=message_json())

    await provider(handler, refusal_fallback=True).generate_response(REQ)
    assert json.loads(seen[0].content)["fallbacks"] == "default"
    assert FALLBACK_BETA in seen[0].headers["anthropic-beta"]


async def test_refusal_is_reported_not_raised() -> None:
    details = {"type": "refusal", "category": "cyber", "explanation": "declined"}

    def handler(req: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=message_json("", stop="refusal", stop_details=details))

    result = await provider(handler).generate_response(REQ)
    assert result.finish_reason == "refusal" and result.refusal_category == "cyber"
    assert result.structured is None


async def test_truncated_and_invalid_json_degrade_gracefully() -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=message_json('{"answer": "cut', stop="max_tokens"))

    result = await provider(handler).generate_response(REQ)
    assert result.finish_reason == "length" and result.structured is None and "cut" in result.text


@pytest.mark.parametrize(
    "status,code,retryable,fatal",
    [
        (401, "llm_auth_failed", False, True),
        (403, "llm_auth_failed", False, True),
        (404, "llm_model_not_found", False, True),
        (400, "llm_bad_request", False, False),
        (429, "llm_rate_limited", True, False),
        (500, "llm_unavailable", True, False),
        (529, "llm_unavailable", True, False),
    ],
)
async def test_errors_are_normalised_and_never_leak(
    status: int, code: str, retryable: bool, fatal: bool
) -> None:
    leak = "SECRET-PROMPT-CONTENT sk-ant-test-secret"

    def handler(req: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            status, json={"type": "error", "error": {"type": "x", "message": leak}}
        )

    with pytest.raises(LLMError) as exc:
        await provider(handler).generate_response(REQ)
    assert (exc.value.code, exc.value.retryable, exc.value.fatal) == (code, retryable, fatal)
    assert "SECRET" not in str(exc.value) and "sk-ant" not in str(exc.value)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


async def test_network_failures_are_normalised() -> None:
    def timeout(req: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=req)

    def down(req: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=req)

    with pytest.raises(LLMError, match="llm_timeout") as t:
        await provider(timeout).generate_response(REQ)
    with pytest.raises(LLMError, match="llm_unavailable") as d:
        await provider(down).generate_response(REQ)
    assert t.value.retryable and d.value.retryable


async def test_sdk_retries_transient_errors() -> None:
    calls = 0

    def handler(req: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx2.Response(
                429,
                headers={"retry-after-ms": "1"},
                json={"type": "error", "error": {"type": "rate_limit_error", "message": "x"}},
            )
        return httpx2.Response(200, json=message_json())

    result = await provider(handler, max_retries=2).generate_response(REQ)
    assert calls == 2 and result.structured == {"answer": "hi"}


def settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[arg-type]


def test_registry_builds_anthropic_with_single_default_model() -> None:
    p = build_llm_provider(settings(anthropic_api_key="sk-ant-x"))
    assert (p.provider, p.model) == ("anthropic", DEFAULT_LLM_MODEL)
    assert DEFAULT_LLM_MODEL == "claude-opus-5-5"
    assert p.capabilities.structured_output
    assert (
        build_llm_provider(settings(anthropic_api_key="k", llm_model="   ")).model
        == DEFAULT_LLM_MODEL
    )
    assert (
        build_llm_provider(settings(anthropic_api_key="k", llm_model="claude-sonnet-5-5")).model
        == "claude-sonnet-5-5"
    )


def test_registry_errors_are_actionable() -> None:
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        build_llm_provider(settings())
    with pytest.raises(ConfigError, match="Unknown LLM_PROVIDER"):
        build_llm_provider(settings(llm_provider="nope"))
    with pytest.raises(ConfigError, match="production"):
        build_llm_provider(settings(llm_provider="fake", app_env="production"))


async def test_new_provider_needs_no_business_logic_changes() -> None:
    register_llm_provider("custom", lambda s: FakeLLMProvider(lambda r: "ok", provider="custom"))
    p = build_llm_provider(settings(llm_provider="custom"))
    out = await p.generate_response(REQ)
    assert out.text == "ok" and out.provider == "custom"
    LLM_PROVIDERS.pop("custom")


async def test_fake_provider_records_requests_and_structured_output() -> None:
    fake = FakeLLMProvider(lambda r: {"answer": "a", "source_ids": [1]})
    out = await fake.generate_response(REQ)
    assert out.structured == {"answer": "a", "source_ids": [1]} and fake.requests == [REQ]
