import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  AgentPublicConfig,
  ChatRequest,
  ChatResponse,
  ErrorResponse,
  parseChatResponse,
  parseChatResponseTolerant,
  parseErrorResponse,
  UiDocument,
} from "../src";

interface Fixture {
  schema: "ui_document" | "chat_request" | "chat_response" | "error_response" | "agent_config";
  valid: boolean;
  description: string;
  payload: unknown;
}

const dir = join(__dirname, "..", "fixtures");
const fixtures: [string, Fixture][] = readdirSync(dir)
  .filter((f) => f.endsWith(".json"))
  .sort()
  .map((f) => [f, JSON.parse(readFileSync(join(dir, f), "utf8")) as Fixture]);

const schemas = {
  ui_document: UiDocument,
  chat_request: ChatRequest,
  chat_response: ChatResponse,
  error_response: ErrorResponse,
  agent_config: AgentPublicConfig,
} as const;

describe("shared protocol fixtures (also validated by pytest/Pydantic)", () => {
  it("has a meaningful number of fixtures of both kinds", () => {
    expect(fixtures.length).toBeGreaterThan(60);
    expect(fixtures.some(([, f]) => f.valid)).toBe(true);
    expect(fixtures.some(([, f]) => !f.valid)).toBe(true);
  });

  it.each(fixtures)("%s", (_file, fixture) => {
    const result = schemas[fixture.schema].safeParse(fixture.payload);
    expect(result.success, fixture.description).toBe(fixture.valid);
  });
});

describe("parse helpers", () => {
  const response = fixtures.find(([f]) => f.includes("chat_response-valid-full"))?.[1].payload as Record<
    string,
    unknown
  >;

  it("parses a valid response", () => {
    const r = parseChatResponse(response);
    expect(r.ok && r.value.sources.length).toBe(1);
  });

  it("never echoes the payload on failure", () => {
    const r = parseChatResponse({ ...response, answer: "SECRET".repeat(2000) });
    expect(r).toEqual({ ok: false, error: "invalid_response" });
  });

  it("tolerant parsing drops an invalid ui but keeps text and sources", () => {
    const poisoned = { ...response, ui: { version: "1", root: { type: "script", src: "https://evil.test/x.js" } } };
    expect(parseChatResponse(poisoned).ok).toBe(false);
    const tolerant = parseChatResponseTolerant(poisoned);
    expect(tolerant.ok && tolerant.value.ui).toBeNull();
    expect(tolerant.ok && tolerant.value.answer).toBe(response.answer);
    expect(tolerant.ok && tolerant.value.sources.length).toBe(1);
  });

  it("tolerant parsing still rejects a response whose text/sources are invalid", () => {
    expect(
      parseChatResponseTolerant({ ...response, sources: [{ id: "s", title: "t", url: "javascript:1" }], ui: null }).ok,
    ).toBe(false);
  });

  it("extracts error envelopes", () => {
    expect(parseErrorResponse({ error: { code: "rate_limited", message: "m", request_id: "r" } })).toEqual({
      code: "rate_limited",
      message: "m",
      requestId: "r",
    });
    expect(parseErrorResponse({ nope: 1 })).toBeNull();
  });
});
