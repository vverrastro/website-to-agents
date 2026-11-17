# Agent response protocol v1

Source of truth: `services/common/src/wta_common/protocol.py` (Pydantic, API side) and `packages/agent-protocol/src/schemas.ts` (Zod, widget/dashboard side). Both are validated against the same fixtures (`packages/agent-protocol/fixtures/`, 64 files) in pytest and vitest, so they cannot drift (this already caught one divergence during development). Decision record: [ADR 0003](../decisions/0003-agent-protocol-and-ui-safety.md).

## Request — `POST /public/v1/agents/{public_id}/chat`

```json
{ "message": "How much is the Pro plan?", "conversation_id": "uuid (optional)" }
```
`Content-Type: application/json` is required (so cross-origin calls are preflighted). Unknown fields, blank or > 2000-character messages are rejected.

## Response

```json
{
  "protocol_version": "1",
  "conversation_id": "uuid", "message_id": "uuid",
  "answer": "The Pro plan costs 20 euros per month.",
  "sources": [{ "id": "s1", "title": "Pricing", "url": "https://…", "section": "Pricing > Pro plan", "excerpt": "…" }],
  "ui": { "version": "1", "root": { "type": "card", "title": "Related", "children": [ … ] } },
  "actions": [],
  "usage": { "input_tokens": 812, "output_tokens": 96 }
}
```

- `answer` is plain text. `sources` lists only pages that were retrieved **and** used; if the pages don't contain the answer the agent says so and returns no sources.
- `ui` is optional. `actions` is reserved (always empty in the MVP).
- Streaming is not implemented in v1.

## UI components (closed allowlist)

| `type` | Properties |
|---|---|
| `text` | `text` (≤ 2000) |
| `link` | `label`, `url` (http/https, no credentials) |
| `button` | `label`, `action`: `{type:"open_url", url}` or `{type:"send_message", text}` |
| `list` | `ordered?`, `items` (≤ 10 nodes) |
| `card` | `title?`, `children` (≤ 10 nodes) |

Every object is strict (unknown properties rejected). A tree may be at most 4 deep and 40 nodes. Unknown component or action types fail validation; consumers **fail closed** (drop the UI, keep text and sources).

## Errors

```json
{ "error": { "code": "rate_limited", "message": "Too many requests. Please slow down.", "request_id": "…" } }
```
Codes include `invalid_request`, `payload_too_large`, `unsupported_media_type`, `origin_not_allowed`, `agent_not_found`, `rate_limited` / `quota_exceeded` (with `Retry-After`), `busy`, `assistant_busy`, `assistant_unavailable`, `assistant_not_configured`, `index_reindex_required`, `timeout`, `internal_error`. Messages never contain stack traces, provider text or input echoes.

## Versioning

`protocol_version` and `ui.version` are literals. A breaking change introduces `"2"`; the widget rejects unknown versions rather than guessing.
