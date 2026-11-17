"use client";
import { Badge, Card, ErrorNote, statusTone } from "@/components/ui";
import { api } from "@/lib/api";
import { formatWhen } from "@/lib/embed";
import { usePoll } from "@/lib/usePoll";

export function PagesTab({ agentId }: { agentId: string }) {
  const pages = usePoll(
    () => api.listPages(agentId),
    4000,
    () => false,
  );
  const skipped = usePoll(
    () => api.listSkipped(agentId),
    4000,
    () => false,
  );
  return (
    <div className="space-y-6">
      <Card>
        <h2 className="font-medium">Pages</h2>
        <ErrorNote message={pages.error} />
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-muted">
              <tr>
                <th className="py-2 pr-3 font-medium">URL</th>
                <th className="py-2 pr-3 font-medium">Status</th>
                <th className="py-2 pr-3 font-medium">Chunks</th>
                <th className="py-2 pr-3 font-medium">Mode</th>
                <th className="py-2 pr-3 font-medium">Last crawled</th>
                <th className="py-2 font-medium">Indexed</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {pages.data?.map((p) => (
                <tr key={p.id}>
                  <td className="max-w-xs truncate py-2 pr-3 font-mono text-xs" title={p.url}>
                    {p.url}
                  </td>
                  <td className="py-2 pr-3">
                    <Badge tone={statusTone(p.status)}>{p.status}</Badge>
                    {p.error ? <span className="ml-2 text-xs text-muted">{p.error}</span> : null}
                  </td>
                  <td className="py-2 pr-3 font-mono">{p.chunk_count}</td>
                  <td className="py-2 pr-3 text-xs">{p.render_mode}</td>
                  <td className="py-2 pr-3 text-xs">{formatWhen(p.last_crawled_at)}</td>
                  <td className="py-2 text-xs">{formatWhen(p.indexed_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {pages.data?.length === 0 ? (
            <p className="py-3 text-sm text-muted">No pages yet. Add a source and start a crawl.</p>
          ) : null}
        </div>
      </Card>

      <Card>
        <h2 className="font-medium">Skipped URLs</h2>
        <p className="mt-1 text-sm text-muted">
          Every skipped URL keeps an audit reason (robots, off-site, non-HTML, limits…).
        </p>
        <ul className="mt-3 divide-y divide-line">
          {skipped.data?.map((s) => (
            <li key={`${s.crawl_run_id}:${s.url}`} className="flex items-center justify-between gap-3 py-2">
              <span className="truncate font-mono text-xs" title={s.url}>
                {s.url}
              </span>
              <Badge>{s.reason}</Badge>
            </li>
          ))}
          {skipped.data?.length === 0 ? <li className="py-2 text-sm text-muted">Nothing skipped.</li> : null}
        </ul>
      </Card>
    </div>
  );
}
