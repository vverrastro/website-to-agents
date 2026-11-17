"use client";
import { useState } from "react";
import { Badge, Button, Card } from "@/components/ui";
import { embedSnippet } from "@/lib/embed";
import type { Agent } from "@/lib/types";

const WIDGET_URL = process.env.NEXT_PUBLIC_WIDGET_URL ?? "http://127.0.0.1:5174";
const API_URL = process.env.NEXT_PUBLIC_API_PUBLIC_URL ?? "http://127.0.0.1:8000";

export function EmbedTab({ agent }: { agent: Agent }) {
  const [copied, setCopied] = useState(false);
  const snippet = embedSnippet({ widgetUrl: WIDGET_URL, apiUrl: API_URL, publicId: agent.public_id });

  return (
    <Card className="space-y-4">
      <div className="flex items-center gap-2">
        <h2 className="font-medium">Embed on a website</h2>
        <Badge tone={agent.published ? "good" : "warn"}>{agent.published ? "Published" : "Not published"}</Badge>
      </div>
      <p className="text-sm text-muted">
        Paste this before <code className="font-mono">&lt;/body&gt;</code>. It contains only the public agent id and
        public URLs — no keys or tokens. The id identifies the agent; it does not authenticate anyone.
      </p>
      <pre className="overflow-x-auto rounded-lg border border-line bg-paper p-3 text-xs">
        <code>{snippet}</code>
      </pre>
      <Button
        variant="secondary"
        onClick={async () => {
          await navigator.clipboard.writeText(snippet);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}
      >
        {copied ? "Copied" : "Copy snippet"}
      </Button>
      <ul className="list-disc space-y-1 pl-5 text-sm text-muted">
        {!agent.published ? <li>Publish the agent, otherwise the widget gets a 404.</li> : null}
        {agent.allowed_origins.length === 0 ? <li>Add the embedding site under Settings → Allowed origins.</li> : null}
        <li>
          Local testing: <code className="font-mono">make widget</code> serves a demo page at {WIDGET_URL}.
        </li>
      </ul>
    </Card>
  );
}
