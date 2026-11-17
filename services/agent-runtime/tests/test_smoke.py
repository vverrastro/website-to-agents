"""Opt-in smoke tests against the real providers (cost money). Run with:
    RUN_SMOKE=1 uv run pytest -m smoke
Keys are read from .env / the environment; nothing is printed."""

import os

import pytest
from wta_agent_runtime.llm import GenerateRequest, Message, build_llm_provider
from wta_common.settings import Settings
from wta_indexing.embeddings import build_embedding_provider

pytestmark = pytest.mark.smoke
SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def _enabled() -> Settings:
    if os.environ.get("RUN_SMOKE") != "1":
        pytest.skip("set RUN_SMOKE=1 to run real-provider smoke tests")
    return Settings()


async def test_real_llm_returns_structured_answer() -> None:
    settings = _enabled()
    if not settings.anthropic_api_key.get_secret_value():
        pytest.skip("ANTHROPIC_API_KEY not set")
    llm = build_llm_provider(settings)
    result = await llm.generate_response(
        GenerateRequest(
            system="Answer in one short sentence.",
            messages=[Message("user", "What is the capital of France?")],
            max_tokens=300,
            json_schema=SCHEMA,
        )
    )
    assert result.finish_reason == "stop" and result.structured
    assert "paris" in result.structured["answer"].lower()
    assert result.usage.output_tokens > 0


async def test_real_embeddings_have_configured_dimensions() -> None:
    settings = _enabled()
    if not settings.voyage_api_key.get_secret_value():
        pytest.skip("VOYAGE_API_KEY not set")
    embedder = build_embedding_provider(settings)
    docs = await embedder.embed(["Paris is the capital of France."], input_type="document")
    query = await embedder.embed(["capital of France"], input_type="query")
    assert len(docs[0]) == len(query[0]) == settings.embedding_dimensions
