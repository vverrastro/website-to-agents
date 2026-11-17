import { type ChatResponseT, parseChatResponseTolerant, parseErrorResponse } from "@wta/agent-protocol";
import type { Agent, CrawlConfig, CrawlRun, PageRow, SkippedRow, Source, Status } from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api/admin${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init.headers ?? {}) },
  });
  if (res.status === 204) return undefined as T;
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const err = parseErrorResponse(body);
    throw new ApiError(res.status, err?.code ?? "error", err?.message ?? `Request failed (${res.status})`);
  }
  return body as T;
}

const json = (value: unknown): RequestInit => ({ body: JSON.stringify(value) });

export interface AgentPatch {
  name?: string;
  instructions?: string | null;
  allowed_origins?: string[];
  crawl_config?: CrawlConfig;
}

export const api = {
  status: () => request<Status>("/status"),
  listAgents: () => request<Agent[]>("/agents"),
  createAgent: (name: string) => request<Agent>("/agents", { method: "POST", ...json({ name }) }),
  getAgent: (id: string) => request<Agent>(`/agents/${id}`),
  updateAgent: (id: string, patch: AgentPatch) => request<Agent>(`/agents/${id}`, { method: "PATCH", ...json(patch) }),
  setPublished: (id: string, published: boolean) =>
    request<Agent>(`/agents/${id}/${published ? "publish" : "unpublish"}`, { method: "POST" }),
  deleteAgent: (id: string) => request<void>(`/agents/${id}`, { method: "DELETE" }),
  addSource: (id: string, url: string) =>
    request<Source>(`/agents/${id}/sources`, { method: "POST", ...json({ url }) }),
  deleteSource: (id: string, sourceId: string) =>
    request<void>(`/agents/${id}/sources/${sourceId}`, { method: "DELETE" }),
  startCrawl: (id: string) => request<CrawlRun[]>(`/agents/${id}/crawl`, { method: "POST", ...json({}) }),
  listRuns: (id: string) => request<CrawlRun[]>(`/agents/${id}/crawl-runs`),
  listPages: (id: string) => request<PageRow[]>(`/agents/${id}/pages?limit=200`),
  listSkipped: (id: string, runId?: string) =>
    request<SkippedRow[]>(`/agents/${id}/skipped${runId ? `?run_id=${runId}` : ""}`),
  /** Preview chat: the response is re-validated at this boundary like any untrusted payload. */
  async previewChat(id: string, message: string, conversationId?: string): Promise<ChatResponseT> {
    const raw = await request<unknown>(`/agents/${id}/preview-chat`, {
      method: "POST",
      ...json({ message, ...(conversationId ? { conversation_id: conversationId } : {}) }),
    });
    const parsed = parseChatResponseTolerant(raw);
    if (!parsed.ok) throw new ApiError(502, "invalid_response", "The server returned an invalid response.");
    return parsed.value;
  },
};

export function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : "Something went wrong.";
}
