import { describe, expect, it, vi } from "vitest";
import { ConfigError, friendlyError, PublicApiClient, readScriptConfig, validateConfig } from "../src";

const U1 = "3f0c2a4e-8b1d-4c6a-9e57-1a2b3c4d5e6f";
const U2 = "7a9d1e2f-4b3c-4d5e-8f60-0a1b2c3d4e5f";
const cfg = { agentId: "pub_abcdef123456", apiBase: "https://api.example.com" };
const okBody = {
  protocol_version: "1",
  conversation_id: U1,
  message_id: U2,
  answer: "Hello",
  sources: [{ id: "s1", title: "T", url: "https://acme.test/" }],
};

const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });

describe("config", () => {
  it("accepts a valid public id and normalises the base url", () => {
    expect(validateConfig({ agentId: " pub_abcdef123456 ", apiBase: "https://api.example.com/" })).toEqual(cfg);
    expect(validateConfig({ agentId: "pub_abcdef123456", apiBase: "http://127.0.0.1:8000/prefix//" }).apiBase).toBe(
      "http://127.0.0.1:8000/prefix",
    );
  });

  it.each([
    [{ agentId: "", apiBase: "https://a.test" }],
    [{ agentId: "agent_123456", apiBase: "https://a.test" }],
    [{ agentId: "pub_x", apiBase: "https://a.test" }],
    [{ agentId: "pub_abc def 123", apiBase: "https://a.test" }],
    [{ agentId: "pub_abcdef123456", apiBase: "javascript:alert(1)" }],
    [{ agentId: "pub_abcdef123456", apiBase: "ftp://a.test" }],
    [{ agentId: "pub_abcdef123456", apiBase: "https://user:pw@a.test" }],
    [{ agentId: "pub_abcdef123456", apiBase: "" }],
    [{ agentId: "pub_abcdef123456" }],
  ])("rejects %j", (input) => {
    expect(() => validateConfig(input)).toThrow(ConfigError);
  });

  it("reads data attributes from the script tag", () => {
    const s = document.createElement("script");
    s.dataset.agentId = "pub_abcdef123456";
    s.dataset.apiBase = "https://api.example.com";
    expect(readScriptConfig(s)).toEqual(cfg);
    expect(() => readScriptConfig(null)).toThrow(ConfigError);
  });
});

describe("PublicApiClient", () => {
  it("sends a minimal, credential-free request to the public endpoint only", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(json(200, okBody));
    const r = await new PublicApiClient(cfg, { fetchImpl }).chat("hi", U1);
    expect(r.ok && r.response.answer).toBe("Hello");
    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.example.com/public/v1/agents/pub_abcdef123456/chat");
    expect(url).not.toContain("/admin");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("omit");
    expect(init.referrerPolicy).toBe("no-referrer");
    expect(JSON.parse(init.body as string)).toEqual({ message: "hi", conversation_id: U1 });
    expect(init.headers).toEqual({ "Content-Type": "application/json" });
  });

  it("omits conversation_id on the first message", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(json(200, okBody));
    await new PublicApiClient(cfg, { fetchImpl }).chat("hi");
    expect(JSON.parse((fetchImpl.mock.calls[0] as [string, RequestInit])[1].body as string)).toEqual({ message: "hi" });
  });

  it("encodes the agent id in the path", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(json(200, okBody));
    await new PublicApiClient({ ...cfg, agentId: "pub_ab/../cdef12" }, { fetchImpl }).chat("hi");
    expect((fetchImpl.mock.calls[0] as [string])[0]).toContain("pub_ab%2F..%2Fcdef12");
  });

  it("returns server errors as values, with retry-after", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(json(429, { error: { code: "rate_limited", message: "slow down" } }, { "retry-after": "12" }));
    const r = await new PublicApiClient(cfg, { fetchImpl }).chat("hi");
    expect(r).toEqual({
      ok: false,
      error: { code: "rate_limited", message: "slow down", status: 429, retryAfterSeconds: 12 },
    });
    expect(r.ok === false && friendlyError(r.error)).toContain("retry in 12s");
  });

  it("treats non-protocol responses as invalid, without leaking content", async () => {
    for (const body of [
      { answer: "x" },
      "<html>",
      null,
      { ...okBody, sources: [{ id: "s", title: "t", url: "javascript:1" }] },
    ]) {
      const fetchImpl = vi.fn().mockResolvedValue(json(200, body));
      const r = await new PublicApiClient(cfg, { fetchImpl }).chat("hi");
      expect(r.ok === false && r.error.code).toBe("invalid_response");
      expect(JSON.stringify(r)).not.toContain("javascript:");
    }
  });

  it("degrades an invalid ui to text + sources", async () => {
    const poisoned = { ...okBody, ui: { version: "1", root: { type: "script", src: "https://evil.test/x.js" } } };
    const r = await new PublicApiClient(cfg, { fetchImpl: vi.fn().mockResolvedValue(json(200, poisoned)) }).chat("hi");
    expect(r.ok && r.response.ui).toBeNull();
    expect(r.ok && r.response.sources).toHaveLength(1);
  });

  it("maps network failures and timeouts", async () => {
    const down = vi.fn().mockRejectedValue(new TypeError("Failed to fetch secret-host"));
    const r1 = await new PublicApiClient(cfg, { fetchImpl: down }).chat("hi");
    expect(r1.ok === false && r1.error).toEqual({
      code: "network_error",
      message: "Cannot reach the assistant.",
      status: 0,
    });

    const hang = vi.fn(
      (_u: string, init: RequestInit) =>
        new Promise<Response>((_res, rej) => {
          init.signal?.addEventListener("abort", () => rej(new DOMException("aborted", "AbortError")));
        }),
    );
    const r2 = await new PublicApiClient(cfg, { fetchImpl: hang as unknown as typeof fetch, timeoutMs: 20 }).chat("hi");
    expect(r2.ok === false && r2.error.code).toBe("timeout");
  });

  it("reads the public config", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(json(200, { protocol_version: "1", name: "Acme" }));
    expect(await new PublicApiClient(cfg, { fetchImpl }).getConfig()).toEqual({ ok: true, name: "Acme" });
    const bad = vi.fn().mockResolvedValue(json(200, { protocol_version: "2", name: "Acme" }));
    expect((await new PublicApiClient(cfg, { fetchImpl: bad }).getConfig()).ok).toBe(false);
  });

  it("falls back to a generic friendly message for unknown codes", () => {
    expect(friendlyError({ code: "weird", message: "stack trace here", status: 500 })).not.toContain("stack");
  });
});
