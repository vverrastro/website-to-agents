# 0005 — Crawler SSRF protections and deterministic change detection

Status: accepted

## Context
The crawler fetches user-supplied URLs (a high-risk boundary) and must keep an index fresh without an LLM and without re-embedding unchanged content.

## Decision
**SSRF**
- Only `http`/`https`; no credentials in URLs; hostnames such as `localhost`, `*.local`, `*.internal` rejected early.
- DNS is resolved and **every** answer must be a public unicast address (loopback, private, link-local, CGNAT, multicast, reserved, unspecified, cloud-metadata ranges, IPv4-mapped, 6to4, NAT64 and Teredo are refused; IP literals are validated directly, independent of the resolver).
- Validation happens twice: a pre-flight check before each request/redirect hop, and again **inside the network backend at connect time**, connecting to the validated IP literal while TLS still verifies the hostname — so a DNS answer that changes in between (rebinding) cannot reach an internal address. Unix sockets are refused; proxy environment variables are ignored.
- Redirects are followed manually (limit 5), revalidated per hop, and must stay on the same site (`www.` aside).
- Limits on response bytes (counted *after* decompression), content types, per-operation and per-hop wall-clock time, total crawl duration, pages and depth. robots.txt is honoured (401/403/5xx on robots.txt ⇒ disallow all; 404/410 ⇒ allow), with `Crawl-delay` and a per-host delay. XML is parsed without entities, DTDs or network access; gzip sitemaps are bounded.
- Playwright is optional and off by default. When enabled: no downloads or service workers, every request goes through the same guard, and it runs only for near-empty client-rendered shells or pages configured as `rendered`. Residual risk: the browser resolves DNS itself.

**Change detection (layered)** — sitemap `lastmod` as a recorded hint (never trusted by default) → conditional GET (ETag/Last-Modified) → normalised-content SHA-256 as the source of truth → scheduled recrawl per agent → re-embed only unseen chunk hashes. `304` responses reuse the stored link list so unchanged pages still lead to discovery. Distinct timestamps are kept for sitemap sighting, crawl, origin modification and indexing. A page that now returns 404/410/401/403, is disallowed by robots or marked `noindex` is removed from the index and the reason is audited in `skipped_urls`.

## Consequences
- Local sites (including `localhost`) cannot be crawled; tests use mock transports and an injectable resolver.
- Some legitimate sites that serve different content to bots, or require JS without Playwright enabled, will index poorly; the Pages tab explains skips.
- The heuristics (trap detection, tracking-parameter list, JS-shell detection) are conservative and configurable in code, not yet per site in the UI.
