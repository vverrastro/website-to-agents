import { configureForUntrustedHosts } from "@wta/agent-protocol";
import { ConfigError, readScriptConfig } from "@wta/widget-sdk";
import { mountWidget, type WidgetHandle } from "./widget";

declare global {
  interface Window {
    WebsiteToAgents?: { open(): void; close(): void };
  }
}

// Captured synchronously: document.currentScript is null once the script has finished running.
const script = document.currentScript instanceof HTMLScriptElement ? document.currentScript : null;

function start(): void {
  configureForUntrustedHosts();
  try {
    const target = script ?? document.querySelector<HTMLScriptElement>("script[data-agent-id]");
    const handle: WidgetHandle = mountWidget(readScriptConfig(target));
    window.WebsiteToAgents = { open: handle.open, close: handle.close };
  } catch (e) {
    // Never break the host page: misconfiguration just means no widget.
    const reason = e instanceof ConfigError ? e.message : "unexpected error";
    console.warn(`[website-to-agents] widget not started: ${reason}`);
  }
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
else start();
