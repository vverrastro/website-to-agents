/** Static stylesheet, applied inside the shadow root (no inline style attributes anywhere). */
export const CSS = `
:host { all: initial; }
*, *::before, *::after { box-sizing: border-box; }
.root {
  --accent: #0f5e57; --on-accent: #ffffff; --bg: #ffffff; --fg: #1c1b19; --muted: #6b675f;
  --line: #e6e1d8; --soft: #f5f2ec; --bad: #a1301f;
  font: 14px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; color: var(--fg);
  position: fixed; right: 20px; bottom: 20px; z-index: 2147483000;
}
@media (prefers-color-scheme: dark) {
  .root { --accent: #5cc4b9; --on-accent: #0b1f1d; --bg: #1d1c1a; --fg: #efece6; --muted: #a29d92;
          --line: #33312d; --soft: #272622; --bad: #f08673; }
}
button { font: inherit; color: inherit; cursor: pointer; }
.launcher { width: 56px; height: 56px; border-radius: 50%; border: 0; background: var(--accent); color: var(--on-accent);
  box-shadow: 0 4px 16px rgba(0,0,0,.25); display: grid; place-items: center; }
.launcher svg { width: 26px; height: 26px; }
.panel { position: absolute; right: 0; bottom: 68px; width: min(380px, calc(100vw - 24px)); height: min(560px, calc(100vh - 100px));
  background: var(--bg); border: 1px solid var(--line); border-radius: 14px; box-shadow: 0 12px 40px rgba(0,0,0,.28);
  display: flex; flex-direction: column; overflow: hidden; }
.panel[hidden] { display: none; }
.header { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 12px 14px;
  background: var(--accent); color: var(--on-accent); }
.title { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.close { background: transparent; border: 0; color: inherit; padding: 4px 8px; border-radius: 6px; font-size: 18px; line-height: 1; }
.log { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 10px; background: var(--soft); }
.msg { max-width: 90%; padding: 8px 11px; border-radius: 12px; overflow-wrap: anywhere; }
.msg.user { align-self: flex-end; background: var(--accent); color: var(--on-accent); }
.msg.bot { align-self: flex-start; background: var(--bg); border: 1px solid var(--line); }
.msg.error { align-self: flex-start; background: var(--bg); border: 1px solid var(--bad); color: var(--bad); }
.msg.typing { color: var(--muted); font-style: italic; }
.answer { white-space: pre-wrap; margin: 0; }
.sources { list-style: none; margin: 8px 0 0; padding: 8px 0 0; border-top: 1px solid var(--line); display: grid; gap: 4px; font-size: 12px; }
.sources .section { color: var(--muted); }
.link { color: var(--accent); text-decoration: underline; text-underline-offset: 2px; }
.ui { margin-top: 8px; display: grid; gap: 8px; }
.ui-card { border: 1px solid var(--line); border-radius: 10px; padding: 8px; display: grid; gap: 6px; }
.ui-card-title { margin: 0; font-size: 11px; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; color: var(--muted); }
.ui-list { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 6px; }
.ui-list.ordered { list-style: decimal; padding-left: 18px; display: block; }
.ui-text { margin: 0; }
.chip { border: 1px solid var(--line); background: var(--soft); border-radius: 999px; padding: 3px 10px; }
.chip:hover { border-color: var(--accent); }
.form { display: flex; gap: 8px; padding: 10px; border-top: 1px solid var(--line); background: var(--bg); }
.input { flex: 1; min-width: 0; border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; background: var(--bg); color: var(--fg); font: inherit; }
.send { border: 0; border-radius: 8px; background: var(--accent); color: var(--on-accent); padding: 8px 14px; font-weight: 600; }
.send:disabled, .input:disabled { opacity: .55; cursor: not-allowed; }
button:focus-visible, .input:focus-visible, .link:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (max-width: 480px) {
  .root { right: 12px; bottom: 12px; }
  .panel { position: fixed; inset: 8px; width: auto; height: auto; }
}
@media (prefers-reduced-motion: no-preference) { .panel { animation: pop .12s ease-out; } }
@keyframes pop { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
`;
