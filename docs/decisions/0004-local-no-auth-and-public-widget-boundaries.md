# 0004 — Local no-auth mode and public widget security boundaries

Status: accepted

## Context
The MVP runs on a developer machine without user accounts, but "no auth" must not mean an unrestricted privileged API, and the widget endpoint is public and potentially hostile.

## Decision
- **Explicit, fail-closed no-auth.** `LOCAL_NO_AUTH` defaults to false; with `APP_ENV=production` the application refuses to start if it is true. Docker ports and the API/dashboard bind to `127.0.0.1`.
- **Separate surfaces.** `/admin/v1/*` is mounted only in local no-auth mode (so it is a 404 otherwise) and, per request, only answers loopback peers (forwarded headers are ignored). The dashboard reaches it through a same-origin Next rewrite: no CORS on the admin API and no secret in browser code. Swagger/OpenAPI are disabled in production.
- **Public runtime = two operations**: `GET …/config` and `POST …/chat` on `/public/v1/agents/{public_id}`. The agent must be *published*; unknown and unpublished agents are indistinguishable (404).
- **Origin allowlist per agent** with a deliberate development-only exception for `localhost`/`127.0.0.1`. Dynamic CORS echoes only an allowed origin, never uses credentials, and `Content-Type: application/json` is required so browsers preflight. Origins are normalised on write and on compare. The docs and code state that this is abuse reduction, **not authentication**: `Origin` can be forged by non-browser clients, and the public agent id is only an identifier.
- **Abuse controls**: per-IP+agent rate limit, per-agent daily quota (only requests that pass the IP limit consume it), body-size cap enforced while streaming, concurrency semaphore, request timeout, strict schemas, generic errors with request ids, security headers (HSTS in production). The limiter fails closed if Redis is down. `X-Forwarded-For` is ignored unless `TRUSTED_PROXY_HOPS` says how many proxies are trusted.
- **Privacy**: conversations store text and cited URLs only; a daily job purges them after `CONVERSATION_RETENTION_DAYS`.

## Consequences
- Anyone with local access to the machine can administer it — acceptable for development, forbidden otherwise.
- Before an internet-facing deployment: real dashboard authentication/authorisation, HTTPS, per-tenant quotas, WAF, and a review of `docs/architecture/threat-model.md`.
