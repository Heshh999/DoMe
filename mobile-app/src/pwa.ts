/**
 * Service-worker registration (vite-plugin-pwa, `registerType: "autoUpdate"`). Registered from a
 * module instead of an inline script because cloud-api serves the app with `script-src 'self'`.
 * The worker precaches the built shell only; nothing under /v1 or /ws is ever cached (vite.config.ts).
 *
 * `requestAppUpdate()` asks for the newer app at once (design screen-view-2a D28): after a deploy, an open app still
 * runs the old bundle until it is reloaded, and that bundle refuses a REST answer with a field it does not know. The
 * devices store calls it when that happens and says "A newer DoMe is ready" instead of an error.
 */
import { registerSW } from "virtual:pwa-register";

import { log } from "./lib/log.ts";

/**
 * What `registerSW()` returned. In prompt mode it activates a waiting worker; with `autoUpdate` (this app) it does
 * nothing at all (vite-plugin-pwa's register.js), so it is never the only thing `requestAppUpdate()` does.
 */
let updateSW: ((reloadPage?: boolean) => Promise<void>) | null = null;

export function registerServiceWorker(): void {
  if (!("serviceWorker" in navigator)) return;
  if (import.meta.env.DEV) return; // devOptions.enabled is false; nothing to register in development
  try {
    updateSW = registerSW({
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

/**
 * Fetch the newer app now: the registration checks for a newer worker (`registration.update()`); with `autoUpdate` a
 * newer one installs, activates and the page reloads. The registration's own update function is called as well (it
 * matters only in prompt mode). A no-op in development and where service workers do not exist. Never throws.
 */
export function requestAppUpdate(): void {
  if (import.meta.env.DEV) return;
  if (!("serviceWorker" in navigator)) return;
  const failed = (e: unknown) => log.info("pwa.update_failed", { name: e instanceof Error ? e.name : "error" });
  try {
    void navigator.serviceWorker
      .getRegistration()
      .then((r) => r?.update())
      .catch(failed);
    if (updateSW) void updateSW(true).catch(failed);
    log.info("pwa.update_requested", { registered: updateSW !== null });
  } catch (e) {
    failed(e);
  }
}

/** Tests only: forget the registration's update function. */
export function resetPwaForTests(): void {
  updateSW = null;
}
