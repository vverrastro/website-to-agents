import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import { StatusBar } from "@/components/StatusBar";
import "./globals.css";

export const metadata: Metadata = {
  title: "Agent Studio · Website-to-Agents",
  description: "Turn a public website into a cited AI agent. Local development build.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <header className="border-b border-line bg-card">
          <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-5 py-3">
            <Link href="/" className="font-semibold tracking-tight">
              Agent Studio
            </Link>
            <StatusBar />
          </div>
          <p className="bg-warn-soft px-5 py-1.5 text-center text-xs text-warn">
            Local development mode — no authentication. Do not expose this app or the API to a network.
          </p>
        </header>
        <main className="mx-auto max-w-5xl px-5 py-8">{children}</main>
      </body>
    </html>
  );
}
