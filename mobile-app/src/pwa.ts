/**
 * Service-worker registration (vite-plugin-pwa, `registerType: "autoUpdate"`). Registered from a
 * module instead of an inline script because cloud-api serves the app with `script-src 'self'`.
 * The worker precaches the built shell only; nothing under /v1 or /ws is ever cached (vite.config.ts).
 */
import { registerSW } from "virtual:pwa-register";

import { log } from "./lib/log.ts";

export function registerServiceWorker(): void {
  if (!("serviceWorker" in navigator)) return;
  if (import.meta.env.DEV) return; // devOptions.enabled is false; nothing to register in development
  try {
    registerSW({
      immediate: true,
      onRegisterError(error: unknown) {
        log.warn("pwa.register_failed", { name: error instanceof Error ? error.name : "error" });
      },
      onOfflineReady() {
        log.info("pwa.offline_ready");
      },
    });
  } catch (e) {
    log.warn("pwa.register_threw", { name: e instanceof Error ? e.name : "error" });
  }
}
