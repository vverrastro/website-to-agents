import type { ActionT, ChatResponseT, UiNodeT } from "@wta/agent-protocol";
import { describe, expect, it, vi } from "vitest";
import { renderAssistantMessage, renderNode, renderUi } from "../src/render";

const U1 = "3f0c2a4e-8b1d-4c6a-9e57-1a2b3c4d5e6f";
const U2 = "7a9d1e2f-4b3c-4d5e-8f60-0a1b2c3d4e5f";
const base = (over: Partial<ChatResponseT> = {}): ChatResponseT => ({
  protocol_version: "1",
  conversation_id: U1,
  message_id: U2,
  answer: "The Pro plan costs 20 euros.",
  sources: [{ id: "s1", title: "Pricing", url: "https://acme.test/pricing", section: "Plans", excerpt: null }],
  actions: [],
  ...over,
});
const noop = { onAction: () => {} };

function everyElement(root: Element): Element[] {
  return [root, ...Array.from(root.querySelectorAll("*"))];
}

describe("assistant message rendering", () => {
  it("renders the answer strictly as text, never as HTML", () => {
    const evil = `<img src=x onerror="alert(1)"><script>alert(2)</script><a href="javascript:alert(3)">x</a>`;
    const node = renderAssistantMessage(base({ answer: evil }), noop);
    expect(node.querySelector("img, script, a[href^='javascript']")).toBeNull();
    expect(node.querySelector(".answer")?.textContent).toBe(evil);
    for (const e of everyElement(node)) {
      expect(e.getAttributeNames().filter((a) => a.startsWith("on"))).toEqual([]);
    }
  });

  it("renders sources as safe external links with their section", () => {
    const node = renderAssistantMessage(base(), noop);
    const a = node.querySelector<HTMLAnchorElement>(".sources a");
    expect(a?.href).toBe("https://acme.test/pricing");
    expect(a?.target).toBe("_blank");
    expect(a?.rel).toBe("noopener noreferrer");
    expect(a?.textContent).toBe("Pricing");
    expect(node.querySelector(".sources .section")?.textContent).toBe(" · Plans");
  });

  it("titles with markup stay text", () => {
    const node = renderAssistantMessage(
      base({ sources: [{ id: "s1", title: "<b>bold</b>", url: "https://acme.test/", section: null, excerpt: null }] }),
      noop,
    );
    expect(node.querySelector(".sources b")).toBeNull();
    expect(node.querySelector(".sources a")?.textContent).toBe("<b>bold</b>");
  });

  it("omits the sources block when there are none; text-only answers stay usable", () => {
    const node = renderAssistantMessage(base({ sources: [] }), noop);
    expect(node.querySelector(".sources")).toBeNull();
    expect(node.textContent).toContain("20 euros");
  });
});

describe("UI tree rendering", () => {
  const ui = {
    version: "1",
    root: {
      type: "card",
      title: "Related",
      children: [
        { type: "text", text: "hello <b>x</b>" },
        { type: "list", ordered: false, items: [{ type: "link", label: "Pricing", url: "https://acme.test/pricing" }] },
        {
          type: "list",
          items: [{ type: "button", label: "More?", action: { type: "send_message", text: "tell me more" } }],
        },
        { type: "button", label: "Open", action: { type: "open_url", url: "https://acme.test/x" } },
      ],
    },
  };

  it("renders allowlisted components", () => {
    const wrap = renderUi(ui, noop);
    expect(wrap?.querySelector(".ui-card-title")?.textContent).toBe("Related");
    expect(wrap?.querySelector(".ui-text")?.textContent).toBe("hello <b>x</b>");
    expect(wrap?.querySelector(".ui-text b")).toBeNull();
    const link = wrap?.querySelector<HTMLAnchorElement>("li a");
    expect(link?.rel).toBe("noopener noreferrer");
    expect(wrap?.querySelectorAll("button.chip")).toHaveLength(2);
  });

  it("wires buttons to explicit, validated actions only", () => {
    const seen: ActionT[] = [];
    const wrap = renderUi(ui, { onAction: (a) => seen.push(a) });
    for (const b of Array.from(wrap?.querySelectorAll<HTMLButtonElement>("button.chip") ?? [])) b.click();
    expect(seen).toEqual([
      { type: "send_message", text: "tell me more" },
      { type: "open_url", url: "https://acme.test/x" },
    ]);
  });

  it.each([
    ["unknown component", { type: "script", src: "https://evil.test/x.js" }],
    ["html component", { type: "html", html: "<img src=x onerror=alert(1)>" }],
    ["unknown property", { type: "text", text: "x", onclick: "alert(1)" }],
    ["javascript link", { type: "link", label: "x", url: "javascript:alert(1)" }],
    ["eval action", { type: "button", label: "x", action: { type: "eval", code: "1" } }],
    ["nested unknown", { type: "card", children: [{ type: "iframe", src: "https://evil.test" }] }],
  ])("fails closed on %s", (_name, root) => {
    expect(renderUi({ version: "1", root }, noop)).toBeNull();
    const msg = renderAssistantMessage(base({ ui: { version: "1", root } as never }), noop);
    expect(msg.querySelector(".ui")).toBeNull();
    expect(msg.querySelector(".answer")?.textContent).toContain("20 euros"); // text + sources survive
    expect(msg.querySelector(".sources a")).not.toBeNull();
  });

  it("rejects a wrong ui version", () => {
    expect(renderUi({ version: "2", root: { type: "text", text: "x" } }, noop)).toBeNull();
  });

  it("does not render a link even if a bad node reaches renderNode directly", () => {
    const bad = { type: "link", label: "x", url: "javascript:alert(1)" } as UiNodeT;
    expect(renderNode(bad, noop)).toBeNull();
    expect(renderNode({ type: "mystery" } as unknown as UiNodeT, noop)).toBeNull();
  });

  it("never emits event-handler attributes or style attributes", () => {
    const wrap = renderUi(ui, noop) as HTMLElement;
    for (const e of everyElement(wrap)) {
      expect(e.getAttributeNames().filter((a) => a.startsWith("on") || a === "style")).toEqual([]);
    }
    expect(vi.isMockFunction(window.open)).toBe(false);
  });
});
