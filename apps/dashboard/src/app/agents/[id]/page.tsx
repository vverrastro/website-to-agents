"use client";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { EmbedTab } from "@/components/agent/EmbedTab";
import { PagesTab } from "@/components/agent/PagesTab";
import { SettingsTab } from "@/components/agent/SettingsTab";
import { SourcesTab } from "@/components/agent/SourcesTab";
import { ChatPanel } from "@/components/ChatPanel";
import { Badge, Button, Card, ErrorNote } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

const TABS = ["Sources", "Pages", "Test chat", "Settings", "Embed"] as const;
type Tab = (typeof TABS)[number];

export default function AgentPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const {
    data: agent,
    error,
    refresh,
  } = usePoll(
    () => api.getAgent(id),
    5000,
    () => false,
  );
  const [tab, setTab] = useState<Tab>("Sources");
  const [actionError, setActionError] = useState<string | null>(null);

  if (error && !agent) return <ErrorNote message={error} />;
  if (!agent) return <p className="text-sm text-muted">Loading…</p>;

  async function togglePublish() {
    setActionError(null);
    try {
      await api.setPublished(id, !agent?.published);
      await refresh();
    } catch (e) {
      setActionError(errorMessage(e));
    }
  }

  async function remove() {
    if (!window.confirm(`Delete “${agent?.name}” and everything indexed for it?`)) return;
    try {
      await api.deleteAgent(id);
      router.push("/");
    } catch (e) {
      setActionError(errorMessage(e));
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <Link href="/" className="text-sm text-muted hover:text-ink">
          ← All agents
        </Link>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
          <div className="space-y-1">
            <h1 className="text-2xl font-semibold tracking-tight">{agent.name}</h1>
            <p className="font-mono text-xs text-muted">{agent.public_id}</p>
          </div>
          <div className="flex items-center gap-2">
            <Badge tone={agent.published ? "good" : "neutral"}>{agent.published ? "Published" : "Draft"}</Badge>
            <Button variant="secondary" onClick={() => void togglePublish()}>
              {agent.published ? "Unpublish" : "Publish"}
            </Button>
            <Button variant="danger" onClick={() => void remove()}>
              Delete
            </Button>
          </div>
        </div>
        <div className="mt-3">
          <ErrorNote message={actionError} />
        </div>
      </div>

      <div role="tablist" className="flex gap-1 border-b border-line">
        {TABS.map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm ${tab === t ? "border-accent font-medium text-ink" : "border-transparent text-muted hover:text-ink"}`}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === "Sources" ? <SourcesTab agent={agent} onChanged={() => void refresh()} /> : null}
      {tab === "Pages" ? <PagesTab agentId={id} /> : null}
      {tab === "Test chat" ? (
        <Card>
          <ChatPanel agentId={id} />
        </Card>
      ) : null}
      {tab === "Settings" ? <SettingsTab agent={agent} onChanged={() => void refresh()} /> : null}
      {tab === "Embed" ? <EmbedTab agent={agent} /> : null}
    </div>
  );
}
