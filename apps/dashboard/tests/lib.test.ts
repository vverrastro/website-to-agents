import { describe, expect, it } from "vitest";
import { embedSnippet, formatWhen, parseOriginLines } from "../src/lib/embed";

describe("embedSnippet", () => {
  it("builds a script tag with only public values", () => {
    const html = embedSnippet({
      widgetUrl: "http://127.0.0.1:5174/",
      apiUrl: "http://127.0.0.1:8000/",
      publicId: "pub_abc",
    });
    expect(html).toBe(
      '<script src="http://127.0.0.1:5174/widget.js" data-agent-id="pub_abc" data-api-base="http://127.0.0.1:8000" async></script>',
    );
  });

  it("escapes attribute values so a hostile id cannot break out of the tag", () => {
    const html = embedSnippet({
      widgetUrl: "https://w.test",
      apiUrl: "https://a.test",
      publicId: '"><script>alert(1)</script>',
    });
    expect(html).not.toContain("<script>alert");
    expect(html.match(/<script/g)?.length).toBe(1);
    expect(html).toContain("&quot;&gt;&lt;script&gt;");
  });
});

describe("helpers", () => {
  it("parses origins from lines and commas", () => {
    expect(parseOriginLines("https://a.test\n, https://b.test ,\n\n")).toEqual(["https://a.test", "https://b.test"]);
  });
  it("formats dates defensively", () => {
    expect(formatWhen(null)).toBe("—");
    expect(formatWhen("garbage")).toBe("—");
    expect(formatWhen("2026-11-17T10:00:00Z")).not.toBe("—");
  });
});
