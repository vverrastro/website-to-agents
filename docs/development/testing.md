# Testing

| Layer | Tooling | Notes |
|---|---|---|
| Python | pytest (+ asyncio) with `httpx.MockTransport` / `httpx2.MockTransport` | `make test`; strict mypy and ruff in `make check` |
| TypeScript | vitest (jsdom for the widget) | `npm test`; `tsc` + Biome in `make check` |
| Contract | shared JSON fixtures validated by Pydantic **and** Zod | add a fixture whenever the protocol changes |

## What is covered

- **Crawler:** URL normalisation/policy, robots semantics, sitemap parsing (XXE, entity expansion, gzip bombs), SSRF classification of IPv4/IPv6/mapped/6to4/NAT64/Teredo, DNS rebinding at connect time, redirect handling, size/decompression/content-type limits, conditional requests, orchestration limits, the Playwright renderer's guards (fake browser).
- **Indexing:** extraction, hashing stability, chunking bounds/overlap, embedding adapter (batching, retries, redaction), pipeline idempotency, incremental re-embedding, removals, agent isolation, model-change refusal and `reindex`, job locks, scheduled recrawls, retention.
- **Runtime:** hybrid retrieval and scoping, prompt-injection escaping, source/ui validation, refusal and malformed-output handling, LLM adapter against a mocked Anthropic HTTP API (request shape, error mapping, no leaks).
- **API:** public and admin behaviour end to end (origin rules, rate limits, size limits, timeouts, error mapping, loopback-only admin), plus `test_e2e_pipeline.py` (admin setup → crawl → index → retrieve → cited answer, including a robots-disallowed page and an injection payload).
- **Widget:** safe rendering, fail-closed UI, behaviour, source greps for banned APIs, built-bundle secret/admin scans, booting the real bundle from a `<script>` tag.
- **Repo hygiene:** `.env` ignored, no key-shaped strings, no generated artefacts tracked.

## Not covered by automation

Real Anthropic/Voyage calls (`RUN_SMOKE=1 uv run pytest -m smoke`, opt-in), a real Chromium for the Playwright fallback, real browsers/screen readers for the widget, load testing.

## Database tests

Fixtures in `wta_common.testing` (loaded via `-p wta_common.testing`) create/migrate `wta_test` once per session and truncate every table before each test. If Postgres is unreachable the tests skip with `run make up first`.
