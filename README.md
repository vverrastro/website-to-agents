# Website-to-Agents

Turn a public website into an AI agent that answers visitor questions **from the site's own pages**, cites its sources, and can be embedded as a chat widget.

- **Deterministic ingestion** – discovery (robots.txt, sitemaps, links, canonicals), SSRF-safe fetching, extraction, chunking and change detection run **without any LLM or LLM key**.
- **Cited RAG answers** – hybrid retrieval (pgvector + PostgreSQL full-text) → Claude → answer + the sources actually used. If the pages don't contain the answer, the agent says so.
- **Safe structured UI** – an A2UI-inspired, versioned, schema-validated contract. The widget renders only allowlisted components and never executes model-generated HTML or JavaScript.
- **Swappable providers** – the LLM (Anthropic first) and embeddings (Voyage first) sit behind interfaces; business logic never imports a vendor SDK.

> **Status: local-development MVP.** There is **no authentication** by design (see [ADR 0004](docs/decisions/0004-local-no-auth-and-public-widget-boundaries.md)). Do not expose the API or dashboard to a network. Production deployment, auth, multi-user, billing and MCP/actions are out of scope — see [Known limitations](#known-limitations).

## Architecture

```
                 ┌────────────── Agent Studio (Next.js, 127.0.0.1:3000) ──────────────┐
 you ───────────▶│ same-origin proxy /api/admin/* ─────────────┐                      │
                 └─────────────────────────────────────────────┼──────────────────────┘
                                                               ▼  (loopback only, dev only)
 visitor ─▶ customer site ─▶ widget.js (Shadow DOM)   ┌──────────────────┐   enqueue    ┌─────────────┐
                    │                                 │  FastAPI (8000)  │────────────▶ │ arq worker  │
                    └── POST /public/v1/agents/{id}/chat ─▶ admin + public routers     │ crawl+index │
                                                      └───────┬──────────┘             └──────┬──────┘
                                              retrieval + LLM │                               │ crawler (SSRF-safe)
                                                              ▼                               ▼
                                   PostgreSQL + pgvector  ◀───────── pages · versions · chunks · embeddings
                                   Redis (jobs, rate limits, crawl locks)
```

| Path | What it is |
|---|---|
| `apps/dashboard` | Agent Studio (Next.js 16, Tailwind): agents, sources, crawls, pages, test chat, embed snippet |
| `apps/widget` | Embeddable widget: vanilla TypeScript, Shadow DOM, one `widget.js` bundle |
| `packages/agent-protocol` | Zod schemas of the response protocol + fixtures shared with the Python tests |
| `packages/widget-sdk` | Public-API client and embed-config validation |
| `services/common` | Settings, SQLAlchemy models, logging, protocol (Pydantic), retention |
| `services/crawler` | URL policy, robots, sitemaps, SSRF guard, fetcher, orchestrator, Playwright fallback |
| `services/indexing` | Extraction, chunking, embeddings (Voyage + fake), pipeline, arq worker, re-index |
| `services/agent-runtime` | Hybrid retrieval, prompt construction, LLM interface (Anthropic + fake), UI building |
| `services/api` | FastAPI: public widget API and (dev-only) admin API |
| `infrastructure/` | Alembic migrations, Postgres init SQL |
| `docs/` | Architecture notes, [ADRs](docs/decisions), development guides |

More detail: [docs/architecture/overview.md](docs/architecture/overview.md).

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Docker + Compose v2 | recent | runs PostgreSQL (pgvector) and Redis |
| Node.js + npm | ≥ 22 | npm workspaces |
| [uv](https://docs.astral.sh/uv/) | recent (developed with 0.13) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` (installs to `~/.local/bin`) or `brew install uv`; manages Python ≥ 3.12 |

## Quick start

```bash
git clone <this repo> && cd website-to-agents

cp .env.example .env          # then edit: see "Configuration" (no keys needed to try it)
make install                  # uv sync + npm install
make up                       # PostgreSQL + Redis on 127.0.0.1 (waits until healthy)
make migrate                  # create the schema
make seed                     # optional: a demo agent pointing at https://example.com/
```

Run each of these in its own terminal:

```bash
make api          # FastAPI        http://127.0.0.1:8000   (docs at /docs in development)
make worker       # crawl + index worker
make dashboard    # Agent Studio   http://127.0.0.1:3000
make widget       # widget demo    http://127.0.0.1:5174
```

Check the stack: `curl 127.0.0.1:8000/healthz` (process is up) and `curl 127.0.0.1:8000/readyz` (Postgres, Redis, pgvector, migrations, embedding config).

### First agent, step by step

1. Open <http://127.0.0.1:3000> → **Create agent**.
2. **Sources** tab → add a public website (e.g. `https://example.com/`) → **Start crawl**. Watch the run finish; the **Pages** tab shows indexed pages and every skipped URL with its reason.
3. **Test chat** tab → ask a question. Answers cite the pages they used.
4. **Publish** the agent, then open the **Embed** tab for the snippet.
5. Try it as a visitor would: open `http://127.0.0.1:5174/?agent=<public id>` (the demo page loads the widget exactly like the snippet does). `http://localhost` / `127.0.0.1` origins are accepted in development; for a real site list its origin under **Settings → Allowed origins**.

### Embed snippet

```html
<script src="https://YOUR-WIDGET-HOST/widget.js"
        data-agent-id="pub_xxxxxxxxxxxx"
        data-api-base="https://YOUR-API-HOST" async></script>
```

It contains only the **public** agent id and public URLs — never a key or token. The id identifies the agent; it does **not** authenticate anyone.

## Configuration

All configuration is server-side environment (`.env`, never committed). `.env.example` lists every variable with placeholders.

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `development` | `development` \| `test` \| `production` |
| `LOCAL_NO_AUTH` | `false` (`true` in `.env.example`) | Enables the unauthenticated admin API. **The app refuses to start if this is true with `APP_ENV=production`.** |
| `DATABASE_URL`, `REDIS_URL` | local compose | Connections (loopback) |
| `LLM_PROVIDER` | `anthropic` | `anthropic` or `fake` (key-less dev; refused in production) |
| `LLM_MODEL` | `claude-opus-5-5` | Blank = this built-in default (defined once in `wta_common/settings.py`) |
| `LLM_EFFORT` | _(model default)_ | `low`…`max` |
| `LLM_REFUSAL_FALLBACK` | `true` | Anthropic server-side refusal fallback |
| `ANTHROPIC_API_KEY` | – | From <https://console.anthropic.com> |
| `EMBEDDING_PROVIDER` | `voyage` | `voyage` or `fake` |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSIONS` | `voyage-4-lite` / `1024` | Dimensions must match the schema's `vector(1024)` |
| `VOYAGE_API_KEY` | – | From <https://dash.voyageai.com> |
| `PUBLIC_RATE_LIMIT_PER_MINUTE` | `20` | Per client IP and agent |
| `PUBLIC_DAILY_QUOTA_PER_AGENT` | `2000` | Shared by all visitors of an agent |
| `PUBLIC_MAX_BODY_BYTES` / `PUBLIC_MAX_CONCURRENT_REQUESTS` / `PUBLIC_REQUEST_TIMEOUT_SECONDS` | `16384` / `8` / `45` | Abuse limits |
| `TRUSTED_PROXY_HOPS` | `0` | Proxies in front of the API; `X-Forwarded-For` is ignored when 0 |
| `CONVERSATION_RETENTION_DAYS` | `30` | Daily purge of old conversations (`0` disables) |
| `CRAWLER_USER_AGENT` | `WebsiteToAgentsBot/0.1` | Sent to sites; also the robots.txt token |
| `CRAWLER_RENDERING_ENABLED` | `false` | JavaScript-rendering fallback (needs Playwright, see below) |

**Trying it without keys:** set `LLM_PROVIDER=fake` and `EMBEDDING_PROVIDER=fake`. Crawling, indexing, retrieval, citations and the widget all work; answers are extractive (they quote the best page) and embeddings are a toy hash — useful for a demo, not for quality. Switch back to the real providers before judging answer quality, then run `make reindex` (a different embedding model is never mixed silently; see [ADR 0002](docs/decisions/0002-provider-abstractions.md)).

## Everyday commands

| Command | What it does |
|---|---|
| `make test` | pytest (Python) + vitest (TypeScript) — needs `make up` for the DB-backed tests |
| `make lint` | ruff check/format-check + Biome |
| `make format` | apply ruff + Biome fixes |
| `make typecheck` | mypy (strict) + `tsc` |
| `make check` | lint + typecheck + test |
| `make build` | build the dashboard and the widget bundle |
| `make reindex` | re-embed every chunk into a new embedding model, then switch to it |
| `RUN_SMOKE=1 uv run pytest -m smoke` | opt-in smoke tests with **real** keys (costs money) |

Automated tests never call paid APIs: providers are mocked (`httpx` mock transports, deterministic fakes). DB-backed tests use a separate `wta_test` database and are skipped with a clear message if Postgres isn't running.

### JavaScript-rendered sites (optional)

The crawler renders a page with Playwright only when its static HTML has almost no text *and* looks like a client-rendered shell, or when the page is listed under the agent's render URLs. It is **off by default**.

```bash
uv sync --all-packages --extra render     # note: a plain `make install` removes the extra again
uv run playwright install chromium
# then set CRAWLER_RENDERING_ENABLED=true in .env and restart the worker
```

The browser is restricted (downloads, service workers and non-http(s) requests blocked; every request is validated by the SSRF guard) — see [ADR 0005](docs/decisions/0005-crawler-ssrf-and-change-detection.md) for the residual risk.

## Shutdown

Stop the four processes with `Ctrl-C`, then:

```bash
make down                  # stops PostgreSQL and Redis; data is kept in the `pgdata` volume
docker compose down -v     # …or also delete the data
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `docker compose up` → `unauthorized: incorrect username or password` | Stale Docker Hub login. `docker logout` (images are public), or `docker login` again. |
| `make migrate` / API can't connect | `make up` first; check `docker compose ps`. Ports are bound to `127.0.0.1`; a local Postgres on 5432 will conflict (change `POSTGRES_PORT` and `DATABASE_URL`). |
| `/readyz` → `migrations not at head` | `make migrate`. |
| `/readyz` → `EMBEDDING_DIMENSIONS=… != schema vector(1024)` | Keep 1024, or add a migration for the new size and re-index. |
| Dashboard shows "API unreachable" / 500 on `/api/admin/*` | The API must be on `API_INTERNAL_URL` (default `http://127.0.0.1:8000`). **Next bakes rewrite targets at build time** — after changing it, restart `make dashboard` (dev) or rebuild. |
| Test chat: "ANTHROPIC_API_KEY is not set" | Put the key in `.env` and restart the API, or use `LLM_PROVIDER=fake`. |
| Chat: "The assistant is being updated" / `index_reindex_required` | The configured embedding model differs from the indexed one → `make reindex`. |
| Crawl says `blocked:forbidden_address` | By design: private, loopback, link-local and metadata addresses can't be crawled — including `localhost` sites. Crawl a public URL. |
| Pages "empty" / few pages | Static HTML had no extractable text (enable rendering), robots.txt disallows it, or limits were hit — the **Pages** tab lists skipped URLs with reasons. |
| Crawl stays `queued` | The worker isn't running: `make worker`. |
| `make install` removed Playwright | Re-run `uv sync --all-packages --extra render`. |
| Widget shows "isn't enabled for this website" | The page's origin isn't in the agent's allowed origins (and isn't `localhost` in development). |

## Security model (short)

- **Secrets** only in `.env` (git-ignored; tests assert it) and server-side; never in the widget, dashboard bundle, snippet or logs (log keys are redacted; provider errors are normalised and never returned verbatim).
- **Crawler** is an SSRF boundary: only http(s); every resolved IP is validated *at connect time* (DNS-rebinding safe); redirects re-validated; size/time/redirect/content-type limits; robots.txt honoured.
- **Crawled text is untrusted data**: escaped, wrapped in `<source>` tags, and the system prompt forbids following instructions found there. The model can only name source ids — URLs and UI components are built by the server.
- **Public vs admin**: widget traffic reaches only `/public/v1/agents/{id}/{config,chat}`; the admin API exists only in local no-auth mode and only answers loopback clients.
- **Public endpoint controls**: published-only agents, origin allowlist (not authentication), per-IP rate limit, daily quota, body-size/concurrency/time limits, strict schemas, generic errors, security headers.

Details: [docs/architecture/threat-model.md](docs/architecture/threat-model.md). **Before any internet-facing deployment**, implement real dashboard authentication/authorisation and review the public-widget threat model.

## Known limitations

- No authentication, orgs, billing, or multi-tenant UI (data is workspace/agent-scoped in the schema, one local workspace used).
- Real providers are **not exercised by the automated suite**: the Anthropic adapter is verified against a mocked HTTP API, the Voyage adapter likewise; `RUN_SMOKE=1` tests exist but were not run in CI/dev for lack of keys. `voyage-4-lite` and the Anthropic request shapes follow the project brief and the current SDK, unverified against the live services.
- Playwright fallback is implemented and unit-tested with a fake browser; it has not been run against a real Chromium in this repo's checks. The browser resolves DNS itself, so DNS rebinding inside the browser can't be fully blocked by per-request validation.
- Retrieval quality (RRF, no reranker, no relevance threshold) is untuned; the full-text side uses the `simple` configuration with a small stop-word list.
- No streaming responses; no PDF/non-HTML ingestion; no `api` render mode beyond the stored flag; stale pages that vanish from a site without a 404 are not garbage-collected.
- Rate limits are per IP and agent; there is no per-visitor identity.
- Widget accessibility was reviewed by construction and jsdom tests, not with a screen reader / real browsers.

Recommended next steps: dashboard auth (then expose the admin API behind it), run the smoke tests with real keys and tune retrieval on a real corpus, streaming answers, reranking, per-agent quotas in the UI, MCP/tools and workflows via the same provider interfaces.
