"use client";
import type { ButtonHTMLAttributes, ReactNode } from "react";

const base =
  "inline-flex items-center justify-center gap-2 rounded-md px-3.5 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50";

export function Button({
  variant = "primary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "ghost" }) {
  const styles = {
    primary: "bg-accent text-paper hover:opacity-90",
    secondary: "border border-line bg-card text-ink hover:bg-accent-soft",
    danger: "border border-bad/40 bg-card text-bad hover:bg-bad-soft",
    ghost: "text-muted hover:text-ink",
  }[variant];
  return <button type="button" className={`${base} ${styles} ${className}`} {...props} />;
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-xl border border-line bg-card p-5 ${className}`}>{children}</section>;
}

export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: "neutral" | "good" | "warn" | "bad";
  children: ReactNode;
}) {
  const t = {
    neutral: "bg-line/60 text-muted",
    good: "bg-accent-soft text-accent",
    warn: "bg-warn-soft text-warn",
    bad: "bg-bad-soft text-bad",
  }[tone];
  return <span className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${t}`}>{children}</span>;
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    // biome-ignore lint/a11y/noLabelWithoutControl: the control is the wrapped child
    <label className="block space-y-1.5">
      <span className="text-sm font-medium">{label}</span>
      {children}
      {hint ? <span className="block text-xs text-muted">{hint}</span> : null}
    </label>
  );
}

export const inputClass =
  "w-full rounded-md border border-line bg-paper px-3 py-2 text-sm text-ink placeholder:text-muted/70";

export function ErrorNote({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p role="alert" className="rounded-md bg-bad-soft px-3 py-2 text-sm text-bad">
      {message}
    </p>
  );
}

export function statusTone(status: string): "neutral" | "good" | "warn" | "bad" {
  if (["indexed", "succeeded"].includes(status)) return "good";
  if (["running", "queued", "discovered"].includes(status)) return "warn";
  if (["failed", "error"].includes(status)) return "bad";
  return "neutral";
}
