# CLAUDE.md

Website-to-Agents: turns a public website into a RAG-powered AI agent. Deterministic crawl/index (no LLM needed), cited answers via Claude, embeddable Shadow-DOM widget, safe schema-validated UI. **Local-development MVP, no authentication by design.** User docs: `README.md`; decisions: `docs/decisions/` (read the ADR before changing a boundary).

## Repository map

- `services/` — one uv workspace, Python ≥ 3.12, src-layout packages:
  - `common` (`wta_common`): `settings.py` (all config; fails closed), `models.py` (SQLAlchemy), `protocol.py` (Pydantic response contract), `logging.py`, `retention.py`, `crawl_config.py`, `testing.py` (pytest DB fixtures)
  - `crawler` (`wta_crawler`): `ssrf.py` (**every connection goes through it**), `fetcher.py`, `urls.py`, `robots.py`, `sitemap.py`, `html_links.py`, `render.py` (Playwright, optional), `crawler.py` (event stream, no DB)
  - `indexing` (`wta_indexing`): `extract.py`, `chunking.py`, `embeddings.py` (interface + Voyage + fake + registry), `pipeline.py` (persist events, hash-based reuse), `jobs.py`/`worker.py` (arq), `reindex.py`, `testing.py` (seed helper)
  - `agent-runtime` (`wta_agent_runtime`): `llm.py` (interface, registry, fake), `anthropic_provider.py`, `providers.py`, `retrieval.py` (vector + FTS + RRF), `rag.py` (prompt, validation), `ui.py`
  - `api` (`wta_api`): `main.py` (factory, `init_state`), `public.py` (widget API), `admin.py` (local dev only), `chat_service.py`, `ratelimit.py`, `errors.py`, `middleware.py`, `health.py`, `seed.py`
- `apps/dashboard` (Next.js 16) · `apps/widget` (vanilla TS, Vite IIFE) · `packages/agent-protocol` (Zod + `fixtures/`) · `packages/widget-sdk`
- `infrastructure/database` (Alembic, `versions/`) · `docker-compose.yml` · `docs/` · `Makefile`

## Commands

Everything goes through `make` (uv lives in `~/.local/bin`; the Makefile adds it to `PATH`).

- `make install` · `make up` / `make down` · `make migrate` · `make seed`
- run: `make api` (8000) · `make worker` · `make dashboard` (3000) · `make widget` (5174)
- `make test` (pytest + vitest) · `make lint` · `make format` · `make typecheck` (mypy strict + tsc) · `make check` (all three) · `make build`
- single suites: `uv run pytest services/crawler -q`, `npm test -w @wta/widget`
- `make reindex` after changing the embedding model · `RUN_SMOKE=1 uv run pytest -m smoke` (real keys, costs money; never in routine runs)
- migrations: edit `wta_common/models.py`, then `uv run alembic -c infrastructure/database/alembic.ini revision --autogenerate --rev-id NNNN -m "…"`, **review the file** (add `import pgvector.sqlalchemy` if it uses `Vector`), `make migrate`, and confirm `alembic … check` reports no drift.

DB-backed tests (`@pytest.mark.integration`) need `make up` and use a separate `wta_test` database; they skip with a message when Postgres is down. Docker image pulls failing with `unauthorized` = stale Docker Hub login → `docker logout`.

## Environment

Copy `.env.example` → `.env` (git-ignored; never commit it, never paste real keys into tests/docs/logs). Key variables: `APP_ENV`, `LOCAL_NO_AUTH`, `DATABASE_URL`, `REDIS_URL`, `LLM_PROVIDER`/`LLM_MODEL`/`ANTHROPIC_API_KEY`, `EMBEDDING_PROVIDER`/`EMBEDDING_MODEL`/`EMBEDDING_DIMENSIONS`/`VOYAGE_API_KEY`. Key-less development: `LLM_PROVIDER=fake EMBEDDING_PROVIDER=fake`.

## Rules (do not weaken these without an ADR)

