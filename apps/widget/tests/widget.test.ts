import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mountWidget } from "../src/widget";

const U1 = "3f0c2a4e-8b1d-4c6a-9e57-1a2b3c4d5e6f";
const U2 = "7a9d1e2f-4b3c-4d5e-8f60-0a1b2c3d4e5f";
const cfg = { agentId: "pub_abcdef123456", apiBase: "https://api.example.com" };

const reply = (over: Record<string, unknown> = {}) => ({
  protocol_version: "1",
  conversation_id: U1,
  message_id: U2,
  answer: "The Pro plan costs 20 euros.",
  sources: [{ id: "s1", title: "Pricing", url: "https://acme.test/pricing" }],
  ...over,
});
const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });

function fakeFetch(handlers: { chat?: () => Response | Promise<Response>; config?: () => Response }) {
  return vi.fn(async (url: string | URL | Request, _init?: RequestInit) => {
    const u = String(url);
    if (u.endsWith("/config")) return handlers.config?.() ?? json(200, { protocol_version: "1", name: "Acme bot" });
    return handlers.chat?.() ?? json(200, reply());
  });
}

const $ = <T extends Element>(root: ShadowRoot, sel: string) => root.querySelector(sel) as T;
const flush = () => new Promise((r) => setTimeout(r, 0));

async function send(root: ShadowRoot, text: string) {
  $<HTMLInputElement>(root, ".input").value = text;
  $<HTMLFormElement>(root, ".form").dispatchEvent(new Event("submit", { cancelable: true }));
  await flush();
  await flush();
}

beforeEach(() => {
  document.body.replaceChildren();
});
afterEach(() => vi.restoreAllMocks());

