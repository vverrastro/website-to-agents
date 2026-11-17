# Threat model — public widget and crawler (MVP)

Scope: the internet-facing surface a *deployed* version would have, assessed against the local MVP code. The MVP itself is local-only and unauthenticated; see [ADR 0004](../decisions/0004-local-no-auth-and-public-widget-boundaries.md).

| Threat | Mitigation in code | Residual risk |
|---|---|---|
| Visitor abuses the chat endpoint for free LLM use / cost | Published-only agents; origin allowlist (browsers); per-IP+agent rate limit; per-agent daily quota; body ≤ 16 KB; concurrency cap; request timeout; Redis failure ⇒ refuse (fail closed). Rejected requests don't burn the shared quota. | Origin/Referer and the public id are not authentication; any HTTP client can omit/forge `Origin`. Distributed abuse needs real auth/quotas/WAF before production. |
| Direct calls to admin functions | Admin router is not mounted unless `LOCAL_NO_AUTH` (never in production) and answers only loopback peers; forwarded headers ignored; public router has two routes only. | Local no-auth admin is intentionally unauthenticated: never expose it. |
| Prompt injection via crawled pages | Page text HTML-escaped inside `<source>` tags (cannot close/open tags); system prompt marks it as data and forbids following it; model outputs ids only; server builds URLs/UI; tool use is not available. | The model can still be *persuaded* to give a wrong answer from poisoned pages (content integrity, not code execution). Source citations let users verify. |
| Malicious model/UI output (XSS) | Strict schema + allowlist validated on the API and again in the widget; text via `textContent`; links re-checked and `noopener noreferrer`; source tests forbid `innerHTML`/`eval`. | — |
| SSRF through the crawler | http(s) only; every resolved IP validated at connect time; redirects revalidated; private/loopback/link-local/metadata/multicast/reserved + mapped/6to4/NAT64/Teredo blocked; byte/time/redirect/content-type limits. | Playwright resolves DNS itself (rebinding inside the browser is only mitigated by request-time checks); keep rendering off unless needed. |
| Crawl abuse (hostile site, traps, bombs) | robots.txt + crawl-delay; page/depth/duration caps; trap heuristics; XML hardened (no entities/DTD/network); bounded gzip/size; per-agent crawl lock. | A site can still waste the configured budget. |
| Cross-agent data leakage | `workspace_id`/`agent_id` on every row and query; retrieval filters before ranking; conversation ids are scoped and not adopted across agents (tested). | One local workspace in the MVP. |
| Secret exposure | Env-only secrets; redacted logs; normalised provider errors; widget/dashboard bundles tested for secrets and admin paths; repo hygiene test. | — |
| Personal data in logs | Conversations store text + source URLs only (no IP/UA); retention purge job; request logs carry ids, not bodies. | Visitors may type personal data into messages; retention default 30 days. |
| Clickjacking / content sniffing on the API & dashboard | `X-Frame-Options`, `frame-ancestors 'none'`, `nosniff`, `Referrer-Policy`, CSP; HSTS in production. | Dashboard CSP allows inline scripts (Next hydration) — tighten with nonces before production. |

**Before an internet-facing deployment:** real dashboard authN/Z (then expose admin behind it), HTTPS everywhere, WAF/CDN rate limiting, per-tenant quotas, review of the retention policy, secret management, and re-run this review.