**Providers.** Business logic (RAG, API, pipeline) depends only on `LLMProvider` / `EmbeddingProvider`. Never import `anthropic`/vendor SDKs outside `anthropic_provider.py`. The default model name lives only in `wta_common/settings.py::DEFAULT_LLM_MODEL`. New provider = implement the protocol + `register_*_provider`. Errors are normalised (`LLMError`/`EmbeddingError`) and must never carry response bodies, prompts or keys. On Claude Opus/Sonnet 5.5, forced `tool_choice` and `thinking: disabled` return 400 — we use `output_config.format` (JSON schema) and omit `thinking`.

**Embeddings.** Index and query vectors must come from the same provider/model/dimensions (`EmbeddingSpace`). A different model is refused (`EmbeddingSpaceMismatch`) until `make reindex`. The column is `vector(1024)`; another size needs a migration.

**Crawler / SSRF.** Any outbound fetch must use `Fetcher` (SafeTransport validates the IP at connect time). Never add a code path that calls `httpx`/`requests`/a browser on a user-supplied URL directly. Don't loosen `is_forbidden_ip`, redirect handling, size/time limits or robots.txt handling. Playwright stays off by default and every browser request goes through `validate_url`.

**Untrusted content.** Crawled text and visitor messages are data. Retrieved text is HTML-escaped and wrapped in `<source>` tags; the system prompt forbids following instructions inside them; the LLM may only name source ids — URLs and UI nodes are built server-side (`ui.py`) and validated again by the widget. Never render model output as HTML, never `eval`, never let a response trigger a request or tool.

**API boundaries.** Public (`/public/v1/agents/{public_id}/{config,chat}`) and admin (`/admin/v1/*`) are separate routers. Admin is mounted only when `LOCAL_NO_AUTH` and not production, and only answers loopback peers (forwarded headers ignored). `LOCAL_NO_AUTH=true` with `APP_ENV=production` must keep failing at startup. Origin allowlists and the public agent id are abuse reduction, **not authentication** — never describe or use them as such. Public errors are generic (`{error:{code,message,request_id}}`), never stack traces or provider details. Every DB query is scoped by `workspace_id`/`agent_id`.

**Protocol.** One contract, two implementations: `wta_common/protocol.py` (Pydantic) and `packages/agent-protocol/src/schemas.ts` (Zod). Change both **and** add fixtures in `packages/agent-protocol/fixtures/` (each file: `schema`, `valid`, `description`, `payload`); pytest and vitest both run every fixture. Component allowlist is closed; bump `protocol_version` for breaking changes.

**Widget.** No `innerHTML`/`outerHTML`/`insertAdjacentHTML`/`eval`/`new Function`/cookies/storage (a test greps the source). Shadow DOM, textContent only, links `rel="noopener noreferrer"`. The bundle must contain no secrets and no `/admin` references (tested). Only a public agent id and API URL are ever configured.

**Secrets & logs.** Secrets only in env/`.env`. Don't log keys, Authorization headers, request bodies or secret-bearing URLs (`wta_common/logging.py` redacts sensitive keys). `tests/test_repo_hygiene.py` scans tracked files for key-shaped strings.

## Testing expectations

- Every behavioural change needs tests; security-relevant code (SSRF, escaping, origin/rate limits, protocol validation) needs adversarial cases, not just happy paths.
- No automated test may call a paid API: mock providers (`httpx2.MockTransport` for the Anthropic SDK, `httpx.MockTransport` elsewhere) or use the fakes. Real-key checks go under `-m smoke` and are opt-in.
- Run `make check` before committing; keep ruff, mypy-strict, Biome and tsc clean. Prefer fixing the cause over `noqa`/`type: ignore` (existing ones carry a reason).
- Be honest in reports: say what was run and what was not (e.g. real providers, real browser).

## Docs

Update `README.md`, the relevant `docs/` page and the ADR whenever behaviour, config or a boundary changes. New cross-cutting decisions get a new ADR in `docs/decisions/`.
