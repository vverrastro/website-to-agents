/** The only configuration a host page provides: a public agent id and a public API URL. */
export interface WidgetConfig {
  agentId: string;
  apiBase: string;
}

const AGENT_ID = /^pub_[A-Za-z0-9_-]{6,64}$/;

export class ConfigError extends Error {}

/** Validate and normalise config. The agent id is an identifier, not a credential. */
export function validateConfig(input: { agentId?: string | null; apiBase?: string | null }): WidgetConfig {
  const agentId = (input.agentId ?? "").trim();
  if (!AGENT_ID.test(agentId)) throw new ConfigError("Invalid data-agent-id");
  let url: URL;
  try {
    url = new URL((input.apiBase ?? "").trim());
  } catch {
    throw new ConfigError("Invalid data-api-base");
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") throw new ConfigError("data-api-base must be http(s)");
  if (url.username || url.password) throw new ConfigError("data-api-base must not contain credentials");
  return { agentId, apiBase: `${url.origin}${url.pathname.replace(/\/+$/, "")}` };
}

/** Read `<script data-agent-id=… data-api-base=…>`. */
export function readScriptConfig(script: HTMLScriptElement | null): WidgetConfig {
  if (!script) throw new ConfigError("Embed script not found");
  return validateConfig({ agentId: script.dataset.agentId, apiBase: script.dataset.apiBase });
}
