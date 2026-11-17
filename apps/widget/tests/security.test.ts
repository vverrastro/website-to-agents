import { readdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";
import { build } from "vite";
import { describe, expect, it } from "vitest";

const srcDirs = [join(__dirname, "..", "src"), join(__dirname, "..", "..", "..", "packages", "widget-sdk", "src")];

function files(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? files(join(dir, e.name)) : e.name.endsWith(".ts") ? [join(dir, e.name)] : [],
  );
}

describe("widget source never uses dangerous DOM / code-execution APIs", () => {
  const banned: [string, RegExp][] = [
    ["innerHTML", /\binnerHTML\b/],
    ["outerHTML", /\bouterHTML\b/],
    ["insertAdjacentHTML", /insertAdjacentHTML/],
    ["document.write", /document\.write/],
    ["eval", /\beval\s*\(/],
    ["new Function", /new\s+Function\b/],
    ["setTimeout with string", /set(Timeout|Interval)\s*\(\s*["'`]/],
    ["DOMParser", /\bDOMParser\b/],
    ["createContextualFragment", /createContextualFragment/],
    ["srcdoc", /\bsrcdoc\b/],
    ["inline style attribute", /setAttribute\(\s*["']style["']/],
    ["localStorage of tokens", /localStorage|sessionStorage|document\.cookie/],
  ];
  const all = srcDirs.flatMap(files);

  it("scans a meaningful set of files", () => {
    expect(all.length).toBeGreaterThanOrEqual(6);
  });

  it.each(banned)("no %s", (_name, pattern) => {
    for (const f of all) expect(readFileSync(f, "utf8"), f).not.toMatch(pattern);
  });
});

const out = join(tmpdir(), `wta-widget-build-${process.pid}`);
await build({
  root: join(__dirname, ".."),
  logLevel: "silent",
  build: { outDir: out, emptyOutDir: true, write: true },
});
const bundle = readFileSync(join(out, "widget.js"), "utf8");
rmSync(out, { recursive: true, force: true });

describe("built bundle", () => {
  it("contains no provider keys, credentials or server-side configuration names", () => {
    for (const needle of [
      /sk-ant-/i,
      /\bpa-[A-Za-z0-9]{20,}/,
      /ANTHROPIC_API_KEY|VOYAGE_API_KEY|DATABASE_URL|REDIS_URL|POSTGRES_PASSWORD/,
      /BEGIN (RSA |EC )?PRIVATE KEY/,
      /Bearer\s+[A-Za-z0-9._-]{16,}/,
    ]) {
      expect(bundle).not.toMatch(needle);
    }
  });

  it("never references the privileged admin API", () => {
    expect(bundle).not.toContain("/admin/");
    expect(bundle).not.toContain("admin/v1");
    expect(bundle).toContain("/public/v1/agents/");
  });

  it("is a single reasonably small file", () => {
    expect(bundle.length).toBeGreaterThan(10_000);
    expect(bundle.length).toBeLessThan(160_000);
  });
});

describe("built bundle boots from a script tag (as a customer's page would load it)", () => {
  async function boot(attrs: string) {
    const warnings: string[] = [];
    const virtualConsole = new VirtualConsole();
    virtualConsole.on("warn", (m: string) => warnings.push(String(m)));
    const html = `<!doctype html><body><p>host page</p><script ${attrs}>${bundle.replaceAll("</script", "<\\/script")}</script></body>`;
    const dom = new JSDOM(html, { runScripts: "dangerously", url: "https://shop.example/", virtualConsole });
    // The widget waits for DOMContentLoaded when the page is still parsing, exactly like a real page.
    await new Promise<void>((resolve) => dom.window.addEventListener("load", () => resolve()));
    return { dom, warnings };
  }

  it("mounts a closed shadow-root widget and exposes only open/close", async () => {
    const { dom } = await boot('data-agent-id="pub_abcdef123456" data-api-base="https://api.example.com"');
    const host = dom.window.document.getElementById("wta-widget-host");
    expect(host).not.toBeNull();
    expect(host?.shadowRoot).toBeNull(); // closed: the host page cannot reach inside
    expect(host?.children).toHaveLength(0);
    const api = (dom.window as unknown as { WebsiteToAgents?: Record<string, unknown> }).WebsiteToAgents;
    expect(Object.keys(api ?? {}).sort()).toEqual(["close", "open"]);
    expect(dom.window.document.querySelector("p")?.textContent).toBe("host page");
  });

  it.each([
    ['data-agent-id="admin" data-api-base="https://api.example.com"'],
    ['data-agent-id="pub_abcdef123456" data-api-base="javascript:alert(1)"'],
    ['data-agent-id="pub_abcdef123456"'],
    [""],
  ])("does not mount (and does not throw) on bad config: %s", async (attrs) => {
    const { dom, warnings } = await boot(attrs);
    expect(dom.window.document.getElementById("wta-widget-host")).toBeNull();
    expect(warnings.join(" ")).toContain("widget not started");
  });
});