describe("mountWidget", () => {
  it("renders inside a shadow root and makes no API call until opened", () => {
    const fetchImpl = fakeFetch({});
    const w = mountWidget(cfg, { fetchImpl: fetchImpl as unknown as typeof fetch, shadowMode: "closed" });
    const host = document.getElementById("wta-widget-host") as HTMLElement;
    expect(host.shadowRoot).toBeNull(); // closed: host-page scripts cannot reach into it
    expect(w.root.querySelector(".launcher")).not.toBeNull();
    expect(host.children).toHaveLength(0); // nothing leaks into the light DOM
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("opens, closes and exposes state to assistive tech", async () => {
    const fetchImpl = fakeFetch({});
    const w = mountWidget(cfg, { fetchImpl: fetchImpl as unknown as typeof fetch, shadowMode: "open" });
    const launcher = $<HTMLButtonElement>(w.root, ".launcher");
    const panel = $<HTMLElement>(w.root, ".panel");
    expect(panel.hidden).toBe(true);
    expect(launcher.getAttribute("aria-expanded")).toBe("false");
    launcher.click();
    await flush();
    expect(panel.hidden).toBe(false);
    expect(launcher.getAttribute("aria-expanded")).toBe("true");
    expect($(w.root, ".title").textContent).toBe("Acme bot"); // config fetched on first open
    expect(panel.getAttribute("role")).toBe("dialog");
    expect($(w.root, ".log").getAttribute("aria-live")).toBe("polite");
    w.root.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(panel.hidden).toBe(true);
    launcher.click();
    launcher.click();
    expect(fetchImpl.mock.calls.filter(([u]) => String(u).endsWith("/config"))).toHaveLength(1);
  });

  it("sends a message and shows the answer with its sources", async () => {
    const fetchImpl = fakeFetch({});
    const w = mountWidget(cfg, { fetchImpl: fetchImpl as unknown as typeof fetch, shadowMode: "open" });
    w.open();
    await send(w.root, "How much is Pro?");
    const texts = Array.from(w.root.querySelectorAll(".msg")).map((m) => m.textContent);
    expect(texts.some((t) => t === "How much is Pro?")).toBe(true);
    expect(w.root.querySelector(".msg.bot .answer")?.textContent).toBe("The Pro plan costs 20 euros.");
    expect(w.root.querySelector<HTMLAnchorElement>(".sources a")?.href).toBe("https://acme.test/pricing");
    expect(w.root.querySelector(".typing")).toBeNull();
    expect($<HTMLInputElement>(w.root, ".input").disabled).toBe(false);
  });

  it("keeps the conversation id across turns", async () => {
    const fetchImpl = fakeFetch({});
    const w = mountWidget(cfg, { fetchImpl: fetchImpl as unknown as typeof fetch, shadowMode: "open" });
    w.open();
    await send(w.root, "one");
    await send(w.root, "two");
    const chats = fetchImpl.mock.calls.filter(([u]) => String(u).endsWith("/chat"));
    const bodies = chats.map(([, init]) => JSON.parse((init as RequestInit).body as string));
    expect(bodies[0]).toEqual({ message: "one" });
    expect(bodies[1]).toEqual({ message: "two", conversation_id: U1 });
  });

  it("shows the visitor's own markup as text", async () => {
    const w = mountWidget(cfg, { fetchImpl: fakeFetch({}) as unknown as typeof fetch, shadowMode: "open" });
    w.open();
    await send(w.root, "<img src=x onerror=alert(1)>");
    expect(w.root.querySelector(".msg.user img")).toBeNull();
    expect(w.root.querySelector(".msg.user")?.textContent).toBe("<img src=x onerror=alert(1)>");
  });

  it("ignores empty input and prevents double submission while busy", async () => {
    let release: (r: Response) => void = () => {};
    const pending = new Promise<Response>((res) => {
      release = res;
    });
    const fetchImpl = fakeFetch({ chat: () => pending as unknown as Response });
    const w = mountWidget(cfg, { fetchImpl: fetchImpl as unknown as typeof fetch, shadowMode: "open" });
    w.open();
    await send(w.root, "   ");
    expect(fetchImpl.mock.calls.filter(([u]) => String(u).endsWith("/chat"))).toHaveLength(0);
    await send(w.root, "first");
    expect($<HTMLInputElement>(w.root, ".input").disabled).toBe(true);
    await send(w.root, "second");
    release(json(200, reply()));
    await flush();
    await flush();
    expect(fetchImpl.mock.calls.filter(([u]) => String(u).endsWith("/chat"))).toHaveLength(1);
  });

  it.each([
    [429, { error: { code: "rate_limited", message: "stack trace here" } }, "too quickly"],
    [403, { error: { code: "origin_not_allowed", message: "x" } }, "isn't enabled for this website"],
    [404, { error: { code: "agent_not_found", message: "x" } }, "not available"],
    [500, { error: { code: "internal_error", message: "Traceback (most recent call last)" } }, "Something went wrong"],
  ])("shows a friendly message for HTTP %i without leaking server text", async (status, body, expected) => {
    const fetchImpl = fakeFetch({ chat: () => json(status, body, { "retry-after": "5" }) });
    const w = mountWidget(cfg, { fetchImpl: fetchImpl as unknown as typeof fetch, shadowMode: "open" });
    w.open();
    await send(w.root, "hi");
    const err = w.root.querySelector(".msg.error")?.textContent ?? "";
    expect(err).toContain(expected);
    expect(err).not.toContain("Traceback");
    expect(err).not.toContain("stack trace");
  });

  it("handles network failure and invalid responses", async () => {
    const down = vi.fn().mockRejectedValue(new TypeError("boom"));
    const w1 = mountWidget(cfg, { fetchImpl: down as unknown as typeof fetch, shadowMode: "open" });
    w1.open();
    await send(w1.root, "hi");
    expect(w1.root.querySelector(".msg.error")?.textContent).toContain("Can't reach");

    const bad = fakeFetch({ chat: () => json(200, { html: "<script>alert(1)</script>" }) });
    const w2 = mountWidget(cfg, { fetchImpl: bad as unknown as typeof fetch, shadowMode: "open" });
    w2.open();
    await send(w2.root, "hi");
    expect(w2.root.querySelector(".msg.error")).not.toBeNull();
    expect(w2.root.querySelector("script")).toBeNull();
  });

  it("follow-up buttons send a message; open_url buttons open safely", async () => {
    const ui = {
      version: "1",
      root: {
        type: "card",
        children: [
          { type: "button", label: "Free plan?", action: { type: "send_message", text: "Is there a free plan?" } },
          { type: "button", label: "Open", action: { type: "open_url", url: "https://acme.test/x" } },
        ],
      },
    };
    const fetchImpl = fakeFetch({ chat: () => json(200, reply({ ui })) });
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    const w = mountWidget(cfg, { fetchImpl: fetchImpl as unknown as typeof fetch, shadowMode: "open" });
    w.open();
    await send(w.root, "hi");
    const [followUp, openBtn] = Array.from(w.root.querySelectorAll<HTMLButtonElement>("button.chip"));
    openBtn?.click();
    expect(open).toHaveBeenCalledWith("https://acme.test/x", "_blank", "noopener,noreferrer");
    followUp?.click();
    await flush();
    await flush();
    const sent = fetchImpl.mock.calls
      .filter(([u]) => String(u).endsWith("/chat"))
      .map(([, init]) => JSON.parse((init as RequestInit).body as string).message);
    expect(sent).toEqual(["hi", "Is there a free plan?"]);
  });

  it("mounting twice leaves a single widget; destroy removes it", () => {
    mountWidget(cfg, { shadowMode: "open" });
    const w = mountWidget(cfg, { shadowMode: "open" });
    expect(document.querySelectorAll("#wta-widget-host")).toHaveLength(1);
    w.destroy();
    expect(document.getElementById("wta-widget-host")).toBeNull();
  });
});
