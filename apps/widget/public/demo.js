// Demo harness only. It builds the same <script> tag a customer would paste.
const params = new URLSearchParams(location.search);
const agent = document.getElementById("agent");
const api = document.getElementById("api");
const msg = document.getElementById("msg");
const snippet = document.getElementById("snippet");
if (params.get("agent")) agent.value = params.get("agent");
if (params.get("api")) api.value = params.get("api");

function load() {
  for (const old of document.querySelectorAll("script[data-agent-id]")) old.remove();
  const s = document.createElement("script");
  s.src = new URL("widget.js", location.href).href;
  s.dataset.agentId = agent.value.trim();
  s.dataset.apiBase = api.value.trim();
  s.async = true;
  document.body.append(s);
  snippet.textContent = s.outerHTML;
  msg.textContent = "Widget script added — look for the chat button at the bottom right.";
}

document.getElementById("load").addEventListener("submit", (e) => {
  e.preventDefault();
  load();
});
if (agent.value) load();
