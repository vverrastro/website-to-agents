"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Badge, Button, Card, ErrorNote, inputClass } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { formatWhen } from "@/lib/embed";
import { usePoll } from "@/lib/usePoll";

export default function Home() {
  const router = useRouter();
  const { data: agents, error } = usePoll(
    () => api.listAgents(),
    5000,
    () => false,
  );
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  async function create() {
    setBusy(true);
    setFormError(null);
    try {
      const agent = await api.createAgent(name.trim());
      router.push(`/agents/${agent.id}`);
    } catch (e) {
      setFormError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <div className="space-y-8">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Your agents</h1>
        <p className="text-sm text-muted">Each agent answers from the pages you let it crawl, and cites them.</p>
      </div>

      <Card>
        <form
          className="flex flex-col gap-3 sm:flex-row"
          onSubmit={(e) => {
            e.preventDefault();
            void create();
          }}
        >
          <input
            className={inputClass}
            placeholder="New agent name, e.g. “Acme support”"
            value={name}
            maxLength={100}
            onChange={(e) => setName(e.target.value)}
            aria-label="Agent name"
          />
          <Button type="submit" disabled={busy || !name.trim()}>
            Create agent
          </Button>
        </form>
        <div className="mt-3">
          <ErrorNote message={formError} />
        </div>
      </Card>

      <ErrorNote message={error} />
      {agents && agents.length === 0 ? (
        <p className="text-sm text-muted">No agents yet. Create one above, then add a website.</p>
      ) : null}
      <ul className="grid gap-3 sm:grid-cols-2">
        {agents?.map((a) => (
          <li key={a.id}>
            <Link
              href={`/agents/${a.id}`}
              className="block rounded-xl border border-line bg-card p-5 transition-colors hover:border-accent"
            >
              <div className="flex items-start justify-between gap-3">
                <h2 className="font-medium">{a.name}</h2>
                <Badge tone={a.published ? "good" : "neutral"}>{a.published ? "Published" : "Draft"}</Badge>
              </div>
              <p className="mt-2 text-sm text-muted">
                {a.sources.length} source{a.sources.length === 1 ? "" : "s"} · {a.page_count} indexed page
                {a.page_count === 1 ? "" : "s"}
              </p>
              <p className="mt-1 text-xs text-muted">Created {formatWhen(a.created_at)}</p>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
