"use client";
import { useState } from "react";
import { Badge, Button, Card, ErrorNote, inputClass, statusTone } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { formatWhen } from "@/lib/embed";
import type { Agent, CrawlRun } from "@/lib/types";
import { usePoll } from "@/lib/usePoll";

const running = (runs: CrawlRun[]) => runs.some((r) => r.status === "queued" || r.status === "running");

export function SourcesTab({ agent, onChanged }: { agent: Agent; onChanged: () => void }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { data: runs, refresh } = usePoll(() => api.listRuns(agent.id), 2000, running);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onChanged();
      await refresh();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const crawling = runs ? running(runs) : false;
  return (
    <div className="space-y-6">
      <Card>
        <h2 className="font-medium">Source websites</h2>
        <p className="mt-1 text-sm text-muted">
          The crawler follows robots.txt, only fetches public http(s) pages, and never calls an LLM. Private and
          internal addresses are refused.
        </p>
        <form
          className="mt-4 flex flex-col gap-3 sm:flex-row"
          onSubmit={(e) => {
            e.preventDefault();
            void act(async () => {
              await api.addSource(agent.id, url.trim());
              setUrl("");
            });
          }}
        >
          <input
            className={inputClass}
            placeholder="https://docs.example.com/"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            aria-label="Website URL"
            inputMode="url"
          />
          <Button type="submit" disabled={busy || !url.trim()}>
            Add source
          </Button>
        </form>
        <div className="mt-3">
          <ErrorNote message={error} />
        </div>
        <ul className="mt-4 divide-y divide-line">
          {agent.sources.map((s) => (
            <li key={s.id} className="flex items-center justify-between gap-3 py-2">
              <span className="truncate font-mono text-sm">{s.url}</span>
              <Button variant="ghost" disabled={busy} onClick={() => void act(() => api.deleteSource(agent.id, s.id))}>
                Remove
              </Button>
            </li>
          ))}
          {agent.sources.length === 0 ? <li className="py-2 text-sm text-muted">No sources yet.</li> : null}
        </ul>
      </Card>

      <Card>
        <div className="flex items-center justify-between gap-3">
          <h2 className="font-medium">Crawl &amp; index</h2>
          <Button
            disabled={busy || crawling || agent.sources.length === 0}
            onClick={() => void act(() => api.startCrawl(agent.id))}
          >
            {crawling ? "Crawling…" : "Start crawl"}
          </Button>
        </div>
        <p className="mt-1 text-sm text-muted">Unchanged pages are detected by content hash and are not re-embedded.</p>
        <ul className="mt-4 space-y-3">
          {runs?.map((r) => (
            <li key={r.id} className="rounded-lg border border-line p-3">
              <div className="flex items-center justify-between gap-3">
                <Badge tone={statusTone(r.status)}>{r.status}</Badge>
                <span className="text-xs text-muted">{formatWhen(r.created_at)}</span>
              </div>
              {r.error ? <p className="mt-2 text-sm text-bad">{r.error}</p> : null}
              {Object.keys(r.stats).length ? (
                <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
                  {Object.entries(r.stats).map(([k, v]) => (
                    <div key={k} className="flex justify-between gap-2">
                      <dt className="text-muted">{k.replaceAll("_", " ")}</dt>
                      <dd className="font-mono">{v}</dd>
                    </div>
                  ))}
                </dl>
              ) : null}
            </li>
          ))}
          {runs?.length === 0 ? <li className="text-sm text-muted">No crawls yet.</li> : null}
        </ul>
      </Card>
    </div>
  );
}
