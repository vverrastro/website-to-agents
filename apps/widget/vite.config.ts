import { defineConfig } from "vite";

// Library build: one self-contained IIFE that a host page loads with a <script> tag.
export default defineConfig({
  build: {
    lib: { entry: "src/main.ts", name: "WebsiteToAgentsWidget", formats: ["iife"], fileName: () => "widget.js" },
    target: "es2020",
    sourcemap: false,
    emptyOutDir: true,
  },
  preview: {
    host: "127.0.0.1",
    port: 5174,
    // CSP for the local demo page only (the widget itself must work under a customer's CSP).
    headers: {
      "Content-Security-Policy":
        "default-src 'self'; script-src 'self'; style-src 'self'; connect-src http://127.0.0.1:8000 http://localhost:8000; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'",
      "X-Content-Type-Options": "nosniff",
    },
  },
});
