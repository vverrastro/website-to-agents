"use client";
import { useState } from "react";
import { Button, Card, ErrorNote, Field, inputClass } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { parseOriginLines } from "@/lib/embed";
import type { Agent } from "@/lib/types";

export function SettingsTab({ agent, onChanged }: { agent: Agent; onChanged: () => void }) {
  const [name, setName] = useState(agent.name);
  const [instructions, setInstructions] = useState(agent.instructions ?? "");
  const [origins, setOrigins] = useState(agent.allowed_origins.join("\n"));
  const [cfg, setCfg] = useState(agent.crawl_config);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const num = (v: string, fallback: number) => (Number.isFinite(Number(v)) && v !== "" ? Number(v) : fallback);

  async function save() {
    setBusy(true);
    setMsg(null);
    try {
      await api.updateAgent(agent.id, {
        name: name.trim(),
        instructions: instructions.trim() || null,
        allowed_origins: parseOriginLines(origins),
        crawl_config: cfg,
      });
      onChanged();
      setMsg({ ok: true, text: "Saved." });
    } catch (e) {
      setMsg({ ok: false, text: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="space-y-5">
      <Field label="Name">
        <input className={inputClass} value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field
        label="Instructions for the assistant (optional)"
        hint="Tone and scope guidance. It can never override the built-in security rules."
      >
        <textarea
          className={`${inputClass} min-h-24`}
          value={instructions}
          maxLength={4000}
          onChange={(e) => setInstructions(e.target.value)}
        />
      </Field>
      <Field
        label="Allowed website origins"
        hint="One per line, e.g. https://shop.example. Only these sites may embed the widget. This reduces abuse; it is not authentication. http://localhost is always accepted in development."
      >
        <textarea
          className={`${inputClass} min-h-20 font-mono`}
          value={origins}
          onChange={(e) => setOrigins(e.target.value)}
        />
      </Field>

      <fieldset className="grid gap-4 sm:grid-cols-2">
        <legend className="mb-2 text-sm font-medium">Crawl limits</legend>
        <Field label="Max pages (1–500)">
          <input
            className={inputClass}
            inputMode="numeric"
            value={cfg.max_pages}
            onChange={(e) => setCfg({ ...cfg, max_pages: num(e.target.value, cfg.max_pages) })}
          />
        </Field>
        <Field label="Max link depth (0–5)">
          <input
            className={inputClass}
            inputMode="numeric"
            value={cfg.max_depth}
            onChange={(e) => setCfg({ ...cfg, max_depth: num(e.target.value, cfg.max_depth) })}
          />
        </Field>
        <Field label="Delay between requests (seconds, ≥ 0.2)">
          <input
            className={inputClass}
            inputMode="decimal"
            value={cfg.min_delay_seconds}
            onChange={(e) => setCfg({ ...cfg, min_delay_seconds: num(e.target.value, cfg.min_delay_seconds) })}
          />
        </Field>
        <Field label="Max crawl duration (seconds)">
          <input
            className={inputClass}
            inputMode="numeric"
            value={cfg.max_duration_seconds}
            onChange={(e) => setCfg({ ...cfg, max_duration_seconds: num(e.target.value, cfg.max_duration_seconds) })}
          />
        </Field>
        <Field label="Recrawl every N hours (blank = manual only)">
          <input
            className={inputClass}
            inputMode="numeric"
            value={cfg.recrawl_interval_hours ?? ""}
            onChange={(e) =>
              setCfg({ ...cfg, recrawl_interval_hours: e.target.value === "" ? null : num(e.target.value, 24) })
            }
          />
        </Field>
        <label className="flex items-center gap-2 self-end pb-2 text-sm">
          <input
            type="checkbox"
            checked={cfg.use_sitemap}
            onChange={(e) => setCfg({ ...cfg, use_sitemap: e.target.checked })}
          />
          Use sitemap.xml
        </label>
      </fieldset>

      {msg ? msg.ok ? <p className="text-sm text-accent">{msg.text}</p> : <ErrorNote message={msg.text} /> : null}
      <Button disabled={busy || !name.trim()} onClick={() => void save()}>
        Save changes
      </Button>
    </Card>
  );
}
