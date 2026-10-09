/// <reference types="vitest/config" />
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";
import { VitePWA } from "vite-plugin-pwa";

const here = fileURLToPath(new URL(".", import.meta.url));
const repoRoot = resolve(here, "..");
const sharedTsSrc = resolve(repoRoot, "shared/ts/src");
const pwaRegistry = resolve(here, "src/protocol/registry.ts");
const pkg = JSON.parse(readFileSync(resolve(here, "package.json"), "utf8")) as { version: string };

/**
 * `@dome/protocol`'s `registry.ts` compiles Ajv validators with `new Function` when imported.
 * cloud-api serves this app with `script-src 'self'` (no `unsafe-eval`), so the browser bundle
 * substitutes `src/protocol/registry.ts` — the same exports, backed by validators precompiled at
 * build time from the same frozen schemas (`pnpm gen:validators`). Only imports of `./registry.ts`
 * *from inside shared/ts/src* are redirected; `test/protocol-parity.test.ts` imports the real module
 * by path and checks the two agree. The substitution applies to dev, test and build alike so the
 * tested code path is the shipped one.
 */
function domeProtocolNoEval(): Plugin {
  return {
    name: "dome-protocol-no-eval",
    enforce: "pre",
    resolveId(source, importer) {
      if (!importer || !importer.startsWith(sharedTsSrc)) return null;
      if (source === "./registry.ts" || source === "./registry") return pwaRegistry;
      return null;
    },
  };
}

export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  plugins: [
    domeProtocolNoEval(),
    react(),
    tailwindcss(),
    VitePWA({
      registerType: "autoUpdate",
      injectRegister: false, // registered from src/pwa.ts (no inline script: CSP script-src 'self')
      manifest: {
        name: "DoMe",
        short_name: "DoMe",
        description: "Your phone is a simple, secure remote for your PC.",
        theme_color: "#0b0f17",
        background_color: "#0b0f17",
        display: "standalone",
        start_url: "/app",
        scope: "/",
        orientation: "portrait",
        icons: [
          { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
          { src: "/icons/icon-192-maskable.png", sizes: "192x192", type: "image/png", purpose: "maskable" },
          { src: "/icons/icon-512-maskable.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
      },
      workbox: {
        // Precache the built application shell only. Nothing under /v1 or /ws is ever cached, and
        // the deep links that carry one-time material (/link, /pair) always go to the network.
        globPatterns: ["**/*.{js,css,html,ico,png,svg,webmanifest}"],
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [/^\/v1\//, /^\/ws\//, /^\/link/, /^\/pair/, /^\/\.well-known\//, /^\/healthz/],
        runtimeCaching: [],
        cleanupOutdatedCaches: true,
        clientsClaim: true,
        skipWaiting: true,
        maximumFileSizeToCacheInBytes: 3 * 1024 * 1024,
      },
      devOptions: { enabled: false },
    }),
  ],
  resolve: {
    preserveSymlinks: false,
  },
  server: {
    host: "localhost",
    port: 5173,
    strictPort: true,
    fs: { allow: [repoRoot] },
    proxy: {
      "/v1": { target: "http://127.0.0.1:8000", changeOrigin: false },
      "/ws": { target: "ws://127.0.0.1:8000", ws: true, changeOrigin: false },
      "/.well-known": { target: "http://127.0.0.1:8000", changeOrigin: false },
    },
  },
  build: {
    target: "es2022",
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: {
          protocol: [pwaRegistry],
        },
      },
    },
  },
  test: {
    include: ["test/**/*.test.{ts,tsx}"],
    environment: "jsdom",
    setupFiles: ["test/setup.ts"],
    globals: false,
    css: false,
  },
});
