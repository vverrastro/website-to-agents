const esc = (v: string) => v.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

export interface EmbedOptions {
  widgetUrl: string;
  apiUrl: string;
  publicId: string;
}

/** The only thing placed in a customer's page: a public agent id and public URLs. No secrets. */
export function embedSnippet({ widgetUrl, apiUrl, publicId }: EmbedOptions): string {
  const src = `${widgetUrl.replace(/\/+$/, "")}/widget.js`;
  return `<script src="${esc(src)}" data-agent-id="${esc(publicId)}" data-api-base="${esc(apiUrl.replace(/\/+$/, ""))}" async></script>`;
}

export function parseOriginLines(text: string): string[] {
  return text
    .split(/[\n,]/)
    .map((l) => l.trim())
    .filter(Boolean);
}

export function formatWhen(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
