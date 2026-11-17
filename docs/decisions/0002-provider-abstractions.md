# 0002 — LLM and embedding provider abstractions

Status: accepted

## Context
Anthropic (Claude) is the first LLM and Voyage AI the first embedding provider, but the brief requires changing either without touching agent, RAG, API or UI logic. Embeddings are not LLM generation, and mixing vectors from different models silently corrupts retrieval.

## Decision
- **Two separate interfaces.** `LLMProvider.generate_response(GenerateRequest) -> GenerateResult` and `EmbeddingProvider.embed(texts, input_type)`. Each carries provider/model identity, a normalised result (text, structured output, usage, finish reason, latency, actual serving model), normalised errors (`LLMError`/`EmbeddingError` with `retryable`/`fatal`, never including bodies, prompts or keys) and a capability record (`structured_output`, `refusal_fallback`).
- **Registries driven by server-side config** (`LLM_PROVIDER`, `EMBEDDING_PROVIDER`, …). Factories validate keys and raise `ConfigError` with an actionable message; they run lazily so crawling works without any key. `fake` providers exist for tests and key-less development and are refused in production. The default model name is defined once (`DEFAULT_LLM_MODEL`).
- **Anthropic adapter** uses the official SDK: structured output via `output_config.format` (JSON schema), no `thinking`/sampling parameters, optional server-side refusal fallback, SDK retries for 408/409/429/5xx and connection errors. We deliberately do **not** use forced `tool_choice` (rejected by current models). The model returns `{answerable, answer, used_source_ids, follow_ups, navigation}`; it never returns URLs or UI nodes (ADR 0003).
- **Voyage adapter** over `httpx`: bounded batches (64), retry with backoff on 429/5xx/network, fatal on 401/403 and on dimension mismatch, safe error codes.
- **Embedding spaces.** Provider, model and dimensions are stored as an `EmbeddingSpace`; exactly one is active. Indexing and retrieval refuse a configured model that differs from the active space (`EmbeddingSpaceMismatch`). Switching is explicit: `make reindex` embeds all chunks into a new space, then flips `active` in one transaction (old vectors are kept for rollback). The vector column is fixed at 1024 dimensions; a different size needs a migration.

## Consequences
- Adding a provider = implement the protocol + register; no change to RAG/API.
- Chunk-hash reuse is scoped to the active space, so a model change never reuses stale vectors.
- Real-provider behaviour is verified only against mocked HTTP in CI; opt-in smoke tests (`RUN_SMOKE=1`) cover the live path.
