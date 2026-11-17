import {
  type ChatResponseT,
  PROTOCOL_VERSION,
  parseChatResponseTolerant,
  parseErrorResponse,
} from "@wta/agent-protocol";
import type { WidgetConfig } from "./config";

export interface ClientError {
  code: string;
  message: string;
  status: number;
  retryAfterSeconds?: number;
}

export type ChatResult = { ok: true; response: ChatResponseT } | { ok: false; error: ClientError };
export type ConfigResult = { ok: true; name: string } | { ok: false; error: ClientError };

export interface ClientOptions {
  fetchImpl?: typeof fetch;
  timeoutMs?: number;
}

/**
 * Client for the two public runtime operations. Every response is validated at this boundary and
 * failures are returned as values (never thrown, never containing server internals).
 */
export class PublicApiClient {
  private readonly fetchImpl: typeof fetch;
  private readonly timeoutMs: number;
  private readonly base: string;

  constructor(config: WidgetConfig, options: ClientOptions = {}) {
    this.base = `${config.apiBase}/public/v1/agents/${encodeURIComponent(config.agentId)}`;
    this.fetchImpl = options.fetchImpl ?? ((...args) => fetch(...args));
    this.timeoutMs = options.timeoutMs ?? 30_000;
  }

  async getConfig(): Promise<ConfigResult> {
    const r = await this.request("/config", { method: "GET" });
    if (!r.ok) return r;
    const body = r.body as { protocol_version?: unknown; name?: unknown } | null;
    if (!body || body.protocol_version !== PROTOCOL_VERSION || typeof body.name !== "string") {
      return { ok: false, error: { code: "invalid_response", message: "Invalid response.", status: r.status } };
    }
    return { ok: true, name: body.name.slice(0, 100) };
  }

  async chat(message: string, conversationId?: string): Promise<ChatResult> {
    const payload: Record<string, string> = { message };
    if (conversationId) payload.conversation_id = conversationId;
    const r = await this.request("/chat", { method: "POST", body: JSON.stringify(payload) });
    if (!r.ok) return r;
    const parsed = parseChatResponseTolerant(r.body);
    if (!parsed.ok) {
      return { ok: false, error: { code: "invalid_response", message: "Invalid response.", status: r.status } };
    }
    return { ok: true, response: parsed.value };
  }

  private async request(
    path: string,
    init: { method: string; body?: string },
  ): Promise<{ ok: true; body: unknown; status: number } | { ok: false; error: ClientError }> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const res = await this.fetchImpl(`${this.base}${path}`, {
        ...init,
        headers: init.body ? { "Content-Type": "application/json" } : {},
        credentials: "omit", // never send the host site's cookies
        mode: "cors",
        cache: "no-store",
        referrerPolicy: "no-referrer",
        signal: controller.signal,
      });
      const body: unknown = await res.json().catch(() => null);
      if (res.ok) return { ok: true, body, status: res.status };
      const err = parseErrorResponse(body);
      const retry = Number(res.headers.get("retry-after"));
      return {
        ok: false,
        error: {
          code: err?.code ?? "http_error",
          message: err?.message ?? "Request failed.",
          status: res.status,
          ...(Number.isFinite(retry) && retry > 0 ? { retryAfterSeconds: retry } : {}),
        },
      };
    } catch (e) {
      const aborted = e instanceof DOMException && e.name === "AbortError";
      return {
        ok: false,
        error: {
          code: aborted ? "timeout" : "network_error",
          message: aborted ? "The request timed out." : "Cannot reach the assistant.",
          status: 0,
        },
      };
    } finally {
      clearTimeout(timer);
    }
  }
}

const FRIENDLY: Record<string, string> = {
  rate_limited: "You're sending messages too quickly. Please wait a moment.",
  quota_exceeded: "This assistant has reached its daily limit. Please try again later.",
  origin_not_allowed: "This assistant isn't enabled for this website.",
  agent_not_found: "This assistant is not available.",
  payload_too_large: "That message is too long.",
  invalid_request: "That message couldn't be sent.",
  assistant_busy: "The assistant is busy. Please try again shortly.",
  busy: "The assistant is busy. Please try again shortly.",
  timeout: "The assistant took too long to answer. Please try again.",
  network_error: "Can't reach the assistant right now.",
};

export function friendlyError(error: ClientError): string {
  const base = FRIENDLY[error.code] ?? "Something went wrong. Please try again.";
  return error.code === "rate_limited" && error.retryAfterSeconds
    ? `${base} (retry in ${error.retryAfterSeconds}s)`
    : base;
}
