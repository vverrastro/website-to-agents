# 0003 — Agent response protocol and A2UI-style rendering safety

Status: accepted

## Context
Answers need text, citations and optionally richer UI, shown on arbitrary third-party sites. Model output and crawled content are untrusted; the widget must never execute model-generated HTML or JavaScript.

## Decision
- A **versioned contract** (`protocol_version: "1"`, `ui.version: "1"`) with `answer`, `sources`, optional `ui`, reserved `actions`, `usage`, and an error envelope. Documented in `docs/architecture/agent-protocol.md`.
- **Closed component allowlist** (`text`, `link`, `button`, `list`, `card`), strict objects, http(s)-only URLs without credentials, explicit action types (`open_url`, `send_message`), bounded depth (4) and size (40 nodes). Unknown things fail validation; consumers fail closed to text + sources.
- **Model never emits UI or URLs.** The LLM may only name retrieved source ids, short labels and follow-up questions; the server maps ids to URLs and builds the tree (`ui.py`). Labels that look like URLs are replaced by the page title so a link cannot impersonate another destination.
- **Validated three times**: Pydantic on the API, Zod in the SDK (a bad `ui` degrades to text + sources; a bad text/sources payload is an error), and again at render time. Both implementations are checked against shared JSON fixtures in CI.
- **Rendering**: vanilla DOM APIs only (`textContent`, `setAttribute` on known attributes), Shadow DOM, constructable stylesheets (works under a strict host CSP), no `innerHTML`/`eval`/`new Function` (Zod runs in jitless mode on host pages). Source-level tests forbid the dangerous APIs; bundle tests forbid secrets and admin paths.
- Streaming is out of scope for v1.

## Consequences
- The UI is less expressive than a free-form generative UI; new components require a protocol change in both languages plus fixtures.
- Keeping two schema implementations costs maintenance but is guarded by the shared fixtures (which caught a divergence on `protocol_version`).
- `actions` is reserved: tools/MCP can later be added behind explicit server-side allowlists without changing the safety model.
