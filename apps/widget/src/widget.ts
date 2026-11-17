import type { ActionT } from "@wta/agent-protocol";
import { friendlyError, PublicApiClient, type WidgetConfig } from "@wta/widget-sdk";
import { type RenderHandlers, renderAssistantMessage } from "./render";
import { CSS } from "./styles";

export interface WidgetHandle {
  open(): void;
  close(): void;
  destroy(): void;
  /** The shadow root (kept by reference so it works even when the root is closed). */
  root: ShadowRoot;
}

export interface MountOptions {
  fetchImpl?: typeof fetch;
  container?: HTMLElement;
  /** "closed" hides the widget's DOM from host-page scripts; tests use "open". */
  shadowMode?: "open" | "closed";
}

const HOST_ID = "wta-widget-host";
const MAX_CHARS = 2000;
const CHAT_ICON = "M4 4h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-5 4v-4H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z";

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

function chatIcon(): SVGSVGElement {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", CHAT_ICON);
  path.setAttribute("fill", "currentColor");
  svg.append(path);
  return svg;
}

function applyStyles(root: ShadowRoot): void {
  // Constructable stylesheets are not subject to a host page's `style-src` CSP (inline <style> is).
  try {
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(CSS);
    root.adoptedStyleSheets = [sheet];
  } catch {
    const style = el("style");
    style.textContent = CSS;
    root.append(style);
  }
}

export function mountWidget(config: WidgetConfig, options: MountOptions = {}): WidgetHandle {
  const container = options.container ?? document.body;
  const existing = container.querySelector(`#${HOST_ID}`);
  if (existing) existing.remove();

  const client = new PublicApiClient(config, options.fetchImpl ? { fetchImpl: options.fetchImpl } : {});
  const host = el("div");
  host.id = HOST_ID;
  const root = host.attachShadow({ mode: options.shadowMode ?? "closed" });
  applyStyles(root);

  const wrap = el("div", "root");
  const launcher = el("button", "launcher");
  launcher.type = "button";
  launcher.setAttribute("aria-label", "Open chat assistant");
  launcher.setAttribute("aria-expanded", "false");
  launcher.append(chatIcon());

  const panel = el("div", "panel");
  panel.hidden = true;
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", "Chat assistant");

  const header = el("div", "header");
  const title = el("span", "title", "Assistant");
  const closeBtn = el("button", "close", "×");
  closeBtn.type = "button";
  closeBtn.setAttribute("aria-label", "Close chat");
  header.append(title, closeBtn);

  const log = el("div", "log");
  log.setAttribute("role", "log");
  log.setAttribute("aria-live", "polite");

  const form = el("form", "form");
  const input = el("input", "input");
  input.type = "text";
  input.maxLength = MAX_CHARS;
  input.placeholder = "Ask a question…";
  input.autocomplete = "off";
  input.setAttribute("aria-label", "Your message");
  const send = el("button", "send", "Send");
  send.type = "submit";
  form.append(input, send);

  panel.append(header, log, form);
  wrap.append(panel, launcher);
  root.append(wrap);
  container.append(host);

  let conversationId: string | undefined;
  let busy = false;
  let configLoaded = false;

  const scroll = () => {
    log.scrollTop = log.scrollHeight;
  };
  const addBubble = (cls: string, text: string): HTMLElement => {
    const b = el("div", `msg ${cls}`, text);
    log.append(b);
    scroll();
    return b;
  };
  const setBusy = (value: boolean) => {
    busy = value;
    input.disabled = value;
    send.disabled = value;
  };

  async function submit(raw: string): Promise<void> {
    const message = raw.trim();
    if (!message || busy) return;
    input.value = "";
    setBusy(true);
    addBubble("user", message); // textContent: the visitor's own text is never parsed as HTML
    const typing = addBubble("bot typing", "Thinking…");
    const result = await client.chat(message, conversationId);
    typing.remove();
    if (result.ok) {
      conversationId = result.response.conversation_id;
      log.append(renderAssistantMessage(result.response, handlers));
      scroll();
    } else {
      addBubble("error", friendlyError(result.error));
    }
    setBusy(false);
    input.focus();
  }

  const handlers: RenderHandlers = {
    onAction(action: ActionT) {
      if (action.type === "send_message") void submit(action.text);
      else window.open(action.url, "_blank", "noopener,noreferrer");
    },
  };

  async function loadConfig(): Promise<void> {
    if (configLoaded) return;
    configLoaded = true; // fetched once, on first open, so merely loading a page makes no API call
    const r = await client.getConfig();
    if (r.ok) title.textContent = r.name;
  }

  function open(): void {
    panel.hidden = false;
    launcher.setAttribute("aria-expanded", "true");
    if (log.childElementCount === 0) addBubble("bot", "Hi! Ask me anything about this website.");
    void loadConfig();
    input.focus();
  }
  function close(): void {
    panel.hidden = true;
    launcher.setAttribute("aria-expanded", "false");
    launcher.focus();
  }

  launcher.addEventListener("click", () => (panel.hidden ? open() : close()));
  closeBtn.addEventListener("click", close);
  root.addEventListener("keydown", (e) => {
    if ((e as KeyboardEvent).key === "Escape" && !panel.hidden) close();
  });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    void submit(input.value);
  });

  return { open, close, destroy: () => host.remove(), root };
}
