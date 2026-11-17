"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Status } from "@/lib/types";
import { Badge } from "./ui";

export function StatusBar() {
  const [status, setStatus] = useState<Status | null>(null);
  const [down, setDown] = useState(false);
  useEffect(() => {
    api.status().then(setStatus, () => setDown(true));
  }, []);
  if (down) return <Badge tone="bad">API unreachable</Badge>;
  if (!status) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
      <Badge tone={status.llm.configured ? "good" : "warn"}>
        LLM {status.llm.provider}
        {status.llm.configured ? "" : " · key missing"}
      </Badge>
      <Badge tone={status.embedding.configured ? "good" : "warn"}>
        Embeddings {status.embedding.provider}
        {status.embedding.configured ? "" : " · key missing"}
      </Badge>
    </div>
  );
}
