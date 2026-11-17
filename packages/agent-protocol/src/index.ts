export * from "./schemas";

import { z } from "zod";

/**
 * Call once in code that runs on third-party pages. Zod's JIT compiler uses `new Function`, which
 * a strict host CSP forbids; jitless mode validates identically without dynamic code generation.
 */
export function configureForUntrustedHosts(): void {
  z.config({ jitless: true });
}

import { ChatResponse, type ChatResponseT, ErrorResponse } from "./schemas";

export type ParseResult<T> = { ok: true; value: T } | { ok: false; error: string };

/** Validate a server response at the trust boundary. Never throws; never echoes the payload. */
export function parseChatResponse(data: unknown): ParseResult<ChatResponseT> {
  const parsed = ChatResponse.safeParse(data);
  if (parsed.success) return { ok: true, value: parsed.data };
  return { ok: false, error: "invalid_response" };
}

/**
 * Like parseChatResponse, but a response whose `ui` is invalid degrades to text + sources
 * instead of being rejected: the answer and its source links remain fully usable.
 */
export function parseChatResponseTolerant(data: unknown): ParseResult<ChatResponseT> {
  const strict = parseChatResponse(data);
  if (strict.ok) return strict;
  if (data && typeof data === "object" && "ui" in data) {
    return parseChatResponse({ ...(data as Record<string, unknown>), ui: null });
  }
  return strict;
}

export function parseErrorResponse(data: unknown): { code: string; message: string; requestId?: string } | null {
  const parsed = ErrorResponse.safeParse(data);
  if (!parsed.success) return null;
  const { code, message, request_id } = parsed.data.error;
  return { code, message, ...(request_id ? { requestId: request_id } : {}) };
}
