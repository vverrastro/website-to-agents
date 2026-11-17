"use client";
import type { ActionT, ChatResponseT } from "@wta/agent-protocol";
import { useRef, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { RenderUi } from "./RenderUi";
import { Button, ErrorNote, inputClass } from "./ui";

interface Turn {
  id: number;
  role: "user" | "assistant";
  text: string;
  response?: ChatResponseT;
}

export function ChatPanel({ agentId }: { agentId: string }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const conversation = useRef<string | undefined>(undefined);
  const nextId = useRef(0);

  async function send(text: string) {
    const message = text.trim();
    if (!message || busy) return;
    setInput("");
    setError(null);
    setBusy(true);
    setTurns((t) => [...t, { id: nextId.current++, role: "user", text: message }]);
    try {
      const res = await api.previewChat(agentId, message, conversation.current);
      conversation.current = res.conversation_id;
      setTurns((t) => [...t, { id: nextId.current++, role: "assistant", text: res.answer, response: res }]);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  function onAction(action: ActionT) {
    if (action.type === "send_message") void send(action.text);
    else window.open(action.url, "_blank", "noopener,noreferrer");
  }

  return (
    <div className="space-y-4">
      <div className="min-h-48 space-y-4 rounded-lg border border-line bg-paper p-4" aria-live="polite">
        {turns.length === 0 ? (
          <p className="text-sm text-muted">
            Ask a question the way a visitor would. Answers use only the indexed pages.
          </p>
        ) : null}
        {turns.map((t) =>
          t.role === "user" ? (
            <p key={t.id} className="ml-auto max-w-[85%] rounded-lg bg-accent px-3 py-2 text-sm text-paper">
              {t.text}
            </p>
          ) : (
            <div key={t.id} className="max-w-[92%] space-y-3 rounded-lg border border-line bg-card px-3 py-2">
              <p className="whitespace-pre-wrap text-sm">{t.text}</p>
              {t.response?.sources.length ? (
                <ul className="space-y-1 border-t border-line pt-2">
                  {t.response.sources.map((s) => (
                    <li key={s.id} className="text-xs">
                      <a
                        href={s.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="font-medium text-accent underline underline-offset-2"
                      >
                        {s.title}
                      </a>
                      {s.section ? <span className="text-muted"> · {s.section}</span> : null}
                    </li>
                  ))}
                </ul>
              ) : null}
              {t.response?.ui ? <RenderUi ui={t.response.ui} onAction={onAction} /> : null}
            </div>
          ),
        )}
        {busy ? <p className="text-sm text-muted">Thinking…</p> : null}
      </div>
      <ErrorNote message={error} />
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void send(input);
        }}
      >
        <input
          className={inputClass}
          value={input}
          maxLength={2000}
          placeholder="Ask about the website…"
          onChange={(e) => setInput(e.target.value)}
          aria-label="Message"
        />
        <Button type="submit" disabled={busy || !input.trim()}>
          Send
        </Button>
      </form>
    </div>
  );
}
