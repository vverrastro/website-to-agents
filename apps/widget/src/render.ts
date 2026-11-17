import { type ActionT, type ChatResponseT, isHttpUrl, UiDocument, type UiNodeT } from "@wta/agent-protocol";

/**
 * DOM construction for assistant messages. Security invariants (enforced by tests):
 *  - no HTML parsing: every string goes through textContent / setAttribute on known attributes;
 *  - the UI tree is re-validated against the allowlist here, even though the SDK already did;
 *  - every link is re-checked to be http(s), and opens with rel="noopener noreferrer".
 */
export interface RenderHandlers {
  onAction(action: ActionT): void;
}

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function safeLink(label: string, url: string, className = "link"): HTMLAnchorElement | null {
  if (!isHttpUrl(url)) return null;
  const a = el("a", className, label);
  a.href = url;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  return a;
}

export function renderNode(node: UiNodeT, h: RenderHandlers): HTMLElement | null {
  switch (node.type) {
    case "text":
      return el("p", "ui-text", node.text);
    case "link":
      return safeLink(node.label, node.url);
    case "button": {
      const b = el("button", "chip", node.label);
      b.type = "button";
      b.addEventListener("click", () => h.onAction(node.action));
      return b;
    }
    case "list": {
      const list = el(node.ordered ? "ol" : "ul", node.ordered ? "ui-list ordered" : "ui-list");
      for (const item of node.items) {
        const child = renderNode(item, h);
        if (!child) continue;
        const li = el("li");
        li.append(child);
        list.append(li);
      }
      return list;
    }
    case "card": {
      const card = el("div", "ui-card");
      if (node.title) card.append(el("p", "ui-card-title", node.title));
      for (const child of node.children) {
        const r = renderNode(child, h);
        if (r) card.append(r);
      }
      return card;
    }
    default:
      return null; // unknown component: fail closed
  }
}

/** Returns null (render nothing) unless the tree passes the allowlist schema. */
export function renderUi(ui: unknown, h: RenderHandlers): HTMLElement | null {
  const parsed = UiDocument.safeParse(ui);
  if (!parsed.success) return null;
  const root = renderNode(parsed.data.root, h);
  if (!root) return null;
  const wrap = el("div", "ui");
  wrap.append(root);
  return wrap;
}

export function renderAssistantMessage(res: ChatResponseT, h: RenderHandlers): HTMLElement {
  const bubble = el("div", "msg bot");
  bubble.append(el("p", "answer", res.answer));

  if (res.sources.length > 0) {
    const list = el("ul", "sources");
    list.setAttribute("aria-label", "Sources");
    for (const s of res.sources) {
      const li = el("li");
      const link = safeLink(s.title || s.url, s.url);
      if (!link) continue;
      li.append(link);
      if (s.section) li.append(el("span", "section", ` · ${s.section}`));
      list.append(li);
    }
    if (list.childElementCount > 0) bubble.append(list);
  }

  const ui = res.ui ? renderUi(res.ui, h) : null;
  if (ui) bubble.append(ui);
  return bubble;
}
