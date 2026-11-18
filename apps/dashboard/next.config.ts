import type { NextConfig } from "next";

// The browser only talks to this Next server. /api/admin/* is proxied to the local API, so there
// is no CORS surface on the privileged API and no secret ever reaches browser code.
const apiInternal = process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8000";
const isDev = process.env.NODE_ENV !== "production";

const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const config: NextConfig = {
  transpilePackages: ["@wta/agent-protocol"],
  poweredByHeader: false,
  // Next 16 dev otherwise writes an AGENTS.md into the repo.
  agentRules: false,
  async rewrites() {
    return [{ source: "/api/admin/:path*", destination: `${apiInternal}/admin/v1/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: csp },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
        ],
      },
    ];
  },
};

export default config;
