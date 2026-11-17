# Architecture overview

## Components and data flow

1. **Setup (dashboard → admin API).** You create an *agent*, add *source* URLs, and start a crawl. The dashboard talks to `/api/admin/*` on its own origin; Next proxies it to the API's `/admin/v1/*` (loopback, local no-auth mode only).
2. **Crawl (worker).** The API creates a `crawl_runs` row and enqueues `crawl_site(run_id)` on Redis (arq). The worker takes a per-agent Redis lock, then `wta_crawler.Crawler` streams events:
   robots.txt → sitemap(s) → BFS over internal links, honouring depth/page/duration limits and a per-host delay. Each fetch is conditional (`If-None-Match`/`If-Modified-Since`) when validators are known.
3. **Index (worker).** For each event `wta_indexing.pipeline` persists in one short transaction per page:
   extract main content (trafilatura, lxml fallback) → normalise → SHA-256 → `page_versions` (new row only if the *content hash* changed) → heading-aware chunks → embeddings (only for chunk hashes not already embedded for that agent; otherwise vectors are copied) → delete the previous version's chunks.
   Skipped URLs are stored with a reason (`skipped_urls`); 404/410/401/403/robots/noindex remove a page from the index.
4. **Answer (API).** `POST /public/v1/agents/{public_id}/chat` → origin/rate-limit/body checks → conversation history → `AgentRuntime`: embed the query, run vector + full-text retrieval filtered by agent, fuse with RRF, build an escaped `<source>` prompt, call the LLM (JSON-schema output), validate, keep only sources the model used *and* that were retrieved, build the UI tree server-side → persist the two messages → return a `ChatResponse`.
5. **Display (widget).** Validates the response again (Zod), renders text, source links and allowlisted UI components in a Shadow DOM.

## Data model (PostgreSQL + pgvector)

`workspaces` → `agents` (public_id, published, allowed_origins, crawl_config) → `sources`, `crawl_runs`, `pages`, `page_versions`, `skipped_urls`, `chunks`, `chunk_embeddings`, `conversations`, `chat_messages`. Global: `embedding_spaces` (provider, model, dimensions, `active`; one active at a time).

- Every row carries `workspace_id` and (where relevant) `agent_id`; every query filters by them.
- `pages` keeps **separate** `last_seen_in_sitemap`, `last_crawled_at`, `origin_last_modified`, `indexed_at`. A timestamp is never evidence of changed content — `content_hash` is.
- `chunks.tsv` is a generated `tsvector('simple', text)` with a GIN index; `chunk_embeddings.embedding` is `vector(1024)` with an HNSW cosine index. `chunk_embeddings.agent_id` is denormalised so retrieval filters before ranking.
- Conversations store message text and cited source URLs only — no IP or client identifier — and are purged after `CONVERSATION_RETENTION_DAYS` by a daily worker cron.

## Change detection (layered)

1. Sitemap `lastmod` is recorded as a hint (an option to trust it for skipping exists but is **off**).
2. ETag / Last-Modified conditional GET → `304` means no body to process; the stored link list of the page is reused so discovery still continues.
3. Normalised-content hash decides whether a new version/embeddings are needed.
4. `recrawl_interval_hours` per agent, scheduled by a worker cron every 30 minutes.
5. Re-embedding is limited to unseen chunk hashes.

## Jobs

`crawl_site` is idempotent: re-running a run converges to the same state (unique `(agent, url)`, `(page, content_hash)`, `(version, chunk_index)`). One crawl per agent at a time (Redis lock). A transient embedding failure marks only that page `error` and the next crawl heals it; a fatal one (bad key, wrong dimensions) fails the run with an actionable message.

## Extension points

New LLM or embedding provider (implement the protocol, register it); new UI component (protocol in both languages + fixtures + renderer); new ingestion format (extractor behind `extract_page`); MCP/tools/workflows would sit beside `AgentRuntime` and must keep the "model names ids, server builds effects" rule.
